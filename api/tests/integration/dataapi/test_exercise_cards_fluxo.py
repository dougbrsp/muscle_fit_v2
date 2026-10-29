"""Cards ilustrados de exercicios ponta a ponta contra MySQL real, nos DOIS
backends (PyMySQL de DEV e o shim da RDS Data API de producao), num schema
com todas as migracoes (064 inclusa).

OpenAI e S3 sao falsos (nada sai do processo; a "OpenAI" devolve uma
ilustracao sintetica de duas cenas em fundo transparente); o resto e o
caminho real: rotas -> servico -> SQL (`FOR UPDATE`, UPDATE condicional com
`rowcount`, UNIQUE, DECIMAL/DATETIME ida e volta) -> worker em processo
(`run_generation_job`, com `load_context` + `build_prompt` reais lendo o
catalogo) -> compositor real (fontes + logo oficiais, musculos do catalogo)
-> preview composto -> aprovacao que so codifica -> ponteiro no catalogo.

O que so o banco prova:

1. generate -> GENERATED -> approve -> PUBLISHED com `card_version=1` no
   catalogo e os 3 objetos imutaveis no S3; `GET /card` antes (null) e depois;
   redelivery do mesmo evento nao chama a OpenAI (`rowcount=0` real);
2. generate -> reject -> regenerate -> approve: filha com
   `parent_generation_id` e o motivo humano no prompt;
3. a 2a publicacao marca a 1a SUPERSEDED e o catalogo passa a `card_version=2`;
4. 409 com geracao viva (e replay da mesma chave = 200).

Isolamento: `isolamento_v3.truncar` (catalogo, usuarios) + a tabela dos cards.
Sem relogio congelado: nada aqui compara o carimbo com um instante fixo.
"""

from __future__ import annotations

import io
import uuid
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

# Importados no topo de proposito: `use_dataapi` so troca o backend de modulos
# `app.*` JA importados (ver tests/support/dataapi_backend.py).
from app import async_jobs, openai_client
from app import exercise_card_composer as composer
from app import exercise_card_jobs as jobs
from app import exercise_card_service as svc
from app import exercise_card_storage as storage
from app import exercise_cards
from app.security import Identity, get_current_identity
from tests.support import card_illustrations as ill
from tests.support.fake_s3 import FakeS3
from tests.support.isolamento_v3 import truncar

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]

BUCKET = "musclefit-uploads"
SUPINO = 42
AGACHAMENTO = 43


# O que a "OpenAI" devolve: a ilustracao (Estrategia B, prompt V2).
PNG = ill.illustration_png()


def _image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _key() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def seed(raw):
    truncar(raw, extras=("exercise_card_generations",))
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_cards', 'x', 'admin')")
        admin_id = cur.lastrowid
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('aluno_cards', 'x', 'regular')")
        regular_id = cur.lastrowid
        cur.execute("INSERT INTO muscle_groups (name) VALUES ('Peitoral')")
        peitoral = cur.lastrowid
        # Mapa corporal do grupo (silhueta do rodape, CARD_LAYOUT_V2). O
        # `truncar` esvazia `muscle_groups` mas nao `muscle_group_body_map`:
        # uma linha velha com o mesmo id (AUTO_INCREMENT reiniciado) acenderia
        # outro slug - por isso apaga antes de gravar.
        cur.execute("DELETE FROM muscle_group_body_map WHERE muscle_group_id = %s", (peitoral,))
        cur.execute("INSERT INTO muscle_group_body_map (muscle_group_id, slugs) VALUES (%s, 'chest')", (peitoral,))
        cur.execute(
            "INSERT INTO exercise_catalog (id, name, is_active, execution_description) VALUES (%s, %s, 1, %s)",
            (SUPINO, "Supino reto com barra", "Preparação:\n- Deite no banco\nExecução:\n- Empurre a barra para cima"),
        )
        cur.execute("INSERT INTO exercise_catalog (id, name, is_active) VALUES (%s, %s, 1)", (AGACHAMENTO, "Agachamento livre"))
        cur.execute(
            "INSERT INTO exercise_muscle_contributions (exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) "
            "VALUES (%s, %s, 'PRIMARY', 1, 1)",
            (SUPINO, peitoral),
        )
    raw.commit()
    return {"admin": admin_id, "regular": regular_id}


@pytest.fixture
def fakes(monkeypatch):
    """S3, OpenAI e o disparo do job falsos; flag ligada."""
    monkeypatch.setenv(svc.ENV_ENABLED, "true")
    monkeypatch.setenv("EQUIPMENT_PHOTOS_BUCKET", BUCKET)
    for name in ("PUBLIC_ASSETS_BASE_URL", "S3_PRESIGN_ENDPOINT_URL", svc.ENV_MAX_PER_DAY, svc.ENV_MAX_IN_FLIGHT):
        monkeypatch.delenv(name, raising=False)

    s3 = FakeS3()
    monkeypatch.setattr(storage, "s3_client", lambda: s3)
    monkeypatch.setattr(storage, "presign_client", lambda: s3)

    prompts: list[str] = []

    def fake_generate(prompt, *, model=None, quality=None, size=None, timeout=None, moderation=None, background=None):
        assert background == "transparent"
        prompts.append(prompt)
        return openai_client.ImageResult(
            image=PNG, model=model, quality=quality, size=size, tokens_in=100, tokens_out=200,
            cost_usd=Decimal("0.05"), attempts=1, elapsed_seconds=2.0,
        )

    monkeypatch.setattr(openai_client, "generate_image", fake_generate)

    events: list[dict] = []
    monkeypatch.setattr(async_jobs, "create_job", lambda job_type, request=None, *, owner_user_id: f"job-{len(events) + 1}")
    monkeypatch.setattr(async_jobs, "invoke_async", lambda env_var, payload: events.append(payload))
    return {"s3": s3, "prompts": prompts, "events": events}


@pytest.fixture
def backend(db_backend, fake_rds) -> str:
    """`db_backend` (os dois backends) + nenhuma transacao Data API
    pendurada no fim (o approve abre e fecha tres)."""
    yield db_backend
    if db_backend == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


def _client(user_id: int, role: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(exercise_cards.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app)


def _row(raw, generation_id: int) -> dict:
    with raw.cursor() as cur:
        cur.execute("SELECT * FROM exercise_card_generations WHERE id = %s", (generation_id,))
        return cur.fetchone()


def _catalog(raw, exercise_id: int) -> dict:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT card_generation_id, card_version, card_published_at FROM exercise_catalog WHERE id = %s",
            (exercise_id,),
        )
        return cur.fetchone()


def _gerar(api: TestClient, fakes: dict, exercise_id: int = SUPINO, **body) -> int:
    resposta = api.post(f"/exercises/{exercise_id}/card-generations", json={"idempotency_key": _key(), **body})
    assert resposta.status_code == 202, resposta.text
    jobs.run_generation_job(fakes["events"][-1])
    return resposta.json()["generation"]["id"]


def test_gerar_aprovar_publica_v1_e_redelivery_nao_cobra(seed, backend, raw, fakes):
    api = _client(seed["admin"])
    assert api.get(f"/exercises/{SUPINO}/card").json() == {"exercise_id": SUPINO, "exercise_card": None}

    criada = api.post(f"/exercises/{SUPINO}/card-generations", json={"idempotency_key": _key(), "instruction": "barra reta"})
    assert criada.status_code == 202, (backend, criada.text)
    gid = criada.json()["generation"]["id"]
    assert criada.json()["generation"]["status"] == "PENDING"
    assert _row(raw, gid)["job_id"] == "job-1"

    jobs.run_generation_job(fakes["events"][-1])
    row = _row(raw, gid)
    assert row["status"] == "GENERATED", (backend, row["error_code"], row["error_message"])
    assert row["prompt_version"] == "MUSCLEFIT_EXERCISE_CARD_V2_ILLUSTRATION"
    # Ilustracao crua guardada; preview = card composto com o conteudo do catalogo.
    ilustracao = fakes["s3"].body(BUCKET, storage.illustration_key(gid))
    preview = fakes["s3"].body(BUCKET, row["preview_s3_key"])
    assert ilustracao == PNG
    # Slug do mapa corporal veio do MESMO SELECT dos musculos (LEFT JOIN em
    # muscle_group_body_map) nos dois backends: a silhueta acende o peitoral.
    esperado = composer.compose_card(
        PNG,
        composer.CardContent("Supino reto com barra", ("Peitoral",), primary_slugs=("chest",)),
        composer.load_card_assets(),
    )
    assert preview == esperado, backend
    assert (row["width"], row["height"], row["bytes"]) == (1024, 1536, len(preview))
    assert row["cost_usd"] == Decimal("0.0500")
    assert "Supino reto com barra" in row["prompt_text"] and "barra reta" in row["prompt_text"]
    assert row["started_at"] is not None and row["generated_at"] is not None

    # Redelivery: o UPDATE condicional devolve rowcount=0 no backend real.
    assert jobs.run_generation_job(fakes["events"][-1])["status"] == "skipped"
    assert len(fakes["prompts"]) == 1

    lida = api.get(f"/exercises/{SUPINO}/card-generations/{gid}").json()["generation"]
    assert lida["status"] == "GENERATED" and lida["preview_url"], backend
    assert lida["cost_usd"] == 0.05 and lida["created_at"].endswith("Z")

    aprovada = api.post(f"/exercises/{SUPINO}/card-generations/{gid}/approve")
    assert aprovada.status_code == 200, (backend, aprovada.text)
    assert aprovada.json()["generation"]["status"] == "PUBLISHED"
    assert aprovada.json()["exercise_card"]["version"] == 1

    catalogo = _catalog(raw, SUPINO)
    assert (catalogo["card_generation_id"], catalogo["card_version"]) == (gid, 1)
    assert catalogo["card_published_at"] is not None
    for key in ("card.webp", "card.png", "thumb.webp"):
        objeto = fakes["s3"].objects[(BUCKET, f"exercise-cards/{SUPINO}/v1/{key}")]
        assert objeto.cache_control == storage.IMMUTABLE_CACHE_CONTROL
    # Publicado = exatamente o preview revisado (sem recompor logo).
    publicado = _image(fakes["s3"].body(BUCKET, f"exercise-cards/{SUPINO}/v1/card.png"))
    assert publicado.tobytes() == _image(preview).tobytes()

    publicado = _client(seed["regular"], role="regular").get(f"/exercises/{SUPINO}/card").json()
    assert publicado["exercise_card"]["url"] == f"https://app.musclefit.com.br/exercise-cards/{SUPINO}/v1/card.webp"
    assert publicado["exercise_card"]["version"] == 1


def test_rejeitar_regenerar_e_aprovar(seed, backend, raw, fakes):
    api = _client(seed["admin"])
    pai = _gerar(api, fakes)
    rejeitada = api.post(f"/exercises/{SUPINO}/card-generations/{pai}/reject", json={"reason": "anilha com número"})
    assert rejeitada.status_code == 200, (backend, rejeitada.text)
    assert _row(raw, pai)["status"] == "REJECTED"

    filha = api.post(f"/exercises/{SUPINO}/card-generations/{pai}/regenerate", json={"idempotency_key": _key()})
    assert filha.status_code == 202, (backend, filha.text)
    filha_id = filha.json()["generation"]["id"]
    assert _row(raw, filha_id)["parent_generation_id"] == pai
    jobs.run_generation_job(fakes["events"][-1])
    assert "anilha com número" in fakes["prompts"][-1]
    assert "regenerated" not in fakes["prompts"][-1]

    aprovada = api.post(f"/exercises/{SUPINO}/card-generations/{filha_id}/approve")
    assert aprovada.status_code == 200, (backend, aprovada.text)
    assert _catalog(raw, SUPINO)["card_version"] == 1
    assert _row(raw, filha_id)["version"] == 1
    assert _row(raw, pai)["version"] is None


def test_regenerar_do_preview_rejeita_o_pai_e_nao_vaza_o_codigo(seed, backend, raw, fakes):
    api = _client(seed["admin"])
    pai = _gerar(api, fakes)
    filha = api.post(
        f"/exercises/{SUPINO}/card-generations/{pai}/regenerate",
        json={"idempotency_key": _key(), "instruction": "pegada mais aberta"},
    )
    assert filha.status_code == 202, (backend, filha.text)
    assert filha.json()["parent"]["status"] == "REJECTED"
    assert _row(raw, pai)["reject_reason"] == "regenerated"
    jobs.run_generation_job(fakes["events"][-1])
    assert "pegada mais aberta" in fakes["prompts"][-1]
    assert "regenerated" not in fakes["prompts"][-1]


def test_segunda_publicacao_marca_a_primeira_superseded(seed, backend, raw, fakes):
    api = _client(seed["admin"])
    primeira = _gerar(api, fakes)
    assert api.post(f"/exercises/{SUPINO}/card-generations/{primeira}/approve").status_code == 200
    segunda = _gerar(api, fakes)
    resposta = api.post(f"/exercises/{SUPINO}/card-generations/{segunda}/approve")
    assert resposta.status_code == 200, (backend, resposta.text)
    assert resposta.json()["exercise_card"]["version"] == 2

    assert (_row(raw, primeira)["status"], _row(raw, primeira)["version"]) == ("SUPERSEDED", 1)
    assert (_row(raw, segunda)["status"], _row(raw, segunda)["version"]) == ("PUBLISHED", 2)
    catalogo = _catalog(raw, SUPINO)
    assert (catalogo["card_generation_id"], catalogo["card_version"]) == (segunda, 2)
    itens = api.get(f"/exercises/{SUPINO}/card-generations").json()["items"]
    assert [item["status"] for item in itens] == ["PUBLISHED", "SUPERSEDED"]
    assert itens[1]["card"]["url"].endswith(f"/exercise-cards/{SUPINO}/v1/card.webp")


def test_geracao_v1_nao_e_aprovavel_e_regenera_em_v2(seed, backend, raw, fakes):
    """As 4 geracoes V1 do DEV (checkpoint rodada 1): aprovar responde 409
    sem tocar no S3; regenerar nasce V2 `minimal` mesmo com pai `standard`."""
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO exercise_card_generations (exercise_id, status, idempotency_key, prompt_version, text_level, "
            "preview_s3_key, created_by, created_at, updated_at) "
            "VALUES (%s, 'GENERATED', %s, 'MUSCLEFIT_EXERCISE_CARD_V1', 'standard', 'exercise-card-drafts/1/preview.png', "
            "%s, UTC_TIMESTAMP(), UTC_TIMESTAMP())",
            (SUPINO, _key(), seed["admin"]),
        )
        v1 = cur.lastrowid
    raw.commit()
    api = _client(seed["admin"])
    resposta = api.post(f"/exercises/{SUPINO}/card-generations/{v1}/approve")
    assert (resposta.status_code, resposta.json()["detail"]["error"]) == (409, "not_reviewable"), backend
    assert _row(raw, v1)["status"] == "GENERATED"
    assert fakes["s3"].calls == []
    filha = api.post(f"/exercises/{SUPINO}/card-generations/{v1}/regenerate", json={"idempotency_key": _key()})
    assert filha.status_code == 202, (backend, filha.text)
    corpo = filha.json()["generation"]
    assert (corpo["text_level"], corpo["prompt_version"]) == ("minimal", "MUSCLEFIT_EXERCISE_CARD_V2_ILLUSTRATION")
    assert _row(raw, v1)["status"] == "REJECTED"


def test_409_com_geracao_viva_e_replay_200(seed, backend, raw, fakes):
    api = _client(seed["admin"])
    key = _key()
    primeira = api.post(f"/exercises/{SUPINO}/card-generations", json={"idempotency_key": key})
    assert primeira.status_code == 202, (backend, primeira.text)
    conflito = api.post(f"/exercises/{SUPINO}/card-generations", json={"idempotency_key": _key()})
    assert conflito.status_code == 409, (backend, conflito.text)
    assert conflito.json()["detail"]["error"] == "exercise_card_generation_in_progress"
    replay = api.post(f"/exercises/{SUPINO}/card-generations", json={"idempotency_key": key})
    assert replay.status_code == 200
    assert replay.json()["generation"]["id"] == primeira.json()["generation"]["id"]
    # Outro exercicio segue livre (o teto global padrao e 2).
    assert api.post(f"/exercises/{AGACHAMENTO}/card-generations", json={"idempotency_key": _key()}).status_code == 202
    with raw.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM exercise_card_generations")
        assert cur.fetchone()["n"] == 2

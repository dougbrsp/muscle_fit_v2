"""Cards ilustrados - servico (estados, limites, sweep, idempotencia,
publicacao) e worker (`run_generation_job`), sem rede e sem MySQL.

Banco: `tests/support/cards_sqlite.py` (SQLite em memoria executando o SQL
real do servico). S3: `tests/support/fake_s3.py`. OpenAI: `generate_image`
falso que conta as chamadas - e o que prova "redelivery nao cobra de novo" -
e devolve uma ilustracao SINTETICA (`tests/support/card_illustrations.py`,
duas cenas em fundo transparente); o compositor e o REAL (Estrategia B: o
worker compoe o card logo depois da OpenAI).
Relogio: `exercise_card_service.utcnow` congelado (o sweep e os limites
comparam carimbos gravados pelo proprio codigo; `isolamento_v3.congelar_relogio`
congela so os modulos do dashboard, entao aqui o ponto de injecao e o
`utcnow` do servico).

Rodar: cd api && python -m pytest tests/unit/test_exercise_card_service.py
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from PIL import Image

from app import async_jobs, openai_client
from app import exercise_card_composer as composer
from app import exercise_card_jobs as jobs
from app import exercise_card_processor as processor
from app import exercise_card_prompt as card_prompt
from tests.support.privado import exigir
from app import exercise_card_service as svc
from app import exercise_card_storage as storage
from tests.support import card_illustrations as ill
from tests.support.cards_sqlite import CardsSqlite
from tests.support.fake_s3 import FakeS3

BUCKET = "musclefit-uploads"
ADMIN = 7
T0 = datetime(2026, 10, 3, 12, 0, 0)


def _png(width: int = 768, height: int = 1152) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 10, 10)).save(buffer, format="PNG")
    return buffer.getvalue()


# O que a OpenAI "devolve": a ilustracao (duas cenas, fundo transparente).
PNG = ill.illustration_png()
# Ilustracao sem primeiro plano nenhum: a composicao falha.
EMPTY_ILLUSTRATION = ill.empty_png()
V1 = "MUSCLEFIT_EXERCISE_CARD_V1"


def _image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _key() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(svc.ENV_ENABLED, "true")
    monkeypatch.setenv("EQUIPMENT_PHOTOS_BUCKET", BUCKET)
    for name in (
        svc.ENV_MAX_PER_DAY,
        svc.ENV_MAX_IN_FLIGHT,
        svc.ENV_STALE_MINUTES,
        "OPENAI_IMAGE_MODEL",
        "OPENAI_IMAGE_QUALITY",
        "OPENAI_IMAGE_SIZE",
        "PUBLIC_ASSETS_BASE_URL",
        "S3_PRESIGN_ENDPOINT_URL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def db(monkeypatch):
    fake = CardsSqlite()
    monkeypatch.setattr(svc, "transaction", fake.transaction)
    monkeypatch.setattr(jobs, "transaction", fake.transaction)
    fake.add_exercise(42, "Supino reto")
    fake.add_exercise(43, "Agachamento livre")
    yield fake
    fake.close()


class _Clock:
    def __init__(self) -> None:
        self.now = T0

    def advance(self, **delta) -> None:
        self.now = self.now + timedelta(**delta)


@pytest.fixture
def clock(monkeypatch):
    relogio = _Clock()
    monkeypatch.setattr(svc, "utcnow", lambda: relogio.now)
    return relogio


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3(endpoint_url="https://fake-s3.local")
    monkeypatch.setattr(storage, "s3_client", lambda: fake)
    monkeypatch.setattr(storage, "presign_client", lambda: fake)
    return fake


@pytest.fixture
def openai(monkeypatch):
    chamadas: list[dict] = []

    def fake_generate(prompt, *, model=None, quality=None, size=None, timeout=None, moderation=None, background=None):
        chamadas.append({"prompt": prompt, "model": model, "quality": quality, "size": size, "background": background})
        return openai_client.ImageResult(
            image=PNG,
            model=model,
            quality=quality,
            size=size,
            tokens_in=11,
            tokens_out=22,
            cost_usd=Decimal("0.05"),
            attempts=1,
            elapsed_seconds=1.5,
        )

    monkeypatch.setattr(openai_client, "generate_image", fake_generate)
    return chamadas


@pytest.fixture
def prompts(monkeypatch):
    """Contexto do catalogo falso (o SQL real de `load_context` usa FIELD(),
    so do MySQL) e espiao do `build_prompt` REAL."""
    vistos: list[dict] = []

    def fake_context(cursor, exercise_id):
        if int(exercise_id) not in (42, 43):
            return None
        return card_prompt.ExerciseCardContext(exercise_id=int(exercise_id), name="Supino reto")

    real_build = card_prompt.build_prompt

    def spy_build(ctx, text_level=card_prompt.DEFAULT_TEXT_LEVEL, instruction=None, parent_reject_reason=None):
        vistos.append({"text_level": text_level, "instruction": instruction, "parent_reject_reason": parent_reject_reason})
        return real_build(ctx, text_level, instruction, parent_reject_reason)

    monkeypatch.setattr(card_prompt, "load_context", fake_context)
    monkeypatch.setattr(card_prompt, "build_prompt", spy_build)
    return vistos


@pytest.fixture
def dispatched(monkeypatch):
    enviados: list[dict] = []
    monkeypatch.setattr(async_jobs, "create_job", lambda job_type, request=None, *, owner_user_id: f"job-{len(enviados) + 1}")
    monkeypatch.setattr(async_jobs, "invoke_async", lambda env_var, payload: enviados.append({"env": env_var, **payload}))
    monkeypatch.setattr(async_jobs, "mark_failed", lambda job_id, error: None)
    return enviados


@pytest.fixture
def worker(db, clock, s3, openai, prompts, dispatched):
    """Tudo falso e ligado: cria -> dispara -> roda o worker em processo."""

    def gerar(exercise_id: int = 42, **kwargs) -> int:
        row, created = svc.create_generation(
            exercise_id=exercise_id, user_id=ADMIN, idempotency_key=kwargs.pop("key", None) or _key(), **kwargs
        )
        assert created
        row = jobs.dispatch(row)
        jobs.run_generation_job(dispatched[-1])
        return int(row["id"])

    return gerar


# ---------------------------------------------------------------------------
# Criar
# ---------------------------------------------------------------------------


def test_criar_grava_pending_com_configuracao_e_autoria(db, clock, monkeypatch):
    monkeypatch.setenv("OPENAI_IMAGE_QUALITY", "high")
    row, created = svc.create_generation(
        exercise_id=42, user_id=ADMIN, idempotency_key=_key(), instruction="  braço mais estendido  "
    )
    assert created is True
    gravada = db.generation(row["id"])
    assert gravada["status"] == svc.STATUS_PENDING
    assert (gravada["model"], gravada["quality"], gravada["size"]) == ("gpt-image-1.5", "high", "1024x1536")
    assert gravada["prompt_version"] == card_prompt.PROMPT_VERSION
    assert gravada["text_level"] == "minimal"
    assert gravada["request_instruction"] == "braço mais estendido"
    assert gravada["created_by"] == ADMIN
    assert gravada["card_type"] == "exercise_execution"
    assert gravada["created_at"] == T0
    # A linha do exercicio foi travada antes da checagem de "1 viva".
    assert any("FROM exercise_catalog" in sql for sql in db.locks)


def test_criar_com_flag_desligada_e_503_e_nao_grava(db, clock, monkeypatch):
    monkeypatch.setenv(svc.ENV_ENABLED, "false")
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=_key())
    assert (exc.value.http_status, exc.value.code) == (503, svc.CODE_DISABLED)
    assert db.raw.execute("SELECT COUNT(*) FROM exercise_card_generations").fetchone()[0] == 0


def test_flag_so_liga_com_valor_explicito(monkeypatch):
    for valor, esperado in (("true", True), ("1", True), ("ON", True), ("false", False), ("", False), ("sim", False)):
        monkeypatch.setenv(svc.ENV_ENABLED, valor)
        assert svc.generation_enabled() is esperado, valor
    monkeypatch.delenv(svc.ENV_ENABLED)
    assert svc.generation_enabled() is False


def test_criar_em_exercicio_inexistente_e_404(db, clock):
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.create_generation(exercise_id=999, user_id=ADMIN, idempotency_key=_key())
    assert (exc.value.http_status, exc.value.code) == (404, svc.CODE_EXERCISE_NOT_FOUND)


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"idempotency_key": None}, svc.CODE_INVALID_IDEMPOTENCY_KEY),
        ({"idempotency_key": "nao-e-uuid"}, svc.CODE_INVALID_IDEMPOTENCY_KEY),
        ({"idempotency_key": 123}, svc.CODE_INVALID_IDEMPOTENCY_KEY),
        ({"text_level": "maximo"}, svc.CODE_INVALID_TEXT_LEVEL),
        # V2: o texto do card e da aplicacao; so `minimal` existe.
        ({"text_level": "standard"}, svc.CODE_INVALID_TEXT_LEVEL),
        ({"text_level": "full"}, svc.CODE_INVALID_TEXT_LEVEL),
        ({"instruction": "x" * (svc.MAX_INSTRUCTION_CHARS + 1)}, svc.CODE_INSTRUCTION_TOO_LONG),
    ],
)
def test_entrada_invalida_e_422(db, clock, kwargs, code):
    params = {"idempotency_key": _key(), **kwargs}
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.create_generation(exercise_id=42, user_id=ADMIN, **params)
    assert (exc.value.http_status, exc.value.code) == (422, code)


def test_replay_da_mesma_chave_devolve_a_existente_sem_criar(db, clock):
    key = _key()
    primeira, criada = svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=key)
    replay, criada_de_novo = svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=key.upper())
    assert criada is True and criada_de_novo is False
    assert replay["id"] == primeira["id"]
    assert db.raw.execute("SELECT COUNT(*) FROM exercise_card_generations").fetchone()[0] == 1


def test_replay_vence_os_limites(db, clock, monkeypatch):
    """A resposta ao replay nao pode virar 409/429 so porque a 1a criou a
    geracao viva que agora conta."""
    monkeypatch.setenv(svc.ENV_MAX_PER_DAY, "1")
    key = _key()
    svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=key)
    _, criada = svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=key)
    assert criada is False


def test_corrida_da_mesma_chave_cai_no_unique_e_vira_replay(db, clock, monkeypatch):
    """Dois POSTs simultaneos: os dois passam pelo SELECT da chave antes de
    qualquer INSERT; o 2o bate no UNIQUE (exercise_id, idempotency_key)."""
    key = _key()
    existente = db.insert_generation(exercise_id=42, idempotency_key=key, status="FAILED", created_at=T0)
    original = svc.find_by_idempotency_key
    chamadas = {"n": 0}

    def cego_na_primeira(cursor, exercise_id, idempotency_key):
        chamadas["n"] += 1
        return None if chamadas["n"] == 1 else original(cursor, exercise_id, idempotency_key)

    monkeypatch.setattr(svc, "find_by_idempotency_key", cego_na_primeira)
    row, criada = svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=key)
    assert criada is False
    assert row["id"] == existente


def test_mesma_chave_em_outro_tipo_de_pedido_e_409(db, clock):
    key = _key()
    pai = db.insert_generation(exercise_id=42, status="FAILED")
    db.insert_generation(exercise_id=42, status="FAILED", idempotency_key=key)  # criada SEM pai
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.regenerate_generation(exercise_id=42, parent_generation_id=pai, user_id=ADMIN, idempotency_key=key)
    assert (exc.value.http_status, exc.value.code) == (409, svc.CODE_IDEMPOTENCY_CONFLICT)


# ---------------------------------------------------------------------------
# Limites
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("viva", ["PENDING", "GENERATING", "APPROVING"])
def test_uma_geracao_viva_por_exercicio(db, clock, viva):
    existente = db.insert_generation(exercise_id=42, status=viva, created_at=T0, updated_at=T0, started_at=T0)
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=_key())
    assert (exc.value.http_status, exc.value.code) == (409, svc.CODE_IN_PROGRESS)
    assert exc.value.generation_id == existente
    # Outro exercicio segue livre.
    _, criada = svc.create_generation(exercise_id=43, user_id=ADMIN, idempotency_key=_key())
    assert criada


@pytest.mark.parametrize("fechada", ["GENERATED", "REJECTED", "FAILED", "PUBLISHED", "SUPERSEDED"])
def test_geracao_fechada_nao_bloqueia(db, clock, fechada):
    db.insert_generation(exercise_id=42, status=fechada)
    _, criada = svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=_key())
    assert criada


def test_teto_global_de_geracoes_em_andamento(db, clock, monkeypatch):
    monkeypatch.setenv(svc.ENV_MAX_IN_FLIGHT, "1")
    db.add_exercise(44, "Remada")
    db.insert_generation(exercise_id=44, status="GENERATING", created_at=T0, started_at=T0)
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=_key())
    assert (exc.value.http_status, exc.value.code) == (429, svc.CODE_GLOBAL_IN_FLIGHT_LIMIT)


def test_approving_nao_ocupa_vaga_de_lambda(db, clock, monkeypatch):
    monkeypatch.setenv(svc.ENV_MAX_IN_FLIGHT, "1")
    db.insert_generation(exercise_id=43, status="APPROVING", updated_at=T0)
    _, criada = svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=_key())
    assert criada


def test_limite_diario_conta_tentativas_do_dia_utc_do_admin(db, clock, monkeypatch):
    monkeypatch.setenv(svc.ENV_MAX_PER_DAY, "2")
    ontem = T0 - timedelta(hours=13)  # 23:00 do dia anterior
    db.insert_generation(exercise_id=43, status="FAILED", created_by=ADMIN, created_at=ontem)
    db.insert_generation(exercise_id=43, status="FAILED", created_by=ADMIN, created_at=T0 - timedelta(hours=1))
    db.insert_generation(exercise_id=43, status="REJECTED", created_by=ADMIN + 1, created_at=T0)  # outro admin
    _, criada = svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=_key())
    assert criada  # 2a de hoje
    db.raw.execute("UPDATE exercise_card_generations SET status = 'FAILED'")
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=_key())
    assert (exc.value.http_status, exc.value.code) == (429, svc.CODE_DAILY_LIMIT)


def test_limites_invalidos_no_ambiente_voltam_ao_padrao(monkeypatch):
    monkeypatch.setenv(svc.ENV_MAX_PER_DAY, "abc")
    monkeypatch.setenv(svc.ENV_MAX_IN_FLIGHT, "0")
    monkeypatch.setenv(svc.ENV_STALE_MINUTES, "-5")
    assert (svc.max_generations_per_day(), svc.max_in_flight(), svc.stale_minutes()) == (100, 2, 10)


# ---------------------------------------------------------------------------
# Sweep (relogio congelado)
# ---------------------------------------------------------------------------


def test_sweep_fecha_orfas_e_poupa_as_recentes(db, clock):
    velha_pending = db.insert_generation(exercise_id=42, status="PENDING", created_at=T0 - timedelta(minutes=11))
    velha_generating = db.insert_generation(
        exercise_id=43, status="GENERATING", created_at=T0 - timedelta(minutes=30), started_at=T0 - timedelta(minutes=10, seconds=1)
    )
    recente = db.insert_generation(
        exercise_id=43, status="GENERATING", created_at=T0 - timedelta(minutes=30), started_at=T0 - timedelta(minutes=9)
    )
    approving_velha = db.insert_generation(exercise_id=42, status="APPROVING", updated_at=T0 - timedelta(minutes=3))
    approving_recente = db.insert_generation(exercise_id=43, status="APPROVING", updated_at=T0 - timedelta(minutes=1))

    with db.transaction() as conn:
        with conn.cursor() as cursor:
            counts = svc.sweep(cursor, now=T0)

    assert counts == {"worker_lost": 2, "publish_interrupted": 1}
    for gid in (velha_pending, velha_generating):
        row = db.generation(gid)
        assert (row["status"], row["error_code"], row["failed_at"]) == ("FAILED", "WORKER_LOST", T0)
    assert db.generation(recente)["status"] == "GENERATING"
    row = db.generation(approving_velha)
    assert (row["status"], row["error_code"]) == ("GENERATED", "PUBLISH_INTERRUPTED")
    assert db.generation(approving_recente)["status"] == "APPROVING"


def test_sweep_respeita_stale_minutes_do_ambiente_e_o_escopo(db, clock, monkeypatch):
    monkeypatch.setenv(svc.ENV_STALE_MINUTES, "30")
    vinte = db.insert_generation(exercise_id=42, status="PENDING", created_at=T0 - timedelta(minutes=20))
    outra = db.insert_generation(exercise_id=43, status="PENDING", created_at=T0 - timedelta(minutes=40))
    with db.transaction() as conn:
        with conn.cursor() as cursor:
            svc.sweep(cursor, now=T0, exercise_id=42)
    assert db.generation(vinte)["status"] == "PENDING"  # 20 < 30
    assert db.generation(outra)["status"] == "PENDING"  # fora do escopo
    with db.transaction() as conn:
        with conn.cursor() as cursor:
            svc.sweep(cursor, now=T0)
    assert db.generation(outra)["status"] == "FAILED"


def test_post_varre_orfa_global_antes_do_teto(db, clock, monkeypatch):
    """Orfa de OUTRO exercicio nao pode ocupar a vaga global para sempre."""
    monkeypatch.setenv(svc.ENV_MAX_IN_FLIGHT, "1")
    orfa = db.insert_generation(exercise_id=43, status="GENERATING", started_at=T0 - timedelta(hours=1))
    _, criada = svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=_key())
    assert criada
    assert db.generation(orfa)["error_code"] == "WORKER_LOST"


def test_sweep_emite_evento_sem_texto_livre(db, clock, caplog):
    db.insert_generation(exercise_id=42, status="PENDING", created_at=T0 - timedelta(hours=1))
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        svc.get_generation(exercise_id=42, generation_id=1)
    eventos = [json.loads(r.getMessage()) for r in caplog.records if r.name == "musclefit.events"]
    assert {"event": "exercise_card.swept", "rule": "WORKER_LOST", "count": 1, "exercise_id": 42} in eventos


# ---------------------------------------------------------------------------
# Worker: reserva atomica e redelivery
# ---------------------------------------------------------------------------


def test_worker_gera_compoe_e_grava_generated(worker, db, s3, openai, prompts, dispatched):
    gid = worker(instruction="pegada mais aberta")
    row = db.generation(gid)
    assert row["status"] == "GENERATED"
    assert row["prompt_version"] == card_prompt.PROMPT_VERSION == "MUSCLEFIT_EXERCISE_CARD_V2_ILLUSTRATION"
    assert row["preview_s3_key"] == f"exercise-card-drafts/{gid}/preview.png"
    # A ilustracao paga fica guardada ao lado; o preview e o card COMPOSTO.
    ilustracao = s3.body(BUCKET, storage.illustration_key(gid))
    preview = s3.body(BUCKET, row["preview_s3_key"])
    assert ilustracao == PNG
    assert _image(preview).size == (composer.CARD_WIDTH, composer.CARD_HEIGHT)
    assert _image(preview).mode == "RGB" and _image(preview).info == {}
    # Largura/altura/bytes gravados sao os do card composto, nao os da ilustracao.
    assert (row["width"], row["height"], row["bytes"]) == (1024, 1536, len(preview))
    assert (row["tokens_in"], row["tokens_out"], row["cost_usd"]) == (11, 22, Decimal("0.05"))
    exigir(bool(row["prompt_text"]) and "Supino reto" in row["prompt_text"], "prompt gravado com o exercício")
    assert row["started_at"] == T0 and row["generated_at"] == T0
    assert row["job_id"] == "job-1"
    # Usa model/quality/size GRAVADOS na linha, nao o ambiente do worker; fundo transparente.
    assert openai[0]["model"] == "gpt-image-1.5" and openai[0]["size"] == "1024x1536"
    assert openai[0]["background"] == "transparent" == jobs.ILLUSTRATION_BACKGROUND
    assert prompts[0] == {"text_level": "minimal", "instruction": "pegada mais aberta", "parent_reject_reason": None}
    for key in (row["preview_s3_key"], storage.illustration_key(gid)):
        assert s3.objects[(BUCKET, key)].cache_control == storage.DRAFT_CACHE_CONTROL
    assert dispatched[0]["job_kind"] == "exercise-card" and dispatched[0]["generation_id"] == gid
    assert dispatched[0]["env"] == "PLAN_GENERATOR_FUNCTION_NAME"


def test_preview_e_o_card_composto_com_o_conteudo_do_catalogo(worker, db, s3, monkeypatch):
    """O preview e exatamente `compose_card(ilustracao, conteudo do contexto,
    assets oficiais)` - o mesmo card que o admin vai aprovar."""
    gid = worker()
    preview = s3.body(BUCKET, storage.draft_key(gid))
    contexto = card_prompt.ExerciseCardContext(exercise_id=42, name="Supino reto")
    esperado = composer.compose_card(PNG, composer.content_from_context(contexto), composer.load_card_assets())
    assert preview == esperado


def test_ordem_do_worker_ilustracao_composicao_preview(db, clock, s3, openai, prompts, monkeypatch):
    passos: list[str] = []
    real_put, real_compose = storage.put_draft, composer.compose_card

    def put_espiao(generation_id, png, *, filename=storage.DRAFT_PREVIEW, client=None, bucket=None):
        passos.append(f"put:{filename}")
        return real_put(generation_id, png, filename=filename, client=client, bucket=bucket)

    def compose_espiao(*args, **kwargs):
        passos.append("compose")
        return real_compose(*args, **kwargs)

    monkeypatch.setattr(storage, "put_draft", put_espiao)
    monkeypatch.setattr(composer, "compose_card", compose_espiao)
    original_generate = openai_client.generate_image

    def generate_espiao(prompt, **kwargs):
        passos.append("openai")
        return original_generate(prompt, **kwargs)

    monkeypatch.setattr(openai_client, "generate_image", generate_espiao)
    gid = db.insert_generation(exercise_id=42, status="PENDING")
    jobs.run_generation_job({"generation_id": gid})
    assert passos == ["openai", "put:illustration.png", "compose", "put:preview.png"]
    assert db.generation(gid)["status"] == "GENERATED"


@pytest.mark.parametrize(
    "quebra",
    ["sem_primeiro_plano", "bug_no_compositor", "asset_ausente"],
)
def test_falha_de_composicao_vira_failed_e_guarda_a_ilustracao(db, clock, s3, prompts, monkeypatch, caplog, quebra):
    def gera(prompt, **kwargs):
        imagem = EMPTY_ILLUSTRATION if quebra == "sem_primeiro_plano" else PNG
        return openai_client.ImageResult(imagem, "gpt-image-1.5", "medium", "1024x1536", 1, 2, Decimal("0.05"), 1, 1.0)

    monkeypatch.setattr(openai_client, "generate_image", gera)
    if quebra == "bug_no_compositor":
        monkeypatch.setattr(composer, "compose_card", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    if quebra == "asset_ausente":
        def sem_assets():
            raise composer.CardCompositionError("Fonte ausente", reason="assets_missing")

        monkeypatch.setattr(composer, "load_card_assets", sem_assets)
    gid = db.insert_generation(exercise_id=42, status="PENDING", request_instruction="INSTRUCAO-SECRETA")
    with caplog.at_level(logging.INFO), pytest.raises(jobs.ExerciseCardJobError) as exc:
        jobs.run_generation_job({"generation_id": gid, "job_id": "j-1"})
    assert str(exc.value) == f"Card generation {gid} falhou: CARD_COMPOSITION_FAILED"
    row = db.generation(gid)
    assert (row["status"], row["error_code"], row["failed_at"]) == ("FAILED", "CARD_COMPOSITION_FAILED", T0)
    assert row["prompt_text"]  # o prompt usado fica para diagnostico (no banco)
    assert row["preview_s3_key"] is None
    # A ilustracao (paga) ficou guardada; o preview nao existe.
    assert s3.keys(BUCKET) == [storage.illustration_key(gid)]
    falhas = [json.loads(r.getMessage()) for r in caplog.records if "exercise_card.failed" in r.getMessage()]
    assert falhas and falhas[-1]["stage"] == "compose" and falhas[-1]["error_code"] == "CARD_COMPOSITION_FAILED"
    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert "INSTRUCAO-SECRETA" not in texto and "Supino reto" not in texto  # nunca logar o prompt


def test_codigo_de_falha_de_composicao_e_o_mesmo_nos_dois_modulos():
    assert svc.ERROR_CARD_COMPOSITION_FAILED == composer.CARD_COMPOSITION_FAILED == "CARD_COMPOSITION_FAILED"


def test_falha_ao_guardar_a_ilustracao_nem_compoe(db, clock, s3, openai, prompts, monkeypatch):
    chamadas: list = []
    monkeypatch.setattr(composer, "compose_card", lambda *a, **k: chamadas.append(1))
    gid = db.insert_generation(exercise_id=42, status="PENDING")
    s3.fail_put_keys.add(storage.illustration_key(gid))
    with pytest.raises(jobs.ExerciseCardJobError):
        jobs.run_generation_job({"generation_id": gid})
    assert db.generation(gid)["error_code"] == storage.S3_UPLOAD_FAILED
    assert chamadas == [] and len(openai) == 1


def test_redelivery_nao_chama_a_openai(worker, db, openai, dispatched):
    gid = worker()
    assert len(openai) == 1
    resultado = jobs.run_generation_job(dispatched[-1])  # o mesmo evento, entregue de novo
    assert resultado["status"] == "skipped"
    assert resultado["generation_status"] == "GENERATED"
    assert len(openai) == 1
    assert db.generation(gid)["status"] == "GENERATED"


def test_reserva_perdida_para_o_sweep_nao_chama_a_openai(db, clock, openai, prompts):
    gid = db.insert_generation(exercise_id=42, status="FAILED", error_code="WORKER_LOST")
    assert jobs.run_generation_job({"generation_id": gid})["status"] == "skipped"
    assert openai == []


def test_reserva_e_um_update_condicional_atomico(db, clock):
    gid = db.insert_generation(exercise_id=42, status="PENDING")
    with db.transaction() as conn:
        with conn.cursor() as cursor:
            assert svc.reserve_for_generation(cursor, gid, T0) is True
            assert svc.reserve_for_generation(cursor, gid, T0) is False
    assert any("WHERE id = %s AND status = %s" in sql for sql in [s.replace("?", "%s") for s in db.statements])


def test_resultado_descartado_se_o_sweep_fechou_a_linha_no_meio(db, clock, s3, prompts, monkeypatch):
    gid = db.insert_generation(exercise_id=42, status="PENDING", model="gpt-image-1.5", quality="medium", size="1024x1536")

    def lenta(prompt, **kwargs):
        db.raw.execute("UPDATE exercise_card_generations SET status = 'FAILED', error_code = 'WORKER_LOST' WHERE id = ?", (gid,))
        return openai_client.ImageResult(PNG, "gpt-image-1.5", "medium", "1024x1536", None, None, None, 1, 1.0)

    monkeypatch.setattr(openai_client, "generate_image", lenta)
    assert jobs.run_generation_job({"generation_id": gid})["status"] == "discarded"
    assert db.generation(gid)["error_code"] == "WORKER_LOST"


@pytest.mark.parametrize(
    "erro, code",
    [
        (openai_client.OpenAIGenerationError("limite", status=429), "OPENAI_RATE_LIMIT"),
        (openai_client.OpenAIGenerationError("timeout", code="OPENAI_TIMEOUT"), "OPENAI_TIMEOUT"),
        (openai_client.OpenAIGenerationError("moderacao", status=400), "OPENAI_INVALID_REQUEST"),
        (RuntimeError("bug"), "INTERNAL_ERROR"),
    ],
)
def test_falha_da_openai_vira_failed_com_codigo(db, clock, s3, prompts, monkeypatch, erro, code):
    gid = db.insert_generation(exercise_id=42, status="PENDING")

    def explode(prompt, **kwargs):
        raise erro

    monkeypatch.setattr(openai_client, "generate_image", explode)
    with pytest.raises(jobs.ExerciseCardJobError) as exc:
        jobs.run_generation_job({"generation_id": gid})
    assert str(exc.value) == f"Card generation {gid} falhou: {code}"
    row = db.generation(gid)
    assert (row["status"], row["error_code"], row["failed_at"]) == ("FAILED", code, T0)
    assert row["prompt_text"]  # o prompt usado fica para diagnostico
    assert s3.keys() == []


def test_falha_do_s3_no_rascunho_vira_failed_com_codigo_proprio(db, clock, s3, openai, prompts):
    gid = db.insert_generation(exercise_id=42, status="PENDING")
    s3.fail_put_keys.add(storage.draft_key(gid))
    with pytest.raises(jobs.ExerciseCardJobError):
        jobs.run_generation_job({"generation_id": gid})
    assert db.generation(gid)["error_code"] == storage.S3_UPLOAD_FAILED
    assert len(openai) == 1
    assert s3.keys(BUCKET) == [storage.illustration_key(gid)]  # a ilustracao paga ficou


def test_exercicio_apagado_falha_sem_chamar_a_openai(db, clock, openai, prompts):
    db.add_exercise(99, "Some")
    gid = db.insert_generation(exercise_id=99, status="PENDING")
    with pytest.raises(jobs.ExerciseCardJobError):
        jobs.run_generation_job({"generation_id": gid})
    assert db.generation(gid)["error_code"] == svc.ERROR_EXERCISE_NOT_FOUND
    assert openai == []


def test_evento_sem_generation_id_e_erro(db):
    with pytest.raises(ValueError):
        jobs.run_generation_job({"job_id": "x"})


def test_falha_no_disparo_vira_failed_e_502(db, clock, monkeypatch):
    monkeypatch.setattr(async_jobs, "create_job", lambda job_type, request=None, *, owner_user_id: "job-x")

    def sem_lambda(env_var, payload):
        raise KeyError(env_var)

    falhos: list = []
    monkeypatch.setattr(async_jobs, "invoke_async", sem_lambda)
    monkeypatch.setattr(async_jobs, "mark_failed", lambda job_id, error: falhos.append(job_id))
    row, _ = svc.create_generation(exercise_id=42, user_id=ADMIN, idempotency_key=_key())
    with pytest.raises(svc.ExerciseCardError) as exc:
        jobs.dispatch(row)
    assert (exc.value.http_status, exc.value.code) == (502, svc.CODE_DISPATCH_FAILED)
    gravada = db.generation(row["id"])
    assert (gravada["status"], gravada["error_code"], gravada["job_id"]) == ("FAILED", "DISPATCH_FAILED", "job-x")
    assert falhos == ["job-x"]


# ---------------------------------------------------------------------------
# Aprovar / publicar
# ---------------------------------------------------------------------------


def test_aprovar_publica_v1_com_os_3_objetos_e_o_ponteiro(worker, db, s3, clock, monkeypatch):
    gid = worker()
    clock.advance(minutes=1)
    codificados: list[bytes] = []
    real_encode = processor.encode_card
    monkeypatch.setattr(processor, "encode_card", lambda png: codificados.append(png) or real_encode(png))
    monkeypatch.setattr(processor, "compose_logo", lambda *a: pytest.fail("a aprovacao nao recompoe o logo"))
    row, card = svc.approve_generation(exercise_id=42, generation_id=gid, user_id=ADMIN)
    preview = s3.body(BUCKET, storage.draft_key(gid))
    # Publica EXATAMENTE o preview revisado (pixels do card.png = do preview).
    assert codificados == [preview]
    publicado = _image(s3.body(BUCKET, "exercise-cards/42/v1/card.png"))
    assert publicado.tobytes() == _image(preview).tobytes()
    assert row["status"] == "PUBLISHED" and row["version"] == 1
    assert (row["approved_by"], row["approved_at"], row["published_at"]) == (ADMIN, clock.now, clock.now)
    assert row["error_code"] is None
    for key in ("exercise-cards/42/v1/card.webp", "exercise-cards/42/v1/card.png", "exercise-cards/42/v1/thumb.webp"):
        assert s3.objects[(BUCKET, key)].cache_control == storage.IMMUTABLE_CACHE_CONTROL
    catalogo = db.catalog(42)
    assert (catalogo["card_generation_id"], catalogo["card_version"], catalogo["card_published_at"]) == (gid, 1, clock.now)
    assert card == {
        "url": "https://app.musclefit.com.br/exercise-cards/42/v1/card.webp",
        "thumbnail_url": "https://app.musclefit.com.br/exercise-cards/42/v1/thumb.webp",
        "png_url": "https://app.musclefit.com.br/exercise-cards/42/v1/card.png",
        "version": 1,
        "published_at": "2026-10-03T12:01:00Z",
    }
    assert svc.published_card(42) == card


def test_segunda_publicacao_marca_a_primeira_superseded(worker, db, s3, clock):
    primeira = worker()
    svc.approve_generation(exercise_id=42, generation_id=primeira, user_id=ADMIN)
    segunda = worker()
    row, card = svc.approve_generation(exercise_id=42, generation_id=segunda, user_id=ADMIN)
    assert row["version"] == 2 and card["version"] == 2
    assert db.generation(primeira)["status"] == "SUPERSEDED"
    assert db.generation(primeira)["version"] == 1
    assert db.catalog(42)["card_version"] == 2


def test_geracao_v1_nao_e_aprovavel(db, clock, s3):
    """V1: a IA desenhava o card inteiro e o logo entrava na aprovacao -
    publicar o preview cru sairia sem logo. 409 e nada muda (nem S3)."""
    gid = db.insert_generation(
        exercise_id=42, status="GENERATED", prompt_version=V1, text_level="standard",
        preview_s3_key="exercise-card-drafts/1/preview.png",
    )
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.approve_generation(exercise_id=42, generation_id=gid, user_id=ADMIN)
    assert (exc.value.http_status, exc.value.code, exc.value.generation_id) == (409, svc.CODE_NOT_REVIEWABLE, gid)
    assert db.generation(gid)["status"] == "GENERATED"
    assert s3.calls == []
    assert db.catalog(42)["card_version"] is None
    assert V1 in svc.LEGACY_UNCOMPOSED_PROMPT_VERSIONS


def test_geracao_v1_ainda_pode_ser_rejeitada_e_regenerada_em_v2(db, clock, dispatched):
    gid = db.insert_generation(exercise_id=42, status="GENERATED", prompt_version=V1, text_level="full")
    filha, pai, criada = svc.regenerate_generation(
        exercise_id=42, parent_generation_id=gid, user_id=ADMIN, idempotency_key=_key()
    )
    assert criada and pai["status"] == "REJECTED"
    # O nivel `full` do pai V1 nao existe mais: a filha nasce `minimal`, em V2.
    assert (filha["text_level"], filha["prompt_version"]) == ("minimal", card_prompt.PROMPT_VERSION)


@pytest.mark.parametrize("status", ["PENDING", "GENERATING", "REJECTED", "FAILED", "PUBLISHED", "APPROVING"])
def test_so_generated_e_aprovavel(db, clock, s3, status):
    gid = db.insert_generation(exercise_id=42, status=status, updated_at=T0, created_at=T0, started_at=T0)
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.approve_generation(exercise_id=42, generation_id=gid, user_id=ADMIN)
    assert (exc.value.http_status, exc.value.code) == (409, svc.CODE_NOT_REVIEWABLE)


def test_outra_aprovacao_em_curso_no_mesmo_exercicio_e_409(db, clock, s3):
    db.insert_generation(exercise_id=42, status="APPROVING", updated_at=T0)
    gid = db.insert_generation(exercise_id=42, status="GENERATED", preview_s3_key="exercise-card-drafts/9/preview.png")
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.approve_generation(exercise_id=42, generation_id=gid, user_id=ADMIN)
    assert (exc.value.http_status, exc.value.code) == (409, svc.CODE_IN_PROGRESS)
    assert db.generation(gid)["status"] == "GENERATED"


def test_falha_de_processamento_volta_a_generated_e_502(worker, db, s3, monkeypatch):
    gid = worker()

    def recusa(png):
        raise processor.ImageProcessingError("proporcao errada", reason="ratio_out_of_range")

    monkeypatch.setattr(processor, "encode_card", recusa)
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.approve_generation(exercise_id=42, generation_id=gid, user_id=ADMIN)
    assert (exc.value.http_status, exc.value.code) == (502, svc.CODE_IMAGE_PROCESSING_FAILED)
    row = db.generation(gid)
    assert (row["status"], row["error_code"], row["version"]) == ("GENERATED", "IMAGE_PROCESSING_FAILED", None)
    assert db.catalog(42)["card_version"] is None


def test_falha_de_upload_volta_a_generated_sem_ponteiro_e_permite_tentar_de_novo(worker, db, s3):
    gid = worker()
    s3.fail_put_keys.add("exercise-cards/42/v1/thumb.webp")  # o 3o upload
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.approve_generation(exercise_id=42, generation_id=gid, user_id=ADMIN)
    assert (exc.value.http_status, exc.value.code) == (502, svc.CODE_S3_UPLOAD_FAILED)
    assert db.generation(gid)["status"] == "GENERATED"
    # card_version so e gravado DEPOIS dos 3 uploads.
    assert db.catalog(42)["card_version"] is None
    s3.fail_put_keys.clear()
    row, _ = svc.approve_generation(exercise_id=42, generation_id=gid, user_id=ADMIN)
    assert row["status"] == "PUBLISHED" and row["version"] == 1 and row["error_code"] is None


def test_falha_ao_ler_o_rascunho_e_502_s3_read_failed(worker, db, s3):
    gid = worker()
    s3.fail_get_keys.add(storage.draft_key(gid))
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.approve_generation(exercise_id=42, generation_id=gid, user_id=ADMIN)
    assert (exc.value.http_status, exc.value.code) == (502, svc.CODE_S3_READ_FAILED)
    assert db.generation(gid)["status"] == "GENERATED"


def test_rascunho_apagado_pelo_lifecycle_e_410_draft_expired(worker, db, s3, clock, dispatched):
    """M72: objeto inexistente (NoSuchKey - o lifecycle apagou antes do
    relogio da api marcar) e 410, nao 502. Volta a GENERATED com o
    error_code, que vira `draft_expired`, e regenerar continua valendo."""
    gid = worker()
    del s3.objects[(BUCKET, storage.draft_key(gid))]
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.approve_generation(exercise_id=42, generation_id=gid, user_id=ADMIN)
    assert (exc.value.http_status, exc.value.code, exc.value.generation_id) == (410, "DRAFT_EXPIRED", gid)
    assert exc.value.message == "O rascunho expirou (mais de 7 dias). Gere o card de novo."
    row = db.generation(gid)
    assert (row["status"], row["error_code"], row["version"]) == ("GENERATED", "DRAFT_EXPIRED", None)
    assert db.catalog(42)["card_version"] is None
    data = svc.serialize(row, presign=lambda key: pytest.fail("expirado nao assina preview"))
    assert data["draft_expired"] is True and data["preview_url"] is None
    filha, pai, criada = svc.regenerate_generation(
        exercise_id=42, parent_generation_id=gid, user_id=ADMIN, idempotency_key=_key()
    )
    assert criada and pai["status"] == "REJECTED" and filha["status"] == "PENDING"


def test_rascunho_vencido_pelo_relogio_e_410_sem_tocar_no_s3(worker, db, s3, clock):
    """M72: a regra e o RELOGIO (generated_at + DRAFT_TTL_DAYS), nao o objeto
    sumir - em DEV o LocalStack nao aplica lifecycle e o resultado e o mesmo.
    Nada muda na linha e rejeitar continua valendo."""
    gid = worker()
    clock.advance(days=storage.DRAFT_TTL_DAYS, seconds=-1)
    assert svc.is_draft_expired(db.generation(gid)) is False
    clock.advance(seconds=1)
    leituras_antes = len(s3.calls_of("get_object"))
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.approve_generation(exercise_id=42, generation_id=gid, user_id=ADMIN)
    assert (exc.value.http_status, exc.value.code) == (410, svc.CODE_DRAFT_EXPIRED)
    assert len(s3.calls_of("get_object")) == leituras_antes  # o objeto ainda existe; nem foi lido
    row = db.generation(gid)
    assert (row["status"], row["error_code"]) == ("GENERATED", None)
    rejeitada = svc.reject_generation(exercise_id=42, generation_id=gid, user_id=ADMIN)
    assert rejeitada["status"] == "REJECTED"


# ---------------------------------------------------------------------------
# Rejeitar / regenerar
# ---------------------------------------------------------------------------


def test_rejeitar_grava_motivo_e_so_vale_para_generated(worker, db, clock):
    gid = worker()
    row = svc.reject_generation(exercise_id=42, generation_id=gid, user_id=ADMIN, reason="  braço torto ")
    assert (row["status"], row["reject_reason"], row["rejected_at"]) == ("REJECTED", "braço torto", T0)
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.reject_generation(exercise_id=42, generation_id=gid, user_id=ADMIN)
    assert (exc.value.http_status, exc.value.code) == (409, svc.CODE_NOT_REVIEWABLE)


def test_motivo_longo_demais_e_422(worker, db):
    gid = worker()
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.reject_generation(exercise_id=42, generation_id=gid, user_id=ADMIN, reason="x" * (svc.MAX_REASON_CHARS + 1))
    assert exc.value.code == svc.CODE_REASON_TOO_LONG


def test_regenerar_de_generated_rejeita_o_pai_na_mesma_transacao(worker, db, dispatched):
    pai = worker()
    filha, pai_row, criada = svc.regenerate_generation(
        exercise_id=42, parent_generation_id=pai, user_id=ADMIN, idempotency_key=_key(), instruction="sem banco"
    )
    assert criada
    assert (pai_row["status"], pai_row["reject_reason"]) == ("REJECTED", svc.REJECT_REASON_REGENERATED)
    assert filha["status"] == "PENDING" and filha["parent_generation_id"] == pai
    assert filha["text_level"] == "minimal"  # herdado do pai
    assert filha["request_instruction"] == "sem banco"


def test_regenerar_com_nivel_fora_da_v2_e_422(worker, db):
    pai = worker()
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.regenerate_generation(
            exercise_id=42, parent_generation_id=pai, user_id=ADMIN, idempotency_key=_key(), text_level="standard"
        )
    assert (exc.value.http_status, exc.value.code) == (422, svc.CODE_INVALID_TEXT_LEVEL)


def test_regenerado_nunca_passa_o_codigo_interno_ao_prompt(worker, db, prompts, dispatched):
    """Anexo C, item 12: `parent_reject_reason` so com motivo HUMANO."""
    pai = worker()
    filha, _, _ = svc.regenerate_generation(
        exercise_id=42, parent_generation_id=pai, user_id=ADMIN, idempotency_key=_key(), instruction="mais perto"
    )
    jobs.dispatch(filha)
    jobs.run_generation_job(dispatched[-1])
    assert prompts[-1]["parent_reject_reason"] is None
    assert prompts[-1]["instruction"] == "mais perto"
    exigir("regenerated" not in db.generation(filha["id"])["prompt_text"], "código interno fora do prompt")


def test_motivo_humano_do_pai_rejeitado_vai_ao_prompt_da_filha(worker, db, prompts, dispatched):
    pai = worker()
    svc.reject_generation(exercise_id=42, generation_id=pai, user_id=ADMIN, reason="halter com número")
    filha, pai_row, _ = svc.regenerate_generation(
        exercise_id=42, parent_generation_id=pai, user_id=ADMIN, idempotency_key=_key()
    )
    assert pai_row["reject_reason"] == "halter com número"  # REJECTED continua com o motivo humano
    jobs.dispatch(filha)
    jobs.run_generation_job(dispatched[-1])
    assert prompts[-1]["parent_reject_reason"] == "halter com número"


def test_filtro_do_motivo_humano():
    assert svc.filter_human_reason("regenerated") is None
    assert svc.filter_human_reason(" Regenerated ") is None
    assert svc.filter_human_reason("") is None
    assert svc.filter_human_reason(None) is None
    assert svc.filter_human_reason("pé fora da plataforma") == "pé fora da plataforma"


@pytest.mark.parametrize("status", ["PENDING", "GENERATING", "APPROVING"])
def test_pai_vivo_nao_e_regeneravel(db, clock, status):
    pai = db.insert_generation(exercise_id=42, status=status, created_at=T0, updated_at=T0, started_at=T0)
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.regenerate_generation(exercise_id=42, parent_generation_id=pai, user_id=ADMIN, idempotency_key=_key())
    assert (exc.value.http_status, exc.value.code) == (409, svc.CODE_NOT_REGENERABLE)


@pytest.mark.parametrize("status", ["REJECTED", "FAILED", "PUBLISHED", "SUPERSEDED"])
def test_pai_fechado_e_regeneravel_e_nao_muda(db, clock, status):
    pai = db.insert_generation(exercise_id=42, status=status)
    filha, pai_row, criada = svc.regenerate_generation(
        exercise_id=42, parent_generation_id=pai, user_id=ADMIN, idempotency_key=_key()
    )
    assert criada and filha["parent_generation_id"] == pai
    assert pai_row["status"] == status


def test_regenerar_respeita_os_limites(db, clock, monkeypatch):
    monkeypatch.setenv(svc.ENV_MAX_PER_DAY, "1")
    pai = db.insert_generation(exercise_id=42, status="GENERATED", created_by=ADMIN, created_at=T0)
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.regenerate_generation(exercise_id=42, parent_generation_id=pai, user_id=ADMIN, idempotency_key=_key())
    assert exc.value.code == svc.CODE_DAILY_LIMIT
    assert db.generation(pai)["status"] == "GENERATED"  # nada mudou: rollback


def test_replay_do_regenerar_devolve_a_mesma_filha(worker, db):
    pai = worker()
    key = _key()
    filha, _, criada = svc.regenerate_generation(exercise_id=42, parent_generation_id=pai, user_id=ADMIN, idempotency_key=key)
    replay, pai_row, de_novo = svc.regenerate_generation(
        exercise_id=42, parent_generation_id=pai, user_id=ADMIN, idempotency_key=key
    )
    assert criada and not de_novo
    assert replay["id"] == filha["id"] and pai_row["id"] == pai


# ---------------------------------------------------------------------------
# Anti-IDOR
# ---------------------------------------------------------------------------


def test_geracao_de_outro_exercicio_e_404_em_tudo(worker, db):
    gid = worker(exercise_id=43)
    chamadas = [
        lambda: svc.get_generation(exercise_id=42, generation_id=gid),
        lambda: svc.approve_generation(exercise_id=42, generation_id=gid, user_id=ADMIN),
        lambda: svc.reject_generation(exercise_id=42, generation_id=gid, user_id=ADMIN),
        lambda: svc.regenerate_generation(exercise_id=42, parent_generation_id=gid, user_id=ADMIN, idempotency_key=_key()),
    ]
    for chamada in chamadas:
        with pytest.raises(svc.ExerciseCardError) as exc:
            chamada()
        assert (exc.value.http_status, exc.value.code) == (404, svc.CODE_GENERATION_NOT_FOUND)
    assert db.generation(gid)["status"] == "GENERATED"


# ---------------------------------------------------------------------------
# Serializacao
# ---------------------------------------------------------------------------


def _row(**fields) -> dict:
    base = {column: None for column in svc.COLUMNS}
    base.update({"id": 5, "exercise_id": 42, "created_by": ADMIN, "created_at": T0, "updated_at": T0})
    base.update(fields)
    return base


class _Assinatura:
    def __init__(self, url):
        self.url = url
        self.expires_at = T0 + timedelta(minutes=15)


@pytest.mark.parametrize("status", ["GENERATED", "APPROVING"])
def test_preview_so_em_generated_e_approving(status):
    data = svc.serialize(
        _row(status=status, preview_s3_key="exercise-card-drafts/5/preview.png"),
        presign=lambda key: _Assinatura(f"https://s3/{key}?sig"),
    )
    assert data["preview_url"] == "https://s3/exercise-card-drafts/5/preview.png?sig"
    assert data["preview_expires_at"] == "2026-10-03T12:15:00Z"
    assert data["card"] is None


@pytest.mark.parametrize("status", ["PENDING", "GENERATING", "REJECTED", "FAILED", "PUBLISHED", "SUPERSEDED"])
def test_sem_preview_fora_da_revisao(status):
    assinou: list = []
    data = svc.serialize(
        _row(status=status, preview_s3_key="exercise-card-drafts/5/preview.png"),
        presign=lambda key: assinou.append(key),
    )
    assert data["preview_url"] is None and data["preview_expires_at"] is None
    assert assinou == []


@pytest.mark.parametrize("status", ["PUBLISHED", "SUPERSEDED"])
def test_card_so_em_published_e_superseded(status):
    data = svc.serialize(
        _row(
            status=status,
            version=3,
            card_s3_key="exercise-cards/42/v3/card.webp",
            card_png_s3_key="exercise-cards/42/v3/card.png",
            thumb_s3_key="exercise-cards/42/v3/thumb.webp",
        )
    )
    assert data["card"] == {
        "url": "https://app.musclefit.com.br/exercise-cards/42/v3/card.webp",
        "png_url": "https://app.musclefit.com.br/exercise-cards/42/v3/card.png",
        "thumbnail_url": "https://app.musclefit.com.br/exercise-cards/42/v3/thumb.webp",
        "version": 3,
    }


def test_serializacao_sem_chaves_s3_com_prompt_opcional_e_datas_em_utc():
    row = _row(status="GENERATED", prompt_text="PROMPT", cost_usd=Decimal("0.0500"), preview_s3_key="exercise-card-drafts/5/preview.png")
    completo = svc.serialize(row, presign=lambda key: _Assinatura("u"))
    listagem = svc.serialize(row, include_prompt=False, presign=lambda key: _Assinatura("u"))
    assert completo["prompt_text"] == "PROMPT"
    assert "prompt_text" not in listagem
    assert completo["cost_usd"] == 0.05
    assert completo["created_at"] == "2026-10-03T12:00:00Z"
    assert not any(key.endswith("s3_key") for key in completo)
    json.dumps(completo)  # JSON puro


@pytest.mark.parametrize("status", ["GENERATED", "APPROVING"])
def test_validade_do_rascunho_e_generated_at_mais_7_dias(status):
    row = _row(status=status, generated_at=T0, preview_s3_key="exercise-card-drafts/5/preview.png")
    assinar = lambda key: _Assinatura(f"https://s3/{key}?sig")  # noqa: E731
    vence = T0 + timedelta(days=7)
    antes = svc.serialize(row, presign=assinar, now=vence - timedelta(seconds=1))
    assert antes["draft_expires_at"] == "2026-10-10T12:00:00Z"
    assert antes["draft_expired"] is False and antes["preview_url"]
    depois = svc.serialize(row, presign=lambda key: pytest.fail("expirado nao assina"), now=vence)
    assert depois["draft_expires_at"] == "2026-10-10T12:00:00Z"
    assert depois["draft_expired"] is True and depois["preview_url"] is None


@pytest.mark.parametrize("status", ["PENDING", "GENERATING", "REJECTED", "FAILED", "PUBLISHED", "SUPERSEDED"])
def test_validade_do_rascunho_so_em_revisao(status):
    row = _row(status=status, generated_at=T0, preview_s3_key="exercise-card-drafts/5/preview.png",
               error_code="DRAFT_EXPIRED")
    data = svc.serialize(row, now=T0 + timedelta(days=30))
    assert data["draft_expires_at"] is None and data["draft_expired"] is False


def test_validade_sem_generated_at_ou_sem_rascunho_nao_expira():
    sem_data = _row(status="GENERATED", preview_s3_key="exercise-card-drafts/5/preview.png")
    sem_rascunho = _row(status="GENERATED", generated_at=T0)
    for row in (sem_data, sem_rascunho):
        assert svc.draft_expires_at(row) is None
        assert svc.is_draft_expired(row, now=T0 + timedelta(days=30)) is False


def test_ttl_do_rascunho_e_o_mesmo_do_lifecycle_do_s3():
    """`DRAFT_TTL_DAYS` espelha `expiration { days = N }` da regra
    `exercise-card-drafts-expire-7d` em infra/s3_uploads.tf."""
    import re
    from pathlib import Path

    tf = (Path(__file__).resolve().parents[3] / "infra" / "s3_uploads.tf").read_text(encoding="utf-8")
    regra = tf[tf.index('id     = "exercise-card-drafts-expire-7d"'):]
    dias = re.search(r"expiration\s*\{\s*days\s*=\s*(\d+)", regra)
    assert dias and int(dias.group(1)) == storage.DRAFT_TTL_DAYS


def test_falha_ao_assinar_nao_derruba_a_resposta():
    def explode(key):
        raise RuntimeError("sem credencial")

    data = svc.serialize(_row(status="GENERATED", preview_s3_key="exercise-card-drafts/5/preview.png"), presign=explode)
    assert data["preview_url"] is None and data["status"] == "GENERATED"


def test_listagem_nao_le_prompt_text_do_banco(worker, db):
    worker()
    db.statements.clear()
    rows = svc.list_generations(exercise_id=42)
    assert len(rows) == 1 and "prompt_text" not in rows[0]
    select = [sql for sql in db.statements if sql.startswith("SELECT") and "ORDER BY id DESC" in sql]
    assert select and "prompt_text" not in select[0]


def test_card_publicado_inexistente_e_none_e_exercicio_inexistente_e_404(db, clock):
    assert svc.published_card(42) is None
    with pytest.raises(svc.ExerciseCardError) as exc:
        svc.published_card(999)
    assert exc.value.http_status == 404


def test_card_publicado_nao_depende_da_flag(worker, db, monkeypatch):
    gid = worker()
    svc.approve_generation(exercise_id=42, generation_id=gid, user_id=ADMIN)
    monkeypatch.setenv(svc.ENV_ENABLED, "false")
    assert svc.published_card(42)["version"] == 1


# ---------------------------------------------------------------------------
# Observabilidade: nada de prompt, instrucao, motivo ou chave nos logs
# ---------------------------------------------------------------------------


def test_eventos_nao_carregam_prompt_instrucao_motivo_nem_chave(worker, db, dispatched, monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-CHAVE-SECRETA")
    with caplog.at_level(logging.DEBUG):
        pai = worker(instruction="INSTRUCAO-SECRETA-1")
        svc.reject_generation(exercise_id=42, generation_id=pai, user_id=ADMIN, reason="MOTIVO-SECRETO-2")
        filha, _, _ = svc.regenerate_generation(
            exercise_id=42, parent_generation_id=pai, user_id=ADMIN, idempotency_key=_key(), instruction="INSTRUCAO-SECRETA-3"
        )
        jobs.dispatch(filha)
        jobs.run_generation_job(dispatched[-1])
        svc.approve_generation(exercise_id=42, generation_id=filha["id"], user_id=ADMIN)

    prompt_real = db.generation(filha["id"])["prompt_text"]
    exigir("MOTIVO-SECRETO-2" in prompt_real and "INSTRUCAO-SECRETA-3" in prompt_real, "feedback no prompt")  # ...
    texto = "\n".join(record.getMessage() for record in caplog.records)
    for segredo in ("INSTRUCAO-SECRETA-1", "MOTIVO-SECRETO-2", "INSTRUCAO-SECRETA-3", "CHAVE-SECRETA", "Supino reto"):
        assert segredo not in texto  # ...mas nunca aos logs
    eventos = {json.loads(r.getMessage())["event"] for r in caplog.records if r.name == "musclefit.events"}
    assert {
        "exercise_card.requested",
        "exercise_card.started",
        "exercise_card.generated",
        "exercise_card.rejected",
        "exercise_card.regenerated",
        "exercise_card.published",
    } <= eventos


def test_evento_de_falha_tem_so_codigo_e_etapa(db, clock, prompts, monkeypatch, caplog):
    gid = db.insert_generation(exercise_id=42, status="PENDING", request_instruction="INSTRUCAO-SECRETA")

    def explode(prompt, **kwargs):
        raise openai_client.OpenAIGenerationError("OpenAI Images respondeu 400: moderation_blocked: recusado", status=400)

    monkeypatch.setattr(openai_client, "generate_image", explode)
    with caplog.at_level(logging.INFO, logger="musclefit.events"), pytest.raises(jobs.ExerciseCardJobError):
        jobs.run_generation_job({"generation_id": gid, "job_id": "j-1"})
    falhas = [json.loads(r.getMessage()) for r in caplog.records if "exercise_card.failed" in r.getMessage()]
    assert falhas == [
        {"event": "exercise_card.failed", "generation_id": gid, "exercise_id": 42, "job_id": "j-1",
         "stage": "openai", "error_code": "OPENAI_INVALID_REQUEST"}
    ]
    assert "moderation_blocked" in db.generation(gid)["error_message"]  # o detalhe fica no banco

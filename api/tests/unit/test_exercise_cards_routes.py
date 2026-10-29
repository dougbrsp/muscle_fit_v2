"""Cards ilustrados - contrato HTTP (`app/exercise_cards.py`), autorizacao,
flag, anti-IDOR, dispatch por `job_kind` na Lambda e `features` do
`/auth/me`. Sem rede e sem MySQL (mesmos dubles de
`test_exercise_card_service.py`, reaproveitados por import).

Rodar: cd api && python -m pytest tests/unit/test_exercise_cards_routes.py
"""

from __future__ import annotations

import io
from contextlib import contextmanager

import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth
from app import exercise_card_jobs as jobs
from app import exercise_card_processor as processor
from app import exercise_card_service as svc
from app import exercise_card_storage as storage
from app import exercise_cards
from app.security import Identity, get_current_identity
from tests.unit.test_exercise_card_service import (  # noqa: F401 - fixtures reaproveitadas
    ADMIN,
    BUCKET,
    PNG,
    V1,
    _env,
    _key,
    clock,
    db,
    dispatched,
    openai,
    prompts,
    s3,
    worker,
)

REGULAR = 8


def _client(identity: Identity | None) -> TestClient:
    app = FastAPI()
    app.include_router(exercise_cards.router)
    if identity is not None:
        app.dependency_overrides[get_current_identity] = lambda: identity
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin(db, clock, s3, openai, prompts, dispatched) -> TestClient:  # noqa: F811 - fixture do pytest importada (falso positivo)
    return _client(Identity(user_id=ADMIN, role="admin", source="session"))


@pytest.fixture
def regular(db, clock) -> TestClient:  # noqa: F811 - fixture do pytest importada (falso positivo)
    return _client(Identity(user_id=REGULAR, role="regular", source="session"))


def _rotas_de_geracao(gid: int = 1):
    base = "/exercises/42/card-generations"
    return [
        ("post", base, {"idempotency_key": _key()}),
        ("get", base, None),
        ("get", f"{base}/{gid}", None),
        ("post", f"{base}/{gid}/approve", None),
        ("post", f"{base}/{gid}/reject", {}),
        ("post", f"{base}/{gid}/regenerate", {"idempotency_key": _key()}),
    ]


def _chamar(client: TestClient, metodo: str, path: str, body):
    if metodo == "get":
        return client.get(path)
    return client.post(path, json=body if body is not None else None)


# ---------------------------------------------------------------------------
# Autorizacao e flag
# ---------------------------------------------------------------------------


def test_nao_admin_recebe_403_em_toda_rota_de_geracao(regular, db):  # noqa: F811 - fixture do pytest importada (falso positivo)
    gid = db.insert_generation(exercise_id=42, status="GENERATED")
    for metodo, path, body in _rotas_de_geracao(gid):
        resposta = _chamar(regular, metodo, path, body)
        assert resposta.status_code == 403, (metodo, path, resposta.text)
    assert db.raw.execute("SELECT status FROM exercise_card_generations").fetchall()[0][0] == "GENERATED"


def test_flag_desligada_e_503_em_toda_rota_de_geracao(admin, db, monkeypatch):  # noqa: F811 - fixture do pytest importada (falso positivo)
    monkeypatch.setenv(svc.ENV_ENABLED, "false")
    gid = db.insert_generation(exercise_id=42, status="GENERATED")
    for metodo, path, body in _rotas_de_geracao(gid):
        resposta = _chamar(admin, metodo, path, body)
        assert resposta.status_code == 503, (metodo, path, resposta.text)
        assert resposta.json()["detail"]["error"] == "exercise_card_generation_disabled"


def test_sem_credencial_e_401(db):  # noqa: F811 - fixture do pytest importada (falso positivo)
    cliente = _client(None)
    assert cliente.get("/exercises/42/card").status_code == 401
    assert cliente.post("/exercises/42/card-generations", json={"idempotency_key": _key()}).status_code == 401


def test_card_publicado_e_liberado_para_qualquer_autenticado_mesmo_com_flag_desligada(regular, monkeypatch):
    monkeypatch.setenv(svc.ENV_ENABLED, "false")
    resposta = regular.get("/exercises/42/card")
    assert resposta.status_code == 200
    assert resposta.json() == {"exercise_id": 42, "exercise_card": None}
    assert regular.get("/exercises/999/card").status_code == 404


# ---------------------------------------------------------------------------
# Fluxo pelo HTTP
# ---------------------------------------------------------------------------


def test_criar_responde_202_e_replay_200(admin, dispatched):  # noqa: F811 - fixture do pytest importada (falso positivo)
    key = _key()
    criada = admin.post("/exercises/42/card-generations", json={"idempotency_key": key, "text_level": "minimal"})
    assert criada.status_code == 202, criada.text
    generation = criada.json()["generation"]
    assert (generation["status"], generation["text_level"], generation["job_id"]) == ("PENDING", "minimal", "job-1")
    assert generation["prompt_version"] == "MUSCLEFIT_EXERCISE_CARD_V2_ILLUSTRATION"
    assert generation["preview_url"] is None and generation["card"] is None
    replay = admin.post("/exercises/42/card-generations", json={"idempotency_key": key})
    assert replay.status_code == 200
    assert replay.json()["generation"]["id"] == generation["id"]
    assert len(dispatched) == 1  # o replay nao dispara de novo


def test_erros_de_negocio_no_formato_detail_error(admin, db):  # noqa: F811 - fixture do pytest importada (falso positivo)
    db.insert_generation(exercise_id=42, status="GENERATING", started_at=svc.utcnow())
    conflito = admin.post("/exercises/42/card-generations", json={"idempotency_key": _key()})
    assert conflito.status_code == 409
    assert conflito.json()["detail"]["error"] == "exercise_card_generation_in_progress"
    assert conflito.json()["detail"]["generation_id"] == 1
    invalido = admin.post("/exercises/43/card-generations", json={"idempotency_key": "x"})
    assert (invalido.status_code, invalido.json()["detail"]["error"]) == (422, "invalid_idempotency_key")
    inexistente = admin.post("/exercises/999/card-generations", json={"idempotency_key": _key()})
    assert (inexistente.status_code, inexistente.json()["detail"]["error"]) == (404, "exercise_not_found")


def _image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def test_geracao_revisao_e_publicacao_pelo_http(admin, db, s3, dispatched, monkeypatch):  # noqa: F811 - fixture do pytest importada (falso positivo)
    gid = admin.post("/exercises/42/card-generations", json={"idempotency_key": _key()}).json()["generation"]["id"]
    jobs.run_generation_job(dispatched[-1])

    lida = admin.get(f"/exercises/42/card-generations/{gid}").json()["generation"]
    assert lida["status"] == "GENERATED"
    # O preview assinado e o do CARD COMPOSTO (nao a ilustracao crua).
    assert lida["preview_url"] == f"https://fake-s3.local/musclefit-uploads/exercise-card-drafts/{gid}/preview.png?X-Fake-Expires=900"
    preview = s3.body(BUCKET, storage.draft_key(gid))
    assert _image(preview).size == (1024, 1536) and preview != PNG
    assert (lida["width"], lida["height"], lida["bytes"]) == (1024, 1536, len(preview))
    assert lida["preview_expires_at"].endswith("Z")
    assert lida["prompt_text"]

    codificados: list[bytes] = []
    real_encode = processor.encode_card
    monkeypatch.setattr(processor, "encode_card", lambda png: codificados.append(png) or real_encode(png))
    aprovada = admin.post(f"/exercises/42/card-generations/{gid}/approve")
    assert aprovada.status_code == 200, aprovada.text
    corpo = aprovada.json()
    assert corpo["generation"]["status"] == "PUBLISHED" and corpo["generation"]["version"] == 1
    assert corpo["generation"]["preview_url"] is None
    assert corpo["generation"]["card"]["thumbnail_url"].endswith("/exercise-cards/42/v1/thumb.webp")
    assert corpo["exercise_card"]["version"] == 1

    publicado = admin.get("/exercises/42/card").json()
    assert publicado["exercise_card"] == corpo["exercise_card"]
    # A aprovacao publicou exatamente o preview revisado, sem recompor.
    assert codificados == [preview]
    assert _image(s3.body(BUCKET, "exercise-cards/42/v1/card.png")).tobytes() == _image(preview).tobytes()

    itens = admin.get("/exercises/42/card-generations?limit=5").json()["items"]
    assert [item["id"] for item in itens] == [gid]
    assert "prompt_text" not in itens[0]


def test_rejeitar_e_regenerar_pelo_http(admin, db, dispatched):  # noqa: F811 - fixture do pytest importada (falso positivo)
    gid = admin.post("/exercises/42/card-generations", json={"idempotency_key": _key()}).json()["generation"]["id"]
    jobs.run_generation_job(dispatched[-1])
    rejeitada = admin.post(f"/exercises/42/card-generations/{gid}/reject", json={"reason": "pé fora"})
    assert rejeitada.status_code == 200
    assert rejeitada.json()["generation"]["reject_reason"] == "pé fora"
    key = _key()
    filha = admin.post(f"/exercises/42/card-generations/{gid}/regenerate", json={"idempotency_key": key, "instruction": "pé no centro"})
    assert filha.status_code == 202, filha.text
    corpo = filha.json()
    assert corpo["generation"]["parent_generation_id"] == gid
    assert corpo["parent"]["id"] == gid and corpo["parent"]["status"] == "REJECTED"
    replay = admin.post(f"/exercises/42/card-generations/{gid}/regenerate", json={"idempotency_key": key})
    assert replay.status_code == 200 and replay.json()["generation"]["id"] == corpo["generation"]["id"]


def test_aprovar_de_novo_e_409_not_reviewable(admin, worker):  # noqa: F811 - fixture do pytest importada (falso positivo)
    gid = worker()
    assert admin.post(f"/exercises/42/card-generations/{gid}/approve").status_code == 200
    de_novo = admin.post(f"/exercises/42/card-generations/{gid}/approve")
    assert (de_novo.status_code, de_novo.json()["detail"]["error"]) == (409, "not_reviewable")


def test_aprovar_geracao_v1_e_409_not_reviewable(admin, db, s3):  # noqa: F811 - fixture do pytest importada (falso positivo)
    gid = db.insert_generation(
        exercise_id=42, status="GENERATED", prompt_version=V1, preview_s3_key="exercise-card-drafts/1/preview.png"
    )
    resposta = admin.post(f"/exercises/42/card-generations/{gid}/approve")
    assert (resposta.status_code, resposta.json()["detail"]["error"]) == (409, "not_reviewable")
    assert resposta.json()["detail"]["generation_id"] == gid
    assert s3.calls == []


def test_aprovar_rascunho_apagado_e_410_draft_expired_e_o_payload_marca(admin, worker, s3):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """M72: NoSuchKey -> 410 `DRAFT_EXPIRED` (nao 502), e o GET passa a
    trazer `draft_expired` sem `preview_url` - o front nao depende do 404 da
    imagem."""
    gid = worker()
    del s3.objects[(BUCKET, storage.draft_key(gid))]
    resposta = admin.post(f"/exercises/42/card-generations/{gid}/approve")
    assert resposta.status_code == 410, resposta.text
    assert resposta.json()["detail"] == {
        "error": "DRAFT_EXPIRED",
        "message": "O rascunho expirou (mais de 7 dias). Gere o card de novo.",
        "generation_id": gid,
    }
    geracao = admin.get(f"/exercises/42/card-generations/{gid}").json()["generation"]
    assert geracao["status"] == "GENERATED"
    assert geracao["draft_expired"] is True and geracao["preview_url"] is None
    assert geracao["draft_expires_at"]


@pytest.mark.parametrize("nivel", ["standard", "full", "maximo"])
def test_text_level_diferente_de_minimal_e_422(admin, worker, nivel):  # noqa: F811 - fixture do pytest importada (falso positivo)
    criar = admin.post("/exercises/42/card-generations", json={"idempotency_key": _key(), "text_level": nivel})
    assert (criar.status_code, criar.json()["detail"]["error"]) == (422, "invalid_text_level")
    gid = worker()
    regenerar = admin.post(
        f"/exercises/42/card-generations/{gid}/regenerate", json={"idempotency_key": _key(), "text_level": nivel}
    )
    assert (regenerar.status_code, regenerar.json()["detail"]["error"]) == (422, "invalid_text_level")


def test_anti_idor_gid_de_outro_exercicio_e_404(admin, worker):  # noqa: F811 - fixture do pytest importada (falso positivo)
    gid = worker(exercise_id=43)
    for metodo, path, body in _rotas_de_geracao(gid)[2:]:
        resposta = _chamar(admin, metodo, path, body)
        assert resposta.status_code == 404, (metodo, path)
        assert resposta.json()["detail"]["error"] == "exercise_card_generation_not_found"


def test_limit_fora_da_faixa_e_422(admin):
    assert admin.get("/exercises/42/card-generations?limit=0").status_code == 422
    assert admin.get("/exercises/42/card-generations?limit=51").status_code == 422


def test_listagem_de_exercicio_inexistente_e_404(admin):
    assert admin.get("/exercises/999/card-generations").status_code == 404


# ---------------------------------------------------------------------------
# Lambda plan-generator: dispatch por job_kind
# ---------------------------------------------------------------------------


def test_job_kind_exercise_card_chama_o_worker_e_marca_done(monkeypatch):
    from app import lambda_plan_generator as lpg

    vistos: dict = {}
    monkeypatch.setattr(jobs, "run_generation_job", lambda event: vistos.setdefault("event", event) and {"status": "GENERATED"})
    monkeypatch.setattr("app.async_jobs.mark_done", lambda job_id, result: vistos.setdefault("done", (job_id, result)))
    monkeypatch.setattr("app.async_jobs.mark_failed", lambda job_id, error: pytest.fail(f"nao podia falhar: {error}"))
    evento = {"job_id": "j-9", "job_kind": "exercise-card", "generation_id": 5, "exercise_id": 42}
    monkeypatch.setattr("app.async_jobs.claim_job", lambda job_id: True)  # M81
    lpg.handler(evento, None)
    assert vistos["event"] == evento
    assert vistos["done"] == ("j-9", {"status": "GENERATED"})


def test_job_kind_exercise_card_com_falha_marca_failed_so_com_o_codigo(monkeypatch):
    from app import lambda_plan_generator as lpg

    vistos: dict = {}

    def falha(event):
        raise jobs.ExerciseCardJobError("Card generation 5 falhou: OPENAI_TIMEOUT")

    monkeypatch.setattr(jobs, "run_generation_job", falha)
    monkeypatch.setattr("app.async_jobs.mark_done", lambda job_id, result: pytest.fail("nao podia concluir"))
    monkeypatch.setattr("app.async_jobs.mark_failed", lambda job_id, error: vistos.setdefault("failed", (job_id, error)))
    monkeypatch.setattr("app.async_jobs.claim_job", lambda job_id: True)  # M81
    lpg.handler({"job_id": "j-9", "job_kind": "exercise-card", "generation_id": 5}, None)
    assert vistos["failed"] == ("j-9", "Card generation 5 falhou: OPENAI_TIMEOUT")


def test_job_kinds_antigos_continuam_no_mesmo_lugar():
    from app import lambda_plan_generator as lpg

    assert (lpg.JOB_KIND_PLAN, lpg.JOB_KIND_PERIODIZATION, lpg.JOB_KIND_EXERCISE_DESCRIPTION) == (
        "plan",
        "periodization",
        "exercise-description",
    )
    assert lpg.JOB_KIND_EXERCISE_CARD == jobs.JOB_KIND == "exercise-card"


# ---------------------------------------------------------------------------
# /auth/me: features.exercise_cards
# ---------------------------------------------------------------------------


class _UserCursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql, params=None):
        return self

    def fetchone(self):
        return {"id": ADMIN, "username": "doug", "full_name": None, "role": "admin", "timezone": "America/Sao_Paulo"}


class _UserConn:
    def cursor(self):
        return _UserCursor()


@contextmanager
def _user_tx():
    yield _UserConn()


@pytest.mark.parametrize("valor, esperado", [("true", True), ("false", False), (None, False)])
def test_auth_me_expoe_a_flag_em_features(monkeypatch, valor, esperado):
    if valor is None:
        monkeypatch.delenv(svc.ENV_ENABLED, raising=False)
    else:
        monkeypatch.setenv(svc.ENV_ENABLED, valor)
    monkeypatch.setattr(auth, "transaction", _user_tx)
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=ADMIN, role="admin", source="session")
    corpo = TestClient(app).get("/auth/me").json()
    assert corpo["features"] == {"exercise_cards": esperado}
    assert corpo["role"] == "admin" and corpo["source"] == "session"  # o resto do contrato intacto

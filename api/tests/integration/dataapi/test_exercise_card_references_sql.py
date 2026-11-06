"""Fotos de referencia dos cards (migracao 109) contra MySQL real, nos DOIS
backends (PyMySQL de DEV e o shim da RDS Data API de producao), num schema com
todas as migracoes. SO RODA NO CI (nunca localmente).

S3 e falso (nada sai do processo); o resto e o caminho real: rotas -> servico ->
SQL. O que so o banco prova:

1. INSERT com `lastrowid` e UPDATE da `s3_key` derivada do id (Data API inclusa);
2. `SELECT ... FOR UPDATE` do exercicio + contagem de ativas (limite de 3);
3. soft-delete com `deleted_at` (DATETIME ida e volta) e `rowcount` confiavel;
4. `active_reference_rows`: so ativas, por `id`, no
   maximo 3, usando o indice `idx_ecr_exercise_active`;
5. nota com acento ida e volta (utf8mb4) e o tipo `BIGINT UNSIGNED` do
   `exercise_id` sem FK (apagar o exercicio nao e bloqueado pela tabela).

Isolamento: `isolamento_v3.truncar` (catalogo, usuarios) + a tabela das fotos.
"""

from __future__ import annotations

import base64
import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

# Importados no topo de proposito: `use_dataapi` so troca o backend de modulos
# `app.*` JA importados (ver tests/support/dataapi_backend.py).
from app import exercise_card_references as refs
from app import exercise_card_service as svc
from app import exercise_card_storage as storage
from app import exercise_cards
from app.security import Identity, get_current_identity
from tests.support.fake_s3 import FakeS3
from tests.support.isolamento_v3 import truncar

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]

BUCKET = "musclefit-uploads"
SUPINO = 42
AGACHAMENTO = 43


def _foto(w: int = 640, h: int = 480) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (w, h), (90, 120, 150)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii").rstrip("=")


@pytest.fixture
def seed(raw):
    truncar(raw, extras=("exercise_card_references",))
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_refs', 'x', 'admin')")
        admin_id = cur.lastrowid
        cur.execute("INSERT INTO exercise_catalog (id, name, is_active) VALUES (%s, %s, 1)", (SUPINO, "Supino reto com barra"))
        cur.execute("INSERT INTO exercise_catalog (id, name, is_active) VALUES (%s, %s, 1)", (AGACHAMENTO, "Agachamento livre"))
    raw.commit()
    return {"admin": admin_id}


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setenv(svc.ENV_ENABLED, "true")
    monkeypatch.setenv("EQUIPMENT_PHOTOS_BUCKET", BUCKET)
    for name in ("PUBLIC_ASSETS_BASE_URL", "S3_PRESIGN_ENDPOINT_URL"):
        monkeypatch.delenv(name, raising=False)
    s3 = FakeS3()
    monkeypatch.setattr(storage, "s3_client", lambda: s3)
    monkeypatch.setattr(storage, "presign_client", lambda: s3)
    return {"s3": s3}


@pytest.fixture
def backend(db_backend, fake_rds) -> str:
    """`db_backend` (os dois backends) + nenhuma transacao Data API pendurada."""
    yield db_backend
    if db_backend == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(exercise_cards.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="admin", source="session")
    return TestClient(app)


def _rows(raw, exercise_id: int) -> list[dict]:
    with raw.cursor() as cur:
        cur.execute("SELECT * FROM exercise_card_references WHERE exercise_id = %s ORDER BY id", (exercise_id,))
        return list(cur.fetchall())


def test_ciclo_completo_nos_dois_backends(seed, backend, raw, fakes):
    api = _client(seed["admin"])
    base = f"/exercises/{SUPINO}/card-references"

    criada = api.post(base, json={"image_base64": _foto(700, 500), "note": "Banco inclinado — 30° (ângulo)"})
    assert criada.status_code == 201, (backend, criada.text)
    ref = criada.json()["reference"]
    (linha,) = _rows(raw, SUPINO)
    assert linha["id"] == ref["id"]
    assert linha["s3_key"] == f"exercise-card-references/{SUPINO}/{ref['id']}.png"
    assert (linha["content_type"], linha["width"], linha["height"], linha["created_by"]) == ("image/png", 700, 500, seed["admin"])
    assert linha["note"] == "Banco inclinado — 30° (ângulo)" and linha["deleted_at"] is None
    assert linha["bytes"] == len(fakes["s3"].body(BUCKET, linha["s3_key"]))

    lista = api.get(base).json()
    assert [i["id"] for i in lista["items"]] == [ref["id"]]
    assert lista["items"][0]["note"] == "Banco inclinado — 30° (ângulo)"
    assert lista["items"][0]["created_at"].endswith("Z")

    with svc.transaction() as conn:
        with conn.cursor() as cursor:
            assert [r["s3_key"] for r in refs.active_reference_rows(cursor, SUPINO)] == [linha["s3_key"]]

    assert api.delete(f"{base}/{ref['id']}").status_code == 204
    (apagada,) = _rows(raw, SUPINO)
    assert apagada["deleted_at"] is not None  # soft-delete; a linha continua
    assert fakes["s3"].keys() == []
    with svc.transaction() as conn:
        with conn.cursor() as cursor:
            assert refs.active_reference_rows(cursor, SUPINO) == []
    assert api.get(base).json()["items"] == []
    assert api.delete(f"{base}/{ref['id']}").status_code == 404


def test_limite_de_tres_ativas_e_por_exercicio(seed, backend, raw, fakes):
    api = _client(seed["admin"])
    base = f"/exercises/{SUPINO}/card-references"
    ids = [api.post(base, json={"image_base64": _foto()}).json()["reference"]["id"] for _ in range(3)]
    quarta = api.post(base, json={"image_base64": _foto()})
    assert quarta.status_code == 409, (backend, quarta.text)
    assert quarta.json()["detail"]["error"] == "exercise_card_reference_limit"
    assert len(_rows(raw, SUPINO)) == 3 and len(fakes["s3"].keys()) == 3
    assert api.post(f"/exercises/{AGACHAMENTO}/card-references", json={"image_base64": _foto()}).status_code == 201

    assert api.delete(f"{base}/{ids[0]}").status_code == 204  # libera uma vaga
    nova = api.post(base, json={"image_base64": _foto()})
    assert nova.status_code == 201 and nova.json()["reference"]["id"] > ids[-1]
    with svc.transaction() as conn:
        with conn.cursor() as cursor:
            assert [r["id"] for r in refs.active_reference_rows(cursor, SUPINO)] == [ids[1], ids[2], nova.json()["reference"]["id"]]


def test_anti_idor_e_exercicio_inexistente(seed, backend, raw, fakes):
    api = _client(seed["admin"])
    de_outro = api.post(f"/exercises/{AGACHAMENTO}/card-references", json={"image_base64": _foto()}).json()["reference"]
    cruzada = api.delete(f"/exercises/{SUPINO}/card-references/{de_outro['id']}")
    assert cruzada.status_code == 404, (backend, cruzada.text)
    assert cruzada.json()["detail"]["error"] == "exercise_card_reference_not_found"
    assert _rows(raw, AGACHAMENTO)[0]["deleted_at"] is None
    inexistente = api.get("/exercises/999999/card-references")
    assert inexistente.status_code == 404 and inexistente.json()["detail"]["error"] == "exercise_not_found"


def test_falha_do_s3_desfaz_a_linha(seed, backend, raw, fakes, monkeypatch):
    api = _client(seed["admin"])

    def s3_fora(*args, **kwargs):
        raise storage.ExerciseCardStorageError("S3 fora (simulado)")

    monkeypatch.setattr(storage, "put_reference", s3_fora)
    resposta = api.post(f"/exercises/{SUPINO}/card-references", json={"image_base64": _foto()})
    assert resposta.status_code == 502, (backend, resposta.text)
    assert resposta.json()["detail"]["error"] == "exercise_card_reference_storage_failed"
    assert _rows(raw, SUPINO) == []


def test_excluir_duas_vezes_e_404_no_mysql(seed, backend, raw, fakes):
    """O UPDATE condicional (`deleted_at IS NULL`) e o SELECT de ativas valem nos
    dois backends: a 2a exclusao e 404 e a contagem de ativas reflete o fluxo real."""
    api = _client(seed["admin"])
    base = f"/exercises/{SUPINO}/card-references"
    ids = [api.post(base, json={"image_base64": _foto()}).json()["reference"]["id"] for _ in range(3)]

    primeira = api.delete(f"{base}/{ids[0]}")
    assert primeira.status_code == 204, (backend, primeira.text)
    segunda = api.delete(f"{base}/{ids[0]}")
    assert segunda.status_code == 404, (backend, segunda.text)
    assert segunda.json()["detail"]["error"] == "exercise_card_reference_not_found"

    # Tres enviadas, uma excluida: duas ativas; a linha excluida continua (soft-delete).
    assert [i["id"] for i in api.get(base).json()["items"]] == [ids[1], ids[2]]
    assert len(_rows(raw, SUPINO)) == 3
    with svc.transaction() as conn:
        with conn.cursor() as cursor:
            assert refs.count_active(cursor, SUPINO) == 2

    # Reenviar uma (volta a 3 ativas) e a 4a e recusada pelo limite.
    reenviada = api.post(base, json={"image_base64": _foto()})
    assert reenviada.status_code == 201, (backend, reenviada.text)
    with svc.transaction() as conn:
        with conn.cursor() as cursor:
            assert refs.count_active(cursor, SUPINO) == 3
    assert api.post(base, json={"image_base64": _foto()}).status_code == 409
    assert api.delete(f"{base}/{ids[0]}").status_code == 404  # a ja excluida segue 404

"""Apelido duplicado sem UNIQUE global por `alias` (formato da Aurora, 029), nos
dois backends (PyMySQL e shim RDS Data API + dublê), num schema proprio com
TODAS as migracoes (001..029) aplicadas pelo runner.

Decisao do usuario (2026-09-14): o mesmo apelido em exercicios diferentes e
ACEITO, como em producao (53 textos ja estao em mais de um exercicio na Aurora).

- POST /exercises/{id}/aliases e POST /exercise-mapping-catalog/{id}/aliases:
  apelido de outro exercicio -> 200, as duas linhas coexistem; mesmo exercicio
  -> 409 "para este exercicio" (UNIQUE (exercise_id, alias)).
- POST /mapping-canonical/associate: garante o texto no alvo (INSERT ou nada),
  nunca apaga/move linhas de outros exercicios, sem 500, idempotente e sem
  trocar vinculos ja validos.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import exercise_catalog_admin as admin
from app import exercise_mapping_canonical as emc
from app import exercise_mapping_catalog as emcat
from app import migrate as m
from app.db_pymysql import Connection as PyMySQLConnection
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations

MASTER = "aliases-duplicados-master"
HEADERS = {"X-API-Token": MASTER}
SUPINO, CRUCIFIXO, REMADA = 9201, 9202, 9203
TABLES = (
    "exercise_sets",
    "exercises",
    "activities",
    "exercise_aliases",
    "exercise_external_mappings",
    "exercise_muscle_contributions",
    "exercise_catalog",
    "muscle_groups",
    "users",
)
ROUTES = {
    "exercises": "/exercises/{id}/aliases",
    "exercise-mapping-catalog": "/exercise-mapping-catalog/{id}/aliases",
}
SAME = "Este apelido já está cadastrado para este exercício."


def _kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


@pytest.fixture(scope="module")
def schema() -> str:
    base = os.environ["MYSQL_DATABASE"]
    database = f"{base[: -len('_test')]}_aliases_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    kw = _kwargs()
    m.migrate(conn_factory=lambda: PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], database))
    return database


@pytest.fixture
def raw(schema):
    with raw_mysql(database=schema, **_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            try:
                for table in TABLES:
                    cur.execute(f"TRUNCATE TABLE `{table}`")
            finally:
                cur.execute("SET FOREIGN_KEY_CHECKS = 1")
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin-aliases', 'x', 'admin')")
            cur.execute("INSERT INTO muscle_groups (name) VALUES ('Peitoral')")
            group_id = cur.lastrowid
            for exercise_id, name in ((SUPINO, "Supino reto"), (CRUCIFIXO, "Crucifixo"), (REMADA, "Remada baixa")):
                cur.execute("INSERT INTO exercise_catalog (id, name, is_active) VALUES (%s, %s, 1)", (exercise_id, name))
                cur.execute(
                    "INSERT INTO exercise_muscle_contributions (exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) "
                    "VALUES (%s, %s, 'PRIMARY', 1, 1)",
                    (exercise_id, group_id),
                )
        yield conn


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch, fake_rds, schema) -> str:
    monkeypatch.setenv("API_TOKEN", MASTER)
    if request.param == "data-api":
        use_dataapi(monkeypatch, fake_rds, schema)
    else:
        monkeypatch.setenv("MYSQL_DATABASE", schema)
    yield request.param
    if request.param == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


@pytest.fixture
def client(backend) -> TestClient:
    app = FastAPI()
    app.include_router(admin.router)
    app.include_router(emcat.router)
    return TestClient(app, raise_server_exceptions=False)


def _aliases(raw) -> list[tuple]:
    with raw.cursor() as cur:
        cur.execute("SELECT exercise_id, alias, source FROM exercise_aliases ORDER BY id")
        return [(r["exercise_id"], r["alias"], r["source"]) for r in cur.fetchall()]


def _insert_alias(raw, exercise_id: int, alias: str) -> None:
    with raw.cursor() as cur:
        cur.execute("INSERT INTO exercise_aliases (exercise_id, alias, source) VALUES (%s, %s, 'seed')", (exercise_id, alias))


def _exercise(raw, name: str, mapping_id) -> int:
    with raw.cursor() as cur:
        cur.execute("SELECT id FROM users LIMIT 1")
        user_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO activities (user_id, local_date, name, activity_type) VALUES (%s, CURDATE(), 'Treino', 'Musculacao')",
            (user_id,),
        )
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) VALUES (%s, 1, %s, %s, 1)",
            (cur.lastrowid, name, mapping_id),
        )
        return cur.lastrowid


def _mapping_ids(raw) -> dict[int, int | None]:
    with raw.cursor() as cur:
        cur.execute("SELECT id, mapping_id FROM exercises ORDER BY id")
        return {r["id"]: r["mapping_id"] for r in cur.fetchall()}


def _associate(name: str, target: int) -> dict:
    return emc.canonical_associate(emc.CanonicalAssociationRequest(alias_name=name, target_mapping_id=target))


def test_pre_condicao_schema_igual_a_aurora_sem_unique_global(schema):
    with raw_mysql(database=schema, **_kwargs()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_aliases'"
        )
        assert {r["INDEX_NAME"] for r in cur.fetchall()} == {
            "PRIMARY",
            "idx_exercise_aliases_exercise",
            "uq_exercise_aliases_exercise_alias",
        }


# -- POST .../aliases ----------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_post_de_apelido_novo_grava(route, client, raw):
    response = client.post(ROUTES[route].format(id=SUPINO), json={"alias": "supino plano", "source": "manual"}, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["item"]["alias"] == "supino plano"
    assert _aliases(raw) == [(SUPINO, "supino plano", "manual")]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("alias", ["supino plano", "SUPINO PLANO"])
def test_post_de_apelido_de_outro_exercicio_grava_e_as_duas_linhas_coexistem(route, alias, client, raw):
    _insert_alias(raw, SUPINO, "supino plano")

    response = client.post(ROUTES[route].format(id=CRUCIFIXO), json={"alias": alias}, headers=HEADERS)

    assert response.status_code == 200, response.text
    assert response.json()["item"]["alias"] == alias
    assert _aliases(raw) == [(SUPINO, "supino plano", "seed"), (CRUCIFIXO, alias, None)]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("alias", ["supino plano", "Supino Pláno"])  # collation utf8mb4_0900_ai_ci
def test_post_de_apelido_repetido_no_mesmo_exercicio_responde_409(route, alias, client, raw):
    _insert_alias(raw, SUPINO, "supino plano")

    response = client.post(ROUTES[route].format(id=SUPINO), json={"alias": alias}, headers=HEADERS)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": SAME}
    assert _aliases(raw) == [(SUPINO, "supino plano", "seed")]


# -- POST /mapping-canonical/associate -----------------------------------------


def test_associate_sem_apelido_cria(backend, raw):
    result = _associate("Supino maquina articulada", SUPINO)
    assert result["operation"] == "created"
    assert _aliases(raw) == [(SUPINO, "Supino maquina articulada", f"manual_alias:{SUPINO}")]


def test_associate_com_o_texto_em_outros_exercicios_acrescenta_no_alvo_sem_apagar(backend, raw):
    _insert_alias(raw, CRUCIFIXO, "peck deck")
    _insert_alias(raw, REMADA, "Peck Deck")

    result = _associate("peck deck", SUPINO)

    assert result["ok"] is True and result["operation"] == "created"
    assert _aliases(raw) == [
        (CRUCIFIXO, "peck deck", "seed"),
        (REMADA, "Peck Deck", "seed"),
        (SUPINO, "peck deck", f"manual_alias:{SUPINO}"),
    ]


def test_associate_com_o_texto_ja_no_alvo_e_em_outro_nao_grava_nada(backend, raw, monkeypatch):
    """Codigo anterior: `SELECT ... LIMIT 1` pegava a linha de Crucifixo e o
    UPDATE para Supino colidia com UNIQUE (exercise_id, alias) -> 1062 -> 500."""
    _insert_alias(raw, CRUCIFIXO, "voador")
    _insert_alias(raw, SUPINO, "voador")
    before = _aliases(raw)

    writes: list[str] = []
    original = emc.execute
    monkeypatch.setattr(emc, "execute", lambda sql, params=(): writes.append(sql) or original(sql, params))

    result = _associate("voador", SUPINO)

    assert result["operation"] == "updated"
    assert writes == []
    assert _aliases(raw) == before


def test_associate_e_idempotente(backend, raw):
    _insert_alias(raw, CRUCIFIXO, "maquina x")
    pending = _exercise(raw, "maquina x", None)

    first = _associate("maquina x", REMADA)
    after_first = (_aliases(raw), _mapping_ids(raw))
    second = _associate("maquina x", REMADA)

    assert (first["operation"], second["operation"]) == ("created", "updated")
    assert second["reclassification"]["written"] == 0
    assert (_aliases(raw), _mapping_ids(raw)) == after_first
    assert _aliases(raw) == [(CRUCIFIXO, "maquina x", "seed"), (REMADA, "maquina x", f"manual_alias:{REMADA}")]
    # sem vinculo antes: recebe um dos exercicios que tem o apelido (matcher ambiguo, decisao do usuario)
    assert after_first[1][pending] in (CRUCIFIXO, REMADA)


def test_associate_nao_altera_vinculos_existentes(backend, raw):
    _insert_alias(raw, CRUCIFIXO, "peck deck")
    same_name_linked = _exercise(raw, "peck deck", CRUCIFIXO)
    other_linked = _exercise(raw, "Supino reto", REMADA)  # vinculo que o matcher resolveria para Supino
    before = _mapping_ids(raw)

    result = _associate("peck deck", SUPINO)

    assert result["operation"] == "created"
    assert result["reclassification"]["written"] == 0
    assert _mapping_ids(raw) == before
    assert (before[same_name_linked], before[other_linked]) == (CRUCIFIXO, REMADA)

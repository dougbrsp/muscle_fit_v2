"""PR-07 nos dois backends (PyMySQL e o caminho de PRODUCAO: shim
`app/db_dataapi.py` + dublê da RDS Data API):

- migracoes 032 (colunas) e 033 (tabelas): plano = 2 + 2 statements, um por
  chamada; reexecucao sem escrita; formato inesperado falha sem aplicar;
  dry-run do lote com a 031 pendente (o runner 001..033 em banco novo foi
  para `test_runner_cadeia_completa.py`, M38);
- `resolve_training_safety` lendo o banco: override > default > NULL = 9;
- GET/PUT /equipment/{id}/safety e /equipment-exercises/{id}/safety:
  autorizacao (401/403), faixa 6-10 (422), tecnica fora do catalogo (422),
  persistencia, `updated_by_user_id`, sem DDL;
- relatorio de backfill somente leitura, com e sem o schema novo.

Schemas proprios (`*_p07_test`, `*_d07_test`), apagados ao final.
"""

from __future__ import annotations

import hashlib
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import advanced_techniques as at
from app import migrate as m
from app import training_safety_admin as admin
from app import training_safety_backfill_report as report
from app.db_pymysql import Connection as PyMySQLConnection
from app.domain import training_safety as ts
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations
from tests.support.safety_032_033 import (
    ALTER_EQUIPMENT,
    ALTER_EQUIPMENT_EXERCISES,
    MIGRATION_032,
    MIGRATION_033,
    NEW_TABLES,
    apply,
)
from tests.support.sql_capture import ddl, recorded_sql, writes
from tests.support.template_versions import versao_v1

MASTER = "training-safety-master"
HEADERS = {"X-API-Token": MASTER}
USER_TOKEN = "training-safety-aluno"
EQUIPMENT_ID, OTHER_EQUIPMENT_ID, EXERCISE_ID, OTHER_EXERCISE_ID = 9701, 9702, 9703, 9704


def _kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


def _database(tag: str) -> str:
    base = os.environ["MYSQL_DATABASE"]
    return f"{base[: -len('_test')]}_{tag}_test"


def _drop_database(database: str) -> None:
    assert database.endswith("_test")
    with raw_mysql(database="information_schema", **_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{database}`")


def _pymysql_factory(database: str):
    kw = _kwargs()
    return lambda: PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], database)


@pytest.fixture(scope="module")
def schema():
    """Banco novo com TODAS as migracoes aplicadas pelo runner (inclui 032/033)."""
    database = _database("p07")
    recreate_schema_with_migrations(database=database, **_kwargs())
    m.migrate(conn_factory=_pymysql_factory(database))
    yield database
    _drop_database(database)


@pytest.fixture
def sraw(schema):
    with raw_mysql(database=schema, **_kwargs()) as conn:
        yield conn


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch, fake_rds, schema) -> str:
    monkeypatch.setenv("API_TOKEN", MASTER)
    if request.param == "data-api":
        use_dataapi(monkeypatch, fake_rds, schema)
    else:
        monkeypatch.setenv("MYSQL_DATABASE", schema)
    at.clear_cache()
    yield request.param
    at.clear_cache()
    if request.param == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


def _run(path, dry_run: bool = False) -> list[str]:
    import app.db as db  # atributo trocado por use_dataapi

    conn = db.get_conn()
    try:
        return apply(conn, path, dry_run=dry_run)
    finally:
        conn.close()


def _columns(raw, table: str) -> dict[str, tuple[str, str]]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
        return {r["COLUMN_NAME"]: (r["COLUMN_TYPE"].lower(), r["IS_NULLABLE"]) for r in cur.fetchall()}


def _drop_pr07_schema(raw) -> None:
    with raw.cursor() as cur:
        for table in NEW_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute("ALTER TABLE equipment DROP COLUMN max_safe_rpe, DROP COLUMN load_increment_kg, DROP COLUMN safety_notes")
        cur.execute("ALTER TABLE equipment_exercises DROP COLUMN max_safe_rpe_override, DROP COLUMN safety_notes")


# ---------------------------------------------------------------------------
# Migracoes
# ---------------------------------------------------------------------------


def test_banco_sem_o_schema_aplica_2_mais_2_statements_e_a_reexecucao_nao_escreve(backend, sraw, monkeypatch, fake_rds):
    _drop_pr07_schema(sraw)

    plan_032 = _run(MIGRATION_032, dry_run=True)
    plan_033 = _run(MIGRATION_033, dry_run=True)
    assert plan_032 == [ALTER_EQUIPMENT, ALTER_EQUIPMENT_EXERCISES]
    assert [sql.split(" (")[0] for sql in plan_033] == [f"CREATE TABLE IF NOT EXISTS {table}" for table in NEW_TABLES]
    assert "max_safe_rpe" not in _columns(sraw, "equipment")  # dry-run nao altera nada

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run(MIGRATION_032) == plan_032
        assert _run(MIGRATION_033) == plan_033
    assert len(ddl(sent)) == 4 and len(writes(sent)) == 4  # um statement por chamada

    equipment = _columns(sraw, "equipment")
    assert equipment["max_safe_rpe"] == ("tinyint", "YES")
    assert equipment["load_increment_kg"] == ("decimal(5,2)", "YES")
    assert equipment["safety_notes"] == ("text", "YES")
    pair = _columns(sraw, "equipment_exercises")
    assert pair["max_safe_rpe_override"] == ("tinyint", "YES") and pair["safety_notes"] == ("text", "YES")
    for table, owner in zip(NEW_TABLES, ("equipment_id", "equipment_exercise_id")):
        assert _columns(sraw, table) == {
            owner: ("bigint", "NO"),
            "technique_code": ("varchar(20)", "NO"),
            "is_compatible": ("tinyint(1)", "NO"),
            "updated_by_user_id": ("bigint", "YES"),
            "updated_at": ("timestamp", "NO"),
        }

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run(MIGRATION_032) == [] and _run(MIGRATION_033) == []
    assert writes(sent) == []


def test_032_so_adiciona_a_coluna_que_falta(backend, sraw):
    with sraw.cursor() as cur:
        cur.execute("ALTER TABLE equipment DROP COLUMN load_increment_kg")
    assert _run(MIGRATION_032) == ["ALTER TABLE equipment ADD COLUMN load_increment_kg DECIMAL(5,2) NULL"]
    assert _run(MIGRATION_032) == []


def test_formato_inesperado_falha_sem_aplicar(backend, sraw):
    with sraw.cursor() as cur:
        cur.execute("ALTER TABLE equipment DROP COLUMN safety_notes")
        cur.execute("ALTER TABLE equipment MODIFY COLUMN max_safe_rpe VARCHAR(10) NULL")
        cur.execute("ALTER TABLE equipment_technique_compatibility MODIFY COLUMN is_compatible TINYINT(1) NULL")
    try:
        with pytest.raises(Exception, match="032: equipment.max_safe_rpe e varchar\\(10\\).*Nenhum statement executado"):
            _run(MIGRATION_032)
        assert "safety_notes" not in _columns(sraw, "equipment")  # nada aplicado
        with pytest.raises(Exception, match="033: .*is_compatible tem IS_NULLABLE=YES.*Nenhum statement executado"):
            _run(MIGRATION_033)
    finally:
        with sraw.cursor() as cur:
            cur.execute("ALTER TABLE equipment MODIFY COLUMN max_safe_rpe TINYINT NULL")
            cur.execute("ALTER TABLE equipment_technique_compatibility MODIFY COLUMN is_compatible TINYINT(1) NOT NULL")
        assert _run(MIGRATION_032) == ["ALTER TABLE equipment ADD COLUMN safety_notes TEXT NULL"]
        assert _run(MIGRATION_033) == []


# O runner 001..033 em banco novo saiu daqui no M38 (2a passada): a cadeia
# inteira roda UMA vez por backend em
# `test_runner_cadeia_completa.py::test_runner_em_banco_novo_aplica_032_e_033`,
# com as MESMAS assercoes. O dry-run abaixo CONTINUA aqui: ele exige um banco
# com a 031 ainda PENDENTE, que e o oposto do estado da cadeia compartilhada.


@pytest.mark.parametrize("runner_backend", BACKENDS)
def test_dry_run_do_lote_com_a_031_pendente(runner_backend, monkeypatch, fake_rds):
    """Banco em que a 031 ainda nao rodou: o dry-run planeja 031..033 sem criar
    `advanced_techniques`; a 033 nao pode derrubar o dry-run."""
    database = _database("d07")
    recreate_schema_with_migrations(database=database, **_kwargs())
    try:
        if runner_backend == "data-api":
            use_dataapi(monkeypatch, fake_rds, database)
            factory = None
        else:
            factory = _pymysql_factory(database)
        planned = {item.filename: item.statements for item in m.migrate(conn_factory=factory, dry_run=True).applied}
        assert len(planned["031_advanced_techniques_catalog.py"]) == 9
        assert planned[MIGRATION_032.name] == [ALTER_EQUIPMENT, ALTER_EQUIPMENT_EXERCISES]
        assert len(planned[MIGRATION_033.name]) == 2
        with raw_mysql(database=database, **_kwargs()) as raw:
            assert "max_safe_rpe" not in _columns(raw, "equipment")
    finally:
        _drop_database(database)


# ---------------------------------------------------------------------------
# Dominio de teste
# ---------------------------------------------------------------------------


@pytest.fixture
def domain(sraw):
    """2 equipamentos, 2 exercicios, 2 vinculos; admin e aluno com sessao."""
    token_hash = hashlib.sha256(USER_TOKEN.encode("utf-8")).hexdigest()
    with sraw.cursor() as cur:
        cur.execute("INSERT IGNORE INTO users (username, password_hash, role) VALUES ('admin-p07', 'x', 'admin')")
        cur.execute("INSERT IGNORE INTO users (username, password_hash, role) VALUES ('aluno-p07', 'x', 'user')")
        cur.execute("SELECT id FROM users WHERE username = 'aluno-p07'")
        user_id = cur.fetchone()["id"]
        cur.execute("DELETE FROM user_sessions WHERE token_hash = %s", (token_hash,))
        cur.execute(
            "INSERT INTO user_sessions (user_id, token_hash, expires_at) VALUES (%s, %s, NOW() + INTERVAL 1 DAY)",
            (user_id, token_hash),
        )
        cur.execute("INSERT IGNORE INTO equipment (id, name) VALUES (%s, 'Supino articulado P07')", (EQUIPMENT_ID,))
        cur.execute("INSERT IGNORE INTO equipment (id, name) VALUES (%s, 'Barra livre P07')", (OTHER_EQUIPMENT_ID,))
        cur.execute("INSERT IGNORE INTO exercise_catalog (id, name, is_active) VALUES (%s, 'Supino reto P07', 1)", (EXERCISE_ID,))
        cur.execute("INSERT IGNORE INTO exercise_catalog (id, name, is_active) VALUES (%s, 'Crucifixo P07', 1)", (OTHER_EXERCISE_ID,))
        for equipment_id, exercise_id in ((EQUIPMENT_ID, EXERCISE_ID), (EQUIPMENT_ID, OTHER_EXERCISE_ID)):
            cur.execute(
                "INSERT IGNORE INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)", (equipment_id, exercise_id)
            )
        cur.execute(
            "SELECT id, exercise_id FROM equipment_exercises WHERE equipment_id = %s ORDER BY exercise_id", (EQUIPMENT_ID,)
        )
        links = {int(r["exercise_id"]): int(r["id"]) for r in cur.fetchall()}
    yield {"link": links[EXERCISE_ID], "other_link": links[OTHER_EXERCISE_ID]}
    with sraw.cursor() as cur:
        cur.execute("DELETE FROM equipment WHERE id IN (%s, %s)", (EQUIPMENT_ID, OTHER_EQUIPMENT_ID))  # cascata nas marcacoes
        cur.execute("DELETE FROM exercise_catalog WHERE id IN (%s, %s)", (EXERCISE_ID, OTHER_EXERCISE_ID))
        cur.execute("DELETE FROM user_sessions WHERE token_hash = %s", (token_hash,))


@pytest.fixture
def client(backend, domain) -> TestClient:
    app = FastAPI()
    app.include_router(admin.router)
    return TestClient(app, raise_server_exceptions=False)


def _resolve(exercise_id=EXERCISE_ID, equipment_id=EQUIPMENT_ID) -> dict:
    return ts.resolve_training_safety(exercise_id, equipment_id).to_dict()


# ---------------------------------------------------------------------------
# Autorizacao
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["get", "put"])
@pytest.mark.parametrize("path", ["/equipment/{equipment}/safety", "/equipment-exercises/{link}/safety"])
def test_rotas_exigem_admin(client, domain, sraw, method, path):
    url = path.format(equipment=EQUIPMENT_ID, link=domain["link"])
    kwargs = {"json": {"max_safe_rpe": 10, "max_safe_rpe_override": 10}} if method == "put" else {}
    if method == "put":
        kwargs["json"].pop("max_safe_rpe_override" if "equipment/" in path else "max_safe_rpe")
    assert getattr(client, method)(url, **kwargs).status_code == 401
    assert getattr(client, method)(url, headers={"X-API-Token": USER_TOKEN}, **kwargs).status_code == 403
    assert getattr(client, method)(url, headers=HEADERS, **kwargs).status_code == 200
    with sraw.cursor() as cur:  # so o admin gravou
        cur.execute("SELECT max_safe_rpe FROM equipment WHERE id = %s", (EQUIPMENT_ID,))
        assert cur.fetchone()["max_safe_rpe"] in (None, 10)


# ---------------------------------------------------------------------------
# /equipment/{id}/safety
# ---------------------------------------------------------------------------


def test_equipamento_novo_cai_nos_defaults_d3(client, domain):
    body = client.get(f"/equipment/{OTHER_EQUIPMENT_ID}/safety", headers=HEADERS).json()
    assert body["max_safe_rpe"] is None and body["load_increment_kg"] is None and body["techniques"] == {}
    assert body["effective"] == {
        "max_safe_rpe": 9,
        "failure_allowed": False,
        "safety_source": None,
        "allowed_advanced_techniques": [],
        "blocked_advanced_techniques": [],
    }


def test_put_equipamento_persiste_e_registra_quem_alterou(backend, client, domain, sraw, monkeypatch, fake_rds):
    payload = {
        "max_safe_rpe": 10,
        "load_increment_kg": 2.5,
        "safety_notes": "  Trava de segurança; falha liberada.  ",
        "techniques": {"rest_pause": True, "drop_set": True, "cheat_reps": False},
    }
    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        response = client.put(f"/equipment/{EQUIPMENT_ID}/safety", json=payload, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert ddl(sent) == []  # sem DDL em runtime
    body = response.json()
    assert body["max_safe_rpe"] == 10 and body["load_increment_kg"] == 2.5
    assert body["safety_notes"] == "Trava de segurança; falha liberada."  # acento integro, sem espacos
    assert body["techniques"] == {"cheat_reps": False, "drop_set": True, "rest_pause": True}
    assert body["effective"]["failure_allowed"] is True
    assert [t["code"] for t in body["effective"]["allowed_advanced_techniques"]] == ["drop_set", "rest_pause"]
    assert client.get(f"/equipment/{EQUIPMENT_ID}/safety", headers=HEADERS).json() == body

    with sraw.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
        admin_id = cur.fetchone()["id"]
        cur.execute(
            "SELECT technique_code, is_compatible, updated_by_user_id FROM equipment_technique_compatibility WHERE equipment_id = %s",
            (EQUIPMENT_ID,),
        )
        rows = {r["technique_code"]: (r["is_compatible"], r["updated_by_user_id"]) for r in cur.fetchall()}
    assert rows == {"rest_pause": (1, admin_id), "drop_set": (1, admin_id), "cheat_reps": (0, admin_id)}


def test_put_parcial_nao_toca_campo_ausente_e_null_limpa(client, domain):
    url = f"/equipment/{EQUIPMENT_ID}/safety"
    client.put(url, json={"max_safe_rpe": 8, "load_increment_kg": 5, "techniques": {"gvt": True, "rest_pause": True}}, headers=HEADERS)
    body = client.put(url, json={"safety_notes": "só notas"}, headers=HEADERS).json()
    assert (body["max_safe_rpe"], body["load_increment_kg"], body["safety_notes"]) == (8, 5.0, "só notas")
    assert body["techniques"] == {"gvt": True, "rest_pause": True}
    # rest_pause exige falha e o equipamento esta em 8: marcada, mas bloqueada (validacao defensiva)
    assert [t["code"] for t in body["effective"]["allowed_advanced_techniques"]] == ["gvt"]
    assert body["effective"]["blocked_advanced_techniques"] == [
        {"code": "rest_pause", "source": "equipment_default", "reason": "FAILURE_REQUIRED_NOT_ALLOWED"}
    ]
    body = client.put(url, json={"max_safe_rpe": None, "techniques": {"gvt": None}}, headers=HEADERS).json()
    assert body["max_safe_rpe"] is None and body["effective"]["max_safe_rpe"] == 9
    assert body["techniques"] == {"rest_pause": True} and body["load_increment_kg"] == 5.0


@pytest.mark.parametrize(
    "payload",
    [
        {"max_safe_rpe": 5},
        {"max_safe_rpe": 11},
        {"max_safe_rpe": 9.5},
        {"max_safe_rpe": "9"},
        {"max_safe_rpe": True},
        {"load_increment_kg": 0},
        {"load_increment_kg": -1},
        {"load_increment_kg": 1000},
        {"load_increment_kg": 1.255},
        {"safety_notes": "x" * 2001},
        {"techniques": {"super_set": True}},
        {"techniques": {"dropset": False}},
        {"techniques": {"x" * 21: None}},
        {"techniques": ["rest_pause"]},
        {"max_safe_rpe_override": 9},  # campo do PAR na rota do equipamento
    ],
)
def test_put_equipamento_invalido_e_422_sem_gravar(client, domain, sraw, payload):
    response = client.put(f"/equipment/{EQUIPMENT_ID}/safety", json={"safety_notes": "nao grava", **payload}, headers=HEADERS)
    assert response.status_code == 422, response.text
    with sraw.cursor() as cur:
        cur.execute("SELECT safety_notes FROM equipment WHERE id = %s", (EQUIPMENT_ID,))
        assert cur.fetchone()["safety_notes"] != "nao grava"


def test_tecnica_inativa_e_422(client, domain, sraw):
    with sraw.cursor() as cur:
        cur.execute("UPDATE advanced_techniques SET is_active = 0 WHERE code = 'gvt'")
    try:
        at.clear_cache()
        response = client.put(f"/equipment/{EQUIPMENT_ID}/safety", json={"techniques": {"gvt": True}}, headers=HEADERS)
        assert response.status_code == 422 and "gvt" in response.json()["detail"]
    finally:
        with sraw.cursor() as cur:
            cur.execute("UPDATE advanced_techniques SET is_active = 1 WHERE code = 'gvt'")


def test_404(client, domain):
    assert client.get("/equipment/999999/safety", headers=HEADERS).status_code == 404
    assert client.put("/equipment/999999/safety", json={"max_safe_rpe": 9}, headers=HEADERS).status_code == 404
    assert client.get("/equipment-exercises/999999/safety", headers=HEADERS).status_code == 404
    assert client.put("/equipment-exercises/999999/safety", json={"max_safe_rpe_override": 9}, headers=HEADERS).status_code == 404


# ---------------------------------------------------------------------------
# /equipment-exercises/{id}/safety + resolvedor lendo o banco
# ---------------------------------------------------------------------------


def test_precedencia_override_default_null_no_banco(client, domain):
    # NULL -> 9
    assert _resolve(equipment_id=OTHER_EQUIPMENT_ID)["max_safe_rpe"] == 9
    assert _resolve(exercise_id=424242, equipment_id=424242)["safety_source"] is None  # par inexistente: defaults D3

    # default do equipamento
    client.put(
        f"/equipment/{EQUIPMENT_ID}/safety",
        json={"max_safe_rpe": 10, "techniques": {"rest_pause": True, "drop_set": True, "gvt": False}},
        headers=HEADERS,
    )
    resolved = _resolve()
    assert (resolved["max_safe_rpe"], resolved["failure_allowed"], resolved["safety_source"]) == (10, True, "equipment_default")
    assert resolved["equipment_exercise_id"] == domain["link"]

    # override do par: restringe o RPE, nega drop_set, libera gvt
    url = f"/equipment-exercises/{domain['link']}/safety"
    response = client.put(
        url,
        json={"max_safe_rpe_override": 8, "safety_notes": "pegada instável", "techniques": {"drop_set": False, "gvt": True}},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["max_safe_rpe_override"] == 8 and body["safety_notes"] == "pegada instável"
    assert body["techniques"] == {"drop_set": False, "gvt": True}
    assert body["inherited"] == {"max_safe_rpe": 10, "techniques": {"drop_set": True, "gvt": False, "rest_pause": True}}
    assert body["resolved"] == _resolve()
    assert body["resolved"]["max_safe_rpe"] == 8 and body["resolved"]["safety_source"] == "exercise_equipment_override"
    assert body["resolved"]["allowed_advanced_techniques"] == [{"code": "gvt", "source": "exercise_equipment_override"}]
    assert body["resolved"]["blocked_advanced_techniques"] == [
        {"code": "rest_pause", "source": "equipment_default", "reason": "FAILURE_REQUIRED_NOT_ALLOWED"}
    ]

    # o outro par do MESMO equipamento continua no default
    other = _resolve(exercise_id=OTHER_EXERCISE_ID)
    assert (other["max_safe_rpe"], other["safety_source"]) == (10, "equipment_default")
    assert [t["code"] for t in other["allowed_advanced_techniques"]] == ["drop_set", "rest_pause"]

    # limpar o override volta a herdar
    body = client.put(url, json={"max_safe_rpe_override": None, "techniques": {"drop_set": None, "gvt": None}}, headers=HEADERS).json()
    assert body["techniques"] == {} and body["resolved"]["safety_source"] == "equipment_default"
    assert [t["code"] for t in body["resolved"]["allowed_advanced_techniques"]] == ["drop_set", "rest_pause"]


@pytest.mark.parametrize(
    "payload",
    [{"max_safe_rpe_override": 5}, {"max_safe_rpe_override": 11}, {"techniques": {"bi_set": True}}, {"max_safe_rpe": 9}, {"load_increment_kg": 2}],
)
def test_put_par_invalido_e_422(client, domain, payload):
    assert client.put(f"/equipment-exercises/{domain['link']}/safety", json=payload, headers=HEADERS).status_code == 422


def test_resolvedor_so_le(backend, client, domain, monkeypatch, fake_rds):
    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        _resolve()
    assert writes(sent) == []


def test_apagar_equipamento_leva_as_marcacoes_em_cascata(client, domain, sraw):
    client.put(f"/equipment/{OTHER_EQUIPMENT_ID}/safety", json={"techniques": {"gvt": True}}, headers=HEADERS)
    with sraw.cursor() as cur:
        cur.execute("DELETE FROM equipment WHERE id = %s", (OTHER_EQUIPMENT_ID,))
        cur.execute("SELECT COUNT(*) AS n FROM equipment_technique_compatibility WHERE equipment_id = %s", (OTHER_EQUIPMENT_ID,))
        assert cur.fetchone()["n"] == 0


# ---------------------------------------------------------------------------
# Relatorio de backfill (somente leitura)
# ---------------------------------------------------------------------------


def _report(backend, monkeypatch, fake_rds) -> dict:
    import app.db as db

    conn = db.get_conn()
    try:
        with recorded_sql(backend, monkeypatch, fake_rds) as sent:
            result = report.build_report(conn)
    finally:
        conn.close()
    assert writes(sent) == []
    return result


@pytest.fixture
def template_usage(sraw, domain):
    """Um modelo usando drop_set no par cadastrado e rest_pause num par sem vinculo."""
    with sraw.cursor() as cur:
        cur.execute("INSERT IGNORE INTO muscle_groups (id, name) VALUES (9705, 'Peitoral P07')")
        cur.execute("SELECT id FROM users WHERE username = 'admin-p07'")
        cur.execute(
            "INSERT INTO workout_plan_templates (name, volume_mode, created_by_user_id) VALUES ('Modelo P07', 'equipment_series', %s)",
            (cur.fetchone()["id"],),
        )
        template_id = cur.lastrowid
        cur.execute("INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name) VALUES (%s, %s, 1, 'A')", (versao_v1(cur, template_id), template_id,))
        slot_id = cur.lastrowid
        for order, (equipment_id, technique) in enumerate(((EQUIPMENT_ID, "drop_set"), (OTHER_EQUIPMENT_ID, "rest_pause")), start=1):
            cur.execute(
                "INSERT INTO workout_plan_template_slot_items "
                "(slot_id, item_order, item_kind, muscle_group_id, equipment_id, mapping_id, sets_count, advanced_technique) "
                "VALUES (%s, %s, 'equipment', 9705, %s, %s, 3, %s)",
                (slot_id, order, equipment_id, EXERCISE_ID, technique),
            )
    yield
    with sraw.cursor() as cur:
        cur.execute("DELETE FROM workout_plan_templates WHERE id = %s", (template_id,))


def _mine(result: dict) -> tuple[list, list]:
    ids = {EQUIPMENT_ID, OTHER_EQUIPMENT_ID}
    return (
        [e for e in result["equipment_without_max_safe_rpe"] if e["equipment_id"] in ids],
        [u for u in result["technique_usages_pending"] if u["equipment_id"] in ids],
    )


def test_relatorio_lista_pendencias_e_some_conforme_o_admin_cadastra(backend, client, domain, template_usage, monkeypatch, fake_rds):
    result = _report(backend, monkeypatch, fake_rds)
    assert result["schema_pending"] is False
    without_rpe, pending = _mine(result)
    assert {e["equipment_id"] for e in without_rpe} == {EQUIPMENT_ID, OTHER_EQUIPMENT_ID}
    assert {(u["equipment_id"], u["technique"], u["marking"], u["reason"]) for u in pending} == {
        (EQUIPMENT_ID, "drop_set", "unmarked", "TECHNIQUE_NOT_COMPATIBLE"),
        (OTHER_EQUIPMENT_ID, "rest_pause", "unmarked", "TECHNIQUE_NOT_COMPATIBLE"),
    }
    assert all(u["source"] == "workout_plan_template_slot_items" and u["rows"] == 1 for u in pending)

    # marcar compativel sem liberar a falha ainda nao resolve (validacao defensiva)
    client.put(f"/equipment/{EQUIPMENT_ID}/safety", json={"techniques": {"drop_set": True}}, headers=HEADERS)
    _, pending = _mine(_report(backend, monkeypatch, fake_rds))
    mine = next(u for u in pending if u["equipment_id"] == EQUIPMENT_ID)
    assert (mine["marking"], mine["reason"], mine["max_safe_rpe"]) == ("compatible", "FAILURE_REQUIRED_NOT_ALLOWED", 9)

    client.put(f"/equipment/{EQUIPMENT_ID}/safety", json={"max_safe_rpe": 10}, headers=HEADERS)
    without_rpe, pending = _mine(_report(backend, monkeypatch, fake_rds))
    assert [e["equipment_id"] for e in without_rpe] == [OTHER_EQUIPMENT_ID]
    assert [u["equipment_id"] for u in pending] == [OTHER_EQUIPMENT_ID]


def test_relatorio_roda_antes_das_migracoes_032_033(backend, sraw, domain, template_usage, monkeypatch, fake_rds):
    """Producao hoje (032/033 pendentes): o relatorio mede o backfill sem o schema novo."""
    _drop_pr07_schema(sraw)
    try:
        result = _report(backend, monkeypatch, fake_rds)
        assert result["schema_pending"] is True
        assert result["schema"]["equipment_columns"] is False and result["schema"]["compatibility_table"] is False
        without_rpe, pending = _mine(result)
        assert len(without_rpe) == 2 and len(pending) == 2
        assert result["equipment_without_max_safe_rpe_active"] == result["equipment_total"]
    finally:
        _run(MIGRATION_032)
        _run(MIGRATION_033)

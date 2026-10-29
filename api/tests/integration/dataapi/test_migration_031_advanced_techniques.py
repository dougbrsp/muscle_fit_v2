"""Migracao 031 (catalogo `advanced_techniques`), `GET /advanced-techniques`,
validacao do builder de modelos e relatorio de legado - nos dois backends:
PyMySQL e o caminho de PRODUCAO (shim `app/db_dataapi.py` + dublê da RDS Data
API).

- banco sem a tabela (Aurora, DEV e CI hoje): 1 CREATE + 8 INSERT, um statement
  por chamada; reexecucao com 0 statements e sem escrita;
- linha ja existente nunca e alterada; so o codigo que falta e reinserido;
- tabela em formato inesperado: falha sem aplicar;
- rota: 8 codigos, campos tipados, inativo so com include_inactive, sem
  escrita/DDL, exige autenticacao;
- POST /workout-plan-templates: aceita os 8 codigos, 422 para inexistente e
  inativo, sem leitura do catalogo quando nao ha tecnica.

O runner 001..031 em banco novo saiu daqui no M38 (2a passada): a cadeia
inteira roda UMA vez por backend em
`test_runner_cadeia_completa.py::test_runner_001_031_em_banco_novo_aplica_a_031`,
com as MESMAS assercoes. Os codigos e a leitura do catalogo vivem em
`tests/support/advanced_techniques_031.py`, compartilhados pelos dois arquivos.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import advanced_techniques as at
from app import advanced_techniques_report as report
from app import migrate as m
from app import workout_plan_templates as wpt
from app.db_pymysql import Connection as PyMySQLConnection
from tests.support.advanced_techniques_031 import CODES
from tests.support.advanced_techniques_031 import MIGRATION_031 as MIGRATION_PATH
from tests.support.advanced_techniques_031 import rows as _rows
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations
from tests.support.sql_capture import ddl, recorded_sql, writes

MASTER = "advanced-techniques-master"
HEADERS = {"X-API-Token": MASTER}
INSERT = (
    "INSERT INTO advanced_techniques (code, name, failure_requirement, requires_partner, auto_applicable, "
    "min_experience, set_type) VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
GROUP_ID, EQUIPMENT_ID, EXERCISE_ID = 9301, 9302, 9303


def _kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


def _pymysql_factory(database: str):
    kw = _kwargs()
    return lambda: PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], database)


@pytest.fixture(scope="module")
def schema() -> str:
    """Banco novo com TODAS as migracoes aplicadas pelo runner (inclui a 031)."""
    base = os.environ["MYSQL_DATABASE"]
    database = f"{base[: -len('_test')]}_p031_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    m.migrate(conn_factory=_pymysql_factory(database))
    return database


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


def _run(dry_run: bool = False) -> list[str]:
    import app.db as db  # atributo trocado por use_dataapi

    conn = db.get_conn()
    try:
        facade = m.MigrationDB(conn, dry_run=dry_run)
        m.load_python_migration(MIGRATION_PATH)(facade)
        if not dry_run:
            conn.commit()
        return facade.statements
    finally:
        conn.close()


def _table_exists(raw) -> bool:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'advanced_techniques'"
        )
        return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Migracao
# ---------------------------------------------------------------------------


# Migracoes posteriores a 031 que dependem de `advanced_techniques`: o teste
# abaixo precisa dropar a tabela, entao desfaz o que depende dela e, no fim
# (mesmo se falhar), reaplica TUDO na ordem do runner - o schema do modulo
# volta ao estado de producao para os testes seguintes.
#   033: tabelas com FK para advanced_techniques(code) (saem inteiras);
#   059: FK exercise_sets.technique_code -> advanced_techniques(code) (so a FK
#        sai; as colunas ficam e a propria 059 recria a FK);
#   060: colunas/coeficientes do catalogo (voltam com a tabela recriada).
DEPENDENTES_DA_031 = (
    "033_equipment_technique_compatibility.py",
    "059_exercise_sets_tecnica_e_ancora.py",
    "060_tecnicas_coeficientes_e_backfill.py",
)


def _fks_para_advanced_techniques(raw) -> list[tuple[str, str]]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME, CONSTRAINT_NAME FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME = 'advanced_techniques'"
        )
        return [(row["TABLE_NAME"], row["CONSTRAINT_NAME"]) for row in cur.fetchall()]


def _restaura_schema_do_runner() -> None:
    import app.db as db  # atributo trocado por use_dataapi

    conn = db.get_conn()
    try:
        paths = (MIGRATION_PATH, *(m.DEFAULT_MIGRATIONS_DIR / f for f in DEPENDENTES_DA_031))
        for path in paths:
            m.load_python_migration(path)(m.MigrationDB(conn))
            conn.commit()
    finally:
        conn.close()


def test_banco_sem_a_tabela_cria_e_semeia_e_a_reexecucao_nao_escreve(backend, sraw, monkeypatch, fake_rds):
    try:
        with sraw.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS equipment_exercise_technique_overrides")
            cur.execute("DROP TABLE IF EXISTS equipment_technique_compatibility")
            for table, constraint in _fks_para_advanced_techniques(sraw):
                cur.execute(f"ALTER TABLE `{table}` DROP FOREIGN KEY `{constraint}`")
            cur.execute("DROP TABLE advanced_techniques")

        plan = _run(dry_run=True)
        assert len(plan) == 9 and plan[0].startswith("CREATE TABLE IF NOT EXISTS advanced_techniques (")
        assert plan[1:] == [INSERT] * 8
        assert not _table_exists(sraw)  # dry-run nao altera nada

        with recorded_sql(backend, monkeypatch, fake_rds) as sent:
            assert _run() == plan
        assert len(ddl(sent)) == 1 and len(writes(sent)) == 9  # um statement por chamada

        rows = _rows(sraw)
        assert sorted(rows) == sorted(CODES)
        assert rows["forced_reps"]["name"] == "Repetições forçadas"  # acento integro nos dois backends
        assert rows["forced_reps"]["requires_partner"] == 1 and rows["negative_reps"]["requires_partner"] == 1
        assert [c for c in CODES if rows[c]["auto_applicable"]] == ["rest_pause", "drop_set"]
        assert {float(r["activation_set_equivalent"]) for r in rows.values()} == {0.0}
        assert {r["recovery_cost_factor"] for r in rows.values()} == {None}
        assert {r["is_active"] for r in rows.values()} == {1}
        assert rows["gvt"]["failure_requirement"] == "failure_not_required"
        assert rows["cheat_reps"]["set_type"] == "forced_rep" and rows["fst7"]["set_type"] == "working"

        with recorded_sql(backend, monkeypatch, fake_rds) as sent:
            assert _run() == []
        assert writes(sent) == []
    finally:
        _restaura_schema_do_runner()

    # isolamento: dependentes de volta (FK da 059 e coeficientes da 060)
    assert ("exercise_sets", "fk_sets_technique") in _fks_para_advanced_techniques(sraw)
    assert float(_rows(sraw)["forced_reps"]["activation_set_equivalent"]) == 0.25


def test_linha_existente_nunca_e_alterada_e_so_o_codigo_que_falta_volta(backend, sraw):
    with sraw.cursor() as cur:
        cur.execute("UPDATE advanced_techniques SET is_active = 0, activation_set_equivalent = 0.50 WHERE code = 'gvt'")
        cur.execute("DELETE FROM advanced_techniques WHERE code = 'fst7'")
    try:
        assert _run() == [INSERT]
        rows = _rows(sraw)
        assert sorted(rows) == sorted(CODES)
        assert rows["gvt"]["is_active"] == 0 and float(rows["gvt"]["activation_set_equivalent"]) == 0.5
    finally:
        with sraw.cursor() as cur:
            cur.execute("UPDATE advanced_techniques SET is_active = 1, activation_set_equivalent = 0 WHERE code = 'gvt'")


def test_tabela_em_formato_inesperado_falha_sem_aplicar(backend, sraw):
    with sraw.cursor() as cur:
        cur.execute("DELETE FROM advanced_techniques WHERE code = 'fst7'")
        cur.execute("ALTER TABLE advanced_techniques MODIFY COLUMN set_type VARCHAR(10) NOT NULL")
    try:
        with pytest.raises(Exception, match="Nenhum statement executado"):
            _run()
        assert "fst7" not in _rows(sraw)
    finally:
        with sraw.cursor() as cur:
            cur.execute("ALTER TABLE advanced_techniques MODIFY COLUMN set_type VARCHAR(40) NOT NULL")
        assert _run() == [INSERT]


# ---------------------------------------------------------------------------
# GET /advanced-techniques
# ---------------------------------------------------------------------------


@pytest.fixture
def client(backend, sraw) -> TestClient:
    with sraw.cursor() as cur:
        cur.execute("INSERT IGNORE INTO users (username, password_hash, role) VALUES ('admin-tecnicas', 'x', 'admin')")
    app = FastAPI()
    app.include_router(at.router)
    app.include_router(wpt.router)
    return TestClient(app, raise_server_exceptions=False)


def test_rota_devolve_os_8_codigos_tipados_sem_escrever(backend, client, monkeypatch, fake_rds):
    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        response = client.get("/advanced-techniques", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert writes(sent) == []
    items = {item["code"]: item for item in response.json()["items"]}
    assert sorted(items) == sorted(CODES)
    assert items["forced_reps"] == {
        "code": "forced_reps",
        "name": "Repetições forçadas",
        "failure_requirement": "beyond_failure",
        "requires_partner": True,
        "auto_applicable": False,
        "min_experience": "advanced",
        "set_type": "forced_rep",
        # coeficientes calibrados pela 060 (o schema do modulo tem todas as migracoes)
        "activation_set_equivalent": 0.25,
        "recovery_cost_factor": 1.2,
        "max_equivalent_per_anchor": 0.5,
        "default_sets_count": None,
        "is_active": True,
    }
    # Fase 5 (PR 3): todas as linhas expõem os campos da 060. fst7/gvt não são
    # conferidos por valor aqui: outros testes deste módulo apagam e re-semeiam
    # a fst7 só pela 031 (sem os coeficientes da 060).
    assert all("max_equivalent_per_anchor" in i and "default_sets_count" in i for i in items.values())
    assert items["rest_pause"]["max_equivalent_per_anchor"] == 1.0
    assert items["rest_pause"]["default_sets_count"] is None


def test_rota_usa_cache_do_processo(backend, client, monkeypatch, fake_rds):
    client.get("/advanced-techniques", headers=HEADERS)
    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert client.get("/advanced-techniques", headers=HEADERS).status_code == 200
    assert [s for s in sent if "advanced_techniques" in s] == []


def test_rota_exige_autenticacao(client):
    assert client.get("/advanced-techniques").status_code == 401


def test_inativo_so_aparece_com_include_inactive(backend, client, sraw):
    with sraw.cursor() as cur:
        cur.execute("UPDATE advanced_techniques SET is_active = 0 WHERE code = 'cheat_reps'")
    try:
        at.clear_cache()
        active = [i["code"] for i in client.get("/advanced-techniques", headers=HEADERS).json()["items"]]
        assert "cheat_reps" not in active and len(active) == 7
        everything = client.get("/advanced-techniques?include_inactive=true", headers=HEADERS).json()["items"]
        assert {i["code"]: i["is_active"] for i in everything}["cheat_reps"] is False
    finally:
        with sraw.cursor() as cur:
            cur.execute("UPDATE advanced_techniques SET is_active = 1 WHERE code = 'cheat_reps'")


# ---------------------------------------------------------------------------
# Validacao no builder de modelos
# ---------------------------------------------------------------------------


def _template(technique, name="Modelo 031") -> dict:
    return {
        "name": name,
        "volume_mode": "equipment_series",
        "slots": [
            {
                "sequence": 1,
                "name": "A",
                "items": [
                    {
                        "item_order": 1,
                        "item_kind": "equipment",
                        "muscle_group_id": GROUP_ID,
                        "equipment_id": EQUIPMENT_ID,
                        "mapping_id": EXERCISE_ID,
                        "sets_count": 3,
                        "advanced_technique": technique,
                        "advanced_technique_applications": 2,
                    }
                ],
            }
        ],
    }


@pytest.fixture
def domain(sraw):
    """Par exercicio x equipamento SEGURO e COMPATIVEL com todo o catalogo.

    O `max_safe_rpe = 10` e as marcacoes de compatibilidade entraram com o
    PR-12: a validacao de seguranca do save (D14) rejeita item NOVO com
    tecnica invalida, e o default D3 e conservador (tecnica sem marcacao =
    incompativel; teto NULL = 9, sem falha permitida). Sem isto, estes testes
    - que sao sobre o CATALOGO de tecnicas (PR-06), nao sobre seguranca -
    passariam a bater no 422 da seguranca antes de chegar ao que verificam."""
    with sraw.cursor() as cur:
        cur.execute("INSERT IGNORE INTO muscle_groups (id, name) VALUES (%s, 'Peitoral 031')", (GROUP_ID,))
        cur.execute(
            "INSERT INTO equipment (id, name, max_safe_rpe) VALUES (%s, 'Banco 031', 10) "
            "ON DUPLICATE KEY UPDATE max_safe_rpe = 10",
            (EQUIPMENT_ID,),
        )
        cur.execute("INSERT IGNORE INTO exercise_catalog (id, name, is_active) VALUES (%s, 'Supino 031', 1)", (EXERCISE_ID,))
        for code in CODES:
            cur.execute(
                "INSERT INTO equipment_technique_compatibility (equipment_id, technique_code, is_compatible) "
                "VALUES (%s, %s, 1) ON DUPLICATE KEY UPDATE is_compatible = 1",
                (EQUIPMENT_ID, code),
            )
    yield
    with sraw.cursor() as cur:
        cur.execute("DELETE FROM workout_plan_templates WHERE name LIKE 'Modelo 031%'")
        cur.execute("DELETE FROM equipment_technique_compatibility WHERE equipment_id = %s", (EQUIPMENT_ID,))


def _template_count(raw) -> int:
    with raw.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM workout_plan_templates WHERE name LIKE 'Modelo 031%'")
        return int(cur.fetchone()["n"])


def test_builder_aceita_os_8_codigos_do_catalogo(backend, client, domain, sraw):
    for code in CODES:
        response = client.post("/workout-plan-templates", json=_template(code, f"Modelo 031 {code}"), headers=HEADERS)
        assert response.status_code == 200, (code, response.text)
        item = response.json()["slots"][0]["items"][0]
        assert item["advanced_technique"] == code
    assert _template_count(sraw) == 8


@pytest.mark.parametrize("code", ["super_set", "dropset", "REST_PAUSE ", "", "x" * 21])
def test_builder_rejeita_codigo_fora_do_catalogo_com_422_sem_gravar(backend, client, domain, sraw, code):
    response = client.post("/workout-plan-templates", json=_template(code), headers=HEADERS)
    assert response.status_code == 422, response.text
    assert _template_count(sraw) == 0


def test_builder_rejeita_codigo_inativo(backend, client, domain, sraw):
    with sraw.cursor() as cur:
        cur.execute("UPDATE advanced_techniques SET is_active = 0 WHERE code = 'gvt'")
    try:
        at.clear_cache()
        response = client.post("/workout-plan-templates", json=_template("gvt"), headers=HEADERS)
        assert response.status_code == 422
        assert "gvt" in response.json()["detail"]
        assert _template_count(sraw) == 0
    finally:
        with sraw.cursor() as cur:
            cur.execute("UPDATE advanced_techniques SET is_active = 1 WHERE code = 'gvt'")


def test_payload_sem_tecnica_nao_le_o_catalogo(backend, client, domain, monkeypatch, fake_rds):
    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        response = client.post("/workout-plan-templates", json=_template(None), headers=HEADERS)
    assert response.status_code == 200, response.text
    assert [s for s in sent if "FROM advanced_techniques" in s] == []


# ---------------------------------------------------------------------------
# Relatorio de legado (somente leitura)
# ---------------------------------------------------------------------------


def test_relatorio_lista_valores_fora_do_catalogo_sem_escrever(backend, client, domain, sraw, monkeypatch, fake_rds):
    assert client.post("/workout-plan-templates", json=_template("drop_set"), headers=HEADERS).status_code == 200
    with sraw.cursor() as cur:
        cur.execute(
            "UPDATE workout_plan_template_slot_items SET advanced_technique = 'bi_set' "
            "WHERE mapping_id = %s AND advanced_technique = 'drop_set'",
            (EXERCISE_ID,),
        )
    import app.db as db

    conn = db.get_conn()
    try:
        with recorded_sql(backend, monkeypatch, fake_rds) as sent:
            result = report.build_report(conn)
    finally:
        conn.close()
    assert writes(sent) == []
    by_table = {t["table"]: t for t in result["tables"]}
    assert by_table["workout_plan_template_slot_items"]["invalid"] == {"bi_set": 1}
    assert by_table["workout_plan_exercises"]["invalid"] == {}
    assert result["total_invalid_rows"] == 1
    assert result["catalog_codes"] == sorted(CODES)

"""PR-08/PR-08b nos dois backends (PyMySQL e o caminho de PRODUCAO: shim
`app/db_dataapi.py` + duble da RDS Data API). Estrelas por PAR
exercicio+equipamento (`equipment_exercises.id`), decisao do usuario em
2026-09-17:

- migracao 035 nos dois cenarios: 034 aplicada antes (o deploy real: cria a
  tabela nova e remove a antiga, um statement por chamada) e estado
  convergido (sem escrita); formato inesperado falha sem aplicar; CHECK 1-5 e
  cascata das FKs (o runner 001..035 em banco novo foi para
  `test_runner_cadeia_completa.py`, M38);
- /me/equipment-exercise-preferences: 401 sem identidade, 422 fora de 1-5 ou
  com `user_id` no corpo, 404 de par inexistente, upsert, DELETE idempotente,
  ISOLAMENTO entre usuarios; as rotas antigas por equipamento nao existem mais;
- recomendador: a nota e DO PAR (mesmo equipamento com notas diferentes por
  exercicio so mexe no exercicio avaliado), ausente = 3, empate de estrelas
  -> recencia DO USUARIO, outro usuario nao interfere (G5), par sem vinculo
  em `equipment_exercises` fica sem nota, campos de seguranca so informam,
  `required_technique` exclui com o motivo certo, contrato aditivo,
  determinismo, numero FIXO de SELECTs (sem N+1), sem escrita, tabela ausente
  nao derruba o recomendador;
- `services.resolve_equipment_id` e a geracao de ficha usam o mesmo ranking.

Schema proprio (`*_p08_test`), apagado ao final.
"""

from __future__ import annotations

import hashlib
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import advanced_techniques as at
from app import equipment_exercise_preferences, gyms
from app import migrate as m
from app import services
from app.db_pymysql import Connection as PyMySQLConnection
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations
from tests.support import preferences_034
from tests.support.preferences_035 import EXPECTED_COLUMNS, OLD_TABLE, TABLE, apply
from tests.support.sql_capture import ddl, recorded_sql, writes

MASTER = "pr08-master"
TOKEN_A, TOKEN_B = "pr08-aluno-a", "pr08-aluno-b"
HEADERS_A, HEADERS_B = {"X-API-Token": TOKEN_A}, {"X-API-Token": TOKEN_B}
GROUP = "Peitoral P08"
EX_SUPINO, EX_CRUCIFIXO = 9801, 9802
EQ_ARTICULADO, EQ_BARRA, EQ_CROSSOVER, EQ_FORA = 9811, 9812, 9813, 9814


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
    """Banco novo com TODAS as migracoes aplicadas pelo runner (034 e 035: so a tabela por PAR sobra)."""
    database = _database("p08")
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


def _run(dry_run: bool = False, migration=apply) -> list[str]:
    import app.db as db  # atributo trocado por use_dataapi

    conn = db.get_conn()
    try:
        return migration(conn, dry_run=dry_run)
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


# ---------------------------------------------------------------------------
# Migracao 035
# ---------------------------------------------------------------------------

DROP_OLD = f"DROP TABLE IF EXISTS {OLD_TABLE}"


def test_034_aplicada_antes_cria_a_nova_e_remove_a_antiga_e_a_reexecucao_nao_escreve(backend, sraw, monkeypatch, fake_rds):
    """Cenario do deploy em producao (034 e 035 sobem juntas) e, em seguida, o
    estado convergido. Nota por equipamento gravada em DEV e descartada."""
    with sraw.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
        cur.execute(f"DROP TABLE IF EXISTS {OLD_TABLE}")
    assert len(_run(migration=preferences_034.apply)) == 1
    assert _columns(sraw, OLD_TABLE) == preferences_034.EXPECTED_COLUMNS

    planned = _run(dry_run=True)
    assert len(planned) == 2
    assert planned[0].lstrip().startswith(f"CREATE TABLE IF NOT EXISTS {TABLE}")
    assert planned[1] == DROP_OLD
    assert _columns(sraw, TABLE) == {} and _columns(sraw, OLD_TABLE) != {}  # dry-run nao altera nada

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == planned
    assert len(ddl(sent)) == 2 and len(writes(sent)) == 2  # um statement por chamada

    assert _columns(sraw, OLD_TABLE) == {}
    assert _columns(sraw, TABLE) == EXPECTED_COLUMNS
    with sraw.cursor() as cur:
        cur.execute(
            "SELECT INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS cols FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s GROUP BY INDEX_NAME",
            (TABLE,),
        )
        indexes = {r["INDEX_NAME"]: r["cols"] for r in cur.fetchall()}
        cur.execute(
            "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL",
            (TABLE,),
        )
        fks = {r["COLUMN_NAME"]: r["REFERENCED_TABLE_NAME"] for r in cur.fetchall()}
    assert indexes["PRIMARY"] == "user_id,equipment_exercise_id"
    assert indexes["idx_ueep_user_stars"] == "user_id,stars"
    assert fks == {"user_id": "users", "equipment_exercise_id": "equipment_exercises"}

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []  # convergido
    assert writes(sent) == []


def test_formato_inesperado_falha_sem_aplicar(backend, sraw):
    with sraw.cursor() as cur:
        cur.execute(f"ALTER TABLE {TABLE} DROP INDEX idx_ueep_user_stars")
    try:
        with pytest.raises(Exception, match="035: indice idx_ueep_user_stars.*Nenhum statement executado"):
            _run()
    finally:
        with sraw.cursor() as cur:
            cur.execute(f"ALTER TABLE {TABLE} ADD KEY idx_ueep_user_stars (user_id, stars)")
    assert _run() == []


def test_tabela_antiga_que_nao_e_a_da_034_nao_e_removida(backend, sraw):
    with sraw.cursor() as cur:
        cur.execute(f"CREATE TABLE {OLD_TABLE} (id INT PRIMARY KEY, outra_coisa INT)")
    try:
        with pytest.raises(Exception, match=f"035: {OLD_TABLE} tem colunas.*Nenhum statement executado"):
            _run()
        assert _columns(sraw, OLD_TABLE) != {}
    finally:
        with sraw.cursor() as cur:
            cur.execute(f"DROP TABLE {OLD_TABLE}")
    assert _run() == []


# O runner 001..035 em banco novo saiu daqui no M38 (2a passada): a cadeia
# inteira roda UMA vez por backend em `test_runner_cadeia_completa.py::
# test_runner_em_banco_novo_aplica_034_e_035_e_so_sobra_a_tabela_por_par`, com
# as MESMAS assercoes.


# ---------------------------------------------------------------------------
# Dominio de teste
# ---------------------------------------------------------------------------


def _session(cur, username: str, token: str) -> int:
    cur.execute("INSERT IGNORE INTO users (username, password_hash, role) VALUES (%s, 'x', 'user')", (username,))
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    user_id = int(cur.fetchone()["id"])
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cur.execute("DELETE FROM user_sessions WHERE token_hash = %s", (token_hash,))
    cur.execute(
        "INSERT INTO user_sessions (user_id, token_hash, expires_at) VALUES (%s, %s, NOW() + INTERVAL 1 DAY)",
        (user_id, token_hash),
    )
    return user_id


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        "SELECT 1 AS ok FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,)
    )
    return bool(cur.fetchone())


@pytest.fixture
def domain(sraw):
    """Academia com 3 associacoes no grupo (2 exercicios), 2 alunos com sessao.
    `pair_*` = `equipment_exercises.id` (ancora da nota).

        ge_articulado : Supino articulado P08 x Supino reto
        ge_barra      : Barra livre P08       x Supino reto
        ge_crossover  : Crossover P08         x Crucifixo
    """
    ids: dict = {}
    with sraw.cursor() as cur:
        cur.execute("INSERT IGNORE INTO users (username, password_hash, role) VALUES ('admin-p08', 'x', 'admin')")
        ids["user_a"] = _session(cur, "aluno-a-p08", TOKEN_A)
        ids["user_b"] = _session(cur, "aluno-b-p08", TOKEN_B)
        cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (GROUP,))
        ids["group"] = cur.lastrowid
        legacy = _table_exists(cur, "exercise_zepp_mappings")
        for exercise_id, name in ((EX_SUPINO, "Supino reto P08"), (EX_CRUCIFIXO, "Crucifixo P08")):
            if legacy:  # gym_equipment.mapping_id ainda pode ter FK para a tabela legada
                cur.execute(
                    "INSERT IGNORE INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                    (exercise_id, name, name),
                )
            cur.execute("INSERT INTO exercise_catalog (id, name, is_active) VALUES (%s, %s, 1)", (exercise_id, name))
            cur.execute(
                "INSERT INTO exercise_muscle_contributions (exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) "
                "VALUES (%s, %s, 'PRIMARY', 1, 1)",
                (exercise_id, ids["group"]),
            )
        for equipment_id, name in (
            (EQ_ARTICULADO, "Supino articulado P08"),
            (EQ_BARRA, "Barra livre P08"),
            (EQ_CROSSOVER, "Crossover P08"),
            (EQ_FORA, "Fora da academia P08"),
        ):
            cur.execute("INSERT INTO equipment (id, name) VALUES (%s, %s)", (equipment_id, name))
        for key, equipment_id, exercise_id in (
            ("pair_articulado", EQ_ARTICULADO, EX_SUPINO),
            ("pair_barra", EQ_BARRA, EX_SUPINO),
            ("pair_crossover", EQ_CROSSOVER, EX_CRUCIFIXO),
            ("pair_fora", EQ_FORA, EX_CRUCIFIXO),  # par do catalogo que a academia nao tem
        ):
            cur.execute(
                "INSERT INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)", (equipment_id, exercise_id)
            )
            ids[key] = cur.lastrowid
        cur.execute("SELECT id FROM users WHERE username = 'admin-p08'")
        cur.execute("INSERT INTO gyms (name, owner_user_id) VALUES ('Academia P08', %s)", (cur.fetchone()["id"],))
        ids["gym"] = cur.lastrowid
        for key, equipment_id, exercise_id in (
            ("ge_articulado", EQ_ARTICULADO, EX_SUPINO),
            ("ge_barra", EQ_BARRA, EX_SUPINO),
            ("ge_crossover", EQ_CROSSOVER, EX_CRUCIFIXO),
        ):
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s, %s, %s, 1)",
                (ids["gym"], exercise_id, equipment_id),
            )
            ids[key] = cur.lastrowid
    yield ids
    with sraw.cursor() as cur:
        cur.execute("DELETE FROM activities WHERE user_id IN (%s, %s)", (ids["user_a"], ids["user_b"]))
        cur.execute("DELETE FROM gym_equipment WHERE gym_id = %s", (ids["gym"],))
        cur.execute("DELETE FROM gyms WHERE id = %s", (ids["gym"],))
        cur.execute(  # cascata: equipment_exercises, marcacoes de tecnica e preferencias
            "DELETE FROM equipment WHERE id IN (%s, %s, %s, %s)", (EQ_ARTICULADO, EQ_BARRA, EQ_CROSSOVER, EQ_FORA)
        )
        cur.execute("DELETE FROM exercise_muscle_contributions WHERE muscle_group_id = %s", (ids["group"],))
        cur.execute("DELETE FROM exercise_catalog WHERE id IN (%s, %s)", (EX_SUPINO, EX_CRUCIFIXO))
        if _table_exists(cur, "exercise_zepp_mappings"):
            cur.execute("DELETE FROM exercise_zepp_mappings WHERE id IN (%s, %s)", (EX_SUPINO, EX_CRUCIFIXO))
        cur.execute("DELETE FROM muscle_groups WHERE id = %s", (ids["group"],))
        cur.execute("DELETE FROM user_sessions WHERE user_id IN (%s, %s)", (ids["user_a"], ids["user_b"]))


@pytest.fixture
def client(backend, domain) -> TestClient:
    app = FastAPI()
    app.include_router(equipment_exercise_preferences.router)
    app.include_router(gyms.router)
    return TestClient(app, raise_server_exceptions=False)


def _use(sraw, user_id: int, gym_equipment_id: int, when: str) -> None:
    """Registra um treino do usuario naquela associacao em `when`."""
    with sraw.cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, activity_type) "
            "VALUES (%s, DATE(%s), %s, 'Treino P08', 'Musculacao')",
            (user_id, when, when),
        )
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, equipment_id, sets_count) "
            "VALUES (%s, 1, 'Supino', NULL, %s, 1)",
            (cur.lastrowid, gym_equipment_id),
        )


def _pair_id(cur, equipment_id: int, exercise_id: int | None = None) -> int:
    """`equipment_exercises.id`; sem `exercise_id`, o par mais antigo do equipamento (o do dominio)."""
    sql, params = "SELECT id FROM equipment_exercises WHERE equipment_id = %s", [equipment_id]
    if exercise_id is not None:
        sql, params = sql + " AND exercise_id = %s", [equipment_id, exercise_id]
    cur.execute(sql + " ORDER BY id ASC LIMIT 1", tuple(params))
    return int(cur.fetchone()["id"])


def _stars(sraw, user_id: int, equipment_id: int, stars: int, exercise_id: int | None = None) -> None:
    """Nota do usuario para o PAR (equipamento, exercicio)."""
    with sraw.cursor() as cur:
        cur.execute(
            f"REPLACE INTO {TABLE} (user_id, equipment_exercise_id, stars) VALUES (%s, %s, %s)",
            (user_id, _pair_id(cur, equipment_id, exercise_id), stars),
        )


def _recommend(client, domain, headers=HEADERS_A, **params) -> dict:
    response = client.get(
        "/gym-equipment/recommend", params={"gym_id": domain["gym"], "primary_group": GROUP, **params}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _app_writes(sent: list[str]) -> list[str]:
    """Escritas da rota, fora o `last_seen_at` da sessao (resolucao de identidade)."""
    return [sql for sql in writes(sent) if not sql.startswith("UPDATE user_sessions SET last_seen_at")]


def _order(body: dict) -> list[int]:
    return [item["id"] for item in body["items"]]


# ---------------------------------------------------------------------------
# Banco: CHECK e cascata
# ---------------------------------------------------------------------------


def test_check_1_a_5_e_cascata_das_fks(sraw, domain):
    for invalid in (0, 6):
        with pytest.raises(Exception, match="chk_ueep_stars|Check constraint"):
            _stars(sraw, domain["user_a"], EQ_FORA, invalid)
    _stars(sraw, domain["user_a"], EQ_FORA, 5)
    _stars(sraw, domain["user_a"], EQ_CROSSOVER, 4)

    def count(pair_id: int) -> int:
        with sraw.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM {TABLE} WHERE equipment_exercise_id = %s", (pair_id,))
            return int(cur.fetchone()["n"])

    assert count(domain["pair_fora"]) == count(domain["pair_crossover"]) == 1
    with sraw.cursor() as cur:
        cur.execute("DELETE FROM equipment WHERE id = %s", (EQ_FORA,))  # equipment -> equipment_exercises -> nota
        cur.execute("DELETE FROM equipment_exercises WHERE id = %s", (domain["pair_crossover"],))  # desvincular o par
    assert count(domain["pair_fora"]) == count(domain["pair_crossover"]) == 0


# ---------------------------------------------------------------------------
# /me/equipment-exercise-preferences
# ---------------------------------------------------------------------------

ROUTE = "/me/equipment-exercise-preferences"


def test_preferencias_exigem_identidade(client, domain):
    assert client.get(ROUTE).status_code == 401
    assert client.put(f"{ROUTE}/{domain['pair_barra']}", json={"stars": 4}).status_code == 401
    assert client.delete(f"{ROUTE}/{domain['pair_barra']}").status_code == 401


def test_rotas_antigas_por_equipamento_nao_existem_mais(client):
    assert client.get("/me/equipment-preferences", headers=HEADERS_A).status_code == 404
    assert client.put(f"/me/equipment-preferences/{EQ_BARRA}", json={"stars": 4}, headers=HEADERS_A).status_code == 404


@pytest.mark.parametrize("payload", [{"stars": 0}, {"stars": 6}, {"stars": "5"}, {"stars": 2.5}, {"stars": None}, {}])
def test_put_fora_de_1_a_5_e_422_e_nao_grava(client, domain, sraw, payload):
    assert client.put(f"{ROUTE}/{domain['pair_barra']}", json=payload, headers=HEADERS_A).status_code == 422
    with sraw.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM {TABLE} WHERE user_id = %s", (domain["user_a"],))
        assert cur.fetchone()["n"] == 0


def test_user_id_do_cliente_nunca_e_aceito(client, domain, sraw):
    url = f"{ROUTE}/{domain['pair_barra']}"
    assert client.put(url, json={"stars": 5, "user_id": domain["user_b"]}, headers=HEADERS_A).status_code == 422
    response = client.put(url, params={"user_id": domain["user_b"]}, json={"stars": 5}, headers=HEADERS_A)
    assert response.status_code == 200  # query desconhecida e ignorada: grava para o usuario corrente
    with sraw.cursor() as cur:
        cur.execute(f"SELECT user_id FROM {TABLE} WHERE equipment_exercise_id = %s", (domain["pair_barra"],))
        assert [int(r["user_id"]) for r in cur.fetchall()] == [domain["user_a"]]


def test_par_inexistente_e_404(client, domain):
    assert client.put(f"{ROUTE}/99999999", json={"stars": 3}, headers=HEADERS_A).status_code == 404
    # id de EQUIPAMENTO nao e id de par
    assert client.put(f"{ROUTE}/{EQ_BARRA}", json={"stars": 3}, headers=HEADERS_A).status_code == 404


def test_usuario_comum_avalia_sem_ser_admin_e_o_get_traz_o_par_completo(client, domain):
    """A tela e o Dominio de exercicios (catalogo = admin); a nota e de qualquer usuario logado."""
    created = client.put(f"{ROUTE}/{domain['pair_barra']}", json={"stars": 2}, headers=HEADERS_A)
    assert created.status_code == 200
    expected = {
        "equipment_exercise_id": domain["pair_barra"],
        "equipment_id": EQ_BARRA,
        "equipment_name": "Barra livre P08",
        "exercise_id": EX_SUPINO,
        "mapping_id": EX_SUPINO,
        "exercise_name": "Supino reto P08",
        "stars": 2,
    }
    assert {k: created.json()["item"][k] for k in expected} == expected
    listed = client.get(ROUTE, headers=HEADERS_A).json()
    assert listed["ok"] is True and listed["default_stars"] == 3
    assert [{k: item[k] for k in expected} for item in listed["items"]] == [expected]


def test_upsert_lista_e_delete_com_isolamento_entre_usuarios(backend, client, domain, monkeypatch, fake_rds):
    barra, crossover = domain["pair_barra"], domain["pair_crossover"]
    assert client.get(ROUTE, headers=HEADERS_A).json() == {"ok": True, "default_stars": 3, "items": []}

    assert client.put(f"{ROUTE}/{barra}", json={"stars": 2}, headers=HEADERS_A).status_code == 200
    assert client.put(f"{ROUTE}/{barra}", json={"stars": 5}, headers=HEADERS_A).json()["item"]["stars"] == 5
    assert client.put(f"{ROUTE}/{barra}", json={"stars": 1}, headers=HEADERS_B).status_code == 200
    assert client.put(f"{ROUTE}/{crossover}", json={"stars": 4}, headers=HEADERS_B).status_code == 200

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        items_a = client.get(ROUTE, headers=HEADERS_A).json()["items"]
        items_b = client.get(ROUTE, headers=HEADERS_B).json()["items"]
    assert _app_writes(sent) == []
    assert [(i["equipment_exercise_id"], i["stars"]) for i in items_a] == [(barra, 5)]
    assert [(i["equipment_exercise_id"], i["stars"]) for i in items_b] == [(barra, 1), (crossover, 4)]

    # B apaga a PROPRIA avaliacao; a de A continua
    assert client.delete(f"{ROUTE}/{barra}", headers=HEADERS_B).json() == {
        "ok": True,
        "equipment_exercise_id": barra,
        "deleted": True,
    }
    assert client.delete(f"{ROUTE}/{barra}", headers=HEADERS_B).json()["deleted"] is False
    assert [(i["equipment_exercise_id"], i["stars"]) for i in client.get(ROUTE, headers=HEADERS_A).json()["items"]] == [
        (barra, 5)
    ]


# ---------------------------------------------------------------------------
# Recomendador
# ---------------------------------------------------------------------------


def test_sem_estrelas_nem_historico_ordena_por_nome_e_informa_os_defaults_d3(client, domain):
    body = _recommend(client, domain)
    assert _order(body) == [domain["ge_barra"], domain["ge_crossover"], domain["ge_articulado"]]
    assert body["recommended"]["id"] == domain["ge_barra"]
    assert [i["id"] for i in body["alternatives"]] == [domain["ge_crossover"], domain["ge_articulado"]]
    assert body["excluded"] == [] and body["required_technique"] is None
    assert body["has_equipment_for_group"] is True
    for item in body["items"]:
        assert item["gym_equipment_id"] == item["id"]
        assert item["user_preference_stars"] is None and item["last_used_by_user_at"] is None
        # D3: nada cadastrado -> so informa o default conservador; ninguem some da lista
        assert (item["max_safe_rpe"], item["failure_allowed"], item["allowed_advanced_techniques"], item["safety_source"]) == (
            9,
            False,
            [],
            None,
        )
        # contrato anterior preservado (campos so aditivos)
        assert {
            "id", "mapping_id", "equipment_id", "equipment_name", "photo_url", "mapping_exercise_name",
            "mapping_amazfit_name", "primary_group", "last_used_date", "last_used_at", "mapping_aliases",
        } <= set(item)  # fmt: skip


def test_ausente_vale_3_e_empate_de_estrelas_decide_pela_recencia_do_usuario(client, domain, sraw):
    _stars(sraw, domain["user_a"], EQ_ARTICULADO, 4)
    _stars(sraw, domain["user_a"], EQ_BARRA, 2)
    # crossover sem avaliacao = 3: fica ENTRE 4 e 2
    assert _order(_recommend(client, domain)) == [domain["ge_articulado"], domain["ge_crossover"], domain["ge_barra"]]

    _stars(sraw, domain["user_a"], EQ_BARRA, 4)
    _stars(sraw, domain["user_a"], EQ_CROSSOVER, 4)
    _use(sraw, domain["user_a"], domain["ge_barra"], "2026-09-10 08:00:00")
    _use(sraw, domain["user_a"], domain["ge_barra"], "2026-09-01 08:00:00")  # vale o uso MAIS recente
    _use(sraw, domain["user_a"], domain["ge_crossover"], "2026-09-05 08:00:00")
    body = _recommend(client, domain)
    # empate em 4: nunca usado -> usado ha mais tempo -> usado por ultimo
    assert _order(body) == [domain["ge_articulado"], domain["ge_crossover"], domain["ge_barra"]]
    by_id = {item["id"]: item for item in body["items"]}
    assert by_id[domain["ge_barra"]]["last_used_by_user_at"].startswith("2026-09-10")
    assert by_id[domain["ge_barra"]]["last_used_date"] == "2026-09-10"
    assert by_id[domain["ge_barra"]]["user_preference_stars"] == 4


def _add_pair_to_gym(sraw, domain, equipment_id: int, exercise_id: int, *, link: bool = True) -> int:
    """Nova associacao na academia (limpa pelo teardown do dominio). `link=False`
    = `gym_equipment` sem linha em `equipment_exercises` (cache orfao)."""
    with sraw.cursor() as cur:
        if link:
            cur.execute(
                "INSERT INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)", (equipment_id, exercise_id)
            )
        cur.execute(
            "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s, %s, %s, 1)",
            (domain["gym"], exercise_id, equipment_id),
        )
        return int(cur.lastrowid)


def test_a_nota_e_do_par_mesmo_equipamento_com_notas_diferentes_por_exercicio(backend, client, domain, sraw):
    """Barra livre serve a Supino e a Crucifixo: 5 estrelas em Barra x Crucifixo
    e 1 em Barra x Supino. Cada nota so mexe no SEU exercicio."""
    from app.generation import mode_b_legacy as engine

    ge_barra_crucifixo = _add_pair_to_gym(sraw, domain, EQ_BARRA, EX_CRUCIFIXO)
    _stars(sraw, domain["user_a"], EQ_BARRA, 5, EX_CRUCIFIXO)
    _stars(sraw, domain["user_a"], EQ_BARRA, 1, EX_SUPINO)

    body = _recommend(client, domain)
    assert {i["id"]: i["user_preference_stars"] for i in body["items"]} == {
        ge_barra_crucifixo: 5,
        domain["ge_crossover"]: None,
        domain["ge_articulado"]: None,
        domain["ge_barra"]: 1,
    }
    assert _order(body) == [ge_barra_crucifixo, domain["ge_crossover"], domain["ge_articulado"], domain["ge_barra"]]

    gym, user = domain["gym"], domain["user_a"]
    # Supino: a Barra (1 estrela NESTE exercicio) perde para o Articulado (sem nota = 3)
    assert engine._find_gym_equipment_for_exercise(gym, EX_SUPINO, user)["id"] == domain["ge_articulado"]
    # Crucifixo: a MESMA Barra (5 estrelas neste exercicio) ganha do Crossover
    assert engine._find_gym_equipment_for_exercise(gym, EX_CRUCIFIXO, user)["id"] == ge_barra_crucifixo
    # outro usuario nao herda nada: nome decide
    assert engine._find_gym_equipment_for_exercise(gym, EX_SUPINO, domain["user_b"])["id"] == domain["ge_barra"]
    assert all(i["user_preference_stars"] is None for i in _recommend(client, domain, headers=HEADERS_B)["items"])


def test_associacao_sem_par_em_equipment_exercises_fica_sem_nota_e_sem_erro(client, domain, sraw):
    orphan = _add_pair_to_gym(sraw, domain, EQ_FORA, EX_SUPINO, link=False)  # EQ_FORA so tem par com Crucifixo
    _stars(sraw, domain["user_a"], EQ_FORA, 5)  # nota de EQ_FORA x Crucifixo: nao vale para EQ_FORA x Supino
    body = _recommend(client, domain)
    item = next(i for i in body["items"] if i["id"] == orphan)
    assert item["user_preference_stars"] is None
    # sem nota = 3: ordena por nome junto com os demais, nao vai para o topo
    assert _order(body) == [domain["ge_barra"], domain["ge_crossover"], orphan, domain["ge_articulado"]]


def test_recencia_e_estrelas_de_outro_usuario_nao_interferem(client, domain, sraw):
    """G5: antes `last_used` era global - o treino de B empurrava o equipamento para o fim da lista de A."""
    baseline = _order(_recommend(client, domain))
    _use(sraw, domain["user_b"], domain["ge_barra"], "2026-09-16 08:00:00")
    _stars(sraw, domain["user_b"], EQ_BARRA, 1)
    _stars(sraw, domain["user_b"], EQ_ARTICULADO, 5)

    body_a = _recommend(client, domain)
    assert _order(body_a) == baseline
    assert all(i["last_used_by_user_at"] is None and i["user_preference_stars"] is None for i in body_a["items"])

    body_b = _recommend(client, domain, headers=HEADERS_B)
    assert _order(body_b) == [domain["ge_articulado"], domain["ge_crossover"], domain["ge_barra"]]


def test_exclude_equipment_id_continua_valendo(client, domain):
    body = _recommend(client, domain, exclude_equipment_id=domain["ge_barra"])
    assert domain["ge_barra"] not in _order(body) and body["has_equipment_for_group"] is True
    assert body["excluded"] == []  # `excluded[]` e so de required_technique


def _mark(sraw, equipment_id: int, max_safe_rpe, techniques: dict[str, int]) -> None:
    with sraw.cursor() as cur:
        cur.execute("UPDATE equipment SET max_safe_rpe = %s WHERE id = %s", (max_safe_rpe, equipment_id))
        for code, compatible in techniques.items():
            cur.execute(
                "REPLACE INTO equipment_technique_compatibility (equipment_id, technique_code, is_compatible) VALUES (%s, %s, %s)",
                (equipment_id, code, compatible),
            )


def test_required_technique_exclui_com_o_motivo_certo(client, domain, sraw):
    _mark(sraw, EQ_ARTICULADO, 10, {"drop_set": 1, "rest_pause": 1})
    _mark(sraw, EQ_BARRA, 9, {"drop_set": 1})  # drop_set exige falha; RPE max 9 -> falha proibida
    _stars(sraw, domain["user_a"], EQ_BARRA, 5)  # o preferido e incompativel: perde

    sem_filtro = _recommend(client, domain)
    assert len(sem_filtro["items"]) == 3 and sem_filtro["excluded"] == []  # sem o parametro nada some
    articulado = next(i for i in sem_filtro["items"] if i["id"] == domain["ge_articulado"])
    assert articulado["max_safe_rpe"] == 10 and articulado["failure_allowed"] is True
    assert articulado["safety_source"] == "equipment_default"
    assert articulado["allowed_advanced_techniques"] == [
        {"code": "drop_set", "source": "equipment_default"},
        {"code": "rest_pause", "source": "equipment_default"},
    ]

    body = _recommend(client, domain, required_technique="drop_set")
    assert body["required_technique"] == "drop_set"
    assert _order(body) == [domain["ge_articulado"]] and body["recommended"]["id"] == domain["ge_articulado"]
    assert [(e["gym_equipment_id"], e["equipment_name"], e["reason"], e["max_safe_rpe"]) for e in body["excluded"]] == [
        (domain["ge_barra"], "Barra livre P08", "FAILURE_REQUIRED_NOT_ALLOWED", 9),
        (domain["ge_crossover"], "Crossover P08", "TECHNIQUE_INCOMPATIBLE", 9),
    ]
    assert body["has_equipment_for_group"] is True

    all_groups = client.get(
        "/gym-equipment/recommend-all", params={"gym_id": domain["gym"], "required_technique": "drop_set"}, headers=HEADERS_A
    ).json()["groups"][GROUP]
    assert all_groups["recommended"]["id"] == domain["ge_articulado"] and all_groups["alternatives"] == []
    assert [e["reason"] for e in all_groups["excluded"]] == ["FAILURE_REQUIRED_NOT_ALLOWED", "TECHNIQUE_INCOMPATIBLE"]


def test_required_technique_fora_do_catalogo_e_422(client, domain):
    response = client.get(
        "/gym-equipment/recommend",
        params={"gym_id": domain["gym"], "primary_group": GROUP, "required_technique": "nao_existe"},
        headers=HEADERS_A,
    )
    assert response.status_code == 422


def test_recommend_all_tem_o_mesmo_ranking_e_os_mesmos_itens(client, domain, sraw):
    _stars(sraw, domain["user_a"], EQ_CROSSOVER, 5)
    _use(sraw, domain["user_a"], domain["ge_barra"], "2026-09-10 08:00:00")
    single = _recommend(client, domain)
    body = client.get("/gym-equipment/recommend-all", params={"gym_id": domain["gym"]}, headers=HEADERS_A).json()
    group = body["groups"][GROUP]
    assert [group["recommended"], *group["alternatives"]] == single["items"]
    assert group["excluded"] == [] and group["has_equipment_for_group"] is True
    assert body["groups_by_id"][str(domain["group"])] == group


def test_determinismo_e_numero_fixo_de_selects_sem_escrita(backend, client, domain, sraw, monkeypatch, fake_rds):
    """Sem N+1: dobrar os equipamentos da academia nao muda a quantidade de SELECTs."""
    _stars(sraw, domain["user_a"], EQ_BARRA, 4)
    _use(sraw, domain["user_a"], domain["ge_articulado"], "2026-09-10 08:00:00")

    def measure() -> tuple[dict, int]:
        with recorded_sql(backend, monkeypatch, fake_rds) as sent:
            body = client.get("/gym-equipment/recommend-all", params={"gym_id": domain["gym"]}, headers=HEADERS_A).json()
        assert _app_writes(sent) == []
        return body, len([s for s in sent if s.lstrip().upper().startswith("SELECT")])

    first, selects_before = measure()
    assert measure()[0] == first  # mesma entrada -> mesma saida

    extra_ids = list(range(9830, 9840))
    with sraw.cursor() as cur:
        for equipment_id in extra_ids:
            cur.execute("INSERT INTO equipment (id, name) VALUES (%s, %s)", (equipment_id, f"Extra P08 {equipment_id}"))
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s, %s, %s, 1)",
                (domain["gym"], EX_CRUCIFIXO, equipment_id),
            )
    try:
        second, selects_after = measure()
        assert len(second["groups"][GROUP]["alternatives"]) == len(first["groups"][GROUP]["alternatives"]) + len(extra_ids)
        assert selects_after == selects_before
    finally:
        with sraw.cursor() as cur:
            cur.execute("DELETE FROM gym_equipment WHERE gym_id = %s AND equipment_id BETWEEN 9830 AND 9839", (domain["gym"],))
            cur.execute("DELETE FROM equipment WHERE id BETWEEN 9830 AND 9839")


def test_tabela_ausente_nao_derruba_o_recomendador_e_a_escrita_responde_503(client, domain, sraw):
    """Codigo publicado antes da migracao (deploy com RUN_DB_MIGRATIONS != true)."""
    with sraw.cursor() as cur:
        cur.execute(f"DROP TABLE {TABLE}")
    try:
        body = _recommend(client, domain)
        assert len(body["items"]) == 3 and all(i["user_preference_stars"] is None for i in body["items"])
        assert client.get(ROUTE, headers=HEADERS_A).status_code == 503
        assert client.put(f"{ROUTE}/{domain['pair_barra']}", json={"stars": 4}, headers=HEADERS_A).status_code == 503
        assert client.delete(f"{ROUTE}/{domain['pair_barra']}", headers=HEADERS_A).status_code == 503
    finally:
        assert len(_run()) == 1  # a propria 035 recria a tabela
    assert client.get(ROUTE, headers=HEADERS_A).status_code == 200


# ---------------------------------------------------------------------------
# services.resolve_equipment_id (criterio unificado)
# ---------------------------------------------------------------------------


def test_resolve_equipment_id_usa_o_mesmo_ranking_do_usuario(backend, client, domain, sraw):
    import app.db as db

    def resolve(user_id):
        with db.transaction() as conn:
            with conn.cursor() as cur:
                return services.resolve_equipment_id(cur, EX_SUPINO, domain["gym"], user_id=user_id)

    assert resolve(domain["user_a"]) == _recommend(client, domain)["recommended"]["id"] == domain["ge_barra"]

    _stars(sraw, domain["user_a"], EQ_ARTICULADO, 5)
    _use(sraw, domain["user_b"], domain["ge_crossover"], "2026-09-16 08:00:00")
    assert resolve(domain["user_a"]) == _recommend(client, domain)["recommended"]["id"] == domain["ge_articulado"]
    # B: sem estrelas; crossover usado por ele vai para o fim -> Barra (nome) primeiro
    assert resolve(domain["user_b"]) == _recommend(client, domain, headers=HEADERS_B)["recommended"]["id"] == domain["ge_barra"]
    # explicito e hint por nome continuam vencendo o ranking - mas o hint SO
    # casa entre os equipamentos do MESMO exercicio (D13, correcao do bug da
    # ficha 112): "Crossover P08" existe na academia, porem para o Crucifixo,
    # e nao serve como equipamento do Supino. Antes esta linha esperava
    # `ge_crossover` aqui: era a substituicao por GRUPO muscular, a mesma que
    # entregava "Remada curvada no orbital row" na Estacao de polia.
    with db.transaction() as conn:
        with conn.cursor() as cur:
            assert services.resolve_equipment_id(cur, EX_SUPINO, domain["gym"], 123, user_id=domain["user_a"]) == 123
            assert (
                services.resolve_equipment_id(
                    cur, EX_SUPINO, domain["gym"], None, "Barra livre P08", user_id=domain["user_a"]
                )
                == domain["ge_barra"]
            )
            assert (
                services.resolve_equipment_id(
                    cur, EX_SUPINO, domain["gym"], None, "Crossover P08", user_id=domain["user_a"]
                )
                == domain["ge_articulado"]  # hint que nao casa no exercicio cai no ranking DELE
            )
            assert (
                services.resolve_equipment_id(
                    cur, EX_CRUCIFIXO, domain["gym"], None, "Crossover P08", user_id=domain["user_a"]
                )
                == domain["ge_crossover"]
            )


# ---------------------------------------------------------------------------
# Geracao de ficha: mesmo ranking, com o user_id de QUEM PEDIU
# ---------------------------------------------------------------------------


def test_fallback_de_equipamento_do_mesmo_exercicio_segue_o_ranking_do_usuario(backend, domain, sraw):
    from app.generation import mode_b_legacy as engine

    gym = domain["gym"]
    assert engine._find_gym_equipment_for_exercise(gym, EX_SUPINO, domain["user_a"])["id"] == domain["ge_barra"]
    _stars(sraw, domain["user_a"], EQ_ARTICULADO, 5)
    chosen = engine._find_gym_equipment_for_exercise(gym, EX_SUPINO, domain["user_a"])
    assert chosen == {
        "id": domain["ge_articulado"],
        "mapping_id": EX_SUPINO,
        "equipment_id": EQ_ARTICULADO,
        "equipment_name": "Supino articulado P08",
    }
    # as estrelas de A nao mudam a escolha de B; nunca empresta associacao de outro exercicio
    assert engine._find_gym_equipment_for_exercise(gym, EX_SUPINO, domain["user_b"])["id"] == domain["ge_barra"]
    assert engine._find_gym_equipment_for_exercise(gym, EX_CRUCIFIXO, domain["user_a"])["id"] == domain["ge_crossover"]
    assert engine._recommend_for_group(gym, GROUP, domain["user_a"])["id"] == domain["ge_articulado"]
    assert engine._recommend_for_group(gym, GROUP, domain["user_b"])["id"] == domain["ge_barra"]


def test_contexto_de_geracao_usa_o_usuario_que_pediu_e_mantem_o_formato_do_prompt(backend, domain, sraw, monkeypatch):
    """Antes: HTTP interno com o token mestre (= admin). Com ranking por
    usuario isso daria a TODOS a ordem do admin. Desde o SEG-04 nao ha mais
    HTTP interno nenhum (academias e catalogo tambem vem do banco)."""
    from app import workout_plan_ai

    monkeypatch.setattr(workout_plan_ai, "_load_gyms", lambda: [])
    _stars(sraw, domain["user_b"], EQ_CROSSOVER, 5)

    context_a = workout_plan_ai.gather_ficha_context(domain["gym"], domain["user_a"])
    recommendation_b = workout_plan_ai.gather_ficha_context(domain["gym"], domain["user_b"])["gym_equipment_recommendation"]
    group_a = context_a["gym_equipment_recommendation"]["groups"][GROUP]
    group_b = recommendation_b["groups"][GROUP]

    assert group_a["recommended"]["id"] == domain["ge_barra"]
    assert group_b["recommended"]["id"] == domain["ge_crossover"]
    assert recommendation_b["groups_by_id"][domain["group"]] == group_b
    assert set(recommendation_b) == {"ok", "gym_id", "groups", "groups_by_id"}
    assert set(group_b) == {"has_equipment_for_group", "recommended", "alternatives"}
    # formato do item = o de antes do PR-08 (nada de estrelas/seguranca no prompt dos motores legados)
    assert set(group_b["recommended"]) == {
        "id", "mapping_id", "equipment_id", "equipment_name", "photo_url", "mapping_exercise_name",
        "mapping_amazfit_name", "muscle_group_id", "primary_group", "last_used_date", "last_used_at", "mapping_aliases",
    }  # fmt: skip

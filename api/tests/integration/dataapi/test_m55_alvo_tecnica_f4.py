"""M55 (migracao 063) contra MySQL real, nos DOIS backends (PyMySQL de dev e
shim da RDS Data API + duble):

- migracao: o runner deixa as duas colunas no tipo esperado; `down`/`up` pelo
  backend sao um statement por chamada e a reexecucao nao escreve nada;
- backfill: banco ANTES da 063 com grupos em F2 e em F5 (F4 ja efetivada,
  `technique_applied_at` preenchido) e trilhas com `technique_code` gravado
  por execucao -> depois da 063 todo alvo e NULL, nenhuma outra coluna muda e
  a prescricao de tecnica e a mesma de antes (legado = filtro por grupo);
- round-trip: a efetivacao do `APPLY_TECHNIQUE` grava o par alvo,
  `load_group_states` o le de volta e so a trilha alvo fica com a tecnica em
  vigor.

Schema proprio (`*_m55_test`), apagado ao final. Isolamento: `truncar` de
`tests/support/isolamento_v3.py` (+ `progression_group_states`) no setup e no
teardown de cada teste.
"""

from __future__ import annotations

import os
from datetime import datetime

import pytest

from app import migrate as m
from app import progression_state
from app.domain import progression_engine as pe
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.isolamento_v3 import truncar
from tests.support.migrations import recreate_schema_with_all_migrations
from tests.support.sql_capture import ddl, recorded_sql, writes

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]

MIGRATION_063 = m.DEFAULT_MIGRATIONS_DIR / "063_progression_technique_target.py"
TABLE = "progression_group_states"
NEW_COLUMNS = ("technique_target_exercise_id", "technique_target_equipment_id")
EXTRAS = (TABLE,)

EX_ALVO, EX_EXECUTADO, EX_OUTRO_GRUPO = 5501, 5502, 5503
EQ = 5511
APPLIED_AT = datetime(2026, 9, 20, 18, 0)


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


@pytest.fixture(scope="module")
def schema():
    """Banco no formato de producao: baseline + TODAS as `.py` (inclui a 063)."""
    database = _database("m55")
    recreate_schema_with_all_migrations(database=database, **_kwargs())
    yield database
    with raw_mysql(database="information_schema", **_kwargs()) as conn:
        with conn.cursor() as cur:
            assert database.endswith("_test")
            cur.execute(f"DROP DATABASE IF EXISTS `{database}`")


def _module() -> dict:
    return m.load_python_migration(MIGRATION_063).__globals__


def _run_raw(database: str, fn: str) -> None:
    """Roda `up`/`down` da 063 numa conexao PyMySQL neutra."""
    from app.db_pymysql import Connection as PyMySQLConnection

    kw = _kwargs()
    app_conn = PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], database)
    try:
        _module()[fn](m.MigrationDB(app_conn))
        app_conn.commit()
    finally:
        app_conn.close()


@pytest.fixture
def sraw(schema):
    with raw_mysql(database=schema, **_kwargs()) as conn:
        truncar(conn, extras=EXTRAS)
        yield conn
        # teardown: dado limpo e colunas de volta (um teste derruba a 063)
        _run_raw(schema, "up")
        truncar(conn, extras=EXTRAS)


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch, fake_rds, schema) -> str:
    if request.param == "data-api":
        use_dataapi(monkeypatch, fake_rds, schema)
    else:
        monkeypatch.setenv("MYSQL_DATABASE", schema)
    yield request.param
    if request.param == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


def _conn():
    import app.db as db  # atributo trocado por use_dataapi

    return db.get_conn()


def _migration(fn: str) -> list[str]:
    conn = _conn()
    try:
        migration_db = m.MigrationDB(conn)
        _module()[fn](migration_db)
        conn.commit()
        return migration_db.statements
    finally:
        conn.close()


def _columns(raw) -> dict[str, tuple[str, str]]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME IN (%s, %s)",
            (TABLE, *NEW_COLUMNS),
        )
        return {
            row["COLUMN_NAME"]: (row["COLUMN_TYPE"].replace("(20)", ""), row["IS_NULLABLE"])
            for row in cur.fetchall()
        }


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def _seed(raw) -> dict:
    """Um aluno, dois grupos e tres trilhas, todas com tecnica gravada por
    EXECUCAO (o caso da ficha 125 / Modelo 28):

    - grupo `f5`: F4 ja efetivada (`technique_applied_at`) - legado sem alvo;
      trilhas EX_ALVO (rest_pause) e EX_EXECUTADO (drop_set);
    - grupo `f2`: ainda em F2; trilha EX_OUTRO_GRUPO (rest_pause).
    """
    ids: dict = {}
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('aluno-m55', 'x', 'regular')")
        ids["user"] = cur.lastrowid
        for key in ("f5", "f2"):
            cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (f"Grupo M55 {key}",))
            ids[key] = cur.lastrowid
        cur.execute("INSERT INTO equipment (id, name) VALUES (%s, 'Banco M55')", (EQ,))
        for key, phase, applied in (("f5", "F5", APPLIED_AT), ("f2", "F2", None)):
            cur.execute(
                f"INSERT INTO {TABLE} (user_id, muscle_group_id, phase, phase_status, reps_min, reps_max, "
                "phase_entered_at, technique_applied_at, engine_version) "
                "VALUES (%s, %s, %s, 'ACTIVE', 10, 12, %s, %s, %s)",
                (ids["user"], ids[key], phase, APPLIED_AT, applied, pe.ENGINE_VERSION),
            )
        for exercise_id, group_key, code in (
            (EX_ALVO, "f5", "rest_pause"),
            (EX_EXECUTADO, "f5", "drop_set"),
            (EX_OUTRO_GRUPO, "f2", "rest_pause"),
        ):
            cur.execute(
                "INSERT INTO progression_tracks (user_id, exercise_id, equipment_id, muscle_group_id, load_status, "
                "working_load_kg, sets_count, technique_code, engine_version) "
                "VALUES (%s, %s, %s, %s, 'ACTIVE', 40, 3, %s, %s)",
                (ids["user"], exercise_id, EQ, ids[group_key], code, pe.ENGINE_VERSION),
            )
    raw.commit()
    return ids


def _group_rows(raw) -> list[dict]:
    with raw.cursor() as cur:
        cur.execute(f"SELECT * FROM {TABLE} ORDER BY muscle_group_id")
        return [dict(row) for row in cur.fetchall()]


def _track_rows(raw) -> list[dict]:
    with raw.cursor() as cur:
        cur.execute("SELECT * FROM progression_tracks ORDER BY exercise_id")
        return [dict(row) for row in cur.fetchall()]


def _without(rows: list[dict], *columns: str) -> list[dict]:
    return [{k: v for k, v in row.items() if k not in columns} for row in rows]


def _in_force(user_id: int) -> dict[int, str | None]:
    conn = _conn()
    try:
        groups = progression_state.load_group_states(conn, user_id)
        tracks = progression_state.load_tracks(conn, user_id)
    finally:
        conn.close()
    return {
        key[0]: pe.track_technique_in_force(track, groups.get(track.muscle_group_id))
        for key, track in sorted(tracks.items())
    }


# ---------------------------------------------------------------------------
# Migracao
# ---------------------------------------------------------------------------


def test_runner_deixou_as_colunas_no_tipo_esperado(sraw):
    assert _columns(sraw) == {
        "technique_target_exercise_id": ("bigint unsigned", "YES"),
        "technique_target_equipment_id": ("bigint", "YES"),
    }


def test_down_up_um_statement_por_chamada_e_reexecucao_sem_escrita(backend, sraw, monkeypatch, fake_rds):
    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert len(_migration("down")) == 2
    assert len(ddl(sent)) == 2
    assert _columns(sraw) == {}

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _migration("up") == [
            "ALTER TABLE progression_group_states ADD COLUMN technique_target_exercise_id BIGINT UNSIGNED NULL",
            "ALTER TABLE progression_group_states ADD COLUMN technique_target_equipment_id BIGINT NULL",
        ]
    assert len(ddl(sent)) == 2
    assert set(_columns(sraw)) == set(NEW_COLUMNS)

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _migration("up") == []  # convergido
    assert writes(sent) == []


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


def test_backfill_deixa_todo_alvo_nulo_e_nao_muda_nenhuma_prescricao(backend, sraw, schema):
    _run_raw(schema, "down")  # banco como producao hoje: sem as colunas
    ids = _seed(sraw)
    groups_before = _group_rows(sraw)
    tracks_before = _track_rows(sraw)

    assert len(_migration("up")) == 2

    groups_after = _group_rows(sraw)
    assert [(row[NEW_COLUMNS[0]], row[NEW_COLUMNS[1]]) for row in groups_after] == [(None, None), (None, None)]
    assert _without(groups_after, *NEW_COLUMNS) == groups_before
    assert _track_rows(sraw) == tracks_before  # `technique_code` intacto

    # grupo F5 legado (F4 efetivada antes da 063): comportamento anterior;
    # grupo F2: nenhuma tecnica executada vira prescricao
    assert _in_force(ids["user"]) == {EX_ALVO: "rest_pause", EX_EXECUTADO: "drop_set", EX_OUTRO_GRUPO: None}


# ---------------------------------------------------------------------------
# Round-trip do alvo
# ---------------------------------------------------------------------------


def test_efetivacao_grava_o_alvo_e_so_ele_fica_com_a_tecnica(backend, sraw):
    ids = _seed(sraw)
    with sraw.cursor() as cur:
        # grupo f5 volta ao estado de antes da efetivacao: F3 com a F4 pendente
        cur.execute(
            f"UPDATE {TABLE} SET phase = 'F3', technique_applied_at = NULL, pending_action = %s, "
            "pending_payload = %s WHERE muscle_group_id = %s",
            (pe.APPLY_TECHNIQUE, f'{{"track_key": [{EX_ALVO}, {EQ}], "technique_code": "rest_pause"}}', ids["f5"]),
        )
    sraw.commit()

    conn = _conn()
    try:
        grupo = progression_state.load_group_states(conn, ids["user"])[ids["f5"]]
        assert grupo.technique_target_key is None
        efetivado = pe.apply_effective(grupo, pe.F4, now=APPLIED_AT, reps=(10, 12))
        progression_state.save_group_state(conn, ids["user"], efetivado)
        conn.commit()
    finally:
        conn.close()

    with sraw.cursor() as cur:
        cur.execute(
            f"SELECT phase, technique_applied_at, pending_action, {', '.join(NEW_COLUMNS)} "
            f"FROM {TABLE} WHERE muscle_group_id = %s",
            (ids["f5"],),
        )
        row = cur.fetchone()
    assert (row["phase"], row["technique_applied_at"], row["pending_action"]) == ("F4", APPLIED_AT, None)
    assert (row[NEW_COLUMNS[0]], row[NEW_COLUMNS[1]]) == (EX_ALVO, EQ)

    # so a trilha alvo: a outra do grupo tem tecnica so EXECUTADA
    assert _in_force(ids["user"]) == {EX_ALVO: "rest_pause", EX_EXECUTADO: None, EX_OUTRO_GRUPO: None}

    conn = _conn()
    try:
        snapshot = progression_state.state_snapshot(conn, ids["user"])
    finally:
        conn.close()
    assert snapshot["groups"][ids["f5"]]["technique_target_key"] == [EX_ALVO, EQ]
    assert snapshot["groups"][ids["f2"]]["technique_target_key"] is None

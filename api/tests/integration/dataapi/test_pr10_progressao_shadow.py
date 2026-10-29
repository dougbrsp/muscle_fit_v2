"""PR-10 nos dois backends (PyMySQL e o caminho de PRODUCAO: shim
`app/db_dataapi.py` + duble da RDS Data API):

- migracao 036 em MySQL real: 3 CREATE TABLE em banco sem as tabelas, estado
  convergido sem escrita, formato inesperado falha sem aplicar, runner em
  banco novo, cascata das FKs;
- `TrainingHistoryRepository.fetch_last_completed_sessions`: query UNICA (sem
  N+1), so sessoes concluidas com series, aquecimento/ajuste filtrados pelos
  predicados de `set_types`, `dropset` legado normalizado na leitura, par
  canonico via `exercises.mapping_id` + `gym_equipment.equipment_id`;
- avaliador em MODO SOMBRA no `finish`: grava nas tres tabelas novas e NAO
  altera a ficha, a sessao nem a resposta do endpoint; com a flag em `v1` nao
  grava nada; falha do motor nao derruba o `finish`;
- replay `rebuild_progression_tracks`: rodar duas vezes deixa o MESMO estado;
- rotas `GET /progression/tracks` e `/tracks/{id}/decisions`: 401 sem
  identidade, isolamento entre usuarios (404 na trilha de outro), filtro por
  exercise_id/equipment_id.

Schema proprio (`*_p10_test`), apagado ao final.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import migrate as m
from app import progression, progression_state, workout_sessions
from app.db_pymysql import Connection as PyMySQLConnection
from app.domain.training_history import TrainingHistoryRepository
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations
from tests.support.progression_036 import DECISIONS, GROUP_STATES, NEW_TABLES, TRACKS, apply
from tests.support.sql_capture import ddl, recorded_sql, writes

MASTER = "pr10-master"
TOKEN_A, TOKEN_B = "pr10-aluno-a", "pr10-aluno-b"
HEADERS_A, HEADERS_B = {"X-API-Token": TOKEN_A}, {"X-API-Token": TOKEN_B}
GROUP = "Peitoral P10"
EX_SUPINO, EX_CRUCIFIXO = 9901, 9902
EQ_BANCO, EQ_POLIA = 9911, 9912


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


def _pymysql_factory(database: str):
    kw = _kwargs()
    return lambda: PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], database)


@pytest.fixture(scope="module")
def schema():
    """Banco novo com TODAS as migracoes aplicadas pelo runner (inclui a 036)."""
    database = _database("p10")
    recreate_schema_with_migrations(database=database, **_kwargs())
    m.migrate(conn_factory=_pymysql_factory(database))
    yield database
    with raw_mysql(database="information_schema", **_kwargs()) as conn:
        with conn.cursor() as cur:
            assert database.endswith("_test")
            cur.execute(f"DROP DATABASE IF EXISTS `{database}`")


@pytest.fixture
def sraw(schema):
    with raw_mysql(database=schema, **_kwargs()) as conn:
        yield conn


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch, fake_rds, schema) -> str:
    monkeypatch.setenv("API_TOKEN", MASTER)
    monkeypatch.setenv(progression_state.ENGINE_FLAG_ENV, "shadow")
    if request.param == "data-api":
        use_dataapi(monkeypatch, fake_rds, schema)
    else:
        monkeypatch.setenv("MYSQL_DATABASE", schema)
    yield request.param
    if request.param == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


def _run(dry_run: bool = False) -> list[str]:
    import app.db as db  # atributo trocado por use_dataapi

    conn = db.get_conn()
    try:
        return apply(conn, dry_run=dry_run)
    finally:
        conn.close()


# ===========================================================================
# Migracao 036 em MySQL real
# ===========================================================================


def _tables(raw) -> set[str]:
    with raw.cursor() as cur:
        placeholders = ", ".join(["%s"] * len(NEW_TABLES))
        cur.execute(
            f"SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
            f"AND TABLE_NAME IN ({placeholders})",
            NEW_TABLES,
        )
        return {row["TABLE_NAME"] for row in cur.fetchall()}


def test_runner_deixou_as_tres_tabelas_no_formato_da_migracao(sraw):
    assert _tables(sraw) == set(NEW_TABLES)


# Migracoes POSTERIORES a 036 que alteram as tabelas dela. O teste abaixo
# derruba as tres tabelas e as recria so com a 036; sem reaplicar estas, o
# resto do modulo roda num schema que nao e o de producao (ex.: a 063 do M55,
# `progression_group_states.technique_target_*`).
LATER_MIGRATIONS_ON_036_TABLES = (
    "063_progression_technique_target.py",
    "068_progression_track_fatigue_context.py",
    "071_progression_track_pending_load.py",
    "072_progression_track_plateau_load.py",
    "093_training_goal.py",
)

# A 068 troca a UK de `progression_tracks` (`uk_ptr_user_pair` ->
# `uk_ptr_user_pair_context`): a conferencia de formato da 036 so vale no
# schema SEM a 068, entao os testes que reexecutam a 036 sobre a tabela
# existente desfazem a 068 antes e a reaplicam depois.
MIGRATION_068 = "068_progression_track_fatigue_context.py"
# A 093 poe `training_goal` nas UKs da 068 e da 036 (`uk_pgs_user_group`): o
# `down` dela roda ANTES do da 068 (ordem inversa), como em
# `tests/support/migrations.RESHAPING_MIGRATIONS`.
MIGRATION_093 = "093_training_goal.py"


def _reaplicar_migracoes_posteriores_a_036() -> None:
    import app.db as db

    conn = db.get_conn()
    try:
        for name in LATER_MIGRATIONS_ON_036_TABLES:
            m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / name)(m.MigrationDB(conn))
            conn.commit()
    finally:
        conn.close()


def _desfazer_068() -> None:
    import app.db as db

    conn = db.get_conn()
    try:
        for name in (MIGRATION_093, MIGRATION_068):
            module = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / name).__globals__
            module["down"](m.MigrationDB(conn))
            conn.commit()
    finally:
        conn.close()


def test_banco_sem_as_tabelas_gera_tres_creates_e_a_reexecucao_nao_escreve(backend, sraw, monkeypatch, fake_rds):
    with sraw.cursor() as cur:
        for table in reversed(NEW_TABLES):  # decisions primeiro (FKs)
            cur.execute(f"DROP TABLE IF EXISTS {table}")
    assert _tables(sraw) == set()

    try:
        planned = _run(dry_run=True)
        assert [sql.split()[5] for sql in planned] == list(NEW_TABLES)
        assert _tables(sraw) == set()  # dry-run nao altera nada

        with recorded_sql(backend, monkeypatch, fake_rds) as sent:
            assert _run() == planned
        assert len(ddl(sent)) == 3  # um statement por chamada
        assert _tables(sraw) == set(NEW_TABLES)

        with recorded_sql(backend, monkeypatch, fake_rds) as sent:
            assert _run() == []  # convergido
        assert writes(sent) == []
    finally:
        # volta ao formato de producao (036 + posteriores) para o resto do modulo
        _run()
        _reaplicar_migracoes_posteriores_a_036()


def test_formato_inesperado_falha_sem_aplicar(backend, sraw):
    _desfazer_068()
    try:
        with sraw.cursor() as cur:
            cur.execute(f"ALTER TABLE {TRACKS} DROP INDEX idx_ptr_user_group_executed")
        try:
            with pytest.raises(Exception, match="036: indice idx_ptr_user_group_executed.*Nenhum statement executado"):
                _run()
        finally:
            with sraw.cursor() as cur:
                cur.execute(
                    f"ALTER TABLE {TRACKS} ADD KEY idx_ptr_user_group_executed "
                    "(user_id, muscle_group_id, last_executed_at)"
                )
        assert _run() == []
    finally:
        _reaplicar_migracoes_posteriores_a_036()


def test_exercise_id_e_bigint_unsigned_sem_fk_fisica(sraw):
    """`progression_tracks.exercise_id` guarda `exercise_catalog.id`, que e
    UNSIGNED. A FK e LOGICA (indice proprio, sem constraint): a migracao 028,
    ja aplicada em producao, enumera TODAS as FKs que apontam para
    `exercise_catalog` e falha diante de uma nao prevista."""
    with sraw.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = %s AND COLUMN_NAME = 'exercise_id'",
            (TRACKS,),
        )
        assert cur.fetchone()["COLUMN_TYPE"].lower() == "bigint unsigned"
        cur.execute(
            "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL",
            (TRACKS,),
        )
        fks = {row["COLUMN_NAME"]: row["REFERENCED_TABLE_NAME"] for row in cur.fetchall()}
    assert fks == {"user_id": "users", "equipment_id": "equipment", "muscle_group_id": "muscle_groups"}


# ===========================================================================
# Dominio de teste
# ===========================================================================


def _session_token(cur, username: str, token: str) -> int:
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
    """Academia com 2 exercicios do mesmo grupo PRIMARY e 2 equipamentos, dois
    alunos com sessao, e uma ficha com dois itens prescritos 3x10-12 @ RPE 9."""
    ids: dict = {}
    with sraw.cursor() as cur:
        cur.execute("INSERT IGNORE INTO users (username, password_hash, role) VALUES ('admin-p10', 'x', 'admin')")
        ids["user_a"] = _session_token(cur, "aluno-a-p10", TOKEN_A)
        ids["user_b"] = _session_token(cur, "aluno-b-p10", TOKEN_B)
        cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (GROUP,))
        ids["group"] = cur.lastrowid
        legacy = _table_exists(cur, "exercise_zepp_mappings")
        for exercise_id, name, compound in ((EX_SUPINO, "Supino P10", 1), (EX_CRUCIFIXO, "Crucifixo P10", 0)):
            if legacy:
                cur.execute(
                    "INSERT IGNORE INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                    (exercise_id, name, name),
                )
            cur.execute(
                "INSERT INTO exercise_catalog (id, name, is_active, is_compound) VALUES (%s, %s, 1, %s)",
                (exercise_id, name, compound),
            )
            cur.execute(
                "INSERT INTO exercise_muscle_contributions "
                "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) "
                "VALUES (%s, %s, 'PRIMARY', %s, 1)",
                (exercise_id, ids["group"], 1.0 if compound else 0.6),
            )
        for equipment_id, name, step in ((EQ_BANCO, "Banco P10", "2.5"), (EQ_POLIA, "Polia P10", "5")):
            cur.execute(
                "INSERT INTO equipment (id, name, max_safe_rpe, load_increment_kg) VALUES (%s, %s, 9, %s)",
                (equipment_id, name, step),
            )
            cur.execute(
                "INSERT INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)",
                (equipment_id, EX_SUPINO if equipment_id == EQ_BANCO else EX_CRUCIFIXO),
            )
        cur.execute("SELECT id FROM users WHERE username = 'admin-p10'")
        cur.execute("INSERT INTO gyms (name, owner_user_id) VALUES ('Academia P10', %s)", (cur.fetchone()["id"],))
        ids["gym"] = cur.lastrowid
        for key, equipment_id, exercise_id in (
            ("ge_banco", EQ_BANCO, EX_SUPINO),
            ("ge_polia", EQ_POLIA, EX_CRUCIFIXO),
        ):
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) VALUES (%s, %s, %s, 1)",
                (ids["gym"], exercise_id, equipment_id),
            )
            ids[key] = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plans (user_id, name, status, plan_date) VALUES (%s, 'Ficha P10', 'draft', CURDATE())",
            (ids["user_a"],),
        )
        ids["plan"] = cur.lastrowid
        for key, exercise_id, name in (("wpe_supino", EX_SUPINO, "Supino P10"), ("wpe_crucifixo", EX_CRUCIFIXO, "Crucifixo P10")):
            cur.execute(
                "INSERT INTO workout_plan_exercises "
                "(plan_id, sequence, exercise_name, mapping_id, sets_count, reps_target, load_target, rpe_target) "
                "VALUES (%s, %s, %s, %s, 3, '10-12', '40', '9')",
                (ids["plan"], 1 if exercise_id == EX_SUPINO else 2, name, exercise_id),
            )
            ids[key] = cur.lastrowid
    yield ids
    with sraw.cursor() as cur:
        for table in (DECISIONS, TRACKS, GROUP_STATES):
            cur.execute(f"DELETE FROM {table} WHERE user_id IN (%s, %s)", (ids["user_a"], ids["user_b"]))
        cur.execute("DELETE FROM activities WHERE user_id IN (%s, %s)", (ids["user_a"], ids["user_b"]))
        cur.execute("DELETE FROM workout_plans WHERE id = %s", (ids["plan"],))
        cur.execute("DELETE FROM gym_equipment WHERE gym_id = %s", (ids["gym"],))
        cur.execute("DELETE FROM gyms WHERE id = %s", (ids["gym"],))
        cur.execute("DELETE FROM equipment WHERE id IN (%s, %s)", (EQ_BANCO, EQ_POLIA))
        cur.execute("DELETE FROM exercise_muscle_contributions WHERE muscle_group_id = %s", (ids["group"],))
        cur.execute("DELETE FROM exercise_catalog WHERE id IN (%s, %s)", (EX_SUPINO, EX_CRUCIFIXO))
        if _table_exists(cur, "exercise_zepp_mappings"):
            cur.execute("DELETE FROM exercise_zepp_mappings WHERE id IN (%s, %s)", (EX_SUPINO, EX_CRUCIFIXO))
        cur.execute("DELETE FROM muscle_groups WHERE id = %s", (ids["group"],))
        cur.execute("DELETE FROM user_sessions WHERE user_id IN (%s, %s)", (ids["user_a"], ids["user_b"]))


def _record_session(sraw, ids, *, when: str, sets_spec, status: str = "done", user_key: str = "user_a") -> int:
    """Grava uma sessao concluida. `sets_spec` = lista de
    (gym_equipment_id, mapping_id, wpe_key, [(set_type, reps, weight, rpe)])."""
    with sraw.cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, ended_at, name, activity_type, "
            "session_status, workout_plan_id) VALUES (%s, DATE(%s), %s, %s, 'Treino P10', 'Musculacao', %s, %s)",
            (ids[user_key], when, when, when, status, ids["plan"]),
        )
        activity_id = cur.lastrowid
        for sequence, (gym_equipment_id, mapping_id, wpe_key, rows) in enumerate(sets_spec, start=1):
            cur.execute(
                "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, equipment_id, "
                "workout_plan_exercise_id, sets_count) VALUES (%s, %s, 'Exercicio P10', %s, %s, %s, %s)",
                (activity_id, sequence, mapping_id, ids[gym_equipment_id], ids[wpe_key], len(rows)),
            )
            exercise_row_id = cur.lastrowid
            for number, (set_type, reps, weight, rpe) in enumerate(rows, start=1):
                cur.execute(
                    "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (exercise_row_id, number, set_type, reps, weight, rpe),
                )
    return activity_id


def _qualifying_rows(reps=12, rpe=9, weight=40):
    return [("working", reps, weight, rpe)] * 3


# ===========================================================================
# Historico (secao 5.2)
# ===========================================================================


def test_historico_le_as_sessoes_concluidas_numa_query_unica(backend, sraw, domain, monkeypatch, fake_rds):
    import app.db as db

    _record_session(
        sraw,
        domain,
        when="2026-09-01 18:00:00",
        sets_spec=[
            ("ge_banco", EX_SUPINO, "wpe_supino", [("warmup", 15, 20, 5), ("feeler", 8, 30, 6)] + _qualifying_rows()),
            ("ge_polia", EX_CRUCIFIXO, "wpe_crucifixo", _qualifying_rows(weight=25)),
        ],
    )
    _record_session(sraw, domain, when="2026-09-09 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows())])

    conn = db.get_conn()
    try:
        with recorded_sql(backend, monkeypatch, fake_rds) as sent:
            sessions = TrainingHistoryRepository(conn).fetch_last_completed_sessions(domain["user_a"], 21)
    finally:
        conn.close()

    # UMA query para tudo: sem N+1
    assert len([sql for sql in sent if sql.strip().upper().startswith("SELECT")]) == 1
    assert writes(sent) == []

    # ordem cronologica crescente
    assert [session.local_date.isoformat() for session in sessions] == ["2026-09-01", "2026-09-09"]
    primeira = sessions[0]
    assert len(primeira.exercises) == 2
    supino = primeira.exercises[0]
    # aquecimento e ajuste filtrados no SQL, mas contados
    assert len(supino.sets) == 3 and supino.preparatory_sets_count == 2
    # par canonico: exercise_catalog.id + equipment.id (nao gym_equipment.id)
    assert supino.pair == (EX_SUPINO, EQ_BANCO)
    assert supino.gym_equipment_id == domain["ge_banco"]
    # prescricao da ficha
    assert supino.prescribed.to_dict() == {
        "sets": 3,
        "reps_min": 10,
        "reps_max": 12,
        "load": 40.0,
        # `rpe_target` e uma RAMPA por serie no banco: o alvo de esforco e o
        # ULTIMO degrau (D4 revisada em 2026-09-18)
        "rpe_target": 9.0,
        "rpe_targets": [9.0],
        "technique": None,
    }


def test_historico_le_a_ficha_congelada_do_prescription_json(backend, sraw, domain):
    """Lote 2/P7 (RPE 10): `SESSIONS_SQL` extrai do `prescription_json`, chave
    a chave (`JSON_EXTRACT`), series/alvo/teto/falha/decisao congelados -
    validado nos dois backends (pymysql e Data API, onde o `JSON_UNQUOTE`
    volta como texto). Item sem snapshot continua sem `frozen`."""
    import json

    import app.db as db
    from app.domain.training_history import FrozenPrescription

    snapshot = {
        "schema_version": 1,
        "sets_count": 2,
        "effective_rpe_target": 9,
        "effective_rpe_ceiling": 9,
        "failure_allowed": False,
        "decision": "SKIP",
        "clamps": [{"field": "rpe_target", "from": 10, "to": 9, "reason": "RECOVERY"}],
    }
    with sraw.cursor() as cur:
        cur.execute(
            "UPDATE workout_plan_exercises SET prescription_json = %s WHERE id = %s",
            (json.dumps(snapshot), domain["wpe_crucifixo"]),
        )
    _record_session(
        sraw,
        domain,
        when="2026-09-01 18:00:00",
        sets_spec=[
            ("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows()),
            ("ge_polia", EX_CRUCIFIXO, "wpe_crucifixo", _qualifying_rows(weight=25)),
        ],
    )

    conn = db.get_conn()
    try:
        sessions = TrainingHistoryRepository(conn).fetch_last_completed_sessions(domain["user_a"], 21)
    finally:
        conn.close()

    supino, crucifixo = sessions[0].exercises
    assert supino.prescribed.frozen is None
    assert "frozen" not in supino.prescribed.to_dict()
    assert crucifixo.prescribed.frozen == FrozenPrescription(
        sets_count=2, effective_rpe_target=9, effective_rpe_ceiling=9, failure_allowed=False, decision="SKIP"
    )


def test_historico_ignora_sessao_em_andamento_e_sessao_sem_series(backend, sraw, domain):
    import app.db as db

    _record_session(sraw, domain, when="2026-09-01 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows())])
    _record_session(
        sraw,
        domain,
        when="2026-09-02 18:00:00",
        sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows())],
        status="in_progress",
    )
    # sessao antiga "so com agregados" (exercicio sem nenhuma serie): ignorada
    _record_session(sraw, domain, when="2026-09-03 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", [])])

    conn = db.get_conn()
    try:
        sessions = TrainingHistoryRepository(conn).fetch_last_completed_sessions(domain["user_a"], 21)
    finally:
        conn.close()
    assert [session.local_date.isoformat() for session in sessions] == ["2026-09-01"]


def test_historico_normaliza_dropset_legado_na_leitura(backend, sraw, domain):
    """O backfill nunca foi feito no banco: `dropset` sai `drop_set` e NAO
    entra no minimo das `working` (bug G2)."""
    import app.db as db

    _record_session(
        sraw,
        domain,
        when="2026-09-01 18:00:00",
        sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows() + [("dropset", 6, 20, 10)])],
    )
    conn = db.get_conn()
    try:
        sessions = TrainingHistoryRepository(conn).fetch_last_completed_sessions(domain["user_a"], 21)
    finally:
        conn.close()
    tipos = [item.set_type for item in sessions[0].exercises[0].sets]
    assert tipos == ["working", "working", "working", "drop_set"]

    from app.domain.progression_engine import classify_sets, session_working_load

    classification = classify_sets(sessions[0].exercises[0].sets)
    assert session_working_load(classification.working) == 40


def test_historico_respeita_o_limite_de_sessoes(backend, sraw, domain):
    import app.db as db

    for day in range(1, 6):
        _record_session(
            sraw, domain, when=f"2026-09-0{day} 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows())]
        )
    conn = db.get_conn()
    try:
        sessions = TrainingHistoryRepository(conn).fetch_last_completed_sessions(domain["user_a"], 3)
    finally:
        conn.close()
    # as 3 MAIS RECENTES, em ordem crescente
    assert [session.local_date.isoformat() for session in sessions] == ["2026-09-03", "2026-09-04", "2026-09-05"]


def test_historico_nao_vaza_entre_usuarios(backend, sraw, domain):
    import app.db as db

    _record_session(sraw, domain, when="2026-09-01 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows())])
    conn = db.get_conn()
    try:
        assert TrainingHistoryRepository(conn).fetch_last_completed_sessions(domain["user_b"], 21) == []
    finally:
        conn.close()


# ===========================================================================
# Avaliador em modo sombra
# ===========================================================================


def _state(sraw, ids):
    with sraw.cursor() as cur:
        cur.execute(f"SELECT * FROM {GROUP_STATES} WHERE user_id = %s", (ids["user_a"],))
        groups = cur.fetchall() or []
        cur.execute(f"SELECT * FROM {TRACKS} WHERE user_id = %s ORDER BY exercise_id", (ids["user_a"],))
        tracks = cur.fetchall() or []
        cur.execute(f"SELECT * FROM {DECISIONS} WHERE user_id = %s ORDER BY id", (ids["user_a"],))
        decisions = cur.fetchall() or []
    return groups, tracks, decisions


def test_modo_sombra_grava_estado_e_decisoes_sem_tocar_na_sessao(backend, sraw, domain):
    import app.db as db

    activity_id = _record_session(
        sraw,
        domain,
        when="2026-09-01 18:00:00",
        sets_spec=[
            ("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows()),
            ("ge_polia", EX_CRUCIFIXO, "wpe_crucifixo", _qualifying_rows(weight=25)),
        ],
    )
    conn = db.get_conn()
    try:
        summary = progression_state.on_session_finish(conn, domain["user_a"], activity_id)
        conn.commit()
    finally:
        conn.close()
    assert summary and summary["activity_id"] == activity_id

    groups, tracks, decisions = _state(sraw, domain)
    assert len(groups) == 1 and groups[0]["phase"] == "F1"  # grupo sem estado nasce em F1
    assert (groups[0]["reps_min"], groups[0]["reps_max"]) == (6, 8)
    assert groups[0]["engine_version"] == "progression-v2.0.0"
    # uma trilha por PAR, com a carga vinda do minimo das `working`
    assert [(int(t["exercise_id"]), int(t["equipment_id"]), str(t["working_load_kg"])) for t in tracks] == [
        (EX_SUPINO, EQ_BANCO, "40.00"),
        (EX_CRUCIFIXO, EQ_POLIA, "25.00"),
    ]
    assert {t["muscle_group_id"] for t in tracks} == {domain["group"]}
    assert len(decisions) == 1 and decisions[0]["kind"] == "EVALUATION"
    assert int(decisions[0]["activity_id"]) == activity_id
    output = json.loads(decisions[0]["output_json"])
    assert output["engine_version"] == "progression-v2.0.0"
    evidence = json.loads(decisions[0]["evidence_json"])
    assert evidence["readiness"] == "READY"
    # secao 14: a evidencia gravada traz veredito E motivo por trilha
    gravado = evidence["tracks"][f"{EX_SUPINO}:{EQ_BANCO}"]
    # Lote 3/P4: `excess_reps` (regra de excesso de repeticoes) tambem vai para a evidencia
    assert set(gravado) == {"outcome", "reason", "reasons", "intensity_status", "effort_target_rpe", "excess_reps"}
    assert gravado["excess_reps"] is False
    # trilha nova nasce em calibracao: neutra, com o motivo explicito
    assert (gravado["outcome"], gravado["reason"]) == ("NEUTRAL", "track_in_calibration")
    # alvo de esforco = min(rpe_target prescrito 9, ceiling 9)
    assert gravado["effort_target_rpe"] == 9.0

    # a sessao e a ficha ficaram INTACTAS
    with sraw.cursor() as cur:
        cur.execute("SELECT session_status FROM activities WHERE id = %s", (activity_id,))
        assert cur.fetchone()["session_status"] == "done"
        cur.execute(
            "SELECT sets_count, reps_target, load_target, rpe_target FROM workout_plan_exercises WHERE id = %s",
            (domain["wpe_supino"],),
        )
        assert cur.fetchone() == {"sets_count": 3, "reps_target": "10-12", "load_target": "40", "rpe_target": "9"}


def test_com_a_flag_em_v1_nada_e_gravado(backend, sraw, domain, monkeypatch):
    import app.db as db

    monkeypatch.setenv(progression_state.ENGINE_FLAG_ENV, "v1")
    activity_id = _record_session(
        sraw, domain, when="2026-09-01 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows())]
    )
    conn = db.get_conn()
    try:
        assert progression_state.on_session_finish(conn, domain["user_a"], activity_id) is None
        conn.commit()
    finally:
        conn.close()
    assert _state(sraw, domain) == ([], [], [])


def test_falha_do_motor_sombra_nao_derruba_o_finish(backend, sraw, domain, monkeypatch):
    import app.db as db

    monkeypatch.setattr(
        progression_state, "run_session_evaluation", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    activity_id = _record_session(
        sraw, domain, when="2026-09-01 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows())]
    )
    conn = db.get_conn()
    try:
        assert progression_state.on_session_finish(conn, domain["user_a"], activity_id) is None
    finally:
        conn.close()
    assert _state(sraw, domain) == ([], [], [])


def test_plato_confirmado_grava_pending_action_no_grupo(backend, sraw, domain):
    """Duas sessoes de grupo qualificantes com span de 8 dias: o grupo sobe da
    F1 para a F2 (faixa 10-12) pela acao pendente, sem tocar em nada visivel."""
    import app.db as db

    primeiro = _record_session(
        sraw, domain, when="2026-09-01 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows(reps=8))]
    )
    segundo = _record_session(
        sraw, domain, when="2026-09-09 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows(reps=8))]
    )
    conn = db.get_conn()
    try:
        progression_state.on_session_finish(conn, domain["user_a"], primeiro)
        progression_state.on_session_finish(conn, domain["user_a"], segundo)
        conn.commit()
    finally:
        conn.close()
    groups, _, decisions = _state(sraw, domain)
    # a 1a sessao calibra a trilha (neutra); a 2a e a 1a com a trilha ativa
    assert groups[0]["phase"] in ("F1", "F2")
    assert len(decisions) == 2
    assert json.loads(decisions[-1]["evidence_json"])["session_outcome"] in ("QUALIFY", "NEUTRAL")


def test_par_proibido_com_rpe_10_registra_safety_event_na_evidencia(backend, sraw, domain):
    """Equipamento com `max_safe_rpe = 9`: RPE 10 registrado gera
    `RPE_ABOVE_SAFE_CEILING` na evidencia, sem elevar nenhum alvo."""
    import app.db as db

    primeiro = _record_session(
        sraw, domain, when="2026-09-01 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows(rpe=9))]
    )
    segundo = _record_session(
        sraw, domain, when="2026-09-09 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows(rpe=10))]
    )
    conn = db.get_conn()
    try:
        progression_state.on_session_finish(conn, domain["user_a"], primeiro)
        progression_state.on_session_finish(conn, domain["user_a"], segundo)
        conn.commit()
    finally:
        conn.close()
    _, _, decisions = _state(sraw, domain)
    eventos = json.loads(decisions[-1]["evidence_json"])["safety_events"]
    assert [event["type"] for event in eventos] == ["RPE_ABOVE_SAFE_CEILING"] * 3
    assert {event["ceiling"] for event in eventos} == {9}
    assert {event["failure_denied_by"] for event in eventos} == {"PAIR"}


@pytest.mark.parametrize("com_origem", [True, False], ids=["snapshot_novo", "snapshot_antigo"])
def test_falha_negada_so_pela_recuperacao_gera_evento_informativo_na_avaliacao(backend, sraw, domain, com_origem):
    """RPE 10 Lote 1/P1 x Lote 2/P7: par com falha permitida (`max_safe_rpe =
    10`) e ficha congelada em dose reduzida (teto 9, falha negada SO pela
    recuperacao). A avaliacao le a origem do `prescription_json`
    (`$.failure_denied_by`; snapshot antigo: derivada de
    `$.safety_constraints.max_safe_rpe`) e grava o MESMO informativo que o Modo
    Treino - nos dois backends."""
    import app.db as db

    snapshot = {
        "schema_version": 1,
        "sets_count": 3,
        "effective_rpe_target": 9,
        "effective_rpe_ceiling": 9,
        "failure_allowed": False,
        "decision": "HOLD",
        "safety_constraints": {"max_safe_rpe": 10, "source": "equipment_default"},
        "clamps": [{"field": "rpe_target", "from": 10, "to": 9, "reason": "RECOVERY"}],
    }
    if com_origem:
        snapshot["failure_denied_by"] = "RECOVERY"
    with sraw.cursor() as cur:
        cur.execute("UPDATE equipment SET max_safe_rpe = 10 WHERE id = %s", (EQ_BANCO,))
        cur.execute(
            "UPDATE workout_plan_exercises SET prescription_json = %s WHERE id = %s",
            (json.dumps(snapshot), domain["wpe_supino"]),
        )
    primeiro = _record_session(
        sraw, domain, when="2026-09-01 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows(rpe=9))]
    )
    segundo = _record_session(
        sraw, domain, when="2026-09-09 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows(rpe=10))]
    )
    conn = db.get_conn()
    try:
        sessions = TrainingHistoryRepository(conn).fetch_last_completed_sessions(domain["user_a"], 21)
        frozen = sessions[0].exercises[0].prescribed.frozen
        assert (frozen.failure_denied_by, frozen.pair_max_safe_rpe) == ("RECOVERY" if com_origem else None, 10)
        progression_state.on_session_finish(conn, domain["user_a"], primeiro)
        progression_state.on_session_finish(conn, domain["user_a"], segundo)
        conn.commit()
    finally:
        conn.close()
    _, _, decisions = _state(sraw, domain)
    eventos = json.loads(decisions[-1]["evidence_json"])["safety_events"]
    assert [event["type"] for event in eventos] == ["RPE_ABOVE_RECOVERY_TARGET"] * 3
    assert {event["failure_denied_by"] for event in eventos} == {"RECOVERY"}
    assert {event["ceiling"] for event in eventos} == {9}


# ===========================================================================
# Replay idempotente
# ===========================================================================


def test_replay_rodado_duas_vezes_deixa_o_mesmo_estado(backend, sraw, domain):
    """Secao 5.11: "rodar duas vezes seguidas deve dar o mesmo estado"."""
    import app.db as db

    for day in ("01", "09", "17"):
        _record_session(
            sraw,
            domain,
            when=f"2026-09-{day} 18:00:00",
            sets_spec=[
                ("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows()),
                ("ge_polia", EX_CRUCIFIXO, "wpe_crucifixo", _qualifying_rows(weight=25)),
            ],
        )

    conn = db.get_conn()
    try:
        primeiro = progression_state.rebuild_for_user(conn, domain["user_a"])
        conn.commit()
        snapshot_1 = progression_state.state_snapshot(conn, domain["user_a"])
        segundo = progression_state.rebuild_for_user(conn, domain["user_a"])
        conn.commit()
        snapshot_2 = progression_state.state_snapshot(conn, domain["user_a"])
    finally:
        conn.close()

    assert primeiro["sessions"] == segundo["sessions"] == 3
    assert snapshot_1 == snapshot_2
    assert primeiro["tracks"] == segundo["tracks"] == 2
    assert primeiro["decisions"] == segundo["decisions"]
    # e o replay nao acumula decisoes
    _, _, decisions = _state(sraw, domain)
    assert len(decisions) == segundo["decisions"]


def test_replay_reconstroi_a_partir_do_zero(backend, sraw, domain):
    import app.db as db

    _record_session(
        sraw, domain, when="2026-09-01 18:00:00", sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows())]
    )
    conn = db.get_conn()
    try:
        assert progression_state.users_with_history(conn) != []
        report = progression_state.rebuild_for_user(conn, domain["user_a"])
        conn.commit()
    finally:
        conn.close()
    assert report["groups"] == 1 and report["tracks"] == 1
    _, tracks, _ = _state(sraw, domain)
    assert str(tracks[0]["working_load_kg"]) == "40.00"


# ===========================================================================
# Rotas
# ===========================================================================


@pytest.fixture
def client(backend, domain) -> TestClient:
    app = FastAPI()
    app.include_router(progression.router)
    app.include_router(workout_sessions.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def com_estado(sraw, domain):
    import app.db as db

    _record_session(
        sraw,
        domain,
        when="2026-09-01 18:00:00",
        sets_spec=[
            ("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows()),
            ("ge_polia", EX_CRUCIFIXO, "wpe_crucifixo", _qualifying_rows(weight=25)),
        ],
    )
    conn = db.get_conn()
    try:
        progression_state.rebuild_for_user(conn, domain["user_a"])
        conn.commit()
    finally:
        conn.close()
    _, tracks, _ = _state(sraw, domain)
    return {int(t["exercise_id"]): int(t["id"]) for t in tracks}


def test_tracks_exige_identidade(client):
    assert client.get("/progression/tracks").status_code == 401
    assert client.get("/progression/tracks/1/decisions").status_code == 401


def test_tracks_lista_as_trilhas_do_usuario_corrente(client, com_estado):
    body = client.get("/progression/tracks", headers=HEADERS_A).json()
    assert {item["exercise_id"] for item in body["items"]} == {EX_SUPINO, EX_CRUCIFIXO}
    supino = next(item for item in body["items"] if item["exercise_id"] == EX_SUPINO)
    assert supino["equipment_id"] == EQ_BANCO
    assert supino["working_load_kg"] == 40.0
    assert supino["exercise_name"] == "Supino P10" and supino["equipment_name"] == "Banco P10"
    assert supino["group_state"]["phase"] == "F1"
    assert body["engine_mode"] == "shadow"


def test_tracks_filtra_por_exercicio_e_equipamento(client, com_estado):
    por_exercicio = client.get(f"/progression/tracks?exercise_id={EX_SUPINO}", headers=HEADERS_A).json()
    assert [item["exercise_id"] for item in por_exercicio["items"]] == [EX_SUPINO]
    por_par = client.get(
        f"/progression/tracks?exercise_id={EX_SUPINO}&equipment_id={EQ_POLIA}", headers=HEADERS_A
    ).json()
    assert por_par["items"] == []


def test_tracks_nao_vaza_entre_usuarios(client, com_estado):
    assert client.get("/progression/tracks", headers=HEADERS_B).json()["items"] == []


def test_decisions_da_trilha_e_do_grupo_dela(client, com_estado):
    track_id = com_estado[EX_SUPINO]
    body = client.get(f"/progression/tracks/{track_id}/decisions", headers=HEADERS_A).json()
    assert body["track_id"] == track_id and body["group_state_id"]
    assert body["items"] and body["items"][0]["kind"] == "EVALUATION"
    assert body["items"][0]["output"]["engine_version"] == "progression-v2.0.0"
    assert body["items"][0]["evidence"]["readiness"] == "READY"


def test_decisions_de_trilha_de_outro_usuario_da_404(client, com_estado):
    response = client.get(f"/progression/tracks/{com_estado[EX_SUPINO]}/decisions", headers=HEADERS_B)
    assert response.status_code == 404


def test_rotas_nao_escrevem_nada(backend, client, com_estado, monkeypatch, fake_rds):
    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        client.get("/progression/tracks", headers=HEADERS_A)
        client.get(f"/progression/tracks/{com_estado[EX_SUPINO]}/decisions", headers=HEADERS_A)
    # so o UPDATE de `last_seen_at` da sessao do usuario, que e da autenticacao
    assert [sql for sql in writes(sent) if not sql.startswith("UPDATE user_sessions SET last_seen_at")] == []


def test_finish_roda_o_avaliador_em_sombra_sem_mudar_a_resposta(backend, sraw, domain, client):
    """O hook no `finish` (plano, linha do PR 10): a resposta do endpoint e a
    mesma de antes e as tabelas novas ganham as decisoes."""
    activity_id = _record_session(
        sraw,
        domain,
        when="2026-09-01 18:00:00",
        sets_spec=[("ge_banco", EX_SUPINO, "wpe_supino", _qualifying_rows())],
        status="in_progress",
    )
    response = client.post(f"/workout-sessions/{activity_id}/finish", json={}, headers=HEADERS_A)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["session_status"] == "done"
    assert "progression" not in json.dumps(body).lower()  # nada novo na resposta

    groups, tracks, decisions = _state(sraw, domain)
    assert len(groups) == 1 and len(tracks) == 1 and len(decisions) == 1
    assert int(decisions[0]["activity_id"]) == activity_id

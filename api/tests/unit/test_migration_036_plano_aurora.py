"""Plano da migracao 036 (estado do Motor de Progressao v2) contra o
`information_schema` REAL da Aurora de producao (recorte composto em
tests/unit/data/aurora_schema_2026_09_17_036.json; ver `_origem` - o unico
pedaco que nao vem de leitura da Aurora e `muscle_groups`, tabela de baseline).

Producao esta na versao 35 (nenhuma das tres tabelas existe) -> 3 CREATE TABLE,
na ordem em que as FKs exigem. Estado convergido -> 0 statements.

O mesmo plano contra MySQL real, nos dois backends, esta em
tests/integration/dataapi/test_pr10_progressao_shadow.py.
"""

from __future__ import annotations

import copy

import pytest

from app import migrate as m
from tests.support.progression_036 import (
    DECISIONS,
    GROUP_STATES,
    MIGRATION_036,
    NEW_TABLES,
    TRACKS,
    load_snapshot,
    module,
    with_tables,
)
from tests.support.safety_032_033 import SafetySnapshotDB

SNAPSHOT = load_snapshot()
REFERENCED_TABLES = ("users", "muscle_groups", "exercise_catalog", "equipment", "activities", "workout_plan_exercises")


def _plan(snapshot):
    db = SafetySnapshotDB(snapshot)
    statements = module()["plan"](db)
    return [(" ".join(sql.split()), params) for sql, params in statements], db


def test_recorte_e_da_aurora_com_os_tipos_que_as_fks_exigem():
    assert SNAPSHOT["versao"][0]["mysql"].startswith("8.0")
    engines = {t["table_name"]: t["engine"] for t in SNAPSHOT["tables"]}
    for table in REFERENCED_TABLES:
        assert engines[table] == "InnoDB", table
    for table in NEW_TABLES:
        assert table not in engines
    ids = {(c["t"], c["col"]): (c["type"], c["ckey"]) for c in SNAPSHOT["columns"] if c["col"] == "id"}
    # exercise_catalog.id e UNSIGNED: `progression_tracks.exercise_id` guarda
    # esse id, dai `BIGINT UNSIGNED` (a FK e logica - ver o teste do DDL).
    assert ids[("exercise_catalog", "id")] == ("bigint unsigned", "PRI")
    for table in set(REFERENCED_TABLES) - {"exercise_catalog"}:
        assert ids[(table, "id")] == ("bigint", "PRI"), table


def test_producao_hoje_cria_as_tres_tabelas_na_ordem_das_fks():
    statements, db = _plan(SNAPSHOT)
    assert [params for _, params in statements] == [None, None, None]
    sqls = [sql for sql, _ in statements]
    assert sqls[0].startswith(f"CREATE TABLE IF NOT EXISTS {GROUP_STATES} (")
    assert sqls[1].startswith(f"CREATE TABLE IF NOT EXISTS {TRACKS} (")
    assert sqls[2].startswith(f"CREATE TABLE IF NOT EXISTS {DECISIONS} (")
    # nenhuma tabela nova existe: nao ha por que ler STATISTICS/KEY_COLUMN_USAGE
    assert len(db.queries) == 2
    assert all("information_schema.STATISTICS" not in q and "KEY_COLUMN_USAGE" not in q for q in db.queries)


def test_ddl_declara_o_que_o_plano_promete():
    sqls = {sql.split()[5]: sql for sql, _ in _plan(SNAPSHOT)[0]}
    group_states = sqls[GROUP_STATES]
    assert "phase ENUM('F1','F2','F3','F4','F5','F6') NOT NULL" in group_states
    assert "UNIQUE KEY uk_pgs_user_group (user_id, muscle_group_id)" in group_states
    assert "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE" in group_states
    assert "FOREIGN KEY (muscle_group_id) REFERENCES muscle_groups(id) ON DELETE CASCADE" in group_states

    tracks = sqls[TRACKS]
    assert "exercise_id BIGINT UNSIGNED NOT NULL" in tracks
    assert "working_load_kg DECIMAL(6,2) NULL" in tracks  # carga TOTAL (D12)
    assert "UNIQUE KEY uk_ptr_user_pair (user_id, exercise_id, equipment_id)" in tracks
    assert "KEY idx_ptr_user_group_executed (user_id, muscle_group_id, last_executed_at)" in tracks
    # FK para `exercise_catalog` e LOGICA: a 028 (ja aplicada em producao)
    # enumera as FKs que apontam para o catalogo e recusa uma nao prevista
    assert "REFERENCES exercise_catalog" not in tracks
    assert "KEY idx_ptr_exercise (exercise_id)" in tracks
    assert "FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE" in tracks
    # FK LOGICA (a 023/031 nao cria FK fisica para advanced_techniques)
    assert "technique_code VARCHAR(20) NULL" in tracks
    assert "REFERENCES advanced_techniques" not in tracks

    decisions = sqls[DECISIONS]
    assert "kind ENUM('EVALUATION','PRESCRIPTION') NOT NULL" in decisions
    assert "KEY idx_pd_group_created (group_state_id, created_at)" in decisions
    assert "KEY idx_pd_track_created (track_id, created_at)" in decisions
    assert "KEY idx_pd_wpe (workout_plan_exercise_id)" in decisions
    assert f"FOREIGN KEY (group_state_id) REFERENCES {GROUP_STATES}(id) ON DELETE CASCADE" in decisions
    assert "FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE SET NULL" in decisions
    assert "FOREIGN KEY (workout_plan_exercise_id) REFERENCES workout_plan_exercises(id) ON DELETE SET NULL" in decisions
    for sql in sqls.values():
        assert sql.endswith("ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci")


def test_estado_convergido_e_zero_statements():
    assert _plan(with_tables(SNAPSHOT))[0] == []


@pytest.mark.parametrize("já_criadas, faltando", [((GROUP_STATES,), (TRACKS, DECISIONS)), ((GROUP_STATES, TRACKS), (DECISIONS,))])
def test_execucao_interrompida_no_meio_retoma_do_ponto(já_criadas, faltando):
    statements, _ = _plan(with_tables(SNAPSHOT, *já_criadas))
    assert [sql.split()[5] for sql, _ in statements] == list(faltando)


def _mutate_column(snap, table, col, **changes):
    snap = copy.deepcopy(snap)
    for column in snap["columns"]:
        if (column["t"], column["col"]) == (table, col):
            column.update(changes)
    return snap


def _drop(snap, key, predicate):
    snap = copy.deepcopy(snap)
    snap[key] = [row for row in snap[key] if not predicate(row)]
    return snap


@pytest.mark.parametrize(
    "mutate, message",
    [
        # tabelas/colunas referenciadas por FK
        (
            lambda s: _drop(s, "tables", lambda t: t["table_name"] == "muscle_groups"),
            "tabela referenciada muscle_groups nao existe",
        ),
        (
            lambda s: _drop(s, "columns", lambda c: (c["t"], c["col"]) == ("activities", "id")),
            "activities.id nao existe",
        ),
        (
            lambda s: _mutate_column(s, "exercise_catalog", "id", type="bigint"),
            "exercise_catalog.id e bigint, esperado bigint unsigned",
        ),
        (lambda s: _mutate_column(s, "users", "id", type="int"), "users.id e int"),
        (
            lambda s: _mutate_column(s, "workout_plan_exercises", "id", ckey=""),
            "workout_plan_exercises.id nao e PRIMARY KEY",
        ),
        (
            lambda s: {
                **s,
                "tables": [{**t, "engine": "MyISAM"} if t["table_name"] == "equipment" else t for t in s["tables"]],
            },
            "equipment usa ENGINE=MyISAM",
        ),
        # tabelas novas em formato diferente
        (lambda s: _mutate_column(s, TRACKS, "working_load_kg", type="decimal(8,2)"), "working_load_kg e decimal"),
        (lambda s: _mutate_column(s, TRACKS, "exercise_id", type="bigint"), "exercise_id e bigint, esperado bigint unsigned"),
        (lambda s: _mutate_column(s, GROUP_STATES, "reps_max", nullable="YES"), "reps_max tem IS_NULLABLE=YES"),
        (
            lambda s: _mutate_column(s, GROUP_STATES, "phase", type="enum('F1','F2','F3')"),
            "phase e enum",
        ),
        (lambda s: _drop(s, "columns", lambda c: (c["t"], c["col"]) == (DECISIONS, "blocked_by")), "blocked_by nao existe"),
        (
            lambda s: _drop(s, "indexes", lambda i: i["t"] == TRACKS and i["idx"] == "uk_ptr_user_pair" and i["seq"] == 3),
            "indice uk_ptr_user_pair",
        ),
        (
            lambda s: _drop(s, "indexes", lambda i: i["t"] == DECISIONS and i["idx"] == "idx_pd_group_created"),
            "indice idx_pd_group_created",
        ),
        (
            lambda s: {
                **s,
                "indexes": [
                    {**i, "non_unique": 1} if (i["t"], i["idx"]) == (GROUP_STATES, "uk_pgs_user_group") else i
                    for i in s["indexes"]
                ],
            },
            "indice uk_pgs_user_group de progression_group_states nao e UNIQUE",
        ),
        (lambda s: _drop(s, "foreign_keys", lambda f: f["fk"] == "fk_ptr_group"), f"FKs de {TRACKS}"),
        (lambda s: _drop(s, "foreign_keys", lambda f: f["fk"] == "fk_pd_activity"), f"FKs de {DECISIONS}"),
    ],
)
def test_estado_inesperado_falha_antes_de_qualquer_statement(mutate, message):
    with pytest.raises(Exception, match=message) as exc:
        _plan(mutate(with_tables(SNAPSHOT)))
    assert "Nenhum statement executado" in str(exc.value)


def test_largura_de_int_legada_nao_e_divergencia():
    """MySQL < 8.0.19 mostra `tinyint(4)`; nao e outro formato."""
    snap = _mutate_column(with_tables(SNAPSHOT), TRACKS, "sets_count", type="tinyint(4)")
    assert _plan(snap)[0] == []


def test_indice_extra_no_banco_nao_e_divergencia():
    snap = copy.deepcopy(with_tables(SNAPSHOT))
    snap["indexes"].append({"t": DECISIONS, "idx": "idx_manual_dba", "seq": 1, "col": "decision", "non_unique": 1})
    assert _plan(snap)[0] == []


def test_plano_e_um_statement_por_chamada_sem_estado_de_sessao():
    sqls = [sql for sql, _ in _plan(SNAPSHOT)[0]]
    assert len(sqls) == 3
    for sql in sqls:
        assert not m._FORBIDDEN_SQL_RE.match(sql)
        assert ";" not in sql
    m.validate_sql_statements(MIGRATION_036.name, sqls)


def test_migracao_e_autocontida_sem_importar_o_app_nem_dataclass():
    source = MIGRATION_036.read_text(encoding="utf-8")
    assert "dataclass" not in source
    assert "from app" not in source and "import app" not in source


def test_recorte_composto_e_reproduzivel():
    """`compose_036_snapshot` gera exatamente o JSON versionado."""
    from tests.support.compose_036_snapshot import compose

    assert compose() == SNAPSHOT

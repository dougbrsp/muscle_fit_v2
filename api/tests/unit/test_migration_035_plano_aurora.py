"""Plano da migracao 035 (estrelas por PAR exercicio+equipamento) contra o
`information_schema` REAL da Aurora de producao (recorte composto em
tests/unit/data/aurora_schema_2026_09_17_035.json; ver `_origem`).

Producao esta na versao 33: nao tem a tabela da 034 nem a da 035. No deploy
034 e 035 sobem JUNTAS, entao a sequencia real na Aurora e:

    034 -> CREATE TABLE user_equipment_preferences
    035 -> CREATE TABLE user_equipment_exercise_preferences
           DROP TABLE IF EXISTS user_equipment_preferences

Estado convergido (035 ja aplicada) -> 0 statements.

O mesmo plano contra MySQL real, nos dois backends, esta em
tests/integration/dataapi/test_pr08_preferencias_recomendador.py.
"""

from __future__ import annotations

import copy

import pytest

from app import migrate as m
from tests.support import preferences_034
from tests.support.preferences_035 import MIGRATION_035, OLD_TABLE, TABLE, load_snapshot, module
from tests.support.safety_032_033 import SafetySnapshotDB

SNAPSHOT = load_snapshot()
DROP_OLD = f"DROP TABLE IF EXISTS {OLD_TABLE}"


def _plan(snapshot):
    db = SafetySnapshotDB(snapshot)
    statements = module()["plan"](db)
    return [(" ".join(sql.split()), params) for sql, params in statements], db


def _add_table(snap, table, columns, indexes, fks):
    snap = copy.deepcopy(snap)
    snap["tables"].append({"table_name": table, "engine": "InnoDB", "collation": "utf8mb4_0900_ai_ci"})
    for pos, (col, type_, ckey) in enumerate(columns, start=1):
        snap["columns"].append(
            {"t": table, "pos": pos, "col": col, "type": type_, "nullable": "NO", "dflt": None, "ckey": ckey, "extra": "", "coll": None}
        )
    for idx, cols, non_unique in indexes:
        for seq, col in enumerate(cols, start=1):
            snap["indexes"].append({"t": table, "idx": idx, "seq": seq, "col": col, "non_unique": non_unique})
    for fk, col, ref_t in fks:
        snap["foreign_keys"].append(
            {"t": table, "fk": fk, "col": col, "ref_t": ref_t, "ref_col": "id", "on_delete": "CASCADE", "on_update": "NO ACTION"}
        )
    return snap


def _after_034(snapshot):
    """Estado da Aurora logo depois de a 034 rodar (mesmo lote do deploy)."""
    return _add_table(
        snapshot,
        OLD_TABLE,
        [("user_id", "bigint", "PRI"), ("equipment_id", "bigint", "PRI"), ("stars", "tinyint", ""), ("updated_at", "timestamp", "")],
        [("PRIMARY", ["user_id", "equipment_id"], 0), ("idx_uep_user_stars", ["user_id", "stars"], 1), ("idx_uep_equipment", ["equipment_id"], 1)],
        [("fk_uep_user", "user_id", "users"), ("fk_uep_equipment", "equipment_id", "equipment")],
    )


def _with_new_table(snapshot):
    return _add_table(
        snapshot,
        TABLE,
        [
            ("user_id", "bigint", "PRI"),
            ("equipment_exercise_id", "bigint", "PRI"),
            ("stars", "tinyint(4)", ""),  # MySQL < 8.0.19 mostra a largura
            ("updated_at", "timestamp", ""),
        ],
        [
            ("PRIMARY", ["user_id", "equipment_exercise_id"], 0),
            ("idx_ueep_user_stars", ["user_id", "stars"], 1),
            ("idx_ueep_pair", ["equipment_exercise_id"], 1),
        ],
        [("fk_ueep_user", "user_id", "users"), ("fk_ueep_pair", "equipment_exercise_id", "equipment_exercises")],
    )


def test_recorte_e_da_aurora_de_hoje():
    assert SNAPSHOT["versao"][0]["mysql"].startswith("8.0")
    tables = {t["table_name"]: t["engine"] for t in SNAPSHOT["tables"]}
    assert tables["users"] == tables["equipment_exercises"] == "InnoDB"
    assert TABLE not in tables and OLD_TABLE not in tables
    ids = {(c["t"], c["col"]): (c["type"], c["ckey"]) for c in SNAPSHOT["columns"] if c["col"] == "id"}
    assert ids[("users", "id")] == ids[("equipment_exercises", "id")] == ("bigint", "PRI")


def _assert_create(sql):
    assert sql.startswith(
        f"CREATE TABLE IF NOT EXISTS {TABLE} ( user_id BIGINT NOT NULL, equipment_exercise_id BIGINT NOT NULL,"
    )
    assert "stars TINYINT NOT NULL" in sql
    assert "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in sql
    assert "PRIMARY KEY (user_id, equipment_exercise_id)" in sql
    assert "KEY idx_ueep_user_stars (user_id, stars)" in sql
    assert "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE" in sql
    assert "FOREIGN KEY (equipment_exercise_id) REFERENCES equipment_exercises(id) ON DELETE CASCADE" in sql
    assert "CHECK (stars BETWEEN 1 AND 5)" in sql
    assert sql.endswith("ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci")


def test_sequencia_real_do_deploy_034_depois_035():
    """E o que acontece em producao: a 034 cria a tabela antiga e, no mesmo
    lote, a 035 cria a nova e remove a antiga."""
    plan_034 = preferences_034.module()["plan"](SafetySnapshotDB(preferences_034.load_snapshot()))
    assert len(plan_034) == 1 and f"CREATE TABLE IF NOT EXISTS {OLD_TABLE}" in plan_034[0][0]

    statements, db = _plan(_after_034(SNAPSHOT))
    assert [params for _, params in statements] == [None, None]
    _assert_create(statements[0][0])
    assert statements[1][0] == DROP_OLD  # so depois de a nova existir
    assert len(db.queries) == 2  # TABLES + COLUMNS; nenhuma leitura de dados


def test_sem_a_034_so_cria_a_tabela_nova():
    statements, _ = _plan(SNAPSHOT)
    assert len(statements) == 1
    _assert_create(statements[0][0])


def test_estado_convergido_e_zero_statements():
    assert _plan(_with_new_table(SNAPSHOT))[0] == []


def test_nova_ja_existe_e_antiga_sobrou_so_dropa_a_antiga():
    """Execucao interrompida entre os dois statements: retomar converge."""
    assert [sql for sql, _ in _plan(_with_new_table(_after_034(SNAPSHOT)))[0]] == [DROP_OLD]


def _mutate_column(snap, table, col, **changes):
    for column in snap["columns"]:
        if (column["t"], column["col"]) == (table, col):
            column.update(changes)
    return snap


def _both(snapshot):
    return _with_new_table(_after_034(snapshot))


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda s: _mutate_column(s, "users", "id", type="bigint unsigned"), "users.id e bigint unsigned"),
        (lambda s: _mutate_column(s, "equipment_exercises", "id", ckey=""), "equipment_exercises.id nao e PRIMARY KEY"),
        (
            lambda s: {**s, "tables": [t for t in s["tables"] if t["table_name"] != "equipment_exercises"]},
            "tabela referenciada equipment_exercises nao existe",
        ),
        (
            lambda s: {**s, "tables": [{**t, "engine": "MyISAM"} if t["table_name"] == "users" else t for t in s["tables"]]},
            "users usa ENGINE=MyISAM",
        ),
        (lambda s: _mutate_column(s, TABLE, "stars", type="int"), "stars e int"),
        (lambda s: _mutate_column(s, TABLE, "stars", nullable="YES"), "stars tem IS_NULLABLE=YES"),
        (
            lambda s: {**s, "columns": [c for c in s["columns"] if (c["t"], c["col"]) != (TABLE, "updated_at")]},
            "updated_at nao existe",
        ),
        (
            lambda s: {
                **s,
                "indexes": [i for i in s["indexes"] if not (i["t"] == TABLE and i["idx"] == "PRIMARY" and i["seq"] == 2)],
            },
            f"PRIMARY KEY de {TABLE}",
        ),
        (
            lambda s: {**s, "indexes": [i for i in s["indexes"] if i["idx"] != "idx_ueep_user_stars"]},
            "indice idx_ueep_user_stars",
        ),
        (
            lambda s: {**s, "foreign_keys": [f for f in s["foreign_keys"] if f["fk"] != "fk_ueep_pair"]},
            f"FKs de {TABLE}",
        ),
        # tabela "antiga" que nao tem o formato da 034 nao e nossa: nao dropar
        (
            lambda s: {**s, "columns": [c for c in s["columns"] if (c["t"], c["col"]) != (OLD_TABLE, "stars")]},
            f"{OLD_TABLE} tem colunas",
        ),
        (
            lambda s: {
                **s,
                "columns": s["columns"]
                + [{"t": OLD_TABLE, "pos": 5, "col": "notes", "type": "text", "nullable": "YES", "dflt": None, "ckey": "", "extra": "", "coll": None}],
            },
            f"{OLD_TABLE} tem colunas",
        ),
    ],
)
def test_estado_inesperado_falha_antes_de_qualquer_statement(mutate, message):
    with pytest.raises(Exception, match=message) as exc:
        _plan(mutate(_both(SNAPSHOT)))
    assert "Nenhum statement executado" in str(exc.value)


def test_plano_e_um_statement_por_chamada_sem_estado_de_sessao():
    sqls = [sql for sql, _ in _plan(_after_034(SNAPSHOT))[0]]
    assert len(sqls) == 2
    for sql in sqls:
        assert not m._FORBIDDEN_SQL_RE.match(sql)
        assert ";" not in sql
    m.validate_sql_statements(MIGRATION_035.name, sqls)


def test_migracao_e_autocontida_sem_importar_o_app_nem_dataclass():
    source = MIGRATION_035.read_text(encoding="utf-8")
    assert "dataclass" not in source
    assert "from app" not in source and "import app" not in source

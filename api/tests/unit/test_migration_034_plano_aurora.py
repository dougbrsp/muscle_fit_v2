"""Plano da migracao 034 contra o `information_schema` REAL da Aurora de
producao (lido em 2026-09-17, somente SELECT via RDS Data API; recorte em
tests/unit/data/aurora_schema_2026_09_17_034.json).

Na Aurora de hoje (versao 33): `user_equipment_preferences` nao existe;
`users.id` e `equipment.id` sao BIGINT signed PRIMARY KEY em InnoDB. Logo o
plano e exatamente 1 `CREATE TABLE`.

O mesmo plano contra MySQL real, nos dois backends, esta em
tests/integration/dataapi/test_pr08_preferencias_recomendador.py.
"""

from __future__ import annotations

import copy

import pytest

from app import migrate as m
from tests.support.preferences_034 import MIGRATION_034, TABLE, load_snapshot, module
from tests.support.safety_032_033 import SafetySnapshotDB

SNAPSHOT = load_snapshot()


def _plan(snapshot):
    db = SafetySnapshotDB(snapshot)
    statements = module()["plan"](db)
    return [(" ".join(sql.split()), params) for sql, params in statements], db


def test_recorte_e_da_aurora_de_hoje():
    assert SNAPSHOT["versao"][0]["mysql"].startswith("8.0")
    tables = {t["table_name"]: t["engine"] for t in SNAPSHOT["tables"]}
    assert tables["users"] == tables["equipment"] == "InnoDB"
    assert TABLE not in tables
    # 032/033 ja aplicadas em producao
    assert {"equipment_technique_compatibility", "equipment_exercise_technique_overrides"} <= set(tables)
    ids = {(c["t"], c["col"]): (c["type"], c["ckey"]) for c in SNAPSHOT["columns"] if c["col"] == "id"}
    assert ids[("users", "id")] == ids[("equipment", "id")] == ("bigint", "PRI")


def test_na_aurora_e_exatamente_um_create_table():
    statements, db = _plan(SNAPSHOT)
    assert len(statements) == 1 and statements[0][1] is None
    sql = statements[0][0]
    assert sql.startswith(f"CREATE TABLE IF NOT EXISTS {TABLE} ( user_id BIGINT NOT NULL, equipment_id BIGINT NOT NULL,")
    assert "stars TINYINT NOT NULL" in sql
    assert "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in sql
    assert "PRIMARY KEY (user_id, equipment_id)" in sql
    assert "KEY idx_uep_user_stars (user_id, stars)" in sql
    assert "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE" in sql
    assert "FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE" in sql
    assert "CHECK (stars BETWEEN 1 AND 5)" in sql
    assert sql.endswith("ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci")
    assert len(db.queries) == 2  # TABLES + COLUMNS; nenhuma leitura de dados


def _with_table(snapshot):
    snap = copy.deepcopy(snapshot)
    snap["tables"].append({"table_name": TABLE, "engine": "InnoDB", "collation": "utf8mb4_0900_ai_ci"})
    for pos, (col, type_, ckey) in enumerate(
        [
            ("user_id", "bigint", "PRI"),
            ("equipment_id", "bigint", "PRI"),
            ("stars", "tinyint(4)", ""),  # MySQL < 8.0.19 mostra a largura
            ("updated_at", "timestamp", ""),
        ],
        start=1,
    ):
        snap["columns"].append(
            {"t": TABLE, "pos": pos, "col": col, "type": type_, "nullable": "NO", "dflt": None, "ckey": ckey, "extra": "", "coll": None}
        )
    snap["indexes"] += [
        {"t": TABLE, "idx": "PRIMARY", "seq": 1, "col": "user_id", "non_unique": 0},
        {"t": TABLE, "idx": "PRIMARY", "seq": 2, "col": "equipment_id", "non_unique": 0},
        {"t": TABLE, "idx": "idx_uep_user_stars", "seq": 1, "col": "user_id", "non_unique": 1},
        {"t": TABLE, "idx": "idx_uep_user_stars", "seq": 2, "col": "stars", "non_unique": 1},
        {"t": TABLE, "idx": "idx_uep_equipment", "seq": 1, "col": "equipment_id", "non_unique": 1},
    ]
    for col, ref_t in (("user_id", "users"), ("equipment_id", "equipment")):
        snap["foreign_keys"].append(
            {"t": TABLE, "fk": f"fk_uep_{col}", "col": col, "ref_t": ref_t, "ref_col": "id", "on_delete": "CASCADE", "on_update": "NO ACTION"}
        )
    return snap


def test_reexecucao_e_zero_statements():
    assert _plan(_with_table(SNAPSHOT))[0] == []


def _mutate_column(snap, table, col, **changes):
    for column in snap["columns"]:
        if (column["t"], column["col"]) == (table, col):
            column.update(changes)
    return snap


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda s: _mutate_column(s, "users", "id", type="bigint unsigned"), "users.id e bigint unsigned"),
        (lambda s: _mutate_column(s, "equipment", "id", ckey=""), "equipment.id nao e PRIMARY KEY"),
        (
            lambda s: {**s, "tables": [t for t in s["tables"] if t["table_name"] != "equipment"]},
            "tabela referenciada equipment nao existe",
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
            "PRIMARY KEY de user_equipment_preferences",
        ),
        (
            lambda s: {**s, "indexes": [i for i in s["indexes"] if i["idx"] != "idx_uep_user_stars"]},
            "indice idx_uep_user_stars",
        ),
        (
            lambda s: {**s, "foreign_keys": [f for f in s["foreign_keys"] if f["fk"] != "fk_uep_equipment_id"]},
            "FKs de user_equipment_preferences",
        ),
    ],
)
def test_estado_inesperado_falha_antes_de_qualquer_statement(mutate, message):
    with pytest.raises(Exception, match=message) as exc:
        _plan(mutate(_with_table(SNAPSHOT)))
    assert "Nenhum statement executado" in str(exc.value)


def test_plano_e_um_statement_por_chamada_sem_estado_de_sessao():
    sqls = [sql for sql, _ in _plan(SNAPSHOT)[0]]
    assert len(sqls) == 1
    assert not m._FORBIDDEN_SQL_RE.match(sqls[0])
    assert ";" not in sqls[0]
    m.validate_sql_statements(MIGRATION_034.name, sqls)


def test_migracao_e_autocontida_sem_importar_o_app_nem_dataclass():
    source = MIGRATION_034.read_text(encoding="utf-8")
    assert "dataclass" not in source
    assert "from app" not in source and "import app" not in source

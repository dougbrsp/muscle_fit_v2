"""Plano das migracoes 032 e 033 contra o `information_schema` REAL da Aurora
de producao (lido em 2026-09-17, somente SELECT via RDS Data API; recorte em
tests/unit/data/aurora_schema_2026_09_17_032_033.json).

Na Aurora de hoje (versao 31: a 031 ja rodou): nenhuma das 5 colunas existe e
nenhuma das 2 tabelas existe. Logo:
- 032 = exatamente 2 `ALTER TABLE`;
- 033 = exatamente 2 `CREATE TABLE`.

O mesmo plano contra MySQL real, nos dois backends, esta em
tests/integration/dataapi/test_migration_032_033_seguranca_equipamento.py.
"""

from __future__ import annotations

import copy

import pytest

from app import migrate as m
from tests.support.safety_032_033 import (
    ALTER_EQUIPMENT,
    ALTER_EQUIPMENT_EXERCISES,
    MIGRATION_032,
    MIGRATION_033,
    NEW_TABLES,
    SafetySnapshotDB,
    load_snapshot,
    module,
    without_advanced_techniques,
)

SNAPSHOT = load_snapshot()


def _plan(path, snapshot, dry_run=False):
    db = SafetySnapshotDB(snapshot, dry_run=dry_run)
    statements = module(path)["plan"](db)
    return [(" ".join(sql.split()), params) for sql, params in statements], db


def _add_column(snapshot, table, col, type_, nullable="YES"):
    snap = copy.deepcopy(snapshot)
    snap["columns"].append(
        {"t": table, "pos": 99, "col": col, "type": type_, "nullable": nullable, "dflt": None, "ckey": "", "extra": "", "coll": None}
    )
    return snap


# ---------------------------------------------------------------------------
# Recorte
# ---------------------------------------------------------------------------


def test_recorte_e_da_aurora_de_hoje():
    assert SNAPSHOT["versao"][0]["mysql"].startswith("8.0")
    names = {t["table_name"] for t in SNAPSHOT["tables"]}
    assert {"equipment", "equipment_exercises", "users"} <= names
    assert "advanced_techniques" in names  # 031 ja aplicada em producao
    assert not set(NEW_TABLES) & names
    types = {(c["t"], c["col"]): c["type"] for c in SNAPSHOT["columns"]}
    assert types[("equipment", "id")] == types[("equipment_exercises", "id")] == types[("users", "id")] == "bigint"
    code = next(c for c in SNAPSHOT["columns"] if (c["t"], c["col"]) == ("advanced_techniques", "code"))
    assert (code["type"], code["ckey"], code["coll"]) == ("varchar(20)", "PRI", "utf8mb4_0900_ai_ci")
    assert not [key for key in types if key[1] in ("max_safe_rpe", "load_increment_kg", "safety_notes", "max_safe_rpe_override")]


# ---------------------------------------------------------------------------
# 032
# ---------------------------------------------------------------------------


def test_032_na_aurora_sao_exatamente_dois_alter_table():
    statements, db = _plan(MIGRATION_032, SNAPSHOT)
    assert statements == [(ALTER_EQUIPMENT, None), (ALTER_EQUIPMENT_EXERCISES, None)]
    assert len(db.queries) == 2  # TABLES + COLUMNS; nenhuma leitura de dados


def test_032_so_adiciona_a_coluna_que_falta():
    snap = _add_column(SNAPSHOT, "equipment", "max_safe_rpe", "tinyint")
    snap = _add_column(snap, "equipment_exercises", "safety_notes", "text")
    statements, _ = _plan(MIGRATION_032, snap)
    assert [sql for sql, _ in statements] == [
        "ALTER TABLE equipment ADD COLUMN load_increment_kg DECIMAL(5,2) NULL, ADD COLUMN safety_notes TEXT NULL",
        "ALTER TABLE equipment_exercises ADD COLUMN max_safe_rpe_override TINYINT NULL",
    ]


def test_032_reexecucao_e_zero_statements():
    snap = SNAPSHOT
    for table, col, type_ in [
        ("equipment", "max_safe_rpe", "tinyint"),
        ("equipment", "load_increment_kg", "decimal(5,2)"),
        ("equipment", "safety_notes", "text"),
        ("equipment_exercises", "max_safe_rpe_override", "tinyint(4)"),  # MySQL < 8.0.19 mostra a largura
        ("equipment_exercises", "safety_notes", "text"),
    ]:
        snap = _add_column(snap, table, col, type_)
    assert _plan(MIGRATION_032, snap)[0] == []


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda s: _add_column(s, "equipment", "max_safe_rpe", "varchar(10)"), "equipment.max_safe_rpe e varchar\\(10\\)"),
        (lambda s: _add_column(s, "equipment", "load_increment_kg", "decimal(5,2)", "NO"), "IS_NULLABLE=NO"),
        (lambda s: {**s, "tables": [t for t in s["tables"] if t["table_name"] != "equipment_exercises"]}, "equipment_exercises nao existe"),
        (
            lambda s: {**s, "tables": [{**t, "engine": "MyISAM"} if t["table_name"] == "equipment" else t for t in s["tables"]]},
            "ENGINE=MyISAM",
        ),
    ],
)
def test_032_estado_inesperado_falha_antes_de_qualquer_statement(mutate, message):
    with pytest.raises(Exception, match=message) as exc:
        _plan(MIGRATION_032, mutate(copy.deepcopy(SNAPSHOT)))
    assert "Nenhum statement executado" in str(exc.value)


# ---------------------------------------------------------------------------
# 033
# ---------------------------------------------------------------------------


def test_033_na_aurora_sao_exatamente_dois_create_table():
    statements, db = _plan(MIGRATION_033, SNAPSHOT)
    assert len(statements) == 2 and all(params is None for _, params in statements)
    compat, overrides = (sql for sql, _ in statements)
    assert compat.startswith("CREATE TABLE IF NOT EXISTS equipment_technique_compatibility ( equipment_id BIGINT NOT NULL,")
    assert "PRIMARY KEY (equipment_id, technique_code)" in compat
    assert "FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE" in compat
    assert overrides.startswith(
        "CREATE TABLE IF NOT EXISTS equipment_exercise_technique_overrides ( equipment_exercise_id BIGINT NOT NULL,"
    )
    assert "PRIMARY KEY (equipment_exercise_id, technique_code)" in overrides
    assert "FOREIGN KEY (equipment_exercise_id) REFERENCES equipment_exercises(id) ON DELETE CASCADE" in overrides
    for sql in (compat, overrides):
        assert "technique_code VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL" in sql
        assert "is_compatible TINYINT(1) NOT NULL" in sql
        assert "updated_by_user_id BIGINT NULL" in sql
        assert "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in sql
        assert "FOREIGN KEY (technique_code) REFERENCES advanced_techniques(code)" in sql
        assert "FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL" in sql
        assert sql.endswith("ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci")
    assert len(db.queries) == 2  # TABLES + COLUMNS; nenhuma leitura de dados


def test_033_sem_a_031_a_execucao_real_falha_segura():
    with pytest.raises(Exception, match="tabela referenciada advanced_techniques nao existe") as exc:
        _plan(MIGRATION_033, without_advanced_techniques(SNAPSHOT))
    assert "Nenhum statement executado" in str(exc.value)


def test_033_dry_run_do_lote_com_a_031_pendente_mostra_o_plano():
    """`python -m app.migrate --dry-run` num banco com a 031 ainda pendente: o
    dry-run nao cria a tabela; o plano da 033 precisa aparecer mesmo assim."""
    statements, _ = _plan(MIGRATION_033, without_advanced_techniques(SNAPSHOT), dry_run=True)
    assert len(statements) == 2


def _with_new_tables(snapshot):
    snap = copy.deepcopy(snapshot)
    for table, owner, owner_table, prefix in (
        ("equipment_technique_compatibility", "equipment_id", "equipment", "fk_etc"),
        ("equipment_exercise_technique_overrides", "equipment_exercise_id", "equipment_exercises", "fk_eeto"),
    ):
        snap["tables"].append({"table_name": table, "engine": "InnoDB", "collation": "utf8mb4_0900_ai_ci"})
        for pos, (col, type_, nullable, ckey) in enumerate(
            [
                (owner, "bigint", "NO", "PRI"),
                ("technique_code", "varchar(20)", "NO", "PRI"),
                ("is_compatible", "tinyint(1)", "NO", ""),
                ("updated_by_user_id", "bigint", "YES", "MUL"),
                ("updated_at", "timestamp", "NO", ""),
            ],
            start=1,
        ):
            snap["columns"].append(
                {"t": table, "pos": pos, "col": col, "type": type_, "nullable": nullable, "dflt": None, "ckey": ckey, "extra": "", "coll": None}
            )
        snap["indexes"] += [
            {"t": table, "idx": "PRIMARY", "seq": 1, "col": owner, "non_unique": 0},
            {"t": table, "idx": "PRIMARY", "seq": 2, "col": "technique_code", "non_unique": 0},
        ]
        for col, ref_t, ref_col in ((owner, owner_table, "id"), ("technique_code", "advanced_techniques", "code"), ("updated_by_user_id", "users", "id")):
            snap["foreign_keys"].append(
                {"t": table, "fk": f"{prefix}_{col}", "col": col, "ref_t": ref_t, "ref_col": ref_col, "on_delete": "CASCADE", "on_update": "NO ACTION"}
            )
    return snap


def test_033_reexecucao_e_zero_statements():
    assert _plan(MIGRATION_033, _with_new_tables(SNAPSHOT))[0] == []


def _mutate_column(snap, table, col, **changes):
    for column in snap["columns"]:
        if (column["t"], column["col"]) == (table, col):
            column.update(changes)
    return snap


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda s: _mutate_column(s, "equipment", "id", type="bigint unsigned"), "equipment.id e bigint unsigned"),
        (lambda s: _mutate_column(s, "users", "id", ckey=""), "users.id nao e PRIMARY KEY"),
        (lambda s: _mutate_column(s, "advanced_techniques", "code", coll="utf8mb4_unicode_ci"), "collation utf8mb4_unicode_ci"),
        (lambda s: _mutate_column(s, "equipment_technique_compatibility", "is_compatible", nullable="YES"), "is_compatible tem IS_NULLABLE=YES"),
        (
            lambda s: {**s, "indexes": [i for i in s["indexes"] if not (i["t"] == "equipment_exercise_technique_overrides" and i["seq"] == 2)]},
            "PRIMARY KEY de equipment_exercise_technique_overrides",
        ),
        (
            lambda s: {**s, "foreign_keys": [f for f in s["foreign_keys"] if f["fk"] != "fk_etc_technique_code"]},
            "FKs de equipment_technique_compatibility",
        ),
    ],
)
def test_033_estado_inesperado_falha_antes_de_qualquer_statement(mutate, message):
    with pytest.raises(Exception, match=message) as exc:
        _plan(MIGRATION_033, mutate(_with_new_tables(SNAPSHOT)))
    assert "Nenhum statement executado" in str(exc.value)


# ---------------------------------------------------------------------------
# Compatibilidade com o shim RDS Data API
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path, snapshot", [(MIGRATION_032, SNAPSHOT), (MIGRATION_033, SNAPSHOT)])
def test_plano_e_um_statement_por_chamada_sem_estado_de_sessao(path, snapshot):
    sqls = [sql for sql, _ in _plan(path, snapshot)[0]]
    assert len(sqls) == 2
    for sql in sqls:
        assert not m._FORBIDDEN_SQL_RE.match(sql), sql
        assert ";" not in sql
    m.validate_sql_statements(path.name, sqls)


@pytest.mark.parametrize("path", [MIGRATION_032, MIGRATION_033])
def test_migracao_e_autocontida_sem_importar_o_app_nem_dataclass(path):
    source = path.read_text(encoding="utf-8")
    assert "dataclass" not in source
    assert "from app" not in source and "import app" not in source

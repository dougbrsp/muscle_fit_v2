"""Plano da migracao 031 contra a lista REAL de tabelas da Aurora de producao
(`information_schema` extraido em 2026-09-14, recorte em
tests/unit/data/aurora_schema_2026_09_14_031.json): `advanced_techniques` nao
existe la, entao o plano e 1 CREATE TABLE + 8 INSERT, com uma unica leitura
(`information_schema.TABLES`) e nenhuma leitura de dados.

O mesmo plano contra MySQL real, nos dois backends, esta em
tests/integration/dataapi/test_migration_031_advanced_techniques.py.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.paridade_028 import SnapshotDB

MIGRATION_PATH = m.DEFAULT_MIGRATIONS_DIR / "031_advanced_techniques_catalog.py"
SNAPSHOT = json.loads((Path(__file__).parent / "data" / "aurora_schema_2026_09_14_031.json").read_text(encoding="utf-8"))
CODES = ["rest_pause", "drop_set", "fst7", "gvt", "forced_reps", "negative_reps", "partial_reps", "cheat_reps"]

# COLUMN_TYPE como o MySQL 8.0 reporta para a tabela criada pela 031.
CREATED_COLUMNS = [
    ("code", "varchar(20)", "NO", "PRI"),
    ("name", "varchar(80)", "NO", ""),
    ("failure_requirement", "enum('failure_not_required','may_reach_failure','requires_failure','beyond_failure')", "NO", ""),
    ("requires_partner", "tinyint(1)", "NO", ""),
    ("auto_applicable", "tinyint(1)", "NO", ""),
    ("min_experience", "enum('beginner','intermediate','advanced')", "NO", ""),
    ("set_type", "varchar(40)", "NO", ""),
    ("activation_set_equivalent", "decimal(4,2)", "NO", ""),
    ("recovery_cost_factor", "decimal(4,2)", "YES", ""),
    ("is_active", "tinyint(1)", "NO", ""),
]


class CatalogSnapshotDB(SnapshotDB):
    """SnapshotDB + a unica leitura de dados da 031: os codigos ja semeados."""

    def __init__(self, snapshot, codes=None):
        super().__init__(snapshot)
        self.codes = codes

    def query(self, sql, params=None):
        if sql == "SELECT code FROM advanced_techniques" and self.codes is not None:
            self.queries.append(sql)
            return [{"code": code} for code in self.codes]
        return super().query(sql, params)


def _module():
    return m.load_python_migration(MIGRATION_PATH).__globals__


def _plan(snapshot, codes=None):
    db = CatalogSnapshotDB(snapshot, codes)
    return _module()["plan"](db), db


def _with_table(engine="InnoDB"):
    snap = copy.deepcopy(SNAPSHOT)
    snap["tables"].append({"table_name": "advanced_techniques", "engine": engine})
    snap["columns"] = [
        {"t": "advanced_techniques", "pos": pos, "col": col, "type": type_, "nullable": nullable, "dflt": None, "ckey": ckey, "extra": ""}
        for pos, (col, type_, nullable, ckey) in enumerate(CREATED_COLUMNS, start=1)
    ]
    return snap


def test_recorte_e_da_aurora_mysql_8_sem_a_tabela():
    assert SNAPSHOT["versao"][0]["mysql"].startswith("8.0")
    names = {t["table_name"] for t in SNAPSHOT["tables"]}
    assert "advanced_techniques" not in names
    assert {"workout_plan_exercises", "workout_plan_template_slot_items"} <= names


def test_aurora_de_producao_031_cria_a_tabela_e_semeia_os_8_codigos():
    statements, db = _plan(SNAPSHOT)
    assert len(db.queries) == 1 and "information_schema.TABLES" in db.queries[0]
    assert len(statements) == 9
    create_sql, create_params = statements[0]
    assert " ".join(create_sql.split()).startswith("CREATE TABLE IF NOT EXISTS advanced_techniques (")
    assert create_params is None
    inserts = statements[1:]
    assert {sql for sql, _ in inserts} == {_module()["INSERT_SQL"]}
    assert [params[0] for _, params in inserts] == CODES


def test_seed_segue_a_tabela_5_9_do_plano():
    seed = {row[0]: row[1:] for row in _module()["SEED"]}
    assert seed == {
        "rest_pause": ("Rest-pause", "requires_failure", 0, 1, "intermediate", "rest_pause"),
        "drop_set": ("Drop-set", "requires_failure", 0, 1, "intermediate", "drop_set"),
        "fst7": ("FST-7", "requires_failure", 0, 0, "advanced", "working"),
        "gvt": ("GVT (German Volume Training)", "failure_not_required", 0, 0, "intermediate", "working"),
        "forced_reps": ("Repetições forçadas", "beyond_failure", 1, 0, "advanced", "forced_rep"),
        "negative_reps": ("Negativas", "beyond_failure", 1, 0, "advanced", "forced_rep"),
        "partial_reps": ("Parciais", "beyond_failure", 0, 0, "advanced", "forced_rep"),
        "cheat_reps": ("Cheat reps", "beyond_failure", 0, 0, "advanced", "forced_rep"),
    }
    # so rest_pause e drop_set sao auto-aplicaveis; cheat_reps nunca (TEC-033)
    assert [code for code, row in seed.items() if row[3]] == ["rest_pause", "drop_set"]
    assert all(len(code) <= 20 for code in seed)


def test_set_type_do_seed_e_o_que_o_backend_normaliza():
    from app import set_types

    canonical = {set_types.REST_PAUSE, set_types.DROP_SET, set_types.FORCED_REP, "working"}
    assert {row[6] for row in _module()["SEED"]} <= canonical


def test_reexecucao_com_tabela_completa_e_zero_statements():
    statements, db = _plan(_with_table(), codes=CODES)
    assert statements == []
    assert len(db.queries) == 3  # TABLES, COLUMNS e os codigos do proprio catalogo


def test_tabela_presente_so_insere_os_codigos_que_faltam_e_nunca_atualiza():
    statements, _ = _plan(_with_table(), codes=["rest_pause", "drop_set", "codigo_custom"])
    assert [params[0] for _, params in statements] == CODES[2:]
    assert all(sql.startswith("INSERT INTO advanced_techniques") for sql, _ in statements)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda s: s["columns"].pop(), "is_active nao existe"),
        (lambda s: s["columns"][0].update(type="varchar(10)"), "esperado varchar\\(20\\)"),
        (lambda s: s["columns"][8].update(nullable="NO"), "recovery_cost_factor tem IS_NULLABLE=NO"),
        (lambda s: s["columns"][0].update(ckey=""), "PRIMARY KEY"),
        (lambda s: s["tables"][-1].update(engine="MyISAM"), "ENGINE=MyISAM"),
    ],
)
def test_tabela_em_formato_inesperado_falha_antes_de_qualquer_statement(mutate, message):
    snap = _with_table()
    mutate(snap)
    with pytest.raises(Exception, match=message) as exc:
        _plan(snap, codes=CODES)
    assert "Nenhum statement executado" in str(exc.value)


def test_plano_e_um_statement_por_chamada_sem_estado_de_sessao():
    statements, _ = _plan(SNAPSHOT)
    sqls = [" ".join(sql.split()) for sql, _ in statements]
    for sql in sqls:
        assert not m._FORBIDDEN_SQL_RE.match(sql), sql
        assert ";" not in sql
    m.validate_sql_statements(MIGRATION_PATH.name, sqls)


def test_migracao_e_autocontida_sem_importar_o_app_nem_dataclass():
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "dataclass" not in source
    assert "from app" not in source and "import app" not in source

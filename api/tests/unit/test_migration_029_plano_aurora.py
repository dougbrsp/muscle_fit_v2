"""Plano da migracao 029 contra o schema REAL da Aurora de producao
(`information_schema` extraido em 2026-09-14, recorte de `exercise_aliases` em
tests/unit/data/aurora_schema_2026_09_14_029.json): 0 statements e nenhuma
leitura de dados.

O mesmo plano contra MySQL real, nos dois backends, esta em
tests/integration/dataapi/test_migration_029_aliases_sem_unique_global.py.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.paridade_028 import SnapshotDB

MIGRATION_PATH = m.DEFAULT_MIGRATIONS_DIR / "029_aliases_sem_unique_global.py"
SNAPSHOT = json.loads((Path(__file__).parent / "data" / "aurora_schema_2026_09_14_029.json").read_text(encoding="utf-8"))
DEV_CI_PLAN = ["ALTER TABLE exercise_aliases DROP INDEX uq_exercise_aliases_alias"]


def _load_up():
    return m.load_python_migration(MIGRATION_PATH)


def _plan(snapshot):
    db = SnapshotDB(snapshot)
    return [sql for sql, _ in _load_up().__globals__["plan"](db)], db


def _dev_ci_snapshot():
    """Recorte da Aurora + o UNIQUE global criado pelo 001 (DEV/CI)."""
    snap = copy.deepcopy(SNAPSHOT)
    snap["indexes"].append({"t": "exercise_aliases", "idx": "uq_exercise_aliases_alias", "seq": 1, "col": "alias", "non_unique": 0})
    return snap


def test_recorte_e_da_aurora_mysql_8_e_nao_tem_indice_em_alias():
    assert SNAPSHOT["versao"][0]["mysql"].startswith("8.0")
    indexes = {}
    for i in SNAPSHOT["indexes"]:
        indexes.setdefault(i["idx"], []).append((i["seq"], i["col"], i["non_unique"]))
    assert indexes == {
        "PRIMARY": [(1, "id", 0)],
        "idx_exercise_aliases_exercise": [(1, "exercise_id", 1)],
        "uq_exercise_aliases_exercise_alias": [(1, "exercise_id", 0), (2, "alias", 0)],
    }
    # nenhum indice (unico ou nao) comeca por `alias` na Aurora
    assert not [i for i in SNAPSHOT["indexes"] if i["col"] == "alias" and i["seq"] == 1]


def test_aurora_de_producao_029_executa_zero_statements():
    statements, db = _plan(SNAPSHOT)
    assert statements == []
    assert len(db.queries) == 2  # so information_schema, sem contagem de linhas

    facade = m.MigrationDB(conn=None)
    facade.query, facade.query_one = db.query, db.query_one
    facade.execute = lambda sql, params=None: pytest.fail(f"escrita na Aurora: {sql}")
    _load_up()(facade)
    assert facade.statements == []


def test_controle_mesmo_dublê_no_formato_dev_ci_remove_o_unique_global():
    statements, _ = _plan(_dev_ci_snapshot())
    assert statements == DEV_CI_PLAN


def test_sem_unique_por_exercicio_falha_antes_de_qualquer_statement():
    snap = _dev_ci_snapshot()
    snap["indexes"] = [i for i in snap["indexes"] if i["idx"] != "uq_exercise_aliases_exercise_alias"]
    with pytest.raises(Exception, match="uq_exercise_aliases_exercise_alias ausente") as exc:
        _plan(snap)
    assert "Nenhum statement executado" in str(exc.value)


def test_unique_global_com_formato_inesperado_falha():
    snap = _dev_ci_snapshot()
    for i in snap["indexes"]:
        if i["idx"] == "uq_exercise_aliases_alias":
            i["non_unique"] = 1
    with pytest.raises(Exception, match="esperado UNIQUE \\(alias\\)"):
        _plan(snap)


def test_tabela_ausente_falha():
    snap = copy.deepcopy(SNAPSHOT)
    snap["tables"] = []
    with pytest.raises(Exception, match="nao existe"):
        _plan(snap)


def test_plano_de_dev_ci_e_um_statement_por_chamada_sem_estado_de_sessao():
    for statement in DEV_CI_PLAN:
        assert not m._FORBIDDEN_SQL_RE.match(statement), statement
        assert ";" not in statement
    m.validate_sql_statements(MIGRATION_PATH.name, DEV_CI_PLAN)

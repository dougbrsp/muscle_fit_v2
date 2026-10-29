"""Plano da migracao 039 (descricao de execucao do exercicio) contra o
`information_schema` REAL de um MySQL 8 na versao 38 de schema
(tests/unit/data/schema_dev_2026_09_20_039.json; ver `_origem`).

Banco na versao 38: nenhuma das 6 colunas novas existe -> 1 statement.
Estado convergido -> 0 statements.
"""

from __future__ import annotations

import copy

import pytest

from tests.support.descricao_execucao_039 import (
    AFTER_039,
    ALTER_CATALOG,
    CATALOG,
    converged,
    load_snapshot,
    module,
)
from tests.support.safety_032_033 import SafetySnapshotDB

SNAPSHOT = load_snapshot()


def _plan(snapshot):
    db = SafetySnapshotDB(snapshot)
    statements = module()["plan"](db)
    return [(" ".join(sql.split()), params) for sql, params in statements], db


def test_recorte_e_de_um_mysql8_na_versao_38_sem_as_colunas_novas():
    assert SNAPSHOT["versao"][0]["mysql"].startswith("8.0")
    assert SNAPSHOT["versao"][0]["schema_migration_files"] == 38
    engines = {t["table_name"]: t["engine"] for t in SNAPSHOT["tables"]}
    assert engines[CATALOG] == "InnoDB"
    presentes = {(c["t"], c["col"]) for c in SNAPSHOT["columns"]}
    for column, *_ in AFTER_039:
        assert (CATALOG, column) not in presentes, column


def test_reviewed_by_e_bigint_signed_como_users_id():
    """`exercise_catalog.id` e BIGINT UNSIGNED (armadilha do catalogo), mas
    `execution_description_reviewed_by` aponta para `users.id`, que e SIGNED -
    por isso o DDL e BIGINT sem UNSIGNED."""
    ids = {(c["t"], c["col"]): c["type"] for c in SNAPSHOT["columns"] if c["col"] == "id"}
    assert ids[("users", "id")] == "bigint"
    assert ids[(CATALOG, "id")] == "bigint unsigned"
    assert "UNSIGNED" not in ALTER_CATALOG.upper()


def test_banco_na_versao_38_executa_um_unico_alter():
    statements, db = _plan(SNAPSHOT)
    assert statements == [(ALTER_CATALOG, None)]
    assert all(q.strip().startswith("SELECT") for q in db.queries)


def test_todas_as_colunas_novas_sao_nullable_e_sem_default():
    """Nenhuma linha existente muda de comportamento: a 039 e 100% aditiva."""
    for _, ddl, _, nullable, default in module()["COLUMNS"]:
        assert ddl.endswith("NULL"), ddl
        assert "NOT NULL" not in ddl, ddl
        assert nullable == "YES"
        assert default is None


def test_reexecucao_sobre_o_estado_convergido_nao_produz_statement():
    statements, _ = _plan(converged(SNAPSHOT))
    assert statements == []


def test_nao_cria_indice_novo():
    statements, _ = _plan(SNAPSHOT)
    assert "ADD KEY" not in statements[0][0]
    assert "ADD INDEX" not in statements[0][0]


@pytest.mark.parametrize(
    "mutacao, trecho",
    [
        ({"type": "varchar(255)"}, "esperado text"),
        ({"nullable": "NO"}, "IS_NULLABLE=NO"),
        ({"dflt": "x"}, "DEFAULT"),
    ],
)
def test_coluna_presente_com_formato_diferente_falha_antes_de_qualquer_statement(mutacao, trecho):
    snap = converged(SNAPSHOT)
    alvo = next(c for c in snap["columns"] if c["col"] == "execution_description")
    alvo.update(mutacao)
    erro = module()["DescricaoExecucaoError"]
    with pytest.raises(erro) as exc:
        _plan(snap)
    assert trecho in str(exc.value)
    assert "Nenhum statement executado" in str(exc.value)


def test_tabela_ausente_falha_em_vez_de_alterar_o_que_nao_existe():
    snap = copy.deepcopy(SNAPSHOT)
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != CATALOG]
    with pytest.raises(module()["DescricaoExecucaoError"]) as exc:
        _plan(snap)
    assert f"tabela {CATALOG} nao existe" in str(exc.value)


def test_engine_diferente_de_innodb_falha():
    snap = copy.deepcopy(SNAPSHOT)
    for table in snap["tables"]:
        if table["table_name"] == CATALOG:
            table["engine"] = "MyISAM"
    with pytest.raises(module()["DescricaoExecucaoError"]) as exc:
        _plan(snap)
    assert "MyISAM" in str(exc.value)


def test_up_executa_exatamente_o_que_plan_devolveu():
    executados: list[tuple[str, object]] = []

    class _DB(SafetySnapshotDB):
        def execute(self, sql, params=None):
            executados.append((" ".join(sql.split()), params))

    db = _DB(copy.deepcopy(SNAPSHOT))
    module()["up"](db)
    assert executados == [(ALTER_CATALOG, None)]

"""Plano da migração 104 (`food_items.table_base_amount`, nutrição D1).

- Coluna ausente -> 1 `ALTER TABLE ... ADD COLUMN` (INSTANT, nullable, sem default).
- Convergido -> 0 statements; forma divergente -> erro antes do primeiro statement.
- Reversível (`down` solta a coluna), sem FK, sem `exercise_catalog`, só LEITURA no plano.

Rodar: cd api && python -m pytest tests/unit/test_migration_104_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "104_food_items_table_base_amount.py"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _sql(texto: str) -> str:
    return " ".join(texto.split())


class FakeDB:
    def __init__(self, *, tabela=True, coluna=("decimal(7,2)", "YES"), dry_run=False):
        self.tabela = tabela
        self.coluna = coluna
        self.dry_run = dry_run
        self.executados: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": "food_items", "ENGINE": "InnoDB"}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            if not self.tabela or self.coluna is None:
                return []
            tipo, nulo = self.coluna
            return [{"COLUMN_NAME": "table_base_amount", "COLUMN_TYPE": tipo, "IS_NULLABLE": nulo}]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executados.append(sql)


def test_coluna_ausente_cria_com_um_statement_aditivo_e_instantaneo():
    db = FakeDB(coluna=None)
    plano = _module()["plan"](db)
    assert [(_sql(s), p) for s, p in plano] == [
        ("ALTER TABLE food_items ADD COLUMN table_base_amount DECIMAL(7,2) NULL, ALGORITHM=INSTANT", None)
    ]
    assert db.executados == []  # o plano so le


def test_up_executa_o_plano_e_e_idempotente_em_banco_convergido():
    ausente = FakeDB(coluna=None)
    _module()["up"](ausente)
    assert len(ausente.executados) == 1
    convergido = FakeDB()
    _module()["up"](convergido)
    assert convergido.executados == []
    assert _module()["plan"](FakeDB(coluna=("decimal(7,2)", "YES"))) == []


@pytest.mark.parametrize("coluna", [("decimal(8,2)", "YES"), ("decimal(7,2)", "NO"), ("int", "YES")])
def test_forma_divergente_falha_antes_do_primeiro_statement(coluna):
    db = FakeDB(coluna=coluna)
    with pytest.raises(_module()["FoodItemsTableBaseError"], match="Nenhum statement executado"):
        _module()["up"](db)
    assert db.executados == []


def test_tabela_ausente_e_erro_na_execucao_real_e_aceita_no_dry_run():
    with pytest.raises(_module()["FoodItemsTableBaseError"]):
        _module()["plan"](FakeDB(tabela=False))
    assert len(_module()["plan"](FakeDB(tabela=False, dry_run=True))) == 1


def test_down_reversivel_e_idempotente():
    assert [_sql(s) for s, _ in _module()["plan_down"](FakeDB())] == ["ALTER TABLE food_items DROP COLUMN table_base_amount"]
    assert _module()["plan_down"](FakeDB(coluna=None)) == []
    assert _module()["plan_down"](FakeDB(tabela=False)) == []
    db = FakeDB()
    _module()["down"](db)
    assert db.executados == [_module()["DROP_SQL"]]


def test_ddl_sem_fk_e_sem_exercise_catalog_e_um_statement_por_chamada():
    for sql in (_module()["ADD_SQL"], _module()["DROP_SQL"]):
        assert "exercise_catalog" not in sql and "REFERENCES" not in sql and ";" not in sql
        assert ":" not in sql  # a Data API leria como parametro nomeado
    assert _module()["EXPECTED_TYPE"] == "decimal(7,2)" and _module()["EXPECTED_NULLABLE"] == "YES"

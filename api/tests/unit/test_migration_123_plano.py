"""Plano da migracao 123 (#248): `carb_cycle_policy` em nutrition_plans, com preenchimento a partir do JSON.
Sem MySQL: FakeDB le um `information_schema` em memoria e aplica o UPDATE em linhas simuladas."""

from __future__ import annotations

import functools

import pytest

from app import migrate as m
from app.domain.nutrition.carb_cycling import CarbCyclePolicy

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "123_nutrition_plans_carb_cycle_policy.py"
ADD = "ALTER TABLE nutrition_plans ADD COLUMN carb_cycle_policy VARCHAR(24) NULL DEFAULT NULL, ALGORITHM=INSTANT"
DROP = "ALTER TABLE nutrition_plans DROP COLUMN carb_cycle_policy"
OK = ("varchar(24)", "YES", None)
CODIGOS = ("vary_kcal", "fat_compensation")


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, *, tabela=True, coluna=None, dry_run=False, linhas=()):
        self.tabela = tabela
        self.coluna = coluna
        self.dry_run = dry_run
        self.executed: list[tuple[str, tuple | None]] = []
        self.linhas = [dict(r) for r in linhas]  # {"json_policy": ..., "carb_cycle_policy": ...}

    def query(self, sql, params=None):
        assert ":" not in sql
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": "nutrition_plans"}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            assert params == ("nutrition_plans", "carb_cycle_policy")
            if not self.tabela or self.coluna is None:
                return []
            tipo, nul, default = self.coluna
            return [{"TABLE_NAME": "nutrition_plans", "COLUMN_TYPE": tipo, "IS_NULLABLE": nul, "COLUMN_DEFAULT": default}]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        assert ":" not in sql
        self.executed.append((sql, params))
        if sql.startswith("UPDATE"):  # semantica do preenchimento: so NULL e so codigo valido
            for linha in self.linhas:
                if linha["carb_cycle_policy"] is None and linha["json_policy"] in params:
                    linha["carb_cycle_policy"] = linha["json_policy"]
        elif sql == ADD:
            self.coluna = OK


def _backfill():
    return _mod()["backfill_sql"]()


def test_banco_atual_recebe_a_coluna_e_o_preenchimento():
    db = FakeDB()
    _mod()["up"](db)
    assert db.executed == [(ADD, None), (_backfill(), CODIGOS)]
    assert "IS NULL" in _backfill() and "$.policy" in _backfill() and "DROP" not in _backfill()


def test_convergido_so_repete_o_preenchimento_idempotente():
    db = FakeDB(coluna=OK)
    _mod()["up"](db)
    assert db.executed == [(_backfill(), CODIGOS)]


def test_preenchimento_so_com_valor_valido_e_so_coluna_nula_e_idempotente():
    linhas = [
        {"json_policy": "fat_compensation", "carb_cycle_policy": None},  # preenche
        {"json_policy": "xyz", "carb_cycle_policy": None},  # invalido: fica NULL
        {"json_policy": None, "carb_cycle_policy": None},  # sem chave (caso medido hoje): fica NULL
        {"json_policy": "fat_compensation", "carb_cycle_policy": "vary_kcal"},  # coluna ja preenchida: nao sobrescreve
    ]
    db = FakeDB(coluna=OK, linhas=linhas)
    _mod()["up"](db)
    _mod()["up"](db)
    assert [r["carb_cycle_policy"] for r in db.linhas] == ["fat_compensation", None, None, "vary_kcal"]


def test_codigos_da_migracao_espelham_o_enum_do_dono():
    assert _mod()["VALID_POLICIES"] == tuple(p.value for p in CarbCyclePolicy)


@pytest.mark.parametrize(
    "coluna",
    [("int", "YES", None), ("varchar(32)", "YES", None), ("varchar(24)", "NO", None), ("varchar(24)", "YES", "vary_kcal")],
)
def test_coluna_divergente_falha_antes_do_primeiro_statement(coluna):
    db = FakeDB(coluna=coluna)
    with pytest.raises(Exception, match="123"):
        _mod()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_e_dry_run_lista():
    with pytest.raises(Exception, match="123"):
        _mod()["plan"](FakeDB(tabela=False))
    assert _mod()["plan"](FakeDB(tabela=False, dry_run=True)) == [(ADD, None), (_backfill(), CODIGOS)]


def test_down_solta_a_coluna_e_e_idempotente():
    db = FakeDB(coluna=OK)
    _mod()["down"](db)
    assert db.executed == [(DROP, None)]
    db2 = FakeDB()
    _mod()["down"](db2)
    assert db2.executed == []

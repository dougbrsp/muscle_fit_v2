"""Plano da migração 082 (`meals` + `meal_items`, nutrição L4).

- Aurora (recorte real somente leitura `aurora_schema_2026_09_17_035.json`, sem
  as tabelas) -> 2 `CREATE TABLE` (refeição antes do item, pela FK); o plano só
  lê `information_schema`.
- Convergido (sintético) -> 0; só os itens faltando -> 1.
- Forma divergente (tipo, nulabilidade, índice ausente ou fora de ordem,
  ENGINE) -> erro ANTES do primeiro statement.
- `users` ausente: erro numa execução real, os 2 CREATE em DRY-RUN.
- DDL: FK só para `users` e `meal_items -> meals` (CASCADE), nutrientes com os
  códigos do motor (`meal_math.NUTRIENT_FIELDS`) e NULL sem DEFAULT (nunca 0).

Rodar: cd api && python -m pytest tests/unit/test_migration_082_plano.py
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import pytest

from app import migrate as m
from app.domain.nutrition import catalog, diary, macros, meal_math
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "082_meals.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"
MEALS, ITEMS = "meals", "meal_items"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _sql(texto: str) -> str:
    return " ".join(texto.split())


def _indices_convergidos() -> dict:
    mod = _module()
    indices = {}
    for tabela, esperados in ((MEALS, mod["EXPECTED_MEAL_INDEXES"]), (ITEMS, mod["EXPECTED_ITEM_INDEXES"])):
        for nome, (unico, cols) in esperados.items():
            indices[(tabela, nome)] = (0 if unico else 1, cols)
    return indices


class FakeDB:
    def __init__(self, *, tabelas=(MEALS, ITEMS), users=True, colunas=None, indices=None, engine="InnoDB", dry_run=False):
        mod = _module()
        self.tabelas = set(tabelas)
        self.users = users
        self.colunas = colunas or {MEALS: dict(mod["EXPECTED_MEAL_COLUMNS"]), ITEMS: dict(mod["EXPECTED_ITEM_COLUMNS"])}
        self.indices = _indices_convergidos() if indices is None else indices
        self.engine = engine
        self.dry_run = dry_run
        self.queries: list[str] = []

    def query(self, sql, params=None):
        self.queries.append(sql)
        if "information_schema.TABLES" in sql:
            linhas = [{"TABLE_NAME": t, "ENGINE": self.engine} for t in sorted(self.tabelas)]
            if self.users:
                linhas.append({"TABLE_NAME": "users", "ENGINE": "InnoDB"})
            return linhas
        if "information_schema.COLUMNS" in sql:
            linhas = []
            if self.users:
                linhas.append({"TABLE_NAME": "users", "COLUMN_NAME": "id", "COLUMN_TYPE": "bigint(20)", "IS_NULLABLE": "NO",
                               "COLUMN_KEY": "PRI"})
            for tabela in sorted(self.tabelas):
                for nome, (tipo, nulo) in self.colunas[tabela].items():
                    linhas.append({"TABLE_NAME": tabela, "COLUMN_NAME": nome, "COLUMN_TYPE": tipo, "IS_NULLABLE": nulo,
                                   "COLUMN_KEY": ""})
            return linhas
        if "information_schema.STATISTICS" in sql:
            return [
                {"TABLE_NAME": tabela, "INDEX_NAME": nome, "SEQ_IN_INDEX": seq, "COLUMN_NAME": col, "NON_UNIQUE": nu}
                for (tabela, nome), (nu, cols) in self.indices.items()
                if tabela in self.tabelas
                for seq, col in enumerate(cols, 1)
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no plano: {sql}")


def test_aurora_hoje_cria_as_duas_tabelas_refeicao_antes_do_item():
    snap = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    nomes = {t["table_name"] for t in snap["tables"]}
    assert MEALS not in nomes and ITEMS not in nomes
    db = SafetySnapshotDB(snap)
    statements = _module()["plan"](db)
    assert [_sql(s) for s, _ in statements] == [_sql(_module()["CREATE_MEALS_SQL"]), _sql(_module()["CREATE_ITEMS_SQL"])]
    assert all(q.strip().upper().startswith("SELECT") and "information_schema" in q for q in db.queries)


def test_convergido_nao_faz_nada_e_parcial_cria_so_o_que_falta():
    assert _module()["plan"](FakeDB()) == []
    so_refeicoes = _module()["plan"](FakeDB(tabelas=(MEALS,)))
    assert [_sql(s) for s, _ in so_refeicoes] == [_sql(_module()["CREATE_ITEMS_SQL"])]


def _com(dicionario, chave, valor):
    return {**dicionario, chave: valor}


def _sem(dicionario, chave):
    return {k: v for k, v in dicionario.items() if k != chave}


def _colunas(**ajuste):
    mod = _module()
    base = {MEALS: dict(mod["EXPECTED_MEAL_COLUMNS"]), ITEMS: dict(mod["EXPECTED_ITEM_COLUMNS"])}
    base.update(ajuste)
    return base


@pytest.mark.parametrize(
    "ajuste",
    [
        {"colunas": _colunas(meals=_com(_module()["EXPECTED_MEAL_COLUMNS"], "energy_kcal", ("decimal(10,2)", "NO")))},
        {"colunas": _colunas(meals=_com(_module()["EXPECTED_MEAL_COLUMNS"], "eaten_at", ("varchar(5)", "YES")))},
        {"colunas": _colunas(meals=_sem(_module()["EXPECTED_MEAL_COLUMNS"], "totals_incomplete_json"))},
        {"colunas": _colunas(meal_items=_com(_module()["EXPECTED_ITEM_COLUMNS"], "amount", ("decimal(7,2)", "NO")))},
        {"colunas": _colunas(meal_items=_sem(_module()["EXPECTED_ITEM_COLUMNS"], "catalog_snapshot_json"))},
        {"indices": _sem(_indices_convergidos(), (MEALS, "idx_meals_user_date"))},
        {"indices": _com(_indices_convergidos(), (ITEMS, "idx_mi_meal"), (1, ("position", "meal_id")))},
        {"indices": _com(_indices_convergidos(), (ITEMS, "idx_mi_food"), (0, ("food_id",)))},
        {"engine": "MyISAM"},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(ajuste):
    with pytest.raises(_module()["MealsError"], match="Nenhum statement executado"):
        _module()["up"](FakeDB(**ajuste))


def test_users_ausente_falha_e_dry_run_planeja_os_dois_creates():
    with pytest.raises(_module()["MealsError"]):
        _module()["plan"](FakeDB(tabelas=(), users=False))
    assert len(_module()["plan"](FakeDB(tabelas=(), users=False, dry_run=True))) == 2


def test_plan_down_apaga_itens_antes_das_refeicoes():
    assert [s for s, _ in _module()["plan_down"](FakeDB())] == [_module()["DROP_ITEMS_SQL"], _module()["DROP_MEALS_SQL"]]
    assert _module()["plan_down"](FakeDB(tabelas=())) == []


def test_ddl_fk_so_para_users_e_meals_e_nutriente_null_sem_default():
    refeicoes = _sql(_module()["CREATE_MEALS_SQL"])
    itens = _sql(_module()["CREATE_ITEMS_SQL"])
    assert re.findall(r"REFERENCES (\w+)", refeicoes) == ["users"]
    assert re.findall(r"REFERENCES (\w+)", itens) == ["meals"]
    for sql in (refeicoes, itens):
        assert "ON DELETE CASCADE" in sql and "exercise_catalog" not in sql
        assert "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4" in sql
        assert ":" not in sql.split("COLLATE")[0], "literal com ':' viraria parâmetro no shim da Data API"
        for campo in meal_math.NUTRIENT_FIELDS:
            assert re.search(rf"{campo} DECIMAL\(\d+,2\) NULL,", sql), campo  # NULL = desconhecido, nunca 0
    assert "KEY idx_meals_user_date (user_id, local_date)" in refeicoes
    assert "KEY idx_mi_meal (meal_id, position)" in itens and "KEY idx_mi_food (food_id)" in itens
    assert "food_id BIGINT NULL," in itens  # referência lógica ao catálogo


@pytest.mark.parametrize(("sql", "chave"), [("CREATE_MEALS_SQL", "EXPECTED_MEAL_COLUMNS"), ("CREATE_ITEMS_SQL", "EXPECTED_ITEM_COLUMNS")])
def test_expected_columns_e_o_create_descrevem_as_mesmas_colunas(sql, chave):
    linhas = [linha.strip() for linha in _module()[sql].splitlines()]
    nomes = [linha.split()[0] for linha in linhas
             if re.match(r"^[a-z_0-9]+ ", linha) and not linha.startswith(("CREATE", "UNIQUE", "KEY", "CONSTRAINT"))]
    assert set(nomes) == set(_module()[chave])


def test_colunas_de_nutriente_sao_os_codigos_do_motor_e_cabem_no_teto_do_diario():
    mod = _module()
    assert tuple(mod["ITEM_NUTRIENT_TYPES"]) == meal_math.NUTRIENT_FIELDS
    assert tuple(mod["MEAL_NUTRIENT_TYPES"]) == meal_math.NUTRIENT_FIELDS

    def _maximo(tipo: str) -> float:
        digitos, casas = (int(x) for x in re.match(r"decimal\((\d+),(\d+)\)", tipo).groups())
        return 10 ** (digitos - casas) - 1

    # Pior item: `ITEM_AMOUNT_MAX` na base × o máximo por 100 que o catálogo aceita
    # (energia da gordura por grama e sódio em mg da massa máxima de 100 ml).
    massa = catalog.max_basis_mass_g(meal_math.Unit.MILLILITER)
    fator = diary.ITEM_AMOUNT_MAX / meal_math.PER_100G_REFERENCE
    piores = {
        "energy_kcal": massa * macros.KCAL_PER_G_FAT * fator,
        "protein_g": massa * fator,
        "sodium_mg": massa * catalog.MG_PER_G * fator,
    }
    for campo, pior in piores.items():
        assert pior <= _maximo(mod["ITEM_NUTRIENT_TYPES"][campo]), campo
        assert pior * diary.MEAL_MAX_ITEMS <= _maximo(mod["MEAL_NUTRIENT_TYPES"][campo]), campo
    assert diary.ITEM_AMOUNT_MAX <= _maximo("decimal(9,2)")

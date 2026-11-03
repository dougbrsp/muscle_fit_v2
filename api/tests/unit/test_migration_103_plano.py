"""Plano da migração 103 (`nutrition_checkins` e `nutrition_checkin_settings`, nutrição N7).

- Aurora (recorte real somente leitura, sem as tabelas) -> 2 `CREATE TABLE`, na ordem.
- Convergido -> 0; forma divergente -> erro antes do primeiro statement.
- DDL: UNIQUE por (usuário, semana), FK só para `users`, reversível.

Rodar: cd api && python -m pytest tests/unit/test_migration_103_plano.py
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "103_nutrition_checkins.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"
CHECKINS, SETTINGS = "nutrition_checkins", "nutrition_checkin_settings"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _sql(texto: str) -> str:
    return " ".join(texto.split())


def _indices_ok() -> dict:
    return {
        (tabela, nome): (0 if unico else 1, cols)
        for tabela, indices in _module()["EXPECTED_INDEXES"].items()
        for nome, (unico, cols) in indices.items()
    }


class FakeDB:
    def __init__(self, *, tabelas=(CHECKINS, SETTINGS), users=True, colunas=None, indices=None, dry_run=False):
        self.tabelas = tuple(tabelas)
        self.users = users
        self.colunas = {t: dict(c) for t, c in (_module()["EXPECTED_COLUMNS"] if colunas is None else colunas).items()}
        self.indices = _indices_ok() if indices is None else indices
        self.dry_run = dry_run

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t, "ENGINE": "InnoDB"} for t in self.tabelas] + (
                [{"TABLE_NAME": "users", "ENGINE": "InnoDB"}] if self.users else []
            )
        if "information_schema.COLUMNS" in sql:
            linhas = [{"TABLE_NAME": "users", "COLUMN_NAME": "id", "COLUMN_TYPE": "bigint", "IS_NULLABLE": "NO",
                       "COLUMN_KEY": "PRI"}] if self.users else []
            for t in self.tabelas:
                linhas += [{"TABLE_NAME": t, "COLUMN_NAME": n, "COLUMN_TYPE": tipo, "IS_NULLABLE": nu, "COLUMN_KEY": ""}
                           for n, (tipo, nu) in self.colunas[t].items()]
            return linhas
        if "information_schema.STATISTICS" in sql:
            return [
                {"TABLE_NAME": t, "INDEX_NAME": nome, "SEQ_IN_INDEX": seq, "COLUMN_NAME": col, "NON_UNIQUE": nu}
                for (t, nome), (nu, cols) in self.indices.items()
                if t in self.tabelas
                for seq, col in enumerate(cols, 1)
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no plano: {sql}")


def test_aurora_hoje_cria_as_duas_tabelas_com_um_statement_cada():
    snap = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    existentes = {t["table_name"] for t in snap["tables"]}
    assert CHECKINS not in existentes and SETTINGS not in existentes
    db = SafetySnapshotDB(snap)
    assert [(_sql(s), p) for s, p in _module()["plan"](db)] == [
        (_sql(_module()["CREATE_CHECKINS_SQL"]), None),
        (_sql(_module()["CREATE_SETTINGS_SQL"]), None),
    ]
    assert all("information_schema" in q for q in db.queries)


def test_convergido_nao_faz_nada_mesmo_com_largura_do_int():
    assert _module()["plan"](FakeDB()) == []
    largura = {**_module()["EXPECTED_COLUMNS"], SETTINGS: {**_module()["EXPECTED_COLUMNS"][SETTINGS], "weekday": ("tinyint(4)", "NO")}}
    assert _module()["plan"](FakeDB(colunas=largura)) == []


def test_so_a_tabela_que_falta_e_criada():
    db = FakeDB(tabelas=(CHECKINS,))
    assert [_sql(s) for s, _ in _module()["plan"](db)] == [_sql(_module()["CREATE_SETTINGS_SQL"])]


def _sem(tabela, coluna):
    return {t: {k: v for k, v in cols.items() if not (t == tabela and k == coluna)} for t, cols in _module()["EXPECTED_COLUMNS"].items()}


def _tipo(tabela, coluna, tipo, nulo="NO"):
    base = {t: dict(cols) for t, cols in _module()["EXPECTED_COLUMNS"].items()}
    base[tabela][coluna] = (tipo, nulo)
    return base


@pytest.mark.parametrize(
    "ajuste",
    [
        {"colunas": _tipo(CHECKINS, "adjustment_kcal", "decimal(6,2)", "YES")},
        {"colunas": _tipo(CHECKINS, "inputs_json", "json", "YES")},
        {"colunas": _tipo(SETTINGS, "weekday", "int")},
        {"colunas": _sem(CHECKINS, "verdict")},
        {"indices": {}},
        {"indices": {**_indices_ok(), (CHECKINS, "uq_nci_user_week"): (1, ("user_id", "week_start"))}},
        {"indices": {**_indices_ok(), (CHECKINS, "uq_nci_user_week"): (0, ("week_start", "user_id"))}},
        {"indices": {**_indices_ok(), (SETTINGS, "PRIMARY"): (1, ("user_id",))}},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(ajuste):
    with pytest.raises(_module()["NutritionCheckinsError"], match="Nenhum statement executado"):
        _module()["up"](FakeDB(**ajuste))


def test_users_ausente_e_down_reversivel():
    with pytest.raises(_module()["NutritionCheckinsError"]):
        _module()["plan"](FakeDB(tabelas=(), users=False))
    assert len(_module()["plan"](FakeDB(tabelas=(), users=False, dry_run=True))) == 2
    assert [s for s, _ in _module()["plan_down"](FakeDB())] == [_module()["DROP_SQL"][CHECKINS], _module()["DROP_SQL"][SETTINGS]]
    assert _module()["plan_down"](FakeDB(tabelas=())) == []


def test_ddl_unico_por_usuario_e_semana_fk_so_para_users():
    sql = _sql(_module()["CREATE_CHECKINS_SQL"])
    assert "CREATE TABLE IF NOT EXISTS nutrition_checkins" in sql
    assert "UNIQUE KEY uq_nci_user_week (user_id, week_start)" in sql
    assert re.findall(r"REFERENCES (\w+)", sql) == ["users"]
    assert "ON DELETE CASCADE" in sql and "exercise_catalog" not in sql
    sql_dia = _sql(_module()["CREATE_SETTINGS_SQL"])
    assert re.findall(r"REFERENCES (\w+)", sql_dia) == ["users"] and "user_id BIGINT NOT NULL PRIMARY KEY" in sql_dia


@pytest.mark.parametrize("tabela,create", [(CHECKINS, "CREATE_CHECKINS_SQL"), (SETTINGS, "CREATE_SETTINGS_SQL")])
def test_expected_columns_e_o_create_descrevem_as_mesmas_colunas(tabela, create):
    linhas = [linha.strip() for linha in _module()[create].splitlines()]
    nomes = [linha.split()[0] for linha in linhas
             if re.match(r"^[a-z_]+ ", linha) and not linha.startswith(("CREATE", "UNIQUE", "KEY", "CONSTRAINT"))]
    assert set(nomes) == set(_module()["EXPECTED_COLUMNS"][tabela])

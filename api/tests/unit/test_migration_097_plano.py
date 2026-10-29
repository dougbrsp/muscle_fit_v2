"""Plano da migração 097 (zonas de FC do Intervals e tempo em zona, M98 C2).

- Banco atual (sem tabela e sem coluna) -> CREATE + ALTER INSTANT + backfill
  (quando há `raw_json` com `icu_hr_zone_times`).
- Convergido -> 0 statements (idempotente); forma divergente -> erro antes do
  primeiro statement. Sem FK; nada de literal com ':'.
- `down` = DROP COLUMN + DROP TABLE, só do que existe.

Rodar: cd api && python -m pytest tests/unit/test_migration_097_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "097_intervals_zonas_fc.py"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _zones_columns():
    return {name: spec for name, spec in _mod()["EXPECTED_ZONES_COLUMNS"].items()}


class FakeDB:
    def __init__(self, *, tabela_zonas=False, tabela_atividades=True, coluna=None, pendentes=0, dry_run=False,
                 colunas_zonas=None, unico=None):
        self.tabela_zonas = tabela_zonas
        self.tabela_atividades = tabela_atividades
        self.coluna = coluna  # (COLUMN_TYPE, IS_NULLABLE) ou None
        self.pendentes = pendentes
        self.dry_run = dry_run
        self.colunas_zonas = colunas_zonas if colunas_zonas is not None else _zones_columns()
        self.unico = unico if unico is not None else (("user_id", 0), ("sport_settings_id", 0))
        self.contagens: list[str] = []

    def query(self, sql, params=None):
        mod = _mod()
        if "information_schema.TABLES" in sql:
            nomes = []
            if self.tabela_zonas:
                nomes.append(mod["ZONES_TABLE"])
            if self.tabela_atividades:
                nomes.append(mod["ACTIVITIES_TABLE"])
            return [{"TABLE_NAME": n} for n in nomes]
        if "information_schema.COLUMNS" in sql:
            linhas = []
            if self.tabela_zonas:
                for nome, (tipo, nulo) in self.colunas_zonas.items():
                    linhas.append({"TABLE_NAME": mod["ZONES_TABLE"], "COLUMN_NAME": nome, "COLUMN_TYPE": tipo, "IS_NULLABLE": nulo})
            if self.coluna is not None:
                tipo, nulo = self.coluna
                linhas.append({"TABLE_NAME": mod["ACTIVITIES_TABLE"], "COLUMN_NAME": mod["COLUMN"], "COLUMN_TYPE": tipo, "IS_NULLABLE": nulo})
            return linhas
        if "information_schema.STATISTICS" in sql:
            if not self.tabela_zonas:
                return []
            return [
                {"INDEX_NAME": mod["UQ_USER_SETTINGS"], "SEQ_IN_INDEX": i + 1, "COLUMN_NAME": c, "NON_UNIQUE": nu}
                for i, (c, nu) in enumerate(self.unico)
            ]
        if sql.startswith("SELECT COUNT(*)"):
            self.contagens.append(sql)
            return [{"n": self.pendentes}]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no plano: {sql}")


def _sqls(plano):
    return [sql for sql, _params in plano]


def test_banco_atual_cria_tabela_coluna_e_backfill():
    mod = _mod()
    plano = mod["plan"](FakeDB(pendentes=537))
    assert _sqls(plano) == [mod["CREATE_SQL"], mod["ADD_COLUMN_SQL"], mod["BACKFILL_SQL"]]
    assert "ALGORITHM=INSTANT" in mod["ADD_COLUMN_SQL"] and "JSON NULL" in mod["ADD_COLUMN_SQL"]
    assert all(params is None for _sql, params in plano)


def test_sem_linha_para_preencher_nao_ha_backfill():
    mod = _mod()
    assert _sqls(mod["plan"](FakeDB(pendentes=0))) == [mod["CREATE_SQL"], mod["ADD_COLUMN_SQL"]]


def test_convergido_zero_statements():
    db = FakeDB(tabela_zonas=True, coluna=("json", "YES"), pendentes=0)
    assert _mod()["plan"](db) == []
    # Com a coluna presente, a contagem só olha as linhas ainda NULL.
    assert "IS NULL" in db.contagens[0]


def test_coluna_presente_com_pendentes_so_reexecuta_o_backfill():
    mod = _mod()
    plano = mod["plan"](FakeDB(tabela_zonas=True, coluna=("json", "YES"), pendentes=3))
    assert _sqls(plano) == [mod["BACKFILL_SQL"]]
    assert "IS NULL" in mod["BACKFILL_SQL"]  # idempotente: só preenche o que falta


@pytest.mark.parametrize(
    "db",
    [
        FakeDB(tabela_zonas=True, coluna=("text", "YES")),
        FakeDB(tabela_zonas=True, coluna=("json", "NO")),
        FakeDB(tabela_zonas=True, coluna=("json", "YES"), colunas_zonas={**_zones_columns(), "max_hr_bpm": ("int", "YES")}),
        FakeDB(tabela_zonas=True, coluna=("json", "YES"), unico=(("user_id", 0),)),
        FakeDB(tabela_zonas=True, coluna=("json", "YES"), unico=(("user_id", 1), ("sport_settings_id", 1))),
    ],
)
def test_forma_divergente_falha_antes_de_qualquer_statement(db):
    with pytest.raises(_mod()["IntervalsZonasError"]):
        _mod()["plan"](db)


def test_tabela_de_atividades_ausente_so_em_dry_run():
    mod = _mod()
    with pytest.raises(mod["IntervalsZonasError"]):
        mod["plan"](FakeDB(tabela_atividades=False))
    plano = mod["plan"](FakeDB(tabela_atividades=False, dry_run=True))
    assert _sqls(plano) == [mod["CREATE_SQL"], mod["ADD_COLUMN_SQL"], mod["BACKFILL_SQL"]]


def test_sem_fk_e_sem_dois_pontos_em_literal():
    mod = _mod()
    for sql in (mod["CREATE_SQL"], mod["ADD_COLUMN_SQL"], mod["BACKFILL_SQL"], mod["DROP_COLUMN_SQL"], mod["DROP_TABLE_SQL"]):
        assert "FOREIGN KEY" not in sql.upper()
        assert "exercise_catalog" not in sql
        for literal in sql.split("'")[1::2]:
            assert ":" not in literal


def test_down_so_do_que_existe():
    mod = _mod()
    assert mod["plan_down"](FakeDB()) == []
    plano = mod["plan_down"](FakeDB(tabela_zonas=True, coluna=("json", "YES")))
    assert _sqls(plano) == [mod["DROP_COLUMN_SQL"], mod["DROP_TABLE_SQL"]]

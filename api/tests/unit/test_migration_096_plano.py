"""Plano da migração 096 (FCmáx manual no perfil, M98 C1).

- Banco atual (sem a coluna) -> 1 ALTER INSTANT, anulável, sem FK.
- Convergido -> 0 (idempotente); forma divergente -> erro antes do primeiro statement.
- O teto do dono (`hr_zones.MANUAL_MAX_HR_MAX_BPM`) cabe no SMALLINT.
- `down` = DROP COLUMN só se a coluna existir.

Rodar: cd api && python -m pytest tests/unit/test_migration_096_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m
from app.domain import hr_zones

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "096_fcmax_manual_perfil.py"
T = "users"
C = "max_hr_bpm_manual"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, *, tabela=True, coluna=None, dry_run=False):
        self.tabela = tabela
        self.coluna = coluna  # (COLUMN_TYPE, IS_NULLABLE) ou None
        self.dry_run = dry_run

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            assert params == (T,)
            return [{"TABLE_NAME": T}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            assert params == (T, C)
            if self.coluna is None:
                return []
            tipo, nulo = self.coluna
            return [{"COLUMN_NAME": C, "COLUMN_TYPE": tipo, "IS_NULLABLE": nulo}]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no plano: {sql}")


def test_teto_do_dono_cabe_no_smallint():
    assert _module()["COLUMN_TYPE"] == "smallint"
    assert hr_zones.MANUAL_MAX_HR_MAX_BPM <= 32767


def test_banco_atual_um_alter_instant_anulavel_sem_fk():
    plano = _module()["plan"](FakeDB())
    assert plano == [(f"ALTER TABLE {T} ADD COLUMN {C} SMALLINT NULL DEFAULT NULL, ALGORITHM=INSTANT", None)]
    sql = plano[0][0].upper()
    assert "FOREIGN KEY" not in sql and "REFERENCES" not in sql and "NOT NULL" not in sql


@pytest.mark.parametrize("tipo", ["smallint", "smallint(6)", "SMALLINT"])
def test_convergido_nao_faz_nada(tipo):
    assert _module()["plan"](FakeDB(coluna=(tipo, "YES"))) == []


@pytest.mark.parametrize("coluna", [("int", "YES"), ("tinyint", "YES"), ("smallint", "NO")])
def test_forma_divergente_falha_antes_do_primeiro_statement(coluna):
    with pytest.raises(_module()["FcmaxManualError"], match="Nenhum statement executado"):
        _module()["plan"](FakeDB(coluna=coluna))


def test_tabela_ausente_falha_e_dry_run_lista_o_plano():
    with pytest.raises(_module()["FcmaxManualError"], match="nao existe"):
        _module()["plan"](FakeDB(tabela=False))
    plano = _module()["plan"](FakeDB(tabela=False, dry_run=True))
    assert [p[0].split()[0] for p in plano] == ["ALTER"]


def test_down_solta_so_se_existir():
    mod = _module()
    assert mod["plan_down"](FakeDB()) == []
    assert mod["plan_down"](FakeDB(coluna=("smallint", "YES"))) == [(f"ALTER TABLE {T} DROP COLUMN {C}", None)]


def test_sem_literal_com_dois_pontos_para_a_data_api():
    mod = _module()
    for sql in (mod["ADD_SQL"], mod["DROP_SQL"]):
        assert ":" not in sql

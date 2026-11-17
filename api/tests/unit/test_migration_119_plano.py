"""Plano da migracao 119 (#188): `manufacturer` em gym_equipment_catalog.
Sem MySQL: FakeDB le um `information_schema` em memoria."""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "119_gym_equipment_catalog_manufacturer.py"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, *, tabela=True, coluna=None, dry_run=False):
        self.tabela = tabela
        self.coluna = coluna
        self.dry_run = dry_run
        self.executed: list[str] = []

    def query(self, sql, params=None):
        assert ":" not in sql
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": "gym_equipment_catalog"}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            assert params == ("gym_equipment_catalog", "manufacturer")
            if not self.tabela or self.coluna is None:
                return []
            tipo, nul = self.coluna
            return [{"TABLE_NAME": "gym_equipment_catalog", "COLUMN_TYPE": tipo, "IS_NULLABLE": nul}]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        assert params is None
        self.executed.append(sql)


def test_banco_atual_recebe_a_coluna():
    db = FakeDB()
    _mod()["up"](db)
    assert db.executed == ["ALTER TABLE gym_equipment_catalog ADD COLUMN manufacturer VARCHAR(100) NULL"]


def test_convergido_nao_faz_nada():
    db = FakeDB(coluna=("varchar(100)", "YES"))
    _mod()["up"](db)
    assert db.executed == []


@pytest.mark.parametrize("coluna", [("varchar(50)", "YES"), ("varchar(100)", "NO"), ("text", "YES")])
def test_coluna_divergente_falha_antes_do_primeiro_statement(coluna):
    db = FakeDB(coluna=coluna)
    with pytest.raises(Exception, match="119"):
        _mod()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_e_dry_run_lista():
    with pytest.raises(Exception, match="119"):
        _mod()["plan"](FakeDB(tabela=False))
    assert len(_mod()["plan"](FakeDB(tabela=False, dry_run=True))) == 1


def test_down_solta_a_coluna_e_e_idempotente():
    db = FakeDB(coluna=("varchar(100)", "YES"))
    _mod()["down"](db)
    assert db.executed == ["ALTER TABLE gym_equipment_catalog DROP COLUMN manufacturer"]
    db2 = FakeDB()
    _mod()["down"](db2)
    assert db2.executed == []


def test_mesmo_tipo_do_catalogo_e_ddl_sem_fk_sem_dml():
    g022 = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "022_equipment_types_parity.py").__globals__
    texto_022 = (m.DEFAULT_MIGRATIONS_DIR / "022_equipment_types_parity.py").read_text(encoding="utf-8")
    assert '("manufacturer", "VARCHAR(100) NULL")' in texto_022 and g022
    assert _mod()["COLUMN_DDL"] == "VARCHAR(100) NULL"
    codigo = MIGRATION.read_text(encoding="utf-8").split('"""', 2)[2]
    for proibido in ("FOREIGN KEY", "REFERENCES", "exercise_catalog", "UPDATE ", "INSERT ", "DELETE "):
        assert proibido not in codigo, proibido

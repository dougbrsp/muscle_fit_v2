"""Plano da migração 105 (`archived_at` em versões de Modelo e planos de nutrição).

- Coluna ausente -> 1 `ALTER TABLE ... ADD COLUMN archived_at DATETIME NULL` por tabela, sem `AFTER`, sem ALGORITHM.
- Convergido -> 0 statements; forma divergente -> erro antes do primeiro statement.
- Reversível (`down` solta as colunas), sem FK, sem `exercise_catalog`, só LEITURA no plano.

Rodar: cd api && python -m pytest tests/unit/test_migration_105_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "105_archived_at_versoes_e_planos.py"
VERSOES = "workout_plan_template_versions"
PLANOS = "nutrition_plans"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _sql(texto: str) -> str:
    return " ".join(texto.split())


class FakeDB:
    def __init__(self, *, tabelas=(VERSOES, PLANOS), colunas=None, dry_run=False):
        self.tabelas = tuple(tabelas)
        self.colunas = {} if colunas is None else dict(colunas)  # tabela -> (tipo, nulo)
        self.dry_run = dry_run
        self.executados: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t} for t in self.tabelas if t in params]
        if "information_schema.COLUMNS" in sql:
            return [
                {"TABLE_NAME": t, "COLUMN_NAME": "archived_at", "COLUMN_TYPE": tipo, "IS_NULLABLE": nulo}
                for t, (tipo, nulo) in self.colunas.items()
                if t in self.tabelas
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executados.append(sql)


CONVERGIDO = {VERSOES: ("datetime", "YES"), PLANOS: ("datetime", "YES")}


def test_colunas_ausentes_criam_um_statement_aditivo_por_tabela():
    db = FakeDB()
    plano = _module()["plan"](db)
    assert [(_sql(s), p) for s, p in plano] == [
        (f"ALTER TABLE {VERSOES} ADD COLUMN archived_at DATETIME NULL", None),
        (f"ALTER TABLE {PLANOS} ADD COLUMN archived_at DATETIME NULL", None),
    ]
    assert db.executados == []  # o plano so le


def test_so_a_tabela_sem_coluna_recebe_o_alter():
    plano = _module()["plan"](FakeDB(colunas={VERSOES: ("datetime", "YES")}))
    assert [_sql(s) for s, _ in plano] == [f"ALTER TABLE {PLANOS} ADD COLUMN archived_at DATETIME NULL"]


def test_up_executa_o_plano_e_e_idempotente_em_banco_convergido():
    ausente = FakeDB()
    _module()["up"](ausente)
    assert len(ausente.executados) == 2
    convergido = FakeDB(colunas=CONVERGIDO)
    _module()["up"](convergido)
    assert convergido.executados == []


@pytest.mark.parametrize("coluna", [("timestamp", "YES"), ("datetime", "NO"), ("date", "YES")])
def test_forma_divergente_falha_antes_do_primeiro_statement(coluna):
    db = FakeDB(colunas={PLANOS: coluna})
    with pytest.raises(_module()["ArchivedAtError"], match="Nenhum statement executado"):
        _module()["up"](db)
    assert db.executados == []


def test_tabela_ausente_e_erro_na_execucao_real_e_aceita_no_dry_run():
    with pytest.raises(_module()["ArchivedAtError"]):
        _module()["plan"](FakeDB(tabelas=(VERSOES,)))
    assert len(_module()["plan"](FakeDB(tabelas=(), dry_run=True))) == 2


def test_down_reversivel_e_idempotente():
    assert [_sql(s) for s, _ in _module()["plan_down"](FakeDB(colunas=CONVERGIDO))] == [
        f"ALTER TABLE {VERSOES} DROP COLUMN archived_at",
        f"ALTER TABLE {PLANOS} DROP COLUMN archived_at",
    ]
    assert _module()["plan_down"](FakeDB()) == []
    assert _module()["plan_down"](FakeDB(tabelas=())) == []
    db = FakeDB(colunas=CONVERGIDO)
    _module()["down"](db)
    assert len(db.executados) == 2


def test_ddl_sem_fk_sem_exercise_catalog_sem_algoritmo_e_um_statement_por_chamada():
    for tabela in (VERSOES, PLANOS):
        for sql in (_module()["_add_sql"](tabela), _module()["_drop_sql"](tabela)):
            assert "exercise_catalog" not in sql and "REFERENCES" not in sql and ";" not in sql
            assert ":" not in sql  # a Data API leria como parametro nomeado
            assert "ALGORITHM" not in sql and "INSTANT" not in sql and "AFTER" not in sql

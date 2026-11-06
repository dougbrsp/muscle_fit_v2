"""Plano da migração 107 (`intervals_sync_runs`, Lote F da nutrição adaptativa, E1).

- Sem a tabela -> 1 `CREATE TABLE`; convergido -> 0; forma divergente -> erro antes do primeiro statement.
- DDL: FK só para `users` (ON DELETE CASCADE), índice (user_id, finished_at), reversível.

Rodar: cd api && python -m pytest tests/unit/test_migration_107_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "107_intervals_sync_runs.py"
TABLE = "intervals_sync_runs"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, *, tabela=True, users=True, colunas=None, indice=None, dry_run=False, engine="InnoDB"):
        self.tabela = tabela
        self.users = users
        self.colunas = dict(_module()["EXPECTED_COLUMNS"] if colunas is None else colunas)
        self.indice = _module()["EXPECTED_INDEX"] if indice is None else indice
        self.dry_run = dry_run
        self.engine = engine
        self.executados: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return ([{"TABLE_NAME": TABLE, "ENGINE": self.engine}] if self.tabela else []) + (
                [{"TABLE_NAME": "users", "ENGINE": "InnoDB"}] if self.users else []
            )
        if "information_schema.COLUMNS" in sql:
            linhas = (
                [{"TABLE_NAME": "users", "COLUMN_NAME": "id", "COLUMN_TYPE": "bigint", "IS_NULLABLE": "NO", "COLUMN_KEY": "PRI"}]
                if self.users
                else []
            )
            if self.tabela:
                linhas += [
                    {"TABLE_NAME": TABLE, "COLUMN_NAME": n, "COLUMN_TYPE": t, "IS_NULLABLE": nu, "COLUMN_KEY": ""}
                    for n, (t, nu) in self.colunas.items()
                ]
            return linhas
        if "information_schema.STATISTICS" in sql:
            if not self.tabela:
                return []
            nome, cols = self.indice
            return [{"INDEX_NAME": nome, "SEQ_IN_INDEX": i, "COLUMN_NAME": c} for i, c in enumerate(cols, 1)]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executados.append(sql)


def test_sem_a_tabela_cria_uma_vez():
    plano = _module()["plan"](FakeDB(tabela=False))
    assert len(plano) == 1
    sql = " ".join(plano[0][0].split())
    assert sql.startswith("CREATE TABLE IF NOT EXISTS intervals_sync_runs")
    assert "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE" in sql
    assert "KEY idx_isr_user_finished (user_id, finished_at)" in sql
    assert "exercise_catalog" not in sql


def test_convergido_nao_faz_nada():
    assert _module()["plan"](FakeDB()) == []


def test_tipo_divergente_falha_antes_de_qualquer_statement():
    colunas = dict(_module()["EXPECTED_COLUMNS"], status=("varchar(8)", "NO"))
    db = FakeDB(colunas=colunas)
    with pytest.raises(Exception, match="107"):
        _module()["up"](db)
    assert db.executados == []


def test_indice_divergente_falha():
    with pytest.raises(Exception, match="107"):
        _module()["plan"](FakeDB(indice=("idx_isr_user_finished", ("user_id",))))


def test_users_ausente_falha_e_em_dry_run_planeja():
    with pytest.raises(Exception, match="107"):
        _module()["plan"](FakeDB(tabela=False, users=False))
    assert len(_module()["plan"](FakeDB(tabela=False, users=False, dry_run=True))) == 1


def test_down_solta_so_se_existe():
    assert [" ".join(s.split()) for s, _ in _module()["plan_down"](FakeDB())] == ["DROP TABLE IF EXISTS intervals_sync_runs"]
    assert _module()["plan_down"](FakeDB(tabela=False)) == []


def test_up_executa_o_plano():
    db = FakeDB(tabela=False)
    _module()["up"](db)
    assert len(db.executados) == 1

"""Plano da migração 108 (`nutrition_day_closures` e `activity_energy_corrections`, Lote G da nutrição adaptativa).

- Sem as tabelas -> 2 `CREATE TABLE` (uma por tabela ausente); convergido -> 0; forma divergente -> erro antes do 1º statement.
- DDL: FK só para `users` (ON DELETE CASCADE), PRIMARY KEY (user_id, day) e (user_id, activity_id), sem ENUM/CHECK, reversível.

Rodar: cd api && python -m pytest tests/unit/test_migration_108_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "108_nutricao_fechamento_dia_correcao_atividades.py"
CLOSURES = "nutrition_day_closures"
CORRECTIONS = "activity_energy_corrections"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, *, existentes=(CLOSURES, CORRECTIONS), users=True, colunas=None, pks=None, dry_run=False,
                 engine="InnoDB"):
        self.existentes = tuple(existentes)
        self.users = users
        self.colunas = {t: dict(c) for t, c in (_module()["EXPECTED_COLUMNS"] if colunas is None else colunas).items()}
        self.pks = dict(_module()["EXPECTED_PK"] if pks is None else pks)
        self.dry_run = dry_run
        self.engine = engine
        self.executados: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t, "ENGINE": self.engine} for t in self.existentes] + (
                [{"TABLE_NAME": "users", "ENGINE": "InnoDB"}] if self.users else []
            )
        if "information_schema.COLUMNS" in sql:
            linhas = (
                [{"TABLE_NAME": "users", "COLUMN_NAME": "id", "COLUMN_TYPE": "bigint", "IS_NULLABLE": "NO", "COLUMN_KEY": "PRI"}]
                if self.users
                else []
            )
            for tabela in self.existentes:
                linhas += [
                    {"TABLE_NAME": tabela, "COLUMN_NAME": n, "COLUMN_TYPE": t, "IS_NULLABLE": nu, "COLUMN_KEY": ""}
                    for n, (t, nu) in self.colunas[tabela].items()
                ]
            return linhas
        if "information_schema.STATISTICS" in sql:
            return [
                {"TABLE_NAME": tabela, "SEQ_IN_INDEX": i, "COLUMN_NAME": c}
                for tabela in self.existentes
                for i, c in enumerate(self.pks[tabela], 1)
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executados.append(sql)


def _sql(plano) -> list[str]:
    return [" ".join(s.split()) for s, _ in plano]


def test_sem_as_tabelas_cria_as_duas_uma_vez_cada():
    plano = _sql(_module()["plan"](FakeDB(existentes=())))
    assert len(plano) == 2
    assert plano[0].startswith(f"CREATE TABLE IF NOT EXISTS {CLOSURES}")
    assert plano[1].startswith(f"CREATE TABLE IF NOT EXISTS {CORRECTIONS}")
    for sql in plano:
        assert "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE" in sql
        assert "exercise_catalog" not in sql and "ENUM" not in sql.upper() and "CHECK" not in sql.upper()
    assert "PRIMARY KEY (user_id, day)" in plano[0] and "closed_at DATETIME NOT NULL" in plano[0]
    assert "PRIMARY KEY (user_id, activity_id)" in plano[1] and "energy_kind VARCHAR(16) NULL" in plano[1]


def test_so_cria_a_que_falta():
    plano = _sql(_module()["plan"](FakeDB(existentes=(CLOSURES,))))
    assert len(plano) == 1 and plano[0].startswith(f"CREATE TABLE IF NOT EXISTS {CORRECTIONS}")


def test_convergido_nao_faz_nada():
    assert _module()["plan"](FakeDB()) == []


def test_tipo_divergente_falha_antes_de_qualquer_statement():
    colunas = {t: dict(c) for t, c in _module()["EXPECTED_COLUMNS"].items()}
    colunas[CORRECTIONS]["activity_id"] = ("varchar(32)", "NO")
    db = FakeDB(existentes=(CORRECTIONS,), colunas=colunas)
    with pytest.raises(Exception, match="108"):
        _module()["up"](db)
    assert db.executados == []


def test_nulabilidade_divergente_falha():
    colunas = {t: dict(c) for t, c in _module()["EXPECTED_COLUMNS"].items()}
    colunas[CORRECTIONS]["energy_kind"] = ("varchar(16)", "NO")
    with pytest.raises(Exception, match="108"):
        _module()["plan"](FakeDB(colunas=colunas))


def test_chave_primaria_divergente_falha():
    pks = dict(_module()["EXPECTED_PK"], **{CLOSURES: ("user_id",)})
    with pytest.raises(Exception, match="108"):
        _module()["plan"](FakeDB(pks=pks))


def test_users_ausente_falha_e_em_dry_run_planeja():
    with pytest.raises(Exception, match="108"):
        _module()["plan"](FakeDB(existentes=(), users=False))
    assert len(_module()["plan"](FakeDB(existentes=(), users=False, dry_run=True))) == 2


def test_down_solta_so_o_que_existe():
    assert _sql(_module()["plan_down"](FakeDB())) == [f"DROP TABLE IF EXISTS {CORRECTIONS}", f"DROP TABLE IF EXISTS {CLOSURES}"]
    assert _sql(_module()["plan_down"](FakeDB(existentes=(CLOSURES,)))) == [f"DROP TABLE IF EXISTS {CLOSURES}"]
    assert _module()["plan_down"](FakeDB(existentes=())) == []


def test_up_executa_o_plano():
    db = FakeDB(existentes=())
    _module()["up"](db)
    assert len(db.executados) == 2

"""Plano da migração 106 (colunas do modo de gasto adaptativo em `nutrition_plans`).

- Coluna ausente -> 1 `ALTER TABLE ... ADD COLUMN` por coluna, sem `AFTER`, sem ALGORITHM, um statement por chamada.
- Convergido -> 0 statements; forma divergente -> erro antes do primeiro statement.
- Reversível (`down` solta as colunas que existem), sem FK, sem `exercise_catalog`, só LEITURA no plano.

Rodar: cd api && python -m pytest tests/unit/test_migration_106_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "106_nutrition_plans_energia_adaptativa.py"
PLANOS = "nutrition_plans"
CONVERGIDO = {
    "energy_params_json": ("json", "YES"),
    "energy_model_version": ("varchar(32)", "YES"),
    "published_preview_json": ("json", "YES"),
}


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _sql(texto: str) -> str:
    return " ".join(texto.split())


class FakeDB:
    def __init__(self, *, tabelas=(PLANOS,), colunas=None, dry_run=False):
        self.tabelas = tuple(tabelas)
        self.colunas = {} if colunas is None else dict(colunas)  # coluna -> (tipo, nulo)
        self.dry_run = dry_run
        self.executados: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t} for t in self.tabelas if t in params]
        if "information_schema.COLUMNS" in sql:
            return [
                {"COLUMN_NAME": c, "COLUMN_TYPE": tipo, "IS_NULLABLE": nulo}
                for c, (tipo, nulo) in self.colunas.items()
                if c in params and PLANOS in self.tabelas
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executados.append(sql)


def test_colunas_ausentes_criam_um_statement_aditivo_por_coluna():
    db = FakeDB()
    plano = _module()["plan"](db)
    assert [(_sql(s), p) for s, p in plano] == [
        (f"ALTER TABLE {PLANOS} ADD COLUMN energy_params_json JSON NULL", None),
        (f"ALTER TABLE {PLANOS} ADD COLUMN energy_model_version VARCHAR(32) NULL", None),
        (f"ALTER TABLE {PLANOS} ADD COLUMN published_preview_json JSON NULL", None),
    ]
    assert db.executados == []  # o plano so le


def test_so_a_coluna_ausente_recebe_o_alter():
    parcial = {k: v for k, v in CONVERGIDO.items() if k != "energy_model_version"}
    plano = _module()["plan"](FakeDB(colunas=parcial))
    assert [_sql(s) for s, _ in plano] == [f"ALTER TABLE {PLANOS} ADD COLUMN energy_model_version VARCHAR(32) NULL"]


def test_up_executa_o_plano_e_e_idempotente_em_banco_convergido():
    ausente = FakeDB()
    _module()["up"](ausente)
    assert len(ausente.executados) == 3
    convergido = FakeDB(colunas=CONVERGIDO)
    _module()["up"](convergido)
    assert convergido.executados == []


@pytest.mark.parametrize(
    "coluna",
    [
        {"energy_params_json": ("text", "YES")},
        {"energy_model_version": ("varchar(64)", "YES")},
        {"published_preview_json": ("json", "NO")},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(coluna):
    db = FakeDB(colunas=coluna)
    with pytest.raises(_module()["EnergiaAdaptativaError"], match="Nenhum statement executado"):
        _module()["up"](db)
    assert db.executados == []


def test_tabela_ausente_e_erro_na_execucao_real_e_aceita_no_dry_run():
    with pytest.raises(_module()["EnergiaAdaptativaError"]):
        _module()["plan"](FakeDB(tabelas=()))
    assert len(_module()["plan"](FakeDB(tabelas=(), dry_run=True))) == 3


def test_down_reversivel_e_idempotente():
    assert [_sql(s) for s, _ in _module()["plan_down"](FakeDB(colunas=CONVERGIDO))] == [
        f"ALTER TABLE {PLANOS} DROP COLUMN published_preview_json",
        f"ALTER TABLE {PLANOS} DROP COLUMN energy_model_version",
        f"ALTER TABLE {PLANOS} DROP COLUMN energy_params_json",
    ]
    assert _module()["plan_down"](FakeDB()) == []
    assert _module()["plan_down"](FakeDB(tabelas=())) == []
    db = FakeDB(colunas=CONVERGIDO)
    _module()["down"](db)
    assert len(db.executados) == 3


def test_ddl_sem_fk_sem_exercise_catalog_sem_algoritmo_e_um_statement_por_chamada():
    for nome, ddl, _tipo in _module()["COLUMNS"]:
        for sql in (_module()["_add_sql"](nome, ddl), _module()["_drop_sql"](nome)):
            assert "exercise_catalog" not in sql and "REFERENCES" not in sql and ";" not in sql
            assert ":" not in sql  # a Data API leria como parametro nomeado
            assert "ALGORITHM" not in sql and "INSTANT" not in sql and "AFTER" not in sql and "DEFAULT" not in sql

"""Plano da migração 098 (recomendação de cardio estruturada, M98 C3a).

- Banco atual (sem as colunas) -> 1 ALTER INSTANT com as duas, anuláveis, sem FK.
- Convergido -> 0 (idempotente); parcial -> só a que falta; forma divergente -> erro
  antes do primeiro statement.
- `down` = DROP COLUMN só das que existirem.

Rodar: cd api && python -m pytest tests/unit/test_migration_098_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m
from app.cardio_agent_ai import PROMPT_VERSION

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "098_recomendacao_cardio_estruturada.py"
T = "cardio_recommendations"
LEGADAS = ("id", "user_id", "reference_date", "recommendation_markdown", "context_snapshot")


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, *, tabela=True, colunas=None, dry_run=False):
        self.tabela = tabela
        self.colunas = colunas or {}  # nome -> (COLUMN_TYPE, IS_NULLABLE)
        self.dry_run = dry_run

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            assert params == (T,)
            return [{"TABLE_NAME": T}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            assert params == (T,)
            linhas = [{"COLUMN_NAME": c, "COLUMN_TYPE": "int", "IS_NULLABLE": "NO"} for c in LEGADAS]
            linhas += [{"COLUMN_NAME": c, "COLUMN_TYPE": t, "IS_NULLABLE": n} for c, (t, n) in self.colunas.items()]
            return linhas
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no plano: {sql}")


def test_banco_atual_um_alter_instant_anulavel_sem_fk():
    plano = _module()["plan"](FakeDB())
    assert plano == [
        (
            f"ALTER TABLE {T} ADD COLUMN recommendation_json JSON NULL DEFAULT NULL, "
            "ADD COLUMN prompt_version VARCHAR(40) NULL DEFAULT NULL, ALGORITHM=INSTANT",
            None,
        )
    ]
    sql = plano[0][0].upper()
    assert "FOREIGN KEY" not in sql and "REFERENCES" not in sql and "NOT NULL" not in sql
    assert "EXERCISE_CATALOG" not in sql


def test_convergido_nao_faz_nada():
    colunas = {"recommendation_json": ("json", "YES"), "prompt_version": ("varchar(40)", "YES")}
    assert _module()["plan"](FakeDB(colunas=colunas)) == []


def test_parcial_adiciona_so_a_que_falta():
    plano = _module()["plan"](FakeDB(colunas={"recommendation_json": ("json", "YES")}))
    assert plano == [(f"ALTER TABLE {T} ADD COLUMN prompt_version VARCHAR(40) NULL DEFAULT NULL, ALGORITHM=INSTANT", None)]


@pytest.mark.parametrize(
    "colunas",
    [{"recommendation_json": ("longtext", "YES")}, {"prompt_version": ("varchar(20)", "YES")}, {"recommendation_json": ("json", "NO")}],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(colunas):
    with pytest.raises(_module()["RecomendacaoCardioError"], match="Nenhum statement executado"):
        _module()["plan"](FakeDB(colunas=colunas))


def test_tabela_ausente_falha_e_dry_run_lista_o_plano():
    with pytest.raises(_module()["RecomendacaoCardioError"], match="nao existe"):
        _module()["plan"](FakeDB(tabela=False))
    plano = _module()["plan"](FakeDB(tabela=False, dry_run=True))
    assert [p[0].split()[0] for p in plano] == ["ALTER"]


def test_down_solta_so_o_que_existir():
    mod = _module()
    assert mod["plan_down"](FakeDB()) == []
    plano = mod["plan_down"](FakeDB(colunas={"prompt_version": ("varchar(40)", "YES")}))
    assert plano == [(f"ALTER TABLE {T} DROP COLUMN prompt_version", None)]


def test_versao_do_prompt_cabe_na_coluna_e_sem_dois_pontos_para_a_data_api():
    mod = _module()
    assert len(PROMPT_VERSION) <= 40
    for sql in (mod["add_sql"](["recommendation_json", "prompt_version"]), mod["drop_sql"](["prompt_version"])):
        assert ":" not in sql

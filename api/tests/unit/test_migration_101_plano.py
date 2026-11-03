"""Plano da migração 101 (prioridade de macros e gramas manuais do plano, N2) contra um
`information_schema` sintético:

- banco atual -> 2 statements (um ADD COLUMN INSTANT por coluna, na ordem);
- convergido -> 0 (idempotente); meio caminho -> só o que falta;
- tipo/nulabilidade/default divergente -> erro antes do 1º statement;
- tabela ausente -> erro (DRY-RUN aceita); `plan_down` inverso e idempotente;
- DDL: nenhuma FK, nenhum backfill (plano antigo = `balanced` e sem gramas), nenhum ':' em literal (Data API);
- o default da coluna é a prioridade padrão do dono `strategy.DEFAULT_MACRO_PRIORITY`.

Rodar: cd api && python -m pytest tests/unit/test_migration_101_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m
from app.domain.nutrition import strategy

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "101_plano_autogerido.py"
TABELAS = ("nutrition_plans",)
ADD_PRIORITY = (
    "ALTER TABLE nutrition_plans ADD COLUMN macro_priority VARCHAR(16) NOT NULL DEFAULT 'balanced', ALGORITHM=INSTANT"
)
ADD_MANUAL = "ALTER TABLE nutrition_plans ADD COLUMN manual_targets_json JSON NULL, ALGORITHM=INSTANT"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _alvos():
    return _mod()["TARGETS"]


class FakeDB:
    def __init__(self, *, tabelas=TABELAS, colunas=None, dry_run=False):
        self.tabelas = set(tabelas)
        self.colunas = {} if colunas is None else colunas  # (tabela, coluna) -> (tipo, nullable, default)
        self.dry_run = dry_run
        self.executed: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            assert set(params) == set(TABELAS)
            return [{"TABLE_NAME": t} for t in TABELAS if t in self.tabelas]
        if "information_schema.COLUMNS" in sql:
            return [
                {"TABLE_NAME": t, "COLUMN_NAME": c, "COLUMN_TYPE": tipo, "IS_NULLABLE": nul, "COLUMN_DEFAULT": default}
                for (t, c), (tipo, nul, default) in self.colunas.items()
                if c in params[len(TABELAS) :]
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executed.append(sql)


def _convergido(**kw):
    return FakeDB(colunas={(t, c): (tipo, nul, default) for t, c, _ddl, tipo, nul, default in _alvos()}, **kw)


def test_banco_atual_recebe_2_statements_na_ordem():
    db = FakeDB()
    _mod()["up"](db)
    assert db.executed == [ADD_PRIORITY, ADD_MANUAL]


def test_convergido_nao_faz_nada_e_reexecutar_e_idempotente():
    assert _mod()["plan"](_convergido()) == []
    db = _convergido()
    _mod()["up"](db)
    _mod()["up"](db)
    assert db.executed == []
    maiusculo = _convergido()
    maiusculo.colunas = {k: (tipo.upper(), nul.lower(), default) for k, (tipo, nul, default) in maiusculo.colunas.items()}
    assert _mod()["plan"](maiusculo) == []


def test_meio_caminho_aplica_so_o_que_falta():
    db = _convergido()
    del db.colunas[("nutrition_plans", "manual_targets_json")]
    assert [s for s, _ in _mod()["plan"](db)] == [ADD_MANUAL]


@pytest.mark.parametrize(
    "coluna, forma",
    [
        ("macro_priority", ("varchar(32)", "NO", "balanced")),
        ("macro_priority", ("varchar(16)", "YES", "balanced")),
        ("macro_priority", ("varchar(16)", "NO", "high_carb")),
        ("manual_targets_json", ("text", "YES", None)),
        ("manual_targets_json", ("json", "NO", None)),
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(coluna, forma):
    db = FakeDB(colunas={("nutrition_plans", coluna): forma})
    with pytest.raises(_mod()["PlanoAutogeridoError"], match="Nenhum statement executado"):
        _mod()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_na_execucao_real_e_passa_no_dry_run():
    with pytest.raises(_mod()["PlanoAutogeridoError"], match="nutrition_plans nao existe"):
        _mod()["plan"](FakeDB(tabelas=()))
    assert len(_mod()["plan"](FakeDB(tabelas=(), dry_run=True))) == 2


def test_plan_down_inverso_e_idempotente():
    assert [s for s, _ in _mod()["plan_down"](_convergido())] == [
        "ALTER TABLE nutrition_plans DROP COLUMN manual_targets_json",
        "ALTER TABLE nutrition_plans DROP COLUMN macro_priority",
    ]
    assert _mod()["plan_down"](FakeDB()) == []
    assert _mod()["plan_down"](FakeDB(tabelas=())) == []


def test_ddl_sem_fk_sem_backfill_e_sem_dois_pontos_em_literal():
    fonte = MIGRATION.read_text(encoding="utf-8")
    statements = [s for s, _ in _mod()["plan"](FakeDB())] + [s for s, _ in _mod()["plan_down"](_convergido())]
    for sql in statements:
        assert "FOREIGN KEY" not in sql.upper() and "REFERENCES" not in sql.upper()
        assert ":" not in sql
        assert sql.count(";") == 0
    codigo = fonte.split('"""', 2)[2].upper()
    for proibido in ("UPDATE ", "INSERT ", "DELETE ", "PREPARE ", "SET @", "FROM APP", "IMPORT APP"):
        assert proibido not in codigo


def test_default_da_coluna_e_a_prioridade_padrao_do_motor():
    """A migração é autocontida; o dono nomeia o padrão. Os dois concordam (plano antigo = comportamento anterior)."""
    (_t, coluna, ddl, _tipo, _nul, default), _manual = _alvos()
    assert coluna == "macro_priority" and default == strategy.DEFAULT_MACRO_PRIORITY.value
    assert f"DEFAULT '{strategy.DEFAULT_MACRO_PRIORITY.value}'" in ddl

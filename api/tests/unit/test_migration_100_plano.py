"""Plano da migração 100 (reps e RPE por lado, M102) contra um
`information_schema` sintético:

- banco atual -> 4 statements (um ADD COLUMN INSTANT por coluna, na ordem);
- convergido -> 0; meio caminho -> só o que falta;
- tipo/nulabilidade/default divergente -> erro antes do 1º statement;
- tabela ausente -> erro (DRY-RUN aceita); `plan_down` inverso;
- DDL: nenhuma FK, nenhum backfill, nenhum ':' em literal (Data API);
- as colunas são as do dono `domain/set_sides.SIDE_FIELDS`.

Rodar: cd api && python -m pytest tests/unit/test_migration_100_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m
from app.domain import set_sides

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "100_series_por_lado.py"
TABELAS = ("exercise_sets",)


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


def test_banco_atual_recebe_4_statements_na_ordem():
    db = FakeDB()
    _mod()["up"](db)
    assert db.executed == [
        "ALTER TABLE exercise_sets ADD COLUMN reps_left INT NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE exercise_sets ADD COLUMN reps_right INT NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE exercise_sets ADD COLUMN rpe_left DECIMAL(4,2) NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE exercise_sets ADD COLUMN rpe_right DECIMAL(4,2) NULL DEFAULT NULL, ALGORITHM=INSTANT",
    ]


def test_convergido_nao_faz_nada():
    assert _mod()["plan"](_convergido()) == []
    antigo = _convergido()
    antigo.colunas = {
        k: ("int(11)" if tipo == "int" else tipo.upper(), nul.lower(), default) for k, (tipo, nul, default) in antigo.colunas.items()
    }
    assert _mod()["plan"](antigo) == []


def test_meio_caminho_aplica_so_o_que_falta():
    db = _convergido()
    del db.colunas[("exercise_sets", "rpe_right")]
    assert [s for s, _ in _mod()["plan"](db)] == [
        "ALTER TABLE exercise_sets ADD COLUMN rpe_right DECIMAL(4,2) NULL DEFAULT NULL, ALGORITHM=INSTANT",
    ]


@pytest.mark.parametrize(
    "coluna, forma",
    [
        ("reps_left", ("bigint", "YES", None)),
        ("reps_right", ("int", "NO", None)),
        ("rpe_left", ("decimal(5,2)", "YES", None)),
        ("rpe_right", ("decimal(4,2)", "YES", "0.00")),
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(coluna, forma):
    db = FakeDB(colunas={("exercise_sets", coluna): forma})
    with pytest.raises(_mod()["SeriesPorLadoError"], match="Nenhum statement executado"):
        _mod()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_na_execucao_real_e_passa_no_dry_run():
    with pytest.raises(_mod()["SeriesPorLadoError"], match="exercise_sets nao existe"):
        _mod()["plan"](FakeDB(tabelas=()))
    assert len(_mod()["plan"](FakeDB(tabelas=(), dry_run=True))) == 4


def test_plan_down_inverso_e_idempotente():
    assert [s for s, _ in _mod()["plan_down"](_convergido())] == [
        "ALTER TABLE exercise_sets DROP COLUMN rpe_right",
        "ALTER TABLE exercise_sets DROP COLUMN rpe_left",
        "ALTER TABLE exercise_sets DROP COLUMN reps_right",
        "ALTER TABLE exercise_sets DROP COLUMN reps_left",
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


def test_colunas_batem_com_o_dono():
    """A migração é autocontida; o dono nomeia os campos. Os dois concordam."""
    assert tuple(c for _t, c, *_r in _alvos()) == set_sides.SIDE_FIELDS

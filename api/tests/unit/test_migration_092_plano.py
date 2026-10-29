"""Plano da migração 092 (Modo Intensivo: `reps_pending`, `completed_at`,
`planned_weight_kg`, `load_adjust_source`, `rpe_target_source` em
`exercise_sets`) contra um `information_schema` sintético:

- banco atual (reps já NULL) -> 5 statements (um ADD COLUMN INSTANT por coluna);
- convergido -> 0; meio caminho -> só o que falta;
- `reps` NOT NULL (banco divergente) -> MODIFY antes das colunas;
- tipo/nulabilidade/default divergente -> erro antes do 1º statement;
- tabela ausente -> erro (DRY-RUN aceita); `plan_down` inverso, sem mexer em `reps`;
- DDL: nenhuma FK, nenhum backfill, nenhum ':' em literal (Data API);
- o vocabulário das origens cabe no VARCHAR.

Rodar: cd api && python -m pytest tests/unit/test_migration_092_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "092_series_modo_intensivo.py"
REPS_OK = ("int", "YES", None)


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _forma_ok(coluna):
    for c, _ddl, tipo, nul, default in _mod()["TARGETS"]:
        if c == coluna:
            return (tipo, nul, default)
    raise AssertionError(coluna)


class FakeDB:
    def __init__(self, *, existe=True, colunas=None, dry_run=False):
        self.existe = existe
        self.colunas = {"reps": REPS_OK} if colunas is None else colunas  # coluna -> (tipo, nullable, default)
        self.dry_run = dry_run
        self.executed: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            assert params == ("exercise_sets",)
            return [{"TABLE_NAME": "exercise_sets"}] if self.existe else []
        if "information_schema.COLUMNS" in sql:
            assert params[0] == "exercise_sets"
            return [
                {"COLUMN_NAME": c, "COLUMN_TYPE": tipo, "IS_NULLABLE": nul, "COLUMN_DEFAULT": default}
                for c, (tipo, nul, default) in self.colunas.items()
                if c in params[1:]
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executed.append(sql)


def _convergido(**kw):
    colunas = {"reps": REPS_OK}
    colunas.update({c: _forma_ok(c) for c, *_r in _mod()["TARGETS"]})
    return FakeDB(colunas=colunas, **kw)


def test_banco_atual_recebe_5_statements_na_ordem():
    db = FakeDB()
    _mod()["up"](db)
    assert db.executed == [
        "ALTER TABLE exercise_sets ADD COLUMN reps_pending TINYINT(1) NOT NULL DEFAULT 0, ALGORITHM=INSTANT",
        "ALTER TABLE exercise_sets ADD COLUMN completed_at DATETIME(3) NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE exercise_sets ADD COLUMN planned_weight_kg DECIMAL(8,2) NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE exercise_sets ADD COLUMN load_adjust_source VARCHAR(16) NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE exercise_sets ADD COLUMN rpe_target_source VARCHAR(16) NULL DEFAULT NULL, ALGORITHM=INSTANT",
    ]


def test_convergido_nao_faz_nada():
    assert _mod()["plan"](_convergido()) == []
    maiusculo = _convergido()
    maiusculo.colunas = {k: (tipo.upper(), nul.lower(), default) for k, (tipo, nul, default) in maiusculo.colunas.items()}
    assert _mod()["plan"](maiusculo) == []
    antigo = _convergido()
    antigo.colunas["reps"] = ("int(11)", "YES", None)
    assert _mod()["plan"](antigo) == []


def test_meio_caminho_aplica_so_o_que_falta():
    db = _convergido()
    del db.colunas["planned_weight_kg"]
    assert [s for s, _ in _mod()["plan"](db)] == [
        "ALTER TABLE exercise_sets ADD COLUMN planned_weight_kg DECIMAL(8,2) NULL DEFAULT NULL, ALGORITHM=INSTANT"
    ]


def test_reps_not_null_vira_null_antes_das_colunas():
    db = _convergido()
    db.colunas["reps"] = ("int", "NO", None)
    assert [s for s, _ in _mod()["plan"](db)] == ["ALTER TABLE exercise_sets MODIFY COLUMN reps INT NULL"]


def test_reps_de_outro_tipo_ou_ausente_falha():
    db = _convergido()
    db.colunas["reps"] = ("varchar(10)", "YES", None)
    with pytest.raises(_mod()["ModoIntensivoError"], match="Nenhum statement executado"):
        _mod()["up"](db)
    del db.colunas["reps"]
    with pytest.raises(_mod()["ModoIntensivoError"], match="reps nao existe"):
        _mod()["up"](db)
    assert db.executed == []


@pytest.mark.parametrize(
    "coluna, forma",
    [
        ("reps_pending", ("tinyint(1)", "YES", "0")),  # nulabilidade
        ("reps_pending", ("tinyint(1)", "NO", "1")),  # default
        ("reps_pending", ("int", "NO", "0")),  # tipo
        ("completed_at", ("datetime", "YES", None)),  # sem fração
        ("planned_weight_kg", ("decimal(10,2)", "YES", None)),
        ("load_adjust_source", ("varchar(16)", "NO", None)),
        ("rpe_target_source", ("varchar(16)", "YES", "protocol")),
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(coluna, forma):
    db = FakeDB(colunas={"reps": REPS_OK, coluna: forma})
    with pytest.raises(_mod()["ModoIntensivoError"], match="Nenhum statement executado"):
        _mod()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_na_execucao_real_e_passa_no_dry_run():
    with pytest.raises(_mod()["ModoIntensivoError"], match="exercise_sets nao existe"):
        _mod()["plan"](FakeDB(existe=False))
    assert len(_mod()["plan"](FakeDB(existe=False, dry_run=True))) == 5


def test_plan_down_inverso_idempotente_e_sem_reps():
    assert [s for s, _ in _mod()["plan_down"](_convergido())] == [
        "ALTER TABLE exercise_sets DROP COLUMN rpe_target_source",
        "ALTER TABLE exercise_sets DROP COLUMN load_adjust_source",
        "ALTER TABLE exercise_sets DROP COLUMN planned_weight_kg",
        "ALTER TABLE exercise_sets DROP COLUMN completed_at",
        "ALTER TABLE exercise_sets DROP COLUMN reps_pending",
    ]
    assert _mod()["plan_down"](FakeDB()) == []
    assert _mod()["plan_down"](FakeDB(existe=False)) == []


def test_ddl_sem_fk_sem_backfill_e_sem_dois_pontos():
    texto = MIGRATION.read_text(encoding="utf-8")
    codigo = texto.split('"""', 2)[2]
    assert "FOREIGN KEY" not in codigo.upper()
    assert "UPDATE " not in codigo.upper()
    for sql, _ in _mod()["plan"](FakeDB(colunas={"reps": ("int", "NO", None)})):
        assert ":" not in sql


def test_vocabulario_das_origens_cabe_na_coluna():
    from app.domain.session_loads import LOAD_ADJUST_SOURCES
    from app.domain.training_safety import RPE_TARGET_SOURCES

    for valor in (*LOAD_ADJUST_SOURCES, *RPE_TARGET_SOURCES):
        assert len(valor) <= _mod()["SOURCE_MAX_LENGTH"]

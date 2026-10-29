"""Plano da migração 089 (medidas de série: `measure`, `duration_seconds*`,
`time_window_seconds`, `default_measure`; Lote T1) contra um
`information_schema` sintético:

- banco atual -> 9 statements (um ADD COLUMN INSTANT por coluna, na ordem);
- convergido -> 0; meio caminho -> só o que falta;
- tipo/nulabilidade/default divergente -> erro antes do 1º statement;
- tabela ausente -> erro (DRY-RUN aceita); `plan_down` inverso;
- DDL: nenhuma FK, nenhum backfill, nenhum ':' em literal (Data API);
- o DEFAULT da coluna é o `DEFAULT_MEASURE` do dono e o VARCHAR cabe o vocabulário.

Rodar: cd api && python -m pytest tests/unit/test_migration_089_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m
from app.domain import set_measures as sm

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "089_medidas_de_serie.py"
TABELAS = ("exercise_catalog", "workout_plan_template_slot_items", "workout_plan_exercises", "exercise_sets")


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _alvos():
    return _mod()["TARGETS"]


def _coluna_ok(tabela, coluna):
    for t, c, _ddl, tipo, nul, default in _alvos():
        if (t, c) == (tabela, coluna):
            return (tipo, nul, default)
    raise AssertionError((tabela, coluna))


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
            assert set(params[: len(TABELAS)]) == set(TABELAS)
            return [
                {"TABLE_NAME": t, "COLUMN_NAME": c, "COLUMN_TYPE": tipo, "IS_NULLABLE": nul, "COLUMN_DEFAULT": default}
                for (t, c), (tipo, nul, default) in self.colunas.items()
                if c in params[len(TABELAS) :]
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executed.append(sql)


def _convergido(**kw):
    return FakeDB(colunas={(t, c): _coluna_ok(t, c) for t, c, *_r in _alvos()}, **kw)


def test_banco_atual_recebe_9_statements_na_ordem():
    db = FakeDB()
    _mod()["up"](db)
    assert db.executed == [
        "ALTER TABLE exercise_catalog ADD COLUMN default_measure VARCHAR(16) NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE workout_plan_template_slot_items ADD COLUMN measure VARCHAR(16) NOT NULL DEFAULT 'reps', ALGORITHM=INSTANT",
        "ALTER TABLE workout_plan_template_slot_items ADD COLUMN duration_seconds_target INT NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE workout_plan_template_slot_items ADD COLUMN time_window_seconds INT NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE workout_plan_exercises ADD COLUMN measure VARCHAR(16) NOT NULL DEFAULT 'reps', ALGORITHM=INSTANT",
        "ALTER TABLE workout_plan_exercises ADD COLUMN duration_seconds_target INT NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE workout_plan_exercises ADD COLUMN time_window_seconds INT NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE exercise_sets ADD COLUMN measure VARCHAR(16) NOT NULL DEFAULT 'reps', ALGORITHM=INSTANT",
        "ALTER TABLE exercise_sets ADD COLUMN duration_seconds INT NULL DEFAULT NULL, ALGORITHM=INSTANT",
    ]
    assert len(db.executed) == 9


def test_convergido_nao_faz_nada():
    assert _mod()["plan"](_convergido()) == []
    maiusculo = _convergido()
    maiusculo.colunas = {k: (tipo.upper(), nul.lower(), default) for k, (tipo, nul, default) in maiusculo.colunas.items()}
    assert _mod()["plan"](maiusculo) == []
    # MySQL < 8.0.19 devolve `int(11)`; é o mesmo tipo.
    antigo = _convergido()
    antigo.colunas = {k: ("int(11)" if tipo == "int" else tipo, nul, default) for k, (tipo, nul, default) in antigo.colunas.items()}
    assert _mod()["plan"](antigo) == []


def test_meio_caminho_aplica_so_o_que_falta():
    db = _convergido()
    del db.colunas[("workout_plan_exercises", "time_window_seconds")]
    del db.colunas[("exercise_sets", "duration_seconds")]
    assert [s for s, _ in _mod()["plan"](db)] == [
        _mod()["add_column_sql"]("workout_plan_exercises", "time_window_seconds", _mod()["SECONDS_DDL"]),
        _mod()["add_column_sql"]("exercise_sets", "duration_seconds", _mod()["SECONDS_DDL"]),
    ]


@pytest.mark.parametrize(
    "tabela, coluna, forma",
    [
        ("exercise_sets", "measure", ("varchar(40)", "NO", "reps")),  # tipo
        ("exercise_sets", "measure", ("varchar(16)", "YES", "reps")),  # nulabilidade
        ("exercise_sets", "measure", ("varchar(16)", "NO", "working")),  # default
        ("exercise_sets", "measure", ("varchar(16)", "NO", None)),  # sem default
        ("exercise_catalog", "default_measure", ("varchar(16)", "NO", None)),
        ("exercise_catalog", "default_measure", ("varchar(16)", "YES", "reps")),
        ("workout_plan_exercises", "duration_seconds_target", ("bigint", "YES", None)),
        ("workout_plan_template_slot_items", "time_window_seconds", ("int", "NO", None)),
        ("exercise_sets", "duration_seconds", ("int", "YES", "0")),
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(tabela, coluna, forma):
    db = FakeDB(colunas={(tabela, coluna): forma})
    with pytest.raises(_mod()["MedidasDeSerieError"], match="Nenhum statement executado"):
        _mod()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_na_execucao_real_e_passa_no_dry_run():
    with pytest.raises(_mod()["MedidasDeSerieError"], match="exercise_sets nao existe"):
        _mod()["plan"](FakeDB(tabelas=tuple(t for t in TABELAS if t != "exercise_sets")))
    assert len(_mod()["plan"](FakeDB(tabelas=(), dry_run=True))) == 9
    assert len(_mod()["plan"](FakeDB(tabelas=("exercise_sets",), dry_run=True))) == 9


def test_plan_down_inverso_e_idempotente():
    assert [s for s, _ in _mod()["plan_down"](_convergido())] == [
        "ALTER TABLE exercise_sets DROP COLUMN duration_seconds",
        "ALTER TABLE exercise_sets DROP COLUMN measure",
        "ALTER TABLE workout_plan_exercises DROP COLUMN time_window_seconds",
        "ALTER TABLE workout_plan_exercises DROP COLUMN duration_seconds_target",
        "ALTER TABLE workout_plan_exercises DROP COLUMN measure",
        "ALTER TABLE workout_plan_template_slot_items DROP COLUMN time_window_seconds",
        "ALTER TABLE workout_plan_template_slot_items DROP COLUMN duration_seconds_target",
        "ALTER TABLE workout_plan_template_slot_items DROP COLUMN measure",
        "ALTER TABLE exercise_catalog DROP COLUMN default_measure",
    ]
    assert _mod()["plan_down"](FakeDB()) == []
    assert _mod()["plan_down"](FakeDB(tabelas=())) == []
    db = _convergido()
    _mod()["down"](db)
    assert len(db.executed) == 9


def test_ddl_sem_fk_sem_backfill_e_sem_dois_pontos_em_literal():
    fonte = MIGRATION.read_text(encoding="utf-8")
    statements = [s for s, _ in _mod()["plan"](FakeDB())] + [s for s, _ in _mod()["plan_down"](_convergido())]
    for sql in statements:
        assert "FOREIGN KEY" not in sql.upper() and "REFERENCES" not in sql.upper()
        assert ":" not in sql
        assert sql.count(";") == 0
    codigo = fonte.split('"""', 2)[2].upper()  # sem a docstring
    for proibido in ("UPDATE ", "INSERT ", "DELETE ", "PREPARE ", "SET @", "FROM APP", "IMPORT APP"):
        assert proibido not in codigo
    # Único uso de exercise_catalog no código: o ADD COLUMN da sugestão (sem FK).
    assert "REFERENCES EXERCISE_CATALOG" not in codigo


def test_default_e_tamanho_batem_com_o_dono():
    """A migração é autocontida (não importa `app`); o dono é quem define o
    vocabulário. Os dois têm de concordar."""
    assert _mod()["MEASURE_DEFAULT"] == sm.DEFAULT_MEASURE.value
    assert max(len(measure.value) for measure in sm.SetMeasure) <= _mod()["MEASURE_MAX_LENGTH"]
    assert _mod()["MEASURE_DDL"] == "VARCHAR(16) NOT NULL DEFAULT 'reps'"

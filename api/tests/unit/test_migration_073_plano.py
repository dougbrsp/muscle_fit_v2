"""Plano da migracao 073 (modo de contagem do volume por Modelo, Lote 2) contra
um `information_schema` sintetico: banco atual -> 6 statements na ordem;
convergido -> 0; parcial -> so o que falta (seed reexecutavel enquanto a
forma final nao fecha); forma divergente -> erro antes do 1o statement;
tabela ausente -> erro (DRY-RUN aceita); `plan_down` recusa Modelo em modo
ponderado; as tabelas da 057 nao sao tocadas; paridade do enum com o motor;
sem FK para `exercise_catalog`."""

from __future__ import annotations

import functools

import pytest

from app import migrate as m
from app.intelligence.methodology_volume import DEFAULT_VOLUME_COUNTING_MODE, VolumeCountingMode

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "073_modo_contagem_volume.py"
MODE_RANGES = "muscle_volume_class_mode_ranges"
WEEKLY = "muscle_volume_class_weekly_targets"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _g(name):
    return _mod()[name]


BASE_TABLES = (
    "workout_plan_template_versions",
    "workout_plan_templates",
    "muscle_volume_classes",
    "muscle_volume_class_ranges",
)


def _new_table_columns() -> dict:
    columns = {}
    for table, (expected, _fk) in _g("NEW_TABLES").items():
        columns.update({(table, name): spec for name, spec in expected.items()})
    return columns


class FakeDB:
    def __init__(self, *, tables=BASE_TABLES, columns=None, fks=(), dry_run=False, non_default=0):
        self.tables = tuple(tables)
        self.columns = dict(columns or {})
        self.fks = tuple(fks)
        self.dry_run = dry_run
        self.non_default = non_default
        self.executed: list[str] = []
        self.queries: list[str] = []

    def query(self, sql, params=None):
        self.queries.append(sql)
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t, "ENGINE": "InnoDB"} for t in self.tables]
        if "information_schema.COLUMNS" in sql:
            return [
                {"TABLE_NAME": t, "COLUMN_NAME": c, "COLUMN_TYPE": typ, "IS_NULLABLE": nul}
                for (t, c), (typ, nul) in self.columns.items()
            ]
        if "information_schema.KEY_COLUMN_USAGE" in sql:
            return [{"TABLE_NAME": t, "CONSTRAINT_NAME": n} for t, n in self.fks]
        if sql.startswith("SELECT COUNT(*)"):
            return [{"n": self.non_default}]
        raise AssertionError(f"consulta inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executed.append(sql)


def _convergido(**overrides) -> FakeDB:
    mode = _g("MODE_COLUMN_TYPE")
    columns = {
        ("workout_plan_template_versions", "volume_counting_mode"): (mode, "NO"),
        ("workout_plan_templates", "volume_counting_mode"): (mode, "NO"),
        **_new_table_columns(),
    }
    params = dict(
        tables=BASE_TABLES + (MODE_RANGES, WEEKLY),
        columns=columns,
        fks=((MODE_RANGES, "fk_mvcmr_class"), (WEEKLY, "fk_mvcwt_class")),
    )
    params.update(overrides)
    return FakeDB(**params)


def test_enum_da_migracao_espelha_o_motor():
    assert _g("COUNTING_MODES") == tuple(mode.value for mode in VolumeCountingMode)
    assert _g("DEFAULT_MODE") == DEFAULT_VOLUME_COUNTING_MODE.value


def test_banco_atual_recebe_os_6_statements_na_ordem():
    statements = _g("plan")(FakeDB())
    assert [sql for sql, _ in statements] == [
        _g("CREATE_MODE_RANGES_SQL"),
        _g("CREATE_WEEKLY_SQL"),
        _g("SEED_MODE_RANGES_SQL"),
        _g("SEED_WEEKLY_SQL"),
        _g("ADD_VERSION_MODE_SQL"),
        _g("ADD_TEMPLATE_MODE_SQL"),
    ]
    for sql in (_g("ADD_VERSION_MODE_SQL"), _g("ADD_TEMPLATE_MODE_SQL")):
        assert "NOT NULL DEFAULT 'primary_only'" in sql


def test_tabelas_da_057_nao_sao_alteradas():
    """Rollback seguro: o codigo antigo le `muscle_volume_class_ranges` sem
    filtro e o CRUD antigo apaga por `class_id` - a tabela fica como esta."""
    todos = [sql for sql, _ in _g("plan")(FakeDB())] + [
        _g(n) for n in ("DROP_TEMPLATE_MODE_SQL", "DROP_VERSION_MODE_SQL", "DROP_WEEKLY_SQL", "DROP_MODE_RANGES_SQL")
    ]
    for sql in todos:
        assert "ALTER TABLE muscle_volume_class_ranges" not in sql
        assert "ALTER TABLE muscle_volume_classes" not in sql
        assert "INSERT INTO muscle_volume_class_ranges" not in sql
        assert "DELETE FROM muscle_volume_class" not in sql


def test_seed_copia_primary_only_marcando_nao_calibrada_e_e_reexecutavel():
    seed_ranges = _g("SEED_MODE_RANGES_SQL")
    for coluna in ("sets_min", "sets_max", "exercises_min", "exercises_max", "sets_source", "sets_evidence",
                   "exercises_source", "exercises_evidence"):
        assert f"r.{coluna}" in seed_ranges
    assert "FROM muscle_volume_class_ranges r" in seed_ranges
    assert "NOT EXISTS" in seed_ranges and "NOT EXISTS" in _g("SEED_WEEKLY_SQL")
    # params: (modo novo, marca de copia, modo novo)
    assert _g("SEED_MODE_RANGES_PARAMS") == ("primary_secondary", "primary_only", "primary_secondary")
    assert _g("SEED_WEEKLY_PARAMS") == ("primary_secondary", "primary_only", "primary_secondary")
    assert seed_ranges.count("%s") == len(_g("SEED_MODE_RANGES_PARAMS"))
    assert _g("SEED_WEEKLY_SQL").count("%s") == len(_g("SEED_WEEKLY_PARAMS"))
    # Data API: sem ':' em literal (o shim trataria como parametro nomeado).
    for sql, _ in _g("plan")(FakeDB()):
        assert ":" not in sql


def test_convergido_nao_faz_nada():
    assert _g("plan")(_convergido()) == []


def test_parcial_com_ddl_feito_e_seed_pendente_repete_o_seed():
    db = _convergido()
    del db.columns[("workout_plan_templates", "volume_counting_mode")]
    assert [sql for sql, _ in _g("plan")(db)] == [
        _g("SEED_MODE_RANGES_SQL"),
        _g("SEED_WEEKLY_SQL"),
        _g("ADD_TEMPLATE_MODE_SQL"),
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda db: db.columns.__setitem__(
            ("workout_plan_template_versions", "volume_counting_mode"), ("varchar(20)", "NO")
        ),
        lambda db: db.columns.__setitem__(
            ("workout_plan_templates", "volume_counting_mode"), (_g("MODE_COLUMN_TYPE"), "YES")
        ),
        lambda db: setattr(db, "fks", ((WEEKLY, "fk_mvcwt_class"),)),
        lambda db: db.columns.pop((WEEKLY, "weekly_source")),
        lambda db: db.columns.__setitem__((MODE_RANGES, "sets_min"), ("decimal(6,2)", "NO")),
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(mutate):
    db = _convergido()
    mutate(db)
    with pytest.raises(_g("CountingModeError")):
        _g("up")(db)
    assert db.executed == []


def test_tabela_ausente_falha_na_execucao_real_e_passa_no_dry_run():
    with pytest.raises(_g("CountingModeError")):
        _g("plan")(FakeDB(tables=BASE_TABLES[1:]))
    assert len(_g("plan")(FakeDB(tables=(), dry_run=True))) == 6


def test_plan_so_le_information_schema():
    db = FakeDB()
    _g("plan")(db)
    assert db.queries and all("information_schema" in sql for sql in db.queries)


def test_sem_fk_para_exercise_catalog():
    texto = MIGRATION.read_text(encoding="utf-8")
    assert "REFERENCES exercise_catalog" not in texto
    assert texto.count("REFERENCES {CLASSES}(id)") == 2


def test_plan_down_inverte():
    assert [sql for sql, _ in _g("plan_down")(_convergido())] == [
        _g("DROP_TEMPLATE_MODE_SQL"),
        _g("DROP_VERSION_MODE_SQL"),
        _g("DROP_WEEKLY_SQL"),
        _g("DROP_MODE_RANGES_SQL"),
    ]
    assert _g("plan_down")(FakeDB()) == []


def test_plan_down_recusa_modelo_em_modo_ponderado():
    db = _convergido(non_default=2)
    with pytest.raises(_g("CountingModeError")):
        _g("down")(db)
    assert db.executed == []

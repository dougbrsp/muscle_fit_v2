"""Plano da migração 075 (`body_measurements.site` ganha `neck`, `forearm_l`,
`forearm_r` no fim do ENUM, nutrição L0) contra um `information_schema`
sintético: ENUM da 047 -> 1 MODIFY; ENUM da 075 -> 0; ENUM estranho, coluna
ausente ou NULL -> erro antes do primeiro statement; tabela ausente -> erro
(DRY-RUN aceita); `plan_down` só sem linhas nos locais novos.

Paridade de vocabulário: a tupla da migração == `domain.body_measurement_sites.MEASUREMENT_SITES`
(dono), e o prefixo == a tupla da 047."""

from __future__ import annotations

import functools

import pytest

from app import migrate as m
from app.domain import body_measurement_sites as sites

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "075_body_measurements_sites.py"
MIGRATION_047 = m.DEFAULT_MIGRATIONS_DIR / "047_body_measurements.py"
T = "body_measurements"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


@functools.lru_cache(maxsize=1)
def _module_047() -> dict:
    return m.load_python_migration(MIGRATION_047).__globals__


def _enum(values) -> str:
    return "enum(" + ",".join(f"'{v}'" for v in values) + ")"


class FakeDB:
    def __init__(self, column_type=None, *, nullable="NO", table=True, dry_run=False, rows_in_added=0):
        self.column_type = column_type
        self.nullable = nullable
        self.table = table
        self.dry_run = dry_run
        self.rows_in_added = rows_in_added
        self.executed: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": T}] if self.table else []
        if "information_schema.COLUMNS" in sql:
            if self.column_type is None:
                return []
            return [{"COLUMN_NAME": "site", "COLUMN_TYPE": self.column_type, "IS_NULLABLE": self.nullable}]
        if sql.startswith("SELECT COUNT(*) AS n FROM body_measurements"):
            assert tuple(params) == _module()["SITES_ADDED"]
            return [{"n": self.rows_in_added}]
        raise AssertionError(f"consulta inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executed.append(sql)


def test_vocabulario_da_migracao_e_o_do_dono_e_a_047_e_o_prefixo():
    assert _module()["SITES_075"] == sites.MEASUREMENT_SITES
    assert _module()["SITES_047"] == _module_047()["MEASUREMENT_SITES"] == sites.SITES_047
    assert _module()["SITES_ADDED"] == sites.SITES_ADDED_075 == ("neck", "forearm_l", "forearm_r")
    assert _module()["SITES_075"][: len(sites.SITES_047)] == sites.SITES_047, "os novos entram NO FIM"


def test_modify_acrescenta_no_fim_e_mantem_not_null():
    sql = _module()["MODIFY_SQL"]
    esperado = "ENUM(" + ",".join(f"'{s}'" for s in sites.MEASUREMENT_SITES) + ") NOT NULL"
    assert sql == f"ALTER TABLE body_measurements MODIFY COLUMN site {esperado}"
    assert sql.index("'calf_r'") < sql.index("'neck'") < sql.index("'forearm_l'") < sql.index("'forearm_r'")


def test_enum_da_047_recebe_um_modify_e_o_da_075_nada():
    db = FakeDB(_enum(sites.SITES_047))
    assert [sql for sql, _ in _module()["plan"](db)] == [_module()["MODIFY_SQL"]]
    _module()["up"](db)
    assert db.executed == [_module()["MODIFY_SQL"]]
    # Reexecução no estado convergido (MySQL devolve o COLUMN_TYPE em minúsculas).
    assert _module()["plan"](FakeDB(_enum(sites.MEASUREMENT_SITES))) == []
    assert _module()["plan"](FakeDB(_enum(sites.MEASUREMENT_SITES).upper())) == []


@pytest.mark.parametrize(
    "db",
    [
        FakeDB(_enum(("waist", "hip"))),  # ENUM estranho
        FakeDB(_enum(sites.SITES_047 + ("neck",))),  # meio caminho desconhecido
        FakeDB(None),  # coluna ausente
        FakeDB(_enum(sites.SITES_047), nullable="YES"),
        FakeDB("varchar(20)"),
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(db):
    with pytest.raises(_module()["BodyMeasurementSitesError"]):
        _module()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_na_execucao_real_e_passa_no_dry_run():
    with pytest.raises(_module()["BodyMeasurementSitesError"]):
        _module()["plan"](FakeDB(table=False))
    assert [sql for sql, _ in _module()["plan"](FakeDB(table=False, dry_run=True))] == [_module()["MODIFY_SQL"]]


def test_plan_down_volta_a_047_so_sem_linhas_nos_locais_novos():
    assert [sql for sql, _ in _module()["plan_down"](FakeDB(_enum(sites.MEASUREMENT_SITES)))] == [_module()["REVERT_SQL"]]
    assert _module()["plan_down"](FakeDB(_enum(sites.SITES_047))) == []
    assert _module()["plan_down"](FakeDB(table=False)) == []
    with pytest.raises(_module()["BodyMeasurementSitesError"]):
        _module()["plan_down"](FakeDB(_enum(sites.MEASUREMENT_SITES), rows_in_added=3))


def test_migracao_e_aditiva_sem_dml():
    fonte = MIGRATION.read_text(encoding="utf-8")
    for proibido in ("UPDATE ", "INSERT ", "DELETE ", "DROP "):
        assert proibido not in fonte.upper().replace("UPDATE CURRENT_TIMESTAMP", "")

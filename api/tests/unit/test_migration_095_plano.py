"""Plano da migração 095 (prescrição de cardio do item do Modelo, M98 C0).

- Banco atual (sem as colunas) -> 2 statements (1 ALTER INSTANT com as 9 colunas + o UPDATE do backfill).
- Convergido (colunas + nada a preencher) -> 0; colunas e itens sem modo -> só o UPDATE.
- Parte das colunas já criada -> o ALTER leva só as que faltam.
- Forma divergente -> erro antes do primeiro statement; sem FK.
- ENUM e colunas = vocabulário e campos do dono (`domain/cardio_prescription.py`).
- `down` = 1 ALTER com DROP COLUMN das que existem.

Rodar: cd api && python -m pytest tests/unit/test_migration_095_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m
from app.domain import cardio_prescription as cp

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "095_prescricao_cardio.py"
T = "workout_plan_template_slot_items"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _tipos() -> dict[str, str]:
    return {nome: tipo for nome, (_ddl, tipo) in _module()["COLUMNS"].items()}


class FakeDB:
    def __init__(self, *, tabela=True, colunas=None, pendentes=0, dry_run=False):
        self.tabela = tabela
        # {coluna: (COLUMN_TYPE, IS_NULLABLE)}; None = nenhuma coluna nova
        self.colunas = dict(colunas or {})
        self.pendentes = pendentes
        self.dry_run = dry_run
        self.contou = False

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            assert params == (T,)
            return [{"TABLE_NAME": T}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            assert params[0] == T
            return [
                {"COLUMN_NAME": nome, "COLUMN_TYPE": tipo, "IS_NULLABLE": nulo}
                for nome, (tipo, nulo) in self.colunas.items()
                if nome in params[1:]
            ]
        if sql.startswith("SELECT COUNT(*)"):
            assert params == ("cardio",)
            self.contou = True
            return [{"n": self.pendentes}]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no plano: {sql}")


def _todas() -> dict[str, tuple[str, str]]:
    return {nome: (tipo, "YES") for nome, tipo in _tipos().items()}


def test_enum_e_colunas_sao_os_do_dono():
    mod = _module()
    assert mod["MODE_VALUES"] == cp.CARDIO_MODE_VALUES
    assert mod["BACKFILL_MODE"] == cp.DEFAULT_CARDIO_MODE.value
    assert mod["MODE_COLUMN"] == cp.MODE_FIELD
    assert mod["DURATION_COLUMN"] == cp.DURATION_FIELD
    assert mod["CARDIO_KIND"] == cp.CARDIO_ITEM_KIND
    assert tuple(mod["COLUMNS"]) == cp.NEW_COLUMNS


def test_tetos_do_dono_cabem_nos_tipos():
    """SMALLINT (32767) e TINYINT (127) assinados comportam o teto de cada campo."""
    limites = {"smallint": 32767, "tinyint": 127}
    for nome, tipo in _tipos().items():
        if nome == cp.MODE_FIELD:
            continue
        assert cp.FIELD_LIMITS[nome][1] <= limites[tipo], nome


def test_banco_atual_um_alter_instant_e_o_backfill():
    plano = _module()["plan"](FakeDB())
    assert len(plano) == 2
    alter, params = plano[0]
    assert params is None
    assert alter.startswith(f"ALTER TABLE {T} ADD COLUMN cardio_mode ENUM('continuous','hiit') NULL DEFAULT NULL")
    assert alter.endswith(", ALGORITHM=INSTANT")
    assert alter.count("ADD COLUMN") == 9
    assert "FOREIGN KEY" not in alter.upper() and "REFERENCES" not in alter.upper()
    assert "NOT NULL" not in alter
    update, params = plano[1]
    assert update == (
        f"UPDATE {T} SET cardio_mode = %s WHERE item_kind = %s AND cardio_mode IS NULL "
        "AND cardio_duration_minutes IS NOT NULL"
    )
    assert params == ("continuous", "cardio")


def test_convergido_nao_faz_nada():
    db = FakeDB(colunas=_todas(), pendentes=0)
    assert _module()["plan"](db) == []
    assert db.contou


def test_colunas_criadas_e_itens_sem_modo_so_o_backfill():
    plano = _module()["plan"](FakeDB(colunas=_todas(), pendentes=3))
    assert [sql for sql, _p in plano] == [_module()["BACKFILL_SQL"]]


def test_parte_das_colunas_criada_o_alter_leva_so_as_que_faltam():
    parcial = {nome: v for nome, v in _todas().items() if nome in ("cardio_mode", "cardio_warmup_minutes")}
    plano = _module()["plan"](FakeDB(colunas=parcial, pendentes=0))
    assert len(plano) == 1
    alter = plano[0][0]
    assert alter.count("ADD COLUMN") == 7
    assert "cardio_mode" not in alter and "cardio_warmup_minutes" not in alter


def test_largura_de_exibicao_de_inteiro_e_ignorada():
    colunas = _todas()
    colunas["cardio_target_zone"] = ("tinyint(4)", "YES")
    colunas["hiit_rounds"] = ("smallint(6)", "YES")
    assert _module()["plan"](FakeDB(colunas=colunas)) == []


@pytest.mark.parametrize(
    "coluna,valor",
    [
        ("cardio_mode", ("varchar(16)", "YES")),
        ("cardio_mode", ("enum('continuous')", "YES")),
        ("hiit_rounds", ("int", "YES")),
        ("cardio_target_zone", ("tinyint", "NO")),
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(coluna, valor):
    colunas = _todas()
    colunas[coluna] = valor
    with pytest.raises(_module()["PrescricaoCardioError"], match="Nenhum statement executado"):
        _module()["plan"](FakeDB(colunas=colunas))


def test_tabela_ausente_falha_e_dry_run_lista_o_plano():
    with pytest.raises(_module()["PrescricaoCardioError"], match="nao existe"):
        _module()["plan"](FakeDB(tabela=False))
    plano = _module()["plan"](FakeDB(tabela=False, dry_run=True))
    assert [p[0].split()[0] for p in plano] == ["ALTER", "UPDATE"]


def test_down_solta_so_as_colunas_que_existem():
    mod = _module()
    assert mod["plan_down"](FakeDB()) == []
    plano = mod["plan_down"](FakeDB(colunas=_todas()))
    assert len(plano) == 1
    assert plano[0][0].startswith(f"ALTER TABLE {T} DROP COLUMN cardio_mode")
    assert plano[0][0].count("DROP COLUMN") == 9
    parcial = {"hiit_rounds": ("smallint", "YES")}
    assert mod["plan_down"](FakeDB(colunas=parcial)) == [(f"ALTER TABLE {T} DROP COLUMN hiit_rounds", None)]


def test_sem_literal_com_dois_pontos_para_a_data_api():
    mod = _module()
    for sql in (mod["add_columns_sql"](mod["COLUMNS"]), mod["BACKFILL_SQL"], mod["PENDING_SQL"]):
        assert ":" not in sql

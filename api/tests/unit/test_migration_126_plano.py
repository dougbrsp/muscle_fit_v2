"""Plano da migracao 126 (#288): perfis de resistencia e historico. Sem MySQL: FakeDB le um `information_schema`
em memoria e registra os statements."""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "126_resistance_profiles.py"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, *, tabelas=(), pais=("users", "equipment_exercises"), dry_run=False, tipo_errado=None,
                 pai_tipo="bigint", pai_engine="InnoDB", perfis=0):
        self.perfis, self.pai_tipo, self.pai_engine = perfis, pai_tipo, pai_engine
        self.tabelas, self.pais = set(tabelas), tuple(pais)
        self.dry_run, self.tipo_errado = dry_run, tipo_errado  # (tabela, coluna, tipo)
        self.executed: list[tuple[str, tuple | None]] = []

    def query(self, sql, params=None):
        assert ":" not in sql
        specs = _mod()["SPECS"]
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t, "ENGINE": self.pai_engine if t in self.pais else "InnoDB"}
                    for t in (*self.tabelas, *self.pais)]
        if "information_schema.COLUMNS" in sql:
            linhas = [{"TABLE_NAME": p, "COLUMN_NAME": "id", "COLUMN_TYPE": self.pai_tipo, "IS_NULLABLE": "NO",
                       "COLUMN_KEY": "PRI"} for p in self.pais]
            for t in self.tabelas:
                for nome, (tipo, nul) in specs[t][1].items():
                    if self.tipo_errado and self.tipo_errado[:2] == (t, nome):
                        tipo = self.tipo_errado[2]
                    linhas.append({"TABLE_NAME": t, "COLUMN_NAME": nome, "COLUMN_TYPE": tipo, "IS_NULLABLE": nul,
                                   "COLUMN_KEY": ""})
            return linhas
        if "information_schema.STATISTICS" in sql:
            return [{"TABLE_NAME": t, "INDEX_NAME": idx, "SEQ_IN_INDEX": i, "COLUMN_NAME": c,
                     "NON_UNIQUE": 0 if unico else 1}
                    for t in self.tabelas for idx, (unico, cols) in specs[t][2].items()
                    for i, c in enumerate(cols, 1)]
        if sql.startswith("SELECT COUNT(*)"):
            return [{"total": self.perfis}]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        assert ":" not in sql
        self.executed.append((sql, params))


def test_banco_novo_cria_perfis_antes_do_historico():
    db = FakeDB()
    _mod()["up"](db)
    assert [s for s, _ in db.executed] == [_mod()["CREATE_PROFILES_SQL"], _mod()["CREATE_HISTORY_SQL"]]
    assert all(p is None for _, p in db.executed)


def test_chaves_cascade_set_null_e_nenhuma_fk_para_o_catalogo():
    perfis, historico = _mod()["CREATE_PROFILES_SQL"], _mod()["CREATE_HISTORY_SQL"]
    assert "exercise_catalog" not in perfis and "exercise_catalog" not in historico
    assert "REFERENCES equipment_exercises(id) ON DELETE CASCADE" in perfis
    assert perfis.count("ON DELETE SET NULL") == 2 and historico.count("ON DELETE SET NULL") == 1
    assert "UNIQUE KEY uq_erp_pair (equipment_exercise_id)" in perfis
    assert perfis.count("DEFAULT 'unknown'") == 6


def test_convergido_nao_executa_nada_e_so_cria_a_que_falta():
    db = FakeDB(tabelas=(_mod()["PROFILES"], _mod()["HISTORY"]))
    _mod()["up"](db)
    assert db.executed == []
    db = FakeDB(tabelas=(_mod()["PROFILES"],))
    _mod()["up"](db)
    assert [s for s, _ in db.executed] == [_mod()["CREATE_HISTORY_SQL"]]


def test_coluna_divergente_falha_antes_do_primeiro_statement():
    db = FakeDB(tabelas=(_mod()["PROFILES"],), tipo_errado=(_mod()["PROFILES"], "flags_json", "text"))
    with pytest.raises(Exception, match="126"):
        _mod()["up"](db)
    assert db.executed == []


def test_pai_ausente_falha_e_dry_run_lista():
    with pytest.raises(Exception, match="126"):
        _mod()["plan"](FakeDB(pais=("users",)))
    assert len(_mod()["plan"](FakeDB(pais=(), dry_run=True))) == 2


@pytest.mark.parametrize("kw", [{"pai_tipo": "int"}, {"pai_engine": "MyISAM"}])
def test_pai_com_tipo_de_id_ou_engine_divergente_falha_sem_executar(kw):
    db = FakeDB(**kw)
    with pytest.raises(Exception, match="126"):
        _mod()["up"](db)
    assert db.executed == []


def test_down_solta_historico_antes_dos_perfis_e_e_idempotente():
    db = FakeDB(tabelas=(_mod()["PROFILES"], _mod()["HISTORY"]))
    _mod()["down"](db)
    assert [s for s, _ in db.executed] == [_mod()["DROP_HISTORY_SQL"], _mod()["DROP_PROFILES_SQL"]]
    db2 = FakeDB()
    _mod()["down"](db2)
    assert db2.executed == []


def test_down_com_perfis_salvos_falha_sem_executar_nenhum_drop():
    db = FakeDB(tabelas=(_mod()["PROFILES"], _mod()["HISTORY"]), perfis=3)
    with pytest.raises(Exception, match="destrutivo"):
        _mod()["down"](db)
    assert db.executed == []

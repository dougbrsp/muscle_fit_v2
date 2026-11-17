"""Plano da migracao 118 (#188): `gym_context_id` em progression_tracks e a
chave `uk_ptr_user_pair_context` refeita com ela. Sem MySQL: FakeDB le um
`information_schema` em memoria."""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "118_progression_track_gym_context.py"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


COLUNA_OK = ("int", "NO", "0")


class FakeDB:
    def __init__(self, *, tabela=True, coluna=None, chave=None, dry_run=False):
        self.tabela = tabela
        self.coluna = coluna
        self.chave = list(_mod()["KEY_BEFORE"]) if chave is None else chave
        self.dry_run = dry_run
        self.executed: list[str] = []

    def query(self, sql, params=None):
        assert ":" not in sql
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": "progression_tracks"}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            assert params == ("progression_tracks", "gym_context_id")
            if not self.tabela or self.coluna is None:
                return []
            tipo, nul, default = self.coluna
            return [{"TABLE_NAME": "progression_tracks", "COLUMN_TYPE": tipo, "IS_NULLABLE": nul, "COLUMN_DEFAULT": default}]
        if "information_schema.STATISTICS" in sql:
            if not self.tabela:
                return []
            return [{"TABLE_NAME": "progression_tracks", "INDEX_NAME": "uk_ptr_user_pair_context", "COLUMN_NAME": c} for c in self.chave]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        assert params is None
        self.executed.append(sql)


def test_banco_atual_recebe_coluna_e_depois_a_chave():
    db = FakeDB()
    _mod()["up"](db)
    assert db.executed == [
        "ALTER TABLE progression_tracks ADD COLUMN gym_context_id INT NOT NULL DEFAULT 0",
        "ALTER TABLE progression_tracks DROP INDEX uk_ptr_user_pair_context, ADD UNIQUE KEY uk_ptr_user_pair_context "
        "(user_id, exercise_id, equipment_id, fatigue_context, training_goal, gym_context_id)",
    ]


def test_convergido_nao_faz_nada():
    db = FakeDB(coluna=COLUNA_OK, chave=list(_mod()["KEY_AFTER"]))
    _mod()["up"](db)
    assert db.executed == []


def test_meio_caminho_so_refaz_a_chave():
    db = FakeDB(coluna=COLUNA_OK)
    assert [sql for sql, _ in _mod()["plan"](db)] == [_mod()["rebuild_key_sql"](_mod()["KEY_AFTER"])]


@pytest.mark.parametrize("coluna", [("bigint unsigned", "NO", "0"), ("int", "YES", "0"), ("int", "NO", None)])
def test_coluna_divergente_falha_antes_do_primeiro_statement(coluna):
    db = FakeDB(coluna=coluna)
    with pytest.raises(Exception, match="118"):
        _mod()["up"](db)
    assert db.executed == []


def test_chave_desconhecida_ou_ausente_falha_e_dry_run_aceita():
    with pytest.raises(Exception, match="118"):
        _mod()["plan"](FakeDB(chave=["user_id", "exercise_id"]))
    with pytest.raises(Exception, match="118"):
        _mod()["plan"](FakeDB(chave=[]))
    with pytest.raises(Exception, match="118"):
        _mod()["plan"](FakeDB(tabela=False))
    assert len(_mod()["plan"](FakeDB(tabela=False, dry_run=True))) == 2
    assert len(_mod()["plan"](FakeDB(chave=[], dry_run=True))) == 2


def test_down_volta_a_chave_da_093_antes_de_soltar_a_coluna():
    db = FakeDB(coluna=COLUNA_OK, chave=list(_mod()["KEY_AFTER"]))
    _mod()["down"](db)
    assert db.executed == [
        _mod()["rebuild_key_sql"](_mod()["KEY_BEFORE"]),
        "ALTER TABLE progression_tracks DROP COLUMN gym_context_id",
    ]


def test_chave_antes_e_a_que_a_093_deixa_e_ddl_sem_fk_sem_backfill():
    g093 = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "093_training_goal.py").__globals__
    after_093 = {(t, k): after for t, k, _b, after in g093["UNIQUE_KEYS"]}
    assert after_093[("progression_tracks", "uk_ptr_user_pair_context")] == _mod()["KEY_BEFORE"]
    codigo = MIGRATION.read_text(encoding="utf-8").split('"""', 2)[2]
    for proibido in ("FOREIGN KEY", "REFERENCES", "exercise_catalog", "UPDATE ", "INSERT "):
        assert proibido not in codigo, proibido

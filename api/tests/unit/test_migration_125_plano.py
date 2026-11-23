"""Plano da migracao 125 (#286): `food_recipe_versions` e `food_recipe_ingredients`. Sem MySQL: FakeDB le um
`information_schema` em memoria e registra os statements."""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "125_food_recipes.py"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, *, tabelas=(), users=True, dry_run=False, tipo_errado=None,
                 users_tipo="bigint", users_engine="InnoDB", versoes=0):
        self.versoes = versoes
        self.users_tipo, self.users_engine = users_tipo, users_engine
        self.tabelas = set(tabelas)
        self.users = users
        self.dry_run = dry_run
        self.tipo_errado = tipo_errado  # (tabela, coluna, tipo)
        self.executed: list[tuple[str, tuple | None]] = []

    def query(self, sql, params=None):
        assert ":" not in sql
        specs = _mod()["SPECS"]
        if "information_schema.TABLES" in sql:
            nomes = [t for t in (*self.tabelas, *(["users"] if self.users else []))]
            return [{"TABLE_NAME": t, "ENGINE": self.users_engine if t == "users" else "InnoDB"} for t in nomes]
        if "information_schema.COLUMNS" in sql:
            linhas = []
            if self.users:
                linhas.append({"TABLE_NAME": "users", "COLUMN_NAME": "id", "COLUMN_TYPE": self.users_tipo,
                               "IS_NULLABLE": "NO", "COLUMN_KEY": "PRI"})
            for t in self.tabelas:
                for nome, (tipo, nul) in specs[t][1].items():
                    if self.tipo_errado and self.tipo_errado[:2] == (t, nome):
                        tipo = self.tipo_errado[2]
                    linhas.append({"TABLE_NAME": t, "COLUMN_NAME": nome, "COLUMN_TYPE": tipo,
                                   "IS_NULLABLE": nul, "COLUMN_KEY": ""})
            return linhas
        if "information_schema.STATISTICS" in sql:
            linhas = []
            for t in self.tabelas:
                for idx, (unico, cols) in specs[t][2].items():
                    for i, c in enumerate(cols, 1):
                        linhas.append({"TABLE_NAME": t, "INDEX_NAME": idx, "SEQ_IN_INDEX": i, "COLUMN_NAME": c,
                                       "NON_UNIQUE": 0 if unico else 1})
            return linhas
        if sql.startswith("SELECT COUNT(*)"):
            return [{"total": self.versoes}]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        assert ":" not in sql
        self.executed.append((sql, params))


def test_banco_novo_cria_versoes_antes_dos_ingredientes():
    db = FakeDB()
    _mod()["up"](db)
    assert [s for s, _ in db.executed] == [_mod()["CREATE_VERSIONS_SQL"], _mod()["CREATE_INGREDIENTS_SQL"]]
    assert all(p is None for _, p in db.executed)


def test_sem_chave_estrangeira_para_food_items_e_cascade_nos_ingredientes():
    versoes, ingredientes = _mod()["CREATE_VERSIONS_SQL"], _mod()["CREATE_INGREDIENTS_SQL"]
    assert "food_items" not in versoes and "food_items" not in ingredientes
    assert versoes.count("FOREIGN KEY") == 1 and "REFERENCES users(id) ON DELETE CASCADE" in versoes
    assert "instructions MEDIUMTEXT NULL" in versoes
    assert ingredientes.count("FOREIGN KEY") == 1 and "ON DELETE CASCADE" in ingredientes


def test_convergido_nao_executa_nada_e_e_idempotente():
    db = FakeDB(tabelas=(_mod()["VERSIONS"], _mod()["INGREDIENTS"]))
    _mod()["up"](db)
    assert db.executed == []


def test_so_cria_a_tabela_que_falta():
    db = FakeDB(tabelas=(_mod()["VERSIONS"],))
    _mod()["up"](db)
    assert [s for s, _ in db.executed] == [_mod()["CREATE_INGREDIENTS_SQL"]]


def test_coluna_divergente_falha_antes_do_primeiro_statement():
    db = FakeDB(tabelas=(_mod()["VERSIONS"],), tipo_errado=("food_recipe_versions", "calculated_json", "text"))
    with pytest.raises(Exception, match="125"):
        _mod()["up"](db)
    assert db.executed == []


def test_users_ausente_falha_e_dry_run_lista():
    with pytest.raises(Exception, match="125"):
        _mod()["plan"](FakeDB(users=False))
    assert len(_mod()["plan"](FakeDB(users=False, dry_run=True))) == 2


def test_down_solta_ingredientes_antes_das_versoes_e_e_idempotente():
    db = FakeDB(tabelas=(_mod()["VERSIONS"], _mod()["INGREDIENTS"]))
    _mod()["down"](db)
    assert [s for s, _ in db.executed] == [_mod()["DROP_INGREDIENTS_SQL"], _mod()["DROP_VERSIONS_SQL"]]
    db2 = FakeDB()
    _mod()["down"](db2)
    assert db2.executed == []


def test_down_com_versoes_salvas_falha_sem_executar_nenhum_drop():
    db = FakeDB(tabelas=(_mod()["VERSIONS"], _mod()["INGREDIENTS"]), versoes=3)
    with pytest.raises(Exception, match="destrutivo"):
        _mod()["down"](db)
    assert db.executed == []


@pytest.mark.parametrize("kw", [{"users_tipo": "int"}, {"users_engine": "MyISAM"}])
def test_users_com_tipo_de_id_ou_engine_divergente_falha_sem_executar(kw):
    db = FakeDB(**kw)
    with pytest.raises(Exception, match="125"):
        _mod()["up"](db)
    assert db.executed == []

"""Migracao 112 (RBAC lote 2A): reconcilia grupos de sistema com `users.role`.

Banco falso que projeta colunas (so devolve as colunas pedidas pelo SELECT).

Rodar: cd api && python -m pytest tests/unit/test_migracao_112_backfill_grupos.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "112_user_access_groups_backfill.py"
MIGRATION_111 = m.DEFAULT_MIGRATIONS_DIR / "111_access_groups_rbac.py"

GROUP_IDS = {"admin": 1, "personal_trainer": 2, "usuario": 3, "free": 4, "custom": 5}
SYSTEM = {"admin", "personal_trainer", "usuario"}


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


@functools.lru_cache(maxsize=1)
def _mod111() -> dict:
    return m.load_python_migration(MIGRATION_111).__globals__


class FakeDB:
    def __init__(self, users, members=(), tables=("access_groups", "user_access_groups"), groups=None):
        self.users = dict(users)  # id -> role
        self.members = set(members)  # (user_id, group_key)
        self.tables = set(tables)
        self.groups = dict(GROUP_IDS if groups is None else groups)  # key -> id
        self.executed: list[tuple[str, tuple | None]] = []

    def _key(self, group_id):
        return next(k for k, v in self.groups.items() if v == group_id)

    def query(self, sql, params=None):
        s = " ".join(sql.split())
        if "information_schema.TABLES" in s:
            return [{"TABLE_NAME": t} for t in params if t in self.tables]
        if s.startswith("SELECT id, group_key FROM access_groups WHERE is_system = 1"):
            return [{"id": v, "group_key": k} for k, v in self.groups.items() if k in SYSTEM]
        if s == "SELECT id, role FROM users":
            return [{"id": i, "role": r} for i, r in self.users.items()]
        if s.startswith("SELECT m.user_id AS user_id, m.group_id AS group_id FROM user_access_groups m JOIN access_groups g"):
            assert "g.is_system = 1" in s
            return [
                {"user_id": u, "group_id": self.groups[k]}
                for u, k in sorted(self.members)
                if k in SYSTEM and k in self.groups
            ]
        raise AssertionError(f"leitura inesperada: {s}")

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        s = " ".join(sql.split())
        if s.startswith("DELETE FROM user_access_groups WHERE user_id = %s AND group_id = %s"):
            self.members.discard((params[0], self._key(params[1])))
        elif s.startswith("INSERT IGNORE INTO user_access_groups (user_id, group_id) VALUES (%s, %s)"):
            self.members.add((params[0], self._key(params[1])))
        else:
            raise AssertionError(f"statement inesperado: {s[:80]}")


def _run(db):
    _mod()["up"](db)
    return db


def test_usuario_sem_linha_recebe_o_grupo_do_papel():
    db = _run(FakeDB({1: "admin", 2: "personal_trainer", 3: "regular"}))
    assert db.members == {(1, "admin"), (2, "personal_trainer"), (3, "usuario")}


def test_papel_divergente_troca_o_grupo_de_sistema_com_delete_antes_do_insert():
    db = FakeDB({1: "personal_trainer"}, members={(1, "usuario")})
    plano = _mod()["plan"](db)
    assert [s.split()[0] for s, _ in plano] == ["DELETE", "INSERT"]
    _run(db)
    assert db.members == {(1, "personal_trainer")}


def test_ja_correto_gera_plano_vazio():
    db = FakeDB({1: "admin", 2: "regular"}, members={(1, "admin"), (2, "usuario")})
    assert _mod()["plan"](db) == []
    _run(db)
    assert db.executed == []


def test_grupo_nao_sistema_e_preservado():
    db = FakeDB({1: "regular", 2: "regular"}, members={(1, "free"), (2, "custom"), (2, "usuario")})
    _run(db)
    assert db.members == {(1, "free"), (1, "usuario"), (2, "custom"), (2, "usuario")}
    assert not [1 for s, _ in db.executed if s.startswith("DELETE")]


def test_papel_desconhecido_fica_sem_grupo_de_sistema_mas_mantem_nao_sistema():
    db = FakeDB({1: "visitante", 2: "outro"}, members={(1, "usuario"), (1, "free")})
    _run(db)
    assert db.members == {(1, "free")}


def test_usuario_inativo_tambem_e_reconciliado():
    # a tabela users falsa nao filtra por is_active: o plano nao pode filtrar
    db = _run(FakeDB({7: "regular"}))
    assert db.members == {(7, "usuario")}
    sql_leitura = _mod()["SELECT_USERS_SQL"]
    assert "is_active" not in sql_leitura and "WHERE" not in sql_leitura


@pytest.mark.parametrize("remover", ["admin", "personal_trainer", "usuario"])
def test_grupo_de_sistema_ausente_e_erro_sem_escrita(remover):
    grupos = {k: v for k, v in GROUP_IDS.items() if k != remover}
    db = FakeDB({1: "admin"}, groups=grupos)
    with pytest.raises(_mod()["UserAccessGroupsBackfillError"]):
        _run(db)
    assert db.executed == []


@pytest.mark.parametrize(
    "presentes",
    [set(), {"access_groups"}, {"user_access_groups"}],
    ids=["ambas_ausentes", "so_grupos", "so_associacoes"],
)
def test_tabela_ausente_devolve_plano_vazio_sem_erro_e_sem_escrita(presentes):
    # dry-run do lote com a 111 pendente: a 111 ainda nao criou as tabelas
    db = FakeDB({1: "admin"}, tables=presentes)
    assert _mod()["plan"](db) == []
    _run(db)
    assert db.executed == []
    assert db.members == set()


def test_segunda_execucao_nao_emite_escrita():
    db = FakeDB(
        {1: "admin", 2: "regular", 3: "personal_trainer", 4: "visitante"},
        members={(2, "admin"), (3, "free"), (4, "usuario")},
    )
    _run(db)
    n = len(db.executed)
    assert n > 0
    assert _mod()["plan"](db) == []
    _run(db)
    assert len(db.executed) == n


def test_interrompida_em_qualquer_ponto_converge():
    def novo():
        return FakeDB({1: "admin", 2: "regular", 3: "visitante"}, members={(1, "usuario"), (2, "admin"), (3, "personal_trainer")})

    completo = _run(novo())
    for corte in range(len(_mod()["plan"](novo())) + 1):
        db = novo()
        for sql, params in _mod()["plan"](db)[:corte]:
            db.execute(sql, params)
        _run(db)
        assert db.members == completo.members
        assert _mod()["plan"](db) == []


def test_down_nao_apaga_nada():
    db = FakeDB({1: "admin"}, members={(1, "admin")})
    assert _mod()["plan_down"](db) == []
    _mod()["down"](db)
    assert db.executed == [] and db.members == {(1, "admin")}


def test_mapeamento_local_tem_paridade_com_a_111():
    assert tuple(_mod()["ROLE_TO_GROUP"]) == tuple(_mod111()["ROLE_TO_GROUP"])
    sistema_111 = {k for k, _, _, is_sys in _mod111()["SEED_GROUPS"] if is_sys}
    assert set(_mod()["SYSTEM_GROUP_KEYS"]) == sistema_111


def test_so_delete_e_insert_ignore_parametrizados_sem_ddl():
    db = _run(FakeDB({1: "regular", 2: "admin"}, members={(2, "usuario")}))
    for sql, params in db.executed:
        s = " ".join(sql.split())
        assert s.startswith(("DELETE FROM user_access_groups", "INSERT IGNORE INTO user_access_groups"))
        assert ";" not in s and params is not None

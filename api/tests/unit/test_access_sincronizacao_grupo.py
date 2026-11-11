"""Sincronizacao `users.role` -> `user_access_groups` (ADR 0011, pre-condicoes do
lote 2): create_user, update_user, ensure_admin_seed e o modulo de membros.

Banco falso com estado e transacao com rollback; o cursor responde pelo SQL
recebido e devolve so as colunas pedidas.

Rodar: cd api && python -m pytest tests/unit/test_access_sincronizacao_grupo.py
"""

from __future__ import annotations

import copy
import functools
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app import auth, users
from app import migrate as m
from app.access import membership
from app.access import service as access_service
from app.access.membership import (
    InvalidMembershipTargetError,
    ROLE_TO_SYSTEM_GROUP,
    MissingSystemGroupError,
    assign_role_group,
    switch_role_group,
)
from app.request_models import UserCreateIn, UserUpdateIn
from app.security import Identity

GROUP_IDS = {"admin": 1, "personal_trainer": 2, "usuario": 3, "free": 4, "equipe_x": 9}
ADMIN_ACTOR = Identity(user_id=1, role="admin", source="session")


def _request():
    """`Request` falso: `permissions_for_request` so usa `request.state`."""
    return SimpleNamespace(state=SimpleNamespace())


@pytest.fixture(autouse=True)
def _permissoes_de_admin(monkeypatch):
    """Resolvedor de permissoes sem banco: o ator e membro do grupo de sistema `admin`
    (acesso total); a sincronizacao papel -> grupo, nao o RBAC, e o que se prova aqui."""
    monkeypatch.setattr(
        access_service,
        "resolve_permissions",
        lambda identity: access_service.merge_grants(
            [{"group_key": "admin", "is_system": 1, "resource": None, "action": None, "scope": None}]
        ),
    )

# Texto exato esperado; um `INSERT IGNORE` cai no ramo "SQL inesperado" e o teste falha.
MEMBERSHIP_PREFIX = "INSERT INTO user_access_groups"
MEMBERSHIP_INSERT = (
    "INSERT INTO user_access_groups (user_id, group_id, created_by_user_id) VALUES (%s, %s, %s) "
    "ON DUPLICATE KEY UPDATE user_id = user_id"
)


class FakeDb:
    def __init__(self):
        self.groups = {
            "admin": {"id": 1, "is_system": 1},
            "personal_trainer": {"id": 2, "is_system": 1},
            "usuario": {"id": 3, "is_system": 1},
            "free": {"id": 4, "is_system": 0},
            "equipe_x": {"id": 9, "is_system": 0},
        }
        self.users = {1: {"id": 1, "username": "adm", "role": "admin", "is_active": 1}}
        self.members = {(1, 1)}  # (user_id, group_id)
        self.next_id = 100
        self.log: list[tuple[str, tuple | None]] = []
        self.commits = 0
        self.rollbacks = 0
        self.cursors_used: set[int] = set()
        self.transactions = 0
        self.fail_membership_insert: Exception | None = None  # simula erro de FK no INSERT
        self.forced_lastrowid = "auto"  # "auto" ou valor devolvido pelo cursor apos INSERT INTO users

    @contextmanager
    def transaction(self):
        self.transactions += 1
        snap = copy.deepcopy((self.users, self.members))
        try:
            yield _Conn(self)
        except BaseException:
            self.users, self.members = snap
            self.rollbacks += 1
            raise
        self.commits += 1

    def sqls(self, prefix):
        return [(s, p) for s, p in self.log if s.startswith(prefix)]


class _Conn:
    def __init__(self, db):
        self.cur = _Cursor(db)

    def cursor(self):
        return self.cur


class _Cursor:
    def __init__(self, db):
        self.db = db
        self._rows: list[dict] = []
        self.lastrowid = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        db = self.db
        flat = " ".join(sql.split())
        db.log.append((flat, params))
        db.cursors_used.add(id(self))
        self._rows = []
        if flat.startswith("SELECT id FROM users WHERE username"):
            self._rows = [{"id": u["id"]} for u in db.users.values() if u["username"] == params[0]]
        elif flat.startswith("INSERT INTO users"):
            uid = db.next_id
            db.next_id += 1
            role = "admin" if "'admin'" in flat else params[6]
            db.users[uid] = {"id": uid, "username": params[0], "role": role, "is_active": 1}
            self.lastrowid = uid if db.forced_lastrowid == "auto" else db.forced_lastrowid
        elif flat.startswith("SELECT * FROM users WHERE id"):
            u = db.users.get(params[0])
            self._rows = [dict(u, password_hash="h", full_name=None)] if u else []
        elif flat.startswith("SELECT id, role FROM users WHERE id"):
            u = db.users.get(params[0])
            self._rows = [{"id": u["id"], "role": u["role"]}] if u else []
        elif flat.startswith("SELECT id FROM access_groups WHERE group_key = %s AND is_system = 1"):
            g = db.groups.get(params[0])
            self._rows = [{"id": g["id"]}] if g and g["is_system"] == 1 else []
        elif flat.startswith("SELECT u.id AS id FROM users u JOIN user_access_groups"):
            assert flat.endswith("FOR UPDATE")
            admin_gid = db.groups["admin"]["id"] if db.groups["admin"]["is_system"] == 1 else None
            self._rows = [
                {"id": u}
                for (u, g) in sorted(db.members)
                if g == admin_gid and db.users[u]["is_active"] == 1
            ]
        elif flat.startswith("SELECT 1 AS member FROM user_access_groups uag JOIN access_groups g"):
            g = db.groups.get(params[1])
            self._rows = (
                [{"member": 1}]
                if g and g["is_system"] == 1 and (params[0], g["id"]) in db.members
                else []
            )
        elif flat == MEMBERSHIP_INSERT:
            # como o banco: FK obrigatoria; par repetido nao duplica (ON DUPLICATE KEY)
            if db.fail_membership_insert is not None:
                raise db.fail_membership_insert
            if params[0] not in db.users or params[1] not in {g["id"] for g in db.groups.values()}:
                raise RuntimeError(f"FK violada em user_access_groups: {params!r}")
            db.members.add((params[0], params[1]))
        elif flat.startswith("DELETE FROM user_access_groups"):
            system_ids = {g["id"] for g in db.groups.values() if g["is_system"] == 1}
            keep = params[1] if "id <> %s" in flat else None
            db.members = {
                (u, g) for (u, g) in db.members if not (u == params[0] and g in system_ids and g != keep)
            }
        elif flat.startswith("UPDATE users SET full_name = COALESCE"):
            u = db.users[params[-1]]
            if params[4] is not None:
                u["role"] = params[4]
            if params[5] is not None:
                u["is_active"] = int(params[5])
        else:
            raise AssertionError(f"SQL inesperado no fake: {flat}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


@pytest.fixture
def db(monkeypatch):
    d = FakeDb()
    monkeypatch.setattr(users, "transaction", d.transaction)
    monkeypatch.setattr(auth, "transaction", d.transaction)
    monkeypatch.setattr(users, "hash_password", lambda p: "hash-" + p)
    monkeypatch.setattr(auth, "hash_password", lambda p: "hash-" + p)
    return d


def _create(role, username="novo"):
    return users.create_user(UserCreateIn(username=username, password="senha-longa-123", role=role), request=_request(), identity=ADMIN_ACTOR)


# 1 -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,group_key", [("admin", "admin"), ("personal_trainer", "personal_trainer"), ("regular", "usuario")]
)
def test_create_user_associa_grupo_de_sistema_do_papel_na_mesma_transacao(db, role, group_key):
    result = _create(role)
    uid = result["item"]["id"]
    assert {g for (u, g) in db.members if u == uid} == {GROUP_IDS[group_key]}
    inserts = db.sqls(MEMBERSHIP_PREFIX)
    assert [p for _, p in inserts] == [(uid, GROUP_IDS[group_key], ADMIN_ACTOR.user_id)]
    # uma unica transacao e um unico cursor para INSERT users e associacao
    assert db.transactions == 1 and db.commits == 1 and db.rollbacks == 0
    assert len(db.cursors_used) == 1
    idx_users = next(i for i, (s, _) in enumerate(db.log) if s.startswith("INSERT INTO users"))
    idx_uag = next(i for i, (s, _) in enumerate(db.log) if s.startswith(MEMBERSHIP_PREFIX))
    assert idx_users < idx_uag


# 2 -------------------------------------------------------------------------


def test_create_user_com_grupo_ausente_desfaz_a_criacao(db):
    del db.groups["usuario"]
    with pytest.raises(MissingSystemGroupError):
        _create("regular")
    assert db.commits == 0 and db.rollbacks == 1
    assert list(db.users) == [1]  # usuario novo nao ficou
    assert db.sqls(MEMBERSHIP_PREFIX) == []


def test_rota_post_users_nao_devolve_sucesso_com_grupo_ausente(db):
    from fastapi.testclient import TestClient

    from app import main
    from app.security import get_current_identity

    del db.groups["usuario"]
    main.app.dependency_overrides[get_current_identity] = lambda: ADMIN_ACTOR
    try:
        client = TestClient(main.app, raise_server_exceptions=False)
        resp = client.post("/users", json={"username": "novo", "password": "senha-longa-123", "role": "regular"})
    finally:
        main.app.dependency_overrides.pop(get_current_identity, None)
    assert resp.status_code >= 400
    assert db.commits == 0 and list(db.users) == [1]


def test_grupo_com_is_system_zero_nao_serve_de_grupo_de_papel(db):
    db.groups["usuario"]["is_system"] = 0
    with pytest.raises(MissingSystemGroupError):
        _create("regular")
    assert db.commits == 0


# 3 -------------------------------------------------------------------------


def test_troca_de_papel_remove_grupo_antigo_insere_novo_e_preserva_grupo_custom(db):
    db.users[5] = {"id": 5, "username": "x", "role": "regular", "is_active": 1}
    db.members |= {(5, GROUP_IDS["usuario"]), (5, GROUP_IDS["equipe_x"]), (5, GROUP_IDS["free"])}
    users.update_user(5, UserUpdateIn(role="personal_trainer"), request=_request(), identity=ADMIN_ACTOR)
    got = {g for (u, g) in db.members if u == 5}
    assert got == {GROUP_IDS["personal_trainer"], GROUP_IDS["equipe_x"], GROUP_IDS["free"]}
    assert db.users[5]["role"] == "personal_trainer"
    assert (1, GROUP_IDS["admin"]) in db.members  # outros usuarios intactos
    ins = db.sqls(MEMBERSHIP_PREFIX)
    assert [p for _, p in ins] == [(5, GROUP_IDS["personal_trainer"], ADMIN_ACTOR.user_id)]


def test_put_sem_mudanca_de_papel_nao_mexe_em_grupos(db):
    db.users[5] = {"id": 5, "username": "x", "role": "regular", "is_active": 1}
    db.members |= {(5, GROUP_IDS["usuario"])}
    before = set(db.members)
    users.update_user(5, UserUpdateIn(full_name="Novo Nome"), request=_request(), identity=ADMIN_ACTOR)
    users.update_user(5, UserUpdateIn(role="regular"), request=_request(), identity=ADMIN_ACTOR)  # mesmo papel
    assert db.members == before
    assert db.sqls(MEMBERSHIP_PREFIX) == []
    assert db.sqls("DELETE FROM user_access_groups") == []
    assert db.sqls("SELECT id FROM access_groups") == []


def test_troca_de_papel_com_grupo_novo_ausente_desfaz_o_update_de_papel(db):
    db.users[5] = {"id": 5, "username": "x", "role": "regular", "is_active": 1}
    db.members |= {(5, GROUP_IDS["usuario"])}
    del db.groups["personal_trainer"]
    with pytest.raises(MissingSystemGroupError):
        users.update_user(5, UserUpdateIn(role="personal_trainer"), request=_request(), identity=ADMIN_ACTOR)
    assert db.users[5]["role"] == "regular"
    assert (5, GROUP_IDS["usuario"]) in db.members
    assert db.rollbacks == 1


# 4 -------------------------------------------------------------------------


def test_seed_cria_admin_com_grupo_admin_na_mesma_transacao(db):
    db.users.clear()
    db.members.clear()
    auth.ensure_admin_seed()
    seeded = [u for u in db.users.values() if u["username"] == auth.ADMIN_SEED_USERNAME]
    assert len(seeded) == 1 and seeded[0]["role"] == "admin"
    assert db.members == {(seeded[0]["id"], GROUP_IDS["admin"])}
    ins = db.sqls(MEMBERSHIP_PREFIX)
    assert [p for _, p in ins] == [(seeded[0]["id"], GROUP_IDS["admin"], None)]
    assert db.transactions == 1 and db.commits == 1 and len(db.cursors_used) == 1


def test_seed_com_usuario_existente_nao_toca_grupos(db):
    db.users[7] = {"id": 7, "username": auth.ADMIN_SEED_USERNAME, "role": "admin", "is_active": 1}
    before = set(db.members)
    auth.ensure_admin_seed()
    assert db.members == before
    assert db.sqls("INSERT") == [] and db.sqls("DELETE") == []


def test_seed_com_grupo_ausente_nao_cria_admin_sem_grupo_e_nao_levanta(db):
    db.users.clear()
    db.members.clear()
    del db.groups["admin"]
    auth.ensure_admin_seed()  # nao levanta
    assert db.users == {} and db.members == set()
    assert db.rollbacks == 1 and db.commits == 0


# 5 -------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _mig111_globals() -> dict:
    return m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "111_access_groups_rbac.py").__globals__


def test_paridade_com_role_to_group_da_migracao_111():
    assert dict(_mig111_globals()["ROLE_TO_GROUP"]) == ROLE_TO_SYSTEM_GROUP
    assert set(ROLE_TO_SYSTEM_GROUP) == {"admin", "personal_trainer", "regular"}


# 6 -------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["superuser", "", None, "ADMIN", 3])
def test_papel_desconhecido_nao_insere_em_assign_e_switch(role):
    d = FakeDb()
    cur = _Cursor(d)
    assert membership.system_group_key_for_role(role) is None
    assert assign_role_group(cur, 5, role, 1) is None
    assert d.sqls("INSERT") == []
    d.members |= {(5, GROUP_IDS["usuario"]), (5, GROUP_IDS["equipe_x"])}
    assert switch_role_group(cur, 5, role, 1) is None
    assert d.sqls("INSERT") == []
    # desconhecido remove os grupos de sistema e preserva os demais
    assert {g for (u, g) in d.members if u == 5} == {GROUP_IDS["equipe_x"]}


def test_assign_role_group_devolve_a_chave_e_grava_uma_linha():
    d = FakeDb()
    d.users[5] = {"id": 5, "username": "x", "role": "regular", "is_active": 1}
    cur = _Cursor(d)
    assert assign_role_group(cur, 5, "regular", 1) == "usuario"
    assert (5, GROUP_IDS["usuario"]) in d.members


# 7: user_id invalido, lastrowid 0/None, FK e idempotencia ------------------------


@pytest.mark.parametrize("bad_id", [None, 0, -1, True], ids=["none", "zero", "negativo", "bool"])
@pytest.mark.parametrize("fn", [assign_role_group, switch_role_group], ids=["assign", "switch"])
def test_user_id_invalido_levanta_e_nao_emite_statement(fn, bad_id):
    d = FakeDb()
    cur = _Cursor(d)
    with pytest.raises(InvalidMembershipTargetError):
        fn(cur, bad_id, "regular", 1)
    assert d.log == []
    assert d.members == {(1, 1)}


def test_invalid_membership_target_error_e_exportado_por_app_access():
    from app import access

    assert access.InvalidMembershipTargetError is InvalidMembershipTargetError


@pytest.mark.parametrize("bad", [0, None], ids=["zero", "none"])
def test_create_user_com_lastrowid_invalido_desfaz_tudo(db, bad):
    db.forced_lastrowid = bad
    with pytest.raises(InvalidMembershipTargetError):
        _create("regular")
    assert db.commits == 0 and db.rollbacks == 1
    assert list(db.users) == [1]  # usuario novo nao ficou gravado
    assert db.members == {(1, 1)}
    assert db.sqls(MEMBERSHIP_PREFIX) == []


@pytest.mark.parametrize("bad", [0, None], ids=["zero", "none"])
def test_rota_post_users_com_lastrowid_invalido_nao_devolve_sucesso(db, bad):
    from fastapi.testclient import TestClient

    from app import main
    from app.security import get_current_identity

    db.forced_lastrowid = bad
    main.app.dependency_overrides[get_current_identity] = lambda: ADMIN_ACTOR
    try:
        client = TestClient(main.app, raise_server_exceptions=False)
        resp = client.post("/users", json={"username": "novo", "password": "senha-longa-123", "role": "regular"})
    finally:
        main.app.dependency_overrides.pop(get_current_identity, None)
    assert resp.status_code >= 400
    assert db.commits == 0 and list(db.users) == [1] and db.members == {(1, 1)}


@pytest.mark.parametrize("bad", [0, None], ids=["zero", "none"])
def test_seed_com_lastrowid_invalido_nao_levanta_e_nao_cria_admin(db, bad):
    db.users.clear()
    db.members.clear()
    db.forced_lastrowid = bad
    auth.ensure_admin_seed()  # nao levanta
    assert db.users == {} and db.members == set()
    assert db.rollbacks == 1 and db.commits == 0
    assert db.sqls(MEMBERSHIP_PREFIX) == []


def test_erro_de_fk_na_associacao_desfaz_a_criacao_do_usuario(db):
    db.fail_membership_insert = RuntimeError("FK violada")
    with pytest.raises(RuntimeError, match="FK violada"):
        _create("regular")
    assert db.commits == 0 and db.rollbacks == 1
    assert list(db.users) == [1] and db.members == {(1, 1)}


def test_erro_de_fk_na_associacao_desfaz_a_troca_de_papel(db):
    db.users[5] = {"id": 5, "username": "x", "role": "regular", "is_active": 1}
    db.members |= {(5, GROUP_IDS["usuario"])}
    db.fail_membership_insert = RuntimeError("FK violada")
    with pytest.raises(RuntimeError, match="FK violada"):
        users.update_user(5, UserUpdateIn(role="personal_trainer"), request=_request(), identity=ADMIN_ACTOR)
    assert db.commits == 0 and db.rollbacks == 1
    assert db.users[5]["role"] == "regular"
    assert {g for (u, g) in db.members if u == 5} == {GROUP_IDS["usuario"]}


def test_fake_simula_fk_para_user_id_inexistente():
    d = FakeDb()
    with pytest.raises(RuntimeError, match="FK"):
        assign_role_group(_Cursor(d), 999, "regular", 1)
    assert not [m_ for m_ in d.members if m_[0] == 999]


def test_associar_duas_vezes_o_mesmo_par_nao_duplica_nem_levanta():
    d = FakeDb()
    d.users[5] = {"id": 5, "username": "x", "role": "regular", "is_active": 1}
    cur = _Cursor(d)
    assert assign_role_group(cur, 5, "regular", 1) == "usuario"
    assert assign_role_group(cur, 5, "regular", 1) == "usuario"
    assert switch_role_group(cur, 5, "regular", 1) == "usuario"
    assert [m_ for m_ in d.members if m_[0] == 5] == [(5, GROUP_IDS["usuario"])]
    assert len(d.sqls(MEMBERSHIP_PREFIX)) == 3


def test_insert_ignore_e_sql_inesperado_no_fake():
    d = FakeDb()
    with pytest.raises(AssertionError, match="SQL inesperado"):
        _Cursor(d).execute("INSERT IGNORE INTO user_access_groups (user_id, group_id) VALUES (%s, %s)", (1, 3))

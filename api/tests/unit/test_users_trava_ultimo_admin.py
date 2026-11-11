"""Trava do ultimo admin ativo (ADR 0011, pre-condicoes do lote 2) em
`PUT /users/{id}` (troca de papel e is_active=false) e `DELETE /users/{id}`.

O banco falso avalia as condicoes do proprio SQL recebido (`is_active = 1`,
`is_system = 1`, `group_key = %s`) e devolve so as colunas pedidas; assim o teste
quebra se a consulta da trava deixar de filtrar ativo/grupo de sistema.

Rodar: cd api && python -m pytest tests/unit/test_users_trava_ultimo_admin.py
"""

from __future__ import annotations

import copy
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from app import users
from app.request_models import UserUpdateIn
from app.security import Identity

ADMIN_GID, PT_GID, USUARIO_GID, FAKE_ADMIN_GID = 1, 2, 3, 8
TARGET_LOCK_PUT = "SELECT id, role FROM users WHERE id = %s FOR UPDATE"
TARGET_LOCK_DELETE = "SELECT id FROM users WHERE id = %s FOR UPDATE"
# Texto exato esperado; um `INSERT IGNORE` cai no ramo "SQL inesperado" e o teste falha.
MEMBERSHIP_INSERT = (
    "INSERT INTO user_access_groups (user_id, group_id, created_by_user_id) VALUES (%s, %s, %s) "
    "ON DUPLICATE KEY UPDATE user_id = user_id"
)


def _user(uid, role="regular", active=1):
    return {"id": uid, "username": f"u{uid}", "role": role, "is_active": active}


class FakeDb:
    """users + access_groups + user_access_groups com rollback na excecao."""

    def __init__(self, users_, members, groups=None):
        self.users = users_
        self.members = set(members)  # (user_id, group_id)
        # id -> (group_key, is_system); pode haver outro grupo com group_key 'admin' nao de sistema
        self.groups = groups or {
            ADMIN_GID: ("admin", 1),
            PT_GID: ("personal_trainer", 1),
            USUARIO_GID: ("usuario", 1),
        }
        self.log: list[tuple[int, str, tuple | None]] = []  # (transacao, sql, params)
        self.tx = 0
        self.commits = 0
        self.rollbacks = 0
        # simula commit concorrente que chega antes de o lock do alvo ser concedido
        self.on_target_lock = None

    @contextmanager
    def transaction(self):
        self.tx += 1
        snap = copy.deepcopy((self.users, self.members))
        try:
            yield _Conn(self, self.tx)
        except BaseException:
            self.users, self.members = snap
            self.rollbacks += 1
            raise
        self.commits += 1

    def writes(self):
        """Statements de escrita em users / user_access_groups."""
        prefixes = ("UPDATE users", "INSERT INTO user_access_groups", "DELETE FROM user_access_groups")
        return [e for e in self.log if e[1].startswith(prefixes)]

    def lock_selects(self):
        return [e for e in self.log if e[1].endswith("FOR UPDATE")]

    def target_locks(self):
        """Lock da linha do alvo (`SELECT id[, role] FROM users WHERE id = %s FOR UPDATE`)."""
        return [e for e in self.lock_selects() if e[1] in (TARGET_LOCK_PUT, TARGET_LOCK_DELETE)]

    def admin_locks(self):
        """Lock dos admins ativos (join users x user_access_groups)."""
        return [e for e in self.lock_selects() if e[1].startswith("SELECT u.id AS id FROM users u JOIN")]


class _Conn:
    def __init__(self, db, tx):
        self.cur = _Cursor(db, tx)

    def cursor(self):
        return self.cur


class _Cursor:
    def __init__(self, db, tx):
        self.db = db
        self.tx = tx
        self._rows: list[dict] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        db = self.db
        flat = " ".join(sql.split())
        db.log.append((self.tx, flat, params))
        self._rows = []
        self.rowcount = 0
        if flat in (TARGET_LOCK_PUT, TARGET_LOCK_DELETE):
            if db.on_target_lock:
                db.on_target_lock(db, params[0])
            u = db.users.get(params[0])
            cols = ("id", "role") if flat == TARGET_LOCK_PUT else ("id",)
            self._rows = [{c: u[c] for c in cols}] if u else []
        elif flat.startswith("SELECT u.id AS id FROM users u JOIN user_access_groups"):
            assert flat.endswith("FOR UPDATE")
            only_active = "u.is_active = 1" in flat
            only_system = "g.is_system = 1" in flat
            self._rows = [
                {"id": uid}
                for (uid, gid) in sorted(db.members)
                if db.groups[gid][0] == params[0]
                and (db.groups[gid][1] == 1 or not only_system)
                and (db.users[uid]["is_active"] == 1 or not only_active)
            ]
        elif flat.startswith("SELECT id FROM access_groups WHERE group_key = %s AND is_system = 1"):
            self._rows = [{"id": gid} for gid, (k, s) in db.groups.items() if k == params[0] and s == 1]
        elif flat == MEMBERSHIP_INSERT:
            # como o banco: FK em users/access_groups; par repetido nao duplica (set + ON DUPLICATE KEY)
            if params[0] not in db.users or params[1] not in db.groups:
                raise RuntimeError(f"FK violada em user_access_groups: {params!r}")
            db.members.add((params[0], params[1]))
        elif flat.startswith("DELETE FROM user_access_groups"):
            system_ids = {gid for gid, (_, s) in db.groups.items() if s == 1}
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
            self.rowcount = 1
        elif flat.startswith("UPDATE users SET is_active = 0 WHERE id"):
            u = db.users.get(params[0])
            if u:
                u["is_active"] = 0
                self.rowcount = 1
        elif flat.startswith("SELECT * FROM users WHERE id"):
            u = db.users.get(params[0])
            self._rows = [dict(u, password_hash="h", full_name=None)] if u else []
        else:
            raise AssertionError(f"SQL inesperado no fake: {flat}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _install(monkeypatch, db):
    monkeypatch.setattr(users, "transaction", db.transaction)
    return db


def _sole_admin_db(extra_users=()):
    us = {1: _user(1, "admin"), 5: _user(5, "regular"), **{u["id"]: u for u in extra_users}}
    return FakeDb(us, {(1, ADMIN_GID), (5, USUARIO_GID)})


def _two_admins_db():
    us = {1: _user(1, "admin"), 2: _user(2, "admin"), 5: _user(5, "regular")}
    return FakeDb(us, {(1, ADMIN_GID), (2, ADMIN_GID), (5, USUARIO_GID)})


OTHER_ADMIN_ACTOR = Identity(user_id=99, role="admin", source="session")  # ex.: token mestre / admin fora do grupo
SELF_ACTOR = Identity(user_id=1, role="admin", source="session")


def _put(uid, actor, **fields):
    return users.update_user(uid, UserUpdateIn(**fields), identity=actor)


def _delete(uid, actor):
    return users.deactivate_user(uid, identity=actor)


def _expect_last_admin(call):
    with pytest.raises(HTTPException) as exc:
        call()
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "last_admin"
    assert isinstance(exc.value.detail["message"], str) and exc.value.detail["message"]


# 7 + 13 --------------------------------------------------------------------

LAST_ADMIN_CASES = {
    "put_role_regular_por_outro": lambda: _put(1, OTHER_ADMIN_ACTOR, role="regular"),
    "put_role_pt_por_outro": lambda: _put(1, OTHER_ADMIN_ACTOR, role="personal_trainer"),
    "put_role_regular_no_proprio_papel": lambda: _put(1, SELF_ACTOR, role="regular"),
    "put_is_active_false_por_outro": lambda: _put(1, OTHER_ADMIN_ACTOR, is_active=False),
    "delete_por_outro": lambda: _delete(1, OTHER_ADMIN_ACTOR),
}


@pytest.mark.parametrize("case", list(LAST_ADMIN_CASES))
def test_unico_admin_ativo_recusa_com_409_last_admin_sem_escrever(monkeypatch, case):
    db = _install(monkeypatch, _sole_admin_db())
    before = (copy.deepcopy(db.users), set(db.members))
    _expect_last_admin(LAST_ADMIN_CASES[case])
    # 13: nenhuma escrita foi emitida e o estado nao mudou
    assert db.writes() == []
    assert (db.users, db.members) == before
    assert len(db.target_locks()) == 1 and len(db.admin_locks()) == 1
    assert db.commits == 0 and db.rollbacks == 1


def test_autodesativacao_por_put_e_por_delete_devolve_400_sem_tocar_o_banco(monkeypatch):
    db = _install(monkeypatch, _sole_admin_db())
    with pytest.raises(HTTPException) as put_exc:
        _put(1, SELF_ACTOR, is_active=False)
    with pytest.raises(HTTPException) as del_exc:
        _delete(1, SELF_ACTOR)
    assert put_exc.value.status_code == 400
    assert del_exc.value.status_code == 400
    assert put_exc.value.detail == del_exc.value.detail == users._SELF_DEACTIVATE_ERROR
    assert db.log == []  # recusa antes de abrir qualquer statement


def test_a_trava_vale_pela_rota_http_com_corpo_estruturado(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    from app.security import require_admin

    db = _install(monkeypatch, _sole_admin_db())
    main.app.dependency_overrides[require_admin] = lambda: OTHER_ADMIN_ACTOR
    try:
        client = TestClient(main.app, raise_server_exceptions=False)
        resp_put = client.put("/users/1", json={"role": "regular"})
        resp_del = client.delete("/users/1")
    finally:
        main.app.dependency_overrides.pop(require_admin, None)
    for resp in (resp_put, resp_del):
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "last_admin"
    assert db.writes() == []


# 8 -------------------------------------------------------------------------


def test_com_dois_admins_ativos_troca_de_papel_passa(monkeypatch):
    db = _install(monkeypatch, _two_admins_db())
    out = _put(2, SELF_ACTOR, role="regular")
    assert out["status"] == "ok"
    assert db.users[2]["role"] == "regular"
    assert {g for (u, g) in db.members if u == 2} == {USUARIO_GID}


def test_com_dois_admins_ativos_put_is_active_false_passa(monkeypatch):
    db = _install(monkeypatch, _two_admins_db())
    assert _put(2, SELF_ACTOR, is_active=False)["status"] == "ok"
    assert db.users[2]["is_active"] == 0


def test_com_dois_admins_ativos_delete_passa(monkeypatch):
    db = _install(monkeypatch, _two_admins_db())
    assert _delete(2, SELF_ACTOR) == {"status": "ok", "id": 2, "is_active": False}
    assert db.users[2]["is_active"] == 0


def test_admin_agindo_sobre_si_mesmo_na_troca_de_papel_passa_com_outro_admin_ativo(monkeypatch):
    db = _install(monkeypatch, _two_admins_db())
    _put(1, SELF_ACTOR, role="personal_trainer")
    assert db.users[1]["role"] == "personal_trainer"
    assert {g for (u, g) in db.members if u == 1} == {PT_GID}


# 9 -------------------------------------------------------------------------


def test_admin_desativado_com_linha_no_grupo_nao_conta_como_ativo(monkeypatch):
    db = _install(monkeypatch, _sole_admin_db([_user(2, "admin", active=0)]))
    db.members.add((2, ADMIN_GID))
    _expect_last_admin(lambda: _delete(1, OTHER_ADMIN_ACTOR))
    _expect_last_admin(lambda: _put(1, OTHER_ADMIN_ACTOR, role="regular"))
    assert db.writes() == []


def test_desativar_admin_ja_inativo_nao_e_barrado_pela_trava(monkeypatch):
    db = _install(monkeypatch, _sole_admin_db([_user(2, "admin", active=0)]))
    db.members.add((2, ADMIN_GID))
    assert _delete(2, OTHER_ADMIN_ACTOR)["status"] == "ok"


# 10 ------------------------------------------------------------------------


def test_membro_de_grupo_admin_nao_de_sistema_nao_conta(monkeypatch):
    db = _install(monkeypatch, _sole_admin_db([_user(2, "regular", active=1)]))
    db.groups[FAKE_ADMIN_GID] = ("admin", 0)  # group_key 'admin' mas is_system = 0
    db.members.add((2, FAKE_ADMIN_GID))
    _expect_last_admin(lambda: _delete(1, OTHER_ADMIN_ACTOR))
    assert db.writes() == []


# 11 ------------------------------------------------------------------------


def _assert_lock_order(db, target_sql, expect_admin_lock):
    """Alvo FOR UPDATE e o 1o statement; admins depois do alvo e antes do UPDATE; mesma transacao."""
    first_tx, first_sql, _ = db.log[0]
    assert first_sql == target_sql and first_sql.endswith("FOR UPDATE")
    upd_idx = next(i for i, e in enumerate(db.log) if e[1].startswith("UPDATE users"))
    assert len(db.target_locks()) == 1
    if expect_admin_lock:
        admin_idx = next(i for i, e in enumerate(db.log) if e in db.admin_locks())
        admin_sql = db.log[admin_idx][1]
        assert 0 < admin_idx < upd_idx
        assert "is_active = 1" in admin_sql and "user_access_groups" in admin_sql
        assert db.log[admin_idx][0] == first_tx
    else:
        assert db.admin_locks() == []
    assert db.log[upd_idx][0] == first_tx
    assert {e[0] for e in db.log} == {first_tx}
    assert db.commits == 1 and db.tx == 1 and db.rollbacks == 0


@pytest.mark.parametrize(
    ("call", "target_sql"),
    [
        (lambda: _put(2, SELF_ACTOR, role="regular"), TARGET_LOCK_PUT),
        (lambda: _put(2, SELF_ACTOR, is_active=False), TARGET_LOCK_PUT),
        (lambda: _delete(2, SELF_ACTOR), TARGET_LOCK_DELETE),
    ],
    ids=["put_role", "put_is_active", "delete"],
)
def test_lock_do_alvo_primeiro_depois_admins_depois_update_na_mesma_transacao(monkeypatch, call, target_sql):
    db = _install(monkeypatch, _two_admins_db())
    call()
    _assert_lock_order(db, target_sql, expect_admin_lock=True)


def test_update_user_le_o_alvo_com_for_update_exato(monkeypatch):
    """Falha se o SELECT do alvo perder o FOR UPDATE (ou deixar de ser o 1o statement)."""
    db = _install(monkeypatch, _sole_admin_db())
    _put(5, OTHER_ADMIN_ACTOR, full_name="X")
    assert db.log[0][1] == "SELECT id, role FROM users WHERE id = %s FOR UPDATE"
    assert db.log[0][2] == (5,)
    _assert_lock_order(db, TARGET_LOCK_PUT, expect_admin_lock=False)


def test_deactivate_user_trava_o_alvo_com_for_update_exato(monkeypatch):
    db = _install(monkeypatch, _two_admins_db())
    _delete(2, SELF_ACTOR)
    assert db.log[0][1] == "SELECT id FROM users WHERE id = %s FOR UPDATE"


def test_put_de_usuario_inexistente_devolve_404_sem_lock_de_admins_e_sem_update(monkeypatch):
    db = _install(monkeypatch, _sole_admin_db())
    with pytest.raises(HTTPException) as exc:
        _put(777, OTHER_ADMIN_ACTOR, role="regular")
    assert exc.value.status_code == 404
    assert len(db.target_locks()) == 1 and db.admin_locks() == []
    assert db.writes() == [] and db.rollbacks == 1 and db.commits == 0


def _demote_user1_concurrently(db, uid):
    """Outro PUT ja commitou: 1 deixou de ser admin (papel + grupo) antes do nosso lock."""
    if uid == 1 and db.users[1]["role"] == "admin":
        db.users[1]["role"] = "regular"
        db.members.discard((1, ADMIN_GID))
        db.members.add((1, USUARIO_GID))


def _promote_user2_concurrently(db, uid):
    """Outro PUT ja commitou: 2 virou admin (papel + grupo) antes do nosso lock."""
    if uid == 2 and db.users[2]["role"] != "admin":
        db.users[2]["role"] = "admin"
        db.members.discard((2, USUARIO_GID))
        db.members.add((2, ADMIN_GID))


def test_papel_mudou_entre_requisicoes_alvo_ja_rebaixado_sob_lock_nao_dispara_trava_nem_troca_de_grupo(monkeypatch):
    # unico admin; um PUT concorrente ja o rebaixou. O nosso PUT (role=regular) le 'regular' sob lock:
    # sem mudanca de papel, sem trava e sem mexer em grupos.
    db = _install(monkeypatch, _sole_admin_db())
    db.on_target_lock = _demote_user1_concurrently
    out = _put(1, OTHER_ADMIN_ACTOR, role="regular")
    assert out["status"] == "ok"
    assert db.admin_locks() == []
    assert not [e for e in db.writes() if "user_access_groups" in e[1]]
    assert db.users[1]["role"] == "regular"
    assert {g for (u, g) in db.members if u == 1} == {USUARIO_GID}


def test_papel_mudou_entre_requisicoes_alvo_promovido_sob_lock_e_unico_admin_recusa_com_409(monkeypatch):
    # 2 era 'regular' quando a requisicao chegou; ao obter o lock ja e o unico admin ativo.
    # A decisao usa o papel lido sob lock: rebaixar o ultimo admin e recusado, sem escrita.
    us = {2: _user(2, "regular"), 5: _user(5, "regular")}
    db = _install(monkeypatch, FakeDb(us, {(2, USUARIO_GID), (5, USUARIO_GID)}))
    db.on_target_lock = _promote_user2_concurrently
    _expect_last_admin(lambda: _put(2, OTHER_ADMIN_ACTOR, role="regular"))
    assert db.writes() == []
    assert len(db.admin_locks()) == 1
    assert db.rollbacks == 1 and db.commits == 0


# 12 ------------------------------------------------------------------------


def test_desativar_usuario_comum_nao_e_barrado_mesmo_com_um_unico_admin(monkeypatch):
    db = _install(monkeypatch, _sole_admin_db())
    assert _delete(5, OTHER_ADMIN_ACTOR)["status"] == "ok"
    assert _put(5, OTHER_ADMIN_ACTOR, role="personal_trainer")["status"] == "ok"
    assert db.users[5]["is_active"] == 0
    assert db.users[1]["is_active"] == 1


def test_delete_de_usuario_comum_trava_o_alvo_depois_os_admins_e_aplica_a_desativacao(monkeypatch):
    db = _install(monkeypatch, _sole_admin_db())
    _delete(5, OTHER_ADMIN_ACTOR)
    # DELETE sempre passa pela trava (que decide dentro do lock dos admins): alvo primeiro, admins depois
    assert db.log[0][1] == TARGET_LOCK_DELETE
    assert len(db.target_locks()) == 1 and len(db.admin_locks()) == 1
    assert db.log.index(db.admin_locks()[0]) > 0
    assert db.users[5]["is_active"] == 0


def test_trocar_papel_para_admin_nao_dispara_a_trava(monkeypatch):
    db = _install(monkeypatch, _sole_admin_db())
    _put(5, OTHER_ADMIN_ACTOR, role="admin")
    assert len(db.target_locks()) == 1 and db.admin_locks() == []
    assert db.users[5]["role"] == "admin"
    assert {g for (u, g) in db.members if u == 5} == {ADMIN_GID}


def test_reativar_e_editar_perfil_nao_disparam_a_trava(monkeypatch):
    db = _install(monkeypatch, _sole_admin_db([_user(2, "regular", active=0)]))
    _put(2, OTHER_ADMIN_ACTOR, is_active=True)
    _put(1, OTHER_ADMIN_ACTOR, full_name="Novo Nome")
    _put(1, OTHER_ADMIN_ACTOR, role="admin")  # mesmo papel
    assert len(db.target_locks()) == 3 and db.admin_locks() == []
    assert db.users[2]["is_active"] == 1


def test_delete_de_usuario_inexistente_404_sem_lock_de_admins_e_sem_update(monkeypatch):
    db = _install(monkeypatch, _sole_admin_db())
    with pytest.raises(HTTPException) as exc:
        _delete(777, OTHER_ADMIN_ACTOR)
    assert exc.value.status_code == 404
    assert len(db.target_locks()) == 1 and db.admin_locks() == []
    assert db.writes() == [] and db.rollbacks == 1 and db.commits == 0

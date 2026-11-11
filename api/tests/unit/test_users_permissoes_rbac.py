"""RBAC do CRUD de usuarios (ADR 0011, lote 2B): rotas HTTP com TestClient.

O resolvedor de permissoes REAL roda (`access.service.resolve_permissions` ->
`merge_grants`) sobre um banco falso: `FakePermissoesDb` responde a consulta de
concessoes (colunas `group_key, is_system, resource, action, scope`) a partir de grupos
e membros, e conta as consultas. O banco de usuarios (`FakeUsersDb`) avalia o proprio SQL
recebido e PROJETA so as colunas pedidas (uma consulta que deixe de pedir uma coluna
quebra o teste). Nenhum rollback e simulado alem do snapshot da transacao; a prova de
"nada foi escrito" olha o log de statements, nao o estado final.

Provas: grupo so com `admin.usuarios` (sem `admin.grupos`) nao cria/promove/rebaixa
admin e nao emite escrita; o mesmo grupo gere usuario comum; admin de sistema e grupo
com as duas permissoes fazem tudo; usuario comum, PT e usuario sem grupo recebem 403 nas
5 rotas administrativas; fotos (proprio sem consulta de permissoes, terceiro 403, admin
passa, escopo `alunos` nao basta, id 0/negativo = 400 `invalid_target`); falha do banco
na resolucao = 503.

Rodar: cd api && python -m pytest tests/unit/test_users_permissoes_rbac.py
"""

from __future__ import annotations

import copy
import re
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from app import users
from app.access import DEFAULT_GRANTS_BY_GROUP
from app.access import service as access_service
from app.security import Identity, get_current_identity

# ---------------------------------------------------------------------------
# Grupos e concessoes do cenario
# ---------------------------------------------------------------------------

GID = {
    "admin": 1,
    "personal_trainer": 2,
    "usuario": 3,
    "gestor_usuarios": 10,
    "gestor_total": 11,
    "so_alunos": 12,
    "free": 13,
}
GRUPOS = {  # id -> (group_key, is_system)
    1: ("admin", 1),
    2: ("personal_trainer", 1),
    3: ("usuario", 1),
    10: ("gestor_usuarios", 0),
    11: ("gestor_total", 0),
    12: ("so_alunos", 0),
    13: ("free", 0),
}
CONCESSOES_CUSTOM = {
    # So admin.usuarios (todos), sem admin.grupos.
    "gestor_usuarios": [("admin.usuarios", a, "todos") for a in ("ver", "editar", "excluir")],
    "gestor_total": [("admin.usuarios", a, "todos") for a in ("ver", "editar", "excluir")]
    + [("admin.grupos", "editar", "todos")],
    "so_alunos": [("admin.usuarios", "ver", "alunos"), ("admin.usuarios", "editar", "alunos")],
    "free": [],
}


class FakePermissoesDb:
    """Substitui `app.db` dentro de `access.service`: responde so a consulta de concessoes."""

    def __init__(self, db: "FakeUsersDb | None" = None, falha: Exception | None = None):
        self.db = db
        self.falha = falha
        self.consultas: list[tuple[str, tuple]] = []

    def fetch_rows(self, sql, params=()):
        self.consultas.append((sql, tuple(params)))
        if self.falha is not None:
            raise self.falha
        assert "FROM user_access_groups m" in sql, f"consulta inesperada: {sql}"
        (user_id,) = params
        rows = []
        for uid, gid in sorted(self.db.members):
            if uid != user_id:
                continue
            chave, sistema = GRUPOS[gid]
            grants = DEFAULT_GRANTS_BY_GROUP.get(chave, ()) if sistema else CONCESSOES_CUSTOM[chave]
            if not grants:
                rows.append({"group_key": chave, "is_system": sistema, "resource": None, "action": None, "scope": None})
            for resource, action, scope in grants:
                rows.append(
                    {"group_key": chave, "is_system": sistema, "resource": resource, "action": action, "scope": scope}
                )
        return rows


# ---------------------------------------------------------------------------
# Banco de usuarios falso (avalia o SQL recebido e projeta colunas)
# ---------------------------------------------------------------------------

_PROJECAO = re.compile(r"^SELECT (?P<cols>.+?) FROM users WHERE id = %s(?P<lock> FOR UPDATE)?$")
PREFIXOS_ESCRITA = (
    "INSERT INTO users",
    "UPDATE users",
    "INSERT INTO user_access_groups",
    "DELETE FROM user_access_groups",
    "UPDATE user_sessions",
)


def _linha(uid, role="regular", ativo=1, foto=None):
    return {
        "id": uid, "username": f"u{uid}", "role": role, "is_active": ativo, "full_name": None,
        "email": None, "password_hash": "h", "photo_path": foto,
    }


class FakeUsersDb:
    def __init__(self):
        self.users = {
            1: _linha(1, "admin"), 2: _linha(2), 3: _linha(3), 4: _linha(4), 5: _linha(5, "personal_trainer"),
            6: _linha(6), 7: _linha(7, "admin"), 8: _linha(8), 9: _linha(9), 14: _linha(14),
        }
        self.members = {
            (1, 1), (7, 1), (2, 10), (3, 11), (4, 3), (5, 2), (8, 3), (9, 12), (14, 13),
        }  # usuario 6 fica SEM grupo
        self.log: list[tuple[str, tuple | None]] = []
        self.next_id = 100
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self):
        snap = copy.deepcopy((self.users, self.members))
        try:
            yield SimpleNamespace(cursor=lambda: _Cursor(self))
        except BaseException:
            self.users, self.members = snap
            self.rollbacks += 1
            raise
        self.commits += 1

    def escritas(self) -> list[str]:
        return [sql for sql, _ in self.log if sql.startswith(PREFIXOS_ESCRITA)]

    def escritas_em_users_ou_grupos(self) -> list[str]:
        return [s for s in self.escritas() if not s.startswith("UPDATE user_sessions")]


class _Cursor:
    def __init__(self, db: FakeUsersDb):
        self.db = db
        self._rows: list[dict] = []
        self.rowcount = 0
        self.lastrowid = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def execute(self, sql, params=None):
        db = self.db
        flat = " ".join(sql.split())
        db.log.append((flat, params))
        self._rows, self.rowcount = [], 0
        proj = _PROJECAO.match(flat)
        if flat == "SELECT * FROM users ORDER BY username":
            self._rows = [dict(u) for u in sorted(db.users.values(), key=lambda u: u["username"])]
        elif flat == "SELECT id FROM users WHERE username = %s":
            self._rows = [{"id": u["id"]} for u in db.users.values() if u["username"] == params[0]]
        elif proj:
            u = db.users.get(params[0])
            cols = [c.strip() for c in proj.group("cols").split(",")]
            if u:
                self._rows = [dict(u) if cols == ["*"] else {c: u[c] for c in cols}]
        elif flat.startswith("SELECT 1 AS member FROM user_access_groups uag JOIN access_groups g"):
            self._rows = [
                {"member": 1} for (uid, gid) in sorted(db.members)
                if uid == params[0] and GRUPOS[gid] == (params[1], 1)
            ][:1]
        elif flat.startswith("INSERT INTO users ("):
            uid = db.next_id
            db.next_id += 1
            db.users[uid] = _linha(uid, params[6])
            db.users[uid]["username"] = params[0]
            self.lastrowid = uid
        elif flat.startswith("SELECT id FROM access_groups WHERE group_key = %s AND is_system = 1"):
            self._rows = [{"id": g} for g, (k, s) in GRUPOS.items() if k == params[0] and s == 1]
        elif flat.startswith("INSERT INTO user_access_groups"):
            db.members.add((params[0], params[1]))
        elif flat.startswith("DELETE FROM user_access_groups"):
            sistema = {g for g, (_, s) in GRUPOS.items() if s == 1}
            manter = params[1] if "id <> %s" in flat else None
            db.members = {(u, g) for (u, g) in db.members if not (u == params[0] and g in sistema and g != manter)}
        elif flat.startswith("SELECT u.id AS id FROM users u JOIN"):
            assert flat.endswith("ORDER BY u.id FOR UPDATE")
            self._rows = [
                {"id": uid} for (uid, gid) in sorted(db.members)
                if GRUPOS[gid] == ("admin", 1) and db.users[uid]["is_active"] == 1
            ]
        elif flat.startswith("UPDATE users SET full_name = COALESCE"):
            u = db.users[params[-1]]
            if params[0] is not None:
                u["full_name"] = params[0]
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
        elif flat.startswith("UPDATE users SET password_hash"):
            self.rowcount = 1
        elif flat.startswith("UPDATE user_sessions"):
            self.rowcount = 0
        elif flat.startswith("UPDATE users SET photo_path"):
            if flat == "UPDATE users SET photo_path = NULL WHERE id = %s":
                db.users[params[0]]["photo_path"] = None
            else:
                db.users[params[1]]["photo_path"] = params[0]
            self.rowcount = 1
        else:
            raise AssertionError(f"SQL inesperado no fake: {flat}")


# ---------------------------------------------------------------------------
# Cenario
# ---------------------------------------------------------------------------

ATORES = {
    "admin": Identity(user_id=1, role="admin", source="session"),
    "gestor": Identity(user_id=2, role="regular", source="session"),  # grupo so com admin.usuarios
    "gestor_total": Identity(user_id=3, role="regular", source="session"),  # + admin.grupos/editar
    "regular": Identity(user_id=4, role="regular", source="session"),
    "pt": Identity(user_id=5, role="personal_trainer", source="session"),
    "sem_grupo": Identity(user_id=6, role="regular", source="session"),
    "so_alunos": Identity(user_id=9, role="regular", source="session"),
    "mestre": Identity(user_id=1, role="admin", source="master_token"),
    # Papel 'admin' na linha de users, mas sem grupo nenhum: nao pode herdar acesso do papel.
    "admin_sem_grupo": Identity(user_id=6, role="admin", source="session"),
}
CORPO_NOVO = {"username": "novo", "password": "senha-longa-123", "role": "regular"}
ROTAS_ADMIN = [
    ("get", "/users", None),
    ("post", "/users", CORPO_NOVO),
    ("put", "/users/8", {"full_name": "X"}),
    ("delete", "/users/8", None),
    ("post", "/users/8/reset-password", None),
]


@pytest.fixture
def cenario(monkeypatch):
    db = FakeUsersDb()
    perms = FakePermissoesDb(db)
    monkeypatch.setattr(users, "transaction", db.transaction)
    monkeypatch.setattr(users, "hash_password", lambda p: "hash-" + p)
    monkeypatch.setattr(users, "_save_photo", lambda tipo, uid, *a, **k: f"users/{uid}.jpg")
    monkeypatch.setattr(users, "_read_photo", lambda key: Response(content=b"img", media_type="image/jpeg"))
    monkeypatch.setattr(users, "_delete_photo", lambda key: None)
    monkeypatch.setattr(access_service, "_db", perms)
    return db, perms


def _client(ator: str) -> TestClient:
    app = FastAPI()
    app.include_router(users.router)
    identidade = ATORES[ator]
    app.dependency_overrides[get_current_identity] = lambda: identidade
    return TestClient(app, raise_server_exceptions=False)


def _chamar(client, metodo, caminho, corpo=None):
    if corpo is not None:
        return getattr(client, metodo)(caminho, json=corpo)
    return getattr(client, metodo)(caminho)


def _foto(client, metodo, user_id):
    caminho = f"/users/{user_id}/photo"
    if metodo == "post":
        return client.post(caminho, files={"file": ("a.jpg", b"xx", "image/jpeg")})
    return getattr(client, metodo)(caminho)


# ---------------------------------------------------------------------------
# Grupo so com admin.usuarios (sem admin.grupos)
# ---------------------------------------------------------------------------


def test_gestor_sem_admin_grupos_nao_cria_admin_e_nao_escreve(cenario):
    db, _ = cenario
    resp = _chamar(_client("gestor"), "post", "/users", {**CORPO_NOVO, "role": "admin"})
    assert resp.status_code == 403 and resp.json()["detail"]["error"] == "forbidden"
    assert db.escritas() == []
    assert not any(sql.startswith("SELECT id FROM users WHERE username") for sql, _ in db.log)  # nem a checagem de nome


def test_gestor_sem_admin_grupos_nao_promove_a_admin_e_nao_escreve(cenario):
    db, _ = cenario
    resp = _chamar(_client("gestor"), "put", "/users/8", {"role": "admin"})
    assert resp.status_code == 403
    assert db.escritas() == []
    assert db.users[8]["role"] == "regular" and (8, GID["admin"]) not in db.members
    assert db.rollbacks == 1 and db.commits == 0


def test_gestor_sem_admin_grupos_nao_rebaixa_admin_e_nao_escreve(cenario):
    db, _ = cenario
    resp = _chamar(_client("gestor"), "put", "/users/7", {"role": "regular"})
    assert resp.status_code == 403
    assert db.escritas() == []
    assert db.users[7]["role"] == "admin" and (7, GID["admin"]) in db.members
    assert db.rollbacks == 1 and db.commits == 0


def test_gestor_cria_e_edita_usuario_comum(cenario):
    db, _ = cenario
    criado = _chamar(_client("gestor"), "post", "/users", CORPO_NOVO)
    assert criado.status_code == 200 and criado.json()["item"]["role"] == "regular"
    novo_id = criado.json()["item"]["id"]
    assert (novo_id, GID["usuario"]) in db.members
    editado = _chamar(_client("gestor"), "put", "/users/8", {"full_name": "Novo Nome"})
    assert editado.status_code == 200 and db.users[8]["full_name"] == "Novo Nome"
    trocado = _chamar(_client("gestor"), "put", "/users/8", {"role": "personal_trainer"})
    assert trocado.status_code == 200
    assert {g for (u, g) in db.members if u == 8} == {GID["personal_trainer"]}


def test_gestor_cria_pt_e_lista_desativa_e_reseta_senha(cenario):
    client = _client("gestor")
    assert _chamar(client, "post", "/users", {**CORPO_NOVO, "role": "personal_trainer"}).status_code == 200
    assert _chamar(client, "get", "/users").status_code == 200
    assert _chamar(client, "post", "/users/8/reset-password").status_code == 200
    assert _chamar(client, "delete", "/users/8").status_code == 200


# Regra unica (ADR 0011, rodada 1): escrita sobre alvo admin exige admin.grupos.
# Usuario 7 = admin (role + grupo); 20 = admin so pelo grupo; 21 = admin so por users.role;
# 22 = admin inativo; 8 = comum.
CHAMADAS_EM_ADMIN = {
    "reset": lambda c, uid: _chamar(c, "post", f"/users/{uid}/reset-password"),
    "put_email": lambda c, uid: _chamar(c, "put", f"/users/{uid}", {"email": "a@b.com"}),
    "put_nome": lambda c, uid: _chamar(c, "put", f"/users/{uid}", {"full_name": "Outro"}),
    "put_ativar": lambda c, uid: _chamar(c, "put", f"/users/{uid}", {"is_active": True}),
    "put_desativar": lambda c, uid: _chamar(c, "put", f"/users/{uid}", {"is_active": False}),
    "put_mesmo_papel": lambda c, uid: _chamar(c, "put", f"/users/{uid}", {"role": "admin"}),
    "delete": lambda c, uid: _chamar(c, "delete", f"/users/{uid}"),
}


def _admins_extras(db):
    db.users[20] = _linha(20, "regular")
    db.members.add((20, GID["admin"]))  # admin so pelo grupo
    db.users[21] = _linha(21, "admin")  # admin so por users.role, sem linha de grupo
    db.users[22] = _linha(22, "admin", ativo=0)
    db.members.add((22, GID["admin"]))


@pytest.mark.parametrize("alvo", [7, 20, 21, 22])
@pytest.mark.parametrize("chamada", list(CHAMADAS_EM_ADMIN))
def test_gestor_sem_admin_grupos_nao_escreve_em_admin(cenario, chamada, alvo):
    db, _ = cenario
    _admins_extras(db)
    resp = CHAMADAS_EM_ADMIN[chamada](_client("gestor"), alvo)
    assert resp.status_code == 403, resp.text  # 403 antes de qualquer 409 de ultimo admin
    assert resp.json()["detail"]["error"] == "forbidden"
    assert "temporary_password" not in resp.text
    assert db.escritas() == []  # nem users, nem grupos, nem sessoes
    assert db.rollbacks <= 1 and db.commits == 0


def test_gestor_sem_admin_grupos_nao_desativa_ultimo_admin_com_409(cenario):
    """Nao vaza por 409 se o alvo e o ultimo admin: 403 vem antes da trava."""
    db, _ = cenario
    db.users[1]["is_active"] = 0  # sobra so o admin 7
    for chamada in ("put_desativar", "delete"):
        assert CHAMADAS_EM_ADMIN[chamada](_client("gestor"), 7).status_code == 403
    assert db.escritas() == []


@pytest.mark.parametrize("chamada", list(CHAMADAS_EM_ADMIN))
def test_alvo_inexistente_e_404_para_gestor_e_gestor_total(cenario, chamada):
    for ator in ("gestor", "gestor_total"):
        assert CHAMADAS_EM_ADMIN[chamada](_client(ator), 999).status_code == 404


def test_gestor_continua_gerindo_usuario_comum(cenario):
    db, _ = cenario
    client = _client("gestor")
    assert _chamar(client, "post", "/users/8/reset-password").status_code == 200
    assert _chamar(client, "put", "/users/8", {"full_name": "Comum", "email": "c@d.com"}).status_code == 200
    assert _chamar(client, "put", "/users/8", {"is_active": False}).status_code == 200
    assert _chamar(client, "put", "/users/8", {"is_active": True}).status_code == 200
    assert _chamar(client, "delete", "/users/8").status_code == 200
    assert _chamar(client, "post", "/users/6/reset-password").status_code == 200  # comum sem grupo


@pytest.mark.parametrize("ator", ["gestor_total", "admin", "mestre"])
def test_quem_tem_admin_grupos_age_sobre_admin(cenario, ator):
    db, _ = cenario
    _admins_extras(db)
    client = _client(ator)
    assert _chamar(client, "post", "/users/7/reset-password").status_code == 200
    assert _chamar(client, "put", "/users/20", {"full_name": "Via grupo"}).status_code == 200
    assert _chamar(client, "put", "/users/21", {"email": "x@y.com"}).status_code == 200
    assert _chamar(client, "put", "/users/22", {"is_active": True}).status_code == 200
    assert _chamar(client, "put", "/users/7", {"is_active": False}).status_code == 200
    assert _chamar(client, "delete", "/users/20").status_code == 200
    assert db.commits >= 6


@pytest.mark.parametrize("ator", ["gestor_total", "admin"])
@pytest.mark.parametrize("chamada", ["put_desativar", "delete"])
def test_trava_do_ultimo_admin_vale_para_quem_tem_admin_grupos(cenario, ator, chamada):
    db, _ = cenario
    db.users[1]["is_active"] = 0  # sobra so o admin 7 (o ator admin segue com a identidade)
    resp = CHAMADAS_EM_ADMIN[chamada](_client(ator), 7)
    assert resp.status_code == 409 and resp.json()["detail"]["error"] == "last_admin"
    assert db.escritas() == []


def test_auto_reset_continua_400_antes_da_checagem_de_admin(cenario):
    db, _ = cenario
    resp = _chamar(_client("admin"), "post", "/users/1/reset-password")
    assert resp.status_code == 400
    assert db.escritas() == []


def test_autodesativacao_continua_400_para_admin(cenario):
    assert _chamar(_client("admin"), "delete", "/users/1").status_code == 400
    assert _chamar(_client("admin"), "put", "/users/1", {"is_active": False}).status_code == 400


def test_grupo_com_as_duas_permissoes_cria_promove_e_rebaixa_admin(cenario):
    db, _ = cenario
    client = _client("gestor_total")
    criado = _chamar(client, "post", "/users", {**CORPO_NOVO, "role": "admin"})
    assert criado.status_code == 200 and (criado.json()["item"]["id"], GID["admin"]) in db.members
    assert _chamar(client, "put", "/users/8", {"role": "admin"}).status_code == 200
    assert (8, GID["admin"]) in db.members and db.users[8]["role"] == "admin"
    assert _chamar(client, "put", "/users/7", {"role": "regular"}).status_code == 200
    assert (7, GID["usuario"]) in db.members and (7, GID["admin"]) not in db.members


@pytest.mark.parametrize("ator", ["admin", "mestre"])
def test_admin_de_sistema_e_token_mestre_fazem_tudo(cenario, ator):
    db, perms = cenario
    client = _client(ator)
    assert _chamar(client, "post", "/users", {**CORPO_NOVO, "role": "admin"}).status_code == 200
    assert _chamar(client, "put", "/users/8", {"role": "admin"}).status_code == 200
    assert _chamar(client, "put", "/users/7", {"role": "regular"}).status_code == 200
    assert _chamar(client, "get", "/users").status_code == 200
    assert _chamar(client, "post", "/users/8/reset-password").status_code == 200
    assert _chamar(client, "delete", "/users/8").status_code == 200
    if ator == "mestre":
        assert perms.consultas == []  # token mestre: acesso total sem banco


# ---------------------------------------------------------------------------
# Quem nao tem a permissao recebe 403 nas 5 rotas administrativas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ator", ["regular", "pt", "sem_grupo", "admin_sem_grupo"])
@pytest.mark.parametrize(("metodo", "caminho", "corpo"), ROTAS_ADMIN)
def test_sem_permissao_recebe_403_e_nao_escreve(cenario, ator, metodo, caminho, corpo):
    db, perms = cenario
    resp = _chamar(_client(ator), metodo, caminho, corpo)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"] == "forbidden"
    assert db.escritas() == []
    assert len(perms.consultas) == 1  # uma consulta de concessoes por requisicao


def test_papel_admin_na_linha_sem_grupo_nao_da_acesso(cenario):
    """Nao ha fallback para `users.role`: admin sem grupo e negado."""
    resp = _chamar(_client("admin_sem_grupo"), "get", "/users")
    assert resp.status_code == 403


def test_usuario_do_grupo_free_vazio_e_negado(cenario):
    db, _ = cenario
    app = FastAPI()
    app.include_router(users.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=14, role="regular", source="session")
    assert TestClient(app, raise_server_exceptions=False).get("/users").status_code == 403


# ---------------------------------------------------------------------------
# Rotas /users/me continuam so com sessao (sem consulta de permissoes)
# ---------------------------------------------------------------------------


def test_users_me_nao_consulta_permissoes(cenario):
    _, perms = cenario
    client = _client("sem_grupo")
    assert client.get("/users/me").status_code == 200
    assert client.get("/users/me").json()["item"]["id"] == 6
    assert perms.consultas == []


# ---------------------------------------------------------------------------
# Fotos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("metodo", ["post", "get", "delete"])
@pytest.mark.parametrize("ator", ["regular", "sem_grupo", "pt"])
def test_foto_do_proprio_usuario_passa_sem_consultar_permissoes(cenario, ator, metodo):
    _, perms = cenario
    uid = ATORES[ator].user_id
    if metodo == "get":
        cenario[0].users[uid]["photo_path"] = f"users/{uid}.jpg"
    resp = _foto(_client(ator), metodo, uid)
    assert resp.status_code == 200, resp.text
    assert perms.consultas == []


@pytest.mark.parametrize("metodo", ["post", "get", "delete"])
@pytest.mark.parametrize("ator", ["regular", "pt", "sem_grupo", "so_alunos"])
def test_foto_de_terceiro_sem_escopo_todos_e_403(cenario, ator, metodo):
    db, perms = cenario
    antes = copy.deepcopy(db.users)
    resp = _foto(_client(ator), metodo, 8)
    assert resp.status_code == 403
    assert db.users == antes
    assert not [s for s, _ in db.log if s.startswith("UPDATE users")]
    assert len(perms.consultas) == 1


@pytest.mark.parametrize("metodo", ["post", "get", "delete"])
@pytest.mark.parametrize("ator", ["admin", "mestre", "gestor"])
def test_foto_de_terceiro_com_admin_usuarios_todos_passa(cenario, ator, metodo):
    db, _ = cenario
    if metodo == "get":
        db.users[8]["photo_path"] = "users/8.jpg"
    assert _foto(_client(ator), metodo, 8).status_code == 200


@pytest.mark.parametrize("ator", ["admin", "regular", "mestre", "sem_grupo"])
@pytest.mark.parametrize("metodo", ["post", "get", "delete"])
@pytest.mark.parametrize("uid", [0, -1, -42])
def test_foto_com_user_id_zero_ou_negativo_e_400_invalid_target(cenario, ator, metodo, uid):
    db, perms = cenario
    resp = _foto(_client(ator), metodo, uid)
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_target"
    assert perms.consultas == [] and db.log == []


def test_foto_get_de_terceiro_com_escopo_alunos_nao_basta(cenario):
    """`authorize_self_or_all` so aceita escopo `todos`; `alunos` (grupo so_alunos) e 403."""
    _, perms = cenario
    assert _foto(_client("so_alunos"), "get", 8).status_code == 403
    assert len(perms.consultas) == 1


# ---------------------------------------------------------------------------
# Falha de banco na resolucao: 503, nunca 200 nem 403
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("metodo", "caminho", "corpo"), ROTAS_ADMIN)
@pytest.mark.parametrize("ator", ["admin", "regular", "gestor"])
def test_falha_do_banco_na_resolucao_e_503(cenario, monkeypatch, ator, metodo, caminho, corpo):
    db, _ = cenario
    falho = FakePermissoesDb(db, falha=RuntimeError("banco fora"))
    monkeypatch.setattr(access_service, "_db", falho)
    resp = _chamar(_client(ator), metodo, caminho, corpo)
    assert resp.status_code == 503, resp.text
    assert db.escritas() == [] and db.log == []


@pytest.mark.parametrize("metodo", ["post", "get", "delete"])
def test_falha_do_banco_na_foto_de_terceiro_e_503_e_a_propria_nao_consulta(cenario, monkeypatch, metodo):
    db, _ = cenario
    monkeypatch.setattr(access_service, "_db", FakePermissoesDb(db, falha=RuntimeError("banco fora")))
    assert _foto(_client("regular"), metodo, 8).status_code == 503
    db.users[4]["photo_path"] = "users/4.jpg"
    assert _foto(_client("regular"), metodo, 4).status_code == 200


def test_falha_na_resolucao_ao_criar_admin_nao_escreve(cenario, monkeypatch):
    """O 503 vem da primeira consulta (rota): nada chega ao banco de usuarios."""
    db, _ = cenario
    monkeypatch.setattr(access_service, "_db", FakePermissoesDb(db, falha=RuntimeError("banco fora")))
    resp = _chamar(_client("gestor"), "post", "/users", {**CORPO_NOVO, "role": "admin"})
    assert resp.status_code == 503 and db.escritas() == []


def test_o_resolvedor_e_consultado_uma_vez_por_requisicao_mesmo_com_permissao_adicional(cenario):
    _, perms = cenario
    resp = _chamar(_client("gestor_total"), "post", "/users", {**CORPO_NOVO, "role": "admin"})
    assert resp.status_code == 200
    assert len(perms.consultas) == 1

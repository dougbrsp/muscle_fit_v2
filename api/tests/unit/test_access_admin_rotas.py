"""API HTTP de "Grupos e acessos" (ADR 0011, lote 3A): as 11 rotas de `app.access_admin`.

O resolvedor REAL de permissoes roda sobre um banco falso (`access_service._db`) com
`DEFAULT_GRANTS_BY_GROUP`; o servico `app.access.admin` e `app.db.transaction` sao dubles.

Rodar: cd api && python -m pytest tests/unit/test_access_admin_rotas.py
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import access_admin as rotas_mod
from app.access import DEFAULT_GRANTS_BY_GROUP
from app.access import admin as svc
from app.access import service as access_service
from app.access.deps import permission_of
from app.access.service import FORBIDDEN_DETAIL
from app.security import Identity, get_current_identity
from tests.unit.test_access_guarda_cobertura_rotas import _iter_api_routes

USUARIO, PT, ADMIN_ID, SEM_GRUPO, DONO_ADMIN_DISPOSITIVO = 7, 8, 1, 9, 1
MESTRE = Identity(user_id=ADMIN_ID, role="admin", source="master_token")
PAPEIS = {USUARIO: "regular", PT: "personal_trainer", ADMIN_ID: "admin", SEM_GRUPO: "regular"}

GRUPO = {"id": 5, "group_key": "g", "name": "G", "description": None, "is_system": False}
GRANTS = {"grants": [{"resource": "treino.planos", "action": "ver", "scope": "todos"}]}

# (metodo, caminho, acao, status de sucesso, corpo)
ROTAS = [
    ("GET", "/access/catalog", "ver", 200, None),
    ("GET", "/access/groups", "ver", 200, None),
    ("GET", "/access/groups/5", "ver", 200, None),
    ("POST", "/access/groups", "editar", 201, {"group_key": "g_novo", "name": "Novo"}),
    ("PUT", "/access/groups/5", "editar", 200, {"name": "N", "description": "d"}),
    ("PUT", "/access/groups/5/grants", "editar", 200, GRANTS),
    ("DELETE", "/access/groups/5", "excluir", 204, None),
    ("GET", "/access/groups/5/members", "ver", 200, None),
    ("POST", "/access/groups/5/members", "editar", 201, {"user_id": 7}),
    ("DELETE", "/access/groups/5/members/7", "excluir", 200, None),
    ("GET", "/access/audit", "ver", 200, None),
]
IDS = [f"{m} {p}" for m, p, *_ in ROTAS]


class FakePermissoes:
    def __init__(self):
        self.falha = None

    @staticmethod
    def _grupo(grupo):
        grants = DEFAULT_GRANTS_BY_GROUP[grupo]
        if not grants:
            return [{"group_key": grupo, "is_system": 1, "resource": None, "action": None, "scope": None}]
        return [{"group_key": grupo, "is_system": 1, "resource": r, "action": a, "scope": s} for r, a, s in grants]

    def fetch_rows(self, sql, params=()):
        if self.falha is not None:
            raise self.falha
        grupos = {USUARIO: "usuario", PT: "personal_trainer", ADMIN_ID: "admin"}
        return self._grupo(grupos[params[0]]) if params[0] in grupos else []


class Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Conn:
    def cursor(self):
        return Cursor()


class Trans:
    def __init__(self):
        self.abertas = 0
        self.saidas = []

    @contextmanager
    def __call__(self):
        self.abertas += 1
        try:
            yield Conn()
        except BaseException as exc:
            self.saidas.append(type(exc).__name__)
            raise
        else:
            self.saidas.append("commit")


class Servico:
    """Dubles de `app.access.admin`: registram a chamada e devolvem `retorno`."""

    def __init__(self, monkeypatch):
        self.chamadas = []
        self.erro = None
        self.membro = {"group_id": 5, "user_id": 7, "added": True, "already_member": False}
        self.retornos = {
            "catalog_view": {"resources": []},
            "list_groups": [{**GRUPO, "created_at": datetime(2026, 11, 12, 10, 30, 5)}],
            "get_group": {**GRUPO, "created_at": datetime(2026, 11, 12, 10, 30, 5)},
            "create_group": GRUPO,
            "update_group": GRUPO,
            "replace_grants": GRUPO,
            "delete_group": None,
            "list_members": [{"user_id": 7}],
            "add_member": self.membro,
            "remove_member": {"group_id": 5, "user_id": 7, "removed": True},
            "list_audit": [{"id": 1, "created_at": datetime(2026, 11, 12, 9, 0, 0)}],
        }
        for nome in self.retornos:
            monkeypatch.setattr(svc, nome, self._dub(nome))

    def _dub(self, nome):
        def f(*args, **kwargs):
            self.chamadas.append((nome, args, kwargs))
            if self.erro is not None:
                raise self.erro
            return self.retornos[nome]

        return f


@pytest.fixture
def ctx(monkeypatch):
    perms = FakePermissoes()
    monkeypatch.setattr(access_service, "_db", perms)
    trans = Trans()
    monkeypatch.setattr(rotas_mod, "transaction", trans)
    servico = Servico(monkeypatch)
    from app.main import app

    state = {"identity": Identity(user_id=ADMIN_ID, role="admin", source="session")}
    app.dependency_overrides[get_current_identity] = lambda: state["identity"]
    client = TestClient(app, raise_server_exceptions=False)
    yield client, state, perms, trans, servico
    app.dependency_overrides.pop(get_current_identity, None)


def _como(state, uid, source="session"):
    state["identity"] = Identity(
        user_id=uid, role=PAPEIS.get(uid, "regular"), source=source, device_id=3 if source == "device" else None
    )


def _chamar(client, metodo, caminho, corpo=None):
    kwargs = {"json": corpo} if corpo is not None else {}
    return client.request(metodo, caminho, **kwargs)


def test_as_11_rotas_existem_com_admin_grupos_e_a_acao_certa():
    from app.main import app

    achadas = {}
    for route in _iter_api_routes(app):
        if getattr(route.endpoint, "__module__", "") != "app.access_admin":
            continue
        perms = set()
        for dep in route.dependant.dependencies:
            p = permission_of(dep.call)
            if p:
                perms.add(p)
        for m in route.methods - {"HEAD", "OPTIONS"}:
            achadas[f"{m} {route.path}"] = perms
    assert len(achadas) == 11
    for metodo, caminho, acao, _, _ in ROTAS:
        modelo = (
            caminho.replace("/groups/5/members/7", "/groups/{group_id}/members/{user_id}")
            .replace("/groups/5", "/groups/{group_id}")
        )
        assert achadas[f"{metodo} {modelo}"] == {("admin.grupos", acao)}, (metodo, modelo)


def test_as_rotas_nao_estao_no_app_de_engines():
    from app.main_engines import app as engines

    assert not [r for r in engines.routes if getattr(r, "path", "").startswith("/access")]


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
def test_admin_passa_com_o_status_de_sucesso(ctx, rota):
    client, state, _, trans, servico = ctx
    metodo, caminho, _, sucesso, corpo = rota
    resp = _chamar(client, metodo, caminho, corpo)
    assert resp.status_code == sucesso, resp.text
    assert trans.saidas == (["commit"] if "catalog" not in caminho else [])


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
@pytest.mark.parametrize("uid", [USUARIO, PT, SEM_GRUPO], ids=["usuario", "personal_trainer", "sem_grupo"])
def test_nao_admin_e_sem_grupo_recebem_403_sem_tocar_dados(ctx, rota, uid):
    client, state, _, trans, servico = ctx
    _como(state, uid)
    metodo, caminho, _, _, corpo = rota
    resp = _chamar(client, metodo, caminho, corpo)
    assert resp.status_code == 403
    assert resp.json()["detail"] == FORBIDDEN_DETAIL
    assert servico.chamadas == [] and trans.abertas == 0


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
def test_dispositivo_de_dono_admin_recebe_403(ctx, rota):
    client, state, _, trans, servico = ctx
    _como(state, DONO_ADMIN_DISPOSITIVO, source="device")
    metodo, caminho, _, _, corpo = rota
    resp = _chamar(client, metodo, caminho, corpo)
    assert resp.status_code == 403
    assert resp.json()["detail"] == FORBIDDEN_DETAIL
    assert servico.chamadas == [] and trans.abertas == 0


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
def test_token_mestre_passa(ctx, rota):
    client, state, *_ = ctx
    state["identity"] = MESTRE
    metodo, caminho, _, sucesso, corpo = rota
    assert _chamar(client, metodo, caminho, corpo).status_code == sucesso


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
def test_falha_de_banco_nas_permissoes_e_503(ctx, rota):
    client, _, perms, _, servico = ctx
    perms.falha = RuntimeError("banco fora")
    metodo, caminho, _, _, corpo = rota
    assert _chamar(client, metodo, caminho, corpo).status_code == 503
    assert servico.chamadas == []


ERROS = [
    (svc.GroupNotFoundError, 404, "group_not_found"),
    (svc.UserNotFoundError, 404, "user_not_found"),
    (svc.MembershipNotFoundError, 404, "membership_not_found"),
    (svc.GroupSystemImmutableError, 409, "group_system_immutable"),
    (svc.GroupMembershipByRoleError, 409, "group_membership_by_role"),
    (svc.GroupKeyTakenError, 409, "group_key_taken"),
    (svc.GroupHasMembersError, 409, "group_has_members"),
    (svc.SelfMembershipForbiddenError, 409, "self_membership_forbidden"),
    (svc.InvalidGrantError, 422, "invalid_grant"),
    (svc.ResourceNotGrantableError, 422, "resource_not_grantable"),
    (svc.InvalidGroupDataError, 422, "invalid_group_data"),
    (svc.InvalidPaginationError, 422, "invalid_pagination"),
    (svc.InvalidIdError, 422, "invalid_id"),
]


@pytest.mark.parametrize("classe,status_http,codigo", ERROS, ids=[e[2] for e in ERROS])
@pytest.mark.parametrize("rota", [r for r in ROTAS if "catalog" not in r[1]], ids=[
    i for r, i in zip(ROTAS, IDS) if "catalog" not in r[1]])
def test_erro_de_negocio_vira_status_e_corpo_e_faz_rollback(ctx, rota, classe, status_http, codigo):
    client, _, _, trans, servico = ctx
    servico.erro = classe("mensagem de teste")
    metodo, caminho, _, _, corpo = rota
    resp = _chamar(client, metodo, caminho, corpo)
    assert resp.status_code == status_http, resp.text
    assert resp.json() == {"error": codigo, "message": "mensagem de teste"}
    assert trans.saidas == [classe.__name__]


@pytest.mark.parametrize(
    "metodo,caminho,corpo",
    [
        ("POST", "/access/groups", {"group_key": "g", "name": "N", "extra": 1}),
        ("POST", "/access/groups", {"group_key": "g"}),
        ("POST", "/access/groups", {}),
        ("PUT", "/access/groups/5", {"name": "N", "extra": 1}),
        ("PUT", "/access/groups/5", {}),
        ("PUT", "/access/groups/5/grants", {}),
        ("PUT", "/access/groups/5/grants", {"grants": [{"resource": "r", "action": "ver"}]}),
        ("PUT", "/access/groups/5/grants", {"grants": [], "x": 1}),
        ("PUT", "/access/groups/5/grants", {"grants": [{"resource": "r", "action": "a", "scope": "s", "x": 1}]}),
        ("POST", "/access/groups/5/members", {}),
        ("POST", "/access/groups/5/members", {"user_id": 7, "x": 1}),
        ("POST", "/access/groups/5/members", {"user_id": "abc"}),
    ],
)
def test_corpo_invalido_e_422_sem_tocar_dados(ctx, metodo, caminho, corpo):
    client, _, _, trans, servico = ctx
    resp = _chamar(client, metodo, caminho, corpo)
    assert resp.status_code == 422
    assert servico.chamadas == [] and trans.abertas == 0


def test_post_de_membro_201_ao_adicionar_e_200_se_ja_era_membro(ctx):
    client, _, _, _, servico = ctx
    r1 = client.post("/access/groups/5/members", json={"user_id": 7})
    assert r1.status_code == 201 and r1.json()["added"] is True
    servico.retornos["add_member"] = {"group_id": 5, "user_id": 7, "added": False, "already_member": True}
    r2 = client.post("/access/groups/5/members", json={"user_id": 7})
    assert r2.status_code == 200 and r2.json()["already_member"] is True
    assert servico.chamadas[0][1][1:] == (5, 7) or servico.chamadas[0][1][2:] == (5, 7)


def test_datas_saem_em_iso_8601(ctx):
    client, *_ = ctx
    assert client.get("/access/groups").json()[0]["created_at"] == "2026-11-12T10:30:05"
    assert client.get("/access/groups/5").json()["created_at"] == "2026-11-12T10:30:05"
    assert client.get("/access/audit").json()[0]["created_at"] == "2026-11-12T09:00:00"


def test_delete_de_grupo_e_204_sem_corpo(ctx):
    client, *_ = ctx
    resp = client.delete("/access/groups/5")
    assert resp.status_code == 204 and resp.content == b""


def test_audit_repassa_filtros_e_padroes(ctx):
    client, _, _, _, servico = ctx
    client.get("/access/audit")
    client.get("/access/audit", params={"group_id": 5, "limit": 10, "offset": 20})
    assert servico.chamadas[0][1][1:] == (None, 50, 0)
    assert servico.chamadas[1][1][1:] == (5, 10, 20)


def test_actor_e_o_usuario_da_identidade(ctx):
    client, *_, servico = ctx
    client.post("/access/groups", json={"group_key": "g_novo", "name": "Novo", "description": "d"})
    nome, args, _ = servico.chamadas[0]
    assert nome == "create_group" and args[1:] == (ADMIN_ID, "g_novo", "Novo", "d")


def test_id_nao_inteiro_no_caminho_e_422(ctx):
    client, *_ = ctx
    assert client.get("/access/groups/abc").status_code == 422

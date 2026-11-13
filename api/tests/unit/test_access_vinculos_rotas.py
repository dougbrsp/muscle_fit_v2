"""Rotas de vinculos treinador-aluno (RBAC lote 5A): `/access/links` e `/access/my-students`.

Resolvedor REAL de permissoes sobre banco falso; servico `app.access.admin` e `app.db.transaction`
sao dubles (padrao de `test_access_admin_rotas.py`).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import access_admin as rotas_mod
from app.access import admin as svc
from app.access import service as access_service
from app.access.deps import permission_of
from app.access.service import FORBIDDEN_DETAIL
from app.security import Identity, get_current_identity
from tests.unit.test_access_admin_rotas import (
    ADMIN_ID,
    DONO_ADMIN_DISPOSITIVO,
    MESTRE,
    PT,
    SEM_GRUPO,
    USUARIO,
    FakePermissoes,
    Trans,
    _como,
)
from tests.unit.test_access_guarda_cobertura_rotas import _iter_api_routes

LINK = {"id": 5, "trainer_user_id": 8, "student_user_id": 7, "status": "ativo",
        "created_at": datetime(2026, 11, 13, 10, 0, 0), "ended_at": None}
CORPO = {"trainer_user_id": 8, "student_user_id": 7}

# (metodo, caminho, acao, status de sucesso, corpo)
ROTAS = [
    ("GET", "/access/links", "ver", 200, None),
    ("POST", "/access/links", "editar", 201, CORPO),
    ("DELETE", "/access/links/5", "excluir", 200, None),
]
IDS = [f"{m} {p}" for m, p, *_ in ROTAS]


class Servico:
    def __init__(self, monkeypatch):
        self.chamadas = []
        self.erro = None
        self.retornos = {
            "list_links": [LINK],
            "create_link": LINK,
            "end_link": {**LINK, "status": "encerrado"},
            "list_my_students": [{"id": 7, "username": "aluno", "full_name": "Aluno"}],
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


def _chamar(client, metodo, caminho, corpo=None):
    kwargs = {"json": corpo} if corpo is not None else {}
    return client.request(metodo, caminho, **kwargs)


def test_rotas_existem_com_admin_grupos_e_my_students_so_com_autenticacao():
    from app.main import app

    achadas = {}
    for route in _iter_api_routes(app):
        if getattr(route.endpoint, "__module__", "") != "app.access_admin":
            continue
        perms = {permission_of(d.call) for d in route.dependant.dependencies} - {None}
        for m in route.methods - {"HEAD", "OPTIONS"}:
            achadas[f"{m} {route.path}"] = perms
    assert achadas["GET /access/links"] == {("admin.grupos", "ver")}
    assert achadas["POST /access/links"] == {("admin.grupos", "editar")}
    assert achadas["DELETE /access/links/{link_id}"] == {("admin.grupos", "excluir")}
    assert achadas["GET /access/my-students"] == set()


def test_rotas_nao_estao_no_app_de_engines():
    from app.main_engines import app as engines

    assert not [r for r in engines.routes if getattr(r, "path", "").startswith("/access")]


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
def test_admin_passa_com_o_status_de_sucesso(ctx, rota):
    client, _, _, trans, _ = ctx
    metodo, caminho, _, sucesso, corpo = rota
    resp = _chamar(client, metodo, caminho, corpo)
    assert resp.status_code == sucesso, resp.text
    assert trans.saidas == ["commit"]


def test_chamadas_ao_servico_levam_ator_e_argumentos(ctx):
    client, _, _, _, servico = ctx
    client.get("/access/links?trainer_user_id=8&student_user_id=7&status=ativo")
    client.post("/access/links", json=CORPO)
    client.delete("/access/links/5")
    assert [(n, a[1:]) for n, a, _ in servico.chamadas] == [
        ("list_links", (8, 7, "ativo")),
        ("create_link", (ADMIN_ID, 8, 7)),
        ("end_link", (ADMIN_ID, 5)),
    ]


def test_get_links_sem_filtros_passa_none(ctx):
    client, _, _, _, servico = ctx
    client.get("/access/links")
    assert servico.chamadas[0][1][1:] == (None, None, None)


def test_datas_saem_em_iso(ctx):
    client, *_ = ctx
    assert client.get("/access/links").json()[0]["created_at"] == "2026-11-13T10:00:00"


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
@pytest.mark.parametrize("uid", [USUARIO, PT, SEM_GRUPO], ids=["usuario", "personal_trainer", "sem_grupo"])
def test_nao_admin_recebe_403_sem_tocar_dados(ctx, rota, uid):
    client, state, _, trans, servico = ctx
    _como(state, uid)
    metodo, caminho, _, _, corpo = rota
    resp = _chamar(client, metodo, caminho, corpo)
    assert resp.status_code == 403 and resp.json()["detail"] == FORBIDDEN_DETAIL
    assert servico.chamadas == [] and trans.abertas == 0


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
def test_dispositivo_de_dono_admin_recebe_403(ctx, rota):
    client, state, _, trans, servico = ctx
    _como(state, DONO_ADMIN_DISPOSITIVO, source="device")
    metodo, caminho, _, _, corpo = rota
    resp = _chamar(client, metodo, caminho, corpo)
    assert resp.status_code == 403 and resp.json()["detail"] == FORBIDDEN_DETAIL
    assert servico.chamadas == [] and trans.abertas == 0


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
def test_token_mestre_passa(ctx, rota):
    client, state, *_ = ctx
    state["identity"] = MESTRE
    metodo, caminho, _, sucesso, corpo = rota
    assert _chamar(client, metodo, caminho, corpo).status_code == sucesso


ERROS = [
    (svc.UserNotFoundError, 404, "user_not_found"),
    (svc.LinkNotFoundError, 404, "link_not_found"),
    (svc.UserInactiveError, 409, "user_inactive"),
    (svc.LinkActorInvolvedError, 409, "link_actor_involved"),
    (svc.LinkAlreadyActiveError, 409, "link_already_active"),
    (svc.LinkAlreadyEndedError, 409, "link_already_ended"),
    (svc.TrainerNotPersonalTrainerError, 422, "trainer_not_personal_trainer"),
    (svc.LinkSelfForbiddenError, 422, "link_self_forbidden"),
    (svc.InvalidLinkDataError, 422, "invalid_link_data"),
    (svc.InvalidIdError, 422, "invalid_id"),
]


@pytest.mark.parametrize("classe,status_http,codigo", ERROS, ids=[e[2] for e in ERROS])
@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
def test_erro_de_negocio_vira_status_e_corpo_e_faz_rollback(ctx, rota, classe, status_http, codigo):
    client, _, _, trans, servico = ctx
    servico.erro = classe("mensagem de teste")
    metodo, caminho, _, _, corpo = rota
    resp = _chamar(client, metodo, caminho, corpo)
    assert resp.status_code == status_http, resp.text
    assert resp.json() == {"error": codigo, "message": "mensagem de teste"}
    assert trans.saidas == [classe.__name__]


@pytest.mark.parametrize(
    "corpo",
    [{}, {"trainer_user_id": 8}, {"trainer_user_id": 8, "student_user_id": 7, "x": 1},
     {"trainer_user_id": "abc", "student_user_id": 7}],
)
def test_corpo_invalido_e_422_sem_tocar_dados(ctx, corpo):
    client, _, _, trans, servico = ctx
    assert client.post("/access/links", json=corpo).status_code == 422
    assert servico.chamadas == [] and trans.abertas == 0


def test_link_id_nao_numerico_e_422(ctx):
    client, _, _, trans, servico = ctx
    assert client.delete("/access/links/abc").status_code == 422
    assert servico.chamadas == [] and trans.abertas == 0


# --- my-students -----------------------------------------------------------------------


@pytest.mark.parametrize("uid", [USUARIO, PT, SEM_GRUPO, ADMIN_ID], ids=["usuario", "pt", "sem_grupo", "admin"])
def test_my_students_exige_so_autenticacao_e_usa_o_proprio_user_id(ctx, uid):
    client, state, perms, trans, servico = ctx
    _como(state, uid)
    resp = client.get("/access/my-students")
    assert resp.status_code == 200
    assert resp.json() == [{"id": 7, "username": "aluno", "full_name": "Aluno"}]
    assert [(n, a[1:]) for n, a, _ in servico.chamadas] == [("list_my_students", (uid,))]
    assert trans.saidas == ["commit"]


def test_my_students_nao_consulta_permissoes(ctx):
    client, _, perms, _, _ = ctx
    perms.falha = RuntimeError("banco fora")  # se resolvesse permissoes, seria 503
    assert client.get("/access/my-students").status_code == 200


def test_my_students_recusa_dispositivo(ctx):
    client, state, _, trans, servico = ctx
    _como(state, DONO_ADMIN_DISPOSITIVO, source="device")
    resp = client.get("/access/my-students")
    assert resp.status_code == 403 and resp.json()["detail"] == FORBIDDEN_DETAIL
    assert servico.chamadas == [] and trans.abertas == 0

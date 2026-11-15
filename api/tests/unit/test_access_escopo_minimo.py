"""Escopo minimo em `require_permission` (RBAC, ADR 0011, lote 2F-1).

`require_permission(recurso, acao, min_scope=...)` so passa se o escopo
concedido cobrir `min_scope` (`proprios` < `alunos` < `todos`). Sem
`min_scope`, qualquer escopo basta (comportamento anterior). Admin (grupo de
sistema) e token mestre tem acesso total (`todos`). `min_scope` fora do
catalogo falha ao declarar a rota. Os helpers `min_scope_of` e
`declared_permission_scopes` leem o escopo minimo; `permission_of` e
`declared_permissions` seguem com pares `(recurso, acao)`.

Sem banco: fake de `app.access.service._db`.
Rodar: cd api && python -m pytest tests/unit/test_access_escopo_minimo.py
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.access import (
    SCOPES,
    declared_permission_scopes,
    declared_permissions,
    min_scope_of,
    permission_of,
    require_permission,
    scope_covers,
)
from app.access import service
from app.security import Identity, get_current_identity

RECURSO, ACAO = "modelos.treino", "ver"


class _FakeDB:
    """`service._db` falso: `{user_id: [(group_key, resource, action, scope), ...]}`."""

    def __init__(self, grupos: dict[int, list[tuple]]):
        self.grupos = grupos
        self.consultas: list[tuple] = []

    def fetch_rows(self, sql, params=()):
        assert "FROM user_access_groups" in sql, sql
        self.consultas.append(tuple(params))
        return [
            {"group_key": g, "is_system": 1, "resource": r, "action": a, "scope": s}
            for g, r, a, s in self.grupos.get(int(params[0]), [])
        ]


@pytest.fixture
def banco(monkeypatch):
    def _instalar(grupos: dict[int, list[tuple]]) -> _FakeDB:
        fake = _FakeDB(grupos)
        monkeypatch.setattr(service, "_db", fake)
        return fake

    return _instalar


def _app(identity: Identity, dep) -> TestClient:
    app = FastAPI()

    @app.get("/rota")
    def _rota(ident: Identity = Depends(dep)):
        return {"user_id": ident.user_id}

    @app.get("/duas", dependencies=[Depends(dep)])
    def _duas(ident: Identity = Depends(dep)):
        return {"ok": True}

    app.dependency_overrides[get_current_identity] = lambda: identity
    return TestClient(app)


def _ident(user_id: int = 10, role: str = "regular", source: str = "session") -> Identity:
    return Identity(user_id=user_id, role=role, source=source)


def _concessao(scope: str) -> dict[int, list[tuple]]:
    return {10: [("grupo_x", RECURSO, ACAO, scope)]}


def test_ordem_dos_escopos():
    assert SCOPES == ("proprios", "alunos", "todos")
    assert scope_covers("todos", "alunos") and scope_covers("alunos", "proprios")
    assert not scope_covers("proprios", "alunos") and not scope_covers("alunos", "todos")


@pytest.mark.parametrize(
    "concedido, minimo, esperado",
    [
        ("proprios", "proprios", 200),
        ("proprios", "alunos", 403),
        ("proprios", "todos", 403),
        ("alunos", "proprios", 200),
        ("alunos", "alunos", 200),
        ("alunos", "todos", 403),
        ("todos", "proprios", 200),
        ("todos", "alunos", 200),
        ("todos", "todos", 200),
    ],
)
def test_escopo_concedido_contra_o_minimo(banco, concedido, minimo, esperado):
    banco(_concessao(concedido))
    resposta = _app(_ident(), require_permission(RECURSO, ACAO, min_scope=minimo)).get("/rota")
    assert resposta.status_code == esperado, resposta.text
    if esperado == 403:
        assert resposta.json()["detail"] == service.FORBIDDEN_DETAIL
    else:
        assert resposta.json() == {"user_id": 10}


def test_concessao_de_outra_acao_nao_conta(banco):
    banco({10: [("grupo_x", RECURSO, "editar", "todos")]})
    resposta = _app(_ident(), require_permission(RECURSO, ACAO, min_scope="proprios")).get("/rota")
    assert resposta.status_code == 403
    assert resposta.json()["detail"] == service.FORBIDDEN_DETAIL


@pytest.mark.parametrize("minimo", [None, "proprios", "todos"])
def test_sem_concessao_nega(banco, minimo):
    banco({10: [("grupo_vazio", None, None, None)]})
    resposta = _app(_ident(), require_permission(RECURSO, ACAO, min_scope=minimo)).get("/rota")
    assert resposta.status_code == 403
    assert resposta.json()["detail"] == service.FORBIDDEN_DETAIL


def test_admin_com_acesso_total_passa_no_todos(banco):
    fake = banco({1: [("admin", None, None, None)]})
    resposta = _app(_ident(user_id=1, role="admin"), require_permission(RECURSO, "excluir", min_scope="todos")).get("/rota")
    assert resposta.status_code == 200, resposta.text
    assert fake.consultas == [(1,)]


def test_token_mestre_passa_sem_banco(banco):
    fake = banco({})
    ident = _ident(user_id=1, role="admin", source="master_token")
    resposta = _app(ident, require_permission(RECURSO, "editar", min_scope="todos")).get("/rota")
    assert resposta.status_code == 200, resposta.text
    assert fake.consultas == []


@pytest.mark.parametrize("invalido", ["", "TODOS", "all", "nenhum"])
def test_min_scope_invalido_falha_ao_declarar(invalido):
    with pytest.raises(ValueError):
        require_permission(RECURSO, ACAO, min_scope=invalido)


def test_recurso_invalido_continua_falhando_ao_declarar():
    with pytest.raises(ValueError):
        require_permission("nao.existe", ACAO, min_scope="todos")


@pytest.mark.parametrize("concedido", ["proprios", "alunos", "todos"])
def test_sem_min_scope_qualquer_escopo_basta(banco, concedido):
    banco(_concessao(concedido))
    resposta = _app(_ident(), require_permission(RECURSO, ACAO)).get("/rota")
    assert resposta.status_code == 200, resposta.text


def test_cache_por_requisicao_uma_consulta(banco):
    """A mesma dependencia declarada duas vezes na rota resolve as permissoes uma vez."""
    fake = banco(_concessao("todos"))
    resposta = _app(_ident(), require_permission(RECURSO, ACAO, min_scope="todos")).get("/duas")
    assert resposta.status_code == 200, resposta.text
    assert fake.consultas == [(10,)]


def test_helpers_leem_o_escopo_minimo():
    com = require_permission(RECURSO, ACAO, min_scope="todos")
    sem = require_permission(RECURSO, ACAO)
    assert min_scope_of(com) == "todos"
    assert min_scope_of(sem) is None
    assert min_scope_of(lambda: None) is None
    assert min_scope_of(None) is None
    # Contrato antigo inalterado: pares (recurso, acao).
    assert permission_of(com) == (RECURSO, ACAO) == permission_of(sem)
    assert com.__name__ == "require_permission__modelos.treino__ver__todos"
    assert sem.__name__ == "require_permission__modelos.treino__ver"


def test_helpers_de_rota():
    app = FastAPI()

    @app.get("/gestao", dependencies=[Depends(require_permission(RECURSO, ACAO, min_scope="todos"))])
    def _gestao():
        return {}

    @app.post("/ia")
    def _ia(ident: Identity = Depends(require_permission("ia.modelo_periodizacao", "editar", min_scope="alunos"))):
        return {}

    @app.get("/meu")
    def _meu(ident: Identity = Depends(require_permission(RECURSO, ACAO))):
        return {}

    @app.get("/livre")
    def _livre():
        return {}

    rotas = {r.path: r for r in app.routes if isinstance(r, APIRoute)}
    assert declared_permission_scopes(rotas["/gestao"]) == [(RECURSO, ACAO, "todos")]
    assert declared_permission_scopes(rotas["/ia"]) == [("ia.modelo_periodizacao", "editar", "alunos")]
    assert declared_permission_scopes(rotas["/meu"]) == [(RECURSO, ACAO, None)]
    assert declared_permission_scopes(rotas["/livre"]) == []
    assert declared_permissions(rotas["/gestao"]) == [(RECURSO, ACAO)]
    assert declared_permissions(rotas["/ia"]) == [("ia.modelo_periodizacao", "editar")]
    assert declared_permissions(rotas["/livre"]) == []

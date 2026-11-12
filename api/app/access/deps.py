"""Dependencia FastAPI do controle de acesso (RBAC, ADR 0011).

`require_permission(recurso, acao)` devolve uma dependencia que depende de
`get_current_identity`, resolve as permissoes (1 consulta, guardada em
`request.state` para o resto da requisicao) e levanta 403 se faltar.

Como a guarda de cobertura identifica uma rota com permissao declarada: a
funcao devolvida carrega os atributos `access_resource` e `access_action`
(constantes `PERMISSION_RESOURCE_ATTR`/`PERMISSION_ACTION_ATTR`). Percorra
`route.dependant` recursivamente e leia `dependant.call`; use
`declared_permissions(route)` (ou `permission_of(call)`).

Escopo minimo (opcional): `require_permission(recurso, acao, min_scope=...)`
exige que o escopo concedido cubra `min_scope` (`catalog.scope_covers`; ordem
`proprios` < `alunos` < `todos`). Sem `min_scope`, qualquer escopo basta (o
comportamento original). Serve para rotas de gestao cujo recurso tambem e
concedido com escopo menor a quem so deve ler o proprio (ex.: `modelos.treino`
`ver` com `proprios` para o grupo `usuario`). O escopo minimo fica no atributo
`access_min_scope` (`PERMISSION_MIN_SCOPE_ATTR`); leia com `min_scope_of(call)`
ou `declared_permission_scopes(route)` (triplas `(recurso, acao, escopo_minimo)`).
`permission_of`/`declared_permissions` seguem devolvendo so `(recurso, acao)`.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import Depends, Request

from ..security import Identity, get_current_identity
from . import catalog, service

PERMISSION_RESOURCE_ATTR = "access_resource"
PERMISSION_ACTION_ATTR = "access_action"
PERMISSION_MIN_SCOPE_ATTR = "access_min_scope"
_STATE_ATTR = "access_permissions"


def permissions_for_request(request: Request, identity: Identity) -> service.EffectivePermissions:
    """Resolve uma vez por requisicao (cache em `request.state`)."""
    cached = getattr(request.state, _STATE_ATTR, None)
    if cached is not None and getattr(request.state, _STATE_ATTR + "_user", None) == identity.user_id:
        return cached
    perms = service.resolve_permissions(identity)
    setattr(request.state, _STATE_ATTR, perms)
    setattr(request.state, _STATE_ATTR + "_user", identity.user_id)
    return perms


def require_permission(
    resource: str, action: str, min_scope: Optional[str] = None
) -> Callable[..., Identity]:
    """Dependencia que exige a concessao (recurso, acao) e, com `min_scope`,
    que o escopo concedido cubra `min_scope`. Recurso/acao/escopo fora do
    catalogo levanta ValueError ao declarar a rota (erro de programacao)."""
    service.check_catalog(resource, action)
    if min_scope is not None and min_scope not in catalog.SCOPES:
        raise ValueError(f"escopo fora do catálogo de acesso: {min_scope!r}")

    def _dependency(
        request: Request, identity: Identity = Depends(get_current_identity)
    ) -> Identity:
        permissoes = permissions_for_request(request, identity)
        if min_scope is None:
            service.require(permissoes, resource, action)
            return identity
        granted = service.permission_scope(permissoes, resource, action)
        if granted is None or not catalog.scope_covers(granted, min_scope):
            raise service.forbidden()
        return identity

    name = f"require_permission__{resource}__{action}"
    if min_scope is not None:
        name += f"__{min_scope}"
    _dependency.__name__ = name
    setattr(_dependency, PERMISSION_RESOURCE_ATTR, resource)
    setattr(_dependency, PERMISSION_ACTION_ATTR, action)
    setattr(_dependency, PERMISSION_MIN_SCOPE_ATTR, min_scope)
    return _dependency


def require_additional_permission(
    request: Request, identity: Identity, resource: str, action: str
) -> None:
    """Exige, de dentro de um handler, uma concessao alem da declarada na rota
    (ex.: `admin.grupos`/`editar` ao criar ou promover admin). Reutiliza o cache
    de `permissions_for_request` (sem segunda consulta na mesma requisicao).
    403 `FORBIDDEN_DETAIL` se faltar; recurso/acao fora do catalogo: ValueError."""
    service.check_catalog(resource, action)
    service.require(permissions_for_request(request, identity), resource, action)


def permission_of(call: Any) -> Optional[tuple[str, str]]:
    """`(recurso, acao)` se `call` veio de `require_permission`, senao None."""
    resource = getattr(call, PERMISSION_RESOURCE_ATTR, None)
    action = getattr(call, PERMISSION_ACTION_ATTR, None)
    if resource is None or action is None:
        return None
    return resource, action


def min_scope_of(call: Any) -> Optional[str]:
    """Escopo minimo declarado em `require_permission(..., min_scope=...)`, ou
    None (sem escopo minimo, ou `call` nao veio de `require_permission`)."""
    if permission_of(call) is None:
        return None
    return getattr(call, PERMISSION_MIN_SCOPE_ATTR, None)


def _walk_calls(route: Any) -> list[Any]:
    calls: list[Any] = []
    dependant = getattr(route, "dependant", None)
    stack = [dependant] if dependant is not None else []
    while stack:
        node = stack.pop()
        calls.append(getattr(node, "call", None))
        stack.extend(getattr(node, "dependencies", ()) or ())
    return calls


def declared_permissions(route: Any) -> list[tuple[str, str]]:
    """Permissoes declaradas por uma rota FastAPI (APIRoute), incluindo as de
    dependencias aninhadas. Lista vazia = rota sem `require_permission`."""
    found: list[tuple[str, str]] = []
    for call in _walk_calls(route):
        pair = permission_of(call)
        if pair is not None:
            found.append(pair)
    return found


def declared_permission_scopes(route: Any) -> list[tuple[str, str, Optional[str]]]:
    """Como `declared_permissions`, mas em triplas `(recurso, acao,
    escopo_minimo)`; `escopo_minimo` None = qualquer escopo basta."""
    found: list[tuple[str, str, Optional[str]]] = []
    for call in _walk_calls(route):
        pair = permission_of(call)
        if pair is not None:
            found.append((pair[0], pair[1], min_scope_of(call)))
    return found

"""Dependencia FastAPI do controle de acesso (RBAC, ADR 0011).

`require_permission(recurso, acao)` devolve uma dependencia que depende de
`get_current_identity`, resolve as permissoes (1 consulta, guardada em
`request.state` para o resto da requisicao) e levanta 403 se faltar.

Como a guarda de cobertura identifica uma rota com permissao declarada: a
funcao devolvida carrega os atributos `access_resource` e `access_action`
(constantes `PERMISSION_RESOURCE_ATTR`/`PERMISSION_ACTION_ATTR`). Percorra
`route.dependant` recursivamente e leia `dependant.call`; use
`declared_permissions(route)` (ou `permission_of(call)`).
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import Depends, Request

from ..security import Identity, get_current_identity
from . import service

PERMISSION_RESOURCE_ATTR = "access_resource"
PERMISSION_ACTION_ATTR = "access_action"
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


def require_permission(resource: str, action: str) -> Callable[..., Identity]:
    """Dependencia que exige a concessao (recurso, acao). Recurso/acao fora do
    catalogo levanta ValueError ao declarar a rota (erro de programacao)."""
    service.check_catalog(resource, action)

    def _dependency(
        request: Request, identity: Identity = Depends(get_current_identity)
    ) -> Identity:
        service.require(permissions_for_request(request, identity), resource, action)
        return identity

    _dependency.__name__ = f"require_permission__{resource}__{action}"
    setattr(_dependency, PERMISSION_RESOURCE_ATTR, resource)
    setattr(_dependency, PERMISSION_ACTION_ATTR, action)
    return _dependency


def permission_of(call: Any) -> Optional[tuple[str, str]]:
    """`(recurso, acao)` se `call` veio de `require_permission`, senao None."""
    resource = getattr(call, PERMISSION_RESOURCE_ATTR, None)
    action = getattr(call, PERMISSION_ACTION_ATTR, None)
    if resource is None or action is None:
        return None
    return resource, action


def declared_permissions(route: Any) -> list[tuple[str, str]]:
    """Permissoes declaradas por uma rota FastAPI (APIRoute), incluindo as de
    dependencias aninhadas. Lista vazia = rota sem `require_permission`."""
    found: list[tuple[str, str]] = []
    dependant = getattr(route, "dependant", None)
    stack = [dependant] if dependant is not None else []
    while stack:
        node = stack.pop()
        pair = permission_of(getattr(node, "call", None))
        if pair is not None:
            found.append(pair)
        stack.extend(getattr(node, "dependencies", ()) or ())
    return found

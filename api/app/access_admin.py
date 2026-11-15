"""API HTTP de "Grupos e acessos" (ADR 0011, lote 3A).

Camada fina sobre `app.access.admin` (dono das regras de negocio): so declara permissao
(`admin.grupos`), valida a forma do corpo (Pydantic, 422), abre UMA transacao por requisicao e
traduz `AccessAdminError` em `{"error": code, "message": message}`.

Dispositivo (`identity.source == "device"`) e recusado com 403 `FORBIDDEN_DETAIL`, mesmo de dono
admin: a administracao de acessos e so de sessao ou token mestre.

Decisoes de contrato:
- DELETE /access/groups/{id}: 204 sem corpo.
- POST /access/groups/{id}/members: 201 quando adicionou, 200 quando ja era membro (idempotente).
- DELETE /access/groups/{id}/members/{user_id}: 200 com o corpo do servico.
- Datas (`created_at`) saem em ISO 8601.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .access import admin as access_admin
from .access import require_permission
from .access.catalog import ACTION_EDITAR, ACTION_EXCLUIR, ACTION_VER
from .access.service import FORBIDDEN_DETAIL
from .db import transaction
from .security import Identity, get_current_identity

router = APIRouter(prefix="/access", tags=["access"])

RESOURCE_GRUPOS = "admin.grupos"

_STATUS_BY_CODE: dict[str, int] = {
    "group_not_found": 404,
    "user_not_found": 404,
    "membership_not_found": 404,
    "group_system_immutable": 409,
    "group_membership_by_role": 409,
    "group_key_taken": 409,
    "group_has_members": 409,
    "self_membership_forbidden": 409,
    "invalid_grant": 422,
    "resource_not_grantable": 422,
    "invalid_group_data": 422,
    "invalid_pagination": 422,
    "invalid_id": 422,
    "invalid_link_data": 422,
    "trainer_not_personal_trainer": 422,
    "link_self_forbidden": 422,
    "user_inactive": 409,
    "link_actor_involved": 409,
    "link_already_active": 409,
    "link_already_ended": 409,
    "link_not_found": 404,
}


class _StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GroupCreateIn(_StrictIn):
    group_key: str
    name: str
    description: Optional[str] = None


class GroupUpdateIn(_StrictIn):
    name: str
    description: Optional[str] = None


class GrantIn(_StrictIn):
    resource: str
    action: str
    scope: str


class GrantsReplaceIn(_StrictIn):
    grants: list[GrantIn]


class MemberAddIn(_StrictIn):
    user_id: int


class LinkCreateIn(_StrictIn):
    trainer_user_id: int
    student_user_id: int


def _reject_device(identity: Identity) -> None:
    if identity.source == "device":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=dict(FORBIDDEN_DETAIL))


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _iso(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_iso(v) for v in value]
    return value


def _error(exc: access_admin.AccessAdminError) -> JSONResponse:
    code = getattr(exc, "code", "invalid_group_data")
    return JSONResponse(
        status_code=_STATUS_BY_CODE.get(code, 422),
        content={"error": code, "message": getattr(exc, "message", str(exc))},
    )


def _run(identity: Identity, work: Callable[[Any], Any]) -> tuple[Any, Optional[JSONResponse]]:
    """Recusa dispositivo, abre a transacao e roda `work(cur)`. Erro de negocio faz rollback e
    vira resposta de erro (devolvida apos o fechamento da transacao)."""
    _reject_device(identity)
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                return work(cur), None
    except access_admin.AccessAdminError as exc:
        return None, _error(exc)


def _ok(identity: Identity, work: Callable[[Any], Any], success: int = 200):
    result, err = _run(identity, work)
    if err is not None:
        return err
    return JSONResponse(status_code=success, content=_iso(result))


@router.get("/catalog")
def get_catalog(identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_VER))):
    _reject_device(identity)
    return _iso(access_admin.catalog_view())


@router.get("/groups")
def list_groups(identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_VER))):
    return _ok(identity, access_admin.list_groups)


@router.get("/groups/{group_id}")
def get_group(group_id: int, identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_VER))):
    return _ok(identity, lambda cur: access_admin.get_group(cur, group_id))


@router.post("/groups", status_code=201)
def create_group(
    body: GroupCreateIn,
    identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_EDITAR)),
):
    return _ok(
        identity,
        lambda cur: access_admin.create_group(
            cur, identity.user_id, body.group_key, body.name, body.description
        ),
        201,
    )


@router.put("/groups/{group_id}")
def update_group(
    group_id: int,
    body: GroupUpdateIn,
    identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_EDITAR)),
):
    return _ok(
        identity,
        lambda cur: access_admin.update_group(cur, identity.user_id, group_id, body.name, body.description),
    )


@router.put("/groups/{group_id}/grants")
def replace_grants(
    group_id: int,
    body: GrantsReplaceIn,
    identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_EDITAR)),
):
    grants = [g.model_dump() for g in body.grants]
    return _ok(identity, lambda cur: access_admin.replace_grants(cur, identity.user_id, group_id, grants))


@router.delete("/groups/{group_id}", status_code=204)
def delete_group(
    group_id: int,
    identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_EXCLUIR)),
):
    _, err = _run(identity, lambda cur: access_admin.delete_group(cur, identity.user_id, group_id))
    if err is not None:
        return err
    return Response(status_code=204)


@router.get("/groups/{group_id}/members")
def list_members(group_id: int, identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_VER))):
    return _ok(identity, lambda cur: access_admin.list_members(cur, group_id))


@router.post("/groups/{group_id}/members")
def add_member(
    group_id: int,
    body: MemberAddIn,
    identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_EDITAR)),
):
    result, err = _run(identity, lambda cur: access_admin.add_member(cur, identity.user_id, group_id, body.user_id))
    if err is not None:
        return err
    return JSONResponse(status_code=201 if result.get("added") else 200, content=_iso(result))


@router.delete("/groups/{group_id}/members/{user_id}")
def remove_member(
    group_id: int,
    user_id: int,
    identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_EXCLUIR)),
):
    return _ok(identity, lambda cur: access_admin.remove_member(cur, identity.user_id, group_id, user_id))


@router.get("/links")
def list_links(
    trainer_user_id: Optional[int] = None,
    student_user_id: Optional[int] = None,
    status: Optional[str] = None,
    identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_VER)),
):
    return _ok(
        identity,
        lambda cur: access_admin.list_links(cur, trainer_user_id, student_user_id, status),
    )


@router.post("/links", status_code=201)
def create_link(
    body: LinkCreateIn,
    identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_EDITAR)),
):
    return _ok(
        identity,
        lambda cur: access_admin.create_link(cur, identity.user_id, body.trainer_user_id, body.student_user_id),
        201,
    )


@router.delete("/links/{link_id}")
def end_link(
    link_id: int,
    identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_EXCLUIR)),
):
    """Encerra o vinculo (status `encerrado`); 200 com o vinculo atualizado."""
    return _ok(identity, lambda cur: access_admin.end_link(cur, identity.user_id, link_id))


@router.get("/my-students")
def my_students(identity: Identity = Depends(get_current_identity)):
    """Alunos com vinculo ativo do proprio usuario. So autenticacao: o filtro e o `user_id` da
    identidade (nao ha alvo de terceiro)."""
    return _ok(identity, lambda cur: access_admin.list_my_students(cur, identity.user_id))


@router.get("/audit")
def list_audit(
    group_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    identity: Identity = Depends(require_permission(RESOURCE_GRUPOS, ACTION_VER)),
):
    return _ok(identity, lambda cur: access_admin.list_audit(cur, group_id, limit, offset))

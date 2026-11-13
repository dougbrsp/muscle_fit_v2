"""Gestao de grupos personalizados e auditoria (RBAC, ADR 0011, lote 3A).

Sem FastAPI e sem transacao propria: toda funcao recebe o cursor da transacao
do chamador (padrao de `membership.py`). SQL parametrizado com `%s`, compativel
com PyMySQL e com o adaptador Data API. Importar nao acessa banco.

Regras (decisoes de 12/11/2026):
- grupos de sistema (`is_system = 1`) sao imutaveis por esta API (nome,
  descricao, concessoes, exclusao e membros); o vinculo a eles segue
  `users.role`;
- grupo personalizado nao recebe recurso do dominio Administracao;
- ninguem altera o proprio vinculo;
- so se exclui grupo sem membros;
- toda escrita grava `access_audit_log` na mesma transacao.

Os erros sao subclasses de `AccessAdminError` com atributo `code`; a camada de
rotas mapeia os codigos para HTTP.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Final, Optional

from ..db import is_duplicate_key_error
from .catalog import ACTIONS, DOMAIN_ADMIN, RESOURCES_BY_KEY, SCOPES, catalog_payload

GROUP_KEY_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
NAME_MAX_LEN: Final = 120
DESCRIPTION_MAX_LEN: Final = 500
AUDIT_LIMIT_MIN: Final = 1
AUDIT_LIMIT_MAX: Final = 200
AUDIT_LIMIT_DEFAULT: Final = 50

TARGET_GROUP: Final = "group"

AUDIT_GROUP_CREATE: Final = "group.create"
AUDIT_GROUP_UPDATE: Final = "group.update"
AUDIT_GROUP_GRANTS_REPLACE: Final = "group.grants.replace"
AUDIT_GROUP_DELETE: Final = "group.delete"
AUDIT_MEMBER_ADD: Final = "member.add"
AUDIT_MEMBER_REMOVE: Final = "member.remove"
AUDIT_ACTIONS: Final[tuple[str, ...]] = (
    AUDIT_GROUP_CREATE,
    AUDIT_GROUP_UPDATE,
    AUDIT_GROUP_GRANTS_REPLACE,
    AUDIT_GROUP_DELETE,
    AUDIT_MEMBER_ADD,
    AUDIT_MEMBER_REMOVE,
)


# --- Erros -----------------------------------------------------------------


class AccessAdminError(Exception):
    """Base dos erros da gestao de grupos. `code` identifica o erro."""

    code: str = "access_admin_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidIdError(AccessAdminError):
    code = "invalid_id"


class InvalidGroupDataError(AccessAdminError):
    code = "invalid_group_data"


class InvalidPaginationError(AccessAdminError):
    code = "invalid_pagination"


class GroupNotFoundError(AccessAdminError):
    code = "group_not_found"


class GroupSystemImmutableError(AccessAdminError):
    code = "group_system_immutable"


class GroupMembershipByRoleError(AccessAdminError):
    code = "group_membership_by_role"


class GroupKeyTakenError(AccessAdminError):
    code = "group_key_taken"


class GroupHasMembersError(AccessAdminError):
    code = "group_has_members"


class InvalidGrantError(AccessAdminError):
    code = "invalid_grant"


class ResourceNotGrantableError(AccessAdminError):
    code = "resource_not_grantable"


class UserNotFoundError(AccessAdminError):
    code = "user_not_found"


class SelfMembershipForbiddenError(AccessAdminError):
    code = "self_membership_forbidden"


class MembershipNotFoundError(AccessAdminError):
    code = "membership_not_found"


# --- Auxiliares --------------------------------------------------------------


def _cell(row: Any, key: str, idx: int) -> Any:
    return row.get(key) if isinstance(row, Mapping) else row[idx]


def _require_id(value: Any, name: str) -> int:
    """Inteiro estritamente positivo (rejeita None, 0, negativo, bool e nao inteiro)."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidIdError(f"{name} inválido: {value!r}.")
    return value


def _validate_name(name: Any) -> str:
    if not isinstance(name, str):
        raise InvalidGroupDataError("O nome do grupo é obrigatório.")
    clean = name.strip()
    if not clean or len(clean) > NAME_MAX_LEN:
        raise InvalidGroupDataError(f"O nome do grupo deve ter de 1 a {NAME_MAX_LEN} caracteres.")
    return clean


def _validate_description(description: Any) -> Optional[str]:
    if description is None:
        return None
    if not isinstance(description, str):
        raise InvalidGroupDataError("A descrição do grupo deve ser texto.")
    clean = description.strip()
    if len(clean) > DESCRIPTION_MAX_LEN:
        raise InvalidGroupDataError(f"A descrição do grupo deve ter até {DESCRIPTION_MAX_LEN} caracteres.")
    return clean or None


def _validate_group_key(group_key: Any) -> str:
    if not isinstance(group_key, str) or not GROUP_KEY_PATTERN.fullmatch(group_key):
        raise InvalidGroupDataError(
            "A chave do grupo deve começar com letra minúscula e ter de 3 a 64 caracteres "
            "entre letras minúsculas, números e '_'."
        )
    return group_key


_GROUP_COLUMNS: Final = "id, group_key, name, description, is_system"


def _group_from_row(row: Any) -> dict[str, Any]:
    is_system = bool(int(_cell(row, "is_system", 4) or 0))
    return {
        "id": int(_cell(row, "id", 0)),
        "group_key": _cell(row, "group_key", 1),
        "name": _cell(row, "name", 2),
        "description": _cell(row, "description", 3),
        "is_system": is_system,
        "editable": not is_system,
    }


def _load_group(cur: Any, group_id: int, *, lock: bool) -> dict[str, Any]:
    sql = f"SELECT {_GROUP_COLUMNS} FROM access_groups WHERE id = %s"
    if lock:
        sql += " FOR UPDATE"
    cur.execute(sql, (group_id,))
    row = cur.fetchone()
    if not row:
        raise GroupNotFoundError(f"Grupo {group_id} não encontrado.")
    return _group_from_row(row)


def _lock_editable_group(cur: Any, group_id: Any) -> dict[str, Any]:
    gid = _require_id(group_id, "group_id")
    group = _load_group(cur, gid, lock=True)
    if group["is_system"]:
        raise GroupSystemImmutableError("Grupos de sistema não podem ser alterados por esta tela.")
    return group


def _lock_group_for_members(cur: Any, group_id: Any) -> dict[str, Any]:
    gid = _require_id(group_id, "group_id")
    group = _load_group(cur, gid, lock=True)
    if group["is_system"]:
        raise GroupMembershipByRoleError(
            "O vínculo a grupos de sistema segue o papel do usuário; altere-o na tela de usuários."
        )
    return group


def _grant_from_row(row: Any) -> dict[str, str]:
    return {
        "resource": _cell(row, "resource", 0),
        "action": _cell(row, "action", 1),
        "scope": _cell(row, "scope", 2),
    }


def _fetch_grants(cur: Any, group_id: int) -> list[dict[str, str]]:
    cur.execute(
        "SELECT resource, action, scope FROM access_group_grants WHERE group_id = %s "
        "ORDER BY resource, action",
        (group_id,),
    )
    return [_grant_from_row(row) for row in cur.fetchall() or []]


def _count_members(cur: Any, group_id: int) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM user_access_groups WHERE group_id = %s", (group_id,))
    row = cur.fetchone()
    return int(_cell(row, "n", 0) or 0) if row else 0


def _snapshot(group: Mapping[str, Any]) -> dict[str, Any]:
    return {"group_key": group["group_key"], "name": group["name"], "description": group["description"]}


def _audit(
    cur: Any,
    actor_user_id: Any,
    action: str,
    target_type: str,
    target_id: Optional[int],
    detalhe: Optional[Mapping[str, Any]],
) -> None:
    """Grava uma linha em `access_audit_log` na transacao do chamador."""
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"Ação de auditoria desconhecida: {action!r}.")
    actor = _require_id(actor_user_id, "actor_user_id")
    payload = None if detalhe is None else json.dumps(detalhe, ensure_ascii=False, default=str)
    cur.execute(
        "INSERT INTO access_audit_log (actor_user_id, action, target_type, target_id, detalhe) "
        "VALUES (%s, %s, %s, %s, %s)",
        (actor, action, target_type, target_id, payload),
    )


# --- Catalogo ----------------------------------------------------------------


def catalog_view() -> dict[str, Any]:
    """Recursos (`key`, `label`, `domain`), acoes e escopos do catalogo."""
    return {"resources": catalog_payload(), "actions": list(ACTIONS), "scopes": list(SCOPES)}


# --- Leitura de grupos ---------------------------------------------------------


def list_groups(cur: Any) -> list[dict[str, Any]]:
    """Todos os grupos (sistema primeiro, depois por nome) com membros e concessoes."""
    cur.execute(
        "SELECT g.id, g.group_key, g.name, g.description, g.is_system, "
        "(SELECT COUNT(*) FROM user_access_groups uag WHERE uag.group_id = g.id) AS member_count "
        "FROM access_groups g ORDER BY g.is_system DESC, g.name, g.id"
    )
    groups: list[dict[str, Any]] = []
    by_id: dict[int, dict[str, Any]] = {}
    for row in cur.fetchall() or []:
        group = _group_from_row(row)
        group["member_count"] = int(_cell(row, "member_count", 5) or 0)
        group["grants"] = []
        groups.append(group)
        by_id[group["id"]] = group
    if not groups:
        return groups
    cur.execute(
        "SELECT group_id, resource, action, scope FROM access_group_grants "
        "ORDER BY group_id, resource, action"
    )
    for row in cur.fetchall() or []:
        target = by_id.get(int(_cell(row, "group_id", 0)))
        if target is not None:
            target["grants"].append(
                {
                    "resource": _cell(row, "resource", 1),
                    "action": _cell(row, "action", 2),
                    "scope": _cell(row, "scope", 3),
                }
            )
    return groups


def get_group(cur: Any, group_id: Any) -> dict[str, Any]:
    gid = _require_id(group_id, "group_id")
    group = _load_group(cur, gid, lock=False)
    group["member_count"] = _count_members(cur, gid)
    group["grants"] = _fetch_grants(cur, gid)
    return group


# --- Escrita de grupos ---------------------------------------------------------


def create_group(
    cur: Any, actor_user_id: Any, group_key: Any, name: Any, description: Any = None
) -> dict[str, Any]:
    """Cria grupo personalizado (`is_system = 0`, sem concessoes)."""
    _require_id(actor_user_id, "actor_user_id")
    key = _validate_group_key(group_key)
    clean_name = _validate_name(name)
    clean_desc = _validate_description(description)
    cur.execute("SELECT id FROM access_groups WHERE group_key = %s", (key,))
    if cur.fetchone():
        raise GroupKeyTakenError(f"Já existe um grupo com a chave '{key}'.")
    try:
        cur.execute(
            "INSERT INTO access_groups (group_key, name, description, is_system) VALUES (%s, %s, %s, 0)",
            (key, clean_name, clean_desc),
        )
    except Exception as exc:  # noqa: BLE001 - so a violacao de UNIQUE vira erro de dominio
        if is_duplicate_key_error(exc):
            raise GroupKeyTakenError(f"Já existe um grupo com a chave '{key}'.") from exc
        raise
    group_id = getattr(cur, "lastrowid", None)
    if not group_id:
        cur.execute("SELECT id FROM access_groups WHERE group_key = %s", (key,))
        row = cur.fetchone()
        if not row:
            raise GroupNotFoundError(f"Grupo '{key}' não encontrado após a criação.")
        group_id = _cell(row, "id", 0)
    group_id = int(group_id)
    after = {"group_key": key, "name": clean_name, "description": clean_desc}
    _audit(cur, actor_user_id, AUDIT_GROUP_CREATE, TARGET_GROUP, group_id, {"antes": None, "depois": after})
    return {**after, "id": group_id, "is_system": False, "editable": True, "member_count": 0, "grants": []}


def update_group(cur: Any, actor_user_id: Any, group_id: Any, name: Any, description: Any = None) -> dict[str, Any]:
    """Altera nome e descricao de grupo personalizado."""
    _require_id(actor_user_id, "actor_user_id")
    clean_name = _validate_name(name)
    clean_desc = _validate_description(description)
    group = _lock_editable_group(cur, group_id)
    before = _snapshot(group)
    cur.execute(
        "UPDATE access_groups SET name = %s, description = %s WHERE id = %s",
        (clean_name, clean_desc, group["id"]),
    )
    group.update(name=clean_name, description=clean_desc)
    _audit(
        cur, actor_user_id, AUDIT_GROUP_UPDATE, TARGET_GROUP, group["id"],
        {"antes": before, "depois": _snapshot(group)},
    )
    return group


def _normalize_grant(item: Any) -> tuple[Any, Any, Any]:
    if isinstance(item, Mapping):
        return item.get("resource"), item.get("action"), item.get("scope")
    if isinstance(item, (list, tuple)) and len(item) == 3:
        return item[0], item[1], item[2]
    raise InvalidGrantError("Cada concessão precisa de recurso, ação e escopo.")


def validate_grants(grants: Any) -> list[dict[str, str]]:
    """Valida concessoes contra o catalogo. Devolve lista ordenada por recurso e acao."""
    if not isinstance(grants, (list, tuple)):
        raise InvalidGrantError("As concessões devem ser uma lista.")
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for item in grants:
        resource, action, scope = _normalize_grant(item)
        if not isinstance(resource, str) or resource not in RESOURCES_BY_KEY:
            raise InvalidGrantError(f"Recurso desconhecido: {resource!r}.")
        if RESOURCES_BY_KEY[resource].domain == DOMAIN_ADMIN:
            raise ResourceNotGrantableError(
                f"O recurso '{resource}' é da Administração e não pode ser concedido a grupo personalizado."
            )
        if not isinstance(action, str) or action not in ACTIONS:
            raise InvalidGrantError(f"Ação inválida: {action!r}.")
        if not isinstance(scope, str) or scope not in SCOPES:
            raise InvalidGrantError(f"Escopo inválido: {scope!r}.")
        pair = (resource, action)
        if pair in seen:
            raise InvalidGrantError(f"Concessão repetida para '{resource}' / '{action}'.")
        seen.add(pair)
        out.append({"resource": resource, "action": action, "scope": scope})
    out.sort(key=lambda g: (g["resource"], g["action"]))
    return out


def replace_grants(cur: Any, actor_user_id: Any, group_id: Any, grants: Any) -> dict[str, Any]:
    """Substitui o conjunto inteiro de concessoes do grupo personalizado."""
    _require_id(actor_user_id, "actor_user_id")
    new_grants = validate_grants(grants)
    group = _lock_editable_group(cur, group_id)
    gid = group["id"]
    before = _fetch_grants(cur, gid)
    cur.execute("DELETE FROM access_group_grants WHERE group_id = %s", (gid,))
    if new_grants:
        placeholders = ", ".join(["(%s, %s, %s, %s)"] * len(new_grants))
        params: list[Any] = []
        for g in new_grants:
            params.extend((gid, g["resource"], g["action"], g["scope"]))
        cur.execute(
            f"INSERT INTO access_group_grants (group_id, resource, action, scope) VALUES {placeholders}",
            tuple(params),
        )
    _audit(
        cur, actor_user_id, AUDIT_GROUP_GRANTS_REPLACE, TARGET_GROUP, gid,
        {"antes": before, "depois": new_grants},
    )
    group["grants"] = new_grants
    return group


def delete_group(cur: Any, actor_user_id: Any, group_id: Any) -> None:
    """Exclui grupo personalizado sem membros (as concessoes saem por CASCADE)."""
    _require_id(actor_user_id, "actor_user_id")
    group = _lock_editable_group(cur, group_id)
    gid = group["id"]
    if _count_members(cur, gid) > 0:
        raise GroupHasMembersError("Remova os membros antes de excluir o grupo.")
    before = {**_snapshot(group), "grants": _fetch_grants(cur, gid)}
    cur.execute("DELETE FROM access_groups WHERE id = %s", (gid,))
    _audit(cur, actor_user_id, AUDIT_GROUP_DELETE, TARGET_GROUP, gid, {"antes": before, "depois": None})


# --- Membros -----------------------------------------------------------------


def list_members(cur: Any, group_id: Any) -> list[dict[str, Any]]:
    gid = _require_id(group_id, "group_id")
    _load_group(cur, gid, lock=False)
    cur.execute(
        "SELECT u.id AS user_id, u.username, u.full_name, u.role, u.is_active, "
        "uag.created_at, uag.created_by_user_id "
        "FROM user_access_groups uag JOIN users u ON u.id = uag.user_id "
        "WHERE uag.group_id = %s ORDER BY u.username, u.id",
        (gid,),
    )
    out = []
    for row in cur.fetchall() or []:
        created_by = _cell(row, "created_by_user_id", 6)
        out.append(
            {
                "user_id": int(_cell(row, "user_id", 0)),
                "username": _cell(row, "username", 1),
                "full_name": _cell(row, "full_name", 2),
                "role": _cell(row, "role", 3),
                "is_active": bool(int(_cell(row, "is_active", 4) or 0)),
                "created_at": _cell(row, "created_at", 5),
                "created_by_user_id": int(created_by) if created_by is not None else None,
            }
        )
    return out


def _member_preconditions(cur: Any, actor_user_id: Any, group_id: Any, user_id: Any) -> tuple[int, int, int]:
    actor = _require_id(actor_user_id, "actor_user_id")
    uid = _require_id(user_id, "user_id")
    group = _lock_group_for_members(cur, group_id)
    if uid == actor:
        raise SelfMembershipForbiddenError("Você não pode alterar o seu próprio vínculo a grupos.")
    cur.execute("SELECT id FROM users WHERE id = %s", (uid,))
    if not cur.fetchone():
        raise UserNotFoundError(f"Usuário {uid} não encontrado.")
    return actor, group["id"], uid


def _is_member(cur: Any, group_id: int, user_id: int) -> bool:
    cur.execute(
        "SELECT 1 AS member FROM user_access_groups WHERE user_id = %s AND group_id = %s",
        (user_id, group_id),
    )
    return bool(cur.fetchone())


def add_member(cur: Any, actor_user_id: Any, group_id: Any, user_id: Any) -> dict[str, Any]:
    """Associa o usuario ao grupo personalizado. Ja membro: nada grava e
    devolve `already_member = True`."""
    actor, gid, uid = _member_preconditions(cur, actor_user_id, group_id, user_id)
    if _is_member(cur, gid, uid):
        return {"group_id": gid, "user_id": uid, "added": False, "already_member": True}
    cur.execute(
        "INSERT INTO user_access_groups (user_id, group_id, created_by_user_id) VALUES (%s, %s, %s)",
        (uid, gid, actor),
    )
    _audit(
        cur, actor, AUDIT_MEMBER_ADD, TARGET_GROUP, gid,
        {"user_id": uid, "antes": {"membro": False}, "depois": {"membro": True}},
    )
    return {"group_id": gid, "user_id": uid, "added": True, "already_member": False}


def remove_member(cur: Any, actor_user_id: Any, group_id: Any, user_id: Any) -> dict[str, Any]:
    """Remove o usuario do grupo personalizado."""
    actor, gid, uid = _member_preconditions(cur, actor_user_id, group_id, user_id)
    if not _is_member(cur, gid, uid):
        raise MembershipNotFoundError(f"O usuário {uid} não é membro do grupo {gid}.")
    cur.execute("DELETE FROM user_access_groups WHERE user_id = %s AND group_id = %s", (uid, gid))
    _audit(
        cur, actor, AUDIT_MEMBER_REMOVE, TARGET_GROUP, gid,
        {"user_id": uid, "antes": {"membro": True}, "depois": {"membro": False}},
    )
    return {"group_id": gid, "user_id": uid, "removed": True}


# --- Auditoria -----------------------------------------------------------------


def _load_detalhe(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def list_audit(
    cur: Any, group_id: Any = None, limit: Any = AUDIT_LIMIT_DEFAULT, offset: Any = 0
) -> list[dict[str, Any]]:
    """Auditoria mais recente primeiro; `group_id` filtra os eventos do grupo."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not AUDIT_LIMIT_MIN <= limit <= AUDIT_LIMIT_MAX:
        raise InvalidPaginationError(f"limit deve estar entre {AUDIT_LIMIT_MIN} e {AUDIT_LIMIT_MAX}.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise InvalidPaginationError("offset deve ser um inteiro não negativo.")
    sql = "SELECT id, actor_user_id, action, target_type, target_id, detalhe, created_at FROM access_audit_log"
    params: list[Any] = []
    if group_id is not None:
        sql += " WHERE target_type = %s AND target_id = %s"
        params.extend((TARGET_GROUP, _require_id(group_id, "group_id")))
    sql += " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
    params.extend((limit, offset))
    cur.execute(sql, tuple(params))
    out = []
    for row in cur.fetchall() or []:
        actor = _cell(row, "actor_user_id", 1)
        target_id = _cell(row, "target_id", 4)
        out.append(
            {
                "id": int(_cell(row, "id", 0)),
                "actor_user_id": int(actor) if actor is not None else None,
                "action": _cell(row, "action", 2),
                "target_type": _cell(row, "target_type", 3),
                "target_id": int(target_id) if target_id is not None else None,
                "detalhe": _load_detalhe(_cell(row, "detalhe", 5)),
                "created_at": _cell(row, "created_at", 6),
            }
        )
    return out

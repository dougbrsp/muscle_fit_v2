"""Controle de acesso por grupos (RBAC, ADR 0011). Importar nao acessa banco."""

from .catalog import (
    ACTIONS,
    DEFAULT_GRANTS_BY_GROUP,
    DOMAINS,
    FULL_ACCESS_GROUP,
    RESOURCE_KEYS,
    RESOURCES,
    SCOPES,
    SYSTEM_GROUP_KEYS,
    Resource,
    catalog_payload,
    scope_covers,
)
from .deps import declared_permissions, permission_of, require_permission
from .service import (
    EffectivePermissions,
    authorize_target,
    check_catalog,
    has_permission,
    permission_scope,
    resolve_permissions,
    serialize_permissions,
)

__all__ = [
    "ACTIONS",
    "DEFAULT_GRANTS_BY_GROUP",
    "DOMAINS",
    "EffectivePermissions",
    "FULL_ACCESS_GROUP",
    "RESOURCES",
    "RESOURCE_KEYS",
    "Resource",
    "SCOPES",
    "SYSTEM_GROUP_KEYS",
    "authorize_target",
    "catalog_payload",
    "check_catalog",
    "declared_permissions",
    "has_permission",
    "permission_of",
    "permission_scope",
    "require_permission",
    "resolve_permissions",
    "scope_covers",
    "serialize_permissions",
]

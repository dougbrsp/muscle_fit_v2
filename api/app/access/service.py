"""Resolucao das permissoes efetivas por grupos (RBAC, ADR 0011).

Concessoes aditivas, negado por padrao: a uniao dos grupos do usuario; para o
mesmo (recurso, acao) vence o escopo mais abrangente. Token mestre e membro do
grupo `admin` tem acesso total (todos os recursos do catalogo, escopo `todos`).
Dispositivo usa o `user_id` do dono (a `Identity` ja vem assim). Usuario sem
grupo nao tem permissao alguma: NAO ha fallback para `users.role`.

Banco: os helpers do projeto (`app.db`, Data API ou pymysql). Uma consulta por
resolucao (grupos + concessoes num JOIN); `authorize_target` soma uma segunda
so para alvo de terceiro com escopo `alunos`. Nada de banco em tempo de import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from fastapi import HTTPException, status

from .. import db as _db
from ..security import Identity, _auth_backend_unavailable
from . import catalog

# 403 de negacao: mesmo formato `{"error", "message"}` usado nas rotas de nutricao.
FORBIDDEN_DETAIL = {
    "error": "forbidden",
    "message": "Você não tem permissão para esta ação.",
}

_GRANTS_SQL = (
    "SELECT g.group_key, g.is_system, gg.resource, gg.action, gg.scope "
    "FROM user_access_groups m "
    "JOIN access_groups g ON g.id = m.group_id "
    "LEFT JOIN access_group_grants gg ON gg.group_id = g.id "
    "WHERE m.user_id = %s"
)
_ACTIVE_LINK_SQL = (
    "SELECT 1 AS ok FROM trainer_students "
    "WHERE trainer_user_id = %s AND student_user_id = %s AND status = 'ativo' LIMIT 1"
)


@dataclass(frozen=True)
class EffectivePermissions:
    """Permissoes efetivas: `grants[recurso][acao] = escopo` (so o concedido)."""

    grants: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    group_keys: tuple[str, ...] = ()
    full_access: bool = False


def check_catalog(resource: str, action: str) -> None:
    """Recurso/acao fora do catalogo e erro de programacao, nunca negacao."""
    if resource not in catalog.RESOURCES_BY_KEY:
        raise ValueError(f"recurso fora do catálogo de acesso: {resource!r}")
    if action not in catalog.ACTIONS:
        raise ValueError(f"ação fora do catálogo de acesso: {action!r}")


def normalize_is_system(value: Any) -> bool:
    """`access_groups.is_system` vindo da Data API (bool/int/str) ou do pymysql
    (int). So 1/True/"1"/"true" (sem caixa/espacos) e de sistema; ausente, nulo
    ou nao reconhecido = NAO e de sistema (negado por padrao)."""
    if value is True:
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, (str, bytes)):
        text = value.decode("utf-8", "ignore") if isinstance(value, bytes) else value
        return text.strip().lower() in ("1", "true")
    return False


def _full_access(group_keys: tuple[str, ...] = ()) -> EffectivePermissions:
    grants = {
        key: {action: catalog.SCOPE_TODOS for action in catalog.ACTIONS}
        for key in catalog.RESOURCE_KEYS
    }
    return EffectivePermissions(grants=grants, group_keys=group_keys, full_access=True)


def _fetch_rows(sql: str, params: tuple[Any, ...], step: str) -> list[dict[str, Any]]:
    try:
        return list(_db.fetch_rows(sql, params))
    except Exception as exc:  # noqa: BLE001 - falha de consulta vira 503, nunca 401/403
        raise _auth_backend_unavailable(step, exc) from exc


def merge_grants(
    rows: list[Mapping[str, Any]],
) -> EffectivePermissions:
    """Une as linhas `(group_key, resource, action, scope)` (resource pode ser
    NULL: grupo sem concessao). Concessao de recurso/acao/escopo que nao existe
    mais no catalogo e ignorada (negado por padrao)."""
    group_keys: list[str] = []
    grants: dict[str, dict[str, str]] = {}
    full_access = False
    for row in rows:
        group_key = str(row["group_key"])
        if group_key not in group_keys:
            group_keys.append(group_key)
        # Acesso total exige a chave do grupo E is_system; grupo nao-sistema com
        # a mesma chave e grupo comum (vale so o que esta em access_group_grants).
        if group_key == catalog.FULL_ACCESS_GROUP and normalize_is_system(row.get("is_system")):
            full_access = True
        resource, action, scope = row.get("resource"), row.get("action"), row.get("scope")
        if resource is None or action is None or scope is None:
            continue
        if (
            resource not in catalog.RESOURCES_BY_KEY
            or action not in catalog.ACTIONS
            or scope not in catalog.SCOPE_RANK
        ):
            continue
        current = grants.setdefault(str(resource), {}).get(str(action))
        if current is None or catalog.SCOPE_RANK[scope] > catalog.SCOPE_RANK[current]:
            grants[str(resource)][str(action)] = str(scope)
    keys = tuple(group_keys)
    if full_access:
        return _full_access(keys)
    return EffectivePermissions(grants=grants, group_keys=keys)


def resolve_permissions(identity: Identity) -> EffectivePermissions:
    """Permissoes efetivas da identidade. Token mestre: acesso total, sem banco.
    Demais: 1 consulta (grupos + concessoes). 503 se o banco falhar."""
    if identity.source == catalog.MASTER_TOKEN_SOURCE:
        return _full_access((catalog.FULL_ACCESS_GROUP,))
    rows = _fetch_rows(_GRANTS_SQL, (int(identity.user_id),), "access_grants")
    return merge_grants(rows)


def permission_scope(
    permissoes: EffectivePermissions, resource: str, action: str
) -> Optional[str]:
    """Escopo concedido para (recurso, acao) ou None."""
    check_catalog(resource, action)
    return permissoes.grants.get(resource, {}).get(action)


def has_permission(permissoes: EffectivePermissions, resource: str, action: str) -> bool:
    """Ha alguma concessao (qualquer escopo) para (recurso, acao)."""
    return permission_scope(permissoes, resource, action) is not None


def forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=dict(FORBIDDEN_DETAIL))


def require(permissoes: EffectivePermissions, resource: str, action: str) -> None:
    """403 se faltar a concessao."""
    if not has_permission(permissoes, resource, action):
        raise forbidden()


def has_active_link(trainer_user_id: int, student_user_id: int) -> bool:
    rows = _fetch_rows(
        _ACTIVE_LINK_SQL,
        (int(trainer_user_id), int(student_user_id)),
        "trainer_link",
    )
    return bool(rows)


INVALID_TARGET_DETAIL = {
    "error": "invalid_target",
    "message": "Usuário alvo inválido: informe um id inteiro positivo.",
}


def invalid_target() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail=dict(INVALID_TARGET_DETAIL)
    )


def validate_target_user_id(target_user_id: Any, *, required: bool = False) -> Optional[int]:
    """Id de usuario alvo estrito: `int > 0` (ou `None`, se `required` for falso).
    `bool`, `float`, `str` e `int <= 0` levantam 400 `invalid_target`; nada de
    coercao (`"10"` e `10.0` nao viram 10). Roda ANTES de qualquer consulta de
    permissao ou de vinculo."""
    if target_user_id is None:
        if required:
            raise invalid_target()
        return None
    if isinstance(target_user_id, bool) or not isinstance(target_user_id, int) or target_user_id <= 0:
        raise invalid_target()
    return target_user_id


def authorize_target(
    identity: Identity,
    resource: str,
    action: str,
    target_user_id: Optional[int] = None,
    *,
    permissoes: Optional[EffectivePermissions] = None,
) -> int:
    """Autoriza `action` em `resource` sobre os dados de `target_user_id` e
    devolve o id do usuario alvo.

    `target_user_id` aceita `None` ou `int > 0`; qualquer outro valor (`bool`,
    `float`, `str`, `int <= 0`) levanta 400 `invalid_target`, antes de qualquer
    consulta. `identity.user_id` invalido e erro de programacao (nao vira 400).

    Alvo ausente ou o proprio: qualquer escopo basta. Terceiro: `todos`, ou
    `alunos` com vinculo ativo em `trainer_students`. Negacao = 403, como
    `nutrition_access.resolve_target_user` (404 la so existe em
    `resolve_plan_owner`, que esconde a existencia de um plano por id; aqui o
    alvo e um usuario informado pelo chamador). `permissoes` evita nova consulta
    quando o chamador ja resolveu (ex.: `require_permission`).

    NAO valida a existencia nem a atividade do usuario alvo: o chamador faz isso
    (como `nutrition_access.py` faz hoje, com 404). Vinculo ativo com aluno
    inativo e decisao do lote 5."""
    check_catalog(resource, action)
    target = validate_target_user_id(target_user_id)
    own_id = int(identity.user_id)
    perms = permissoes if permissoes is not None else resolve_permissions(identity)
    scope = permission_scope(perms, resource, action)
    if scope is None:
        raise forbidden()
    if target is None or target == own_id:
        return own_id
    if scope == catalog.SCOPE_TODOS:
        return target
    if scope == catalog.SCOPE_ALUNOS and has_active_link(own_id, target):
        return target
    raise forbidden()


def authorize_self_or_all(
    identity: Identity,
    resource: str,
    action: str,
    target_user_id: int,
    *,
    permissoes: Optional[EffectivePermissions] = None,
) -> int:
    """Autoriza o proprio usuario ou quem tem a permissao sobre terceiros; devolve o alvo.

    `target_user_id` e obrigatorio e estrito (`int > 0`, senao 400
    `invalid_target`). Alvo igual ao `user_id` da identidade passa SEM exigir
    concessao e sem consultar o banco. Alvo diferente exige `(recurso, acao)`
    com escopo `todos`; qualquer outro caso e 403 `FORBIDDEN_DETAIL`. O escopo
    `alunos` NAO libera aqui (nao consulta vinculo): esta funcao e para recursos
    administrativos, nao para a relacao treinador-aluno (use `authorize_target`).
    Recurso/acao fora do catalogo: `ValueError`. Falha de banco: 503."""
    check_catalog(resource, action)
    target = validate_target_user_id(target_user_id, required=True)
    if target == int(identity.user_id):
        return target
    perms = permissoes if permissoes is not None else resolve_permissions(identity)
    scope = permission_scope(perms, resource, action)
    if scope != catalog.SCOPE_TODOS:
        raise forbidden()
    return target


def owns_row_or_all(
    identity: Identity,
    permissoes: EffectivePermissions,
    resource: str,
    action: str,
    owner_user_id: Optional[int],
) -> bool:
    """Posse da linha (ADR 0011): True se a linha e do usuario ou se o escopo
    concedido para `(recurso, acao)` e `todos`.

    Dono: `owner_user_id` nao nulo e igual ao `user_id` da identidade, com
    qualquer escopo concedido. Linha orfa (`owner_user_id` None) so passa com
    `todos`. Escopo `alunos` nao basta (sem consulta de vinculo). Sem concessao
    = False. Nao levanta nada: o chamador escolhe a recusa (404 para esconder a
    existencia, 403 quando o id ja veio explicito). Sem banco: usa as
    permissoes ja resolvidas na requisicao. Recurso/acao fora do catalogo:
    `ValueError`."""
    scope = permission_scope(permissoes, resource, action)
    if scope is None:
        return False
    if scope == catalog.SCOPE_TODOS:
        return True
    if owner_user_id is None:
        return False
    return int(owner_user_id) == int(identity.user_id)


def serialize_permissions(permissoes: EffectivePermissions) -> dict[str, Any]:
    """Payload de `/auth/me`: `{"permissions": {recurso: {acao: escopo}},
    "groups": [chave, ...]}`, so com o que foi concedido."""
    return {
        "permissions": {
            resource: dict(actions) for resource, actions in permissoes.grants.items() if actions
        },
        "groups": list(permissoes.group_keys),
    }

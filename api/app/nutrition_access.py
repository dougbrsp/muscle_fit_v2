"""Quem vê e quem define o plano nutricional de quem (plano de nutrição §5 D1,
decisão D-L; Lote L2b).

Regra de hoje (decisão do usuário, 2026-10-14): o próprio usuário passa;
TERCEIRO exige admin ou personal trainer (`security.require_admin_or_pt`), e o
personal trainer vê/define para QUALQUER aluno — sem vínculo formal PT↔aluno,
como nos Modelos e nas Metas de Ativação. Centralizado aqui para que o vínculo
formal (L11) troque a regra num lugar só: as rotas de nutrição nunca checam
papel por conta própria.

Catálogo de alimentos (L3a, D-G): quem vê, usa, edita, promove e cria porção
de um alimento também é decidido só aqui (`can_*_food`, `portion_owner_for`).
Diário (L4): o diário de um aluno segue `resolve_target_user`; alimento e
porção que entram nele, `can_log_food`/`can_log_portion`.

Sem regra numérica (nada de motor aqui).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, HTTPException

from .domain.nutrition import catalog
from .security import Identity, get_current_identity, require_admin_or_pt

_USER_SQL = "SELECT id, username, full_name FROM users WHERE id = %s"


def require_plan_manager(identity: Identity = Depends(get_current_identity)) -> Identity:
    """Dependência das rotas que criam/editam/publicam planos: admin ou PT."""
    return require_admin_or_pt(identity)


def resolve_target_user(identity: Identity, user_id: Optional[int]) -> int:
    """Aluno-alvo da chamada. Sem `user_id` ou o próprio -> o usuário logado;
    terceiro -> exige admin/PT (403 senão)."""
    if user_id is None or int(user_id) == int(identity.user_id):
        return int(identity.user_id)
    require_admin_or_pt(identity)
    return int(user_id)


def fetch_target_user(cur: Any, user_id: int) -> dict[str, Any]:
    """`{id, username, full_name}` do aluno ou 404."""
    cur.execute(_USER_SQL, (int(user_id),))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "user_not_found", "message": "Usuário não encontrado."})
    return {"id": int(row["id"]), "username": row.get("username"), "full_name": row.get("full_name")}


# ---------------------------------------------------------------------------
# Catálogo de alimentos (Lote L3a, decisão D-G): alimento cadastrado pelo
# usuário nasce PRIVADO (`owner_user_id` = ele); público = `owner_user_id` NULL
# (TACO e o que o admin promoveu). Fonte somente leitura (TACO) não se edita;
# a regra de QUAIS fontes é do motor (`catalog.is_read_only`).
# ---------------------------------------------------------------------------

ADMIN_ROLE = "admin"


def _owner(food: dict[str, Any]) -> Optional[int]:
    dono = food.get("owner_user_id")
    return None if dono is None else int(dono)


def is_admin(identity: Identity) -> bool:
    return identity.role == ADMIN_ROLE


def can_view_food(identity: Identity, food: dict[str, Any]) -> bool:
    """Público, do próprio usuário ou (admin) qualquer um — o admin precisa ver
    o privado para promovê-lo."""
    dono = _owner(food)
    return dono is None or dono == int(identity.user_id) or is_admin(identity)


def can_use_food(identity: Identity, food: dict[str, Any]) -> bool:
    """Entra na busca/diário do usuário: público ou dele (admin incluído)."""
    dono = _owner(food)
    return dono is None or dono == int(identity.user_id)


def can_edit_food(identity: Identity, food: dict[str, Any]) -> bool:
    """O dono edita o privado; o admin edita o público. Fonte somente leitura nunca."""
    if catalog.is_read_only(food.get("source")):
        return False
    dono = _owner(food)
    return dono == int(identity.user_id) if dono is not None else is_admin(identity)


def can_add_portion(identity: Identity, food: dict[str, Any]) -> bool:
    return can_use_food(identity, food)


def portion_owner_for(identity: Identity, food: dict[str, Any]) -> Optional[int]:
    """Dono da porção nova: o admin num alimento público cria porção pública;
    no resto a porção é do próprio usuário."""
    if _owner(food) is None and is_admin(identity):
        return None
    return int(identity.user_id)


def can_promote_food(identity: Identity, food: dict[str, Any]) -> bool:
    return is_admin(identity) and _owner(food) is not None


def can_list_private_foods(identity: Identity) -> bool:
    """Só o admin lista os alimentos privados de todos (para promover)."""
    return is_admin(identity)


def ownership(identity: Identity, owner_user_id: Any) -> dict[str, bool]:
    """`{is_public, is_own}` de um alimento ou porção para ESTE usuário."""
    dono = None if owner_user_id is None else int(owner_user_id)
    return {"is_public": dono is None, "is_own": dono is not None and dono == int(identity.user_id)}


# Condições de visibilidade em SQL (a regra é a de `can_use_food`/D-G; o SQL só
# a repete para filtrar no banco). Cada uma leva os parâmetros da função.
VISIBLE_FOODS_SQL = "(owner_user_id IS NULL OR owner_user_id = %s)"
PRIVATE_FOODS_SQL = "owner_user_id IS NOT NULL"
VISIBLE_PORTIONS_SQL = "(owner_user_id IS NULL OR owner_user_id IN (%s, %s))"
# Alimentos que o diário do aluno pode usar (a regra de `can_log_food`, sem o
# `is_active`, que a busca já filtra): públicos, do aluno ou de quem registra.
DIARY_FOODS_SQL = "(owner_user_id IS NULL OR owner_user_id IN (%s, %s))"


def foods_visibility(identity: Identity, *, private_all: bool) -> tuple[str, tuple[Any, ...]]:
    """Filtro da busca: públicos + os do usuário; ou (admin) os privados de todos."""
    if private_all:
        return PRIVATE_FOODS_SQL, ()
    return VISIBLE_FOODS_SQL, (int(identity.user_id),)


def diary_foods_visibility(registrant_user_id: int, diary_user_id: int) -> tuple[str, tuple[Any, ...]]:
    """Filtro da busca do worker da leitura por IA (L5b): os alimentos que
    `can_log_food` aceitaria no diário do aluno `diary_user_id` registrado por
    `registrant_user_id` (o mesmo usuário quando é o próprio diário)."""
    return DIARY_FOODS_SQL, (int(diary_user_id), int(registrant_user_id))


def can_log_food(identity: Identity, diary_user_id: int, food: dict[str, Any]) -> bool:
    """Alimento que entra no diário do aluno `diary_user_id` (L4): ativo e
    público, do aluno ou de quem está registrando (o admin/PT registrando para
    o aluno usa os próprios cadastros; quem não é admin/PT só chega aqui para o
    próprio diário, `resolve_target_user`)."""
    if not food.get("is_active", 1):
        return False
    dono = _owner(food)
    return dono is None or dono in (int(diary_user_id), int(identity.user_id))


def can_log_portion(identity: Identity, diary_user_id: int, portion: dict[str, Any]) -> bool:
    """Porção que o diário do aluno pode usar: pública, do aluno ou de quem registra."""
    dono = _owner(portion)
    return dono is None or dono in (int(diary_user_id), int(identity.user_id))


def portions_visibility(identity: Identity, food: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    """Porções visíveis de um alimento: as públicas, as do usuário e, num
    alimento PRIVADO, as do dono dele (o admin que vai promover vê o que será
    promovido junto)."""
    dono = _owner(food)
    return VISIBLE_PORTIONS_SQL, (int(identity.user_id), dono if dono is not None else int(identity.user_id))


__all__ = [
    "DIARY_FOODS_SQL",
    "PRIVATE_FOODS_SQL",
    "VISIBLE_FOODS_SQL",
    "VISIBLE_PORTIONS_SQL",
    "can_add_portion",
    "can_edit_food",
    "can_list_private_foods",
    "can_log_food",
    "can_log_portion",
    "can_promote_food",
    "can_use_food",
    "can_view_food",
    "diary_foods_visibility",
    "fetch_target_user",
    "foods_visibility",
    "is_admin",
    "ownership",
    "portion_owner_for",
    "portions_visibility",
    "require_plan_manager",
    "resolve_target_user",
]

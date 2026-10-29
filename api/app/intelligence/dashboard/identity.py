"""Guardiao de identidade do Novo Dashboard (plano §9, §15).

Todo servico novo em `intelligence/dashboard/` chama `require_user_id` antes
de montar qualquer SQL: `WHERE user_id = %s` e obrigatorio, e `None` nunca
vira "sem filtro" (a lacuna historica dos servicos antigos, documentada em
`IntelligenceServiceBase.get_latest_intervals_wellness` e corrigida no
`DashboardAggregatorService` neste mesmo PR).
"""

from __future__ import annotations

from typing import Any


def require_user_id(user_id: Any) -> int:
    """Devolve `user_id` como `int` positivo ou lanca `ValueError`.

    - `None` -> `ValueError("user_id obrigatorio")`;
    - bool, nao numerico, zero ou negativo -> `ValueError("user_id invalido: ...")`.
    """
    if user_id is None:
        raise ValueError("user_id obrigatorio")
    if isinstance(user_id, bool):
        raise ValueError(f"user_id invalido: {user_id!r}")
    try:
        value = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"user_id invalido: {user_id!r}") from exc
    if isinstance(user_id, float) and not user_id.is_integer():
        raise ValueError(f"user_id invalido: {user_id!r}")
    if value <= 0:
        raise ValueError(f"user_id invalido: {user_id!r}")
    return value


__all__ = ["require_user_id"]

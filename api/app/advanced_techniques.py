"""Catalogo de tecnicas avancadas (tabela `advanced_techniques`, migracao 031).

Fonte unica dos codigos de tecnica aceitos pelo builder de modelos
(`WorkoutPlanTemplateSlotItemIn.advanced_technique`) e consumidos pelo
frontend via `GET /advanced-techniques` (api-core, catch-all `{proxy+}`).

Custo: o catalogo tem <= algumas dezenas de linhas e muda raramente. Fica em
cache por processo (TTL curto) - uma leitura por Lambda quente a cada
`CACHE_TTL_SECONDS`, e nenhuma para payloads sem tecnica. Sem DDL em runtime:
a tabela vem da migracao 031, que roda antes do deploy do codigo.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Iterable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .db import transaction
from .security import get_current_identity
from .set_labels import technique_names

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300

router = APIRouter(
    prefix="/advanced-techniques",
    tags=["advanced-techniques"],
    dependencies=[Depends(get_current_identity)],
)

_cache_lock = threading.Lock()
_cache: Optional[tuple[float, list[dict[str, Any]]]] = None


def _to_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def _row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": row["code"],
        "name": row["name"],
        "failure_requirement": row["failure_requirement"],
        "requires_partner": bool(int(row["requires_partner"] or 0)),
        "auto_applicable": bool(int(row["auto_applicable"] or 0)),
        "min_experience": row["min_experience"],
        "set_type": row["set_type"],
        "activation_set_equivalent": _to_float(row["activation_set_equivalent"]) or 0.0,
        "recovery_cost_factor": _to_float(row["recovery_cost_factor"]),
        # Coeficientes da migracao 060 (Fase 5): o Modo Treino espelha a regra
        # de `domain/technique_accounting.py` com eles. NULL = nao calibrado.
        "max_equivalent_per_anchor": _to_float(row.get("max_equivalent_per_anchor")),
        "default_sets_count": None if row.get("default_sets_count") is None else int(row["default_sets_count"]),
        "is_active": bool(int(row["is_active"] or 0)),
    }


def _load_catalog() -> list[dict[str, Any]]:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT code, name, failure_requirement, requires_partner, auto_applicable,
                       min_experience, set_type, activation_set_equivalent,
                       recovery_cost_factor, max_equivalent_per_anchor,
                       default_sets_count, is_active
                FROM advanced_techniques
                ORDER BY code
                """
            )
            return [_row_to_item(row) for row in cur.fetchall() or []]


def clear_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def list_techniques(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Catalogo (copia) a partir do cache do processo. Erro de banco propaga:
    cache vazio nunca e gravado no lugar de uma falha."""
    global _cache
    now = time.monotonic()
    with _cache_lock:
        cached = _cache
    if cached is None or now - cached[0] >= CACHE_TTL_SECONDS:
        items = _load_catalog()
        with _cache_lock:
            _cache = (now, items)
    else:
        items = cached[1]
    return [dict(item) for item in items if include_inactive or item["is_active"]]


def active_codes() -> set[str]:
    return {item["code"] for item in list_techniques()}


def technique_display_names() -> dict[str, str]:
    """`{code: nome}` do catalogo (inclusive inativas) para os prompts de IA
    (M95, M95b, M95c): o prompt nunca recebe o codigo cru da tecnica. Leitura
    unica para todos os consumidores (resumo de treino, resumo de exercicio,
    avaliacao de serie, insights). Falha de leitura -> `{}` e log: o prompt
    sai com o rotulo generico de `set_labels`, nunca derruba a IA."""
    try:
        return technique_names(list_techniques(include_inactive=True))
    except Exception:  # noqa: BLE001 - rotulo legivel e contexto opcional
        logger.exception("advanced_techniques.names_read_failed")
        return {}


def ensure_valid_codes(codes: Iterable[Optional[str]]) -> None:
    """422 se algum codigo nao nulo estiver fora do catalogo ATIVO. Nao toca
    o banco quando nenhum codigo foi informado."""
    wanted = {code for code in codes if code is not None}
    if not wanted:
        return
    invalid = sorted(wanted - active_codes())
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Técnica avançada inválida ou inativa: {', '.join(invalid)}. "
                "Consulte GET /advanced-techniques para os códigos aceitos."
            ),
        )


@router.get("")
def get_advanced_techniques(include_inactive: bool = Query(default=False)):
    return {"items": list_techniques(include_inactive=include_inactive)}

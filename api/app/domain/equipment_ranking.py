"""Ranking UNICO de equipamento por usuario (PR-08 da refatoracao dos motores;
PLANO_20260912.md secoes 4 "Selecao" e 6.1 passos 4-5).

Ordem deterministica entre candidatos equivalentes:

    (stars DO PAR exercicio+equipamento DESC [ausente = 3], last_used_by_user ASC NULLS FIRST,
     equipment.name ASC, gym_equipment.id ASC)

"Variar entre os de maior preferencia" = recencia DO USUARIO dentro da mesma
faixa de estrelas; so depois vem as demais faixas.

Seguranca (D3): hoje nenhum equipamento tem seguranca cadastrada, entao ela
NUNCA filtra por padrao - so informa. `split_by_required_technique` so exclui
quando o chamador passa a tecnica explicitamente.

Funcoes PURAS (sem banco, sem FastAPI). O acesso a dados fica em
`app/equipment_recommender.py`, que tambem aplica este ranking para
`services.resolve_equipment_id` (nao existe uma segunda definicao da ordem
em SQL).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from . import training_safety as ts

DEFAULT_STARS = 3
MIN_STARS = 1
MAX_STARS = 5

# excluded[].reason
TECHNIQUE_INCOMPATIBLE = "TECHNIQUE_INCOMPATIBLE"
FAILURE_REQUIRED_NOT_ALLOWED = ts.FAILURE_REQUIRED_NOT_ALLOWED


def effective_stars(value: Any) -> int:
    """Estrelas usadas no ranking: ausente (ou valor sujo) = 3, neutro."""
    if value is None or isinstance(value, bool):
        return DEFAULT_STARS
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_STARS
    return number if MIN_STARS <= number <= MAX_STARS else DEFAULT_STARS


def ranking_key(item: Mapping[str, Any]) -> tuple:
    """Chave de ordenacao de um item do recomendador (campos do contrato:
    `user_preference_stars`, `last_used_by_user_at`, `equipment_name`, `id`)."""
    last_used = item.get("last_used_by_user_at")
    return (
        -effective_stars(item.get("user_preference_stars")),
        last_used is not None,  # NULLS FIRST: nunca usado pelo usuario vem antes
        "" if last_used is None else str(last_used),
        str(item.get("equipment_name") or "").casefold(),
        str(item.get("equipment_name") or ""),
        int(item.get("id") or 0),
    )


def rank_items(items: Iterable[Mapping[str, Any]]) -> list[Any]:
    return sorted(items, key=ranking_key)


def exclusion_reason(safety: ts.TrainingSafety, required_technique: str) -> Optional[str]:
    """None = o par aceita a tecnica. Falha exigida em par sem falha permitida
    -> FAILURE_REQUIRED_NOT_ALLOWED (mesmo se marcada compativel); qualquer
    outro motivo (sem marcacao, negada, fora do catalogo) -> TECHNIQUE_INCOMPATIBLE."""
    validation = ts.validate_technique(required_technique, safety)
    if validation.valid:
        return None
    if validation.reason == ts.FAILURE_REQUIRED_NOT_ALLOWED:
        return FAILURE_REQUIRED_NOT_ALLOWED
    return TECHNIQUE_INCOMPATIBLE


def safety_fields(safety: ts.TrainingSafety) -> dict[str, Any]:
    """Campos de seguranca embutidos no item de recomendacao (secao 9)."""
    return {
        "max_safe_rpe": safety.max_safe_rpe,
        "failure_allowed": safety.failure_allowed,
        "allowed_advanced_techniques": [item.to_dict() for item in safety.allowed_techniques],
        "safety_source": safety.safety_source,
    }


def excluded_entry(item: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "gym_equipment_id": item.get("id"),
        "id": item.get("id"),
        "equipment_id": item.get("equipment_id"),
        "equipment_name": item.get("equipment_name"),
        "mapping_id": item.get("mapping_id"),
        "mapping_exercise_name": item.get("mapping_exercise_name"),
        "primary_group": item.get("primary_group"),
        "user_preference_stars": item.get("user_preference_stars"),
        "reason": reason,
        "max_safe_rpe": item.get("max_safe_rpe"),
        "failure_allowed": item.get("failure_allowed"),
    }


def split_by_required_technique(
    ranked_items: Iterable[Mapping[str, Any]],
    safety_by_item_id: Mapping[Any, ts.TrainingSafety],
    required_technique: Optional[str],
) -> tuple[list[Any], list[dict[str, Any]]]:
    """(itens mantidos, excluded[]), preservando a ordem recebida. Sem
    `required_technique` NADA e excluido (D3)."""
    items = list(ranked_items)
    if not required_technique:
        return items, []
    kept: list[Any] = []
    excluded: list[dict[str, Any]] = []
    for item in items:
        safety = safety_by_item_id.get(item.get("id"))
        reason = exclusion_reason(safety, required_technique) if safety is not None else TECHNIQUE_INCOMPATIBLE
        if reason is None:
            kept.append(item)
        else:
            excluded.append(excluded_entry(item, reason))
    return kept, excluded

"""Motor de ficha v2 (USE_TEMPLATE_VOLUME_ENGINE_V2) — Fase 3: matriz de
candidatos.

`build_candidate_matrix` REEMPACOTA o resultado já existente de
`GET /gym-equipment/recommend-all` (`gym_equipment_recommendation`, já
carregado por `gather_ficha_context`/`context["gym_equipment_recommendation"]`
— nenhuma query nova a `gym_equipment`) e faz UMA ÚNICA chamada batch a
`_resolve_muscle_contributions_for_mappings` para TODOS os candidatos de
TODOS os grupos pedidos, evitando N+1 (seção 24 do documento: "Antes da IA,
montar os exercícios válidos... Fase 2 — matriz de candidatos").
"""

from __future__ import annotations

from typing import Any, Iterable, Optional


def _candidates_for_group(
    group_id: int, gym_equipment_recommendation: Optional[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not gym_equipment_recommendation:
        return []
    groups_by_id = {
        int(key): value
        for key, value in (gym_equipment_recommendation.get("groups_by_id") or {}).items()
        if str(key).lstrip("-").isdigit()
    }
    group_info = groups_by_id.get(group_id) or {}
    return [c for c in [group_info.get("recommended"), *(group_info.get("alternatives") or [])] if c]


def build_candidate_matrix(
    muscle_group_ids: Iterable[int],
    gym_equipment_recommendation: Optional[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    """Para cada `muscle_group_id` em `muscle_group_ids`, retorna a lista de
    candidatos válidos (do catálogo da academia, já filtrado, ordenados do
    equipamento menos usado recentemente para o mais usado — mesma ordem de
    `gym_equipment_recommendation`), cada um enriquecido com
    `muscle_contributions` (lista de `{muscle_group_id, muscle_group_name,
    role, stimulus_weight}`, seção 24/25 do documento — a IA (Fase 4) só
    pode escolher `mapping_id` presentes aqui, nunca inventar contribuição).

    Uma única chamada batch a `_resolve_muscle_contributions_for_mappings`
    para todos os `mapping_id` de todos os grupos — sem N+1 mesmo com
    dezenas de grupos/candidatos (ver `tests/test_candidate_exercise_provider.py`,
    que conta chamadas mockadas)."""
    from .muscle_contribution_resolver import _resolve_muscle_contributions_for_mappings

    group_ids = list(muscle_group_ids)
    candidates_by_group: dict[int, list[dict[str, Any]]] = {
        group_id: _candidates_for_group(group_id, gym_equipment_recommendation) for group_id in group_ids
    }

    all_mapping_ids: set[int] = {
        candidate["mapping_id"]
        for candidates in candidates_by_group.values()
        for candidate in candidates
        if candidate.get("mapping_id")
    }

    contributions_by_mapping_id = _resolve_muscle_contributions_for_mappings(all_mapping_ids)

    matrix: dict[int, list[dict[str, Any]]] = {}
    for group_id, candidates in candidates_by_group.items():
        enriched: list[dict[str, Any]] = []
        for candidate in candidates:
            mapping_id = candidate.get("mapping_id")
            contributions = contributions_by_mapping_id.get(mapping_id, []) if mapping_id else []
            enriched.append(
                {
                    "mapping_id": mapping_id,
                    "exercise_name": candidate.get("mapping_exercise_name"),
                    "amazfit_name": candidate.get("mapping_amazfit_name"),
                    "equipment_id": candidate.get("equipment_id"),
                    "equipment_name": candidate.get("equipment_name"),
                    "primary_group_id": group_id,
                    "muscle_contributions": [
                        {
                            "muscle_group_id": c.get("muscle_group_id"),
                            "muscle_group_name": c.get("muscle_group_name"),
                            "role": c.get("role"),
                            "stimulus_weight": c.get("stimulus_weight"),
                        }
                        for c in contributions
                    ],
                }
            )
        matrix[group_id] = enriched

    return matrix


# ---------------------------------------------------------------------------
# PR-13 (Modo A deterministico) - matriz de candidatos do ranking novo
# ---------------------------------------------------------------------------


def build_ranked_candidate_matrix(
    muscle_group_ids: Iterable[int],
    gym_equipment_recommendation: Optional[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    """Mesma matriz de `build_candidate_matrix`, mas para o motor
    DETERMINISTICO (secao 6.1 passos 3-5): cada candidato carrega tambem o que
    o ranking e o filtro de seguranca precisam ler - `gym_equipment_id`,
    `user_preference_stars` (nota do PAR exercicio+equipamento),
    `last_used_by_user_at`, `max_safe_rpe`, `failure_allowed`,
    `allowed_advanced_techniques`, `safety_source` - e o `fatigue_weight` de
    cada contribuicao (secao 6.1 passo 8, ordenacao por fadiga compartilhada).

    Por que uma funcao separada em vez de enriquecer `build_candidate_matrix`:
    a matriz antiga vai SERIALIZADA no prompt do seletor com LLM
    (`template_ai_selector_v2`, so no caminho legado). Acrescentar campos la
    mudaria o prompt do motor legado - e a promessa da flag e que, com
    `GENERATION_ENGINE=legacy`, nada muda. As duas funcoes compartilham
    `_candidates_for_group` e a mesma chamada batch de contribuicoes.

    O payload de entrada e o do recomendador COMPLETO
    (`equipment_recommender.recommend_all`), nao o enxugado por
    `workout_plan_ai._trim_gym_equipment_recommendation` - que remove
    justamente estrelas/recencia/seguranca. Campo ausente vira `None` (o
    ranking trata: estrelas ausentes = 3).
    """
    from .muscle_contribution_resolver import _resolve_muscle_contributions_for_mappings

    candidates_by_group: dict[int, list[dict[str, Any]]] = {
        int(group_id): _candidates_for_group(int(group_id), gym_equipment_recommendation)
        for group_id in muscle_group_ids
    }
    all_mapping_ids: set[int] = {
        candidate["mapping_id"]
        for candidates in candidates_by_group.values()
        for candidate in candidates
        if candidate.get("mapping_id")
    }
    contributions_by_mapping_id = _resolve_muscle_contributions_for_mappings(all_mapping_ids)

    matrix: dict[int, list[dict[str, Any]]] = {}
    for group_id, candidates in candidates_by_group.items():
        enriched: list[dict[str, Any]] = []
        for candidate in candidates:
            mapping_id = candidate.get("mapping_id")
            contributions = contributions_by_mapping_id.get(mapping_id, []) if mapping_id else []
            enriched.append(
                {
                    "mapping_id": mapping_id,
                    "exercise_name": candidate.get("mapping_exercise_name"),
                    "amazfit_name": candidate.get("mapping_amazfit_name"),
                    "equipment_id": candidate.get("equipment_id"),
                    "equipment_name": candidate.get("equipment_name"),
                    # `id` do item do recomendador = `gym_equipment.id`: ultimo
                    # criterio de desempate do ranking (passo 5).
                    "gym_equipment_id": candidate.get("gym_equipment_id") or candidate.get("id"),
                    "primary_group_id": group_id,
                    "user_preference_stars": candidate.get("user_preference_stars"),
                    "last_used_by_user_at": candidate.get("last_used_by_user_at"),
                    "max_safe_rpe": candidate.get("max_safe_rpe"),
                    "failure_allowed": candidate.get("failure_allowed"),
                    "allowed_advanced_techniques": candidate.get("allowed_advanced_techniques") or [],
                    "safety_source": candidate.get("safety_source"),
                    "muscle_contributions": [
                        {
                            "muscle_group_id": c.get("muscle_group_id"),
                            "muscle_group_name": c.get("muscle_group_name"),
                            "role": c.get("role"),
                            "stimulus_weight": c.get("stimulus_weight"),
                            "fatigue_weight": c.get("fatigue_weight"),
                        }
                        for c in contributions
                    ],
                }
            )
        matrix[group_id] = enriched
    return matrix

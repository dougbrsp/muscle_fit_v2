"""Motor de ficha v2 (USE_TEMPLATE_VOLUME_ENGINE_V2) — Fase 4: AI selector.

A IA recebe budgets JÁ CALCULADOS deterministicamente (Fase 2) e uma matriz
de candidatos JÁ FECHADA por `mapping_id` (Fase 3) — ela só escolhe QUAIS
exercícios/sets usar para se aproximar dos budgets; nunca decide grupos,
volume semanal, `stimulus_weight` ou cria exercício fora do catálogo (seções
21-26, 70-71 do documento de especificação). Toda a matemática de validação
fica em `template_volume_validator.py` (Fase 5) — este módulo só monta o
prompt e faz o parsing defensivo da resposta.

Reaproveita por IMPORT DIRETO `call_openai`/`_extract_json` de
`workout_plan_ai.py` — mesmo cliente/timeout/tratamento de erro
(`WorkoutPlanGenerationError`) do resto do pipeline de geração."""

from __future__ import annotations

import json
from typing import Any, Optional

from ..domain.prescription_defaults import (
    DEFAULT_MAX_REST_SECONDS,
    DEFAULT_MIN_REST_SECONDS,
    DEFAULT_PRESCRIPTION_REST_SECONDS,
    DEFAULT_REPS_TARGET,
    MIN_SETS_COUNT,
    midpoint_rest_seconds,
)
from .. import prompt_store
from ..prompt_user_data import USER_NAME_MAX_CHARS, user_data_inline, user_data_rule


# M103 P3: a PROSA do prompt mora no repositorio privado,
# `api/privado/prompts/template_selector/<versao>[.parte].md` (`prompt_store`).
# A versao nomeia so os templates (nao entra no texto do prompt); o sha256 do
# prompt renderizado e travado por
# `tests/unit/test_m103_p3_hash_prompts_sem_versao.py`.
PROMPT_FEATURE_DIR = "template_selector"
PROMPT_TEMPLATE_VERSION = "template-selector-v2"


def _selector_text(part: str, values: dict[str, str]) -> str:
    return prompt_store.render(PROMPT_FEATURE_DIR, f"{PROMPT_TEMPLATE_VERSION}{part}", values)


def build_v2_selection_prompt(
    slot: dict[str, Any],
    muscle_budgets: list[dict[str, Any]],
    candidate_matrix: dict[int, list[dict[str, Any]]],
    rag_text: str = "",
    recent_exercises_by_group: Optional[dict[str, list[str]]] = None,
    min_rest_seconds: int = DEFAULT_MIN_REST_SECONDS,
    max_rest_seconds: int = DEFAULT_MAX_REST_SECONDS,
) -> str:
    """`muscle_budgets` é a serialização (já em dict, ver
    `MuscleSlotBudget`) dos budgets do slot atual — cada item já traz
    `slot_effective_sets_min/max` (envelope pós-recuperação, seção 23 do
    documento). `candidate_matrix` é `{muscle_group_id: [candidatos]}` de
    `build_candidate_matrix` (Fase 3) — a IA só pode usar `mapping_id`
    presentes aqui."""
    recent_exercises_by_group = recent_exercises_by_group or {}

    system_rules = _selector_text(
        "",
        {
            "descanso_min": f"{min_rest_seconds}",
            "descanso_max": f"{max_rest_seconds}",
            "regra_dados_usuario": f"{user_data_rule()}",
        },
    )

    prompt_context = {
        # IA-10: o nome do slot e digitado pelo usuario no editor de Modelo.
        "slot": {
            "id": slot.get("id"),
            "name": user_data_inline(slot.get("name"), USER_NAME_MAX_CHARS) or slot.get("name"),
            "sequence": slot.get("sequence"),
        },
        "muscle_budgets": muscle_budgets,
        "candidate_matrix": candidate_matrix,
        "recent_exercises_by_group": recent_exercises_by_group,
    }

    # M76 (Lote 4c, E8/E10): o exemplo de reps sai do dono dos defaults.
    schema_hint = "\n" + _selector_text(".schema", {"reps": DEFAULT_REPS_TARGET}) + "\n"

    parts = [
        system_rules,
        "\n\n# Regras adicionais do metodo (RAG):\n" + (rag_text or "(sem conteudo RAG disponivel)"),
        "\n\n# Dados/contexto (JSON):\n" + json.dumps(prompt_context, ensure_ascii=False, default=str),
        "\n\n" + schema_hint,
    ]
    return "\n".join(parts)


def _valid_mapping_ids(candidate_matrix: dict[int, list[dict[str, Any]]]) -> set[int]:
    return {
        candidate["mapping_id"]
        for candidates in candidate_matrix.values()
        for candidate in candidates
        if candidate.get("mapping_id")
    }


def _coerce_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_v2_selection_response(
    raw_text: str,
    candidate_matrix: dict[int, list[dict[str, Any]]],
    default_rest_seconds: int = DEFAULT_PRESCRIPTION_REST_SECONDS,
) -> list[dict[str, Any]]:
    """Parseia a resposta da IA e DESCARTA silenciosamente qualquer item cujo
    `mapping_id` não esteja em `candidate_matrix` — proteção contra
    alucinação (seção 25 do documento). Nunca levanta exceção por item
    inválido isolado; só propaga `WorkoutPlanGenerationError` se o JSON
    inteiro vier malformado (via `_extract_json`)."""
    from ..workout_plan_ai import _extract_json

    parsed = _extract_json(raw_text)
    raw_exercises = parsed.get("exercises") if isinstance(parsed.get("exercises"), list) else []

    valid_ids = _valid_mapping_ids(candidate_matrix)

    result: list[dict[str, Any]] = []
    for item in raw_exercises:
        if not isinstance(item, dict):
            continue
        mapping_id = _coerce_int(item.get("mapping_id"))
        if mapping_id is None or mapping_id not in valid_ids:
            continue
        sets_count = _coerce_int(item.get("sets_count")) or MIN_SETS_COUNT
        result.append(
            {
                "mapping_id": mapping_id,
                "preferred_equipment_id": _coerce_int(item.get("preferred_equipment_id")),
                "sets_count": max(MIN_SETS_COUNT, sets_count),
                "reps_target": str(item.get("reps_target") or "").strip() or None,
                "rpe_target": str(item.get("rpe_target") or "").strip() or None,
                "rest_seconds": _coerce_int(item.get("rest_seconds")) or default_rest_seconds,
            }
        )
    return result


def select_exercises_for_slot_v2(
    slot: dict[str, Any],
    muscle_budgets: list[dict[str, Any]],
    candidate_matrix: dict[int, list[dict[str, Any]]],
    rag_text: str = "",
    recent_exercises_by_group: Optional[dict[str, list[str]]] = None,
    min_rest_seconds: int = DEFAULT_MIN_REST_SECONDS,
    max_rest_seconds: int = DEFAULT_MAX_REST_SECONDS,
) -> list[dict[str, Any]]:
    """Orquestra prompt -> chamada IA -> parsing/filtragem defensiva. Levanta
    `WorkoutPlanGenerationError` (via `call_openai`/`_extract_json`) se a
    chamada falhar ou o JSON vier malformado — o chamador
    (`generation/mode_a_legacy.py`, Fase 6) deve capturar isso e
    cair no fallback heurístico (`greedy_deterministic_selection`), nunca
    propagar 502 diretamente."""
    from ..workout_plan_ai import call_openai

    prompt = build_v2_selection_prompt(
        slot,
        muscle_budgets,
        candidate_matrix,
        rag_text=rag_text,
        recent_exercises_by_group=recent_exercises_by_group,
        min_rest_seconds=min_rest_seconds,
        max_rest_seconds=max_rest_seconds,
    )
    raw_text = call_openai(prompt)
    reinforcement_rest_seconds = midpoint_rest_seconds(min_rest_seconds, max_rest_seconds)
    return parse_v2_selection_response(raw_text, candidate_matrix, default_rest_seconds=reinforcement_rest_seconds)

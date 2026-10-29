"""Modo A LEGADO — o motor de ficha v2 com LLM, em QUARENTENA (PR-13).

Este arquivo era `app/workout_plan_templates_engine_v2.py`. O conteúdo está
intocado: ele continua sendo o caminho de `volume_mode='percent_budget'`
enquanto `GENERATION_ENGINE` estiver em `legacy` (default, inclusive em
produção), e é ele que garante "com a flag desligada nada muda". O motor novo,
determinístico e sem LLM, é `generation/mode_a.py` (§6.1 do
PLANO_20260912.md); quem escolhe entre os dois é `generation/mode_a.py`
mesmo, pela flag.

Só este módulo (e o Modo B legado) ainda importa `template_ai_selector_v2`,
o RAG da geração e `repair_slot_exercises`/`split_concentrated_exercises` —
todos eles existem para consertar saída de LLM e saem junto com este arquivo
no PR-19/20 (§11). O caminho determinístico não chama nenhum deles.

Segue o texto original, que descreve o pipeline com IA:

Motor de ficha v2 (USE_TEMPLATE_VOLUME_ENGINE_V2) — Fase 6: orquestração.

`generate_plan_draft_from_template_v2` conecta as Fases 1-5
(`app/intelligence/template_volume_domain.py`, `template_volume_planner.py`,
`candidate_exercise_provider.py`, `template_ai_selector_v2.py`,
`template_volume_validator.py`) num pipeline único, plugado em
`POST /workout-plans/generate` (`workout_plans.py`) SOMENTE quando a flag
`USE_TEMPLATE_VOLUME_ENGINE_V2` está ligada E o template tem
`training_type_id` — caso contrário o chamador usa o engine v1 legado
(`workout_plan_templates_engine.py`), que permanece 100% intocado.

Retorna o MESMO shape de dict que `generate_plan_draft_from_template` (v1)
já retorna — confirmado por leitura direta do v1 antes de implementar (ver
`workout_plan_templates_engine.py::generate_plan_draft_from_template`).

Se a chamada à IA (`select_exercises_for_slot_v2`) falhar, cai no fallback
heurístico (`greedy_deterministic_selection`) em vez de propagar erro —
nunca gera HTTP 502 por falha de IA (seção 66 do documento de
especificação)."""

from __future__ import annotations

import unicodedata
from typing import Any, Optional

from ..domain import cardio_prescription
from ..db import transaction
from ..template_versions import current_version_id_of
from ..domain.prescription_defaults import (
    DEFAULT_MAX_REST_SECONDS,
    DEFAULT_MIN_REST_SECONDS,
    DEFAULT_REPS_TARGET,
    DEFAULT_RPE_TARGET,
    DEFAULT_SETS_COUNT,
    MIN_SETS_COUNT,
    midpoint_rest_seconds,
)


def _normalize_name(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "").strip().casefold())


def _coerce_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_muscle_group_names(muscle_group_ids: set[int]) -> dict[int, str]:
    if not muscle_group_ids:
        return {}
    with transaction() as conn:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(muscle_group_ids))
            cur.execute(
                f"SELECT id, name FROM muscle_groups WHERE id IN ({placeholders})",
                tuple(muscle_group_ids),
            )
            return {row["id"]: row["name"] for row in cur.fetchall()}


def _budget_to_prompt_dict(budget) -> dict[str, Any]:
    return {
        "muscle_group_id": budget.muscle_group_id,
        "muscle_group_name": budget.muscle_group_name,
        "weekly_target": budget.weekly_target,
        "slot_effective_sets_min": round(budget.effective_slot_min, 2),
        "slot_effective_sets_max": round(budget.effective_slot_max, 2),
        "slot_effective_sets_mid": round((budget.effective_slot_min + budget.effective_slot_max) / 2, 2),
        "reps_target": budget.reps_target,
        "rpe_target": budget.rpe_target,
        "recovery_status": budget.recovery_status,
        "prioritize": budget.prioritize,
    }


def _reverse_group_by_mapping_id(candidate_matrix: dict[int, list[dict[str, Any]]]) -> dict[int, int]:
    reverse: dict[int, int] = {}
    for group_id, candidates in candidate_matrix.items():
        for candidate in candidates:
            mapping_id = candidate.get("mapping_id")
            if mapping_id is not None:
                reverse.setdefault(mapping_id, group_id)
    return reverse


def _candidate_info_by_mapping_id(candidate_matrix: dict[int, list[dict[str, Any]]]) -> dict[int, dict[str, Any]]:
    info: dict[int, dict[str, Any]] = {}
    for candidates in candidate_matrix.values():
        for candidate in candidates:
            mapping_id = candidate.get("mapping_id")
            if mapping_id is not None:
                info.setdefault(mapping_id, candidate)
    return info


def _ai_selection_to_exercises(
    selected: list[dict[str, Any]],
    candidate_matrix: dict[int, list[dict[str, Any]]],
    budgets_by_group: dict[int, Any],
) -> list[dict[str, Any]]:
    reverse_group = _reverse_group_by_mapping_id(candidate_matrix)
    info_by_mapping_id = _candidate_info_by_mapping_id(candidate_matrix)

    # Índice por mapping_id já emitido nesta seleção — corrige bug real
    # observado em produção (2026-08-29): a IA às vezes devolve o MESMO
    # mapping_id duas vezes em "exercises" (mesmo grupo, mesmo equipamento),
    # e nada no pipeline dedupava isso antes desta correção
    # (`excluded_due_to_duplicate` do motor v2 é hardcoded `[]` — a
    # deduplicação do v1, `deduplicate_by_group_and_name`, nunca é chamada
    # aqui, e de qualquer forma seria agressiva demais para o v2, que
    # DELIBERADAMENTE permite múltiplos exercícios do mesmo grupo
    # — ver `split_concentrated_exercises`). A correção certa é na origem:
    # duas entradas com o MESMO mapping_id nunca podem virar dois exercícios
    # distintos — são somadas num único exercício (sets_count somado), antes
    # de repair/split rodarem, para eles convergirem sobre a lista já
    # correta.
    exercises_by_mapping_id: dict[int, dict[str, Any]] = {}
    exercises: list[dict[str, Any]] = []
    for item in selected:
        mapping_id = item.get("mapping_id")
        group_id = reverse_group.get(mapping_id)
        if group_id is None or group_id not in budgets_by_group:
            # Defesa extra (além do filtro de _extract_json em
            # template_ai_selector_v2): mapping_id que não pertence a
            # nenhum grupo desta seleção nunca deveria acontecer, mas se
            # acontecer é descartado aqui, nunca propagado à ficha.
            continue

        existing = exercises_by_mapping_id.get(mapping_id)
        if existing is not None:
            existing["sets_count"] = (existing.get("sets_count") or 0) + (item.get("sets_count") or DEFAULT_SETS_COUNT)
            continue

        candidate = info_by_mapping_id.get(mapping_id, {})
        budget = budgets_by_group[group_id]
        # `preferred_equipment_id` só é aceito quando bate com o equipamento
        # do próprio candidato resolvido (mesma defesa anti-alucinação de
        # `mapping_id`) — a IA não pode apontar um mapping_id de um grupo
        # para o equipment_id de outro grupo (ex.: peitoral -> leg press).
        preferred_equipment_id = item.get("preferred_equipment_id")
        equipment_id = candidate.get("equipment_id")
        if preferred_equipment_id and preferred_equipment_id == equipment_id:
            equipment_id = preferred_equipment_id
        exercise = {
            "mapping_id": mapping_id,
            "exercise_name": candidate.get("exercise_name"),
            "amazfit_name": candidate.get("amazfit_name"),
            "equipment_id": equipment_id,
            "equipment_name": candidate.get("equipment_name"),
            "primary_group_id": group_id,
            "sets_count": item.get("sets_count") or DEFAULT_SETS_COUNT,
            "reps_target": item.get("reps_target") or budget.reps_target or DEFAULT_REPS_TARGET,
            "rpe_target": item.get("rpe_target") or budget.rpe_target or DEFAULT_RPE_TARGET,
            "rest_seconds": item.get("rest_seconds"),
        }
        exercises_by_mapping_id[mapping_id] = exercise
        exercises.append(exercise)
    return exercises


def _to_final_exercise_shape(
    exercise: dict[str, Any],
    muscle_group_names_by_id: dict[int, str],
    template_rest_seconds: int,
) -> dict[str, Any]:
    group_id = exercise.get("primary_group_id")
    return {
        "exercise_name": exercise.get("exercise_name"),
        "amazfit_name": exercise.get("amazfit_name"),
        "muscle_primary": muscle_group_names_by_id.get(group_id),
        "muscle_group_id": group_id,
        "muscle_secondary": None,
        "equipment_name": exercise.get("equipment_name"),
        "mapping_id": exercise.get("mapping_id"),
        "sets_count": exercise.get("sets_count") or MIN_SETS_COUNT,
        "reps_target": exercise.get("reps_target") or DEFAULT_REPS_TARGET,
        "load_target": None,
        "rpe_target": exercise.get("rpe_target") or DEFAULT_RPE_TARGET,
        "rest_seconds": exercise.get("rest_seconds") or template_rest_seconds,
        "warmup": False,
        "substitution_options": [],
        "notes": "",
    }


def generate_plan_draft_from_template_v2(
    template: dict[str, Any],
    slot: dict[str, Any],
    gym_id: Optional[int],
    gym_name: Optional[str],
    plan_date: Optional[str],
    user_id: Optional[int] = None,
    min_rest_seconds: int = DEFAULT_MIN_REST_SECONDS,
    max_rest_seconds: int = DEFAULT_MAX_REST_SECONDS,
    plan_time: Optional[str] = None,
) -> dict[str, Any]:
    from ..intelligence.candidate_exercise_provider import build_candidate_matrix
    from ..intelligence.template_volume_planner import build_template_cycle_context, build_template_volume_plan
    from ..intelligence.template_volume_domain import GenerationVolumeSummary
    from ..intelligence.template_ai_selector_v2 import select_exercises_for_slot_v2
    from ..intelligence.template_volume_validator import (
        calculate_slot_activation,
        enforce_minimum_sets_floor,
        greedy_deterministic_selection,
        order_exercises_for_session,
        repair_slot_exercises,
        split_concentrated_exercises,
    )
    from ..workout_plan_ai import (
        WorkoutPlanGenerationError,
        assign_progression_indicators,
        assign_warmup_flags,
        gather_ficha_context,
        get_recent_fullbody_exercise_names_by_group,
        normalize_load_target,
        reconcile_with_gym_catalog,
        resolve_gym_name,
    )
    from ..workout_plan_templates_engine import _empty_rest_day_draft
    from ..domain.effective_intensity import clamp_reduced_dose_rpe_targets

    if slot.get("slot_type") == "descanso":
        return _empty_rest_day_draft(template, slot, gym_id, gym_name, plan_date)

    template_rest_seconds = midpoint_rest_seconds(min_rest_seconds, max_rest_seconds)

    context = build_template_cycle_context(
        template, user_id=user_id, gym_id=gym_id, plan_date=plan_date, plan_time=plan_time
    )
    all_budgets = build_template_volume_plan(context, slot["id"])

    if not all_budgets:
        raise WorkoutPlanGenerationError(
            "Nenhum grupo muscular com meta semanal disponível para montar esta ficha a partir do modelo "
            "(slot sem itens de grupo muscular, ou grupos sem meta cadastrada no Tipo de Treinamento)."
        )

    excluded_due_to_recovery = [
        {"muscle_primary": b.muscle_group_name, "muscle_group_id": b.muscle_group_id, "reason": "NO_DIRECT_WORK"}
        for b in all_budgets
        if b.is_hard_excluded
    ]
    active_budgets = [b for b in all_budgets if not b.is_hard_excluded]
    reduced_dose_groups = sorted({b.muscle_group_name for b in active_budgets if b.recovery_status == "REDUCED_DOSE"})

    if not active_budgets:
        raise WorkoutPlanGenerationError(
            "Todos os grupos musculares deste slot estão indisponíveis por recuperação insuficiente "
            "(NO_DIRECT_WORK)."
        )

    budgets_by_group = {b.muscle_group_id: b for b in active_budgets}
    muscle_group_names_by_id = {b.muscle_group_id: b.muscle_group_name for b in all_budgets}

    ficha_context = gather_ficha_context(gym_id, user_id)
    gym_name = resolve_gym_name(gym_id, gym_name, ficha_context.get("gyms") or [])
    gym_equipment_recommendation = ficha_context.get("gym_equipment_recommendation")

    candidate_matrix = build_candidate_matrix(list(budgets_by_group.keys()), gym_equipment_recommendation)

    from ..rag_chunking import select_rag_text

    rag_folder = None
    template_training_type_id = template.get("training_type_id")
    if template_training_type_id:
        from ..training_types import _get_training_type

        training_type = _get_training_type(template_training_type_id)
        if training_type:
            rag_folder = training_type.get("rag_folder")

    has_training_type = bool(template_training_type_id)
    try:
        rag_text = select_rag_text(
            [b.muscle_group_name for b in active_budgets],
            rag_folder=rag_folder,
            has_training_type=has_training_type,
        )
    except ValueError:
        import logging

        logging.getLogger("fitness.workout_plan_templates_engine_v2").warning(
            "rag_folder invalido/inexistente no disco para training_type_id=%s (rag_folder=%r); "
            "prosseguindo sem RAG especifico do tipo de treino.",
            template_training_type_id,
            rag_folder,
        )
        rag_text = select_rag_text(
            [b.muscle_group_name for b in active_budgets], has_training_type=has_training_type
        )
    recent_exercises_by_group = get_recent_fullbody_exercise_names_by_group(gym_id, plan_type=None, user_id=user_id)

    muscle_budgets_prompt = [_budget_to_prompt_dict(b) for b in active_budgets]

    used_ai = True
    try:
        selected = select_exercises_for_slot_v2(
            slot,
            muscle_budgets_prompt,
            candidate_matrix,
            rag_text=rag_text,
            recent_exercises_by_group=recent_exercises_by_group,
            min_rest_seconds=min_rest_seconds,
            max_rest_seconds=max_rest_seconds,
        )
        exercises = _ai_selection_to_exercises(selected, candidate_matrix, budgets_by_group)
        exercises, repair_log = repair_slot_exercises(
            exercises, active_budgets, candidate_matrix, muscle_group_names_by_id
        )
    except WorkoutPlanGenerationError:
        used_ai = False
        exercises, repair_log = greedy_deterministic_selection(
            active_budgets, candidate_matrix, muscle_group_names_by_id
        )

    # "Variedade de equipamento" (causa raiz do problema reportado — ficha
    # concentrando muitas séries num único exercício mesmo com a academia
    # tendo alternativas): roda DEPOIS que o orçamento já bate, cobrindo o
    # gap que repair_slot_exercises não cobre (concentração não é desvio de
    # orçamento). Reverte por grupo se mudar o deviation_status — ver
    # docstring de split_concentrated_exercises.
    exercises, split_log = split_concentrated_exercises(
        exercises, active_budgets, candidate_matrix, muscle_group_names_by_id
    )
    repair_log = repair_log + split_log

    # Piso de 2 séries (causa raiz do problema reportado — exercício de 1
    # série sobrevivendo sem NENHUM registro no log): roda por ÚLTIMO,
    # depois que repair+split já convergiram o orçamento, cobrindo o caso em
    # que um exercício termina com sets_count < 2 sem nunca ter passado por
    # concentração (split só olha sets_count ACIMA do teto) nem por déficit
    # de orçamento (repair só olha UNDER/OVER_ALLOCATED do grupo, não o
    # sets_count individual de cada exercício) — ver docstring de
    # `enforce_minimum_sets_floor`.
    exercises, floor_log = enforce_minimum_sets_floor(
        exercises, active_budgets, candidate_matrix, muscle_group_names_by_id
    )
    repair_log = repair_log + floor_log

    # Ordenação final (prioritize) SÓ agora, depois do repair+split convergir
    # — seção 36.3 do documento: nunca antes, nunca dentro do cálculo de volume.
    exercises = order_exercises_for_session(exercises, active_budgets)

    final_exercises = [
        _to_final_exercise_shape(ex, muscle_group_names_by_id, template_rest_seconds) for ex in exercises
    ]

    final_exercises, adjusted_due_to_catalog_mismatch, excluded_due_to_no_equipment = reconcile_with_gym_catalog(
        final_exercises, gym_equipment_recommendation
    )

    assign_warmup_flags(final_exercises)
    for index, exercise in enumerate(final_exercises, start=1):
        exercise["sequence"] = index

    assign_progression_indicators(final_exercises, user_id=user_id)
    # Paridade com o fluxo ABC/v1 (abc_workout_ai.py): a UI le load_target
    # como o campo de carga efetivo (WorkoutPlansClient.tsx so mostra
    # suggested_load_kg como placeholder/dica, nao como valor do input).
    # assign_progression_indicators so preenche suggested_load_kg — sem
    # este passo, load_target ficava sempre None (definido em
    # _to_final_exercise_shape) e a carga sugerida pelo motor de progressao
    # nunca chegava ao campo realmente exibido/editado/persistido.
    for exercise in final_exercises:
        exercise["load_target"] = normalize_load_target(exercise.get("suggested_load_kg"))
    clamp_reduced_dose_rpe_targets(final_exercises, reduced_dose_groups)

    # Ativação final (pós-reconciliação de catálogo) para o resumo de
    # auditoria (seção 79 do documento) — populado em raw_payload, sem
    # migration nova (raw_payload já é JSON existente em workout_plans).
    # M76 E3: payload único de ativação (com a técnica do exercício).
    from ..intelligence.technique_volume import activation_payload

    activation_exercises = [activation_payload(ex) for ex in final_exercises]
    final_activation = calculate_slot_activation(activation_exercises, muscle_group_names_by_id)

    volume_summary = GenerationVolumeSummary()
    for budget in active_budgets:
        total = final_activation.get(budget.muscle_group_id, {}).get("total", 0.0)
        volume_summary.add_entry(
            GenerationVolumeSummary.Entry(
                muscle_group_id=budget.muscle_group_id,
                muscle_group_name=budget.muscle_group_name,
                weekly_target=budget.weekly_target,
                slot_min=budget.effective_slot_min,
                slot_max=budget.effective_slot_max,
                planned_activation=total,
                deviation_status=budget.deviation_status(total),
                recovery_status=budget.recovery_status,
                # Reserva/projeção do CICLO INTEIRO (seções 16/17 do
                # documento) fica fora do escopo desta entrega (Fase 7 —
                # observability persistente — explicitamente excluída do
                # plano aprovado); por ora, cycle_planned_total reflete só
                # este slot. Documentado aqui para não ser confundido com
                # o total do ciclo completo.
                cycle_planned_total=total,
                weekly_volume_deficit=max(0.0, budget.effective_slot_min - total),
                note=(
                    "cycle_planned_total reflete só este slot nesta versão — "
                    "projeção do ciclo completo fica para uma iteração futura (Fase 7)."
                ),
            )
        )
    for group in excluded_due_to_recovery:
        volume_summary.warnings.append(
            f"{group['muscle_primary']}: excluído por recuperação (NO_DIRECT_WORK)."
        )
    for entry in repair_log:
        if entry.get("action") == "deficit_catalog_exhausted":
            group_name = muscle_group_names_by_id.get(entry["muscle_group_id"], str(entry["muscle_group_id"]))
            volume_summary.warnings.append(
                f"{group_name}: catálogo esgotado, déficit de {entry.get('deficit', 0):g} séries equivalentes."
            )
        elif entry.get("action") == "forced_single_set":
            # Visibilidade do caso de borda documentado em
            # `enforce_minimum_sets_floor` — nunca mais silencioso (causa
            # raiz do Problema 1 relatado pelo usuário: exercício de 1 série
            # sobrevivia sem NENHUM registro explicando o motivo).
            group_name = muscle_group_names_by_id.get(entry["muscle_group_id"], str(entry["muscle_group_id"]))
            volume_summary.warnings.append(
                f"{group_name}: exercício com 1 série apenas (piso de 2 não alcançável sem sair "
                f"do orçamento validado — catálogo/orçamento não comporta um segundo exercício)."
            )

    generation_summary_text = (
        f"Ficha gerada via motor v2 (orientado a Tipo de Treinamento) a partir do modelo "
        f"'{template.get('name')}', slot '{slot.get('name')}'. "
        f"{'Seleção via IA' if used_ai else 'Seleção via fallback determinístico (IA indisponível)'}, "
        f"com validação/reparo determinístico de volume aplicado a {len(active_budgets)} grupo(s) muscular(es)."
    )

    # `effective_max_sets` = teto EFETIVO (`MuscleSlotBudget.effective_slot_max`,
    # já multiplicado por `recovery_multiplier` do dia — mesmo teto que
    # `deviation_status`/o reparo do motor respeitam internamente para nunca
    # devolver OVER_ALLOCATED), consumido por
    # MuscleActivationTable.tsx::activationAssessment para sinalizar "excesso"
    # na tabela "Ativação muscular (ficha em edição)" — distinto de
    # `sets_count` (`nominal_slot_mid`, o alvo/meta de séries, inalterado).
    # Campo NOVO e opcional: não existe equivalente no motor v1 (ver
    # `workout_plan_templates_engine.py::generate_plan_draft_from_template`,
    # que usa `sets_count` fixo do slot sem noção de teto/recuperação), então
    # esta é a ÚNICA origem de `effective_max_sets` no payload.
    template_slot_targets = [
        {
            "muscle_group_id": b.muscle_group_id,
            "muscle_group_name": b.muscle_group_name,
            "sets_count": round(b.nominal_slot_mid, 2),
            "effective_max_sets": round(b.effective_slot_max, 2),
        }
        for b in all_budgets
    ]

    return {
        "name": f"{template.get('name')} — {slot.get('name')}",
        "plan_type": template.get("template_type") or "full_body",
        "goal": template.get("description"),
        "plan_date": plan_date,
        "gym_id": gym_id,
        "gym_name": gym_name,
        "template_id": template.get("id"),
        "template_slot_id": slot.get("id"),
        # 066: versao do Modelo que gerou o rascunho (congela no save).
        "template_version_id": current_version_id_of(template),
        "template_slot_sequence": slot.get("sequence"),
        "template_slot_targets": template_slot_targets,
        "is_rest_day": False,
        # M98 C0: o cardio do slot chega ao rascunho (dono `cardio_prescription`).
        **cardio_prescription.draft_cardio(slot.get("items")),
        "exercises": final_exercises,
        "excluded_due_to_recovery": excluded_due_to_recovery,
        "excluded_due_to_duplicate": [],
        "adjusted_due_to_catalog_mismatch": adjusted_due_to_catalog_mismatch,
        "excluded_due_to_no_equipment": excluded_due_to_no_equipment,
        "reduced_dose_groups": reduced_dose_groups,
        "insufficient_target_volume_groups": [
            {
                "muscle_primary": muscle_group_names_by_id.get(entry["muscle_group_id"]),
                "muscle_group_id": entry["muscle_group_id"],
                "reason": "catalogo_esgotado",
                "deficit_equivalent_sets": entry.get("deficit"),
            }
            for entry in repair_log
            if entry.get("action") == "deficit_catalog_exhausted"
        ],
        "generation_summary": generation_summary_text,
        "raw_payload": {
            "template_id": template.get("id"),
            "slot_id": slot.get("id"),
            "slot_sequence": slot.get("sequence"),
            "engine_version": "v2",
            "used_ai": used_ai,
            "repair_log": repair_log,
            "volume_planning": volume_summary.as_dict(),
        },
    }

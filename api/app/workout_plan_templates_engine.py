"""Motor de geração da feature "Modelos de Ficha de Treino Personalizados"
(Fase 2 — geração via IA, mesma família arquitetural do Full Body em
`workout_plan_ai.py::generate_plan_draft`; ver histórico da Fase 1
100% determinística em `melhoria-eager-origami.md`, seções 2.3 e 4).

Plugado em `POST /workout-plans/generate` (workout_plans.py::generate_plan):
a escolha de usar um modelo é SEMPRE EXPLÍCITA do usuário, via
`payload.template_id` — nunca automática/silenciosa. Quando informado,
`get_template_if_assigned(template_id, user_id)` valida que o modelo está
ativo e atribuído a este usuário (proteção contra template_id forjado de
outro usuário); se válido e um slot é resolvido (`resolve_next_slot` —
escolha manual via `payload.template_slot_sequence`, ou continuação
automática a partir do último plano salvo com este modelo), a ficha é
gerada a partir do modelo (`generate_plan_draft_from_template`), ignorando
`payload.plan_type` — o modelo já carrega seu próprio `template_type`. Sem
`template_id` no payload, o chamador cai no fluxo estático atual (ABC/Full
Body), inalterado.

Diferença chave em relação ao Full Body: lá a IA decide QUAIS grupos
musculares entram na sessão (via `build_fullbody_slots`, dinâmico por
recuperação); aqui os grupos e o volume-alvo (`target_sets`) de cada um já
vêm FIXOS do slot do modelo (`payload.template_id`/`template_slot_sequence`),
só passando pelo mesmo gating de recuperação (NO_DIRECT_WORK exclui,
REDUCED_DOSE reduz dose). A partir daí, a IA (mesma chamada OpenAI de
`workout_plan_ai.py::call_openai`) decide, para cada grupo, 1 a 3
exercícios reais que somem (via contribuição ponderada primário/secundário/
estabilizador) o volume-alvo daquele grupo — em vez de exatamente 1
exercício-placeholder por grupo como na Fase 1. Um reforço determinístico
no backend (`_reinforce_template_target_groups`, adaptado de
`abc_volume_reinforcement.py`) garante o volume mesmo quando a IA erra a
conta, no mesmo espírito do reforço de volume mínimo do fluxo ABC.

Reaproveita por IMPORT DIRETO (mesmo padrão de abc_workout_template.py
importando de workout_plan_ai.py) as funções já existentes em
`workout_plan_ai.py`: `gather_ficha_context`, `resolve_gym_name`,
`project_recovery_for_date`, `reconcile_with_gym_catalog`,
`get_recent_fullbody_exercise_names_by_group`, `assign_progression_indicators`,
`assign_warmup_flags`,
`normalize_load_target`, `call_openai`, `_extract_json`,
`format_intervals_wellness_section`, `get_latest_intervals_wellness`,
`_strip_amazfit_fields` — nenhuma reescrita. Se a chamada à IA falhar
(`WorkoutPlanGenerationError`), o erro propaga sem fallback determinístico
silencioso — tratado em `workout_plans.py` (HTTP 502).
"""

from __future__ import annotations

import math
import unicodedata
from collections import defaultdict
from typing import Any, Optional

from .db import transaction
from .domain import cardio_prescription
from .template_versions import current_version_id_of
from .domain.prescription_defaults import (
    DEFAULT_MAX_REST_SECONDS,
    DEFAULT_MIN_REST_SECONDS,
    DEFAULT_REPS_TARGET,
    DEFAULT_RPE_TARGET,
    DEFAULT_SETS_COUNT,
    MIN_SETS_COUNT,
    midpoint_rest_seconds,
)


# M103 P3: a PROSA do prompt da ficha do Modelo (`build_template_prompt`) mora
# no repositorio privado, `api/privado/prompts/plan_templates/<versao>[.parte].md`
# (`prompt_store`). A versao nomeia so os templates (nao entra no texto do
# prompt nem em cache/dedupe); o sha256 do prompt renderizado e travado por
# `tests/unit/test_m103_p3_hash_prompts_sem_versao.py`.
PROMPT_FEATURE_DIR = "plan_templates"
PROMPT_TEMPLATE_VERSION = "plan-templates-v1"


def _plan_text(part: str, values: Optional[dict[str, str]] = None) -> str:
    from . import prompt_store

    return prompt_store.render(PROMPT_FEATURE_DIR, f"{PROMPT_TEMPLATE_VERSION}{part}", values or {})


def get_template_if_assigned(
    template_id: int, user_id: int, *, as_of: Optional[Any] = None
) -> Optional[dict[str, Any]]:
    """Modelo completo (via `_get_template`) SE estiver ATIVO e o `user_id`
    constar entre as atribuições dele; caso contrário `None`.

    Proteção contra um usuário gerar ficha usando o modelo de outro via
    `template_id` forjado na requisição — a escolha do modelo agora é sempre
    explícita (payload.template_id), então esta é a única validação de posse.

    "Vínculo vivo" (decisão já validada, seção 2 do contexto do plano): lê o
    modelo atual a cada chamada, sem cache/cópia na atribuição — qualquer
    edição no builder reflete na próxima ficha gerada, mesmo sem re-atribuir.

    M23 (migração 049): aceita atribuição VIGENTE **ou já encerrada**, de
    propósito. Esta função é uma checagem de POSSE ("este modelo já foi seu
    alguma vez?"), não a escolha de qual modelo seguir — e é ela que
    `/analytics/dashboard-cycle`, `adherence_service` e
    `muscle_summary_service` usam para ler um ciclo ANTIGO. Restringi-la ao
    vigente apagaria da tela justamente o histórico que o M23 veio preservar.
    Quem decide qual modelo está valendo é
    `template_assignments.resolve_active_template_id`, e é ele que alimenta o
    `template_id` que chega aqui.
    """
    # Import tardio: workout_plan_templates.py não importa este módulo, então
    # não há ciclo — mas mantém o padrão de import local já usado no restante
    # do projeto (workout_plan_ai.py/abc_workout_template.py) para módulos
    # pesados/de fronteira.
    from .workout_plan_templates import _get_template

    # `as_of` (PR3): leitura HISTORICA - slots e cabecalho da versao daquela
    # data (`_get_template`). A posse continua sendo a mesma checagem.
    template = _get_template(template_id, as_of=as_of)
    if not template or not template.get("is_active"):
        return None
    if not any(a.get("user_id") == user_id for a in template.get("assignments") or []):
        return None
    return template


def resolve_next_slot(
    template: dict[str, Any],
    user_id: int,
    explicit_slot_sequence: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Escolhe o slot a usar na geração — substitui a antiga regra cíclica por
    calendário (`resolve_slot_for_date`, removida). Duas opções:

    1. Escolha MANUAL (`explicit_slot_sequence` informado): retorna o slot
       com esse `sequence` exato, ou `None` se não existir nenhum com esse
       valor no modelo atual (o chamador trata como 422).
    2. Continuar AUTOMATICAMENTE (`explicit_slot_sequence` é `None`): olha a
       ÚLTIMA ficha salva (`workout_plans`) que usou este modelo
       (`template_id`) para este usuário e avança para o próximo slot da
       sequência, com wraparound. Sem nenhuma ficha salva ainda para este
       modelo+usuário, usa o primeiro slot (menor `sequence`).

       A posição de avanço é calculada pelo VALOR de `sequence` na lista
       ATUAL de slots (ordenada), não por índice cru guardado antigamente —
       isso tolera o modelo ter sido editado (slots adicionados/removidos)
       entre uma geração e outra: se o `sequence` do último plano salvo não
       existir mais no modelo atual, reinicia do primeiro slot.
    """
    ordered_slots = sorted(template.get("slots") or [], key=lambda slot: slot["sequence"])
    slot_count = len(ordered_slots)
    if slot_count == 0:
        return None

    if explicit_slot_sequence is not None:
        return next((slot for slot in ordered_slots if slot["sequence"] == explicit_slot_sequence), None)

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT template_slot_sequence
                FROM workout_plans
                WHERE user_id = %s AND template_id = %s AND template_slot_sequence IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id, template.get("id")),
            )
            last_row = cur.fetchone()

    if not last_row:
        return ordered_slots[0]

    last_sequence = last_row["template_slot_sequence"]
    current_position = next(
        (index for index, slot in enumerate(ordered_slots) if slot["sequence"] == last_sequence),
        None,
    )
    if current_position is None:
        return ordered_slots[0]

    next_position = (current_position + 1) % slot_count
    return ordered_slots[next_position]


def _load_muscle_group_names(cur, muscle_group_ids: set[int]) -> dict[int, str]:
    if not muscle_group_ids:
        return {}
    placeholders = ",".join(["%s"] * len(muscle_group_ids))
    cur.execute(f"SELECT id, name FROM muscle_groups WHERE id IN ({placeholders})", tuple(muscle_group_ids))
    return {row["id"]: row["name"] for row in cur.fetchall()}


def _load_exercise_catalog_names(cur, mapping_ids: set[int]) -> dict[int, str]:
    if not mapping_ids:
        return {}
    placeholders = ",".join(["%s"] * len(mapping_ids))
    cur.execute(f"SELECT id, name FROM exercise_catalog WHERE id IN ({placeholders})", tuple(mapping_ids))
    return {row["id"]: row["name"] for row in cur.fetchall()}


def _empty_rest_day_draft(
    template: dict[str, Any],
    slot: dict[str, Any],
    gym_id: Optional[int],
    gym_name: Optional[str],
    plan_date: Optional[str],
) -> dict[str, Any]:
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
        # Slot de descanso não pode ter item de grupo muscular (validado em
        # _validate_template_payload) — sempre vazio aqui.
        "template_slot_targets": [],
        "is_rest_day": True,
        # M98 C0: todos os itens de cardio do slot, com a prescrição pronta
        # (antes: só a duração do 1º item).
        **cardio_prescription.draft_cardio(slot.get("items")),
        "exercises": [],
        "excluded_due_to_recovery": [],
        "excluded_due_to_duplicate": [],
        "adjusted_due_to_catalog_mismatch": [],
        "excluded_due_to_no_equipment": [],
        "reduced_dose_groups": [],
        "generation_summary": "Dia de descanso conforme o modelo de treino atribuído.",
        "raw_payload": {
            "template_id": template.get("id"),
            "slot_id": slot.get("id"),
            "slot_sequence": slot.get("sequence"),
        },
    }


def _normalize_name(value: Any) -> str:
    """Normaliza um nome (exercício/grupo muscular) para comparação
    case-insensitive tolerante a acentuação/espaçamento (NFC + casefold) —
    mesmo padrão usado em `reconcile_with_gym_catalog`/`abc_volume_reinforcement`."""
    return unicodedata.normalize("NFC", str(value or "").strip().casefold())


def _coerce_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_template_prompt(
    context: dict[str, Any],
    rag_text: str,
    plan_type: str,
    goal: Optional[str],
    gym_name: Optional[str],
    target_groups: list[dict[str, Any]],
    recent_exercises_by_group: Optional[dict[str, list[str]]] = None,
    intervals_wellness_section: Optional[str] = None,
    min_rest_seconds: int = DEFAULT_MIN_REST_SECONDS,
    max_rest_seconds: int = DEFAULT_MAX_REST_SECONDS,
) -> str:
    """Prompt para o fluxo de geração via modelo — mesma família de
    `workout_plan_ai.build_prompt` (Full Body), mas o campo de estrutura fixa
    passa a ser "target_groups" (grupo + volume-alvo em séries equivalentes,
    já definidos pelo slot do modelo) em vez de "slots" (1 exercício por
    slot). Aqui a IA tem liberdade de escolher de 1 a 3 exercícios reais por
    grupo, desde que a soma ponderada das contribuições bata com
    `target_sets` — a validação/reforço final é sempre determinística no
    backend (`_reinforce_template_target_groups`), esta é só a tentativa
    inicial da IA."""
    import json

    from .intelligence.muscle_recovery_service import MuscleRecoveryService
    from .workout_plan_ai import _strip_amazfit_fields

    recent_exercises_by_group = recent_exercises_by_group or {}
    reduced_dose_names = [g["muscle_group"] for g in target_groups if g.get("dose_level") == "REDUCED_DOSE"]
    # M76 B2: a regra da dose reduzida vem do modelo (nunca "1-2 séries" à mão).
    reduced_dose_rule = MuscleRecoveryService.reduced_dose_prompt_rule()

    system_rules = _plan_text(
        "",
        {
            "regra_dose_reduzida": reduced_dose_rule,
            "descanso_min": f"{min_rest_seconds}",
            "descanso_max": f"{max_rest_seconds}",
        },
    )

    hard_rule = ""
    if reduced_dose_names:
        hard_rule = "\n\n" + _plan_text(
            ".dose_reduzida", {"regra_dose_reduzida": reduced_dose_rule, "grupos": ", ".join(reduced_dose_names)}
        )

    # IA-17: `gyms` (lista COMPARTILHADA de academias de todos os usuarios,
    # com notas e horarios) fica fora do prompt. Ela so serve para
    # `resolve_gym_name`, que roda antes, fora da LLM; a academia do pedido ja
    # vai na linha "# Pedido" e o equipamento vem de
    # `gym_equipment_recommendation`.
    prompt_context = _strip_amazfit_fields(
        {
            "target_groups": target_groups,
            "recent_exercises_by_group": recent_exercises_by_group,
            **{key: value for key, value in context.items() if key != "gyms"},
        }
    )

    schema_hint = "\n" + _plan_text(".schema") + "\n"

    parts = [
        system_rules + hard_rule,
        "\n\n# Regras adicionais do metodo (RAG):\n" + (rag_text or "(sem conteudo RAG disponivel)"),
        f"\n\n# Pedido: tipo de plano = {plan_type}; objetivo = {goal or 'nao informado'}; academia = {gym_name or 'nao informada'}.",
        intervals_wellness_section or "",
        "\n\n# Dados/contexto (JSON):\n" + json.dumps(prompt_context, ensure_ascii=False, default=str),
        "\n\n" + schema_hint,
    ]
    return "\n".join(parts)


def _reinforce_template_target_groups(
    exercises: list[dict[str, Any]],
    target_groups: list[dict[str, Any]],
    gym_equipment_recommendation: Optional[dict[str, Any]],
    muscle_group_names_by_id: Optional[dict[int, str]] = None,
    min_rest_seconds: int = DEFAULT_MIN_REST_SECONDS,
    max_rest_seconds: int = DEFAULT_MAX_REST_SECONDS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Adaptação de `abc_volume_reinforcement._reinforce_target_groups_minimum_sets`
    para esta feature: o alvo por grupo vem de `target_groups[i]["target_sets"]`
    (definido pelo MODELO), em vez da constante fixa MIN_EQUIVALENT_SETS_PER_GROUP
    do fluxo ABC, e reps/RPE de exercícios novos vêm de
    `target_groups[i]["reps_target"]`/`["rpe_target"]` em vez de um "role"
    biomecânico ABC (não existe papel fixo aqui, o modelo já prescreve
    reps/rpe por grupo). Grupos com dose_level=REDUCED_DOSE são pulados
    inteiramente (Fase 1 e Fase 2), igual ao ABC pular reduced_dose/unavailable
    — grupos NO_DIRECT_WORK nem chegam a `target_groups` (já ficaram em
    `excluded_due_to_recovery` antes desta função ser chamada).

    Reaproveita as constantes MAX_NEW_EXERCISES_PER_GROUP/
    TARGET_MAX_EXERCISES_PER_DAY/MAX_SETS_PER_EXERCISE_REINFORCEMENT
    diretamente de `abc_volume_reinforcement.py` por IMPORT (decisão:
    são tetos operacionais genéricos de "orçamento de exercícios por dia",
    não uma regra de negócio específica do ABC — duplicá-las aqui só criaria
    dois lugares para manter o mesmo número sincronizado)."""
    from .abc_volume_reinforcement import (
        MAX_NEW_EXERCISES_PER_GROUP,
        MAX_SETS_PER_EXERCISE_REINFORCEMENT,
        TARGET_MAX_EXERCISES_PER_DAY,
    )
    from .intelligence.muscle_contribution_resolver import (
        _accumulate_exercise_contribution,
        _resolve_muscle_contributions_for_mappings,
    )

    if not gym_equipment_recommendation:
        return exercises, []

    muscle_group_names_by_id = muscle_group_names_by_id or {}
    reinforcement_rest_seconds = midpoint_rest_seconds(min_rest_seconds, max_rest_seconds)

    groups_info = {
        int(key): value
        for key, value in (gym_equipment_recommendation.get("groups_by_id") or {}).items()
        if str(key).lstrip("-").isdigit()
    }

    def _display_name(group_id: int) -> str:
        return muscle_group_names_by_id.get(group_id, str(group_id))

    reinforceable_groups = [
        g for g in target_groups if g.get("muscle_group_id") is not None and g.get("dose_level") != "REDUCED_DOSE"
    ]

    mapping_ids: set[int] = {exercise["mapping_id"] for exercise in exercises if exercise.get("mapping_id")}
    for group in reinforceable_groups:
        group_info = groups_info.get(group["muscle_group_id"]) or {}
        candidates = [c for c in [group_info.get("recommended"), *(group_info.get("alternatives") or [])] if c]
        for candidate in candidates:
            candidate_mapping_id = candidate.get("mapping_id")
            if candidate_mapping_id:
                mapping_ids.add(candidate_mapping_id)

    contributions_by_mapping_id = _resolve_muscle_contributions_for_mappings(mapping_ids)

    totals: dict[int, float] = defaultdict(float)
    for exercise in exercises:
        _accumulate_exercise_contribution(exercise, contributions_by_mapping_id, totals)

    used_names: set[str] = {
        _normalize_name(exercise.get("exercise_name")) for exercise in exercises if exercise.get("exercise_name")
    }
    used_mapping_ids: set[int] = {exercise["mapping_id"] for exercise in exercises if exercise.get("mapping_id")}
    total_exercise_count = len(exercises)

    insufficient_target_volume_groups: list[dict[str, Any]] = []

    for group in reinforceable_groups:
        group_id = group["muscle_group_id"]
        target_sets = float(group.get("target_sets") or DEFAULT_SETS_COUNT)

        group_info = groups_info.get(group_id) or {}
        candidates = [c for c in [group_info.get("recommended"), *(group_info.get("alternatives") or [])] if c]

        exercises_added_for_group = 0

        # Fase 1: adiciona exercicios novos do catalogo ate atingir o alvo
        # do grupo ou esgotar candidatos/limite de novos exercicios por
        # grupo, sem ultrapassar o teto de exercicios totais do dia.
        while (
            totals[group_id] < target_sets
            and exercises_added_for_group < MAX_NEW_EXERCISES_PER_GROUP
            and total_exercise_count < TARGET_MAX_EXERCISES_PER_DAY
        ):
            candidate = next(
                (
                    c
                    for c in candidates
                    if c.get("mapping_id") not in used_mapping_ids
                    and _normalize_name(c.get("mapping_exercise_name")) not in used_names
                ),
                None,
            )
            if candidate is None:
                break

            candidate_mapping_id = candidate.get("mapping_id")
            new_exercise = {
                "sequence": 0,
                "exercise_name": candidate.get("mapping_exercise_name"),
                "amazfit_name": candidate.get("mapping_amazfit_name"),
                "equipment_name": candidate.get("equipment_name"),
                "mapping_id": candidate_mapping_id,
                "muscle_primary": _display_name(group_id),
                "muscle_group_id": group_id,
                "sets_count": DEFAULT_SETS_COUNT,
                "reps_target": group.get("reps_target") or DEFAULT_REPS_TARGET,
                "rpe_target": group.get("rpe_target") or DEFAULT_RPE_TARGET,
                "rest_seconds": reinforcement_rest_seconds,
                "notes": (
                    f"Exercício adicionado automaticamente para atingir o volume-alvo "
                    f"({target_sets:g} séries equivalentes) do grupo {_display_name(group_id)} definido pelo "
                    "modelo (considerando contribuição de músculos secundários de outros exercícios do dia)."
                ),
                "warmup": False,
                "substitution_options": [],
            }
            exercises.append(new_exercise)
            exercises_added_for_group += 1
            total_exercise_count += 1
            used_names.add(_normalize_name(candidate.get("mapping_exercise_name")))
            if candidate_mapping_id:
                used_mapping_ids.add(candidate_mapping_id)
            _accumulate_exercise_contribution(new_exercise, contributions_by_mapping_id, totals)

        # Fase 2: round-robin +1 serie no exercicio PRIMARY de menor
        # sets_count do grupo, ate atingir o alvo ou esgotar os candidatos
        # abaixo do teto rigido por exercicio.
        if totals[group_id] < target_sets:
            primary_candidates = [ex for ex in exercises if ex.get("muscle_group_id") == group_id]
            if primary_candidates:
                remaining_sets = math.ceil(target_sets - totals[group_id])
                for _ in range(remaining_sets):
                    below_cap_candidates = [
                        ex
                        for ex in primary_candidates
                        if (ex.get("sets_count") or 0) < MAX_SETS_PER_EXERCISE_REINFORCEMENT
                    ]
                    if not below_cap_candidates:
                        break
                    target_exercise = min(below_cap_candidates, key=lambda ex: ex.get("sets_count") or 0)
                    target_exercise["sets_count"] = (target_exercise.get("sets_count") or 0) + 1
                    target_stimulus_weight = next(
                        (
                            c.get("stimulus_weight", 1.0)
                            for c in contributions_by_mapping_id.get(target_exercise.get("mapping_id"), [])
                            if c.get("muscle_group_id") == group_id and str(c.get("role", "")).upper() == "PRIMARY"
                        ),
                        1.0,
                    )
                    totals[group_id] += target_stimulus_weight

        if totals[group_id] < target_sets:
            insufficient_target_volume_groups.append(
                {
                    "muscle_primary": _display_name(group_id),
                    "muscle_group_id": group_id,
                    "equivalent_sets_achieved": round(totals[group_id], 2),
                    "equivalent_sets_target": target_sets,
                    "exercises_added": exercises_added_for_group,
                    "reason": "catalogo_esgotado",
                }
            )

    return exercises, insufficient_target_volume_groups


def generate_plan_draft_from_template(
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
    """Monta um rascunho de ficha via IA a partir de um slot já resolvido
    (`resolve_next_slot`) de um modelo atribuído — mesma família
    arquitetural do Full Body (`workout_plan_ai.generate_plan_draft`), mas a
    estrutura de grupos/volume-alvo vem FIXA do modelo em vez de calculada
    dinamicamente por recuperação. Slot `descanso`: retorna draft com
    `exercises=[]` (ver `_empty_rest_day_draft`), preservando o formato de
    retorno esperado pelo resto do pipeline (mesmas chaves de
    `generate_plan_draft`/`generate_abc_plan_draft`).

    Se a chamada à IA falhar, `WorkoutPlanGenerationError` propaga sem
    fallback determinístico silencioso — tratado pelo chamador
    (`workout_plans.py`, HTTP 502)."""
    if slot.get("slot_type") == "descanso":
        return _empty_rest_day_draft(template, slot, gym_id, gym_name, plan_date)

    from .domain.effective_intensity import clamp_reduced_dose_rpe_targets
    from .workout_plan_ai import (
        _extract_json,
        assign_progression_indicators,
        assign_warmup_flags,
        call_openai,
        format_intervals_wellness_section,
        gather_ficha_context,
        get_latest_intervals_wellness,
        get_recent_fullbody_exercise_names_by_group,
        normalize_load_target,
        project_recovery_for_date,
        reconcile_with_gym_catalog,
        resolve_gym_name,
    )

    template_rest_seconds = midpoint_rest_seconds(min_rest_seconds, max_rest_seconds)

    context = gather_ficha_context(gym_id, user_id)
    gym_name = resolve_gym_name(gym_id, gym_name, context.get("gyms") or [])
    context["muscle_recovery"] = project_recovery_for_date(
        context["muscle_recovery"], plan_date, plan_time, user_id=user_id
    )

    unavailable_ids: set[int] = set()
    unavailable_names: set[str] = set()
    reduced_dose_ids: set[int] = set()
    reduced_dose_names: set[str] = set()
    for item in context["muscle_recovery"]:
        status = item.get("projected_readiness_status")
        group_id = item.get("muscle_group_id")
        group_name = item.get("muscle_group")
        if status == "NO_DIRECT_WORK":
            if group_id is not None:
                unavailable_ids.add(group_id)
            if group_name:
                unavailable_names.add(group_name)
        elif status == "REDUCED_DOSE":
            if group_id is not None:
                reduced_dose_ids.add(group_id)
            if group_name:
                reduced_dose_names.add(group_name)

    slot_items = sorted(slot.get("items") or [], key=lambda item: item.get("item_order") or 0)
    # prioritize=True só define ordem de resolução de catálogo (processa
    # primeiro, evita esgotamento de equipamento) — sort estável preserva a
    # ordem original (item_order) dentro de cada grupo prioritize/não.
    items_ordered = sorted(slot_items, key=lambda item: 0 if item.get("prioritize") else 1)

    muscle_group_ids_needed = {
        item["muscle_group_id"]
        for item in items_ordered
        if item.get("item_kind") == "muscle_group" and item.get("muscle_group_id")
    }

    with transaction() as conn:
        with conn.cursor() as cur:
            muscle_group_names = _load_muscle_group_names(cur, muscle_group_ids_needed)

    # 1. Monta target_groups (grupo + volume-alvo/reps/rpe do modelo) a
    # partir dos itens muscle_group do slot, aplicando o mesmo gating de
    # recuperação de sempre (NO_DIRECT_WORK exclui, REDUCED_DOSE reduz
    # dose). item_kind == "exercise" é um tipo legado, não mais aceito em
    # novos modelos (ver workout_plan_templates.py::_validate_template_payload)
    # — ignorado aqui, incompatível com o modelo "1..3 exercícios por
    # grupo via IA" desta feature.
    target_groups: list[dict[str, Any]] = []
    excluded_due_to_recovery: list[dict[str, Any]] = []

    for item in items_ordered:
        kind = item.get("item_kind")

        if kind != "muscle_group":
            continue

        group_id = item.get("muscle_group_id")
        group_name = muscle_group_names.get(group_id)
        if not group_name:
            continue

        is_unavailable = group_id in unavailable_ids or group_name in unavailable_names
        if is_unavailable:
            excluded_due_to_recovery.append(
                {
                    "muscle_primary": group_name,
                    "muscle_group_id": group_id,
                    "reason": "NO_DIRECT_WORK",
                }
            )
            continue

        is_reduced_dose = group_id in reduced_dose_ids or group_name in reduced_dose_names
        target_groups.append(
            {
                "muscle_group": group_name,
                "muscle_group_id": group_id,
                "target_sets": item.get("sets_count") or DEFAULT_SETS_COUNT,
                "reps_target": item.get("reps_target"),
                "rpe_target": item.get("rpe_target"),
                "prioritize": bool(item.get("prioritize")),
                "dose_level": "REDUCED_DOSE" if is_reduced_dose else "READY",
            }
        )

    reduced_dose_groups = sorted({g["muscle_group"] for g in target_groups if g["dose_level"] == "REDUCED_DOSE"})

    if not target_groups:
        from .workout_plan_ai import WorkoutPlanGenerationError

        raise WorkoutPlanGenerationError(
            "Nenhum grupo muscular disponível para montar esta ficha a partir do modelo "
            "(todos excluídos por recuperação insuficiente, ou o slot não tem nenhum item de grupo muscular)."
        )

    # 2. RAG especifico dos grupos desta sessao (mesmo padrao do Full Body).
    # Quando o template tem training_type_id, acrescenta tambem o RAG
    # especifico daquele tipo de treino (rag_folder), se configurado.
    from .rag_chunking import select_rag_text

    rag_folder = None
    template_training_type_id = template.get("training_type_id")
    if template_training_type_id:
        from .training_types import _get_training_type

        training_type = _get_training_type(template_training_type_id)
        if training_type:
            rag_folder = training_type.get("rag_folder")

    has_training_type = bool(template_training_type_id)
    try:
        rag_text = select_rag_text(
            [g["muscle_group"] for g in target_groups],
            rag_folder=rag_folder,
            has_training_type=has_training_type,
        )
    except ValueError:
        import logging

        logging.getLogger("fitness.workout_plan_templates_engine").warning(
            "rag_folder invalido/inexistente no disco para training_type_id=%s (rag_folder=%r); "
            "prosseguindo sem RAG especifico do tipo de treino.",
            template_training_type_id,
            rag_folder,
        )
        rag_text = select_rag_text(
            [g["muscle_group"] for g in target_groups], has_training_type=has_training_type
        )

    # 3. Historico de variedade: olha fichas de QUALQUER tipo (plan_type=None),
    # nao so "full_body" — o modelo pode gerar fichas de qualquer template_type.
    recent_exercises_by_group = get_recent_fullbody_exercise_names_by_group(gym_id, plan_type=None, user_id=user_id)

    intervals_wellness_section = format_intervals_wellness_section(get_latest_intervals_wellness(user_id=user_id))

    prompt = build_template_prompt(
        context,
        rag_text,
        template.get("template_type") or "full_body",
        template.get("description"),
        gym_name,
        target_groups,
        recent_exercises_by_group,
        intervals_wellness_section,
        min_rest_seconds=min_rest_seconds,
        max_rest_seconds=max_rest_seconds,
    )
    raw_text = call_openai(prompt)
    ai_draft = _extract_json(raw_text)
    ai_exercises_raw = ai_draft.get("exercises") if isinstance(ai_draft.get("exercises"), list) else []

    # 6. Pos-processamento deterministico. Filtro de seguranca: descarta
    # itens sem exercise_name/muscle_primary, ou cujo muscle_primary nao bate
    # com nenhum target_groups — propaga muscle_group_id do target_groups
    # correspondente (a IA nao necessariamente sabe o id).
    target_group_by_name = {_normalize_name(g["muscle_group"]): g for g in target_groups}

    exercises: list[dict[str, Any]] = []
    for item in ai_exercises_raw:
        if not isinstance(item, dict):
            continue
        exercise_name = str(item.get("exercise_name") or "").strip()
        muscle_primary_raw = str(item.get("muscle_primary") or "").strip()
        if not exercise_name or not muscle_primary_raw:
            continue
        target_group = target_group_by_name.get(_normalize_name(muscle_primary_raw))
        if target_group is None:
            continue

        exercises.append(
            {
                "exercise_name": exercise_name,
                "amazfit_name": str(item.get("amazfit_name") or "").strip() or None,
                "muscle_primary": target_group["muscle_group"],
                "muscle_group_id": target_group["muscle_group_id"],
                "muscle_secondary": str(item.get("muscle_secondary") or "").strip() or None,
                "equipment_name": str(item.get("equipment_name") or "").strip() or None,
                "mapping_id": None,
                "sets_count": _coerce_int(item.get("sets_count")) or DEFAULT_SETS_COUNT,
                "reps_target": str(item.get("reps_target") or "").strip() or target_group.get("reps_target") or DEFAULT_REPS_TARGET,
                "load_target": item.get("load_target"),
                "rpe_target": str(item.get("rpe_target") or "").strip() or target_group.get("rpe_target") or DEFAULT_RPE_TARGET,
                "rest_seconds": _coerce_int(item.get("rest_seconds")) or template_rest_seconds,
                "warmup": False,
                "substitution_options": (
                    item.get("substitution_options") if isinstance(item.get("substitution_options"), list) else []
                ),
                "notes": str(item.get("notes") or "").strip(),
            }
        )

    # Dose reduzida determinística (não confia só no prompt): M76 B2 - a regra
    # única do modelo, `ceil(S × 0,75)` por exercício, mínimo 1
    # (`MuscleRecoveryService.reduced_sets`). Antes era um teto próprio de 2
    # séries somadas por grupo. O RPE é cortado pelo teto do contrato
    # REDUCED_DOSE mais abaixo (`clamp_reduced_dose_rpe_targets`).
    from .intelligence.muscle_recovery_service import MuscleRecoveryService

    reduced_dose_group_ids = {g["muscle_group_id"] for g in target_groups if g["dose_level"] == "REDUCED_DOSE"}
    if reduced_dose_group_ids:
        policy = MuscleRecoveryService.reduction_policy()
        dose_note = (
            f"Dose reduzida por recuperação parcial do grupo: {policy['sets_rule']}, "
            f"RPE máx. {policy['maximum_rpe']}, sem falha, sem progressão."
        )
        for exercise in exercises:
            if exercise.get("muscle_group_id") not in reduced_dose_group_ids:
                continue
            exercise["sets_count"] = MuscleRecoveryService.reduced_sets(exercise.get("sets_count") or MIN_SETS_COUNT, "REDUCED_DOSE")
            existing_notes = (exercise.get("notes") or "").strip()
            exercise["notes"] = f"{existing_notes} {dose_note}".strip()

    # Dedupe simples por exercise_name exato repetido (case-insensitive) —
    # deduplicate_by_group_and_name (workout_plan_ai.py) nao serve aqui, pois
    # removeria qualquer segundo exercicio do MESMO grupo, incompativel com
    # "1 a 3 exercicios por grupo".
    seen_names: set[str] = set()
    deduped_exercises: list[dict[str, Any]] = []
    for exercise in exercises:
        name_key = _normalize_name(exercise.get("exercise_name"))
        if name_key and name_key in seen_names:
            continue
        if name_key:
            seen_names.add(name_key)
        deduped_exercises.append(exercise)
    exercises = deduped_exercises

    exercises, adjusted_due_to_catalog_mismatch, excluded_due_to_no_equipment = reconcile_with_gym_catalog(
        exercises, context.get("gym_equipment_recommendation")
    )

    exercises, insufficient_target_volume_groups = _reinforce_template_target_groups(
        exercises,
        target_groups,
        context.get("gym_equipment_recommendation"),
        muscle_group_names_by_id=muscle_group_names,
        min_rest_seconds=min_rest_seconds,
        max_rest_seconds=max_rest_seconds,
    )

    for exercise in exercises:
        if not exercise.get("rest_seconds"):
            exercise["rest_seconds"] = template_rest_seconds

    assign_warmup_flags(exercises)

    for exercise in exercises:
        exercise["load_target"] = normalize_load_target(exercise.get("load_target"))

    for index, exercise in enumerate(exercises, start=1):
        exercise["sequence"] = index

    assign_progression_indicators(exercises, user_id=user_id)
    clamp_reduced_dose_rpe_targets(exercises, reduced_dose_groups)

    # Alvo de séries por grupo muscular definido pelo MODELO neste slot —
    # usado pelo frontend (WorkoutPlansClient.tsx) para avaliar a tabela
    # "Ativação muscular (ficha em edição)" contra o modelo em vez dos
    # limiares genéricos (ver MuscleActivationTable.tsx::activationAssessment).
    # Só itens item_kind == "muscle_group" (itens "cardio" não têm grupo).
    # Inalterado: continua vindo dos itens ORIGINAIS do slot, não de
    # target_groups (mesma lógica de sempre).
    template_slot_targets = [
        {
            "muscle_group_id": item.get("muscle_group_id"),
            "muscle_group_name": muscle_group_names.get(item.get("muscle_group_id")),
            "sets_count": item.get("sets_count") or 0,
        }
        for item in items_ordered
        if item.get("item_kind") == "muscle_group"
        and item.get("muscle_group_id")
        and muscle_group_names.get(item.get("muscle_group_id"))
    ]

    generation_summary = ai_draft.get("generation_summary") or (
        f"Ficha gerada via IA a partir do modelo '{template.get('name')}', slot '{slot.get('name')}'."
    )

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
        # M98 C0: tempo total e prescrição pelo dono (`cardio_prescription`).
        **cardio_prescription.draft_cardio(slot_items),
        "exercises": exercises,
        "excluded_due_to_recovery": excluded_due_to_recovery,
        "excluded_due_to_duplicate": [],
        "adjusted_due_to_catalog_mismatch": adjusted_due_to_catalog_mismatch,
        "excluded_due_to_no_equipment": excluded_due_to_no_equipment,
        "reduced_dose_groups": reduced_dose_groups,
        "insufficient_target_volume_groups": insufficient_target_volume_groups,
        "generation_summary": generation_summary,
        "raw_payload": {
            "template_id": template.get("id"),
            "slot_id": slot.get("id"),
            "slot_sequence": slot.get("sequence"),
            "unavailable_group_ids": sorted(unavailable_ids),
            "reduced_dose_group_ids": sorted(reduced_dose_ids),
            "recent_exercises_by_group": recent_exercises_by_group,
            "target_groups": target_groups,
            "generation_summary": generation_summary,
            "raw_ai_text": raw_text,
        },
    }

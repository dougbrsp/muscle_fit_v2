from __future__ import annotations

import math
import unicodedata
from collections import defaultdict
from typing import Any

# _resolve_muscle_contributions_for_mappings/_accumulate_exercise_contribution
# viviam neste modulo e foram movidas para
# `intelligence/muscle_contribution_resolver.py` (modulo neutro dentro do
# pacote `intelligence/`) porque `VolumeService.get_equivalent_sets_by_activity`
# (tabela "Series equivalentes executadas" de /volume-grupamentos) tambem
# precisa reaproveita-las, e `intelligence/volume_service.py` nao pode
# importar deste modulo sem criar um ciclo (este modulo ja importa
# VolumeService de `intelligence/volume_service.py`). Import aqui preservado
# por nome (via `import ... as` implicito) para nao quebrar o teste existente
# que faz `mock.patch.object(reinforcement_module, "_resolve_muscle_contributions_for_mappings", ...)`.
from .intelligence.muscle_contribution_resolver import (
    _accumulate_exercise_contribution,
    _resolve_muscle_contributions_for_mappings,
)
from .domain.prescription_defaults import (
    DEFAULT_MAX_REST_SECONDS,
    DEFAULT_MIN_REST_SECONDS,
    DEFAULT_REPS_TARGET,
    DEFAULT_RPE_TARGET,
    DEFAULT_SETS_COUNT,
    midpoint_rest_seconds,
)
from .periodization.limits import (
    LEGACY_REINFORCEMENT_MAX_EXERCISES_PER_DAY,
    LEGACY_REINFORCEMENT_MAX_SETS_PER_EXERCISE,
)

# Regra de negocio (2026-08-22): cada grupo muscular alvo do dia (fluxo ABC)
# deve receber no minimo 9 series EQUIVALENTES — contando a contribuicao
# ponderada de musculos secundarios de OUTROS exercicios do mesmo dia, nao so
# series diretas do proprio exercicio do grupo. Reaproveita a mesma formula
# de "serie equivalente ponderada" ja usada retrospectivamente por
# VolumeService sobre series logadas (PRIMARY = peso 1.0, SECONDARY/
# STABILIZER = stimulus_weight real, fallback 0.50), sem alterar
# volume_service.py.
MIN_EQUIVALENT_SETS_PER_GROUP = 9.0
MAX_NEW_EXERCISES_PER_GROUP = 3

# Regra de negocio (2026-08-24): teto RIGIDO de series por exercico
# individual na Fase 2 do reforco (round-robin). Igual ao proprio minimo
# MIN_EQUIVALENT_SETS_PER_GROUP, NAO um valor menor — bater exatamente 9
# series num unico exercicio e esperado/aceitavel quando ele e o unico
# equipamento disponivel para o grupo (e o proprio minimo da regra de
# negocio). O teto existe so para garantir que o reforco NUNCA ultrapasse
# esse numero por erro de arredondamento (math.ceil) ou distribuicao.
MAX_SETS_PER_EXERCISE_REINFORCEMENT = LEGACY_REINFORCEMENT_MAX_SETS_PER_EXERCISE

# Regra de negocio (2026-08-24): para otimizar o tempo de treino, o reforco
# deve procurar manter o total de exercicios do dia num teto suave de ~12
# (soft target — priorizar variedade de equipamento, evitar passar do teto).
# TARGET_MAX_EXERCISES_PER_DAY e o teto: uma vez atingido, a Fase 1
# (adicionar exercicio NOVO) para de rodar para os grupos restantes,
# priorizando a Fase 2 (aumentar sets_count de exercicio ja presente).
TARGET_MAX_EXERCISES_PER_DAY = LEGACY_REINFORCEMENT_MAX_EXERCISES_PER_DAY


def _normalize_name(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "").strip().casefold())


def _reinforce_target_groups_minimum_sets(
    exercises: list[dict[str, Any]],
    target_group_ids: list[int],
    reduced_dose_ids: set[int],
    unavailable_ids: set[int],
    gym_equipment_recommendation: dict[str, Any] | None,
    role_by_muscle_group_id: dict[int, dict[str, Any]],
    muscle_group_names_by_id: dict[int, str] | None = None,
    min_rest_seconds: int = DEFAULT_MIN_REST_SECONDS,
    max_rest_seconds: int = DEFAULT_MAX_REST_SECONDS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Garante que cada grupo em `target_group_ids` (exceto os em
    reduced_dose_ids OU unavailable_ids, que nunca sao reforcados) receba no
    minimo MIN_EQUIVALENT_SETS_PER_GROUP series equivalentes, contando
    contribuicao ponderada de musculos secundarios de outros exercicios do
    mesmo dia.

    Correcao 2026-08-25 (bug de nomenclatura renomeada em `muscle_groups`):
    esta funcao indexava tudo por NOME de grupo muscular (texto), inclusive
    strings hardcoded vindas de ABC_TEMPLATE — quando o nome mudava no banco
    (`muscle_groups.name`), o lookup em `gym_equipment_recommendation`
    (sempre indexado pelo nome VIVO) parava de bater e o grupo nunca era
    reforcado. Toda a funcao passou a indexar por `muscle_group_id` (estavel,
    nunca muda) — `gym_equipment_recommendation["groups_by_id"]` (aditivo,
    ver `gyms.py`) substitui `groups_by_id.get(nome)`. `muscle_group_names_by_id`
    e usado SOMENTE para preencher o campo de exibicao `muscle_primary` de
    qualquer exercicio novo/entrada de `insufficient_target_volume_groups` —
    nunca para matching.

    `unavailable_ids` sao os grupos NO_DIRECT_WORK (projetados para a data
    do plano) — mesmo quando o unico sobrevivente desse grupo na ficha e um
    exercicio `mandatory_rotator` (prehab leve de manguito rotador, nunca
    excluido por NO_DIRECT_WORK em abc_workout_template.py), esse grupo NAO
    deve ser inflado pelo reforco: o exercicio fica com o `sets_count`
    baseline do template. Grupos NO_DIRECT_WORK tambem nao aparecem em
    `insufficient_target_volume_groups` — estao fora do reforco de
    proposito, nao por falta de catalogo.

    Para otimizar o tempo de treino, o total de exercicios do dia procura
    ficar num teto suave de ~12 (TARGET_MAX_EXERCISES_PER_DAY, priorizando
    variedade de equipamento): Fase 1 adiciona ate MAX_NEW_EXERCISES_PER_GROUP
    exercicios novos do catalogo da academia (gym_equipment_recommendation)
    por grupo, mas para de adicionar novos exercicios assim que o total do
    dia atinge esse teto. Fase 2: quando o catalogo estiver esgotado OU o
    teto de exercicios do dia for atingido e ainda faltar volume, distribui
    o deficit (1 serie por vez, round-robin no exercicio PRIMARY do grupo
    com menor sets_count, respeitando o teto RIGIDO
    MAX_SETS_PER_EXERCISE_REINFORCEMENT por exercicio) entre os exercicios
    PRIMARY do grupo ja presentes na ficha, em vez de concentrar tudo num
    unico exercicio/equipamento — so concentra no mesmo exercicio quando ele
    e o UNICO PRIMARY do grupo na ficha. Se todos os candidatos do grupo
    atingirem o teto por exercicio e ainda faltar volume (só pode acontecer
    com multiplos exercicios do mesmo grupo, cada um ja em
    MAX_SETS_PER_EXERCISE_REINFORCEMENT), o loop para e o deficit
    remanescente cai na checagem de `insufficient_target_volume_groups`.

    `min_rest_seconds`/`max_rest_seconds` (faixa desejada pelo usuario ao
    gerar a ficha, ver `WorkoutPlanGenerateIn`) definem o `rest_seconds` de
    qualquer exercicio NOVO adicionado por este reforco (Fase 1) — usa o
    ponto medio arredondado da faixa, ja que este caminho e determinístico
    (sem IA para prescrever um valor especifico).

    Retorna (exercises, insufficient_target_volume_groups). `exercises` e
    mutado in-place (novos exercicios sao appendados, sets_count existentes
    podem ser incrementados), mas tambem e retornado por conveniencia."""
    if not gym_equipment_recommendation:
        return exercises, []

    muscle_group_names_by_id = muscle_group_names_by_id or {}
    reinforcement_rest_seconds = midpoint_rest_seconds(min_rest_seconds, max_rest_seconds)

    # Normaliza chaves de volta para int — `gym_equipment_recommendation`
    # chega aqui apos um round-trip HTTP interno (gather_ficha_context),
    # que serializa chaves inteiras de `groups_by_id` como string em JSON.
    groups_info = {
        int(key): value for key, value in (gym_equipment_recommendation.get("groups_by_id") or {}).items()
        if str(key).lstrip("-").isdigit()
    }

    def _display_name(group_id: int) -> str:
        return muscle_group_names_by_id.get(group_id, str(group_id))

    mapping_ids: set[int] = {
        exercise["mapping_id"] for exercise in exercises if exercise.get("mapping_id")
    }
    for group_id in target_group_ids:
        group_info = groups_info.get(group_id) or {}
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
    used_mapping_ids: set[int] = {
        exercise["mapping_id"] for exercise in exercises if exercise.get("mapping_id")
    }
    total_exercise_count = len(exercises)

    insufficient_target_volume_groups: list[dict[str, Any]] = []

    for group_id in sorted(target_group_ids):
        if group_id in reduced_dose_ids or group_id in unavailable_ids:
            continue

        group_info = groups_info.get(group_id) or {}
        candidates = [c for c in [group_info.get("recommended"), *(group_info.get("alternatives") or [])] if c]

        exercises_added_for_group = 0

        # Fase 1: adiciona exercicios novos do catalogo ate atingir o minimo
        # ou esgotar candidatos/limite de novos exercicios por grupo, sem
        # ultrapassar o teto de exercicios totais do dia.
        while (
            totals[group_id] < MIN_EQUIVALENT_SETS_PER_GROUP
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

            role = role_by_muscle_group_id.get(group_id, {})
            candidate_mapping_id = candidate.get("mapping_id")
            new_exercise = {
                "sequence": 0,
                "exercise_name": candidate.get("mapping_exercise_name"),
                "amazfit_name": candidate.get("mapping_amazfit_name"),
                "equipment_name": candidate.get("equipment_name"),
                "mapping_id": candidate_mapping_id,
                "muscle_primary": _display_name(group_id),
                "muscle_group_id": group_id,
                "sets_count": role.get("sets_count_baseline") or DEFAULT_SETS_COUNT,
                "reps_target": role.get("reps_target_default") or DEFAULT_REPS_TARGET,
                "rpe_target": DEFAULT_RPE_TARGET,
                "rest_seconds": reinforcement_rest_seconds,
                "notes": (
                    f"Exercício adicionado automaticamente para atingir o mínimo de 9 séries equivalentes "
                    f"do grupo {_display_name(group_id)} (considerando contribuição de músculos secundários de "
                    "outros exercícios do dia)."
                ),
                "warmup": False,
            }
            exercises.append(new_exercise)
            exercises_added_for_group += 1
            total_exercise_count += 1
            used_names.add(_normalize_name(candidate.get("mapping_exercise_name")))
            if candidate_mapping_id:
                used_mapping_ids.add(candidate_mapping_id)
            _accumulate_exercise_contribution(new_exercise, contributions_by_mapping_id, totals)

        # Fase 2: catalogo esgotado, limite de novos exercicios do grupo
        # atingido, ou teto de exercicios totais do dia atingido — e ainda
        # falta volume — distribui o deficit entre os exercicios PRIMARY do
        # grupo ja presentes na ficha, uma serie por vez, sempre no de menor
        # sets_count (round-robin). Evita concentrar todo o reforco num unico
        # exercicio/equipamento (o que empurraria um exercicio isolado a 9+
        # series); quando so existe UM exercicio PRIMARY do grupo, ele
        # inevitavelmente recebe o deficit inteiro (nao ha outro equipamento
        # do grupo na ficha para dividir).
        if totals[group_id] < MIN_EQUIVALENT_SETS_PER_GROUP:
            primary_candidates = [ex for ex in exercises if ex.get("muscle_group_id") == group_id]
            if primary_candidates:
                remaining_sets = math.ceil(MIN_EQUIVALENT_SETS_PER_GROUP - totals[group_id])
                for _ in range(remaining_sets):
                    below_cap_candidates = [
                        ex
                        for ex in primary_candidates
                        if (ex.get("sets_count") or 0) < MAX_SETS_PER_EXERCISE_REINFORCEMENT
                    ]
                    if not below_cap_candidates:
                        # Todos os candidatos do grupo ja atingiram o teto
                        # rigido por exercicio — para o loop e deixa o
                        # deficit remanescente cair na checagem abaixo, que
                        # registra em insufficient_target_volume_groups.
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

        if totals[group_id] < MIN_EQUIVALENT_SETS_PER_GROUP:
            insufficient_target_volume_groups.append(
                {
                    "muscle_primary": _display_name(group_id),
                    "muscle_group_id": group_id,
                    "equivalent_sets_achieved": round(totals[group_id], 2),
                    "equivalent_sets_target": MIN_EQUIVALENT_SETS_PER_GROUP,
                    "exercises_added": exercises_added_for_group,
                    "reason": "catalogo_esgotado",
                }
            )

    return exercises, insufficient_target_volume_groups

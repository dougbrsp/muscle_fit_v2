"""Motor de ficha v2 (USE_TEMPLATE_VOLUME_ENGINE_V2) — Fase 5:
validador/reparo determinístico.

Esta é a fase mais crítica do motor v2 (seção 22 do documento: "a IA não deve
ser o validador matemático" — toda a matemática de volume fica em código
Python determinístico). Regra NÃO-NEGOCIÁVEL, checada por teste dedicado
(Casos 17/18 da seção 64): `prioritize` NUNCA é lido por
`calculate_slot_activation`/`detect_deviations`/`repair_slot_exercises`/
`split_concentrated_exercises`/`greedy_deterministic_selection` — só
`order_exercises_for_session` lê `prioritize`, e só para decidir ORDEM,
depois que repair+split já convergiram.

`split_concentrated_exercises` é o passo de "variedade de equipamento":
roda DEPOIS de `repair_slot_exercises` já ter fechado o orçamento
(`BALANCED`) e cobre um gap que o reparo por déficit/excesso não cobre —
um grupo pode bater o orçamento com toda a ativação concentrada num único
exercício, e isso não conta como desvio de orçamento (não há
`UNDER_ALLOCATED`/`OVER_ALLOCATED` a corrigir).
"""

from __future__ import annotations

import itertools
from typing import Any, Optional

from ..periodization.limits import LEGACY_V2_MAX_SETS_PER_EXERCISE
from .template_volume_domain import MuscleSlotBudget

# Reparo nunca roda para sempre (seção 67: heurística de fallback também
# serve de base do reparo pós-IA) — teto de iterações determinístico.
# Elevado de 6 para 10 junto com a correção de "evitar 1 série órfã": quando
# 3+ exercícios diretos de um grupo precisam convergir ao piso preferido
# (2 séries) antes do reparo poder remover um deles em vez de deixar sobra
# de 1, cada exercício consome 1 iteração de decremento + a remoção em si —
# com 3 exercícios isso já usa 4 dos 6 slots antigos, sem folga pra
# rebalancear (increase_sets) depois da remoção (bug real observado:
# Bíceps ficava OVER_ALLOCATED porque o reparo esgotava as iterações
# reduzindo/removendo, sem chance de reequilibrar). Continua um teto FINITO
# e determinístico, só maior.
MAX_REPAIR_ITERATIONS = 10

# Referência inicial de "sessão compacta" (seção 20 do documento:
# "preferred_sets_per_exercise = 2-4... se o catálogo for extremamente
# limitado, permitir aumento controlado"). Deliberadamente MENOR que o piso
# legado do ABC (MAX_SETS_PER_EXERCISE_REINFORCEMENT=9,
# abc_volume_reinforcement.py) — aquele valor é do fluxo ABC hardcoded, não
# deve ser reaproveitado cegamente pelo motor v2 genérico.
MAX_SETS_PER_EXERCISE_V2 = LEGACY_V2_MAX_SETS_PER_EXERCISE  # dono: periodization/limits.py (M76, E9)

# Faixa PREFERIDA (soft range) de séries por exercício, usada pelo
# pós-processamento `split_concentrated_exercises` — não é o mesmo conceito
# de `MAX_SETS_PER_EXERCISE_V2` (esse é o teto RÍGIDO respeitado durante o
# reparo por déficit/excesso, `repair_slot_exercises`). Valor calibrado pelo
# feedback real do usuário: o motor deve mirar 3-4 séries por exercício
# sempre que possível, e evitar ao máximo exercícios com menos de 2 séries.
# `PREFERRED_MAX_SETS_PER_EXERCISE_V2` (teto do range) continua sendo o
# GATILHO de quando disparar o split — só a DISTRIBUIÇÃO do resultado passou
# a mirar o range inteiro, não só o teto.
PREFERRED_SETS_PER_EXERCISE_RANGE_V2 = (3, 4)
PREFERRED_MAX_SETS_PER_EXERCISE_V2 = PREFERRED_SETS_PER_EXERCISE_RANGE_V2[1]

# Piso que a distribuição do split tenta preservar em cada exercício
# resultante — só é violado (parte com 1 série) quando matematicamente
# impossível evitar dado o número de candidatos disponíveis no catálogo
# daquela academia/grupo (ver `_balanced_set_distribution`).
MIN_PREFERRED_SETS_PER_EXERCISE_V2 = 2


def _normalize_name(value: Any) -> str:
    import unicodedata

    return unicodedata.normalize("NFC", str(value or "").strip().casefold())


def calculate_slot_activation(
    exercises: list[dict[str, Any]],
    muscle_group_names_by_id: dict[int, str],
) -> dict[int, dict[str, float]]:
    """Wrapper tipado sobre `VolumeService.get_muscle_activation_for_exercises`
    (fonte única de `sets × stimulus_weight`, nunca reimplementada aqui),
    cruzando nome->id — `VolumeService` indexa o resultado por NOME de
    grupo, não por id (risco documentado: dois grupos com o mesmo nome
    colidiriam; sem UNIQUE constraint hoje em `muscle_groups.name`, ver
    seção "Riscos principais" do plano aprovado).

    `exercises` precisa ter `mapping_id` e `sets_count`. Retorna
    `{muscle_group_id: {"primary": f, "secondary": f, "stabilizer": f, "total": f}}`
    — grupos sem nenhuma ativação não aparecem no dict (mesmo comportamento
    "ignora incompleto" do resto do projeto)."""
    from .technique_volume import activation_payload
    from .volume_service import VolumeService

    names_by_id_normalized = {
        _normalize_name(name): group_id for group_id, name in muscle_group_names_by_id.items()
    }

    # Fase 5 (PR 5): a técnica do item (quando houver) segue junto - o
    # VolumeService soma o equivalente PLANEJADO às séries, e o desvio
    # (UNDER/OVER_ALLOCATED) passa a enxergá-lo, como o preview do builder.
    # M76 E3: pelo payload único de ativação.
    activation_input = [activation_payload(ex) for ex in exercises if ex.get("mapping_id") and ex.get("sets_count")]

    service = VolumeService()
    rows = service.get_muscle_activation_for_exercises(activation_input)

    result: dict[int, dict[str, float]] = {}
    for row in rows:
        group_id = names_by_id_normalized.get(_normalize_name(row.get("muscle_group")))
        if group_id is None:
            continue
        primary = float(row.get("primary_equivalent") or 0.0)
        secondary = float(row.get("secondary_equivalent") or 0.0)
        stabilizer = float(row.get("stabilizer_equivalent") or 0.0)
        result[group_id] = {
            "primary": primary,
            "secondary": secondary,
            "stabilizer": stabilizer,
            "total": primary + secondary + stabilizer,
        }
    return result


def detect_deviations(
    budgets: list[MuscleSlotBudget],
    activation: dict[int, dict[str, float]],
) -> list[dict[str, Any]]:
    """Classifica cada budget contra a ativação atual — UNDER_ALLOCATED /
    BALANCED / OVER_ALLOCATED, contra o envelope EFETIVO (pós-recuperação).
    Não lê `prioritize` (o dataclass carrega o campo, mas esta função nunca
    o consulta — só `deviation_status`/`effective_slot_min/max`)."""
    deviations: list[dict[str, Any]] = []
    for budget in budgets:
        total = activation.get(budget.muscle_group_id, {}).get("total", 0.0)
        deviations.append(
            {
                "muscle_group_id": budget.muscle_group_id,
                "muscle_group_name": budget.muscle_group_name,
                "activation_total": total,
                "effective_slot_min": budget.effective_slot_min,
                "effective_slot_max": budget.effective_slot_max,
                "status": budget.deviation_status(total),
                "recovery_status": budget.recovery_status,
            }
        )
    return deviations


def repair_slot_exercises(
    exercises: list[dict[str, Any]],
    budgets: list[MuscleSlotBudget],
    candidate_matrix: dict[int, list[dict[str, Any]]],
    muscle_group_names_by_id: dict[int, str],
    max_iterations: int = MAX_REPAIR_ITERATIONS,
    max_sets_per_exercise: int = MAX_SETS_PER_EXERCISE_V2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Loop determinístico de ajuste (seção 67): para cada grupo
    UNDER_ALLOCATED (nunca `NO_DIRECT_WORK` — hard constraint, seção 18),
    tenta (a) somar 1 série num exercício existente cujo PRIMARY é esse
    grupo, respeitando `max_sets_per_exercise`; senão (b) adicionar um novo
    candidato do catálogo ainda não usado; se nada disso for possível,
    registra déficit (Caso 7: catálogo limitado -> déficit explicado, nunca
    inventa exercício). Para OVER_ALLOCATED, reduz 1 série do exercício de
    maior `sets_count` cujo PRIMARY é o grupo, nunca abaixo de 1 série.

    Ordem de iteração dos grupos é por `muscle_group_id` (determinística) —
    NUNCA por `prioritize`, que não é lido em nenhum ponto desta função
    (regra não-negociável da seção 36 do documento, checada pelo teste do
    Caso 17/18: repair_slot_exercises deve produzir EXATAMENTE a mesma
    seleção/sets_count com prioritize ligado ou desligado nos budgets).

    Nunca roda mais que `max_iterations` (loop infinito impossível — cada
    iteração sem nenhuma mudança encerra o loop antes do teto)."""
    exercises = [dict(ex) for ex in exercises]
    budgets_by_group = {b.muscle_group_id: b for b in budgets}
    used_mapping_ids: set[int] = {ex["mapping_id"] for ex in exercises if ex.get("mapping_id")}
    repair_log: list[dict[str, Any]] = []

    for _ in range(max_iterations):
        activation = calculate_slot_activation(exercises, muscle_group_names_by_id)
        changed = False

        for group_id in sorted(budgets_by_group.keys()):
            budget = budgets_by_group[group_id]
            if budget.is_hard_excluded:
                continue  # NO_DIRECT_WORK: nunca adiciona trabalho direto.

            total = activation.get(group_id, {}).get("total", 0.0)

            if total < budget.effective_slot_min:
                bumped = next(
                    (
                        ex
                        for ex in exercises
                        if ex.get("primary_group_id") == group_id
                        and (ex.get("sets_count") or 0) < max_sets_per_exercise
                    ),
                    None,
                )
                if bumped is not None:
                    bumped["sets_count"] = (bumped.get("sets_count") or 0) + 1
                    repair_log.append(
                        {"action": "increase_sets", "muscle_group_id": group_id, "mapping_id": bumped["mapping_id"]}
                    )
                    changed = True
                    continue

                candidates = candidate_matrix.get(group_id, [])
                new_candidate = next(
                    (c for c in candidates if c.get("mapping_id") not in used_mapping_ids), None
                )
                if new_candidate is not None:
                    new_exercise = {
                        "mapping_id": new_candidate["mapping_id"],
                        "exercise_name": new_candidate.get("exercise_name"),
                        "equipment_id": new_candidate.get("equipment_id"),
                        "equipment_name": new_candidate.get("equipment_name"),
                        "primary_group_id": group_id,
                        "sets_count": 2,
                        "reps_target": budget.reps_target,
                        "rpe_target": budget.rpe_target,
                        "rest_seconds": None,
                    }
                    exercises.append(new_exercise)
                    used_mapping_ids.add(new_candidate["mapping_id"])
                    repair_log.append(
                        {"action": "add_exercise", "muscle_group_id": group_id, "mapping_id": new_candidate["mapping_id"]}
                    )
                    changed = True
                    continue

                repair_log.append(
                    {
                        "action": "deficit_catalog_exhausted",
                        "muscle_group_id": group_id,
                        "deficit": round(budget.effective_slot_min - total, 2),
                    }
                )

            elif total > budget.effective_slot_max:
                group_exercises = [ex for ex in exercises if ex.get("primary_group_id") == group_id]
                # Prefere reduzir mantendo o piso de
                # `MIN_PREFERRED_SETS_PER_EXERCISE_V2` (2 séries) quando há
                # mais de um exercício direto para o grupo — sem isso, o
                # reparo sempre reduzia o exercício de MAIOR sets_count a
                # cada iteração, e em empate o desempate (ordem estável de
                # `exercises`) recaía sempre no MESMO exercício, sobrando
                # com 1 série sozinho enquanto o outro ficava com 2+ (bug
                # real relatado pelo usuário).
                reducible_above_floor = sorted(
                    (ex for ex in group_exercises if (ex.get("sets_count") or 0) > MIN_PREFERRED_SETS_PER_EXERCISE_V2),
                    key=lambda ex: -(ex.get("sets_count") or 0),
                )
                if reducible_above_floor:
                    target_exercise = reducible_above_floor[0]
                    target_exercise["sets_count"] = (target_exercise.get("sets_count") or 0) - 1
                    repair_log.append(
                        {
                            "action": "decrease_sets",
                            "muscle_group_id": group_id,
                            "mapping_id": target_exercise["mapping_id"],
                        }
                    )
                    changed = True
                elif len(group_exercises) > 1:
                    # Todos os exercícios diretos do grupo já estão no piso
                    # preferido (2 séries) e o total ainda excede o
                    # orçamento — a ativação INDIRETA (secundária/
                    # estabilizadora vinda de exercícios de outros grupos)
                    # já basta para estourar o teto sozinha. Reduzir mais
                    # deixaria um exercício "órfão" de 1 série sozinho, então
                    # prefere REMOVER o exercício com menor sets_count do
                    # grupo (mantendo pelo menos um exercício direto) —
                    # `used_mapping_ids` continua marcado, então esse
                    # candidato não volta a ser adicionado em iterações
                    # seguintes, evitando oscilação remove/adiciona.
                    smallest_index = min(
                        (i for i, ex in enumerate(exercises) if ex.get("primary_group_id") == group_id),
                        key=lambda i: exercises[i].get("sets_count") or 0,
                    )
                    removed = exercises.pop(smallest_index)
                    repair_log.append(
                        {
                            "action": "remove_exercise_avoid_single_set",
                            "muscle_group_id": group_id,
                            "mapping_id": removed.get("mapping_id"),
                        }
                    )
                    changed = True
                else:
                    # Único exercício direto para o grupo -- sem outro pra
                    # remover/rebalancear, cai para o piso rígido de 1
                    # (comportamento legado, ver `test_caso6`).
                    reducible = sorted(
                        (ex for ex in group_exercises if (ex.get("sets_count") or 0) > 1),
                        key=lambda ex: -(ex.get("sets_count") or 0),
                    )
                    if reducible:
                        target_exercise = reducible[0]
                        target_exercise["sets_count"] = (target_exercise.get("sets_count") or 0) - 1
                        repair_log.append(
                            {
                                "action": "decrease_sets",
                                "muscle_group_id": group_id,
                                "mapping_id": target_exercise["mapping_id"],
                            }
                        )
                        changed = True

        if not changed:
            break

    return exercises, repair_log


def _stimulus_weight_for_group(
    mapping_id: Optional[int],
    muscle_group_id: int,
    candidate_matrix: dict[int, list[dict[str, Any]]],
) -> float:
    """Busca o `stimulus_weight` que `mapping_id` contribui para
    `muscle_group_id`, procurando em `candidate_matrix[muscle_group_id]`
    (mesma lista usada para popular o slot desse grupo). Prefere a
    contribuição PRIMARY (mesmo critério de peso usado em
    `greedy_deterministic_selection`); cai para a primeira contribuição
    encontrada se não houver PRIMARY; `1.0` se o candidato não carregar
    `muscle_contributions` (dado ausente, nunca winvent a favor do split)."""
    for candidate in candidate_matrix.get(muscle_group_id, []):
        if candidate.get("mapping_id") != mapping_id:
            continue
        contributions = candidate.get("muscle_contributions") or []
        fallback_weight: Optional[float] = None
        for contribution in contributions:
            if contribution.get("muscle_group_id") != muscle_group_id:
                continue
            weight = float(contribution.get("stimulus_weight") or 1.0)
            if (contribution.get("role") or "").upper() == "PRIMARY":
                return weight
            if fallback_weight is None:
                fallback_weight = weight
        if fallback_weight is not None:
            return fallback_weight
    return 1.0  # dado ausente no candidato -> nunca inventa peso a favor do split


def _split_evenly(total_sets: int, parts_count: int) -> list[int]:
    """Divide `total_sets` em `parts_count` partes o mais iguais possível
    (diferença máxima de 1 série entre elas), maior parte primeiro —
    distribuição estável e determinística (sem depender de ordem de
    iteração)."""
    base, remainder = divmod(total_sets, parts_count)
    parts = [base + 1] * remainder + [base] * (parts_count - remainder)
    return sorted(parts, reverse=True)


def _balanced_set_distribution(total_sets: int, max_parts: int) -> tuple[list[int], bool]:
    """Escolhe em quantos exercícios (`k`, de 1 até `max_parts` — 1 original
    + candidatos alternativos disponíveis no catálogo) dividir `total_sets`,
    preferindo o MENOR `k` cuja divisão caiba inteiramente na faixa
    preferida `PREFERRED_SETS_PER_EXERCISE_RANGE_V2` (3-4 séries por
    exercício); se nenhum `k` couber exatamente nessa faixa, relaxa para
    `[MIN_PREFERRED_SETS_PER_EXERCISE_V2, teto_da_faixa]` (2-4); se mesmo
    assim nenhum `k` até `max_parts` servir (catálogo com candidatos
    demais para um total pequeno — cenário quase impossível na prática,
    pois o `k` mínimo relaxado quase sempre cabe bem antes de `max_parts`),
    usa `max_parts` e divide o mais igual possível, mesmo que alguma parte
    fique abaixo de 2 séries — o segundo valor retornado (`forced_single`)
    sinaliza esse caso extremo para o chamador registrar no `split_log`.

    Retorna as partes em ORDEM DECRESCENTE (maior parte primeiro — vai para
    o exercício original, que já tem o `stimulus_weight` conhecido; partes
    menores vão para os candidatos alternativos)."""
    low, high = PREFERRED_SETS_PER_EXERCISE_RANGE_V2
    max_parts = max(1, max_parts)

    for k in range(1, max_parts + 1):
        if k * low <= total_sets <= k * high:
            return _split_evenly(total_sets, k), False

    for k in range(1, max_parts + 1):
        if k * MIN_PREFERRED_SETS_PER_EXERCISE_V2 <= total_sets <= k * high:
            return _split_evenly(total_sets, k), False

    parts = _split_evenly(total_sets, max_parts)
    forced_single = any(part < MIN_PREFERRED_SETS_PER_EXERCISE_V2 for part in parts)
    return parts, forced_single


def split_concentrated_exercises(
    exercises: list[dict[str, Any]],
    budgets: list[MuscleSlotBudget],
    candidate_matrix: dict[int, list[dict[str, Any]]],
    muscle_group_names_by_id: dict[int, str],
    preferred_max_sets_per_exercise: int = PREFERRED_MAX_SETS_PER_EXERCISE_V2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pós-processamento de "variedade de equipamento" (causa raiz do
    problema reportado pelo usuário): roda DEPOIS que `repair_slot_exercises`
    já convergiu o orçamento (grupo pode estar BALANCED e ainda assim ter
    toda a ativação concentrada num único `mapping_id` com `sets_count` alto
    — `repair_slot_exercises` nunca intervém aqui, porque tecnicamente não há
    `UNDER_ALLOCATED`/`OVER_ALLOCATED` a corrigir; esse é exatamente o gap
    que este passo cobre).

    Para cada exercício cujo `primary_group_id` está com `sets_count` acima
    de `preferred_max_sets_per_exercise` (soft cap, teto de
    `PREFERRED_SETS_PER_EXERCISE_RANGE_V2` — NÃO é o teto rígido
    `MAX_SETS_PER_EXERCISE_V2` do reparo por déficit): calcula, via
    `_balanced_set_distribution`, a distribuição de `sets_count` MAIS
    EQUILIBRADA possível entre 2+ exercícios (mirando 3-4 séries por
    exercício, nunca deixando 1 série a menos que matematicamente
    inevitável — feedback real do usuário: o motor gerava divisões como
    5+1 ou deixava sobra de 1 série em vez de distribuir bem). A maior parte
    fica no próprio exercício original; as demais vão para candidatos ainda
    não usados de `candidate_matrix[group_id]`, convertendo cada parte por
    `stimulus_weight` (candidatos com pesos diferentes não têm a mesma
    "série equivalente").

    Só CONFIRMA a distribuição se a ativação recalculada de TODOS os grupos
    com budget continuar exatamente no mesmo `deviation_status` de antes
    (normalmente BALANCED) — do contrário reverte essa combinação de
    candidatos e tenta a PRÓXIMA combinação ainda não usada (itera por
    `itertools.combinations` sobre os candidatos disponíveis, na mesma
    ordem em que aparecem em `candidate_matrix` — menos usado primeiro),
    para nunca alterar a matemática de orçamento validada por
    `repair_slot_exercises` (regra de preservação do plano aprovado).

    A verificação cobre TODOS os grupos com budget, não só o grupo sendo
    dividido: candidatos costumam contribuir SECONDARY/STABILIZER para
    outros grupos (ex.: um exercício de "Costas" que também ativa "Bíceps"
    como SECONDARY) com um peso diferente do candidato original — trocar de
    candidato pode empurrar esse OUTRO grupo para fora do envelope mesmo
    quando o grupo dividido continua BALANCED (bug real observado: split em
    "Costas — miolo/densidade" fazia "Bíceps" virar OVER_ALLOCATED sem
    nenhuma verificação notar, porque só o status do próprio grupo dividido
    era comparado).

    Se nenhuma combinação de candidatos disponíveis preservar os status de
    todos os grupos (ou não houver candidato algum — catálogo genuinamente
    limitado, seção 20), a concentração original é mantida como está: nunca
    força um exercício irrelevante só para dividir série. Quando a própria
    distribuição matemática não conseguiu evitar uma parte com 1 série
    (`forced_single` de `_balanced_set_distribution` — catálogo com
    candidatos demais para um total pequeno, cenário extremo), o
    `split_log` registra `"forced_single_set": True` com uma nota
    explicando o motivo, em vez de esconder o caso de borda.

    NUNCA lê `prioritize` (mesma regra não-negociável de
    `repair_slot_exercises`/`detect_deviations`) — só reorganiza
    `sets_count`/composição, a ordenação final continua exclusivamente em
    `order_exercises_for_session`."""
    exercises = [dict(ex) for ex in exercises]
    budgets_by_group = {b.muscle_group_id: b for b in budgets}
    used_mapping_ids: set[int] = {ex["mapping_id"] for ex in exercises if ex.get("mapping_id")}
    split_log: list[dict[str, Any]] = []

    def _all_statuses(exercise_list: list[dict[str, Any]]) -> dict[int, str]:
        activation = calculate_slot_activation(exercise_list, muscle_group_names_by_id)
        return {
            gid: b.deviation_status(activation.get(gid, {}).get("total", 0.0))
            for gid, b in budgets_by_group.items()
        }

    for exercise in sorted(exercises, key=lambda ex: (ex.get("primary_group_id") or 0, ex.get("mapping_id") or 0)):
        group_id = exercise.get("primary_group_id")
        if group_id is None:
            continue
        budget = budgets_by_group.get(group_id)
        if budget is None or budget.is_hard_excluded:
            continue

        sets_count = exercise.get("sets_count") or 0
        if sets_count <= preferred_max_sets_per_exercise:
            continue

        candidates = candidate_matrix.get(group_id, [])
        alternatives = [
            c
            for c in candidates
            if c.get("mapping_id") is not None
            and c.get("mapping_id") != exercise.get("mapping_id")
            and c.get("mapping_id") not in used_mapping_ids
        ]
        if not alternatives:
            continue  # catálogo limitado para este grupo -> concentração aceitável.

        # max_parts = 1 (o próprio exercício) + quantos candidatos livres
        # existem — `_balanced_set_distribution` escolhe o MENOR número de
        # exercícios necessário dentro disso, não necessariamente todos.
        distribution, forced_single = _balanced_set_distribution(sets_count, 1 + len(alternatives))
        if len(distribution) < 2:
            continue  # não há como melhorar a concentração (não deveria ocorrer aqui).

        original_part, remaining_parts = distribution[0], distribution[1:]

        statuses_before = _all_statuses(exercises)
        weight_original = _stimulus_weight_for_group(exercise.get("mapping_id"), group_id, candidate_matrix)
        original_sets_backup = exercise["sets_count"]

        confirmed = False
        for combo in itertools.combinations(alternatives, len(remaining_parts)):
            exercise["sets_count"] = original_part
            new_exercises: list[dict[str, Any]] = []
            for part, alternative in zip(remaining_parts, combo):
                weight_alternative = _stimulus_weight_for_group(
                    alternative.get("mapping_id"), group_id, candidate_matrix
                )
                activation_for_part = part * weight_original
                alternative_sets_count = (
                    max(1, round(activation_for_part / weight_alternative)) if weight_alternative else part
                )
                new_exercises.append(
                    {
                        "mapping_id": alternative["mapping_id"],
                        "exercise_name": alternative.get("exercise_name"),
                        "equipment_id": alternative.get("equipment_id"),
                        "equipment_name": alternative.get("equipment_name"),
                        "primary_group_id": group_id,
                        "sets_count": alternative_sets_count,
                        "reps_target": exercise.get("reps_target") or budget.reps_target,
                        "rpe_target": exercise.get("rpe_target") or budget.rpe_target,
                        "rest_seconds": exercise.get("rest_seconds"),
                    }
                )
            exercises.extend(new_exercises)

            statuses_after = _all_statuses(exercises)

            if statuses_after != statuses_before:
                # Essa combinação de candidatos mudaria o deviation_status de
                # ALGUM grupo com budget (o próprio grupo dividido, ou outro
                # que recebe SECONDARY/STABILIZER de um dos candidatos com
                # peso diferente) — reverte e tenta a próxima combinação
                # disponível em vez de arriscar a matemática de orçamento já
                # validada.
                exercise["sets_count"] = original_sets_backup
                for _ in new_exercises:
                    exercises.pop()
                continue

            for alternative, new_exercise in zip(combo, new_exercises):
                used_mapping_ids.add(alternative["mapping_id"])
                log_entry = {
                    "action": "split_concentrated_exercise",
                    "muscle_group_id": group_id,
                    "from_mapping_id": exercise.get("mapping_id"),
                    "to_mapping_id": alternative["mapping_id"],
                    "sets_moved": new_exercise["sets_count"],
                }
                if forced_single and (
                    original_part < MIN_PREFERRED_SETS_PER_EXERCISE_V2
                    or new_exercise["sets_count"] < MIN_PREFERRED_SETS_PER_EXERCISE_V2
                ):
                    log_entry["forced_single_set"] = True
                    log_entry["note"] = (
                        "distribuição não pôde evitar 1 série: catálogo tem mais candidatos "
                        "livres do que séries dá para distribuir mantendo o mínimo de 2 por "
                        "exercício"
                    )
                split_log.append(log_entry)
            confirmed = True
            break

        if not confirmed:
            # Nenhuma combinação de candidatos disponíveis preservou os
            # status de todos os grupos -> mantém a concentração original
            # (exercise["sets_count"] já está com o backup, pois o loop só
            # sai sem confirmar depois de reverter todas as tentativas).
            exercise["sets_count"] = original_sets_backup

    return exercises, split_log


def enforce_minimum_sets_floor(
    exercises: list[dict[str, Any]],
    budgets: list[MuscleSlotBudget],
    candidate_matrix: dict[int, list[dict[str, Any]]],
    muscle_group_names_by_id: dict[int, str],
    max_iterations: int = MAX_REPAIR_ITERATIONS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Passo FINAL de reparo (roda depois de `split_concentrated_exercises`):
    garante que nenhum exercício sobreviva com `sets_count < 2` por um
    caminho que nunca passa por reparo/split de verdade.

    Bug real observado em produção (2026-08-29): um exercício de Tríceps
    sobrevivia com 1 série sem NENHUM `forced_single_set` no log — a causa
    raiz é que `repair_slot_exercises`, quando um grupo com um ÚNICO
    exercício direto fica OVER_ALLOCATED (normalmente por ativação
    SECONDARY/STABILIZER vinda de outro grupo, não por excesso do próprio
    exercício), reduz esse único exercício até o piso rígido de 1 série
    (comportamento legado documentado ali, "cai para o piso rígido de 1") —
    e nem `split_concentrated_exercises` (só dispara para `sets_count`
    ACIMA do teto preferido) nem `repair_slot_exercises` revisita isso depois
    (o loop de reparo só olha UNDER/OVER_ALLOCATED, não "sets_count baixo
    demais"). Ou seja: `_balanced_set_distribution`/split cobrem só o lado
    de CONCENTRAÇÃO (série demais num exercício); este passo cobre o lado
    oposto (série de menos), que nenhuma das duas rotinas cobria.

    Estratégia determinística, por exercício com `sets_count <
    MIN_PREFERRED_SETS_PER_EXERCISE_V2`:
    1. Pega emprestada 1 série de outro exercício do MESMO grupo que tenha
       sobra acima do piso (`sets_count > MIN_PREFERRED_SETS_PER_EXERCISE_V2`);
    2. Senão, tenta simplesmente elevar este exercício ao piso (2 séries);
    3. Senão, se houver outro exercício direto do mesmo grupo, remove este
       (evita órfão de 1 série sozinho, mesma filosofia de
       `remove_exercise_avoid_single_set` do reparo por excesso);
    4. Se nada disso for possível sem mudar o `deviation_status` de QUALQUER
       grupo com budget (mesma verificação de segurança de
       `split_concentrated_exercises`), mantém como está e registra
       `forced_single_set=True` com nota explicando — nunca esconde o caso
       de borda.

    NUNCA lê `prioritize` (mesma regra não-negociável dos outros passos desta
    fase)."""
    exercises = [dict(ex) for ex in exercises]
    budgets_by_group = {b.muscle_group_id: b for b in budgets}
    log: list[dict[str, Any]] = []
    already_logged_forced: set[int] = set()

    def _all_statuses(exercise_list: list[dict[str, Any]]) -> dict[int, str]:
        activation = calculate_slot_activation(exercise_list, muscle_group_names_by_id)
        return {
            gid: b.deviation_status(activation.get(gid, {}).get("total", 0.0))
            for gid, b in budgets_by_group.items()
        }

    for _ in range(max_iterations):
        changed = False

        for exercise in list(exercises):
            sets_count = exercise.get("sets_count") or 0
            if sets_count >= MIN_PREFERRED_SETS_PER_EXERCISE_V2:
                continue

            group_id = exercise.get("primary_group_id")
            budget = budgets_by_group.get(group_id)
            if budget is None or budget.is_hard_excluded:
                continue

            statuses_before = _all_statuses(exercises)
            siblings = [
                ex for ex in exercises if ex is not exercise and ex.get("primary_group_id") == group_id
            ]

            # (1) Pega 1 série emprestada de um "irmão" do mesmo grupo com
            # sobra acima do piso.
            donor = next(
                (
                    ex
                    for ex in sorted(siblings, key=lambda ex: -(ex.get("sets_count") or 0))
                    if (ex.get("sets_count") or 0) > MIN_PREFERRED_SETS_PER_EXERCISE_V2
                ),
                None,
            )
            if donor is not None:
                donor["sets_count"] = (donor.get("sets_count") or 0) - 1
                exercise["sets_count"] = sets_count + 1
                if _all_statuses(exercises) == statuses_before:
                    log.append(
                        {
                            "action": "raise_floor_borrow",
                            "muscle_group_id": group_id,
                            "mapping_id": exercise.get("mapping_id"),
                            "from_mapping_id": donor.get("mapping_id"),
                        }
                    )
                    changed = True
                    continue
                donor["sets_count"] = (donor.get("sets_count") or 0) + 1
                exercise["sets_count"] = sets_count

            # (2) Eleva o próprio exercício ao piso, se o orçamento comportar.
            exercise["sets_count"] = MIN_PREFERRED_SETS_PER_EXERCISE_V2
            if _all_statuses(exercises) == statuses_before:
                log.append(
                    {
                        "action": "raise_floor_bump",
                        "muscle_group_id": group_id,
                        "mapping_id": exercise.get("mapping_id"),
                    }
                )
                changed = True
                continue
            exercise["sets_count"] = sets_count

            # (3) Remove o órfão em vez de deixá-lo com 1 série sozinho,
            # desde que outro exercício direto do grupo continue cobrindo o
            # orçamento (nunca deixa o grupo cair para UNDER_ALLOCATED).
            if siblings:
                trial = [ex for ex in exercises if ex is not exercise]
                trial_statuses = _all_statuses(trial)
                if trial_statuses.get(group_id) == statuses_before.get(group_id):
                    exercises.remove(exercise)
                    log.append(
                        {
                            "action": "raise_floor_remove_orphan",
                            "muscle_group_id": group_id,
                            "mapping_id": exercise.get("mapping_id"),
                        }
                    )
                    changed = True
                    continue

            # (4) Nenhuma opção segura — mantém como está, log explicativo
            # (uma vez só por exercício, mesmo se outras iterações rodarem).
            mapping_id = exercise.get("mapping_id")
            if mapping_id not in already_logged_forced:
                already_logged_forced.add(mapping_id)
                log.append(
                    {
                        "action": "forced_single_set",
                        "muscle_group_id": group_id,
                        "mapping_id": mapping_id,
                        "sets_count": exercise.get("sets_count"),
                        "note": (
                            "nao foi possivel elevar ao piso de 2 series sem alterar o "
                            "deviation_status de algum grupo com budget (unico exercicio "
                            "direto do grupo sem candidato/irmao disponivel, ou orcamento "
                            "nao comporta o ajuste) -- mantido como esta em vez de forcar "
                            "uma mudanca matematica nao validada."
                        ),
                    }
                )

        if not changed:
            break

    return exercises, log


def order_exercises_for_session(
    exercises: list[dict[str, Any]],
    budgets: list[MuscleSlotBudget],
) -> list[dict[str, Any]]:
    """Aplica `prioritize` (seção 36.4) SÓ na ordenação final — chamado
    depois que `repair_slot_exercises` já convergiu. Não altera
    `sets_count`/`mapping_id`/composição da lista, apenas reordena (sort
    estável): exercícios cujo `primary_group_id` está marcado
    `prioritize=true` formam o bloco inicial (preservando a ordem relativa
    original entre si), seguidos do restante (idem). Direção inicial
    conforme o documento (seção 36.4) — refinamentos biomecânicos
    (composto/isolador/prehab) ficam para uma iteração futura, aqui a
    garantia estrutural mais importante é: NUNCA ler `prioritize` em volume,
    só aqui."""
    prioritized_groups = {b.muscle_group_id for b in budgets if b.prioritize}

    indexed = list(enumerate(exercises))
    indexed.sort(key=lambda pair: (0 if pair[1].get("primary_group_id") in prioritized_groups else 1, pair[0]))
    return [ex for _, ex in indexed]


def fatigue_base_key(
    index: int,
    exercise: dict[str, Any],
    prioritized_group_ids: set[int],
) -> tuple[int, int, int]:
    """Ordem BASE do passo 8 da secao 6.1, antes do greedy: primeiro os itens
    de grupo `prioritize`, depois os compostos (`is_compound`), depois a ordem
    do slot (o indice recebido). E tambem o criterio de desempate do greedy."""
    return (
        0 if exercise.get("primary_group_id") in prioritized_group_ids else 1,
        0 if exercise.get("is_compound") else 1,
        index,
    )


def fatigue_overlap(previous: dict[Any, float], candidate: dict[Any, float]) -> float:
    """`overlap = sum_g min(fatigue_prev_g, fatigue_next_g)` (secao 6.1 passo 8):
    quanto de fadiga os dois exercicios dividem. Zero = nenhum grupo em comum."""
    if not previous or not candidate:
        return 0.0
    return sum(
        min(float(previous.get(group_id) or 0.0), float(weight or 0.0))
        for group_id, weight in candidate.items()
        if group_id in previous
    )


def order_exercises_by_fatigue(
    exercises: list[dict[str, Any]],
    prioritized_group_ids: set[int],
) -> list[dict[str, Any]]:
    """Ordenacao final da sessao do Modo A DETERMINISTICO (secao 6.1 passo 8),
    que substitui `order_exercises_for_session` no caminho novo.

    Greedy deterministico, sem nenhuma aleatoriedade e sem empate irresolvido:

    1. a ordem BASE e `(prioritize, is_compound, ordem do slot)`;
    2. o primeiro exercicio e o primeiro dessa ordem;
    3. cada proximo e, entre os restantes, o que MINIMIZA
       `overlap = sum_g min(fatigue_prev_g, fatigue_next_g)` com o anterior -
       evitar fadiga compartilhada em sequencia e o objetivo;
    4. empate de overlap resolve pela ordem base (nunca por dicionario).

    Cada exercicio precisa trazer `muscle_fatigue` (`{muscle_group_id:
    fatigue_weight}`, de `exercise_muscle_contributions`), `is_compound` e
    `primary_group_id`. Funcao PURA: nao altera series, composicao nem nada
    alem da ordem, e `prioritize` continua entrando SO aqui (regra da Fase 5).
    """
    remaining = sorted(
        enumerate(exercises),
        key=lambda pair: fatigue_base_key(pair[0], pair[1], prioritized_group_ids),
    )
    if not remaining:
        return []

    ordered: list[tuple[int, dict[str, Any]]] = [remaining.pop(0)]
    while remaining:
        previous_fatigue = ordered[-1][1].get("muscle_fatigue") or {}
        best_position = min(
            range(len(remaining)),
            key=lambda position: (
                fatigue_overlap(previous_fatigue, remaining[position][1].get("muscle_fatigue") or {}),
                fatigue_base_key(
                    remaining[position][0], remaining[position][1], prioritized_group_ids
                ),
            ),
        )
        ordered.append(remaining.pop(best_position))
    return [exercise for _, exercise in ordered]


def greedy_deterministic_selection(
    budgets: list[MuscleSlotBudget],
    candidate_matrix: dict[int, list[dict[str, Any]]],
    muscle_group_names_by_id: dict[int, str],
    max_sets_per_exercise: int = MAX_SETS_PER_EXERCISE_V2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fallback SEM IA (seção 66/67 — evita HTTP 502 quando a chamada à IA
    falha e o problema pode ser resolvido sem LLM). Semeia 1 candidato por
    grupo não excluído por recuperação (o primeiro do catálogo — já vem
    ordenado por menor uso recente) com `sets_count` aproximando o midpoint
    nominal, depois converge com `repair_slot_exercises` (mesma função usada
    após a IA — "isso também serve como base do reparo pós-IA")."""
    seeded: list[dict[str, Any]] = []
    used_mapping_ids: set[int] = set()

    for budget in sorted(budgets, key=lambda b: b.muscle_group_id):
        if budget.is_hard_excluded or budget.effective_slot_max <= 0:
            continue
        candidates = candidate_matrix.get(budget.muscle_group_id, [])
        candidate = next((c for c in candidates if c.get("mapping_id") not in used_mapping_ids), None)
        if candidate is None:
            continue

        weight = 1.0
        for contribution in candidate.get("muscle_contributions") or []:
            if contribution.get("muscle_group_id") == budget.muscle_group_id and (
                contribution.get("role") or ""
            ).upper() == "PRIMARY":
                weight = float(contribution.get("stimulus_weight") or 1.0)
                break

        mid = budget.nominal_slot_mid * budget.recovery_multiplier
        sets_count = max(1, min(max_sets_per_exercise, round(mid / weight) if weight else 1))

        seeded.append(
            {
                "mapping_id": candidate["mapping_id"],
                "exercise_name": candidate.get("exercise_name"),
                "equipment_id": candidate.get("equipment_id"),
                "equipment_name": candidate.get("equipment_name"),
                "primary_group_id": budget.muscle_group_id,
                "sets_count": sets_count,
                "reps_target": budget.reps_target,
                "rpe_target": budget.rpe_target,
                "rest_seconds": None,
            }
        )
        used_mapping_ids.add(candidate["mapping_id"])

    return repair_slot_exercises(
        seeded, budgets, candidate_matrix, muscle_group_names_by_id, max_sets_per_exercise=max_sets_per_exercise
    )

"""Desempenho por GRUPO MUSCULAR em `/dashboard/progressao` - parte pura.

## Fonte unica do status (2026-11-01)

O `performance_status` do grupo NAO e mais uma classificacao propria. Ele e a
AGREGACAO PONDERADA dos status dos EXERCICIOS do grupo, calculados pelo dono
(`dashboard/comparability.classify_exercise_progression`, o mesmo da home):
`comparability.aggregate_group_progression` (regra e constantes la). Os pesos
sao os `stimulus_weight` de `exercise_muscle_contributions` (participacao
muscular que ja existia), sem peso novo. Antes, uma regra propria (melhor e1RM
da janela contra a anterior, banda de +-5%, descarte de +-50%, ativacao minima)
dava um numero diferente do da home - terceira divergencia reportada.

## O que sobrou deste modulo (informativo, nunca gera status)

A variacao do melhor e1RM por exercicio entre as duas janelas (chave,
excecao do §8.1, academia da maquina e limiar de atipico) vem pronta do dono
`comparability.group_e1rm_changes`. Aqui so se monta a resposta da tela:
`performance_change_pct` (media ponderada pelo `stimulus_weight`, sem os
atipicos) e `performance_exercises`. Nada disso muda o status do grupo.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .dashboard.comparability import (
    GROUP_REASON_LOW_ACTIVATION,
    GROUP_REASON_NO_COMPARABLE_EXERCISE,
    GROUP_E1RM_OUTLIER_REASON,
    aggregate_group_progression,
    group_e1rm_changes,
)
from .dashboard.metrics_registry import ExerciseProgressionStatus
from .muscle_group_progression import (
    DADOS_INSUFICIENTES,
    ESTAVEL,
    PROGREDINDO,
    REGREDINDO,
)

# Base da variacao de e1RM (informativa; ecoada no payload).
PERFORMANCE_BASIS = "best_e1rm_epley_same_exercise"
# Base do STATUS do grupo: agregacao ponderada dos status dos exercicios (dono).
STATUS_BASIS = "weighted_exercise_progression_status"

# Motivo de exclusao da media informativa (dono), reexportado para a tela.
OUTLIER_CHANGE = GROUP_E1RM_OUTLIER_REASON

# Motivos de `performance_insufficient_reason` (None quando ha classificacao);
# definidos pelo dono, reexportados aqui para o contrato da tela.
NO_COMPARABLE_EXERCISE = GROUP_REASON_NO_COMPARABLE_EXERCISE
LOW_ACTIVATION = GROUP_REASON_LOW_ACTIVATION
# Lote de progressao falhou: o grupo fica sem status por indisponibilidade, nao por falta de dado.
PROGRESSION_UNAVAILABLE = "PROGRESSION_UNAVAILABLE"

# Mesmo vocabulario do enum do dono, com os valores de enum do grupo.
_GROUP_STATUS = {
    ExerciseProgressionStatus.PROGRESSING: PROGREDINDO,
    ExerciseProgressionStatus.STABLE: ESTAVEL,
    ExerciseProgressionStatus.REGRESSING: REGREDINDO,
    ExerciseProgressionStatus.INSUFFICIENT_DATA: DADOS_INSUFICIENTES,
}

_ZERO_COUNTS = {
    "progressing": 0, "stable": 0, "plateau_suspected": 0, "regressing": 0,
    "comparable_total": 0, "not_comparable": 0, "insufficient_data": 0,
}


def insufficient() -> Dict[str, Any]:
    """Leitura de um grupo sem exercicio comparavel nas duas janelas."""
    return {
        "performance_status": DADOS_INSUFICIENTES,
        "performance_change_pct": None,
        "performance_exercises_compared": 0,
        "performance_exercises_excluded": 0,
        "performance_activation_sum": 0.0,
        "performance_insufficient_reason": NO_COMPARABLE_EXERCISE,
        "performance_exercises": [],
        "progression_counts": dict(_ZERO_COUNTS),
    }


def mark_progression_unavailable(items: Iterable[Dict[str, Any]]) -> None:
    """Degradacao: o motivo de cada grupo passa a ser `PROGRESSION_UNAVAILABLE`
    (o status ja e DADOS_INSUFICIENTES porque nao ha status de exercicio)."""
    for item in items:
        item["performance_insufficient_reason"] = PROGRESSION_UNAVAILABLE


def build_group_performance(
    rows: Iterable[Dict[str, Any]],
    *,
    exercise_items: Optional[Iterable[Dict[str, Any]]] = None,
    group_weights: Optional[Dict[int, Dict[int, float]]] = None,
) -> Dict[int, Dict[str, Any]]:
    """STATUS: `exercise_items` (itens de `get_exercises_progression_batch`,
    `exercise_id` canonico + `status`) e `group_weights`
    (`{grupo: {exercicio canonico: stimulus_weight}}`) alimentam
    `comparability.aggregate_group_progression`. Sem eles, nenhum grupo tem
    status (todos `insufficient()`).

    `rows` (INFORMATIVO, nao gera status): uma linha por (periodo, exercicio
    canonico, equipamento, convencao bruta, grupo) com `period`
    (current ou previous), `exercise_id`, `exercise_name`, `equipment_id`,
    `load_convention` (bruta; `None` = sem cadastro = `total`),
    `muscle_group_id`, `stimulus_weight` e `best_e1rm_kg`. Devolve
    `{muscle_group_id: leitura}`; grupo ausente = sem exercicio comparavel
    (quem monta usa `insufficient()`)."""
    # Status (a) de cada exercicio, como o lote o classificou: so repassado ao
    # item de `performance_exercises` para a tela ver o status individual.
    status_by_exercise = {int(item["exercise_id"]): item["status"] for item in (exercise_items or [])}
    by_group: Dict[int, List[Dict[str, Any]]] = {}
    for change in group_e1rm_changes(rows):
        group_id = change.pop("muscle_group_id")
        change.pop("gym_id")  # chave interna do dono; fora do contrato da tela
        change["status"] = status_by_exercise.get(change["exercise_id"])
        by_group.setdefault(group_id, []).append(change)

    # Status: agregacao do dono sobre os status dos exercicios do grupo.
    aggregated: Dict[int, Dict[str, Any]] = {}
    for group_id, weights in (group_weights or {}).items():
        entries = [
            (status_by_exercise[int(exercise_id)], weight)
            for exercise_id, weight in weights.items()
            if int(exercise_id) in status_by_exercise
        ]
        if entries:
            aggregated[int(group_id)] = aggregate_group_progression(entries)

    result: Dict[int, Dict[str, Any]] = {}
    for group_id in set(by_group) | set(aggregated):
        exercises = by_group.get(group_id, [])
        used = [item for item in exercises if not item["outlier"]]
        total_weight = sum(item["stimulus_weight"] for item in used)
        change_pct = (
            round(sum(item["_change"] * item["stimulus_weight"] for item in used) / total_weight, 1)
            if used and total_weight > 0
            else None
        )
        verdict = aggregated.get(group_id)
        if verdict is None:
            status, reason, weight_sum, counts = DADOS_INSUFICIENTES, NO_COMPARABLE_EXERCISE, 0.0, dict(_ZERO_COUNTS)
        else:
            status = _GROUP_STATUS[verdict["status"]]
            reason, weight_sum, counts = verdict["reason"], verdict["comparable_weight"], verdict["counts"]
        exercises.sort(key=lambda i: (i["outlier"], -i["stimulus_weight"], i["exercise_name"] or ""))
        for item in exercises:
            del item["_change"]
        result[group_id] = {
            "performance_status": status,
            "performance_change_pct": change_pct,
            "performance_exercises_compared": len(used),
            "performance_exercises_excluded": len(exercises) - len(used),
            "performance_activation_sum": round(weight_sum, 3),
            "performance_insufficient_reason": reason,
            "performance_exercises": exercises,
            "progression_counts": counts,
        }
    return result

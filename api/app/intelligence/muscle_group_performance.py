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

Para cada exercicio presente nas DUAS janelas com a MESMA chave de
comparabilidade (`comparability.build_key`) compara-se o MELHOR e1RM de cada
janela (Epley com RIR pelo dono `domain/e1rm.reliable_e1rm`, politica
`MissingRpePolicy.RIR_ZERO`, series de trabalho com ate
`E1RM_MAX_RELIABLE_REPS` reps - ver `analytics._fetch_group_e1rm_rows`). O
resultado e `performance_change_pct` (media das variacoes ponderada pelo
`stimulus_weight`) e a lista `performance_exercises`, para o usuario CONFERIR
o numero. A variacao de mais de `OUTLIER_CHANGE_PCT` (+-50%) e atipica
(quase sempre registro errado) e fica FORA da media informativa, marcada com
`outlier: true`. Nada disso muda o status do grupo.

Regras herdadas de `dashboard/comparability.py`: troca de maquina/convencao
NAO e piora (chaves diferentes nao se comparam); equipamento ausente so compara
quando todas as exposicoes do exercicio estao sem equipamento (§8.1);
`assisted` e `bodyweight` nao tem e1RM; so series de trabalho.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from .dashboard.comparability import (
    GROUP_REASON_LOW_ACTIVATION,
    GROUP_REASON_NO_COMPARABLE_EXERCISE,
    ComparabilityKey,
    aggregate_group_progression,
    all_equipment_missing,
    build_key,
    keys_match,
)
from .dashboard.metrics_registry import ExerciseProgressionStatus
from ..load_conventions import normalize_load_convention, supports_e1rm
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

# Variacao de e1RM (em %, absoluta) acima da qual o exercicio e tratado como
# atipico ("conferir registro") e fica fora da media INFORMATIVA. Estritamente
# maior: +-50% exatos ainda contam. Nunca altera o status do grupo.
OUTLIER_CHANGE_PCT = 50.0
OUTLIER_CHANGE = "OUTLIER_CHANGE"

# Motivos de `performance_insufficient_reason` (None quando ha classificacao);
# definidos pelo dono, reexportados aqui para o contrato da tela.
NO_COMPARABLE_EXERCISE = GROUP_REASON_NO_COMPARABLE_EXERCISE
LOW_ACTIVATION = GROUP_REASON_LOW_ACTIVATION

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


def _float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _is_outlier(change_pct: float) -> bool:
    return abs(change_pct) > OUTLIER_CHANGE_PCT


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
    rows = list(rows)

    # Excecao do §8.1: decidida por EXERCICIO, olhando as duas janelas juntas.
    equipment_by_exercise: Dict[int, List[Optional[int]]] = {}
    for row in rows:
        if row.get("exercise_id") is None:
            continue
        equipment_by_exercise.setdefault(int(row["exercise_id"]), []).append(
            None if row.get("equipment_id") is None else int(row["equipment_id"])
        )
    agnostic = {ex: all_equipment_missing(ids) for ex, ids in equipment_by_exercise.items()}

    # (grupo, chave) -> melhor e1RM por janela + peso + nome.
    pairs: Dict[Tuple[int, ComparabilityKey], Dict[str, Any]] = {}
    for row in rows:
        best = _float(row.get("best_e1rm_kg"))
        weight = _float(row.get("stimulus_weight")) or 0.0
        if row.get("exercise_id") is None or best is None or best <= 0 or weight <= 0:
            continue
        convention = normalize_load_convention(row.get("load_convention"))
        if not supports_e1rm(convention):
            continue
        exercise_id = int(row["exercise_id"])
        key = build_key(exercise_id, row.get("equipment_id"), convention, equipment_agnostic=agnostic[exercise_id])
        if not keys_match(key, key):  # equipamento nao resolvido fora da excecao
            continue
        entry = pairs.setdefault(
            (int(row["muscle_group_id"]), key),
            {"exercise_name": row.get("exercise_name"), "weight": weight, "current": None, "previous": None},
        )
        period = "current" if row["period"] == "current" else "previous"
        # `None` e `total` normalizam para a mesma chave: fica o maior.
        entry[period] = best if entry[period] is None else max(entry[period], best)
        entry["weight"] = max(entry["weight"], weight)
        entry["exercise_name"] = entry["exercise_name"] or row.get("exercise_name")

    by_group: Dict[int, List[Dict[str, Any]]] = {}
    for (group_id, key), entry in pairs.items():
        if entry["current"] is None or entry["previous"] is None:
            continue
        change = (entry["current"] - entry["previous"]) / entry["previous"] * 100.0
        by_group.setdefault(group_id, []).append(
            {
                "exercise_id": key.canonical_exercise_id,
                "exercise_name": entry["exercise_name"],
                "equipment_id": key.equipment_id,
                "load_convention": key.load_convention.value,
                "previous_e1rm_kg": round(entry["previous"], 1),
                "current_e1rm_kg": round(entry["current"], 1),
                "change_pct": round(change, 1),
                "stimulus_weight": round(entry["weight"], 3),
                "outlier": _is_outlier(change),
                "excluded_reason": OUTLIER_CHANGE if _is_outlier(change) else None,
                "_change": change,
            }
        )

    # Status: agregacao do dono sobre os status dos exercicios do grupo.
    status_by_exercise = {int(item["exercise_id"]): item["status"] for item in (exercise_items or [])}
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

"""Nucleo PURO da tela de briefing do exercicio (M4).

Responsabilidades, nenhuma delas tocando banco ou HTTP:

1. `summarize_last_execution` - condensa a ultima execucao daquele exercicio
   (series de trabalho, carga de topo, faixa de reps) no minimo que a tela
   mostra: "como foram as series e cargas da ultima vez".
2. `summarize_performance` - responde "estou progredindo, estavel ou
   regredindo?" com a REGRA UNICA do dashboard (`comparability`): ultima
   exposicao contra a anterior comparavel (mesmo exercicio + equipamento +
   convencao de carga). Mesmos codigos e rotulos de `/dashboard/exercicios`.
3. `describe_adjustment` - o AJUSTE DE HOJE decidido pelo motor
   (INCREASE_LOAD, ADD_SET, HOLD...), exibido como acao ("Aumentar carga").

Por que separar 2 e 3: ate aqui o badge "Progredindo/Mantendo/Regredindo"
saia da DECISAO do motor (ou da carga prescrita contra a executada) - ou
seja, media a PRESCRICAO com rotulos de DESEMPENHO. "Reduzir carga" hoje virava
"Regredindo" mesmo com a ultima execucao melhor que a anterior, e o dashboard
dizia outra coisa do mesmo exercicio. Agora o rotulo de desempenho so sai de
`comparability`; a decisao do motor continua visivel, mas como acao.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Mapping, Optional, Sequence

from .progression_engine import (
    ADD_SET,
    APPLY_TECHNIQUE,
    DECREASE_LOAD,
    HOLD,
    INCREASE_LOAD,
    SET_REP_RANGE_10_12,
    SKIP,
)
from .progression_status_labels import EXERCISE_PROGRESSION_LABELS
from .training_history import ExerciseExecution
from ..set_types import is_working_set

UNKNOWN = "unknown"

SOURCE_DECISION = "engine_decision"
SOURCE_COMPARABILITY = "comparability"

PERFORMANCE_SCOPE_EQUIPMENT = "equipment"
PERFORMANCE_SCOPE_EXERCISE = "exercise"

@lru_cache(maxsize=1)
def performance_labels() -> dict[str, str]:
    """Rotulos (dono `progression_status_labels`, M76 Lote 3c, C13) dos status
    comparaveis do dono `comparability.COMPARABLE_PROGRESSION_STATUSES`. Import
    preguicoso: o modulo nao carrega o pacote `intelligence` no import (api-core)."""
    from ..intelligence.dashboard.comparability import COMPARABLE_PROGRESSION_STATUSES

    return {status.value: EXERCISE_PROGRESSION_LABELS[status.value] for status in COMPARABLE_PROGRESSION_STATUSES}


NO_COMPARISON_LABEL = "Sem comparação"

# Acoes do motor em pt-BR - mesmos textos de `PROGRESSION_DECISION_LABELS`
# (`frontend/src/lib/exercise-progress.ts`). E o AJUSTE de hoje, nao desempenho.
DECISION_LABELS = {
    INCREASE_LOAD: "Aumentar carga",
    DECREASE_LOAD: "Reduzir carga",
    ADD_SET: "Acrescentar série",
    APPLY_TECHNIQUE: "Aplicar técnica avançada",
    SET_REP_RANGE_10_12: "Definir faixa de 10 a 12 repetições",
    HOLD: "Manter",
    SKIP: "Sem progressão nesta sessão",
}

# ---------------------------------------------------------------------------
# Ultima execucao
# ---------------------------------------------------------------------------


def summarize_last_execution(
    execution: Optional[ExerciseExecution],
    *,
    local_date: Any = None,
    activity_id: Optional[int] = None,
    equipment_name: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Resumo da ultima execucao, ou `None` quando nao houve nenhuma.

    So series de TRABALHO entram nos numeros: aquecimento e aplicacao de
    tecnica nao dizem nada sobre "como foi" e inflariam a contagem. As series
    aparecem todas na lista (com o tipo), porque o usuario quer ver o que
    fez - mas `working_sets`/`top_weight_kg` so contam as de trabalho.
    """
    if execution is None:
        return None
    sets = [
        {
            "set_number": item.set_number,
            "set_type": item.set_type,
            "reps": item.reps,
            "weight_kg": float(item.weight_kg) if item.weight_kg is not None else None,
            "rpe": float(item.rpe) if item.rpe is not None else None,
            "is_working": is_working_set(item.set_type),
        }
        for item in execution.sets
    ]
    trabalho = [item for item in sets if item["is_working"]]
    pesos = [item["weight_kg"] for item in trabalho if item["weight_kg"] is not None]
    reps = [item["reps"] for item in trabalho if item["reps"] is not None]
    rpes = [item["rpe"] for item in trabalho if item["rpe"] is not None]
    return {
        "activity_id": activity_id,
        "local_date": local_date.isoformat() if hasattr(local_date, "isoformat") else local_date,
        "equipment_name": equipment_name,
        "sets": sets,
        "working_sets": len(trabalho),
        "top_weight_kg": max(pesos) if pesos else None,
        "reps_min": min(reps) if reps else None,
        "reps_max": max(reps) if reps else None,
        "top_rpe": max(rpes) if rpes else None,
    }


def find_last_execution(
    sessions: Sequence[Any],
    *,
    exercise_id: Optional[int],
    workout_plan_exercise_id: Optional[int] = None,
) -> Optional[tuple[Any, ExerciseExecution]]:
    """A execucao MAIS RECENTE deste exercicio nas sessoes dadas.

    `sessions` vem do `TrainingHistoryRepository` em ordem cronologica
    CRESCENTE - por isso a varredura e de tras para frente.

    O casamento e por `exercise_id` CANONICO do catalogo, nunca por nome: o
    nome do exercicio na ficha e o do historico divergem quando o catalogo e
    renomeado, e o mesmo exercicio em outro equipamento continua sendo o
    mesmo exercicio (e justamente o que o usuario quer comparar). A sessao em
    que a ficha ATUAL foi executada e ignorada via
    `workout_plan_exercise_id`: "ultima execucao" quer dizer a anterior a
    esta, nunca a linha que esta sendo executada agora.
    """
    if exercise_id is None:
        return None
    for session in reversed(list(sessions)):
        for execution in session.exercises:
            if execution.exercise_id != int(exercise_id):
                continue
            if (
                workout_plan_exercise_id is not None
                and execution.workout_plan_exercise_id == int(workout_plan_exercise_id)
            ):
                continue
            if not execution.sets:
                continue
            return session, execution
    return None


# ---------------------------------------------------------------------------
# Desempenho (comparability) x ajuste de hoje (motor)
# ---------------------------------------------------------------------------


def summarize_performance(
    exposures: Sequence[Mapping[str, Any]],
    *,
    equipment_id: Optional[int] = None,
    gym_context: Any = ...,
) -> dict[str, Any]:
    """Status de DESEMPENHO do exercicio - a mesma regra do dashboard.

    #188: `gym_context` = academia do contexto de MAQUINA do item de hoje (ja
    resolvida pelo dono `equipment_context` a partir do vinculo; `...` = nao
    informado, sem recorte). O recorte e o codigo "primeira vez nesta
    academia" (`equipment_context_code`) saem do dono `exposure_service`.

    `exposures` sao as de `exposure_service.get_exposures_between` (ordem
    cronologica). A classificacao e `classify_exercise_progression`: ultima
    exposicao contra a anterior comparavel (mesmo exercicio + equipamento +
    convencao de carga). Nada e recalculado aqui.

    Quando o item de hoje tem equipamento (`equipment_id` = `equipment.id`
    canonico) e ha historico NELE, so as exposicoes desse equipamento entram
    (`scope="equipment"`): a pergunta da tela e "como estou neste aparelho?".
    Sem historico nele, vale o historico do exercicio (`scope="exercise"`),
    igual ao drill-down do dashboard. Sem exposicao nenhuma, `scope=None`.
    No escopo do aparelho, o dono recebe o historico COMPLETO ate (inclusive)
    a ultima exposicao nele, sem subconjunto: outro aparelho no meio
    interrompe a sequencia e a troca decide a calibracao (issue #188) - o
    mesmo veredito do painel para esse historico.
    """
    from ..intelligence.dashboard.comparability import COMPARABLE_PROGRESSION_STATUSES
    from ..intelligence.dashboard.exposure_service import equipment_context_code, outcome_for_equipment

    outcome, historico, no_aparelho = outcome_for_equipment(exposures, equipment_id, gym_context)
    escopo: Optional[str] = None
    if historico:
        escopo = PERFORMANCE_SCOPE_EQUIPMENT if no_aparelho else PERFORMANCE_SCOPE_EXERCISE
    status = outcome.status
    ultima = historico[-1] if historico else None
    return {
        "status": status.value,
        "reasons": list(outcome.reasons),
        "comparable": status in COMPARABLE_PROGRESSION_STATUSES,
        "scope": escopo,
        "comparable_exposures": outcome.comparable_count,
        "equipment_name": (ultima or {}).get("equipment_name"),
        "load_convention": (ultima or {}).get("load_convention"),
        "last_local_date": (ultima or {}).get("local_date"),
        "equipment_context_code": equipment_context_code(exposures, equipment_id, gym_context, outcome),
    }


def performance_unavailable() -> dict[str, Any]:
    """Desempenho quando o historico nao pode ser lido: sem comparacao (a tela
    nao mostra veredito inventado)."""
    from ..intelligence.dashboard.metrics_registry import ExerciseProgressionStatus

    return {
        "status": ExerciseProgressionStatus.INSUFFICIENT_DATA.value,
        "reasons": [],
        "comparable": False,
        "scope": None,
        "comparable_exposures": 0,
        "equipment_name": None,
        "load_convention": None,
        "last_local_date": None,
        "equipment_context_code": None,
    }


def describe_adjustment(prescription: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """O AJUSTE DE HOJE decidido pelo motor - `{"decision", "label", "reason",
    "source"}` ou `None` quando a ficha nao traz decisao.

    E a prescricao (o que fazer agora), nao desempenho: por isso o rotulo e a
    acao ("Aumentar carga"), nunca "Progredindo"/"Regredindo".

    Autoridade UNICA: a decisao do motor v2 congelada na prescricao
    (`prescription["decision"]`). M76 (C9): sem decisao, sem ajuste - o
    indicador do motor legado (`prescription["progression"]`, a seta de
    carga) nao vira mais "Ajuste de hoje"."""
    prescription = prescription or {}
    motivo = str(prescription.get("reason") or prescription.get("progression_note") or "").strip() or None

    decision = prescription.get("decision")
    if decision and decision in DECISION_LABELS:
        return {
            "decision": decision,
            "label": DECISION_LABELS[decision],
            "reason": motivo,
            "source": SOURCE_DECISION,
            # RPE 10 Lote 6/P9: sinais de recuperacao congelados na ficha
            # (`recovery_signal_summary`); o briefing mostra "<texto> em DD/MM".
            "recovery_signals": [dict(item) for item in prescription.get("recovery_signals") or []],
            "effective_rpe_ceiling": prescription.get("effective_rpe_ceiling"),
        }

    return None


def trend_from_performance(performance: Mapping[str, Any], adjustment: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Campo `trend` LEGADO (`{"status", "label", "reason", "source"}`), mantido
    para clientes em cache que ainda o leem. Agora DERIVADO do desempenho
    (`performance`): o rotulo "Progredindo"/"Regredindo" nunca mais sai da
    prescricao. O ajuste do motor vai so no texto, como "Ajuste de hoje"."""
    from ..intelligence.dashboard.comparability import performance_trend

    code = str(performance.get("status") or "")
    status = performance_trend(code) or UNKNOWN
    label = performance_labels().get(code, NO_COMPARISON_LABEL) if performance.get("comparable") else NO_COMPARISON_LABEL
    partes = [
        "Desempenho da última execução comparado com a anterior no mesmo equipamento e convenção de carga."
        if performance.get("comparable")
        else "Sem duas execuções comparáveis (mesmo equipamento e convenção de carga) para avaliar o desempenho."
    ]
    if adjustment:
        partes.append(f"Ajuste de hoje: {str(adjustment['label']).lower()}.")
    return {"status": status, "label": label, "reason": " ".join(partes), "source": SOURCE_COMPARABILITY}

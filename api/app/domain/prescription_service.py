"""Sugestão de carga/reps para UM par exercício × equipamento, fora da geração
de ficha (M76, Lote 1a — C1, C2, C3).

Antes deste módulo existiam três motores de prescrição. O legado
(`progression_service.suggest_load_and_reps`, +5% sem passo, faixa derivada da
própria sessão — e por isso nunca subia carga) alimentava quatro telas:
`GET /exercises/suggested-progression` (troca de equipamento no Modo Treino,
treino livre, exercício extra em `/planos-treino`), o card "Sugestão para a
próxima sessão" de `/dashboard/progressao`, o drill-down de músculos e o
congelamento de linhas acrescentadas à mão na ficha. Todas passam a ler daqui,
e daqui só sai o que o motor v2 decide (`generation/deterministic_prescription.prescribe_pairs`
→ `domain/progression_engine.prescribe`), com a mesma segurança do par e o
mesmo contrato de recuperação PROJETADO da geração de ficha.

O que este módulo NÃO faz (regra do M76: consumidor não recalcula):

- não decide carga, reps, RPE, incremento nem teto — tudo vem da decisão;
- não tira média de RPE nem olha aquecimento;
- a ÚNICA leitura própria é a da última execução, para o que a tela mostra
  como "última vez" (`last_*`) e para a carga de partida de uma trilha que o
  avaliador ainda não calibrou — e mesmo essa usa a regra de calibração do
  motor (`progression_engine.session_working_load`: mínimo das séries de
  trabalho, seção 5.10).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from .progression_engine import (
    ADD_SET,
    APPLY_TECHNIQUE,
    DECREASE_LOAD,
    EVIDENCE_TRANSITION_DEFERRED,
    INCREASE_LOAD,
    LOAD_NOT_APPLICABLE,
    SET_REP_RANGE_10_12,
    SKIP,
    ReadinessContract,
    classify_sets,
    resolve_track,
    session_working_load,
)
from .session_loads import load_step
from .set_measures import is_rep_based, normalize_measure
from .progression_evaluator import goal_tracks
from .training_history import SetRecord

# Rótulo do motor gravado no snapshot de linha acrescentada à mão na ficha
# (congelamento fora dos Modos A/B). O contrato do snapshot não muda.
PAIR_SUGGESTION_ENGINE_VERSION = "pair-suggestion-v2"

LOAD_SOURCE_ENGINE = "engine"  # carga da trilha (ou da decisão do motor)
LOAD_SOURCE_LAST_EXECUTION = "last_execution"  # trilha sem carga: calibração pela execução

# Mesmos códigos de `progression` que a UI já desenha como seta.
PROGRESSION_INCREASED = "increased"
PROGRESSION_DECREASED = "decreased"
PROGRESSION_SAME = "same"
_TREND_BY_PROGRESSION = {PROGRESSION_INCREASED: "up", PROGRESSION_DECREASED: "down", PROGRESSION_SAME: "same"}


@dataclass(frozen=True)
class PairQuery:
    """Pedido de sugestão. `equipment_id` é o `equipment.id` CANÔNICO (não o
    `gym_equipment.id`); ausente = usa a trilha mais recente do exercício."""

    exercise_id: int
    equipment_id: Optional[int] = None
    muscle_group_id: Optional[int] = None
    rpe_target: Optional[str] = None  # alvo de RPE da linha/ficha (intenção; o motor corta)
    sets_count: Optional[int] = None
    # M97 F1: objetivo da linha (VALOR do dono `training_goal`; None = default).
    # A trilha e a fase lidas são SÓ as desse objetivo.
    training_goal: Optional[str] = None

    @property
    def key(self) -> tuple[int, Optional[int]]:
        return (int(self.exercise_id), None if self.equipment_id is None else int(self.equipment_id))


@dataclass(frozen=True)
class LastExecution:
    """Última execução do exercício (só séries de TRABALHO contam)."""

    local_date: Any
    equipment_id: Optional[int]
    last_load_kg: Optional[float]  # última série de trabalho
    last_reps: Optional[int]
    working_load_kg: Optional[float]  # mínimo das séries de trabalho (calibração, seção 5.10)


@dataclass(frozen=True)
class PairSuggestion:
    exercise_id: int
    equipment_id: Optional[int]
    decision: Mapping[str, Any]
    suggested_load_kg: Optional[float]
    load_source: Optional[str]
    load_increment_kg: float
    last_execution: Optional[LastExecution] = None
    track_load_status: Optional[str] = None

    @property
    def reps_target(self) -> Optional[str]:
        low, high = self.decision.get("suggested_reps_min"), self.decision.get("suggested_reps_max")
        if low is None or high is None:
            return None
        return f"{low}-{high}"

    @property
    def rpe_target(self) -> Optional[str]:
        target = self.decision.get("effective_rpe_target")
        return None if target is None else str(target)

    @property
    def strength_load(self) -> Optional[dict[str, Any]]:
        """Faixa de carga de forca por %1RM da decisao (evidencia do motor
        `progression_engine.EVIDENCE_STRENGTH_E1RM_LOAD`); None fora dela."""
        from .progression_engine import EVIDENCE_STRENGTH_E1RM_LOAD

        value = (self.decision.get("evidence") or {}).get(EVIDENCE_STRENGTH_E1RM_LOAD)
        return dict(value) if value else None

    @property
    def progression(self) -> Optional[str]:
        """Seta da UI: carga sugerida contra a última carga de trabalho."""
        last = self.last_execution.last_load_kg if self.last_execution else None
        if self.suggested_load_kg is None or last is None:
            return None
        if self.suggested_load_kg > last:
            return PROGRESSION_INCREASED
        if self.suggested_load_kg < last:
            return PROGRESSION_DECREASED
        return PROGRESSION_SAME

    @property
    def trend(self) -> str:
        return _TREND_BY_PROGRESSION.get(self.progression or "", "unknown")


# ---------------------------------------------------------------------------
# Funções puras
# ---------------------------------------------------------------------------


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _set_record(raw: Mapping[str, Any]) -> SetRecord:
    weight = raw.get("weight_kg")
    rpe = raw.get("rpe")
    return SetRecord(
        set_number=int(raw.get("set_number") or 0),
        set_type=raw.get("set_type") or "working",
        reps=_int_or_none(raw.get("reps")),
        weight_kg=None if weight is None else Decimal(str(weight)),
        rpe=None if rpe is None else Decimal(str(rpe)),
        technique_code=raw.get("technique_code"),
        anchor_set_number=_int_or_none(raw.get("anchor_set_number")),
        # Lote T3: medida da serie (ausente = reps).
        measure=normalize_measure(raw.get("measure")),
        duration_seconds=_int_or_none(raw.get("duration_seconds")),
    )


def last_execution_from_logs(
    logs: Sequence[Mapping[str, Any]], exercise_id: int, equipment_id: Optional[int] = None
) -> Optional[LastExecution]:
    """Última execução do exercício em `services.list_exercise_logs` (já em
    ordem do mais recente para o mais antigo, `mapping_id` já canônico).

    Prefere a última NO equipamento pedido (`equipment_catalog_id`); sem
    nenhuma nele, a última do exercício. Ocorrência sem série de trabalho com
    carga (só aquecimento, só técnica) é pulada: aquecimento não conta e
    aplicação de técnica nunca vira carga base (bug G2)."""
    candidates = []
    for entry in logs or ():
        if _int_or_none(entry.get("mapping_id")) != int(exercise_id):
            continue
        records = [_set_record(item) for item in (entry.get("sets") or []) if item]
        # Lote T3 (medidas de serie): serie por tempo/maximo de reps nao e carga
        # base (`set_measures.is_rep_based`), como a aplicacao de tecnica.
        working = [
            item
            for item in classify_sets(records).working
            if item.weight_kg is not None and is_rep_based(item.measure)
        ]
        if not working:
            continue
        candidates.append((entry, working))
    if not candidates:
        return None
    chosen = candidates[0]
    if equipment_id is not None:
        same = [item for item in candidates if _int_or_none(item[0].get("equipment_catalog_id")) == int(equipment_id)]
        if same:
            chosen = same[0]
    entry, working = chosen
    last = working[-1]
    minimum = session_working_load(working)
    return LastExecution(
        local_date=entry.get("local_date"),
        equipment_id=_int_or_none(entry.get("equipment_catalog_id")),
        last_load_kg=float(last.weight_kg) if last.weight_kg is not None else None,
        last_reps=last.reps,
        working_load_kg=float(minimum) if minimum is not None else None,
    )


def progression_note(suggestion: PairSuggestion) -> str:
    """Texto curto da decisão para a tela (com acentos). O motor grava
    `reason` técnico; este é o rótulo para o usuário, a partir dos MESMOS
    rótulos do "Ajuste de hoje" (`exercise_briefing.DECISION_LABELS`)."""
    from .exercise_briefing import DECISION_LABELS

    decision = suggestion.decision
    code = decision.get("decision")
    evidence = decision.get("evidence") or {}
    reps = suggestion.reps_target
    if suggestion.last_execution is None and suggestion.suggested_load_kg is None:
        return f"Sem histórico registrado — comece conservador dentro da faixa {reps or 'prescrita'}."
    if code == SKIP:
        return "Grupo sem trabalho direto pela recuperação: sem progressão nesta sessão."
    partes: list[str] = []
    if evidence.get(EVIDENCE_TRANSITION_DEFERRED):
        partes.append("Dose reduzida pela recuperação: manter a carga e adiar a progressão.")
    elif code == INCREASE_LOAD and evidence.get("load_increment_kg") is not None:
        partes.append(f"Aumentar carga: +{_kg(evidence['load_increment_kg'])} kg (passo do equipamento).")
    elif code == DECREASE_LOAD and evidence.get("load_increment_kg") is not None:
        partes.append(
            f"Reduzir carga: −{_kg(abs(float(evidence['load_increment_kg'])))} kg "
            "(duas sessões abaixo do piso de repetições)."
        )
    elif code in (ADD_SET, APPLY_TECHNIQUE, SET_REP_RANGE_10_12):
        partes.append(f"{DECISION_LABELS[code]}.")
    else:
        partes.append("Manter carga e faixa de repetições até cumprir o critério de progressão.")
    if suggestion.load_source == LOAD_SOURCE_LAST_EXECUTION:
        partes.append("Carga de partida = a da última execução (calibração).")
    return " ".join(partes)


def _kg(value: Any) -> str:
    number = float(value)
    return str(int(number)) if number == int(number) else f"{number:.2f}".rstrip("0").rstrip(".").replace(".", ",")


# ---------------------------------------------------------------------------
# Acesso a dados (somente SELECT) + motor
# ---------------------------------------------------------------------------


def current_readiness(user_id: int) -> dict[int, ReadinessContract]:
    """Contrato de recuperação por grupo para AGORA, pela mesma função da
    geração de ficha (`freeze_mode_b.projected_readiness`, alvo = agora)."""
    from ..generation.freeze_mode_b import projected_readiness

    return projected_readiness(int(user_id), None, None)


def _has_pair_track(tracks: Mapping[tuple, Any], exercise_id: int, equipment_id: Optional[int]) -> bool:
    """Ha trilha do par (em qualquer contexto de fadiga) entre `tracks` (ja
    as do objetivo pedido, `progression_evaluator.goal_tracks`)?"""
    if equipment_id is None:
        return False
    return any(key[0] == int(exercise_id) and key[1] == int(equipment_id) for key in tracks)


def _latest_track_equipment(tracks: Mapping[tuple, Any], exercise_id: int) -> Optional[int]:
    """Equipamento da trilha executada mais recentemente para o exercício
    (entre `tracks`, ja as do objetivo pedido)."""
    found = [track for key, track in tracks.items() if key[0] == int(exercise_id)]
    if not found:
        return None
    found.sort(key=lambda track: (track.last_executed_at or datetime.min, track.id or 0))
    return int(found[-1].equipment_id)


def suggest_for_pairs(
    conn: Any,
    user_id: int,
    queries: Sequence[PairQuery],
    *,
    readiness: Optional[Mapping[int, ReadinessContract]] = None,
    logs: Optional[Sequence[Mapping[str, Any]]] = None,
    now: Optional[datetime] = None,
) -> dict[tuple[int, Optional[int]], PairSuggestion]:
    """Uma sugestão por pedido (chave = `PairQuery.key` como veio), em número
    fixo de queries (o motor lê em lote).

    `readiness` ausente = contrato PROJETADO para agora (nunca READY fixo);
    `logs` = `services.list_exercise_logs` do usuário, para a última execução."""
    from .. import progression_state as ps
    from ..generation.deterministic_prescription import PairRequest, prescribe_pairs

    queries = list(queries)
    if not queries:
        return {}
    if readiness is None:
        readiness = current_readiness(user_id)

    all_tracks = ps.load_tracks(conn, int(user_id))

    resolved: list[tuple[PairQuery, Optional[int]]] = []
    for query in queries:
        # M97 F1: so as trilhas do objetivo do pedido
        tracks = goal_tracks(all_tracks, query.training_goal)
        equipment_id = query.equipment_id
        if equipment_id is None:
            # Sem equipamento pedido: o da ULTIMA execucao (a "ultima vez" que a
            # tela mostra), se houver trilha nele; senao a trilha executada mais
            # recentemente.
            executed = last_execution_from_logs(logs or (), int(query.exercise_id)) if logs else None
            if executed is not None and _has_pair_track(tracks, int(query.exercise_id), executed.equipment_id):
                equipment_id = executed.equipment_id
            else:
                equipment_id = _latest_track_equipment(tracks, int(query.exercise_id))
        resolved.append((query, equipment_id))

    requests = [
        PairRequest(
            exercise_id=int(query.exercise_id),
            equipment_id=equipment_id,
            muscle_group_id=query.muscle_group_id,
            model_sets_count=query.sets_count,
            model_rpe_target=query.rpe_target,
            training_goal=query.training_goal,
        )
        for query, equipment_id in resolved
    ]
    prescriptions = prescribe_pairs(conn, int(user_id), requests, readiness=readiness, now=now)
    steps = ps.load_equipment_steps(conn, [equipment for _, equipment in resolved if equipment is not None])

    result: dict[tuple[int, Optional[int]], PairSuggestion] = {}
    for (query, equipment_id), request in zip(resolved, requests):
        prescription = prescriptions.get(request.key)
        if prescription is None:
            continue
        decision = prescription.decision.to_dict()
        # Sem posicao planejada: a trilha do par executada mais recentemente
        # (qualquer contexto de fadiga) - a mesma que `prescribe_pairs` usou.
        track = (
            resolve_track(all_tracks, int(query.exercise_id), equipment_id, None, query.training_goal)[0]
            if equipment_id is not None
            else None
        )
        last = last_execution_from_logs(logs or (), int(query.exercise_id), equipment_id) if logs is not None else None
        load = decision.get("suggested_load_kg")
        source = LOAD_SOURCE_ENGINE if load is not None else None
        load_applicable = track is None or track.load_status != LOAD_NOT_APPLICABLE
        if load is None and load_applicable and last is not None and last.working_load_kg is not None:
            # Trilha que o avaliador ainda nao calibrou: a calibracao do motor
            # (secao 5.10) adota o minimo das series de trabalho executadas.
            load, source = last.working_load_kg, LOAD_SOURCE_LAST_EXECUTION
        step = steps.get(int(equipment_id)) if equipment_id is not None else None
        result[query.key] = PairSuggestion(
            exercise_id=int(query.exercise_id),
            equipment_id=equipment_id,
            decision=decision,
            suggested_load_kg=load,
            load_source=source,
            load_increment_kg=float(load_step(step)),
            last_execution=last,
            track_load_status=track.load_status if track is not None else None,
        )
    return result


def suggest_for_pair(
    conn: Any,
    user_id: int,
    exercise_id: int,
    equipment_id: Optional[int] = None,
    *,
    readiness: Optional[Mapping[int, ReadinessContract]] = None,
    logs: Optional[Sequence[Mapping[str, Any]]] = None,
    rpe_target: Optional[str] = None,
    now: Optional[datetime] = None,
    training_goal: Optional[str] = None,
) -> Optional[PairSuggestion]:
    """Atalho de `suggest_for_pairs` para um par só. `training_goal` (VALOR do
    dono `training_goal`; None = default) escolhe a trilha, a fase e a escada
    do objetivo (M97, a mesma `PairQuery.training_goal` da geração de ficha)."""
    query = PairQuery(
        exercise_id=int(exercise_id), equipment_id=equipment_id, rpe_target=rpe_target, training_goal=training_goal
    )
    return suggest_for_pairs(conn, user_id, [query], readiness=readiness, logs=logs, now=now).get(query.key)


def legacy_indicators(suggestion: Optional[PairSuggestion]) -> dict[str, Any]:
    """Os 4 indicadores que a ficha já exibia (`progression`,
    `progression_note`, `suggested_load_kg`, `suggested_reps`), agora vindos
    da decisão do motor v2."""
    if suggestion is None:
        return {"progression": None, "progression_note": None, "suggested_load_kg": None, "suggested_reps": None}
    return {
        "progression": suggestion.progression,
        "progression_note": progression_note(suggestion),
        "suggested_load_kg": suggestion.suggested_load_kg,
        "suggested_reps": suggestion.decision.get("suggested_reps_max"),
    }

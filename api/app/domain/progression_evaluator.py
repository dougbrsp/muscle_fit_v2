"""Avaliador de sessao do Motor de Progressao v2 (PR-10; pseudocodigo
`on_session_finish` da secao 5.11 do PLANO_20260912.md).

Continua PURO: recebe a janela de sessoes (no maximo 21, em ordem
cronologica), o estado persistido e as entradas dos modulos donos de cada
assunto (seguranca, recuperacao, catalogo de tecnicas), e devolve o estado
novo + as decisoes a gravar. Quem grava e `app/progression_state.py`.

A sequencia de qualificacao do grupo e RECALCULADA da janela a cada
avaliacao (modelo "inferido + eventos" da secao 5.11): e isso que faz o
replay de `scripts/rebuild_progression_tracks.py` ser idempotente por
construcao.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional, Sequence

from ..set_types import normalize_set_type
from .training_goal import DEFAULT_GOAL, goal_value, goals_in
from .progression_engine import reps_for_phase as _engine_reps_for_phase
from .progression_engine import working_has_non_rep_sets
from .progression_engine import (
    ACTIVE,
    ADD_SET,
    APPLY_TECHNIQUE,
    CALIBRATION,
    DECREASE_LOAD,
    EVIDENCE_EXCESS_REPS_FAST_TRACK,
    EVIDENCE_GROUP_INCREASE_BY_TRACK,
    EVIDENCE_GROUP_PHASE_ADVANCE,
    EVIDENCE_LOAD_ADOPTED_FROM_EXECUTION,
    EVIDENCE_STRENGTH_PLATEAU_RESET,
    EVIDENCE_TRACK_PLATEAU_LOAD,
    EVIDENCE_TRACK_PLATEAUS,
    LOAD_ADOPTION_SOURCE,
    F1,
    F2,
    F3,
    F4,
    F5,
    F6,
    HOLD,
    INCREASE_LOAD,
    LOAD_NOT_APPLICABLE,
    PENDING_LOAD_APPLIED,
    PENDING_LOAD_SOURCE_PLATEAU,
    SET_REP_RANGE_10_12,
    GroupState,
    PhaseStep,
    Plateau,
    ProgressionDecision,
    ReadinessContract,
    READY_CONTRACT,
    TechniqueCatalogEntry,
    TrackSessionOutcome,
    TrackState,
    _as_date,  # COD-06: copia identica; o motor de progressao e o dono
    adopted_working_load,
    advance_load_phase,
    apply_effective,
    apply_strength_plateau_reset,
    arm_excess_reps_fast_track,
    arm_track_plateau_load,
    clamp_sets_count,
    classify_sets,
    eligible_techniques,
    evaluate_group_session,
    evaluate_track_session,
    execution_applies_pending,
    group_load_phase_progress,
    group_pending_effective,
    ladder_for,
    load_phase_by_track,
    new_group_state,
    next_step,
    pending_load_source,
    plateau_confirmed,
    regression_triggered,
    session_working_load,
    strength_stall_triggered,
    track_key,
    track_key_payload,
    track_label,
    track_pending_load_status,
)
from .effective_intensity import from_prescription
from .training_history import ExerciseExecution, SessionRecord
from .training_safety import DEFAULT_MAX_SAFE_RPE, READY, IntensityComposition, TrainingSafety, compose

# DEFAULT_MAX_SAFE_RPE (D3, par sem seguranca carregada): dono e `training_safety` (M76, C10).


def track_evidence_outcome(value: Any) -> Optional[str]:
    """Veredito de uma trilha em `evidence["tracks"]`, tolerante ao formato
    antigo. Ate 2026-09-18 gravava-se so a string (`"NEUTRAL"`); agora grava-se
    `{"outcome": ..., "reason": ...}`. Decisoes ja gravadas continuam legiveis
    sem migracao (e o replay reescreve as do usuario que ele reprocessa)."""
    if isinstance(value, Mapping):
        return value.get("outcome")
    return value if value is None or isinstance(value, str) else str(value)


def track_evidence_reason(value: Any) -> Optional[str]:
    """Motivo da nao-qualificacao; None no formato antigo, que nao o gravava."""
    return value.get("reason") if isinstance(value, Mapping) else None


@dataclass(frozen=True)
class SessionInputs:
    """Tudo o que o avaliador precisa saber sobre os pares da sessao.

    `safety` vem de `domain/training_safety.py`, `readiness` do contrato da
    secao 5.8 e `catalog` de `advanced_techniques`. Nada disso e decidido aqui.
    """

    safety: Mapping[tuple[int, int], TrainingSafety] = field(default_factory=dict)
    readiness: Mapping[int, ReadinessContract] = field(default_factory=dict)
    catalog: Mapping[str, TechniqueCatalogEntry] = field(default_factory=dict)
    primary_group: Mapping[int, int] = field(default_factory=dict)  # exercise_id -> grupo PRIMARY
    stimulus_weight: Mapping[int, float] = field(default_factory=dict)  # exercise_id -> peso PRIMARY
    is_compound: Mapping[int, bool] = field(default_factory=dict)  # exercise_catalog.is_compound
    load_increment_kg: Mapping[int, Decimal] = field(default_factory=dict)  # equipment_id -> step
    is_advanced_user: bool = False
    model_fixed_sets: bool = False
    activation_max_reached: Mapping[int, bool] = field(default_factory=dict)
    # Fase 5, PR 6: teto de ativacao calculado POR SESSAO (janela de 7 dias
    # que termina nela) - `intelligence.progression_activation.ActivationCeiling`.
    # Devolve `grupo -> leitura` com `.reached` e `.to_evidence()`. Presente,
    # vence o mapa fixo acima para os grupos que ele avaliou.
    activation_probe: Optional[Callable[[Sequence[SessionRecord], SessionRecord], Mapping[int, Any]]] = None
    # PR 2 da fadiga previa: `exercises.id` -> contexto de fadiga da execucao
    # na sessao REAL (`PriorFatigueBand.value`, dono `domain/prior_fatigue.py`).
    # Ausente = descansado.
    fatigue_context: Mapping[int, str] = field(default_factory=dict)

    def activation_for(self, window: Sequence[SessionRecord], session: SessionRecord) -> Mapping[int, Any]:
        if self.activation_probe is None:
            return {}
        return self.activation_probe(window, session) or {}

    def activation_reached(self, muscle_group_id: int, activation: Mapping[int, Any]) -> bool:
        found = activation.get(muscle_group_id)
        if found is not None:
            return bool(found.reached)
        return bool(self.activation_max_reached.get(muscle_group_id))

    @staticmethod
    def activation_in_range(muscle_group_id: int, activation: Mapping[int, Any]) -> bool:
        found = activation.get(muscle_group_id)
        return bool(found is not None and getattr(found, "in_range", False))

    def track_key_for(self, execution: ExerciseExecution) -> Optional[tuple[int, int, str, str]]:
        """Trilha da execucao: o par + o contexto de fadiga da execucao na
        sessao real (`fatigue_context`, montado por
        `progression_state.build_session_inputs` com o dono
        `domain/prior_fatigue.py`) + o objetivo da execucao (M97 F1,
        `exercises.training_goal`). Sem contexto medido vale o descansado."""
        pair = execution.pair
        if pair is None:
            return None
        return track_key(
            pair[0],
            pair[1],
            self.fatigue_context.get(int(execution.exercise_row_id)),
            execution.training_goal,
        )

    def safety_for(self, key: tuple) -> TrainingSafety:
        """Seguranca e do PAR (exercicio x equipamento), em qualquer contexto."""
        key = (int(key[0]), int(key[1]))
        found = self.safety.get(key)
        if found is not None:
            return found
        return TrainingSafety(
            exercise_id=key[0],
            equipment_id=key[1],
            equipment_exercise_id=None,
            max_safe_rpe=DEFAULT_MAX_SAFE_RPE,
            failure_allowed=False,
            safety_source=None,
        )

    def readiness_for(self, muscle_group_id: Optional[int]) -> ReadinessContract:
        if muscle_group_id is None:
            return READY_CONTRACT
        return self.readiness.get(muscle_group_id, READY_CONTRACT)

    def composition_for(self, key: tuple[int, int], muscle_group_id: Optional[int]) -> IntensityComposition:
        """Composicao de intensidade do par nesta sessao - so de `training_safety`."""
        # M97 F2: o objetivo da trilha (4a posicao da chave) entra na
        # composicao (forca: teto 9); hipertrofia = a composicao de sempre.
        goal = key[3] if len(key) > 3 else None
        return compose(self.safety_for(key), self.readiness_for(muscle_group_id).readiness, training_goal=goal)

    def ceiling_for(self, key: tuple[int, int], muscle_group_id: Optional[int]) -> tuple[int, bool]:
        """Teto seguro efetivo do par nesta sessao - so de `training_safety`."""
        composition = self.composition_for(key, muscle_group_id)
        return composition.ceiling, composition.failure_allowed

    def evaluation_intensity_for(
        self, execution: ExerciseExecution, key: tuple, muscle_group_id: Optional[int]
    ) -> tuple[int, bool, str, Optional[str]]:
        """`(teto, falha permitida, status de recuperacao, origem da negacao da
        falha)` contra os quais a sessao JA EXECUTADA e avaliada (Lote 2/P7,
        2026-10-11). A origem (`failure_denied_by`, Lote 1/P1) e a da ficha
        congelada com o overlay de hoje (snapshot antigo: derivada do teto do
        par congelado) - a MESMA que o Modo Treino usou para gravar o
        `safety_event` da serie, entao tela e avaliacao concordam.

        Com ficha congelada: o teto e a falha da FICHA daquele dia com o
        overlay de seguranca de hoje - exatamente o que o Modo Treino usou
        para gravar a serie (`effective_intensity.from_prescription`, via
        `intensity_for_row`), entao tela e avaliacao concordam sobre a mesma
        serie. O contrato de recuperacao NAO entra de novo: ele ja foi
        composto no congelamento (dose reduzida e o que a ficha mandou), e a
        sessao que seguiu a ficha CONTA - o status volta READY.

        Sem ficha congelada (treino livre, ficha legada): o caminho anterior,
        teto do par composto com o contrato informado (READY no `finish`)."""
        frozen = execution.prescribed.frozen
        if frozen is not None and frozen.effective_rpe_ceiling is not None:
            intensity = from_prescription(frozen.intensity_snapshot(), self.safety_for(key))
            if intensity is not None and intensity.ceiling is not None:
                return intensity.ceiling, intensity.failure_allowed, READY, intensity.failure_denied_by
        composition = self.composition_for(key, muscle_group_id)
        return (
            composition.ceiling,
            composition.failure_allowed,
            self.readiness_for(muscle_group_id).status,
            composition.failure_denied_by,
        )


@dataclass(frozen=True)
class SessionDecisions:
    """Decisoes geradas pela avaliacao de UMA sessao, com a sessao de origem.

    O replay (`evaluate_history`) precisa devolver o vinculo decisao -> sessao
    para que `progression_decisions.activity_id` seja gravado como no fluxo do
    `finish` (`run_session_evaluation`)."""

    activity_id: int
    decisions: tuple[ProgressionDecision, ...] = ()


@dataclass(frozen=True)
class EvaluationResult:
    group_states: Mapping[int, GroupState] = field(default_factory=dict)
    tracks: Mapping[tuple[int, int], TrackState] = field(default_factory=dict)
    decisions: tuple[ProgressionDecision, ...] = ()
    track_outcomes: Mapping[tuple[int, int], TrackSessionOutcome] = field(default_factory=dict)
    group_outcomes: Mapping[int, str] = field(default_factory=dict)
    # So o replay preenche: as mesmas `decisions`, na mesma ordem, agrupadas
    # pela sessao que as gerou. `decisions` continua sendo a lista plana.
    session_decisions: tuple[SessionDecisions, ...] = ()


# ---------------------------------------------------------------------------
# Trilhas
# ---------------------------------------------------------------------------


def track_for(
    execution: ExerciseExecution, tracks: Mapping[tuple[int, int], TrackState], inputs: SessionInputs
) -> TrackState:
    """Trilha do par executado NO CONTEXTO DE FADIGA da execucao, com os
    metadados de catalogo atualizados. Trilha nova (par novo, ou par
    conhecido num contexto ainda sem historico) nasce em `CALIBRATION`:
    neutra para o grupo (secao 5.10), entao a primeira sessao num contexto
    novo nao conta para regressao (D10), deload nem plato.

    A trilha nova nasce com as series do Modelo/ficha. Depois disso so a
    acao `ADD_SET` (F5) muda `sets_count` (Lote 2/P3)."""
    key = inputs.track_key_for(execution)
    if key is None:  # pragma: no cover - o chamador filtra
        raise ValueError("execucao sem par exercicio+equipamento resolvido")
    existing = tracks.get(key)
    metadata = {
        "sequence": execution.sequence,
        "stimulus_weight": inputs.stimulus_weight.get(key[0], 0.0),
        "is_compound": bool(inputs.is_compound.get(key[0], False)),
        "load_increment_kg": inputs.load_increment_kg.get(key[1]),
    }
    if existing is not None:
        return replace(existing, **metadata)
    return TrackState(
        exercise_id=key[0],
        equipment_id=key[1],
        muscle_group_id=inputs.primary_group.get(key[0]),
        fatigue_context=key[2],
        training_goal=key[3],
        load_status=CALIBRATION,
        # A PRESCRICAO tambem respeita o teto de 4 (secao 5.6): a ficha pode
        # pedir 9 series, a trilha nunca guarda mais de 4 - senao a secao 5.4
        # congela a trilha (`sets_below_prescribed_dose`) para sempre.
        sets_count=clamp_sets_count(execution.prescribed.sets),
        **metadata,
    )


def update_track_execution(
    track: TrackState,
    execution: ExerciseExecution,
    *,
    now: datetime,
    reps_min: Optional[int] = None,
    group: Optional[GroupState] = None,
) -> TrackState:
    """Efeito da execucao na trilha (secao 5.11): calibracao (a 1a sessao
    define L = MINIMO das `working`), **adocao da carga executada**,
    `last_executed_at` e a marca de efetivacao da acao pendente. Series de
    aplicacao de tecnica NUNCA viram carga base - e a regressao do bug G2.

    A adocao (`adopted_working_load`) e o que fecha o ciclo da carga: sem ela a
    trilha so mudava de carga na calibracao, porque nem a efetivacao do
    `INCREASE_LOAD` escrevia `working_load_kg`. O resultado era prescrever
    indefinidamente a carga da primeira sessao, muito abaixo do que a pessoa
    levanta. Reducao continua sendo so da D10 (`DECREASE_LOAD`).

    `sets_count` NAO acompanha a execucao (Lote 2/P3, 2026-10-11): series
    extras numa sessao sao volume legitimo e nao mudam a dose da trilha. Antes
    a execucao subia `sets_count` (ate 4) e uma unica sessao com series a mais
    deixava a trilha exigindo mais do que qualquer ficha pedia - NEUTRAL
    `sets_below_prescribed_dose` para sempre. Agora so a acao `ADD_SET` (F5)
    muda `sets_count`; a trilha nasce com as series do Modelo (`track_for`) e
    a dose exigida da sessao e a da ficha congelada
    (`progression_engine.required_dose_sets`).

    Lote 3/P5 (2026-10-11): a carga executada da sessao e a de TOPO que
    cumpriu o piso e se repetiu em metade das series (Lote 7/D1)
    (`session_working_load(working, reps_min)`), tambem na
    calibracao. `pending_action_applied_at` so e marcado quando a execucao
    efetiva a acao pendente do grupo nesta trilha
    (`execution_applies_pending`: `INCREASE_LOAD` exige carga executada >=
    carga alvo); o aumento pendente DA TRILHA (regra de excesso, P4) sai quando
    a carga alvo e executada ou expira (`track_pending_load_status`).

    Lote 4/P6 (2026-10-11): o aumento por PLATO DA TRILHA usa o mesmo
    mecanismo; executado, grava `plateau_load_applied_at` (a fase do grupo
    avanca pela maioria dos exercicios com essa marca, Lote 7/D2).
    """
    classification = classify_sets(execution.sets)
    # Lote T3 (medidas de serie): sessao com serie de trabalho por tempo/maximo
    # de reps nao calibra, nao adota carga, nao efetiva pendencia nem muda o
    # `load_status` da trilha - so registra que o exercicio foi executado.
    if working_has_non_rep_sets(classification.working):
        return replace(track, last_executed_at=now)
    load = session_working_load(classification.working, reps_min)
    changes: dict[str, Any] = {"last_executed_at": now}
    if execution_applies_pending(track, group, load):
        changes["pending_action_applied_at"] = now
    resolved = track_pending_load_status(track, load, now=now)
    if resolved is not None:
        if resolved == PENDING_LOAD_APPLIED and pending_load_source(track) == PENDING_LOAD_SOURCE_PLATEAU:
            # Lote 4/P6: a marca que a fase do grupo conta
            changes["plateau_load_applied_at"] = now
        changes["pending_load_kg"] = None
        changes["pending_load_since"] = None
        changes["pending_load_source"] = None
    if classification.working and load is None and track.working_load_kg is None:
        # series de trabalho sem nenhuma carga: peso corporal (secao 5.10)
        changes["load_status"] = LOAD_NOT_APPLICABLE
        if track.in_calibration:
            changes["calibrated_at"] = now
    elif track.in_calibration and load is not None:
        changes["load_status"] = ACTIVE
        changes["working_load_kg"] = load
        changes["last_load_change_at"] = now
        # a sessao que calibrou nao entra no historico (`counts_for_history`)
        changes["calibrated_at"] = now
    else:
        adopted = adopted_working_load(track, classification.working, reps_min=reps_min)
        if adopted is not None:
            changes["working_load_kg"] = adopted
            changes["last_load_change_at"] = now
    if classification.technique and not track.technique_code:
        first = classification.technique[0]
        # Fase 5: a tecnica gravada na linha (059) vence a derivada do set_type
        code = (first.technique_code or "").strip() or normalize_set_type(first.set_type)
        if code in ("rest_pause", "drop_set"):
            changes["technique_code"] = code
    return replace(track, **changes)


# ---------------------------------------------------------------------------
# Historico de qualificacao (recalculado da janela)
# ---------------------------------------------------------------------------


def _outcome_for(
    execution: ExerciseExecution,
    track: TrackState,
    group: Optional[GroupState],
    inputs: SessionInputs,
) -> TrackSessionOutcome:
    """Resultado da sessao para a trilha, avaliado contra a ficha CONGELADA
    daquele dia quando ela existe (Lote 2/P7): teto, falha, alvo e dose da
    ficha (`SessionInputs.evaluation_intensity_for`,
    `progression_engine.required_dose_sets`/`prescribed_effort_rpe`)."""
    reps_min, reps_max = group.reps_range if group else reps_for_phase(F1, track.training_goal)
    ceiling, failure_allowed, readiness_status, failure_denied_by = inputs.evaluation_intensity_for(
        execution, track.key, track.muscle_group_id
    )
    return evaluate_track_session(
        execution,
        track,
        reps_min=reps_min,
        reps_max=reps_max,
        ceiling=ceiling,
        failure_allowed=failure_allowed,
        readiness_status=readiness_status,
        failure_denied_by=failure_denied_by,
    )


def _track_sessions(
    window: Sequence[SessionRecord],
    key: tuple,
    track: TrackState,
    group: Optional[GroupState],
    inputs: SessionInputs,
):
    """`(sessao, resultado)` de cada execucao da trilha na janela, em ordem,
    sem a sessao que a calibrou (`counts_for_history`)."""
    for session in window:
        if not counts_for_history(track, session):
            continue
        for execution in session.exercises:
            if inputs.track_key_for(execution) == key:
                yield session, _outcome_for(execution, track, group, inputs)


def track_session_history(
    window: Sequence[SessionRecord],
    key: tuple[int, int],
    group_states: Mapping[int, GroupState],
    tracks: Mapping[tuple[int, int], TrackState],
    inputs: SessionInputs,
) -> list[TrackSessionOutcome]:
    """Resultado de cada sessao da janela para UMA trilha (par + contexto de
    fadiga), em ordem. A sessao que CALIBROU a trilha nao entra (ver
    `counts_for_history`)."""
    track = tracks.get(key)
    if track is None:
        return []
    group = group_states.get(track.muscle_group_id) if track.muscle_group_id is not None else None
    return [outcome for _session, outcome in _track_sessions(window, key, track, group, inputs)]


def track_plateau_history(
    window: Sequence[SessionRecord],
    key: tuple,
    group_states: Mapping[int, GroupState],
    tracks: Mapping[tuple, TrackState],
    inputs: SessionInputs,
) -> list[tuple[date, str]]:
    """Lote 4/P6 (2026-10-11): `[(data, QUALIFY|NEUTRAL|RESET)]` DA TRILHA, a
    entrada de `plateau_confirmed` para o aumento de carga por trilha. So as
    sessoes desde a entrada do grupo na fase (como `group_session_history`) e
    sem a sessao que calibrou a trilha (`counts_for_history`). Sessoes de
    OUTROS exercicios do grupo nao entram: o RESET de um nao zera a sequencia
    do outro.

    O chamador passa a trilha JA com a execucao da sessao (carga adotada):
    sessoes numa carga menor que a atual zeram por `load_below_track`, entao
    depois de um aumento a sequencia recomeca na carga nova."""
    track = tracks.get(key)
    if track is None:
        return []
    group = group_states.get(track.muscle_group_id) if track.muscle_group_id is not None else None
    since = group.phase_entered_at if group else None
    history: list[tuple[date, str]] = []
    for session, outcome in _track_sessions(window, key, track, group, inputs):
        moment = session.occurred_at
        if since is not None and moment is not None and moment < since:
            continue
        history.append((_as_date(moment) or session.local_date, outcome.outcome))
    return history


def group_window_tracks(
    window: Sequence[SessionRecord],
    muscle_group_id: int,
    tracks: Mapping[tuple, TrackState],
    inputs: SessionInputs,
) -> list[TrackState]:
    """Trilhas do grupo executadas na janela (secao 5.6: ativa = executada nas
    ultimas 21 sessoes), uma vez cada, na ordem da primeira execucao. Base da
    maioria da fase de carga (`group_load_phase_progress`, Lote 4/P6)."""
    seen: dict[tuple, TrackState] = {}
    for session in window:
        for execution in session.exercises:
            key = inputs.track_key_for(execution)
            track = tracks.get(key) if key else None
            if track is not None and track.muscle_group_id == muscle_group_id and key not in seen:
                seen[key] = track
    return list(seen.values())


def session_moment(session: SessionRecord) -> datetime:
    """O instante da sessao para o avaliador (o mesmo de `evaluate_session`)."""
    return session.occurred_at or datetime.combine(session.local_date, datetime.min.time())


def counts_for_history(track: TrackState, session: SessionRecord) -> bool:
    """A sessao conta no historico (regressao D10 e plato) desta trilha?

    A sessao que CALIBROU a trilha (`calibrated_at`) e as anteriores nao: na
    calibracao a trilha e neutra (secao 5.10), e reavaliar essa sessao depois
    com a carga ja calibrada faria a primeira exposicao num contexto de
    fadiga novo contar para regressao/deload/plato - o que a decisao de
    2026-10-07 proibe. Trilha sem `calibrated_at` (gravada antes da 068, ate o
    replay) mantem o comportamento anterior."""
    if track.calibrated_at is None:
        return True
    return session_moment(session) > track.calibrated_at


def group_session_history(
    window: Sequence[SessionRecord],
    muscle_group_id: int,
    group_states: Mapping[int, GroupState],
    tracks: Mapping[tuple[int, int], TrackState],
    inputs: SessionInputs,
) -> list[tuple[date, str]]:
    """`[(data, QUALIFY|NEUTRAL|RESET)]` por sessao, nivel GRUPO (secao 5.4),
    so das sessoes desde `phase_entered_at`. Sessao sem execucao do grupo nao
    e avaliada e nao entra na lista."""
    group = group_states.get(muscle_group_id)
    since = group.phase_entered_at if group else None
    history: list[tuple[date, str]] = []
    for session in window:
        moment = session.occurred_at
        if since is not None and moment is not None and moment < since:
            continue
        outcomes = []
        for execution in session.exercises:
            key = inputs.track_key_for(execution)
            track = tracks.get(key) if key else None
            if track is None or track.muscle_group_id != muscle_group_id:
                continue
            if not counts_for_history(track, session):
                continue
            outcomes.append(_outcome_for(execution, track, group, inputs))
        if not outcomes:
            continue
        day = _as_date(moment) or session.local_date
        history.append((day, evaluate_group_session(outcomes)))
    return history


# ---------------------------------------------------------------------------
# Fase destino de cada acao pendente
# ---------------------------------------------------------------------------

# A tabela da secao 5.6 e um degrau por platô: a fase destino de uma acao
# pendente e sempre a fase de entrada daquela acao.
_PENDING_PHASE = {SET_REP_RANGE_10_12: F2, APPLY_TECHNIQUE: F4, ADD_SET: F5}


def phase_of_pending(group: GroupState) -> str:
    action = group.pending_action
    if action == INCREASE_LOAD and not ladder_for(group.training_goal).advances_past_load_phase:
        return group.phase  # M97 F2: a escada de forca tem uma fase so
    if action == INCREASE_LOAD:
        return F3 if group.phase in (F1, F2) else F6
    if action == DECREASE_LOAD:
        return group.phase  # regressao nao muda a fase (D10)
    return _PENDING_PHASE.get(action or "", group.phase)


def reps_for_phase(phase: str, training_goal: Any = None) -> tuple[int, int]:
    """Faixa da fase no objetivo (dono `progression_engine.reps_for_phase`;
    hipertrofia: 6-8 na F1, 10-12 depois)."""
    return _engine_reps_for_phase(phase, training_goal)


def eligible_by_track(
    tracks: Sequence[TrackState], inputs: SessionInputs, muscle_group_id: int
) -> dict[tuple[int, int], tuple[str, ...]]:
    readiness = inputs.readiness_for(muscle_group_id)
    result: dict[tuple[int, int], tuple[str, ...]] = {}
    for track in tracks:
        _, failure_allowed = inputs.ceiling_for(track.key, muscle_group_id)
        result[track.key] = eligible_techniques(
            inputs.safety_for(track.key),
            inputs.catalog,
            readiness=readiness,
            is_advanced_user=inputs.is_advanced_user,
            composed_failure_allowed=failure_allowed,
            track=track,
        )
    return result


# ---------------------------------------------------------------------------
# Avaliador
# ---------------------------------------------------------------------------


def evaluate_session(
    session: SessionRecord,
    window: Sequence[SessionRecord],
    *,
    group_states: Mapping[int, GroupState],
    tracks: Mapping[tuple[int, int], TrackState],
    inputs: SessionInputs,
    now: Optional[datetime] = None,
    training_goal: Any = None,
) -> EvaluationResult:
    """Avalia UMA sessao (a ultima de `window`) e devolve o estado novo.

    Avaliacao de UM objetivo (M97 F1): `session`, `window`, `group_states` e
    `tracks` sao todos do objetivo `training_goal` (ausente = default), e o
    grupo sem estado nasce nele. Quem tem sessoes de mais de um objetivo
    chama `evaluate_session_by_goal`."""
    moment = now or session.occurred_at or datetime.combine(session.local_date, datetime.min.time())
    new_groups = dict(group_states)
    new_tracks = dict(tracks)
    decisions: list[ProgressionDecision] = []

    touched = [
        (execution, track_for(execution, new_tracks, inputs))
        for execution in session.exercises
        if execution.pair is not None
    ]

    # TODO o julgamento desta avaliacao usa o estado da trilha ANTES da
    # execucao: e a carga L, o nº de series S e o `load_status` com que a
    # sessao foi de fato prescrita (secao 5.4). Isso tambem e o que mantem
    # "trilha em calibracao e neutra" valendo na propria sessao que a calibra.
    prior_tracks: dict[tuple[int, int], TrackState] = {**new_tracks}
    prior_tracks.update({track.key: track for _, track in touched})

    # teto de ativacao da janela desta sessao (Fase 5, PR 6): uma leitura por
    # sessao, para todos os grupos
    activation = inputs.activation_for(window, session) if touched else {}

    outcomes: dict[tuple[int, int], TrackSessionOutcome] = {}
    for execution, track in touched:
        group = new_groups.get(track.muscle_group_id) if track.muscle_group_id is not None else None
        outcomes[track.key] = _outcome_for(execution, track, group, inputs)

    # adocao da carga executada, por trilha, para a evidencia da decisao
    adoptions: dict[tuple[int, int], dict[str, Any]] = {}
    # aumento de carga DA TRILHA (excesso, Lote 3/P4; plato da trilha, Lote
    # 4/P6) executado ou expirado nesta sessao: (origem, detalhe)
    track_load_events: dict[tuple, list[tuple[str, dict[str, Any]]]] = {}
    # trilha logo depois da execucao, antes de qualquer armamento (para saber
    # o que foi ARMADO nesta sessao)
    executed_state: dict[tuple, TrackState] = {}
    for execution, track in touched:
        group = new_groups.get(track.muscle_group_id) if track.muscle_group_id is not None else None
        reps_min = group.reps_min if group else reps_for_phase(F1, track.training_goal)[0]
        updated = update_track_execution(track, execution, now=moment, reps_min=reps_min, group=group)
        if updated.working_load_kg != track.working_load_kg and not track.in_calibration:
            adoptions[track.key] = {
                "from_kg": None if track.working_load_kg is None else float(track.working_load_kg),
                "to_kg": None if updated.working_load_kg is None else float(updated.working_load_kg),
                "source": LOAD_ADOPTION_SOURCE,
            }
        working = classify_sets(execution.sets).working
        # Lote T3: mesma porta de `update_track_execution` - sessao com serie
        # fora de reps nao efetiva nem expira a carga pendente da trilha.
        resolved = (
            None
            if working_has_non_rep_sets(working)
            else track_pending_load_status(track, session_working_load(working, reps_min), now=moment)
        )
        if resolved is not None:
            track_load_events.setdefault(track.key, []).append(
                (pending_load_source(track), {"status": resolved, "target_kg": float(track.pending_load_kg)})
            )
        executed_state[track.key] = updated
        new_tracks[track.key] = arm_excess_reps_fast_track(updated, outcomes[track.key], now=moment)

    group_outcomes: dict[int, str] = {}
    # M97 F2: a escada do objetivo avaliado (hipertrofia = a de sempre)
    ladder = ladder_for(training_goal)
    groups_touched = sorted({track.muscle_group_id for _, track in touched if track.muscle_group_id is not None})
    for muscle_group_id in groups_touched:
        group = new_groups.get(muscle_group_id) or new_group_state(muscle_group_id, moment, training_goal)
        # julgamento e escolha do degrau: estado ANTES da execucao
        group_tracks = [prior_tracks[track.key] for _, track in touched if track.muscle_group_id == muscle_group_id]
        # efetivacao: estado DEPOIS, porque e a execucao que efetiva a acao
        executed_tracks = [new_tracks[track.key] for _, track in touched if track.muscle_group_id == muscle_group_id]
        group_keys = [track.key for _, track in touched if track.muscle_group_id == muscle_group_id]
        # trilhas do grupo na janela (depois da execucao): base da maioria da
        # fase de carga (Lote 4/P6)
        window_tracks = group_window_tracks(window, muscle_group_id, new_tracks, inputs)
        session_outcome = evaluate_group_session([outcomes[key] for key in group_keys])
        group_outcomes[muscle_group_id] = session_outcome

        plateau = plateau_confirmed(
            group_session_history(window, muscle_group_id, new_groups, prior_tracks, inputs),
            min_sessions=ladder.plateau_min_sessions,
            min_span_days=ladder.plateau_min_span_days,
        )
        phase_before = group.phase
        evidence: dict[str, Any] = {
            "session_outcome": session_outcome,
            "qualifying_sessions": [day.isoformat() for day in plateau.qualifying_dates],
            "span_days": plateau.span_days,
            "readiness": inputs.readiness_for(muscle_group_id).status,
            # veredito E motivo por trilha (secao 14: "por que bloqueou").
            # Formato antigo (so a string do veredito) segue legivel por
            # `track_evidence_outcome`.
            "tracks": {track_label(key): outcomes[key].to_evidence() for key in group_keys},
            "safety_events": [
                dict(event) for key in group_keys for event in outcomes[key].safety_events
            ],
            **ladder.evidence(),
        }
        if muscle_group_id in activation:
            evidence["activation"] = activation[muscle_group_id].to_evidence()
        adopted = {track_label(key): adoptions[key] for key in group_keys if key in adoptions}
        if adopted:
            # auditoria da secao 14: "por que a carga mudou sem decisao do motor"
            evidence[EVIDENCE_LOAD_ADOPTED_FROM_EXECUTION] = adopted
            # a carga do grupo mudou de fato: o intervalo de 14 dias da F6
            # conta do ultimo aumento REAL, nao do ultimo aumento decidido
            group = replace(group, last_load_change_at=moment)
        step: Optional[PhaseStep] = None

        legacy_increase = group.pending_action == INCREASE_LOAD
        effective = group_pending_effective(
            group, window_tracks if legacy_increase else executed_tracks, now=moment
        )
        if effective:
            target_phase = phase_of_pending(group)
            group = apply_effective(
                group, target_phase, now=moment, reps=reps_for_phase(target_phase, group.training_goal)
            )
            evidence["effective"] = True
        elif legacy_increase:
            # Lote 4/P6: `INCREASE_LOAD` DO GRUPO gravado antes do Lote 4 sai
            # da fila sem promover a fase; o plato de cada trilha rearma o
            # aumento dela abaixo
            evidence[EVIDENCE_GROUP_INCREASE_BY_TRACK] = True
            group = replace(group, pending_action=None, pending_payload=None)

        armed_by_plateau: list[str] = []
        if load_phase_by_track(group, window_tracks):
            # Lote 4/P6: fase de CARGA - o plato e POR TRILHA. Cada trilha
            # executada olha so o historico DELA (com a carga ja adotada) e
            # arma o proprio aumento; o veredito do grupo nao entra.
            groups_now = {**new_groups, muscle_group_id: group}
            plateaus: dict[str, Any] = {}
            for key in group_keys:
                own = plateau_confirmed(
                    track_plateau_history(window, key, groups_now, new_tracks, inputs),
                    min_sessions=ladder.plateau_min_sessions,
                    min_span_days=ladder.plateau_min_span_days,
                )
                plateaus[track_label(key)] = {
                    "confirmed": own.confirmed,
                    "qualifying_sessions": [day.isoformat() for day in own.qualifying_dates],
                    "span_days": own.span_days,
                }
                new_tracks[key] = arm_track_plateau_load(new_tracks[key], group, own, now=moment)
            evidence[EVIDENCE_TRACK_PLATEAUS] = plateaus
            if group.phase == F2 and group.pending_action is None and ladder.advances_past_load_phase:
                # a FASE continua do grupo: F2 -> F3 pela maioria dos
                # exercicios (Lote 7/D2)
                progress = group_load_phase_progress(group, window_tracks)
                evidence[EVIDENCE_GROUP_PHASE_ADVANCE] = progress.to_evidence()
                if progress.complete:
                    group = advance_load_phase(group, now=moment)
                    evidence["effective"] = True
        elif not effective and plateau.confirmed:
            step = next_step(
                group,
                group_tracks,
                now=moment,
                eligible_by_track=eligible_by_track(group_tracks, inputs, muscle_group_id),
                model_fixed_sets=inputs.model_fixed_sets,
                activation_max_reached=inputs.activation_reached(muscle_group_id, activation),
                volume_in_range=inputs.activation_in_range(muscle_group_id, activation),
            )
            payload: dict[str, Any] = {}
            if step.target_track_key:
                payload["track_key"] = track_key_payload(step.target_track_key)
            if step.technique_code:
                payload["technique_code"] = step.technique_code
            if step.recommendation:
                payload["recommendation"] = step.recommendation
            group = replace(
                group,
                pending_action=step.pending_action,
                pending_payload=payload or None,
                phase_status=step.phase_status,
                next_eligible_progression_at=step.next_eligible_progression_at,
            )
            if step.pending_action is None:
                # Degrau sem acao (F5 -> F6, ACTIVATION_MAX, series fixas pelo
                # modelo): nao ha nada a efetivar, a fase avanca na hora.
                group = replace(
                    group,
                    phase=step.phase_after,
                    phase_entered_at=moment,
                    reps_min=step.reps_min,
                    reps_max=step.reps_max,
                )
            evidence["plateau"] = True
            evidence["pending_action"] = step.pending_action
            evidence["phase_target"] = step.phase_after

        # aumentos de carga DA TRILHA desta sessao, por origem (auditoria)
        excess_events: dict[str, Any] = {}
        plateau_events: dict[str, Any] = {}
        for key in group_keys:
            label = track_label(key)
            for source, detail in track_load_events.get(key, ()):
                target = plateau_events if source == PENDING_LOAD_SOURCE_PLATEAU else excess_events
                target[label] = detail
            before, after = executed_state[key], new_tracks[key]
            if after.pending_load_kg is not None and (
                after.pending_load_kg != before.pending_load_kg
                or pending_load_source(after) != pending_load_source(before)
            ):
                source = pending_load_source(after)
                target = plateau_events if source == PENDING_LOAD_SOURCE_PLATEAU else excess_events
                target[label] = {
                    "status": "armed",
                    "from_kg": None if after.working_load_kg is None else float(after.working_load_kg),
                    "target_kg": float(after.pending_load_kg),
                }
                if source == PENDING_LOAD_SOURCE_PLATEAU:
                    armed_by_plateau.append(label)
        if excess_events:
            # Lote 3/P4: ajuste de carga DA TRILHA pela regra de excesso
            evidence[EVIDENCE_EXCESS_REPS_FAST_TRACK] = excess_events
        if plateau_events:
            # Lote 4/P6: aumento de carga pelo plato DA TRILHA
            evidence[EVIDENCE_TRACK_PLATEAU_LOAD] = plateau_events

        plateau_reset_labels: list[str] = []
        if group.pending_action is None and ladder.stall_plateau_reset:
            # M97 F2, forca: platô por estagnacao recua a carga DA TRILHA (no
            # lugar da D10). Avaliado contra a trilha ja com a execucao.
            plateau_resets: dict[str, Any] = {}
            for key in group_keys:
                history = track_session_history(window, key, new_groups, new_tracks, inputs)
                if strength_stall_triggered(history, new_tracks[key]):
                    new_tracks[key], detail = apply_strength_plateau_reset(new_tracks[key], now=moment)
                    if detail is not None:
                        plateau_resets[track_label(key)] = detail
                        plateau_reset_labels.append(track_label(key))
            if plateau_resets:
                evidence[EVIDENCE_STRENGTH_PLATEAU_RESET] = plateau_resets
        elif group.pending_action is None:
            for key in group_keys:
                history = track_session_history(window, key, new_groups, prior_tracks, inputs)
                if regression_triggered(history):
                    group = replace(
                        group,
                        pending_action=DECREASE_LOAD,
                        pending_payload={"track_key": track_key_payload(key), "rule": "D10"},
                    )
                    evidence["regression"] = track_label(key)
                    break

        new_groups[muscle_group_id] = group
        # Lote 4/P6: sem acao do grupo, a decisao registra o aumento DA TRILHA
        # armado pelo plato dela nesta sessao (a ficha de cada trilha sai de
        # `prescribe`, a partir do aumento pendente da trilha)
        decision = group.pending_action or (
            INCREASE_LOAD if armed_by_plateau else (DECREASE_LOAD if plateau_reset_labels else HOLD)
        )
        decisions.append(
            ProgressionDecision(
                group_state_id=group.id,
                muscle_group_id=muscle_group_id,
                track_id=None,
                engine_version=ladder.engine_version,
                phase_before=phase_before,
                phase_after=group.phase,
                decision=decision,
                suggested_reps_min=group.reps_min,
                suggested_reps_max=group.reps_max,
                can_progress=decision != HOLD,
                blocked_by=tuple(step.blocked_by) if step else (),
                evidence=evidence,
                next_eligible_progression_at=group.next_eligible_progression_at,
                reason=evaluation_reason(phase_before, group, plateau, session_outcome, armed_by_plateau),
            )
        )

    return EvaluationResult(
        group_states=new_groups,
        tracks=new_tracks,
        decisions=tuple(decisions),
        track_outcomes=outcomes,
        group_outcomes=group_outcomes,
    )


def evaluate_history(
    sessions: Sequence[SessionRecord],
    *,
    inputs: SessionInputs,
    window_size: int = 21,
) -> EvaluationResult:
    """Replay: avalia a historia inteira, sessao por sessao, com janela
    deslizante de `window_size`. Rodar duas vezes com a mesma entrada da o
    mesmo estado (a sequencia e sempre recalculada da janela).

    `decisions` e a lista plana de todas as sessoes (compatibilidade);
    `session_decisions` traz as mesmas decisoes agrupadas pela sessao de
    origem, para quem grava precisar do `activity_id`."""
    group_states: Mapping[int, GroupState] = {}
    tracks: Mapping[tuple[int, int], TrackState] = {}
    decisions: list[ProgressionDecision] = []
    per_session: list[SessionDecisions] = []
    # M97 D: sessao de deload nao e avaliada e fica na janela sem execucao.
    sessions = [replace(item, exercises=()) if item.is_deload and item.exercises else item for item in sessions]
    for index, session in enumerate(sessions):
        if session.is_deload:
            per_session.append(SessionDecisions(activity_id=session.activity_id, decisions=()))
            continue
        window = list(sessions[max(0, index + 1 - window_size) : index + 1])
        result = evaluate_session(
            session, window, group_states=group_states, tracks=tracks, inputs=inputs
        )
        group_states, tracks = result.group_states, result.tracks
        decisions.extend(result.decisions)
        per_session.append(SessionDecisions(activity_id=session.activity_id, decisions=tuple(result.decisions)))
    return EvaluationResult(
        group_states=group_states,
        tracks=tracks,
        decisions=tuple(decisions),
        session_decisions=tuple(per_session),
    )


# ---------------------------------------------------------------------------
# Separacao por objetivo de treino (M97 F1)
# ---------------------------------------------------------------------------
#
# Progressao, plato, fase e sugestao de carga sao SEPARADOS por objetivo
# (decisao do usuario, 2026-10-25): cada objetivo e avaliado pelo nucleo acima
# (`evaluate_session`), so com as execucoes dele, contra os estados dele. A
# janela e a MESMA (as ultimas sessoes concluidas, de qualquer objetivo), com
# as execucoes do outro objetivo retiradas (`SessionRecord.only_goal`) - assim
# o `finish` e o replay veem a mesma janela. A sessao sem execucao de um
# objetivo nao e avaliada nele.
#
# O que NAO e separado: o contexto de fadiga previa (a sessao REAL, montado
# fora daqui) e o teto de ativacao (`activation_probe` recebe as sessoes
# inteiras - volume/ativacao so passam a ponderar o objetivo na F2).
#
# Com um objetivo so (todo o historico anterior a M97), `only_goal` devolve
# a propria sessao e o resultado e o do nucleo, identico ao de antes.


def _goal_inputs(inputs: SessionInputs, full_by_id: Mapping[int, SessionRecord]) -> SessionInputs:
    """Os mesmos `inputs`, com o teto de ativacao lido nas sessoes INTEIRAS
    (todas as series da janela), nao so nas do objetivo avaliado."""
    probe = inputs.activation_probe
    if probe is None:
        return inputs

    def full_probe(window: Sequence[SessionRecord], session: SessionRecord) -> Mapping[int, Any]:
        return probe(
            [full_by_id.get(item.activity_id, item) for item in window],
            full_by_id.get(session.activity_id, session),
        )

    return replace(inputs, activation_probe=full_probe)


def goal_group_states(
    group_states: Mapping[Any, GroupState], training_goal: Any
) -> dict[int, GroupState]:
    """Os estados de grupo de UM objetivo, por `muscle_group_id` (a forma que
    o nucleo usa). Aceita o mapa por `GroupState.key` ou qualquer outro: o
    objetivo e o do proprio estado."""
    goal = goal_value(training_goal)
    return {
        int(state.muscle_group_id): state
        for state in group_states.values()
        if goal_value(state.training_goal) == goal
    }


def goal_tracks(tracks: Mapping[Any, TrackState], training_goal: Any) -> dict[tuple, TrackState]:
    """As trilhas de UM objetivo, por `TrackState.key`."""
    goal = goal_value(training_goal)
    return {track.key: track for track in tracks.values() if goal_value(track.training_goal) == goal}


@dataclass(frozen=True)
class GoalEvaluation:
    """Resultado de uma avaliacao por objetivo: `results[objetivo]` e o
    `EvaluationResult` do nucleo para aquele objetivo (estados e trilhas SO
    dele). A ordem e a do vocabulario (`training_goal.goals_in`)."""

    results: Mapping[str, EvaluationResult] = field(default_factory=dict)
    # M97 D: motivo da avaliacao PULADA (decisao neutra, nada gravado):
    # `deload.SKIP_REASON_DELOAD` para a sessao de deload; None = avaliada.
    skipped_reason: Optional[str] = None

    @property
    def decisions(self) -> tuple[ProgressionDecision, ...]:
        return tuple(decision for result in self.results.values() for decision in result.decisions)

    @property
    def group_outcomes(self) -> dict[Any, str]:
        """`grupo` (o id, o formato de sempre) no objetivo default e
        `"grupo:objetivo"` nos outros."""
        outcomes: dict[Any, str] = {}
        for goal, result in self.results.items():
            for group_id, outcome in result.group_outcomes.items():
                label = group_id if goal == DEFAULT_GOAL.value else f"{group_id}:{goal}"
                outcomes[label] = outcome
        return outcomes


def evaluate_session_by_goal(
    session: SessionRecord,
    window: Sequence[SessionRecord],
    *,
    group_states: Mapping[Any, GroupState],
    tracks: Mapping[Any, TrackState],
    inputs: SessionInputs,
    now: Optional[datetime] = None,
) -> GoalEvaluation:
    """Avalia UMA sessao (a ultima de `window`), objetivo por objetivo.
    `group_states`/`tracks` podem ter estados de todos os objetivos; cada
    objetivo da sessao so ve os seus.

    M97 D: sessao de deload (`SessionRecord.is_deload`) e PULADA - nenhum
    objetivo e avaliado, nenhum estado muda (`skipped_reason`); e, na janela,
    as sessoes de deload nao tem execucao para a progressao
    (`SessionRecord.progression_view`)."""
    if session.is_deload:
        from .deload import SKIP_REASON_DELOAD

        return GoalEvaluation(results={}, skipped_reason=SKIP_REASON_DELOAD)
    full_by_id = {item.activity_id: item for item in window}
    full_by_id[session.activity_id] = session
    goal_inputs = _goal_inputs(inputs, full_by_id)
    results: dict[str, EvaluationResult] = {}
    for goal in session.training_goals:
        results[goal] = evaluate_session(
            session.progression_view(goal),
            [item.progression_view(goal) for item in window],
            group_states=goal_group_states(group_states, goal),
            tracks=goal_tracks(tracks, goal),
            inputs=goal_inputs,
            now=now,
            training_goal=goal,
        )
    return GoalEvaluation(results=results)


def evaluate_history_by_goal(
    sessions: Sequence[SessionRecord],
    *,
    inputs: SessionInputs,
    window_size: int = 21,
) -> GoalEvaluation:
    """Replay por objetivo: o `evaluate_history` de cada objetivo presente no
    historico, com a janela deslizante das sessoes INTEIRAS (a mesma do
    `finish`) e a sessao sem execucao do objetivo fora da avaliacao dele.
    Com um objetivo so, e exatamente o `evaluate_history` de antes.

    M97 D: sessao de deload fica na janela sem execucao para a progressao
    (`SessionRecord.progression_view`) e nao e avaliada; sem deload no
    historico, nada muda."""
    sessions = list(sessions)
    full_by_id = {item.activity_id: item for item in sessions}
    goal_inputs = _goal_inputs(inputs, full_by_id)
    goals = goals_in(goal for item in sessions if not item.is_deload for goal in item.training_goals)
    results: dict[str, EvaluationResult] = {}
    for goal in goals:
        group_states: Mapping[int, GroupState] = {}
        tracks: Mapping[tuple, TrackState] = {}
        decisions: list[ProgressionDecision] = []
        per_session: list[SessionDecisions] = []
        filtered = [item.progression_view(goal) for item in sessions]
        for index, session in enumerate(filtered):
            if not session.exercises:
                continue
            window = filtered[max(0, index + 1 - window_size) : index + 1]
            result = evaluate_session(
                session,
                window,
                group_states=group_states,
                tracks=tracks,
                inputs=goal_inputs,
                training_goal=goal,
            )
            group_states, tracks = result.group_states, result.tracks
            decisions.extend(result.decisions)
            per_session.append(SessionDecisions(activity_id=session.activity_id, decisions=tuple(result.decisions)))
        results[goal] = EvaluationResult(
            group_states=group_states,
            tracks=tracks,
            decisions=tuple(decisions),
            session_decisions=tuple(per_session),
        )
    return GoalEvaluation(results=results)


def evaluation_reason(
    phase_before: str,
    group: GroupState,
    plateau: Plateau,
    session_outcome: str,
    armed_by_plateau: Sequence[str] = (),
) -> str:
    if group.pending_action and plateau.confirmed:
        return (
            f"Platô confirmado em {len(plateau.qualifying_dates)} sessoes de grupo "
            f"({plateau.span_days} dias): {group.pending_action} pendente."
        )
    if group.pending_action:
        return f"{group.pending_action} pendente."
    if phase_before != group.phase:
        return f"Fase do grupo avanca de {phase_before} para {group.phase}."
    if armed_by_plateau:
        return f"Platô da trilha confirmado ({', '.join(armed_by_plateau)}): aumento de carga da trilha pendente."
    return f"Sessao {session_outcome.lower()}: sem mudanca de fase (HOLD)."

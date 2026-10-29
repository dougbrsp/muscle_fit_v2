"""Cenarios de progressao como DADOS + runner puro (Lote 0 da investigacao RPE 10).

Nucleo portado do harness da investigacao RPE 10 (PR #515; arquivo privado) para
os testes unitarios: encadeia, sessao a sessao, o que a producao faz com um par
exercicio x equipamento, sem banco:

  1. FICHA  : `progression_engine.prescribe` (como
              `generation/deterministic_prescription.prescribe_pairs`), congelada por
              `domain/prescription.build_prescription`, com as series da ficha do
              Modo B (`generation/mode_b.sets_for_recovery`).
  2. SERIES : o que o Modo Treino grava por serie
              (`effective_intensity.from_prescription` + `safety_event_for` +
              `session_intensity_status`).
  3. FINISH : `progression_evaluator.evaluate_session`, contra a ficha
              congelada daquele dia (Lote 2/P7); o contrato de recuperacao da
              AVALIACAO so vale sem ficha congelada.
  4. TELA   : comparability do dashboard (`exposure_service`).

NENHUMA regra e reimplementada aqui (regra "Motores: fonte unica da verdade",
`docs/motores/FONTES_DA_VERDADE.md`): tudo e chamada aos modulos donos. O unico
"espelho" e `_prescription_track`, que monta a trilha como
`deterministic_prescription._track_for` (series do Modelo + metadados do catalogo),
sem decidir nada.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional, Sequence

from app.domain import effective_intensity as ei
from app.domain import prescription as pres
from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev
from app.domain import training_safety as ts
from app.domain.training_history import (
    ExerciseExecution,
    FrozenPrescription,
    PrescribedSpec,
    SessionRecord,
    SetRecord,
    frozen_columns_from_snapshot,
)
from app.generation import deterministic_prescription as dp
from app.generation.mode_b import sets_for_recovery
from app.intelligence.dashboard import exposure_service as es
from app.intelligence.muscle_recovery_service import MuscleRecoveryService
from app.domain.set_measures import DEFAULT_MEASURE, SetMeasure
from app.set_types import is_working_set
from tests.support import progression_fixtures as fx

EX, EQ, GID = 1, 1, 10


# ---------------------------------------------------------------------------
# Cenarios como DADOS
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetSpec:
    reps: Optional[int]
    load: Any  # kg, None, ou "ficha" (= carga prescrita pela ficha desta sessao)
    rpe: Any = None
    set_type: str = "working"
    # Lote T3 (medidas de serie): medida gravada e segundos (tempo/janela).
    measure: str = DEFAULT_MEASURE.value
    duration_seconds: Optional[int] = None


def T(seconds: int, load: Any = None, rpe: Any = None) -> SetSpec:
    """Serie de trabalho POR TEMPO (prancha): sem reps, segundos gravados."""
    return SetSpec(None, load, rpe, measure=SetMeasure.DURATION.value, duration_seconds=seconds)


def M(reps: int, load: Any = None, rpe: Any = None, window: Optional[int] = None) -> SetSpec:
    """Serie de trabalho de MAXIMO DE REPS (com janela opcional)."""
    return SetSpec(reps, load, rpe, measure=SetMeasure.MAX_REPS.value, duration_seconds=window)


def S(reps: Optional[int], load: Any, rpe: Any = None) -> SetSpec:
    return SetSpec(reps, load, rpe)


def W(reps: int, load: Any) -> SetSpec:
    return SetSpec(reps, load, None, "warmup")


@dataclass(frozen=True)
class SessionSpec:
    day: str
    sets: tuple[SetSpec, ...]
    freeze_readiness: str = ts.READY  # contrato projetado ao CONGELAR a ficha
    eval_readiness: str = ts.READY  # contrato na AVALIACAO (producao: READY sempre)
    model_sets: Optional[int] = None  # override do sets_count do Modelo nesta sessao


def sess(day: str, *sets: SetSpec, **kw: Any) -> SessionSpec:
    return SessionSpec(day, tuple(sets), **kw)


@dataclass(frozen=True)
class Scenario:
    code: str
    title: str
    sessions: tuple[SessionSpec, ...]
    is_compound: bool = True
    step: str = "2.5"
    max_safe_rpe: int = 10
    override_max_safe_rpe: Any = None
    model_rpe_target: Optional[str] = "10"  # intencao do Modelo (texto da ficha)
    model_sets: int = 3
    initial_phase: str = pe.F2  # grupo ja em 10-12 (como em producao); F1 = grupo novo


DAYS = ["2026-09-01", "2026-09-08", "2026-09-15", "2026-09-22", "2026-09-29", "2026-10-06"]

U_SESSIONS = (
    sess(DAYS[0], S(10, 35, 8), S(9, 35, 10), S(8, 35, 10)),
    sess(DAYS[1], S(12, 35, 10), S(11, 35, 10), S(10, 35, 10)),
    sess(DAYS[2], S(15, 35, 10), S(13, 35, 10), S(12, 35, 10)),
    sess(DAYS[3], S(17, 35, 10), S(12, 35, 8), S(12, 45, 10)),
)

A_IDEAL = (S(12, 35, 8), S(12, 35, 9), S(12, 35, 10))
A_FICHA_DROP = (S(12, "ficha", 10), S(11, "ficha", 10), S(10, "ficha", 10))

SCENARIOS: dict[str, Scenario] = {
    sc.code: sc
    for sc in (
        # Lote T3 (medidas de serie): prancha 3x60 s em duas sessoes - a trilha
        # nunca avalia (NEUTRAL `measure_not_rep_based`), nao calibra nem adota.
        Scenario(
            "PRANCHA",
            "Prancha por tempo: 3x60 s @8 em duas sessoes",
            (
                sess(DAYS[0], T(60, None, 8), T(60, None, 8), T(55, None, 9)),
                sess(DAYS[1], T(60, None, 8), T(60, None, 9), T(60, None, 9)),
            ),
            is_compound=False,
        ),
        # Lote T3: trilha calibrada em reps; uma sessao de MAXIMO de reps com
        # carga maior nao vira carga da trilha.
        Scenario(
            "RM",
            "Duas sessoes 12x35 e uma de maximo de reps em 30 s com 45 kg",
            (
                sess(DAYS[0], *A_IDEAL),
                sess(DAYS[1], *A_IDEAL),
                sess(DAYS[2], M(15, 45, 10, 30), M(12, 45, 10, 30), M(10, 45, 10, 30)),
            ),
        ),
        Scenario("U", "Cenario do usuario (literal): supino composto, passo 2,5, RPE 10, grupo em F2", U_SESSIONS),
        Scenario(
            "A",
            "'Ideal para o motor': 12x35@8/9/10 em duas sessoes, depois 12/11/10@10 na carga da ficha",
            (sess(DAYS[0], *A_IDEAL), sess(DAYS[1], *A_IDEAL), sess(DAYS[2], *A_FICHA_DROP), sess(DAYS[3], *A_FICHA_DROP)),
        ),
        Scenario(
            "A2",
            "'Ideal' repetido 3 sessoes (confirma o plato), depois duas sessoes na carga da ficha",
            (
                sess(DAYS[0], *A_IDEAL),
                sess(DAYS[1], *A_IDEAL),
                sess(DAYS[2], *A_IDEAL),
                sess(DAYS[3], *A_FICHA_DROP),
                sess(DAYS[4], S(12, "ficha", 9), S(12, "ficha", 10), S(12, "ficha", 10)),
            ),
        ),
        Scenario(
            "B",
            "RPE 10 em tudo, reps caem: 12/10/8 x35@10 por 4 sessoes",
            tuple(sess(DAYS[i], S(12, 35, 10), S(10, 35, 10), S(8, 35, 10)) for i in range(4)),
        ),
        Scenario(
            "E",
            "Series a menos que a trilha: S1 4x12x35@9; S2 e S3 3x12x35@10 (Modelo com 3 series)",
            (
                sess(DAYS[0], S(12, 35, 9), S(12, 35, 9), S(12, 35, 9), S(12, 35, 9)),
                sess(DAYS[1], S(12, 35, 10), S(12, 35, 10), S(12, 35, 10)),
                sess(DAYS[2], S(12, 35, 10), S(12, 35, 10), S(12, 35, 10)),
            ),
        ),
        Scenario(
            "G",
            "Dose reduzida ao congelar a 2a ficha; avaliacao como em producao (READY)",
            (
                sess(DAYS[0], *A_IDEAL),
                sess(DAYS[1], *A_IDEAL, freeze_readiness=ts.REDUCED_DOSE),
                sess(DAYS[2], *A_IDEAL),
                sess(DAYS[3], *A_FICHA_DROP),
            ),
        ),
        Scenario(
            "H",
            "Muito acima da faixa, mesma carga: 16/14/12 x35@10 por 5 sessoes (ignora a carga nova)",
            tuple(sess(DAYS[i], S(16, 35, 10), S(14, 35, 10), S(12, 35, 10)) for i in range(5)),
        ),
        Scenario(
            "H2",
            "Muito acima da faixa, ultima serie abaixo de 12: 17/17/11 x35@10 por 4 sessoes",
            tuple(sess(DAYS[i], S(17, 35, 10), S(17, 35, 10), S(11, 35, 10)) for i in range(4)),
        ),
        Scenario(
            "R",
            "Cenario sintetico baseado em caso real: Abdominal crunch na maquina, isolamento, override max_safe_rpe=10; "
            "11/10 congelada com REDUCED_DOSE e Modelo com 2 series nesse dia",
            (
                sess("2026-08-20", S(13, "47.5", 8), S(14, "47.5", 9), S(10, 50, 10)),
                sess("2026-08-24", S(10, 50, 8), S(10, 50, "8.5"), S(13, 50, 10)),
                sess("2026-08-28", S(12, 50, 8), S(15, 50, 9), S(16, 50, 10)),
                sess("2026-09-01", S(12, "52.5", 8), S(13, "52.5", 8), S(13, "52.5", "8.5"), S(11, "52.5", 9), S(9, "52.5", 10)),
                sess("2026-09-05", S(15, 50, 8), S(17, 50, "8.5"), S(12, "52.5", 10)),
                sess("2026-09-11", S(15, "52.5", 10), S(12, 55, 10)),
                sess("2026-09-19", W(12, "7.5"), S(13, 55, 10), S(11, 55, 10)),
                sess("2026-09-23", S(16, 55, 10), S(14, 55, 10), S(9, 55, 10)),
                sess("2026-10-01", S(16, 55, 10), S(13, 60, 10), S(9, 60, 10), S(6, 55, 10)),
                sess("2026-10-03", S(17, 55, 9), S(17, 55, 10), S(10, "57.5", 10)),
                sess("2026-10-11", S(12, "57.5", 7), S(12, "57.5", 8), freeze_readiness=ts.REDUCED_DOSE, model_sets=2),
            ),
            is_compound=False,
            max_safe_rpe=9,
            override_max_safe_rpe=10,
        ),
    )
}


# ---------------------------------------------------------------------------
# Resultado (so o que os testes leem)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ficha:
    """Ficha congelada ANTES da sessao (ou a seguinte, 7 dias apos a ultima)."""

    decision: str
    load: Optional[Decimal]
    sets_count: int
    reps_target: Optional[str]
    rpe_target: Optional[str]
    effective_rpe_target: Any
    effective_rpe_ceiling: Any
    failure_allowed: bool
    reason: Optional[str]
    reps_min: Optional[int]
    reps_max: Optional[int]
    snapshot: dict


@dataclass(frozen=True)
class SessionResult:
    day: str
    ficha: Ficha
    safety_events: tuple[Optional[str], ...]  # o que o Modo Treino grava por serie
    session_intensity_status: Optional[str]  # o que a tela do Modo Treino mostra
    outcome: str  # QUALIFY / RESET / NEUTRAL da trilha
    reasons: tuple[str, ...]
    eval_safety_events: tuple[str, ...]  # eventos re-derivados na avaliacao
    group_decision: str
    group_reason: Optional[str]
    phase_after: str
    pending_action: Optional[str]
    effective: bool
    track_load: Optional[Decimal]
    track_sets: int
    track_load_status: str
    comparability_status: Optional[str]
    # Lote 3/P4 + Lote 4/P6: aumento de carga pendente DA TRILHA e a origem
    track_pending_load: Optional[Decimal] = None
    track_pending_source: Optional[str] = None


@dataclass(frozen=True)
class ScenarioResult:
    code: str
    sessions: tuple[SessionResult, ...]
    next_ficha: Ficha

    @property
    def fichas(self) -> list[Ficha]:
        """Todas as fichas em ordem: a da 1a sessao ... a seguinte a ultima."""
        return [s.ficha for s in self.sessions] + [self.next_ficha]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _dec(value: Any) -> Optional[Decimal]:
    return None if value is None else Decimal(str(value))


def _contract(status: str) -> pe.ReadinessContract:
    """Contrato da secao 5.8 a partir da tabela do dono (`MuscleRecoveryService`)."""
    readiness = ts.readiness_for_status(status)
    return pe.ReadinessContract(
        muscle_group_id=GID,
        status=status,
        max_rpe=readiness.max_rpe,
        allow_failure=readiness.allow_failure,
        volume_multiplier=MuscleRecoveryService.volume_multiplier_for(status),
    )


def _prescription_track(tracks: dict, sc: Scenario, model_sets: int) -> pe.TrackState:
    """Como `deterministic_prescription._track_for`: trilha do par (ou nova) com
    `sets_count` DO MODELO e metadados do catalogo."""
    existing, _own = pe.resolve_track(tracks, EX, EQ, pe.DEFAULT_FATIGUE_CONTEXT)
    if existing is None:
        existing = pe.TrackState(exercise_id=EX, equipment_id=EQ, muscle_group_id=GID)
    return dataclasses.replace(
        existing,
        muscle_group_id=GID,
        sets_count=model_sets,
        stimulus_weight=1.0,
        is_compound=sc.is_compound,
        load_increment_kg=Decimal(sc.step),
        sequence=1,
    )


def _fmt_kg(value: Any) -> str:
    return f"{Decimal(str(value)).normalize():f}".replace(".", ",")


def _freeze(sc: Scenario, decision: Any, model_sets: int, status: str, safety: Any, now: datetime) -> Ficha:
    """O que `generation/mode_b.py` + o congelamento da ficha gravam."""
    ficha_sets = sets_for_recovery(model_sets, SimpleNamespace(decision=decision), status)
    pair = dp.PairPrescription(decision=decision, safety=safety, technique=decision.advanced_technique)
    load = decision.suggested_load_kg
    row = {
        "sets_count": ficha_sets,
        "reps_target": pair.reps_target,
        "load_target": None if load is None else f"{_fmt_kg(load)} kg",
        "suggested_load_kg": load,
        "rpe_target": pair.rpe_target or sc.model_rpe_target,
        "advanced_technique": decision.advanced_technique,
    }
    frozen = pres.build_prescription(
        row,
        safety=safety,
        source=pres.SOURCE_PLAN_CREATED,
        engine_version=dp.MODE_B_ENGINE_VERSION,
        decision=decision.to_dict(),
        readiness=_contract(status).readiness,
        frozen_at=now,
    )
    return Ficha(
        decision=decision.decision,
        load=_dec(load),
        sets_count=int(frozen["sets_count"]),
        reps_target=frozen["reps_target"],
        rpe_target=frozen["rpe_target"],
        effective_rpe_target=decision.effective_rpe_target,
        effective_rpe_ceiling=decision.effective_rpe_ceiling,
        failure_allowed=bool(decision.failure_allowed),
        reason=decision.reason,
        reps_min=decision.suggested_reps_min,
        reps_max=decision.suggested_reps_max,
        snapshot=frozen,
    )


def _build_sets(ss: SessionSpec, ficha_load: Optional[Decimal]) -> list[SetRecord]:
    return [
        SetRecord(
            set_number=index,
            set_type=spec.set_type,
            reps=spec.reps,
            weight_kg=ficha_load if spec.load == "ficha" else _dec(spec.load),
            rpe=_dec(spec.rpe),
            measure=spec.measure,
            duration_seconds=spec.duration_seconds,
        )
        for index, spec in enumerate(ss.sets, start=1)
    ]


def _exposure_rows(
    activity_id: int, day: date, moment: datetime, sets: Sequence[SetRecord], reps_text: Optional[str]
) -> list[dict]:
    """Linhas no formato de `exposure_service.EXPOSURES_SQL_TEMPLATE` (so `working`)."""
    return [
        {
            "exercise_row_id": activity_id,
            "activity_id": activity_id,
            "local_date": day,
            "started_at": moment - timedelta(hours=1),
            "position_in_session": 1,
            "equipment_id": EQ,
            "equipment_name": "Equipamento unico",
            "load_convention": "total",
            "prescribed_reps_target": reps_text,
            "set_number": item.set_number,
            "reps": item.reps,
            "weight_kg": item.weight_kg,
            "rpe": item.rpe,
            "rest_seconds": None,
            "notes": None,
            "measure": item.measure,
            "duration_seconds": item.duration_seconds,
        }
        for item in sets
        if is_working_set(item.set_type)
    ]


def run_scenario(sc: Scenario) -> ScenarioResult:
    safety = fx.safety(max_safe_rpe=sc.max_safe_rpe, override=sc.override_max_safe_rpe, exercise_id=EX, equipment_id=EQ)
    base_inputs = ev.SessionInputs(
        safety={(EX, EQ): safety},
        readiness={},
        catalog=fx.CATALOG,
        primary_group={EX: GID},
        stimulus_weight={EX: 1.0},
        is_compound={EX: sc.is_compound},
        load_increment_kg={EQ: Decimal(sc.step)},
        model_fixed_sets=True,
    )
    key = pe.track_key(EX, EQ)
    first_day = date.fromisoformat(sc.sessions[0].day)
    initial_group = None
    if sc.initial_phase != pe.F1:
        initial_group = pe.GroupState(
            muscle_group_id=GID,
            phase=sc.initial_phase,
            reps_min=pe.PROGRESSED_REPS[0],
            reps_max=pe.PROGRESSED_REPS[1],
            phase_entered_at=datetime.combine(first_day, datetime.min.time()) - timedelta(days=1),
        )
    group_states: dict = {GID: initial_group} if initial_group else {}
    tracks: dict = {}
    built: list[SessionRecord] = []
    exposure_rows: list[dict] = []
    legacy_rpe = pres.parse_rpe(sc.model_rpe_target)
    results: list[SessionResult] = []

    def ficha_for(now: datetime, status: str, model_sets: int) -> Ficha:
        group = group_states.get(GID) or pe.new_group_state(GID, now)
        decision = pe.prescribe(
            _prescription_track(tracks, sc, model_sets),
            group,
            safety,
            _contract(status),
            now=now,
            catalog=fx.CATALOG,
            model_fixed_sets=True,
            legacy_rpe_target=legacy_rpe,
        )
        return _freeze(sc, decision, model_sets, status, safety, now)

    for index, ss in enumerate(sc.sessions):
        day = date.fromisoformat(ss.day)
        moment = datetime.combine(day, datetime.min.time()) + timedelta(hours=18)
        model_sets = ss.model_sets or sc.model_sets
        ficha = ficha_for(moment - timedelta(hours=1), ss.freeze_readiness, model_sets)

        # 2. execucao + o que o Modo Treino grava por serie
        sets = _build_sets(ss, ficha.load)
        intensity = ei.from_prescription(ficha.snapshot, safety)
        events = tuple(ei.safety_event_for(item.set_type, item.rpe, intensity) for item in sets)
        status_rows = [
            {
                "set_type": item.set_type,
                "rpe": item.rpe,
                "rpe_target_effective": intensity.rpe_target_effective if item.set_type == "working" else None,
                "safety_event": event,
            }
            for item, event in zip(sets, events)
        ]
        session_status = ei.session_intensity_status(status_rows, failure_allowed=intensity.failure_allowed)

        rpe_value = pres.parse_rpe(ficha.rpe_target)
        prescribed = PrescribedSpec(
            sets=ficha.sets_count,
            reps_min=ficha.reps_min,
            reps_max=ficha.reps_max,
            load=ficha.load,
            rpe_target=_dec(rpe_value),
            rpe_targets=() if rpe_value is None else (Decimal(str(rpe_value)),),
            # Lote 2/P7: a ficha congelada chega como `SESSIONS_SQL` a le
            # (colunas `frozen_*` do `prescription_json`), pelo mesmo parser.
            frozen=FrozenPrescription.from_row(frozen_columns_from_snapshot(ficha.snapshot)),
        )
        execution = ExerciseExecution(
            exercise_row_id=100 + index,
            sequence=1,
            exercise_id=EX,
            equipment_id=EQ,
            gym_equipment_id=10,
            workout_plan_exercise_id=500 + index,
            prescription_snapshot_id=500 + index,
            prescribed=prescribed,
            preparatory_sets_count=sum(1 for s in sets if s.set_type in ("warmup", "feeler")),
            sets=tuple(sets),
        )
        record = SessionRecord(
            activity_id=index + 1,
            local_date=day,
            started_at=moment - timedelta(hours=1),
            ended_at=moment,
            exercises=(execution,),
        )

        # 3. finish: avaliacao do motor
        built.append(record)
        inputs = dataclasses.replace(base_inputs, readiness={GID: _contract(ss.eval_readiness)})
        result = ev.evaluate_session(record, built[-21:], group_states=group_states, tracks=tracks, inputs=inputs)
        group_states, tracks = dict(result.group_states), dict(result.tracks)
        outcome = result.track_outcomes[key]
        group_decision = result.decisions[0]
        evidence = dict(group_decision.evidence)
        track_after = tracks[key]
        group_after = group_states[GID]

        # 4. comparability (tela de desempenho)
        exposure_rows.extend(_exposure_rows(index + 1, day, moment, sets, ficha.reps_target))
        exposures = es.assemble_exposures(es.build_exposures(exposure_rows), EX)
        verdict = es.progression_verdict(exposures)

        results.append(
            SessionResult(
                day=ss.day,
                ficha=ficha,
                safety_events=events,
                session_intensity_status=session_status,
                outcome=outcome.outcome,
                reasons=tuple(outcome.reasons),
                eval_safety_events=tuple(outcome.safety_events),
                group_decision=group_decision.decision,
                group_reason=group_decision.reason,
                phase_after=group_after.phase,
                pending_action=group_after.pending_action,
                effective=bool(evidence.get("effective", False)),
                track_load=_dec(track_after.working_load_kg),
                track_sets=int(track_after.sets_count),
                track_load_status=track_after.load_status,
                comparability_status=verdict.get("status"),
                track_pending_load=_dec(track_after.pending_load_kg),
                track_pending_source=pe.pending_load_source(track_after),
            )
        )

    last = datetime.combine(date.fromisoformat(sc.sessions[-1].day), datetime.min.time()) + timedelta(hours=18)
    next_ficha = ficha_for(last + timedelta(days=7), ts.READY, sc.model_sets)
    return ScenarioResult(code=sc.code, sessions=tuple(results), next_ficha=next_ficha)

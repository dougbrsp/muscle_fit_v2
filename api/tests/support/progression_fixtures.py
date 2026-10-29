"""Construtores enxutos para os testes do Motor de Progressao v2 (PR-10).

Tudo aqui monta os DTOs de `domain/training_history.py` e os estados de
`domain/progression_engine.py` sem banco, para os testes da matriz da secao 13
serem sobre funcoes PURAS.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from app.domain.progression_engine import (
    GroupState,
    ReadinessContract,
    TechniqueCatalogEntry,
    TrackState,
)
from app.domain.progression_evaluator import SessionInputs
from app.domain.training_history import (
    ExerciseExecution,
    PrescribedSpec,
    SessionRecord,
    SetRecord,
)
from app.domain.training_safety import build_training_safety

# --- catalogo de tecnicas (as duas auto_applicable da secao 5.9) ------------
CATALOG = {
    "rest_pause": TechniqueCatalogEntry("rest_pause", "requires_failure", True, "intermediate", "rest_pause"),
    "drop_set": TechniqueCatalogEntry("drop_set", "requires_failure", True, "intermediate", "drop_set"),
    "fst7": TechniqueCatalogEntry("fst7", "requires_failure", False, "advanced", "working"),
    "gvt": TechniqueCatalogEntry("gvt", "failure_not_required", False, "intermediate", "working"),
    "forced_reps": TechniqueCatalogEntry("forced_reps", "beyond_failure", False, "advanced", "forced_rep"),
}
ALL_MARKS = {code: 1 for code in CATALOG}
REQUIREMENTS = {code: entry.failure_requirement for code, entry in CATALOG.items()}


def safety(
    *,
    max_safe_rpe: int = 10,
    override: Any = None,
    exercise_id: int = 1,
    equipment_id: int = 1,
    techniques: Optional[dict[str, Any]] = None,
) -> Any:
    """`TrainingSafety` do par. `max_safe_rpe=9` = falha proibida."""
    return build_training_safety(
        exercise_id=exercise_id,
        equipment_id=equipment_id,
        equipment_exercise_id=exercise_id * 100 + equipment_id,
        equipment_max_safe_rpe=max_safe_rpe,
        override_max_safe_rpe=override,
        equipment_techniques=ALL_MARKS if techniques is None else techniques,
        technique_failure_requirements=REQUIREMENTS,
    )


def sets(
    *,
    reps: Sequence[int] | int = 12,
    rpe: Sequence[Any] | Any = 10,
    weight: Any = 40,
    count: int = 3,
    set_type: str = "working",
    muscle_pain: Any = None,
    joint_pain: Any = None,
    start: int = 1,
) -> list[SetRecord]:
    """`count` series iguais, ou uma por item quando `reps`/`rpe` sao listas."""
    reps_list = list(reps) if isinstance(reps, (list, tuple)) else [reps] * count
    rpe_list = list(rpe) if isinstance(rpe, (list, tuple)) else [rpe] * len(reps_list)
    weight_list = list(weight) if isinstance(weight, (list, tuple)) else [weight] * len(reps_list)
    return [
        SetRecord(
            set_number=start + index,
            set_type=set_type,
            reps=None if reps_list[index] is None else int(reps_list[index]),
            weight_kg=None if weight_list[index] is None else Decimal(str(weight_list[index])),
            rpe=None if rpe_list[index] is None else Decimal(str(rpe_list[index])),
            muscle_pain=muscle_pain,
            joint_pain=joint_pain,
        )
        for index in range(len(reps_list))
    ]


def execution(
    *,
    exercise_id: int = 1,
    equipment_id: int = 1,
    sequence: int = 1,
    set_list: Optional[Iterable[SetRecord]] = None,
    prescribed: Optional[PrescribedSpec] = None,
    muscle_pain: Any = None,
    joint_pain: Any = None,
    preparatory_sets_count: int = 0,
    workout_plan_exercise_id: Optional[int] = 500,
) -> ExerciseExecution:
    return ExerciseExecution(
        exercise_row_id=exercise_id * 1000 + sequence,
        sequence=sequence,
        exercise_id=exercise_id,
        equipment_id=equipment_id,
        gym_equipment_id=equipment_id * 10,
        workout_plan_exercise_id=workout_plan_exercise_id,
        prescription_snapshot_id=workout_plan_exercise_id,
        prescribed=prescribed or PrescribedSpec(sets=3, reps_min=10, reps_max=12, rpe_target=10),
        muscle_pain=muscle_pain,
        joint_pain=joint_pain,
        preparatory_sets_count=preparatory_sets_count,
        sets=tuple(set_list if set_list is not None else sets()),
    )


def session(
    *,
    activity_id: int = 1,
    day: date | str = "2026-09-01",
    executions: Optional[Sequence[ExerciseExecution]] = None,
    hour: int = 18,
) -> SessionRecord:
    moment = date.fromisoformat(day) if isinstance(day, str) else day
    ended = datetime.combine(moment, datetime.min.time()) + timedelta(hours=hour)
    return SessionRecord(
        activity_id=activity_id,
        local_date=moment,
        started_at=ended - timedelta(hours=1),
        ended_at=ended,
        workout_plan_id=None,
        exercises=tuple(executions if executions is not None else [execution()]),
    )


def track(
    *,
    exercise_id: int = 1,
    equipment_id: int = 1,
    muscle_group_id: int = 10,
    load: Any = 40,
    sets_count: int = 3,
    load_status: str = "ACTIVE",
    technique_code: Optional[str] = None,
    stimulus_weight: float = 1.0,
    is_compound: bool = False,
    load_increment_kg: Any = None,
    sequence: int = 1,
    track_id: Optional[int] = None,
    last_load_change_at: Optional[datetime] = None,
) -> TrackState:
    return TrackState(
        exercise_id=exercise_id,
        equipment_id=equipment_id,
        muscle_group_id=muscle_group_id,
        id=track_id,
        load_status=load_status,
        working_load_kg=None if load is None else Decimal(str(load)),
        sets_count=sets_count,
        technique_code=technique_code,
        stimulus_weight=stimulus_weight,
        is_compound=is_compound,
        load_increment_kg=None if load_increment_kg is None else Decimal(str(load_increment_kg)),
        sequence=sequence,
        last_load_change_at=last_load_change_at,
    )


def group(
    *,
    muscle_group_id: int = 10,
    phase: str = "F2",
    reps: tuple[int, int] = (10, 12),
    pending_action: Optional[str] = None,
    pending_payload: Any = None,
    phase_entered_at: Any = None,
    last_load_change_at: Any = None,
    group_id: Optional[int] = None,
    next_eligible_progression_at: Any = None,
    technique_applied_at: Any = None,
) -> GroupState:
    return GroupState(
        muscle_group_id=muscle_group_id,
        id=group_id,
        phase=phase,
        reps_min=reps[0],
        reps_max=reps[1],
        pending_action=pending_action,
        pending_payload=pending_payload,
        phase_entered_at=phase_entered_at,
        last_load_change_at=last_load_change_at,
        next_eligible_progression_at=next_eligible_progression_at,
        technique_applied_at=technique_applied_at,
    )


def inputs(
    *,
    tracks: Sequence[TrackState] = (),
    readiness: Optional[dict[int, ReadinessContract]] = None,
    safety_by_pair: Optional[dict[tuple[int, int], Any]] = None,
    catalog: Optional[dict[str, TechniqueCatalogEntry]] = None,
    is_advanced_user: bool = False,
    model_fixed_sets: bool = False,
    activation_max_reached: Optional[dict[int, bool]] = None,
    max_safe_rpe: int = 10,
) -> SessionInputs:
    """`SessionInputs` derivado das trilhas: grupo PRIMARY, pesos e metadados."""
    pairs = {item.pair for item in tracks}
    return SessionInputs(
        safety=safety_by_pair
        or {key: safety(exercise_id=key[0], equipment_id=key[1], max_safe_rpe=max_safe_rpe) for key in pairs},
        readiness=readiness or {},
        catalog=CATALOG if catalog is None else catalog,
        primary_group={item.exercise_id: item.muscle_group_id for item in tracks if item.muscle_group_id},
        stimulus_weight={item.exercise_id: item.stimulus_weight for item in tracks},
        is_compound={item.exercise_id: item.is_compound for item in tracks},
        load_increment_kg={item.equipment_id: item.load_increment_kg for item in tracks if item.load_increment_kg},
        is_advanced_user=is_advanced_user,
        model_fixed_sets=model_fixed_sets,
        activation_max_reached=activation_max_reached or {},
    )


def state_maps(tracks: Sequence[TrackState], groups: Sequence[GroupState] = ()):
    return ({item.muscle_group_id: item for item in groups}, {item.key: item for item in tracks})

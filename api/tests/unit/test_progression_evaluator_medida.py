"""Progressão neutra para séries fora de `reps` (Lote T3 das medidas de série).

- `evaluate_track_session`: alguma série de trabalho por tempo/máximo de reps
  -> NEUTRAL `measure_not_rep_based`, ANTES de qualquer checagem de RESET e da
  calibração;
- `update_track_execution`: nesse caso só registra `last_executed_at` (sem
  calibrar, sem adotar carga, sem efetivar pendência);
- `SetRecord.from_row` lê a medida (ausente = reps).

Rodar: cd api && python -m pytest tests/unit/test_progression_evaluator_medida.py
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev
from app.domain.set_measures import REASON_MEASURE_NOT_REP_BASED, SetMeasure
from app.domain.training_history import SESSIONS_SQL, ExerciseExecution, SetRecord

NOW = datetime(2026, 10, 20, 18)
TRILHA = pe.TrackState(exercise_id=1, equipment_id=1, load_status=pe.ACTIVE, working_load_kg=Decimal(35), sets_count=3)


def _execucao(*sets: SetRecord) -> ExerciseExecution:
    return ExerciseExecution(exercise_row_id=1, sequence=1, exercise_id=1, equipment_id=1, sets=tuple(sets))


def _reps(n, reps, kg, rpe=9):
    return SetRecord(set_number=n, set_type="working", reps=reps, weight_kg=Decimal(kg), rpe=Decimal(rpe))


def _maximo(n, reps, kg, rpe=10):
    return replace(_reps(n, reps, kg, rpe), measure=SetMeasure.MAX_REPS.value, duration_seconds=30)


def _tempo(n, seconds, rpe=8):
    return SetRecord(
        set_number=n, set_type="working", rpe=Decimal(rpe), measure=SetMeasure.DURATION.value, duration_seconds=seconds
    )


def _avaliar(execution, track=TRILHA):
    return pe.evaluate_track_session(execution, track, reps_min=10, reps_max=12, ceiling=10, failure_allowed=True)


def test_reexporta_o_motivo_do_dono():
    assert pe.REASON_MEASURE_NOT_REP_BASED == REASON_MEASURE_NOT_REP_BASED


def test_serie_nao_reps_fica_neutra_antes_do_reset():
    # Carga abaixo da trilha daria RESET `load_below_track` numa sessão de reps.
    abaixo = _execucao(_reps(1, 12, 20), _reps(2, 12, 20), _reps(3, 12, 20))
    assert _avaliar(abaixo).outcome == pe.RESET
    misturada = _execucao(_reps(1, 12, 20), _reps(2, 12, 20), _maximo(3, 15, 20))
    resultado = _avaliar(misturada)
    assert (resultado.outcome, resultado.reasons) == (pe.NEUTRAL, (REASON_MEASURE_NOT_REP_BASED,))


def test_prancha_neutra_mesmo_em_calibracao():
    calibrando = pe.TrackState(exercise_id=1, equipment_id=1)
    resultado = _avaliar(_execucao(_tempo(1, 60), _tempo(2, 60), _tempo(3, 55)), calibrando)
    assert (resultado.outcome, resultado.reasons) == (pe.NEUTRAL, (REASON_MEASURE_NOT_REP_BASED,))


def test_sessao_so_de_reps_segue_igual():
    resultado = _avaliar(_execucao(_reps(1, 12, 35), _reps(2, 12, 35), _reps(3, 12, 35, 10)))
    assert REASON_MEASURE_NOT_REP_BASED not in resultado.reasons


def test_trilha_nao_adota_nem_calibra_com_serie_nao_reps():
    maximo = _execucao(_maximo(1, 15, 45), _maximo(2, 12, 45), _maximo(3, 10, 45))
    depois = ev.update_track_execution(TRILHA, maximo, now=NOW, reps_min=10)
    assert depois == replace(TRILHA, last_executed_at=NOW)
    calibrando = pe.TrackState(exercise_id=1, equipment_id=1)
    prancha = _execucao(_tempo(1, 60), _tempo(2, 60))
    depois = ev.update_track_execution(calibrando, prancha, now=NOW, reps_min=10)
    assert depois.load_status == pe.CALIBRATION and depois.working_load_kg is None and depois.calibrated_at is None


def test_trilha_adota_com_reps_como_antes():
    reps = _execucao(_reps(1, 12, 40), _reps(2, 12, 40), _reps(3, 11, 40))
    assert ev.update_track_execution(TRILHA, reps, now=NOW, reps_min=10).working_load_kg == Decimal(40)


def test_set_record_le_a_medida_e_sql_traz_as_colunas():
    linha = {"set_number": 1, "set_type": "working", "measure": "duration", "duration_seconds": 60}
    registro = SetRecord.from_row(linha)
    assert (registro.measure, registro.duration_seconds, registro.reps) == ("duration", 60, None)
    assert SetRecord.from_row({"set_number": 1, "set_type": "working", "reps": 10}).measure == "reps"
    assert "es.measure" in SESSIONS_SQL and "es.duration_seconds" in SESSIONS_SQL

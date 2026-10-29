"""Medida da série na prescrição congelada (Lote T2).

- `build_prescription` grava `measure` (pelo dono; ausente = reps),
  `duration_seconds_target` e `time_window_seconds` — chaves ADITIVAS, o
  `schema_version` não muda e o resto do snapshot é o mesmo de antes.
- `training_history.PrescribedSpec`/`FrozenPrescription` leem a medida
  (ausente = reps) e só a serializam fora de `reps` (a evidência gravada em
  `progression_decisions` não muda para série de repetições)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.domain import prescription as pd
from app.domain import training_history as th
from app.domain.set_measures import SetMeasure

QUANDO = datetime(2026, 10, 20, 12, 0)


def _exercise(**kwargs):
    base = {
        "sets_count": 3,
        "reps_target": "10-12",
        "load_target": "40",
        "rpe_target": "9",
        "advanced_technique": None,
        "suggested_reps": 10,
    }
    base.update(kwargs)
    return base


def test_snapshot_de_reps_ganha_so_as_chaves_aditivas():
    antes = pd.build_prescription(_exercise(), safety=None, frozen_at=QUANDO)
    assert antes["measure"] == SetMeasure.REPS.value
    assert antes["duration_seconds_target"] is None and antes["time_window_seconds"] is None
    assert antes["schema_version"] == pd.SCHEMA_VERSION == 1
    com_medida = pd.build_prescription(_exercise(measure=SetMeasure.REPS.value), safety=None, frozen_at=QUANDO)
    assert com_medida == antes


def test_snapshot_por_tempo_congela_a_medida_e_o_alvo():
    snapshot = pd.build_prescription(
        _exercise(measure=SetMeasure.DURATION.value, duration_seconds_target="60", reps_target=None, suggested_reps=None),
        safety=None,
        frozen_at=QUANDO,
    )
    assert snapshot["measure"] == SetMeasure.DURATION.value
    assert snapshot["duration_seconds_target"] == 60
    assert snapshot["reps_target"] is None and snapshot["suggested_reps"] is None


def test_snapshot_de_maximo_congela_a_janela_e_le_medida_com_caixa():
    snapshot = pd.build_prescription(
        _exercise(measure=" MAX_REPS ", time_window_seconds=30, reps_target=None), safety=None, frozen_at=QUANDO
    )
    assert snapshot["measure"] == SetMeasure.MAX_REPS.value
    assert snapshot["time_window_seconds"] == 30


def _row(**overrides):
    base = {
        "activity_id": 1,
        "local_date": date(2026, 9, 1),
        "started_at": datetime(2026, 9, 1, 17, 0),
        "ended_at": datetime(2026, 9, 1, 18, 0),
        "workout_plan_id": 77,
        "exercise_row_id": 10,
        "sequence": 1,
        "exercise_id": 1001,
        "equipment_id": 500,
        "gym_equipment_id": 900,
        "workout_plan_exercise_id": 300,
        "prescribed_sets_count": 3,
        "prescribed_reps_target": "10-12",
        "prescribed_rpe_target": "9",
        "set_row_id": 1,
        "set_number": 1,
        "set_type": "working",
        "reps": 12,
        "weight_kg": Decimal("40.00"),
        "rpe": Decimal("9.00"),
    }
    return {**base, **overrides}


def _prescribed(row):
    return th.build_sessions([row])[0].exercises[0].prescribed


def test_linha_sem_medida_e_reps_e_serializa_como_antes():
    prescribed = _prescribed(_row())
    assert prescribed.measure is SetMeasure.REPS
    assert "measure" not in prescribed.to_dict()


def test_medida_vem_da_coluna_ou_do_snapshot_congelado():
    pela_coluna = _prescribed(_row(prescribed_measure=SetMeasure.DURATION.value))
    assert pela_coluna.measure is SetMeasure.DURATION
    assert pela_coluna.to_dict()["measure"] == SetMeasure.DURATION.value
    congelado = {
        "schema_version": 1,
        "sets_count": 3,
        "effective_rpe_ceiling": 9,
        "measure": SetMeasure.MAX_REPS.value,
    }
    pelo_snapshot = _prescribed(_row(**th.frozen_columns_from_snapshot(congelado)))
    assert pelo_snapshot.measure is SetMeasure.MAX_REPS
    assert pelo_snapshot.frozen.measure is SetMeasure.MAX_REPS
    assert pelo_snapshot.frozen.to_dict()["measure"] == SetMeasure.MAX_REPS.value


def test_snapshot_antigo_sem_medida_e_reps_e_frozen_serializa_como_antes():
    antigo = {"schema_version": 1, "sets_count": 3, "effective_rpe_ceiling": 9}
    frozen = th.FrozenPrescription.from_row(th.frozen_columns_from_snapshot(antigo))
    assert frozen.measure is SetMeasure.REPS
    assert "measure" not in frozen.to_dict()
    nulo = _prescribed(_row(frozen_measure="null", frozen_sets_count="3"))
    assert nulo.measure is SetMeasure.REPS


def test_sql_da_sessao_le_a_medida_do_snapshot():
    assert "'$.measure'" in th.SESSIONS_SQL and "AS frozen_measure" in th.SESSIONS_SQL

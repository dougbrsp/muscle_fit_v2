"""Dono único do e1RM (M76, Lote 3a): `app/domain/e1rm.py`."""

from __future__ import annotations

import pytest

from app.domain import e1rm
from app.domain.e1rm import MissingRpePolicy
from app.intelligence.dashboard.metrics_registry import THRESHOLDS
from app.intelligence.muscle_recovery_service import MuscleRecoveryService


def test_epley_com_rir():
    # 100 kg x 10 reps @ RPE 8 -> RIR 2 -> 100 x (1 + 12/30) = 140
    assert e1rm.estimated_1rm(100.0, 10, 8.0) == pytest.approx(140.0)
    assert e1rm.rir_from_rpe(8.0) == 2.0
    assert e1rm.epley_multiplier(10, 2) == pytest.approx(1.4)


def test_rpe_10_e_acima_e_rir_zero():
    assert e1rm.rir_from_rpe(10.0) == 0.0
    assert e1rm.rir_from_rpe(10.5) == 0.0
    assert e1rm.estimated_1rm(120.0, 10, 10.0) == pytest.approx(160.0)


@pytest.mark.parametrize("rpe", [None, ""])
def test_sem_rpe_nas_duas_politicas(rpe):
    assert e1rm.estimated_1rm(120.0, 10, rpe) is None
    assert e1rm.estimated_1rm(120.0, 10, rpe, missing_rpe=MissingRpePolicy.SKIP) is None
    assert e1rm.estimated_1rm(120.0, 10, rpe, missing_rpe=MissingRpePolicy.RIR_ZERO) == pytest.approx(160.0)
    assert e1rm.resolve_rir(rpe) is None
    assert e1rm.resolve_rir(rpe, MissingRpePolicy.RIR_ZERO) == 0.0


def test_skip_e_o_padrao():
    assert e1rm.estimated_1rm.__kwdefaults__["missing_rpe"] is MissingRpePolicy.SKIP
    assert e1rm.reliable_e1rm.__kwdefaults__["missing_rpe"] is MissingRpePolicy.SKIP


@pytest.mark.parametrize(
    "load, reps",
    [(None, 10), (100.0, None), (0, 10), (-5, 10), (100.0, 0), (100.0, -1), ("abc", 10)],
)
def test_sem_carga_ou_reps_positivas_nao_ha_e1rm(load, reps):
    assert e1rm.estimated_1rm(load, reps, 8.0) is None
    assert e1rm.estimated_1rm(load, reps, None, missing_rpe=MissingRpePolicy.RIR_ZERO) is None


def test_teto_12_vs_13_reps():
    assert e1rm.E1RM_MAX_RELIABLE_REPS == 12
    assert e1rm.is_reliable_reps(12) and not e1rm.is_reliable_reps(13)
    assert e1rm.reliable_e1rm(100.0, 12, 10.0) == pytest.approx(140.0)
    assert e1rm.reliable_e1rm(100.0, 13, 10.0) is None
    # sem teto, o número existe (é o que a regra de progressão trava por e1rm_reps)
    assert e1rm.estimated_1rm(100.0, 13, 10.0) == pytest.approx(143.333, abs=1e-3)
    assert e1rm.reliable_e1rm(100.0, 12, None, missing_rpe=MissingRpePolicy.RIR_ZERO) == pytest.approx(140.0)
    assert e1rm.reliable_e1rm(100.0, 13, None, missing_rpe=MissingRpePolicy.RIR_ZERO) is None


def test_limiar_tem_um_lugar_so():
    assert THRESHOLDS["e1rm_max_reliable_reps"] == e1rm.E1RM_MAX_RELIABLE_REPS


def test_recuperacao_so_delega_ao_dono():
    assert MuscleRecoveryService.estimated_1rm(100.0, 10, 8.0) == e1rm.estimated_1rm(100.0, 10, 8.0)
    assert MuscleRecoveryService.estimate_rir(7.5) == e1rm.rir_from_rpe(7.5)
    # contrato antigo: 0.0 (não None) quando não há e1RM
    assert MuscleRecoveryService.estimated_1rm(0, 10, 8.0) == 0.0
    assert MuscleRecoveryService.estimated_1rm(100.0, None, 8.0) == 0.0


def test_dose_inputs_defaults_sem_dado():
    assert e1rm.dose_inputs(None, None) == (e1rm.DOSE_REPS_WHEN_MISSING, e1rm.DOSE_RPE_WHEN_MISSING) == (8, 7.0)
    assert e1rm.dose_inputs(12, 9.5) == (12, 9.5)
    assert e1rm.dose_inputs(0, 0) == (0, 0.0)

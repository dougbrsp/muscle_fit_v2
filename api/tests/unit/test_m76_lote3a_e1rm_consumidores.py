"""M76 Lote 3a: consumidores do e1RM leem o dono (`domain/e1rm.py`).

- `VolumeService._complete_history_item` (base do `load_spike_factor`): só
  séries de trabalho e só convenções com e1RM;
- `services.compute_exercise_stats`: aplicação de técnica fora de
  `best_weight_kg`/`avg_rpe`;
- `exercise_performance`: emparelhamento de `comparability`, Epley do dono.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain import e1rm
from app.intelligence import exercise_performance as ep
from app.intelligence.dashboard import comparability
from app.intelligence.dashboard.comparability import ComparabilityKey, ExposureSummary, WorkingSet
from app.intelligence.volume_service import VolumeService
from app.load_conventions import LoadConvention
from app.services import compute_exercise_stats


def _item(rows, convention=None):
    return {"set_rows": rows, "load_convention": convention}


def _row(n, set_type, reps, weight, rpe):
    return {"set_number": n, "set_type": set_type, "reps": reps, "weight_kg": weight, "rpe": rpe}


# ---------------------------------------------------------------------------
# load_spike_factor: melhor e1RM da sessão
# ---------------------------------------------------------------------------


def test_pico_de_carga_so_olha_series_de_trabalho():
    rows = [
        _row(1, "warmup", 10, 60.0, 5.0),
        _row(2, "working", 10, 100.0, 8.0),  # 140
        _row(3, "drop_set", 20, 90.0, 10.0),  # 150: técnica, fora
        _row(4, "backoff", 15, 95.0, 9.0),  # 152: backoff, fora
        _row(5, "rest_pause", 5, 100.0, 10.0),
    ]
    item = VolumeService._complete_history_item(_item(rows, "total"))
    assert item["best_e1rm"] == pytest.approx(e1rm.estimated_1rm(100.0, 10, 8.0))


@pytest.mark.parametrize("convention", ["assisted", "bodyweight"])
def test_pico_de_carga_sem_e1rm_em_assistido_e_peso_corporal(convention):
    rows = [_row(1, "working", 8, 40.0, 9.0)]
    assert VolumeService._complete_history_item(_item(rows, convention))["best_e1rm"] is None


def test_pico_de_carga_sem_convencao_e_total_e_sem_rpe_e_sem_e1rm():
    rows = [_row(1, None, 8, 100.0, 9.0), _row(2, "working", 8, 120.0, None)]
    item = VolumeService._complete_history_item(_item(rows))
    assert item["best_e1rm"] == pytest.approx(e1rm.estimated_1rm(100.0, 8, 9.0))


# ---------------------------------------------------------------------------
# compute_exercise_stats
# ---------------------------------------------------------------------------


def test_stats_excluem_aplicacao_de_tecnica_da_melhor_carga_e_do_rpe_medio():
    sets = [
        {"set_type": "warmup", "reps": 10, "weight_kg": 40, "rpe": 5},
        {"set_type": "working", "reps": 10, "weight_kg": 100, "rpe": 8},
        {"set_type": None, "reps": 8, "weight_kg": 100, "rpe": 9},
        {"set_type": "rest_pause", "reps": 4, "weight_kg": 105, "rpe": 10},
        {"set_type": "drop_set", "reps": 8, "weight_kg": 80, "rpe": 10},
    ]
    stats = compute_exercise_stats(sets)
    assert stats["best_weight_kg"] == Decimal("100")
    assert stats["avg_rpe"] == Decimal("8.5")
    # contagens, reps, volume e menor carga seguem com todas as não preparatórias
    assert stats["sets_count"] == 4
    assert stats["reps_total"] == 30
    assert stats["min_weight_kg"] == Decimal("80")
    assert stats["volume_kg"] == Decimal(10 * 100 + 8 * 100 + 4 * 105 + 8 * 80)


def test_stats_so_com_tecnica_nao_tem_melhor_carga():
    stats = compute_exercise_stats([{"set_type": "drop_set", "reps": 8, "weight_kg": 80, "rpe": 10}])
    assert stats["best_weight_kg"] is None and stats["avg_rpe"] is None
    assert stats["min_weight_kg"] == Decimal("80")


# ---------------------------------------------------------------------------
# exercise_performance (C12)
# ---------------------------------------------------------------------------

_KEY = ComparabilityKey(canonical_exercise_id=1, equipment_id=5, load_convention=LoadConvention.TOTAL)


def _summary(reps, weight=100.0, rpe=None):
    return ExposureSummary(
        key=_KEY,
        total_reps=sum(reps),
        best_load_kg=weight,
        avg_rpe=rpe,
        e1rm=None,
        e1rm_reps=None,
        sets=tuple(WorkingSet(reps=r, weight_kg=weight, rpe=rpe) for r in reps),
    )


def test_exercise_performance_nao_copia_comparability():
    for nome in ("_same_load", "_top_load_reps", "_paired_top_reps", "_rir"):
        assert not hasattr(ep, nome), nome
    assert ep.paired_top_reps is comparability.paired_top_reps


def test_magnitude_por_reps_usa_o_epley_do_dono():
    previous, current = _summary([15, 15]), _summary([13, 13, 13])
    esperado = e1rm.epley_multiplier(13, 0) / e1rm.epley_multiplier(15, 0)
    assert ep.paired_reps_epley_ratio(previous, current) == pytest.approx(esperado)
    com_rpe = ep.paired_reps_epley_ratio(_summary([12], rpe=8.0), _summary([10], rpe=9.0))
    assert com_rpe == pytest.approx(e1rm.epley_multiplier(10, 1) / e1rm.epley_multiplier(12, 2))

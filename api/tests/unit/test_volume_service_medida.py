"""Volume/recuperação com a medida da série (Lote T3 das medidas de série).

- a dose conta a série em qualquer medida; as reps da dose saem de
  `set_measures.dose_reps_for_measure` (série por tempo -> fallback
  `DOSE_REPS_WHEN_MISSING`, a mesma assunção de hoje para série sem reps);
- queda de reps e e1RM da sessão só olham séries de repetições;
- as leituras (série a série e empacotada) trazem a medida.

Rodar: cd api && python -m pytest tests/unit/test_volume_service_medida.py
"""

from __future__ import annotations

from app.intelligence import recovery_read_model as rrm
from app.intelligence.volume_service import VolumeService


def _serie(n, reps, kg, rpe, **extra):
    return {"set_number": n, "set_type": "working", "reps": reps, "weight_kg": kg, "rpe": rpe, **extra}


def test_queda_de_reps_ignora_maximo_de_reps():
    so_reps = [_serie(1, 12, 40, 8), _serie(2, 7, 40, 8)]
    assert VolumeService._rep_drop_from_sets(so_reps) is not None
    # A última "série" é um máximo de reps: a queda entre séries de trabalho some.
    com_maximo = [_serie(1, 12, 40, 8), _serie(2, 12, 40, 8), _serie(3, 6, 40, 8, measure="max_reps")]
    assert VolumeService._rep_drop_from_sets(com_maximo) is None


def test_e1rm_da_sessao_so_de_series_de_reps():
    item = {"set_rows": [_serie(1, 5, 100, 10, measure="max_reps"), _serie(2, 10, 40, 8)], "load_convention": None}
    so_reps = {"set_rows": [_serie(2, 10, 40, 8)], "load_convention": None}
    assert VolumeService._complete_history_item(item)["best_e1rm"] == VolumeService._complete_history_item(so_reps)["best_e1rm"]


def test_dose_conta_a_prancha_como_serie_sem_reps():
    prancha = {"set_rows": [_serie(1, None, None, 8, measure="duration", duration_seconds=60)]}
    sem_reps = {"set_rows": [_serie(1, None, None, 8)]}
    dose_prancha = VolumeService._session_dose_for_item(prancha, 1.0, 1.0, 1.0)
    assert dose_prancha > 0
    # Mesma dose da série de reps sem reps registradas (DOSE_REPS_WHEN_MISSING).
    assert dose_prancha == VolumeService._session_dose_for_item(sem_reps, 1.0, 1.0, 1.0)


def test_dose_do_maximo_usa_as_reps_gravadas():
    maximo = {"set_rows": [_serie(1, 20, 40, 9, measure="max_reps")]}
    reps = {"set_rows": [_serie(1, 20, 40, 9)]}
    assert VolumeService._session_dose_for_item(maximo, 1.0, 1.0, 1.0) == VolumeService._session_dose_for_item(
        reps, 1.0, 1.0, 1.0
    )


def test_empacotado_traz_a_medida():
    assert "es.measure" in rrm.HISTORY_PACKED_SQL and "es.measure" in rrm.WINDOW_SETS_SQL
    assert rrm._parse_set("1::8:0:0::duration")["measure"] == "duration"
    assert rrm._parse_set("1:10:8:40:0::")["measure"] == "reps"

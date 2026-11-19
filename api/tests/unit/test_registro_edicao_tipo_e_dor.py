"""#219: edição pós-treino do Registro de exercícios. Tipo e medida da série só
mudam em série comum (dono: `set_types`); a dor por série consolida em
`exercises` por UMA função (`workout_sessions.rollup_exercise_pain`)."""

from __future__ import annotations

import pytest

from app import set_types as st
from app.exercise_set_update_merge import (
    SET_MEASURE_LOCKED,
    SET_TYPE_LOCKED,
    SetEditLockedError,
    merge_sets_for_update,
)
from app.workout_sessions import rollup_exercise_pain

STORED = [
    {"set_number": 1, "set_type": "working", "measure": "reps", "duration_seconds": None},
    {"set_number": 2, "set_type": "warmup", "measure": "reps", "duration_seconds": None},
    {"set_number": 3, "set_type": "drop_set", "measure": "reps", "duration_seconds": None},
    {"set_number": 4, "set_type": "laboral", "measure": "time", "duration_seconds": 30},
]


def _s(n, **extra):
    return {"set_number": n, "reps": 8, "weight_kg": 40, "rpe": 8, **extra}


def _merge(sent, stored=STORED):
    return merge_sets_for_update(sent, stored, lock_types=True)


def test_dono_define_trocaveis_e_regra():
    assert set(st.SWAPPABLE_SET_TYPES) == {"working", "backoff", "warmup", "feeler"}
    assert st.can_change_set_type("working", "backoff") and st.can_change_set_type(None, "warm-up")
    assert not st.can_change_set_type("working", "drop_set")
    assert not st.can_change_set_type("drop_set", "working")
    assert not st.can_change_set_type("laboral", "warmup") and not st.can_change_set_type("warmup", "laboral")
    assert st.can_change_set_type("dropset", "drop_set") and st.can_change_set_type("laboral", "laboral")
    assert st.can_add_set_type("feeler") and not st.can_add_set_type("rest_pause") and not st.can_add_set_type("laboral")


def test_troca_entre_comuns_permitida():
    merged = _merge([_s(1, set_type="warmup"), _s(2, set_type="BACKOFF")])
    assert [m["set_type"] for m in merged] == ["warmup", "backoff"]


@pytest.mark.parametrize("numero,tipo", [(1, "drop_set"), (2, "laboral"), (3, "working"), (4, "working"), (3, "warmup")])
def test_troca_com_tecnica_ou_laboral_e_recusada(numero, tipo):
    with pytest.raises(SetEditLockedError) as exc:
        _merge([_s(numero, set_type=tipo)])
    assert exc.value.code == SET_TYPE_LOCKED and exc.value.set_number == numero


def test_round_trip_do_mesmo_tipo_inclusive_alias_e_omitido():
    merged = _merge([_s(1), _s(2, set_type="warm_up"), _s(3, set_type="dropset"), _s(4, set_type="laboral", measure="time", duration_seconds=30)])
    assert [m["set_type"] for m in merged] == ["working", "warmup", "drop_set", "laboral"]


def test_serie_nova_so_com_tipo_comum():
    assert _merge([_s(5, set_type="backoff")], [])[0]["set_type"] == "backoff"
    assert _merge([_s(5)], [])[0]["set_type"] == "working"
    for tipo in ("rest_pause", "drop_set", "forced_rep", "laboral"):
        with pytest.raises(SetEditLockedError) as exc:
            _merge([_s(1, set_type=tipo)], [])
        assert exc.value.code == SET_TYPE_LOCKED


def test_medida_travada_em_tecnica_e_laboral():
    with pytest.raises(SetEditLockedError) as exc:
        _merge([_s(3, measure="time", duration_seconds=20)])
    assert exc.value.code == SET_MEASURE_LOCKED and exc.value.set_number == 3
    with pytest.raises(SetEditLockedError) as exc:
        _merge([_s(4, duration_seconds=45)])
    assert exc.value.code == SET_MEASURE_LOCKED
    # medida igual a gravada (ou omitida) passa
    assert _merge([_s(3, measure="reps"), _s(4)])[1]["duration_seconds"] == 30


def test_medida_livre_em_serie_comum():
    merged = _merge([_s(1, measure="time", duration_seconds=40)])
    assert (merged[0]["measure"], merged[0]["duration_seconds"]) == ("time", 40)


def test_sem_lock_types_o_comportamento_antigo_segue():
    merged = merge_sets_for_update([_s(3, set_type="working")], STORED)
    assert merged[0]["set_type"] == "working"


class _Cur:
    def __init__(self, row):
        self.row, self.calls = row, []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


def test_rollup_dor_usa_o_maximo_das_series_confirmadas():
    cur = _Cur({"muscle_pain": 4, "joint_pain": 2})
    rollup_exercise_pain(cur, 7)
    assert "MAX(muscle_pain)" in cur.calls[0][0] and "reps_pending = 0" in cur.calls[0][0]
    assert cur.calls[1][1] == (4, 2, 7) and "COALESCE" in cur.calls[1][0]


def test_rollup_sem_dor_preserva_ou_limpa_conforme_o_pedido():
    cur = _Cur({"muscle_pain": None, "joint_pain": None})
    rollup_exercise_pain(cur, 7)
    assert "COALESCE(%s, muscle_pain)" in cur.calls[1][0] and cur.calls[1][1] == (None, None, 7)
    cur = _Cur(None)
    rollup_exercise_pain(cur, 7, keep_stored_when_empty=False)
    assert "COALESCE" not in cur.calls[1][0] and cur.calls[1][1] == (None, None, 7)

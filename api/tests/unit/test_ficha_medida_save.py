"""Save da ficha e a medida da série (Lote T2): `_replace_plan_exercises`
grava `measure` (NOT NULL; ausente = `reps` pelo dono), `duration_seconds_target`
e `time_window_seconds`; numa linha existente, o cliente que não manda os
campos preserva os gravados (`workout_plan_exercise_merge`)."""

from __future__ import annotations

import pytest

from app import services
from app.domain.set_measures import SetMeasure
from app.domain.training_goal import DEFAULT_GOAL


class _Cursor:
    def __init__(self, existing=()):
        self.existing = [dict(row) for row in existing]
        self.calls = []
        self.lastrowid = 900
        self._last = None

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), tuple(params)))
        self._last = sql

    def fetchall(self):
        return list(self.existing)


@pytest.fixture(autouse=True)
def _sem_resolucao(monkeypatch):
    monkeypatch.setattr(services, "resolve_mapping_id", lambda name, amazfit, explicit, cur=None: explicit)
    monkeypatch.setattr(services, "is_pair_coherent", lambda cur, mapping_id, gym_equipment_id: True)


def _escrita(cur):
    (sql, params), = [c for c in cur.calls if c[0].startswith(("INSERT INTO workout_plan_exercises", "UPDATE workout_plan_exercises"))]
    return sql, params


def test_insert_sem_medida_grava_reps_e_alvos_nulos():
    cur = _Cursor()
    services._replace_plan_exercises(cur, 1, [{"sequence": 1, "exercise_name": "Supino", "mapping_id": 5}])
    sql, params = _escrita(cur)
    assert "measure, duration_seconds_target, time_window_seconds" in sql
    assert params[-4:] == (SetMeasure.REPS.value, None, None, DEFAULT_GOAL.value)
    assert sql.count("%s") == len(params)


def test_insert_grava_a_medida_canonica_do_modelo():
    cur = _Cursor()
    services._replace_plan_exercises(
        cur,
        1,
        [{"sequence": 1, "exercise_name": "Prancha", "mapping_id": 5, "measure": " DURATION ", "duration_seconds_target": 60}],
    )
    _sql, params = _escrita(cur)
    assert params[-4:-1] == (SetMeasure.DURATION.value, 60, None)


def test_update_do_cliente_antigo_preserva_a_medida_gravada():
    existente = {
        "id": 77,
        "sequence": 1,
        "exercise_name": "Prancha",
        **{campo: None for campo in services.ADVANCED_EXERCISE_FIELDS},
        "measure": SetMeasure.DURATION.value,
        "duration_seconds_target": 60,
    }
    cur = _Cursor(existing=[existente])
    services._replace_plan_exercises(cur, 1, [{"sequence": 1, "exercise_name": "Prancha", "mapping_id": 5}])
    sql, params = _escrita(cur)
    assert sql.startswith("UPDATE workout_plan_exercises")
    assert "measure = %s, duration_seconds_target = %s, time_window_seconds = %s" in sql
    assert params[-5:] == (SetMeasure.DURATION.value, 60, None, DEFAULT_GOAL.value, 77)
    assert sql.count("%s") == len(params)


def test_get_da_ficha_devolve_os_tres_campos():
    import inspect

    fonte = inspect.getsource(services.get_workout_plan)
    assert "wpe.measure, wpe.duration_seconds_target, wpe.time_window_seconds" in fonte

"""#219: PUT/POST do Registro: trava de tipo/medida (422), objetivo do treino
(validado pelo dono), dor por série x dor do exercício e `started_at` com fuso.

Rodar: cd api && python -m pytest tests/unit/test_registro_edicao_campos_pos_treino.py
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException

from app import main, services
from app.domain import training_goal
from app.main import _exercise_log_data
from app.models import ExerciseLogCreateIn, ExerciseLogUpdateIn
from tests.unit.test_registro_edicao_treino_encerrado import _Cur, _setup


def _body(sets=None, **extra):
    return {"local_date": "2026-11-10", "original_name": "Supino",
            "sets": sets or [{"set_number": 1, "reps": 8, "weight_kg": 50}], **extra}


def _put(**extra):
    return services.update_exercise_log(10, _exercise_log_data(ExerciseLogUpdateIn.model_validate(_body(**extra))), user_id=7)


def _stored(number, set_type, **extra):
    return {"set_number": number, "reps_pending": 0, "set_type": set_type, "measure": "reps", "duration_seconds": None, **extra}


def _route_put(**extra):
    return main.update_exercise_log_by_id(10, ExerciseLogUpdateIn.model_validate(_body(**extra)), identity=type("I", (), {"user_id": 7})())


def test_trava_de_tipo_vira_422_estavel_e_troca_comum_grava(monkeypatch):
    cur = _Cur()
    cur.stored_sets = [_stored(1, "drop_set"), _stored(2, "working")]
    _setup(monkeypatch, cur)
    with pytest.raises(HTTPException) as exc:
        _route_put(sets=[{"set_number": 1, "set_type": "working", "reps": 8}])
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "set_type_locked" and exc.value.detail["set_number"] == 1
    assert "Série 1" in exc.value.detail["message"]
    # working -> warmup grava; round-trip do tipo gravado passa
    _route_put(sets=[{"set_number": 1, "set_type": "drop_set", "reps": 8}, {"set_number": 2, "set_type": "warmup", "reps": 8}])
    inserts = cur.starting("INSERT INTO exercise_sets")
    assert [p[2] for p in inserts] == ["drop_set", "warmup"]


def test_trava_vale_para_o_post(monkeypatch):
    cur = _Cur()
    _setup(monkeypatch, cur)
    body = ExerciseLogCreateIn.model_validate(_body(activity_id=20, sets=[{"set_number": 1, "set_type": "drop_set", "reps": 8}]))
    with pytest.raises(HTTPException) as exc:
        main.create_exercise_log_in_closed_session(body, identity=type("I", (), {"user_id": 7})())
    assert exc.value.detail["code"] == "set_type_locked"


def test_objetivo_invalido_422_e_valido_grava_ausente_preserva(monkeypatch):
    cur = _Cur()
    _setup(monkeypatch, cur)
    with pytest.raises(HTTPException) as exc:
        _route_put(training_goal="cardio")
    assert exc.value.status_code == 422 and exc.value.detail["code"] == "training_goal_invalid"
    assert cur.starting("UPDATE exercises SET original_name") == []
    _put(training_goal="strength")
    assert cur.starting("UPDATE exercises SET original_name")[0][-2] == "strength"
    _put()
    assert cur.starting("UPDATE exercises SET original_name")[1][-2] is None  # COALESCE preserva
    _put(training_goal=None)
    assert cur.starting("UPDATE exercises SET original_name")[2][-2] is None
    assert "COALESCE(%s, training_goal)" in [s for s, _ in cur.log if s.startswith("UPDATE exercises SET original_name")][0]
    assert training_goal.optional_goal_value("strength") == "strength"


def test_post_objetivo_enviado_prevalece_e_reavalia_depois(monkeypatch):
    cur = _Cur()
    _, calls = _setup(monkeypatch, cur)
    services.create_exercise_log(20, _exercise_log_data(ExerciseLogCreateIn.model_validate(_body(activity_id=20, training_goal="strength"))), user_id=7)
    assert cur.starting("UPDATE exercises SET original_name")[0][-2] == "strength"
    assert calls and calls[0][2] == len(cur.log)  # reavaliação por último


class _PainCur(_Cur):
    """Simula as colunas de dor de `exercises` e das séries (rollup real)."""

    def __init__(self, set_pain, stored=(5, True)):
        super().__init__()
        self.row_pain = list(stored)
        self.set_pain = set_pain

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        super().execute(sql, params)
        if flat.startswith("UPDATE exercises SET original_name"):
            self.row_pain = [params[-5], params[-4]]
        elif flat.startswith("SELECT MAX(muscle_pain)"):
            self._result = [{"muscle_pain": self.set_pain[0], "joint_pain": self.set_pain[1]}]
        elif flat.startswith("UPDATE exercises SET muscle_pain = COALESCE"):
            m, j, _ = params
            self.row_pain = [m if m is not None else self.row_pain[0], j if j is not None else self.row_pain[1]]


def _pain(monkeypatch, set_pain, **extra):
    cur = _PainCur(set_pain)
    _setup(monkeypatch, cur)
    _put(**extra)
    return tuple(cur.row_pain)


def test_dor_series_com_dor_maximo_vence_o_campo(monkeypatch):
    assert _pain(monkeypatch, (7, True), muscle_pain=3, joint_pain=False) == (7, True)


def test_dor_series_sem_dor_valor_enviado_null_explicito_e_ausente(monkeypatch):
    assert _pain(monkeypatch, (None, None), muscle_pain=2, joint_pain=True) == (2, True)
    assert _pain(monkeypatch, (None, None), muscle_pain=None, joint_pain=None) == (None, None)
    assert _pain(monkeypatch, (None, None)) == (5, True)


def test_started_at_com_fuso_normaliza_antes_de_comparar(monkeypatch):
    from app import workout_sessions
    from app.intelligence.dashboard import time_windows

    monkeypatch.setattr(time_windows, "user_timezone", lambda conn, uid: "America/Sao_Paulo")
    gravado = datetime(2026, 11, 10, 7, 0)  # 10:00Z em Sao Paulo (-03:00)
    anchors = []

    def run(started):
        cur = _Cur(started_at=gravado)
        _, calls = _setup(monkeypatch, cur)
        _put(started_at=started)
        anchors.append(calls[0][3])

    run("2026-11-10T07:00:00-03:00")  # mesmo instante: hora de parede 07:00
    run("2026-11-10T08:00:00-03:00")  # muda o instante
    assert anchors[0] is None and anchors[1] is not None
    assert workout_sessions._to_user_wall_clock

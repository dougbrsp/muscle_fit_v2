"""#205: o Registro de exercicios marca/desmarca o aprimoramento de tecnica e
reavalia a progressao so quando a marca muda em sessao encerrada.

Rodar: cd api && python -m pytest tests/unit/test_form_practice_edicao_registro.py
"""

from __future__ import annotations

import pytest

from app import progression_state, services
from app.domain import effective_intensity
from app.domain.form_practice import resolve_form_practice
from app.models import ExerciseLogUpdateIn
from tests.support.fake_db import LoopingExecuteMany, fake_transaction


class _Cur(LoopingExecuteMany):
    def __init__(self, stored, status):
        self.stored, self.status = stored, status
        self.update_params = None
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if flat.startswith("SELECT e.id, e.activity_id") or flat.startswith("SELECT id, activity_id"):
            self._result = [{"id": 10, "activity_id": 20, "original_name": "Supino", "mapping_id": 5,
                             "workout_plan_exercise_id": None, "equipment_id": None, "muscle_pain": None,
                             "joint_pain": None, "form_practice": self.stored, "session_status": self.status}]
        elif flat.startswith("UPDATE exercises SET original_name"):
            self.update_params = params
            self._result = []
        elif flat.startswith("SELECT set_number,"):
            self._result = []
        else:
            self._result = [{"avg_rpe": None}]

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


def _run(monkeypatch, stored, status, sent, user_id=7, boom=False):
    cur = _Cur(stored, status)
    calls = []
    monkeypatch.setattr(services, "transaction", fake_transaction(cur))
    monkeypatch.setattr(
        effective_intensity, "resolve_for_logged_exercise", lambda conn, eid: effective_intensity.UNKNOWN_INTENSITY
    )

    def fake_reeval(conn, uid, aid, **kw):
        calls.append((uid, aid))
        if boom:
            raise RuntimeError("falhou")
        return {"ok": True}

    monkeypatch.setattr(progression_state, "reevaluate_after_correction", fake_reeval)
    payload = {"local_date": "2026-11-18", "original_name": "Supino", "sets": []}
    if sent is not None:
        payload["form_practice"] = sent
    return services.update_exercise_log(10, payload, user_id=user_id), cur, calls


def test_modelo_aceita_o_campo_e_default_none():
    assert ExerciseLogUpdateIn.model_fields["form_practice"].default is None
    m = ExerciseLogUpdateIn(local_date="2026-11-18", original_name="x", form_practice=True)
    assert m.model_dump()["form_practice"] is True


def test_resolve_omitido_preserva():
    assert resolve_form_practice(None, 1) is True and resolve_form_practice(None, 0) is False
    assert resolve_form_practice(False, 1) is False and resolve_form_practice(True, 0) is True


@pytest.mark.parametrize("stored", [0, 1])
def test_omitido_preserva_a_marca_e_reavalia(monkeypatch, stored):
    out, cur, calls = _run(monkeypatch, stored, "done", None)
    assert cur.update_params[-3] == stored and calls == [(7, 20)]
    assert out["form_practice"] is bool(stored) and out["progression_reevaluated"] is True


def test_marcar_grava_e_reavalia(monkeypatch):
    out, cur, calls = _run(monkeypatch, 0, "done", True)
    assert cur.update_params[-3] == 1 and calls == [(7, 20)]
    assert out["form_practice"] is True and out["progression_reevaluated"] is True


def test_desmarcar_grava_e_reavalia(monkeypatch):
    out, cur, calls = _run(monkeypatch, 1, "done", False)
    assert cur.update_params[-3] == 0 and calls == [(7, 20)]
    assert out["form_practice"] is False and out["progression_reevaluated"] is True


def test_mesmo_valor_reavalia_em_sessao_encerrada(monkeypatch):
    out, _, calls = _run(monkeypatch, 1, "done", True)
    assert calls == [(7, 20)] and out["progression_reevaluated"] is True


@pytest.mark.parametrize("status", ["in_progress", None])
def test_sessao_nao_encerrada_nao_reavalia(monkeypatch, status):
    out, cur, calls = _run(monkeypatch, 0, status, True)
    assert cur.update_params[-3] == 1 and calls == [] and out["progression_reevaluated"] is False


def test_sem_user_id_nao_reavalia(monkeypatch):
    out, _, calls = _run(monkeypatch, 0, "done", True, user_id=None)
    assert calls == [] and out["progression_reevaluated"] is False


def test_erro_na_reavaliacao_propaga(monkeypatch):
    with pytest.raises(RuntimeError):
        _run(monkeypatch, 0, "done", True, boom=True)

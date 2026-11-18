"""#205 lote B1: editar (qualquer campo) ou excluir exercicio de sessao encerrada
reavalia a progressao na mesma transacao.

Rodar: cd api && python -m pytest tests/unit/test_registro_edicao_reavalia_progressao.py
"""

from __future__ import annotations

import pytest

from app import progression_state, services
from app.domain import effective_intensity
from tests.support.fake_db import LoopingExecuteMany, fake_transaction


class _Cur(LoopingExecuteMany):
    def __init__(self, status):
        self.status = status
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
                             "joint_pain": None, "form_practice": 0, "session_status": self.status}]
        elif flat.startswith("SELECT set_number,"):
            self._result = []
        else:
            self._result = [{"avg_rpe": None}]

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


def _setup(monkeypatch, status, ret=None, boom=False):
    cur = _Cur(status)
    calls = []
    monkeypatch.setattr(services, "transaction", fake_transaction(cur))
    monkeypatch.setattr(
        effective_intensity, "resolve_for_logged_exercise", lambda conn, eid: effective_intensity.UNKNOWN_INTENSITY
    )

    def fake_reeval(conn, uid, aid, **kw):
        calls.append((uid, aid))
        if boom:
            raise RuntimeError("falhou")
        return ret

    monkeypatch.setattr(progression_state, "reevaluate_after_correction", fake_reeval)
    return calls


def _edit(user_id=7):
    payload = {"local_date": "2026-11-18", "original_name": "Supino", "sets": []}
    return services.update_exercise_log(10, payload, user_id=user_id)


def test_edicao_de_series_em_sessao_encerrada_reavalia(monkeypatch):
    calls = _setup(monkeypatch, "done", ret={"ok": 1})
    out = _edit()
    assert calls == [(7, 20)] and out["progression_reevaluated"] is True


def test_edicao_em_andamento_nao_reavalia(monkeypatch):
    calls = _setup(monkeypatch, "in_progress", ret={"ok": 1})
    assert _edit()["progression_reevaluated"] is False and calls == []


def test_edicao_sem_user_id_nao_reavalia(monkeypatch):
    calls = _setup(monkeypatch, "done", ret={"ok": 1})
    assert _edit(user_id=None)["progression_reevaluated"] is False and calls == []


def test_reavaliacao_devolvendo_none_nao_marca_reavaliado(monkeypatch):
    calls = _setup(monkeypatch, "done", ret=None)
    assert _edit()["progression_reevaluated"] is False and calls == [(7, 20)]


def test_exclusao_em_sessao_encerrada_reavalia(monkeypatch):
    calls = _setup(monkeypatch, "done", ret={"ok": 1})
    out = services.delete_exercise_log(10, user_id=7)
    assert calls == [(7, 20)] and out["activity_id"] == 20


@pytest.mark.parametrize("user_id,status", [(7, "in_progress"), (None, "done")])
def test_exclusao_sem_reavaliar(monkeypatch, user_id, status):
    calls = _setup(monkeypatch, status, ret={"ok": 1})
    assert services.delete_exercise_log(10, user_id=user_id) is not None
    assert calls == []


def test_erro_propaga_na_edicao(monkeypatch):
    _setup(monkeypatch, "done", boom=True)
    with pytest.raises(RuntimeError):
        _edit()


def test_erro_propaga_na_exclusao(monkeypatch):
    _setup(monkeypatch, "done", boom=True)
    with pytest.raises(RuntimeError):
        services.delete_exercise_log(10, user_id=7)

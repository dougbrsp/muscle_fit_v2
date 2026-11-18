"""Issue #205, lote B2: deload de treino ENCERRADO pode mudar depois, com a
progressão reavaliada. Regra no dono `domain/deload.py`; a rota só chama.

Sem MySQL: cursor roteirizado no estilo de `test_m97_f3_objetivo_telas.py`.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import HTTPException

from app import workout_sessions
from app.domain import deload as dl
from app.security import Identity
from tests.support.fake_db import fake_transaction

IDENTITY = Identity(user_id=1, role="regular", source="session")
DONE = dl.SESSION_STATUS_DONE
MANUAL = dl.DeloadSource.MANUAL.value
PERIOD = dl.DeloadSource.PERIOD.value
PERIODO = {"id": 4, "starts_on": _dt.date(2026, 10, 20), "ends_on": _dt.date(2026, 10, 26), "cancelled_at": None}


# --- dono -------------------------------------------------------------------


def test_dono_em_andamento_com_serie_trava_e_sem_parametro_novo_e_como_antes():
    with pytest.raises(dl.DeloadToggleLocked):
        dl.toggled_deload_source(None, requested=True, saved_sets=2, period_active=False)
    with pytest.raises(dl.DeloadToggleLocked):
        dl.toggled_deload_source(MANUAL, requested=False, saved_sets=2, period_active=False, session_status="in_progress")
    assert not dl.can_toggle_deload(2) and not dl.can_toggle_deload(2, session_status=None)
    assert dl.can_toggle_deload(0, session_status="in_progress")


def test_dono_encerrada_liga_e_desliga_com_series():
    assert dl.can_toggle_deload(9, session_status=DONE)
    assert dl.toggled_deload_source(None, requested=True, saved_sets=9, period_active=False, session_status=DONE) == MANUAL
    assert dl.toggled_deload_source(MANUAL, requested=False, saved_sets=9, period_active=False, session_status=DONE) is None
    assert dl.toggled_deload_source(PERIOD, requested=False, saved_sets=9, period_active=True, session_status=DONE) is None


def test_dono_encerrada_idempotente_e_periodo_vence_marca_avulsa():
    assert dl.toggled_deload_source(MANUAL, requested=True, saved_sets=9, period_active=False, session_status=DONE) == MANUAL
    assert dl.toggled_deload_source(None, requested=False, saved_sets=9, period_active=False, session_status=DONE) is None
    assert dl.toggled_deload_source(None, requested=True, saved_sets=9, period_active=True, session_status=DONE) == PERIOD


# --- rota -------------------------------------------------------------------


class _Cursor:
    def __init__(self, *, source=None, status=DONE, saved_sets=5, period=None):
        self.source, self.status, self.saved_sets, self.period = source, status, saved_sets, period
        self.executed: list[str] = []
        self._pending = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        self.executed.append(flat)
        if flat.startswith("SELECT id, gym_id FROM activities"):
            self._pending = {"id": params[0], "gym_id": None}
        elif flat.startswith("SELECT a.deload_source"):
            self._pending = {
                "deload_source": self.source,
                "local_date": _dt.date(2026, 10, 25),
                "session_status": self.status,
            }
        elif "FROM exercise_sets" in flat:
            self._pending = {"n": self.saved_sets}
        elif "FROM deload_periods" in flat:
            self._pending = self.period
        else:
            self._pending = None

    def fetchone(self):
        return self._pending


def _patch(monkeypatch, cursor, reavaliar, **payload):
    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(cursor))
    monkeypatch.setattr(workout_sessions, "reevaluate_after_correction", reavaliar)
    return workout_sessions.update_session_meta(77, workout_sessions.SessionMetaIn(**payload), identity=IDENTITY)


def test_encerrada_muda_e_reavalia_depois_do_update(monkeypatch):
    cursor = _Cursor()
    chamadas = []

    def reavaliar(conn, user_id, activity_id):
        chamadas.append((user_id, activity_id, len(cursor.executed)))
        assert cursor.executed[-1].startswith("UPDATE activities")  # depois do UPDATE
        return {"changed": 1}

    out = _patch(monkeypatch, cursor, reavaliar, deload=True)
    assert chamadas == [(1, 77, len(cursor.executed))]
    assert out["progression_reevaluated"] is True
    assert out["deload"]["active"] is True and out["deload"]["can_toggle"] is True


def test_encerrada_desligar_tambem_reavalia(monkeypatch):
    cursor = _Cursor(source=MANUAL)
    out = _patch(monkeypatch, cursor, lambda *a: {"ok": 1}, deload=False)
    assert out["deload"]["active"] is False and out["progression_reevaluated"] is True


def test_reavaliacao_devolvendo_none_vira_falso(monkeypatch):
    out = _patch(monkeypatch, _Cursor(), lambda *a: None, deload=True)
    assert out["progression_reevaluated"] is False


def test_sem_mudanca_nao_reavalia(monkeypatch):
    def proibido(*a):
        raise AssertionError("sem mudança de origem não reavalia")

    out = _patch(monkeypatch, _Cursor(source=MANUAL), proibido, deload=True)
    assert out["progression_reevaluated"] is False


def test_em_andamento_nao_reavalia_e_com_serie_trava(monkeypatch):
    def proibido(*a):
        raise AssertionError("sessão em andamento não reavalia")

    out = _patch(monkeypatch, _Cursor(status="in_progress", saved_sets=0), proibido, deload=True)
    assert out["progression_reevaluated"] is False and out["deload"]["can_toggle"] is True
    with pytest.raises(HTTPException) as erro:
        _patch(monkeypatch, _Cursor(status="in_progress", saved_sets=2), proibido, deload=True)
    assert erro.value.status_code == 409 and erro.value.detail["error"] == "deload_locked"


def test_erro_na_reavaliacao_propaga(monkeypatch):
    def quebra(*a):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _patch(monkeypatch, _Cursor(), quebra, deload=True)


def test_meta_sem_deload_nao_traz_o_campo_novo(monkeypatch):
    out = _patch(monkeypatch, _Cursor(), lambda *a: None, name="Treino A")
    assert "progression_reevaluated" not in out and "deload" not in out


def test_get_deload_encerrada_pode_alternar(monkeypatch):
    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(_Cursor(source=MANUAL, saved_sets=8)))
    out = workout_sessions.get_session_deload(77, identity=IDENTITY)
    assert out["deload"]["can_toggle"] is True

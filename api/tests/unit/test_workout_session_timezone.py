"""`started_at`/`ended_at` do Modo Treino na hora de PAREDE do usuario.

Bug real (2026-09-15, atividades 219..235): `start_session` fazia
`started_at = payload.started_at or datetime.now()`; o Modo Treino nao manda
`started_at` e a Lambda roda em UTC -> started_at em UTC, 3 h fora da janela
de 3600 s do casamento de FC com o Intervals (`NO_CANDIDATE`).

Sem banco e sem HTTP: `transaction` vira um dublê de cursor que responde por
padrao de SQL (mesma tecnica de `test_finish_sessao_idempotente.py`) e o
relogio e fixado via `user_now`.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from app import workout_sessions
from app.intelligence.dashboard import time_windows
from app.security import Identity
from app.workout_sessions import SessionFinishIn, SessionMetaIn, SessionStartIn, _to_user_wall_clock
from tests.support.fake_db import fake_transaction

IDENTITY = Identity(user_id=1, role="regular", source="session")
SP = "America/Sao_Paulo"
# 2026-10-02 12:34:56 UTC = 09:34:56 em Sao Paulo (UTC-3, sem horario de verao).
NOW_UTC = _dt.datetime(2026, 10, 2, 12, 34, 56, tzinfo=_dt.timezone.utc)


class FakeCursor:
    def __init__(self, executed, *, timezone=SP, activity=None):
        self._executed = executed
        self._timezone = timezone
        self._activity = activity
        self._pending = None
        self.lastrowid = 999

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self._executed.append((normalized, params))
        if normalized.startswith("SELECT timezone FROM users"):
            self._pending = {"timezone": self._timezone}
        elif normalized.startswith("SELECT ended_at FROM activities"):
            update = next(p for s, p in reversed(self._executed) if s.startswith("UPDATE activities SET ended_at"))
            self._pending = {"ended_at": update[0]}
        elif "FROM activities WHERE id" in normalized:
            self._pending = self._activity
        elif "COUNT(*) AS exercises" in normalized:
            self._pending = {"exercises": 3}
        elif "SELECT AVG(avg_rpe)" in normalized:
            self._pending = {"avg_rpe": None}
        else:
            self._pending = None

    def fetchone(self):
        return self._pending


@pytest.fixture
def harness(monkeypatch):
    """Instala o dublê e o relogio fixo. Devolve fabrica (timezone, activity) -> executed."""

    def _install(timezone=SP, activity=None):
        executed: list[tuple[str, tuple]] = []
        cursor = FakeCursor(executed, timezone=timezone, activity=activity)

        monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(cursor))
        monkeypatch.setattr(workout_sessions, "source_id", lambda *a, **k: 7)
        monkeypatch.setattr(workout_sessions, "resolve_gym_id", lambda *a, **k: None)
        monkeypatch.setattr(workout_sessions, "on_session_finish", lambda *a, **k: None)
        monkeypatch.setattr(workout_sessions, "user_now", lambda tz: time_windows.user_now(tz, NOW_UTC))
        return executed

    return _install


def _insert_params(executed):
    return next(p for s, p in executed if s.startswith("INSERT INTO activities"))


# --- helper -------------------------------------------------------------------


def test_aware_e_convertido_para_a_parede_do_usuario():
    aware = _dt.datetime(2026, 10, 2, 12, 0, tzinfo=_dt.timezone.utc)
    assert _to_user_wall_clock(aware, SP) == _dt.datetime(2026, 10, 2, 9, 0)


def test_naive_e_mantido():
    naive = _dt.datetime(2026, 10, 2, 9, 0)
    assert _to_user_wall_clock(naive, SP) is naive
    assert _to_user_wall_clock(None, SP) is None


def test_fuso_invalido_cai_em_sao_paulo():
    aware = _dt.datetime(2026, 10, 2, 12, 0, tzinfo=_dt.timezone.utc)
    assert _to_user_wall_clock(aware, "Nao/Existe") == _dt.datetime(2026, 10, 2, 9, 0)


# --- start_session ----------------------------------------------------------------


def test_start_sem_started_at_grava_hora_local_do_usuario(harness):
    executed = harness()
    out = workout_sessions.start_session(SessionStartIn(), identity=IDENTITY)
    params = _insert_params(executed)
    local_date, started_at = params[5], params[6]
    assert started_at == _dt.datetime(2026, 10, 2, 9, 34, 56)
    assert started_at.tzinfo is None
    assert local_date == _dt.date(2026, 10, 2)
    assert out["started_at"] == "2026-10-02T09:34:56"
    assert params[1].startswith("zepp-session-")


def test_start_as_01h30_utc_cai_no_dia_anterior(harness, monkeypatch):
    executed = harness()
    madrugada = _dt.datetime(2026, 10, 3, 1, 30, tzinfo=_dt.timezone.utc)
    monkeypatch.setattr(workout_sessions, "user_now", lambda tz: time_windows.user_now(tz, madrugada))
    workout_sessions.start_session(SessionStartIn(), identity=IDENTITY)
    params = _insert_params(executed)
    assert params[6] == _dt.datetime(2026, 10, 2, 22, 30)
    assert params[5] == _dt.date(2026, 10, 2)


def test_start_com_started_at_aware_e_convertido(harness):
    executed = harness()
    payload = SessionStartIn(started_at="2026-10-02T12:00:00Z")
    workout_sessions.start_session(payload, identity=IDENTITY)
    params = _insert_params(executed)
    assert params[6] == _dt.datetime(2026, 10, 2, 9, 0)
    assert params[5] == _dt.date(2026, 10, 2)


def test_start_com_started_at_naive_e_mantido(harness):
    executed = harness()
    payload = SessionStartIn(started_at="2026-10-02T07:15:00", local_date="2026-10-01")
    workout_sessions.start_session(payload, identity=IDENTITY)
    params = _insert_params(executed)
    assert params[6] == _dt.datetime(2026, 10, 2, 7, 15)
    # local_date explicito do cliente prevalece.
    assert params[5] == _dt.date(2026, 10, 1)


def test_start_com_fuso_invalido_no_perfil_usa_sao_paulo(harness):
    executed = harness(timezone="Fuso/Quebrado")
    workout_sessions.start_session(SessionStartIn(), identity=IDENTITY)
    assert _insert_params(executed)[6] == _dt.datetime(2026, 10, 2, 9, 34, 56)


# --- finish_session ----------------------------------------------------------------


def test_finish_ao_vivo_grava_ended_at_local_e_duracao_coerente(harness):
    started = _dt.datetime(2026, 10, 2, 8, 34, 56)  # local, 1 h antes de "agora"
    executed = harness(
        activity={
            "id": 240,
            "gym_id": None,
            "session_status": "in_progress",
            "started_at": started,
            "ended_at": None,
            "duration_seconds": None,
            "workout_plan_id": None,
        }
    )
    out = workout_sessions.finish_session(240, SessionFinishIn(), identity=IDENTITY)
    assert out["ended_at"] == "2026-10-02T09:34:56"
    assert out["duration_seconds"] == 3600
    assert any(s.startswith("SELECT timezone FROM users") for s, _ in executed)


def test_finish_com_ended_at_aware_e_normalizado(harness):
    started = _dt.datetime(2026, 10, 2, 9, 0)
    harness(
        activity={
            "id": 240,
            "gym_id": None,
            "session_status": "in_progress",
            "started_at": started,
            "ended_at": None,
            "duration_seconds": None,
            "workout_plan_id": None,
        }
    )
    payload = SessionFinishIn(ended_at="2026-10-02T13:00:00Z")
    out = workout_sessions.finish_session(240, payload, identity=IDENTITY)
    assert out["ended_at"] == "2026-10-02T10:00:00"
    assert out["duration_seconds"] == 3600


def test_finish_idempotente_nao_consulta_fuso(harness):
    executed = harness(
        activity={
            "id": 230,
            "gym_id": 1,
            "session_status": "done",
            "started_at": _dt.datetime(2026, 9, 25, 9, 12, 21),
            "ended_at": _dt.datetime(2026, 9, 25, 10, 8, 27),
            "duration_seconds": 3365,
            "workout_plan_id": None,
        }
    )
    out = workout_sessions.finish_session(230, SessionFinishIn(), identity=IDENTITY)
    assert out["already_finished"] is True
    assert not any("timezone" in s for s, _ in executed)


# --- update_session_meta ----------------------------------------------------------------


def test_meta_started_at_aware_e_normalizado(harness):
    executed = harness(activity={"id": 240, "gym_id": None})
    payload = SessionMetaIn(started_at="2026-10-02T12:00:00+00:00")
    workout_sessions.update_session_meta(240, payload, identity=IDENTITY)
    update = next(p for s, p in executed if s.startswith("UPDATE activities SET started_at"))
    assert update[0] == _dt.datetime(2026, 10, 2, 9, 0)


def test_meta_started_at_naive_nao_consulta_fuso(harness):
    executed = harness(activity={"id": 240, "gym_id": None})
    payload = SessionMetaIn(started_at="2026-10-02T07:00:00")
    workout_sessions.update_session_meta(240, payload, identity=IDENTITY)
    update = next(p for s, p in executed if s.startswith("UPDATE activities SET started_at"))
    assert update[0] == _dt.datetime(2026, 10, 2, 7, 0)
    assert not any("timezone" in s for s, _ in executed)

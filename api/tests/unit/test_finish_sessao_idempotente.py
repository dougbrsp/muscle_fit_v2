"""POST /workout-sessions/{id}/finish e idempotente.

Cenario sintetico baseado em bug real (2026-09-25): a PRIMEIRA chamada efetivou o
encerramento no servidor (`ended_at=2026-09-25 10:00:00`,
`session_status='done'`, `duration_seconds=3599`) e so a RESPOSTA nao voltou
ao browser - o API Gateway tem teto de 29 s e devolve
`{"message":"Endpoint request timed out"}`, que aparece como erro de API/CORS
(mesmo precedente do PR #188). A segunda tentativa do usuario recebia
409 "Este treino ja foi finalizado." e a tela mostrava faixa vermelha num
treino perfeitamente encerrado.

Sem banco e sem HTTP: `transaction` e substituido por um dublê de cursor que
responde por padrao de SQL e registra tudo que foi executado - o que permite
provar que a repeticao NAO reescreve nada.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import HTTPException

from app import workout_sessions
from app.security import Identity
from app.workout_sessions import SessionFinishIn
from tests.support.fake_db import fake_transaction

ENDED_AT = _dt.datetime(2026, 9, 25, 10, 0, 0)
STARTED_AT = _dt.datetime(2026, 9, 25, 9, 0, 0)
IDENTITY = Identity(user_id=1, role="regular", source="session")


class FakeCursor:
    def __init__(self, activity: dict | None, executed: list[tuple[str, tuple]]):
        self._activity = activity
        self._executed = executed
        self._pending: dict | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._executed.append((" ".join(sql.split()), params))
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT ended_at FROM activities"):
            self._pending = {"ended_at": ENDED_AT}
        elif "FROM activities WHERE id" in normalized:
            self._pending = self._activity
        elif "COUNT(*) AS exercises" in normalized:
            self._pending = {"exercises": 8}
        elif "SELECT AVG(avg_rpe)" in normalized:
            self._pending = {"avg_rpe": None}
        else:
            self._pending = None

    def fetchone(self):
        return self._pending


@pytest.fixture
def finish(monkeypatch):
    """Chama finish_session com uma activity dublê. Devolve (resposta, SQL executado)."""

    def _run(activity: dict | None, payload: SessionFinishIn | None = None):
        executed: list[tuple[str, tuple]] = []
        cursor = FakeCursor(activity, executed)

        monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(cursor))
        # O motor de progressao em sombra nao e o objeto deste teste.
        monkeypatch.setattr(workout_sessions, "on_session_finish", lambda *a, **k: None)
        out = workout_sessions.finish_session(50, payload or SessionFinishIn(), identity=IDENTITY)
        return out, executed

    return _run


def _writes(executed):
    return [sql for sql, _ in executed if sql.upper().startswith(("UPDATE", "INSERT", "DELETE"))]


def test_sessao_ja_encerrada_responde_sucesso_e_nao_reescreve_nada(finish):
    out, executed = finish(
        {
            "id": 50,
            "gym_id": 1,
            "session_status": "done",
            "started_at": STARTED_AT,
            "ended_at": ENDED_AT,
            "duration_seconds": 3599,
            "workout_plan_id": 10,
        }
    )
    assert out["status"] == "ok"
    assert out["session_status"] == "done"
    assert out["already_finished"] is True
    # O estado devolvido e o que JA estava gravado - nao a hora da 2a tentativa.
    assert out["ended_at"] == ENDED_AT.isoformat()
    assert out["duration_seconds"] == 3599
    assert out["exercises"] == 8
    assert out["workout_plan_marked_done"] is True
    assert _writes(executed) == []


def test_primeira_finalizacao_de_sessao_ao_vivo_continua_gravando(finish):
    out, executed = finish(
        {
            "id": 50,
            "gym_id": 1,
            "session_status": "in_progress",
            "started_at": STARTED_AT,
            "ended_at": None,
            "duration_seconds": None,
            "workout_plan_id": 10,
        },
        SessionFinishIn(ended_at=ENDED_AT),
    )
    assert out["already_finished"] is False
    assert out["ended_at"] == ENDED_AT.isoformat()
    # started_at 09:00:00 -> ended_at 10:00:00 = 3600 s (o cliente pode mandar
    # `duration_seconds` proprio, que prevalece).
    assert out["duration_seconds"] == 3600
    assert out["workout_plan_marked_done"] is True
    writes = _writes(executed)
    assert any("UPDATE activities" in sql and "session_status = 'done'" in sql for sql in writes)
    assert any("UPDATE workout_plans SET status = 'done'" in sql for sql in writes)


def test_activity_inexistente_ou_de_outro_usuario_continua_404(finish):
    with pytest.raises(HTTPException) as exc:
        finish(None)
    assert exc.value.status_code == 404

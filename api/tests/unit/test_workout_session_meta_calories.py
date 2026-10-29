"""`PATCH /workout-sessions/{id}/meta` com `calories_kcal` (nutrição L0, migração 076).
A origem gravada vem do dono (`domain/calories_source.source_on_manual_write`):
valor diferente da kcal do link do Intervals grava `manual`; valor igual ao do
link (0,01) grava origem `NULL` (a leitura volta a inferir `intervals`); `null`
explícito limpa os dois; ausente não toca; negativo é 422. Sem banco:
`transaction` vira um dublê de cursor (padrão de `test_workout_session_timezone.py`)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app import workout_sessions
from app.domain.calories_source import CALORIES_SOURCE_MANUAL
from app.security import Identity
from app.workout_sessions import SessionMetaIn
from tests.support.fake_db import fake_transaction

IDENTITY = Identity(user_id=1, role="regular", source="session")
LINK_SQL = "SELECT calories_kcal FROM activity_intervals_link WHERE activity_id = %s"


class FakeCursor:
    def __init__(self, executed, link_kcal):
        self._executed = executed
        self._link_kcal = link_kcal
        self._pending = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self._executed.append((normalized, params))
        if "FROM activities WHERE id" in normalized:
            self._pending = {"id": 240, "gym_id": None}
        elif normalized == LINK_SQL:
            self._pending = None if self._link_kcal is None else {"calories_kcal": self._link_kcal}
        else:
            self._pending = None

    def fetchone(self):
        return self._pending


def _fixture(monkeypatch, link_kcal):
    calls: list[tuple[str, tuple]] = []
    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(FakeCursor(calls, link_kcal)))
    monkeypatch.setattr(workout_sessions, "resolve_gym_id", lambda *a, **k: None)
    return calls


@pytest.fixture
def executed(monkeypatch):
    """Treino sem link do Intervals."""
    return _fixture(monkeypatch, None)


@pytest.fixture
def executed_com_link(monkeypatch):
    """Treino ligado a uma atividade do Intervals com 412,37 kcal."""
    return _fixture(monkeypatch, Decimal("412.37"))


def _update(executed):
    return next(((s, p) for s, p in executed if s.startswith("UPDATE activities SET")), None)


def test_kcal_sem_link_grava_valor_e_origem_manual(executed):
    out = workout_sessions.update_session_meta(240, SessionMetaIn(calories_kcal=412), identity=IDENTITY)
    assert (LINK_SQL, (240,)) in executed, "a rota busca a kcal do link para o dono decidir"
    sql, params = _update(executed)
    assert sql == "UPDATE activities SET calories_kcal = %s, calories_source = %s WHERE id = %s"
    assert params == (Decimal("412"), CALORIES_SOURCE_MANUAL, 240)
    assert out["updated_fields"] == ["calories_kcal", "calories_source"]


def test_kcal_diferente_da_do_link_grava_manual(executed_com_link):
    workout_sessions.update_session_meta(240, SessionMetaIn(calories_kcal=412), identity=IDENTITY)
    _sql, params = _update(executed_com_link)
    assert params == (Decimal("412"), CALORIES_SOURCE_MANUAL, 240)


def test_kcal_igual_a_do_link_nao_vira_manual(executed_com_link):
    # Salvar sem mudar (ou digitar o mesmo valor do Intervals) deixa a origem
    # NULL: a leitura volta a inferir `intervals`.
    workout_sessions.update_session_meta(240, SessionMetaIn(calories_kcal="412.37"), identity=IDENTITY)
    sql, params = _update(executed_com_link)
    assert sql == "UPDATE activities SET calories_kcal = %s, calories_source = %s WHERE id = %s"
    assert params == (Decimal("412.37"), None, 240)


def test_null_explicito_limpa_kcal_e_origem(executed_com_link):
    payload = SessionMetaIn.model_validate({"calories_kcal": None})
    workout_sessions.update_session_meta(240, payload, identity=IDENTITY)
    sql, params = _update(executed_com_link)
    assert "calories_kcal = %s, calories_source = %s" in sql
    assert params == (None, None, 240)


def test_ausente_nao_toca_na_kcal_nem_le_o_link(executed):
    workout_sessions.update_session_meta(240, SessionMetaIn(name="Peito"), identity=IDENTITY)
    sql, params = _update(executed)
    assert "calories" not in sql
    assert params == ("Peito", 240)
    assert not any("activity_intervals_link" in s for s, _ in executed)


def test_kcal_negativa_e_rejeitada_antes_do_banco():
    with pytest.raises(ValidationError):
        SessionMetaIn(calories_kcal=-1)
    assert SessionMetaIn(calories_kcal=0).calories_kcal == 0


def test_kcal_mantem_os_demais_campos_do_cabecalho(executed):
    payload = SessionMetaIn(name="Costas", calories_kcal="350.5")
    workout_sessions.update_session_meta(240, payload, identity=IDENTITY)
    sql, params = _update(executed)
    assert sql.startswith("UPDATE activities SET name = %s, calories_kcal = %s, calories_source = %s")
    assert params == ("Costas", Decimal("350.5"), CALORIES_SOURCE_MANUAL, 240)

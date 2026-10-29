"""HRV somente rMSSD + wellness do /dashboard lido do cache (fonte unica).

Sem banco real e sem rede: a conexao e um duble que grava os statements, o
cliente do Intervals.icu e trocado por funcoes locais.

Cobre:
- `upsert_wellness` nao usa `hrvSDNN` como HRV (sem rMSSD -> NULL);
- `_refresh_wellness_cache`: throttle, sem credencial, falha do Intervals.icu,
  falha de banco -> nunca levanta; sucesso grava pelo upsert compartilhado
  com janela curta; a chave nunca aparece no log;
- `_cached_wellness_trend`: ausente = None (nao 0);
- GET /dashboard devolve o wellness do cache mesmo com refresh falho.

Rodar: cd api && python -m pytest tests/unit/test_intervals_wellness_fonte_unica.py
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import intervals_integration as ii
from app import intervals_store
from app.db import DataApiError
from app.intervals_client import IntervalsClientError
from app.intervals_store import IntervalsCredentialsError
from app.security import Identity, get_current_identity
from tests.support.fake_db import LoopingExecuteMany

API_KEY = "chave-super-secreta-do-intervals-1234567890"


class FakeCursor(LoopingExecuteMany):
    def __init__(self, conn: "FakeConn"):
        self.conn = conn
        self._row: dict | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.conn.statements.append((text, tuple(params or ())))
        if "COUNT(*) AS recent" in text:
            self._row = {"recent": self.conn.recent}
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row else []


class FakeConn:
    def __init__(self, recent: int = 0):
        self.recent = recent
        self.statements: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def cursor(self):
        return FakeCursor(self)

    def wellness_upserts(self):
        return [params for text, params in self.statements if text.startswith("INSERT INTO intervals_wellness")]


# ---------------------------------------------------------------------------
# HRV = somente rMSSD
# ---------------------------------------------------------------------------

# Posicao de hrv_ms na tupla de parametros do _UPSERT_WELLNESS_SQL.
HRV_PARAM = 8


def test_upsert_wellness_nao_usa_sdnn_como_hrv():
    conn = FakeConn()
    intervals_store.upsert_wellness(
        conn,
        7,
        [
            {"id": "2026-10-02", "hrvSDNN": 24, "hrv_sdnn": 24},
            {"id": "2026-10-01", "hrv": 42, "hrvSDNN": 24},
        ],
    )
    rows = conn.wellness_upserts()
    assert rows[0][0] == "2026-10-02" and rows[0][HRV_PARAM] is None
    assert rows[1][0] == "2026-10-01" and rows[1][HRV_PARAM] == 42.0


# ---------------------------------------------------------------------------
# Refresh do cache ao abrir /intervals-icu
# ---------------------------------------------------------------------------


@pytest.fixture
def refresh_env(monkeypatch):
    env = {"conns": [], "fetch_calls": [], "recent": 0, "fetch": None, "creds": None, "conn_error": None}

    def fake_get_conn():
        if env["conn_error"] is not None:
            raise env["conn_error"]
        conn = FakeConn(recent=env["recent"])
        env["conns"].append(conn)
        return conn

    def fake_credentials(conn, user_id):
        if env["creds"] is not None:
            raise env["creds"]
        return "i12345", API_KEY

    def fake_fetch_wellness(athlete_id, api_key, oldest, newest):
        env["fetch_calls"].append((athlete_id, oldest, newest))
        if env["fetch"] is not None:
            raise env["fetch"]
        return [{"id": newest.isoformat(), "hrv": 42, "hrvSDNN": 24, "ctl": 50, "atl": 40}]

    monkeypatch.setattr(ii, "get_conn", fake_get_conn)
    monkeypatch.setattr(intervals_store, "get_user_intervals_credentials", fake_credentials)
    monkeypatch.setattr(ii.intervals_client, "fetch_wellness", fake_fetch_wellness)
    return env


def _all_upserts(env):
    return [row for conn in env["conns"] for row in conn.wellness_upserts()]


def test_refresh_grava_janela_curta_pelo_upsert_compartilhado(refresh_env, caplog):
    caplog.set_level(logging.DEBUG)
    assert ii._refresh_wellness_cache(7, 90) == "refreshed"

    (_, oldest, newest), = refresh_env["fetch_calls"]
    assert newest == date.today()
    assert newest - oldest == timedelta(days=ii.WELLNESS_REFRESH_MAX_DAYS)
    upserts = _all_upserts(refresh_env)
    assert len(upserts) == 1 and upserts[0][1] == 7 and upserts[0][HRV_PARAM] == 42.0
    assert API_KEY not in caplog.text


def test_refresh_janela_menor_que_o_maximo_respeita_days(refresh_env):
    ii._refresh_wellness_cache(7, 3)
    (_, oldest, newest), = refresh_env["fetch_calls"]
    assert newest - oldest == timedelta(days=3)


def test_refresh_throttled_nao_chama_intervals(refresh_env):
    refresh_env["recent"] = 1
    assert ii._refresh_wellness_cache(7, 90) == "throttled"
    assert refresh_env["fetch_calls"] == []
    assert _all_upserts(refresh_env) == []


def test_refresh_sem_credencial_nao_quebra(refresh_env):
    refresh_env["creds"] = IntervalsCredentialsError("Configure...", "not_configured")
    assert ii._refresh_wellness_cache(7, 90) == "not_configured"
    assert refresh_env["fetch_calls"] == []


def test_refresh_falha_do_intervals_serve_cache(refresh_env, caplog):
    refresh_env["fetch"] = IntervalsClientError("Intervals.icu respondeu 429", status_code=429)
    assert ii._refresh_wellness_cache(7, 90) == "failed"
    assert _all_upserts(refresh_env) == []
    assert "intervals_wellness_refresh_failed" in [getattr(r, "event", None) for r in caplog.records]
    assert API_KEY not in caplog.text


def test_refresh_falha_de_banco_nao_levanta(refresh_env):
    refresh_env["conn_error"] = DataApiError("cluster pausado")
    assert ii._refresh_wellness_cache(7, 90) == "failed"


# ---------------------------------------------------------------------------
# Leitura do cache
# ---------------------------------------------------------------------------


def test_cached_wellness_trend_ausente_e_none(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params=None):
        captured["sql"] = " ".join(sql.split())
        captured["params"] = params
        return [
            {"wellness_date": "2026-10-02", "fitness_ctl": 50.0, "fatigue_atl": 40.0, "form_tsb": None,
             "resting_hr_bpm": None, "hrv_ms": None, "ramp_rate": None},
            {"wellness_date": "2026-10-01", "fitness_ctl": None, "fatigue_atl": None, "form_tsb": None,
             "resting_hr_bpm": 52, "hrv_ms": 42, "ramp_rate": 1.5},
        ]

    monkeypatch.setattr(ii, "_fetch_all", fake_fetch_all)
    trend = ii._cached_wellness_trend(7, 90)

    assert "FROM intervals_wellness" in captured["sql"]
    assert captured["params"] == (7, 90)
    assert trend == [
        {"date": "2026-10-01", "fitness": None, "fatigue": None, "form": None,
         "restingHr": 52.0, "restingHrSource": "intervals", "hrv": 42.0, "hrvSource": "intervals", "rampRate": 1.5},
        {"date": "2026-10-02", "fitness": 50.0, "fatigue": 40.0, "form": 10.0,
         "restingHr": None, "restingHrSource": "intervals", "hrv": None, "hrvSource": "intervals", "rampRate": None},
    ]


# ---------------------------------------------------------------------------
# Rota
# ---------------------------------------------------------------------------


def test_dashboard_le_wellness_do_cache_mesmo_com_refresh_falho(monkeypatch):
    cached = [{"date": "2026-10-02", "fitness": 50.0, "fatigue": 40.0, "form": 10.0,
               "restingHr": None, "hrv": 42.0, "rampRate": None}]
    calls = []
    monkeypatch.setattr(ii, "_refresh_wellness_cache", lambda user_id, days: calls.append((user_id, days)) or "failed")
    monkeypatch.setattr(
        ii, "_fetch_live_activities", lambda user_id, days: ([], (date.today() - timedelta(days=days), date.today()))
    )
    monkeypatch.setattr(ii, "_cached_wellness_trend", lambda user_id, days: cached)
    # M53: a gravacao das atividades tem teste proprio (test_m53_*).
    monkeypatch.setattr(ii, "_store_live_activities", lambda user_id, items, today: "unchanged")

    app = FastAPI()
    app.include_router(ii.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=7, role="regular", source="session")
    response = TestClient(app).get("/integrations/intervals/dashboard?days=30")

    assert response.status_code == 200
    body = response.json()
    assert calls == [(7, 30)]
    assert body["wellness_trend"] == cached
    assert body["wellness_count"] == 1
    assert body["wellness_source"] == "cache"
    assert body["wellness_refresh"] == "failed"

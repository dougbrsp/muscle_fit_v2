"""M53 - GET /integrations/intervals/dashboard grava no cache as atividades
que ja buscou ao vivo.

Sem banco e sem rede: a conexao e um duble que grava os statements; o
Intervals.icu e trocado por funcoes locais.

Cobre:
- recorte do que e gravado (faltando no cache + ultimos dias; nada alem do
  teto de dias), com data/id saindo dos MESMOS helpers do upsert;
- `_store_live_activities`: throttle, nada a gravar, sucesso (upsert
  compartilhado + associacao de FC na MESMA conexao), falha de banco e de
  upsert -> nunca levanta;
- a rota segue respondendo 200 com o dado ao vivo quando a gravacao falha.

Rodar: cd api && python -m pytest tests/unit/test_m53_intervals_activities_cache.py
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import intervals_integration as ii
from app import intervals_store, intervals_sync
from app.db import DataApiError
from app.security import Identity, get_current_identity
from tests.support.fake_db import LoopingExecuteMany

HOJE = date(2026, 10, 3)


def _atividade(external_id: str, dia: date, sport: str = "Run") -> dict:
    return {
        "id": external_id,
        "start_date_local": f"{dia.isoformat()}T07:00:00",
        "name": f"Atividade {external_id}",
        "type": sport,
        "moving_time": 1800,
    }


class FakeCursor(LoopingExecuteMany):
    def __init__(self, conn: "FakeConn"):
        self.conn = conn
        self._rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.conn.statements.append((text, tuple(params or ())))
        if "COUNT(*) AS recent" in text:
            self._rows = [{"recent": self.conn.recent}]
        elif text.startswith("SELECT external_id FROM intervals_activities"):
            self._rows = [{"external_id": value} for value in self.conn.cached_ids]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, recent: int = 0, cached_ids=()):
        self.recent = recent
        self.cached_ids = list(cached_ids)
        self.statements: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def cursor(self):
        return FakeCursor(self)


# ---------------------------------------------------------------------------
# Recorte
# ---------------------------------------------------------------------------


def test_recorte_grava_o_que_falta_e_os_ultimos_dias():
    recente_no_cache = _atividade("recente", HOJE - timedelta(days=2))
    antiga_no_cache = _atividade("antiga-cache", HOJE - timedelta(days=20))
    antiga_faltando = _atividade("antiga-falta", HOJE - timedelta(days=20))
    alem_do_teto = _atividade("teto", HOJE - timedelta(days=ii.ACTIVITIES_CACHE_MAX_DAYS + 1))
    sem_data = {"id": "sem-data", "name": "?"}

    escolhidas = ii._activities_to_store(
        [recente_no_cache, antiga_no_cache, antiga_faltando, alem_do_teto, sem_data],
        {"recente", "antiga-cache", "teto"},
        HOJE,
    )
    assert [item["id"] for item in escolhidas] == ["recente", "antiga-falta"]


def test_recorte_limites_inclusivos():
    no_limite_reescrita = _atividade("r", HOJE - timedelta(days=ii.ACTIVITIES_CACHE_REWRITE_DAYS))
    no_teto = _atividade("t", HOJE - timedelta(days=ii.ACTIVITIES_CACHE_MAX_DAYS))
    escolhidas = ii._activities_to_store([no_limite_reescrita, no_teto], {"r"}, HOJE)
    assert [item["id"] for item in escolhidas] == ["r", "t"]


def test_data_do_recorte_e_a_mesma_do_upsert():
    # Fonte unica: o upsert le a data pelo mesmo helper.
    item = {"id": "x", "startDate": "2026-10-01T23:59:00Z"}
    assert intervals_store.activity_date_of(item) == "2026-10-01"


# ---------------------------------------------------------------------------
# Gravacao
# ---------------------------------------------------------------------------


@pytest.fixture
def store_env(monkeypatch):
    env = {"conns": [], "recent": 0, "cached": [], "conn_error": None, "links": []}

    def fake_get_conn():
        if env["conn_error"] is not None:
            raise env["conn_error"]
        conn = FakeConn(recent=env["recent"], cached_ids=env["cached"])
        env["conns"].append(conn)
        return conn

    def fake_link(conn, user_id, oldest, newest, result):
        env["links"].append((conn, user_id, oldest, newest))
        result.hr_matched += 1

    monkeypatch.setattr(ii, "get_conn", fake_get_conn)
    monkeypatch.setattr(intervals_sync, "link_heart_rate", fake_link)
    return env


def _upserts(env):
    return [
        params
        for conn in env["conns"]
        for text, params in conn.statements
        if text.startswith("INSERT INTO intervals_activities")
    ]


def test_grava_pelo_upsert_compartilhado_e_associa_fc_na_mesma_conexao(store_env, caplog):
    caplog.set_level(logging.INFO)
    atividades = [
        _atividade("a1", HOJE - timedelta(days=1), "WeightTraining"),
        _atividade("a2", HOJE - timedelta(days=5)),
    ]
    assert ii._store_live_activities(7, atividades, HOJE) == "stored"

    upserts = _upserts(store_env)
    assert sorted(params[1] for params in upserts) == ["a1", "a2"]
    assert all(params[0] == 7 for params in upserts), "user_id do upsert e SEMPRE o do chamador"

    (conn, user_id, oldest, newest), = store_env["links"]
    assert user_id == 7
    assert (oldest, newest) == (HOJE - timedelta(days=5), HOJE - timedelta(days=1))
    # Upsert e associacao na MESMA transacao (mesma conexao), como no job.
    assert any(text.startswith("INSERT INTO intervals_activities") for text, _ in conn.statements)
    assert "intervals_activities_cached" in [getattr(r, "event", None) for r in caplog.records]


def test_throttle_nao_grava(store_env):
    store_env["recent"] = 1
    assert ii._store_live_activities(7, [_atividade("a1", HOJE)], HOJE) == "throttled"
    assert _upserts(store_env) == [] and store_env["links"] == []


def test_tudo_ja_no_cache_e_antigo_nao_grava(store_env):
    store_env["cached"] = ["a1"]
    antiga = _atividade("a1", HOJE - timedelta(days=30))
    assert ii._store_live_activities(7, [antiga], HOJE) == "unchanged"
    assert _upserts(store_env) == [] and store_env["links"] == []


def test_lista_vazia_nao_grava(store_env):
    assert ii._store_live_activities(7, [], HOJE) == "unchanged"


def test_falha_de_banco_nao_levanta(store_env, caplog):
    store_env["conn_error"] = DataApiError("cluster pausado")
    assert ii._store_live_activities(7, [_atividade("a1", HOJE)], HOJE) == "failed"
    assert "intervals_activities_cache_failed" in [getattr(r, "event", None) for r in caplog.records]


def test_falha_no_upsert_nao_levanta(store_env, monkeypatch):
    def boom(conn, user_id, items):
        raise RuntimeError("Deadlock found")

    monkeypatch.setattr(intervals_store, "upsert_activities", boom)
    assert ii._store_live_activities(7, [_atividade("a1", HOJE)], HOJE) == "failed"
    assert store_env["links"] == []


# ---------------------------------------------------------------------------
# Rota
# ---------------------------------------------------------------------------


def _client(user_id: int = 7) -> TestClient:
    app = FastAPI()
    app.include_router(ii.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app)


def test_rota_grava_com_o_usuario_autenticado(monkeypatch):
    chamadas = []
    atividades = [_atividade("a1", HOJE)]
    monkeypatch.setattr(ii, "_refresh_wellness_cache", lambda user_id, days: "throttled")
    monkeypatch.setattr(ii, "_fetch_live_activities", lambda user_id, days: (atividades, (HOJE - timedelta(days=days), HOJE)))
    monkeypatch.setattr(
        ii, "_store_live_activities", lambda user_id, items, today: chamadas.append((user_id, items, today)) or "stored"
    )
    monkeypatch.setattr(ii, "_cached_wellness_trend", lambda user_id, days: [])

    response = _client(42).get("/integrations/intervals/dashboard?days=30")

    assert response.status_code == 200
    assert response.json()["activities_cache"] == "stored"
    assert chamadas == [(42, atividades, HOJE)]


def test_rota_segue_com_dado_ao_vivo_quando_a_gravacao_falha(monkeypatch):
    atividades = [_atividade("a1", HOJE)]
    monkeypatch.setattr(ii, "_refresh_wellness_cache", lambda user_id, days: "throttled")
    monkeypatch.setattr(ii, "_fetch_live_activities", lambda user_id, days: (atividades, (HOJE - timedelta(days=days), HOJE)))
    monkeypatch.setattr(ii, "_cached_wellness_trend", lambda user_id, days: [])

    def conn_quebrada():
        raise DataApiError("cluster pausado")

    monkeypatch.setattr(ii, "get_conn", conn_quebrada)

    response = _client().get("/integrations/intervals/dashboard?days=30")

    assert response.status_code == 200
    body = response.json()
    assert body["activities_cache"] == "failed"
    assert body["activity_count"] == 1
    assert body["recent_activities"][0]["name"] == "Atividade a1"


def test_sem_credencial_nao_chega_a_gravar(monkeypatch):
    """Sem Intervals configurado a busca ao vivo ja responde 400 (como antes)
    e nada e gravado."""
    from fastapi import HTTPException

    gravou = []

    def sem_credencial(user_id, days):
        raise HTTPException(status_code=400, detail="Configure sua chave")

    monkeypatch.setattr(ii, "_refresh_wellness_cache", lambda user_id, days: "not_configured")
    monkeypatch.setattr(ii, "_fetch_live_activities", sem_credencial)
    monkeypatch.setattr(ii, "_store_live_activities", lambda *a: gravou.append(a) or "stored")

    response = _client().get("/integrations/intervals/dashboard?days=30")
    assert response.status_code == 400
    assert gravou == []

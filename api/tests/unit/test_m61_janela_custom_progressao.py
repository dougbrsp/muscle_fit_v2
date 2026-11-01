"""Unit + contrato: janela `custom` de `/dashboard/progressao` (M61).

Antes, a tela mandava so o NUMERO de dias da faixa escolhida e a janela
sempre terminava hoje. Agora as tres rotas v2 da tela
(`dashboard-progression`, `dashboard-progression-trend` e
`dashboard-progression-by-muscle-group`) aceitam `date_from`/`date_to`
opcionais; sem eles o contrato por `days` continua igual.

Sem banco: o "hoje" do usuario e as leituras SQL sao substituidos por duble.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import analytics, security
from app.intelligence.muscle_group_progression import comparison_windows, range_comparison_windows

HOJE = date(2026, 10, 3)


# ---------------------------------------------------------------------------
# Parte pura
# ---------------------------------------------------------------------------


def test_janela_custom_no_passado_compara_com_o_periodo_anterior_de_mesmo_tamanho():
    w = range_comparison_windows(date(2026, 8, 1), date(2026, 8, 10))
    assert w == {
        "current_from": date(2026, 8, 1),
        "current_to": date(2026, 8, 10),
        "previous_from": date(2026, 7, 22),
        "previous_to": date(2026, 7, 31),
    }


def test_janela_custom_de_um_dia():
    w = range_comparison_windows(date(2026, 8, 1), date(2026, 8, 1))
    assert w["previous_from"] == w["previous_to"] == date(2026, 7, 31)


def test_rolling_e_o_caso_particular_terminando_hoje():
    for days in (1, 7, 28, 180):
        assert comparison_windows(HOJE, days) == range_comparison_windows(
            comparison_windows(HOJE, days)["current_from"], HOJE
        )


def test_janela_custom_invertida_e_recusada():
    with pytest.raises(ValueError):
        range_comparison_windows(date(2026, 8, 10), date(2026, 8, 1))


def test_series_do_exercicio_filtram_pelas_duas_datas():
    captured = {}

    def fake_fetch_all(sql, params=None):
        captured["sql"], captured["params"] = sql, params
        return []

    original = analytics._fetch_all
    analytics._fetch_all = fake_fetch_all
    try:
        analytics._fetch_exercise_set_rows(
            7, 10, mapping_id=42, date_range=(date(2026, 8, 1), date(2026, 8, 10))
        )
    finally:
        analytics._fetch_all = original

    assert "CURDATE()" not in captured["sql"]
    assert "a.local_date >= %s" in captured["sql"] and "a.local_date <= %s" in captured["sql"]
    assert captured["params"] == (date(2026, 8, 1), date(2026, 8, 10), 7, 42)


def test_series_do_exercicio_sem_faixa_mantem_o_recorte_por_days():
    captured = {}

    def fake_fetch_all(sql, params=None):
        captured["sql"], captured["params"] = sql, params
        return []

    original = analytics._fetch_all, analytics._user_today
    analytics._fetch_all = fake_fetch_all
    analytics._user_today = lambda user_id: HOJE
    try:
        analytics._fetch_exercise_set_rows(7, 90, mapping_id=42)
    finally:
        analytics._fetch_all, analytics._user_today = original

    # M76 Lote 2b: os `days` contam do "hoje" do usuario, nao de CURDATE().
    assert "CURDATE()" not in captured["sql"]
    assert "a.local_date >= %s" in captured["sql"]
    assert captured["params"] == (HOJE - timedelta(days=90), 7, 42)


# ---------------------------------------------------------------------------
# Contrato das rotas
# ---------------------------------------------------------------------------


@pytest.fixture
def chamadas(monkeypatch):
    log: dict = {"set_rows": [], "windows": [], "exposures": []}

    def fake_set_rows(user_id, days, exercise_name=None, mapping_id=None, date_range=None, training_goal=None):
        log["set_rows"].append({"days": days, "date_range": date_range})
        return []

    def fake_exposures(user_id, canonical, days, date_range, limit, training_goal=None):
        # veredito da tela = regra da home (exposure_service), desde 2026-10-06
        log["exposures"].append({"days": days, "date_range": date_range})
        return []

    def fake_tonnage(user_id, windows, load_convention_expr=None):
        log["windows"].append(windows)
        return []

    monkeypatch.setattr(analytics, "_user_today", lambda user_id: HOJE)
    monkeypatch.setattr(analytics, "_fetch_exercise_set_rows", fake_set_rows)
    monkeypatch.setattr(analytics, "_progression_exposures", fake_exposures)
    monkeypatch.setattr(analytics, "_fetch_group_tonnage_rows", fake_tonnage)
    monkeypatch.setattr(analytics, "_fetch_group_e1rm_rows", lambda *a, **k: [])
    monkeypatch.setattr(analytics, "_fetch_exercise_group_weights", lambda *a, **k: {})
    monkeypatch.setattr(analytics, "_exercise_progression_items", lambda *a, **k: [])
    monkeypatch.setattr(analytics, "_load_convention_expr", lambda: "NULL")
    monkeypatch.setattr(analytics, "_group_phases", lambda user_id, *_: {})
    return log


@pytest.fixture
def client(chamadas):
    app = FastAPI()
    app.include_router(analytics.router)
    app.dependency_overrides[security.get_current_identity] = lambda: security.Identity(
        user_id=7, role="regular", source="session"
    )
    return TestClient(app)


def test_contrato_janela_custom_respeita_inicio_e_fim_nas_tres_rotas(client, chamadas):
    faixa = "date_from=2026-08-01&date_to=2026-08-10&days=10"

    prog = client.get(f"/analytics/dashboard-progression?mapping_id=42&{faixa}")
    trend = client.get(f"/analytics/dashboard-progression-trend?mapping_id=42&{faixa}")
    grupos = client.get(f"/analytics/dashboard-progression-by-muscle-group?{faixa}")

    assert prog.status_code == trend.status_code == grupos.status_code == 200
    for body in (prog.json(), trend.json()):
        assert body["days"] == 10
        assert body["date_from"] == "2026-08-01"
        assert body["date_to"] == "2026-08-10"
    # tabela: series por sessao; veredito: exposicoes (regra da home) - as
    # duas com a MESMA faixa custom
    assert chamadas["set_rows"] == [{"days": 10, "date_range": (date(2026, 8, 1), date(2026, 8, 10))}]
    assert chamadas["exposures"] == [{"days": 10, "date_range": (date(2026, 8, 1), date(2026, 8, 10))}]

    body = grupos.json()
    assert body["days"] == 10
    assert (body["current_from"], body["current_to"]) == ("2026-08-01", "2026-08-10")
    assert (body["previous_from"], body["previous_to"]) == ("2026-07-22", "2026-07-31")


def test_contrato_sem_datas_continua_por_days_terminando_hoje(client, chamadas):
    prog = client.get("/analytics/dashboard-progression?mapping_id=42&days=28")
    grupos = client.get("/analytics/dashboard-progression-by-muscle-group?days=28")

    assert prog.status_code == grupos.status_code == 200
    assert prog.json()["days"] == 28
    assert prog.json()["date_from"] is None and prog.json()["date_to"] is None
    assert chamadas["set_rows"] == [{"days": 28, "date_range": None}]
    assert grupos.json()["current_to"] == HOJE.isoformat()
    assert grupos.json()["current_from"] == "2026-09-06"


@pytest.mark.parametrize(
    "faixa",
    [
        "date_from=2026-08-01",  # so uma das datas
        "date_to=2026-08-01",
        "date_from=2026-08-10&date_to=2026-08-01",  # fim antes do inicio
        "date_from=2026-10-01&date_to=2026-10-04",  # fim no futuro (fuso do usuario)
        "date_from=2025-01-01&date_to=2026-01-01",  # 366 dias > MAX_WINDOW_DAYS
    ],
)
def test_faixa_invalida_e_400_nas_tres_rotas(client, faixa):
    for url in (
        f"/analytics/dashboard-progression?mapping_id=42&{faixa}",
        f"/analytics/dashboard-progression-trend?mapping_id=42&{faixa}",
        f"/analytics/dashboard-progression-by-muscle-group?{faixa}",
    ):
        assert client.get(url).status_code == 400, url


def test_fim_hoje_e_aceito(client):
    resp = client.get("/analytics/dashboard-progression-by-muscle-group?date_from=2026-09-04&date_to=2026-10-03")
    assert resp.status_code == 200
    assert resp.json()["days"] == 30
    assert resp.json()["current_to"] == HOJE.isoformat()

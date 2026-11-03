"""Rota `GET /nutrition/dashboard` (`app/nutrition_dashboard.py`, N5) sem banco
real: aderência por dia e por semana sobre os totais gravados, dias incompletos,
peso/%G/massa muscular, projeção do `phases.project_goal` com a premissa
`is_minimum_estimate`, TDEE adaptativo que só sugere, estados vazios (sem plano,
sem pesagem, poucos dias) e acesso (dono; terceiro 403; admin/PT qualquer aluno).

Banco em memória: `tests/support/nutrition_dashboard_fake.py`."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_dashboard as dash_mod
from app import nutrition_meals as meals_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition import dashboard as dash
from app.nutrition_schema import diary_tables_missing_error
from app.security import Identity, get_current_identity
from tests.support.nutrition_dashboard_fake import FakeDashboardDB

HOJE = date(2026, 10, 17)
ALUNO, OUTRO, ADMIN = 2, 4, 1
INICIO_DO_PLANO = date(2026, 9, 1)
ARROZ = {"energy_kcal": 128.26, "carbohydrates_g": 28.06, "protein_g": 2.52, "total_fat_g": 0.23, "fiber_g": 1.56,
         "sodium_mg": 1.20}


@pytest.fixture
def db(monkeypatch):
    banco = FakeDashboardDB()
    banco.arroz = banco.add_food("Arroz, tipo 1, cozido", **ARROZ)
    for modulo in (dash_mod, meals_mod):
        monkeypatch.setattr(modulo, "get_conn", banco.conn)
    monkeypatch.setattr(meals_mod, "require_diary_tables", lambda cur: None)
    monkeypatch.setattr(meals_mod, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(dash_mod, "require_diary_tables", lambda cur: None)
    monkeypatch.setattr(dash_mod, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(dash_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    return banco


def _publicar(banco, strategy="cutting", **settings):
    return banco.add_plan(ALUNO, status="published", strategy=strategy, effective_from=INICIO_DO_PLANO,
                          published_at=datetime(2026, 9, 1, 9, 0, 0), **settings)


def _client(role: str = "regular", user_id: int = ALUNO) -> TestClient:
    app = FastAPI()
    app.include_router(dash_mod.router)
    app.include_router(meals_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _refeicao(cliente, dia: date, food_id, gramas: float = 500):
    resposta = cliente.post("/nutrition/meals", json={
        "local_date": dia.isoformat(), "meal_slot": "lunch",
        "items": [{"food_id": food_id, "quantity": gramas, "unit": "g"}]})
    assert resposta.status_code == 201, resposta.text


def _codigos(itens) -> list[str]:
    return [item["code"] for item in itens]


def test_o_router_esta_no_app_real_da_api_core():
    from app.main import app

    assert "/nutrition/dashboard" in app.openapi()["paths"]
    assert TestClient(app, raise_server_exceptions=False).get("/nutrition/dashboard").status_code in (401, 403)


def test_tabelas_ausentes_respondem_503(db, monkeypatch):
    def _sem(cur):
        raise diary_tables_missing_error()

    monkeypatch.setattr(dash_mod, "require_diary_tables", _sem)
    assert _client().get("/nutrition/dashboard").status_code == 503


def test_sem_nenhum_dado_devolve_estados_vazios_e_nao_quebra(db):
    resposta = _client().get("/nutrition/dashboard")
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["window_days"] == dash.DASHBOARD_DEFAULT_DAYS and len(corpo["days"]) == dash.DASHBOARD_DEFAULT_DAYS
    assert corpo["plan"] is None and corpo["projection"] is None
    assert {dash.EMPTY_NO_PLAN, dash.EMPTY_NO_WEIGHT, dash.EMPTY_NO_BIOIMPEDANCE,
            dash.EMPTY_FEW_LOGGED_DAYS} <= set(_codigos(corpo["empty_states"]))
    assert all(item["label"] != item["code"] for item in corpo["empty_states"])
    assert corpo["body"]["points"] == [] and corpo["adaptive_tdee"]["applied"] is False
    assert corpo["adherence"]["logged_days"] == 0 and corpo["adherence"]["incomplete_days"] == []


def test_janela_pedida_e_limitada(db):
    cliente = _client()
    assert len(cliente.get("/nutrition/dashboard?days=7").json()["days"]) == 7
    assert cliente.get("/nutrition/dashboard?days=999").json()["window_days"] == dash.DASHBOARD_MAX_DAYS
    corpo = cliente.get("/nutrition/dashboard").json()
    assert corpo["end"] == HOJE.isoformat() and corpo["start"] == (HOJE - timedelta(days=27)).isoformat()


def test_com_plano_so_hoje_tem_meta_e_os_demais_dias_sao_avisados(db):
    _publicar(db, strategy="maintenance")
    db.add_body(ALUNO, HOJE - timedelta(days=3), weight=80)
    cliente = _client()
    _refeicao(cliente, HOJE, db.arroz, 500)
    corpo = cliente.get("/nutrition/dashboard").json()
    hoje = corpo["days"][-1]
    assert hoje["local_date"] == HOJE.isoformat() and hoje["has_target"] and hoje["logged"]
    assert hoje["macros"]["energy_kcal"]["within_target"] == pytest.approx(5 * 128.26)
    assert hoje["macros"]["energy_kcal"]["to_consume"] > 0
    assert corpo["adherence"]["days_with_target"] == 1 and corpo["adherence"]["logged_days"] == 1
    assert dash.EMPTY_DAYS_WITHOUT_TARGET in _codigos(corpo["empty_states"])
    assert corpo["plan"]["strategy"] == "maintenance" and corpo["plan"]["effective_from"] == INICIO_DO_PLANO.isoformat()
    ultima = corpo["weeks"][-1]
    assert ultima["report"]["logged_days"] == [HOJE.isoformat()]
    assert all(set(item) == {"code", "label"} for item in ultima["report"]["limitations"])


def test_dia_incompleto_aparece_na_lista(db):
    _publicar(db, strategy="maintenance")
    sal = db.add_food("Sal, grosso", sodium_mg=39943.20)
    cliente = _client()
    _refeicao(cliente, HOJE - timedelta(days=1), sal, 5)
    corpo = cliente.get("/nutrition/dashboard").json()
    assert (HOJE - timedelta(days=1)).isoformat() in corpo["adherence"]["incomplete_days"]
    ontem = corpo["days"][-2]
    assert ontem["intake_incomplete"] and not ontem["logged"]


def test_corpo_projecao_e_tdee_adaptativo_so_sugere(db):
    _publicar(db, strategy="cutting", goal_weight_kg=Decimal("75"))
    for indice in range(28):
        dia = HOJE - timedelta(days=27 - indice)
        db.add_body(ALUNO, dia, weight=round(80.0 - 0.05 * indice, 2), body_fat=20.0 if indice % 7 == 0 else None,
                    muscle=36.0 if indice % 7 == 0 else None)
    corpo = _client().get("/nutrition/dashboard").json()
    pontos = corpo["body"]["points"]
    assert len(pontos) == 28 and pontos[0]["weight_trend_kg"] == pontos[0]["weight_kg"]
    assert pontos[0]["body_fat_percent"] == 20.0 and pontos[0]["muscle_mass_kg"] == 36.0
    assert corpo["body"]["weight_trend"]["weekly_change_kg"] == pytest.approx(-0.35, abs=0.01)
    projecao = corpo["projection"]
    assert projecao["goal_kind"] == "weight" and projecao["status"] == "projected"
    assert projecao["is_minimum_estimate"] is False and projecao["weeks"] > 0
    assert projecao["progress_percent"] is not None
    assert dash.EMPTY_NO_WEIGHT not in _codigos(corpo["empty_states"])
    adaptativo = corpo["adaptive_tdee"]
    assert adaptativo["applied"] is False and adaptativo["observed"]["kcal"] is None  # sem diário no período
    assert adaptativo["observed"]["limitation_items"]


def test_alvo_de_gordura_traz_a_premissa_de_estimativa_minima(db):
    _publicar(db, strategy="cutting", goal_body_fat_percent=Decimal("12"))
    db.add_body(ALUNO, HOJE - timedelta(days=2), weight=80.0, body_fat=20.0, muscle=36.0)
    projecao = _client().get("/nutrition/dashboard").json()["projection"]
    assert projecao["goal_kind"] == "body_fat" and projecao["status"] == "projected"
    assert projecao["is_minimum_estimate"] is True
    assert "projection_lean_mass_constant" in _codigos(projecao["reason_items"])


def test_plano_sem_alvo_avisa_e_nao_projeta(db):
    _publicar(db, strategy="cutting")
    corpo = _client().get("/nutrition/dashboard").json()
    assert corpo["projection"] is None and dash.EMPTY_NO_GOAL in _codigos(corpo["empty_states"])


def test_acesso_dono_terceiro_e_admin(db):
    assert _client(user_id=ALUNO).get("/nutrition/dashboard").status_code == 200
    assert _client(user_id=OUTRO).get(f"/nutrition/dashboard?user_id={ALUNO}").status_code == 403
    admin = _client(role="admin", user_id=ADMIN).get(f"/nutrition/dashboard?user_id={ALUNO}")
    assert admin.status_code == 200 and admin.json()["user"]["id"] == ALUNO

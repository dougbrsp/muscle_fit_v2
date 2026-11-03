"""Rotas `GET /nutrition/dashboard` e `GET /nutrition/today` (`app/nutrition_dashboard.py`, N5)
sem banco real: aderência sobre os totais gravados (hoje aberto fora da conta, "sem registro"
à parte, dia incompleto, antes do plano), peso/%G/massa muscular, projeção do
`phases.project_goal` (premissa `is_minimum_estimate`, alvo atingido, sequência de versões),
TDEE adaptativo que só sugere, estados vazios e acesso (dono, terceiro 403, admin/PT).

Banco em memória: `tests/support/nutrition_dashboard_fake.py`."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_dashboard as dash_mod
from app import nutrition_days as days_mod
from app import nutrition_meals as meals_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition import adaptive_tdee as atdee
from app.domain.nutrition import dashboard as dash
from app.nutrition_schema import diary_tables_missing_error
from app.security import Identity, get_current_identity
from tests.support.nutrition_dashboard_fake import FakeDashboardDB

HOJE = date(2026, 10, 17)  # sábado
ALUNO, OUTRO, ADMIN = 2, 4, 1
INICIO_DO_PLANO = date(2026, 9, 1)
ARROZ = {"energy_kcal": 128.26, "carbohydrates_g": 28.06, "protein_g": 2.52, "total_fat_g": 0.23, "fiber_g": 1.56,
         "sodium_mg": 1.20}


@pytest.fixture
def db(monkeypatch):
    banco = FakeDashboardDB()
    banco.arroz = banco.add_food("Arroz, tipo 1, cozido", **ARROZ)
    for modulo in (dash_mod, meals_mod, days_mod):
        monkeypatch.setattr(modulo, "get_conn", banco.conn)
    for modulo in (meals_mod, dash_mod, days_mod):
        monkeypatch.setattr(modulo, "require_diary_tables", lambda cur: None)
    for modulo in (dash_mod, days_mod):
        monkeypatch.setattr(modulo, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(meals_mod, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(dash_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(days_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    return banco


def _publicar(banco, strategy="cutting", inicio=INICIO_DO_PLANO, **settings):
    return banco.add_plan(ALUNO, status="published", strategy=strategy, effective_from=inicio,
                          published_at=datetime.combine(inicio, datetime.min.time()), **settings)


def _client(role: str = "regular", user_id: int = ALUNO) -> TestClient:
    app = FastAPI()
    for modulo in (dash_mod, meals_mod, days_mod):
        app.include_router(modulo.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _refeicao(cliente, dia: date, food_id, gramas: float = 500):
    resposta = cliente.post("/nutrition/meals", json={
        "local_date": dia.isoformat(), "meal_slot": "lunch",
        "items": [{"food_id": food_id, "quantity": gramas, "unit": "g"}]})
    assert resposta.status_code == 201, resposta.text


def _codigos(itens) -> list[str]:
    return [item["code"] for item in itens]


def _energia(linha_dia: dict) -> dict:
    return linha_dia["macros"]["energy_kcal"]


# ---------------------------------------------------------------------------
# Infra
# ---------------------------------------------------------------------------


def test_os_routers_estao_no_app_real_da_api_core():
    from app.main import app

    caminhos = app.openapi()["paths"]
    assert "/nutrition/dashboard" in caminhos and "/nutrition/today" in caminhos
    cliente = TestClient(app, raise_server_exceptions=False)
    assert cliente.get("/nutrition/dashboard").status_code in (401, 403)
    assert cliente.get("/nutrition/today").status_code in (401, 403)


def test_tabelas_ausentes_respondem_503(db, monkeypatch):
    def _sem(cur):
        raise diary_tables_missing_error()

    monkeypatch.setattr(dash_mod, "require_diary_tables", _sem)
    cliente = _client()
    assert cliente.get("/nutrition/dashboard").status_code == 503
    assert cliente.get("/nutrition/today").status_code == 503


def test_o_sql_das_refeicoes_nao_traz_texto_livre():
    assert "description_raw" not in dash_mod.MEAL_TOTALS_SQL and "notes" not in dash_mod.MEAL_TOTALS_SQL
    assert "totals_incomplete_json" in dash_mod.MEAL_TOTALS_SQL and "local_date" in dash_mod.MEAL_TOTALS_SQL


def test_janela_pedida_e_limitada_e_valor_invalido_e_recusado(db):
    cliente = _client()
    assert len(cliente.get("/nutrition/dashboard?days=7").json()["days"]) == 7
    assert cliente.get("/nutrition/dashboard?days=999").json()["window_days"] == dash.DASHBOARD_MAX_DAYS
    assert cliente.get("/nutrition/dashboard?days=1").json()["window_days"] == dash.DASHBOARD_MIN_DAYS
    assert cliente.get("/nutrition/dashboard?days=abc").status_code == 422
    corpo = cliente.get("/nutrition/dashboard").json()
    assert corpo["window_days"] == dash.DASHBOARD_DEFAULT_DAYS and corpo["window_choices"] == list(dash.WINDOW_CHOICES_DAYS)
    assert corpo["end"] == HOJE.isoformat() and corpo["start"] == (HOJE - timedelta(days=27)).isoformat()


def test_sem_nenhum_dado_devolve_estados_vazios_e_nao_quebra(db):
    resposta = _client().get("/nutrition/dashboard")
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert len(corpo["days"]) == dash.DASHBOARD_DEFAULT_DAYS and corpo["plan"] is None and corpo["projection"] is None
    assert {dash.EMPTY_NO_PLAN, dash.EMPTY_NO_WEIGHT, dash.EMPTY_NO_BIOIMPEDANCE} <= set(_codigos(corpo["empty_states"]))
    assert dash.EMPTY_FEW_LOGGED_DAYS not in _codigos(corpo["empty_states"])  # sem plano o aviso é o do plano
    assert "relógio" in next(i["label"] for i in corpo["empty_states"] if i["code"] == dash.EMPTY_NO_WEIGHT)
    assert all(item["label"] != item["code"] for item in corpo["empty_states"])
    assert corpo["body"]["points"] == [] and corpo["adaptive_tdee"]["applied"] is False
    assert corpo["adherence"]["logged_days"] == 0 and corpo["adherence"]["incomplete_days"] == []
    assert {linha["state"] for linha in corpo["days"]} == {"out_of_plan"}


# ---------------------------------------------------------------------------
# Aderência: o que entra na conta
# ---------------------------------------------------------------------------


def test_aderencia_conta_dias_fechados_hoje_fica_fora_e_sem_registro_e_a_parte(db):
    _publicar(db, strategy="maintenance")
    db.add_body(ALUNO, HOJE - timedelta(days=3), weight=80)
    cliente = _client()
    _refeicao(cliente, HOJE, db.arroz, 500)
    _refeicao(cliente, HOJE - timedelta(days=2), db.arroz, 500)  # fechado, sem meta gravada (sem backfill)
    corpo = cliente.get("/nutrition/dashboard").json()
    hoje = corpo["days"][-1]
    assert hoje["local_date"] == HOJE.isoformat() and hoje["state"] == "open_today" and not hoje["logged"]
    assert _energia(hoje)["within_target"] is None  # hoje ainda não fechou
    adh = corpo["adherence"]
    assert adh["open_today"] is True and adh["logged_days"] == 1
    assert adh["no_record_days"] == adh["counted_days"] - 1
    assert adh["meal_days_without_target"] == 1  # só o dia COM refeição e sem meta
    assert dash.EMPTY_DAYS_WITHOUT_TARGET in _codigos(corpo["empty_states"])
    assert corpo["plan"]["strategy"] == "maintenance" and corpo["plan"]["effective_from"] == INICIO_DO_PLANO.isoformat()
    assert (ALUNO, HOJE - timedelta(days=2)) not in db.targets  # o dashboard não faz backfill
    ultima = corpo["weeks"][-1]
    assert HOJE.isoformat() not in ultima["report"]["logged_days"]
    assert all(set(item) == {"code", "label"} for item in ultima["report"]["limitations"])


def test_com_plano_e_poucos_dias_registrados_avisa(db):
    _publicar(db, strategy="maintenance")
    assert dash.EMPTY_FEW_LOGGED_DAYS in _codigos(_client().get("/nutrition/dashboard").json()["empty_states"])


def test_gasto_da_formula_ignora_a_meta_de_hoje(db):
    _publicar(db, strategy="maintenance")
    db.add_body(ALUNO, HOJE - timedelta(days=3), weight=80)
    corpo = _client().get("/nutrition/dashboard").json()  # só hoje tem meta gravada
    assert (ALUNO, HOJE) in db.targets
    assert corpo["adaptive_tdee"]["suggestion"]["formula_kcal"] is None


def test_dias_antes_do_plano_ficam_fora_da_conta(db):
    _publicar(db, strategy="maintenance", inicio=HOJE - timedelta(days=5))
    corpo = _client().get("/nutrition/dashboard").json()
    estados = [linha["state"] for linha in corpo["days"]]
    assert estados.count("out_of_plan") == dash.DASHBOARD_DEFAULT_DAYS - 6  # 5 fechados + hoje
    assert corpo["adherence"]["counted_days"] == 5 and corpo["adherence"]["no_record_days"] == 5


def test_dia_incompleto_aparece_na_lista_e_na_semana(db):
    _publicar(db, strategy="maintenance")
    sal = db.add_food("Sal, grosso", sodium_mg=39943.20)
    ontem = HOJE - timedelta(days=1)
    _refeicao(_client(), ontem, sal, 5)
    corpo = _client().get("/nutrition/dashboard").json()
    assert ontem.isoformat() in corpo["adherence"]["incomplete_days"]
    assert corpo["days"][-2]["intake_incomplete"] and not corpo["days"][-2]["logged"]
    assert ontem.isoformat() in corpo["weeks"][-1]["report"]["incomplete_days"]


def test_troca_de_plano_no_periodo_usa_a_meta_de_cada_dia(db):
    _publicar(db, strategy="maintenance", inicio=HOJE - timedelta(days=20))
    _publicar(db, strategy="cutting", inicio=HOJE - timedelta(days=3))
    corpo = _client().get("/nutrition/dashboard").json()
    assert corpo["plan"]["strategy"] == "cutting"
    assert sum(1 for linha in corpo["days"] if linha["state"] != "out_of_plan") == 21


# ---------------------------------------------------------------------------
# Dia e semana: meta sem refeição, excesso, kcal nulo
# ---------------------------------------------------------------------------


def test_today_e_days_com_meta_e_sem_refeicao_mostram_a_meta_toda_a_consumir(db):
    _publicar(db, strategy="maintenance")
    db.add_body(ALUNO, HOJE - timedelta(days=3), weight=80)
    cliente = _client()
    hoje = cliente.get("/nutrition/today")
    assert hoje.status_code == 200, hoje.text
    dia = cliente.get(f"/nutrition/days/{HOJE.isoformat()}").json()
    for corpo in (hoje.json(), dia):
        energia = corpo["balance"]["rows"][0]
        assert energia["field"] == "energy_kcal" and energia["target"] == pytest.approx(corpo["target"]["kcal"])
        assert (energia["within_target"], energia["excess"]) == (0.0, 0.0)
        assert energia["to_consume"] == pytest.approx(corpo["target"]["kcal"])
    assert hoje.json()["local_date"] == HOJE.isoformat() and hoje.json()["totals"] is None
    assert (ALUNO, HOJE) in db.targets and db.writes == [f"upsert {HOJE.isoformat()}"]  # materializa só hoje


def test_today_sem_plano_nao_tem_meta_nem_parte_de_grafico(db):
    corpo = _client().get("/nutrition/today").json()
    assert corpo["target"] is None and corpo["balance"]["rows"][0]["to_consume"] is None


def test_excesso_na_rota_e_na_semana(db):
    _publicar(db, strategy="maintenance")
    db.add_body(ALUNO, HOJE - timedelta(days=3), weight=80)
    cliente = _client()
    for _ in range(3):
        _refeicao(cliente, HOJE, db.arroz, 2000)
    energia = cliente.get("/nutrition/today").json()["balance"]["rows"][0]
    assert energia["excess"] > 0 and energia["to_consume"] == 0.0 and energia["within_target"] == pytest.approx(energia["target"])
    semana = cliente.get("/nutrition/weeks").json()
    assert next(d for d in semana["days"] if d["local_date"] == HOJE.isoformat())["intake_kcal"] == pytest.approx(60 * 128.26)


def test_semana_expoe_kcal_nulo_quando_o_dia_e_incompleto_ou_vazio(db):
    _publicar(db, strategy="maintenance")
    sal = db.add_food("Sal, grosso", sodium_mg=39943.20)
    ontem = HOJE - timedelta(days=1)
    _refeicao(_client(), ontem, sal, 5)
    semana = _client().get("/nutrition/weeks").json()
    por_dia = {d["local_date"]: d for d in semana["days"]}
    assert por_dia[ontem.isoformat()]["intake_incomplete"] and por_dia[ontem.isoformat()]["intake_kcal"] is None
    assert por_dia[HOJE.isoformat()]["intake_kcal"] is None


# ---------------------------------------------------------------------------
# Corpo, projeção e TDEE
# ---------------------------------------------------------------------------


def test_corpo_media_movel_projecao_e_tdee_so_sugere(db):
    _publicar(db, strategy="cutting", goal_weight_kg=Decimal("75"))
    cliente = _client()
    for indice in range(28):
        dia = HOJE - timedelta(days=27 - indice)
        db.add_body(ALUNO, dia, weight=round(80.0 - 0.05 * indice, 2), body_fat=20.0 if indice % 7 == 0 else None,
                    muscle=36.0 if indice % 7 == 0 else None)
        if dia < HOJE:
            _refeicao(cliente, dia, db.arroz, 1500)
    _refeicao(cliente, HOJE, db.arroz, 1500)
    corpo = cliente.get("/nutrition/dashboard").json()
    pontos = corpo["body"]["points"]
    assert len(pontos) == 28 and pontos[0]["weight_trend_kg"] == pontos[0]["weight_kg"]
    assert pontos[1]["weight_trend_kg"] == pytest.approx((pontos[0]["weight_kg"] + pontos[1]["weight_kg"]) / 2, abs=0.01)
    assert pontos[0]["body_fat_percent"] == 20.0 and pontos[0]["muscle_mass_kg"] == 36.0
    assert corpo["body"]["weight_trend"]["weekly_change_kg"] == pytest.approx(-0.35, abs=0.01)
    projecao = corpo["projection"]
    assert projecao["goal_kind"] == "weight" and projecao["status"] == "projected" and projecao["is_minimum_estimate"] is False
    assert projecao["weeks"] > 0 and projecao["progress_percent"] is not None
    observado = corpo["adaptive_tdee"]["observed"]
    assert corpo["adaptive_tdee"]["applied"] is False
    assert observado["window_days"] == atdee.PREFERRED_WINDOW_DAYS
    assert observado["logged_days"] == atdee.PREFERRED_WINDOW_DAYS - 1  # hoje (aberto) fica fora do TDEE
    assert observado["kcal"] is not None


def test_tdee_observado_olha_sempre_a_janela_preferida_mesmo_com_periodo_curto(db):
    _publicar(db, strategy="maintenance")
    cliente = _client()
    for indice in range(1, atdee.PREFERRED_WINDOW_DAYS):
        dia = HOJE - timedelta(days=indice)
        db.add_body(ALUNO, dia, weight=round(80.0 - 0.02 * (28 - indice), 2))
        _refeicao(cliente, dia, db.arroz, 1500)
    curto = cliente.get("/nutrition/dashboard?days=7").json()
    assert len(curto["days"]) == 7
    assert curto["adaptive_tdee"]["observed"]["window_days"] == atdee.PREFERRED_WINDOW_DAYS
    assert curto["adaptive_tdee"]["observed"]["logged_days"] == atdee.PREFERRED_WINDOW_DAYS - 1


def test_alvo_de_gordura_traz_a_premissa_de_estimativa_minima(db):
    _publicar(db, strategy="cutting", goal_body_fat_percent=Decimal("12"))
    db.add_body(ALUNO, HOJE - timedelta(days=2), weight=80.0, body_fat=20.0, muscle=36.0)
    projecao = _client().get("/nutrition/dashboard").json()["projection"]
    assert projecao["goal_kind"] == "body_fat" and projecao["status"] == "projected"
    assert projecao["is_minimum_estimate"] is True
    assert "projection_lean_mass_constant" in _codigos(projecao["reason_items"])


def test_alvo_ja_atingido_na_rota(db):
    _publicar(db, strategy="cutting", goal_weight_kg=Decimal("85"))
    db.add_body(ALUNO, INICIO_DO_PLANO + timedelta(days=4), weight=90.0)
    db.add_body(ALUNO, HOJE - timedelta(days=2), weight=80.0)
    projecao = _client().get("/nutrition/dashboard").json()["projection"]
    assert projecao["status"] == "goal_reached" and projecao["progress_percent"] == 100.0 and projecao["weeks"] == 0.0


def test_sem_pesagem_e_sem_bioimpedancia_com_alvo_nao_inventa_projecao(db):
    _publicar(db, strategy="cutting", goal_body_fat_percent=Decimal("12"))
    corpo = _client().get("/nutrition/dashboard").json()
    assert corpo["projection"]["status"] == "insufficient_data" and corpo["projection"]["weeks"] is None
    assert "projection_missing_weight" in _codigos(corpo["projection"]["reason_items"])
    assert {dash.EMPTY_NO_WEIGHT, dash.EMPTY_NO_BIOIMPEDANCE} <= set(_codigos(corpo["empty_states"]))
    assert corpo["body"]["points"] == [] and corpo["adaptive_tdee"]["observed"]["kcal"] is None


def test_plano_sem_alvo_avisa_e_nao_projeta(db):
    _publicar(db, strategy="cutting")
    corpo = _client().get("/nutrition/dashboard").json()
    assert corpo["projection"] is None and corpo["plan"] is not None and dash.EMPTY_NO_GOAL in _codigos(corpo["empty_states"])


def test_sequencia_de_versoes_mantem_o_inicio_do_caminho(db):
    # Duas versões do MESMO plano (estratégia e alvo iguais): o caminho parte da mais antiga.
    _publicar(db, strategy="cutting", inicio=date(2026, 9, 1), goal_weight_kg=Decimal("75"))
    _publicar(db, strategy="cutting", inicio=date(2026, 10, 1), goal_weight_kg=Decimal("75"))
    db.add_body(ALUNO, date(2026, 9, 5), weight=82.0)
    db.add_body(ALUNO, date(2026, 10, 5), weight=81.0)
    db.add_body(ALUNO, HOJE - timedelta(days=1), weight=79.0)
    projecao = _client().get("/nutrition/dashboard").json()["projection"]
    assert projecao["progress_percent"] == pytest.approx(100 * (82 - 79) / (82 - 75))


def test_mudar_o_alvo_recomeca_o_caminho(db):
    _publicar(db, strategy="cutting", inicio=date(2026, 9, 1), goal_weight_kg=Decimal("78"))
    _publicar(db, strategy="cutting", inicio=date(2026, 10, 1), goal_weight_kg=Decimal("75"))
    db.add_body(ALUNO, date(2026, 9, 5), weight=82.0)
    db.add_body(ALUNO, date(2026, 10, 5), weight=81.0)
    db.add_body(ALUNO, HOJE - timedelta(days=1), weight=79.0)
    projecao = _client().get("/nutrition/dashboard").json()["projection"]
    assert projecao["progress_percent"] == pytest.approx(100 * (81 - 79) / (81 - 75))


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------


def test_acesso_dono_terceiro_admin_e_personal(db):
    for caminho in ("/nutrition/dashboard", "/nutrition/today"):
        assert _client(user_id=ALUNO).get(caminho).status_code == 200
        assert _client(user_id=OUTRO).get(f"{caminho}?user_id={ALUNO}").status_code == 403
        admin = _client(role="admin", user_id=ADMIN).get(f"{caminho}?user_id={ALUNO}")
        assert admin.status_code == 200 and admin.json()["user"]["id"] == ALUNO
        personal = _client(role="personal_trainer", user_id=OUTRO).get(f"{caminho}?user_id={ALUNO}")
        assert personal.status_code == 200 and personal.json()["user"]["id"] == ALUNO

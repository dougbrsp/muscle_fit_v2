"""Rotas do check-in semanal (`app/nutrition_checkins.py`, N7) sem banco real: geração ao abrir (uma por semana, mesmo
com aberturas concorrentes), inconclusivo, aceitar (nova versão do plano, idempotente), recusar, expiração em 3 dias,
plano trocado depois da proposta, dia da semana do check-in e acesso (dono, terceiro, admin/PT).

Banco em memória: `tests/support/nutrition_checkins_fake.py`."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_checkins as ck_mod
from app import nutrition_dashboard as dash_mod
from app import nutrition_days as days_mod
from app import nutrition_meals as meals_mod
from app import nutrition_plans as plans_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition import weekly_checkin as wc
from app.nutrition_schema import checkin_tables_missing_error
from app.security import Identity, get_current_identity
from tests.support.nutrition_checkins_fake import FakeCheckinDB

HOJE = date(2026, 11, 2)  # segunda-feira: o check-in avalia 26/10 a 01/11
SEMANA = date(2026, 10, 26)
ALUNO, OUTRO, ADMIN = 2, 4, 1
INICIO_DO_PLANO = date(2026, 9, 1)


@pytest.fixture
def db(monkeypatch):
    banco = FakeCheckinDB()
    banco.mix = banco.add_food("Mix calórico", energy_kcal=400, carbohydrates_g=50, protein_g=20, total_fat_g=10)
    for modulo in (ck_mod, dash_mod, meals_mod, days_mod, plans_mod):
        monkeypatch.setattr(modulo, "get_conn", banco.conn)
    for modulo in (meals_mod, dash_mod, days_mod, ck_mod):
        monkeypatch.setattr(modulo, "require_diary_tables", lambda cur: None)
    for modulo in (dash_mod, days_mod, plans_mod, ck_mod):
        monkeypatch.setattr(modulo, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(ck_mod, "require_checkin_tables", lambda cur: None)
    monkeypatch.setattr(meals_mod, "require_food_tables", lambda cur: None)
    for modulo in (ck_mod, dash_mod, days_mod, plans_mod):
        monkeypatch.setattr(modulo, "_today_for", lambda conn, user_id: banco.hoje)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    banco.hoje = HOJE
    return banco


def _client(role: str = "regular", user_id: int = ALUNO) -> TestClient:
    app = FastAPI()
    for modulo in (ck_mod, dash_mod, meals_mod, days_mod, plans_mod):
        app.include_router(modulo.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _publicar(banco, strategy="cutting", inicio=INICIO_DO_PLANO, **settings):
    return banco.add_plan(ALUNO, status="published", strategy=strategy, effective_from=inicio,
                          published_at=datetime.combine(inicio, datetime.min.time()), **settings)


def _semana_completa(banco, *, mudanca_kg_semana=0.0, dias_com_refeicao=7, fator=1.0, pesar=True):  # pesar="antigas": só antes das 2 últimas semanas
    """Janela de 28 dias terminando em 01/11: peso e %G diários, metas gravadas e refeições da semana fechada."""
    janela = wc.window_dates(SEMANA)
    if pesar:
        for i, dia in enumerate(janela):
            if pesar == "antigas" and i >= len(janela) - wc.WEIGHT_WINDOW_DAYS:
                continue
            peso = 80.0 + mudanca_kg_semana / 7.0 * (i - (len(janela) - 1))
            banco.add_body(ALUNO, dia, weight=round(peso, 2), body_fat=20.0)
    cur = banco.conn().cursor()
    entradas = svc.load_user_inputs(cur, ALUNO, janela[0], HOJE, HOJE)
    planos = svc.load_published_plans(cur, ALUNO)
    for dia in janela:
        em_vigor = planos.for_day(dia)
        if em_vigor is None:
            continue
        comp = svc.compute_day(entradas, em_vigor[1], dia, HOJE)
        cur._upsert(svc._upsert_params(ALUNO, em_vigor[0], comp, f"seed-{dia}"))
    cliente = _client()
    for dia in janela[-7:][:dias_com_refeicao]:
        kcal = float(banco.targets[(ALUNO, dia)]["kcal_target"]) * fator
        resposta = cliente.post("/nutrition/meals", json={
            "local_date": dia.isoformat(), "meal_slot": "lunch",
            "items": [{"food_id": banco.mix, "quantity": round(float(kcal) / 4.0, 1), "unit": "g"}]})
        assert resposta.status_code == 201, resposta.text


def _vigente(banco) -> int:
    return next(r["id"] for r in banco.plans.values() if r["user_id"] == ALUNO and r["status"] == "published" and r["superseded_at"] is None)


# ---------------------------------------------------------------------------
# Infra e geração
# ---------------------------------------------------------------------------


def test_o_router_esta_no_app_real_da_api_core():
    from app.main import app

    caminhos = app.openapi()["paths"]
    assert {"/nutrition/checkin", "/nutrition/checkins", "/nutrition/checkin/settings"} <= set(caminhos)
    assert "/nutrition/checkins/{checkin_id}/accept" in caminhos
    assert TestClient(app, raise_server_exceptions=False).get("/nutrition/checkin").status_code in (401, 403)


def test_tabelas_ausentes_respondem_503(db, monkeypatch):
    def _sem(cur):
        raise checkin_tables_missing_error()

    monkeypatch.setattr(ck_mod, "require_checkin_tables", _sem)
    resposta = _client().get("/nutrition/checkin")
    assert resposta.status_code == 503 and resposta.json()["detail"]["migration"] == "103"


def test_sem_plano_explica_por_que_nao_ha_checkin(db):
    corpo = _client().get("/nutrition/checkin").json()
    assert corpo["checkin"] is None and corpo["none_items"][0]["code"] == wc.NONE_NO_PLAN
    assert corpo["settings"]["weekday"] == 0 and corpo["settings"]["is_default"] is True
    assert db.checkin_inserts == 0


def test_plano_mais_novo_que_a_semana_nao_gera_checkin(db):
    _publicar(db, inicio=HOJE)
    corpo = _client().get("/nutrition/checkin").json()
    assert corpo["checkin"] is None and corpo["none_items"][0]["code"] == wc.NONE_PLAN_AFTER_WEEK


def test_gera_uma_proposta_por_semana_mesmo_com_aberturas_concorrentes(db):
    _publicar(db)
    _semana_completa(db, mudanca_kg_semana=0.0)
    cliente = _client()
    primeira = cliente.get("/nutrition/checkin")
    assert primeira.status_code == 200, primeira.text
    corpo = primeira.json()
    assert corpo["week_start"] == SEMANA.isoformat() and corpo["week_end"] == "2026-11-01"
    ck = corpo["checkin"]
    assert ck["verdict"] == "slow" and ck["status"] == "proposed" and ck["can_respond"] and ck["show_card"]
    assert ck["proposal"]["delta_kcal"] < 0 and ck["adjustment_kcal"] == pytest.approx(ck["proposal"]["delta_kcal"])
    assert ck["proposal"]["change_items"][0]["field"] == "weekly_rate_share"
    assert ck["expires_on"] == (HOJE + timedelta(days=wc.PROPOSAL_VALIDITY_DAYS)).isoformat()
    assert ck["summary"]["complete_days"] == 7 and len(ck["days"]) == 7
    for _ in range(3):  # aberturas seguintes (e simultâneas) leem a mesma linha
        assert cliente.get("/nutrition/checkin").json()["checkin"]["id"] == ck["id"]
    assert db.checkin_inserts == 1 and len(db.checkins) == 1


def test_a_leitura_com_a_linha_gravada_nao_reavalia_a_semana(db, monkeypatch):
    _publicar(db)
    _semana_completa(db)
    cliente = _client()
    cliente.get("/nutrition/checkin")

    def _proibido(*args, **kwargs):  # pragma: no cover - só roda se a leitura leve reavaliar
        raise AssertionError("a leitura leve não pode reavaliar a semana")

    monkeypatch.setattr(ck_mod.wc, "evaluate_week", _proibido)
    monkeypatch.setattr(ck_mod, "load_user_inputs", _proibido)
    assert cliente.get("/nutrition/checkin").json()["checkin"]["status"] == "proposed"


def test_poucos_dias_completos_e_inconclusivo_com_o_que_faltou_e_sem_proposta(db):
    _publicar(db)
    _semana_completa(db, dias_com_refeicao=3)
    ck = _client().get("/nutrition/checkin").json()["checkin"]
    assert ck["verdict"] == "inconclusive" and ck["status"] == "informative" and ck["proposal"] is None
    assert wc.MISSING_FEW_COMPLETE_DAYS in [m["code"] for m in ck["missing_items"]]
    assert ck["can_respond"] is False and ck["show_card"] is True
    assert all(m["label"] != m["code"] for m in ck["missing_items"])


def test_sem_pesagem_na_janela_do_peso_e_inconclusivo(db):
    _publicar(db)
    _semana_completa(db, pesar="antigas")
    ck = _client().get("/nutrition/checkin").json()["checkin"]
    assert ck["verdict"] == "inconclusive" and ck["proposal"] is None
    assert wc.MISSING_FEW_WEIGHINGS in [m["code"] for m in ck["missing_items"]]


# ---------------------------------------------------------------------------
# Aceitar, recusar, expirar
# ---------------------------------------------------------------------------


def _com_proposta(db):
    _publicar(db)
    _semana_completa(db)
    return _client().get("/nutrition/checkin").json()["checkin"]


def test_aceitar_publica_nova_versao_do_plano_e_regrava_so_as_metas_nao_congeladas(db):
    antigo = _publicar(db)
    _semana_completa(db)
    ck = _client().get("/nutrition/checkin").json()["checkin"]
    resposta = _client().post(f"/nutrition/checkins/{ck['id']}/accept")
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    novo = corpo["plan"]["id"]
    assert novo != antigo and corpo["checkin"]["status"] == "accepted" and corpo["checkin"]["generated_plan_id"] == novo
    assert db.plans[antigo]["superseded_at"] is not None and db.plans[novo]["status"] == "published"
    assert db.plans[novo]["effective_from"] == HOJE and db.plans[novo]["strategy"] == "cutting"
    assert float(db.plans[novo]["weekly_rate_share"]) > 0.007  # o ritmo subiu: déficit maior
    assert db.plans[novo]["goal_weight_kg"] == db.plans[antigo]["goal_weight_kg"]  # mesma estratégia e alvo
    assert db.targets[(ALUNO, HOJE)]["plan_id"] == novo  # a meta de hoje já sai com o plano novo
    assert db.targets[(ALUNO, HOJE - timedelta(days=1))]["plan_id"] == antigo  # o passado fica intacto
    assert "Check-in semanal" in db.plans[novo]["notes"] and db.plans[novo]["created_by"] == ALUNO


def test_aceite_duplicado_e_idempotente_e_trava_a_linha(db):
    _publicar(db)
    _semana_completa(db)
    ck = _client().get("/nutrition/checkin").json()["checkin"]
    primeiro = _client().post(f"/nutrition/checkins/{ck['id']}/accept").json()
    planos_antes = len(db.plans)
    segundo = _client().post(f"/nutrition/checkins/{ck['id']}/accept")
    assert segundo.status_code == 200 and segundo.json()["already_accepted"] is True
    assert len(db.plans) == planos_antes and segundo.json()["checkin"]["generated_plan_id"] == primeiro["plan"]["id"]
    assert ("checkin", ck["id"]) in db.locks  # `SELECT ... FOR UPDATE` serializa aceites simultâneos


def test_recusar_mantem_as_metas_e_e_idempotente(db):
    ck = _com_proposta(db)
    antes = dict(db.plans)
    resposta = _client().post(f"/nutrition/checkins/{ck['id']}/decline")
    assert resposta.status_code == 200 and resposta.json()["checkin"]["status"] == "declined"
    assert db.plans.keys() == antes.keys() and not _client().get("/nutrition/checkin").json()["checkin"]["show_card"]
    assert _client().post(f"/nutrition/checkins/{ck['id']}/decline").json()["already_declined"] is True
    assert _client().post(f"/nutrition/checkins/{ck['id']}/accept").status_code == 409  # recusado não se aceita


def test_proposta_expira_em_tres_dias_e_aceitar_depois_responde_409(db):
    ck = _com_proposta(db)
    db.hoje = HOJE + timedelta(days=wc.PROPOSAL_VALIDITY_DAYS)  # último dia: ainda vale
    assert _client().get("/nutrition/checkin").json()["checkin"]["status"] == "proposed"
    db.hoje = HOJE + timedelta(days=wc.PROPOSAL_VALIDITY_DAYS + 1)
    lida = _client().get("/nutrition/checkin").json()["checkin"]
    assert lida["status"] == "expired" and lida["can_respond"] is False and lida["show_card"] is False
    planos = len(db.plans)
    resposta = _client().post(f"/nutrition/checkins/{ck['id']}/accept")
    assert resposta.status_code == 409 and resposta.json()["detail"]["error"] == "checkin_expired"
    assert len(db.plans) == planos and db.checkins[ck["id"]]["status"] == "expired"  # gravado como expirado
    assert _client().post(f"/nutrition/checkins/{ck['id']}/decline").status_code == 409


def test_plano_trocado_depois_da_proposta_responde_409_e_nao_publica(db):
    ck = _com_proposta(db)
    outro = db.add_plan(ALUNO, status="draft", strategy="cutting")
    publicado = _client().post(f"/nutrition/plans/{outro}/publish", json={"expected_current_id": _vigente(db)})
    assert publicado.status_code == 200, publicado.text
    planos = len(db.plans)
    resposta = _client().post(f"/nutrition/checkins/{ck['id']}/accept")
    assert resposta.status_code == 409 and resposta.json()["detail"]["error"] == "checkin_plan_changed"
    assert len(db.plans) == planos and db.checkins[ck["id"]]["status"] == "proposed"


def test_check_in_sem_proposta_nao_tem_o_que_aceitar(db):
    _publicar(db)
    _semana_completa(db, dias_com_refeicao=3)
    ck = _client().get("/nutrition/checkin").json()["checkin"]
    assert _client().post(f"/nutrition/checkins/{ck['id']}/accept").status_code == 409
    assert _client().post(f"/nutrition/checkins/{ck['id']}/decline").status_code == 409


# ---------------------------------------------------------------------------
# Dia do check-in, histórico e acesso
# ---------------------------------------------------------------------------


def test_dia_do_checkin_padrao_segunda_e_escolhido_pelo_usuario(db):
    cliente = _client()
    assert cliente.get("/nutrition/checkin/settings").json()["weekday"] == 0
    assert cliente.get("/nutrition/checkin/settings").json()["is_default"] is True
    salvo = cliente.put("/nutrition/checkin/settings", json={"weekday": 4})
    assert salvo.status_code == 200 and salvo.json()["weekday"] == 4 and salvo.json()["weekday_label"] == "Sexta-feira"
    assert db.checkin_settings == {ALUNO: 4}
    corpo = cliente.get("/nutrition/checkin").json()
    assert corpo["settings"]["weekday"] == 4 and corpo["due_date"] == "2026-10-30"  # a sexta que já passou
    assert len(corpo["weekday_options"]) == 7


@pytest.mark.parametrize("valor", [7, -1])
def test_dia_da_semana_invalido_e_recusado_pelo_motor(db, valor):
    resposta = _client().put("/nutrition/checkin/settings", json={"weekday": valor})
    assert resposta.status_code == 400 and resposta.json()["detail"]["error"] == "invalid_checkin_weekday"
    assert db.checkin_settings == {}


def test_historico_lista_as_semanas_mais_recentes_primeiro(db):
    _com_proposta(db)
    db.checkins[2] = {**db.checkins[1], "id": 2, "week_start": SEMANA - timedelta(days=7)}
    itens = _client().get("/nutrition/checkins").json()["items"]
    assert [i["week_start"] for i in itens] == [SEMANA.isoformat(), (SEMANA - timedelta(days=7)).isoformat()]
    assert "days" not in itens[0] and itens[0]["verdict_label"]
    assert len(_client().get("/nutrition/checkins?limit=1").json()["items"]) == 1


def test_terceiro_nao_alcanca_o_checkin_de_outro_mas_admin_e_pt_sim(db):
    ck = _com_proposta(db)
    intruso = _client("regular", OUTRO)
    assert intruso.get(f"/nutrition/checkin?user_id={ALUNO}").status_code == 403
    assert intruso.get(f"/nutrition/checkins?user_id={ALUNO}").status_code == 403
    assert intruso.post(f"/nutrition/checkins/{ck['id']}/accept").status_code == 404
    assert intruso.post(f"/nutrition/checkins/{ck['id']}/decline").status_code == 404
    assert intruso.put("/nutrition/checkin/settings", json={"weekday": 2, "user_id": ALUNO}).status_code == 403
    assert db.checkins[ck["id"]]["status"] == "proposed" and db.checkin_settings == {}
    for papel in ("admin", "personal_trainer"):
        staff = _client(papel, ADMIN)
        assert staff.get(f"/nutrition/checkin?user_id={ALUNO}").json()["checkin"]["id"] == ck["id"]
    recusado = _client("admin", ADMIN).post(f"/nutrition/checkins/{ck['id']}/decline")
    assert recusado.status_code == 200


def test_check_in_inexistente_responde_404(db):
    assert _client().post("/nutrition/checkins/999/accept").status_code == 404

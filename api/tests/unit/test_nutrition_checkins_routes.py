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

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

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
    for existe in ("has_nutrition_tables", "has_diary_tables", "has_checkin_tables"):
        monkeypatch.setattr(ck_mod, existe, lambda cur: True)
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


def test_sem_as_tabelas_a_leitura_responde_200_sem_checkin_e_a_escrita_segue_503(db, monkeypatch):
    """Janela do deploy: o código novo atende antes da migração 103. A home nunca pode gerar 5xx por isso."""
    monkeypatch.setattr(ck_mod, "has_checkin_tables", lambda cur: False)

    def _sem(cur):
        raise checkin_tables_missing_error()

    monkeypatch.setattr(ck_mod, "require_checkin_tables", _sem)
    leitura = _client().get("/nutrition/checkin")
    assert leitura.status_code == 200, leitura.text
    corpo = leitura.json()
    assert corpo["checkin"] is None and corpo["none_items"][0]["code"] == wc.NONE_MIGRATION_PENDING
    assert corpo["none_items"][0]["label"] != wc.NONE_MIGRATION_PENDING and len(corpo["weekday_options"]) == 7
    assert db.statements == 0 or db.checkin_inserts == 0
    escrita = _client().put("/nutrition/checkin/settings", json={"weekday": 2})
    assert escrita.status_code == 503 and escrita.json()["detail"]["migration"] == "103"
    assert _client().post("/nutrition/checkins/1/accept").status_code == 503
    assert _client().get("/nutrition/checkins").status_code == 503


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


def test_aceitar_sem_a_106_responde_503_citando_a_migracao_e_nao_grava_nada(db):
    """Janela de deploy (código novo sem a 106): o aceite lê e grava as colunas novas do plano; sem elas, 503 e nada gravado."""
    from app import nutrition_schema

    ck = _com_proposta(db)
    antes = {pid: dict(p) for pid, p in db.plans.items()}
    escritas = len(db.writes)
    db.plan_energy_columns = False
    nutrition_schema.reset_nutrition_tables_cache()
    resposta = _client().post(f"/nutrition/checkins/{ck['id']}/accept")
    assert resposta.status_code == 503, resposta.text
    assert resposta.json()["detail"]["error"] == "nutrition_plan_migration_pending" and resposta.json()["detail"]["migration"] == "106"
    assert {pid: dict(p) for pid, p in db.plans.items()} == antes and len(db.writes) == escritas
    leitura = _client().get("/nutrition/checkin")  # a leitura do check-in NÃO exige a 106
    assert leitura.status_code == 200 and leitura.json()["checkin"]["status"] == "proposed"
    db.plan_energy_columns = True
    nutrition_schema.reset_nutrition_tables_cache()


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


def test_plano_adaptativo_o_checkin_e_informativo_sem_proposta_e_nao_aceita(db):
    """Sem dois corretores: o gasto adaptativo governa a meta, então o check-in só informa (mesmo no cenário que no
    plano legado gera proposta) e aceitar/recusar respondem 409."""
    _publicar(db, strategy="maintenance", training_kcal_mode="adaptive_balance", activity_level="light")
    _semana_completa(db, mudanca_kg_semana=0.0)
    corpo = _client().get("/nutrition/checkin").json()
    ck = corpo["checkin"]
    assert ck["status"] == "informative" and ck["proposal"] is None and ck["can_respond"] is False
    assert wc.REASON_ADAPTIVE_INFORMATIVE in str(ck)
    assert _client().post(f"/nutrition/checkins/{ck['id']}/accept").status_code == 409
    assert _client().post(f"/nutrition/checkins/{ck['id']}/decline").status_code == 409
    assert db.plans[_vigente(db)]["training_kcal_mode"] == "adaptive_balance"  # nada foi publicado em cima


def test_check_in_de_plano_adaptativo_nao_le_o_historico_do_gasto(db, monkeypatch):
    """O check-in só usa as séries do aluno (o gasto vem gravado nas metas): não paga a leitura nem o `replay` do histórico."""
    _publicar(db, strategy="maintenance", training_kcal_mode="adaptive_balance", activity_level="light")
    _semana_completa(db)
    cargas: list[str] = []

    def contado(*args, **kwargs):  # pragma: no cover - só roda se o check-in carregar o histórico
        cargas.append("carga")
        raise AssertionError("o check-in não deve carregar o histórico do gasto")

    monkeypatch.setattr(svc, "load_history", contado)
    resposta = _client().get("/nutrition/checkin")
    assert resposta.status_code == 200, resposta.text
    assert cargas == [] and resposta.json()["checkin"]["status"] == "informative"


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


# ---------------------------------------------------------------------------
# Rodada 1 de revisão: troca de dia, plano republicado, leitura corrente, goal_sequence_start, limites
# ---------------------------------------------------------------------------


def test_trocar_o_dia_com_proposta_pendente_nunca_volta_de_semana(db):
    _publicar(db)
    _semana_completa(db)
    cliente = _client()
    primeira = cliente.get("/nutrition/checkin").json()["checkin"]
    assert primeira["status"] == "proposed" and primeira["week_start"] == SEMANA.isoformat()
    db.hoje = HOJE + timedelta(days=1)  # terça
    for dia in (2, 4, 6):  # quarta, sexta e domingo: o calendário apontaria para a semana ANTERIOR (19/10)
        assert cliente.put("/nutrition/checkin/settings", json={"weekday": dia}).status_code == 200
        lido = cliente.get("/nutrition/checkin").json()
        assert lido["checkin"]["id"] == primeira["id"] and lido["week_start"] == SEMANA.isoformat()
    assert db.checkin_inserts == 1 and lido["checkin"]["status"] == "proposed"


def test_trocar_o_dia_depois_de_aceitar_nao_gera_outra_semana(db):
    _publicar(db)
    _semana_completa(db)
    cliente = _client()
    ck = cliente.get("/nutrition/checkin").json()["checkin"]
    assert cliente.post(f"/nutrition/checkins/{ck['id']}/accept").status_code == 200
    db.hoje = HOJE + timedelta(days=1)
    cliente.put("/nutrition/checkin/settings", json={"weekday": 4})
    lido = cliente.get("/nutrition/checkin").json()["checkin"]
    assert lido["id"] == ck["id"] and lido["status"] == "accepted" and lido["show_card"] is False
    assert db.checkin_inserts == 1


def test_trocar_o_dia_no_outro_sentido_reaproveita_a_semana_ja_avaliada(db):
    _publicar(db)
    _semana_completa(db)
    cliente = _client()
    cliente.put("/nutrition/checkin/settings", json={"weekday": 2})
    db.hoje = HOJE + timedelta(days=2)  # quarta: o check-in nasce hoje, da semana 26/10
    ck = cliente.get("/nutrition/checkin").json()["checkin"]
    assert ck["week_start"] == SEMANA.isoformat() and ck["due_date"] == (HOJE + timedelta(days=2)).isoformat()
    cliente.put("/nutrition/checkin/settings", json={"weekday": 0})  # volta para a segunda (02/11, mesma semana)
    outra = cliente.get("/nutrition/checkin").json()["checkin"]
    assert outra["id"] == ck["id"] and db.checkin_inserts == 1


def test_proposta_de_plano_que_nao_e_mais_o_vigente_expira_na_leitura_sem_oferecer_aceitar(db):
    ck = _com_proposta(db)
    outro = db.add_plan(ALUNO, status="draft", strategy="cutting")
    assert _client().post(f"/nutrition/plans/{outro}/publish", json={"expected_current_id": _vigente(db)}).status_code == 200
    lido = _client().get("/nutrition/checkin").json()["checkin"]
    assert lido["id"] == ck["id"] and lido["status"] == "expired" and lido["can_respond"] is False and lido["show_card"] is False
    assert db.checkins[ck["id"]]["status"] == "expired"  # gravado: o aceite responde 409 expirado, sem tentar publicar
    resposta = _client().post(f"/nutrition/checkins/{ck['id']}/accept")
    assert resposta.status_code == 409 and resposta.json()["detail"]["error"] == "checkin_expired"


def test_republicar_o_plano_antes_de_abrir_nao_diz_primeiro_check_in(db):
    antigo = _publicar(db)
    _semana_completa(db)
    novo = db.add_plan(ALUNO, status="draft", strategy="cutting")
    publicado = _client().post(f"/nutrition/plans/{novo}/publish", json={"expected_current_id": antigo})
    assert publicado.status_code == 200, publicado.text
    corpo = _client().get("/nutrition/checkin").json()
    ck = corpo["checkin"]
    assert ck is not None and corpo["none_items"] == []  # não é a mensagem de "o primeiro check-in vem na próxima semana"
    assert ck["verdict"] == "inconclusive" and ck["proposal"] is None and ck["source_plan_id"] == novo
    assert [m["code"] for m in ck["missing_items"]][0] == wc.MISSING_PLAN_CHANGED_IN_WEEK
    assert ck["summary"]["days_with_target"] == 0  # nenhum dia da semana avaliada foi sob o plano vigente


def test_plano_mudou_no_meio_da_semana_e_inconclusivo_com_motivo_proprio(db):
    antigo = _publicar(db)
    _semana_completa(db)
    novo = db.add_plan(ALUNO, status="published", strategy="cutting", effective_from=SEMANA + timedelta(days=3),
                       published_at=datetime.combine(SEMANA + timedelta(days=3), datetime.min.time()))
    db.plans[antigo]["superseded_at"] = datetime.combine(SEMANA + timedelta(days=3), datetime.min.time())
    for dia in wc.window_dates(SEMANA)[-4:]:  # quinta a domingo sob o plano novo (4 dias < mínimo)
        db.targets[(ALUNO, dia)]["plan_id"] = novo
    ck = _client().get("/nutrition/checkin").json()["checkin"]
    assert ck["verdict"] == "inconclusive" and ck["summary"]["days_with_target"] == 4
    assert wc.MISSING_PLAN_CHANGED_IN_WEEK in [m["code"] for m in ck["missing_items"]]


def test_insert_absorvido_sob_snapshot_antigo_le_a_linha_com_leitura_corrente(db):
    _publicar(db)
    _semana_completa(db)
    cliente = _client()
    primeira = cliente.get("/nutrition/checkin").json()["checkin"]
    db.invisible_to_snapshot.add(primeira["id"])  # outra abertura gravou e o snapshot desta transação não a enxerga
    lido = cliente.get("/nutrition/checkin")
    assert lido.status_code == 200 and lido.json()["checkin"] is not None, lido.text
    assert lido.json()["checkin"]["id"] == primeira["id"] and db.checkin_inserts == 1
    assert ("checkin-share", ALUNO) not in db.locks and any(trava[0] == "checkin-share" for trava in db.locks)


def test_aceitar_mantem_o_inicio_do_caminho_do_painel_de_ponta_a_ponta(db):
    from app.domain.nutrition import dashboard as dash

    antigo = _publicar(db)
    _semana_completa(db)
    ck = _client().get("/nutrition/checkin").json()["checkin"]
    novo = _client().post(f"/nutrition/checkins/{ck['id']}/accept").json()["plan"]["id"]
    cur = db.conn().cursor()
    planos = svc.load_published_plans(cur, ALUNO)
    versoes = [
        dash.PlanVersion(plan_id=ref.plan_id, effective_from=ref.effective_from, published_at=ref.published_at,
                         strategy=planos.settings[ref.plan_id].strategy.value,
                         goal_weight_kg=planos.settings[ref.plan_id].goal_weight_kg,
                         goal_body_fat_percent=planos.settings[ref.plan_id].goal_body_fat_percent)
        for ref in planos.refs
    ]
    assert {v.plan_id for v in versoes} == {antigo, novo}
    # Mesma estratégia e mesmo alvo: o caminho continua desde a primeira versão, não recomeça no aceite.
    assert dash.goal_sequence_start(versoes, HOJE) == INICIO_DO_PLANO


def test_historico_respeita_o_limite_pedido_o_padrao_e_o_maximo(db):
    _com_proposta(db)
    base = dict(db.checkins[1])
    for i in range(2, 2 + wc.HISTORY_LIMIT_MAX + 5):
        db.checkins[i] = {**base, "id": i, "week_start": SEMANA - timedelta(days=7 * i)}
    cliente = _client()
    assert len(cliente.get("/nutrition/checkins").json()["items"]) == wc.HISTORY_LIMIT_DEFAULT
    assert len(cliente.get("/nutrition/checkins?limit=2").json()["items"]) == 2
    assert len(cliente.get("/nutrition/checkins?limit=0").json()["items"]) == 1
    assert len(cliente.get("/nutrition/checkins?limit=9999").json()["items"]) == wc.HISTORY_LIMIT_MAX


def test_o_aceite_trava_a_vigente_antes_de_ler_com_trava_compartilhada(db):
    ck = _com_proposta(db)
    db.locks.clear()
    assert _client().post(f"/nutrition/checkins/{ck['id']}/accept").status_code == 200
    ordem = [trava[0] for trava in db.locks]
    assert ordem.index("current") < len(ordem) and ordem[0] == "checkin" and ordem[1] == "current"  # linha, depois a vigente


def test_chamadas_ao_banco_da_leitura_leve_da_primeira_geracao_e_do_aceite(db):
    """Contagem de statements (cada `execute` é uma chamada à Data API) no banco em memória, NÃO uma medida do DEV."""
    _publicar(db)
    _semana_completa(db)
    cliente = _client()
    db.statements = 0
    cliente.get("/nutrition/checkin")
    geracao = db.statements
    db.statements = 0
    cliente.get("/nutrition/checkin")
    leve = db.statements
    ck_id = next(iter(db.checkins))
    db.statements = 0
    assert cliente.post(f"/nutrition/checkins/{ck_id}/accept").status_code == 200
    aceite = db.statements
    print(f"STATEMENTS geracao={geracao} leitura_leve={leve} aceite={aceite}")
    assert leve <= 10 and geracao <= 30 and aceite <= 60


def test_aceite_da_manutencao_faz_a_meta_de_hoje_cair_abaixo_do_gasto(db):
    """O ajuste aceito chega à meta materializada: kcal = gasto + ajuste (negativo) a partir de hoje."""
    antigo = _publicar(db, strategy="maintenance")
    _semana_completa(db, mudanca_kg_semana=0.6)  # subindo além da banda
    ck = _client().get("/nutrition/checkin").json()["checkin"]
    assert ck["verdict"] == "above_band" and ck["proposal"]["change_items"][0]["field"] == "maintenance_kcal_adjust"
    novo = _client().post(f"/nutrition/checkins/{ck['id']}/accept").json()["plan"]["id"]
    ajuste = int(db.plans[novo]["maintenance_kcal_adjust"])
    assert ajuste < 0 and int(db.plans[antigo]["maintenance_kcal_adjust"]) == 0
    hoje = db.targets[(ALUNO, HOJE)]
    assert hoje["plan_id"] == novo
    assert float(hoje["kcal_target"]) == pytest.approx(float(hoje["tdee_kcal"]) + ajuste, abs=0.5)
    passado = db.targets[(ALUNO, HOJE - timedelta(days=1))]  # o passado fica com o plano antigo
    assert float(passado["kcal_target"]) == pytest.approx(float(passado["tdee_kcal"]), abs=0.5)

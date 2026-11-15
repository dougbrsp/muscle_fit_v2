"""Rotas do plano autogerido (N2) sem banco real: gramas manuais (D6: aviso salva, bloqueio e falta de peso não
salvam), dias alto/normal/baixo materializados na semana com a média preservada, dia congelado intocado,
"puxar do Modelo" (D5), preview com projeção e avisos, e o plano antigo (linha sem os campos do N2) com
resultado idêntico.

Banco em memória: `tests/support/nutrition_fake.py`."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_plans as plans_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition import macros as mc
from app.domain.nutrition import strategy as st
from app.domain.nutrition.daily_target import TargetWindow, week_window
from app.security import Identity, get_current_identity
from tests.support.nutrition_fake import FakeNutritionDB

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

SEGUNDA = date(2026, 10, 12)
SABADO = date(2026, 10, 17)
ALUNO = 2
DIAS_ALTOS = {"0": "high", "1": "low", "2": "high", "3": "low", "4": "high", "5": "low", "6": "low"}
MANUAL = {"protein_g": 160, "carb_g": 280, "fat_g": 70}


@pytest.fixture
def hoje():
    return {"dia": SABADO}


@pytest.fixture
def db(monkeypatch, hoje):
    banco = FakeNutritionDB()
    banco.add_weight(ALUNO, SEGUNDA - timedelta(days=3), 80)
    monkeypatch.setattr(plans_mod, "get_conn", banco.conn)
    monkeypatch.setattr(plans_mod, "_today_for", lambda conn, user_id: hoje["dia"])
    monkeypatch.setattr(plans_mod, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    monkeypatch.setattr(plans_mod, "resolve_active_template_id", lambda uid: next(iter(banco.templates), None))
    return banco


def _cliente(role: str = "regular", user_id: int = ALUNO) -> TestClient:
    app = FastAPI()
    app.include_router(plans_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _criar(cliente, **corpo):
    return cliente.post("/nutrition/plans", json={"strategy": "maintenance", **corpo})


def _codigos(resposta, chave="errors"):
    return [item["code"] for item in resposta.json()["detail"][chave]]


# ---------------------------------------------------------------------------
# Gramas manuais (D6)
# ---------------------------------------------------------------------------


def test_gramas_manuais_dentro_da_faixa_salvam_sem_aviso(db):
    resposta = _criar(_cliente(), manual_targets=MANUAL)
    assert resposta.status_code == 201, resposta.text
    plano = resposta.json()
    assert plano["manual_targets"] == {"protein_g": 160.0, "carb_g": 280.0, "fat_g": 70.0, "training_mode": "fixed"}
    assert plano["macro_priority"] == "balanced" and plano["warnings"] == []
    gravado = db.plans[plano["id"]]
    assert gravado["manual_targets_json"] == {"protein_g": 160.0, "carb_g": 280.0, "fat_g": 70.0, "training_mode": "fixed"}


def test_gramas_fora_da_faixa_salvam_com_aviso_rotulado(db):
    resposta = _criar(_cliente(), manual_targets={"protein_g": 110, "carb_g": 280, "fat_g": 70})  # 1,4 g/kg
    assert resposta.status_code == 201, resposta.text
    avisos = {item["code"]: item["label"] for item in resposta.json()["warnings"]}
    assert "manual_below_range:protein_g" in avisos and avisos["manual_below_range:protein_g"]
    assert db.plans  # salvou


def test_gramas_bloqueadas_nao_salvam(db):
    resposta = _criar(_cliente(), manual_targets={"protein_g": 40, "carb_g": 280, "fat_g": 70})  # 0,5 g/kg
    assert resposta.status_code == 400
    assert "manual_blocked_below:protein_g" in _codigos(resposta)
    assert db.plans == {}


def test_sem_peso_do_aluno_gramas_manuais_nao_salvam(db):
    db.composition.clear()
    resposta = _criar(_cliente(), manual_targets=MANUAL)
    assert resposta.status_code == 400
    assert "manual_targets_require_weight" in _codigos(resposta)
    assert all(item["label"] for item in resposta.json()["detail"]["errors"])
    assert db.plans == {}


def test_edicao_com_gramas_manuais_aplica_a_mesma_regra(db):
    cliente = _cliente()
    plano = _criar(cliente).json()
    ruim = cliente.put(f"/nutrition/plans/{plano['id']}", json={"strategy": "maintenance", "manual_targets": {"protein_g": 40, "carb_g": 280, "fat_g": 70}})
    assert ruim.status_code == 400 and "manual_blocked_below:protein_g" in _codigos(ruim)
    assert db.plans[plano["id"]]["manual_targets_json"] is None
    bom = cliente.put(f"/nutrition/plans/{plano['id']}", json={"strategy": "maintenance", "manual_targets": MANUAL})
    assert bom.status_code == 200 and bom.json()["manual_targets"]["protein_g"] == 160.0
    # Voltar para o automático limpa as gramas.
    limpo = cliente.put(f"/nutrition/plans/{plano['id']}", json={"strategy": "maintenance"})
    assert limpo.json()["manual_targets"] is None and db.plans[plano["id"]]["manual_targets_json"] is None


def test_prioridade_junto_com_gramas_manuais_e_recusada(db):
    resposta = _criar(_cliente(), macro_priority="high_fat", manual_targets=MANUAL)
    assert resposta.status_code == 400 and "param_not_applicable:macro_priority" in _codigos(resposta)


def test_cliente_antigo_que_manda_carb_cycling_enabled_continua_aceito(db):
    resposta = _criar(_cliente(), carb_cycling_enabled=False)
    assert resposta.status_code == 201


# ---------------------------------------------------------------------------
# Dias alto/normal/baixo, prioridade e gasto do treino chegam à meta materializada
# ---------------------------------------------------------------------------


def _publicado(db, cliente, **corpo):
    plano = _criar(cliente, **corpo).json()
    resposta = cliente.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None})
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def _carbo_da_semana(db):
    return {dia: float(linha["carbs_g"]) for (uid, dia), linha in sorted(db.targets.items()) if uid == ALUNO}


def test_semana_materializada_com_os_dias_escolhidos_preserva_a_soma(db, hoje):
    hoje["dia"] = SEGUNDA  # a semana inteira ainda é calculável
    _publicado(db, _cliente(), cycle_days=DIAS_ALTOS)
    com_ciclo = _carbo_da_semana(db)
    assert len(com_ciclo) == 7
    tipos = {dia: linha["day_kind"] for (uid, dia), linha in db.targets.items()}
    assert [tipos[SEGUNDA + timedelta(days=i)] for i in range(7)] == [DIAS_ALTOS[str(i)] for i in range(7)]
    # Mesmo aluno, sem ciclo: a soma semanal do carbo é a mesma.
    outro = FakeNutritionDB()
    outro.add_weight(ALUNO, SEGUNDA - timedelta(days=3), 80)
    plans_mod.get_conn = outro.conn  # type: ignore[assignment]
    try:
        _publicado(outro, _cliente())
    finally:
        plans_mod.get_conn = db.conn  # type: ignore[assignment]
    sem_ciclo = _carbo_da_semana(outro)
    assert sum(com_ciclo.values()) == pytest.approx(sum(sem_ciclo.values()), abs=0.5)  # linha arredondada a 2 casas
    assert com_ciclo[SEGUNDA] > sem_ciclo[SEGUNDA] and com_ciclo[SEGUNDA + timedelta(days=1)] < sem_ciclo[SEGUNDA + timedelta(days=1)]


def test_dia_congelado_nunca_e_reescrito(db, hoje):
    """Metas de dias congelados (hoje >= dia + 2) ficam como estão, mesmo com o plano novo e a janela cobrindo-as."""
    cliente = _cliente()
    congelado = SABADO - timedelta(days=3)
    db.targets[(ALUNO, congelado)] = {"user_id": ALUNO, "local_date": congelado, "plan_id": 99, "carbs_g": Decimal("111.00"),
                                      "engine_version": "nutrition-v1", "inputs_hash": "x", "computed_at": db.now}
    antes = dict(db.targets[(ALUNO, congelado)])
    _publicado(db, cliente, cycle_days=DIAS_ALTOS)
    conn = db.conn()
    janela = TargetWindow(date_from=congelado, date_to=SABADO + timedelta(days=1), create_until=SABADO + timedelta(days=1))
    svc.ensure_daily_targets(conn, ALUNO, janela, today=SABADO)
    assert db.targets[(ALUNO, congelado)] == antes
    assert all(not escrita.endswith(congelado.isoformat()) for escrita in db.writes)


def test_prioridade_de_macros_chega_na_meta_materializada(db, hoje):
    hoje["dia"] = SEGUNDA
    db.composition.clear()
    db.add_weight(ALUNO, SEGUNDA - timedelta(days=3), 80, body_fat_percent=Decimal("20"), fat_free_mass_kg=Decimal("64"))
    _publicado(db, _cliente(), strategy="cutting", macro_priority="high_carb")
    linha = db.targets[(ALUNO, SEGUNDA)]
    params = st.StrategyParams.defaults_for(st.Strategy.CUTTING)
    esperado = mc.macro_split_for_priority(float(linha["kcal_target"]), params, "high_carb", 80, 64)
    assert float(linha["fat_g"]) == pytest.approx(esperado.fat_g, abs=0.01)


def test_gasto_do_treino_embutido_ignora_a_sessao_e_o_explicito_soma(db, hoje):
    hoje["dia"] = SEGUNDA
    db.add_session(ALUNO, SEGUNDA, 500)
    terca = SEGUNDA + timedelta(days=1)  # futuro: treino desconhecido
    cliente = _cliente()
    _publicado(db, cliente, tdee_mode="included_in_factor")
    assert float(db.targets[(ALUNO, SEGUNDA)]["kcal_target"]) == float(db.targets[(ALUNO, terca)]["kcal_target"])
    vigente = next(p for p in db.plans if db.plans[p]["superseded_at"] is None and db.plans[p]["status"] == "published")
    plano = _criar(cliente, tdee_mode="explicit_training").json()
    cliente.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": vigente})
    assert float(db.targets[(ALUNO, SEGUNDA)]["kcal_target"]) - float(db.targets[(ALUNO, terca)]["kcal_target"]) == pytest.approx(500 - 1750.0 / 1440 * 60, abs=1.0)  # líquido do repouso da sessão (60 min)


# ---------------------------------------------------------------------------
# Plano antigo: linha sem os campos do N2 = resultado idêntico
# ---------------------------------------------------------------------------


def test_linha_antiga_sem_os_campos_novos_vira_settings_padrao():
    linha = {"strategy": "maintenance", "advanced": 0, "bmr_formula": "auto", "activity_level": None,
             "training_kcal_mode": "explicit_training", "protein_g_per_kg": Decimal("2.00"), "fat_kcal_share": Decimal("0.2500"),
             "meals_per_day": None}
    cfg = svc.settings_from_row(linha)
    assert cfg.macro_priority.value == "balanced" and cfg.manual_macros is None and cfg.cycle_kinds is None
    novo = svc.settings_from_row({**linha, "macro_priority": "balanced", "manual_targets_json": None, "carb_cycling_json": None})
    assert cfg == novo


def test_json_do_banco_pode_vir_como_texto():
    linha = {"strategy": "maintenance", "advanced": 0, "bmr_formula": "auto", "training_kcal_mode": "explicit_training",
             "protein_g_per_kg": 2.0, "fat_kcal_share": 0.25,
             "manual_targets_json": '{"protein_g": 150, "carb_g": 250, "fat_g": 70}',
             "carb_cycling_json": '{"0": "high", "1": "low"}'}
    cfg = svc.settings_from_row(linha)
    assert cfg.manual_macros.as_tuple() == (150.0, 250.0, 70.0)
    assert cfg.cycle_kinds is not None and cfg.cycle_days["0"] == "high"


def test_plano_antigo_publicado_gera_a_mesma_meta_de_sempre(db, hoje):
    hoje["dia"] = SEGUNDA
    db.add_plan(ALUNO, status="published", effective_from=SEGUNDA - timedelta(days=5), published_at=db.now)
    # Linha SEM as chaves do N2 (como no banco antes da 101 / depois dela com defaults).
    for chave in ("macro_priority", "manual_targets_json", "carb_cycling_json"):
        db.plans[1].pop(chave)
    svc.ensure_daily_targets(db.conn(), ALUNO, week_window(SEGUNDA), today=SEGUNDA)
    linha = db.targets[(ALUNO, SEGUNDA)]
    params = st.StrategyParams.defaults_for(st.Strategy.MAINTENANCE)
    esperado = mc.macro_split(float(linha["kcal_target"]), params, 80, None)
    assert float(linha["protein_g"]) == pytest.approx(esperado.protein_g, abs=0.01)
    assert float(linha["carbs_g"]) == pytest.approx(esperado.carb_g, abs=0.01)
    assert linha["day_kind"] == "normal"


# ---------------------------------------------------------------------------
# Preview: projeção, avisos e checagem das gramas
# ---------------------------------------------------------------------------


def test_preview_traz_projecao_com_a_premissa_de_estimativa_minima(db, hoje):
    db.composition.clear()
    db.add_weight(ALUNO, SEGUNDA - timedelta(days=3), 90, body_fat_percent=Decimal("25"), fat_free_mass_kg=Decimal("67.5"))
    cliente = _cliente()
    plano = _criar(cliente, strategy="cutting", goal_body_fat_percent=12).json()
    resposta = cliente.post(f"/nutrition/plans/{plano['id']}/preview")
    assert resposta.status_code == 200, resposta.text
    projecao = resposta.json()["projection"]
    assert projecao["status"] == "projected" and projecao["is_minimum_estimate"] is True
    assert projecao["weeks"] > 0 and projecao["eta_date"]
    assert any(item["code"] == "projection_lean_mass_constant" and item["label"] for item in projecao["reason_items"])


def test_preview_sem_alvo_explica_em_vez_de_inventar(db, hoje):
    cliente = _cliente()
    plano = _criar(cliente).json()
    projecao = cliente.post(f"/nutrition/plans/{plano['id']}/preview").json()["projection"]
    assert projecao["status"] == "insufficient_data" and projecao["weeks"] is None
    assert projecao["reason_items"]


def test_preview_do_bulking_avancado_nao_avisa_ritmo_e_o_superavit_alto_avisa_sem_recusar(db, hoje):
    db.composition.clear()
    db.add_weight(ALUNO, SEGUNDA - timedelta(days=3), 80, body_fat_percent=Decimal("15"), fat_free_mass_kg=Decimal("68"))
    cliente = _cliente()
    avancado = _criar(cliente, strategy="bulking", advanced=True)
    assert avancado.status_code == 201
    avisos = cliente.post(f"/nutrition/plans/{avancado.json()['id']}/preview").json()["warnings"]
    assert "projection_rate_outside_range" not in [item["code"] for item in avisos]  # o default do motor cabe na faixa
    alto = _criar(cliente, strategy="bulking", surplus_share=0.20)  # 20% implica ~0,83%/semana neste usuário
    assert alto.status_code == 201  # nunca recusa
    resposta = cliente.post(f"/nutrition/plans/{alto.json()['id']}/preview")
    assert resposta.status_code == 200, resposta.text
    assert "projection_rate_outside_range" in [item["code"] for item in resposta.json()["warnings"]]


def test_preview_com_gramas_manuais_devolve_a_checagem_do_motor(db, hoje):
    cliente = _cliente()
    plano = _criar(cliente, manual_targets={"protein_g": 110, "carb_g": 280, "fat_g": 70}).json()
    dados = cliente.post(f"/nutrition/plans/{plano['id']}/preview").json()
    check = dados["manual_check"]
    assert check["can_save"] is True and check["status"] == "warning"
    proteina = next(campo for campo in check["fields"] if campo["field"] == "protein_g")
    assert proteina["label"] and proteina["code_items"][0]["code"] == "manual_below_range:protein_g"
    assert dados["target"]["macros"]["protein_g"] == 110.0  # as gramas do usuário mandam
    assert "manual_below_range:protein_g" in [item["code"] for item in dados["warnings"]]


def test_publicar_com_gramas_que_deixaram_de_valer_e_recusado(db, hoje):
    cliente = _cliente()
    plano = _criar(cliente, manual_targets=MANUAL).json()
    db.composition.clear()  # o aluno ficou sem peso depois de salvar o rascunho
    resposta = cliente.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None})
    assert resposta.status_code == 400 and "manual_targets_require_weight" in _codigos(resposta)


# ---------------------------------------------------------------------------
# "Puxar do Modelo" (D5)
# ---------------------------------------------------------------------------


def test_puxar_do_modelo_propoe_dias_altos_nos_dias_de_treino(db):
    db.templates[7] = {
        "name": "ABC",
        "slots": [(1, "normal"), (2, "descanso"), (3, "normal"), (4, "descanso"), (5, "normal"), (6, "descanso"), (7, "descanso")],
    }
    dados = _cliente().get("/nutrition/cycle-days/from-template").json()
    assert dados["template"] == {"id": 7, "name": "ABC"}
    assert dados["training_weekdays"] == [0, 2, 4]
    assert dados["cycle_days"] == DIAS_ALTOS and dados["cycles"] is True and dados["notes"] == []


def test_puxar_do_modelo_sem_modelo_explica(db):
    dados = _cliente().get("/nutrition/cycle-days/from-template").json()
    assert dados["template"] is None and dados["cycle_days"] is None and dados["cycles"] is False
    assert [item["code"] for item in dados["notes"]] == ["template_not_assigned"]
    assert dados["notes"][0]["label"]


def test_puxar_do_modelo_sem_dia_da_semana_nos_slots_explica(db):
    db.templates[7] = {"name": "Ciclo", "slots": [(None, "normal"), (None, "descanso"), (None, "normal")]}
    dados = _cliente().get("/nutrition/cycle-days/from-template").json()
    assert dados["cycle_days"] is None and [item["code"] for item in dados["notes"]] == ["template_without_weekdays"]


def test_puxar_do_modelo_que_treina_todo_dia_diz_que_nao_cicla(db):
    db.templates[7] = {"name": "Full", "slots": [(i, "normal") for i in range(1, 8)]}
    dados = _cliente().get("/nutrition/cycle-days/from-template").json()
    assert dados["cycles"] is False and [item["code"] for item in dados["notes"]] == ["template_no_cycle"]


def test_a_proposta_do_modelo_serve_de_entrada_do_plano(db):
    db.templates[7] = {"name": "ABC", "slots": [(1, "normal"), (2, "descanso"), (3, "normal"), (4, "descanso"), (5, "normal"), (6, "descanso"), (7, "descanso")]}
    cliente = _cliente()
    proposta = cliente.get("/nutrition/cycle-days/from-template").json()["cycle_days"]
    proposta["6"] = "normal"  # o usuário ajusta antes de salvar
    salvo = _criar(cliente, cycle_days=proposta)
    assert salvo.status_code == 201 and salvo.json()["cycle_days"]["6"] == "normal"
    assert db.plans[salvo.json()["id"]]["carb_cycling_json"]["6"] == "normal"


def test_admin_puxa_o_modelo_de_um_aluno_e_comum_nao_alcanca_o_de_outro(db):
    db.templates[7] = {"name": "ABC", "slots": [(1, "normal"), (2, "descanso")]}
    assert _cliente("admin", 1).get(f"/nutrition/cycle-days/from-template?user_id={ALUNO}").status_code == 200
    assert _cliente("regular", 9).get(f"/nutrition/cycle-days/from-template?user_id={ALUNO}").status_code == 403

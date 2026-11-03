"""N2, rodada 2 de revisão: TDEE estável da projeção, projeção com gramas manuais exige massa gorda, bloqueios do
ciclo em campo próprio, publicação condicionada à versão do rascunho e percentual pronto no motor."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_plans as plans_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition import labels as lb
from app.domain.nutrition import plan as pl
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.phases import GoalProjection, ProjectionStatus
from app.security import Identity, get_current_identity
from tests.support.nutrition_fake import FakeNutritionDB

SEGUNDA = date(2026, 10, 12)
SABADO = date(2026, 10, 17)
ALUNO = 2
SO_PESO = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=90, activity_level=ActivityLevel.MODERATE)
GORDO = replace(SO_PESO, body_fat_percent=25.0)
MANUAL_CUT = {"protein_g": 180, "carb_g": 200, "fat_g": 70}
MANUAL_BULK = {"protein_g": 180, "carb_g": 500, "fat_g": 100}


# ---------------------------------------------------------------------------
# A: gramas manuais sem massa gorda
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("estrategia", "manual", "alvo"),
    [("cutting", MANUAL_CUT, {"goal_weight_kg": 80.0}), ("bulking", MANUAL_BULK, {"goal_weight_kg": 100.0})],
)
def test_gramas_manuais_sem_massa_gorda_nao_projetam(estrategia, manual, alvo):
    cfg = pl.build_plan_settings(strategy=estrategia, manual_targets=manual, **alvo).settings
    sem = pl.project_plan_goal(cfg, SO_PESO, 2800, SEGUNDA)
    assert sem.status == ProjectionStatus.INSUFFICIENT_DATA and sem.reasons == (pl.REASON_MANUAL_MISSING_FAT_MASS,)
    assert sem.weeks is None and sem.weekly_rate_share is None
    com = pl.project_plan_goal(cfg, GORDO, 2800, SEGUNDA)  # com %G a projeção volta
    assert com.status in (ProjectionStatus.PROJECTED, ProjectionStatus.INCOHERENT)
    assert lb.limitation_label(pl.REASON_MANUAL_MISSING_FAT_MASS) != pl.REASON_MANUAL_MISSING_FAT_MASS


# ---------------------------------------------------------------------------
# B: TDEE estável
# ---------------------------------------------------------------------------


def _tdee(modo, treino=0.0):
    return ee.tdee(1800.0, ActivityLevel.MODERATE, modo, treino)


def test_tdee_estavel_nao_depende_do_treino_do_dia():
    sem_treino = pl.projection_tdee(_tdee(ee.TdeeMode.EXPLICIT_TRAINING, 0.0), [0.0, 400.0, 0.0, 600.0])
    com_treino = pl.projection_tdee(_tdee(ee.TdeeMode.EXPLICIT_TRAINING, 900.0), [0.0, 400.0, 0.0, 600.0])
    assert sem_treino == com_treino
    base = 1800.0 * ee.activity_factor_for(ActivityLevel.MODERATE, ee.TdeeMode.EXPLICIT_TRAINING)
    assert sem_treino[0] == pytest.approx(base + 250.0) and sem_treino[1] == ()  # média de 4 dias conhecidos


def test_tdee_estavel_ignora_dia_de_treino_sem_kcal_e_sem_historico_usa_a_base_com_premissa():
    explicito = _tdee(ee.TdeeMode.EXPLICIT_TRAINING)
    base = 1800.0 * ee.activity_factor_for(ActivityLevel.MODERATE, ee.TdeeMode.EXPLICIT_TRAINING)
    assert pl.projection_tdee(explicito, [None, 300.0])[0] == pytest.approx(base + 300.0)
    assert pl.projection_tdee(explicito, [0.0, 0.0, None]) == (pytest.approx(base), (pl.REASON_TDEE_WITHOUT_TRAINING,))
    assert pl.projection_tdee(explicito, []) == (pytest.approx(base), (pl.REASON_TDEE_WITHOUT_TRAINING,))


def test_tdee_com_o_treino_no_fator_e_o_do_fator():
    incluido = _tdee(ee.TdeeMode.INCLUDED_IN_FACTOR, 700.0)
    assert pl.projection_tdee(incluido, [500.0, 0.0]) == (incluido.kcal, ())
    assert pl.projection_tdee(ee.tdee(None, ActivityLevel.MODERATE, ee.TdeeMode.EXPLICIT_TRAINING, 0.0), [100.0]) == (None, ())


def test_treino_do_dia_e_dia_sem_treino_ou_sem_kcal():
    assert pl.training_kcal_of_day(False, None) == 0.0
    assert pl.training_kcal_of_day(True, None) is None  # treino sem kcal, sem duração ou sem peso
    assert pl.training_kcal_of_day(True, 350) == 350.0


def test_premissa_do_tdee_so_aparece_quando_ele_entrou_no_ritmo_projetado():
    premissa = (pl.REASON_TDEE_WITHOUT_TRAINING,)
    manual = pl.build_plan_settings(strategy="cutting", goal_weight_kg=80.0, manual_targets=MANUAL_CUT).settings
    assert pl.REASON_TDEE_WITHOUT_TRAINING in pl.project_plan_goal(manual, GORDO, 2800, SEGUNDA, premissa).reasons
    bulking = pl.build_plan_settings(strategy="bulking", goal_weight_kg=100.0).settings
    assert pl.REASON_TDEE_WITHOUT_TRAINING in pl.project_plan_goal(bulking, GORDO, 2800, SEGUNDA, premissa).reasons
    # Cutting automático projeta com o ritmo do plano: o TDEE não entrou, sem premissa.
    cutting = pl.build_plan_settings(strategy="cutting", goal_weight_kg=80.0).settings
    assert pl.REASON_TDEE_WITHOUT_TRAINING not in pl.project_plan_goal(cutting, GORDO, 2800, SEGUNDA, premissa).reasons
    # Nem quando a projeção não saiu (sem massa gorda).
    sem_gordura = pl.project_plan_goal(manual, SO_PESO, 2800, SEGUNDA, premissa)
    assert sem_gordura.reasons == (pl.REASON_MANUAL_MISSING_FAT_MASS,)


def test_percentual_do_ritmo_vem_pronto_do_motor():
    assert GoalProjection(status=ProjectionStatus.PROJECTED, goal_kind=None, weekly_rate_share=0.007).to_dict()[
        "weekly_rate_percent"
    ] == pytest.approx(0.7)
    assert GoalProjection(status=ProjectionStatus.INSUFFICIENT_DATA, goal_kind=None).to_dict()["weekly_rate_percent"] is None


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


@pytest.fixture
def db(monkeypatch):
    banco = FakeNutritionDB()
    banco.add_weight(ALUNO, SABADO - timedelta(days=3), 90, body_fat_percent=Decimal("25"), fat_free_mass_kg=Decimal("67.5"))
    monkeypatch.setattr(plans_mod, "get_conn", banco.conn)
    monkeypatch.setattr(plans_mod, "_today_for", lambda conn, user_id: SABADO)
    monkeypatch.setattr(plans_mod, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    return banco


def _cliente(role="regular", user_id=ALUNO):
    app = FastAPI()
    app.include_router(plans_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _criar(cliente, **corpo):
    return cliente.post("/nutrition/plans", json={"strategy": "maintenance", **corpo}).json()


def _projecao(cliente, plano_id):
    resposta = cliente.post(f"/nutrition/plans/{plano_id}/preview")
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["projection"]


def test_mesmo_plano_com_e_sem_treino_hoje_tem_o_mesmo_veredito_e_prazo(db):
    cliente = _cliente()
    plano = _criar(cliente, strategy="cutting", goal_weight_kg=80, manual_targets=MANUAL_CUT)
    sem = _projecao(cliente, plano["id"])
    db.add_session(ALUNO, SABADO, 900)  # treino de hoje
    com = _projecao(cliente, plano["id"])
    assert sem["status"] == com["status"] == "projected"
    assert sem["weeks"] == com["weeks"] and sem["weekly_rate_percent"] == com["weekly_rate_percent"]
    assert "projection_tdee_without_training" in [i["code"] for i in sem["reason_items"]]  # sem histórico: premissa declarada


def test_historico_de_treino_dos_dias_passados_entra_no_tdee_da_projecao(db):
    cliente = _cliente()
    plano = _criar(cliente, strategy="cutting", goal_weight_kg=80, manual_targets=MANUAL_CUT)
    sem = _projecao(cliente, plano["id"])
    for dias in (1, 3, 5):
        db.add_session(ALUNO, SABADO - timedelta(days=dias), 600)
    com = _projecao(cliente, plano["id"])
    assert "projection_tdee_without_training" not in [i["code"] for i in com["reason_items"]]
    assert com["weekly_rate_percent"] > sem["weekly_rate_percent"] and com["weeks"] < sem["weeks"]


def test_projecao_com_gramas_manuais_sem_percentual_de_gordura_explica(db):
    db.composition.clear()
    db.add_weight(ALUNO, SABADO - timedelta(days=3), 90)
    cliente = _cliente()
    plano = _criar(cliente, strategy="cutting", goal_weight_kg=80, manual_targets=MANUAL_CUT)
    proj = _projecao(cliente, plano["id"])
    assert proj["status"] == "insufficient_data" and proj["weeks"] is None
    assert "projection_manual_missing_fat_mass" in [i["code"] for i in proj["reason_items"]]


UM_ALTO_SEIS_BAIXOS = {str(i): "high" if i == 0 else "low" for i in range(7)}


def test_bloqueio_do_ciclo_fica_em_campo_proprio_e_desabilita_publicar(db):
    db.composition.clear()
    db.add_weight(ALUNO, SABADO - timedelta(days=3), 50)
    db.profiles[ALUNO] = {**db.profiles[ALUNO], "sex": "F", "height_cm": Decimal("160.0"), "activity_level": "sedentary"}
    # Rascunho que o salvar recusaria (gravado por fora da validação, como um dado antigo).
    plano_id = db.add_plan(ALUNO, manual_targets_json={"protein_g": 45, "carb_g": 100, "fat_g": 25}, carb_cycling_json=UM_ALTO_SEIS_BAIXOS)
    dados = _cliente().post(f"/nutrition/plans/{plano_id}/preview").json()
    assert dados["can_publish"] is False
    assert "cycle_kcal_below_minimum" in [i["code"] for i in dados["blocking"]] and all(i["label"] for i in dados["blocking"])
    assert "cycle_kcal_below_minimum" not in [i["code"] for i in dados["warnings"]]


def test_plano_sem_bloqueio_pode_publicar(db):
    cliente = _cliente()
    plano = _criar(cliente)
    dados = cliente.post(f"/nutrition/plans/{plano['id']}/preview").json()
    assert dados["can_publish"] is True and dados["blocking"] == []


# ---------------------------------------------------------------------------
# E: publicação condicionada à versão
# ---------------------------------------------------------------------------


def test_edicao_concorrente_depois_da_conferencia_vira_409_e_nao_publica(db, monkeypatch):
    cliente = _cliente()
    plano = _criar(cliente)
    versao = plano["updated_at"]
    real = plans_mod.db_now

    def edicao_no_meio(cur):  # outra aba edita o rascunho entre a conferência e o UPDATE da publicação
        db.plans[plano["id"]]["updated_at"] = db.now + timedelta(seconds=30)
        return real(cur)

    monkeypatch.setattr(plans_mod, "db_now", edicao_no_meio)
    resposta = cliente.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None, "expected_updated_at": versao})
    assert resposta.status_code == 409 and resposta.json()["detail"]["error"] == "draft_changed"
    assert db.plans[plano["id"]]["status"] == "draft" and db.targets == {}


def test_sem_expected_updated_at_a_publicacao_segue_como_antes(db):
    cliente = _cliente()
    plano = _criar(cliente)
    assert cliente.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None}).status_code == 200


def test_sessao_so_com_duracao_na_janela_entra_na_media_e_nao_gera_a_premissa(db):
    cliente = _cliente()
    plano = _criar(cliente, strategy="cutting", goal_weight_kg=80, manual_targets=MANUAL_CUT)
    sem = _projecao(cliente, plano["id"])
    for dias in (1, 3):
        db.add_session(ALUNO, SABADO - timedelta(days=dias), None, seconds=3600)  # sem kcal: o motor estima por MET
    com = _projecao(cliente, plano["id"])
    assert "projection_tdee_without_training" in [i["code"] for i in sem["reason_items"]]
    assert "projection_tdee_without_training" not in [i["code"] for i in com["reason_items"]]
    assert com["weekly_rate_percent"] > sem["weekly_rate_percent"]


def test_aviso_de_ritmo_do_bulking_nao_varia_com_o_treino_de_hoje(db):
    cliente = _cliente()
    plano = _criar(cliente, strategy="bulking", advanced=True)
    antes = cliente.post(f"/nutrition/plans/{plano['id']}/preview").json()["warnings"]
    db.add_session(ALUNO, SABADO, 1500)
    depois = cliente.post(f"/nutrition/plans/{plano['id']}/preview").json()["warnings"]
    assert [i["code"] for i in antes] == [i["code"] for i in depois]


def test_zero_linhas_na_publicacao_condicionada_desfaz_a_substituicao_do_vigente(db, monkeypatch):
    cliente = _cliente()
    vigente = _criar(cliente)
    assert cliente.post(f"/nutrition/plans/{vigente['id']}/publish", json={"expected_current_id": None}).status_code == 200
    novo = _criar(cliente)
    real = plans_mod.db_now

    def edicao_no_meio(cur):
        db.plans[novo["id"]]["updated_at"] = db.now + timedelta(seconds=30)
        return real(cur)

    monkeypatch.setattr(plans_mod, "db_now", edicao_no_meio)
    resposta = cliente.post(
        f"/nutrition/plans/{novo['id']}/publish", json={"expected_current_id": vigente["id"], "expected_updated_at": novo["updated_at"]}
    )
    assert resposta.status_code == 409 and resposta.json()["detail"]["error"] == "draft_changed"
    # O SUPERSEDE executado antes foi desfeito: o vigente segue vigente e o novo segue rascunho.
    assert db.plans[vigente["id"]]["superseded_at"] is None
    assert db.plans[novo["id"]]["status"] == "draft"

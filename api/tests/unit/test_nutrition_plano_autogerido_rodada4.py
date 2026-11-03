"""N2, rodada 4 (escolhas do usuário): gramas manuais com o treino por plano (`fixed` | `add_training`) e faixas de
ritmo do bulking alinhadas ao superávit (grade realista)."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_plans as plans_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition import DayContext, PlanParams, compute_daily_target
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition import macros as mc
from app.domain.nutrition import plan as pl
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.daily_target import LIMIT_MANUAL_TARGETS_IGNORE_PHASE
from app.domain.nutrition.manual_targets import DEFAULT_MANUAL_TRAINING_MODE, ManualTrainingMode
from app.domain.nutrition.phases import PhaseKind
from app.security import Identity, get_current_identity
from tests.support.nutrition_fake import FakeNutritionDB

SEGUNDA = date(2026, 10, 12)
SABADO = date(2026, 10, 17)
ALUNO = 2
S = st.Strategy
FIXED, ADD = ManualTrainingMode.FIXED, ManualTrainingMode.ADD_TRAINING
SUJEITO = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, activity_level=ActivityLevel.MODERATE)
COM_MLG = replace(SUJEITO, body_fat_percent=15.0)
TREINO = 600.0


def _plano(estrategia=S.MAINTENANCE, modo=FIXED, manual=(160.0, 300.0, 70.0), **extra):
    return PlanParams(
        strategy_params=st.StrategyParams.defaults_for(estrategia), manual_macros=manual, manual_training_mode=modo, **extra
    )


def _dia(treino=False, **extra):
    if treino:
        return DayContext(day=SEGUNDA, is_training_day=True, training_kcal_reported=TREINO, **extra)
    return DayContext(day=SEGUNDA, **extra)


# ---------------------------------------------------------------------------
# Modo das gramas manuais
# ---------------------------------------------------------------------------


def test_modo_fixo_e_o_padrao_e_nao_acompanha_o_treino():
    assert DEFAULT_MANUAL_TRAINING_MODE == FIXED
    repouso = compute_daily_target(COM_MLG, _plano(), _dia())
    treino = compute_daily_target(COM_MLG, _plano(), _dia(True))
    for meta in (repouso, treino):
        assert (meta.macros.protein_g, meta.macros.carb_g, meta.macros.fat_g) == (160.0, 300.0, 70.0)


def test_modo_soma_treino_soma_o_gasto_do_dia_em_carbo_pela_conversao_do_dono():
    repouso = compute_daily_target(COM_MLG, _plano(modo=ADD), _dia())
    treino = compute_daily_target(COM_MLG, _plano(modo=ADD), _dia(True))
    assert (repouso.macros.protein_g, repouso.macros.carb_g, repouso.macros.fat_g) == (160.0, 300.0, 70.0)
    assert treino.macros.carb_g == pytest.approx(300.0 + TREINO / mc.KCAL_PER_G_CARB)
    assert treino.macros.protein_g == 160.0 and treino.macros.fat_g == 70.0
    assert treino.macros.kcal == pytest.approx(mc.kcal_from_macros(160, treino.macros.carb_g, 70))
    assert treino.macros.kcal - repouso.macros.kcal == pytest.approx(TREINO)  # o saldo contra o gasto não muda


def test_modo_soma_treino_sem_treino_a_parte_nao_soma_nada():
    embutido = _plano(modo=ADD, tdee_mode=ee.TdeeMode.INCLUDED_IN_FACTOR)
    assert compute_daily_target(COM_MLG, embutido, _dia(True)).macros.carb_g == 300.0


def test_pausa_e_refeed_mantem_o_aviso_nos_dois_modos():
    for modo in (FIXED, ADD):
        meta = compute_daily_target(COM_MLG, _plano(S.CUTTING, modo), _dia(phase_kind=PhaseKind.REFEED))
        assert LIMIT_MANUAL_TARGETS_IGNORE_PHASE in meta.limitations
        assert meta.macros.carb_g == 300.0


def test_aviso_de_energia_no_modo_soma_treino_compara_com_o_dia_sem_treino():
    repouso = compute_daily_target(COM_MLG, replace(_plano(), manual_macros=None), _dia())
    kcal = repouso.kcal_target.kcal
    gramas = (160.0, (kcal - 160 * 4 - 70 * 9) / 4, 70.0)  # exatamente a meta de repouso da estratégia
    add = compute_daily_target(COM_MLG, _plano(modo=ADD, manual=gramas), _dia(True))
    fixo = compute_daily_target(COM_MLG, _plano(modo=FIXED, manual=gramas), _dia(True))
    assert "manual_differs_from_target:kcal" not in add.limitations
    assert "manual_differs_from_target:kcal" in fixo.limitations  # no fixo, a meta do dia de treino ficou 600 kcal acima




def test_ciclo_com_modo_soma_treino_preserva_a_soma_do_carbo():
    from app.domain.nutrition import carb_cycling as cc

    D = cc.DayKind
    kinds = (D.HIGH, D.LOW, D.HIGH, D.LOW, D.HIGH, D.LOW, D.LOW)
    sem = []
    com = []
    for i in range(7):
        ctx = DayContext(day=SEGUNDA + timedelta(days=i), is_training_day=i in (0, 2, 4), training_kcal_reported=TREINO if i in (0, 2, 4) else None)
        sem.append(compute_daily_target(COM_MLG, _plano(modo=ADD), ctx))
        com.append(compute_daily_target(COM_MLG, _plano(modo=ADD, cycle_kinds=kinds), ctx))
    assert sum(m.macros.carb_g for m in com) == pytest.approx(sum(m.macros.carb_g for m in sem))
    assert sum(m.macros.carb_g for m in sem) == pytest.approx(7 * 300.0 + 3 * TREINO / 4)


# ---------------------------------------------------------------------------
# Validação, armazenamento e retrocompatibilidade
# ---------------------------------------------------------------------------


MANUAL = {"protein_g": 160, "carb_g": 300, "fat_g": 70}


def test_modo_valido_padrao_e_invalido():
    assert pl.build_plan_settings(strategy="maintenance", manual_targets=MANUAL).settings.manual_macros.training_mode == FIXED
    ok = pl.build_plan_settings(strategy="maintenance", manual_targets=MANUAL, manual_training_mode="add_training")
    assert ok.ok and ok.settings.manual_macros.training_mode == ADD and ok.settings.manual_adds_training
    assert "invalid_code:manual_training_mode" in pl.build_plan_settings(
        strategy="maintenance", manual_targets=MANUAL, manual_training_mode="x"
    ).errors
    assert "param_not_applicable:manual_training_mode" in pl.build_plan_settings(
        strategy="maintenance", manual_training_mode="add_training"
    ).errors
    assert pl.build_plan_settings(strategy="maintenance", manual_training_mode="fixed").ok  # o padrão sem gramas é inofensivo


def test_com_o_gasto_embutido_o_modo_soma_treino_nao_soma():
    cfg = pl.build_plan_settings(
        strategy="maintenance", manual_targets=MANUAL, manual_training_mode="add_training", tdee_mode="included_in_factor"
    ).settings
    assert cfg.manual_macros.training_mode == ADD and cfg.manual_adds_training is False


def test_plano_antigo_e_hash_inalterados():
    sem_manual = pl.build_plan_settings(strategy="cutting").settings
    assert set(sem_manual.fingerprint_dict()) == {
        "strategy", "advanced", "protein_g_per_kg", "protein_basis", "fat_kcal_share", "weekly_rate_share",
        "surplus_share", "carb_min_g_per_kg", "bmr_formula", "tdee_mode", "activity_level", "meals_per_day",
        "goal_body_fat_percent", "goal_weight_kg", "carb_cycling_enabled",
    }
    fixo = pl.build_plan_settings(strategy="maintenance", manual_targets=MANUAL).settings
    # No modo fixo o hash do plano de gramas manuais é o de antes da rodada 4 (sem a chave do modo).
    assert fixo.fingerprint_dict()["manual_targets"] == {"protein_g": 160.0, "carb_g": 300.0, "fat_g": 70.0}
    add = pl.build_plan_settings(strategy="maintenance", manual_targets=MANUAL, manual_training_mode="add_training").settings
    assert add.fingerprint_dict()["manual_targets"]["training_mode"] == "add_training"
    assert "manual_training_mode" not in _plano().to_dict()  # PlanParams do modo fixo não ganha chave


def test_linha_do_banco_com_e_sem_o_modo():
    base = {"strategy": "maintenance", "advanced": 0, "bmr_formula": "auto", "training_kcal_mode": "explicit_training",
            "protein_g_per_kg": 2.0, "fat_kcal_share": 0.25}
    antiga = svc.settings_from_row({**base, "manual_targets_json": '{"protein_g": 160, "carb_g": 300, "fat_g": 70}'})
    assert antiga.manual_macros.training_mode == FIXED
    nova = svc.settings_from_row(
        {**base, "manual_targets_json": {"protein_g": 160, "carb_g": 300, "fat_g": 70, "training_mode": "add_training"}}
    )
    assert nova.manual_macros.training_mode == ADD and nova.manual_macros.as_tuple() == (160.0, 300.0, 70.0)


# ---------------------------------------------------------------------------
# Pisos e projeção coerentes com o modo
# ---------------------------------------------------------------------------


def test_pisos_do_ciclo_sao_os_do_dia_sem_treino_nos_dois_modos():
    leve = Subject(sex=Sex.FEMALE, age_years=30, height_cm=160, weight_kg=50, activity_level=ActivityLevel.SEDENTARY)
    dias = {0: "high", 1: "low", 2: "low", 3: "low", 4: "low", 5: "low", 6: "low"}
    manual = {"protein_g": 45, "carb_g": 100, "fat_g": 25}
    resultados = []
    for modo in ("fixed", "add_training"):
        cfg = pl.build_plan_settings(strategy="maintenance", manual_targets=manual, cycle_days=dias, manual_training_mode=modo).settings
        resultados.append(pl.check_cycle_floors(cfg, leve))
    assert resultados[0] == resultados[1] and resultados[0][0] == (pl.ERR_CYCLE_KCAL_BELOW_MIN,)


def test_tdee_da_projecao_nao_conta_o_treino_duas_vezes_no_modo_soma_treino():
    tdee = ee.tdee(1800.0, ActivityLevel.MODERATE, ee.TdeeMode.EXPLICIT_TRAINING, 700.0)
    base = 1800.0 * ee.activity_factor_for(ActivityLevel.MODERATE, ee.TdeeMode.EXPLICIT_TRAINING)
    historico = [0.0, 500.0, 0.0, 800.0]
    assert pl.projection_tdee(tdee, historico, training_in_targets=True) == (pytest.approx(base), ())
    assert pl.projection_tdee(tdee, historico)[0] == pytest.approx(base + 325.0)  # no fixo o treino médio entra


@pytest.fixture
def db(monkeypatch):
    banco = FakeNutritionDB()
    banco.add_weight(ALUNO, SABADO - timedelta(days=3), 90, body_fat_percent=Decimal("25"), fat_free_mass_kg=Decimal("67.5"))
    monkeypatch.setattr(plans_mod, "get_conn", banco.conn)
    monkeypatch.setattr(plans_mod, "_today_for", lambda conn, user_id: SABADO)
    monkeypatch.setattr(plans_mod, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    return banco


def _cliente():
    app = FastAPI()
    app.include_router(plans_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=ALUNO, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _projecao(cliente, corpo):
    plano = cliente.post("/nutrition/plans", json={"strategy": "cutting", "goal_weight_kg": 80, "manual_targets": {"protein_g": 200, "carb_g": 150, "fat_g": 60}, **corpo})
    assert plano.status_code == 201, plano.text
    return plano.json(), cliente.post(f"/nutrition/plans/{plano.json()['id']}/preview").json()


def test_rota_grava_o_modo_junto_das_gramas_e_a_projecao_nao_depende_do_treino_no_modo_soma(db):
    cliente = _cliente()
    plano, previa = _projecao(cliente, {"manual_training_mode": "add_training"})
    assert plano["manual_targets"]["training_mode"] == "add_training"
    assert db.plans[plano["id"]]["manual_targets_json"]["training_mode"] == "add_training"
    sem = previa["projection"]
    for dias in (1, 3, 5):
        db.add_session(ALUNO, SABADO - timedelta(days=dias), 600)
    com = cliente.post(f"/nutrition/plans/{plano['id']}/preview").json()["projection"]
    assert sem["weeks"] == com["weeks"] and sem["weekly_rate_percent"] == com["weekly_rate_percent"]
    assert "projection_tdee_without_training" not in [i["code"] for i in com["reason_items"]]


def test_no_modo_fixo_o_treino_dos_dias_passados_continua_entrando_na_projecao(db):
    cliente = _cliente()
    plano, previa = _projecao(cliente, {})
    for dias in (1, 3, 5):
        db.add_session(ALUNO, SABADO - timedelta(days=dias), 600)
    com = cliente.post(f"/nutrition/plans/{plano['id']}/preview").json()["projection"]
    assert com["weeks"] < previa["projection"]["weeks"]


# ---------------------------------------------------------------------------
# Faixas de ritmo do bulking alinhadas ao superávit
# ---------------------------------------------------------------------------

PESOS = range(55, 111, 5)
TDEES = range(1800, 3501, 100)
GORDURAS = range(8, 31, 2)


def _avisa(avancado, peso, tdee, gordura):
    cfg = pl.build_plan_settings(strategy="bulking", advanced=avancado).settings  # os defaults do motor
    sujeito = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=float(peso), body_fat_percent=float(gordura),
                      activity_level=ActivityLevel.MODERATE)
    return pl.plan_warnings(cfg, sujeito, float(tdee)) == (pl.REASON_RATE_OUTSIDE_RANGE,)


def _tipico(peso, tdee, gordura):
    return 30 <= tdee / peso <= 40 and 10 <= gordura <= 25  # TDEE de 30 a 40 kcal/kg e %G de 10 a 25


@pytest.mark.parametrize("avancado", [False, True])
def test_default_do_motor_nunca_gera_aviso_de_ritmo_no_caso_tipico(avancado):
    tipicos = [(p, t, g) for p, t, g in itertools.product(PESOS, TDEES, GORDURAS) if _tipico(p, t, g)]
    assert len(tipicos) > 500
    fora = [caso for caso in tipicos if _avisa(avancado, *caso)]
    assert fora == []


@pytest.mark.parametrize("avancado", [False, True])
def test_fora_do_caso_tipico_a_faixa_ainda_depende_do_individuo(avancado):
    """A razão TDEE/peso varia quase 4 vezes na grade, mais que a largura de qualquer faixa: há combinações
    realistas fora do tipo em que o default avisa (só aviso, nunca recusa)."""
    atipicos = [(p, t, g) for p, t, g in itertools.product(PESOS, TDEES, GORDURAS) if not _tipico(p, t, g)]
    assert any(_avisa(avancado, *caso) for caso in atipicos)


def test_faixas_do_ritmo_do_bulking_coerentes_entre_si():
    padrao, avancado = st.weekly_rate_share_range_for(S.BULKING), st.weekly_rate_share_range_for(S.BULKING, True)
    assert avancado[0] < padrao[0] and avancado[1] < padrao[1] and avancado[1] > padrao[0] * 0.9
    assert st.weekly_rate_share_range_for(S.CUTTING) == st.CUT_WEEKLY_RATE_SHARE_RANGE == st.weekly_rate_share_range_for(S.CUTTING, True)
    assert st.weekly_rate_share_range_for(S.MAINTENANCE) is None


def test_plano_de_bulking_existente_nao_muda_de_hash_nem_de_meta():
    """Só a faixa de AVISO mudou: superávit, defaults e metas não; o hash das entradas não contém a faixa."""
    for avancado, superavit in ((False, 0.10), (True, 0.05)):
        cfg = pl.build_plan_settings(strategy="bulking", advanced=avancado, surplus_share=superavit).settings
        assert cfg.surplus_share == superavit
        assert "weekly_rate_range" not in cfg.fingerprint_dict()
    meta = compute_daily_target(COM_MLG, PlanParams(strategy_params=st.StrategyParams.defaults_for(S.BULKING)), DayContext(day=SEGUNDA))
    assert meta.kcal_target.kcal == pytest.approx(meta.tdee.kcal * 1.10)
    assert st.BULK_WEEKLY_RATE_SHARE_PROJECTION_DEFAULT == 0.0025  # projeção sem TDEE: o valor que o N1 gravou

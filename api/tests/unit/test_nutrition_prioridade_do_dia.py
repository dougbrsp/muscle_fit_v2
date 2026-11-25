"""Prioridade nutricional do dia (#272): dono `carb_cycling.day_priority`, integrada por `daily_target.py`, rotulada por
`labels.py` e entregue pelo serviço de metas. O rótulo reflete os macros FINAIS, não só o tipo de dia do calendário."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import nutrition_targets_service as svc
from app.domain.nutrition import DayContext, PlanParams, compute_daily_target
from app.domain.nutrition import carb_cycling as cc
from app.domain.nutrition import labels as lb
from app.domain.nutrition import macros as mc
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.carb_cycling import DayPriority as P
from app.domain.nutrition.daily_target import ENGINE_VERSION, TargetWindow
from tests.support.nutrition_fake import FakeNutritionDB

H, N, L = cc.DayKind.HIGH, cc.DayKind.NORMAL, cc.DayKind.LOW
FAT = cc.CarbCyclePolicy.FAT_COMPENSATION
SEGUNDA = date(2026, 10, 12)
SUJEITO = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, activity_level=ActivityLevel.MODERATE)
SEMANA = (H, L, H, L, H, L, L)
COM_NORMAL = (H, N, L, L, L, L, L)
MANUAL = (160.0, 250.0, 70.0)


def _alvo(proteina=160.0, carbo=250.0, gordura=70.0) -> mc.MacroTargets:
    kcal = mc.kcal_from_macros(proteina, carbo, gordura)
    return mc.MacroTargets(
        kcal=kcal, protein_g=proteina, carb_g=carbo, fat_g=gordura, fiber_g=mc.fiber_target_g(kcal),
        protein_basis=st.ProteinBasis.BODY_WEIGHT, protein_basis_kg=80.0, protein_g_per_kg=2.0,
    )


def _nao_calculavel() -> mc.MacroTargets:
    return mc.MacroTargets(
        kcal=None, protein_g=None, carb_g=None, fat_g=None, fiber_g=None,
        protein_basis=st.ProteinBasis.BODY_WEIGHT, protein_basis_kg=None, protein_g_per_kg=None,
    )


def _plano(**extra) -> PlanParams:
    return PlanParams(strategy_params=st.StrategyParams.defaults_for(st.Strategy.MAINTENANCE), **extra)


def _dia(i: int, **extra) -> DayContext:
    return DayContext(day=SEGUNDA + timedelta(days=i), **extra)


# --- dono ------------------------------------------------------------------------------------------------------


def test_dono_alto_com_mais_carbo_prioriza_carbo():
    assert cc.day_priority(H, _alvo(), _alvo(carbo=300.0)) == P.CARB


def test_dono_baixo_com_mais_gordura_prioriza_gordura():
    assert cc.day_priority(L, _alvo(), _alvo(carbo=200.0, gordura=90.0)) == P.FAT


def test_dono_baixo_legado_sem_mais_gordura_e_carbo_reduzido():
    assert cc.day_priority(L, _alvo(), _alvo(carbo=200.0)) == P.REDUCED_CARB


@pytest.mark.parametrize("final", [_alvo(), _alvo(carbo=300.0), _alvo(carbo=200.0, gordura=90.0)])
def test_dono_dia_normal_e_sempre_normal_mesmo_com_macros_diferentes(final):
    assert cc.day_priority(N, _alvo(), final) == P.NORMAL


@pytest.mark.parametrize("kind", [H, L])
def test_dono_ciclo_sem_efeito_nos_macros_finais_e_normal(kind):
    assert cc.day_priority(kind, _alvo(), _alvo()) == P.NORMAL


def test_dono_dia_alto_com_menos_carbo_final_nao_vira_carb():
    assert cc.day_priority(H, _alvo(), _alvo(carbo=200.0)) == P.NORMAL


def test_dono_tolerancia_em_gramas():
    pouco = cc.DAY_PRIORITY_CARB_TOLERANCE_G / 2
    assert cc.day_priority(H, _alvo(), _alvo(carbo=250.0 + pouco)) == P.NORMAL
    assert cc.day_priority(L, _alvo(), _alvo(carbo=250.0 - pouco, gordura=90.0)) == P.NORMAL
    assert cc.day_priority(H, _alvo(), _alvo(carbo=250.0 + 2 * cc.DAY_PRIORITY_CARB_TOLERANCE_G)) == P.CARB


def test_dono_gordura_dentro_da_tolerancia_nao_conta_como_maior():
    pouco = cc.DAY_PRIORITY_CARB_TOLERANCE_G / 2
    assert cc.day_priority(L, _alvo(), _alvo(carbo=200.0, gordura=70.0 + pouco)) == P.REDUCED_CARB


@pytest.mark.parametrize("antes,depois", [(_nao_calculavel(), _alvo()), (_alvo(), _nao_calculavel())])
@pytest.mark.parametrize("kind", [H, N, L])
def test_dono_meta_nao_calculavel_e_indisponivel(antes, depois, kind):
    assert cc.day_priority(kind, antes, depois) == P.UNAVAILABLE


# --- motor de ponta a ponta ------------------------------------------------------------------------------------


def _prio(plano: PlanParams, i: int, **extra):
    return compute_daily_target(SUJEITO, plano, _dia(i, **extra)).nutrition_priority


def test_motor_alto_baixo_legado():
    plano = _plano(manual_macros=MANUAL, cycle_kinds=SEMANA)
    assert _prio(plano, 0) == P.CARB and _prio(plano, 1) == P.REDUCED_CARB


def test_motor_baixo_com_compensacao_efetiva_prioriza_gordura():
    plano = _plano(cycle_kinds=SEMANA, carb_cycle_policy=FAT)
    assert _prio(plano, 0) == P.CARB and _prio(plano, 1) == P.FAT


def test_motor_gramas_manuais_com_compensacao_viram_vary_kcal_e_nao_priorizam_gordura():
    plano = _plano(manual_macros=MANUAL, cycle_kinds=SEMANA, carb_cycle_policy=FAT)
    meta = compute_daily_target(SUJEITO, plano, _dia(1))
    assert cc.LIMIT_FAT_COMPENSATION_IGNORED_MANUAL in meta.limitations
    assert meta.nutrition_priority != P.FAT


def test_motor_dia_normal_com_multiplicador_diferente_de_um_e_normal():
    plano = _plano(manual_macros=MANUAL, cycle_kinds=COM_NORMAL)
    meta = compute_daily_target(SUJEITO, plano, _dia(1))
    assert meta.day_kind == N and meta.nutrition_priority == P.NORMAL


def test_motor_semana_plana_pelo_piso_e_normal():
    # carbo minúsculo: o piso de carbo prende o ciclo e o dia sai igual ao pré-ciclo
    plano = _plano(manual_macros=(160.0, 20.0, 70.0), cycle_kinds=SEMANA)
    metas = [compute_daily_target(SUJEITO, plano, _dia(i)) for i in range(7)]
    assert any(cc.LIMIT_CARB_FLOOR_PREVENTS_CYCLE in m.limitations for m in metas)
    for m in metas:
        if m.macros.carb_g == 20.0:
            assert m.nutrition_priority == P.NORMAL


def test_motor_sem_ciclo_e_distribuicao_normal():
    assert _prio(_plano(), 0) == P.NORMAL
    assert _prio(_plano(manual_macros=MANUAL), 0) == P.NORMAL


def test_motor_ciclo_legado_por_treino():
    plano = _plano(carb_cycling_enabled=True)
    alto = [compute_daily_target(SUJEITO, plano, _dia(i, week_training_days=(True, False, True, False, True, False, False)))
            for i in range(7)]
    assert {m.nutrition_priority for m in alto} <= {P.CARB, P.REDUCED_CARB, P.NORMAL}
    assert alto[0].nutrition_priority == P.CARB


def test_motor_legado_sem_semana_nao_inventa_prioridade_de_ciclo():
    meta = compute_daily_target(SUJEITO, _plano(carb_cycling_enabled=True), _dia(0))
    assert meta.nutrition_priority == P.NORMAL


def test_motor_meta_nao_calculavel_e_indisponivel_com_e_sem_ciclo():
    sem_dados = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=None, activity_level=ActivityLevel.MODERATE)
    for plano in (_plano(), _plano(cycle_kinds=SEMANA)):
        meta = compute_daily_target(sem_dados, plano, _dia(0))
        assert not meta.computable and meta.nutrition_priority == P.UNAVAILABLE


def test_to_dict_inclui_a_chave():
    meta = compute_daily_target(SUJEITO, _plano(manual_macros=MANUAL, cycle_kinds=SEMANA), _dia(0))
    assert meta.to_dict()["nutrition_priority"] == "carb"
    assert compute_daily_target(SUJEITO, _plano(), _dia(0)).to_dict()["nutrition_priority"] == "normal"


# --- rótulos ---------------------------------------------------------------------------------------------------


def test_rotulos_cobrem_tudo_menos_indisponivel_e_nao_vao_para_o_espelho():
    esperados = set(P) - {P.UNAVAILABLE}
    assert set(lb.DAY_PRIORITY_LABELS) == esperados == set(lb.DAY_PRIORITY_EXPLANATIONS)
    assert all(t and t == t.strip() for m in (lb.DAY_PRIORITY_LABELS, lb.DAY_PRIORITY_EXPLANATIONS) for t in m.values())
    assert len(set(lb.DAY_PRIORITY_LABELS.values())) == len(esperados)
    from app.domain import nutrition

    assert "day_priority" not in json.dumps(nutrition.export_for_frontend())


# --- serviço e contrato ----------------------------------------------------------------------------------------


def _comp(meta):
    return SimpleNamespace(day=meta.day, target=meta, context=SimpleNamespace(is_training_day=False, training_known=True))


def test_day_public_traz_os_tres_campos():
    meta = compute_daily_target(SUJEITO, _plano(cycle_kinds=SEMANA, carb_cycle_policy=FAT), _dia(1))
    dia = svc.day_public(_comp(meta), SEGUNDA)
    assert dia["nutrition_priority"] == "fat"
    assert dia["nutrition_priority_label"] == lb.DAY_PRIORITY_LABELS[P.FAT]
    assert dia["nutrition_priority_explanation"] == lb.DAY_PRIORITY_EXPLANATIONS[P.FAT]


def test_day_public_indisponivel_tem_codigo_e_texto_nulo():
    sem_dados = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=None, activity_level=ActivityLevel.MODERATE)
    dia = svc.day_public(_comp(compute_daily_target(sem_dados, _plano(), _dia(0))), SEGUNDA)
    assert dia["nutrition_priority"] == "unavailable"
    assert dia["nutrition_priority_label"] is None and dia["nutrition_priority_explanation"] is None


def _linha(valor):
    return {"local_date": SEGUNDA, "plan_id": 1, "nutrition_priority_json": valor}


def test_target_row_public_com_a_chave():
    # o banco devolve o JSON_EXTRACT como texto JSON (aspas incluídas)
    for bruto in ('"reduced_carb"', "reduced_carb"):
        linha = svc.target_row_public(_linha(bruto), SEGUNDA)
        assert linha["nutrition_priority"] == "reduced_carb"
        assert linha["nutrition_priority_label"] == lb.DAY_PRIORITY_LABELS[P.REDUCED_CARB]
        assert linha["nutrition_priority_explanation"] == lb.DAY_PRIORITY_EXPLANATIONS[P.REDUCED_CARB]


@pytest.mark.parametrize("bruto", [None, "", "null", '"codigo_que_nao_existe"', "123"])
def test_target_row_public_dia_antigo_ou_codigo_desconhecido_vira_tres_nulos(bruto):
    linha = svc.target_row_public(_linha(bruto), SEGUNDA)
    assert (linha["nutrition_priority"], linha["nutrition_priority_label"], linha["nutrition_priority_explanation"]) == (None, None, None)


def test_target_row_public_sem_o_alias_no_banco_e_tres_nulos():
    linha = svc.target_row_public({"local_date": SEGUNDA, "plan_id": 1}, SEGUNDA)
    assert linha["nutrition_priority"] is None


def test_target_row_public_indisponivel():
    linha = svc.target_row_public(_linha('"unavailable"'), SEGUNDA)
    assert linha["nutrition_priority"] == "unavailable"
    assert linha["nutrition_priority_label"] is None and linha["nutrition_priority_explanation"] is None


def test_hash_das_entradas_nao_carrega_a_prioridade():
    a = compute_daily_target(SUJEITO, _plano(manual_macros=MANUAL, cycle_kinds=SEMANA), _dia(0))
    assert "nutrition_priority" not in json.dumps(PlanParams.to_dict(_plano(cycle_kinds=SEMANA)))
    assert "nutrition_priority" not in json.dumps(_dia(0).to_dict()) and a.nutrition_priority == P.CARB


# --- persistência: a versão regrava a janela aberta, nunca o dia congelado ------------------------------------------

HOJE = date(2026, 10, 17)
ALUNO = 2


@pytest.fixture(autouse=True)
def _fita_existe(monkeypatch):
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)


def _db() -> FakeNutritionDB:
    db = FakeNutritionDB()
    db.add_weight(ALUNO, HOJE - timedelta(days=30), 80)
    db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=10), published_at=datetime(2026, 10, 7, 12))
    return db


def _ensure(db, de, ate):
    return svc.ensure_daily_targets(db.conn(), ALUNO, TargetWindow(date_from=de, date_to=ate, create_until=ate), today=HOJE)


def test_versao_anterior_na_janela_aberta_e_regravada_com_a_chave_e_congelado_fica_intacto():
    db = _db()
    anteontem = HOJE - timedelta(days=2)
    db.targets[(ALUNO, anteontem)] = {
        "user_id": ALUNO, "local_date": anteontem, "plan_id": 1, "day_kind": "normal", "is_training_day": 0,
        "training_known": 0, "phase_kind": None, "kcal_target": Decimal("1234.00"), "protein_g": None, "carbs_g": None,
        "fat_g": None, "fiber_g": None, "water_ml": None, "bmr_kcal": None, "bmr_formula": "auto", "tdee_kcal": None,
        "training_kcal": None, "training_kcal_nature": None, "limitations_json": "[]", "inputs_hash": None,
        "engine_version": "nutrition-v5", "computed_at": datetime(2026, 10, 15, 8),
    }
    _ensure(db, HOJE, HOJE)
    assert db.targets[(ALUNO, HOJE)]["inputs_json"]["nutrition_priority"] == "normal"
    db.writes.clear()
    db.targets[(ALUNO, HOJE)]["engine_version"] = "nutrition-v5"
    linhas = {linha["local_date"]: linha for linha in _ensure(db, anteontem, HOJE)}
    assert f"upsert {HOJE.isoformat()}" in db.writes
    assert f"upsert {anteontem.isoformat()}" not in db.writes
    antigo = linhas[anteontem.isoformat()]
    assert antigo["kcal"] == 1234.0 and antigo["engine_version"] == "nutrition-v5" and antigo["nutrition_priority"] is None
    assert linhas[HOJE.isoformat()]["nutrition_priority"] == "normal"
    assert ENGINE_VERSION == "nutrition-v8"

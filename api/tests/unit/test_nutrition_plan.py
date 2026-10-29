"""Plano do aluno (dono `app/domain/nutrition/plan.py`, L2b): defaults da
estratégia, validação nas faixas da fonte (400 com o código do motor), campo
de outra estratégia, ciclo de carbo recusado até o L8, estado e vigência."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.domain.nutrition import bmr as bm
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition import plan as pl
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import WEIGHT_KG_RANGE, ActivityLevel, Sex, Subject
from app.domain.nutrition.body_composition import BODY_FAT_RANGE_VALID
from app.domain.nutrition.macros import MEALS_PER_DAY_RANGE
from app.domain.nutrition.phases import BULK_STOP_BODY_FAT_PERCENT, CUT_STOP_BODY_FAT_PERCENT, resolve_stop_body_fat

S = st.Strategy


@pytest.mark.parametrize("estrategia", list(S))
def test_so_a_estrategia_recebe_os_defaults_do_motor(estrategia):
    resultado = pl.build_plan_settings(strategy=estrategia.value)
    assert resultado.ok and resultado.errors == ()
    base = st.StrategyParams.defaults_for(estrategia)
    assert resultado.settings.strategy_params() == base
    assert resultado.settings.bmr_formula == bm.BmrFormula.AUTO
    assert resultado.settings.tdee_mode == ee.DEFAULT_TDEE_MODE
    assert resultado.settings.activity_level is None
    assert resultado.settings.carb_cycling_enabled is False


def test_bulking_avancado_usa_o_default_avancado():
    resultado = pl.build_plan_settings(strategy="bulking", advanced=True)
    assert resultado.ok
    assert resultado.settings.surplus_share == st.BULK_SURPLUS_SHARE_DEFAULT_ADVANCED


def test_valores_informados_dentro_da_faixa_sao_mantidos():
    resultado = pl.build_plan_settings(
        strategy="cutting",
        protein_g_per_kg=st.CUT_PROTEIN_G_PER_KG_FFM_RANGE[1],
        fat_kcal_share=st.CUT_FAT_KCAL_SHARE_RANGE[0],
        weekly_rate_share=st.CUT_WEEKLY_RATE_SHARE_RANGE[1],
        bmr_formula="katch_mcardle",
        tdee_mode="included_in_factor",
        activity_level="Muito ativo",
        meals_per_day=MEALS_PER_DAY_RANGE[1],
        goal_body_fat_percent=BODY_FAT_RANGE_VALID[0],
        goal_weight_kg=WEIGHT_KG_RANGE[1],
    )
    assert resultado.ok, resultado.errors
    s = resultado.settings
    assert s.protein_g_per_kg == st.CUT_PROTEIN_G_PER_KG_FFM_RANGE[1]
    assert s.weekly_rate_share == st.CUT_WEEKLY_RATE_SHARE_RANGE[1]
    assert s.bmr_formula == bm.BmrFormula.KATCH_MCARDLE
    assert s.tdee_mode == ee.TdeeMode.INCLUDED_IN_FACTOR
    assert s.activity_level == ActivityLevel.VERY_ACTIVE
    assert s.meals_per_day == MEALS_PER_DAY_RANGE[1]
    params = s.plan_params(ffm_age_days=10)
    assert params.ffm_age_days == 10 and params.strategy_params.protein_basis == st.ProteinBasis.FAT_FREE_MASS
    assert s.to_dict()["protein_basis"] == st.ProteinBasis.FAT_FREE_MASS.value


@pytest.mark.parametrize(
    ("campo", "valor", "codigo"),
    [
        ("protein_g_per_kg", st.CUT_PROTEIN_G_PER_KG_FFM_RANGE[1] + 1, "param_out_of_range:protein_g_per_kg"),
        ("protein_g_per_kg", 0, "param_out_of_range:protein_g_per_kg"),
        ("fat_kcal_share", st.CUT_FAT_KCAL_SHARE_RANGE[0] / 2, "param_out_of_range:fat_kcal_share"),
        ("weekly_rate_share", st.CUT_WEEKLY_RATE_SHARE_RANGE[1] * 2, "param_out_of_range:weekly_rate_share"),
        ("meals_per_day", MEALS_PER_DAY_RANGE[1] + 1, "param_out_of_range:meals_per_day"),
        ("goal_body_fat_percent", BODY_FAT_RANGE_VALID[1] + 1, "param_out_of_range:goal_body_fat_percent"),
        ("goal_weight_kg", WEIGHT_KG_RANGE[0] - 1, "param_out_of_range:goal_weight_kg"),
        ("bmr_formula", "keytel", "invalid_code:bmr_formula"),
        ("tdee_mode", "add_observed", "invalid_code:tdee_mode"),
        ("activity_level", "atleta olímpico", "invalid_code:activity_level"),
        ("surplus_share", st.BULK_SURPLUS_SHARE_DEFAULT, "param_not_applicable:surplus_share"),
        ("carb_min_g_per_kg", st.BULK_CARB_MIN_G_PER_KG, "param_not_applicable:carb_min_g_per_kg"),
    ],
)
def test_fora_da_faixa_ou_fora_da_estrategia_vira_codigo_do_motor(campo, valor, codigo):
    resultado = pl.build_plan_settings(strategy="cutting", **{campo: valor})
    assert not resultado.ok
    assert codigo in resultado.errors


def test_estrategia_invalida_para_antes_de_tudo():
    resultado = pl.build_plan_settings(strategy="keto")
    assert resultado.settings is None and resultado.errors == ("invalid_code:strategy",)


def test_superavit_fora_da_faixa_do_avancado():
    assert "param_out_of_range:surplus_share" in pl.build_plan_settings(
        strategy="bulking", advanced=True, surplus_share=st.BULK_SURPLUS_SHARE_RANGE[1]
    ).errors
    assert pl.build_plan_settings(strategy="bulking", surplus_share=st.BULK_SURPLUS_SHARE_RANGE[1]).ok


def test_ciclo_de_carbo_recusado_ate_o_l8():
    resultado = pl.build_plan_settings(strategy="maintenance", carb_cycling_enabled=True)
    assert resultado.errors == (pl.ERR_CARB_CYCLING_UNAVAILABLE,)
    assert resultado.settings.carb_cycling_enabled is False


def test_refeicoes_precisam_ser_inteiras():
    assert "param_out_of_range:meals_per_day" in pl.build_plan_settings(strategy="maintenance", meals_per_day=4.5).errors
    assert "param_out_of_range:meals_per_day" in pl.build_plan_settings(strategy="maintenance", meals_per_day=True).errors


@pytest.mark.parametrize(
    ("status", "substituido", "estado"),
    [
        ("draft", None, pl.PlanState.DRAFT),
        ("published", None, pl.PlanState.CURRENT),
        ("published", datetime(2026, 10, 17, 12), pl.PlanState.SUPERSEDED),
        ("qualquer", None, pl.PlanState.DRAFT),
    ],
)
def test_estado_do_plano(status, substituido, estado):
    assert pl.plan_state(status, substituido) == estado


def test_plano_em_vigor_por_dia():
    a = pl.PublishedPlanRef(1, date(2026, 10, 1), datetime(2026, 10, 1, 10))
    b = pl.PublishedPlanRef(2, date(2026, 10, 10), datetime(2026, 10, 10, 9))
    c = pl.PublishedPlanRef(3, date(2026, 10, 10), datetime(2026, 10, 10, 15))
    planos = [c, a, b]
    assert pl.plan_in_force(planos, date(2026, 9, 30)) is None
    assert pl.plan_in_force(planos, date(2026, 10, 9)) == a
    # Dois publicados no mesmo dia: vale o publicado por último.
    assert pl.plan_in_force(planos, date(2026, 10, 10)) == c
    assert pl.plan_in_force(planos, date(2026, 12, 31)) == c
    assert pl.plan_in_force([], date(2026, 10, 10)) is None


def test_default_da_formula_da_tmb_e_do_dono():
    assert pl.build_plan_settings(strategy="maintenance").settings.bmr_formula == bm.DEFAULT_BMR_FORMULA


@pytest.mark.parametrize(
    ("estrategia", "sexo", "do_plano", "esperado"),
    [
        (S.CUTTING, Sex.MALE, None, (CUT_STOP_BODY_FAT_PERCENT[Sex.MALE], True)),
        (S.BULKING, Sex.FEMALE, None, (BULK_STOP_BODY_FAT_PERCENT[Sex.FEMALE], True)),
        (S.CUTTING, Sex.MALE, 12.0, (12.0, False)),
        (S.MAINTENANCE, Sex.MALE, None, (None, False)),
        (S.CUTTING, Sex.OTHER, None, (None, False)),
        (S.CUTTING, None, None, (None, False)),
    ],
)
def test_percentual_de_parada_do_plano_ou_default_do_motor(estrategia, sexo, do_plano, esperado):
    assert resolve_stop_body_fat(estrategia, sexo, do_plano) == esperado


def test_resumo_da_meta_de_composicao():
    sujeito = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, body_fat_percent=9.5)
    cutting = pl.build_plan_settings(strategy="cutting").settings
    resumo = pl.goal_summary(cutting, sujeito)
    assert resumo.goal_body_fat_percent == CUT_STOP_BODY_FAT_PERCENT[Sex.MALE]
    assert resumo.goal_body_fat_is_default is True
    assert resumo.should_stop is True  # 9,5% já está abaixo do piso do cutting
    assert resumo.target_weight_for_goal_kg == pytest.approx(80 * (1 - 0.095) / (1 - resumo.goal_body_fat_percent / 100))
    assert resumo.to_dict()["goal_weight_kg"] is None

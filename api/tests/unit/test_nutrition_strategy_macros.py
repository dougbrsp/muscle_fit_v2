"""Estratégias e macros (donos `app/domain/nutrition/strategy.py` e `macros.py`):
faixas/defaults de Helms 2014, Iraki 2019 e ISSN 2017, clamps do cutting (D-B)
e a divisão em gramas."""

from __future__ import annotations

import json

import pytest

from app.domain.nutrition import energy_density as ed
from app.domain.nutrition import macros as mc
from app.domain.nutrition import strategy as st

S = st.Strategy
P = st.ProteinBasis

PESO, MLG, MASSA_GORDA = 80.0, 68.0, 12.0
TMB = 1755.0
TDEE = TMB * 1.4 + 294.0  # 2751


def test_faixas_e_defaults_das_fontes():
    assert st.CUT_WEEKLY_RATE_SHARE_RANGE == (0.005, 0.010) and st.CUT_WEEKLY_RATE_SHARE_DEFAULT == 0.007
    assert st.CUT_MAX_DEFICIT_SHARE == 0.25 and st.CUT_MIN_INTAKE_BMR_MULTIPLE == 1.0
    assert st.CUT_PROTEIN_G_PER_KG_FFM_RANGE == (2.3, 3.1) and st.CUT_PROTEIN_G_PER_KG_FFM_DEFAULT == 2.6
    assert st.CUT_FAT_KCAL_SHARE_RANGE == (0.15, 0.30) and st.CUT_FAT_KCAL_SHARE_DEFAULT == 0.25
    assert st.BULK_SURPLUS_SHARE_RANGE == (0.10, 0.20) and st.BULK_SURPLUS_SHARE_RANGE_ADVANCED == (0.05, 0.10)
    assert st.BULK_WEEKLY_RATE_SHARE_RANGE == (0.0025, 0.005)
    assert st.BULK_PROTEIN_G_PER_KG_RANGE == (1.6, 2.2) and st.BULK_PROTEIN_G_PER_KG_DEFAULT == 2.0
    assert st.BULK_FAT_G_PER_KG_RANGE == (0.5, 1.5) and st.BULK_FAT_KCAL_SHARE_RANGE == (0.20, 0.35)
    assert st.BULK_CARB_MIN_G_PER_KG_RANGE == (3.0, 5.0) and st.BULK_CARB_MIN_G_PER_KG == 3.0
    assert st.MAINT_TOLERANCE_SHARE == 0.05 and st.MAINT_PROTEIN_G_PER_KG_RANGE == (1.6, 2.2)


@pytest.mark.parametrize(
    ("estrategia", "avancado", "proteina", "base", "gordura", "ritmo", "superavit"),
    [
        (S.CUTTING, False, 2.6, P.FAT_FREE_MASS, 0.25, 0.007, None),
        (S.BULKING, False, 2.0, P.BODY_WEIGHT, 0.25, None, 0.10),
        (S.BULKING, True, 2.0, P.BODY_WEIGHT, 0.25, None, 0.05),
        (S.MAINTENANCE, False, 2.0, P.BODY_WEIGHT, 0.25, None, None),
    ],
)
def test_defaults_for(estrategia, avancado, proteina, base, gordura, ritmo, superavit):
    params = st.StrategyParams.defaults_for(estrategia, advanced=avancado)
    assert (params.protein_g_per_kg, params.protein_basis, params.fat_kcal_share) == (proteina, base, gordura)
    assert (params.weekly_rate_share, params.surplus_share) == (ritmo, superavit)
    assert params.out_of_range() == ()
    json.dumps(params.to_dict())


def test_out_of_range_avisa_sem_bloquear():
    params = st.StrategyParams(strategy=S.CUTTING, protein_g_per_kg=1.0, protein_basis=P.FAT_FREE_MASS, fat_kcal_share=0.5, weekly_rate_share=0.02)
    avisos = params.out_of_range()
    assert set(avisos) == {
        f"{st.LIMIT_PARAM_OUT_OF_RANGE}:protein_g_per_kg",
        f"{st.LIMIT_PARAM_OUT_OF_RANGE}:fat_kcal_share",
        f"{st.LIMIT_PARAM_OUT_OF_RANGE}:weekly_rate_share",
    }


def test_cutting_default_sem_clamp():
    params = st.StrategyParams.defaults_for(S.CUTTING)
    alvo = st.kcal_target(params, TDEE, TMB, PESO, MASSA_GORDA)
    deficit = ed.daily_kcal_for_weekly_rate(PESO * 0.007, MASSA_GORDA)
    assert alvo.kcal == pytest.approx(TDEE - deficit)
    assert alvo.delta_kcal == pytest.approx(-deficit)
    assert alvo.rate_applied == pytest.approx(0.007)
    assert alvo.clamped_by == () and alvo.limitations == ()


def test_cutting_clamp_do_deficit_maximo():
    params = st.StrategyParams.defaults_for(S.CUTTING)
    params = st.StrategyParams(**{**params.__dict__, "weekly_rate_share": 0.02})
    alvo = st.kcal_target(params, TDEE, TMB, PESO, MASSA_GORDA)
    assert alvo.kcal == pytest.approx(TDEE * (1 - 0.25))
    assert alvo.clamped_by == (st.CLAMP_MAX_DEFICIT_SHARE,)
    assert alvo.rate_applied < 0.02  # o ritmo real caiu com o clamp
    assert alvo.rate_applied == pytest.approx(ed.weekly_kg_for_daily_kcal(TDEE * 0.25, MASSA_GORDA) / PESO)


def test_cutting_piso_de_uma_tmb_vence_o_deficit():
    params = st.StrategyParams.defaults_for(S.CUTTING)
    params = st.StrategyParams(**{**params.__dict__, "weekly_rate_share": 0.01})
    tdee_sedentario = TMB * 1.2
    alvo = st.kcal_target(params, tdee_sedentario, TMB, PESO, MASSA_GORDA)
    assert alvo.kcal == pytest.approx(TMB)
    assert alvo.clamped_by == (st.CLAMP_MAX_DEFICIT_SHARE, st.CLAMP_MIN_INTAKE_BMR_MULTIPLE)


def test_cutting_sem_massa_gorda_nao_calcula_nunca_7700():
    params = st.StrategyParams.defaults_for(S.CUTTING)
    alvo = st.kcal_target(params, TDEE, TMB, PESO, None)
    assert alvo.kcal is None and alvo.limitations == (st.LIMIT_MISSING_FAT_MASS,)
    sem_peso = st.kcal_target(params, TDEE, TMB, None, MASSA_GORDA)
    assert sem_peso.kcal is None and st.LIMIT_MISSING_WEIGHT in sem_peso.limitations


def test_cutting_sem_tmb_calcula_mas_avisa_que_o_piso_nao_foi_checado():
    params = st.StrategyParams.defaults_for(S.CUTTING)
    alvo = st.kcal_target(params, TDEE, None, PESO, MASSA_GORDA)
    assert alvo.kcal is not None and alvo.limitations == (st.LIMIT_MISSING_BMR_FOR_FLOOR,)


def test_bulking_e_manutencao():
    normal = st.kcal_target(st.StrategyParams.defaults_for(S.BULKING), TDEE)
    assert normal.kcal == pytest.approx(TDEE * 1.10) and normal.delta_kcal == pytest.approx(TDEE * 0.10)
    avancado = st.kcal_target(st.StrategyParams.defaults_for(S.BULKING, advanced=True), TDEE)
    assert avancado.kcal == pytest.approx(TDEE * 1.05)
    manutencao = st.kcal_target(st.StrategyParams.defaults_for(S.MAINTENANCE), TDEE)
    assert (manutencao.kcal, manutencao.delta_kcal, manutencao.rate_applied) == (TDEE, 0.0, None)
    assert st.kcal_target(st.StrategyParams.defaults_for(S.MAINTENANCE), None).limitations == (st.LIMIT_MISSING_TDEE,)
    assert st.maintenance_band(2000) == (pytest.approx(1900), pytest.approx(2100))


def test_as_maintenance_preserva_proteina_e_gordura():
    params = st.StrategyParams.defaults_for(S.CUTTING).as_maintenance()
    assert params.strategy == S.MAINTENANCE and params.protein_g_per_kg == 2.6 and params.protein_basis == P.FAT_FREE_MASS
    assert params.weekly_rate_share is None


# --- macros -------------------------------------------------------------------


def test_fatores_de_atwater_fibra_e_faixas():
    assert (mc.KCAL_PER_G_PROTEIN, mc.KCAL_PER_G_CARB, mc.KCAL_PER_G_FAT, mc.KCAL_PER_G_ALCOHOL) == (4.0, 4.0, 9.0, 7.0)
    assert mc.FIBER_G_PER_1000_KCAL == 14.0
    assert mc.PROTEIN_PER_MEAL_G_PER_KG_RANGE == (0.40, 0.55) and mc.MEALS_PER_DAY_RANGE == (3, 6)
    assert mc.kcal_from_macros(100, 200, 50) == 400 + 800 + 450
    assert mc.kcal_from_macros(0, 0, 0, 10) == 70.0
    assert mc.kcal_from_macros(-1, 0, 0) is None
    assert mc.fiber_target_g(2000) == 28.0


def test_macro_split_cutting_fecha_a_conta():
    params = st.StrategyParams.defaults_for(S.CUTTING)
    kcal = 2281.29
    metas = mc.macro_split(kcal, params, weight_kg=PESO, ffm_kg=MLG)
    assert metas.computable
    assert metas.protein_g == pytest.approx(2.6 * MLG)
    assert metas.fat_g == pytest.approx(0.25 * kcal / 9)
    assert metas.carb_g == pytest.approx((kcal - 4 * 2.6 * MLG - 0.25 * kcal) / 4)
    assert mc.kcal_from_macros(metas.protein_g, metas.carb_g, metas.fat_g) == pytest.approx(kcal)
    assert metas.fiber_g == pytest.approx(14 * kcal / 1000)
    assert metas.protein_basis == P.FAT_FREE_MASS and metas.protein_basis_kg == MLG
    assert metas.limitations == ()
    json.dumps(metas.to_dict())


def test_macro_split_bulking_usa_peso_e_avisa_carbo_abaixo_do_minimo():
    params = st.StrategyParams.defaults_for(S.BULKING)
    ok = mc.macro_split(3026.1, params, weight_kg=PESO, ffm_kg=MLG)
    assert ok.protein_g == pytest.approx(2.0 * PESO) and ok.limitations == ()
    baixo = mc.macro_split(2000, params, weight_kg=PESO, ffm_kg=MLG)
    assert baixo.carb_g < 3.0 * PESO and baixo.limitations == (mc.LIMIT_CARB_BELOW_MIN,)


def test_macro_split_sem_energia_para_proteina_e_gordura_zera_carbo():
    params = st.StrategyParams.defaults_for(S.CUTTING)
    metas = mc.macro_split(900, params, weight_kg=PESO, ffm_kg=MLG)
    assert metas.carb_g == 0.0 and metas.limitations == (mc.LIMIT_KCAL_BELOW_PROTEIN_AND_FAT,)


def test_macro_split_none_sem_kcal_ou_sem_base():
    params = st.StrategyParams.defaults_for(S.CUTTING)
    sem_kcal = mc.macro_split(None, params, weight_kg=PESO, ffm_kg=MLG)
    assert not sem_kcal.computable and sem_kcal.limitations == (mc.LIMIT_MISSING_KCAL,)
    sem_mlg = mc.macro_split(2200, params, weight_kg=PESO, ffm_kg=None)
    assert not sem_mlg.computable and sem_mlg.limitations == (mc.LIMIT_MISSING_PROTEIN_BASIS,)
    assert sem_mlg.as_macros() is None


def test_proteina_por_refeicao():
    assert mc.protein_per_meal(176.8, 4) == pytest.approx(44.2)
    assert mc.protein_per_meal(176.8, 0) is None and mc.protein_per_meal(None, 4) is None
    assert mc.protein_per_meal_range_g(PESO) == (pytest.approx(32.0), pytest.approx(44.0))
    assert mc.meals_per_day_is_valid(4) and not mc.meals_per_day_is_valid(7) and not mc.meals_per_day_is_valid(None)


def test_macros_plus_e_scaled():
    a = mc.Macros(kcal=100, protein_g=10, carb_g=5, fat_g=2, fiber_g=1)
    b = mc.Macros(kcal=50, protein_g=5, carb_g=5, fat_g=1)
    soma = a.plus(b)
    assert (soma.kcal, soma.protein_g, soma.carb_g, soma.fat_g, soma.fiber_g) == (150, 15, 10, 3, 1)
    assert a.scaled(0.5).fiber_g == 0.5 and b.scaled(2).fiber_g is None

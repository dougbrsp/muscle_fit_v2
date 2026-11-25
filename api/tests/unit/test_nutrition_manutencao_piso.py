"""Piso da manutenção com ajuste NEGATIVO (N7 rodada 2; dono `strategy.kcal_target`, mapa §83).

Ajuste 0 e positivo seguem iguais bit a bit; só o negativo respeita `CUT_MIN_INTAKE_BMR_MULTIPLE × TMB` e o bloqueio de
energia `manual_targets.KCAL_BLOCK_MIN`, e o limite aplicado fica em `clamped_by`."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.daily_target import DayContext, compute_daily_target
from app.domain.nutrition.manual_targets import KCAL_BLOCK_MIN
from app.domain.nutrition.plan import build_plan_settings


def _params(ajuste: int) -> st.StrategyParams:
    return replace(st.StrategyParams.defaults_for(st.Strategy.MAINTENANCE), maintenance_kcal_adjust=ajuste)


def test_ajuste_zero_e_positivo_nao_sofrem_piso():
    zero = st.kcal_target(_params(0), 2000.0, 1500.0)  # gasto acima da TMB: o piso nem é consultado
    assert (zero.kcal, zero.delta_kcal, zero.clamped_by, zero.limitations) == (2000.0, 0.0, (), ())
    mais = st.kcal_target(_params(300), 2000.0, 1500.0)
    assert (mais.kcal, mais.delta_kcal, mais.clamped_by) == (2300.0, 300.0, ())


def test_ajuste_negativo_acima_do_piso_fica_como_pedido():
    alvo = st.kcal_target(_params(-300), 2600.0, 1800.0)
    assert (alvo.kcal, alvo.delta_kcal, alvo.clamped_by) == (2300.0, -300.0, ())


def test_ajuste_negativo_nao_desce_abaixo_da_tmb_e_registra_o_limite():
    alvo = st.kcal_target(_params(-500), 2000.0, 1800.0)
    assert alvo.kcal == 1800.0 and alvo.delta_kcal == -200.0
    assert alvo.clamped_by == (st.CLAMP_MIN_INTAKE_BMR_MULTIPLE,)


def test_ajuste_negativo_nao_desce_abaixo_do_bloqueio_de_energia_e_sem_tmb_avisa():
    alvo = st.kcal_target(_params(-500), 1000.0, None)
    assert alvo.kcal == KCAL_BLOCK_MIN and st.CLAMP_MIN_KCAL_BLOCK in alvo.clamped_by
    assert st.LIMIT_MISSING_BMR_FOR_FLOOR in alvo.limitations


@pytest.mark.parametrize("ajuste", [0, 300, -100])
def test_gasto_abaixo_da_tmb_sobe_a_meta_ao_piso_do_cutting(ajuste):
    """L54 (#313): gasto estimado (adaptativo) menor que a TMB não leva a ingestão abaixo de 1,0 × TMB, como no cutting."""
    alvo = st.kcal_target(_params(ajuste), 1200.0, 1839.0)
    assert alvo.kcal == st.CUT_MIN_INTAKE_BMR_MULTIPLE * 1839.0
    assert st.CLAMP_MIN_INTAKE_BMR_MULTIPLE in alvo.clamped_by


def test_adaptativo_manutencao_120kg_gasto_1200_fecha_atwater_com_carbo():
    from app.domain.nutrition import adaptive_energy as ae

    sujeito = Subject(sex=Sex.MALE, age_years=30, height_cm=178, weight_kg=120, body_fat_percent=10, activity_level=ActivityLevel.MODERATE)
    plano = build_plan_settings(strategy="maintenance", tdee_mode="adaptive_balance", activity_level="moderate").settings
    dia = date(2026, 11, 23)
    estimativa = ae._estimate_from(dia, 1200, 0.0, 0.5, False, False, False, {})
    meta = compute_daily_target(sujeito, plano.plan_params(), DayContext(day=dia, energy_estimate=estimativa))
    m = meta.macros
    assert m.kcal >= meta.bmr.kcal and m.carb_g > 0
    assert 4 * m.protein_g + 4 * m.carb_g + 9 * m.fat_g == pytest.approx(m.kcal, abs=1.0)
    assert st.CLAMP_MIN_INTAKE_BMR_MULTIPLE in meta.kcal_target.clamped_by

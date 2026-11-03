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
    zero = st.kcal_target(_params(0), 2000.0, 3000.0)  # TMB acima do gasto: o piso nem é consultado
    assert (zero.kcal, zero.delta_kcal, zero.clamped_by, zero.limitations) == (2000.0, 0.0, (), ())
    mais = st.kcal_target(_params(300), 2000.0, 3000.0)
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


def test_piso_nunca_fica_acima_do_proprio_gasto():
    alvo = st.kcal_target(_params(-100), 700.0, 1500.0)  # gasto abaixo da TMB: não "sobe" a meta acima do gasto
    assert alvo.kcal <= 700.0


def test_materializacao_com_tmb_alta_e_ajuste_menos_500_respeita_a_tmb():
    pesado = Subject(sex=Sex.MALE, age_years=30, height_cm=180.0, weight_kg=130.0, activity_level=ActivityLevel.SEDENTARY)
    plano = build_plan_settings(strategy="maintenance", activity_level="sedentary", maintenance_kcal_adjust=-500)
    assert plano.ok
    meta = compute_daily_target(pesado, plano.settings.plan_params(), DayContext(day=date(2026, 11, 2)))
    assert meta.macros.kcal >= meta.bmr.kcal  # sem o piso seria gasto - 500, abaixo da TMB
    assert st.CLAMP_MIN_INTAKE_BMR_MULTIPLE in meta.kcal_target.clamped_by
    sem = build_plan_settings(strategy="maintenance", activity_level="sedentary").settings
    base = compute_daily_target(pesado, sem.plan_params(), DayContext(day=date(2026, 11, 2)))
    assert base.kcal_target.kcal == pytest.approx(base.tdee.kcal) and base.kcal_target.clamped_by == ()

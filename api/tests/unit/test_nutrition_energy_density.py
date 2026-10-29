"""Densidade energética (dono `app/domain/nutrition/energy_density.py`): Hall 2008 +
Forbes. Referência do plano §14: massa gorda 15 kg."""

from __future__ import annotations

import pytest

from app.domain.nutrition import energy_density as ed


def test_constantes_de_hall_e_forbes():
    assert ed.FAT_TISSUE_KCAL_PER_KG == 9400.0
    assert ed.LEAN_TISSUE_KCAL_PER_KG == 1800.0
    assert ed.FORBES_CONSTANT_KG == 10.4
    assert ed.DAYS_PER_WEEK == 7


def test_fracao_de_gordura_na_variacao_fm_15():
    assert ed.fat_share_of_weight_change(15) == pytest.approx(15 / 25.4)
    assert ed.fat_share_of_weight_change(0) == 0.0
    assert ed.fat_share_of_weight_change(-1) is None
    assert ed.fat_share_of_weight_change(None) is None


def test_densidade_fm_15_kg():
    p = 15 / 25.4
    assert ed.tissue_energy_density(15) == pytest.approx(p * 9400 + (1 - p) * 1800)
    assert ed.tissue_energy_density(15) == pytest.approx(6288.19, abs=0.01)
    assert ed.tissue_energy_density(0) == 1800.0  # sem gordura: só tecido magro
    assert ed.tissue_energy_density(30) > ed.tissue_energy_density(15) > ed.tissue_energy_density(5)
    assert ed.tissue_energy_density(None) is None


def test_kcal_dia_para_ritmo_semanal_e_inverso():
    diario = ed.daily_kcal_for_weekly_rate(0.5, 15)
    assert diario == pytest.approx(0.5 * 6288.19 / 7, abs=0.01)
    assert ed.weekly_kg_for_daily_kcal(diario, 15) == pytest.approx(0.5)
    assert ed.daily_kcal_for_weekly_rate(-0.25, 15) < 0  # ganho: superávit
    assert ed.daily_kcal_for_weekly_rate(0.5, None) is None
    assert ed.daily_kcal_for_weekly_rate(None, 15) is None
    assert ed.daily_kcal_for_weekly_rate("x", 15) is None


def test_nunca_usa_7700_fixo():
    # Dois sujeitos com a mesma meta de kg/semana e massas gordas diferentes
    # precisam de déficits diferentes.
    assert ed.daily_kcal_for_weekly_rate(0.5, 10) != ed.daily_kcal_for_weekly_rate(0.5, 30)

"""`energy_params` em plano NÃO adaptativo (#202): só o padrão passa; `unknown` (não sei) sai do padrão e é recusado.

Dono: `domain/nutrition/plan.py` (`build_plan_settings`, `ERR_PARAM_NOT_APPLICABLE`)."""

from __future__ import annotations

import pytest

from app.domain.nutrition import activity_energy as ae
from app.domain.nutrition.plan import (
    DEFAULT_WATCH_CALORIE_MODE,
    ENERGY_PARAM_WATCH_CALORIE_MODE,
    ERR_PARAM_NOT_APPLICABLE,
    FIELD_ENERGY_PARAMS,
    build_plan_settings,
)


def test_unknown_em_plano_nao_adaptativo_devolve_param_not_applicable():
    r = build_plan_settings(
        strategy="maintenance",
        tdee_mode="explicit_training",
        energy_params={ENERGY_PARAM_WATCH_CALORIE_MODE: ae.CalorieMode.UNKNOWN.value},
    )
    assert not r.ok
    assert f"{ERR_PARAM_NOT_APPLICABLE}:{FIELD_ENERGY_PARAMS}" in r.errors


def test_valor_padrao_em_plano_nao_adaptativo_passa():
    r = build_plan_settings(
        strategy="maintenance",
        tdee_mode="explicit_training",
        energy_params={ENERGY_PARAM_WATCH_CALORIE_MODE: DEFAULT_WATCH_CALORIE_MODE.value},
    )
    assert r.ok, r.errors


@pytest.mark.parametrize("modo", [ae.CalorieMode.GROSS, ae.CalorieMode.UNKNOWN])
def test_modo_fora_do_padrao_so_vale_no_adaptativo(modo):
    valor = {ENERGY_PARAM_WATCH_CALORIE_MODE: modo.value}
    assert build_plan_settings(strategy="maintenance", tdee_mode="adaptive_balance", energy_params=valor).ok
    recusado = build_plan_settings(strategy="maintenance", tdee_mode="explicit_training", energy_params=valor)
    assert not recusado.ok

"""TMB (dono `app/domain/nutrition/bmr.py`): valores de referência das fontes
(plano §3.1/§14) e a seleção automática.

Nota: o plano §14 cita "Mifflin H 80 kg/180 cm/35 a → 1.780"; pela fórmula
publicada (Mifflin 1990) 80/180/35 dá 1.755 e 1.780 corresponde a 30 anos.
Os dois casos ficam fixados aqui.
"""

from __future__ import annotations

import json

import pytest

from app.domain.nutrition import bmr
from app.domain.nutrition.anthropometrics import Sex, Subject

F = bmr.BmrFormula


@pytest.mark.parametrize(
    ("sexo", "peso", "altura", "idade", "esperado"),
    [
        (Sex.MALE, 80, 180, 30, 1780.0),
        (Sex.MALE, 80, 180, 35, 1755.0),
        (Sex.FEMALE, 60, 165, 30, 1320.25),
        (Sex.OTHER, 80, 180, 35, None),
        (None, 80, 180, 35, None),
        (Sex.MALE, 80, 90, 35, None),  # altura fora da faixa
        (Sex.MALE, 80, 180, None, None),
        (Sex.MALE, None, 180, 35, None),
        (Sex.MALE, "80", "180", "35", 1755.0),
    ],
)
def test_mifflin_st_jeor(sexo, peso, altura, idade, esperado):
    assert bmr.mifflin_st_jeor(sexo, peso, altura, idade) == esperado


@pytest.mark.parametrize(("mlg", "katch", "cunningham"), [(65, 1774.0, 1930.0), (50, 1450.0, 1600.0), (0, None, None), (None, None, None)])
def test_katch_mcardle_e_cunningham(mlg, katch, cunningham):
    assert bmr.katch_mcardle(mlg) == katch
    assert bmr.cunningham(mlg) == cunningham


def test_harris_benedict_revisada_so_comparacao():
    assert bmr.harris_benedict(Sex.MALE, 80, 180, 30) == pytest.approx(1853.632, abs=1e-3)
    assert bmr.harris_benedict(Sex.FEMALE, 60, 165, 30) == pytest.approx(447.593 + 9.247 * 60 + 3.098 * 165 - 4.330 * 30, abs=1e-6)
    assert bmr.harris_benedict(Sex.OTHER, 80, 180, 30) is None


def _sujeito(**extra) -> Subject:
    base = dict(sex=Sex.MALE, age_years=35, height_cm=180.0, weight_kg=80.0)
    base.update(extra)
    return Subject(**base)


def test_selecao_automatica_prefere_katch_com_mlg_recente():
    assert bmr.select_formula(_sujeito(fat_free_mass_kg=65.0)) == F.KATCH_MCARDLE
    assert bmr.select_formula(_sujeito(fat_free_mass_kg=65.0), ffm_age_days=60) == F.KATCH_MCARDLE
    assert bmr.select_formula(_sujeito(fat_free_mass_kg=65.0), ffm_age_days=61) == F.MIFFLIN_ST_JEOR
    assert bmr.select_formula(_sujeito(body_fat_percent=15.0)) == F.KATCH_MCARDLE  # MLG derivada de peso × %G
    assert bmr.select_formula(_sujeito()) == F.MIFFLIN_ST_JEOR
    assert bmr.select_formula(_sujeito(fat_free_mass_kg=65.0), requested=F.CUNNINGHAM) == F.CUNNINGHAM
    assert bmr.FFM_FRESHNESS_MAX_DAYS == 60


def test_bmr_auto_com_mlg_usa_katch():
    resultado = bmr.bmr(_sujeito(fat_free_mass_kg=65.0))
    assert resultado.kcal == 1774.0 and resultado.formula == F.KATCH_MCARDLE
    assert resultado.requested_formula == F.AUTO and resultado.limitations == ()


def test_bmr_auto_sem_mlg_usa_mifflin():
    resultado = bmr.bmr(_sujeito())
    assert resultado.kcal == 1755.0 and resultado.formula == F.MIFFLIN_ST_JEOR


def test_sex_other_sem_mlg_nao_calcula_e_com_mlg_usa_katch():
    sem = bmr.bmr(_sujeito(sex=Sex.OTHER))
    assert sem.kcal is None and bmr.LIMIT_SEX_OTHER_REQUIRES_FFM in sem.limitations
    com = bmr.bmr(_sujeito(sex=Sex.OTHER, fat_free_mass_kg=65.0))
    assert com.kcal == 1774.0 and com.formula == F.KATCH_MCARDLE


def test_bmr_none_sem_altura_ou_sexo_com_limitacoes():
    sem_altura = bmr.bmr(_sujeito(height_cm=None))
    assert sem_altura.kcal is None and bmr.LIMIT_MISSING_HEIGHT in sem_altura.limitations
    sem_sexo = bmr.bmr(_sujeito(sex=None))
    assert sem_sexo.kcal is None and bmr.LIMIT_MISSING_SEX in sem_sexo.limitations
    pedido_katch = bmr.bmr(_sujeito(), formula=F.KATCH_MCARDLE)
    assert pedido_katch.kcal is None and pedido_katch.limitations == (bmr.LIMIT_MISSING_FFM,)


def test_mlg_velha_pedida_explicitamente_calcula_mas_avisa():
    resultado = bmr.bmr(_sujeito(fat_free_mass_kg=65.0), formula=F.KATCH_MCARDLE, ffm_age_days=90)
    assert resultado.kcal == 1774.0 and resultado.limitations == (bmr.LIMIT_FFM_STALE,)


def test_compare_with_scale_avisa_acima_de_15_por_cento():
    perto = bmr.compare_with_scale(1755, 1700)
    assert perto is not None and perto.warn is False
    assert perto.deviation_share == pytest.approx((1700 - 1755) / 1755)
    longe = bmr.compare_with_scale(1755, 2100)
    assert longe is not None and longe.warn is True
    assert bmr.compare_with_scale(None, 1700) is None
    assert bmr.compare_with_scale(1755, 0) is None
    assert bmr.SCALE_BMR_DEVIATION_WARN_SHARE == 0.15


def test_to_dict_serializavel():
    dados = bmr.bmr(_sujeito(fat_free_mass_kg=65.0)).to_dict()
    assert dados["formula"] == "katch_mcardle" and dados["limitations"] == []
    json.dumps(dados)
    json.dumps(bmr.compare_with_scale(1755, 1700).to_dict())

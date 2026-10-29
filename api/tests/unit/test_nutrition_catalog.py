"""Catálogo de alimentos (dono `app/domain/nutrition/catalog.py`, Lote L3a):
fontes, citação/licença da TACO, base g/ml, campos da RDC 429, regras de
entrada de um alimento (erros bloqueiam; Atwater, subtotal e soma dos macros só
avisam) e da porção."""

from __future__ import annotations

import json

import pytest

from app.domain.nutrition import catalog as cat
from app.domain.nutrition import labels as lb
from app.domain.nutrition import meal_math as mm
from app.domain.nutrition.macros import KCAL_PER_G_FAT

ARROZ = {"energy_kcal": 128.26, "carbohydrates_g": 28.06, "protein_g": 2.52, "total_fat_g": 0.23, "fiber_g": 1.56}


def test_campos_sao_codigos_da_rdc429_na_ordem_do_dono():
    rdc = [campo.code for campo in lb.RDC429_FIELDS]
    assert list(cat.PER_100_FIELDS) == [c for c in rdc if c in cat.PER_100_FIELDS]
    assert set(cat.SERVING_FIELDS) <= set(rdc)
    assert set(cat.REQUIRED_PER_100_FIELDS) <= set(cat.PER_100_FIELDS)
    assert set(cat.SUBTOTAL_OF) | set(cat.SUBTOTAL_OF.values()) <= set(cat.PER_100_FIELDS)
    assert set(cat.GRAM_FIELDS) | set(cat.MILLIGRAM_FIELDS) | {"energy_kcal"} == set(cat.PER_100_FIELDS)


def test_taco_citada_e_somente_leitura():
    assert "TACO" in cat.TACO_CITATION and "2011" in cat.TACO_CITATION and "NEPA" in cat.TACO_CITATION
    assert "citada a fonte" in cat.TACO_LICENSE
    assert cat.citation_for("taco") == cat.TACO_CITATION and cat.license_for(cat.FoodSource.TACO) == cat.TACO_LICENSE
    assert cat.citation_for("user") is None and cat.citation_for("nao_existe") is None
    assert cat.is_read_only("taco") and cat.is_read_only(cat.FoodSource.TACO)
    assert not cat.is_read_only("user") and not cat.is_read_only(None)
    assert cat.USER_ENTRY_SOURCE == cat.FoodSource.USER


@pytest.mark.parametrize(("bruto", "base"), [(None, mm.Unit.GRAM), ("g", mm.Unit.GRAM), (" ML ", mm.Unit.MILLILITER),
                                             (mm.Unit.MILLILITER, mm.Unit.MILLILITER), ("kg", None), (mm.Unit.CUP, None)])
def test_normalize_basis(bruto, base):
    assert cat.normalize_basis(bruto) == base


def test_alimento_valido_sem_erros_nem_avisos():
    resultado = cat.validate_food_values(ARROZ, "g")
    assert resultado.ok and resultado.errors == () and resultado.warnings == ()
    assert resultado.values["energy_kcal"] == 128.26 and resultado.values["sodium_mg"] is None
    assert resultado.atwater is not None and resultado.atwater.consistent
    json.dumps(resultado.to_dict())


def test_obrigatorios_ausentes_e_valores_invalidos_bloqueiam():
    resultado = cat.validate_food_values({"energy_kcal": -1, "protein_g": "x", "fiber_g": True})
    assert not resultado.ok
    assert set(resultado.errors) == {
        "invalid_value:energy_kcal",
        "invalid_value:protein_g",
        "invalid_value:fiber_g",
        "missing_field:carbohydrates_g",
        "missing_field:total_fat_g",
    }
    assert resultado.atwater is None


def test_base_invalida_bloqueia():
    resultado = cat.validate_food_values(ARROZ, "kg")
    assert resultado.errors[0] == cat.ERR_INVALID_BASIS and resultado.basis is None


def test_componente_acima_da_base_bloqueia_e_ml_aceita_a_densidade_maxima():
    acima = {**ARROZ, "protein_g": 100.5, "sodium_mg": 100_001}
    resultado = cat.validate_food_values(acima, "g")
    assert "above_basis:protein_g" in resultado.errors and "above_basis:sodium_mg" in resultado.errors
    # 100 ml podem pesar até 100 × densidade máxima.
    mel = {"energy_kcal": 430, "carbohydrates_g": 110, "protein_g": 0.4, "total_fat_g": 0}
    assert cat.validate_food_values(mel, "g").errors == ("above_basis:carbohydrates_g",)
    assert cat.validate_food_values(mel, "ml").ok
    teto_kcal = mm.PER_100G_REFERENCE * KCAL_PER_G_FAT
    assert "above_basis:energy_kcal" in cat.validate_food_values({**ARROZ, "energy_kcal": teto_kcal + 1}).errors
    assert cat.max_basis_mass_g(mm.Unit.MILLILITER) == mm.PER_100G_REFERENCE * cat.ML_BASIS_MAX_DENSITY_G_PER_ML


def test_avisos_nao_bloqueiam():
    resultado = cat.validate_food_values(
        {**ARROZ, "energy_kcal": 300, "total_sugars_g": 30, "added_sugars_g": 31, "saturated_fat_g": 1, "trans_fat_g": 0},
        "g",
    )
    assert resultado.ok
    assert cat.WARN_ATWATER in resultado.warnings
    assert "subtotal_above_total:total_sugars_g" in resultado.warnings  # 30 > 28,06 de carboidrato
    assert "subtotal_above_total:added_sugars_g" in resultado.warnings
    assert "subtotal_above_total:saturated_fat_g" in resultado.warnings  # 1 > 0,23
    assert not resultado.atwater.consistent
    macros = cat.validate_food_values({"energy_kcal": 900, "carbohydrates_g": 60, "protein_g": 30, "total_fat_g": 20})
    assert cat.WARN_MACROS_ABOVE_BASIS in macros.warnings and macros.ok


@pytest.mark.parametrize(
    ("porcao", "embalagem", "erros"),
    [
        (30, 10, set()),
        (None, None, set()),
        (0, None, {"invalid_value:serving_size"}),
        (cat.PORTION_AMOUNT_MAX + 1, None, {"invalid_value:serving_size"}),
        (30, 0, {"invalid_value:servings_per_package"}),
        ("x", -1, {"invalid_value:serving_size", "invalid_value:servings_per_package"}),
    ],
)
def test_porcao_do_rotulo(porcao, embalagem, erros):
    resultado = cat.validate_food_values({**ARROZ, "serving_size": porcao, "servings_per_package": embalagem})
    assert set(resultado.errors) == erros
    if not erros:
        assert resultado.values["serving_size"] == porcao and resultado.values["servings_per_package"] == embalagem


def test_campo_fora_da_tabela_e_ignorado():
    assert cat.validate_food_values({**ARROZ, "cholesterol_mg": -5}).ok


@pytest.mark.parametrize(("quantidade", "erro"), [(86, None), (0.5, None), (cat.PORTION_AMOUNT_MAX, None),
                                                  (0, cat.ERR_INVALID_PORTION_AMOUNT), (-1, cat.ERR_INVALID_PORTION_AMOUNT),
                                                  (cat.PORTION_AMOUNT_MAX + 1, cat.ERR_INVALID_PORTION_AMOUNT),
                                                  (None, cat.ERR_INVALID_PORTION_AMOUNT), ("x", cat.ERR_INVALID_PORTION_AMOUNT)])
def test_validate_portion_amount(quantidade, erro):
    assert cat.validate_portion_amount(quantidade) == erro


def test_todo_codigo_do_catalogo_tem_texto():
    for nome, valor in vars(cat).items():
        if nome.startswith(("ERR_", "WARN_")):
            texto = lb.limitation_label(valor) if valor in lb.LIMITATION_LABELS else lb.limitation_label(f"{valor}:protein_g")
            assert texto and texto != valor and not texto.startswith(valor), nome

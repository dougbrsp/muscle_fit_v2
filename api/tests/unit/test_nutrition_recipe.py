"""Receita (issue #286): cálculo do dono `domain/nutrition/recipe.py`."""

import pytest

from app.domain.nutrition import catalog, diary, labels
from app.domain.nutrition import recipe as rc
from app.domain.nutrition.meal_math import Unit

ARROZ = {"energy_kcal": 350, "carbohydrates_g": 78, "total_sugars_g": 1, "added_sugars_g": 0, "protein_g": 7,
         "total_fat_g": 1, "saturated_fat_g": 0.3, "trans_fat_g": 0, "fiber_g": 2, "sodium_mg": 5}
FRANGO = {"energy_kcal": 160, "carbohydrates_g": 0, "total_sugars_g": 0, "added_sugars_g": 0, "protein_g": 30,
          "total_fat_g": 4, "saturated_fat_g": 1, "trans_fat_g": 0, "fiber_g": 0, "sodium_mg": 60}


def _ing(label, amount, per_100, basis=Unit.GRAM):
    return rc.RecipeIngredient(label, amount, basis, per_100)


def _base(final=450):
    return rc.calculate_recipe([_ing("arroz", 200, ARROZ), _ing("frango", 100, FRANGO)], final)


def test_caso_base_com_numeros_fechados():
    r = _base()
    assert r.totals["energy_kcal"] == pytest.approx(860)
    assert r.totals["protein_g"] == pytest.approx(44)
    assert r.totals["sodium_mg"] == pytest.approx(70)
    assert r.per_100_g["energy_kcal"] == pytest.approx(860 / 4.5)
    assert r.per_100_g["carbohydrates_g"] == pytest.approx(156 / 4.5)
    assert r.per_100_g["protein_g"] == pytest.approx(44 / 4.5)
    assert r.per_100_g["fiber_g"] == pytest.approx(4 / 4.5)
    assert r.raw_weight_g == 300 and r.final_weight_g == 450
    assert r.yield_percent == pytest.approx(150)
    assert r.incomplete_fields == () and r.limitations == () and r.warnings == ()
    assert r.atwater is not None and r.atwater.consistent
    assert r.engine_version == rc.RECIPE_ENGINE_VERSION == "recipe-v1"
    assert r.to_dict()["per_100_g"]["energy_kcal"] == r.per_100_g["energy_kcal"]


def test_rendimento_abaixo_de_100():
    r = _base(200)
    assert r.yield_percent == pytest.approx(200 / 3)
    assert r.per_100_g["energy_kcal"] == pytest.approx(430)


def test_ingrediente_sem_fibra_deixa_fibra_incompleta_e_nao_zero():
    sem_fibra = {**FRANGO, "fiber_g": None}
    r = rc.calculate_recipe([_ing("arroz", 200, ARROZ), _ing("frango", 100, sem_fibra)], 450)
    assert r.incomplete_fields == ("fiber_g",)
    assert r.totals["fiber_g"] == pytest.approx(4)  # mínimo conhecido
    assert r.per_100_g["fiber_g"] is None
    assert r.missing_by_field == {"fiber_g": ("frango",)}
    assert "recipe_incomplete:fiber_g" in r.limitations
    assert r.per_100_g["protein_g"] is not None


def test_campo_que_ninguem_informa_fica_none():
    sem = {**ARROZ, "trans_fat_g": None}
    r = rc.calculate_recipe([_ing("a", 100, sem)], 100)
    assert r.totals["trans_fat_g"] is None and r.incomplete_fields == ("trans_fat_g",)


@pytest.mark.parametrize("ingredientes,final,codigo", [
    ([], 100, rc.ERR_NO_INGREDIENTS),
    ([_ing(f"i{n}", 10, ARROZ) for n in range(rc.RECIPE_MAX_INGREDIENTS + 1)], 100, rc.ERR_TOO_MANY_INGREDIENTS),
    ([_ing("a", 100, ARROZ)], 0, rc.ERR_INVALID_FINAL_WEIGHT),
    ([_ing("a", 100, ARROZ)], -5, rc.ERR_INVALID_FINAL_WEIGHT),
    ([_ing("a", 100, ARROZ)], None, rc.ERR_INVALID_FINAL_WEIGHT),
    ([_ing("a", 100, ARROZ)], 10**6, rc.ERR_INVALID_FINAL_WEIGHT),
    ([_ing("a", 100, ARROZ)], 9, rc.ERR_YIELD_IMPLAUSIBLE),
    ([_ing("a", 100, ARROZ)], 501, rc.ERR_YIELD_IMPLAUSIBLE),
    ([_ing("a", 0, ARROZ)], 100, f"{rc.ERR_INVALID_INGREDIENT_AMOUNT}:0"),
])
def test_erros_tipados(ingredientes, final, codigo):
    with pytest.raises(rc.RecipeError) as erro:
        rc.calculate_recipe(ingredientes, final)
    assert erro.value.code == codigo


def test_limites_de_rendimento_sao_aceitos():
    assert rc.calculate_recipe([_ing("a", 100, ARROZ)], 10).yield_percent == pytest.approx(10)
    assert rc.calculate_recipe([_ing("a", 100, ARROZ)], 500).yield_percent == pytest.approx(500)


def test_ingrediente_em_ml_entra_nos_nutrientes_mas_nao_no_peso_cru():
    leite = {**FRANGO, "energy_kcal": 60}
    r = rc.calculate_recipe([_ing("arroz", 100, ARROZ), _ing("leite", 200, leite, Unit.MILLILITER)], 250)
    assert r.raw_weight_g == 100
    assert rc.LIMIT_RAW_WEIGHT_EXCLUDES_ML in r.limitations
    assert r.totals["energy_kcal"] == pytest.approx(350 + 120)
    assert r.yield_percent is None


def test_gramas_mais_ml_nao_aplica_trava_de_rendimento():
    leite = {**FRANGO, "energy_kcal": 60}
    r = rc.calculate_recipe([_ing("aveia", 50, ARROZ), _ing("leite", 500, leite, Unit.MILLILITER)], 550)
    assert r.yield_percent is None and r.raw_weight_g == 50
    assert rc.LIMIT_RAW_WEIGHT_EXCLUDES_ML in r.limitations


def test_gramas_mais_ml_continua_com_teto_absoluto_do_peso_final():
    with pytest.raises(rc.RecipeError) as erro:
        rc.calculate_recipe([_ing("a", 50, ARROZ), _ing("l", 500, FRANGO, Unit.MILLILITER)],
                            rc.RECIPE_FINAL_WEIGHT_MAX_G + 1)
    assert erro.value.code == rc.ERR_INVALID_FINAL_WEIGHT


def test_so_ml_sem_peso_cru_nao_calcula_rendimento():
    r = rc.calculate_recipe([_ing("leite", 200, FRANGO, Unit.MILLILITER)], 200)
    assert r.raw_weight_g == 0 and r.yield_percent is None


def test_checagem_de_energia_avisa_quando_inconsistente():
    ruim = {**ARROZ, "energy_kcal": 900}
    r = rc.calculate_recipe([_ing("a", 100, ruim)], 100)
    assert r.warnings == (rc.WARN_ATWATER,) and not r.atwater.consistent


def test_substituicao_manual_preserva_o_calculado():
    calculado = _base().per_100_g
    r = rc.apply_overrides(calculado, {"sodium_mg": 30})
    assert r.effective["sodium_mg"] == 30 and r.calculated["sodium_mg"] == pytest.approx(70 / 4.5)
    assert r.overridden_fields == ("sodium_mg",)
    assert r.effective["protein_g"] == pytest.approx(calculado["protein_g"])
    assert rc.apply_overrides(calculado, None).overridden_fields == ()


def test_substituicao_none_significa_sem_substituicao():
    calculado = _base().per_100_g
    r = rc.apply_overrides(calculado, {"sodium_mg": None, "protein_g": None})
    assert r.overridden_fields == ()
    assert r.effective["sodium_mg"] == pytest.approx(r.calculated["sodium_mg"])
    assert r.effective["protein_g"] == pytest.approx(calculado["protein_g"])
    with pytest.raises(rc.RecipeError):
        rc.apply_overrides(calculado, {"campo_inexistente": None})


def test_teto_de_ingredientes_vem_do_diario():
    assert rc.RECIPE_MAX_INGREDIENTS == diary.MEAL_MAX_ITEMS


def test_substituicao_pode_preencher_campo_incompleto():
    r = rc.apply_overrides({**_base().per_100_g, "fiber_g": None}, {"fiber_g": 1.5})
    assert r.effective["fiber_g"] == 1.5 and r.calculated["fiber_g"] is None


@pytest.mark.parametrize("overrides,codigo", [
    ({"nome": 1}, f"{rc.ERR_OVERRIDE_FIELD}:nome"),
    ({"protein_g": -1}, f"{rc.ERR_OVERRIDE_VALUE}:protein_g"),
    ({"protein_g": "abc"}, f"{rc.ERR_OVERRIDE_VALUE}:protein_g"),
    ({"protein_g": 150}, f"{rc.ERR_OVERRIDE_VALUE}:protein_g"),  # acima da base de 100 g
    ({"protein_g": True}, f"{rc.ERR_OVERRIDE_VALUE}:protein_g"),  # bool não vira 1.0
])
def test_substituicao_invalida_e_recusada(overrides, codigo):
    with pytest.raises(rc.RecipeError) as erro:
        rc.apply_overrides(_base().per_100_g, overrides)
    assert erro.value.code == codigo


def test_origem_receita_no_catalogo():
    assert catalog.is_recipe_source("recipe") and catalog.is_recipe_source(catalog.FoodSource.RECIPE)
    assert not catalog.is_recipe_source("user") and not catalog.is_recipe_source(None)
    assert catalog.FoodSource.RECIPE not in catalog.READ_ONLY_SOURCES
    assert catalog.FoodSource.RECIPE not in catalog.USER_ENTRY_SOURCES
    assert catalog.user_entry_source("recipe") is None
    origem = catalog.food_origin("recipe", None)
    assert origem.value == "recipe" and labels.FOOD_ORIGIN_LABELS[origem] == "Receita"


def test_conjunto_obrigatorio_da_receita_exige_so_energia():
    assert catalog.RECIPE_REQUIRED_PER_100_FIELDS == ("energy_kcal",)
    req = catalog.RECIPE_REQUIRED_PER_100_FIELDS
    ok = catalog.validate_food_values({"energy_kcal": 100, "total_fat_g": 1}, required=req)
    assert ok.errors == ()
    assert ok.values["carbohydrates_g"] is None and ok.values["protein_g"] is None
    sem_energia = catalog.validate_food_values({"protein_g": 5}, required=req)
    assert f"{catalog.ERR_MISSING_FIELD}:energy_kcal" in sem_energia.errors
    padrao = catalog.validate_food_values({"energy_kcal": 100, "total_fat_g": 1})
    assert f"{catalog.ERR_MISSING_FIELD}:carbohydrates_g" in padrao.errors


def test_status_do_ingrediente_no_catalogo_current_outdated_unavailable():
    base = {"energy_kcal": 130.0, "protein_g": None}
    assert rc.ingredient_catalog_status(base, dict(base)) == rc.CATALOG_CURRENT
    assert rc.ingredient_catalog_status(base, {**base, "energy_kcal": 131.0}) == rc.CATALOG_OUTDATED
    assert rc.ingredient_catalog_status(base, {**base, "protein_g": 1.0}) == rc.CATALOG_OUTDATED
    assert rc.ingredient_catalog_status(base, None) == rc.CATALOG_UNAVAILABLE


def test_teto_do_ingrediente_e_proprio_e_a_chave_normaliza_a_unidade():
    from app.domain.nutrition import meal_math

    assert rc.RECIPE_INGREDIENT_AMOUNT_MAX > diary.ITEM_AMOUNT_MAX
    ok = meal_math.resolve_amount(diary.ITEM_AMOUNT_MAX + 1, "g", "g")
    assert rc.ingredient_amount_errors(ok) == ()
    acima = meal_math.resolve_amount(rc.RECIPE_INGREDIENT_AMOUNT_MAX + 1, "g", "g")
    assert rc.ingredient_amount_errors(acima) == (rc.ERR_INGREDIENT_AMOUNT_OUT_OF_RANGE,)
    assert rc.ingredient_key(1, 100, " G ", None) == rc.ingredient_key(1, 100.0, "g", None)


def test_overrides_da_versao_gravada_e_nome_da_copia():
    efetivo = {"effective": {"energy_kcal": 170, "protein_g": 25}, "overridden_fields": ["energy_kcal"]}
    assert rc.overrides_from_effective(efetivo) == {"energy_kcal": 170}
    assert rc.overrides_from_effective(None) == {}
    copia = rc.copy_name("x" * rc.RECIPE_NAME_MAX_CHARS)
    assert len(copia) == rc.RECIPE_NAME_MAX_CHARS and copia.endswith(rc.RECIPE_COPY_SUFFIX)

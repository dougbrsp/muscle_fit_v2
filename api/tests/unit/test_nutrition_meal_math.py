"""Matemática da refeição (dono `app/domain/nutrition/meal_math.py`, D2/E4/E9):
gramas por unidade/porção/medida caseira, macros do item por 100 g, natureza da
refeição, consistência de Atwater (15%) e do rótulo RDC 429 (10%)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app import migrate as m
from app.domain.data_nature import DataNature
from app.domain.nutrition import catalog
from app.domain.nutrition import macros as mc
from app.domain.nutrition import meal_math as mm

U = mm.Unit
D, E, M = mm.MealNature.DERIVED, mm.MealNature.ESTIMATED, mm.MealNature.MIXED


def test_constantes():
    assert mm.PER_100G_REFERENCE == 100.0 and mm.DEFAULT_DENSITY_G_PER_ML == 1.0
    assert mm.KJ_PER_KCAL == 4.184
    assert mm.ATWATER_TOLERANCE_SHARE == 0.15 and mm.LABEL_SERVING_TOLERANCE_SHARE == 0.10
    assert {u: p.ml for u, p in mm.GENERIC_PORTIONS.items()} == {
        U.TABLESPOON: 15.0,
        U.DESSERT_SPOON: 10.0,
        U.TEASPOON: 5.0,
        U.CUP: 240.0,
    }
    assert all(p.code == u.value and p.label for u, p in mm.GENERIC_PORTIONS.items())


@pytest.mark.parametrize(
    ("quantidade", "unidade", "porcao_g", "densidade", "gramas", "assumida", "limitacoes"),
    [
        (150, "g", None, None, 150.0, False, ()),
        (" 150 ", " G ", None, None, 150.0, False, ()),
        (200, "ml", None, 1.03, 206.0, False, ()),
        (200, "ml", None, None, 200.0, True, (mm.LIMIT_ASSUMED_DENSITY,)),
        (2, "colher_sopa", None, 0.9, 27.0, False, ()),
        (1, U.CUP, None, None, 240.0, True, (mm.LIMIT_ASSUMED_DENSITY,)),
        (3, "colher_cha", None, 1.0, 15.0, False, ()),
        (2, "colher_sobremesa", None, 0.5, 10.0, False, ()),
        (3, "unit", 50, None, 150.0, False, ()),
        (1.5, "portion", 30, None, 45.0, False, ()),
        (1, "portion", None, None, None, False, (mm.LIMIT_MISSING_PORTION_G,)),
        (2, "unit", 0, None, None, False, (mm.LIMIT_MISSING_PORTION_G,)),
        (0, "g", None, None, None, False, (mm.LIMIT_MISSING_QUANTITY,)),
        (None, "g", None, None, None, False, (mm.LIMIT_MISSING_QUANTITY,)),
        (100, "kg", None, None, None, False, (mm.LIMIT_UNKNOWN_UNIT,)),
        (100, None, None, None, None, False, (mm.LIMIT_UNKNOWN_UNIT,)),
    ],
)
def test_resolve_grams(quantidade, unidade, porcao_g, densidade, gramas, assumida, limitacoes):
    resultado = mm.resolve_grams(quantidade, unidade, porcao_g, densidade)
    assert resultado.grams == (pytest.approx(gramas) if gramas is not None else None)
    assert resultado.assumed_density is assumida
    assert resultado.limitations == limitacoes
    json.dumps(resultado.to_dict())


ARROZ_100G = mc.Macros(kcal=128.0, protein_g=2.5, carb_g=28.1, fat_g=0.2, fiber_g=1.6)


@pytest.mark.parametrize(("gramas", "fator"), [(150, 1.5), (100, 1.0), (0, 0.0), (35.5, 0.355)])
def test_item_macros_escala_o_catalogo_por_100_g(gramas, fator):
    item = mm.item_macros(ARROZ_100G, gramas)
    assert item.kcal == pytest.approx(128.0 * fator) and item.carb_g == pytest.approx(28.1 * fator)
    assert item.fiber_g == pytest.approx(1.6 * fator)


@pytest.mark.parametrize("gramas", [None, -1, "abc"])
def test_item_macros_sem_gramas_validas(gramas):
    assert mm.item_macros(ARROZ_100G, gramas) is None


def test_sum_macros_ignora_item_sem_macros():
    total = mm.sum_macros([mm.item_macros(ARROZ_100G, 150), None, mc.Macros(kcal=70.0, protein_g=6.0, carb_g=0.5, fat_g=5.0)])
    assert total.kcal == pytest.approx(192.0 + 70.0) and total.protein_g == pytest.approx(3.75 + 6.0)
    assert total.fiber_g == pytest.approx(2.4)
    assert mm.sum_macros([]) == mc.ZERO_MACROS


def test_natureza_da_refeicao_reusa_o_vocabulario_comum():
    """DERIVED/ESTIMATED são os valores de `DataNature` (dono `domain/data_nature.py`);
    só MIXED é da refeição."""
    assert D.value == DataNature.DERIVED.value and E.value == DataNature.ESTIMATED.value
    assert M.value == "MIXED" and M.value not in {n.value for n in DataNature}
    assert [n.value for n in mm.MealNature] == ["DERIVED", "ESTIMATED", "MIXED"]


@pytest.mark.parametrize(
    ("naturezas", "esperado"),
    [((), None), ((D, D), D), ((E,), E), ((D, E), M), ((D, D, E), M), ((M,), M)],
)
def test_meal_nature(naturezas, esperado):
    assert mm.meal_nature(naturezas) == esperado


# Proteína 10 g, carbo 20 g, gordura 5 g -> 4·10 + 4·20 + 9·5 = 165 kcal (Atwater).
@pytest.mark.parametrize(
    ("declarada", "consistente"),
    [
        (165.0, True),
        (141.0, True),  # 24/165 = 14,5%
        (140.0, False),  # 25/165 = 15,2%
        (194.0, True),  # 29/194 = 14,9% (desvio relativo ao maior dos dois)
        (195.0, False),  # 30/195 = 15,4%
    ],
)
def test_atwater_tolerancia_de_15_por_cento(declarada, consistente):
    checagem = mm.atwater_check(mc.Macros(kcal=declarada, protein_g=10.0, carb_g=20.0, fat_g=5.0))
    assert checagem.computed_kcal == pytest.approx(165.0)
    assert checagem.consistent is consistente
    assert checagem.deviation_share == pytest.approx(abs(declarada - 165.0) / max(declarada, 165.0))
    json.dumps(checagem.to_dict())


def test_atwater_no_limite_exato_e_consistente():
    # 25 g de proteína = 100 kcal; declarada 85 = 15% exatos.
    assert mm.atwater_check(mc.Macros(kcal=85.0, protein_g=25.0, carb_g=0.0, fat_g=0.0)).consistent is True


def test_atwater_bordas():
    assert mm.atwater_check(mc.ZERO_MACROS).consistent is True
    assert mm.atwater_check(mc.Macros(kcal=-1.0, protein_g=1.0, carb_g=1.0, fat_g=1.0)) is None
    assert mm.atwater_check(mc.Macros(kcal=10.0, protein_g=-1.0, carb_g=1.0, fat_g=1.0)) is None


POR_100G = mc.Macros(kcal=400.0, protein_g=10.0, carb_g=70.0, fat_g=8.0, fiber_g=5.0)


@pytest.mark.parametrize(
    ("por_porcao", "fora"),
    [
        (mc.Macros(kcal=120.0, protein_g=3.0, carb_g=21.0, fat_g=2.4, fiber_g=1.5), ()),
        (mc.Macros(kcal=132.0, protein_g=3.0, carb_g=21.0, fat_g=2.4, fiber_g=1.5), ()),  # 12/132 = 9,1%
        (mc.Macros(kcal=135.0, protein_g=3.0, carb_g=21.0, fat_g=2.4, fiber_g=1.5), ("kcal",)),  # 11,1%
        (mc.Macros(kcal=120.0, protein_g=3.0, carb_g=21.0, fat_g=3.0, fiber_g=2.0), ("fat_g", "fiber_g")),
        (mc.Macros(kcal=120.0, protein_g=3.0, carb_g=21.0, fat_g=2.4), ()),  # sem fibra declarada
    ],
)
def test_rotulo_por_porcao_bate_com_por_100_g_em_10_por_cento(por_porcao, fora):
    checagem = mm.label_consistency(POR_100G, por_porcao, 30)
    assert checagem.serving_g == 30.0
    assert checagem.fields_off == fora and checagem.consistent is (not fora)
    json.dumps(checagem.to_dict())


@pytest.mark.parametrize("porcao", [None, 0, -5])
def test_rotulo_sem_porcao(porcao):
    assert mm.label_consistency(POR_100G, POR_100G, porcao) is None


def test_kj_e_kcal():
    assert mm.kj_to_kcal(418.4) == pytest.approx(100.0)
    assert mm.kcal_to_kj(100) == pytest.approx(418.4)
    assert mm.kj_to_kcal(-1) is None and mm.kcal_to_kj(None) is None


@pytest.mark.parametrize(("bruto", "esperado"), [("g", U.GRAM), (" XICARA ", U.CUP), (U.UNIT, U.UNIT), ("xícara", None), (None, None)])
def test_normalize_unit(bruto, esperado):
    assert mm.normalize_unit(bruto) == esperado


# ---------------------------------------------------------------------------
# Diário (L4): campos, regra do macro ausente com as linhas REAIS da TACO,
# quantidade na base e totais incompletos (None nunca vira 0 em silêncio)
# ---------------------------------------------------------------------------

_SEED = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "081_food_catalog_seed_taco.py").__globals__
# 084 (M85): campos `*` ("em reavaliação") da TACO preenchidos com o equivalente do USDA.
_USDA = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "084_food_items_taco_usda_reavaliacao.py").__globals__
TACO_081 = {linha[0]: dict(zip(_SEED["SEED_COLUMNS"], linha)) for linha in _SEED["SEED_ROWS"]}
# O catálogo como fica no banco: seed da 081 + preenchimentos da 084.
TACO = {sid: dict(linha) for sid, linha in TACO_081.items()}
for _registro in _USDA["fills"]():
    TACO[_registro["source_id"]].update({campo: str(valor) for campo, valor in _registro["values"].items()})
ARROZ, OLEO_SOJA, FRANGO_PEITO, LEITE_INTEGRAL, AGUARDENTE, SAL_GROSSO = "3", "272", "410", "458", "472", "517"


def _taco(source_id: str) -> mm.Nutrients:
    return mm.nutrients_from_values(TACO[source_id])


def test_campos_do_diario_sao_os_codigos_do_catalogo():
    assert set(mm.NUTRIENT_FIELDS) <= set(catalog.PER_100_FIELDS)
    assert set(mm.ATWATER_MACRO_FIELDS) < set(mm.NUTRIENT_FIELDS)
    assert list(mm.Nutrients().to_dict()) == list(mm.NUTRIENT_FIELDS)


def test_linhas_da_taco_usadas_no_teste_sao_as_esperadas():
    assert TACO[OLEO_SOJA]["name"] == "Óleo, de soja"
    assert TACO[SAL_GROSSO]["name"] == "Sal, grosso"
    assert TACO[LEITE_INTEGRAL]["name"] == "Leite, de vaca, integral"  # em reavaliação na TACO
    assert TACO[AGUARDENTE]["name"] == "Cana, aguardente"


def test_oleo_da_taco_infere_proteina_e_carbo_zero_pela_energia():
    """884 kcal, 100 g de gordura, proteína e carbo "NA": 900 kcal de Atwater
    explicam a energia (1,8% < 15%) -> os dois macros valem 0, com o código."""
    completo = mm.complete_missing_nutrients(_taco(OLEO_SOJA))
    assert completo.nutrients.protein_g == 0.0 and completo.nutrients.carbohydrates_g == 0.0
    assert completo.nutrients.fiber_g == 0.0  # carboidrato 0 -> fibra 0
    assert completo.inferred_zero == ("protein_g", "carbohydrates_g", "fiber_g")
    assert completo.unknown == ("sodium_mg",)  # sódio "NA": nunca inferido
    assert completo.limitations == (
        "macro_inferred_zero:protein_g",
        "macro_inferred_zero:carbohydrates_g",
        mm.LIMIT_FIBER_INFERRED_ZERO,
        "nutrient_unknown:sodium_mg",
    )
    json.dumps(completo.to_dict())


def test_sal_da_taco_sem_energia_fica_desconhecido():
    """Sem energia não há o que os macros expliquem: tudo menos o sódio fica None."""
    completo = mm.complete_missing_nutrients(_taco(SAL_GROSSO))
    assert completo.inferred_zero == ()
    assert completo.unknown == ("energy_kcal", "protein_g", "carbohydrates_g", "total_fat_g", "fiber_g")
    assert completo.nutrients.sodium_mg == pytest.approx(39943.20)


def test_item_em_reavaliacao_na_taco_usa_o_usda_e_deixa_de_ficar_desconhecido():
    """Leite integral: energia e macros `*` na TACO. Só com o seed da 081 tudo
    fica desconhecido; com a 084 (USDA SR Legacy, FDC ID 172217) energia e os
    três macros são conhecidos. A fibra é `NA` na TACO (não aplicável): continua
    NULL e, com carboidrato > 0, desconhecida (a regra do L4 não muda)."""
    so_081 = mm.complete_missing_nutrients(mm.nutrients_from_values(TACO_081[LEITE_INTEGRAL]))
    assert so_081.nutrients.energy_kcal is None and mm.ENERGY_FIELD in so_081.unknown
    completo = mm.complete_missing_nutrients(_taco(LEITE_INTEGRAL))
    assert completo.inferred_zero == () and completo.unknown == ("fiber_g",)
    assert completo.nutrients.energy_kcal == pytest.approx(61.0)
    assert completo.nutrients.protein_g == pytest.approx(3.15)
    assert completo.nutrients.carbohydrates_g == pytest.approx(4.78)
    assert completo.nutrients.total_fat_g == pytest.approx(3.27)
    assert completo.nutrients.sodium_mg == pytest.approx(63.76)  # medido pela TACO


def test_energia_que_os_macros_nao_explicam_nao_infere_nada():
    """Aguardente: 215,66 kcal (álcool) e nenhum macro -> 0 kcal de Atwater, 100% de desvio."""
    completo = mm.complete_missing_nutrients(_taco(AGUARDENTE))
    assert completo.inferred_zero == ()
    assert set(mm.ATWATER_MACRO_FIELDS) <= set(completo.unknown)


def test_carne_com_carbo_zero_tem_fibra_zero_e_arroz_completo_fica_igual():
    frango = mm.complete_missing_nutrients(_taco(FRANGO_PEITO))
    assert frango.inferred_zero == ("fiber_g",) and frango.unknown == ()
    arroz = mm.complete_missing_nutrients(_taco(ARROZ))
    assert arroz.inferred_zero == () and arroz.unknown == () and arroz.nutrients == _taco(ARROZ)


@pytest.mark.parametrize(
    ("quantidade", "unidade", "base", "porcao", "densidade", "quantia", "assumida", "limitacoes"),
    [
        (150, "g", "g", None, None, 150.0, False, ()),
        (200, "ml", "ml", None, None, 200.0, False, ()),
        (200, "ml", "g", None, None, 200.0, True, (mm.LIMIT_ASSUMED_DENSITY,)),
        (200, "ml", "g", None, 1.03, 206.0, False, ()),
        (2, "colher_sopa", "g", None, None, 30.0, True, (mm.LIMIT_ASSUMED_DENSITY,)),
        (1, "xicara", "ml", None, None, 240.0, False, ()),
        (103, "g", "ml", None, 1.03, 100.0, False, ()),
        (50, "g", "ml", None, None, 50.0, True, (mm.LIMIT_ASSUMED_DENSITY,)),
        (2, "portion", "g", 30, None, 60.0, False, ()),
        (1, "unit", "ml", 200, None, 200.0, False, ()),
        (1, "portion", "g", None, None, None, False, (mm.LIMIT_MISSING_PORTION_G,)),
        (0, "g", "g", None, None, None, False, (mm.LIMIT_MISSING_QUANTITY,)),
        (100, "kg", "g", None, None, None, False, (mm.LIMIT_UNKNOWN_UNIT,)),
        (100, "g", "kg", None, None, None, False, (mm.LIMIT_UNKNOWN_BASIS,)),
        (100, "g", "colher_sopa", None, None, None, False, (mm.LIMIT_UNKNOWN_BASIS,)),
    ],
)
def test_resolve_amount_na_base_do_alimento(quantidade, unidade, base, porcao, densidade, quantia, assumida, limitacoes):
    resolucao = mm.resolve_amount(quantidade, unidade, base, porcao, densidade)
    assert resolucao.amount == (pytest.approx(quantia) if quantia is not None else None)
    assert resolucao.assumed_density is assumida and resolucao.limitations == limitacoes
    assert resolucao.resolved is (quantia is not None)
    assert set(resolucao.limitations) & mm.AMOUNT_BLOCKING_LIMITATIONS == (set() if quantia else set(limitacoes))
    json.dumps(resolucao.to_dict())


@pytest.mark.parametrize(
    ("unidade", "escolhida", "rotulo", "esperado"),
    [
        ("portion", 30, 50, 30.0),
        ("portion", None, 50, 50.0),  # sem porção cadastrada: a do rótulo
        ("unit", None, 50, None),  # unidade sem peso cadastrado não usa o rótulo
        ("unit", 86, None, 86.0),
        ("g", 30, 50, None),
        ("portion", None, None, None),
    ],
)
def test_portion_amount_for(unidade, escolhida, rotulo, esperado):
    assert mm.portion_amount_for(unidade, escolhida, rotulo) == esperado


def _item(source_id: str, gramas: float) -> mm.ItemNutrition:
    return mm.item_nutrition(_taco(source_id), mm.resolve_amount(gramas, "g", "g"))


def test_item_escala_por_100_e_carrega_as_limitacoes():
    arroz = _item(ARROZ, 150)
    assert arroz.amount == 150.0 and arroz.basis == U.GRAM and arroz.nature == D
    assert arroz.nutrients.energy_kcal == pytest.approx(128.26 * 1.5)
    assert arroz.nutrients.sodium_mg == pytest.approx(1.20 * 1.5)
    oleo = _item(OLEO_SOJA, 10)
    assert oleo.nutrients.energy_kcal == pytest.approx(88.4) and oleo.nutrients.protein_g == 0.0
    assert oleo.nutrients.sodium_mg is None and "nutrient_unknown:sodium_mg" in oleo.limitations
    assert mm.item_nutrition(_taco(ARROZ), mm.resolve_amount(0, "g", "g")) is None
    ml = mm.item_nutrition(_taco(ARROZ), mm.resolve_amount(100, "ml", "g"))
    assert ml.limitations[0] == mm.LIMIT_ASSUMED_DENSITY
    json.dumps(oleo.to_dict())


def test_total_da_refeicao_soma_o_conhecido_e_marca_o_incompleto():
    arroz, oleo, sal = _item(ARROZ, 150), _item(OLEO_SOJA, 10), _item(SAL_GROSSO, 1)
    completo = mm.sum_items([arroz, _item(FRANGO_PEITO, 120)])
    assert completo.incomplete_fields == () and completo.limitations == ()
    assert completo.is_complete("energy_kcal") and completo.item_count == 2 and completo.nature == D
    total = mm.sum_items([arroz, oleo, sal])
    # Energia: sal sem energia -> soma do que se conhece + campo incompleto (um mínimo, não um total).
    assert total.nutrients.energy_kcal == pytest.approx(arroz.nutrients.energy_kcal + 88.4)
    assert set(total.incomplete_fields) == {"energy_kcal", "protein_g", "carbohydrates_g", "total_fat_g", "fiber_g", "sodium_mg"}
    assert not total.is_complete("energy_kcal") and "total_incomplete:energy_kcal" in total.limitations
    assert total.complete_nutrients().energy_kcal is None
    assert total.nutrients.sodium_mg == pytest.approx(1.20 * 1.5 + 399.432)
    so_sal = mm.sum_items([sal])
    assert so_sal.nutrients.energy_kcal is None and so_sal.nutrients.protein_g is None  # nenhuma contribuição: None
    vazio = mm.sum_items([])
    assert vazio.nutrients == mm.Nutrients() and vazio.incomplete_fields == mm.NUTRIENT_FIELDS and vazio.nature is None
    json.dumps(total.to_dict())


def test_total_do_dia_combina_as_refeicoes_e_sem_refeicao_e_none():
    cafe = mm.sum_items([_item(ARROZ, 100)])
    almoco = mm.sum_items([_item(OLEO_SOJA, 10)])
    dia = mm.combine_totals([cafe, almoco])
    assert dia.nutrients.energy_kcal == pytest.approx(128.26 + 88.4)
    assert dia.incomplete_fields == ("sodium_mg",) and dia.item_count == 2
    assert mm.combine_totals([]) is None
    estimado = mm.NutritionTotals(nutrients=mm.Nutrients(energy_kcal=100.0), incomplete_fields=(), nature=E, item_count=1)
    assert mm.combine_totals([cafe, estimado]).nature == M


def test_total_gravado_volta_ao_motor():
    total = mm.sum_items([_item(ARROZ, 150), _item(OLEO_SOJA, 10)])
    gravado = {campo: (None if v is None else Decimal(str(round(v, 2)))) for campo, v in total.nutrients.to_dict().items()}
    lido = mm.totals_from_stored(gravado, ["sodium_mg", "campo_estranho"], "DERIVED", 2)
    assert lido.incomplete_fields == ("sodium_mg",) and lido.nature == D and lido.item_count == 2
    assert lido.nutrients.energy_kcal == pytest.approx(total.nutrients.energy_kcal, abs=0.01)
    assert mm.totals_from_stored({}, None).nature is None
    assert mm.normalize_meal_nature("mixed") == M and mm.normalize_meal_nature("x") is None


def test_as_macros_so_com_energia_e_tres_macros():
    assert mm.Nutrients(energy_kcal=100.0, protein_g=1.0, carbohydrates_g=2.0, total_fat_g=3.0).as_macros() == mc.Macros(
        kcal=100.0, protein_g=1.0, carb_g=2.0, fat_g=3.0
    )
    assert mm.Nutrients(energy_kcal=100.0, protein_g=1.0, carbohydrates_g=2.0).as_macros() is None

"""Motor das fontes externas do catálogo (`app/domain/nutrition/external_sources.py`,
L3b) contra os payloads REAIS do USDA FoodData Central e do Open Food Facts
(`tests/support/nutrition_external_payloads.py`): ids/chaves de nutriente,
unidades, escolha da energia, ausente = None (nunca 0), validação do catálogo
(erro recusa, Atwater vira aviso), citação/licença por fonte, GTIN e limite.

Mapa: `docs/motores/FONTES_DA_VERDADE.md` §3.7 e §49."""

from __future__ import annotations

import pytest

from app.domain.nutrition import catalog, labels, meal_math
from app.domain.nutrition import external_sources as ext
from app.domain.nutrition.catalog import FoodSource
from app.domain.nutrition.external_sources import EnergyOrigin
from app.domain.nutrition.meal_math import Unit
from tests.support import nutrition_external_payloads as payloads


def _sem_nutriente(food: dict, ident: int) -> dict:
    food["foodNutrients"] = [n for n in food["foodNutrients"] if (n.get("nutrient") or {}).get("id") != ident]
    return food


def _nutriente(food: dict, ident: int) -> dict:
    return next(n for n in food["foodNutrients"] if (n.get("nutrient") or {}).get("id") == ident)


def _off_nutrientes(produto: dict) -> dict:
    return produto["nutrition"]["aggregated_set"]["nutrients"]


# ---------------------------------------------------------------------------
# GTIN / EAN
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bruto", "canonico"),
    [
        ("7891000100103", "7891000100103"),  # EAN-13 (Leite Moça)
        ("789 1000-100103", "7891000100103"),  # espaços e hífens são removidos
        ("012345678905", "0012345678905"),  # UPC-A -> EAN-13 com 0 à esquerda
        ("00012345678905", "0012345678905"),  # GTIN-14 com 0 à esquerda -> 13
        ("10012345678902", "10012345678902"),  # GTIN-14 de verdade fica
        ("96385074", "96385074"),  # EAN-8
        ("7891000100104", None),  # dígito verificador errado
        ("789100010010", None),  # 12 dígitos com verificador errado
        ("78910001001", None),  # 11 dígitos
        ("78910001001O3", None),  # letra
        ("", None),
        (None, None),
    ],
)
def test_normalize_gtin(bruto, canonico):
    assert ext.normalize_gtin(bruto) == canonico


def test_dados_reais_tem_gtin_valido():
    assert ext.gtin_check_digit_ok(payloads.OFF_MOCA_CODE)
    assert ext.gtin_check_digit_ok(payloads.OFF_NOT_FOUND_CODE)


def test_busca_do_usda_corpo_e_resultado_do_motor():
    corpo = ext.usda_search_request("rice")
    assert corpo == {"query": "rice", "dataType": ["Foundation", "SR Legacy"], "pageSize": ext.USDA_SEARCH_PAGE_SIZE,
                     "pageNumber": 1}
    resultado = ext.usda_search_result(payloads.load(payloads.USDA_SEARCH))
    assert resultado["total_hits"] == 141 and len(resultado["items"]) == 6
    assert ext.usda_search_result({"totalHits": "x"}) == {"total_hits": None, "items": []}


def test_limite_diario_do_off():
    limite = ext.OFF_LOOKUPS_PER_USER_DAY
    assert limite == 30  # plano §8.1
    assert ext.OFF_LOOKUP_COUNTED_SOURCE is FoodSource.OFF
    assert ext.off_lookup_allowed(0) and ext.off_lookup_allowed(limite - 1)
    assert not ext.off_lookup_allowed(limite) and not ext.off_lookup_allowed(limite + 5)


# ---------------------------------------------------------------------------
# USDA FoodData Central (payloads reais)
# ---------------------------------------------------------------------------

FOUNDATION_ESPERADO = {
    "energy_kcal": 365.25, "carbohydrates_g": 75.5, "total_sugars_g": None, "added_sugars_g": None,
    "protein_g": 7.19, "total_fat_g": 3.85, "saturated_fat_g": None, "trans_fat_g": None, "fiber_g": None,
    "sodium_mg": 1.0,
}
SR_LEGACY_ESPERADO = {
    "energy_kcal": 416.0, "carbohydrates_g": 82.64, "total_sugars_g": 0.0, "added_sugars_g": None,
    "protein_g": 10.0, "total_fat_g": 5.0, "saturated_fat_g": 0.0, "trans_fat_g": 0.002, "fiber_g": 0.0,
    "sodium_mg": 233.0,
}


@pytest.mark.parametrize(
    ("arquivo", "fdc_id", "tipo", "versao", "categoria", "esperado"),
    [
        (payloads.USDA_FOUNDATION, "1104812", "Foundation", "foundation-2020-10-30", "Cereal Grains and Pasta",
         FOUNDATION_ESPERADO),
        (payloads.USDA_SR_LEGACY, "173161", "SR Legacy", "sr_legacy-2019-04-01", "Snacks", SR_LEGACY_ESPERADO),
    ],
    ids=["foundation", "sr_legacy"],
)
def test_usda_detalhe_real_mapeia_para_o_catalogo(arquivo, fdc_id, tipo, versao, categoria, esperado):
    alimento = ext.map_usda_food(payloads.load(arquivo))
    assert alimento.ok and alimento.validation.warnings == ()
    assert alimento.source is FoodSource.USDA and alimento.source_id == fdc_id and alimento.barcode is None
    assert alimento.source_version == versao and alimento.category == categoria
    assert alimento.basis is Unit.GRAM and alimento.energy_origin is EnergyOrigin.SOURCE_KCAL
    assert {campo: alimento.values[campo] for campo in catalog.PER_100_FIELDS} == esperado
    # Ausente vira None, nunca 0 (Foundation sem fibra/açúcar/saturada); presente 0 continua 0 (SR Legacy).
    assert alimento.citation == f"{catalog.USDA_CITATION} {tipo}, FDC ID {fdc_id}."
    assert alimento.license == catalog.USDA_LICENSE
    assert alimento.name_normalized  # normalizado pelo motor do texto


def test_usda_energia_cai_para_kj_quando_falta_a_kcal():
    food = _sem_nutriente(payloads.load(payloads.USDA_FOUNDATION), 1008)
    alimento = ext.map_usda_food(food)
    assert alimento.energy_origin is EnergyOrigin.FROM_KJ
    assert alimento.values["energy_kcal"] == pytest.approx(meal_math.kj_to_kcal(1582.206))


def test_usda_sem_nenhuma_energia_e_recusado():
    food = _sem_nutriente(_sem_nutriente(payloads.load(payloads.USDA_FOUNDATION), 1008), 1062)
    alimento = ext.map_usda_food(food)
    assert alimento.values["energy_kcal"] is None and alimento.energy_origin is None
    assert not alimento.ok and f"{catalog.ERR_MISSING_FIELD}:energy_kcal" in alimento.errors


def test_usda_unidade_inesperada_e_ignorada():
    food = payloads.load(payloads.USDA_SR_LEGACY)
    _nutriente(food, 1093)["nutrient"]["unitName"] = "g"  # sódio deveria vir em mg
    _nutriente(food, 1003)["nutrient"]["unitName"] = "mg"  # proteína deveria vir em g
    alimento = ext.map_usda_food(food)
    assert alimento.values["sodium_mg"] is None and alimento.values["protein_g"] is None


def test_usda_tipo_de_dado_fora_de_foundation_e_sr_legacy_e_recusado():
    food = payloads.load(payloads.USDA_SR_LEGACY)
    food["dataType"] = "Branded"
    alimento = ext.map_usda_food(food)
    assert not alimento.ok and ext.ERR_USDA_DATA_TYPE in alimento.errors
    assert ext.usda_data_type("Branded") is None and ext.usda_data_type("SR Legacy") is ext.UsdaDataType.SR_LEGACY


def test_usda_id_do_pedido_quando_o_payload_nao_traz():
    food = payloads.load(payloads.USDA_SR_LEGACY)
    del food["fdcId"]
    assert ext.map_usda_food(food, 173161).source_id == "173161"


def test_usda_busca_real_resume_os_itens_aceitos():
    busca = payloads.load(payloads.USDA_SEARCH)
    busca["foods"].append({**busca["foods"][0], "fdcId": 1, "dataType": "Branded"})
    itens = ext.usda_search_items(busca)
    assert [i["fdc_id"] for i in itens] == [173161, 167967, 1104812, 2710825, 171942, 171013]
    assert {i["data_type"] for i in itens} == {"Foundation", "SR Legacy"}
    por_id = {i["fdc_id"]: i for i in itens}
    preto = por_id[2710825]  # Foundation novo: sem 1008, com 2047 (370) e 2048 (361)
    assert preto["per_100"]["energy_kcal"] == 361 and preto["energy_origin"] == EnergyOrigin.ATWATER_SPECIFIC.value
    assert preto["name"] == "Rice, black, unenriched, raw" and preto["category"] == "Cereal Grains and Pasta"
    assert por_id[173161]["per_100"]["sodium_mg"] == 233 and por_id[173161]["per_100"]["added_sugars_g"] is None
    assert por_id[171013]["per_100"]["protein_g"] == 0  # óleo: 0 declarado continua 0
    assert all(set(i["per_100"]) == set(catalog.PER_100_FIELDS) for i in itens)


# ---------------------------------------------------------------------------
# Open Food Facts (payload real da API v3.6)
# ---------------------------------------------------------------------------


def test_off_produto_real_mapeia_para_o_catalogo():
    alimento = ext.map_off_product(payloads.load(payloads.OFF_MOCA), payloads.OFF_MOCA_CODE)
    assert alimento.ok and alimento.validation.warnings == ()  # 4·7 + 4·55 + 9·8 = 320 ≈ 325 kcal
    assert alimento.source is FoodSource.OFF and alimento.source_id == alimento.barcode == payloads.OFF_MOCA_CODE
    assert alimento.name == "Leite Condensado Integral moça" and alimento.brand == "Nestlé, Moça"
    assert alimento.basis is Unit.GRAM and alimento.household_measure == "20 g"
    assert alimento.source_version == "2026-03-03"  # last_modified_t, UTC
    assert alimento.energy_origin is EnergyOrigin.SOURCE_KCAL
    assert {campo: alimento.values[campo] for campo in catalog.PER_100_FIELDS} == {
        "energy_kcal": 325.0, "carbohydrates_g": 55.0, "total_sugars_g": 55.0,
        "added_sugars_g": None,  # só a estimativa do OFF a partir dos ingredientes: ignorada
        "protein_g": 7.0, "total_fat_g": 8.0, "saturated_fat_g": 5.0, "trans_fat_g": None, "fiber_g": 0.0,
        "sodium_mg": 0.0,
    }
    assert alimento.values["serving_size"] == 20.0
    assert alimento.citation.startswith(catalog.OFF_CITATION)
    assert alimento.citation.endswith(f"https://world.openfoodfacts.org/product/{payloads.OFF_MOCA_CODE}")
    assert alimento.license == catalog.OFF_LICENSE
    assert len(alimento.citation) <= 500  # VARCHAR(500) da 080


def test_off_sodio_em_g_vira_mg():
    produto = payloads.load(payloads.OFF_MOCA)
    _off_nutrientes(produto)["sodium"]["value"] = 0.11
    alimento = ext.map_off_product(produto, payloads.OFF_MOCA_CODE)
    assert alimento.values["sodium_mg"] == pytest.approx(0.11 * catalog.MG_PER_G)


@pytest.mark.parametrize(
    ("remover", "origem", "kj"),
    [
        (("energy-kcal",), EnergyOrigin.FROM_KJ, 1365.0),
        (("energy-kcal", "energy-kj"), EnergyOrigin.FROM_KJ, 1365.0),
    ],
    ids=["energy-kj", "energy"],
)
def test_off_energia_declarada_antes_da_conversao(remover, origem, kj):
    produto = payloads.load(payloads.OFF_MOCA)
    for chave in remover:
        del _off_nutrientes(produto)[chave]
    alimento = ext.map_off_product(produto, payloads.OFF_MOCA_CODE)
    assert alimento.energy_origin is origem
    assert alimento.values["energy_kcal"] == pytest.approx(meal_math.kj_to_kcal(kj))


def test_off_value_computed_nunca_e_usado():
    produto = payloads.load(payloads.OFF_MOCA)
    nutrientes = _off_nutrientes(produto)
    for chave in ("energy-kcal", "energy-kj", "energy"):
        nutrientes[chave] = {"source": "packaging", "unit": nutrientes[chave]["unit"], "value_computed": 270}
    alimento = ext.map_off_product(produto, payloads.OFF_MOCA_CODE)
    assert alimento.values["energy_kcal"] is None and alimento.energy_origin is None
    assert not alimento.ok and f"{catalog.ERR_MISSING_FIELD}:energy_kcal" in alimento.errors


def test_off_por_100ml_vira_base_ml():
    produto = payloads.load(payloads.OFF_MOCA)
    produto["nutrition"]["aggregated_set"]["per"] = "100ml"
    alimento = ext.map_off_product(produto, payloads.OFF_MOCA_CODE)
    assert alimento.basis is Unit.MILLILITER and alimento.values["energy_kcal"] == 325.0


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("per", "serving"), ("preparation", "prepared")],
)
def test_off_conjunto_por_porcao_ou_preparado_nao_serve(campo, valor):
    produto = payloads.load(payloads.OFF_MOCA)
    produto["nutrition"]["aggregated_set"][campo] = valor
    alimento = ext.map_off_product(produto, payloads.OFF_MOCA_CODE)
    assert all(alimento.values[c] is None for c in catalog.PER_100_FIELDS)
    assert not alimento.ok


def test_off_sem_tabela_nutricional_e_recusado():
    produto = payloads.load(payloads.OFF_MOCA)
    del produto["nutrition"]
    alimento = ext.map_off_product(produto, payloads.OFF_MOCA_CODE)
    assert alimento.errors == (f"{catalog.ERR_MISSING_FIELD}:energy_kcal",)


def test_off_unidade_inesperada_e_ignorada():
    produto = payloads.load(payloads.OFF_MOCA)
    _off_nutrientes(produto)["proteins"]["unit"] = "mg"
    assert ext.map_off_product(produto, payloads.OFF_MOCA_CODE).values["protein_g"] is None


def test_off_valor_impossivel_recusa_e_atwater_vira_aviso():
    produto = payloads.load(payloads.OFF_MOCA)
    _off_nutrientes(produto)["proteins"]["value"] = 150
    acima = ext.map_off_product(produto, payloads.OFF_MOCA_CODE)
    assert not acima.ok and f"{catalog.ERR_ABOVE_BASIS}:protein_g" in acima.errors

    produto = payloads.load(payloads.OFF_MOCA)
    _off_nutrientes(produto)["proteins"]["value"] = -1
    assert f"{catalog.ERR_INVALID_VALUE}:protein_g" in ext.map_off_product(produto, payloads.OFF_MOCA_CODE).errors

    produto = payloads.load(payloads.OFF_MOCA)
    _off_nutrientes(produto)["energy-kcal"]["value"] = 600
    aviso = ext.map_off_product(produto, payloads.OFF_MOCA_CODE)
    assert aviso.ok and catalog.WARN_ATWATER in aviso.validation.warnings


def test_off_nome_em_portugues_primeiro_e_fallback():
    produto = payloads.load(payloads.OFF_MOCA)
    produto["product_name"] = "Sweetened condensed milk"
    assert ext.map_off_product(produto, payloads.OFF_MOCA_CODE).name == "Leite Condensado Integral moça"
    for campo in ext.OFF_NAME_FIELDS:
        produto.pop(campo, None)
    sem_nome = ext.map_off_product(produto, payloads.OFF_MOCA_CODE)
    assert sem_nome.name == ext.OFF_UNNAMED_TEMPLATE.format(code=payloads.OFF_MOCA_CODE) and sem_nome.ok


def test_off_porcao_fora_da_regra_e_ignorada():
    produto = payloads.load(payloads.OFF_MOCA)
    produto["serving_quantity"] = catalog.PORTION_AMOUNT_MAX * 2
    alimento = ext.map_off_product(produto, payloads.OFF_MOCA_CODE)
    assert alimento.ok and alimento.values["serving_size"] is None


def test_off_campos_pedidos_cobrem_o_que_o_mapeamento_le():
    assert "nutrition" in ext.OFF_PRODUCT_FIELDS and "nutriments" not in ext.OFF_PRODUCT_FIELDS
    assert set(ext.OFF_NAME_FIELDS) <= set(ext.OFF_PRODUCT_FIELDS)


# ---------------------------------------------------------------------------
# Citação, licença, somente leitura e textos
# ---------------------------------------------------------------------------


def test_citacao_e_licenca_por_fonte_e_somente_leitura():
    for fonte in (FoodSource.TACO, FoodSource.USDA, FoodSource.OFF):
        assert catalog.citation_for(fonte) and catalog.license_for(fonte)
        assert catalog.is_read_only(fonte)
    assert "ODbL" in catalog.OFF_CITATION and "ODbL" in catalog.OFF_LICENSE and "CC0" in catalog.USDA_LICENSE
    assert not catalog.is_read_only(FoodSource.USER)


def test_textos_dos_codigos_com_numero_do_dono():
    assert str(ext.OFF_LOOKUPS_PER_USER_DAY) in labels.limitation_label(ext.ERR_OFF_DAILY_LIMIT)
    assert all(str(n) in labels.limitation_label(ext.ERR_INVALID_BARCODE) for n in ext.GTIN_LENGTHS)
    texto_tipo = labels.limitation_label(ext.ERR_USDA_DATA_TYPE)
    assert all(labels.USDA_DATA_TYPE_LABELS[t] in texto_tipo for t in ext.USDA_DATA_TYPES)


def test_to_dict_serializa_codigos():
    dados = ext.map_off_product(payloads.load(payloads.OFF_MOCA), payloads.OFF_MOCA_CODE).to_dict()
    assert dados["source"] == "off" and dados["basis"] == "g" and dados["energy_origin"] == "source_kcal"
    assert dados["errors"] == [] and dados["values"]["added_sugars_g"] is None

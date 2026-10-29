"""Motor da leitura da refeição por IA (`domain/nutrition/meal_parse.py`, L5b):
texto, limpeza da saída da IA, quantidade proposta, decisão do casamento contra
os nomes REAIS da TACO (seed da 081), melhor busca com e sem o preparo, item
estimado no diário e a origem do item (`diary.item_source_errors`).

Rodar: cd api && python -m pytest tests/unit/test_nutrition_meal_parse.py
"""

from __future__ import annotations

import functools

import pytest

from app.domain.data_nature import DataNature
from app.domain.nutrition import catalog, diary, food_text, meal_math
from app.domain.nutrition import meal_parse as mp
from app.domain.nutrition.meal_math import Unit
from tests.support.meal_parse_eval import catalogo_taco


@functools.lru_cache(maxsize=None)
def _ranking(busca: str) -> tuple[food_text.Match, ...]:
    return food_text.rank_candidates(busca, [(linha["id"], linha["name"]) for linha in catalogo_taco()])


def _estimativa(**extra) -> dict:
    return {"energy_kcal": 400.0, "protein_g": 80.0, "carbohydrates_g": 10.0, "total_fat_g": 5.0, "fiber_g": None,
            **extra}


def _item(**extra) -> dict:
    base = {"name": "arroz branco", "quantity": None, "unit": "g", "grams_estimate": 120, "preparation": "cozido",
            "confidence": 0.9, mp.ESTIMATE_KEY: None}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Vocabulário e texto
# ---------------------------------------------------------------------------


def test_vocabulario_da_ia_e_o_do_diario_menos_a_porcao():
    assert mp.AI_UNITS == tuple(u for u in Unit if u is not Unit.PORTION)
    assert set(meal_math.GENERIC_PORTIONS) <= set(mp.AI_UNITS)
    assert mp.ESTIMATE_KEY == f"estimated_per_{meal_math.PER_100G_REFERENCE:g}g"
    assert set(mp.ESTIMATE_FIELDS) <= set(meal_math.NUTRIENT_FIELDS)
    assert meal_math.SODIUM_FIELD not in mp.ESTIMATE_FIELDS  # o plano não pede sódio: fica desconhecido
    assert set(mp.ESTIMATE_REQUIRED_FIELDS) == set(catalog.REQUIRED_PER_100_FIELDS) <= set(mp.ESTIMATE_FIELDS)
    assert mp.ESTIMATE_SOURCE is catalog.FoodSource.AI_ESTIMATE


@pytest.mark.parametrize(
    ("texto", "esperado", "erros"),
    [
        ("  arroz,   feijão\n e bife ", "arroz, feijão e bife", ()),
        ("", "", (mp.ERR_TEXT_EMPTY,)),
        ("   \n ", "", (mp.ERR_TEXT_EMPTY,)),
        (None, "", (mp.ERR_TEXT_EMPTY,)),
    ],
)
def test_texto_da_refeicao(texto, esperado, erros):
    assert mp.validate_text(texto) == (esperado, erros)


def test_texto_acima_do_teto():
    assert mp.validate_text("a" * mp.TEXT_MAX_CHARS)[1] == ()
    assert mp.validate_text("a" * (mp.TEXT_MAX_CHARS + 1))[1] == (mp.ERR_TEXT_TOO_LONG,)


def test_origem_da_refeicao():
    assert mp.meal_source("job-1") is diary.MealSource.AI_TEXT
    assert mp.meal_source(None) is diary.MealSource.MANUAL and mp.meal_source("  ") is diary.MealSource.MANUAL


@pytest.mark.parametrize(("bruto", "esperado"), [(0.7, 0.7), (-1, 0.0), (3, 1.0), ("x", None), (True, None),
                                                  (float("nan"), None), (None, None)])
def test_confianca_na_faixa(bruto, esperado):
    assert mp.normalize_confidence(bruto) == esperado


# ---------------------------------------------------------------------------
# Limpeza da saída da IA
# ---------------------------------------------------------------------------


def test_item_limpo_com_estimativa_valida():
    item = mp.normalize_ai_item(_item(name="  whey   protein ", unit="g", quantity=30, grams_estimate=30,
                                      preparation=None, **{mp.ESTIMATE_KEY: _estimativa()}))
    assert item.name == "whey protein" and item.unit is Unit.GRAM and item.quantity == 30.0
    assert item.estimate.energy_kcal == 400.0 and item.estimate.sodium_mg is None
    assert item.limitations == ()


def test_unidade_fora_do_vocabulario_some_e_a_estimativa_invalida_e_descartada():
    item = mp.normalize_ai_item(_item(unit="portion", **{mp.ESTIMATE_KEY: _estimativa(energy_kcal=None)}))
    assert item.unit is None and item.estimate is None
    assert item.limitations == (mp.LIMIT_ESTIMATE_INVALID,)
    acima = mp.normalize_ai_item(_item(**{mp.ESTIMATE_KEY: _estimativa(protein_g=500)}))
    assert acima.estimate is None and acima.limitations == (mp.LIMIT_ESTIMATE_INVALID,)


def test_aviso_de_atwater_da_estimativa_vira_limitacao():
    item = mp.normalize_ai_item(_item(**{mp.ESTIMATE_KEY: _estimativa(energy_kcal=900)}))
    assert item.estimate is not None and catalog.WARN_ATWATER in item.limitations


@pytest.mark.parametrize("bruto", [None, "x", {"name": ""}, {"name": "   "}, {"quantity": 1}])
def test_item_sem_nome_e_ignorado(bruto):
    assert mp.normalize_ai_item(bruto) is None


def test_numeros_invalidos_viram_desconhecidos():
    item = mp.normalize_ai_item(_item(quantity=-2, grams_estimate="abc", confidence="x"))
    assert item.quantity is None and item.grams_estimate is None and item.confidence is None


def test_lista_vazia_e_corte_no_teto_de_itens():
    assert mp.normalize_ai_items([]) == ((), (mp.LIMIT_NO_ITEMS,))
    assert mp.normalize_ai_items(None) == ((), (mp.LIMIT_NO_ITEMS,))
    itens, limitacoes = mp.normalize_ai_items([_item(name=f"item {n}") for n in range(diary.MEAL_MAX_ITEMS + 2)])
    assert len(itens) == diary.MEAL_MAX_ITEMS and limitacoes == (mp.LIMIT_ITEMS_TRUNCATED,)


# ---------------------------------------------------------------------------
# Quantidade proposta
# ---------------------------------------------------------------------------


def test_gramas_ditos_em_gramas_sem_aviso():
    item = mp.normalize_ai_item(_item(quantity=150, unit="g", grams_estimate=150))
    assert mp.proposed_quantity(item) == (150.0, Unit.GRAM, ())


@pytest.mark.parametrize(("quantidade", "unidade"), [(1, "xicara"), (2, "unit"), (300, "ml"), (None, "g"), (4, "colher_sopa")])
def test_medida_que_nao_e_grama_usa_os_gramas_da_ia(quantidade, unidade):
    """Volume vira massa pela densidade (que a TACO não tem): uma xícara de
    alface não pesa 240 g. Os gramas da IA vencem, com aviso."""
    item = mp.normalize_ai_item(_item(quantity=quantidade, unit=unidade, grams_estimate=35))
    assert mp.proposed_quantity(item) == (35.0, Unit.GRAM, (mp.LIMIT_QUANTITY_FROM_GRAMS,))


def test_sem_gramas_usa_a_medida_da_ia_se_converte_senao_fica_sem_quantidade():
    colher = mp.normalize_ai_item(_item(quantity=2, unit="colher_sopa", grams_estimate=None))
    assert mp.proposed_quantity(colher) == (2.0, Unit.TABLESPOON, ())
    unidade = mp.normalize_ai_item(_item(quantity=2, unit="unit", grams_estimate=None))
    assert mp.proposed_quantity(unidade) == (None, Unit.GRAM, (mp.LIMIT_QUANTITY_MISSING,))


# ---------------------------------------------------------------------------
# Casamento com a TACO real
# ---------------------------------------------------------------------------


def test_preselecao_com_aceite_automatico_do_food_text():
    item = mp.normalize_ai_item(_item())
    decidido = mp.classify_item(0, item, _ranking("arroz branco cozido"))
    assert decidido.status is mp.MatchStatus.SELECTED and decidido.food_id == 3  # Arroz, tipo 1, cozido
    assert decidido.match_confidence == decidido.candidates[0].score
    assert not decidido.candidate_for_catalog and len(decidido.candidates) <= mp.CANDIDATES_PER_ITEM


def test_candidatos_sem_aceite_a_pessoa_escolhe():
    item = mp.normalize_ai_item(_item(name="peito de frango", preparation="grelhado"))
    ranking = _ranking("peito de frango grelhado")
    assert ranking and not ranking[0].auto_accept
    decidido = mp.classify_item(1, item, ranking)
    assert decidido.status is mp.MatchStatus.CHOICE and decidido.food_id is None
    assert 410 in {m.candidate_id for m in decidido.candidates} and decidido.match_confidence is None


def test_sem_candidato_vale_a_estimativa_e_vira_candidato_a_cadastro():
    item = mp.normalize_ai_item(_item(name="whey protein", preparation=None, **{mp.ESTIMATE_KEY: _estimativa()}))
    assert _ranking("whey protein") == ()
    decidido = mp.classify_item(0, item, ())
    assert decidido.status is mp.MatchStatus.ESTIMATED and decidido.candidate_for_catalog
    assert mp.LIMIT_AI_ESTIMATE in decidido.limitations
    corpo = decidido.to_dict()
    assert corpo["estimate"] == {"name": "whey protein", **{c: _estimativa()[c] for c in mp.ESTIMATE_FIELDS}}


def test_sem_candidato_e_sem_estimativa_fica_nao_encontrado():
    decidido = mp.classify_item(0, mp.normalize_ai_item(_item(name="xyz")), ())
    assert decidido.status is mp.MatchStatus.UNMATCHED and decidido.candidate_for_catalog
    assert decidido.to_dict()["estimate"] is None


def test_candidatos_cortados_no_numero_do_plano():
    muitos = tuple(food_text.Match(candidate_id=n, name=f"x{n}", score=0.7, auto_accept=False) for n in range(9))
    decidido = mp.classify_item(0, mp.normalize_ai_item(_item()), muitos)
    assert len(decidido.candidates) == mp.CANDIDATES_PER_ITEM


def test_buscas_com_e_sem_o_preparo_e_a_melhor_vence():
    item = mp.normalize_ai_item(_item(name="feijoada", preparation="cozida"))
    assert item.match_queries == ("feijoada cozida", "feijoada")
    com, sem = (_ranking(busca) for busca in item.match_queries)
    assert com == () and sem[0].candidate_id == 540 and sem[0].auto_accept  # "Feijoada"
    assert mp.best_ranking([com, sem]) == sem
    # Preparo já no nome: uma busca só.
    assert mp.normalize_ai_item(_item(name="couve refogada", preparation="refogada")).match_queries == ("couve refogada",)
    # Empate: a primeira (com o preparo).
    um = (food_text.Match(1, "a", 1.0, True),)
    outro = (food_text.Match(2, "b", 1.0, True),)
    assert mp.best_ranking([um, outro]) == um and mp.best_ranking([(), ()]) == ()


def test_resultado_junta_itens_e_limitacoes():
    itens, limitacoes = mp.normalize_ai_items([_item(), _item(name="whey protein", preparation=None,
                                                             **{mp.ESTIMATE_KEY: _estimativa()})])
    resultado = mp.build_result(itens, [_ranking("arroz branco cozido"), ()], limitacoes, "  quantidade  estimada ")
    corpo = resultado.to_dict()
    assert [i["status"] for i in corpo["items"]] == ["selected", "estimated"]
    assert corpo["notes"] == "quantidade estimada" and corpo["limitations"] == []
    with pytest.raises(ValueError):
        mp.build_result(itens, [()], limitacoes)


# ---------------------------------------------------------------------------
# Item estimado no diário
# ---------------------------------------------------------------------------


def test_item_estimado_pela_conta_do_diario_com_natureza_estimated():
    validada = mp.validate_estimate(_estimativa())
    resolucao = meal_math.resolve_amount(30, "g", mp.ESTIMATE_BASIS.value)
    nutricao = mp.estimated_item_nutrition(validada.nutrients, resolucao)
    referencia = meal_math.item_nutrition(validada.nutrients, resolucao)
    assert nutricao.nature is meal_math.MealNature.ESTIMATED and nutricao.nature.value == DataNature.ESTIMATED.value
    assert nutricao.nutrients == referencia.nutrients  # a MESMA conta do diário
    assert nutricao.limitations[-1] == mp.LIMIT_AI_ESTIMATE and "nutrient_unknown:sodium_mg" in nutricao.limitations
    assert mp.estimated_item_nutrition(validada.nutrients, meal_math.resolve_amount(None, "g", "g")) is None


def test_snapshot_do_item_estimado():
    validada = mp.validate_estimate(_estimativa())
    snapshot = mp.estimate_snapshot("  Whey  ", validada.nutrients)
    assert snapshot["portion"] is None and snapshot["food"]["id"] is None
    assert snapshot["food"]["source"] == "ai_estimate" and snapshot["food"]["basis"] == "g"
    assert snapshot["food"]["name"] == "Whey" and set(snapshot["food"]["per_100"]) == set(mp.ESTIMATE_FIELDS)
    assert mp.snapshot_is_estimate(snapshot["food"]) and not mp.snapshot_is_estimate({"source": "taco"})
    assert not mp.snapshot_is_estimate(None)


def test_estimativa_invalida_devolve_os_codigos_do_catalogo():
    assert mp.validate_estimate(None).errors == (mp.LIMIT_ESTIMATE_INVALID,)
    erros = mp.validate_estimate(_estimativa(energy_kcal=None)).errors
    assert f"{catalog.ERR_MISSING_FIELD}:energy_kcal" in erros


def test_modelo_estrito_da_estimativa_tem_os_campos_do_dono():
    """`MealItemEstimateIn` (`extra=forbid`) repete os campos de `ESTIMATE_FIELDS`
    e o tamanho de `ITEM_LABEL_MAX_CHARS`: se o dono ganhar um campo, a tela
    devolveria uma estimativa que a API recusa (422)."""
    from app.request_models import MealItemEstimateIn

    assert set(MealItemEstimateIn.model_fields) == {"name", *mp.ESTIMATE_FIELDS}
    restricoes = MealItemEstimateIn.model_fields["name"].metadata
    assert any(getattr(r, "max_length", None) == mp.ITEM_LABEL_MAX_CHARS for r in restricoes)


@pytest.mark.parametrize(("alimento", "estimativa", "erros"), [
    (True, False, ()), (False, True, ()), (True, True, (diary.ERR_ITEM_FOOD_OR_ESTIMATE,)),
    (False, False, (diary.ERR_ITEM_FOOD_OR_ESTIMATE,)),
])
def test_origem_do_item_e_exatamente_uma(alimento, estimativa, erros):
    assert diary.item_source_errors(alimento, estimativa) == erros

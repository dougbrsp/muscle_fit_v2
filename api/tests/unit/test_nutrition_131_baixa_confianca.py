"""Issue #131: item lido com confiança da IA abaixo do limiar vira "estimativa a conferir"."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import nutrition_meal_parse_worker as worker
from app import nutrition_meal_photo_ai as ai
from app.domain.nutrition import meal_parse, meal_photo

LIMIAR = meal_parse.LOW_CONFIDENCE_BELOW


@pytest.mark.parametrize(
    "confianca, esperado",
    [(LIMIAR - 0.01, True), (0.0, True), (LIMIAR, False), (LIMIAR + 0.01, False), (1.0, False), (None, False)],
)
def test_baixa_confianca_e_estritamente_abaixo_do_limiar(confianca, esperado):
    assert meal_parse.is_low_confidence(confianca) is esperado


def _resultado(confianca):
    itens, _ = meal_parse.normalize_ai_items([{"name": "arroz", "grams_estimate": 100, "confidence": confianca}])
    return meal_parse.build_result(itens, [()])


@pytest.mark.parametrize("confianca, esperado", [(LIMIAR - 0.1, True), (LIMIAR, False), (None, False)])
def test_chave_no_item_publico_da_leitura_por_texto(confianca, esperado):
    [item] = worker.result_public(_resultado(confianca), {})["items"]
    assert item["low_confidence"] is esperado


def test_chave_no_item_publico_da_leitura_por_foto():
    leitura = SimpleNamespace(model="m", photos_read=1)  # photos_read: forma da leitura a partir do PR #335 (#325); ignorado antes dele
    corpo = ai.job_result(_resultado(LIMIAR - 0.1), {}, leitura, meal_photo.PlateStatus.RECOGNIZED, {}, 1)
    assert corpo["items"][0]["low_confidence"] is True


def test_usa_a_confianca_da_ia_e_nao_a_nota_do_casamento():
    item = _resultado(1.0).to_dict()["items"][0]
    assert item["match_confidence"] is None and item["low_confidence"] is False

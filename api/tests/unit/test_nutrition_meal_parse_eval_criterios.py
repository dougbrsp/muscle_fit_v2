"""Critérios do eval pago da leitura da refeição (`criterios_llm.avaliar_parse_refeicao`,
L5b) provados SEM LLM: o caminho inteiro do eval (caso congelado -> executor ->
worker real -> motor -> TACO em memória -> critério) com a OpenAI trocada por
respostas escritas à mão — uma que cumpre o pedido passa, cada violação é pega."""

from __future__ import annotations

import json

import pytest

from app.domain.nutrition import meal_parse as mp
from tests.eval import criterios_llm
from tests.support import llm_casos


class _Resposta:
    def __init__(self, data):
        self._data = data

    def read(self):
        return json.dumps(self._data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _item(nome, gramas, preparo=None, estimativa=None, unidade="g", quantidade=None):
    return {"name": nome, "quantity": quantidade, "unit": unidade, "grams_estimate": gramas, "preparation": preparo,
            "confidence": 0.9, mp.ESTIMATE_KEY: estimativa}


WHEY = {"energy_kcal": 400, "protein_g": 80, "carbohydrates_g": 10, "total_fat_g": 5, "fiber_g": None}
BOA = {"items": [_item("whey protein", 30, estimativa=WHEY, quantidade=30),
                 _item("leite desnatado", 300, unidade="ml", quantidade=300),
                 _item("banana prata", 90, preparo="crua", unidade="unit", quantidade=1)], "notes": None}


def _rodar(monkeypatch, caso_id: str, saida: dict):
    from app import openai_client

    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(
        openai_client.urllib.request, "urlopen",
        lambda request, timeout=None: _Resposta({"output_text": json.dumps(saida), "model": "m",
                                                 "usage": {"input_tokens": 1, "output_tokens": 1}}),
    )
    caso = next(c for c in llm_casos.carregar("meal_parse") if c["id"] == caso_id)
    resultado = llm_casos.executar("meal_parse", caso, monkeypatch, rag="fixo")
    return resultado, caso


def test_resposta_que_cumpre_o_pedido_passa(monkeypatch):
    saida, caso = _rodar(monkeypatch, "pos_treino_whey", BOA)
    assert criterios_llm.avaliar_parse_refeicao(saida, caso) == []
    resumo = criterios_llm.resumo_parse_refeicao([(saida, caso)])
    assert resumo["casos_sem_problema"] == 1 and resumo["itens_com_proposta_aceita"] == 3
    assert resumo["pre_selecoes_erradas"] == 0 and saida["uso"]["tokens_out"] == 1


@pytest.mark.parametrize(
    ("caso_id", "saida", "trecho"),
    [
        ("pos_treino_whey", {**BOA, "items": BOA["items"][:2]}, "banana: ausente na leitura"),
        ("pos_treino_whey", {**BOA, "items": [*BOA["items"], _item("açúcar", 10)]}, "item a mais: 'açúcar'"),
        ("pos_treino_whey", {**BOA, "items": [_item("whey protein", 30, estimativa=None), *BOA["items"][1:]]},
         "whey: unmatched"),
        ("pos_treino_whey", {**BOA, "items": [BOA["items"][0], BOA["items"][1], _item("banana prata", 900)]},
         "banana: 900 g fora de"),
        ("sem_comida", {"items": [_item("água", 300)], "notes": None}, "item a mais"),
        # Pré-seleção errada: "arroz integral cozido" é aceite automático do 1, mas o caso pede arroz branco.
        ("almoco_prato_feito", {"items": [_item("arroz integral", 150, preparo="cozido")], "notes": None},
         "arroz: pré-seleção errada"),
    ],
)
def test_resposta_que_viola_o_pedido_e_pega(monkeypatch, caso_id, saida, trecho):
    resultado, caso = _rodar(monkeypatch, caso_id, saida)
    problemas = criterios_llm.avaliar_parse_refeicao(resultado, caso)
    assert any(trecho in p for p in problemas), problemas


def test_texto_da_saida_para_o_relatorio(monkeypatch):
    saida, _ = _rodar(monkeypatch, "pos_treino_whey", BOA)
    texto = criterios_llm.texto_da_saida("meal_parse", saida)
    linhas = json.loads(texto)
    assert [i["name"] for i in linhas] == ["whey protein", "leite desnatado", "banana prata"]
    assert linhas[0]["status"] == "estimated" and linhas[0]["estimate"]["energy_kcal"] == 400

"""Cada feature real ligada na Anthropic (rede falsa): o MESMO prompt que iria
para a OpenAI vai para o Claude, o schema real passa pelo adaptador e a
resposta volta pelo caminho de sempre da feature. Nas features com
`prompt_cache_key`, o prefixo estavel (instrucoes + RAG) vai num bloco com
`cache_control`. Nenhuma chamada paga."""

from __future__ import annotations

import pytest

from app import llm_anthropic, openai_client
from tests.support import llm_casos
from tests.unit.test_llm_prompt_renderizado_snapshot import capturar

# Primeiro caso de cada feature (o snapshot completo ja roda todos na OpenAI).
PRIMEIRO_POR_FEATURE = {}
for _feature, _caso in llm_casos.todos_os_casos():
    PRIMEIRO_POR_FEATURE.setdefault(_feature, _caso)


class _Cliente:
    def __init__(self, texto):
        self.texto = texto
        self.pedidos = []
        self.messages = self

    def create(self, **kwargs):
        self.pedidos.append(kwargs)
        return {
            "model": kwargs["model"],
            "stop_reason": "end_turn",
            "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": self.texto}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }


@pytest.mark.parametrize("feature", sorted(PRIMEIRO_POR_FEATURE))
def test_feature_na_anthropic_recebe_o_mesmo_prompt(monkeypatch, feature):
    caso = PRIMEIRO_POR_FEATURE[feature]
    corpo_openai = capturar(monkeypatch, feature, caso)
    prompt_openai = corpo_openai["input"]
    assert isinstance(prompt_openai, str)

    monkeypatch.setenv(llm_anthropic.feature_env(llm_anthropic.ENV_PROVIDER_PREFIX, feature), "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "chave-de-teste")
    cliente = _Cliente(llm_casos.RESPOSTA_FALSA.get(feature, llm_casos.RESPOSTA_FALSA_PADRAO))
    monkeypatch.setattr(llm_anthropic, "_client", lambda api_key: cliente)
    monkeypatch.setattr(openai_client.urllib.request, "urlopen", lambda *a, **k: pytest.fail("foi para a OpenAI"))
    llm_casos.executar(feature, caso, monkeypatch, rag="fixo")

    [pedido] = cliente.pedidos
    blocos = pedido["messages"][0]["content"]
    assert "".join(b["text"] for b in blocos) == prompt_openai
    assert pedido["model"] == llm_anthropic.DEFAULT_ANTHROPIC_MODEL[feature]
    if "text" in corpo_openai:  # estruturada na OpenAI -> estruturada no Claude
        assert pedido["output_config"]["format"]["type"] == "json_schema"
    if feature in openai_client.PROMPT_CACHE_KEY_FEATURES:
        prefixo, resto = blocos
        assert prefixo["cache_control"] == {"type": "ephemeral"} and "cache_control" not in resto
        # O RAG (marcador fixo no snapshot) esta no prefixo; os dados, nao.
        if llm_casos.RAG_FIXO in prompt_openai:
            assert llm_casos.RAG_FIXO in prefixo["text"]
    else:
        assert len(blocos) == 1 and "cache_control" not in blocos[0]

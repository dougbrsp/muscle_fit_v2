"""Fallback inverso (issue #326): primario openai, fallback anthropic (foto do prato).

Rede sempre falsa: `urlopen` da OpenAI e o cliente do SDK da Anthropic sao dubles."""

from __future__ import annotations

import io
import json
import logging
import os
import urllib.error

import pytest

from app import llm_anthropic, openai_client
from app.openai_client import USER_MESSAGE_UNAVAILABLE, OpenAIGenerationError

FEATURE = "meal_photo"


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("LLM_PROVIDER_", "ANTHROPIC_MODEL_", "ANTHROPIC_EFFORT_", "ANTHROPIC_THINKING_", "LLM_FALLBACK_PROVIDER")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "chave-openai-de-teste")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-chave-de-teste-123456")
    monkeypatch.setenv("LLM_PROVIDER_MEAL_PHOTO", "openai")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER_MEAL_PHOTO", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL_MEAL_PHOTO", "claude-haiku-5-5")
    monkeypatch.setenv("ANTHROPIC_EFFORT_MEAL_PHOTO", "medium")
    monkeypatch.setattr(openai_client, "_sleep", lambda s: None)
    llm_anthropic._client_cache.clear()


class _Resposta:
    def read(self):
        return json.dumps({"output_text": "da openai", "model": "gpt-5.4-mini"}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http(status):
    return urllib.error.HTTPError("https://api.openai.com/v1/responses", status, "erro", {}, io.BytesIO(b"{}"))


def _openai(monkeypatch, falha=None):
    chamadas = []

    def _urlopen(request, timeout=None):
        chamadas.append(timeout)
        if falha is not None:
            raise falha
        return _Resposta()

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _urlopen)
    return chamadas


class _Claude:
    def __init__(self, texto="do claude"):
        self.texto = texto
        self.pedidos = []
        self.messages = self

    def create(self, **kwargs):
        self.pedidos.append(kwargs)
        return {
            "id": "msg_teste",
            "model": "claude-haiku-5-5",
            "stop_reason": "end_turn",
            "stop_details": None,
            "content": [{"type": "text", "text": self.texto}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }


def _claude(monkeypatch, texto="do claude"):
    cliente = _Claude(texto)
    monkeypatch.setattr(llm_anthropic, "_client", lambda api_key: cliente)
    return cliente


def _eventos(caplog, nome):
    return [
        p for p in (json.loads(r.getMessage()) for r in caplog.records if r.name == "musclefit.events")
        if p.get("event") == nome
    ]


def _chama():
    return openai_client.call_openai("p", "gpt-5.4-mini", timeout=50, feature=FEATURE, prompt_version="v1")


def test_config_publicada_le_openai_com_fallback_anthropic():
    assert llm_anthropic.provider_for(FEATURE) == "openai"
    assert llm_anthropic.fallback_provider_for(FEATURE) == "anthropic"


def test_openai_ok_nao_chama_o_claude(monkeypatch):
    _openai(monkeypatch)
    monkeypatch.setattr(llm_anthropic, "_client", lambda api_key: pytest.fail("Claude nao pode ser chamado"))
    assert _chama() == "da openai"


@pytest.mark.parametrize(
    "falha",
    [_http(500), _http(429), _http(408), urllib.error.URLError("rede fora"), TimeoutError()],
    ids=["5xx", "429", "408", "rede", "timeout"],
)
def test_falha_transitoria_refaz_no_claude_e_registra_o_evento(monkeypatch, caplog, falha):
    _openai(monkeypatch, falha)
    cliente = _claude(monkeypatch)
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        assert _chama() == "do claude"
    assert cliente.pedidos[0]["model"] == "claude-haiku-5-5"
    [ev] = _eventos(caplog, "llm.fallback")
    assert (ev["from_provider"], ev["to_provider"], ev["feature"]) == ("openai", "anthropic", FEATURE)
    usos = {(ev["provider"], ev["model"]) for ev in _eventos(caplog, "llm.usage") if ev.get("status") == "ok"}
    assert usos == {("anthropic", "claude-haiku-5-5")}


@pytest.mark.parametrize("falha", [_http(400), _http(401), _http(403)], ids=["400", "401", "403"])
def test_falha_nao_elegivel_propaga_sem_claude(monkeypatch, caplog, falha):
    _openai(monkeypatch, falha)
    monkeypatch.setattr(llm_anthropic, "_client", lambda api_key: pytest.fail("Claude nao pode ser chamado"))
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        with pytest.raises(OpenAIGenerationError) as exc:
            _chama()
    assert exc.value.status == falha.code
    assert not _eventos(caplog, "llm.fallback")


def test_sem_chave_openai_nao_refaz_no_claude(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setattr(llm_anthropic, "_client", lambda api_key: pytest.fail("Claude nao pode ser chamado"))
    with pytest.raises(OpenAIGenerationError) as exc:
        _chama()
    assert "OPENAI_API_KEY" in exc.value.diagnostic


def test_fallback_desligado_propaga_o_erro_da_openai(monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK_PROVIDER_MEAL_PHOTO")
    _openai(monkeypatch, _http(500))
    monkeypatch.setattr(llm_anthropic, "_client", lambda api_key: pytest.fail("Claude nao pode ser chamado"))
    with pytest.raises(OpenAIGenerationError) as exc:
        _chama()
    assert exc.value.status == 500


def test_global_openai_nao_liga_fallback_em_feature_openai(monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK_PROVIDER_MEAL_PHOTO")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    _openai(monkeypatch, _http(500))
    monkeypatch.setattr(llm_anthropic, "_client", lambda api_key: pytest.fail("Claude nao pode ser chamado"))
    with pytest.raises(OpenAIGenerationError):
        _chama()


def test_sem_chave_anthropic_no_fallback_sobe_erro_claro(monkeypatch, caplog):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    _openai(monkeypatch, _http(503))
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        with pytest.raises(OpenAIGenerationError) as exc:
            _chama()
    assert exc.value.code == llm_anthropic.ANTHROPIC_NOT_CONFIGURED
    assert str(exc.value) == USER_MESSAGE_UNAVAILABLE and "ANTHROPIC_API_KEY" in exc.value.diagnostic
    assert len(_eventos(caplog, "llm.fallback")) == 1


def test_anthropic_com_primario_anthropic_e_invalido(monkeypatch, caplog):
    monkeypatch.setenv("LLM_PROVIDER_MEAL_PHOTO", "anthropic")
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        assert llm_anthropic.fallback_provider_for(FEATURE) is None
    [ev] = _eventos(caplog, "llm.config_invalid")
    assert ev["env"] == "LLM_FALLBACK_PROVIDER_MEAL_PHOTO"


def test_global_continua_aceitando_so_openai(monkeypatch, caplog):
    monkeypatch.delenv("LLM_FALLBACK_PROVIDER_MEAL_PHOTO")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "anthropic")
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        assert llm_anthropic.fallback_provider_for(FEATURE) is None
    assert _eventos(caplog, "llm.config_invalid")


@pytest.mark.parametrize("falha", [_http(500), urllib.error.URLError("rede fora")], ids=["5xx", "rede"])
def test_foto_do_prato_com_imagem_refaz_no_claude_com_o_bloco_de_imagem(monkeypatch, caplog, falha):
    import base64

    _openai(monkeypatch, falha)
    cliente = _claude(monkeypatch, '{"ok": true}')
    imagem = openai_client.ImageInput(data=b"JPEG-prato-123", content_type="image/jpeg", detail="low")
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        resultado = openai_client.call_openai_structured_vision(
            "p", [imagem], "gpt-5.4-mini", schema, schema_name="s", timeout=50, feature=FEATURE, prompt_version="v1"
        )
    assert resultado.data == {"ok": True}
    blocos = [b for m in cliente.pedidos[0]["messages"] for b in m["content"] if b.get("type") == "image"]
    assert blocos == [{
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(imagem.data).decode("ascii")},
    }]
    [ev] = _eventos(caplog, "llm.fallback")
    assert (ev["from_provider"], ev["to_provider"], ev["feature"]) == ("openai", "anthropic", FEATURE)

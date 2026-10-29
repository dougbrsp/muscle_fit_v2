"""`app/captcha.py` (M5): decisao de aceite do Turnstile com a camada HTTP
substituida - sucesso, rejeicao, erro HTTP, timeout/rede e JSON invalido.
Sem secret -> `CaptchaNotConfigured` (fail-closed), sem chamar a rede.
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error

import pytest

from app import captcha

SECRET_TESTE = "1x0000000000000000000000000000000AA"


@pytest.fixture
def com_secret(monkeypatch):
    monkeypatch.setenv(captcha.TURNSTILE_SECRET_ENV, SECRET_TESTE)


def _http_que_devolve(payload):
    chamadas = []

    def fake(url, fields, timeout):
        chamadas.append({"url": url, "fields": dict(fields), "timeout": timeout})
        if isinstance(payload, BaseException):
            raise payload
        return payload

    return fake, chamadas


def test_sem_secret_falha_fechado_sem_chamar_a_rede(monkeypatch):
    monkeypatch.delenv(captcha.TURNSTILE_SECRET_ENV, raising=False)
    fake, chamadas = _http_que_devolve({"success": True})
    monkeypatch.setattr(captcha, "_http_post_form", fake)
    with pytest.raises(captcha.CaptchaNotConfigured):
        captcha.verify_turnstile("tok", "1.2.3.4")
    assert chamadas == []


def test_secret_em_branco_conta_como_ausente(monkeypatch):
    monkeypatch.setenv(captcha.TURNSTILE_SECRET_ENV, "   ")
    with pytest.raises(captcha.CaptchaNotConfigured):
        captcha.verify_turnstile("tok", None)


def test_token_vazio_e_invalido_sem_chamar_a_rede(com_secret, monkeypatch):
    fake, chamadas = _http_que_devolve({"success": True})
    monkeypatch.setattr(captcha, "_http_post_form", fake)
    assert captcha.verify_turnstile(None, "1.2.3.4") is False
    assert captcha.verify_turnstile("   ", "1.2.3.4") is False
    assert chamadas == []


def test_sucesso_envia_secret_token_e_ip_para_o_siteverify(com_secret, monkeypatch):
    fake, chamadas = _http_que_devolve({"success": True})
    monkeypatch.setattr(captcha, "_http_post_form", fake)
    assert captcha.verify_turnstile("tok-abc", "10.0.0.1") is True
    (chamada,) = chamadas
    assert chamada["url"] == captcha.TURNSTILE_VERIFY_URL
    assert chamada["fields"] == {"secret": SECRET_TESTE, "response": "tok-abc", "remoteip": "10.0.0.1"}
    assert chamada["timeout"] == captcha.VERIFY_TIMEOUT_SECONDS


def test_rejeicao_da_cloudflare_e_invalido_e_loga_error_codes(com_secret, monkeypatch, caplog):
    fake, _ = _http_que_devolve({"success": False, "error-codes": ["invalid-input-response"]})
    monkeypatch.setattr(captcha, "_http_post_form", fake)
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        assert captcha.verify_turnstile("tok-ruim", None) is False
    eventos = [json.loads(r.getMessage()) for r in caplog.records if r.name == "musclefit.events"]
    (evento,) = [e for e in eventos if e["event"] == captcha.EVENT_CAPTCHA_VERIFY_FAILED]
    assert evento["reason"] == "rejected"
    assert evento["error_codes"] == ["invalid-input-response"]
    assert "tok-ruim" not in caplog.text


@pytest.mark.parametrize(
    "excecao, reason",
    [
        (urllib.error.HTTPError("u", 500, "err", None, None), "http_error"),
        (urllib.error.URLError(socket.timeout("timed out")), "URLError"),
        (TimeoutError("timed out"), "TimeoutError"),
        (ValueError("JSON invalido"), "ValueError"),
    ],
)
def test_falha_de_rede_timeout_ou_json_e_tratada_como_invalido(com_secret, monkeypatch, caplog, excecao, reason):
    fake, _ = _http_que_devolve(excecao)
    monkeypatch.setattr(captcha, "_http_post_form", fake)
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        assert captcha.verify_turnstile("tok", "1.1.1.1") is False
    assert any(f'"reason": "{reason}"' in r.getMessage() for r in caplog.records)


def test_payload_que_nao_e_objeto_e_invalido(com_secret, monkeypatch):
    fake, _ = _http_que_devolve(["nao", "e", "dict"])
    monkeypatch.setattr(captcha, "_http_post_form", fake)
    assert captcha.verify_turnstile("tok", None) is False


def test_timeout_real_do_urlopen_vira_invalido(com_secret, monkeypatch):
    """Cobre a camada HTTP de verdade (`_http_post_form`), com `urlopen`
    levantando o mesmo erro que um timeout de socket produz."""

    def urlopen_falso(request, timeout):
        assert timeout == captcha.VERIFY_TIMEOUT_SECONDS
        assert request.get_method() == "POST"
        raise urllib.error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr(captcha.urllib.request, "urlopen", urlopen_falso)
    assert captcha.verify_turnstile("tok", "1.1.1.1") is False

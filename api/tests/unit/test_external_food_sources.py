"""Cliente HTTP das fontes externas (`app/external_food_sources.py`, L3b) sem rede:
a porta de saída `_open` trocada por um dublê. Cobre a chave do USDA no cabeçalho
(nunca na URL, no log, na mensagem), o User-Agent do OFF (sem e-mail), o timeout
curto e a tradução de cada falha externa em 404/502/503/504 — nunca 500."""

from __future__ import annotations

import http.client
import io
import json
import socket
import urllib.error
import urllib.request
from typing import Any

import pytest

from app import external_food_sources as ext
from app.domain.nutrition import external_sources as fontes
from tests.support import nutrition_external_payloads as payloads

CHAVE = "chave-de-teste-0123456789abcdef0123456789"


class _Resposta:
    def __init__(self, corpo: bytes):
        self._corpo = io.BytesIO(corpo)

    def read(self, n: int = -1) -> bytes:
        return self._corpo.read(n)

    def __enter__(self) -> "_Resposta":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def rede(monkeypatch):
    """Dublê de `urlopen`: grava as requisições e devolve/levanta o configurado."""
    estado: dict[str, Any] = {"pedidos": [], "resposta": b"{}", "erro": None, "eventos": []}

    def _open(request, timeout):
        estado["pedidos"].append((request, timeout))
        if estado["erro"] is not None:
            raise estado["erro"]
        return _Resposta(estado["resposta"])

    monkeypatch.setattr(ext, "_open", _open)
    monkeypatch.setattr(ext, "log_event", lambda evento, **campos: estado["eventos"].append((evento, campos)))
    monkeypatch.setenv(ext.USDA_API_KEY_ENV, CHAVE)
    return estado


def _http_error(status: int, corpo: bytes = b"{}") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://exemplo", status, "erro", {}, io.BytesIO(corpo))


def _cabecalhos(request) -> dict[str, str]:
    return {k.lower(): v for k, v in request.header_items()}


# ---------------------------------------------------------------------------
# USDA
# ---------------------------------------------------------------------------


def test_usda_busca_manda_a_chave_no_cabecalho_e_os_filtros_no_corpo(rede):
    rede["resposta"] = json.dumps(payloads.load(payloads.USDA_SEARCH)).encode()
    corpo = ext.usda_search(fontes.usda_search_request("rice"))
    assert corpo["totalHits"] == 141
    request, timeout = rede["pedidos"][0]
    assert request.get_method() == "POST" and request.full_url == f"{ext.USDA_BASE_URL}/foods/search"
    assert CHAVE not in request.full_url and _cabecalhos(request)["x-api-key"] == CHAVE
    assert json.loads(request.data) == fontes.usda_search_request("rice")  # o corpo é o do motor
    assert timeout == ext.EXTERNAL_HTTP_TIMEOUT_SECONDS < 29


def test_usda_detalhe(rede):
    rede["resposta"] = json.dumps(payloads.load(payloads.USDA_SR_LEGACY)).encode()
    assert ext.usda_food(173161)["fdcId"] == 173161
    request, _ = rede["pedidos"][0]
    assert request.get_method() == "GET" and request.full_url == f"{ext.USDA_BASE_URL}/food/173161"
    assert CHAVE not in request.full_url


def test_usda_sem_chave_e_503_sem_chamar_a_rede(rede, monkeypatch):
    monkeypatch.delenv(ext.USDA_API_KEY_ENV)
    assert not ext.usda_configured()
    for chamada in (lambda: ext.usda_search(fontes.usda_search_request("rice")),
                    lambda: ext.usda_food(1), ext.ensure_usda_configured):
        with pytest.raises(ext.ExternalSourceError) as erro:
            chamada()
        assert (erro.value.status_code, erro.value.code) == (503, "usda_not_configured")
    assert rede["pedidos"] == []


@pytest.mark.parametrize(
    ("falha", "status", "codigo"),
    [
        (lambda: _http_error(404), 404, "usda_not_found"),
        (lambda: _http_error(429), 503, "usda_unavailable"),
        (lambda: _http_error(403), 503, "usda_unavailable"),
        (lambda: _http_error(503), 503, "usda_unavailable"),
        (lambda: _http_error(500), 502, "usda_bad_gateway"),
        (lambda: _http_error(400), 502, "usda_bad_gateway"),
        (lambda: urllib.error.URLError(socket.timeout("timed out")), 504, "usda_timeout"),
        (lambda: socket.timeout("timed out"), 504, "usda_timeout"),
        (lambda: TimeoutError(), 504, "usda_timeout"),
        (lambda: urllib.error.URLError(ConnectionRefusedError()), 502, "usda_bad_gateway"),
        (lambda: ConnectionResetError(), 502, "usda_bad_gateway"),
        (lambda: http.client.IncompleteRead(b"{"), 502, "usda_bad_gateway"),
        (lambda: http.client.BadStatusLine("x"), 502, "usda_bad_gateway"),
        (lambda: ValueError("Invalid header value"), 502, "usda_bad_gateway"),
        (lambda: _http_error(302), 502, "usda_bad_gateway"),  # redirecionamento não é seguido
    ],
)
def test_usda_falha_externa_vira_404_502_503_504_sem_vazar_a_chave(rede, falha, status, codigo):
    rede["erro"] = falha()
    with pytest.raises(ext.ExternalSourceError) as erro:
        ext.usda_food(1)
    assert (erro.value.status_code, erro.value.code, erro.value.source) == (status, codigo, "usda")
    texto = f"{erro.value} {erro.value.message} {rede['eventos']!r}"
    assert CHAVE not in texto and "api.nal.usda.gov" not in texto
    assert rede["eventos"][-1][0] == ext.EVENT_EXTERNAL_FAILED


@pytest.mark.parametrize(
    ("corpo", "codigo"),
    [(b"<html>", "usda_bad_gateway"), (b"[1, 2]", "usda_bad_gateway"),
     (b" " * (ext.MAX_RESPONSE_BYTES + 1), "usda_bad_gateway")],
    ids=["json_invalido", "payload_inesperado", "grande_demais"],
)
def test_usda_resposta_estranha_e_502(rede, corpo, codigo):
    rede["resposta"] = corpo
    with pytest.raises(ext.ExternalSourceError) as erro:
        ext.usda_food(1)
    assert (erro.value.status_code, erro.value.code) == (502, codigo)


# ---------------------------------------------------------------------------
# Open Food Facts
# ---------------------------------------------------------------------------


def test_off_produto_com_user_agent_do_app_e_os_campos_pedidos(rede):
    rede["resposta"] = json.dumps({"code": payloads.OFF_MOCA_CODE, "product": payloads.load(payloads.OFF_MOCA),
                                   "result": {"id": "product_found"}, "status": "success"}).encode()
    produto = ext.off_product(payloads.OFF_MOCA_CODE, fields=("code", "nutrition"))
    assert produto["code"] == payloads.OFF_MOCA_CODE
    request, timeout = rede["pedidos"][0]
    assert request.full_url == (f"{ext.OFF_BASE_URL}/api/v3.6/product/{payloads.OFF_MOCA_CODE}.json"
                                "?fields=code%2Cnutrition")
    agente = _cabecalhos(request)["user-agent"]
    assert agente == ext.APP_USER_AGENT and "MuscleFit/" in agente and "https://musclefit.com.br" in agente
    assert "@" not in agente  # sem e-mail de ninguém
    assert "x-api-key" not in _cabecalhos(request) and timeout == ext.EXTERNAL_HTTP_TIMEOUT_SECONDS


def test_off_inexistente_real_e_404(rede):
    gravado = payloads.load(payloads.OFF_NOT_FOUND)
    assert gravado["http_status"] == 404 and gravado["body"]["status"] == "failure"
    rede["erro"] = _http_error(gravado["http_status"], json.dumps(gravado["body"]).encode())
    with pytest.raises(ext.ExternalSourceError) as erro:
        ext.off_product(payloads.OFF_NOT_FOUND_CODE, fields=("code",))
    assert (erro.value.status_code, erro.value.code) == (404, "off_not_found")


@pytest.mark.parametrize(
    "corpo",
    [{"status": "failure", "result": {"id": "product_not_found"}}, {"status": 0}, {"status": "success", "product": {}}],
    ids=["v3_falha_com_200", "v2_status_0", "sem_produto"],
)
def test_off_200_sem_produto_tambem_e_404(rede, corpo):
    rede["resposta"] = json.dumps(corpo).encode()
    with pytest.raises(ext.ExternalSourceError) as erro:
        ext.off_product("7891000100103", fields=("code",))
    assert erro.value.code == "off_not_found"


@pytest.mark.parametrize(
    ("falha", "status", "codigo"),
    [
        (lambda: _http_error(503), 503, "off_unavailable"),
        (lambda: _http_error(429), 503, "off_unavailable"),
        (lambda: _http_error(502), 502, "off_bad_gateway"),
        (lambda: urllib.error.URLError(socket.timeout("timed out")), 504, "off_timeout"),
    ],
)
def test_off_falha_externa(rede, falha, status, codigo):
    rede["erro"] = falha()
    with pytest.raises(ext.ExternalSourceError) as erro:
        ext.off_product("7891000100103", fields=("code",))
    assert (erro.value.status_code, erro.value.code) == (status, codigo)
    assert "openfoodfacts" not in erro.value.message.lower().replace("open food facts", "")


@pytest.mark.parametrize("valor", ["", "   ", "chave com espaco", "chave\x07controle", "chave\nquebrada"])
def test_chave_vazia_ou_impossivel_num_cabecalho_conta_como_nao_configurada(monkeypatch, valor):
    monkeypatch.setenv(ext.USDA_API_KEY_ENV, valor)
    assert ext.usda_api_key() is None and not ext.usda_configured()
    monkeypatch.setenv(ext.USDA_API_KEY_ENV, f"  {CHAVE}  ")
    assert ext.usda_api_key() == CHAVE


def test_o_opener_nao_segue_redirecionamento():
    """O 3xx não leva o `X-Api-Key` para outro host: o handler recusa e o urllib
    levanta `HTTPError` com o 3xx (que vira `bad_gateway`)."""
    handler = ext._NoRedirect()
    pedido = urllib.request.Request(f"{ext.USDA_BASE_URL}/food/1", headers={"X-Api-Key": CHAVE})
    assert handler.redirect_request(pedido, None, 302, "Found", {}, "http://outro.exemplo/x") is None
    assert any(isinstance(h, ext._NoRedirect) for h in ext._OPENER.handlers)
    assert not any(type(h) is urllib.request.HTTPRedirectHandler for h in ext._OPENER.handlers)

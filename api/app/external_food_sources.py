"""Clientes HTTP finos das fontes externas do catálogo de alimentos (plano de
nutrição §4, §8.1 e §12; Lote L3b): USDA FoodData Central e Open Food Facts.

Só transporte, SEM regra: quais tipos de dado do USDA entram, quais campos do
OFF são lidos, o mapeamento de nutrientes, conversões de unidade, validação do
código de barras e o limite diário são do motor
`domain/nutrition/external_sources` (mapa `docs/motores/FONTES_DA_VERDADE.md`
§3.7 e §49). Quem chama passa os parâmetros prontos do motor.

Mesmo padrão de `captcha.py` e `intervals_client.py`: `urllib.request` puro
(nenhuma dependência nova), timeout explícito e curto
(`EXTERNAL_HTTP_TIMEOUT_SECONDS`, bem abaixo dos 29 s do API Gateway) e todo
erro externo reempacotado em `ExternalSourceError`, que já carrega o status
HTTP da NOSSA resposta (502/503/504/404), nunca 500.

Segredos: a chave do USDA (`USDA_API_KEY`, hidratada no cold start por
`secrets_bootstrap.py` a partir de `USDA_API_KEY_SECRET_ARN`; no DEV vem do
`.env`) vai no cabeçalho `X-Api-Key` (api.data.gov aceita o cabeçalho), nunca
na URL — assim ela não aparece em log, em mensagem de erro nem em resposta.
Os eventos de log levam só a fonte, a operação, o motivo e o status externo.

User-Agent do OFF (obrigatório pela documentação da API): nome + versão do app
+ site, sem e-mail de ninguém.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Optional, Sequence

from .observability import log_event

APP_USER_AGENT = "MuscleFit/1.0 (+https://musclefit.com.br)"
# Timeout de CADA operação de rede (conexão e cada leitura; o DNS não tem
# limite). A rota faz UMA chamada externa por requisição, mas o teto real é o
# do API Gateway (29 s): Aurora acordando do auto-pause (até ~20 s no
# `db_dataapi`) + uma resposta que pinga devagar podem passar dele (504 do
# gateway). Limitação registrada no mapa §49.
EXTERNAL_HTTP_TIMEOUT_SECONDS = 8.0
# Teto do corpo lido (o detalhe de um alimento Foundation tem ~100 KB).
MAX_RESPONSE_BYTES = 5 * 1024 * 1024

USDA_API_KEY_ENV = "USDA_API_KEY"
USDA_BASE_URL = "https://api.nal.usda.gov/fdc/v1"
OFF_BASE_URL = "https://world.openfoodfacts.org"
OFF_PRODUCT_PATH = "/api/v3.6/product/{code}.json"

SOURCE_USDA = "usda"
SOURCE_OFF = "off"
_SOURCE_NAMES = {SOURCE_USDA: "USDA FoodData Central", SOURCE_OFF: "Open Food Facts"}

EVENT_EXTERNAL_FAILED = "nutrition.external_source_failed"

# Tipos de falha -> (status HTTP da nossa resposta, texto).
KIND_NOT_CONFIGURED = "not_configured"
KIND_UNAVAILABLE = "unavailable"
KIND_TIMEOUT = "timeout"
KIND_BAD_GATEWAY = "bad_gateway"
KIND_NOT_FOUND = "not_found"

_KIND_STATUS: Mapping[str, int] = {
    KIND_NOT_CONFIGURED: 503,
    KIND_UNAVAILABLE: 503,
    KIND_TIMEOUT: 504,
    KIND_BAD_GATEWAY: 502,
    KIND_NOT_FOUND: 404,
}
_KIND_MESSAGE: Mapping[str, str] = {
    KIND_NOT_CONFIGURED: "{name} não configurado neste ambiente (chave da API ausente).",
    KIND_UNAVAILABLE: "{name} indisponível no momento (limite de uso ou acesso recusado). Tente mais tarde.",
    KIND_TIMEOUT: "{name} não respondeu a tempo. Tente de novo em instantes.",
    KIND_BAD_GATEWAY: "{name} respondeu com erro. Tente de novo em instantes.",
    KIND_NOT_FOUND: "Item não encontrado no {name}.",
}


class ExternalSourceError(Exception):
    """Falha de uma fonte externa, já traduzida para a resposta da API.

    `code` = `<fonte>_<tipo>` (ex.: `usda_timeout`, `off_not_found`);
    `status_code` é o da NOSSA resposta; `upstream_status` é o da fonte
    (None em rede/timeout). A mensagem nunca leva URL nem chave."""

    def __init__(self, source: str, kind: str, upstream_status: Optional[int] = None):
        self.source = source
        self.kind = kind
        self.upstream_status = upstream_status
        self.status_code = _KIND_STATUS[kind]
        self.code = f"{source}_{kind}"
        self.message = _KIND_MESSAGE[kind].format(name=_SOURCE_NAMES.get(source, source))
        super().__init__(self.code)


def usda_api_key() -> Optional[str]:
    """A chave, ou None se ausente ou impossível num cabeçalho HTTP (espaço ou
    caractere de controle no meio: secret gravado errado conta como "não
    configurado", nunca como 500)."""
    valor = os.environ.get(USDA_API_KEY_ENV, "").strip()
    if not valor or not valor.isprintable() or any(c.isspace() for c in valor):
        return None
    return valor


def usda_configured() -> bool:
    return usda_api_key() is not None


def _fail(source: str, operation: str, kind: str, reason: str, upstream_status: Optional[int] = None) -> ExternalSourceError:
    log_event(EVENT_EXTERNAL_FAILED, source=source, operation=operation, kind=kind, reason=reason,
              upstream_status=upstream_status)
    return ExternalSourceError(source, kind, upstream_status)


def _kind_for_http_status(status: int) -> str:
    if status == 404:
        return KIND_NOT_FOUND
    if status in (401, 403, 429, 503):
        return KIND_UNAVAILABLE
    return KIND_BAD_GATEWAY


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, (TimeoutError, socket.timeout))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Não segue 3xx: o redirecionamento copiaria o `X-Api-Key` para outro host
    (e um POST viraria GET sem corpo). O 3xx vira `HTTPError` -> `bad_gateway`."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - assinatura do urllib
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def _open(request: urllib.request.Request, timeout: float) -> Any:
    """Única porta de saída HTTP (os testes trocam só esta função)."""
    return _OPENER.open(request, timeout=timeout)


def _request_json(
    source: str,
    operation: str,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    body: Optional[Mapping[str, Any]] = None,
    timeout: float = EXTERNAL_HTTP_TIMEOUT_SECONDS,
) -> Any:
    """Faz a requisição e devolve o JSON. Separado para os testes trocarem só a
    camada HTTP. Todo erro vira `ExternalSourceError` (com log sem URL/chave)."""
    dados = None if body is None else json.dumps(body).encode("utf-8")
    cabecalhos = {"Accept": "application/json", "User-Agent": APP_USER_AGENT, **headers}
    if dados is not None:
        cabecalhos["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=dados, method=method, headers=cabecalhos)
    try:
        with _open(request, timeout) as response:
            bruto = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise _fail(source, operation, _kind_for_http_status(exc.code), "http_error", exc.code) from None
    except urllib.error.URLError as exc:
        kind = KIND_TIMEOUT if _is_timeout(exc.reason) else KIND_BAD_GATEWAY  # type: ignore[arg-type]
        raise _fail(source, operation, kind, type(exc.reason).__name__) from None
    except (TimeoutError, socket.timeout):
        raise _fail(source, operation, KIND_TIMEOUT, "timeout") from None
    except (OSError, http.client.HTTPException, ValueError) as exc:
        # `HTTPException` (IncompleteRead, BadStatusLine, LineTooLong) não é OSError
        # e o urlopen não o embrulha em URLError; ValueError = cabeçalho inválido.
        raise _fail(source, operation, KIND_BAD_GATEWAY, type(exc).__name__) from None
    if len(bruto) > MAX_RESPONSE_BYTES:
        raise _fail(source, operation, KIND_BAD_GATEWAY, "response_too_large")
    try:
        return json.loads(bruto.decode("utf-8"))
    except ValueError:
        raise _fail(source, operation, KIND_BAD_GATEWAY, "invalid_json") from None


# ---------------------------------------------------------------------------
# USDA FoodData Central (https://fdc.nal.usda.gov/api-guide/)
# ---------------------------------------------------------------------------


def _usda_headers(operation: str) -> dict[str, str]:
    chave = usda_api_key()
    if chave is None:
        raise _fail(SOURCE_USDA, operation, KIND_NOT_CONFIGURED, "missing_api_key")
    return {"X-Api-Key": chave}


def ensure_usda_configured() -> None:
    """`usda_not_configured` (503) quando a chave não existe — as rotas do USDA
    checam antes de qualquer outra coisa."""
    _usda_headers("configuration")


def usda_search(body: Mapping[str, Any]) -> dict[str, Any]:
    """`POST /foods/search` com o corpo montado pelo motor
    (`external_sources.usda_search_request`: termo, tipos aceitos, página)."""
    cabecalhos = _usda_headers("search")
    payload = _request_json(SOURCE_USDA, "search", "POST", f"{USDA_BASE_URL}/foods/search", headers=cabecalhos, body=body)
    if not isinstance(payload, dict):
        raise _fail(SOURCE_USDA, "search", KIND_BAD_GATEWAY, "unexpected_payload")
    return payload


def usda_food(fdc_id: int) -> dict[str, Any]:
    """`GET /food/{fdcId}` (formato completo). 404 da fonte -> `usda_not_found`."""
    cabecalhos = _usda_headers("food")
    payload = _request_json(SOURCE_USDA, "food", "GET", f"{USDA_BASE_URL}/food/{int(fdc_id)}", headers=cabecalhos)
    if not isinstance(payload, dict):
        raise _fail(SOURCE_USDA, "food", KIND_BAD_GATEWAY, "unexpected_payload")
    return payload


# ---------------------------------------------------------------------------
# Open Food Facts (https://openfoodfacts.github.io/openfoodfacts-server/api/)
# ---------------------------------------------------------------------------


def off_product(code: str, *, fields: Sequence[str]) -> dict[str, Any]:
    """Produto pelo código de barras (API v3.6), só com os `fields` que o motor lê.

    Produto inexistente -> `off_not_found` (HTTP 404 da fonte, ou 200 com
    `status` de falha / sem `product`, as duas formas aceitas)."""
    consulta = urllib.parse.urlencode({"fields": ",".join(fields)})
    url = f"{OFF_BASE_URL}{OFF_PRODUCT_PATH.format(code=urllib.parse.quote(code, safe=''))}?{consulta}"
    payload = _request_json(SOURCE_OFF, "product", "GET", url, headers={})
    if not isinstance(payload, dict):
        raise _fail(SOURCE_OFF, "product", KIND_BAD_GATEWAY, "unexpected_payload")
    produto = payload.get("product")
    if payload.get("status") in ("failure", 0) or not isinstance(produto, dict) or not produto:
        raise ExternalSourceError(SOURCE_OFF, KIND_NOT_FOUND)
    return produto


__all__ = [
    "APP_USER_AGENT",
    "EXTERNAL_HTTP_TIMEOUT_SECONDS",
    "ExternalSourceError",
    "ensure_usda_configured",
    "off_product",
    "usda_api_key",
    "usda_configured",
    "usda_food",
    "usda_search",
]

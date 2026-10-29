"""Verificacao de captcha (Cloudflare Turnstile) para rotas publicas.

Modulo isolado de proposito: a rota que exige captcha so precisa de
`verify_turnstile(token, remote_ip) -> bool`. A chamada HTTP segue o mesmo
padrao de `app/intervals_client.py` (`urllib.request` puro, timeout explicito,
sem dependencia nova).

Politica FAIL-CLOSED: sem `TURNSTILE_SECRET_KEY` no ambiente o chamador recebe
`CaptchaNotConfigured` e deve responder 503 - nunca aceitar sem captcha. Nao
existe bypass por env de dev: em DEV usam-se as chaves de TESTE oficiais da
Cloudflare (que sempre passam), com exatamente o mesmo codigo de producao.

Qualquer falha de rede/timeout/resposta inesperada e tratada como token
INVALIDO (retorna False), com evento estruturado - o token do captcha nunca
vai para o log.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Optional

from .observability import log_event

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TURNSTILE_SECRET_ENV = "TURNSTILE_SECRET_KEY"
VERIFY_TIMEOUT_SECONDS = 5.0

EVENT_CAPTCHA_NOT_CONFIGURED = "captcha.not_configured"
EVENT_CAPTCHA_VERIFY_FAILED = "captcha.verify_failed"


class CaptchaNotConfigured(Exception):
    """`TURNSTILE_SECRET_KEY` ausente: a rota deve falhar fechado (503)."""


def turnstile_secret() -> Optional[str]:
    value = os.environ.get(TURNSTILE_SECRET_ENV, "").strip()
    return value or None


def _http_post_form(url: str, fields: Mapping[str, str], timeout: float) -> Any:
    """POST `application/x-www-form-urlencoded` e devolve o JSON da resposta.

    Separado para os testes trocarem so a camada HTTP (sucesso, falha,
    timeout) sem tocar na logica de decisao."""
    body = urllib.parse.urlencode(dict(fields)).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def verify_turnstile(token: Optional[str], remote_ip: Optional[str], *, timeout: float = VERIFY_TIMEOUT_SECONDS) -> bool:
    """True somente se a Cloudflare confirmar o token.

    - sem secret configurado -> `CaptchaNotConfigured` (fail-closed);
    - token vazio -> False sem chamar a rede;
    - erro HTTP, rede, timeout ou JSON inesperado -> False + evento.
    """
    secret = turnstile_secret()
    if not secret:
        log_event(EVENT_CAPTCHA_NOT_CONFIGURED, env=TURNSTILE_SECRET_ENV)
        raise CaptchaNotConfigured(f"{TURNSTILE_SECRET_ENV} nao configurada.")

    token = (token or "").strip()
    if not token:
        return False

    fields = {"secret": secret, "response": token}
    if remote_ip:
        fields["remoteip"] = remote_ip

    try:
        payload = _http_post_form(TURNSTILE_VERIFY_URL, fields, timeout)
    except urllib.error.HTTPError as exc:
        log_event(EVENT_CAPTCHA_VERIFY_FAILED, reason="http_error", status_code=exc.code)
        return False
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        # URLError cobre DNS/conexao/timeout do socket; ValueError cobre JSON
        # invalido. O nome da classe basta para diagnostico - sem token.
        log_event(EVENT_CAPTCHA_VERIFY_FAILED, reason=type(exc).__name__)
        return False

    if not isinstance(payload, dict):
        log_event(EVENT_CAPTCHA_VERIFY_FAILED, reason="unexpected_payload")
        return False

    success = bool(payload.get("success"))
    if not success:
        # `error-codes` e a lista documentada pela Cloudflare (ex.:
        # `invalid-input-response`, `timeout-or-duplicate`) - util no log e
        # nao identifica ninguem.
        log_event(EVENT_CAPTCHA_VERIFY_FAILED, reason="rejected", error_codes=payload.get("error-codes"))
    return success

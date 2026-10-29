"""M5 - "Esqueci minha senha": solicitacao e confirmacao de redefinicao de
senha por e-mail (rotas PUBLICAS, sem sessao).

Fluxo:
  POST /auth/password-reset/request {email|username, captcha_token}
    -> captcha (Cloudflare Turnstile, `app/captcha.py`, fail-closed)
    -> resposta SEMPRE 202 e IDENTICA para conta existente ou nao (nao vaza
       cadastro); se a conta existe, e ativa e tem e-mail, grava o hash de um
       token e envia a URL por SES.
  POST /auth/password-reset/confirm {token, new_password}
    -> hash + expiracao + uso unico (UPDATE atomico), troca a senha com a
       mesma politica de `/auth/change-password`, revoga todas as sessoes,
       zera `failed_login_attempts`/`locked_until` e queima os demais tokens
       pendentes do usuario.

Segredos e logs: o token cru so existe na memoria desta requisicao e no
e-mail; o banco guarda SHA-256 (`password_reset_tokens.token_hash`, migracao
043). Nenhum evento de log recebe token, URL, e-mail ou nome - so ids
numericos e contagens (regra de `observability.log_event`).

Limitacao de taxa sem infra nova: `password_reset_attempts` (043) conta
tentativas por janela deslizante, por IP e por identificador digitado (hash,
exista a conta ou nao - assim o 429 tambem nao distingue contas). O IP e
`request.client.host`: no Lambda o Mangum o preenche com o `sourceIp` do API
Gateway, que o cliente nao controla (X-Forwarded-For seria falsificavel).

Lockout (`auth.register_failed_login`, PR #147) continua exclusivo do login:
estas rotas nao adivinham credenciais (token de 256 bits) e o reset e
justamente a saida de uma conta bloqueada - por isso `request` aceita conta
bloqueada e `confirm` limpa o bloqueio.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth import MIN_PASSWORD_LENGTH, hash_password
from .client_ip import client_ip  # SEG-14: IP atras do Lambda Web Adapter
from .captcha import CaptchaNotConfigured, verify_turnstile
from .db import transaction
from .observability import log_event

router = APIRouter(prefix="/auth/password-reset", tags=["auth"])

TOKEN_TTL_MINUTES = 30
RATE_LIMIT_WINDOW_MINUTES = 15
MAX_REQUESTS_PER_IP = 10
MAX_REQUESTS_PER_ACCOUNT = 3
MAX_CONFIRMS_PER_IP = 10

SCOPE_REQUEST_IP = "request_ip"
SCOPE_REQUEST_ACCOUNT = "request_account"
SCOPE_CONFIRM_IP = "confirm_ip"

FROM_EMAIL_ENV = "PASSWORD_RESET_FROM_EMAIL"
APP_BASE_URL_ENV = "APP_BASE_URL"
RESET_PATH = "/redefinir-senha"

# Mensagens em constantes para que TODOS os caminhos devolvam o mesmo texto.
ACCEPTED_DETAIL = (
    "Se existir uma conta ativa com esses dados, enviaremos um e-mail com as instrucoes "
    "para redefinir a senha."
)
RATE_LIMITED_DETAIL = "Muitas solicitacoes. Aguarde alguns minutos e tente novamente."
INVALID_TOKEN_DETAIL = "Link de redefinicao invalido, expirado ou ja utilizado. Solicite um novo."
CAPTCHA_INVALID_DETAIL = "Verificacao de seguranca (captcha) invalida. Recarregue a pagina e tente novamente."
CAPTCHA_UNAVAILABLE_DETAIL = "Verificacao de seguranca indisponivel no momento. Tente novamente mais tarde."
IDENTIFIER_REQUIRED_DETAIL = "Informe o e-mail ou o usuario da conta."

EVENT_REQUESTED = "password_reset.requested"
EVENT_NO_EMAIL = "password_reset.no_email"
EVENT_EMAIL_SENT = "password_reset.email_sent"
EVENT_EMAIL_FAILED = "password_reset.email_failed"
EVENT_EMAIL_NOT_CONFIGURED = "password_reset.email_not_configured"
EVENT_RATE_LIMITED = "password_reset.rate_limited"
EVENT_CAPTCHA_REJECTED = "password_reset.captcha_rejected"
EVENT_CONFIRMED = "password_reset.confirmed"
EVENT_CONFIRM_REJECTED = "password_reset.confirm_rejected"


class ResetRequestIn(BaseModel):
    email: Optional[str] = None
    username: Optional[str] = None
    # Opcional no schema para a ausencia virar 400 (regra do captcha), nao 422.
    captcha_token: Optional[str] = None


class ResetConfirmIn(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_reset_url(base_url: str, raw_token: str) -> str:
    return f"{base_url.rstrip('/')}{RESET_PATH}?{urllib.parse.urlencode({'token': raw_token})}"


def _normalize_identifier(payload: ResetRequestIn) -> Optional[tuple[str, str]]:
    """Devolve ('email', valor) ou ('username', valor); None se nada veio.
    E-mail e comparado sem distinguir maiusculas (LOWER no SQL)."""
    email = (payload.email or "").strip().lower()
    if email:
        return "email", email
    username = (payload.username or "").strip()
    if username:
        return "username", username
    return None


def _count_attempts(cur, scope: str, subject_hash: str, since: datetime) -> int:
    cur.execute(
        "SELECT COUNT(*) AS n FROM password_reset_attempts "
        "WHERE scope = %s AND subject_hash = %s AND created_at >= %s",
        (scope, subject_hash, since),
    )
    row = cur.fetchone()
    return int((row or {}).get("n") or 0)


def _throttle(scope: str, subject: str, limit: int) -> bool:
    """Registra UMA tentativa e devolve True se o limite da janela ja foi
    atingido ANTES dela. Transacao propria: o registro precisa sobreviver ao
    `HTTPException` que o chamador levanta em seguida (mesma licao do
    `register_failed_login`)."""
    now = datetime.utcnow()
    since = now - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)
    subject_hash = _sha256(f"{scope}:{subject}")
    with transaction() as conn:
        with conn.cursor() as cur:
            # Faxina barata das linhas fora de qualquer janela (indice cobre).
            cur.execute(
                "DELETE FROM password_reset_attempts WHERE created_at < %s",
                (now - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES * 4),),
            )
            exceeded = _count_attempts(cur, scope, subject_hash, since) >= limit
            cur.execute(
                "INSERT INTO password_reset_attempts (scope, subject_hash, created_at) VALUES (%s, %s, %s)",
                (scope, subject_hash, now),
            )
    if exceeded:
        log_event(EVENT_RATE_LIMITED, scope=scope, limit=limit, window_minutes=RATE_LIMIT_WINDOW_MINUTES)
    return exceeded


def _rate_limited() -> HTTPException:
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=RATE_LIMITED_DETAIL)


# ---------------------------------------------------------------------------
# E-mail (SES via boto3). Em DEV `AWS_ENDPOINT_URL` ja aponta para o
# LocalStack, entao e o MESMO codigo de producao - sem backend "console".
# ---------------------------------------------------------------------------


def _ses_client():
    import boto3  # import local: nao pagar custo de import em cold start

    region_name = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "sa-east-1"
    return boto3.client("ses", region_name=region_name)


def _email_bodies(username: str, reset_url: str) -> tuple[str, str]:
    text = (
        f"Ola, {username}.\n\n"
        "Recebemos um pedido para redefinir a senha da sua conta MuscleFit. "
        f"Abra o link abaixo (valido por {TOKEN_TTL_MINUTES} minutos e de uso unico):\n\n"
        f"{reset_url}\n\n"
        "Se voce nao pediu isso, ignore este e-mail: sua senha continua a mesma.\n"
    )
    html = (
        f"<p>Ola, <strong>{username}</strong>.</p>"
        "<p>Recebemos um pedido para redefinir a senha da sua conta MuscleFit. "
        f"O link abaixo vale por {TOKEN_TTL_MINUTES} minutos e so pode ser usado uma vez:</p>"
        f'<p><a href="{reset_url}">Redefinir minha senha</a></p>'
        "<p>Se voce nao pediu isso, ignore este e-mail: sua senha continua a mesma.</p>"
    )
    return text, html


def send_reset_email(*, user_id: int, to_email: str, username: str, raw_token: str) -> bool:
    """Envia o e-mail com a URL de redefinicao. Nunca levanta: falha vira
    evento estruturado (sem token) e a rota segue respondendo 202."""
    from_email = os.environ.get(FROM_EMAIL_ENV, "").strip()
    base_url = os.environ.get(APP_BASE_URL_ENV, "").strip()
    missing = [name for name, value in ((FROM_EMAIL_ENV, from_email), (APP_BASE_URL_ENV, base_url)) if not value]
    if missing:
        log_event(EVENT_EMAIL_NOT_CONFIGURED, user_id=user_id, missing_env=missing)
        return False

    reset_url = build_reset_url(base_url, raw_token)
    text, html = _email_bodies(username, reset_url)
    try:
        _ses_client().send_email(
            Source=from_email,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": "MuscleFit - redefinicao de senha", "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text, "Charset": "UTF-8"},
                    "Html": {"Data": html, "Charset": "UTF-8"},
                },
            },
        )
    except Exception as exc:  # noqa: BLE001 - qualquer falha de envio e nao-fatal
        # `MessageRejected` (identidade nao verificada / sandbox do SES),
        # `AccessDenied` etc. chegam como botocore ClientError: o codigo do
        # erro vai para o log; a resposta HTTP segue 202, sem vazar a conta.
        error_code = None
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            error_code = (response.get("Error") or {}).get("Code")
        log_event(EVENT_EMAIL_FAILED, user_id=user_id, reason=type(exc).__name__, error_code=error_code)
        return False

    log_event(EVENT_EMAIL_SENT, user_id=user_id)
    return True


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


def _find_active_users(kind: str, value: str) -> list[dict[str, Any]]:
    column = "LOWER(email)" if kind == "email" else "username"
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, username, email FROM users WHERE {column} = %s AND is_active = 1 ORDER BY id",
                (value,),
            )
            return list(cur.fetchall() or [])


def _issue_token(user_id: int, ip: str) -> str:
    raw_token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_ip, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (user_id, _sha256(raw_token), now + timedelta(minutes=TOKEN_TTL_MINUTES), ip, now),
            )
    return raw_token


@router.post("/request", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(payload: ResetRequestIn, request: Request):
    ip = client_ip(request)

    # 1. Limite por IP: conta TODA chamada, inclusive as que falham no captcha.
    if _throttle(SCOPE_REQUEST_IP, ip, MAX_REQUESTS_PER_IP):
        raise _rate_limited()

    # 2. Captcha ANTES de qualquer consulta a conta (fail-closed sem secret).
    try:
        captcha_ok = verify_turnstile(payload.captcha_token, ip)
    except CaptchaNotConfigured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=CAPTCHA_UNAVAILABLE_DETAIL)
    if not captcha_ok:
        log_event(EVENT_CAPTCHA_REJECTED)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CAPTCHA_INVALID_DETAIL)

    identifier = _normalize_identifier(payload)
    if identifier is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=IDENTIFIER_REQUIRED_DETAIL)
    kind, value = identifier

    # 3. Limite por conta, chaveado pelo TEXTO digitado (exista ou nao).
    if _throttle(SCOPE_REQUEST_ACCOUNT, f"{kind}:{value}", MAX_REQUESTS_PER_ACCOUNT):
        raise _rate_limited()

    # 4. Daqui em diante NADA muda a resposta: conta inexistente, inativa, sem
    # e-mail ou falha de envio recebem exatamente o mesmo 202.
    for user in _find_active_users(kind, value):
        log_event(EVENT_REQUESTED, user_id=user["id"])
        if not user.get("email"):
            log_event(EVENT_NO_EMAIL, user_id=user["id"])
            continue
        raw_token = _issue_token(user["id"], ip)
        send_reset_email(user_id=user["id"], to_email=user["email"], username=user["username"], raw_token=raw_token)

    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"status": "accepted", "detail": ACCEPTED_DETAIL})


@router.post("/confirm")
def confirm_password_reset(payload: ResetConfirmIn, request: Request):
    ip = client_ip(request)
    if _throttle(SCOPE_CONFIRM_IP, ip, MAX_CONFIRMS_PER_IP):
        raise _rate_limited()

    token_hash = _sha256(payload.token.strip())
    new_hash = hash_password(payload.new_password)
    now = datetime.utcnow()

    # Nenhum `raise` dentro do `with transaction()`: decisao em flags, excecao
    # so depois do commit (mesma regra do login em auth.py).
    user_id: Optional[int] = None
    inactive = False
    revoked_sessions = 0

    with transaction() as conn:
        with conn.cursor() as cur:
            # Reserva atomica: so UMA requisicao consegue marcar o token como
            # usado; expirado/usado/inexistente caem no mesmo rowcount = 0.
            cur.execute(
                "UPDATE password_reset_tokens SET used_at = %s "
                "WHERE token_hash = %s AND used_at IS NULL AND expires_at > %s",
                (now, token_hash, now),
            )
            if int(cur.rowcount or 0) == 1:
                cur.execute(
                    "SELECT t.user_id, u.is_active FROM password_reset_tokens t "
                    "JOIN users u ON u.id = t.user_id WHERE t.token_hash = %s",
                    (token_hash,),
                )
                row = cur.fetchone()
                if row and row.get("is_active"):
                    user_id = int(row["user_id"])
                    cur.execute(
                        "UPDATE users SET password_hash = %s, must_change_password = 0, "
                        "failed_login_attempts = 0, locked_until = NULL WHERE id = %s",
                        (new_hash, user_id),
                    )
                    cur.execute(
                        "UPDATE user_sessions SET revoked_at = CURRENT_TIMESTAMP "
                        "WHERE user_id = %s AND revoked_at IS NULL",
                        (user_id,),
                    )
                    revoked_sessions = int(cur.rowcount or 0)
                    # Demais links pendentes do mesmo usuario deixam de valer.
                    cur.execute(
                        "UPDATE password_reset_tokens SET used_at = %s WHERE user_id = %s AND used_at IS NULL",
                        (now, user_id),
                    )
                else:
                    inactive = True

    if user_id is None:
        log_event(EVENT_CONFIRM_REJECTED, reason="inactive_user" if inactive else "invalid_or_expired")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_TOKEN_DETAIL)

    log_event(EVENT_CONFIRMED, user_id=user_id, revoked_sessions=revoked_sessions)
    return {"status": "ok"}

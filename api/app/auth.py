"""Autenticação multiusuário: login, logout, sessão e troca de senha.

Parte 1 da migração multiusuário: fundação de identidade/autenticação. MCP/
Telegram e o pareamento do relógio Zepp continuam usando exclusivamente o
token mestre único (ou tokens de dispositivo pareado) — não passam por este
router, e são resolvidos para o usuário admin por security.resolve_identity.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import string
from functools import lru_cache
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import login_throttle
from .client_ip import client_ip
from .db import transaction
from .db_helpers import jsonable
from .domain.nutrition import anthropometrics as anthro
from .exercise_card_service import generation_enabled as exercise_cards_enabled
from .security import (
    Identity,
    SESSION_COOKIE_NAME,
    get_current_identity,
    session_cookie_domain,
    session_cookie_secure,
)
from .timezones import normalize_timezone

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger("fitness.auth")

SESSION_TTL_DAYS = 30
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
# Conta bloqueada por tentativas (login OU troca de senha). Aqui o usuário já
# está autenticado: dizer que a conta está bloqueada não revela nada (o login
# continua com a resposta única de SEG-07).
ACCOUNT_LOCKED_DETAIL = (
    "Muitas tentativas com senha incorreta. "
    f"A conta está bloqueada por até {LOCKOUT_MINUTES} minutos; tente novamente depois."
)
MIN_PASSWORD_LENGTH = 8

ADMIN_SEED_USERNAME = "doug"


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


# Mensagens de erro do login. Ficam em constantes para que TODOS os caminhos
# de falha devolvam exatamente o mesmo texto (usuário inexistente, inativo e
# senha errada são indistinguíveis para quem chama - não revelamos se o
# usuário existe).
#
# SEG-07: conta bloqueada (ja estava ou acabou de ficar) tambem devolve
# exatamente esta resposta - 401 sem o 423 distinto, que revelava a existencia
# da conta. O aviso do bloqueio temporario vai na mensagem de TODOS os casos.
INVALID_CREDENTIALS_DETAIL = (
    "Usuário ou senha inválidos. Após várias tentativas seguidas a conta é bloqueada por 15 minutos."
)


# Erros da troca de senha (`POST /auth/change-password`). NUNCA 401: o
# frontend (`apiFetch`) trata todo 401 como sessão encerrada e manda para o
# login - e aqui a sessão é válida. Com 401, a senha atual errada sumia num
# redirect e o usuário da troca obrigatória ficava em loop achando que a
# senha nova tinha sido salva (produção, 2026-10-10). 401 fica só para
# sessão/token inválido (`security.py`).
WRONG_CURRENT_PASSWORD_CODE = "wrong_current_password"
WRONG_CURRENT_PASSWORD_DETAIL = "Senha atual incorreta."
ACCOUNT_LOCKED_CODE = "account_locked"


# Custo do bcrypt (log2 do numero de iteracoes). 12 e EXATAMENTE o default do
# `bcrypt.gensalt()` que este modulo sempre usou - producao nao muda.
#
# Por que virou configuravel (M38): o custo do bcrypt e deliberadamente alto, e
# no CI ele domina os testes que criam usuarios e tentam login. Com 12 rounds
# cada hash custa ~0,3 s; `test_divida_lockout_login_e_reset_admin.py` e
# `test_m5_esqueci_minha_senha.py` juntos gastavam ~17 s dos 92 s do passo
# `pytest integration` QUASE SO nisso. Os testes baixam o custo por env (ver
# `api/tests/conftest.py`), sem tocar em nada da aplicacao.
#
# `BCRYPT_ROUNDS` NAO e definida em lugar nenhum de `infra/` (Lambdas),
# `docker-compose.yml` nem `.env.example` - e a garantia, testada em
# `tests/unit/test_bcrypt_rounds.py`, de que producao continua em 12.
DEFAULT_BCRYPT_ROUNDS = 12
BCRYPT_ROUNDS_ENV = "BCRYPT_ROUNDS"
_BCRYPT_ROUNDS_RANGE = (4, 31)  # limites do proprio algoritmo


def bcrypt_rounds() -> int:
    """Custo do bcrypt para ESTE processo. Lido a cada chamada (nao no import)
    para que o valor do processo de teste valha mesmo se `app.auth` ja tiver
    sido importado. Valor ausente, invalido ou fora da faixa -> o default."""
    raw = os.environ.get(BCRYPT_ROUNDS_ENV, "").strip()
    if not raw:
        return DEFAULT_BCRYPT_ROUNDS
    try:
        rounds = int(raw)
    except ValueError:
        logger.warning("%s=%r invalido (nao e inteiro); usando %d", BCRYPT_ROUNDS_ENV, raw, DEFAULT_BCRYPT_ROUNDS)
        return DEFAULT_BCRYPT_ROUNDS
    low, high = _BCRYPT_ROUNDS_RANGE
    if not low <= rounds <= high:
        logger.warning("%s=%d fora de %d..%d; usando %d", BCRYPT_ROUNDS_ENV, rounds, low, high, DEFAULT_BCRYPT_ROUNDS)
        return DEFAULT_BCRYPT_ROUNDS
    return rounds


# O bcrypt so usa os primeiros 72 bytes da senha. Ate o bcrypt 4 a lib truncava
# em silencio; o bcrypt 5 passou a lancar ValueError. A API aceita senha de ate
# 256 caracteres (`request_models.py`), entao truncamos aqui nos MESMOS 72 bytes
# que a lib truncava: hash novo e verificacao de hash antigo ficam identicos ao
# comportamento anterior (sem 500 no cadastro/troca/reset e sem trancar fora
# quem ja tem senha longa).
BCRYPT_MAX_PASSWORD_BYTES = 72


def _bcrypt_input(raw_password: str) -> bytes:
    return raw_password.encode("utf-8")[:BCRYPT_MAX_PASSWORD_BYTES]


def hash_password(raw_password: str) -> str:
    return bcrypt.hashpw(_bcrypt_input(raw_password), bcrypt.gensalt(rounds=bcrypt_rounds())).decode("utf-8")


def verify_password(raw_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_bcrypt_input(raw_password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


@lru_cache(maxsize=4)
def _dummy_hash(rounds: int) -> str:
    """Hash fictício (custo = o do bcrypt real) para igualar o tempo do login de
    usuário inexistente/inativo/bloqueado ao de senha errada (SEG-07)."""
    return bcrypt.hashpw(b"musclefit-dummy", bcrypt.gensalt(rounds=rounds)).decode("utf-8")


def _burn_password_check(raw_password: str) -> None:
    verify_password(raw_password, _dummy_hash(bcrypt_rounds()))


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def ensure_admin_seed() -> None:
    """Garante que o usuário admin seed ('doug') existe, gerando uma senha
    temporária aleatória na primeira vez.

    Decisão de implementação: o seed roda em Python (chamado no startup do
    app, ver main.py), NÃO como INSERT em SQL puro no 001_schema.sql. Motivo:
    a senha temporária precisa ser (a) gerada aleatoriamente a cada nova
    instalação (secrets.token_urlsafe) e (b) hasheada com bcrypt antes de
    persistir — SQL puro não tem bcrypt disponível, e colar um hash bcrypt
    FIXO no .sql tornaria a senha temporária previsível/pública no
    repositório (qualquer um com acesso ao git teria a senha do admin em
    texto claro no histórico). A tabela `users` em si continua sendo criada
    via `CREATE TABLE IF NOT EXISTS` no .sql, no mesmo estilo das demais
    tabelas — só o INSERT do usuário admin com senha aleatória fica aqui.

    Idempotente: só insere se 'doug' ainda não existir; reinicializações
    seguintes são no-op silencioso (sem gerar senha nova nem logar nada).
    """
    temp_password: Optional[str] = None

    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s", (ADMIN_SEED_USERNAME,))
                if cur.fetchone():
                    return

                temp_password = secrets.token_urlsafe(12)
                password_hash = hash_password(temp_password)

                cur.execute(
                    """
                    INSERT INTO users (username, password_hash, full_name, role, is_active, must_change_password)
                    VALUES (%s, %s, %s, 'admin', 1, 1)
                    """,
                    (ADMIN_SEED_USERNAME, password_hash, "Doug"),
                )
    except Exception:
        logger.exception("Falha ao criar usuário admin seed ('doug').")
        return

    if temp_password:
        # SEG-20: a senha temporaria NUNCA vai para log/stdout (CloudWatch guarda
        # logs). O admin define a senha pelo fluxo de redefinicao.
        logger.warning(
            "Senha temporaria definida para %s (must_change_password=1); use o fluxo de redefinicao.",
            ADMIN_SEED_USERNAME,
        )


def _set_session_cookie(response: Response, raw_token: str, request: Request) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
        path="/",
        domain=session_cookie_domain(),
        secure=session_cookie_secure(request),
        httponly=True,
        samesite="lax",
    )


def _clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        domain=session_cookie_domain(),
        secure=session_cookie_secure(request),
        httponly=True,
        samesite="lax",
    )


def _public_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "full_name": row.get("full_name"),
        "role": row["role"],
        "must_change_password": bool(row.get("must_change_password")),
        "intervals_configured": bool(row.get("intervals_athlete_id")) and bool(row.get("intervals_api_key_encrypted")),
        # F0.1 (migração 044): fuso em que o dashboard resolve "hoje" para o
        # usuário. Sempre válido na resposta (default se a coluna faltar).
        "timezone": normalize_timezone(row.get("timezone")),
        # Nutrição L0 (migração 074) + L2a: perfil antropométrico SÓ LEITURA
        # aqui (a escrita validada é `PUT /users/me`). `sex` e `activity_level`
        # saem no código canônico do motor (`domain/nutrition/anthropometrics`),
        # normalizados também na leitura enquanto houver valor antigo (a 077
        # converte o banco); desconhecido ou linha anterior à 074 -> `null`.
        "sex": anthro.sex_code(row.get("sex")),
        "birth_date": jsonable(row.get("birth_date")),
        "height_cm": jsonable(row.get("height_cm")),
        "activity_level": anthro.activity_level_code(row.get("activity_level")),
    }


def generate_temp_password(length: int = 16) -> str:
    """Gera uma senha temporária forte (usada pelo reset administrativo).

    Sorteio criptográfico (`secrets`), com pelo menos um caractere de cada
    classe (minúscula, maiúscula, dígito e símbolo). A senha crua só existe
    em memória e na resposta HTTP do reset - nunca é logada nem persistida
    em claro (o banco guarda só o hash bcrypt).
    """
    length = max(length, MIN_PASSWORD_LENGTH)
    groups = (string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%&*-_?")
    alphabet = "".join(groups)

    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if all(any(char in group for char in candidate) for group in groups):
            return candidate


def register_failed_login(user_id: int) -> bool:
    """Registra UMA tentativa de login malsucedida, de forma durável.

    Por que existe (dívida técnica corrigida aqui): o incremento de
    `failed_login_attempts` era feito DENTRO do `with transaction()` do
    login e logo em seguida o endpoint levantava `HTTPException`. O context
    manager trata qualquer exceção como falha e dá ROLLBACK, então o UPDATE
    era descartado: o contador nunca saía de 0, `locked_until` nunca era
    gravado e o bloqueio por tentativas era, na prática, inoperante (força
    bruta ilimitada). Aqui o registro roda em transação PRÓPRIA, que commita
    antes de o chamador levantar a exceção de erro.

    O incremento é feito por um UPDATE atômico (`failed_login_attempts + 1`
    no próprio SQL, não lido-e-reescrito em Python), para que requisições
    simultâneas não percam contagens e não consigam passar do limite.

    Semântica preservada da versão anterior: ao ATINGIR
    `MAX_FAILED_ATTEMPTS`, grava `locked_until` e ZERA o contador - assim,
    quando o lockout expira, o usuário volta com as tentativas cheias em vez
    de já nascer bloqueado na primeira tentativa seguinte.

    Ordem das atribuições do SET é intencional: no MySQL, uma atribuição
    posterior enxerga o valor JÁ atualizado pelas anteriores; `locked_until`
    vem primeiro justamente para ainda ler o `failed_login_attempts` antigo.

    Retorna True se a conta ficou bloqueada agora.
    """
    now = datetime.utcnow()
    locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET locked_until = CASE
                        WHEN failed_login_attempts + 1 >= %s THEN %s
                        ELSE locked_until
                    END,
                    failed_login_attempts = CASE
                        WHEN failed_login_attempts + 1 >= %s THEN 0
                        ELSE failed_login_attempts + 1
                    END
                WHERE id = %s
                """,
                (MAX_FAILED_ATTEMPTS, locked_until, MAX_FAILED_ATTEMPTS, user_id),
            )

            cur.execute("SELECT locked_until FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()

    current_lock = row.get("locked_until") if row else None
    return bool(current_lock and current_lock > now)


@router.post("/login")
def login(payload: LoginIn, request: Request, response: Response):
    # Garante o admin seed mesmo se o startup event de main.py não tiver
    # rodado ainda (ex.: primeira chamada logo após subir o container).
    ensure_admin_seed()

    # SEG-07: limite de falhas por IP (tabela compartilhada; ver login_throttle).
    ip = client_ip(request)
    if login_throttle.is_ip_blocked(ip):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=login_throttle.RATE_LIMITED_DETAIL)

    # Nenhum `raise` acontece dentro do `with transaction()` abaixo: a
    # decisão é acumulada em flags e a exceção só é levantada DEPOIS que a
    # transação fecha. Sem isso, qualquer escrita feita no caminho de erro
    # seria descartada pelo rollback do context manager (ver
    # `register_failed_login`).
    invalid_credentials = False
    wrong_password = False
    user = None
    raw_token = None

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (payload.username.strip(),))
            user = cur.fetchone()

            now = datetime.utcnow()

            if not user:
                _burn_password_check(payload.password)
                invalid_credentials = True
            else:
                locked_until = user.get("locked_until")
                if locked_until and locked_until > now:
                    _burn_password_check(payload.password)
                    invalid_credentials = True
                elif not user.get("is_active"):
                    _burn_password_check(payload.password)
                    invalid_credentials = True
                elif not verify_password(payload.password, user["password_hash"]):
                    wrong_password = True
                else:
                    raw_token = secrets.token_urlsafe(32)
                    token_hash = _hash_token(raw_token)
                    expires_at = now + timedelta(days=SESSION_TTL_DAYS)

                    cur.execute(
                        "INSERT INTO user_sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
                        (user["id"], token_hash, expires_at),
                    )

                    # Continua no MESMO commit da criação da sessão.
                    cur.execute(
                        "UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE id = %s",
                        (user["id"],),
                    )

    if invalid_credentials:
        login_throttle.record_failure(ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS_DETAIL)

    if wrong_password:
        # Commita ANTES de levantar a exceção - é o que torna o bloqueio real.
        register_failed_login(user["id"])
        login_throttle.record_failure(ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS_DETAIL)

    _set_session_cookie(response, raw_token, request)

    # Fase 3: o token cru não volta mais no corpo — a sessão passa a viver
    # exclusivamente no cookie httpOnly acima, inacessível a JS no navegador.
    return {"user": _public_user(user)}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    x_api_token: Optional[str] = Header(default=None),
):
    # Aceita tanto o header (compat: chamadas antigas/scripts) quanto o
    # cookie de sessão httpOnly, que é o que o frontend web envia desde a
    # Fase 3 (não manda mais X-API-Token para a própria sessão).
    raw_token = x_api_token or request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.")

    token_hash = _hash_token(raw_token)
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE user_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE token_hash = %s AND revoked_at IS NULL",
                (token_hash,),
            )

    _clear_session_cookie(response, request)
    return {"status": "ok"}


@router.get("/me")
def me(identity: Identity = Depends(get_current_identity)):
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (identity.user_id,))
            user = cur.fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    result = _public_user(user)
    result["source"] = identity.source
    # Feature flags que o frontend consulta (aditivo). `exercise_cards` =
    # geracao de cards ilustrados ligada neste ambiente
    # (`EXERCISE_CARD_GENERATION_ENABLED`); quem pode gerar continua sendo
    # decidido por `role` (admin).
    result["features"] = {"exercise_cards": exercise_cards_enabled()}
    return result


@router.post("/change-password")
def change_password(
    payload: ChangePasswordIn,
    request: Request,
    identity: Identity = Depends(get_current_identity),
):
    if identity.source != "session":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Troca de senha exige uma sessão de usuário autenticado via login (não token mestre/dispositivo).",
        )

    # Token da sessão atual (header tem prioridade, como em get_current_identity):
    # é a única sessão que sobrevive à troca (SEG-06).
    raw_token = request.headers.get("x-api-token") or request.cookies.get(SESSION_COOKIE_NAME) or ""
    current_token_hash = _hash_token(raw_token)

    wrong_current = False
    locked_until = None
    with transaction() as conn:
        with conn.cursor() as cur:
            # COD-11: so as colunas que a troca usa (nao traz o resto da linha do usuario).
            cur.execute("SELECT id, password_hash, locked_until FROM users WHERE id = %s", (identity.user_id,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="Usuário não encontrado.")

            current_lock = user.get("locked_until")
            if current_lock and current_lock > datetime.utcnow():
                # Bloqueada (por login ou por esta rota): nem confere a senha.
                # Sem isso o lockout não limitava nada aqui - o contador zera
                # ao bloquear e a rota seguia aceitando tentativas.
                locked_until = current_lock
            elif not verify_password(payload.current_password, user["password_hash"]):
                wrong_current = True
            else:
                new_hash = hash_password(payload.new_password)
                cur.execute(
                    "UPDATE users SET password_hash = %s, must_change_password = 0 WHERE id = %s",
                    (new_hash, identity.user_id),
                )
                # SEG-06: as demais sessões do usuário deixam de valer.
                cur.execute(
                    "UPDATE user_sessions SET revoked_at = CURRENT_TIMESTAMP "
                    "WHERE user_id = %s AND revoked_at IS NULL AND token_hash <> %s",
                    (identity.user_id, current_token_hash),
                )

    if locked_until is not None:
        return _account_locked_response(locked_until)

    if wrong_current:
        # Fora do `with transaction()` acima: precisa commitar antes de
        # responder. Conta no mesmo lockout do login (SEG-06).
        if register_failed_login(identity.user_id):
            return _account_locked_response(datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES))
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": WRONG_CURRENT_PASSWORD_DETAIL, "code": WRONG_CURRENT_PASSWORD_CODE},
        )

    return {"status": "ok"}


def _account_locked_response(locked_until: datetime) -> JSONResponse:
    """429 `{"detail", "code": "account_locked"}` + `Retry-After` (segundos
    até o fim do bloqueio). Nunca 401: a sessão continua válida."""
    retry_after = max(1, int((locked_until - datetime.utcnow()).total_seconds()))
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": ACCOUNT_LOCKED_DETAIL, "code": ACCOUNT_LOCKED_CODE},
        headers={"Retry-After": str(retry_after)},
    )

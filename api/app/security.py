import hashlib
import hmac
import logging
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from .db import transaction

# ---------------------------------------------------------------------------
# Cookie de sessão httpOnly (Fase 3: sessão do frontend deixa de viajar em
# localStorage/X-API-Token e passa a viajar num cookie httpOnly emitido pela
# API em POST /auth/login — ver auth.py). O nome e as flags do cookie ficam
# aqui, junto do restante da resolução de identidade, para que
# get_current_identity/resolve_identity e a emissão em auth.py usem
# exatamente a mesma configuração.
# ---------------------------------------------------------------------------

SESSION_COOKIE_NAME = "musclefit_session"

logger = logging.getLogger("fitness.security")


# ---------------------------------------------------------------------------
# M56: falha ao CONSULTAR a identidade nao e "nao autenticado".
#
# Antes, todo `except Exception` deste modulo devolvia None/False, e
# `resolve_identity` transformava isso num 401. Um erro de banco (Aurora
# voltando do auto-pause depois de esgotar o retry de `db_dataapi`, erro
# transitorio da RDS Data API, MySQL fora do ar em dev) virava "Token
# invalido" - e o frontend, corretamente, trata 401 como sessao encerrada e
# manda o usuario para o login. A sessao continuava valida no banco.
#
# Regra agora:
# - sessao/token inexistente, expirado, revogado ou usuario inativo -> 401
#   (a consulta RESPONDEU que nao ha identidade);
# - a consulta NAO respondeu (qualquer excecao) -> 503 + `Retry-After`, com
#   log de erro e stack (sem token, hash ou cookie).
#
# Nao ha classificacao de "transitorio x permanente" aqui de proposito: o
# retry do auto-pause ja acontece dentro de `db_dataapi` (so
# `DatabaseResumingException`, ver `_call_with_resume_retry`), e qualquer
# outra falha de consulta, transitoria ou nao, continua sendo "nao sei quem
# voce e" - nunca "voce nao esta logado". Repetir a pergunta e decisao do
# cliente (o frontend retenta GETs; ver `frontend/src/lib/api.ts`).
#
# Subclasse de HTTPException para valer em QUALQUER app FastAPI que use estas
# dependencias (inclusive os apps minimos dos testes), sem depender de um
# exception handler registrado em `main.py`.
# ---------------------------------------------------------------------------

AUTH_BACKEND_UNAVAILABLE_DETAIL = (
    "Não foi possível validar sua sessão agora (instabilidade temporária). Tente novamente em instantes."
)
AUTH_BACKEND_RETRY_AFTER_SECONDS = 5


class AuthBackendUnavailable(HTTPException):
    """503: a consulta de identidade falhou - a sessao NAO foi invalidada."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AUTH_BACKEND_UNAVAILABLE_DETAIL,
            headers={"Retry-After": str(AUTH_BACKEND_RETRY_AFTER_SECONDS)},
        )


def _auth_backend_unavailable(step: str, exc: BaseException) -> AuthBackendUnavailable:
    """Loga a falha (com stack) e devolve o 503 a ser levantado.

    So entram no log o passo e o tipo da excecao: nunca o token, o hash dele
    ou o cookie. A mensagem da excecao vem do driver (`DataApiError` traz o
    SQL com placeholders `:p0`, sem os valores dos parametros)."""
    logger.error(
        "auth: falha ao consultar o banco em %s (%s); respondendo 503 sem invalidar a sessao",
        step,
        type(exc).__name__,
        exc_info=exc,
        extra={"event": "auth_backend_unavailable", "step": step, "error_type": type(exc).__name__},
    )
    return AuthBackendUnavailable()


def session_cookie_domain() -> Optional[str]:
    """Domínio do cookie de sessão, configurável via SESSION_COOKIE_DOMAIN.

    Default `.musclefit.com.br` (cobre app.musclefit.com.br e
    api.musclefit.com.br, mesmo site para fins de SameSite=Lax). Em dev local
    sem domínio configurado, defina SESSION_COOKIE_DOMAIN="" para omitir o
    atributo Domain do cookie (necessário para funcionar em localhost/127.0.0.1).
    """
    domain = os.environ.get("SESSION_COOKIE_DOMAIN", ".musclefit.com.br").strip()
    return domain or None


def session_cookie_secure(request: Request) -> bool:
    """Decide a flag Secure do cookie de sessão.

    Sem env "ENV"/"ENVIRONMENT" pré-existente no projeto para basear essa
    decisão (checado em auth.py/security.py) — em vez disso, detecta https
    pela própria requisição: scheme direto (uvicorn com TLS) ou
    X-Forwarded-Proto (API Gateway/CloudFront na frente da API em produção).
    SESSION_COOKIE_SECURE, se definida, força o valor (útil para testes).
    """
    forced = os.environ.get("SESSION_COOKIE_SECURE")
    if forced is not None:
        return forced.strip().lower() in ("1", "true", "yes")

    if request.url.scheme == "https":
        return True

    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    return forwarded_proto.split(",")[0].strip().lower() == "https"


# M40: o `last_seen_at` do dispositivo e gravado no maximo uma vez a cada
# DEVICE_LAST_SEEN_THROTTLE_MINUTES. O relogio chama a API varias vezes por
# serie; gravar a cada requisicao seria uma escrita na Data API por chamada
# so para um campo de exibicao ("ultimo uso" na tela de Dispositivos).
DEVICE_LAST_SEEN_THROTTLE_MINUTES = 10


def _device_identity(x_api_token: str | None) -> Optional["Identity"]:
    """Identidade do DONO do dispositivo pareado cujo token e `x_api_token`.

    M40: antes todo token de dispositivo virava o admin; agora vale
    `devices.user_id` com o papel do proprio dono (mesma tabela/colunas que
    `_session_identity` le). O token cru nunca e persistido: comparacao por
    SHA-256.

    None = a consulta respondeu e nao ha dispositivo ativo (inexistente,
    revogado, `is_active = 0`) ou o dono esta inativo (-> 401). Falha ao
    consultar levanta `AuthBackendUnavailable` (503) - ver M56 no topo.

    `last_seen_at` so e regravado quando a ultima gravacao tem pelo menos
    DEVICE_LAST_SEEN_THROTTLE_MINUTES (ou nunca houve): a decisao vem do
    proprio SELECT, sem UPDATE nas demais chamadas."""
    if not x_api_token:
        return None

    token_hash = hashlib.sha256(x_api_token.encode("utf-8")).hexdigest()
    throttle_minutes = int(DEVICE_LAST_SEEN_THROTTLE_MINUTES)

    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT d.id AS device_id, d.user_id, u.role, u.is_active,
                           (d.last_seen_at IS NULL
                            OR d.last_seen_at <= NOW() - INTERVAL {throttle_minutes} MINUTE) AS needs_touch
                    FROM devices d
                    JOIN users u ON u.id = d.user_id
                    WHERE d.token_hash = %s AND d.is_active = 1 AND d.revoked_at IS NULL
                    LIMIT 1
                    """,
                    (token_hash,),
                )
                row = cur.fetchone()
                if not row or not row["is_active"]:
                    return None

                if row.get("needs_touch"):
                    cur.execute(
                        "UPDATE devices SET last_seen_at = CURRENT_TIMESTAMP WHERE id = %s",
                        (row["device_id"],),
                    )
                return Identity(
                    user_id=row["user_id"],
                    role=row["role"],
                    source="device",
                    device_id=row["device_id"],
                )
    except Exception as exc:  # noqa: BLE001 - falha de consulta vira 503, nunca 401
        raise _auth_backend_unavailable("device_token", exc) from exc


def device_token_matches(x_api_token: str | None) -> bool:
    """Verifica se o token pertence a um dispositivo pareado ativo de um
    usuario ativo (ver `_device_identity`).

    O token mestre e comparado ANTES desta funcao (`token_is_valid`/
    `resolve_identity`), entao ela nao participa da autenticacao por token
    mestre. M56: falha ao consultar o banco levanta `AuthBackendUnavailable`
    (503) em vez de responder "nao e dispositivo"."""
    return _device_identity(x_api_token) is not None


def master_token_matches(x_api_token: str | None, expected_master: str | None) -> bool:
    """Compara o token recebido com o token mestre em tempo constante (SEG-04).

    `hmac.compare_digest` em bytes: `==` em str sai no primeiro caractere
    diferente e vaza, pelo tempo de resposta, quanto do prefixo bateu. Vazio
    de um lado ou do outro nunca casa (API_TOKEN nao configurado nao vira
    "qualquer token vazio entra")."""
    if not x_api_token or not expected_master:
        return False
    return hmac.compare_digest(x_api_token.encode("utf-8"), expected_master.encode("utf-8"))


def token_is_valid(x_api_token: str | None, expected_master: str) -> bool:
    """Aceita o token mestre (API_TOKEN) ou um token de dispositivo pareado."""
    if master_token_matches(x_api_token, expected_master):
        return True
    return device_token_matches(x_api_token)


def require_token(x_api_token: str | None = Header(default=None)):
    expected = os.environ.get("API_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=500, detail="API_TOKEN não configurado no servidor.")
    if not token_is_valid(x_api_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.")
    return True


def require_master_token(x_api_token: str | None = Header(default=None)):
    """Exige exclusivamente o token mestre (gestão de dispositivos, pareamento)."""
    expected = os.environ.get("API_TOKEN", "")
    if not expected or expected == "change_api_token":
        raise HTTPException(status_code=500, detail="API_TOKEN não configurado no servidor.")
    if not master_token_matches(x_api_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.")
    return True


# ---------------------------------------------------------------------------
# Multiusuário (Parte 1): resolução de identidade.
#
# MCP/Telegram autenticam com o token mestre único (atribuído ao usuário
# admin). M40: o token de dispositivo pareado (relógio Zepp) resolve para o
# DONO do dispositivo (`devices.user_id`), com o papel dele - não mais para o
# admin. Ver `_device_identity`.
# ---------------------------------------------------------------------------


@dataclass
class Identity:
    user_id: int
    role: str  # 'admin' | 'regular'
    source: str  # 'master_token' | 'device' | 'session'
    # SEG-06: usuario com troca de senha obrigatoria pendente (so sessoes).
    must_change_password: bool = False
    # M40: id do dispositivo pareado (so quando source == 'device').
    device_id: Optional[int] = None


def _admin_user_id() -> Optional[int]:
    """Busca o id do usuário admin seed ('doug'). Assume que existe exatamente
    um usuário com role='admin'. Não cacheia entre chamadas: não é crítico de
    performance nesta fase, e evita id desatualizado em cenários de teste que
    recriam o usuário admin.

    None = a consulta respondeu e não há admin ativo. Falha ao consultar
    levanta `AuthBackendUnavailable` (503) - ver M56 no topo do módulo."""
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id ASC LIMIT 1"
                )
                row = cur.fetchone()
                return row["id"] if row else None
    except Exception as exc:  # noqa: BLE001 - falha de consulta vira 503, nunca 401
        raise _auth_backend_unavailable("admin_user", exc) from exc


def _session_identity(x_api_token: str) -> Optional["Identity"]:
    """Identidade da sessão de usuário cujo token é `x_api_token`.

    None = a consulta respondeu e a sessão não existe, expirou, foi revogada
    ou o usuário está inativo (-> 401 em `resolve_identity`). Falha ao
    consultar levanta `AuthBackendUnavailable` (503): a sessão pode estar
    perfeitamente válida e o cliente NÃO deve descartá-la."""
    token_hash = hashlib.sha256(x_api_token.encode("utf-8")).hexdigest()

    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.id AS session_id, s.user_id, u.role, u.is_active, u.must_change_password
                    FROM user_sessions s
                    JOIN users u ON u.id = s.user_id
                    WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > NOW()
                    LIMIT 1
                    """,
                    (token_hash,),
                )
                row = cur.fetchone()
                if not row or not row["is_active"]:
                    return None

                cur.execute(
                    "UPDATE user_sessions SET last_seen_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (row["session_id"],),
                )
                return Identity(
                    user_id=row["user_id"],
                    role=row["role"],
                    source="session",
                    must_change_password=bool(row.get("must_change_password")),
                )
    except Exception as exc:  # noqa: BLE001 - falha de consulta vira 503, nunca 401
        raise _auth_backend_unavailable("session", exc) from exc


def resolve_identity(x_api_token: Optional[str]) -> Identity:
    """Resolve o token recebido em X-API-Token para uma identidade, na ordem:
    token mestre -> token de dispositivo pareado -> sessão de usuário válida.
    Levanta 401 se nenhuma dessas bater, e 503 (`AuthBackendUnavailable`) se
    não foi possível consultar o banco para decidir."""
    if not x_api_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.")

    expected_master = os.environ.get("API_TOKEN", "")
    if master_token_matches(x_api_token, expected_master):
        admin_id = _admin_user_id()
        if admin_id is not None:
            return Identity(user_id=admin_id, role="admin", source="master_token")

    device_identity = _device_identity(x_api_token)
    if device_identity is not None:
        return device_identity

    session_identity = _session_identity(x_api_token)
    if session_identity is not None:
        return session_identity

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.")


# SEG-06: enquanto `must_change_password` esta ativo, a API so atende estas
# rotas (o suficiente para o usuario trocar a senha e sair). Todo o resto
# responde 403 com `code = must_change_password`.
MUST_CHANGE_PASSWORD_CODE = "must_change_password"
MUST_CHANGE_PASSWORD_DETAIL = "Troca de senha obrigatória antes de continuar."
_MUST_CHANGE_EXEMPT = frozenset(
    {
        ("GET", "/auth/me"),
        ("POST", "/auth/change-password"),
        ("POST", "/auth/logout"),
        ("GET", "/users/me"),
        ("GET", "/health"),
    }
)


class MustChangePasswordRequired(HTTPException):
    """403 com `code = must_change_password`. O corpo `{"detail", "code"}` e
    montado pelo handler registrado em `main.py`; sem ele (apps minimos de
    teste) continua um 403 com `detail` em texto."""

    code = MUST_CHANGE_PASSWORD_CODE

    def __init__(self) -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=MUST_CHANGE_PASSWORD_DETAIL)


def must_change_password_handler(_request: Request, exc: MustChangePasswordRequired) -> JSONResponse:
    """Handler de `MustChangePasswordRequired`, registrado em `main.py` e em
    `main_engines.py`: corpo `{"detail": ..., "code": "must_change_password"}`
    (o frontend reconhece o `code` e leva o usuario a troca de senha)."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code})


def _is_must_change_exempt(request: Request) -> bool:
    path = request.scope.get("path", "") or ""
    root = request.scope.get("root_path", "") or ""
    if root and path.startswith(root):
        path = path[len(root):]
    path = path.rstrip("/") or "/"
    return (request.method.upper(), path) in _MUST_CHANGE_EXEMPT


def resolve_identity_from_credentials(
    x_api_token: Optional[str],
    musclefit_session: Optional[str] = None,
) -> Identity:
    """Header X-API-Token ou, na falta dele, o cookie de sessao -> identidade.

    COD-03: substitui o `_require_identity` que existia em
    `exercise_catalog_admin`, `exercise_mapping_catalog`, `gyms` e
    `equipment_exercises` (que o importam com esse nome; `muscle_volume_classes`
    reusa o de `exercise_mapping_catalog`).

    SEG-06: aplica a MESMA trava de `get_current_identity` - sessao com troca
    de senha pendente recebe 403 `must_change_password`. Sem o `Request` aqui,
    nao ha checagem de `_MUST_CHANGE_EXEMPT`: nenhuma rota desses routers esta
    na lista (troca de senha, logout, /auth/me, /users/me e /health usam
    `get_current_identity` ou nao exigem identidade), o que e travado por
    teste. Token mestre e dispositivo nunca tem a flag e seguem iguais."""
    identity = resolve_identity(x_api_token or musclefit_session)
    if identity.must_change_password:
        raise MustChangePasswordRequired()
    return identity


def get_current_identity(
    request: Request,
    x_api_token: Optional[str] = Header(default=None),
    musclefit_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Identity:
    """Resolve a identidade a partir do header X-API-Token (token mestre,
    dispositivo pareado ou sessão) quando presente; senão cai para o cookie
    httpOnly de sessão (`musclefit_session`), usado pelo frontend web desde a
    Fase 3. O header continua tendo prioridade e continua funcionando
    exatamente como antes para MCP/Telegram e o app Zepp — nenhum dos dois
    fluxos envia o cookie.

    SEG-06: sessão de usuário com troca de senha pendente só acessa as rotas
    de `_MUST_CHANGE_EXEMPT`; as demais recebem 403 `must_change_password`."""
    identity = resolve_identity(x_api_token or musclefit_session)
    if identity.must_change_password and not _is_must_change_exempt(request):
        raise MustChangePasswordRequired()
    return identity


def require_admin(identity: Identity = Depends(get_current_identity)) -> Identity:
    if identity.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso restrito a administradores.")
    return identity


# ---------------------------------------------------------------------------
# Guard aditivo (feature "Modelos de Ficha de Treino Personalizados"):
# Personal Trainer é um papel amplo, análogo a admin mas escopado à seção de
# modelos de treino (sem vínculo formal PT<->aluno nesta fase — vê/atribui
# para qualquer usuário). NÃO modifica require_admin acima, para não afetar
# nenhum endpoint hoje admin-only puro.
# ---------------------------------------------------------------------------


ADMIN_OR_PT_ROLES = ("admin", "personal_trainer")


def is_admin_or_pt(identity: Identity) -> bool:
    """Mesma regra de `require_admin_or_pt`, para quem só precisa SABER (ex.:
    a tela da recomendação de cardio mostra "Aplicar no Modelo" só a quem
    edita o Modelo, M98 C3b)."""
    return identity.role in ADMIN_OR_PT_ROLES


def require_admin_or_pt(identity: Identity = Depends(get_current_identity)) -> Identity:
    if not is_admin_or_pt(identity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores e personal trainers.",
        )
    return identity

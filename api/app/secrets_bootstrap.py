"""Carrega segredos do Secrets Manager para variaveis de ambiente no cold
start da Lambda `musclefit-api-core`.

O codigo de aplicacao portado (`openai_client.py`) le `OPENAI_API_KEY`
diretamente de `os.environ`, exatamente como no ambiente on-premise (onde a
variavel vinha do `.env`/docker compose). Em vez de reescrever esse call-site,
resolvemos o segredo uma vez por cold start e populamos a mesma variavel de
ambiente - menor mudanca possivel no codigo de negocio portado.

`OPENAI_API_KEY_SECRET_ARN` (ARN do secret `musclefit/openai-api-key`) e
opcional: se ausente (ex.: execucao local/testes), este modulo e um no-op e
`openai_client.call_openai` cai no proprio erro de "OPENAI_API_KEY nao
configurada" que ja existia antes desta migracao.
"""

from __future__ import annotations

import json
import os

from .observability import logger as _events_logger

# Secret com ARN definido que nao virou variavel de ambiente (GetSecretValue
# falhou ou veio sem SecretString). O cold start SEGUE: o call-site responde
# "nao configurada" (OpenAI) ou 503 (Turnstile, USDA). Campos: o NOME da env
# do ARN, o NOME da env de destino, `error_code` (codigo AWS, quando houver) e
# `error_class`. **Nunca** o valor, a mensagem da excecao nem o ARN.
EVENT_SECRET_LOAD_FAILED = "secrets_bootstrap.load_failed"


# (env var carregada pela app, env var que aponta para o ARN do secret)
_SECRETS = [
    ("OPENAI_API_KEY", "OPENAI_API_KEY_SECRET_ARN"),
    ("API_TOKEN", "API_TOKEN_SECRET_ARN"),
    ("SECRET_ENCRYPTION_KEY", "SECRET_ENCRYPTION_KEY_SECRET_ARN"),
    # M5: secret do Cloudflare Turnstile (`musclefit/turnstile-secret-key`),
    # lido por `app/captcha.py`. Em DEV o `.env` define TURNSTILE_SECRET_KEY
    # direto (chave de teste da Cloudflare) e o ARN nao existe: no-op aqui.
    ("TURNSTILE_SECRET_KEY", "TURNSTILE_SECRET_KEY_SECRET_ARN"),
    # Nutricao: chave do USDA FoodData Central (`musclefit/usda-api-key`), so
    # na api-core (infra/lambda_api_core.tf). Em DEV vem do `.env` como
    # USDA_API_KEY e o ARN nao existe: no-op aqui. Sem ARN, ou com o secret
    # ilegivel, a variavel fica ausente e o cold start segue (best-effort
    # abaixo); quem consome responde 503 (plano de nutricao, secao 8.1).
    ("USDA_API_KEY", "USDA_API_KEY_SECRET_ARN"),
]


def _warn_load_failed(arn_env: str, env_name: str, *, error_class: str, error_code: str | None = None) -> None:
    """Uma linha JSON no formato de `observability.log_event`, mas em WARNING.

    `load_secrets()` roda no import do entrypoint, ANTES de
    `configure_logging()` (app/logging_config.py): ate la os loggers da
    aplicacao herdam WARNING do raiz e um INFO (o nivel do `log_event`) seria
    descartado. WARNING sai pelo handler do runtime (awslambdaric) ou pelo
    `logging.lastResort` (uvicorn atras do Lambda Web Adapter), e o CloudWatch
    recebe a linha. Nunca levanta: log nao derruba o cold start."""
    try:
        payload = {"event": EVENT_SECRET_LOAD_FAILED, "arn_env": arn_env, "env": env_name, "error_class": error_class}
        if error_code:
            payload["error_code"] = error_code
        _events_logger.warning(json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - log nunca quebra o chamador
        pass


def load_secrets() -> None:
    pending = [
        (env_name, arn_env, arn)
        for env_name, arn_env in _SECRETS
        if (arn := os.environ.get(arn_env)) and not os.environ.get(env_name)
    ]
    if not pending:
        return

    import boto3

    region_name = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "sa-east-1"
    client = boto3.client("secretsmanager", region_name=region_name)

    for env_name, arn_env, secret_arn in pending:
        try:
            resp = client.get_secret_value(SecretId=secret_arn)
        except Exception as exc:
            # Best-effort: se um secret especifico nao puder ser lido
            # (permissao, secret apagado etc), registra o aviso e deixa o
            # call-site original reportar a variavel como "nao configurada",
            # do jeito que ja fazia antes desta migracao. So o codigo AWS e a
            # classe do erro: a mensagem da excecao nao entra no log.
            response = getattr(exc, "response", None)
            error_code = (response.get("Error") or {}).get("Code") if isinstance(response, dict) else None
            _warn_load_failed(arn_env, env_name, error_class=type(exc).__name__, error_code=error_code)
            continue
        value = resp.get("SecretString")
        if value:
            os.environ[env_name] = value
        else:
            _warn_load_failed(arn_env, env_name, error_class="EmptySecretString")


# SEG-19: tokens-padrao do ambiente local. Valem so com DB_BACKEND=pymysql
# (DEV); em qualquer outro backend (Aurora/Data API = AWS) a API recusa subir.
KNOWN_DEFAULT_API_TOKENS = frozenset({"dev-local-token", "change_api_token"})


class InsecureDefaultSecretError(RuntimeError):
    """Segredo-padrao conhecido em ambiente que nao e o DEV local."""


def assert_no_default_secrets() -> None:
    """Recusa subir com API_TOKEN de exemplo fora do DEV local.

    Chamada logo apos `load_secrets()`, entao um token vindo do Secrets
    Manager ja foi resolvido. DEV (`DB_BACKEND=pymysql`) segue livre."""
    if os.environ.get("DB_BACKEND", "data-api") == "pymysql":
        return
    if os.environ.get("API_TOKEN", "") in KNOWN_DEFAULT_API_TOKENS:
        raise InsecureDefaultSecretError(
            "API_TOKEN com valor padrao conhecido fora do DEV local (DB_BACKEND != pymysql). "
            "Defina um token aleatorio: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

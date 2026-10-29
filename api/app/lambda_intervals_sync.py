"""Entrypoint da Lambda `musclefit-intervals-sync` (melhoria M2).

Mesma imagem das demais Lambdas sem servidor HTTP (`api/Dockerfile.ai`,
ENTRYPOINT awslambdaric), so trocando o `image_config.command` - o mesmo
padrao de plan-generator/cardio-agent/migrations. Infra em
`infra/lambda_intervals_sync.tf`: uma regra do EventBridge dispara esta
funcao uma vez por dia.

Evento (todos os campos opcionais):
    {"days": 30, "user_ids": [1, 2], "dry_run": false}

- `days`   janela em dias (default `INTERVALS_SYNC_DAYS`, ou 30);
- `user_ids` restringe a execucao (troubleshooting); sem o campo, roda para
  TODOS os usuarios com Intervals.icu configurado - e so para eles;
- `dry_run` faz todo o trabalho e da rollback (default false; a regra
  agendada nao manda o campo).

Falha de um usuario NAO falha a invocacao: vai para `failures` no retorno e
para o evento `intervals_sync.user_failed`. A invocacao so termina com
FunctionError se nem a lista de usuarios puder ser lida - ai a regra do
EventBridge tenta de novo, e o alarme de erro da funcao dispara.
"""

from __future__ import annotations

import json
import logging
import os

# Igual a lambda_plan_generator/lambda_cardio_agent: resolver segredos ANTES
# de importar modulo de negocio (a chave de criptografia dos segredos do
# usuario e lida de SECRET_ENCRYPTION_KEY_SECRET_ARN).
from .secrets_bootstrap import load_secrets

load_secrets()

from . import intervals_sync  # noqa: E402
from .logging_config import configure_logging  # noqa: E402

logger = logging.getLogger("musclefit.lambda_intervals_sync")


class IntervalsSyncEventError(Exception):
    """Evento malformado - falha explicita, sem adivinhar o que foi pedido."""


def _guard_backend_in_lambda() -> None:
    # Regra anti-vazamento dev/prod (mesma de app/migrate.py): dentro de uma
    # Lambda real o backend TEM que ser o Data API.
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") and os.environ.get("DB_BACKEND", "data-api") != "data-api":
        raise IntervalsSyncEventError("DB_BACKEND diferente de data-api dentro de uma Lambda; recusando executar.")


def _user_ids(raw):
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or not raw:
        raise IntervalsSyncEventError(f"user_ids invalido: {raw!r} (esperado lista nao vazia de inteiros)")
    try:
        return [int(value) for value in raw]
    except (TypeError, ValueError) as exc:
        raise IntervalsSyncEventError(f"user_ids invalido: {raw!r} (esperado lista de inteiros)") from exc


def _days(raw):
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise IntervalsSyncEventError(f"days invalido: {raw!r} (esperado inteiro)") from exc


def _dry_run(raw) -> bool:
    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise IntervalsSyncEventError(f"dry_run invalido: {raw!r} (esperado true ou false)")
    return raw


def handler(event, context) -> dict:
    configure_logging()
    logging.getLogger().setLevel(logging.INFO)
    _guard_backend_in_lambda()

    payload = event if isinstance(event, dict) else {}
    report = intervals_sync.sync_users(
        _user_ids(payload.get("user_ids")),
        days=_days(payload.get("days")),
        dry_run=_dry_run(payload.get("dry_run")),
    )
    result = report.to_dict()
    logger.info("resultado: %s", json.dumps(result, default=str))
    return result

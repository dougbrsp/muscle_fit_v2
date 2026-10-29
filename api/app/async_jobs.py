"""Padrao assincrono 202 + job_id + polling, usado pelos dois endpoints de IA
de longa duracao (`POST /workout-plans/generate`, `POST
/cardio-recommendations/generate`) - ver docs/aws-migration-plan.md, secao
1.2 e Fase 3.

Estado do job guardado na tabela DynamoDB `musclefit-async-jobs` (nome via
env var `ASYNC_JOBS_TABLE`, criada pelo Terraform em `infra/dynamodb.tf`):
- `job_id` (PK, string, uuid4)
- `job_type` ("plan-generator" | "cardio-agent")
- `owner_user_id` (numero) - usuario autenticado que criou o job (M65).
  `GET /jobs/{job_id}` so devolve o job para esse usuario; job sem dono
  (criado antes do M65) ou de outro usuario e tratado como inexistente (404)
- `status` ("pending" | "done" | "failed")
- `request` (JSON string, o payload que originou o job - so para auditoria/debug)
- `result` (JSON string, presente quando status="done")
- `error` (string, presente quando status="failed")
- `error_code` (string, opcional; `worker_lost` quando o job foi fechado por
  prazo - ver `WORKER_LOST_ERROR_CODE`)
- `created_at` (epoch segundos)
- `started_at` (epoch segundos, opcional; gravado pelo handler ao REIVINDICAR
  o job - `claim_job`, M81. Interno: `get_job` nao o expoe)
- `finished_at` (epoch segundos, opcional; gravado junto com o estado terminal)
- `ttl` (epoch segundos, TTL do DynamoDB - expira jobs antigos automaticamente)

`musclefit-api-core` cria o job (`create_job`) e dispara a Lambda de IA
correspondente de forma assincrona (`invoke_async`, `InvocationType=Event`).
O handler da Lambda (`musclefit-plan-generator`/`musclefit-cardio-agent`/
`musclefit-workout-ai`) primeiro reivindica o job (`claim_job`; se nao
conseguir, nao chama nenhuma IA) e ao terminar marca o job como
concluido/falho (`mark_done`/`mark_failed` - updates CONDICIONAIS: so gravam
se o job ainda esta em andamento). `GET /jobs/{job_id}` (`app/jobs.py`) le o
estado atual (`get_job`) e fecha como `failed`/`worker_lost` o job em
andamento alem do prazo.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from decimal import Decimal
from typing import Any, Optional

# Dono puro dos job types da nutricao (L5a/L5b): a guarda `cota_ia_nutricao`
# recusa o literal fora dele.
from .domain.nutrition import ai_usage

logger = logging.getLogger("musclefit.async_jobs")

_JOB_TTL_SECONDS = 60 * 60 * 6  # 6h - tempo generoso para o usuario dar poll, sem acumular jobs para sempre

# ---------------------------------------------------------------------------
# M73: job orfao (`worker_lost`)
# ---------------------------------------------------------------------------
# Os handlers das Lambdas de IA capturam excecao e gravam `failed`, mas quando
# a Lambda MORRE (timeout, OOM, crash de init, falha do proprio `mark_failed`)
# o job fica `pending` ate o TTL. Desde o M70 (`maximum_retry_attempts = 0`,
# infra/lambda_async_invoke_config.tf) nao ha reentrega por erro: passado o
# timeout da Lambda que executa o job, nenhum resultado vai chegar. `get_job`
# entao fecha o job como `failed`/`worker_lost` (e persiste, com update
# condicional). A excecao - entrega atrasada pela fila de throttling ou
# duplicada - e tratada pelo M81, abaixo: o resultado tardio e descartado.
#
# FONTE UNICA dos timeouts (segundos) - espelho de `timeout` em infra/*.tf;
# tests/unit/test_m73_async_jobs_worker_lost.py le os .tf e falha se divergir.
LAMBDA_TIMEOUT_SECONDS: dict[str, int] = {
    "musclefit-plan-generator": 180,  # infra/lambda_ai_generators.tf
    "musclefit-cardio-agent": 120,  # infra/lambda_ai_generators.tf
    "musclefit-workout-ai": 60,  # infra/lambda_workout_ai.tf
}

# job_type -> Lambda que o executa (a mesma escolhida em `invoke_async` pelo
# chamador). Literais aqui para nao importar os modulos de rota (ciclo); o
# teste confere contra as constantes JOB_TYPE* de cada modulo.
JOB_TYPE_LAMBDA: dict[str, str] = {
    "plan-generator": "musclefit-plan-generator",  # workout_plans.py
    "periodization": "musclefit-plan-generator",  # workout_plan_templates.py
    "exercise-card": "musclefit-plan-generator",  # exercise_card_jobs.py
    "exercise-description": "musclefit-plan-generator",  # exercise_description_autogen.py
    "cardio-agent": "musclefit-cardio-agent",  # cardio_recommendations.py
    "insights": "musclefit-workout-ai",  # main.py
    "workouts_analysis": "musclefit-workout-ai",  # workouts_analysis.py
    "workout-ai-evaluate-set": "musclefit-workout-ai",  # main.py (Modo Treino)
    "workout-ai-exercise-summary": "musclefit-workout-ai",  # main.py (Modo Treino)
    "workout-ai-activity-summary": "musclefit-workout-ai",  # main.py (Modo Treino)
    ai_usage.MEAL_PARSE_JOB_TYPE: "musclefit-workout-ai",  # nutrition_meal_parse.py (L5b, decisao E8)
    # Nutricao: o job type e do dono (`domain/nutrition/ai_usage`, puro); a Lambda
    # e a da decisao E8 do plano (imagem na plan-generator).
    ai_usage.LABEL_PHOTO_JOB_TYPE: "musclefit-plan-generator",  # nutrition_label_photo.py
}

# Folga sobre o timeout da Lambda: cold start + atraso de entrega do invoke
# assincrono + a escrita final no DynamoDB.
WORKER_LOST_GRACE_SECONDS = 60

WORKER_LOST_ERROR_CODE = "worker_lost"
WORKER_LOST_MESSAGE = (
    "A geração não respondeu dentro do prazo e foi interrompida. Tente novamente."
)

_IN_FLIGHT_STATUSES = ("pending", "running")

# ---------------------------------------------------------------------------
# M81: entrega atrasada ou duplicada do invoke assincrono
# ---------------------------------------------------------------------------
# `maximum_retry_attempts = 0` NAO cobre a fila de throttling: um invoke acima
# da concorrencia reservada da funcao (`reserved_concurrent_executions`, #438)
# fica retido pela AWS e e reentregue por ate 6 h (`maximum_event_age`) SEM a
# funcao ter rodado. E o invoke assincrono e at-least-once: a mesma entrega
# pode chegar duas vezes. Nos dois casos o handler pode comecar DEPOIS de
# `get_job` ter fechado o job como `worker_lost` (o usuario ja tentou de
# novo) - e, sem protecao, chamaria a OpenAI (cobranca) e sobrescreveria o
# `failed` com um `done` que ninguem le. Decisao do M81 (fecha a "decisao
# aberta" do M73, que deixava o resultado atrasado vencer):
# - o handler REIVINDICA o job antes de chamar qualquer IA (`claim_job`:
#   grava `started_at` so se o job ainda esta `pending` e nunca foi
#   iniciado). Job ja fechado ou ja reivindicado -> nada roda, nada e cobrado;
# - `mark_done`/`mark_failed` so gravam se o job ainda esta em andamento
#   (`#status IN (:pending, :running)`); resultado atrasado e descartado com
#   warning no log e nunca sobrescreve o `worker_lost`;
# - o prazo do `worker_lost` conta a partir de `started_at` quando existe: o
#   job que esperou na fila ganha o timeout inteiro a partir do inicio real,
#   e o poll nao fecha como perdido um worker que esta de fato rodando.
# O frontend so conhece `pending | done | failed`: o job reivindicado segue
# `pending` (nenhum status novo visivel).

_IN_FLIGHT_CONDITION = "#status IN (:pending, :running)"
_CLAIM_CONDITION = "#status = :pending AND attribute_not_exists(started_at)"


def worker_lost_deadline_seconds(job_type: Optional[str]) -> Optional[int]:
    """Idade (s) a partir da qual um job ainda `pending`/`running` e dado como
    perdido: timeout da Lambda que o executa + `WORKER_LOST_GRACE_SECONDS`.
    `None` para job_type desconhecido (sem prazo - fica como antes)."""
    function = JOB_TYPE_LAMBDA.get(job_type or "")
    if function is None:
        return None
    return LAMBDA_TIMEOUT_SECONDS[function] + WORKER_LOST_GRACE_SECONDS


def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "sa-east-1"


def _table():
    import boto3

    table_name = os.environ["ASYNC_JOBS_TABLE"]
    return boto3.resource("dynamodb", region_name=_region()).Table(table_name)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def create_job(job_type: str, request: Optional[dict[str, Any]] = None, *, owner_user_id: int) -> str:
    """Cria o job `pending`. `owner_user_id` e obrigatorio (M65): e o unico
    usuario que `GET /jobs/{job_id}` deixa ler o job."""
    owner = _owner_id(owner_user_id)
    if owner is None:
        raise ValueError("create_job exige owner_user_id (M65)")
    job_id = str(uuid.uuid4())
    now = int(time.time())
    item: dict[str, Any] = {
        "job_id": job_id,
        "job_type": job_type,
        "owner_user_id": owner,
        "status": "pending",
        "created_at": now,
        "ttl": now + _JOB_TTL_SECONDS,
    }
    if request is not None:
        item["request"] = _dumps(request)
    _table().put_item(Item=item)
    return job_id


def _is_condition_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", None) or {}
    return response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _update_if_in_flight(
    job_id: str, *, update_expression: str, names: dict[str, str], values: dict[str, Any]
) -> bool:
    """Update condicional: so grava se o job ainda esta `pending`/`running`.
    Devolve False quando a condicao falhou (job ja terminal); qualquer outro
    erro do DynamoDB propaga."""
    from botocore.exceptions import ClientError

    try:
        _table().update_item(
            Key={"job_id": job_id},
            UpdateExpression=update_expression,
            ConditionExpression=_IN_FLIGHT_CONDITION,
            ExpressionAttributeNames={"#status": "status", **names},
            ExpressionAttributeValues={
                **values,
                ":pending": _IN_FLIGHT_STATUSES[0],
                ":running": _IN_FLIGHT_STATUSES[1],
            },
        )
    except ClientError as exc:
        if _is_condition_failure(exc):
            return False
        raise
    return True


def claim_job(job_id: str, now: Optional[int] = None) -> bool:
    """Reivindica o job para ESTA execucao do handler (M81): grava `started_at`
    so se o job ainda esta `pending` e nunca foi iniciado (update condicional).

    Devolve False - e o handler entao NAO chama nenhuma IA - quando:
    - o job ja foi fechado (`failed`/`worker_lost` pelo poll de `get_job`,
      ou `done`/`failed` por outra execucao). Acontece com a fila de
      throttling do invoke assincrono: acima da concorrencia reservada a AWS
      segura o evento e o reentrega por ate 6 h sem a funcao rodar
      (`maximum_retry_attempts = 0` nao cobre esse caso); enquanto isso o job
      passou do prazo e o usuario ja tentou de novo;
    - o job ja foi reivindicado por outra entrega do MESMO evento (invoke
      assincrono e at-least-once): `started_at` ja existe.
    Qualquer outro erro do DynamoDB propaga."""
    from botocore.exceptions import ClientError

    try:
        _table().update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET started_at = :started_at",
            ConditionExpression=_CLAIM_CONDITION,
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":started_at": int(now if now is not None else time.time()),
                ":pending": _IN_FLIGHT_STATUSES[0],
            },
        )
    except ClientError as exc:
        if _is_condition_failure(exc):
            return False
        raise
    return True


def mark_done(job_id: str, result: Any) -> bool:
    """Grava `done` + `result` SO se o job ainda esta em andamento (M81).
    Devolve False (com warning no log) se ele ja foi fechado - `worker_lost`
    pelo poll ou entrega duplicada: o resultado e descartado, ninguem o leria."""
    done = _update_if_in_flight(
        job_id,
        update_expression="SET #status = :status, #result = :result, finished_at = :finished_at",
        names={"#result": "result"},
        values={":status": "done", ":result": _dumps(result), ":finished_at": int(time.time())},
    )
    if not done:
        logger.warning("Resultado descartado: job %s já estava fechado (worker_lost ou duplicado)", job_id)
    return done


def mark_failed(job_id: str, error: str) -> bool:
    """Grava `failed` + `error` SO se o job ainda esta em andamento (M81);
    mesma regra de `mark_done`. Devolve False se o job ja estava fechado."""
    failed = _update_if_in_flight(
        job_id,
        update_expression="SET #status = :status, #error = :error, finished_at = :finished_at",
        names={"#error": "error"},
        values={":status": "failed", ":error": error, ":finished_at": int(time.time())},
    )
    if not failed:
        logger.warning("Resultado descartado: job %s já estava fechado (worker_lost ou duplicado)", job_id)
    return failed


def mark_worker_lost(job_id: str, now: Optional[int] = None) -> bool:
    """Fecha o job como `failed`/`worker_lost` SO se ele ainda estiver
    `pending`/`running` (update condicional): um resultado que chegou no
    limite nao e sobrescrito. Devolve True se esta chamada fechou o job."""
    return _update_if_in_flight(
        job_id,
        update_expression="SET #status = :failed, #error = :error, error_code = :code, finished_at = :finished_at",
        names={"#error": "error"},
        values={
            ":failed": "failed",
            ":error": WORKER_LOST_MESSAGE,
            ":code": WORKER_LOST_ERROR_CODE,
            ":finished_at": int(now if now is not None else time.time()),
        },
    )


def worker_lost_reference_seconds(item: dict[str, Any]) -> Optional[int]:
    """Instante (epoch s) de onde o prazo do `worker_lost` conta: `started_at`
    quando o handler ja reivindicou o job (M81 - o job que esperou na fila de
    throttling ganha o timeout inteiro a partir do inicio real), senao
    `created_at`. `None` sem nenhum dos dois."""
    started_at = _to_int(item.get("started_at"))
    if started_at is not None:
        return started_at
    return _to_int(item.get("created_at"))


def _is_worker_lost(item: dict[str, Any], now: int) -> bool:
    if item.get("status") not in _IN_FLIGHT_STATUSES:
        return False
    deadline = worker_lost_deadline_seconds(item.get("job_type"))
    reference = worker_lost_reference_seconds(item)
    if deadline is None or reference is None:
        return False
    return now - reference > deadline


def _owner_id(value: Any) -> Optional[int]:
    """`owner_user_id` normalizado para int (o DynamoDB devolve `Decimal`).
    `None`/bool/valor nao inteiro -> `None` (sem dono valido)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    if number != number.to_integral_value():
        return None
    return int(number)


def get_job(job_id: str, now: Optional[int] = None, *, owner_user_id: int) -> Optional[dict[str, Any]]:
    """Estado atual do job, SO para o dono (M65): job de outro usuario ou sem
    `owner_user_id` (criado antes do M65) volta `None` - o chamador responde
    404, sem revelar que o id existe. A checagem vem ANTES do fechamento por
    worker_lost, entao so o dono dispara essa escrita.

    Job `pending`/`running` alem do prazo (`worker_lost_deadline_seconds`)
    volta como `failed`/`worker_lost` e o estado terminal e persistido (ver
    `mark_worker_lost`)."""
    table = _table()
    resp = table.get_item(Key={"job_id": job_id})
    item = resp.get("Item")
    if not item:
        return None
    requester = _owner_id(owner_user_id)
    if requester is None or _owner_id(item.get("owner_user_id")) != requester:
        return None

    current = int(now if now is not None else time.time())
    if _is_worker_lost(item, current):
        if not mark_worker_lost(job_id, now=current):
            # Perdeu a corrida para o worker (mark_done/mark_failed no limite):
            # vale o que ele gravou.
            item = table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item") or item
        else:
            item = {
                **item,
                "status": "failed",
                "error": WORKER_LOST_MESSAGE,
                "error_code": WORKER_LOST_ERROR_CODE,
                "finished_at": current,
            }

    result: dict[str, Any] = {
        "job_id": item.get("job_id"),
        "job_type": item.get("job_type"),
        "status": item.get("status"),
        "created_at": _to_int(item.get("created_at")),
        "finished_at": _to_int(item.get("finished_at")),
    }
    if item.get("status") == "done" and item.get("result"):
        try:
            result["result"] = json.loads(item["result"])
        except (TypeError, ValueError):
            result["result"] = None
    if item.get("status") == "failed":
        result["error"] = item.get("error")
        if item.get("error_code"):
            result["error_code"] = item.get("error_code")
    return result


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return int(value)
    return int(value)


def invoke_async(function_name_env_var: str, payload: dict[str, Any]) -> None:
    """Dispara uma Lambda de IA (`musclefit-plan-generator`/`musclefit-cardio-agent`)
    de forma assincrona (fire-and-forget - `InvocationType=Event`), sem
    esperar o resultado. `function_name_env_var` e o NOME da env var em
    `musclefit-api-core` que guarda o nome real da funcao (`PLAN_GENERATOR_FUNCTION_NAME`/
    `CARDIO_AGENT_FUNCTION_NAME`), nao o nome da funcao em si - ver
    infra/lambda_ai_generators.tf."""
    import boto3

    function_name = os.environ[function_name_env_var]
    client = boto3.client("lambda", region_name=_region())
    client.invoke(
        FunctionName=function_name,
        InvocationType="Event",
        Payload=_dumps(payload).encode("utf-8"),
    )

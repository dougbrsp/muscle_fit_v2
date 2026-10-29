"""Leitura da refeição por IA: a rota que dispara o job (nutrição, Lote L5b; plano
§8.1, §8.3 e §10). Vive na api-core (`{proxy+}`); a IA roda no worker da Lambda
`musclefit-workout-ai` (`nutrition_meal_parse_worker.py`).

Rotas (prefixo `/nutrition/meals`):
- `POST /parse`        `MealParseIn` -> **202** `{job_id, status, quota}`; a tela faz
                       polling em `GET /jobs/{job_id}` e, com o resultado, abre o
                       editor da refeição para confirmar (gravação pelo
                       `POST /nutrition/meals` de sempre, com `ai_job_id`)
- `GET  /parse/quota`  a cota do dia de quem pede (`{feature, limit, used,
                       remaining, allowed}`), para a tela mostrar "x de N hoje"

Contrato do L5a (mapa §54): `nutrition_ai_quota.open_usage` (trava do usuário,
contagem do dia, 429 `ai_quota_exceeded` ou a linha `pending`) ->
`async_jobs.create_job` -> `link_job` -> `async_jobs.invoke_async` com o
`ai_usage_id`. Se o despacho falhar, a linha fecha como `failed` e a rota
responde 503. A cota é de QUEM PEDE (o admin/PT que lê a refeição de um aluno
gasta a própria cota): é dele o job (`GET /jobs` só devolve ao dono, M65) e o
custo; o aluno do diário vai no payload para a busca no catálogo dele.

O pedido gravado no DynamoDB (`request`, auditoria) não leva o texto: só o
tamanho, o dia, a refeição e o aluno. O texto vai no evento da Lambda e, se a
pessoa salvar, no `meals.description_raw` (nosso banco); na OpenAI, `store=false`.

Regra dos motores (mapa §55): texto, slot e cota são de `domain/nutrition`
(`meal_parse`, `diary`, `ai_usage`); esta camada só orquestra.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from . import async_jobs
from . import nutrition_ai_quota as quota
from .db import get_conn
from .db_helpers import connect_or_503
from .domain.nutrition import diary, meal_parse
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.ai_usage import MEAL_PARSE_JOB_TYPE, AiFeature, AiUsageStatus
from .nutrition_access import fetch_target_user, resolve_target_user
from .nutrition_meal_parse_ai import PROMPT_VERSION
from .nutrition_schema import require_diary_tables, require_food_tables
from .request_models import MealParseIn
from .security import Identity, get_current_identity

logger = logging.getLogger("musclefit.nutrition.meal_parse")

router = APIRouter(prefix="/nutrition/meals", tags=["nutrition"])

FEATURE = AiFeature.MEAL_PARSE


def _connect():
    return connect_or_503(get_conn)


def _invalid_parse(codes: Any) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "error": "invalid_meal_parse",
            "message": "A descrição da refeição não segue as regras da leitura.",
            "errors": nutrition_labels.limitation_items(codes),
        },
    )


def _quota_public(user_id: int) -> Optional[dict[str, Any]]:
    """Uso de hoje pela decisão do motor; None se a leitura falhar (a resposta
    principal não depende disso)."""
    try:
        with _connect() as conn:
            return quota.usage_today(conn, user_id=user_id, feature=FEATURE).to_dict()
    except Exception:  # noqa: BLE001 - informativo
        logger.exception("Falha ao ler a cota de leitura da refeição")
        return None


@router.get("/parse/quota")
def meal_parse_quota(identity: Identity = Depends(get_current_identity)):
    """Cota de hoje de quem pede (dia do fuso do perfil). 503 antes da 085."""
    with _connect() as conn:
        return quota.usage_today(conn, user_id=int(identity.user_id), feature=FEATURE).to_dict()


@router.post("/parse", status_code=202)
def parse_meal(payload: MealParseIn, identity: Identity = Depends(get_current_identity)):
    aluno = resolve_target_user(identity, payload.user_id)
    texto, erros = meal_parse.validate_text(payload.text)
    if payload.meal_slot is not None and diary.normalize_meal_slot(payload.meal_slot) is None:
        erros = (*erros, diary.ERR_INVALID_MEAL_SLOT)
    if erros:
        raise _invalid_parse(erros)
    slot = diary.normalize_meal_slot(payload.meal_slot)
    solicitante = int(identity.user_id)

    # Antes de gastar a cota: o catálogo e o diário existem e o aluno existe.
    with _connect() as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            require_diary_tables(cur)
            if aluno != solicitante:
                fetch_target_user(cur, aluno)

    usage_id = quota.open_usage(user_id=solicitante, feature=FEATURE, prompt_version=PROMPT_VERSION)
    pedido = {
        "local_date": payload.local_date.isoformat(),
        "meal_slot": slot.value if slot else None,
        "diary_user_id": aluno,
    }
    try:
        job_id = async_jobs.create_job(
            MEAL_PARSE_JOB_TYPE,
            request={"payload": {**pedido, "text_chars": len(texto)}, "user_id": solicitante},
            owner_user_id=solicitante,
        )
        quota.link_job(usage_id, job_id)
        async_jobs.invoke_async(
            "WORKOUT_AI_FUNCTION_NAME",
            {
                "job_id": job_id,
                "job_type": MEAL_PARSE_JOB_TYPE,
                # O `job_id` também no payload: o resultado o devolve, e a tela grava a
                # refeição com ele (`ai_job_id`, origem `ai_text`).
                "payload": {**pedido, "text": texto, "job_id": job_id},
                "user_id": solicitante,
                "ai_usage_id": usage_id,
            },
        )
    except Exception as exc:
        logger.exception("Falha ao despachar a leitura da refeição por IA")
        quota.finish_usage(usage_id, status=AiUsageStatus.FAILED, error_code=meal_parse.ERR_DISPATCH_FAILED)
        raise HTTPException(
            status_code=503,
            detail={
                "error": meal_parse.ERR_DISPATCH_FAILED,
                "message": nutrition_labels.limitation_label(meal_parse.ERR_DISPATCH_FAILED),
            },
        ) from exc
    return {"job_id": job_id, "status": "pending", "quota": _quota_public(solicitante)}


__all__ = ["meal_parse_quota", "parse_meal", "router"]

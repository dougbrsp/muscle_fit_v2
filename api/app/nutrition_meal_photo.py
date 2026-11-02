"""Foto do prato: upload e disparo da leitura pela IA (nutrição, Lote L10). Vive na
api-core (`{proxy+}`), no mesmo desenho da foto do rótulo (`nutrition_label_photo`).

Rotas (prefixo `/nutrition/meals/photo`):
- `POST ""`      `MealPhotoUploadIn` (a foto JÁ REDUZIDA pela tela, em JPEG, base64 sem
                 padding) -> 202 `{job_id, status, poll_timeout_seconds}`. Ordem: acesso
                 ao aluno (403/404) -> tamanho, alfabeto, tipo e refeição (413/400/415,
                 não gastam cota) -> reserva da cota (429 `ai_quota_exceeded`) -> S3
                 `nutrition-meals/{quem pede}/{uuid}.jpg` -> job no DynamoDB -> vínculo
                 job × linha -> invoke assíncrono da `musclefit-plan-generator`. Falha ao
                 gravar ou despachar fecha a linha como `failed` e responde 503.
- `GET  "/usage"` uso da cota hoje (`{feature, limit, used, remaining, allowed}`).

O resultado chega por `GET /jobs/{job_id}`, que só devolve ao dono do job (M65): os
itens com a decisão do motor, para o editor de refeição; a gravação é o `POST
/nutrition/meals` de sempre, com `ai_job_id` e `ai_source=ai_photo`.

A cota e a foto são de QUEM PEDE (o admin/PT que lê o prato de um aluno gasta a própria
cota): é dele o job e o custo; o aluno do diário vai no payload, para a busca no
catálogo dele. O corpo é JSON com base64 sem padding pelo mesmo motivo do rótulo
(CRS do WAF); a tela sempre reencoda a foto pelo canvas, sem EXIF/XMP.

Regra dos motores (mapa §80): limite, tipo, chave e códigos de
`domain/nutrition/meal_photo`; cota, feature e job type de `ai_usage`; o prazo do
polling é o do worker perdido do M73. Esta camada só transporta.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from . import async_jobs, nutrition_ai_quota, nutrition_meal_photo_storage
from .db import get_conn
from .db_helpers import connect_or_503
from .domain.nutrition import diary, label_photo, meal_photo
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.ai_usage import MEAL_PHOTO_JOB_TYPE, AiFeature, AiUsageStatus
from .gyms import sniff_image_content_type
from .nutrition_access import fetch_target_user, resolve_target_user
from .nutrition_meal_photo_ai import PROMPT_VERSION
from .nutrition_schema import require_diary_tables, require_food_tables
from .request_models import MealPhotoUploadIn
from .security import Identity, get_current_identity

logger = logging.getLogger("musclefit.nutrition.meal_photo")

router = APIRouter(prefix="/nutrition/meals/photo", tags=["nutrition"])

FEATURE = AiFeature.MEAL_PHOTO

_STATUS_BY_UPLOAD_ERROR: dict[str, int] = {
    meal_photo.ERR_MEAL_PHOTO_EMPTY: 400,
    meal_photo.ERR_MEAL_PHOTO_INVALID_ENCODING: 400,
    meal_photo.ERR_MEAL_PHOTO_TOO_LARGE: 413,
    meal_photo.ERR_MEAL_PHOTO_UNSUPPORTED_TYPE: 415,
}


def _error(status: int, code: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": code, "message": nutrition_labels.limitation_label(code), **extra},
    )


def _close_failed(usage_id: int, code: str) -> None:
    nutrition_ai_quota.finish_usage(usage_id, status=AiUsageStatus.FAILED, error_code=code)


@router.get("/usage")
def meal_photo_usage(identity: Identity = Depends(get_current_identity)):
    with connect_or_503(get_conn) as conn:
        return nutrition_ai_quota.usage_today(conn, user_id=int(identity.user_id), feature=FEATURE).to_dict()


@router.post("", status_code=202)
def upload_meal_photo(payload: MealPhotoUploadIn, identity: Identity = Depends(get_current_identity)):
    aluno = resolve_target_user(identity, payload.user_id)
    solicitante = int(identity.user_id)

    dados, erro = meal_photo.decode_upload(payload.image_base64)
    dados = dados or b""
    erro = erro or meal_photo.upload_error(len(dados), sniff_image_content_type(dados[:label_photo.PHOTO_SNIFF_BYTES]))
    if erro:
        raise _error(_STATUS_BY_UPLOAD_ERROR[erro], erro, max_bytes=meal_photo.MEAL_PHOTO_MAX_BYTES)
    slot = diary.normalize_meal_slot(payload.meal_slot)
    if payload.meal_slot is not None and slot is None:
        raise _error(400, diary.ERR_INVALID_MEAL_SLOT)

    # Antes de gastar a cota: o catálogo e o diário existem e o aluno existe.
    with connect_or_503(get_conn) as conn:
        with conn.cursor() as cur:
            require_food_tables(cur)
            require_diary_tables(cur)
            if aluno != solicitante:
                fetch_target_user(cur, aluno)

    usage_id = nutrition_ai_quota.open_usage(
        user_id=solicitante,
        feature=FEATURE,
        prompt_version=PROMPT_VERSION,
        image_count=meal_photo.MEAL_PHOTO_IMAGES_PER_READING,
    )
    try:
        chave = nutrition_meal_photo_storage.put_meal_photo(solicitante, dados)
    except nutrition_meal_photo_storage.MealPhotoStorageError as exc:
        logger.exception("Falha ao gravar a foto do prato (ai_usage_id=%s)", usage_id)
        _close_failed(usage_id, exc.code)
        raise _error(503, exc.code) from exc

    pedido = {
        "local_date": payload.local_date.isoformat(),
        "meal_slot": slot.value if slot else None,
        "diary_user_id": aluno,
    }
    try:
        job_id = async_jobs.create_job(
            MEAL_PHOTO_JOB_TYPE,
            request={**pedido, "s3_key": chave, "ai_usage_id": usage_id},
            owner_user_id=solicitante,
        )
    except Exception as exc:  # noqa: BLE001 - DynamoDB fora: nada foi despachado
        logger.exception("Falha ao criar o job da foto do prato (ai_usage_id=%s)", usage_id)
        _close_failed(usage_id, meal_photo.ERR_MEAL_PHOTO_DISPATCH_FAILED)
        raise _error(503, meal_photo.ERR_MEAL_PHOTO_DISPATCH_FAILED) from exc
    nutrition_ai_quota.link_job(usage_id, job_id)

    try:
        async_jobs.invoke_async(
            "PLAN_GENERATOR_FUNCTION_NAME",
            {
                "job_id": job_id,
                "job_kind": MEAL_PHOTO_JOB_TYPE,
                "user_id": solicitante,
                "ai_usage_id": usage_id,
                "s3_key": chave,
                # O `job_id` também no payload: o resultado o devolve, e a tela grava a
                # refeição com ele (`ai_job_id`, origem `ai_photo`).
                "payload": {**pedido, "job_id": job_id},
            },
        )
    except Exception as exc:  # noqa: BLE001 - invoke falhou: o job nunca vai rodar
        logger.exception("Falha ao despachar a foto do prato (job_id=%s)", job_id)
        _close_failed(usage_id, meal_photo.ERR_MEAL_PHOTO_DISPATCH_FAILED)
        try:
            async_jobs.mark_failed(job_id, nutrition_labels.limitation_label(meal_photo.ERR_MEAL_PHOTO_DISPATCH_FAILED))
        except Exception:  # noqa: BLE001 - o 503 abaixo já avisa a tela
            logger.exception("Falha ao marcar o job %s como failed", job_id)
        raise _error(503, meal_photo.ERR_MEAL_PHOTO_DISPATCH_FAILED) from exc

    return {
        "job_id": job_id,
        "status": "pending",
        "poll_timeout_seconds": async_jobs.worker_lost_deadline_seconds(MEAL_PHOTO_JOB_TYPE),
    }


__all__ = ["router"]

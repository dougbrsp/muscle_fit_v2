"""Foto do rótulo nutricional: upload e leitura assíncrona pela IA (plano de
nutrição §8.1, §8.3 e §9 b; Lote L6). Vive na api-core (`{proxy+}`).

Rotas (prefixo `/nutrition/foods/label-photo`):
- `POST ""`      JSON `{image_base64}` (a foto JÁ REDUZIDA pela tela, em JPEG, base64
                 sem padding) -> 202 `{job_id, status, poll_timeout_seconds}`. Ordem:
                 tamanho, alfabeto e tipo (413/400/415, pela assinatura dos bytes; não
                 gastam cota) -> reserva da
                 cota (`nutrition_ai_quota.open_usage`, 429 `ai_quota_exceeded`) ->
                 S3 `nutrition-labels/{user}/{uuid}.jpg` -> job no DynamoDB ->
                 vínculo job × linha -> invoke assíncrono da `musclefit-plan-generator`.
                 Falha ao gravar ou despachar fecha a linha como `failed` e responde 503.
- `GET  "/usage"` uso da cota hoje (`{feature, limit, used, remaining, allowed}`).

O resultado chega por `GET /jobs/{job_id}` (`nutrition_label_ai.job_result`):
formulário pré-preenchido + erros e avisos do motor. O cadastro é o `POST
/nutrition/foods` de sempre, com `source=label_photo`.

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md` §3.7, §54 e §56): limite,
tipo, chave e códigos de `domain/nutrition/label_photo`; cota, feature e job
type de `domain/nutrition/ai_usage`; o prazo do polling é o do worker perdido do
M73 (`async_jobs.worker_lost_deadline_seconds`). Esta camada só transporta.

WAF (CRS bloqueando XSS/LFI/RFI/SSRF no corpo desde o #579): o corpo é JSON com
a foto em base64 sem padding — o alfabeto `[A-Za-z0-9+/]` não forma as
assinaturas do CRS, que bytes de JPEG crus num multipart poderiam formar por
acaso (revisão de impacto, 2026-10-18). A tela SEMPRE reencoda a foto pelo canvas
(sem EXIF/XMP); esta rota não aceita outro tipo que não o JPEG.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from . import async_jobs, nutrition_ai_quota, nutrition_label_storage
from .db import get_conn
from .db_helpers import connect_or_503
from .domain.nutrition import label_photo
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.ai_usage import LABEL_PHOTO_JOB_TYPE, AiFeature, AiUsageStatus
from .gyms import sniff_image_content_type
from .nutrition_label_ai import PROMPT_VERSION
from .request_models import LabelPhotoUploadIn
from .security import Identity, get_current_identity

logger = logging.getLogger("musclefit.nutrition.label_photo")

router = APIRouter(prefix="/nutrition/foods/label-photo", tags=["nutrition"])

# Bytes que bastam para a assinatura da imagem (`gyms.sniff_image_content_type`).
_SNIFF_BYTES = 16

_STATUS_BY_UPLOAD_ERROR: dict[str, int] = {
    label_photo.ERR_LABEL_PHOTO_EMPTY: 400,
    label_photo.ERR_LABEL_PHOTO_INVALID_ENCODING: 400,
    label_photo.ERR_LABEL_PHOTO_TOO_LARGE: 413,
    label_photo.ERR_LABEL_PHOTO_UNSUPPORTED_TYPE: 415,
}


def _error(status: int, code: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": code, "message": nutrition_labels.limitation_label(code), **extra},
    )


def _close_failed(usage_id: int, code: str) -> None:
    nutrition_ai_quota.finish_usage(usage_id, status=AiUsageStatus.FAILED, error_code=code)


@router.get("/usage")
def label_photo_usage(identity: Identity = Depends(get_current_identity)):
    with connect_or_503(get_conn) as conn:
        return nutrition_ai_quota.usage_today(conn, user_id=int(identity.user_id), feature=AiFeature.LABEL_PHOTO).to_dict()


@router.post("", status_code=202)
def upload_label_photo(payload: LabelPhotoUploadIn, identity: Identity = Depends(get_current_identity)):
    usuario = int(identity.user_id)
    dados, erro = label_photo.decode_upload(payload.image_base64)
    dados = dados or b""
    erro = erro or label_photo.upload_error(len(dados), sniff_image_content_type(dados[:_SNIFF_BYTES]))
    if erro:
        raise _error(_STATUS_BY_UPLOAD_ERROR[erro], erro, max_bytes=label_photo.LABEL_PHOTO_MAX_BYTES)

    usage_id = nutrition_ai_quota.open_usage(
        user_id=usuario,
        feature=AiFeature.LABEL_PHOTO,
        prompt_version=PROMPT_VERSION,
        image_count=label_photo.LABEL_PHOTO_IMAGES_PER_READING,
    )
    try:
        chave = nutrition_label_storage.put_label_photo(usuario, dados)
    except nutrition_label_storage.LabelPhotoStorageError as exc:
        logger.exception("Falha ao gravar a foto do rótulo (ai_usage_id=%s)", usage_id)
        _close_failed(usage_id, exc.code)
        raise _error(503, exc.code) from exc

    try:
        job_id = async_jobs.create_job(
            LABEL_PHOTO_JOB_TYPE,
            request={"s3_key": chave, "ai_usage_id": usage_id},
            owner_user_id=usuario,
        )
    except Exception as exc:  # noqa: BLE001 - DynamoDB fora: nada foi despachado
        logger.exception("Falha ao criar o job da leitura do rótulo (ai_usage_id=%s)", usage_id)
        _close_failed(usage_id, label_photo.ERR_LABEL_PHOTO_DISPATCH_FAILED)
        raise _error(503, label_photo.ERR_LABEL_PHOTO_DISPATCH_FAILED) from exc
    nutrition_ai_quota.link_job(usage_id, job_id)

    try:
        async_jobs.invoke_async(
            "PLAN_GENERATOR_FUNCTION_NAME",
            {
                "job_id": job_id,
                "job_kind": LABEL_PHOTO_JOB_TYPE,
                "user_id": usuario,
                "ai_usage_id": usage_id,
                "s3_key": chave,
            },
        )
    except Exception as exc:  # noqa: BLE001 - invoke falhou: o job nunca vai rodar
        logger.exception("Falha ao despachar a leitura do rótulo (job_id=%s)", job_id)
        _close_failed(usage_id, label_photo.ERR_LABEL_PHOTO_DISPATCH_FAILED)
        try:
            async_jobs.mark_failed(job_id, nutrition_labels.limitation_label(label_photo.ERR_LABEL_PHOTO_DISPATCH_FAILED))
        except Exception:  # noqa: BLE001 - o 503 abaixo já avisa a tela
            logger.exception("Falha ao marcar o job %s como failed", job_id)
        raise _error(503, label_photo.ERR_LABEL_PHOTO_DISPATCH_FAILED) from exc

    return {
        "job_id": job_id,
        "status": "pending",
        "poll_timeout_seconds": async_jobs.worker_lost_deadline_seconds(LABEL_PHOTO_JOB_TYPE),
    }


__all__ = ["router"]

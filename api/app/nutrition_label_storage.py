"""Foto do rótulo nutricional no bucket de uploads (`EQUIPMENT_PHOTOS_BUCKET` =
`musclefit-uploads`, o mesmo das fotos e dos cards). Plano de nutrição §8.1,
§9 (b) e §12; Lote L6.

Só transporte. Chave, prefixo, tipo e retenção são do motor
(`domain/nutrition/label_photo`: `label_photo_key`, `LABEL_PHOTO_CONTENT_TYPE`,
`LABEL_PHOTO_RETENTION_DAYS`, espelho da regra `nutrition-labels-expire-30d` de
`infra/s3_uploads.tf`). Quem grava é a api-core (`musclefit-api-core-uploads`
já cobre `PutObject` em `musclefit-uploads/*`); quem lê é o worker da
`musclefit-plan-generator` (`s3:GetObject` só em `nutrition-labels/*`, em
`infra/lambda_ai_generators.tf`). Fora da CDN: a bucket policy do OAC só libera
`exercise-cards/*`.

DEV: `AWS_ENDPOINT_URL` do container aponta o client para o LocalStack (mesmo
bucket, criado pelo `00-bootstrap.sh`); nenhum ramo `if dev`.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Optional

from .domain.nutrition import label_photo

# Foto do usuário: nunca em cache compartilhado (e nem passa pela CDN).
CACHE_CONTROL = "private, no-store"

# `Error.Code` do `ClientError` do botocore para objeto inexistente.
_MISSING_OBJECT_CODES = frozenset({"NoSuchKey", "404", "NotFound"})


class LabelPhotoStorageError(RuntimeError):
    """Falha de armazenamento, com o código do motor (`label_photo.ERR_*`)."""

    def __init__(self, message: str, *, code: str = label_photo.ERR_LABEL_PHOTO_STORAGE_FAILED):
        super().__init__(message)
        self.code = code


def bucket_name() -> str:
    bucket = (os.environ.get("EQUIPMENT_PHOTOS_BUCKET") or "").strip()
    if not bucket:
        raise LabelPhotoStorageError("EQUIPMENT_PHOTOS_BUCKET não configurado.")
    return bucket


def s3_client():
    import boto3  # import local: não pagar o custo no cold start de quem não usa

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "sa-east-1"
    return boto3.client("s3", region_name=region)


def _is_missing(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        if str((response.get("Error") or {}).get("Code") or "") in _MISSING_OBJECT_CODES:
            return True
    return type(exc).__name__ == "NoSuchKey"


def put_label_photo(user_id: int, data: bytes, *, client: Any = None, bucket: Optional[str] = None) -> str:
    """Grava a foto (já validada pela rota) numa chave nova do usuário e devolve a chave."""
    key = label_photo.label_photo_key(user_id, uuid.uuid4().hex)
    try:
        (client or s3_client()).put_object(
            Bucket=bucket or bucket_name(),
            Key=key,
            Body=data,
            ContentType=label_photo.LABEL_PHOTO_CONTENT_TYPE,
            CacheControl=CACHE_CONTROL,
        )
    except LabelPhotoStorageError:
        raise
    except Exception as exc:  # noqa: BLE001 - qualquer falha do S3 vira o erro tipado
        raise LabelPhotoStorageError(f"Falha ao gravar a foto do rótulo no S3: {type(exc).__name__}") from exc
    return key


def read_label_photo(key: str, *, client: Any = None, bucket: Optional[str] = None) -> bytes:
    """Bytes da foto. Objeto inexistente (a retenção apagou) -> `ERR_LABEL_PHOTO_NOT_FOUND`;
    qualquer outra falha -> `ERR_LABEL_PHOTO_STORAGE_FAILED`. Em produção a role do
    worker não tem `s3:ListBucket`, então o S3 responde 403 (não `NoSuchKey`) para
    objeto inexistente e o código fica `STORAGE_FAILED`; o LocalStack responde
    `NoSuchKey`. O job lê a foto segundos depois do upload: o caso não ocorre no
    fluxo normal (registrado no mapa §56)."""
    try:
        resposta = (client or s3_client()).get_object(Bucket=bucket or bucket_name(), Key=key)
        corpo = resposta["Body"]
        try:
            return corpo.read()
        finally:
            corpo.close()
    except LabelPhotoStorageError:
        raise
    except Exception as exc:  # noqa: BLE001
        if _is_missing(exc):
            raise LabelPhotoStorageError(
                f"Foto do rótulo {key} não existe mais no S3.", code=label_photo.ERR_LABEL_PHOTO_NOT_FOUND
            ) from exc
        raise LabelPhotoStorageError(f"Falha ao ler a foto do rótulo do S3: {type(exc).__name__}") from exc


__all__ = ["CACHE_CONTROL", "LabelPhotoStorageError", "bucket_name", "put_label_photo", "read_label_photo", "s3_client"]

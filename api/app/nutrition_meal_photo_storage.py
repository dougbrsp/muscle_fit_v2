"""Foto do prato no bucket de uploads (nutrição, Lote L10). Só transporte, com o
cliente S3 e o bucket do armazenamento do rótulo (`nutrition_label_storage`).

Chave, prefixo, tipo e retenção são do motor (`domain/nutrition/meal_photo`). Quem
grava é a api-core (`musclefit-api-core-uploads` cobre `PutObject` em
`musclefit-uploads/*`); quem lê é o worker da `musclefit-plan-generator` (`s3:GetObject`
só em `nutrition-meals/*`, em `infra/lambda_ai_generators.tf`). Fora da CDN: a bucket
policy do OAC só libera `exercise-cards/*`. Sem ramo `if dev`: no DEV, o
`AWS_ENDPOINT_URL` aponta o client para o LocalStack.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from .domain.nutrition import meal_photo
from .nutrition_label_storage import CACHE_CONTROL, LabelPhotoStorageError, _is_missing, bucket_name, s3_client


class MealPhotoStorageError(RuntimeError):
    """Falha de armazenamento, com o código do motor (`meal_photo.ERR_*`)."""

    def __init__(self, message: str, *, code: str = meal_photo.ERR_MEAL_PHOTO_STORAGE_FAILED):
        super().__init__(message)
        self.code = code


def put_meal_photo(user_id: int, data: bytes, *, client: Any = None, bucket: Optional[str] = None) -> str:
    """Grava a foto (já validada pela rota) numa chave nova do usuário e devolve a chave."""
    key = meal_photo.meal_photo_key(user_id, uuid.uuid4().hex)
    try:
        (client or s3_client()).put_object(
            Bucket=bucket or bucket_name(),
            Key=key,
            Body=data,
            ContentType=meal_photo.MEAL_PHOTO_CONTENT_TYPE,
            CacheControl=CACHE_CONTROL,
        )
    except LabelPhotoStorageError as exc:  # bucket não configurado
        raise MealPhotoStorageError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - qualquer falha do S3 vira o erro tipado
        raise MealPhotoStorageError(f"Falha ao gravar a foto do prato no S3: {type(exc).__name__}") from exc
    return key


def read_meal_photo(key: str, *, client: Any = None, bucket: Optional[str] = None) -> bytes:
    """Bytes da foto. Objeto inexistente -> `ERR_MEAL_PHOTO_NOT_FOUND`; outra falha ->
    `ERR_MEAL_PHOTO_STORAGE_FAILED` (em produção a role do worker não tem
    `s3:ListBucket`, então o objeto inexistente responde 403 e cai aqui; o job lê a
    foto segundos depois do upload, como no rótulo)."""
    try:
        resposta = (client or s3_client()).get_object(Bucket=bucket or bucket_name(), Key=key)
        corpo = resposta["Body"]
        try:
            return corpo.read()
        finally:
            corpo.close()
    except LabelPhotoStorageError as exc:  # bucket não configurado
        raise MealPhotoStorageError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        if _is_missing(exc):
            raise MealPhotoStorageError(
                f"Foto do prato {key} não existe mais no S3.", code=meal_photo.ERR_MEAL_PHOTO_NOT_FOUND
            ) from exc
        raise MealPhotoStorageError(f"Falha ao ler a foto do prato do S3: {type(exc).__name__}") from exc


__all__ = ["MealPhotoStorageError", "put_meal_photo", "read_meal_photo"]

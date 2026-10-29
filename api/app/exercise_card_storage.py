"""Armazenamento dos cards ilustrados de exercicios no bucket de uploads
(`EQUIPMENT_PHOTOS_BUCKET` = `musclefit-uploads`, o mesmo das fotos).

Duas areas, com regras opostas:

- **Rascunho** (`exercise-card-drafts/{generation_id}/`): imagem paga e
  AINDA NAO APROVADA, dois arquivos por geracao:
  `illustration.png` (a ilustracao crua da OpenAI, guardada para recompor
  sem pagar de novo - `illustration_key`) e `preview.png` (o card COMPOSTO,
  exatamente o que o admin revisa e o que a aprovacao publica -
  `draft_key`). A chave da ilustracao deriva do `generation_id`: nenhuma
  coluna nova. Fica FORA da CDN (a bucket policy do OAC so libera
  `exercise-cards/*`), sem cache publico, e chega ao admin por URL
  PRE-ASSINADA de 15 minutos gerada pela api-core. O IAM do worker ja
  permite `PutObject` em `exercise-card-drafts/*`.
- **Publicado** (`exercise-cards/{exercise_id}/v{n}/card.webp|card.png|
  thumb.webp`): imutavel por versao - uma versao nova e um PATH novo, nunca
  sobrescrita -, entao vai com `Cache-Control: public, max-age=31536000,
  immutable` gravado no proprio objeto e e servido pela CDN.

Chaves so com ids internos (secao 27 da especificacao): nada de texto livre
do usuario no path.

Sem regra de negocio aqui (estados, versao, transacao: servico). Cada funcao
aceita `client`/`bucket` injetados - os testes usam `tests/support/fake_s3.py`.

DEV: `AWS_ENDPOINT_URL` do container aponta o client padrao para o
LocalStack (`http://localstack:4566`), que o NAVEGADOR nao resolve. Por isso
o presign usa um client proprio quando `S3_PRESIGN_ENDPOINT_URL` existe
(so no compose; nunca em infra/): a assinatura sai para o host que o
navegador alcanca. Em producao a variavel nao existe e o presign usa o
endpoint S3 padrao da regiao.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import public_assets

DRAFT_PREFIX = "exercise-card-drafts"
PUBLISHED_PREFIX = "exercise-cards"

# Arquivos do rascunho de uma geracao (lista fechada: nada de nome livre no path).
DRAFT_PREVIEW = "preview.png"  # card composto (o que o admin revisa)
DRAFT_ILLUSTRATION = "illustration.png"  # ilustracao crua da OpenAI
DRAFT_FILENAMES = (DRAFT_PREVIEW, DRAFT_ILLUSTRATION)

IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
# Rascunho: nunca em cache compartilhado (e nem passa pela CDN).
DRAFT_CACHE_CONTROL = "private, no-store"

PRESIGN_EXPIRES_SECONDS = 15 * 60

# M72 - validade do rascunho. ESPELHA a regra de lifecycle
# `exercise-card-drafts-expire-7d` (`expiration { days = 7 }`) em
# `infra/s3_uploads.tf`: mudou la, muda aqui (e vice-versa). A api-core usa
# este numero para marcar o rascunho como expirado SEM depender do objeto ter
# sumido (em DEV o LocalStack nao aplica lifecycle) - ver
# `exercise_card_service.draft_expires_at`. O S3 arredonda a expiracao para a
# meia-noite UTC seguinte, entao o objeto vive >= 7 dias: contar 7 dias a
# partir do `generated_at` erra para o lado seguro (bloqueia antes de sumir).
DRAFT_TTL_DAYS = 7

CONTENT_TYPE_PNG = "image/png"
CONTENT_TYPE_WEBP = "image/webp"

S3_UPLOAD_FAILED = "S3_UPLOAD_FAILED"
S3_READ_FAILED = "S3_READ_FAILED"
# Objeto do rascunho inexistente (NoSuchKey/404): o lifecycle de 7 dias apagou.
DRAFT_EXPIRED = "DRAFT_EXPIRED"

# `Error.Code` do `ClientError` do botocore para objeto inexistente: `NoSuchKey`
# no GetObject, `404`/`NotFound` no HeadObject (sem corpo de erro).
_MISSING_OBJECT_CODES = frozenset({"NoSuchKey", "404", "NotFound"})
STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"


class ExerciseCardStorageError(RuntimeError):
    """Falha de armazenamento, com `code` para o `error_code` da geracao."""

    def __init__(self, message: str, *, code: str = S3_UPLOAD_FAILED):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PublishedKeys:
    card_webp: str
    card_png: str
    thumb_webp: str


@dataclass(frozen=True)
class PresignedUrl:
    url: str
    expires_at: datetime


# ---------------------------------------------------------------------------
# Chaves
# ---------------------------------------------------------------------------


def _positive_int(value: Any, name: str) -> int:
    number = int(value)
    if number <= 0:
        raise ValueError(f"{name} precisa ser inteiro positivo (recebido {value!r}).")
    return number


def draft_key(generation_id: int, filename: str = DRAFT_PREVIEW) -> str:
    if filename not in DRAFT_FILENAMES:
        raise ValueError(f"Arquivo de rascunho desconhecido: {filename!r} (use {', '.join(DRAFT_FILENAMES)}).")
    return f"{DRAFT_PREFIX}/{_positive_int(generation_id, 'generation_id')}/{filename}"


def illustration_key(generation_id: int) -> str:
    """Ilustracao crua da OpenAI (base para recompor o card sem custo)."""
    return draft_key(generation_id, DRAFT_ILLUSTRATION)


def published_keys(exercise_id: int, version: int) -> PublishedKeys:
    base = (
        f"{PUBLISHED_PREFIX}/{_positive_int(exercise_id, 'exercise_id')}"
        f"/v{_positive_int(version, 'version')}"
    )
    return PublishedKeys(
        card_webp=f"{base}/card.webp",
        card_png=f"{base}/card.png",
        thumb_webp=f"{base}/thumb.webp",
    )


def public_urls(keys: PublishedKeys) -> dict[str, str]:
    """URLs publicas (CDN em producao, LocalStack em DEV) da versao publicada."""
    return {
        "url": public_assets.url_for(keys.card_webp),
        "png_url": public_assets.url_for(keys.card_png),
        "thumbnail_url": public_assets.url_for(keys.thumb_webp),
    }


def training_card(exercise_id: Any, card_version: Any) -> Optional[dict[str, Any]]:
    """Resumo do card PUBLICADO para o Modo Treino (`{url, thumbnail_url,
    version}`), montado so a partir do ponteiro do catalogo - sem I/O.

    `exercise_id` e o id CANONICO de `exercise_catalog` (quem chama ja
    resolveu o legado) e `card_version` e `exercise_catalog.card_version`,
    gravado pelo servico so DEPOIS dos 3 uploads: com ele presente os objetos
    existem. `None` em qualquer um dos dois (exercicio sem card, item da ficha
    sem catalogo) -> `None`, e a UI nao mostra nada. Valor invalido tambem
    vira `None`: a leitura da ficha nunca pode quebrar por causa do card."""
    if exercise_id is None or card_version is None:
        return None
    try:
        version = _positive_int(card_version, "card_version")
        urls = public_urls(published_keys(exercise_id, version))
    except (TypeError, ValueError):
        return None
    return {"url": urls["url"], "thumbnail_url": urls["thumbnail_url"], "version": version}


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


def bucket_name() -> str:
    bucket = (os.environ.get("EQUIPMENT_PHOTOS_BUCKET") or "").strip()
    if not bucket:
        raise ExerciseCardStorageError("EQUIPMENT_PHOTOS_BUCKET nao configurado.", code=STORAGE_UNAVAILABLE)
    return bucket


def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "sa-east-1"


def s3_client():
    """Client de leitura/escrita - mesmo jeito de `gyms._s3_client` (em DEV,
    `AWS_ENDPOINT_URL` leva ao LocalStack)."""
    import boto3  # import local: nao pagar o custo em cold start de quem nao usa

    return boto3.client("s3", region_name=_region())


def presign_client():
    """Client SO para assinar URL (operacao local, sem rede). SigV4 explicito
    nos dois ambientes; com `S3_PRESIGN_ENDPOINT_URL` (so DEV), endpoint e
    path-style do host que o navegador alcanca."""
    import boto3
    from botocore.config import Config

    endpoint = (os.environ.get("S3_PRESIGN_ENDPOINT_URL") or "").strip()
    if endpoint:
        return boto3.client(
            "s3",
            region_name=_region(),
            endpoint_url=endpoint,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
    return boto3.client("s3", region_name=_region(), config=Config(signature_version="s3v4"))


# ---------------------------------------------------------------------------
# Operacoes
# ---------------------------------------------------------------------------


def _put(client, bucket: str, key: str, body: bytes, content_type: str, cache_control: str) -> None:
    if not body:
        raise ExerciseCardStorageError(f"Corpo vazio para {key}.")
    try:
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type, CacheControl=cache_control)
    except Exception as exc:  # noqa: BLE001 - qualquer falha do S3 vira o erro tipado
        raise ExerciseCardStorageError(f"Falha ao gravar {key} no S3: {type(exc).__name__}") from exc


def put_draft(
    generation_id: int,
    png: bytes,
    *,
    filename: str = DRAFT_PREVIEW,
    client=None,
    bucket: Optional[str] = None,
) -> str:
    """Grava um PNG do rascunho (`preview.png` = card composto, padrao;
    `illustration.png` = ilustracao crua) e devolve a key."""
    key = draft_key(generation_id, filename)
    _put(client or s3_client(), bucket or bucket_name(), key, png, CONTENT_TYPE_PNG, DRAFT_CACHE_CONTROL)
    return key


def is_missing_object_error(exc: BaseException) -> bool:
    """True se a falha do S3 e "objeto nao existe" (`ClientError` do botocore
    com `Error.Code` NoSuchKey/404/NotFound, ou a subclasse `NoSuchKey` do
    client). Qualquer outra falha (permissao, rede, throttling) e False."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str((response.get("Error") or {}).get("Code") or "")
        if code in _MISSING_OBJECT_CODES:
            return True
    return type(exc).__name__ == "NoSuchKey"


def read_draft(key: str, *, client=None, bucket: Optional[str] = None) -> bytes:
    """Bytes de um arquivo do rascunho (a aprovacao publica o preview; o
    script de calibracao recompoe a partir da ilustracao). Objeto inexistente
    -> `DRAFT_EXPIRED` (o lifecycle de `DRAFT_TTL_DAYS` apagou); qualquer outra
    falha -> `S3_READ_FAILED`."""
    if not str(key or "").startswith(f"{DRAFT_PREFIX}/"):
        raise ValueError(f"Key fora da area de rascunho: {key!r}.")
    try:
        response = (client or s3_client()).get_object(Bucket=bucket or bucket_name(), Key=key)
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()
    except Exception as exc:  # noqa: BLE001
        if is_missing_object_error(exc):
            raise ExerciseCardStorageError(f"Rascunho {key} nao existe mais no S3.", code=DRAFT_EXPIRED) from exc
        raise ExerciseCardStorageError(f"Falha ao ler {key} do S3: {type(exc).__name__}", code=S3_READ_FAILED) from exc


def put_published(
    exercise_id: int,
    version: int,
    *,
    card_webp: bytes,
    card_png: bytes,
    thumb_webp: bytes,
    client=None,
    bucket: Optional[str] = None,
) -> PublishedKeys:
    """Grava os 3 arquivos da versao. Falha no meio deixa objetos orfaos num
    path de versao que NINGUEM aponta: o ponteiro do catalogo
    (`card_version`) so e gravado pelo servico depois dos 3 uploads, e a
    proxima tentativa sobrescreve os mesmos paths."""
    keys = published_keys(exercise_id, version)
    s3 = client or s3_client()
    target = bucket or bucket_name()
    _put(s3, target, keys.card_webp, card_webp, CONTENT_TYPE_WEBP, IMMUTABLE_CACHE_CONTROL)
    _put(s3, target, keys.card_png, card_png, CONTENT_TYPE_PNG, IMMUTABLE_CACHE_CONTROL)
    _put(s3, target, keys.thumb_webp, thumb_webp, CONTENT_TYPE_WEBP, IMMUTABLE_CACHE_CONTROL)
    return keys


def presign_draft_url(
    key: str,
    *,
    client=None,
    bucket: Optional[str] = None,
    expires_in: int = PRESIGN_EXPIRES_SECONDS,
    now: Optional[datetime] = None,
) -> PresignedUrl:
    """URL pre-assinada de GET do rascunho (15 min). So rascunho: o publicado
    tem URL publica estavel pela CDN."""
    if not str(key or "").startswith(f"{DRAFT_PREFIX}/"):
        raise ValueError(f"Key fora da area de rascunho: {key!r}.")
    signer = client or presign_client()
    url = signer.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket or bucket_name(), "Key": key},
        ExpiresIn=int(expires_in),
    )
    issued = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    return PresignedUrl(url=url, expires_at=issued + timedelta(seconds=int(expires_in)))

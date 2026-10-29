from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Cookie, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response

from . import equipment_recommender, security
from .advanced_techniques import ensure_valid_codes as ensure_valid_technique_codes
from .db import DataApiError, get_conn
from .security import resolve_identity_from_credentials as _require_identity
from .request_models import GymCreateIn
from .gym_equipment_sync import sync_gym_equipment_for_gym

router = APIRouter(tags=["gyms"])

logger = logging.getLogger(__name__)


# Fotos de academia (logo)/equipamento guardadas no S3 (musclefit-uploads),
# nao mais em disco local - a Lambda nao tem disco persistente entre
# invocacoes/containers (ao contrario do volume Docker on-premise). Ver
# docs/aws-migration-plan.md, Fase 3, item 2.
#
# Servidas publicamente como arquivo estatico via CloudFront (mesma
# distribuicao do frontend, ver infra/cloudfront_uploads.tf) para evitar
# rajada de invocacoes Lambda (GET /equipment/{id}/photo e GET
# /gyms/{id}/logo, ainda existentes abaixo, ficam preservadas mas sem uso
# pelo frontend novo) - a concorrencia da conta e limitada a 10 execucoes
# simultaneas, compartilhada entre as 6 Lambdas do projeto, e uma tela com
# varias fotos estourava esse limite (Throttles no CloudWatch -> 500 no
# navegador). Foto de perfil de usuario (users.py) NAO entra nessa mudanca -
# e dado pessoal, continua via Lambda autenticada.
PUBLIC_ASSETS_DOMAIN = "app.musclefit.com.br"
PHOTO_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_EXT_TO_CONTENT_TYPE = {ext: content_type for content_type, ext in PHOTO_CONTENT_TYPES.items()}
MAX_PHOTO_BYTES = 8 * 1024 * 1024
_PHOTO_READ_CHUNK = 64 * 1024


def sniff_image_content_type(head: bytes) -> Optional[str]:
    """Tipo da imagem pela ASSINATURA (magic bytes), nunca pelo `content_type`
    declarado pelo cliente (SEG-11). None = nao e PNG/JPEG/WEBP/GIF."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def read_limited_photo(file: UploadFile) -> bytes:
    """Le o upload em partes e aborta (413) ao passar de MAX_PHOTO_BYTES, sem
    carregar o corpo inteiro na memoria antes de conferir o tamanho."""
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(_PHOTO_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_PHOTO_BYTES:
            raise HTTPException(status_code=413, detail="Imagem muito grande (máx. 8MB).")
        chunks.append(chunk)
    return b"".join(chunks)


def _s3_bucket() -> str:
    bucket = os.environ.get("EQUIPMENT_PHOTOS_BUCKET")
    if not bucket:
        raise HTTPException(
            status_code=503,
            detail={"error": "photo_storage_unavailable", "message": "EQUIPMENT_PHOTOS_BUCKET não configurado."},
        )
    return bucket


def _s3_client():
    import boto3

    region_name = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "sa-east-1"
    return boto3.client("s3", region_name=region_name)


def _invalidate_cloudfront_paths(keys: List[str]) -> None:
    """Invalida no CloudFront o(s) path(s) de foto/logo afetado(s) por um
    upload ou delete, para nao servir versao cacheada obsoleta (a
    distribuicao usa CachingOptimized, TTL longo - ver
    infra/cloudfront_uploads.tf). Nao-bloqueante: falha aqui vira warning no
    log, nunca derruba a request de upload/delete (o objeto no S3 ja foi
    escrito/removido com sucesso antes desta chamada)."""
    keys = [key for key in dict.fromkeys(keys) if key]
    if not keys:
        return

    distribution_id = os.environ.get("CLOUDFRONT_DISTRIBUTION_ID")
    if not distribution_id:
        logger.warning("CLOUDFRONT_DISTRIBUTION_ID não configurado; pulando invalidação para %s", keys)
        return

    try:
        import boto3

        cloudfront = boto3.client("cloudfront")
        cloudfront.create_invalidation(
            DistributionId=distribution_id,
            InvalidationBatch={
                "CallerReference": f"gyms-photo-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
                "Paths": {
                    "Quantity": len(keys),
                    "Items": [f"/{key}" for key in keys],
                },
            },
        )
    except Exception:
        logger.warning("Falha ao invalidar CloudFront para %s", keys, exc_info=True)


def _connect():
    # Portado para RDS Data API via app/db.py - ver docs/aws-migration-plan.md, Fase 3, item 1.
    try:
        return get_conn()
    except DataApiError:
        logger.exception("Banco indisponivel ao conectar (RDS Data API).")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "database_unavailable",
                "message": "Não foi possível conectar ao banco (RDS Data API) da Fitness API.",
            },
        )


def _require_admin_identity(
    x_api_token: Optional[str],
    musclefit_session: Optional[str] = None,
) -> security.Identity:
    return _assert_admin(_require_identity(x_api_token, musclefit_session))


def _assert_admin(identity: security.Identity) -> security.Identity:
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
    return identity


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, timedelta):
        # PyMySQL retorna colunas TIME como datetime.timedelta; converte
        # para "HH:MM:SS" (formato aceito por TIME_RE, ver abaixo) em vez
        # de deixar o encoder JSON padrao serializar como float de segundos.
        total_seconds = int(value.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _to_json(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value)


def _row_jsonable(row: Dict[str, Any]) -> Dict[str, Any]:
    result = {key: _jsonable(value) for key, value in row.items()}
    if isinstance(result.get("usual_weekdays"), str):
        try:
            result["usual_weekdays"] = json.loads(result["usual_weekdays"])
        except ValueError:
            pass
    if "photo_path" in result:
        photo_path = result.pop("photo_path")
        result["has_photo"] = bool(photo_path)
        result["photo_url"] = f"https://{PUBLIC_ASSETS_DOMAIN}/{photo_path}" if photo_path else None
    if "logo_path" in result:
        logo_path = result.pop("logo_path")
        result["has_logo"] = bool(logo_path)
        result["logo_url"] = f"https://{PUBLIC_ASSETS_DOMAIN}/{logo_path}" if logo_path else None
    return result


TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(:([0-5]\d))?$")


def _validate_time_field(value: Any, field_name: str) -> Any:
    """Valida opens_at/closes_at como HH:MM ou HH:MM:SS. None limpa o campo."""
    if value is None:
        return None
    if not isinstance(value, str) or not TIME_RE.match(value):
        raise HTTPException(status_code=400, detail=f"{field_name} inválido. Use o formato HH:MM ou HH:MM:SS.")
    return value


def _rows_jsonable(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_row_jsonable(row) for row in rows]


def _save_photo(table: str, row_id: int, subdir: str, file: UploadFile) -> str:
    """Sobe a foto para o S3 (musclefit-uploads) e devolve a key
    (`{subdir}/{row_id}{ext}`, guardada em `gyms.logo_path`/`equipment.photo_path`
    - mesmo formato de "caminho relativo" de antes, so que agora e uma S3
    key em vez de um caminho de disco)."""
    contents = read_limited_photo(file)
    # SEG-11: o tipo e a extensao saem da assinatura dos primeiros bytes; o
    # `content_type` declarado pelo cliente e ignorado.
    detected_type = sniff_image_content_type(contents[:16])
    ext = PHOTO_CONTENT_TYPES.get(detected_type or "")
    if not ext:
        raise HTTPException(status_code=400, detail="Formato de imagem não suportado. Use JPEG, PNG, WEBP ou GIF.")

    s3 = _s3_client()
    bucket = _s3_bucket()

    # Remove qualquer foto anterior do mesmo registro, mesmo com extensao
    # diferente (equivalente ao glob(f"{row_id}.*") + unlink de antes).
    prefix = f"{subdir}/{row_id}."
    listing = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    old_keys = [obj["Key"] for obj in listing.get("Contents", [])]
    for old_key in old_keys:
        s3.delete_object(Bucket=bucket, Key=old_key)

    key = f"{subdir}/{row_id}{ext}"
    s3.put_object(Bucket=bucket, Key=key, Body=contents, ContentType=detected_type)

    # Invalida a key nova (para nao servir 403/404 cacheado de antes do
    # upload) e qualquer key antiga com extensao diferente (para nao seguir
    # servindo a foto antiga cacheada num path que nao aponta mais para
    # nenhum registro).
    _invalidate_cloudfront_paths(old_keys + [key])

    return key


def _read_photo(key: str) -> Response:
    s3 = _s3_client()
    bucket = _s3_bucket()
    ext = "." + key.rsplit(".", 1)[-1] if "." in key else ""
    content_type = _EXT_TO_CONTENT_TYPE.get(ext, "application/octet-stream")
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.NoSuchKey:
        raise HTTPException(status_code=404, detail="Arquivo de foto não encontrado.")
    return Response(content=obj["Body"].read(), media_type=content_type)


def _delete_photo(key: str) -> None:
    s3 = _s3_client()
    s3.delete_object(Bucket=_s3_bucket(), Key=key)
    _invalidate_cloudfront_paths([key])


# ---------------------------------------------------------------------------
# gyms
# ---------------------------------------------------------------------------


@router.get("/gyms")
def list_gyms(
    active_only: bool = Query(default=True),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    # Regra especial (Multiusuario Parte 4): leitura de /gyms permanece
    # COMPARTILHADA entre todos os usuarios, sem filtro por owner_user_id -
    # so a escrita muda. Ver CLAUDE.md.
    _require_identity(x_api_token, musclefit_session)
    with _connect() as conn:
        items = list_gym_items(conn, active_only=active_only)

    return {"items": items}


def list_gym_items(conn, *, active_only: bool = True) -> List[Dict[str, Any]]:
    """Academias no formato de `GET /gyms` (leitura compartilhada, sem filtro
    por usuario). Fonte unica da rota e da geracao de ficha
    (`workout_plan_ai.gather_ficha_context`), que antes fazia self-call HTTP
    com o token mestre (SEG-04)."""
    where = "WHERE is_active = 1" if active_only else ""
    with conn.cursor() as cursor:
        cursor.execute(f"SELECT * FROM gyms {where} ORDER BY name")
        rows = cursor.fetchall()
    return _rows_jsonable(rows)


@router.get("/gyms/{gym_id}")
def get_gym(
    gym_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM gyms WHERE id = %s", (gym_id,))
            row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Academia não encontrada.")

    return {"item": _row_jsonable(row)}


@router.post("/gyms")
def create_gym(
    payload: GymCreateIn,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    identity = _require_identity(x_api_token, musclefit_session)
    item = payload.model_dump()
    name = str(item.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name é obrigatório.")

    is_recurring = item.get("is_recurring")
    is_recurring = True if is_recurring is None else bool(is_recurring)
    # Academia avulsa não tem frequência habitual: ignora usual_weekdays
    # enviado junto, mesmo que o cliente não tenha limpo o campo.
    usual_weekdays = item.get("usual_weekdays") if is_recurring else []

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO gyms (owner_user_id, name, usual_weekdays, is_recurring, notes)
                VALUES (%s, %s, CAST(%s AS JSON), %s, %s)
                ON DUPLICATE KEY UPDATE
                  usual_weekdays = VALUES(usual_weekdays),
                  is_recurring = VALUES(is_recurring),
                  notes = VALUES(notes),
                  is_active = 1
                """,
                (identity.user_id, name, _to_json(usual_weekdays), is_recurring, item.get("notes")),
            )
            cursor.execute("SELECT * FROM gyms WHERE name = %s", (name,))
            row = cursor.fetchone()

    return {"status": "ok", "item": _row_jsonable(row)}


def _assert_gym_writable(cursor, gym_id: int, identity: security.Identity) -> None:
    """Regra de dono (Multiusuario Parte 4): permite escrever se
    owner_user_id == identity.user_id OU identity.role == 'admin'. Academias
    legadas com owner_user_id NULL (dado orfao da Parte 1, sem backfill) so
    podem ser editadas/excluidas por admin - nao deixa qualquer usuario
    reivindicar uma academia orfa. 404 (nao 403) para nao vazar existencia."""
    cursor.execute("SELECT owner_user_id FROM gyms WHERE id = %s", (gym_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Academia não encontrada.")

    owner_user_id = row.get("owner_user_id")
    if identity.role == "admin":
        return
    if owner_user_id is not None and owner_user_id == identity.user_id:
        return
    raise HTTPException(status_code=404, detail="Academia não encontrada.")


ADMIN_ONLY_GYM_FIELDS = ("name", "opens_at", "closes_at")


@router.put("/gyms/{gym_id}")
def update_gym(
    gym_id: int,
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    identity = _require_identity(x_api_token, musclefit_session)

    # name/opens_at/closes_at sao admin-only, independente de quem e dono da
    # academia; os demais campos ja aceitos continuam usando
    # _assert_gym_writable (dono OU admin) sem regressao.
    has_admin_only_field = any(field in item for field in ADMIN_ONLY_GYM_FIELDS)
    if has_admin_only_field:
        identity = _require_admin_identity(x_api_token, musclefit_session)

    opens_at = _validate_time_field(item["opens_at"], "opens_at") if "opens_at" in item else None
    closes_at = _validate_time_field(item["closes_at"], "closes_at") if "closes_at" in item else None

    is_recurring = item.get("is_recurring")
    # Se o payload marca a academia como avulsa (is_recurring=false),
    # limpa usual_weekdays mesmo que o cliente não o tenha enviado — uma
    # academia avulsa não deve carregar dias de frequência habitual.
    usual_weekdays = [] if is_recurring is False else item.get("usual_weekdays")

    with _connect() as conn:
        with conn.cursor() as cursor:
            _assert_gym_writable(cursor, gym_id, identity)

            if "opens_at" in item or "closes_at" in item:
                # opens_at/closes_at aceitam null explicito para limpar o
                # campo, entao nao dá pra usar COALESCE(%s, col) - so
                # atualiza a coluna quando a chave veio no payload.
                cursor.execute(
                    """
                    UPDATE gyms
                    SET opens_at = CASE WHEN %s THEN %s ELSE opens_at END,
                        closes_at = CASE WHEN %s THEN %s ELSE closes_at END
                    WHERE id = %s
                    """,
                    (
                        "opens_at" in item,
                        opens_at,
                        "closes_at" in item,
                        closes_at,
                        gym_id,
                    ),
                )

            cursor.execute(
                """
                UPDATE gyms
                SET name = COALESCE(%s, name),
                    usual_weekdays = COALESCE(CAST(%s AS JSON), usual_weekdays),
                    is_recurring = COALESCE(%s, is_recurring),
                    notes = %s,
                    is_active = COALESCE(%s, is_active)
                WHERE id = %s
                """,
                (
                    item.get("name"),
                    _to_json(usual_weekdays),
                    is_recurring,
                    item.get("notes"),
                    item.get("is_active"),
                    gym_id,
                ),
            )
            cursor.execute("SELECT * FROM gyms WHERE id = %s", (gym_id,))
            row = cursor.fetchone()

    return {"status": "ok", "item": _row_jsonable(row)}


@router.delete("/gyms/{gym_id}")
def deactivate_gym(
    gym_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    identity = _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            _assert_gym_writable(cursor, gym_id, identity)

            cursor.execute("UPDATE gyms SET is_active = 0 WHERE id = %s", (gym_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Academia não encontrada.")

    return {"status": "ok", "id": gym_id, "is_active": False}


@router.post("/gyms/{gym_id}/logo")
def upload_gym_logo(
    gym_id: int,
    file: UploadFile = File(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM gyms WHERE id = %s", (gym_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Academia não encontrada.")

            relative_path = _save_photo("gyms", gym_id, "gyms", file)

            cursor.execute("UPDATE gyms SET logo_path = %s WHERE id = %s", (relative_path, gym_id))
            cursor.execute("SELECT * FROM gyms WHERE id = %s", (gym_id,))
            updated = cursor.fetchone()

    return {"status": "ok", "item": _row_jsonable(updated)}


@router.get("/gyms/{gym_id}/logo")
def get_gym_logo(
    gym_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    # Logo nao e informacao sensivel: liberado a qualquer usuario autenticado.
    _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT logo_path FROM gyms WHERE id = %s", (gym_id,))
            row = cursor.fetchone()

    if not row or not row.get("logo_path"):
        raise HTTPException(status_code=404, detail="Esta academia não possui logo.")

    return _read_photo(row["logo_path"])


@router.delete("/gyms/{gym_id}/logo")
def delete_gym_logo(
    gym_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT logo_path FROM gyms WHERE id = %s", (gym_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Academia não encontrada.")

            if row.get("logo_path"):
                _delete_photo(row["logo_path"])

            cursor.execute("UPDATE gyms SET logo_path = NULL WHERE id = %s", (gym_id,))

    return {"status": "ok", "id": gym_id, "has_logo": False}


# ---------------------------------------------------------------------------
# equipment (catalogo global, independente de academia/grupo muscular)
# ---------------------------------------------------------------------------


@router.get("/equipment")
def list_equipment(
    query: str = Query(default=""),
    active_only: bool = Query(default=True),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    # Catalogo global (Multiusuario Parte 4): leitura livre a qualquer
    # usuario autenticado.
    _require_identity(x_api_token, musclefit_session)

    where = []
    params: List[Any] = []

    if active_only:
        where.append("is_active = 1")
    if query:
        where.append("name LIKE %s")
        params.append(f"%{query}%")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT * FROM equipment {where_sql} ORDER BY name", tuple(params))
            rows = cursor.fetchall()

    return {"items": _rows_jsonable(rows)}


@router.post("/equipment")
def create_equipment(
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    # Catalogo global: escrita restrita a admin.
    _require_admin_identity(x_api_token, musclefit_session)

    name = str(item.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name é obrigatório.")

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO equipment (name, notes, equipment_type_id, manufacturer, model, location)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  notes = VALUES(notes),
                  equipment_type_id = VALUES(equipment_type_id),
                  manufacturer = VALUES(manufacturer),
                  model = VALUES(model),
                  location = VALUES(location),
                  is_active = 1
                """,
                (
                    name,
                    item.get("notes"),
                    item.get("equipment_type_id"),
                    item.get("manufacturer"),
                    item.get("model"),
                    item.get("location"),
                ),
            )
            cursor.execute("SELECT * FROM equipment WHERE name = %s", (name,))
            row = cursor.fetchone()

    return {"status": "ok", "item": _row_jsonable(row)}


@router.put("/equipment/{equipment_id}")
def update_equipment(
    equipment_id: int,
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM equipment WHERE id = %s", (equipment_id,))
            current = cursor.fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="Equipamento não encontrado.")

            cursor.execute(
                """
                UPDATE equipment
                SET name = COALESCE(%s, name),
                    notes = %s,
                    is_active = COALESCE(%s, is_active),
                    equipment_type_id = %s,
                    manufacturer = %s,
                    model = %s,
                    location = %s
                WHERE id = %s
                """,
                (
                    item.get("name"),
                    item.get("notes"),
                    item.get("is_active"),
                    item.get("equipment_type_id") if "equipment_type_id" in item else current["equipment_type_id"],
                    item.get("manufacturer") if "manufacturer" in item else current["manufacturer"],
                    item.get("model") if "model" in item else current["model"],
                    item.get("location") if "location" in item else current["location"],
                    equipment_id,
                ),
            )
            cursor.execute("SELECT * FROM equipment WHERE id = %s", (equipment_id,))
            row = cursor.fetchone()

    return {"status": "ok", "item": _row_jsonable(row)}


@router.delete("/equipment/{equipment_id}")
def deactivate_equipment(
    equipment_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE equipment SET is_active = 0 WHERE id = %s", (equipment_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Equipamento não encontrado.")

    return {"status": "ok", "id": equipment_id, "is_active": False}


@router.post("/equipment/{equipment_id}/photo")
def upload_equipment_photo(
    equipment_id: int,
    file: UploadFile = File(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM equipment WHERE id = %s", (equipment_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Equipamento não encontrado.")

            relative_path = _save_photo("equipment", equipment_id, "equipment", file)

            cursor.execute("UPDATE equipment SET photo_path = %s WHERE id = %s", (relative_path, equipment_id))
            cursor.execute("SELECT * FROM equipment WHERE id = %s", (equipment_id,))
            updated = cursor.fetchone()

    return {"status": "ok", "item": _row_jsonable(updated)}


@router.get("/equipment/{equipment_id}/photo")
def get_equipment_photo(
    equipment_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT photo_path FROM equipment WHERE id = %s", (equipment_id,))
            row = cursor.fetchone()

    if not row or not row.get("photo_path"):
        raise HTTPException(status_code=404, detail="Este equipamento não possui foto.")

    return _read_photo(row["photo_path"])


@router.delete("/equipment/{equipment_id}/photo")
def delete_equipment_photo(
    equipment_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_admin_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT photo_path FROM equipment WHERE id = %s", (equipment_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Equipamento não encontrado.")

            if row.get("photo_path"):
                _delete_photo(row["photo_path"])

            cursor.execute("UPDATE equipment SET photo_path = NULL WHERE id = %s", (equipment_id,))

    return {"status": "ok", "id": equipment_id, "has_photo": False}


# ---------------------------------------------------------------------------
# gym_equipment (associacao: academia + grupo muscular/mapeamento + equipamento)
# ---------------------------------------------------------------------------


@router.get("/gym-equipment")
def list_gym_equipment(
    gym_id: Optional[int] = Query(default=None),
    mapping_id: Optional[int] = Query(default=None),
    primary_group: Optional[str] = Query(default=None),
    active_only: bool = Query(default=True),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)

    where = []
    params: List[Any] = []

    if active_only:
        where.append("ge.is_active = 1")
    if gym_id is not None:
        where.append("ge.gym_id = %s")
        params.append(gym_id)
    if mapping_id is not None:
        where.append("ge.mapping_id = %s")
        params.append(mapping_id)
    if primary_group:
        where.append("pg.name = %s")
        params.append(primary_group)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    sql = f"""
        SELECT
            ge.id, ge.gym_id, g.name AS gym_name, ge.mapping_id,
            zm.name AS mapping_exercise_name,
            zm.description AS mapping_exercise_description,
            (
                SELECT zam.external_name
                FROM exercise_external_mappings zam
                WHERE zam.exercise_id = ge.mapping_id AND zam.provider = 'AMAZFIT' AND zam.is_active = 1
                ORDER BY zam.id ASC LIMIT 1
            ) AS mapping_amazfit_name,
            pg.name AS primary_group, sg.name AS secondary_group,
            ge.equipment_id, eq.name AS equipment_name, (eq.photo_path IS NOT NULL) AS has_photo,
            (CASE WHEN eq.photo_path IS NOT NULL THEN CONCAT('https://{PUBLIC_ASSETS_DOMAIN}/', eq.photo_path) ELSE NULL END) AS photo_url,
            ge.is_active, ge.created_at, ge.updated_at
        FROM gym_equipment ge
        JOIN gyms g ON g.id = ge.gym_id
        JOIN exercise_catalog zm ON zm.id = ge.mapping_id
        LEFT JOIN muscle_groups pg ON pg.id = (
            SELECT emc_pri.muscle_group_id
            FROM exercise_muscle_contributions emc_pri
            WHERE emc_pri.exercise_id = ge.mapping_id
              AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
            ORDER BY emc_pri.id ASC LIMIT 1
        )
        LEFT JOIN muscle_groups sg ON sg.id = (
            SELECT emc_sec.muscle_group_id
            FROM exercise_muscle_contributions emc_sec
            WHERE emc_sec.exercise_id = ge.mapping_id
              AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
            ORDER BY emc_sec.id ASC LIMIT 1
        )
        JOIN equipment eq ON eq.id = ge.equipment_id
        {where_sql}
        ORDER BY g.name, pg.name, eq.name
    """

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()

    return {"items": _rows_jsonable(rows)}


@router.post("/gym-equipment")
def create_gym_equipment(
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    identity = _require_identity(x_api_token, musclefit_session)

    gym_id = item.get("gym_id")
    mapping_id = item.get("mapping_id")
    equipment_id = item.get("equipment_id")

    if not gym_id or not mapping_id or not equipment_id:
        raise HTTPException(status_code=400, detail="gym_id, mapping_id e equipment_id são obrigatórios.")

    with _connect() as conn:
        with conn.cursor() as cursor:
            # SEG-03: so dono da academia ou admin escreve no vinculo.
            _assert_gym_equipment_catalog_writable(cursor, gym_id, identity)
            cursor.execute("SELECT id FROM equipment WHERE id = %s", (equipment_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="equipment_id não encontrado no catálogo.")

            cursor.execute(
                """
                INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  is_active = 1
                """,
                (gym_id, mapping_id, equipment_id),
            )
            cursor.execute(
                "SELECT * FROM gym_equipment WHERE gym_id = %s AND mapping_id = %s AND equipment_id = %s",
                (gym_id, mapping_id, equipment_id),
            )
            row = cursor.fetchone()

    return {"status": "ok", "item": _row_jsonable(row)}


@router.put("/gym-equipment/{link_id}")
def update_gym_equipment(
    link_id: int,
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    identity = _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, gym_id FROM gym_equipment WHERE id = %s", (link_id,))
            link = cursor.fetchone()
            if not link:
                raise HTTPException(status_code=404, detail="Associação não encontrada.")
            # SEG-03: so dono da academia ou admin escreve no vinculo.
            _assert_gym_equipment_catalog_writable(cursor, link["gym_id"], identity)

            cursor.execute(
                """
                UPDATE gym_equipment
                SET equipment_id = COALESCE(%s, equipment_id),
                    is_active = COALESCE(%s, is_active)
                WHERE id = %s
                """,
                (item.get("equipment_id"), item.get("is_active"), link_id),
            )
            cursor.execute("SELECT * FROM gym_equipment WHERE id = %s", (link_id,))
            row = cursor.fetchone()

    return {"status": "ok", "item": _row_jsonable(row)}


@router.delete("/gym-equipment/{link_id}")
def deactivate_gym_equipment(
    link_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    identity = _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT gym_id FROM gym_equipment WHERE id = %s", (link_id,))
            link = cursor.fetchone()
            if not link:
                raise HTTPException(status_code=404, detail="Associação não encontrada.")
            # SEG-03: so dono da academia ou admin desativa o vinculo.
            _assert_gym_equipment_catalog_writable(cursor, link["gym_id"], identity)
            cursor.execute("UPDATE gym_equipment SET is_active = 0 WHERE id = %s", (link_id,))

    return {"status": "ok", "id": link_id, "is_active": False}


_REQUIRED_TECHNIQUE_DOC = (
    "Código de técnica avançada prescrita (catálogo GET /advanced-techniques). Quando informado, os itens "
    "incompatíveis saem da lista e vão para `excluded[]` com `reason`. Sem ele, nenhum item é filtrado."
)


def _normalize_required_technique(required_technique: Optional[str]) -> Optional[str]:
    """Vazio = ausente; codigo fora do catalogo ATIVO -> 422 (mesma validacao
    do builder de modelos)."""
    code = (required_technique or "").strip() or None
    ensure_valid_technique_codes([code])
    return code


@router.get("/gym-equipment/recommend")
def recommend_gym_equipment(
    gym_id: int = Query(...),
    mapping_id: Optional[int] = Query(default=None),
    primary_group: Optional[str] = Query(default=None),
    exclude_equipment_id: Optional[int] = Query(default=None),
    required_technique: Optional[str] = Query(default=None, description=_REQUIRED_TECHNIQUE_DOC),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Sugere equipamento para o grupo muscular na academia informada, no
    ranking DO USUARIO corrente (PR-08): estrelas do PAR exercicio+equipamento DESC (ausente = 3), depois o
    menos usado recentemente POR ELE (nunca usado primeiro), nome e id. Usado
    tanto para montar ficha (variar equipamento entre sessões) quanto para
    trocar um equipamento ocupado.

    `exclude_equipment_id` refere-se ao id da ASSOCIAÇÃO (`gym_equipment.id`),
    não ao id do equipamento no catálogo — é o mesmo valor gravado em
    `exercises.equipment_id`.

    `has_equipment_for_group` indica se a academia tem QUALQUER equipamento
    ativo cadastrado para esse grupo muscular (ignorando exclude_equipment_id
    e required_technique). Quando `false`, esse grupo muscular não deve ser
    sugerido nessa academia de forma alguma — nem ao montar ficha, nem como
    alternativa de troca. Quando `true` mas `recommended` vier `null`, a
    academia tem equipamento para o grupo, porém nenhuma alternativa além da
    excluída (equipamento ocupado sem substituto cadastrado nessa academia).

    Cada item traz `user_preference_stars` (null = sem avaliação),
    `last_used_by_user_at` e a segurança do par (`max_safe_rpe`,
    `failure_allowed`, `allowed_advanced_techniques`, `safety_source`). A
    segurança só INFORMA: itens só saem da lista (para `excluded[]`) quando
    `required_technique` é passado.
    """
    identity = _require_identity(x_api_token, musclefit_session)

    if not mapping_id and not primary_group:
        raise HTTPException(status_code=400, detail="Informe mapping_id ou primary_group.")
    technique = _normalize_required_technique(required_technique)

    with _connect() as conn:
        group = primary_group
        if not group and mapping_id:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pg.name AS primary_group
                    FROM exercise_catalog zm
                    LEFT JOIN muscle_groups pg ON pg.id = (
                        SELECT emc_pri.muscle_group_id
                        FROM exercise_muscle_contributions emc_pri
                        WHERE emc_pri.exercise_id = zm.id
                          AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
                        ORDER BY emc_pri.id ASC LIMIT 1
                    )
                    WHERE zm.id = %s
                    """,
                    (mapping_id,),
                )
                mapping_row = cursor.fetchone()
            if not mapping_row:
                raise HTTPException(status_code=404, detail="mapping_id não encontrado.")
            group = mapping_row["primary_group"]

        if not group:
            raise HTTPException(status_code=422, detail="O mapeamento informado não possui grupo muscular definido.")

        payload = equipment_recommender.recommend_for_group(
            conn,
            gym_id=gym_id,
            user_id=identity.user_id,
            group=group,
            exclude_gym_equipment_id=exclude_equipment_id,
            required_technique=technique,
        )

    return {"ok": True, **payload}


@router.get("/gym-equipment/recommend-all")
def recommend_gym_equipment_all(
    gym_id: int = Query(...),
    required_technique: Optional[str] = Query(default=None, description=_REQUIRED_TECHNIQUE_DOC),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    """Mesma lógica de /gym-equipment/recommend (ranking do usuário corrente +
    segurança do par), mas para TODOS os grupos musculares de uma vez em uma
    única chamada. Use isto ao montar uma ficha de treino (em vez de chamar
    /recommend uma vez por grupo muscular) para evitar dezenas de chamadas MCP
    sequenciais. Para trocar um equipamento ocupado durante o treino (um grupo
    específico, com exclude_equipment_id), continue usando
    /gym-equipment/recommend.

    Retorna, para cada grupo muscular com exercício ativo no catálogo,
    `has_equipment_for_group`, a recomendação e `excluded[]` (só preenchido
    com `required_technique`). Grupos com `has_equipment_for_group = false`
    não devem ser sugeridos nessa academia.
    """
    identity = _require_identity(x_api_token, musclefit_session)
    technique = _normalize_required_technique(required_technique)

    with _connect() as conn:
        payload = equipment_recommender.recommend_all(
            conn, gym_id=gym_id, user_id=identity.user_id, required_technique=technique
        )

    return {"ok": True, **payload}


# ---------------------------------------------------------------------------
# gym_equipment_catalog (associacao: quais equipamentos do catalogo uma
# academia possui, SEM grupo muscular/exercicio). Escrever aqui recalcula
# gym_equipment (cache materializado) para a academia afetada.
# ---------------------------------------------------------------------------


@router.get("/gym-equipment-catalog")
def list_gym_equipment_catalog(
    gym_id: Optional[int] = Query(default=None),
    equipment_id: Optional[int] = Query(default=None),
    active_only: bool = Query(default=True),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    _require_identity(x_api_token, musclefit_session)

    where = []
    params: List[Any] = []

    if active_only:
        where.append("gec.is_active = 1")
    if gym_id is not None:
        where.append("gec.gym_id = %s")
        params.append(gym_id)
    if equipment_id is not None:
        where.append("gec.equipment_id = %s")
        params.append(equipment_id)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    sql = f"""
        SELECT
            gec.id, gec.gym_id, g.name AS gym_name,
            gec.equipment_id, eq.name AS equipment_name, (eq.photo_path IS NOT NULL) AS has_photo,
            (CASE WHEN eq.photo_path IS NOT NULL THEN CONCAT('https://{PUBLIC_ASSETS_DOMAIN}/', eq.photo_path) ELSE NULL END) AS photo_url,
            gec.is_active, gec.created_at, gec.updated_at
        FROM gym_equipment_catalog gec
        JOIN gyms g ON g.id = gec.gym_id
        JOIN equipment eq ON eq.id = gec.equipment_id
        {where_sql}
        ORDER BY g.name, eq.name
    """

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()

    return {"items": _rows_jsonable(rows)}


def _assert_gym_equipment_catalog_writable(cursor, gym_id: int, identity: security.Identity) -> None:
    """Checagem de dono para POST/DELETE /gym-equipment-catalog (Multiusuario
    Parte 4): permite se a academia pertence ao usuario (owner_user_id ==
    identity.user_id) OU identity.role == 'admin'; 403 caso contrario
    (diferente de _assert_gym_writable, que usa 404 - aqui o gym_id ja veio
    explicito no payload do chamador, entao nao ha risco de vazar
    existencia)."""
    cursor.execute("SELECT owner_user_id FROM gyms WHERE id = %s", (gym_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="gym_id não encontrado.")

    owner_user_id = row.get("owner_user_id")
    if identity.role == "admin":
        return
    if owner_user_id is not None and owner_user_id == identity.user_id:
        return
    raise HTTPException(status_code=403, detail="Você não tem permissão para gerenciar equipamentos desta academia.")


@router.post("/gym-equipment-catalog")
def create_gym_equipment_catalog(
    item: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    identity = _require_identity(x_api_token, musclefit_session)

    gym_id = item.get("gym_id")
    equipment_id = item.get("equipment_id")

    if not gym_id or not equipment_id:
        raise HTTPException(status_code=400, detail="gym_id e equipment_id são obrigatórios.")

    with _connect() as conn:
        with conn.cursor() as cursor:
            _assert_gym_equipment_catalog_writable(cursor, gym_id, identity)

            cursor.execute("SELECT id FROM equipment WHERE id = %s", (equipment_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="equipment_id não encontrado no catálogo.")

            cursor.execute(
                """
                INSERT INTO gym_equipment_catalog (gym_id, equipment_id)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE
                  is_active = 1
                """,
                (gym_id, equipment_id),
            )
            cursor.execute(
                "SELECT * FROM gym_equipment_catalog WHERE gym_id = %s AND equipment_id = %s",
                (gym_id, equipment_id),
            )
            row = cursor.fetchone()

            sync_gym_equipment_for_gym(conn, gym_id)

    return {"status": "ok", "item": _row_jsonable(row)}


@router.delete("/gym-equipment-catalog/{link_id}")
def deactivate_gym_equipment_catalog(
    link_id: int,
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    musclefit_session: Optional[str] = Cookie(default=None, alias=security.SESSION_COOKIE_NAME),
):
    identity = _require_identity(x_api_token, musclefit_session)

    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT gym_id FROM gym_equipment_catalog WHERE id = %s", (link_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Associação não encontrada.")
            gym_id = row["gym_id"]

            _assert_gym_equipment_catalog_writable(cursor, gym_id, identity)

            cursor.execute("UPDATE gym_equipment_catalog SET is_active = 0 WHERE id = %s", (link_id,))

            sync_gym_equipment_for_gym(conn, gym_id)

    return {"status": "ok", "id": link_id, "is_active": False}

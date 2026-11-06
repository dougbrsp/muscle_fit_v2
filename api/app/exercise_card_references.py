"""Fotos de referencia dos cards de exercicios (V3 fotorrealista).

O admin anexa ate `MAX_ACTIVE_REFERENCES` fotos reais do aparelho/movimento a
um exercicio; o worker as le (`active_reference_keys` -> S3) e as envia a
OpenAI junto do prompt das duas cenas. Este modulo e o dono da regra: limites,
validacao e normalizacao da imagem, SQL da tabela `exercise_card_references`
(migracao 109) e a serializacao. As rotas (`exercise_cards.py`) so chamam
`create_reference`, `list_references` e `delete_reference`.

Seguranca do upload
-------------------

- Transporte: JSON com `image_base64` SEM padding (mesmo padrao da foto do
  rotulo, por causa do CRS do WAF). NUNCA URL externa: o servidor nao busca
  nada na rede.
- O texto e recusado ANTES de decodificar se passar do tamanho que
  `MAX_UPLOAD_BYTES` teria em base64.
- O tipo vem da ASSINATURA dos bytes (PNG, JPEG, WebP), nunca do declarado.
- As dimensoes sao lidas do cabecalho e `MAX_SOURCE_PIXELS` e conferido ANTES
  de decodificar os pixels (anti-bomba de descompressao).
- A imagem e re-encodada com o Pillow: orientacao EXIF aplicada, RGB (alfa
  achatado sobre branco), maior borda reduzida a `MAX_EDGE_PX`, JPEG q90 SEM
  metadados (EXIF, GPS, texto). So esse JPEG vai para o S3 (PNG a 1536 px pesaria 2-4 MB e a cena 2 envia a cena 1 + ate 3 fotos no multipart).

Regras de negocio
-----------------

- No maximo `MAX_ACTIVE_REFERENCES` ativas por exercicio: checado na transacao
  com a linha do exercicio travada (`svc.lock_exercise`), a mesma trava que
  serializa a geracao do exercicio. Excedeu -> 409.
- Anti-IDOR: referencia de OUTRO exercicio responde 404, igual a inexistente.
- Excluir e soft-delete (`deleted_at`); o objeto do S3 sai em melhor esforco.
- A chave do S3 deriva dos ids (`exercise-card-references/{exercise_id}/
  {id}.jpg`), nunca de texto do usuario.

SQL cru com `%s`, um statement por `execute` (PyMySQL e Data API); relogio em
UTC gravado pelo codigo (`svc.utcnow`).
"""

from __future__ import annotations

import base64
import binascii
import io
import logging
import re
from typing import Any, Optional

from PIL import Image, ImageOps

from . import exercise_card_service as svc
from . import exercise_card_storage as storage

logger = logging.getLogger("musclefit.exercise_cards")

TABLE = "exercise_card_references"

MAX_ACTIVE_REFERENCES = 3
MAX_NOTE_CHARS = 160
# Decodificado. O API Gateway REST tem binary_media_types = ["*/*"] (infra/api_gateway.tf:25) e
# re-codifica TODO o corpo em base64 para a Lambda: 3 MiB virariam ~4,19 MB de base64 no JSON e
# ~5,6 MB no evento, contra o teto sincrono de 6 MB (acima disso e 413 cru, sem {"detail"} e sem
# CORS). Com 2 MiB, a conta "x4/3 duas vezes" fica em ~3,7 MB. O front ja reduz a <= 1536 px
# JPEG q90 (normalmente < 1 MB).
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_EDGE_PX = 1536
MIN_EDGE_PX = 256
MAX_SOURCE_PIXELS = 40_000_000  # conferido no cabecalho, antes de decodificar
CLIENT_RESIZE_JPEG_QUALITY_PERCENT = 90  # exposto ao front em `limits`
ACCEPTED_TYPES = ("image/png", "image/jpeg", "image/webp")  # pela assinatura, nunca pelo declarado

# base64: cada bloco de 3 bytes vira 4 caracteres.
BASE64_BLOCK_BYTES = 3
BASE64_BLOCK_CHARS = 4
MAX_BASE64_CHARS = -(-MAX_UPLOAD_BYTES // BASE64_BLOCK_BYTES) * BASE64_BLOCK_CHARS
_BASE64_UNPADDED = re.compile(r"[A-Za-z0-9+/]*")

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_RIFF = b"RIFF"
_WEBP = b"WEBP"

# Codigos de erro estaveis (`detail.error`) e status HTTP.
CODE_INVALID_ENCODING = "reference_invalid_encoding"  # 400
CODE_UNSUPPORTED_TYPE = "reference_unsupported_type"  # 400
CODE_TOO_LARGE = "reference_too_large"  # 400
CODE_TOO_SMALL = "reference_too_small"  # 400
CODE_TOO_MANY_PIXELS = "reference_too_many_pixels"  # 400
CODE_NOTE_TOO_LONG = "reference_note_too_long"  # 422
CODE_NOT_FOUND = "exercise_card_reference_not_found"  # 404
CODE_LIMIT = "exercise_card_reference_limit"  # 409
CODE_STORAGE_FAILED = "exercise_card_reference_storage_failed"  # 502

_BAD_REQUEST = 400


def limits() -> dict[str, Any]:
    """Limites que o front mostra/usa (ele nao escreve esses numeros)."""
    return {
        "max_active": MAX_ACTIVE_REFERENCES,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "resize_max_edge_px": MAX_EDGE_PX,
        "resize_jpeg_quality_percent": CLIENT_RESIZE_JPEG_QUALITY_PERCENT,
        "accepted_types": list(ACCEPTED_TYPES),
        "note_max_chars": MAX_NOTE_CHARS,
    }


# ---------------------------------------------------------------------------
# Entrada: nota, base64 e imagem
# ---------------------------------------------------------------------------


def normalize_note(value: Any) -> Optional[str]:
    """Texto da dica sem espacos nas pontas e com espacos internos colapsados;
    vazio -> None. Acima de `MAX_NOTE_CHARS` -> 422."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if len(text) > MAX_NOTE_CHARS:
        raise svc.ExerciseCardError(
            CODE_NOTE_TOO_LONG, 422, f"A dica da foto pode ter no máximo {MAX_NOTE_CHARS} caracteres."
        )
    return text


def decode_upload(text: Any) -> bytes:
    """Bytes da foto a partir do base64 do corpo (com ou sem padding). Vazio,
    fora do alfabeto ou ilegivel -> 400 `reference_invalid_encoding`; texto
    maior que o limite (conferido antes de decodificar) -> 400
    `reference_too_large`."""
    if not isinstance(text, str) or not text:
        raise svc.ExerciseCardError(CODE_INVALID_ENCODING, _BAD_REQUEST, "A foto não foi enviada.")
    bare = text.rstrip("=")
    if len(bare) > MAX_BASE64_CHARS:
        raise svc.ExerciseCardError(CODE_TOO_LARGE, _BAD_REQUEST, "A foto é grande demais.")
    if len(text) - len(bare) > 2 or not _BASE64_UNPADDED.fullmatch(bare) or len(bare) % BASE64_BLOCK_CHARS == 1:
        raise svc.ExerciseCardError(CODE_INVALID_ENCODING, _BAD_REQUEST, "A foto está em um formato inválido.")
    padded = bare + "=" * (-len(bare) % BASE64_BLOCK_CHARS)
    try:
        return base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        raise svc.ExerciseCardError(
            CODE_INVALID_ENCODING, _BAD_REQUEST, "A foto está em um formato inválido."
        ) from None


def sniff_content_type(raw: bytes) -> Optional[str]:
    """Tipo detectado pela assinatura dos bytes (ou None)."""
    head = bytes(raw[:12])
    if head.startswith(_PNG_SIGNATURE):
        return "image/png"
    if head.startswith(_JPEG_SIGNATURE):
        return "image/jpeg"
    if head[:4] == _RIFF and head[8:12] == _WEBP:
        return "image/webp"
    return None


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    """RGB; alfa achatado sobre branco (nao sobre preto, que apagaria a foto)."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def normalize_reference_image(raw: bytes) -> tuple[bytes, int, int]:
    """Valida e re-encoda a foto: `(jpeg, largura, altura)` do JPEG final (RGB,
    sem metadados, maior borda <= `MAX_EDGE_PX`). Qualquer problema ->
    `svc.ExerciseCardError` 400 com o codigo da regra violada; nunca outra
    excecao do Pillow."""
    if not raw:
        raise svc.ExerciseCardError(CODE_INVALID_ENCODING, _BAD_REQUEST, "A foto não foi enviada.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise svc.ExerciseCardError(CODE_TOO_LARGE, _BAD_REQUEST, "A foto é grande demais.")
    if sniff_content_type(raw) not in ACCEPTED_TYPES:
        raise svc.ExerciseCardError(
            CODE_UNSUPPORTED_TYPE, _BAD_REQUEST, "Formato de foto não suportado. Use PNG, JPEG ou WebP."
        )
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            width, height = probe.size
            if width * height > MAX_SOURCE_PIXELS:  # ANTES de decodificar os pixels
                raise svc.ExerciseCardError(CODE_TOO_MANY_PIXELS, _BAD_REQUEST, "A foto tem pixels demais.")
            if min(width, height) < MIN_EDGE_PX:
                raise svc.ExerciseCardError(
                    CODE_TOO_SMALL, _BAD_REQUEST, f"A foto é pequena demais (mínimo {MIN_EDGE_PX} px no menor lado)."
                )
            probe.load()
            oriented = ImageOps.exif_transpose(probe)
            rgb = _flatten_to_rgb(oriented)
    except svc.ExerciseCardError:
        raise
    except Image.DecompressionBombError:
        raise svc.ExerciseCardError(CODE_TOO_MANY_PIXELS, _BAD_REQUEST, "A foto tem pixels demais.") from None
    except Exception:  # noqa: BLE001 - fronteira de parser: qualquer falha = arquivo ruim
        raise svc.ExerciseCardError(
            CODE_UNSUPPORTED_TYPE, _BAD_REQUEST, "Não foi possível ler a foto. Use PNG, JPEG ou WebP."
        ) from None
    if max(rgb.size) > MAX_EDGE_PX:
        rgb.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX), Image.Resampling.LANCZOS)
    clean = Image.frombytes("RGB", rgb.size, rgb.tobytes())  # sem info/EXIF
    buffer = io.BytesIO()
    clean.save(buffer, format="JPEG", quality=CLIENT_RESIZE_JPEG_QUALITY_PERCENT)
    return buffer.getvalue(), clean.width, clean.height


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------


def active_reference_rows(cursor, exercise_id: int) -> list[dict[str, Any]]:
    """Fotos ATIVAS do exercicio, da mais antiga para a mais nova, no maximo
    `MAX_ACTIVE_REFERENCES` (indice `idx_ecr_exercise_active`)."""
    cursor.execute(
        f"SELECT id, s3_key, width, height, bytes, note, created_at FROM {TABLE} "
        "WHERE exercise_id = %s AND deleted_at IS NULL ORDER BY id LIMIT %s",
        (int(exercise_id), MAX_ACTIVE_REFERENCES),
    )
    return [dict(row) for row in (cursor.fetchall() or [])]


def active_reference_keys(cursor, exercise_id: int) -> list[str]:
    """Chaves S3 das fotos ativas (mesma ordem de `active_reference_rows`):
    o contrato com o worker, que le os bytes com
    `exercise_card_storage.get_reference_bytes`."""
    cursor.execute(
        f"SELECT s3_key FROM {TABLE} WHERE exercise_id = %s AND deleted_at IS NULL ORDER BY id LIMIT %s",
        (int(exercise_id), MAX_ACTIVE_REFERENCES),
    )
    return [str(row["s3_key"]) for row in (cursor.fetchall() or [])]


def count_active(cursor, exercise_id: int) -> int:
    cursor.execute(
        f"SELECT COUNT(*) AS n FROM {TABLE} WHERE exercise_id = %s AND deleted_at IS NULL",
        (int(exercise_id),),
    )
    return int((cursor.fetchone() or {}).get("n") or 0)


def _exercise_not_found() -> svc.ExerciseCardError:
    return svc.ExerciseCardError(svc.CODE_EXERCISE_NOT_FOUND, 404, "Exercício não encontrado.")


def _storage_failed(exc: Exception) -> svc.ExerciseCardError:
    logger.warning("Falha de S3 nas fotos de referencia: %s", type(exc).__name__)
    return svc.ExerciseCardError(CODE_STORAGE_FAILED, 502, "Não foi possível guardar a foto. Tente novamente.")


# ---------------------------------------------------------------------------
# Serializacao
# ---------------------------------------------------------------------------


def serialize_reference(row: dict[str, Any], url: Optional[storage.PresignedUrl]) -> dict[str, Any]:
    """`Ref` da API. A chave S3 nunca sai; `url` e a pre-assinada de 15 min
    (`None` se a assinatura falhou: a lista ainda sai)."""
    return {
        "id": int(row["id"]),
        "url": url.url if url else None,
        "url_expires_at": svc.iso(url.expires_at) if url else None,
        "width": int(row["width"]),
        "height": int(row["height"]),
        "bytes": int(row["bytes"]),
        "note": row.get("note"),
        "created_at": svc.iso(row.get("created_at")),
    }


def _sign(key: str) -> Optional[storage.PresignedUrl]:
    try:
        return storage.presign_reference_url(key)
    except Exception:  # noqa: BLE001 - sem miniatura, mas a resposta sai
        logger.warning("Falha ao assinar a foto de referencia", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Operacoes (chamadas pelas rotas; todas exigem a flag ligada)
# ---------------------------------------------------------------------------


def list_references(*, exercise_id: int) -> dict[str, Any]:
    svc.require_enabled()
    with svc.transaction() as conn:
        with conn.cursor() as cursor:
            if not svc.exercise_exists(cursor, exercise_id):
                raise _exercise_not_found()
            rows = active_reference_rows(cursor, exercise_id)
    return {"items": [serialize_reference(row, _sign(str(row["s3_key"]))) for row in rows], "limits": limits()}


def create_reference(*, exercise_id: int, user_id: int, image_base64: Any, note: Any = None) -> dict[str, Any]:
    """Valida, normaliza e guarda a foto. Ordem: nota (barato) -> base64 ->
    imagem (CPU, fora de qualquer transacao) -> transacao com o exercicio
    travado: limite de ativas, INSERT, S3. Falha do S3 desfaz a linha (rollback);
    falha depois do upload apaga o objeto (melhor esforco)."""
    svc.require_enabled()
    clean_note = normalize_note(note)
    png, width, height = normalize_reference_image(decode_upload(image_base64))
    now = svc.utcnow()
    uploaded_key: Optional[str] = None
    try:
        with svc.transaction() as conn:
            with conn.cursor() as cursor:
                if not svc.lock_exercise(cursor, exercise_id):
                    raise _exercise_not_found()
                if count_active(cursor, exercise_id) >= MAX_ACTIVE_REFERENCES:
                    raise svc.ExerciseCardError(
                        CODE_LIMIT,
                        409,
                        f"Este exercício já tem {MAX_ACTIVE_REFERENCES} fotos de referência. Exclua uma para enviar outra.",
                    )
                cursor.execute(
                    f"INSERT INTO {TABLE} (exercise_id, s3_key, content_type, width, height, bytes, note, created_by, "
                    "created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (int(exercise_id), "", storage.CONTENT_TYPE_JPEG, width, height, len(png), clean_note, int(user_id), now),
                )
                ref_id = int(cursor.lastrowid)
                key = storage.reference_key(exercise_id, ref_id)
                cursor.execute(f"UPDATE {TABLE} SET s3_key = %s WHERE id = %s", (key, ref_id))
                try:
                    storage.put_reference(exercise_id, ref_id, png)
                except storage.ExerciseCardStorageError as exc:
                    raise _storage_failed(exc) from None
                uploaded_key = key
                row = {
                    "id": ref_id, "s3_key": key, "width": width, "height": height, "bytes": len(png),
                    "note": clean_note, "created_at": now,
                }
    except Exception:
        _discard_object(uploaded_key)
        raise
    return serialize_reference(row, _sign(key))


def delete_reference(*, exercise_id: int, ref_id: int, user_id: int) -> None:
    """Soft-delete da foto (some da lista e do worker) e remocao do objeto em
    melhor esforco. Foto de outro exercicio, inexistente ou ja excluida -> 404."""
    svc.require_enabled()
    key: Optional[str] = None
    with svc.transaction() as conn:
        with conn.cursor() as cursor:
            if not svc.lock_exercise(cursor, exercise_id):
                raise _exercise_not_found()
            cursor.execute(
                f"SELECT s3_key FROM {TABLE} WHERE id = %s AND exercise_id = %s AND deleted_at IS NULL",
                (int(ref_id), int(exercise_id)),
            )
            found = cursor.fetchone()
            if not found:
                raise svc.ExerciseCardError(CODE_NOT_FOUND, 404, "Foto de referência não encontrada.")
            key = str(found["s3_key"])
            cursor.execute(
                f"UPDATE {TABLE} SET deleted_at = %s WHERE id = %s AND exercise_id = %s AND deleted_at IS NULL",
                (svc.utcnow(), int(ref_id), int(exercise_id)),
            )
    logger.info("Foto de referencia %s do exercicio %s excluida pelo usuario %s", ref_id, exercise_id, user_id)
    _discard_object(key)


def _discard_object(key: Optional[str]) -> None:
    """Remove o objeto do S3 sem nunca levantar (a linha ja mudou de estado)."""
    if not key:
        return
    try:
        storage.delete_reference(key)
    except Exception:  # noqa: BLE001 - melhor esforco: orfao privado e inofensivo
        logger.warning("Nao foi possivel remover o objeto da foto de referencia", exc_info=True)

"""Foto do rótulo nutricional: upload, armazenamento, redução da foto no
navegador e a revisão determinística da leitura feita pela IA.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7 e §56; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrões `foto_rotulo_nutricao`
e `foto_rotulo_numeros`). Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`
§4, §8.1, §8.3, §9 (b) e §12. Nasceu no Lote L6. A cota e o job type da
feature são de `ai_usage` (L5a); os campos e textos da RDC 429/2020 + IN
75/2020, de `labels`; as regras de entrada do alimento, de `catalog`; Atwater,
consistência do rótulo e kJ -> kcal, de `meal_math`.

- Upload (`POST /nutrition/foods/label-photo`, api-core): JPEG
  (`LABEL_PHOTO_CONTENT_TYPE`; o tipo sai da assinatura dos bytes, nunca do que
  o cliente declara) até `LABEL_PHOTO_MAX_BYTES` (1,5 MiB, plano §8.1): acima,
  413; outro tipo, 415. Uma imagem por leitura (`LABEL_PHOTO_IMAGES_PER_READING`).
  O corpo é JSON com a foto em base64 SEM padding (`decode_upload`), não
  multipart: o alfabeto do base64 não forma as assinaturas que o CRS do WAF
  bloqueia no corpo desde o #579, e bytes de JPEG crus poderiam formar.
- Redução no navegador (`RESIZE_MAX_EDGE_PX` 1280 px na maior borda,
  `RESIZE_JPEG_QUALITY` 0,85; plano §9 b): a tela SEMPRE reencoda a foto por
  canvas antes de enviar (sem EXIF/XMP, tamanho dentro do limite); o arquivo
  cru nunca é enviado.
- Chave S3 (`label_photo_key`): `nutrition-labels/{user_id}/{uuid hex}.jpg`, só
  ids internos (nada de texto do usuário no caminho). `is_label_photo_key_of`
  confere que a chave é daquele usuário (o worker não lê foto de outro).
  Retenção de `LABEL_PHOTO_RETENTION_DAYS` (30 d, plano §9): espelho da regra
  `nutrition-labels-expire-30d` de `infra/s3_uploads.tf`, com teste de paridade.
- Revisão da leitura (`review_reading`): a IA só TRANSCREVE o rótulo; quem
  decide é o motor. Base g/ml (sem base lida, a padrão do catálogo, com aviso);
  energia em kJ sem kcal vira kcal por `meal_math.kj_to_kcal`; campo que a
  tabela declara "sem quantidade significativa" (a IA só lista o código em
  `not_significant`) vira zero com aviso, sem sobrescrever valor impresso;
  campo por 100 ausente com o valor por porção e a porção lidos vira por 100
  por `meal_math.per_reference_from_serving` (aviso por campo); entrada pelas regras de `catalog`
  (`validate_food_values`: erros bloqueiam o cadastro, avisos não; inclui
  Atwater); por porção × por 100 pela tolerância de `meal_math.label_consistency`
  (`LABEL_SERVING_TOLERANCE_SHARE`); código de barras pelo GTIN de
  `external_sources`. Foto ilegível não pré-preenche nada.

Defaults de desenho, SEM fonte bibliográfica: 1,5 MiB, 1280 px, 0,85 e 30 dias
(plano §8.1, §9 b e §9); `PREFILL_DECIMALS` é a escala das colunas DECIMAL(…,2)
da migração 080.

Módulo PURO: sem banco, sem FastAPI, sem I/O e sem relógio.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Mapping, Optional

from . import catalog
from .anthropometrics import non_negative_number, positive_number
from .body_composition import PERCENT
from .external_sources import normalize_gtin
from .meal_math import (
    CARB_FIELD,
    ENERGY_FIELD,
    FAT_FIELD,
    FIBER_FIELD,
    PROTEIN_FIELD,
    AtwaterCheck,
    LabelCheck,
    Unit,
    kj_to_kcal,
    label_consistency,
    nutrients_from_values,
    per_reference_from_serving,
)


class LabelLegibility(str, Enum):
    """Quanto da tabela a IA conseguiu ler (dito por ela; o motor só respeita)."""

    OK = "ok"
    PARTIAL = "partial"
    UNREADABLE = "unreadable"


# ---------------------------------------------------------------------------
# Upload e armazenamento (plano §8.1 e §9)
# ---------------------------------------------------------------------------

BYTES_PER_MIB: Final[int] = 1024 * 1024
LABEL_PHOTO_MAX_MIB: Final[float] = 1.5
LABEL_PHOTO_MAX_BYTES: Final[int] = int(LABEL_PHOTO_MAX_MIB * BYTES_PER_MIB)
# A tela reencoda tudo em JPEG (canvas); a api-core só aceita o que ela gera.
LABEL_PHOTO_CONTENT_TYPE: Final[str] = "image/jpeg"
LABEL_PHOTO_ACCEPTED_CONTENT_TYPES: Final[frozenset[str]] = frozenset({LABEL_PHOTO_CONTENT_TYPE})
LABEL_PHOTO_EXTENSION: Final[str] = ".jpg"
LABEL_PHOTO_S3_PREFIX: Final[str] = "nutrition-labels"
LABEL_PHOTO_RETENTION_DAYS: Final[int] = 30
LABEL_PHOTO_IMAGES_PER_READING: Final[int] = 1

# Redução no navegador (canvas): maior borda e qualidade do JPEG reencodado. A
# qualidade fica em % (a escala dos editores de imagem; o literal da fração em
# Python é reservado pela guarda `limiar_fadiga`); o canvas recebe a fração.
RESIZE_MAX_EDGE_PX: Final[int] = 1280
RESIZE_JPEG_QUALITY_PERCENT: Final[int] = 85
RESIZE_JPEG_QUALITY: Final[float] = RESIZE_JPEG_QUALITY_PERCENT / PERCENT

# `detail` do `input_image` da Responses API: a tabela tem letra miúda.
LABEL_IMAGE_DETAIL: Final[str] = "high"

# Casas dos valores pré-preenchidos (escala das colunas DECIMAL da 080).
PREFILL_DECIMALS: Final[int] = 2
# Observações livres da IA mostradas ao usuário (limite de exibição).
AI_NOTES_MAX: Final[int] = 5
AI_NOTE_MAX_CHARS: Final[int] = 200

# Transporte (decisão da revisão de impacto, 2026-10-18): a foto vai num JSON como
# base64 SEM padding. O CRS do WAF (bloqueio de XSS/LFI/RFI/SSRF no corpo desde o
# #579) lê o corpo como texto; bytes de JPEG crus num multipart podem casar uma
# assinatura por acaso, e o alfabeto `[A-Za-z0-9+/]` não forma nenhuma (sem `<`, `:`,
# `.`, `=`, aspas, `&` nem `%`). O limite vale para os bytes decodificados; o texto
# é recusado antes de decodificar se passar do tamanho que esses bytes teriam.
# Base64: cada bloco de 3 bytes vira 4 caracteres.
BASE64_BLOCK_BYTES: Final[int] = 3
BASE64_BLOCK_CHARS: Final[int] = 4
LABEL_PHOTO_MAX_BASE64_CHARS: Final[int] = -(-LABEL_PHOTO_MAX_BYTES // BASE64_BLOCK_BYTES) * BASE64_BLOCK_CHARS
_BASE64_UNPADDED = re.compile(r"[A-Za-z0-9+/]*")

ERR_LABEL_PHOTO_EMPTY = "label_photo_empty"
ERR_LABEL_PHOTO_INVALID_ENCODING = "label_photo_invalid_encoding"
ERR_LABEL_PHOTO_TOO_LARGE = "label_photo_too_large"
ERR_LABEL_PHOTO_UNSUPPORTED_TYPE = "label_photo_unsupported_type"
ERR_LABEL_PHOTO_STORAGE_FAILED = "label_photo_storage_failed"
ERR_LABEL_PHOTO_DISPATCH_FAILED = "label_photo_dispatch_failed"
ERR_LABEL_PHOTO_KEY_INVALID = "label_photo_key_invalid"
ERR_LABEL_PHOTO_NOT_FOUND = "label_photo_not_found"
ERR_LABEL_READING_INVALID = "label_reading_invalid"

_UUID_HEX = re.compile(r"[0-9a-f]{32}")


def accepts_content_type(content_type: Optional[str]) -> bool:
    """O tipo detectado pela assinatura da imagem é aceito?"""
    return content_type in LABEL_PHOTO_ACCEPTED_CONTENT_TYPES


def upload_error(size_bytes: int, content_type: Optional[str]) -> Optional[str]:
    """Código do erro do upload (vazio, grande demais, tipo fora) ou None."""
    if size_bytes <= 0:
        return ERR_LABEL_PHOTO_EMPTY
    if size_bytes > LABEL_PHOTO_MAX_BYTES:
        return ERR_LABEL_PHOTO_TOO_LARGE
    if not accepts_content_type(content_type):
        return ERR_LABEL_PHOTO_UNSUPPORTED_TYPE
    return None


def decode_upload(text: Any) -> tuple[Optional[bytes], Optional[str]]:
    """Bytes da foto a partir do base64 do corpo (com ou sem padding), ou o código
    do erro: vazio, maior que o limite (antes de decodificar) ou fora do alfabeto."""
    if not isinstance(text, str) or not text:
        return None, ERR_LABEL_PHOTO_EMPTY
    sem_padding = text.rstrip("=")
    if len(sem_padding) > LABEL_PHOTO_MAX_BASE64_CHARS:
        return None, ERR_LABEL_PHOTO_TOO_LARGE
    if len(text) - len(sem_padding) > 2 or not _BASE64_UNPADDED.fullmatch(sem_padding):
        return None, ERR_LABEL_PHOTO_INVALID_ENCODING
    if len(sem_padding) % BASE64_BLOCK_CHARS == 1:
        return None, ERR_LABEL_PHOTO_INVALID_ENCODING
    preenchido = sem_padding + "=" * (-len(sem_padding) % BASE64_BLOCK_CHARS)
    try:
        dados = base64.b64decode(preenchido, validate=True)
    except (binascii.Error, ValueError):
        return None, ERR_LABEL_PHOTO_INVALID_ENCODING
    return dados, None


def _user_id(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        numero = int(value)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 and str(numero) == str(value).strip() else None


def label_photo_key(user_id: Any, photo_id: str) -> str:
    """`nutrition-labels/{user_id}/{uuid hex}.jpg` (só ids internos)."""
    usuario = _user_id(user_id)
    if usuario is None:
        raise ValueError(f"user_id inválido para a chave da foto: {user_id!r}")
    if not isinstance(photo_id, str) or not _UUID_HEX.fullmatch(photo_id):
        raise ValueError(f"id da foto precisa ser um uuid em hex (32 caracteres): {photo_id!r}")
    return f"{LABEL_PHOTO_S3_PREFIX}/{usuario}/{photo_id}{LABEL_PHOTO_EXTENSION}"


def is_label_photo_key_of(key: Any, user_id: Any) -> bool:
    """A chave tem a forma de `label_photo_key` E é do usuário?"""
    usuario = _user_id(user_id)
    if usuario is None or not isinstance(key, str):
        return False
    prefixo, barra, resto = key.partition("/")
    dono, barra2, arquivo = resto.partition("/")
    if prefixo != LABEL_PHOTO_S3_PREFIX or not barra or not barra2 or dono != str(usuario):
        return False
    nome, extensao = arquivo[: -len(LABEL_PHOTO_EXTENSION)], arquivo[-len(LABEL_PHOTO_EXTENSION):]
    return extensao == LABEL_PHOTO_EXTENSION and bool(_UUID_HEX.fullmatch(nome))


# ---------------------------------------------------------------------------
# Campos da leitura (códigos da RDC 429 de `labels.RDC429_FIELDS`)
# ---------------------------------------------------------------------------

# Energia em kJ: a tabela da RDC 429 traz as duas; o catálogo guarda só kcal.
ENERGY_KJ_FIELD: Final[str] = "energy_kj"
# Linhas de nutriente da tabela (por 100 e por porção), na ordem da RDC 429.
LABEL_NUTRIENT_FIELDS: Final[tuple[str, ...]] = (
    catalog.PER_100_FIELDS[0],
    ENERGY_KJ_FIELD,
    *catalog.PER_100_FIELDS[1:],
)
assert LABEL_NUTRIENT_FIELDS[0] == ENERGY_FIELD

# `meal_math.label_consistency` fala o vocabulário de `Macros`; o aviso usa o código da RDC 429.
_LABEL_CHECK_FIELD_CODES: Final[Mapping[str, str]] = {
    "kcal": ENERGY_FIELD,
    "protein_g": PROTEIN_FIELD,
    "carb_g": CARB_FIELD,
    "fat_g": FAT_FIELD,
    "fiber_g": FIBER_FIELD,
}

WARN_LABEL_PARTIAL = "label_partially_legible"
ERR_LABEL_UNREADABLE = "label_unreadable"
WARN_LABEL_BASIS_ASSUMED = "label_basis_assumed"
WARN_LABEL_ENERGY_FROM_KJ = "label_energy_from_kj"
WARN_LABEL_NAME_MISSING = "label_name_missing"
# `<código>:<campo da RDC 429>`.
WARN_LABEL_DERIVED_FROM_SERVING = "label_derived_from_serving"
WARN_LABEL_SERVING_INCONSISTENT = "label_serving_inconsistent"
WARN_LABEL_NOT_SIGNIFICANT = "label_not_significant"


def normalize_legibility(raw: Any) -> LabelLegibility:
    """Legibilidade dita pela IA; código desconhecido vale como parcial (revisar)."""
    try:
        return LabelLegibility(raw.value if isinstance(raw, Enum) else str(raw).strip().lower())
    except ValueError:
        return LabelLegibility.PARTIAL


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    texto = " ".join(str(value).split())
    return texto or None


def _rounded(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, PREFILL_DECIMALS)


def clean_ai_notes(raw: Any) -> tuple[str, ...]:
    """Observações livres da IA para exibir: espaços normalizados, sem repetir,
    até `AI_NOTES_MAX` notas de até `AI_NOTE_MAX_CHARS` caracteres."""
    notas: list[str] = []
    for item in raw if isinstance(raw, (list, tuple)) else ():
        texto = " ".join(str(item).split())[:AI_NOTE_MAX_CHARS]
        if texto and texto not in notas:
            notas.append(texto)
        if len(notas) >= AI_NOTES_MAX:
            break
    return tuple(notas)


def _not_significant(raw: Any) -> tuple[str, ...]:
    """Campos que a tabela declara sem quantidade significativa (códigos do catálogo)."""
    pedidos = {str(item).strip() for item in raw} if isinstance(raw, (list, tuple)) else set()
    return tuple(campo for campo in catalog.PER_100_FIELDS if campo in pedidos)


@dataclass(frozen=True)
class LabelReview:
    """Leitura revisada: o que pré-preenche o cadastro (`food`), o valor por
    porção lido, os erros que bloqueiam o cadastro (regras de `catalog`) e os
    avisos para o usuário conferir."""

    legibility: LabelLegibility
    name: Optional[str]
    brand: Optional[str]
    barcode: Optional[str]
    basis: Unit
    per_100: Mapping[str, Optional[float]]
    per_serving: Mapping[str, Optional[float]]
    serving_size: Optional[float]
    household_measure: Optional[str]
    servings_per_package: Optional[float]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    atwater: Optional[AtwaterCheck]
    label_check: Optional[LabelCheck]

    @property
    def readable(self) -> bool:
        return self.legibility != LabelLegibility.UNREADABLE

    def food_prefill(self) -> dict[str, Any]:
        """Campos do cadastro (`FoodItemIn`), na base do motor; `None` = não lido."""
        return {
            "name": self.name,
            "brand": self.brand,
            "category": None,
            "basis": self.basis.value,
            **{campo: self.per_100.get(campo) for campo in catalog.PER_100_FIELDS},
            "serving_size": self.serving_size,
            "household_measure": self.household_measure,
            "servings_per_package": self.servings_per_package,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "legibility": self.legibility.value,
            "readable": self.readable,
            "barcode": self.barcode,
            "food": self.food_prefill(),
            "per_serving": dict(self.per_serving),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "atwater": self.atwater.to_dict() if self.atwater else None,
            "label_check": self.label_check.to_dict() if self.label_check else None,
        }


def _nutrients(raw: Any) -> dict[str, Any]:
    """Valores crus da IA por campo (os de fora de `LABEL_NUTRIENT_FIELDS` ficam de fora)."""
    origem = raw if isinstance(raw, Mapping) else {}
    return {campo: origem.get(campo) for campo in LABEL_NUTRIENT_FIELDS}


def _with_energy(valores: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """kcal ausente com kJ lido -> kcal pelo dono da conversão (True quando converteu)."""
    if valores.get(ENERGY_FIELD) is None:
        kcal = kj_to_kcal(valores.get(ENERGY_KJ_FIELD))
        if kcal is not None:
            return {**valores, ENERGY_FIELD: _rounded(kcal)}, True
    return valores, False


def _unreadable(legibilidade: LabelLegibility, basis: Unit) -> LabelReview:
    vazio = {campo: None for campo in catalog.PER_100_FIELDS}
    return LabelReview(
        legibility=legibilidade,
        name=None,
        brand=None,
        barcode=None,
        basis=basis,
        per_100=vazio,
        per_serving=dict(vazio),
        serving_size=None,
        household_measure=None,
        servings_per_package=None,
        errors=(ERR_LABEL_UNREADABLE,),
        warnings=(),
        atwater=None,
        label_check=None,
    )


def review_reading(reading: Mapping[str, Any]) -> LabelReview:
    """Revisão da leitura da IA (já no formato do schema do worker): base,
    energia, por 100 derivado da porção, regras de entrada do catálogo,
    consistência por porção × por 100 e código de barras. Pura."""
    legibilidade = normalize_legibility(reading.get("legibility"))
    base_lida = catalog.normalize_basis(reading.get("basis")) if reading.get("basis") is not None else None
    base = base_lida or catalog.DEFAULT_FOOD_BASIS
    if legibilidade == LabelLegibility.UNREADABLE:
        return _unreadable(legibilidade, base)

    avisos: list[str] = []
    if legibilidade == LabelLegibility.PARTIAL:
        avisos.append(WARN_LABEL_PARTIAL)
    if base_lida is None:
        avisos.append(WARN_LABEL_BASIS_ASSUMED)

    por_100, convertida_100 = _with_energy(_nutrients(reading.get("per_100")))
    por_porcao, convertida_porcao = _with_energy(_nutrients(reading.get("per_serving")))
    if convertida_100 or convertida_porcao:
        avisos.append(WARN_LABEL_ENERGY_FROM_KJ)

    # "Não contém quantidade significativa de X" (RDC 429): a IA só lista o campo;
    # o zero é do motor, com aviso. Valor impresso na tabela não é sobrescrito.
    for campo in _not_significant(reading.get("not_significant")):
        if por_100.get(campo) is None:
            por_100[campo] = 0.0
            avisos.append(f"{WARN_LABEL_NOT_SIGNIFICANT}:{campo}")
        if por_porcao.get(campo) is None:
            por_porcao[campo] = 0.0

    porcao = positive_number(reading.get("serving_size"))
    for campo in catalog.PER_100_FIELDS:
        derivado = per_reference_from_serving(por_porcao.get(campo), porcao)
        if por_100.get(campo) is None and derivado is not None:
            por_100[campo] = _rounded(derivado)
            avisos.append(f"{WARN_LABEL_DERIVED_FROM_SERVING}:{campo}")

    entrada = {
        **{campo: por_100.get(campo) for campo in catalog.PER_100_FIELDS},
        "serving_size": reading.get("serving_size"),
        "servings_per_package": reading.get("servings_per_package"),
    }
    validacao = catalog.validate_food_values(entrada, base)
    avisos.extend(validacao.warnings)

    checagem: Optional[LabelCheck] = None
    macros_100 = nutrients_from_values(validacao.values).as_macros()
    macros_porcao = nutrients_from_values(por_porcao).as_macros()
    if macros_100 is not None and macros_porcao is not None and porcao is not None:
        checagem = label_consistency(macros_100, macros_porcao, porcao)
        if checagem is not None:
            avisos.extend(
                f"{WARN_LABEL_SERVING_INCONSISTENT}:{_LABEL_CHECK_FIELD_CODES[campo]}" for campo in checagem.fields_off
            )

    nome = _text(reading.get("product_name"))
    if nome is None:
        avisos.append(WARN_LABEL_NAME_MISSING)

    return LabelReview(
        legibility=legibilidade,
        name=nome,
        brand=_text(reading.get("brand")),
        barcode=normalize_gtin(reading.get("barcode")),
        basis=base,
        per_100={campo: validacao.values.get(campo) for campo in catalog.PER_100_FIELDS},
        per_serving={campo: non_negative_number(por_porcao.get(campo)) for campo in catalog.PER_100_FIELDS},
        serving_size=validacao.values.get("serving_size"),
        household_measure=_text(reading.get("household_measure")),
        servings_per_package=validacao.values.get("servings_per_package"),
        errors=tuple(validacao.errors),
        warnings=tuple(dict.fromkeys(avisos)),
        atwater=validacao.atwater,
        label_check=checagem,
    )


__all__ = [
    "BASE64_BLOCK_BYTES",
    "BASE64_BLOCK_CHARS",
    "BYTES_PER_MIB",
    "ENERGY_KJ_FIELD",
    "ERR_LABEL_PHOTO_DISPATCH_FAILED",
    "ERR_LABEL_PHOTO_EMPTY",
    "ERR_LABEL_PHOTO_INVALID_ENCODING",
    "ERR_LABEL_PHOTO_KEY_INVALID",
    "ERR_LABEL_PHOTO_NOT_FOUND",
    "ERR_LABEL_PHOTO_STORAGE_FAILED",
    "ERR_LABEL_PHOTO_TOO_LARGE",
    "ERR_LABEL_PHOTO_UNSUPPORTED_TYPE",
    "ERR_LABEL_READING_INVALID",
    "ERR_LABEL_UNREADABLE",
    "AI_NOTES_MAX",
    "AI_NOTE_MAX_CHARS",
    "LABEL_IMAGE_DETAIL",
    "LABEL_NUTRIENT_FIELDS",
    "LABEL_PHOTO_ACCEPTED_CONTENT_TYPES",
    "LABEL_PHOTO_CONTENT_TYPE",
    "LABEL_PHOTO_EXTENSION",
    "LABEL_PHOTO_IMAGES_PER_READING",
    "LABEL_PHOTO_MAX_BASE64_CHARS",
    "LABEL_PHOTO_MAX_BYTES",
    "LABEL_PHOTO_MAX_MIB",
    "LABEL_PHOTO_RETENTION_DAYS",
    "LABEL_PHOTO_S3_PREFIX",
    "PREFILL_DECIMALS",
    "RESIZE_JPEG_QUALITY",
    "RESIZE_JPEG_QUALITY_PERCENT",
    "RESIZE_MAX_EDGE_PX",
    "WARN_LABEL_BASIS_ASSUMED",
    "WARN_LABEL_DERIVED_FROM_SERVING",
    "WARN_LABEL_ENERGY_FROM_KJ",
    "WARN_LABEL_NAME_MISSING",
    "WARN_LABEL_NOT_SIGNIFICANT",
    "WARN_LABEL_PARTIAL",
    "WARN_LABEL_SERVING_INCONSISTENT",
    "LabelLegibility",
    "LabelReview",
    "accepts_content_type",
    "clean_ai_notes",
    "decode_upload",
    "is_label_photo_key_of",
    "label_photo_key",
    "normalize_legibility",
    "review_reading",
    "upload_error",
]

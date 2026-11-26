"""Foto do prato: upload, armazenamento e a revisão determinística do que a IA viu
(Lote L10; decisões D1 e D11 de 2026-11-02).

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §80; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão `foto_prato_nutricao`).
Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §9 (c). A cota e o job type são de
`ai_usage`; o transporte da foto (limite, JPEG, base64 sem padding, redução no
navegador, `detail`), de `label_photo`, que esta feature reaproveita em vez de
copiar; os itens, o casamento e a proposta, de `meal_parse`; os nutrientes, de
`meal_math`.

- Upload (`POST /nutrition/meals/photo`, api-core): as regras de `label_photo`, com
  códigos próprios (`ERR_MEAL_PHOTO_*`), porque o texto do erro não pode falar de
  rótulo. Uma imagem por leitura.
- Chave S3: `nutrition-meals/{user_id}/{uuid hex}.jpg`, só ids internos.
  `is_meal_photo_key_of` confere que a chave é do usuário (o worker não lê foto de
  outro). Retenção de `MEAL_PHOTO_RETENTION_DAYS` (1 d: a foto só é lida uma vez pelo
  worker e a refeição não guarda a chave): espelho da regra `nutrition-meals-expire-1d`
  de `infra/s3_uploads.tf`, com teste de paridade.
- Revisão (`review_reading`): a IA só IDENTIFICA os alimentos e estima os gramas; o
  motor decide. `PlateStatus` diferente de `RECOGNIZED`, ou nenhum item válido, devolve
  o resultado VAZIO com a limitação de por quê, descartando qualquer item que tenha
  vindo junto (nada de item inventado). Gramas acima de `diary.ITEM_AMOUNT_MAX` são
  descartados e a pessoa informa a quantidade. Depois do casamento, o item casado é
  DERIVED (`meal_math`) e o sem casamento é ESTIMATED (`item_nature`), como no texto.
- Dicas da pessoa (issue #323, `nutrition-photo-v2`): descrição e peso total opcionais
  (`user_hints_error`, antes da cota). Por item, a natureza da evidência
  (`ItemEvidence`), a faixa de gramas e a incerteza principal (`Uncertainty`); faixa
  incoerente com o valor central é descartada (`item_detail`). Com o peso total
  informado, soma dos itens fora de `MEAL_PHOTO_TOTAL_GRAMS_TOLERANCE` vira
  `LIMIT_TOTAL_GRAMS_MISMATCH`, sem reescalar os pesos (a pessoa decide).

Defaults de desenho, SEM fonte bibliográfica: 1 dia (revisão do lote N4, 2026-11-02; o plano §9 c dizia 30);
descrição de até 500 caracteres, peso total até 5000 g e tolerância de 10 % (issue #323, 2026-11-26).

Módulo PURO: sem banco, sem FastAPI, sem I/O e sem relógio.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Mapping, Optional

from . import label_photo, meal_parse
from .diary import ITEM_AMOUNT_MAX
from .meal_math import MealNature
from .meal_parse import AiItem, MatchStatus, ParsedItem


class PlateStatus(str, Enum):
    """O que a IA diz que viu na foto (só ela vê; o motor só respeita)."""

    RECOGNIZED = "recognized"  # há comida ou bebida identificável
    NO_FOOD = "no_food"  # a foto não mostra comida
    UNCLEAR = "unclear"  # pode ser comida, mas não dá para identificar


class ItemEvidence(str, Enum):
    """De onde veio o item (issue #323): o que a IA viu, o que a pessoa disse ou hipótese."""

    OBSERVED = "observed"
    USER_INFORMED = "user_informed"
    HYPOTHESIS = "hypothesis"


class Uncertainty(str, Enum):
    """A incerteza concreta que mais pesa no item ou na leitura (lista fechada, sem texto livre)."""

    NONE = "none"
    HIDDEN_PORTION = "hidden_portion"  # parte coberta por outro alimento
    PORTION_SCALE = "portion_scale"  # tamanho sem escala confiável
    PREPARATION = "preparation"  # preparo ou estado (cru/cozido, pele, caldo)
    ADDED_FAT = "added_fat"  # óleo ou gordura que não se vê
    IDENTITY = "identity"  # qual é o alimento
    INEDIBLE_PARTS = "inedible_parts"  # osso, casca, sobra


# ---------------------------------------------------------------------------
# Upload e armazenamento
# ---------------------------------------------------------------------------

MEAL_PHOTO_MAX_BYTES: Final[int] = label_photo.LABEL_PHOTO_MAX_BYTES
MEAL_PHOTO_CONTENT_TYPE: Final[str] = label_photo.LABEL_PHOTO_CONTENT_TYPE
MEAL_PHOTO_S3_PREFIX: Final[str] = "nutrition-meals"
MEAL_PHOTO_RETENTION_DAYS: Final[int] = 1
MEAL_PHOTO_IMAGES_PER_READING: Final[int] = 1
MEAL_IMAGE_DETAIL: Final[str] = label_photo.LABEL_IMAGE_DETAIL

ERR_MEAL_PHOTO_EMPTY = "meal_photo_empty"
ERR_MEAL_PHOTO_INVALID_ENCODING = "meal_photo_invalid_encoding"
ERR_MEAL_PHOTO_TOO_LARGE = "meal_photo_too_large"
ERR_MEAL_PHOTO_UNSUPPORTED_TYPE = "meal_photo_unsupported_type"
ERR_MEAL_PHOTO_STORAGE_FAILED = "meal_photo_storage_failed"
ERR_MEAL_PHOTO_DISPATCH_FAILED = "meal_photo_dispatch_failed"
ERR_MEAL_PHOTO_KEY_INVALID = "meal_photo_key_invalid"
ERR_MEAL_PHOTO_NOT_FOUND = "meal_photo_not_found"
ERR_MEAL_READING_INVALID = "meal_photo_reading_invalid"
ERR_MEAL_READING_UNEXPECTED = "meal_photo_reading_unexpected"

# Dicas da pessoa no upload (issue #323): opcionais, conferidas antes da cota.
MEAL_PHOTO_DESCRIPTION_MAX_CHARS: Final[int] = 500
MEAL_PHOTO_TOTAL_GRAMS_MAX: Final[int] = 5000
MEAL_PHOTO_TOTAL_GRAMS_MIN: Final[int] = 1
# Apresentação do item cuja quantidade/unidade a pessoa alterou no editor: a evidência
# vira "informada pela pessoa" e a faixa de gramas da IA deixa de valer.
MEAL_PHOTO_EDITED_ITEM_EVIDENCE: Final[ItemEvidence] = ItemEvidence.USER_INFORMED
MEAL_PHOTO_EDITED_ITEM_KEEPS_RANGE: Final[bool] = False
# Fração do peso total informado que a soma dos itens pode desviar sem aviso.
MEAL_PHOTO_TOTAL_GRAMS_TOLERANCE: Final[float] = 0.10
ERR_MEAL_PHOTO_DESCRIPTION_TOO_LONG = "meal_photo_description_too_long"
ERR_MEAL_PHOTO_TOTAL_GRAMS_INVALID = "meal_photo_total_grams_invalid"

# Limitações da leitura inteira (resultado vazio, com o motivo).
LIMIT_PHOTO_NO_FOOD = "meal_photo_no_food"
LIMIT_PHOTO_UNCLEAR = "meal_photo_unclear"
# Limitação do item: gramas fora do teto do diário.
LIMIT_GRAMS_OUT_OF_RANGE = "meal_photo_grams_out_of_range"
# Limitação da leitura: soma dos itens longe do peso total que a pessoa informou.
LIMIT_TOTAL_GRAMS_MISMATCH = "meal_photo_total_grams_mismatch"

_UPLOAD_ERRORS: Final[Mapping[str, str]] = {
    label_photo.ERR_LABEL_PHOTO_EMPTY: ERR_MEAL_PHOTO_EMPTY,
    label_photo.ERR_LABEL_PHOTO_INVALID_ENCODING: ERR_MEAL_PHOTO_INVALID_ENCODING,
    label_photo.ERR_LABEL_PHOTO_TOO_LARGE: ERR_MEAL_PHOTO_TOO_LARGE,
    label_photo.ERR_LABEL_PHOTO_UNSUPPORTED_TYPE: ERR_MEAL_PHOTO_UNSUPPORTED_TYPE,
}


def decode_upload(text: Any) -> tuple[Optional[bytes], Optional[str]]:
    """Bytes da foto a partir do base64 do corpo, ou o código do erro (`label_photo`)."""
    dados, erro = label_photo.decode_upload(text)
    return dados, _UPLOAD_ERRORS[erro] if erro else None


def upload_error(size_bytes: int, content_type: Optional[str]) -> Optional[str]:
    """Código do erro do upload (vazio, grande demais, tipo fora) ou None."""
    erro = label_photo.upload_error(size_bytes, content_type)
    return _UPLOAD_ERRORS[erro] if erro else None


def meal_photo_key(user_id: Any, photo_id: str) -> str:
    """`nutrition-meals/{user_id}/{uuid hex}.jpg` (só ids internos)."""
    return label_photo.photo_key(MEAL_PHOTO_S3_PREFIX, user_id, photo_id)


def is_meal_photo_key_of(key: Any, user_id: Any) -> bool:
    """A chave tem a forma de `meal_photo_key` E é do usuário?"""
    return label_photo.is_photo_key_of(MEAL_PHOTO_S3_PREFIX, key, user_id)


def normalize_description(raw: Any) -> Optional[str]:
    """Descrição da pessoa sem espaço nas pontas; vazia vale como ausente."""
    texto = raw.strip() if isinstance(raw, str) else ""
    return texto or None


def user_hints_error(description: Any, total_grams: Any) -> Optional[str]:
    """Código do erro das dicas da pessoa ou None (ausentes valem). O código nunca
    carrega o texto: a descrição não vai para log nem para mensagem."""
    texto = normalize_description(description)
    if texto is not None and len(texto) > MEAL_PHOTO_DESCRIPTION_MAX_CHARS:
        return ERR_MEAL_PHOTO_DESCRIPTION_TOO_LONG
    if total_grams is not None and (
        isinstance(total_grams, bool)
        or not isinstance(total_grams, int)
        or not MEAL_PHOTO_TOTAL_GRAMS_MIN <= total_grams <= MEAL_PHOTO_TOTAL_GRAMS_MAX
    ):
        return ERR_MEAL_PHOTO_TOTAL_GRAMS_INVALID
    return None


# ---------------------------------------------------------------------------
# Revisão do que a IA viu
# ---------------------------------------------------------------------------


def normalize_plate_status(raw: Any) -> PlateStatus:
    """Status dito pela IA; desconhecido vale como não identificado (nada de item)."""
    try:
        return PlateStatus(raw.value if isinstance(raw, Enum) else str(raw).strip().lower())
    except ValueError:
        return PlateStatus.UNCLEAR


def _within_diary_limit(item: AiItem) -> AiItem:
    """Gramas (ou, sem eles, a quantidade) acima do teto do diário não são propostos:
    a pessoa informa. O alimento identificado continua valendo."""
    medida = item.grams_estimate if item.grams_estimate is not None else item.quantity
    if medida is None or medida <= ITEM_AMOUNT_MAX:
        return item
    return dataclasses.replace(
        item,
        grams_estimate=None,
        quantity=None,
        unit=None,
        limitations=tuple(dict.fromkeys((*item.limitations, LIMIT_GRAMS_OUT_OF_RANGE))),
    )


def _enum_or_none(enum: type[Enum], raw: Any) -> Any:
    try:
        return enum(raw)
    except ValueError:
        return None


def _grams(raw: Any) -> Optional[float]:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw) if 0 < raw <= ITEM_AMOUNT_MAX else None


@dataclass(frozen=True)
class PhotoItemDetail:
    """O que a foto acrescenta ao item lido (issue #323). Ausente = None (leitura v1)."""

    evidence: Optional[ItemEvidence] = None
    grams_low: Optional[float] = None
    grams_high: Optional[float] = None
    uncertainty: Optional[Uncertainty] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": self.evidence.value if self.evidence else None,
            "grams_low": self.grams_low,
            "grams_high": self.grams_high,
            "uncertainty": self.uncertainty.value if self.uncertainty else None,
        }


def item_detail(raw: Any, grams_estimate: Optional[float]) -> PhotoItemDetail:
    """Evidência, faixa e incerteza do item cru. A faixa só vale inteira e em volta do
    valor central já revisado (`low <= estimate <= high`); fora disso é descartada,
    sem falhar a leitura."""
    if not isinstance(raw, Mapping):
        return PhotoItemDetail()
    baixo, alto = _grams(raw.get("grams_low")), _grams(raw.get("grams_high"))
    if grams_estimate is None or baixo is None or alto is None or not baixo <= grams_estimate <= alto:
        baixo = alto = None
    return PhotoItemDetail(
        evidence=_enum_or_none(ItemEvidence, raw.get("evidence")),
        grams_low=baixo,
        grams_high=alto,
        uncertainty=_enum_or_none(Uncertainty, raw.get("uncertainty")),
    )


def total_grams_mismatch(items: tuple[AiItem, ...], total_grams: Optional[int]) -> bool:
    """A soma dos gramas dos itens foge do peso total informado além da tolerância?
    Sem total, ou com item sem gramas (a soma seria parcial), não há o que comparar."""
    if not total_grams or not items or any(item.grams_estimate is None for item in items):
        return False
    soma = sum(item.grams_estimate for item in items)  # type: ignore[misc]
    return abs(soma - total_grams) > total_grams * MEAL_PHOTO_TOTAL_GRAMS_TOLERANCE


@dataclass(frozen=True)
class MealPhotoReview:
    """Itens limpos (na ordem da IA), as limitações da leitura inteira e, alinhados
    aos itens, os detalhes da foto (#323)."""

    status: PlateStatus
    items: tuple[AiItem, ...]
    limitations: tuple[str, ...]
    details: tuple[PhotoItemDetail, ...] = ()
    main_uncertainty: Optional[Uncertainty] = None


def review_reading(reading: Mapping[str, Any], *, total_grams: Optional[int] = None) -> MealPhotoReview:
    """Revisão da leitura da IA (já no formato do schema do worker). Pura. Foto sem
    comida, prato não identificado ou nenhum item válido -> nenhum item. Com o peso
    total da pessoa, a soma divergente vira limitação; os pesos ficam como a IA disse."""
    status = normalize_plate_status(reading.get("plate_status"))
    if status is PlateStatus.NO_FOOD:
        return MealPhotoReview(status, (), (LIMIT_PHOTO_NO_FOOD,))
    if status is PlateStatus.UNCLEAR:
        return MealPhotoReview(status, (), (LIMIT_PHOTO_UNCLEAR,))
    brutos = reading.get("items")
    # Os mesmos itens que `normalize_ai_items` aceita, para alinhar o detalhe ao item.
    validos = [b for b in (brutos if isinstance(brutos, (list, tuple)) else ()) if meal_parse.normalize_ai_item(b)]
    itens, limitacoes = meal_parse.normalize_ai_items(validos)
    if not itens:
        return MealPhotoReview(PlateStatus.UNCLEAR, (), (LIMIT_PHOTO_UNCLEAR,))
    revisados = tuple(_within_diary_limit(item) for item in itens)
    detalhes = tuple(item_detail(bruto, item.grams_estimate) for bruto, item in zip(validos, revisados))
    if total_grams_mismatch(revisados, total_grams):
        limitacoes = (*limitacoes, LIMIT_TOTAL_GRAMS_MISMATCH)
    principal = _enum_or_none(Uncertainty, reading.get("main_uncertainty"))
    return MealPhotoReview(status, revisados, limitacoes, detalhes, principal)


# Natureza do item proposto: casado pelo motor do diário, estimado pela IA. Sem
# entrada aqui (`CHOICE`, `UNMATCHED`) a origem só existe depois que a pessoa escolher.
_NATURE_BY_STATUS: Final[Mapping[MatchStatus, MealNature]] = {
    MatchStatus.SELECTED: meal_parse.CATALOG_ITEM_NATURE,
    MatchStatus.ESTIMATED: meal_parse.ESTIMATE_ITEM_NATURE,
}


def item_nature(item: ParsedItem) -> Optional[MealNature]:
    """Natureza da proposta do item, ou None enquanto a pessoa não escolher."""
    return _NATURE_BY_STATUS.get(item.status)


__all__ = [
    "ERR_MEAL_PHOTO_DESCRIPTION_TOO_LONG",
    "ERR_MEAL_PHOTO_DISPATCH_FAILED",
    "ERR_MEAL_PHOTO_EMPTY",
    "ERR_MEAL_PHOTO_INVALID_ENCODING",
    "ERR_MEAL_PHOTO_KEY_INVALID",
    "ERR_MEAL_PHOTO_NOT_FOUND",
    "ERR_MEAL_PHOTO_STORAGE_FAILED",
    "ERR_MEAL_PHOTO_TOO_LARGE",
    "ERR_MEAL_PHOTO_TOTAL_GRAMS_INVALID",
    "ERR_MEAL_PHOTO_UNSUPPORTED_TYPE",
    "ERR_MEAL_READING_INVALID",
    "ERR_MEAL_READING_UNEXPECTED",
    "ItemEvidence",
    "LIMIT_GRAMS_OUT_OF_RANGE",
    "LIMIT_PHOTO_NO_FOOD",
    "LIMIT_PHOTO_UNCLEAR",
    "LIMIT_TOTAL_GRAMS_MISMATCH",
    "MEAL_IMAGE_DETAIL",
    "MEAL_PHOTO_CONTENT_TYPE",
    "MEAL_PHOTO_DESCRIPTION_MAX_CHARS",
    "MEAL_PHOTO_EDITED_ITEM_EVIDENCE",
    "MEAL_PHOTO_EDITED_ITEM_KEEPS_RANGE",
    "MEAL_PHOTO_TOTAL_GRAMS_MIN",
    "MEAL_PHOTO_IMAGES_PER_READING",
    "MEAL_PHOTO_MAX_BYTES",
    "MEAL_PHOTO_RETENTION_DAYS",
    "MEAL_PHOTO_S3_PREFIX",
    "MEAL_PHOTO_TOTAL_GRAMS_MAX",
    "MEAL_PHOTO_TOTAL_GRAMS_TOLERANCE",
    "MealPhotoReview",
    "PhotoItemDetail",
    "PlateStatus",
    "Uncertainty",
    "decode_upload",
    "is_meal_photo_key_of",
    "item_detail",
    "item_nature",
    "meal_photo_key",
    "normalize_description",
    "normalize_plate_status",
    "review_reading",
    "total_grams_mismatch",
    "upload_error",
    "user_hints_error",
]

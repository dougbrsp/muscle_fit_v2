"""Leitura da foto do rótulo nutricional pela IA (plano de nutrição §8.3 e
§9 b; Lote L6). Roda no worker da `musclefit-plan-generator` (180 s), job
`ai_usage.LABEL_PHOTO_JOB_TYPE`, enfileirado por `POST
/nutrition/foods/label-photo` (`app/nutrition_label_photo.py`).

Fluxo do job (`run_label_job`): confere que a chave da foto é do usuário
(`label_photo.is_label_photo_key_of`) -> lê a foto do S3
(`nutrition_label_storage`) -> uma chamada à OpenAI com a imagem
(`openai_client.call_openai_structured_vision`, saída estruturada) -> valida a
forma com Pydantic -> revisão DETERMINÍSTICA do motor
(`label_photo.review_reading`: base, kJ -> kcal, por 100 derivado da porção,
regras de entrada do `catalog` com Atwater e `meal_math.label_consistency`) ->
fecha a linha do `ai_usage_log` (`nutrition_ai_quota.finish_usage`) em TODOS os
caminhos -> devolve o formulário pré-preenchido. O usuário confere e salva por
`POST /nutrition/foods` com `source=label_photo`; nada é gravado no catálogo
aqui.

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md` §3.7 e §56): o prompt
INTERPOLA tudo o que tem número ou vocabulário do domínio — a norma
(`labels.LABEL_REGULATION_REFERENCE`), os campos e unidades da tabela
(`labels.RDC429_FIELDS` ∩ `label_photo.LABEL_NUTRIENT_FIELDS`), a base por 100
(`meal_math.PER_100G_REFERENCE`, `catalog.FOOD_BASES`) e a legibilidade
(`label_photo.LabelLegibility`); os códigos aceitos em `not_significant` vêm do
enum do schema (`catalog.PER_100_FIELDS`, o strict recusa outro). A IA só
transcreve: nenhuma conta nem conferência é dela (a consistência por porção ×
por 100 é de `meal_math.label_consistency`, dentro de `review_reading`), e o
zero do "sem quantidade significativa" é do motor.

Decisão D-E do plano (saída estruturada strict + `input_image`): confirmada no
eval pago de 2026-10-18 (`RESPONSE_FORMAT`); o `json_object` com validação
Pydantic fica como alternativa no cliente (`STRUCTURED_FORMAT_JSON_OBJECT`).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from . import nutrition_ai_quota, nutrition_label_storage, openai_client, prompt_store
from .domain.nutrition import catalog, label_photo
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.ai_usage import AiFeature, AiUsageStatus
from .domain.nutrition.meal_math import PER_100G_REFERENCE
from .openai_client import ImageInput, OpenAIGenerationError
from .request_models import FoodItemIn

logger = logging.getLogger("musclefit.nutrition.label_ai")

PROMPT_VERSION = "nutrition-label-v2"
# Pasta do template em `api/privado/prompts/` (M103 P1).
PROMPT_FEATURE_DIR = "nutrition_label"
FEATURE = AiFeature.LABEL_PHOTO.value
SCHEMA_NAME = "nutrition_label_reading"

# Modelo: `OPENAI_NUTRITION_VISION_MODEL` na `musclefit-plan-generator`
# (`infra/lambda_ai_generators.tf`, mesmo default: teste de paridade).
MODEL_ENV_VAR = "OPENAI_NUTRITION_VISION_MODEL"
DEFAULT_MODEL = "gpt-5.4-mini"

# Timeout do cliente (plano §8.3): MENOR que os 180 s da Lambda
# (`async_jobs.LAMBDA_TIMEOUT_SECONDS`, teste de paridade), para o estouro virar
# job `failed` com mensagem, e não Lambda morta.
LLM_TIMEOUT_SECONDS = 90

# D-E confirmada (strict + imagem): ver o docstring.
RESPONSE_FORMAT = openai_client.STRUCTURED_FORMAT_JSON_SCHEMA

ERR_UNEXPECTED = "label_reading_unexpected"


def model_name() -> str:
    return (os.environ.get(MODEL_ENV_VAR) or "").strip() or DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Prompt e schema (do motor; nenhum número escrito à mão)
# ---------------------------------------------------------------------------


def _nutrient_fields() -> list[nutrition_labels.Rdc429Field]:
    por_codigo = {campo.code: campo for campo in nutrition_labels.RDC429_FIELDS}
    return [por_codigo[codigo] for codigo in label_photo.LABEL_NUTRIENT_FIELDS]


def prompt_values() -> dict[str, str]:
    """Os valores que o prompt interpola, todos do dono (o teste renderiza o prompt
    com marcadores no lugar deles e exige zero dígito no texto)."""
    base_g, base_ml = (base.value for base in catalog.FOOD_BASES)
    return {
        "regulation": nutrition_labels.LABEL_REGULATION_REFERENCE,
        "reference": f"{PER_100G_REFERENCE:g}",
        "basis_g": base_g,
        "basis_ml": base_ml,
        "ok": label_photo.LabelLegibility.OK.value,
        "partial": label_photo.LabelLegibility.PARTIAL.value,
        "unreadable": label_photo.LabelLegibility.UNREADABLE.value,
    }


def build_prompt(values: Optional[Mapping[str, str]] = None) -> str:
    """Prosa em `api/privado/prompts/nutrition_label/<PROMPT_VERSION>.md`
    (`prompt_store`); aqui só os valores do dono e a lista de campos."""
    v = dict(values) if values is not None else prompt_values()
    campos = "\n".join(f"- {campo.code}: {campo.label} ({campo.unit})" for campo in _nutrient_fields())
    return prompt_store.render(PROMPT_FEATURE_DIR, PROMPT_VERSION, {**v, "campos": campos})


def _nullable(tipo: str, descricao: Optional[str] = None) -> dict[str, Any]:
    campo: dict[str, Any] = {"type": [tipo, "null"]}
    if descricao:
        campo["description"] = descricao
    return campo


def output_schema() -> dict[str, Any]:
    """JSON Schema strict da leitura: todos os campos obrigatórios (null = não lido)."""
    nutrientes = {
        "type": "object",
        "additionalProperties": False,
        "required": list(label_photo.LABEL_NUTRIENT_FIELDS),
        "properties": {campo.code: _nullable("number", f"{campo.label} ({campo.unit})") for campo in _nutrient_fields()},
    }
    propriedades: dict[str, Any] = {
        "product_name": _nullable("string"),
        "brand": _nullable("string"),
        "barcode": _nullable("string"),
        "basis": {"type": ["string", "null"], "enum": [*(base.value for base in catalog.FOOD_BASES), None]},
        "serving_size": _nullable("number"),
        "household_measure": _nullable("string"),
        "servings_per_package": _nullable("number"),
        "per_100": nutrientes,
        "per_serving": nutrientes,
        "legibility": {"type": "string", "enum": [item.value for item in label_photo.LabelLegibility]},
        "not_significant": {"type": "array", "items": {"type": "string", "enum": list(catalog.PER_100_FIELDS)}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(propriedades),
        "properties": propriedades,
    }


# Validação da forma (vale para o strict e para o `json_object` da alternativa D-E).
_LabelNutrientsOut = create_model(  # type: ignore[call-overload]
    "LabelNutrientsOut",
    __config__=ConfigDict(extra="ignore"),
    **{codigo: (Optional[float], None) for codigo in label_photo.LABEL_NUTRIENT_FIELDS},
)


class LabelReadingOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    product_name: Optional[str] = None
    brand: Optional[str] = None
    barcode: Optional[str] = None
    basis: Optional[str] = None
    serving_size: Optional[float] = None
    household_measure: Optional[str] = None
    servings_per_package: Optional[float] = None
    per_100: _LabelNutrientsOut = Field(default_factory=_LabelNutrientsOut)  # type: ignore[valid-type]
    per_serving: _LabelNutrientsOut = Field(default_factory=_LabelNutrientsOut)  # type: ignore[valid-type]
    legibility: str
    not_significant: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Chamada
# ---------------------------------------------------------------------------


class LabelPhotoJobError(RuntimeError):
    """Falha do job com o código do motor; a mensagem é o texto pt-BR de `labels`."""

    def __init__(self, code: str, *, model: Optional[str] = None, tokens_in: Any = None, tokens_out: Any = None):
        super().__init__(nutrition_labels.limitation_label(code))
        self.code = code
        self.model = model
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


@dataclass(frozen=True)
class LabelReading:
    """Leitura da IA já validada na forma, com o que a auditoria grava."""

    data: dict[str, Any]
    model: str
    tokens_in: Optional[int]
    tokens_out: Optional[int]


def read_label(
    image: bytes,
    *,
    user_id: Any = None,
    model: Optional[str] = None,
    response_format: str = RESPONSE_FORMAT,
    timeout: float = LLM_TIMEOUT_SECONDS,
) -> LabelReading:
    """Uma chamada à OpenAI com a foto. `OpenAIGenerationError` da chamada
    propaga; forma inválida vira `LabelPhotoJobError(ERR_LABEL_READING_INVALID)`
    com os tokens gastos."""
    resultado = openai_client.call_openai_structured_vision(
        build_prompt(),
        [ImageInput(image, label_photo.LABEL_PHOTO_CONTENT_TYPE, label_photo.LABEL_IMAGE_DETAIL)],
        model or model_name(),
        output_schema(),
        schema_name=SCHEMA_NAME,
        timeout=timeout,
        feature=FEATURE,
        user_id=user_id,
        prompt_version=PROMPT_VERSION,
        response_format=response_format,
    )
    try:
        leitura = LabelReadingOut.model_validate(resultado.data)
    except ValidationError as exc:
        logger.warning("Leitura do rótulo fora da forma (%s erro(s))", exc.error_count())
        raise LabelPhotoJobError(
            label_photo.ERR_LABEL_READING_INVALID,
            model=resultado.model,
            tokens_in=resultado.tokens_in,
            tokens_out=resultado.tokens_out,
        ) from exc
    return LabelReading(
        data=leitura.model_dump(), model=resultado.model, tokens_in=resultado.tokens_in, tokens_out=resultado.tokens_out
    )


# ---------------------------------------------------------------------------
# Resultado do job (o formulário que a tela pré-preenche)
# ---------------------------------------------------------------------------


def _max_length(field: str) -> Optional[int]:
    for meta in FoodItemIn.model_fields[field].metadata:
        valor = getattr(meta, "max_length", None)
        if valor is not None:
            return int(valor)
    return None


def _clip(field: str, value: Any) -> Any:
    teto = _max_length(field)
    if not isinstance(value, str) or teto is None:
        return value
    return value[:teto].strip() or None


def job_result(review: label_photo.LabelReview, reading: LabelReading) -> dict[str, Any]:
    revisada = review.to_dict()
    alimento = {campo: _clip(campo, valor) for campo, valor in revisada["food"].items()}
    return {
        "prompt_version": PROMPT_VERSION,
        "model": reading.model,
        "legibility": revisada["legibility"],
        "readable": revisada["readable"],
        "barcode": revisada["barcode"],
        "food": alimento,
        "per_serving": revisada["per_serving"],
        "errors": nutrition_labels.limitation_items(revisada["errors"]),
        "warnings": nutrition_labels.limitation_items(revisada["warnings"]),
        "ai_notes": list(label_photo.clean_ai_notes(reading.data.get("warnings"))),
        "atwater": revisada["atwater"],
        "label_check": revisada["label_check"],
    }


def _fail(usage_id: Any, exc: BaseException, *, code: str, model: Any = None, tokens_in: Any = None,
          tokens_out: Any = None) -> None:
    nutrition_ai_quota.finish_usage(
        usage_id, status=AiUsageStatus.FAILED, model=model, tokens_in=tokens_in, tokens_out=tokens_out, error_code=code
    )
    logger.warning("Leitura do rótulo falhou (ai_usage_id=%s, code=%s, tipo=%s)", usage_id, code, type(exc).__name__)


def run_label_job(event: Mapping[str, Any]) -> dict[str, Any]:
    """O job `LABEL_PHOTO_JOB_TYPE` da plan-generator. Fecha a linha do
    `ai_usage_log` em todos os caminhos (sucesso, falha da IA, forma inválida,
    foto ausente) e propaga a falha para o handler gravar `failed`."""
    usage_id = event.get("ai_usage_id")
    user_id = event.get("user_id")
    key = event.get("s3_key")
    try:
        if not label_photo.is_label_photo_key_of(key, user_id):
            raise LabelPhotoJobError(label_photo.ERR_LABEL_PHOTO_KEY_INVALID)
        try:
            imagem = nutrition_label_storage.read_label_photo(str(key))
        except nutrition_label_storage.LabelPhotoStorageError as exc:
            raise LabelPhotoJobError(exc.code) from exc
        # Defesa: a api-core já conferiu tamanho e tipo antes de gravar.
        if label_photo.upload_error(len(imagem), label_photo.LABEL_PHOTO_CONTENT_TYPE):
            raise LabelPhotoJobError(label_photo.ERR_LABEL_PHOTO_TOO_LARGE)
        leitura = read_label(imagem, user_id=user_id)
        revisao = label_photo.review_reading(leitura.data)
        resultado = job_result(revisao, leitura)
    except LabelPhotoJobError as exc:
        _fail(usage_id, exc, code=exc.code, model=exc.model, tokens_in=exc.tokens_in, tokens_out=exc.tokens_out)
        raise
    except OpenAIGenerationError as exc:
        _fail(usage_id, exc, code=exc.code)
        raise
    except Exception as exc:
        _fail(usage_id, exc, code=ERR_UNEXPECTED)
        raise
    nutrition_ai_quota.finish_usage(
        usage_id,
        status=AiUsageStatus.SUCCEEDED,
        model=leitura.model,
        tokens_in=leitura.tokens_in,
        tokens_out=leitura.tokens_out,
    )
    return resultado


__all__ = [
    "DEFAULT_MODEL",
    "FEATURE",
    "LLM_TIMEOUT_SECONDS",
    "MODEL_ENV_VAR",
    "PROMPT_VERSION",
    "RESPONSE_FORMAT",
    "LabelPhotoJobError",
    "LabelReading",
    "LabelReadingOut",
    "build_prompt",
    "job_result",
    "model_name",
    "output_schema",
    "prompt_values",
    "read_label",
    "run_label_job",
]

"""Leitura da foto do prato pela IA (nutrição, Lote L10; decisão D1: Claude Haiku 5.5
com visão e fallback OpenAI). Roda no worker da `musclefit-plan-generator` (180 s), job
`ai_usage.MEAL_PHOTO_JOB_TYPE`, enfileirado por `POST /nutrition/meals/photo`
(`app/nutrition_meal_photo.py`).

Fluxo do job (`run_meal_photo_job`): confere que a chave da foto é do usuário
(`meal_photo.is_meal_photo_key_of`) -> lê a foto do S3 (`nutrition_meal_photo_storage`)
-> uma chamada com a imagem (`openai_client.call_openai_structured_vision`: o provedor,
o modelo Claude, o esforço e o fallback vêm das envs da feature, `llm_anthropic`) ->
valida a forma com Pydantic ESTRITO -> revisão do motor (`meal_photo.review_reading`) ->
casamento com o catálogo, o mesmo do texto (`nutrition_meal_parse_worker.match_items`)
-> decisão de cada item (`meal_parse.build_result`) -> fecha a linha do `ai_usage_log`
em TODOS os caminhos. Nada é gravado no diário aqui: a pessoa confere no editor e salva
pelo `POST /nutrition/meals`.

A foto é dado NÃO CONFIÁVEL: o prompt manda ignorar texto na imagem; a saída passa por
schema estrito sem campo de texto livre para a tela (`notes` não existe aqui), e nome e
preparo só servem para a busca e para o rótulo do item, cortados pelo motor.

Regra dos motores (mapa §80): a IA só identifica os alimentos e estima os gramas (e a
estimativa por 100 g que o item sem casamento usa, como no texto); os nutrientes são de
`meal_math`, no preview e na gravação. Nenhum número do prompt é escrito à mão:
`prompt_values` interpola da fonte (`api/privado/tests/unit/test_nutrition_meal_photo_prompt.py`
confere).

`prompt_version` / mudanças:
- nutrition-photo-v1 (2026-11-02): versão inicial.
- nutrition-photo-v2 (2026-11-26, issue #323): descrição e peso total opcionais da pessoa
  (bloco `<dados_do_usuario>` no fim, prevalece sobre a dedução visual; texto da imagem
  segue sem autoridade); por item `evidence`, `grams_low`/`grams_high` e `uncertainty`,
  e `main_uncertainty` na leitura (enums do dono `meal_photo`). Os campos novos são
  opcionais na validação: leitura sem eles continua válida.
- nutrition-photo-v3 (2026-11-26, issue #325): até `meal_photo.MEAL_PHOTO_IMAGES_PER_READING`
  fotos da mesma refeição numa chamada só (um `ImageInput` por foto; `fotos_max` no
  prompt). O evento leva `s3_keys`; o `s3_key` antigo segue aceito.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, ValidationError, create_model

from . import (
    nutrition_ai_quota,
    nutrition_label_ai,
    nutrition_meal_parse_ai,
    nutrition_meal_parse_worker,
    nutrition_meal_photo_storage,
    openai_client,
    prompt_store,
)
from .domain.nutrition import ENGINE_VERSION, diary, meal_parse, meal_photo
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.ai_usage import AiFeature, AiUsageStatus
from .openai_client import ImageInput, OpenAIGenerationError
from .prompt_user_data import sanitize_user_text, user_data_block, user_data_rule

logger = logging.getLogger("musclefit.nutrition.meal_photo_ai")

PROMPT_VERSION = "nutrition-photo-v3"
# Pasta do template em `api/privado/prompts/`.
PROMPT_FEATURE_DIR = "nutrition_meal_photo"
FEATURE = AiFeature.MEAL_PHOTO.value
SCHEMA_NAME = "nutrition_meal_photo"

# Timeout do cliente: o mesmo da foto do rótulo, menor que os 180 s da Lambda.
LLM_TIMEOUT_SECONDS = nutrition_label_ai.LLM_TIMEOUT_SECONDS


def model_name() -> str:
    """Modelo da OpenAI (o caminho do fallback): a mesma env do rótulo. O Claude vem
    de `ANTHROPIC_MODEL_MEAL_PHOTO` (`infra/llm_providers.tf`)."""
    return nutrition_label_ai.model_name()


# ---------------------------------------------------------------------------
# Prompt e schema (do motor; nenhum número escrito à mão)
# ---------------------------------------------------------------------------


def prompt_values() -> dict[str, str]:
    """Os valores que o prompt interpola, todos do dono (o teste renderiza o prompt com
    marcadores no lugar deles e exige zero dígito no texto). O vocabulário do item é o
    da leitura por texto (`nutrition_meal_parse_ai.prompt_values`)."""
    texto = nutrition_meal_parse_ai.prompt_values()
    return {
        **{
            chave: texto[chave]
            for chave in (
                "unidades",
                "unidade_grama",
                "unidade_contavel",
                "confianca_min",
                "confianca_max",
                "chave_estimativa",
                "base",
                "campos_estimativa",
                "campos_obrigatorios",
            )
        },
        "status_reconhecido": meal_photo.PlateStatus.RECOGNIZED.value,
        "status_sem_comida": meal_photo.PlateStatus.NO_FOOD.value,
        "status_incerto": meal_photo.PlateStatus.UNCLEAR.value,
        "gramas_max": f"{diary.ITEM_AMOUNT_MAX:g}",
        "itens_max": str(diary.MEAL_MAX_ITEMS),
        "regra_dados_usuario": user_data_rule(),
        "evidencia_vista": meal_photo.ItemEvidence.OBSERVED.value,
        "evidencia_informada": meal_photo.ItemEvidence.USER_INFORMED.value,
        "evidencia_hipotese": meal_photo.ItemEvidence.HYPOTHESIS.value,
        "incerteza_nenhuma": meal_photo.Uncertainty.NONE.value,
        "incerteza_oculta": meal_photo.Uncertainty.HIDDEN_PORTION.value,
        "incerteza_escala": meal_photo.Uncertainty.PORTION_SCALE.value,
        "incerteza_preparo": meal_photo.Uncertainty.PREPARATION.value,
        "incerteza_gordura": meal_photo.Uncertainty.ADDED_FAT.value,
        "incerteza_identidade": meal_photo.Uncertainty.IDENTITY.value,
        "incerteza_partes": meal_photo.Uncertainty.INEDIBLE_PARTS.value,
        "fotos_max": str(meal_photo.MEAL_PHOTO_IMAGES_PER_READING),
    }


_NAO_INFORMADO = "não informado"


def build_prompt(
    values: Optional[Mapping[str, str]] = None, *, description: Any = None, total_grams: Optional[int] = None
) -> str:
    """Prosa em `api/privado/prompts/nutrition_meal_photo/<PROMPT_VERSION>.md`
    (`prompt_store`) e, no fim, o que a pessoa informou (`<PROMPT_VERSION>.pedido.md`):
    a descrição saneada dentro do bloco de dados do usuário e o peso total."""
    instrucoes = prompt_store.render(
        PROMPT_FEATURE_DIR, PROMPT_VERSION, dict(values) if values is not None else prompt_values()
    )
    texto = sanitize_user_text(description, meal_photo.MEAL_PHOTO_DESCRIPTION_MAX_CHARS)
    pedido = prompt_store.render(
        PROMPT_FEATURE_DIR,
        f"{PROMPT_VERSION}.pedido",
        {
            "descricao": user_data_block(texto) if texto else _NAO_INFORMADO,
            "peso_total": f"{int(total_grams)} g" if total_grams else _NAO_INFORMADO,
        },
    )
    return f"{instrucoes}\n\n{pedido}"


def output_schema() -> dict[str, Any]:
    """JSON Schema strict: o item é o da leitura por texto; no lugar de `notes`, o
    `plate_status` (sem texto livre vindo da foto até a tela). v2 (#323): por item a
    evidência, a faixa de gramas e a incerteza; na leitura, a incerteza principal."""
    itens = nutrition_meal_parse_ai.output_schema()["properties"]["items"]
    incerteza = {"type": "string", "enum": [valor.value for valor in meal_photo.Uncertainty]}
    item = itens["items"]
    item["required"] = [*item["required"], "evidence", "grams_low", "grams_high", "uncertainty"]
    item["properties"] = {
        **item["properties"],
        "evidence": {"type": "string", "enum": [valor.value for valor in meal_photo.ItemEvidence]},
        "grams_low": {"type": ["number", "null"]},
        "grams_high": {"type": ["number", "null"]},
        "uncertainty": incerteza,
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["plate_status", "items", "main_uncertainty"],
        "properties": {
            "plate_status": {"type": "string", "enum": [status.value for status in meal_photo.PlateStatus]},
            "items": itens,
            "main_uncertainty": incerteza,
        },
    }


_Number = StrictInt | StrictFloat

_EstimateOut = create_model(  # type: ignore[call-overload]
    "MealPhotoEstimateOut",
    __config__=ConfigDict(extra="forbid"),
    **{
        campo: ((_Number, ...) if campo in meal_parse.ESTIMATE_REQUIRED_FIELDS else (Optional[_Number], ...))
        for campo in meal_parse.ESTIMATE_FIELDS
    },
)

_ItemOut = create_model(  # type: ignore[call-overload]
    "MealPhotoItemOut",
    __config__=ConfigDict(extra="forbid"),
    name=(StrictStr, ...),
    quantity=(Optional[_Number], ...),
    unit=(StrictStr, ...),
    grams_estimate=(_Number, ...),
    preparation=(Optional[StrictStr], ...),
    confidence=(_Number, ...),
    **{meal_parse.ESTIMATE_KEY: (Optional[_EstimateOut], ...)},
    # v2 (#323): opcionais na validação (leitura sem eles segue válida); o motor descarta o incoerente.
    evidence=(Optional[StrictStr], None),
    grams_low=(Optional[_Number], None),
    grams_high=(Optional[_Number], None),
    uncertainty=(Optional[StrictStr], None),
)


class MealPhotoOut(BaseModel):
    """Forma exigida da resposta (a mesma do schema): o que fugir dela é falha do job,
    não um item aproveitado pela metade."""

    model_config = ConfigDict(extra="forbid")

    plate_status: StrictStr
    items: list[_ItemOut] = Field(...)  # type: ignore[valid-type]
    main_uncertainty: Optional[StrictStr] = None


# ---------------------------------------------------------------------------
# Chamada
# ---------------------------------------------------------------------------


class MealPhotoJobError(RuntimeError):
    """Falha do job com o código do motor; a mensagem é o texto pt-BR de `labels`."""

    def __init__(self, code: str, *, model: Optional[str] = None, tokens_in: Any = None, tokens_out: Any = None):
        super().__init__(nutrition_labels.limitation_label(code))
        self.code = code
        self.model = model
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


@dataclass(frozen=True)
class MealPhotoReading:
    """Leitura da IA já validada na forma, com o que a auditoria grava."""

    data: dict[str, Any]
    model: str
    tokens_in: Optional[int]
    tokens_out: Optional[int]
    photos_read: int = 1


def read_meal_photo(
    images: Union[bytes, Sequence[bytes]],
    *,
    user_id: Any = None,
    timeout: float = LLM_TIMEOUT_SECONDS,
    description: Any = None,
    total_grams: Optional[int] = None,
) -> MealPhotoReading:
    """Uma chamada com as fotos da mesma refeição (uma `ImageInput` por foto; `bytes`
    soltos valem como uma foto) e as dicas da pessoa. `OpenAIGenerationError` da chamada
    propaga; forma inválida vira `MealPhotoJobError(ERR_MEAL_READING_INVALID)` com os
    tokens gastos."""
    fotos = [images] if isinstance(images, (bytes, bytearray)) else list(images)
    resultado = openai_client.call_openai_structured_vision(
        build_prompt(description=description, total_grams=total_grams),
        [ImageInput(foto, meal_photo.MEAL_PHOTO_CONTENT_TYPE, meal_photo.MEAL_IMAGE_DETAIL) for foto in fotos],
        model_name(),
        output_schema(),
        schema_name=SCHEMA_NAME,
        timeout=timeout,
        feature=FEATURE,
        user_id=user_id,
        prompt_version=PROMPT_VERSION,
    )
    try:
        leitura = MealPhotoOut.model_validate(resultado.data)
    except ValidationError as exc:
        # Só a contagem: o conteúdo veio de uma foto de usuário.
        logger.warning("Leitura da foto do prato fora da forma (%s erro(s))", exc.error_count())
        raise MealPhotoJobError(
            meal_photo.ERR_MEAL_READING_INVALID,
            model=resultado.model,
            tokens_in=resultado.tokens_in,
            tokens_out=resultado.tokens_out,
        ) from exc
    return MealPhotoReading(
        data=leitura.model_dump(), model=resultado.model, tokens_in=resultado.tokens_in, tokens_out=resultado.tokens_out,
        photos_read=len(fotos),
    )


# ---------------------------------------------------------------------------
# Resultado do job
# ---------------------------------------------------------------------------


def job_result(
    resultado: meal_parse.MealParseResult,
    linhas: Mapping[int, dict[str, Any]],
    reading: MealPhotoReading,
    status: meal_photo.PlateStatus,
    payload: Mapping[str, Any],
    aluno: int,
    review: Optional[meal_photo.MealPhotoReview] = None,
) -> dict[str, Any]:
    """Mesma forma do resultado da leitura por texto (o editor é o mesmo), mais o
    `plate_status`, a natureza proposta de cada item e, v2 (#323), a evidência, a faixa
    e a incerteza do item e a incerteza principal (null quando a leitura não as trouxe)."""
    corpo = nutrition_meal_parse_worker.result_public(resultado, linhas)
    detalhes = review.details if review else ()
    for indice, (publico, item) in enumerate(zip(corpo["items"], resultado.items)):
        natureza = meal_photo.item_nature(item)
        publico["nature"] = natureza.value if natureza else None
        detalhe = detalhes[indice] if indice < len(detalhes) else meal_photo.PhotoItemDetail()
        publico.update(detalhe.to_dict())
    principal = review.main_uncertainty if review else None
    return {
        **corpo,
        "plate_status": status.value,
        "main_uncertainty": principal.value if principal else None,
        "photos_read": reading.photos_read,
        "job_id": payload.get("job_id"),
        "user_id": aluno,
        "local_date": payload.get("local_date"),
        "meal_slot": payload.get("meal_slot"),
        "model": reading.model,
        "prompt_version": PROMPT_VERSION,
        "engine_version": ENGINE_VERSION,
    }


def _fail(usage_id: Any, exc: BaseException, *, code: str, model: Any = None, tokens_in: Any = None,
          tokens_out: Any = None) -> None:
    nutrition_ai_quota.finish_usage(
        usage_id, status=AiUsageStatus.FAILED, model=model, tokens_in=tokens_in, tokens_out=tokens_out, error_code=code
    )
    logger.warning("Foto do prato falhou (ai_usage_id=%s, code=%s, tipo=%s)", usage_id, code, type(exc).__name__)


def run_meal_photo_job(event: Mapping[str, Any]) -> dict[str, Any]:
    """O job `MEAL_PHOTO_JOB_TYPE` da plan-generator. Fecha a linha do `ai_usage_log` em
    todos os caminhos e propaga a falha para o handler gravar `failed`. Foto sem comida
    é SUCESSO com resultado vazio: a IA respondeu e a tentativa custou tokens."""
    usage_id = event.get("ai_usage_id")
    user_id = event.get("user_id")
    # `s3_keys` (#325); `s3_key` antigo: job enfileirado antes do deploy.
    keys = event.get("s3_keys")
    keys = [event.get("s3_key")] if keys is None else keys
    payload = event.get("payload") or {}
    leitura: Optional[MealPhotoReading] = None
    try:
        # Toda chave do usuário, antes de ler qualquer uma; quantidade no teto do dono.
        if (
            not isinstance(keys, list)
            or not 1 <= len(keys) <= meal_photo.MEAL_PHOTO_IMAGES_PER_READING
            or not all(meal_photo.is_meal_photo_key_of(key, user_id) for key in keys)
        ):
            raise MealPhotoJobError(meal_photo.ERR_MEAL_PHOTO_KEY_INVALID)
        imagens: list[bytes] = []
        for key in keys:
            try:
                imagem = nutrition_meal_photo_storage.read_meal_photo(str(key))
            except nutrition_meal_photo_storage.MealPhotoStorageError as exc:
                raise MealPhotoJobError(exc.code) from exc
            # Defesa: a api-core já conferiu tamanho e tipo antes de gravar.
            erro = meal_photo.upload_error(len(imagem), meal_photo.MEAL_PHOTO_CONTENT_TYPE)
            if erro:
                raise MealPhotoJobError(erro)
            imagens.append(imagem)
        erro = meal_photo.total_size_error(len(imagem) for imagem in imagens)
        if erro:
            raise MealPhotoJobError(erro)
        # Dicas da pessoa (#323), já conferidas na api-core; nunca vão para log. Defesa:
        # total fora da faixa do dono é ignorado.
        total = payload.get("total_grams")
        total = None if meal_photo.user_hints_error(None, total) else total
        leitura = read_meal_photo(imagens, user_id=user_id, description=payload.get("description"), total_grams=total)
        revisao = meal_photo.review_reading(leitura.data, total_grams=total)
        solicitante = int(user_id)
        aluno = int(payload.get("diary_user_id") or solicitante)
        try:
            rankings, linhas = nutrition_meal_parse_worker.match_items(
                revisao.items, registrant_user_id=solicitante, diary_user_id=aluno
            )
        except Exception as exc:
            logger.exception("Falha na busca do catálogo depois da foto do prato")
            raise MealPhotoJobError(
                meal_parse.ERR_MATCH_FAILED, model=leitura.model, tokens_in=leitura.tokens_in,
                tokens_out=leitura.tokens_out,
            ) from exc
        decididos = meal_parse.build_result(revisao.items, rankings, revisao.limitations)
        resultado = job_result(decididos, linhas, leitura, revisao.status, payload, aluno, revisao)
    except MealPhotoJobError as exc:
        _fail(usage_id, exc, code=exc.code, model=exc.model, tokens_in=exc.tokens_in, tokens_out=exc.tokens_out)
        raise
    except OpenAIGenerationError as exc:
        _fail(usage_id, exc, code=exc.code or meal_photo.ERR_MEAL_READING_UNEXPECTED)
        raise
    except Exception as exc:
        gastos = {"model": leitura.model, "tokens_in": leitura.tokens_in, "tokens_out": leitura.tokens_out} if leitura else {}
        logger.exception("Falha inesperada na foto do prato (ai_usage_id=%s)", usage_id)
        _fail(usage_id, exc, code=meal_photo.ERR_MEAL_READING_UNEXPECTED, **gastos)
        # A tela nunca vê `str(exc)`: o handler grava a mensagem do dono no job.
        raise MealPhotoJobError(meal_photo.ERR_MEAL_READING_UNEXPECTED, **gastos) from exc
    nutrition_ai_quota.finish_usage(
        usage_id,
        status=AiUsageStatus.SUCCEEDED,
        model=leitura.model,
        tokens_in=leitura.tokens_in,
        tokens_out=leitura.tokens_out,
    )
    return resultado


__all__ = [
    "FEATURE",
    "LLM_TIMEOUT_SECONDS",
    "MealPhotoJobError",
    "MealPhotoOut",
    "MealPhotoReading",
    "PROMPT_FEATURE_DIR",
    "PROMPT_VERSION",
    "SCHEMA_NAME",
    "build_prompt",
    "job_result",
    "model_name",
    "output_schema",
    "prompt_values",
    "read_meal_photo",
    "run_meal_photo_job",
]

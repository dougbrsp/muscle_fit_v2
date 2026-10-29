"""Prompt, schema estrito e chamada da leitura da refeição por IA (nutrição,
Lote L5b; plano §8.3 e §9 a). Roda no worker da Lambda `musclefit-workout-ai`
(`nutrition_meal_parse_worker.py`), nunca na api-core.

A IA só INTERPRETA o texto em itens; o casamento com o catálogo, a proposta de
cada item e os nutrientes são do motor (`domain/nutrition/meal_parse`,
`food_text`, `meal_math`). Regra dos motores (mapa §55): nenhum número do
prompt é escrito à mão — as unidades e as medidas caseiras (`meal_math.Unit`,
`GENERIC_PORTIONS`), os rótulos das refeições (`labels.MEAL_SLOT_LABELS`), os
campos da estimativa (`meal_parse.ESTIMATE_FIELDS`, rótulos da RDC 429), a base
por 100 g e a faixa da confiança vêm da fonte
(`api/privado/tests/unit/test_nutrition_meal_parse_prompt.py` confere). A prosa
mora no repositório privado (`prompt_store`, M103 P1). O texto do usuário entra como DADO entre `<dados_do_usuario>`
(`prompt_user_data`), saneado e cortado no teto do motor.

Ordem do prompt: instruções fixas primeiro e os dados do pedido no fim
(prefixo estável para o cache automático da OpenAI quando passar do mínimo).

`prompt_version` / mudanças:
- nutrition-parse-v1 (2026-10-18): versão inicial.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition import meal_math, meal_parse
from .domain.nutrition.ai_usage import AiFeature
from .domain.nutrition.diary import normalize_meal_slot
from .openai_client import LLM_TIMEOUT_LIVE_SECONDS, StructuredResult, call_openai_structured
from . import prompt_store
from .prompt_user_data import sanitize_user_text, user_data_block, user_data_rule

PROMPT_VERSION = "nutrition-parse-v1"
# Pasta do template em `api/privado/prompts/` (M103 P1).
PROMPT_FEATURE_DIR = "nutrition_meal_parse"
SCHEMA_NAME = "nutrition_meal_parse"
FEATURE = AiFeature.MEAL_PARSE.value

# Modelo: a env que o Terraform publica na `musclefit-workout-ai`
# (`infra/lambda_workout_ai.tf`, `var.openai_model_nutrition_parse`). Sem a env,
# o mesmo modelo "mini" que o Terraform e as outras features de texto da
# Lambda usam por padrão (paridade: DEV sem a env = produção com o default).
MODEL_ENV_VAR = "OPENAI_NUTRITION_PARSE_MODEL"
DEFAULT_MODEL = "gpt-5.4-mini"

# Timeout do cliente: o das chamadas de texto da Lambda de 60 s (`openai_client`).
OPENAI_TIMEOUT_SECONDS = LLM_TIMEOUT_LIVE_SECONDS


def resolve_model() -> str:
    return (os.environ.get(MODEL_ENV_VAR) or "").strip() or DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Schema estrito (formato; a semântica é do motor: `meal_parse.normalize_ai_items`)
# ---------------------------------------------------------------------------


def output_schema() -> dict[str, Any]:
    """JSON Schema do `text.format` (strict): toda propriedade em `required`,
    opcional como tipo anulável, `additionalProperties: false`, sem faixa
    numérica (não suportada no modo estrito; quem confere é o motor)."""
    estimativa = {
        "type": "object",
        "additionalProperties": False,
        "required": list(meal_parse.ESTIMATE_FIELDS),
        "properties": {
            campo: {"type": "number"} if campo in meal_parse.ESTIMATE_REQUIRED_FIELDS else {"type": ["number", "null"]}
            for campo in meal_parse.ESTIMATE_FIELDS
        },
    }
    item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "quantity", "unit", "grams_estimate", "preparation", "confidence", meal_parse.ESTIMATE_KEY],
        "properties": {
            "name": {"type": "string"},
            "quantity": {"type": ["number", "null"]},
            "unit": {"type": "string", "enum": [unidade.value for unidade in meal_parse.AI_UNITS]},
            "grams_estimate": {"type": "number"},
            "preparation": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
            meal_parse.ESTIMATE_KEY: {"anyOf": [estimativa, {"type": "null"}]},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["items", "notes"],
        "properties": {
            "items": {"type": "array", "items": item},
            "notes": {"type": ["string", "null"]},
        },
    }


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def _unit_lines() -> str:
    linhas = []
    for unidade in meal_parse.AI_UNITS:
        rotulo = nutrition_labels.UNIT_LABELS[unidade]
        porcao = meal_math.GENERIC_PORTIONS.get(unidade)
        detalhe = f" = {porcao.ml:g} ml" if porcao else ""
        linhas.append(f"  - `{unidade.value}`: {rotulo}{detalhe}")
    return "\n".join(linhas)


def _estimate_fields() -> str:
    return "; ".join(
        f"`{campo}` = {nutrition_labels.RDC429_FIELD_LABELS[campo]}" for campo in meal_parse.ESTIMATE_FIELDS
    )


def _required_estimate_fields() -> str:
    return ", ".join(f"`{campo}`" for campo in meal_parse.ESTIMATE_REQUIRED_FIELDS)


def prompt_values() -> dict[str, str]:
    """Valores que a parte fixa interpola, todos da fonte (`meal_math`,
    `meal_parse`, `labels`, `prompt_user_data`)."""
    minimo, maximo = meal_parse.CONFIDENCE_RANGE
    # Códigos dos exemplos: os do motor (`meal_math.Unit`), nunca escritos à mão.
    return {
        "regra_dados_usuario": user_data_rule(),
        "unidades": _unit_lines(),
        "unidade_colher_sopa": meal_math.Unit.TABLESPOON.value,
        "unidade_grama": meal_math.Unit.GRAM.value,
        "unidade_contavel": meal_math.Unit.UNIT.value,
        "unidade_ml": meal_math.Unit.MILLILITER.value,
        "unidade_sem_quantidade": meal_parse.UNSTATED_QUANTITY_UNIT.value,
        "confianca_min": f"{minimo:g}",
        "confianca_max": f"{maximo:g}",
        "chave_estimativa": f"{meal_parse.ESTIMATE_KEY}",
        "base": f"{meal_math.PER_100G_REFERENCE:g} {meal_parse.ESTIMATE_BASIS.value}",
        "campos_estimativa": _estimate_fields(),
        "campos_obrigatorios": _required_estimate_fields(),
    }


def instructions() -> str:
    """Parte fixa do prompt (igual para todo pedido). Prosa em
    `api/privado/prompts/nutrition_meal_parse/<PROMPT_VERSION>.md` (`prompt_store`, M103 P1)."""
    return prompt_store.render(PROMPT_FEATURE_DIR, PROMPT_VERSION, prompt_values())


def build_prompt(text: Any, meal_slot: Any = None) -> str:
    """Prompt completo: instruções fixas e, no fim, a refeição e o texto (dado)."""
    slot = normalize_meal_slot(meal_slot)
    refeicao = nutrition_labels.MEAL_SLOT_LABELS[slot] if slot is not None else "não informada"
    texto = sanitize_user_text(text, meal_parse.TEXT_MAX_CHARS)
    pedido = prompt_store.render(
        PROMPT_FEATURE_DIR,
        f"{PROMPT_VERSION}.pedido",
        {"refeicao": refeicao, "descricao": user_data_block(texto)},
    )
    return f"{instructions()}\n\n{pedido}"


def parse_meal_text(text: Any, meal_slot: Any = None, *, user_id: Optional[int] = None) -> StructuredResult:
    """Uma chamada estruturada à OpenAI (`openai_client`, `store=false`, teto de
    saída da feature). Levanta `OpenAIGenerationError` (mensagem genérica em
    pt-BR, diagnóstico só no log)."""
    return call_openai_structured(
        build_prompt(text, meal_slot),
        resolve_model(),
        output_schema(),
        schema_name=SCHEMA_NAME,
        timeout=OPENAI_TIMEOUT_SECONDS,
        feature=FEATURE,
        user_id=user_id,
        prompt_version=PROMPT_VERSION,
    )


__all__ = [
    "DEFAULT_MODEL",
    "FEATURE",
    "MODEL_ENV_VAR",
    "OPENAI_TIMEOUT_SECONDS",
    "PROMPT_VERSION",
    "SCHEMA_NAME",
    "build_prompt",
    "instructions",
    "prompt_values",
    "output_schema",
    "parse_meal_text",
    "resolve_model",
]

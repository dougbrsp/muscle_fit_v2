"""Sugestão de metas por IA: prompt, schema estrito e worker (nutrição N6, decisão D3). Roda na
`musclefit-workout-ai` (texto, decisão E8 do plano), job `ai_usage.PLAN_SUGGESTION_JOB_TYPE`,
enfileirado por `POST /nutrition/plan-suggestion` (`app/nutrition_plan_suggestion.py`).

Fluxo: lê o perfil e o histórico do aluno -> monta o contexto mínimo (`plan_suggestion.build_context`,
sem nome, e-mail nem data de nascimento) -> uma chamada estruturada (`openai_client`: provedor,
modelo, esforço e fallback vêm das envs de `meal_parse`, `llm_anthropic.FEATURE_CONFIG_SOURCE`) ->
revisão do motor (`plan_suggestion.review_suggestion`, que valida com `build_plan_settings`). Nada é
gravado no plano: o resultado volta para o formulário como rascunho, e quem calcula as gramas é a prévia
do motor (`POST /nutrition/plan-preview`). A IA nunca devolve grama nem kcal como meta.

A revisão é do motor (forma, tipo e faixa), sem Pydantic no meio: um código só para cada recusa. Recusa não
corrige nada: o job falha com a mensagem do dono, e os códigos ficam no `ai_usage_log`. A saída não tem texto
livre: os motivos são códigos fechados (`SuggestionReason`, rótulos em `labels`).

Regra dos motores (mapa §84): nenhum número do prompt é escrito à mão; `prompt_values` interpola as faixas da
fonte (`api/privado/tests/unit/test_nutrition_plan_suggestion_prompt.py` confere).

`prompt_version` / mudanças:
- nutrition-plan-suggestion-v1 (2026-11-03): versão inicial.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping, Optional

from . import nutrition_ai_quota, nutrition_meal_parse_ai, prompt_store
from .db import get_conn
from .db_helpers import connect_or_503
from .domain.nutrition import ENGINE_VERSION, plan_suggestion
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.ai_usage import AiFeature, AiUsageStatus
from .domain.nutrition.phases import BULK_STOP_BODY_FAT_PERCENT, CUT_STOP_BODY_FAT_PERCENT
from .domain.nutrition.plan import PROJECTION_TRAINING_WINDOW_DAYS, projection_tdee
from .domain.nutrition.anthropometrics import Subject
from .domain.nutrition.strategy import MacroPriority, Strategy
from .intelligence.dashboard.time_windows import user_today_for
from .nutrition_targets_service import UserInputs, compute_day, load_user_inputs, past_training_kcal
from .openai_client import (
    LLM_TIMEOUT_LIVE_SECONDS,
    OPENAI_TIMEOUT,
    OpenAIGenerationError,
    StructuredResult,
    call_openai_structured,
)
from .prompt_user_data import user_data_block, user_data_rule

logger = logging.getLogger("musclefit.nutrition.plan_suggestion_ai")

PROMPT_VERSION = "nutrition-plan-suggestion-v1"
# Pasta do template em `api/privado/prompts/`.
PROMPT_FEATURE_DIR = "nutrition_plan_suggestion"
SCHEMA_NAME = "nutrition_plan_suggestion"
FEATURE = AiFeature.PLAN_SUGGESTION.value

# O timeout das chamadas de texto da Lambda de 60 s.
LLM_TIMEOUT_SECONDS = LLM_TIMEOUT_LIVE_SECONDS


def model_name() -> str:
    """Modelo da OpenAI (o caminho do fallback): a env de `meal_parse`, a mesma Lambda."""
    return nutrition_meal_parse_ai.resolve_model()


# ---------------------------------------------------------------------------
# Prompt e schema (do motor; nenhum número escrito à mão)
# ---------------------------------------------------------------------------


def _range_text(faixa: tuple[Any, Any]) -> str:
    return f"{faixa[0]:g} a {faixa[1]:g}"


def _ranges_text() -> str:
    linhas = []
    for estrategia, faixas in plan_suggestion.suggestion_ranges().items():
        linhas.append(f"- `{estrategia}`:")
        linhas.extend(f"  - `{campo}`: {_range_text(faixa)}" for campo, faixa in faixas.items())
    return "\n".join(linhas)


def _stop_text(estrategia: Strategy, tabela: Mapping[Any, float]) -> str:
    valores = ", ".join(f"`{sexo.value}` {limite:g}" for sexo, limite in tabela.items())
    return f"- `{estrategia.value}`: {valores}"


def prompt_values() -> dict[str, str]:
    """Os valores que a parte fixa interpola, todos dos donos (o teste renderiza o prompt com marcadores no lugar
    deles e exige zero dígito no texto)."""
    refeicoes = plan_suggestion.meals_range()
    return {
        "regra_dados_usuario": user_data_rule(),
        "estrategias": ", ".join(f"`{s.value}`" for s in Strategy),
        "estrategia_avancado": f"`{Strategy.BULKING.value}`",
        "estrategia_exige_massa_gorda": f"`{Strategy.CUTTING.value}`",
        "faixas": _ranges_text(),
        "paradas": "\n".join(
            (
                _stop_text(Strategy.CUTTING, CUT_STOP_BODY_FAT_PERCENT),
                _stop_text(Strategy.BULKING, BULK_STOP_BODY_FAT_PERCENT),
            )
        ),
        "prioridades": ", ".join(f"`{p.value}`" for p in MacroPriority),
        "refeicoes": _range_text(refeicoes),
        "motivos": "\n".join(
            f"- `{motivo.value}`: {nutrition_labels.PLAN_SUGGESTION_REASON_LABELS[motivo]}"
            for motivo in plan_suggestion.SuggestionReason
        ),
        "motivos_max": str(plan_suggestion.REASONS_MAX),
    }


def instructions() -> str:
    """Parte fixa (igual para todo pedido): `api/privado/prompts/nutrition_plan_suggestion/<PROMPT_VERSION>.md`."""
    return prompt_store.render(PROMPT_FEATURE_DIR, PROMPT_VERSION, prompt_values())


def build_prompt(context: Mapping[str, Any]) -> str:
    """Instruções fixas e, no fim, o contexto como dado. O contexto só tem números e códigos do motor."""
    dados = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    pedido = prompt_store.render(PROMPT_FEATURE_DIR, f"{PROMPT_VERSION}.pedido", {"dados": user_data_block(dados)})
    return f"{instructions()}\n\n{pedido}"


def output_schema() -> dict[str, Any]:
    """JSON Schema strict: só a lista fechada do motor, sem faixa numérica (não suportada no modo estrito; quem
    confere é o motor) e sem texto livre."""
    propriedades: dict[str, Any] = {
        plan_suggestion.FIELD_STRATEGY: {"type": "string", "enum": [s.value for s in Strategy]},
        plan_suggestion.FIELD_ADVANCED: {"type": "boolean"},
        plan_suggestion.FIELD_MACRO_PRIORITY: {"type": "string", "enum": [p.value for p in MacroPriority]},
        plan_suggestion.FIELD_MEALS: {"type": "integer"},
        **{campo: {"type": "number"} for campo in plan_suggestion.ALWAYS_NUMBER_FIELDS},
        **{campo: {"type": ["number", "null"]} for campo in plan_suggestion.OPTIONAL_NUMBER_FIELDS},
        plan_suggestion.REASONS_KEY: {
            "type": "array",
            "maxItems": plan_suggestion.REASONS_MAX,
            "items": {"type": "string", "enum": [motivo.value for motivo in plan_suggestion.SuggestionReason]},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [*plan_suggestion.SUGGESTION_FIELDS, plan_suggestion.REASONS_KEY],
        "properties": {campo: propriedades[campo] for campo in (*plan_suggestion.SUGGESTION_FIELDS, plan_suggestion.REASONS_KEY)},
    }


def call_model(context: Mapping[str, Any], *, user_id: Optional[int] = None) -> StructuredResult:
    """Uma chamada estruturada. Levanta `OpenAIGenerationError` (mensagem genérica, diagnóstico só no log)."""
    return call_openai_structured(
        build_prompt(context),
        model_name(),
        output_schema(),
        schema_name=SCHEMA_NAME,
        timeout=LLM_TIMEOUT_SECONDS,
        feature=FEATURE,
        user_id=user_id,
        prompt_version=PROMPT_VERSION,
    )


# ---------------------------------------------------------------------------
# Contexto (lê o banco, consulta os motores)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SuggestionInputs:
    """O contexto enviado à IA e o que a revisão usa depois: o sujeito de hoje e as entradas do cálculo."""

    context: dict[str, Any]
    subject: Subject
    inputs: UserInputs
    today: date


def load_context(aluno: int, current: Mapping[str, Any]) -> SuggestionInputs:
    """Contexto mínimo do aluno, com o nível de atividade, o modo do gasto e a fórmula do formulário. Só leitura."""
    settings = plan_suggestion.reference_settings(current)
    with connect_or_503(get_conn) as conn:
        hoje = user_today_for(conn, aluno)
        with conn.cursor() as cur:
            inicio = hoje - timedelta(days=PROJECTION_TRAINING_WINDOW_DAYS)
            entradas = load_user_inputs(cur, aluno, inicio, hoje, hoje, [settings])
    comp = compute_day(entradas, settings, hoje, hoje)
    passado = past_training_kcal(entradas, settings, hoje)
    tdee, _premissas = projection_tdee(comp.target.tdee, passado)
    contexto = plan_suggestion.build_context(
        comp.subject.subject,
        bmr_kcal=comp.target.bmr.kcal,
        tdee_kcal=tdee,
        weights=plan_suggestion.daily_weights(entradas.series.weight, hoje),
        past_training_kcal=passado,
        current=current,
    )
    return SuggestionInputs(context=contexto, subject=comp.subject.subject, inputs=entradas, today=hoje)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class PlanSuggestionError(RuntimeError):
    """Falha do job; a mensagem (o que a tela vê) é SEMPRE o texto pt-BR do código do motor em `labels`, nunca o do provedor."""


def _public_message(code: str) -> str:
    return nutrition_labels.limitation_label(code)


def dispatch_plan_suggestion(payload: dict[str, Any], user_id: Any, ai_usage_id: Optional[int] = None) -> dict[str, Any]:
    """Resultado do job (`GET /jobs/{id}`): os campos para o formulário, os motivos e "o que mudou". Fecha a linha
    do `ai_usage_log` em todos os caminhos; a recusa do motor custou tokens e conta como falha com o código."""
    solicitante = int(user_id)
    aluno = int(payload.get("target_user_id") or solicitante)
    atual = plan_suggestion.normalize_current(payload.get("current"))
    try:
        entradas = load_context(aluno, atual)
    except Exception as exc:
        logger.exception("Falha ao montar o contexto da sugestão de metas (ai_usage_id=%s)", ai_usage_id)
        nutrition_ai_quota.finish_usage(
            ai_usage_id, status=AiUsageStatus.FAILED, error_code=plan_suggestion.ERR_SUGGESTION_FAILED
        )
        raise PlanSuggestionError(_public_message(plan_suggestion.ERR_SUGGESTION_FAILED)) from exc

    try:
        resposta = call_model(entradas.context, user_id=solicitante)
    except OpenAIGenerationError as exc:
        nutrition_ai_quota.finish_usage(
            ai_usage_id,
            status=AiUsageStatus.FAILED,
            model=model_name(),
            error_code=exc.code or plan_suggestion.ERR_SUGGESTION_FAILED,
        )
        codigo = plan_suggestion.ERR_SUGGESTION_TIMEOUT if exc.code == OPENAI_TIMEOUT else plan_suggestion.ERR_SUGGESTION_FAILED
        raise PlanSuggestionError(_public_message(codigo)) from exc
    except Exception as exc:
        logger.exception("Falha inesperada na sugestão de metas (ai_usage_id=%s)", ai_usage_id)
        nutrition_ai_quota.finish_usage(
            ai_usage_id, status=AiUsageStatus.FAILED, model=model_name(), error_code=plan_suggestion.ERR_SUGGESTION_FAILED
        )
        raise PlanSuggestionError(_public_message(plan_suggestion.ERR_SUGGESTION_FAILED)) from exc

    uso = {"model": resposta.model, "tokens_in": resposta.tokens_in, "tokens_out": resposta.tokens_out}
    revisao = plan_suggestion.review_suggestion(resposta.data, subject=entradas.subject, current=atual)
    erros = revisao.errors
    if revisao.settings is not None and not erros:
        # Válida na faixa, mas o motor precisa conseguir calcular (cutting sem massa gorda, por exemplo).
        try:
            meta = compute_day(entradas.inputs, revisao.settings, entradas.today, entradas.today).target
        except Exception as exc:
            logger.exception("Falha ao calcular a sugestão de metas (ai_usage_id=%s)", ai_usage_id)
            nutrition_ai_quota.finish_usage(
                ai_usage_id, status=AiUsageStatus.FAILED, error_code=plan_suggestion.ERR_SUGGESTION_FAILED, **uso
            )
            raise PlanSuggestionError(_public_message(plan_suggestion.ERR_SUGGESTION_FAILED)) from exc
        if not meta.computable:
            erros = (plan_suggestion.ERR_SUGGESTION_NOT_COMPUTABLE,)
    if erros or revisao.settings is None:
        # Só os códigos, que passaram pelo alfabeto do motor: nunca o conteúdo da resposta.
        logger.warning("Sugestão de metas recusada pelo motor (ai_usage_id=%s, códigos=%s)", ai_usage_id, erros)
        nutrition_ai_quota.finish_usage(ai_usage_id, status=AiUsageStatus.FAILED, error_code=erros[0], **uso)
        proprio = erros[0] in plan_suggestion.REFUSALS_WITH_OWN_LABEL
        raise PlanSuggestionError(_public_message(erros[0] if proprio else plan_suggestion.ERR_SUGGESTION_REFUSED))

    nutrition_ai_quota.finish_usage(ai_usage_id, status=AiUsageStatus.SUCCEEDED, **uso)
    return {
        "fields": plan_suggestion.suggestion_fields(revisao.settings),
        "reasons": nutrition_labels.plan_suggestion_reason_items(revisao.reasons),
        "changes": nutrition_labels.plan_suggestion_change_items(plan_suggestion.suggestion_changes(atual, revisao.settings)),
        "user_id": aluno,
        "model": resposta.model,
        "prompt_version": PROMPT_VERSION,
        "engine_version": ENGINE_VERSION,
    }


__all__ = [
    "FEATURE",
    "LLM_TIMEOUT_SECONDS",
    "PROMPT_FEATURE_DIR",
    "PROMPT_VERSION",
    "PlanSuggestionError",
    "SCHEMA_NAME",
    "SuggestionInputs",
    "build_prompt",
    "call_model",
    "dispatch_plan_suggestion",
    "instructions",
    "load_context",
    "model_name",
    "output_schema",
    "prompt_values",
]

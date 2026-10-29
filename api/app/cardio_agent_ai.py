from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Any, Optional

from . import cardio_recommendation_v3 as contract
from . import prompt_store
from .domain import cardio_envelope, cardio_policy, cardio_prescription
from .domain.hr_zones import HR_ZONE_MAX, HR_ZONE_MIN, zone_label
from .intelligence.cardio_context_v3 import CardioAgentContextV3Service
from .openai_client import OpenAIGenerationError
from .openai_client import call_openai_structured as _call_openai_structured
from .prompt_user_data import (
    USER_NAME_MAX_CHARS,
    USER_NOTES_MAX_CHARS,
    delimit_all_strings,
    delimit_user_fields,
    user_data_rule,
)
from .rag_chunking import select_cardio_rag_text


def _resolve_model() -> str:
    """`OPENAI_MODEL_CARDIO` (a que o Terraform publica) primeiro, depois a
    antiga `OPENAI_CARDIO_AGENT_MODEL`; default `gpt-5.4-mini`."""
    for name in ("OPENAI_MODEL_CARDIO", "OPENAI_CARDIO_AGENT_MODEL"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return "gpt-5.4-mini"


OPENAI_MODEL = _resolve_model()

# Versao do prompt (IA-11). Vai para o evento `llm.usage`, para a coluna
# `cardio_recommendations.prompt_version` (098) e para a chave do cache do
# prefixo. Suba SEMPRE que o texto renderizado mudar de proposito (snapshot:
# tests/unit/test_llm_prompt_renderizado_snapshot.py; docs/ia/EVALS.md).
# v2 (IA-10): texto livre do usuario delimitado por `<dados_do_usuario>`.
# v3 (M98 C3a): saida JSON estruturada (schema strict de
# `cardio_recommendation_v3`), validada por `cardio_prescription` e filtrada
# pelo envelope de `cardio_envelope`; contexto compacto no fuso do usuario
# (`CardioAgentContextV3Service`), com cardio realizado, aderencia, zonas e os
# proximos dias do plano; RAG de `CARDIO.md` (`select_cardio_rag_text`).
# v4 (27/10/2026, decisoes do usuario): HIIT vetado so na VESPERA do treino de
# perna (no proprio dia, permitido depois da forca); cortes de TSB -25/-35
# (interpolados de `cardio_envelope`). Mesmo schema e contexto da v3.
# v5 (M99, 27/10/2026): o EPOC/PAI manual saiu do contexto; entra
# `effort_metrics_estimated` (EPOC e PAI ACUMULADOS DE 7 DIAS, ESTIMADOS do
# tempo em zona do Intervals pelo motor `cardio_load`). A meta de PAI do
# `cardio_policy` e semanal (acumulado de 7 dias). Mesmo schema.
PROMPT_VERSION = "cardio-agent-v5"

# Chaves de texto livre do usuario no contexto: `notes` de sono,
# `activity_name` de treinos e de cardio realizado, nomes de slot e de Modelo.
_USER_TEXT_FIELDS = {
    "notes": USER_NOTES_MAX_CHARS,
    "activity_name": USER_NAME_MAX_CHARS,
    "name": USER_NAME_MAX_CHARS,
    "slot_name": USER_NAME_MAX_CHARS,
    "template_name": USER_NAME_MAX_CHARS,
}

RAG_HEADER = "## Base de conhecimento (RAG de cardio)"


class CardioAgentGenerationError(OpenAIGenerationError):
    pass


def _limits_text() -> str:
    """Mínimo–máximo de cada campo, lidos do dono (`FIELD_LIMITS`)."""
    return cardio_prescription.limits_text()


_ZONES = f"{zone_label(HR_ZONE_MIN)}–{zone_label(HR_ZONE_MAX)}"
# Padrão COM o modo (`default_prescription`): `describe(DEFAULTS[HIIT])`
# leria o item como Normal e o HIIT sairia sem as séries.
_DEFAULT_CONTINUOUS = cardio_prescription.describe(
    cardio_prescription.default_prescription(cardio_prescription.CardioMode.CONTINUOUS)
)
_DEFAULT_HIIT = cardio_prescription.describe(cardio_prescription.default_prescription(cardio_prescription.CardioMode.HIIT))

# Pasta do template em `api/privado/prompts/` (M103 P1): a prosa do prompt de
# sistema mora no repositorio privado (`<PROMPT_VERSION>.md`).
PROMPT_FEATURE_DIR = "cardio_agent"


def prompt_values() -> dict[str, str]:
    """Valores que o template interpola. Numeros de dominio: `cardio_policy`
    (PAI, escada de Z2, faixa de VO2max, protocolo de HIIT, memoria
    adaptativa), `cardio_prescription` (campos, limites, padroes),
    `cardio_envelope` (horizonte, interferencia, tetos) e `hr_zones`
    (vocabulario de zonas). O prompt so interpola."""
    return {
        "pai_meta_min": f"{cardio_policy.PAI_TARGET_MIN}",
        "zonas": _ZONES,
        "limites": _limits_text(),
        "sessao_max_min": f"{cardio_prescription.CARDIO_SESSION_MAX_MINUTES}",
        "padrao_continuo": _DEFAULT_CONTINUOUS,
        "padrao_hiit": _DEFAULT_HIIT,
        "hiit_semana_max": f"{cardio_envelope.WEEKLY_HIIT_MAX}",
        "sinal_recuperacao_dias": f"{cardio_envelope.RECOVERY_SIGNAL_DAYS}",
        "hiit_tsb_min": f"{cardio_envelope.HIIT_MIN_TSB}",
        "tsb_risco_alto": f"{cardio_envelope.TSB_HIGH_RISK_BELOW}",
        "sessoes_dia_max": f"{cardio_envelope.SESSIONS_PER_DAY_MAX}",
        "sessoes_semana_max": f"{cardio_envelope.WEEKLY_CARDIO_SESSIONS_MAX}",
        "sessoes_semana_min": f"{cardio_envelope.WEEKLY_CARDIO_SESSIONS_MIN}",
        "continuo_teto_min": f"{cardio_envelope.CONTINUOUS_SESSION_MAX_MINUTES}",
        "escada_preferencia": cardio_policy.preference_ladder_text(),
        "hiit_protocolo": cardio_policy.hiit_protocol_text(),
        "z2_zona": zone_label(cardio_prescription.DEFAULT_CONTINUOUS_TARGET_ZONE),
        "z2_vo2max_pct": cardio_policy.range_text(cardio_policy.Z2_VO2MAX_PERCENT_RANGE),
        "recuperacao_ativa_min": cardio_policy.range_text(cardio_policy.active_recovery_minutes()),
        "z2_correcao_min": cardio_policy.range_text(cardio_policy.Z2_TREND_CORRECTION_MINUTES),
        "exemplo_z2_min": f"{cardio_policy.EXAMPLE_EVIDENCE_Z2_MINUTES}",
        "exemplo_aptidao": f"{cardio_policy.EXAMPLE_EVIDENCE_FITNESS}",
        "exemplo_pai_min": f"{cardio_policy.EXAMPLE_EVIDENCE_PAI_MIN}",
        "exemplo_ramp_rate": cardio_policy.format_decimal_br(cardio_policy.EXAMPLE_EVIDENCE_RAMP_RATE_MIN),
        "adaptativo_min_ocorrencias": f"{cardio_policy.ADAPTIVE_MIN_OCCURRENCES}",
        "regra_dados_usuario": user_data_rule(),
    }


def system_prompt() -> str:
    """Instrucoes fixas do agente, renderizadas sob demanda (o template e lido
    uma vez por processo pelo `prompt_store`; falta de template ou de valor
    falha alto, na chamada, e nao no import)."""
    return prompt_store.render(PROMPT_FEATURE_DIR, PROMPT_VERSION, prompt_values())


def prompt_context(context: dict[str, Any]) -> dict[str, Any]:
    """Copia do contexto para o PROMPT, com o texto livre do usuario
    delimitado (IA-10). `context` (persistido como `context_snapshot`)
    continua com o texto cru."""
    delimited = delimit_user_fields(
        {key: value for key, value in context.items() if key != "proposed_workout"}, _USER_TEXT_FIELDS
    )
    if "proposed_workout" in context:
        # Montado pelo frontend e sem schema (IA-10): toda string e do usuario.
        delimited["proposed_workout"] = delimit_all_strings(context["proposed_workout"], USER_NOTES_MAX_CHARS)
    return {key: delimited[key] for key in context}


def load_rag_text() -> str:
    """RAG do agente (ponto unico de troca no snapshot/eval)."""
    return select_cardio_rag_text()


def build_prompt(context: dict[str, Any], rag_text: str = "") -> str:
    """Instrucoes fixas -> RAG (fixo por versao) -> dados: o prefixo e
    cacheavel (`prompt_cache_key`). Dados em JSON compacto."""
    rag = f"\n\n{RAG_HEADER}\n{rag_text}" if rag_text else ""
    dados = json.dumps(prompt_context(context), ensure_ascii=False, default=str, separators=(",", ":"))
    return system_prompt() + rag + "\n\nDados:\n" + dados


def call_openai(prompt: str, user_id: int | None = None) -> dict[str, Any]:
    """Saida estruturada (schema strict); qualquer falha vira
    `CardioAgentGenerationError` (subclasse de `OpenAIGenerationError`)."""
    try:
        result = _call_openai_structured(
            prompt,
            OPENAI_MODEL,
            contract.llm_schema(),
            schema_name=contract.SCHEMA_NAME,
            timeout=90,
            feature="cardio_agent",
            user_id=user_id,
            prompt_version=PROMPT_VERSION,
        )
    except OpenAIGenerationError as exc:
        raise CardioAgentGenerationError(str(exc)) from exc
    return result.data


def build_envelope(context: dict[str, Any]) -> cardio_envelope.Envelope:
    """Envelope do dono a partir do contexto (antes do LLM)."""
    signals = cardio_envelope.assess_recovery(context.get("intervals_wellness") or [], context.get("muscle_recovery") or [])
    intense = [item.get("date") for item in context.get("realized_cardio") or [] if item.get("intense")]
    after = context.get("plan_day_after_horizon") or {}
    return cardio_envelope.build_envelope(
        context.get("plan_days") or [],
        signals,
        realized_intense_dates=intense,
        next_day_groups=after.get("muscle_groups") or [],
    )


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def generate_cardio_recommendation(
    days: int = 14,
    proposed_workout: dict | str | None = None,
    reference_date: str | None = None,
    reference_time: str | None = None,
    user_id: int | None = None,
    workout_plan_id: int | None = None,
) -> dict[str, Any]:
    """Gera a recomendacao estruturada (v3): contexto compacto -> envelope do
    dono -> LLM com schema strict -> validacao pelo dono + envelope de novo ->
    `recommendation_json` + markdown legado. Nao persiste (quem persiste e
    `cardio_generation_core.dispatch_cardio_generation`)."""
    context = CardioAgentContextV3Service().build_context_v3(
        days=days,
        user_id=user_id,
        reference_date=_parse_date(reference_date),
        workout_plan_id=workout_plan_id,
    )
    if proposed_workout:
        context["proposed_workout"] = proposed_workout
    context["today"] = context.get("today") or date.today().isoformat()
    context["now_time"] = context.get("now_time") or datetime.now().strftime("%H:%M")
    if reference_date:
        context["reference_date"] = reference_date
    if reference_time:
        context["reference_time"] = reference_time

    envelope = build_envelope(context)
    context["envelope"] = envelope.to_dict()
    rag_text = load_rag_text()
    prompt = build_prompt(context, rag_text)
    data = call_openai(prompt, user_id)
    recommendation = contract.process_llm_output(data, envelope, context, prompt_version=PROMPT_VERSION)
    # RAG vazio (S3/arquivo indisponível) não derruba a geração, mas fica
    # registrado na linha para o smoke e para a auditoria.
    recommendation["rag_chars"] = len(rag_text or "")

    return {
        "model": OPENAI_MODEL,
        "prompt_version": PROMPT_VERSION,
        "recommendation_json": recommendation,
        "recommendation_markdown": contract.render_markdown(recommendation),
        "context_snapshot": context,
    }

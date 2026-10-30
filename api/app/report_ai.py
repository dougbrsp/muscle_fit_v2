from __future__ import annotations

import json
import os
import re
from typing import Any

from . import prompt_store
from .domain.e1rm import E1RM_MAX_RELIABLE_REPS
from .intelligence.dashboard.metrics_registry import threshold
from .intelligence.progression_service import RECENT_RECORDS_DAYS
from .openai_client import LLM_TIMEOUT_LONG_SECONDS, OpenAIGenerationError
from .openai_client import call_openai as _call_openai_shared
from .prompt_user_data import USER_NAME_MAX_CHARS, USER_NOTES_MAX_CHARS, delimit_user_fields, user_data_rule

OPENAI_MODEL = os.getenv("OPENAI_REPORTS_MODEL", "gpt-5.4-mini")

# Versao do prompt da "Analise por IA" (IA-11). So vai para o evento
# `llm.usage`, nunca para o texto do prompt. Suba SEMPRE que o texto
# renderizado mudar de proposito (snapshot:
# tests/unit/test_llm_prompt_renderizado_snapshot.py; docs/ia/EVALS.md).
# v2 (IA-06 b): exemplo de formatacao com cargas ilustrativas (X.XXX/Y.YYY) e
# RAG sem percentual de carga de aquecimento/ajuste nem faixa de reps em prosa.
# v3 (IA-02/IA-05): ordem fixa "instrucoes -> RAG -> dados" (a janela `days`
# saiu da 1a frase e foi para junto dos dados: prefixo cacheavel) e RAG com
# teto, sem o fallback de TIPOS_TREINO (`ReportDataService.build_rag_context`).
# v4 (IA-17): sai `intervals_external_id` de `intervals_correlation` (id da
# atividade na conta Intervals.icu do usuario; opaco, nao serve a analise).
# v5 (IA-10, prompt injection): nomes de treino e de exercicio (e notas, se
# vierem) vao delimitados por `<dados_do_usuario>` no JSON de dados, e a regra
# `user_data_rule()` entra nas instrucoes.
PROMPT_VERSION = "workouts-analysis-v5"

# M103 P2: a PROSA do prompt mora no repositorio privado,
# `api/privado/prompts/workouts_analysis/<PROMPT_VERSION>[.rag].md`
# (`prompt_store`); aqui ficam a ordem, os dados e os valores (dos donos).
PROMPT_FEATURE_DIR = "workouts_analysis"

# Chaves de texto livre do usuario no payload `training`: `name` (treinos de
# `weekly_summary`/`intervals_correlation`), `exercise_name` (catalogo, com
# fallback para o nome digitado/importado) e `notes`.
_USER_TEXT_FIELDS = {
    "name": USER_NAME_MAX_CHARS,
    "exercise_name": USER_NAME_MAX_CHARS,
    "notes": USER_NOTES_MAX_CHARS,
}

# Dono: `metrics_registry.THRESHOLDS["plateau_min_exposures"]` (lido por
# `comparability.classify_exercise_progression`). O prompt so interpola.
PLATEAU_MIN_EXPOSURES = int(threshold("plateau_min_exposures"))


class ReportGenerationError(OpenAIGenerationError):
    """Erro ao gerar a analise de evolucao dos treinos via OpenAI.

    Este modulo nasceu para o relatorio em PDF (5 analises por IA). O PDF foi
    substituido pelo relatorio em Markdown sem IA (`app/reports.py`); sobrou
    so `analyze_training_evolution`, usada pela "Analise por IA" de
    `/dashboard/treinos` (`workouts_analysis_core.py`)."""


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)


def _call_openai(prompt: str, user_id: Any = None, cache_prefix: str | None = None) -> str:
    # Colapsa linhas em branco excedentes deixadas pela insercao condicional
    # do bloco de RAG (_rag_section retorna "" quando nao ha contexto) - so
    # cosmetica, nao muda o conteudo do prompt.
    prompt = _collapse_blank_lines(prompt)
    if cache_prefix:
        cache_prefix = _collapse_blank_lines(cache_prefix).rstrip("\n")
    try:
        return _call_openai_shared(
            prompt,
            model=OPENAI_MODEL,
            timeout=LLM_TIMEOUT_LONG_SECONDS,
            feature="workouts_analysis",
            user_id=user_id,
            prompt_version=PROMPT_VERSION,
            cache_prefix=cache_prefix,
        )
    except OpenAIGenerationError as exc:
        raise ReportGenerationError(str(exc)) from exc


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str, indent=2)


def _rag_section(rag_context: str) -> str:
    """Monta o bloco de material de referencia do RAG local de metodo de
    treino a ser injetado no prompt, ANTES do JSON de dados. Retorna string
    vazia quando `rag_context` vier vazio (RAG indisponivel/sem grupos
    musculares para filtrar), para nao deixar um bloco vazio confuso no
    prompt."""
    if not rag_context:
        return ""
    return prompt_store.render(PROMPT_FEATURE_DIR, f"{PROMPT_VERSION}.rag", {"rag": rag_context}).strip()


def prompt_values() -> dict[str, str]:
    """Numeros das regras do prompt, lidos dos donos (M74/M76): o veredito de
    progressao (`exercise_progression`) vem pronto da regra unica
    (`comparability`); a janela dos recordes, de
    `progression_service.RECENT_RECORDS_DAYS` (definicao em
    `session_records.record_events`); o teto do e1RM confiavel, de
    `domain/e1rm`; o minimo de exposicoes do plato, de `metrics_registry`."""
    return {
        "exposicoes_minimas_plato": f"{PLATEAU_MIN_EXPOSURES}",
        "e1rm_max_reps": f"{E1RM_MAX_RELIABLE_REPS}",
        "dias_recordes_recentes": f"{RECENT_RECORDS_DAYS}",
    }


def _ai_training_payload(training_payload: dict[str, Any]) -> dict[str, Any]:
    """Copia do payload `training` para o prompt. `recent_records` (regra
    unica de recorde, M76 Lote 3b) vai como esta; o antigo
    `progression_highlights` (MAX(best_weight_kg) por nome) nao existe mais.
    O payload original (devolvido a tela) nao muda. IA-17: sem o
    `intervals_external_id` das atividades correlacionadas."""
    payload = dict(training_payload or {})
    payload.pop("progression_highlights", None)
    payload.setdefault("recent_records", [])
    payload.setdefault("exercise_progression", [])
    correlation = payload.get("intervals_correlation")
    if isinstance(correlation, dict) and isinstance(correlation.get("matched_workouts"), list):
        # IA-17: o id externo do Intervals.icu identifica a conta do usuario
        # la fora e nao entra em nenhuma secao da analise.
        payload["intervals_correlation"] = {
            **correlation,
            "matched_workouts": [
                {k: v for k, v in item.items() if k != "intervals_external_id"} if isinstance(item, dict) else item
                for item in correlation["matched_workouts"]
            ],
        }
    # IA-10: texto livre do usuario delimitado (so na copia do prompt).
    return delimit_user_fields(payload, _USER_TEXT_FIELDS)


def build_prompt(training_payload: dict[str, Any], days: int, rag_context: str = "") -> str:
    """Prompt da "Analise por IA" (antes do colapso de linhas em branco de
    `_call_openai`). A prosa vem do template privado; os numeros, de
    `prompt_values()`; os dados, do payload `training`."""
    return prompt_store.render(PROMPT_FEATURE_DIR, PROMPT_VERSION, _values(training_payload, days, rag_context)).strip()


# Placeholders que mudam a cada pedido (fim do prefixo cacheavel).
REQUEST_PLACEHOLDERS = ("dias", "dados")


def build_cache_prefix(training_payload: dict[str, Any], days: int, rag_context: str = "") -> str:
    """Comeco estavel de `build_prompt` (instrucoes + RAG), para o
    `cache_control` do provedor Anthropic. Nao muda o prompt."""
    return prompt_store.render_prefix(
        PROMPT_FEATURE_DIR, PROMPT_VERSION, _values(training_payload, days, rag_context), REQUEST_PLACEHOLDERS
    ).lstrip()


def _values(training_payload: dict[str, Any], days: int, rag_context: str) -> dict[str, str]:
    rag_section = _rag_section(rag_context)
    # Ordem fixa (IA-05): instrucoes (iguais em toda chamada) -> RAG (igual
    # para o mesmo conjunto de grupos) -> janela e dados (variam por pedido).
    # O prompt caching automatico da OpenAI so reaproveita PREFIXO identico.
    return {
        **prompt_values(),
        "regra_dados_usuario": user_data_rule(),
        "secao_rag": rag_section,
        "dias": f"{days}",
        "dados": _dump(_ai_training_payload(training_payload)),
    }


def analyze_training_evolution(
    training_payload: dict[str, Any], days: int, rag_context: str = "", user_id: Any = None
) -> str:
    """Analisa a evolução dos treinos (volume semanal, progressão, sessões,
    recuperação) do payload `training` produzido por
    `DashboardAggregatorService.build_training_evolution_payload`.
    `rag_context` (opcional) e o texto do RAG local de metodo de treino,
    montado por `ReportDataService.build_rag_context`, usado so para embasar a
    analise com principios tecnicos - nunca como fonte de numeros.

    Progressao por exercicio: a IA recebe o status da regra unica
    (`exercise_progression`) e e instruida a usa-lo em vez de inferir (M74)."""
    return _call_openai(
        build_prompt(training_payload, days, rag_context),
        user_id,
        cache_prefix=build_cache_prefix(training_payload, days, rag_context),
    )

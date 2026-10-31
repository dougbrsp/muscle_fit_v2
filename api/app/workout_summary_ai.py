"""Resumo de fim de TREINO via OpenAI, gerado quando o usuário finaliza a
sessão na tela /modo-treino. Diferente de exercise_summary_ai.py (resumo por
exercício, descartado ao fim da sessão), este resumo é PERSISTIDO em
activities.ai_summary/ai_summary_generated_at (ver POST
/activities/{id}/ai-summary/generate em main.py) para consulta futura na
tela /registro-exercicios.

Segue EXATAMENTE o mesmo padrão arquitetural dos demais módulos de IA do
backend: chamada direta à OpenAI via urllib (POST
https://api.openai.com/v1/responses), env vars próprias, exceção própria,
sem nenhuma dependência de SDK externo.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Mapping, Optional

from . import prompt_store
from .domain.deload import is_deload_session
from .domain.set_sides import WORST_SIDE_RULE_TEXT, has_sides, prompt_version_with_sides, sides_text
from .engine_decision_context import EngineDecision, GroupDecision, format_decision, format_group_decision
from .goal_prompt_context import focus_text, goal_label_suffix, prompt_section
from .goal_prompt_context import prompt_version as goal_prompt_version
from .load_conventions import format_tonnage
from .openai_client import LLM_TIMEOUT_LIVE_SECONDS, OpenAIGenerationError, TextResult, primary_model
from .openai_client import call_openai_text as _call_openai_shared
from .prompt_user_data import USER_NAME_MAX_CHARS, USER_NOTES_MAX_CHARS, user_data_inline, user_data_rule
from .set_labels import set_display_label
from .set_types import non_preparatory_sets

OPENAI_MODEL = os.environ.get("OPENAI_WORKOUT_SUMMARY_MODEL", "gpt-5.4-mini")

# Versao do prompt (IA-11). So vai para o evento `llm.usage`, nunca para o
# texto do prompt. Suba SEMPRE que o texto renderizado mudar de proposito
# (snapshot: tests/unit/test_llm_prompt_renderizado_snapshot.py; docs/ia/EVALS.md).
# v3 (IA-17): sai a linha "Academia" (nome da academia nao serve a analise e
# e dado de localizacao do usuario).
# v4 (IA-10, prompt injection): nome do treino, nomes de exercicio e notas (do
# exercicio e gerais) entram delimitados por `<dados_do_usuario>`, com
# `user_data_rule()`.
# v5 (M95): serie de tecnica entra com o NOME do catalogo `advanced_techniques`
# (resolvido pelo `technique_code` real), nunca com o codigo cru do `set_type`.
# M97 F5: objetivo nao-default (exercicio marcado com o objetivo, persona e
# bloco de regras interpolado dos donos) e sessao de deload (bloco proprio)
# entram so nos ramos novos, versionados por sufixo (`effective_prompt_version`:
# `+goal-v1`, `+deload-v2`, `goal_prompt_context`). Hipertrofia sem deload: o
# texto e a versao de sempre.
PROMPT_VERSION = "workout-summary-v5"

# M103 P2: a PROSA do prompt mora no repositorio privado,
# `api/privado/prompts/workout_summary/<PROMPT_VERSION>.md` (`prompt_store`).
# O prompt RENDERIZADO entra na impressao digital (`input_fingerprint`): os
# bytes do template sao parte da chave de dedupe do resumo (IA-14).
PROMPT_FEATURE_DIR = "workout_summary"


class WorkoutSummaryError(OpenAIGenerationError):
    pass


def _format_exercises(
    exercises: list[dict[str, Any]],
    exercise_decisions: Optional[Mapping[int, EngineDecision]] = None,
    technique_names: Optional[Mapping[str, str]] = None,
) -> str:
    if not exercises:
        return "(nenhum exercício registrado)"
    lines = []
    for ex in exercises:
        all_sets = ex.get("sets") or []
        # Aquecimento/ajuste nao sao series de trabalho: ficam fora da
        # contagem/lista e so sao mencionados como quantidade. Aplicacoes de
        # tecnica continuam, marcadas com o nome legivel da tecnica
        # (`set_labels.set_display_label`, catalogo `advanced_techniques`).
        sets = non_preparatory_sets(all_sets)
        preparatory_count = len(all_sets) - len(sets)
        sets_summary = ", ".join(
            f"{s.get('reps')}x{s.get('weight_kg')}kg"
            + (f"@RPE{s.get('rpe')}" if s.get("rpe") is not None else "")
            # M102: lados da série unilateral (texto do dono `set_sides`).
            + (f" (por lado: {side})" if (side := sides_text(s)) else "")
            + (f" [{label}]" if (label := set_display_label(s, technique_names)) else "")
            for s in sets
        )
        # Nao usa mapped_name (amazfit_name) como fallback: e so o nome do
        # relogio Amazfit/Zepp, nao deve aparecer em prompts de IA (regra de
        # negocio) - usa o nome canonico do catalogo (exercise_name).
        name = user_data_inline(ex.get("original_name") or ex.get("exercise_name"), USER_NAME_MAX_CHARS)
        line = f"- {name or 'Exercício'}"
        if ex.get("muscle_primary"):
            line += f" ({ex.get('muscle_primary')})"
        line += goal_label_suffix(ex.get("training_goal"))
        line += f": {len(sets)} série(s) — {sets_summary or 'sem séries'}"
        if preparatory_count:
            line += f" (+{preparatory_count} de aquecimento/ajuste, não contadas)"
        if ex.get("avg_rpe") is not None:
            line += f" — RPE médio {ex.get('avg_rpe')}"
        if ex.get("muscle_pain") is not None:
            line += f" — dor muscular {ex.get('muscle_pain')}/10"
        if ex.get("joint_pain"):
            line += " — dor articular reportada"
        note = user_data_inline(ex.get("notes"), USER_NOTES_MAX_CHARS)
        if note:
            line += f" — nota: {note}"
        decision = (exercise_decisions or {}).get(ex.get("id"))
        line += (
            f" — decisão do motor para hoje: {format_decision(decision)}"
            if decision
            else " — decisão do motor para hoje: nenhuma"
        )
        lines.append(line)
    if any(has_sides(s) for ex in exercises for s in ex.get("sets") or []):
        lines.append(f"Nota: {WORST_SIDE_RULE_TEXT}")
    return "\n".join(lines)


def _format_group_decisions(groups: Optional[list[GroupDecision]]) -> str:
    if not groups:
        return "(nenhuma decisão por grupo registrada ao finalizar este treino)"
    return "\n".join(f"- {format_group_decision(group)}" for group in groups)


def activity_goals(activity: Mapping[str, Any]) -> list[Any]:
    """Objetivos carimbados nos exercicios do treino (`exercises.training_goal`)."""
    return [ex.get("training_goal") for ex in activity.get("exercises") or []]


def activity_is_deload(activity: Mapping[str, Any]) -> bool:
    """A sessao e de deload? (`activities.deload_source`, dono `domain/deload`)."""
    return is_deload_session(activity.get("deload_source"))


def effective_prompt_version(activity: Mapping[str, Any]) -> str:
    """`PROMPT_VERSION` com o sufixo dos ramos novos (objetivo/deload)."""
    version = goal_prompt_version(PROMPT_VERSION, activity_goals(activity), activity_is_deload(activity))
    # M102: séries com lados = ramo novo do prompt (sufixo do dono).
    sets = (s for ex in activity.get("exercises") or [] for s in ex.get("sets") or [])
    return prompt_version_with_sides(version, any(has_sides(s) for s in sets))


def build_prompt(
    activity: dict[str, Any],
    engine_decisions: Optional[Mapping[str, Any]] = None,
    technique_names: Optional[Mapping[str, str]] = None,
) -> str:
    """`engine_decisions` = `engine_decision_context.load_activity_decisions`:
    decisão de hoje por exercício (`exercises`, chave `exercises.id`) e a
    decisão por grupo gravada ao finalizar o treino (`groups`).
    `technique_names` = `{code: name}` do catálogo `advanced_techniques`
    (`set_labels.technique_names`); ausente, a série de técnica sai com o
    rótulo genérico, nunca com o código cru."""
    decisions = engine_decisions or {}
    exercises_text = _format_exercises(
        activity.get("exercises") or [], decisions.get("exercises"), technique_names
    )
    groups_text = _format_group_decisions(decisions.get("groups"))
    duration = activity.get("duration_seconds")
    duration_text = f"{round(duration / 60)} minutos" if duration else "não registrada"
    workout_name = user_data_inline(activity.get("name"), USER_NAME_MAX_CHARS) or "Treino"
    workout_notes = user_data_inline(activity.get("notes"), USER_NOTES_MAX_CHARS) or "(nenhuma)"
    goals = activity_goals(activity)
    # M97 F5: "" para hipertrofia sem deload (prompt de sempre).
    goal_text = prompt_section(goals, activity_is_deload(activity), technique_names=technique_names)

    prompt = prompt_store.render(
        PROMPT_FEATURE_DIR,
        PROMPT_VERSION,
        {
            "foco": focus_text(goals),
            "regra_dados_usuario": user_data_rule(),
            "treino": workout_name,
            "data": f"{activity.get('local_date')}",
            "duracao": duration_text,
            "volume": format_tonnage(activity.get("total_volume_kg")),
            "rpe_medio": f"{activity.get('avg_rpe') or 'não informado'}",
            "exercicios": exercises_text,
            "notas": workout_notes,
            "decisoes_grupo": groups_text,
            "bloco_objetivo": goal_text,
        },
    )
    return prompt.strip()


FEATURE = "workout_summary"


def input_fingerprint(prompt: str, model: Optional[str] = None) -> str:
    """Impressao digital da ENTRADA do resumo (IA-14): SHA-256 de modelo +
    `PROMPT_VERSION` + prompt renderizado. O prompt ja carrega tudo o que
    muda a resposta (series, notas, decisoes do motor); modelo e versao
    entram porque a mesma entrada num modelo/prompt novo e outro resumo.
    Gravada em `activities.ai_summary_input_sha256` (migracao 070).

    `model`: o nome CONFIGURADO do modelo que respondeu
    (`TextResult.requested_model`, estavel; nunca o eco datado). Sem ele, o do
    provedor primario da feature (`openai_client.primary_model`): `OPENAI_MODEL`
    com a feature na OpenAI - a mesma impressao de antes do provedor por
    feature - ou o Claude da feature com `LLM_PROVIDER_WORKOUT_SUMMARY=anthropic`.
    Trocar de provedor muda a impressao: o resumo salvo antes so e gerado de
    novo quando alguem o pede outra vez (sob demanda), nunca em lote."""
    material = "\n".join((model or primary_model(FEATURE, OPENAI_MODEL), PROMPT_VERSION, prompt))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def call_openai(prompt: str, user_id: Any = None, prompt_version: str = PROMPT_VERSION) -> TextResult:
    """Wrapper fino sobre `openai_client.call_openai_text` (cliente unico: chave
    lida a cada chamada, retry so em 429/5xx, evento `llm.usage`, provedor por
    feature com fallback). Devolve o texto E o modelo que respondeu. Reexporta
    toda falha como `WorkoutSummaryError`, subclasse de `OpenAIGenerationError`."""
    try:
        return _call_openai_shared(
            prompt,
            model=OPENAI_MODEL,
            timeout=LLM_TIMEOUT_LIVE_SECONDS,
            feature=FEATURE,
            user_id=user_id,
            prompt_version=prompt_version,
        )
    except OpenAIGenerationError as exc:
        raise WorkoutSummaryError(str(exc)) from exc


def generate_workout_summary(
    activity: dict[str, Any],
    engine_decisions: Optional[Mapping[str, Any]] = None,
    user_id: Any = None,
    technique_names: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Gera o resumo de fim de treino via OpenAI a partir do payload completo
    de ActivityDetail (services.activity_detail). Não persiste nada — isso é
    feito pelo endpoint POST /activities/{activity_id}/ai-summary/generate,
    mesmo padrão de "gera e já salva numa chamada só" de
    POST /cardio-recommendations/generate."""
    if not activity.get("id"):
        raise WorkoutSummaryError("activity inválida: id ausente.")

    prompt = build_prompt(activity, engine_decisions, technique_names)
    result = call_openai(prompt, user_id, effective_prompt_version(activity))

    # Modelo que RESPONDEU (inclusive depois do fallback); a impressao usa o
    # nome configurado dele, para o dedupe bater na proxima chamada igual.
    return {
        "model": result.model,
        "summary": result.text.strip(),
        "input_sha256": input_fingerprint(prompt, result.requested_model),
    }

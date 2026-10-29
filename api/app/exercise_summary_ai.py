"""Resumo de fim de EXERCÍCIO via OpenAI, usado pela tela mobile /modo-treino
logo após a última série de um exercício ser salva (tanto no fluxo guiado por
ficha quanto no treino avulso).

Segue EXATAMENTE o mesmo padrão arquitetural de app.exercise_set_evaluation_ai/
app.cardio_agent_ai/app.workout_summary_ai: chamada direta à OpenAI via
urllib (POST https://api.openai.com/v1/responses), env vars próprias,
exceção própria, sem nenhuma dependência de SDK externo. Deliberadamente NÃO
persiste nada no banco — diferente do resumo de fim de TREINO
(workout_summary_ai.py), este é consumido ao vivo pelo frontend e descartado
depois.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from . import prompt_store
from .engine_decision_context import EngineDecision, format_decision
from .goal_prompt_context import focus_text, prompt_section
from .goal_prompt_context import prompt_version as goal_prompt_version
from .openai_client import LLM_TIMEOUT_LIVE_SECONDS, OpenAIGenerationError
from .openai_client import call_openai as _call_openai_shared
from .prompt_user_data import USER_NAME_MAX_CHARS, USER_NOTES_MAX_CHARS, user_data_inline, user_data_rule
from .domain.set_sides import WORST_SIDE_RULE_TEXT, has_sides, prompt_version_with_sides, sides_text
from .set_labels import set_display_label

OPENAI_MODEL = os.environ.get("OPENAI_EXERCISE_SUMMARY_MODEL", "gpt-5.4-mini")

# Versao do prompt (IA-11). So vai para o evento `llm.usage`, nunca para o
# texto do prompt. Suba SEMPRE que o texto renderizado mudar de proposito
# (snapshot: tests/unit/test_llm_prompt_renderizado_snapshot.py; docs/ia/EVALS.md).
# v2 (IA-10, prompt injection): nome do exercicio e anotacoes (da serie e
# agregada) entram delimitados por `<dados_do_usuario>`, com `user_data_rule()`.
# M97 F5: objetivo nao-default do exercicio (`payload["training_goal"]`, o
# carimbado em `exercises`) e sessao de deload (`payload["deload"]`) entram so
# nos ramos novos, versionados por sufixo (`effective_prompt_version`).
# v3 (M95b/M95c): serie de tecnica rotulada com o NOME do catalogo
# `advanced_techniques` (`set_labels.set_display_label`, pelo `technique_code`
# real da linha) e aquecimento/ajuste/back-off com rotulo legivel, nunca o
# codigo cru do `set_type`; as tecnicas vetadas no bloco do objetivo tambem
# saem pelo nome. Os nomes chegam prontos em `payload["technique_names"]`.
PROMPT_VERSION = "exercise-summary-v3"

# M103 P2: a PROSA do prompt mora no repositorio privado,
# `api/privado/prompts/exercise_summary/<PROMPT_VERSION>[.parte].md`
# (`prompt_store`); aqui ficam os ramos, a ordem e os dados.
PROMPT_FEATURE_DIR = "exercise_summary"


def _text(part: str, values: Mapping[str, str]) -> str:
    return prompt_store.render(PROMPT_FEATURE_DIR, f"{PROMPT_VERSION}{part}", values)


def effective_prompt_version(payload: dict[str, Any]) -> str:
    """`PROMPT_VERSION` com o sufixo dos ramos novos (objetivo/deload)."""
    version = goal_prompt_version(PROMPT_VERSION, payload.get("training_goal"), bool(payload.get("deload")))
    # M102: séries com lados = ramo novo do prompt (sufixo do dono).
    return prompt_version_with_sides(version, any(has_sides(s) for s in payload.get("sets") or []))


class ExerciseSummaryError(OpenAIGenerationError):
    pass


def _format_sets(sets: list[dict[str, Any]], technique_names: Optional[Mapping[str, str]] = None) -> str:
    if not sets:
        return "(nenhuma série registrada)"
    lines = []
    for item in sets:
        # Marca explicitamente series que nao sao de trabalho (aquecimento,
        # ajuste, aplicacoes de tecnica) para a IA nao trata-las como tal, com
        # rotulo legivel (M95b: nome do catalogo, nunca o codigo cru).
        display = set_display_label(item, technique_names)
        label = f" [{display}]" if display else ""
        line = f"- série {item.get('set_number')}{label}: {item.get('reps')} reps @ {item.get('weight_kg')}kg"
        if item.get("rpe") is not None:
            line += f", RPE {item.get('rpe')}"
        # M102: série unilateral com lados -> os valores por lado (texto do dono).
        sides = sides_text(item)
        if sides:
            line += f" (por lado: {sides})"
        if item.get("muscle_pain") is not None:
            line += f", dor muscular {item.get('muscle_pain')}/10"
        if item.get("joint_pain"):
            line += ", com dor articular"
        note = user_data_inline(item.get("notes"), USER_NOTES_MAX_CHARS)
        if note:
            line += f" — nota: {note}"
        lines.append(line)
    if any(has_sides(item) for item in sets):
        lines.append(f"Nota: {WORST_SIDE_RULE_TEXT}")
    return "\n".join(lines)


def _format_engine_guidance(decision: Optional[EngineDecision]) -> str:
    """Orientação da próxima vez a partir da decisão PRONTA do motor v2 (M76
    Lote 1c). A IA não infere progressão: a próxima carga/reps é decidida
    pelo motor quando a próxima ficha for gerada."""
    if decision is None:
        return _text(".sem_motor", {})
    return _text(".motor", {"decisao": format_decision(decision)})


def build_prompt(payload: dict[str, Any]) -> str:
    exercise_name = user_data_inline(payload.get("exercise_name"), USER_NAME_MAX_CHARS) or "Exercício"
    reps_target = payload.get("reps_target")
    load_target = payload.get("load_target")
    rpe_target = payload.get("rpe_target")
    target_line = (
        _text(".alvo", {"reps": f"{reps_target or '-'}", "carga": f"{load_target or '-'}", "rpe": f"{rpe_target or '-'}"})
        if (reps_target or load_target or rpe_target)
        else _text(".sem_alvo", {})
    )
    technique_names = payload.get("technique_names")
    sets_text = _format_sets(payload.get("sets") or [], technique_names)
    pain_line = ""
    notes = user_data_inline(payload.get("notes"), USER_NOTES_MAX_CHARS)
    if payload.get("muscle_pain") is not None or payload.get("joint_pain") is not None or notes:
        pain_line = _text(
            ".dor",
            {
                "dor_muscular": f"{payload.get('muscle_pain')}",
                "dor_articular": "sim" if payload.get("joint_pain") else "não",
            },
        )
        if notes:
            pain_line += _text(".anotacao", {"anotacao": notes})

    engine_text = _format_engine_guidance(payload.get("engine_decision"))
    # M97 F5: "" para hipertrofia fora do deload (prompt de sempre).
    goal_text = prompt_section(
        payload.get("training_goal"), bool(payload.get("deload")), technique_names=technique_names
    )

    prompt = _text(
        "",
        {
            "foco": focus_text(payload.get("training_goal")),
            "regra_dados_usuario": user_data_rule(),
            "exercicio": exercise_name,
            "linha_alvo": target_line,
            "series": sets_text,
            "linha_dor": pain_line,
            "orientacao_motor": engine_text,
            "bloco_objetivo": goal_text,
        },
    )
    return prompt.strip()


def call_openai(prompt: str, user_id: Any = None, prompt_version: str = PROMPT_VERSION) -> str:
    """Wrapper fino sobre `openai_client.call_openai` (cliente unico: chave lida
    a cada chamada, retry so em 429/5xx, evento `llm.usage`). Reexporta toda
    falha como `ExerciseSummaryError`, subclasse de `OpenAIGenerationError`."""
    try:
        return _call_openai_shared(
            prompt,
            model=OPENAI_MODEL,
            timeout=LLM_TIMEOUT_LIVE_SECONDS,
            feature="exercise_summary",
            user_id=user_id,
            prompt_version=prompt_version,
        )
    except OpenAIGenerationError as exc:
        raise ExerciseSummaryError(str(exc)) from exc


def summarize_exercise(payload: dict[str, Any], user_id: Any = None) -> dict[str, Any]:
    """Gera o resumo de um exercício concluído via OpenAI. Não persiste nada —
    o resultado é consumido diretamente pelo frontend (/modo-treino) e
    descartado ao fim da sessão."""
    if not payload.get("exercise_name"):
        raise ExerciseSummaryError("exercise_name é obrigatório.")

    prompt = build_prompt(payload)
    summary = call_openai(prompt, user_id, effective_prompt_version(payload)).strip()

    return {"model": OPENAI_MODEL, "summary": summary}

"""Avaliação de uma série de treino via OpenAI, usada pela tela mobile
/modo-treino logo após o usuário salvar uma série (rodando em paralelo com o
início do RestTimer, sem bloquear o fluxo do treino).

Segue EXATAMENTE o mesmo padrão arquitetural de app.insights_ai/
app.cardio_agent_ai: chamada direta à OpenAI via urllib (POST
https://api.openai.com/v1/responses), env vars próprias, exceção própria, sem
nenhuma dependência de SDK externo.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from .domain.effective_intensity import SetIntensityReading
from .domain.intensity_status_labels import intensity_status_label, reps_position_label, safety_event_label
from .domain.next_set_load import (
    DEFAULT_RULESET,
    RULESET_SOURCE_DEFAULT,
    NextSetLoad,
    RuleSet,
    describe_matched_rule,
    describe_pain_rule,
    describe_ruleset,
)
from .domain.progression_engine import DECREASE_LOAD, HOLD, INCREASE_LOAD, SAFETY_ALERT_EVENTS
from .domain.set_evaluation_categories import (  # noqa: F401 - SET_EVALUATION_CATEGORIES reexportado
    SET_EVALUATION_CATEGORIES,
    SET_EVALUATION_CATEGORY_LABELS,
    category_for_load,
    category_label,
    category_rule_text,
)
from .domain.set_sides import WORST_SIDE_RULE_TEXT, has_sides, prompt_version_with_sides, sides_text
from .domain.training_safety import (
    FAILURE_DENIED_BY_RECOVERY,
    FAILURE_RPE,
    IntensityComposition,
    clamp_ai_suggestion,
)
from .engine_decision_context import PairEngineContext, bound_ai_load, format_decision, format_kg
from .goal_prompt_context import focus_text, prompt_section
from .goal_prompt_context import prompt_version as goal_prompt_version
from .intelligence.muscle_recovery_service import MUSCLE_PAIN_BLOCK_ABOVE, muscle_pain_blocks_direct_work
from .rag_storage import ensure_rag_local
from .models import SET_EVAL_EXERCISE_NAME_MAX_CHARS, SET_EVAL_NOTES_MAX_CHARS
from . import prompt_store
from .openai_client import LLM_TIMEOUT_LIVE_SECONDS, OpenAIGenerationError, call_openai_structured
from .prompt_user_data import sanitize_user_text, user_data_block, user_data_inline, user_data_rule
from .set_types import WORKING, is_preparatory_set, normalize_set_type

RAG_MARKDOWN_DIR = Path(os.environ.get("RAG_MARKDOWN_DIR", "/app/rag"))

OPENAI_MODEL = os.environ.get("OPENAI_SET_EVAL_MODEL", "gpt-5.4-mini")

# Versao do prompt (IA-11). So vai para o evento `llm.usage`, nunca para o
# texto do prompt. Suba SEMPRE que o texto renderizado mudar de proposito
# (snapshot: tests/unit/test_llm_prompt_renderizado_snapshot.py; docs/ia/EVALS.md).
# v2 (IA-09/IA-10): saida ESTRUTURADA (`text.format` json_schema strict,
# `SET_EVALUATION_SCHEMA`) no lugar do formato imposto por texto + regex; a
# anotacao do usuario vai dentro de `<dados_do_usuario>` como DADO.
# v3 (IA-10, prompt injection): o nome do exercicio tambem vai delimitado e a
# regra geral `user_data_rule()` entra junto do papel (antes do RAG: prefixo
# estatico, o cache continua valendo).
# v4 (RPE 10 Lote 6/P9, "uma voz no Modo Treino"): as categorias passam a ser
# acoes do MOTOR (`domain/set_evaluation_categories.py`: HOLD/INCREASE_LOAD/
# DECREASE_LOAD, rotulos de `DECISION_LABELS`) e o prompt recebe o STATUS DA
# SERIE calculado pelo motor (`progression_engine.set_intensity_status`), com a
# instrucao de explicar em cima dele, com as mesmas palavras, sem contradizer.
# v5 (2026-10-12, regressao do eval da v4 no caso de dor muscular 9/10): o
# status do motor e de ESFORCO ("Esforço atingido", rotulo do dono) e vem ao
# lado da posicao das reps contra a faixa (`progression_engine.
# set_reps_position`); regra de dor muscular alta com o limiar do motor de
# recuperacao (`muscle_pain_blocks_direct_work`/`MUSCLE_PAIN_BLOCK_ABOVE`) ->
# DECREASE_LOAD ou encerrar; instrucao de nao copiar a linha de status; a regra
# de direcao da categoria (`set_evaluation_categories.category_rule_text`, a de
# `category_for_load`) entra no prompt para nota, categoria e carga concordarem.
# v6 (2026-10-12, regra do usuario para a carga da PROXIMA serie na sessao): o
# prompt recebe a sugestao PRONTA do motor (`domain/next_set_load.py`: direcao,
# carga no passo e motivo, limiares interpolados das constantes do dono) e
# explica em cima dela; a carga final da resposta e a da regra
# (`engine_decision_context.bound_ai_load`). A regra critica de RPE deixa de
# dizer que RPE 10 nunca reduz: RPE de falha antes do minimo de reps reduz.
# v7 (2026-10-12, rampa planejada como excecao): com rampa de RPE na ficha
# (`next_set_load.planned_ramp_rpe`), a linha da regra diz que o limiar de RPE
# da condicao de 12+ reps e o menor entre o limiar e o RPE planejado da serie
# (numero interpolado da sugestao do motor).
# v8 (2026-10-20, Feature R/R3, regras de carga cadastraveis): a secao da
# proxima serie deixa de escrever a regra a partir das constantes
# `NEXT_SET_*` e cita a regra QUE CASOU (`NextSetLoad.rule_text`, de
# `next_set_load.describe_rule`) mais o conjunto vigente inteiro, na ordem
# (`describe_ruleset`, com a versao `RuleSet.version`; 0 = padrao em codigo).
# O conjunto e o publicado lido em `dispatch_evaluate_set` (padrao sem
# publicado). Dor (limiar do motor de recuperacao) e rampa continuam.
# M97 F5: objetivo nao-default do exercicio em andamento
# (`PairEngineContext.training_goal`, do snapshot congelado) muda o foco da
# persona e acrescenta o bloco de regras do objetivo; sessao de deload
# (`PairEngineContext.deload`) acrescenta o bloco do deload com a prescricao
# pronta. So nos ramos novos, versionados por sufixo (`effective_prompt_version`).
PROMPT_VERSION = "set-eval-v8"

# M103 P4: a PROSA do prompt mora no repositorio privado,
# `api/privado/prompts/set_evaluation/<PROMPT_VERSION>[.parte].md`
# (`prompt_store`); aqui ficam os ramos, a ordem e os dados. Os numeros entram
# por placeholder, lidos do dono (limiar de dor, RPE de falha, teto composto).
PROMPT_FEATURE_DIR = "set_evaluation"


def engine_goal(engine: Optional[PairEngineContext]) -> Any:
    return engine.training_goal if engine is not None else None


def engine_is_deload(engine: Optional[PairEngineContext]) -> bool:
    return engine is not None and engine.deload is not None


def effective_prompt_version(engine: Optional[PairEngineContext] = None) -> str:
    """`PROMPT_VERSION` com o sufixo dos ramos novos (objetivo/deload)."""
    return goal_prompt_version(PROMPT_VERSION, engine_goal(engine), engine_is_deload(engine))

# `SET_EVALUATION_CATEGORIES` vem do dono (`domain/set_evaluation_categories.py`)
# e e reexportado daqui: o enum do schema, o parsing e o eval leem dele.


# Schema da resposta (IA-09). `strict: true` exige todas as chaves em
# `required` e `additionalProperties: false`; "sem sugestao" e `null`. Os
# numeros NAO sao limitados aqui: o servidor aplica o teto de RPE
# (`apply_safety_clamp`) e o passo/direcao do motor (`apply_engine_load_bound`)
# depois, como antes.
SET_EVALUATION_SCHEMA_NAME = "set_evaluation"
SET_EVALUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["category", "note", "next"],
    "properties": {
        "category": {"type": "string", "enum": list(SET_EVALUATION_CATEGORIES)},
        "note": {"type": "string"},
        "next": {
            "type": "object",
            "additionalProperties": False,
            "required": ["reps", "load_kg", "rpe"],
            "properties": {
                "reps": {"type": ["number", "null"]},
                "load_kg": {"type": ["number", "null"]},
                "rpe": {"type": ["number", "null"]},
            },
        },
    },
}


class SetEvaluationError(OpenAIGenerationError):
    pass


def load_progressao_rag_text() -> str:
    """Lê apenas workspace/rag/markdown/PROGRESSAO.md (regra universal de
    progressão de carga) — diferente de workout_plan_ai.load_rag_text() (dump
    completo dos 6+ arquivos), aqui basta a regra de progressão para avaliar
    UMA série já executada, sem custo de contexto desnecessário."""
    ensure_rag_local()
    path = RAG_MARKDOWN_DIR / "PROGRESSAO.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _text(part: str, values: Optional[Mapping[str, str]] = None) -> str:
    """Prosa do template `<PROMPT_VERSION><part>` do repositório privado (M103 P4)."""
    return prompt_store.render(PROMPT_FEATURE_DIR, f"{PROMPT_VERSION}{part}", values or {})


def _format_previous_sets(previous_sets: Optional[list[dict[str, Any]]]) -> str:
    # Aquecimento/serie de ajuste nao sao contexto de fadiga das series de
    # trabalho: ficam fora. Aplicacoes de tecnica sao mantidas, mas marcadas
    # explicitamente. Payload antigo sem set_type = working.
    relevant = [item for item in (previous_sets or []) if not is_preparatory_set(item)]
    if not relevant:
        return _text(".sem_series_anteriores")
    lines = []
    for item in relevant:
        set_type = normalize_set_type(item.get("set_type"))
        label = "" if set_type == WORKING else f" [{set_type}]"
        lines.append(
            f"- série {item.get('set_number')}{label}: {item.get('reps')} reps @ "
            f"{item.get('weight_kg')}kg, RPE {item.get('rpe')}"
        )
    return "\n".join(lines)


def _format_subjective_signals(payload: dict[str, Any]) -> str:
    """Formata dor muscular/articular e anotação livre da série (campos
    opcionais, capturados por série na tela /modo-treino) para o prompt.
    Retorna um texto neutro quando nada foi informado, em vez de omitir a
    seção, para deixar explícito à IA que não há sinal de dor reportado."""
    muscle_pain = payload.get("muscle_pain")
    joint_pain = payload.get("joint_pain")
    notes_text = sanitize_user_text(payload.get("notes"), SET_EVAL_NOTES_MAX_CHARS)

    if muscle_pain is None and joint_pain is None and not notes_text:
        return _text(".sem_sinais")

    lines = []
    if muscle_pain is not None:
        lines.append(f"- Dor muscular reportada: {muscle_pain}/10")
    if joint_pain is not None:
        lines.append(f"- Dor articular: {'sim' if joint_pain else 'não'}")
    if notes_text:
        lines.append(_text(".anotacao", {"anotacao": user_data_block(notes_text)}))
    return "\n".join(lines)


def _format_engine_section(engine: Optional[PairEngineContext], next_set: Optional[NextSetLoad] = None) -> str:
    """Decisão do motor de progressão v2 para o par, PRONTA (M76 Lote 1c).

    A IA não calcula progressão nem incremento: recebe a ação e a carga alvo
    do motor e o passo do equipamento. O servidor ainda arredonda a carga da
    resposta ao passo e a limita pela direção do motor (`apply_engine_load_bound`).
    v6: com a regra da próxima série (`next_set`), a carga da próxima série é
    a dela; a decisão do motor fica como contexto da SESSÃO."""
    step_line = "\n" + _text(".passo", {"passo": format_kg(engine.step_kg)}) if engine else ""
    decision = engine.decision if engine else None
    if next_set is not None:
        decision_line = (
            f"\n- {format_decision(decision)}" if decision is not None else " " + _text(".nenhuma_decisao")
        )
        return _text(".motor_sessao", {"linha_decisao": decision_line, "linha_passo": step_line})
    if decision is None:
        return _text(".motor_sem_decisao", {"nenhuma_decisao": _text(".nenhuma_decisao"), "linha_passo": step_line})
    limit = (
        _text(".motor_limite", {"carga_alvo": format_kg(decision.load_kg)})
        if decision.load_kg is not None
        else _text(".motor_sem_limite")
    )
    return _text(".motor", {"decisao": format_decision(decision), "linha_passo": step_line, "limite": limit})


def _format_number(value: float) -> str:
    """`9.0` -> `9`, `8.5` -> `8,5` (mesma grafia do resto do prompt)."""
    text = f"{float(value):g}"
    return text.replace(".", ",")


def _category_text(code: str) -> str:
    """`HOLD ("Manter")`: o código do schema com o rótulo do motor."""
    return f'{code} ("{SET_EVALUATION_CATEGORY_LABELS[code]}")'


def _sides_suffix(payload: Mapping[str, Any]) -> str:
    """M102: lados da série unilateral e a regra do pior lado (texto do dono);
    vazio sem lados (prompt igual ao de antes)."""
    sides = sides_text(payload)
    return f" (por lado: {sides}). {WORST_SIDE_RULE_TEXT}" if sides else ""


def _format_reps_line(reading: Optional[SetIntensityReading]) -> Optional[str]:
    """Reps da série contra a faixa PELO MOTOR (`progression_engine.
    set_reps_position`, rótulo do dono). Sem faixa: `None` (linha omitida)."""
    label = reps_position_label(reading.reps_position) if reading is not None else None
    if label is None:
        return None
    if reading.reps_min == reading.reps_max:
        faixa = f"alvo de {reading.reps_max} reps"
    else:
        faixa = f"faixa de {reading.reps_min} a {reading.reps_max} reps"
    return f"- Repetições: {label} ({reading.reps} reps executadas; {faixa})."


def _format_set_status(reading: Optional[SetIntensityReading]) -> str:
    """Status da série PELO MOTOR: o de ESFORÇO (`progression_engine.
    set_intensity_status`, rótulo do dono `intensity_status_labels`) e, ao
    lado, as reps contra a faixa (`set_reps_position`) - números da própria
    leitura, nenhum escrito à mão. A IA explica em cima deles, com palavras
    próprias (v5: sem copiar a linha)."""
    label = intensity_status_label(reading.status) if reading is not None else None
    reps_line = _format_reps_line(reading)
    if label is None:
        lines = [_text(".status_sem_alvo")]
        if reps_line:
            lines.append(reps_line)
        return "\n".join(lines)
    numbers = []
    if reading.rpe is not None:
        numbers.append(f"RPE executado {_format_number(reading.rpe)}")
    if reading.target is not None:
        numbers.append(f"alvo efetivo do dia RPE {reading.target}")
    if reading.ceiling is not None:
        failure = "falha permitida" if reading.failure_allowed else "falha não permitida hoje"
        numbers.append(f"teto RPE {reading.ceiling}, {failure}")
    detail = f" ({'; '.join(numbers)})" if numbers else ""
    lines = [_text(".status", {"rotulo": label, "detalhe": detail})]
    if reps_line:
        lines.append(reps_line)
    event = safety_event_label(reading.safety_event)
    if event:
        kind = _text(".evento_alerta" if reading.safety_event in SAFETY_ALERT_EVENTS else ".evento_informativo")
        lines.append(_text(".evento", {"evento": event, "tipo": kind}))
    lines.append(_text(".status_explicar", {"rotulo": label}))
    return "\n".join(lines)


def _format_next_set_section(next_set: Optional[NextSetLoad], ruleset: Optional[RuleSet] = None) -> Optional[str]:
    """Carga da próxima série PRONTA pelo motor (dono `domain/next_set_load.py`):
    direção, carga no passo e motivo, a regra que casou (`rule_text`) e o
    conjunto vigente na ordem (`describe_ruleset`, v8). Nenhum limiar escrito
    aqui: tudo vem do dono. Sem regra aplicável: `None` (a seção não entra)."""
    if next_set is None:
        return None
    ruleset = ruleset or DEFAULT_RULESET
    if next_set.load_kg is not None:
        target = (
            f"{format_kg(next_set.load_kg)} (executado {format_kg(next_set.executed_kg)}; "
            f"passo {format_kg(next_set.step_kg)})"
        )
        use = _text(".proxima_serie_uso_carga")
    else:
        target = _text(".proxima_serie_sem_passo", {"executado": format_kg(next_set.executed_kg)})
        use = _text(".proxima_serie_uso_sem_carga")
    matched_text = describe_matched_rule(next_set)
    matched = f"- {matched_text}.\n" if matched_text else ""
    ramp = (
        _text(".rampa", {"rpe_planejado": _format_number(next_set.planned_rpe)}) + "\n"
        if next_set.planned_rpe is not None
        else ""
    )
    version = f"v{ruleset.version}"
    if ruleset.source == RULESET_SOURCE_DEFAULT:
        version = f"{version}, conjunto padrão"
    rules = "\n".join(f"  {line}" for line in describe_ruleset(ruleset).splitlines())
    return _text(
        ".proxima_serie",
        {
            "direcao": _category_text(next_set.direction),
            "alvo": target,
            "motivo": next_set.reason_label,
            "regra_casou": matched,
            "regra_dor": describe_pain_rule(),
            "versao": version,
            "regras": rules,
            "rampa": ramp,
            "uso": use,
        },
    )


def _format_muscle_pain_rule(payload: dict[str, Any], engine: Optional[PairEngineContext]) -> Optional[str]:
    """Regra de dor muscular ALTA (v5). O limiar é o do motor de recuperação
    (`muscle_pain_blocks_direct_work`: acima de `MUSCLE_PAIN_BLOCK_ABOVE` o
    grupo vai para NO_DIRECT_WORK) - interpolado, nunca escrito à mão. Abaixo
    do limiar: `None` (a seção não entra)."""
    muscle_pain = payload.get("muscle_pain")
    if not muscle_pain_blocks_direct_work(muscle_pain):
        return None
    return _text(
        ".dor_alta",
        {
            "dor": _format_number(muscle_pain),
            "limiar_dor": f"{MUSCLE_PAIN_BLOCK_ABOVE}",
            "reduzir": _category_text(DECREASE_LOAD),
            "passo": f" ({format_kg(engine.step_kg)})" if engine else "",
        },
    )


def _format_intensity_rule(
    payload: dict[str, Any], composition: Optional[IntensityComposition], next_set: Optional[NextSetLoad] = None
) -> str:
    """Regra crítica de RPE com o teto JÁ COMPOSTO do par (`training_safety`)
    e o RPE de falha da fonte — nenhum número escrito à mão."""
    if composition is not None and composition.failure_denied_by == FAILURE_DENIED_BY_RECOVERY:
        # Lote 6/P9: a falha foi negada so pela recuperacao do dia (o par a
        # permite) - mesma origem que o status/evento da serie dizem.
        last_set = _text(".ultima_serie_recuperacao", {"teto": f"{composition.ceiling}"})
    elif composition is not None:
        failure = _text(".falha_permitida" if composition.failure_allowed else ".falha_negada_par")
        last_set = _text(".ultima_serie_par", {"teto": f"{composition.ceiling}", "falha": failure})
    else:
        last_set = _text(".ultima_serie_sem_composicao")
    if next_set is not None:
        # v6: a categoria da próxima série é a da regra de carga do motor.
        return _text(".regra_intensidade_proxima_serie", {"ultima_serie": last_set, "rpe_falha": f"{FAILURE_RPE}"})
    return _text(
        ".regra_intensidade",
        {
            "ultima_serie": last_set,
            "serie": f"{payload.get('set_number')}",
            "total_series": f"{payload.get('sets_count')}",
            "limiar_dor": f"{MUSCLE_PAIN_BLOCK_ABOVE}",
            "manter": _category_text(HOLD),
            "reduzir": _category_text(DECREASE_LOAD),
            "rpe_falha": f"{FAILURE_RPE}",
        },
    )


def build_prompt(
    payload: dict[str, Any],
    rag_text: str,
    *,
    engine: Optional[PairEngineContext] = None,
    composition: Optional[IntensityComposition] = None,
    set_reading: Optional[SetIntensityReading] = None,
    next_set: Optional[NextSetLoad] = None,
    ruleset: Optional[RuleSet] = None,
    technique_names: Optional[Mapping[str, str]] = None,
) -> str:
    categories = ", ".join(_category_text(code) for code in SET_EVALUATION_CATEGORIES)
    engine_text = _format_engine_section(engine, next_set)
    next_set_text = _format_next_set_section(next_set, ruleset)
    if next_set_text:
        engine_text = f"{engine_text}\n\n{next_set_text}"
    intensity_rule = _format_intensity_rule(payload, composition, next_set)
    pain_rule = _format_muscle_pain_rule(payload, engine)
    if pain_rule:
        intensity_rule = f"{intensity_rule}\n\n{pain_rule}"
    executed = payload.get("weight_kg")
    category_rule = category_rule_text(format_kg(executed) if executed is not None else "carga executada")
    category_scope = (
        _text(".escopo_proxima_serie")
        if next_set is not None
        else _text(".escopo_motor", {"aumentar": _category_text(INCREASE_LOAD)})
    )
    # M97 F5: "" para hipertrofia fora do deload (prompt de sempre).
    goal_text = prompt_section(
        engine_goal(engine),
        engine_is_deload(engine),
        deload_prescription=engine.deload if engine is not None else None,
        technique_names=technique_names,
    )

    prompt = _text(
        "",
        {
            "foco": focus_text(engine_goal(engine)),
            "regra_dados_usuario": user_data_rule(),
            "rag": rag_text,
            "exercicio": user_data_inline(payload.get("exercise_name"), SET_EVAL_EXERCISE_NAME_MAX_CHARS),
            "serie": f"{payload.get('set_number')}",
            "total_series": f"{payload.get('sets_count')}",
            "reps_alvo": f"{payload.get('reps_target')}",
            "carga_alvo": f"{payload.get('load_target')}",
            "rpe_alvo": f"{payload.get('rpe_target')}",
            "reps": f"{payload.get('reps')}",
            "carga": f"{payload.get('weight_kg')}",
            "rpe": f"{payload.get('rpe')}",
            "lados": _sides_suffix(payload),
            "status": _format_set_status(set_reading),
            "motor": engine_text,
            "bloco_objetivo": goal_text,
            "series_anteriores": _format_previous_sets(payload.get("previous_sets_this_session")),
            "sinais": _format_subjective_signals(payload),
            "regra_intensidade": intensity_rule,
            "categorias": categories,
            "escopo_categoria": category_scope,
            "regra_categoria": category_rule,
        },
    )
    return prompt.strip()


def call_openai(prompt: str, user_id: Any = None, prompt_version: str = PROMPT_VERSION) -> dict[str, Any]:
    """Chamada ESTRUTURADA (IA-09) via `openai_client.call_openai_structured`
    (cliente unico: chave lida a cada chamada, retry so em 429/5xx, evento
    `llm.usage`). Devolve o objeto do schema. Reexporta toda falha (HTTP,
    recusa, JSON invalido) como `SetEvaluationError`, subclasse de
    `OpenAIGenerationError`."""
    try:
        result = call_openai_structured(
            prompt,
            OPENAI_MODEL,
            SET_EVALUATION_SCHEMA,
            schema_name=SET_EVALUATION_SCHEMA_NAME,
            timeout=LLM_TIMEOUT_LIVE_SECONDS,
            feature="set_evaluation",
            user_id=user_id,
            prompt_version=prompt_version,
        )
    except OpenAIGenerationError as exc:
        raise SetEvaluationError(str(exc)) from exc
    return result.data


def _coerce_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_structured_evaluation(data: Any) -> dict[str, Any]:
    """Valida o objeto do schema (`SET_EVALUATION_SCHEMA`).

    Com `strict: true` a OpenAI ja garante o formato; a checagem aqui e a
    defesa contra uma resposta fora do contrato, e FALHA de forma visivel
    (`SetEvaluationError` -> 503) em vez do antigo fallback silencioso para
    MANTER, que fazia uma categoria errada parecer uma avaliacao."""
    if not isinstance(data, dict):
        raise SetEvaluationError("Avaliação da série fora do formato esperado.")
    category = data.get("category")
    if category not in SET_EVALUATION_CATEGORIES:
        raise SetEvaluationError(f"Categoria de avaliação desconhecida: {category!r}.")
    nxt = data.get("next") if isinstance(data.get("next"), dict) else {}
    return {
        "category": category,
        "note": str(data.get("note") or "").strip(),
        "reps": _coerce_number(nxt.get("reps")),
        "load_kg": _coerce_number(nxt.get("load_kg")),
        "rpe": _coerce_number(nxt.get("rpe")),
    }


def apply_safety_clamp(result: dict[str, Any], composition: IntensityComposition) -> dict[str, Any]:
    """Limita a sugestao numerica da IA ao teto seguro JA COMPOSTO (PR-14,
    secao 5.7 e furo G6).

    Fonte unica da regra: `training_safety.clamp_ai_suggestion` - aqui so
    entra/sai o formato da resposta deste endpoint. So o RPE e limitado;
    reps/carga passam como vieram. O corte vai em `clamps[]` para a UI poder
    explicar por que o numero mudou, e `effective_rpe_ceiling`/
    `failure_allowed` dizem qual limite valeu.

    O texto livre da IA (`note`/`raw_text`) NAO e reescrito: ele e orientacao,
    nao alvo aplicavel - o que o app aplica e a sugestao numerica, e ela sai
    daqui sempre dentro do teto.
    """
    suggestion = {
        "reps": result.get("suggested_next_reps"),
        "load_kg": result.get("suggested_next_load_kg"),
        "rpe": result.get("suggested_next_rpe"),
    }
    clamped, clamps = clamp_ai_suggestion(suggestion, composition)
    out = dict(result)
    out["suggested_next_rpe"] = clamped.get("rpe")
    out["clamps"] = [clamp.to_dict() for clamp in clamps]
    out["effective_rpe_ceiling"] = composition.ceiling
    out["failure_allowed"] = composition.failure_allowed
    return out


def apply_engine_load_bound(
    result: dict[str, Any],
    engine: Optional[PairEngineContext],
    *,
    executed_kg: Any = None,
    next_set: Optional[NextSetLoad] = None,
) -> dict[str, Any]:
    """Carga da próxima série no passo do equipamento e na direção do motor
    (M76 Lote 1c). Fonte da regra: `engine_decision_context.bound_ai_load`;
    aqui só entra/sai o formato da resposta. O ajuste vai em
    `load_adjustments[]` — NÃO em `clamps[]`, que a UI mostra como corte de
    RPE por segurança.

    v6: com a regra da próxima série (`next_set`, dono `domain/next_set_load.py`)
    a carga final É a da regra e a categoria é a direção dela; a sugestão do
    motor vai pronta em `next_set_load`."""
    load, adjustments = bound_ai_load(
        result.get("suggested_next_load_kg"), engine, executed_kg=executed_kg, rule=next_set
    )
    out = dict(result)
    out["suggested_next_load_kg"] = load
    out["load_adjustments"] = adjustments
    out["load_step_kg"] = float(engine.step_kg) if engine else None
    out["engine_decision"] = engine.decision.decision if engine and engine.decision else None
    # Lote 6/P9: a categoria (acao do motor) segue a direcao da carga FINAL
    # (`set_evaluation_categories.category_for_load`); a troca fica registrada.
    out["next_set_load"] = next_set.to_dict() if next_set is not None else None
    category = result.get("category")
    if category in SET_EVALUATION_CATEGORIES:
        coherent = next_set.direction if next_set is not None else category_for_load(category, load, executed_kg)
        if coherent != category:
            out["category_from_ai"] = category
            out["category"] = coherent
        out["category_label"] = category_label(out["category"])
    return out


def evaluate_set(
    payload: dict[str, Any],
    *,
    engine: Optional[PairEngineContext] = None,
    composition: Optional[IntensityComposition] = None,
    set_reading: Optional[SetIntensityReading] = None,
    next_set: Optional[NextSetLoad] = None,
    ruleset: Optional[RuleSet] = None,
    user_id: Any = None,
    technique_names: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Gera a avaliação de uma série via OpenAI, injetando a regra de
    progressão do RAG local (PROGRESSAO.md), a decisão pronta do motor para o
    par (`engine`) e o teto já composto (`composition`) no prompt. Não
    persiste nada — o resultado é consumido diretamente pelo frontend
    (/modo-treino).

    `technique_names` (M95c): `{code: nome}` do catalogo `advanced_techniques`
    para o bloco do objetivo citar as tecnicas vetadas pelo nome."""
    if not payload.get("exercise_name"):
        raise SetEvaluationError("exercise_name é obrigatório.")

    rag_text = load_progressao_rag_text()
    prompt = build_prompt(
        payload,
        rag_text,
        engine=engine,
        composition=composition,
        set_reading=set_reading,
        next_set=next_set,
        ruleset=ruleset,
        technique_names=technique_names,
    )
    # M102: entrada com lados = ramo novo do prompt (sufixo do dono).
    version = prompt_version_with_sides(effective_prompt_version(engine), has_sides(payload))
    data = call_openai(prompt, user_id, version)
    parsed = parse_structured_evaluation(data)

    return {
        "model": OPENAI_MODEL,
        "category": parsed["category"],
        "category_label": category_label(parsed["category"]),
        "note": parsed["note"],
        # Contrato da resposta (`frontend/src/lib/workout-mode-types.ts`): desde
        # a v2 e o JSON estruturado devolvido pelo modelo.
        "raw_text": json.dumps(data, ensure_ascii=False),
        "suggested_next_reps": parsed["reps"],
        "suggested_next_load_kg": parsed["load_kg"],
        "suggested_next_rpe": parsed["rpe"],
        # Lote 6/P9: o status do motor que a IA recebeu (a tela mostra o mesmo).
        "set_intensity_status": set_reading.status if set_reading is not None else None,
        "set_intensity_status_label": intensity_status_label(set_reading.status) if set_reading is not None else None,
    }

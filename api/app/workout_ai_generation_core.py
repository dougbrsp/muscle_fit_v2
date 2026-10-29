"""Logica de geracao das chamadas de IA sincronas do modo treino (`POST
/exercises/evaluate-set`, `POST /exercises/{id}/summarize`,
`POST /activities/{activity_id}/ai-summary/generate`) e da analise de
evolucao de treinos da tela /treinos (`POST /workouts-analysis/generate`),
extraida de `main.py`/`workouts_analysis.py` para ser reutilizavel tanto
pelos endpoints FastAPI (que agora so validam payload, criam o job e
retornam 202) quanto pelo handler da Lambda `musclefit-workout-ai`
(`lambda_workout_ai.py`), que roda esta mesma logica de forma assincrona -
ver docs/aws-migration-plan.md, Fase 3, mesmo padrao 202 + job_id + DynamoDB
ja usado por `plan_generation_core.py`/`cardio_generation_core.py`.

Um `job_type` por chamada (usado tanto como chave de roteamento do handler
quanto como valor persistido em `musclefit-async-jobs.job_type`, para
depuracao/auditoria):
- "workout-ai-evaluate-set"     -> POST /exercises/evaluate-set
- "workout-ai-exercise-summary" -> POST /exercises/{id}/summarize
- "workout-ai-activity-summary" -> POST /activities/{id}/ai-summary/generate
- "workouts_analysis"           -> POST /workouts-analysis/generate (logica em workouts_analysis_core.py)
- "insights"                    -> POST /insights/generate (logica em insights_core.py)
- "nutrition-meal-parse"        -> POST /nutrition/meals/parse (nutricao L5b; logica em
                                   nutrition_meal_parse_worker.py; job type do dono
                                   `domain/nutrition/ai_usage.MEAL_PARSE_JOB_TYPE`)

Mantido o comportamento exato dos 3 endpoints sincronos anteriores (mesmas
excecoes - `HTTPException`, `SetEvaluationError`, `ExerciseSummaryError`,
`WorkoutSummaryError`): como agora ninguem devolve uma resposta HTTP
diretamente a partir destas funcoes (o job assincrono so grava a excecao
serializada como texto no DynamoDB), nao ha necessidade de trocar os tipos
de excecao usados - so `str(exc)` do texto do erro e aproveitado (ver
`lambda_workout_ai.py`, mesmo padrao de `_error_message` de
`lambda_plan_generator.py`/`lambda_cardio_agent.py`)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import HTTPException

from .advanced_techniques import technique_display_names
from .db import transaction
from .domain import effective_intensity
from .domain.deload import is_deload_session
from .domain.next_set_load import (
    DEFAULT_RULESET,
    planned_ramp_rpe,
    ruleset_for_goal,
    ruleset_uses_fatigue,
    ruleset_uses_published,
)
from .domain.prescription import UNKNOWN_PAIR_SAFETY
from .engine_decision_context import (
    PairEngineContext,
    equipment_step_kg,
    evaluation_reps_range,
    load_activity_decisions,
    load_logged_exercise_decision,
    load_pair_context,
    next_set_load_for,
    payload_working_set_index,
    safe_read,
)
from .intelligence.session_fatigue_reader import load_session_prior_fatigue
from .next_set_load_rules import load_published_ruleset_autocommit
from .exercise_set_evaluation_ai import (
    SetEvaluationError,
    apply_engine_load_bound,
    apply_safety_clamp,
    evaluate_set,
)
from .exercise_summary_ai import ExerciseSummaryError, summarize_exercise
from .goal_prompt_context import non_default_goals
from .domain.nutrition.ai_usage import MEAL_PARSE_JOB_TYPE
from .insights_core import JOB_TYPE_INSIGHTS, dispatch_insights
from .observability import EVENT_AI_SET_EVAL_CLAMPED, EVENT_WORKOUT_SUMMARY_REUSED, log_event
from .set_types import WORKING
from .services import activity_detail, exercise_summary_context, save_activity_ai_summary
from .workout_summary_ai import WorkoutSummaryError, build_prompt, generate_workout_summary, input_fingerprint
from .workouts_analysis_core import JOB_TYPE_WORKOUTS_ANALYSIS, dispatch_workouts_analysis

logger = logging.getLogger(__name__)

JOB_TYPE_EVALUATE_SET = "workout-ai-evaluate-set"
JOB_TYPE_EXERCISE_SUMMARY = "workout-ai-exercise-summary"
JOB_TYPE_ACTIVITY_SUMMARY = "workout-ai-activity-summary"
# JOB_TYPE_WORKOUTS_ANALYSIS ("workouts_analysis") reexportado de
# workouts_analysis_core.py, que carrega a analise de evolucao de treinos da
# tela /treinos (POST /workouts-analysis/generate) - migrada para este mesmo
# despacho assincrono na Fase 3, mesmo motivo dos 3 job_types acima.
# JOB_TYPE_INSIGHTS ("insights") reexportado de insights_core.py, que carrega
# o insight livre da tela /insights (POST /insights/generate) - migrada para
# este mesmo despacho assincrono na Fase 3, mesmo motivo dos demais job_types
# acima.


def dispatch_workout_ai_generation(
    job_type: str, payload: dict[str, Any], user_id: int, ai_usage_id: Optional[int] = None
) -> dict[str, Any]:
    """Roteia para a funcao de geracao certa a partir de `job_type` - unico
    ponto de entrada usado por `lambda_workout_ai.handler`. `ai_usage_id` (so
    as features de IA da nutricao): a linha do `ai_usage_log` que o worker fecha."""
    if job_type == JOB_TYPE_EVALUATE_SET:
        return dispatch_evaluate_set(payload, user_id)
    if job_type == JOB_TYPE_EXERCISE_SUMMARY:
        return dispatch_exercise_summary(payload, user_id)
    if job_type == JOB_TYPE_ACTIVITY_SUMMARY:
        return dispatch_activity_summary(payload, user_id)
    if job_type == JOB_TYPE_WORKOUTS_ANALYSIS:
        return dispatch_workouts_analysis(payload, user_id)
    if job_type == JOB_TYPE_INSIGHTS:
        return dispatch_insights(payload, user_id)
    if job_type == MEAL_PARSE_JOB_TYPE:
        # Import tardio: a api-core importa este módulo no boot (`main.py`) e não
        # deve depender da cadeia do worker da nutrição (revisão de impacto, L5b).
        from .nutrition_meal_parse_worker import dispatch_meal_parse

        return dispatch_meal_parse(payload, user_id, ai_usage_id)
    raise HTTPException(status_code=400, detail=f"job_type desconhecido: {job_type!r}")


def _evaluate_set_engine(payload: dict[str, Any], user_id: Any) -> PairEngineContext:
    """Decisao do motor v2 e passo do equipamento do par da serie (M76 Lote
    1c), resolvidos NO SERVIDOR. Falha de leitura -> sem decisao e passo
    default do motor: a IA segue, so sem decisao pronta (e e instruida a nao
    sugerir progressao)."""

    def _read():
        with transaction() as conn:
            return load_pair_context(conn, user_id, payload.get("mapping_id"), payload.get("equipment_id"))

    return safe_read(_read, default=PairEngineContext(step_kg=equipment_step_kg(None)))


def _evaluate_set_intensity(payload: dict[str, Any]) -> effective_intensity.EffectiveIntensity:
    """Teto seguro desta serie, resolvido NO SERVIDOR (PR-14, furo G6).

    O par vem do payload (`mapping_id` + `equipment_id` = `gym_equipment.id`).
    Par ausente/desconhecido ou falha de leitura -> default conservador D3
    (teto 9): o clamp precisa existir sempre, e na duvida ele restringe. O
    teto mandado pelo cliente (`effective_rpe_ceiling`, da prescricao
    congelada, que ja compos a recuperacao projetada) so pode restringir
    ainda mais."""
    try:
        with transaction() as conn:
            safety = effective_intensity.resolve_pair_safety(
                conn, payload.get("mapping_id"), payload.get("equipment_id")
            )
    except Exception:  # noqa: BLE001 - avaliar a serie nunca pode 500 por isso
        logger.exception("Falha ao resolver seguranca do par para a IA de serie")
        safety = None
    intensity = effective_intensity.from_safety(safety or UNKNOWN_PAIR_SAFETY)
    return effective_intensity.restrict_ceiling(intensity, payload.get("effective_rpe_ceiling"))


def _evaluate_set_logged_intensity(engine: PairEngineContext) -> Optional[effective_intensity.EffectiveIntensity]:
    """Intensidade efetiva do exercicio JA LANCADO na sessao em andamento
    (ficha congelada + overlay de hoje). Sem exercicio lancado (treino livre)
    ou falha de leitura: None (vale a intensidade do par desta avaliacao)."""
    if engine.exercise_row_id is None:
        return None

    def _read():
        with transaction() as conn:
            return effective_intensity.resolve_for_logged_exercise(conn, engine.exercise_row_id)

    return safe_read(_read, event="evaluate_set.reading_failed")


def _evaluate_set_reading(
    payload: dict[str, Any],
    engine: PairEngineContext,
    intensity: effective_intensity.EffectiveIntensity,
    logged: Optional[effective_intensity.EffectiveIntensity],
) -> effective_intensity.SetIntensityReading:
    """Status da serie PELO MOTOR para o prompt (RPE 10 Lote 6/P9) - o mesmo
    que o salvamento da serie devolveu ao Modo Treino: a intensidade do
    exercicio lancado (`logged`), senao a do par desta avaliacao (sem alvo ->
    sem status).

    M96: o alvo cobrado e o do degrau da rampa desta serie (`rpe_target` e
    ordem das series do payload, o mesmo `planned_ramp_rpe` da carga da
    proxima serie), para o status e o motivo da carga sairem coerentes."""
    return effective_intensity.set_intensity_reading(
        payload.get("set_type") or WORKING,
        payload.get("rpe"),
        logged or intensity,
        reps=payload.get("reps"),
        reps_range=evaluation_reps_range(engine, payload.get("reps_target")),
        measure=payload.get("measure"),
        planned_rpe=planned_ramp_rpe(payload.get("rpe_target"), payload_working_set_index(payload)),
    )


def _optional_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _evaluate_set_fatigue_band(payload: dict[str, Any], user_id: Any):
    """Faixa de fadiga previa do exercicio da serie (R1): `session_id` +
    `exercise_id` (`exercises.id`) do payload, lidos pelo mesmo leitor do
    salvamento da serie (`session_fatigue_reader.load_session_prior_fatigue`),
    que so enxerga atividades do proprio usuario (`activities.user_id`).
    Ausentes, de outro usuario ou falha de leitura: None (so regras "qualquer
    fadiga" casam)."""
    session_id = _optional_int(payload.get("session_id"))
    exercise_id = _optional_int(payload.get("exercise_id"))
    owner = _optional_int(user_id)
    if session_id is None or exercise_id is None or owner is None:
        return None

    def _read():
        with transaction() as conn:
            return load_session_prior_fatigue(conn, owner, [session_id]).get(exercise_id)

    prior = safe_read(_read, event="evaluate_set.prior_fatigue_failed")
    return prior.band if prior is not None else None


def dispatch_evaluate_set(payload: dict[str, Any], user_id: Any = None) -> dict[str, Any]:
    """Corpo do antigo `evaluate_set_endpoint` sincrono (o payload ja chega
    validado pelo Pydantic `SetEvaluationIn` no endpoint FastAPI) + o clamp de
    seguranca do PR-14.

    A sugestao da LLM passa OBRIGATORIAMENTE por
    `training_safety.clamp_ai_suggestion` aqui, antes de qualquer coisa chegar
    ao cliente (secao 9: "resultado passa por clamp antes de mark_done"), e o
    corte vai em `clamps[]`. Antes deste PR a sugestao era aplicada direto no
    Modo Treino, sem limite nenhum, e podia elevar o alvo de RPE acima do teto
    seguro do par.

    M76 Lote 1c: o teto composto e a decisao do motor v2 para o par sao
    resolvidos ANTES da chamada e entram prontos no prompt; a carga sugerida
    sai no passo do equipamento e nao sobe alem da carga alvo do motor (ou
    da carga recem-executada, se maior) (`apply_engine_load_bound`, ajustes
    em `load_adjustments[]`).

    set-eval-v6: quando a regra da carga da proxima serie se aplica
    (`engine_decision_context.next_set_load_for`), a carga final e a dela.

    R1 (2026-10-20): os fatos do conjunto de regras - ordem da serie entre as
    de trabalho do payload, faixa de fadiga previa (pelo `session_id` +
    `exercise_id` do payload) e teto efetivo da serie - entram prontos.

    R2 (migracao 088): o conjunto e o PUBLICADO (`next_set_load_rules`, uma
    leitura em autocommit; sem linha/falha -> `DEFAULT_RULESET`) e a faixa de
    fadiga so e lida quando alguma regra dele a exige (`ruleset_uses_fatigue`)."""
    intensity = _evaluate_set_intensity(payload)
    composition = effective_intensity.composition_of(intensity)
    engine = _evaluate_set_engine(payload, user_id)
    logged = _evaluate_set_logged_intensity(engine)
    reading = _evaluate_set_reading(payload, engine, intensity, logged)
    # Regra da carga da proxima serie (2026-10-12, dono `domain/next_set_load.py`):
    # pronta ANTES da IA, entra no prompt e decide a carga final.
    # M97 F2: conjunto do OBJETIVO do exercicio em andamento (hipertrofia = o
    # publicado, como sempre; forca = o de forca, dono `next_set_load`).
    published = (
        safe_read(
            load_published_ruleset_autocommit, default=DEFAULT_RULESET, event="evaluate_set.next_set_ruleset_failed"
        )
        if ruleset_uses_published(engine.training_goal)
        else None
    )
    ruleset = ruleset_for_goal(engine.training_goal, published)
    next_set = next_set_load_for(
        payload,
        engine,
        working_set_index=payload_working_set_index(payload) + 1,
        fatigue_band=_evaluate_set_fatigue_band(payload, user_id) if ruleset_uses_fatigue(ruleset) else None,
        rpe_ceiling=(logged or intensity).ceiling,
        ruleset=ruleset,
        intensity_status=reading.status,
    )
    try:
        result = evaluate_set(
            payload,
            engine=engine,
            composition=composition,
            set_reading=reading,
            next_set=next_set,
            ruleset=ruleset,
            user_id=user_id,
            # M95c: nomes do catalogo so quando o bloco do objetivo aparece
            # (objetivo nao-default); hipertrofia nao paga a leitura.
            technique_names=technique_display_names() if non_default_goals(engine.training_goal) else None,
        )
    except SetEvaluationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    clamped = apply_engine_load_bound(
        apply_safety_clamp(result, composition), engine, executed_kg=payload.get("weight_kg"), next_set=next_set
    )
    for clamp in clamped["clamps"]:
        log_event(
            EVENT_AI_SET_EVAL_CLAMPED,
            field=clamp.get("field"),
            from_value=clamp.get("from"),
            to_value=clamp.get("to"),
            reason_code=clamp.get("reason"),
            effective_rpe_ceiling=intensity.ceiling,
            model=result.get("model"),
        )
    return clamped


def dispatch_exercise_summary(payload: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Corpo exato do antigo `summarize_exercise_endpoint` sincrono - so
    parametrizado por `user_id` em vez de `Identity` do FastAPI. A leitura de
    `exercise_summary_context` (antes feita no endpoint, antes de criar o
    job) migrou para ca, mesmo padrao de `cardio_generation_core.
    dispatch_cardio_generation` (que le `workout_plans` dentro do dispatch,
    nao no endpoint sincrono)."""
    exercise_id = payload.get("exercise_id")
    if exercise_id is None:
        raise HTTPException(status_code=400, detail="exercise_id é obrigatório.")

    context = exercise_summary_context(exercise_id, user_id=user_id)
    if not context:
        raise HTTPException(status_code=404, detail="Exercício não encontrado.")

    def _read_decision():
        with transaction() as conn:
            return load_logged_exercise_decision(conn, user_id, exercise_id)

    ai_payload = {
        "engine_decision": safe_read(_read_decision),
        "exercise_name": context["original_name"],
        "reps_target": context.get("reps_target"),
        "load_target": context.get("load_target"),
        "rpe_target": context.get("rpe_target"),
        "sets": context.get("sets") or [],
        "muscle_pain": context.get("muscle_pain"),
        "joint_pain": context.get("joint_pain"),
        "notes": context.get("notes"),
        # M97 F5: objetivo carimbado na linha e deload da sessao (prontos).
        "training_goal": context.get("training_goal"),
        "deload": is_deload_session(context.get("deload_source")),
        # M95b/M95c: nome legivel das tecnicas (catalogo, cache do processo)
        # para as series e o bloco do objetivo; falha -> rotulo generico.
        "technique_names": technique_display_names(),
    }

    try:
        return summarize_exercise(ai_payload, user_id=user_id)
    except ExerciseSummaryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def dispatch_activity_summary(payload: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Corpo exato do antigo `generate_activity_ai_summary_endpoint`
    sincrono - so parametrizado por `user_id` em vez de `Identity` do
    FastAPI. Gera E JA PERSISTE (`save_activity_ai_summary`) numa chamada
    so, mesmo padrao de `cardio_generation_core.dispatch_cardio_generation`
    (gera + `_insert_recommendation` no mesmo dispatch).

    Dedupe (IA-14): se a atividade ja tem resumo e a impressao digital da
    entrada (`input_fingerprint` do prompt que SERIA enviado agora) e igual a
    gravada com ele, devolve o resumo salvo SEM chamar a OpenAI - reentrega
    da Lambda ou pedido repetido nao pagam de novo. Treino editado, decisao
    do motor diferente, prompt ou modelo novos mudam a impressao e geram de
    novo. `payload["force"]` verdadeiro sempre gera (regerar explicito)."""
    activity_id = payload.get("activity_id")
    if activity_id is None:
        raise HTTPException(status_code=400, detail="activity_id é obrigatório.")

    detail = activity_detail(activity_id, user_id=user_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Atividade não encontrada.")

    def _read_decisions():
        with transaction() as conn:
            return load_activity_decisions(conn, user_id, activity_id)

    decisions = safe_read(_read_decisions, default={"exercises": {}, "groups": []})
    # M95: nome legivel das tecnicas (catalogo `advanced_techniques`, cache do
    # processo). Falha de leitura -> rotulo generico, nunca o codigo cru.
    technique_names = technique_display_names()

    saved_summary = detail.get("ai_summary")
    saved_fingerprint = detail.get("ai_summary_input_sha256")
    if not payload.get("force") and saved_summary and saved_fingerprint:
        if input_fingerprint(build_prompt(detail, decisions, technique_names)) == saved_fingerprint:
            log_event(EVENT_WORKOUT_SUMMARY_REUSED, activity_id=activity_id, user_id=user_id)
            return {"summary": saved_summary, "generated_at": _iso(detail.get("ai_summary_generated_at"))}

    try:
        result = generate_workout_summary(
            detail, engine_decisions=decisions, user_id=user_id, technique_names=technique_names
        )
    except WorkoutSummaryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    saved = save_activity_ai_summary(
        activity_id, result["summary"], user_id=user_id, input_sha256=result.get("input_sha256")
    )
    if not saved:
        raise HTTPException(status_code=404, detail="Atividade não encontrada.")

    return {"summary": result["summary"], "generated_at": _iso(saved.get("ai_summary_generated_at"))}

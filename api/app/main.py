from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .secrets_bootstrap import assert_no_default_secrets, load_secrets

# Resolve OPENAI_API_KEY do Secrets Manager (musclefit/openai-api-key) uma vez
# por cold start, antes de qualquer router que dependa dela ser importado -
# ver docs/aws-migration-plan.md, Fase 3.
load_secrets()
assert_no_default_secrets()

from . import async_jobs
from .db import get_conn
from .exercise_set_update_merge import validate_sent_technique_codes
from .models import (
    ActivityIn,
    ExerciseLogUpdateIn,
    ExerciseMappingIn,
    InsightsGenerateIn,
    SetEvaluationIn,
)
from .training_goal_query import GOAL_QUERY_DESCRIPTION, goal_query_value
from .security import (
    Identity,
    MustChangePasswordRequired,
    get_current_identity,
    must_change_password_handler,
    require_admin,
)
from .services import (
    activity_detail,
    delete_activity,
    delete_exercise_log,
    exercise_history,
    get_current_group_fatigue,
    get_last_session_sets,
    high_rpe_sets,
    list_exercise_logs,
    latest_activities,
    update_exercise_log,
    WorkoutPlanExerciseNotFoundError,
    update_activity,
    upsert_activity,
    upsert_mapping,
)
from .set_measure_input import SetMeasureInputError
from .workout_ai_generation_core import (
    JOB_TYPE_ACTIVITY_SUMMARY,
    JOB_TYPE_EVALUATE_SET,
    JOB_TYPE_EXERCISE_SUMMARY,
    JOB_TYPE_INSIGHTS,
)


from .api_docs import api_docs_kwargs
from .fastapi_compat import fastapi_compat_kwargs
from .logging_config import configure_logging  # noqa: E402

# Eventos `log_event` (musclefit.events) e demais INFO da aplicacao em stdout,
# uma linha por evento - ver app/logging_config.py.
configure_logging()

app = FastAPI(
    title="Doug Fitness API",
    version="1.0.0",
    description="API para treinos, séries, RPE, bioimpedância e histórico fitness para integração com OpenClaw.",
    **api_docs_kwargs(),
    **fastapi_compat_kwargs(),
)

# CORS: a Settings App do Zepp roda num webview com origem
# https://zepp-os.zepp.com e chama a API via browser (M40: o token é gerado no
# perfil do app web e colado ali; o webview usa `GET /devices/me` e as demais
# rotas com o token de dispositivo).
# A autenticação por X-API-Token continua obrigatória em todos os endpoints;
# liberar origens aqui só permite que navegadores façam a requisição.
#
# Fase 3 (cookie httpOnly de sessão): allow_credentials precisa ser True para
# o navegador enviar/receber o cookie `musclefit_session` em requisições
# cross-origin (app.musclefit.com.br chamando api.musclefit.com.br) — e o
# CORS spec proíbe allow_origins=["*"] junto com credentials, então a lista
# de origens passa a ser explícita via CORS_ALLOWED_ORIGINS (env, separada
# por vírgula), com default cobrindo o frontend de produção.
# SEG-17: métodos e headers também explícitos (sem curinga), centralizados em
# app/cors_config.py e compartilhados com o musclefit-engines.
import os

from .cors_config import cors_middleware_kwargs  # noqa: E402

app.add_middleware(CORSMiddleware, **cors_middleware_kwargs())

# M56: uma linha JSON por requisicao (metodo, template da rota, status,
# duracao) - sem corpo, headers ou query. Adicionado DEPOIS do CORS para ficar
# por fora dele e medir tambem os preflights. Ver app/access_log.py.
from .access_log import AccessLogMiddleware  # noqa: E402

app.add_middleware(AccessLogMiddleware)

# PERF-01/02/04: memo POR REQUISICAO GET das leituras que a mesma chamada repete
# (Modelo, fuso, nomes de grupos, trilhas). Nunca global. Ver app/request_memo.py.
from .request_memo import RequestMemoMiddleware  # noqa: E402

app.add_middleware(RequestMemoMiddleware)


# SEG-06: corpo `{"detail": ..., "code": "must_change_password"}` (o frontend
# reconhece o `code` e leva o usuario a troca de senha).
app.add_exception_handler(MustChangePasswordRequired, must_change_password_handler)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            row = cur.fetchone()
        return {"status": "ready", "db": row["ok"] == 1}
    finally:
        conn.close()


@app.post("/activities")
def create_or_replace_activity(payload: ActivityIn, identity: Identity = Depends(get_current_identity)):
    result = upsert_activity(payload.model_dump(), user_id=identity.user_id)
    return {"status": "ok", **result}


@app.get("/activities")
def list_activities(
    limit: int = Query(default=10, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    gym_id: int | None = Query(default=None),
    conducted_by: str | None = Query(default=None),
    q: str | None = Query(default=None),
    session_status: str | None = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    return {
        "items": latest_activities(
            limit=limit,
            offset=offset,
            date_from=date_from,
            date_to=date_to,
            gym_id=gym_id,
            conducted_by=conducted_by,
            q=q,
            session_status=session_status,
            user_id=identity.user_id,
        )
    }


@app.get("/activities/{activity_id}")
def get_activity(activity_id: int, identity: Identity = Depends(get_current_identity)):
    detail = activity_detail(activity_id, user_id=identity.user_id, include_pending=True)
    if not detail:
        raise HTTPException(status_code=404, detail="Atividade não encontrada.")
    # Interno do dedupe do resumo por IA (IA-14, migração 070): não é dado
    # de tela.
    detail.pop("ai_summary_input_sha256", None)
    return detail


@app.put("/activities/{activity_id}")
def update_activity_by_id(activity_id: int, payload: ActivityIn, identity: Identity = Depends(get_current_identity)):
    result = update_activity(activity_id, payload.model_dump(), user_id=identity.user_id)
    if not result:
        raise HTTPException(status_code=404, detail="Atividade não encontrada.")
    return {"status": "ok", **result}


@app.delete("/activities/{activity_id}")
def delete_activity_by_id(activity_id: int, identity: Identity = Depends(get_current_identity)):
    deleted = delete_activity(activity_id, user_id=identity.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Atividade não encontrada.")
    return {"status": "ok", "activity_id": activity_id}


@app.get("/exercises/history")
def get_exercise_history(
    name: str,
    limit: int = Query(default=30, ge=1, le=200),
    identity: Identity = Depends(get_current_identity),
):
    return {"items": exercise_history(name, limit, user_id=identity.user_id)}


@app.get("/exercises/last-session-sets")
def get_last_session_sets_by_mapping(
    mapping_id: int = Query(...),
    exclude_activity_id: int = Query(...),
    equipment_id: int | None = Query(None),
    goal: str | None = Query(None, description=GOAL_QUERY_DESCRIPTION),
    identity: Identity = Depends(get_current_identity),
):
    """Series da sessao anterior mais recente (excluindo a sessao atual) em que
    o mesmo exercicio canonico (mapping_id) foi executado, usadas como
    referencia historica de reps/carga por serie no modo treino. Nao ha
    sessao anterior e' um caso normal, nao um erro: retorna item=None.
    Quando equipment_id e informado, o match e estrito por equipamento
    (ver get_last_session_sets).

    M79: `current_group_fatigue` = "Fadiga atual do grupo" na sessao em
    andamento (`exclude_activity_id`) antes da 1a serie do exercicio, e cada
    serie de `item.sets` traz `group_fatigue` (antes daquela serie, na sessao
    anterior). Ambos do dono `domain/prior_fatigue.py`; None sem leitura.

    M97 F1: `goal` opcional (o frontend ainda nao manda): sem ele, o objetivo
    e o da linha da ficha da sessao atual (sem ficha = default); a sessao
    anterior e a do MESMO objetivo."""
    return {
        "item": get_last_session_sets(
            mapping_id,
            exclude_activity_id,
            user_id=identity.user_id,
            equipment_id=equipment_id,
            training_goal=None if goal is None else goal_query_value(goal),
        ),
        "current_group_fatigue": get_current_group_fatigue(
            mapping_id, exclude_activity_id, user_id=identity.user_id
        ),
    }


@app.get("/exercises/logs")
def get_exercise_logs(
    limit: int = Query(default=100, ge=1, le=500),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    return {
        "items": list_exercise_logs(
            limit, date_from=date_from, date_to=date_to, user_id=identity.user_id, include_pending=True
        )
    }


@app.put("/exercises/logs/{exercise_id}")
def update_exercise_log_by_id(
    exercise_id: int,
    payload: ExerciseLogUpdateIn,
    identity: Identity = Depends(get_current_identity),
):
    data = payload.model_dump()
    # workout_plan_exercise_id precisa distinguir "não enviado" (preserva
    # vínculo atual) de "enviado como null" (desvincula); model_dump() sem
    # exclude_unset não deixa essa distinção explícita, então sinalizamos
    # separadamente com base em model_fields_set.
    data["workout_plan_exercise_id_provided"] = "workout_plan_exercise_id" in payload.model_fields_set
    # Por série, só as chaves ENVIADAS: campo omitido preserva o valor gravado
    # (ver app/exercise_set_update_merge.py). Sem exclude_unset, o default
    # `set_type="working"` apagaria um drop-set/aquecimento na edição.
    data["sets"] = [s.model_dump(exclude_unset=True) for s in payload.sets]
    validate_sent_technique_codes(data["sets"])
    try:
        result = update_exercise_log(exercise_id, data, user_id=identity.user_id)
    except WorkoutPlanExerciseNotFoundError:
        raise HTTPException(status_code=404, detail="Item de ficha não encontrado.")
    except SetMeasureInputError as exc:
        # Lote T3: série mesclada sem a forma da sua medida (dono `set_measures`).
        raise HTTPException(status_code=422, detail=exc.detail())
    if not result:
        raise HTTPException(status_code=404, detail="Exercício não encontrado.")
    return {"status": "ok", **result}


@app.delete("/exercises/logs/{exercise_id}")
def delete_exercise_log_by_id(exercise_id: int, identity: Identity = Depends(get_current_identity)):
    result = delete_exercise_log(exercise_id, user_id=identity.user_id)
    if not result:
        raise HTTPException(status_code=404, detail="Exercício não encontrado.")
    return {"status": "ok", **result}


# GET /exercises/suggested-progression e GET
# /exercises/suggested-set-count-for-substitution foram MOVIDOS para
# `musclefit-engines` (ver app/engines_extra.py e
# docs/aws-migration-plan.md) - motor deterministico puro, sem CRUD
# misturado no handler. Mesmos paths publicos; so o backend do API Gateway
# passou a apontar para a Lambda nova (ver infra/api_gateway.tf). Este
# arquivo NAO precisa mais de suggested_progression_for_mapping/
# suggested_set_count_for_substitution (import removido acima).


@app.get("/sets/high-rpe")
def get_high_rpe_sets(
    min_rpe: float = 9.0,
    limit: int = Query(default=50, ge=1, le=200),
    identity: Identity = Depends(get_current_identity),
):
    return {"items": high_rpe_sets(min_rpe, limit, user_id=identity.user_id)}


@app.post("/exercise-mappings", dependencies=[Depends(require_admin)])
def create_or_update_exercise_mapping(payload: ExerciseMappingIn):
    # exercise_mappings e o catalogo GLOBAL de mapeamento (nao dado pessoal) -
    # restrito a admin, mesmo padrao dos demais endpoints de catalogo.
    return upsert_mapping(payload.model_dump())


@app.post("/insights/generate", status_code=202)
def generate_insight_endpoint(payload: InsightsGenerateIn, identity: Identity = Depends(get_current_identity)):
    """Assíncrono desde a Fase 3 da migração AWS (202 + job_id + polling via
    GET /jobs/{job_id}) — a geração em si (`insights_core.dispatch_insights`,
    mesma lógica exata do endpoint síncrono anterior) roda na Lambda
    `musclefit-workout-ai` via `workout_ai_generation_core.
    dispatch_workout_ai_generation`, invocada aqui de forma assíncrona
    (`InvocationType=Event`). Mesmo motivo dos demais endpoints de IA do
    modo treino (POST /exercises/evaluate-set etc.) e de
    POST /workouts-analysis/generate — a chamada a OpenAI (com o dump
    completo do RAG local injetado no prompt) é incompatível com o teto de
    29s do API Gateway em condições de rede ruins, ver
    docs/aws-migration-plan.md, seção 1.2.

    **F3.2 — o contexto passou a ser montado no servidor.** O cliente manda
    apenas `question` + `window` (e `date_from`/`date_to` para `custom`); a
    Lambda monta o `intelligence_package` (contrato fechado, ≤ 40 KB) a
    partir da MESMA leitura da visão geral e injeta o RAG **filtrado** pelos
    grupos musculares presentes nesse pacote — não mais o dump completo de
    `rag/markdown`.

    `context` continua aceito no schema e é IGNORADO (com o evento
    `insights.legacy_context_ignored`) durante a janela de compatibilidade do
    deploy: frontend e API são artefatos separados e não sobem no mesmo
    instante. Ver `insights_core.py` para a janela e para quando o campo
    sai."""
    request_payload = payload.model_dump(mode="json")
    job_id = async_jobs.create_job(
        JOB_TYPE_INSIGHTS,
        request={"payload": request_payload, "user_id": identity.user_id},
        owner_user_id=identity.user_id,
    )
    async_jobs.invoke_async(
        "WORKOUT_AI_FUNCTION_NAME",
        {
            "job_id": job_id,
            "job_type": JOB_TYPE_INSIGHTS,
            "payload": request_payload,
            "user_id": identity.user_id,
        },
    )
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "pending"})


@app.post("/exercises/evaluate-set", status_code=202)
def evaluate_set_endpoint(
    payload: SetEvaluationIn,
    identity: Identity = Depends(get_current_identity),
):
    """Assíncrono desde a Fase 3 da migração AWS (202 + job_id + polling via
    GET /jobs/{job_id}) — a avaliação em si
    (`workout_ai_generation_core.dispatch_evaluate_set`, mesma lógica exata
    do endpoint síncrono anterior) roda na Lambda `musclefit-workout-ai`,
    invocada aqui de forma assíncrona (`InvocationType=Event`). Motivo: a
    chamada à OpenAI é incompatível com o teto de 29s do API Gateway em
    condições de rede ruins — mesmo motivo de
    POST /workout-plans/generate/POST /cardio-recommendations/generate, ver
    docs/aws-migration-plan.md, seção 1.2.

    Avalia via OpenAI uma série recém-executada (tela /modo-treino),
    classificando-a numa ação do motor (HOLD/INCREASE_LOAD/DECREASE_LOAD,
    `domain/set_evaluation_categories.py`) em cima do status da série
    calculado pelo motor, com uma orientação prática curta. Chamado em
    paralelo com o início do timer de descanso no frontend — não bloqueia o
    fluxo do treino; o frontend faz polling curto (ver
    frontend/src/lib/workout-mode-types.ts) e trata falha do job como
    "avaliação indisponível", sem travar nada. `identity` aceita token
    mestre, dispositivo ou sessão de usuário (Multiusuario) mas não é usado
    no corpo do job: a chamada à OpenAI não lê/grava dado por usuário."""
    job_id = async_jobs.create_job(
        JOB_TYPE_EVALUATE_SET,
        request={"payload": payload.model_dump(mode="json"), "user_id": identity.user_id},
        owner_user_id=identity.user_id,
    )
    async_jobs.invoke_async(
        "WORKOUT_AI_FUNCTION_NAME",
        {
            "job_id": job_id,
            "job_type": JOB_TYPE_EVALUATE_SET,
            "payload": payload.model_dump(mode="json"),
            "user_id": identity.user_id,
        },
    )
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "pending"})


@app.post("/exercises/{exercise_id}/summarize", status_code=202)
def summarize_exercise_endpoint(exercise_id: int, identity: Identity = Depends(get_current_identity)):
    """Assíncrono desde a Fase 3 da migração AWS (202 + job_id + polling via
    GET /jobs/{job_id}) — mesmo padrão/motivo do endpoint acima. A leitura
    de contexto do exercício (`exercise_summary_context`) e a geração via
    OpenAI (`workout_ai_generation_core.dispatch_exercise_summary`, mesma
    lógica exata do endpoint síncrono anterior) rodam na Lambda
    `musclefit-workout-ai` — não mais aqui, mesmo padrão de
    `cardio_generation_core.dispatch_cardio_generation` (lê o banco dentro
    do dispatch assíncrono, não no endpoint síncrono).

    Gera via OpenAI um resumo do exercício recém-concluído (tela
    /modo-treino, disparado quando a última série de um exercício é salva),
    a partir das séries realmente registradas em `exercise_sets`. NÃO
    persiste nada — mesmo padrão de "não persiste" de
    POST /exercises/evaluate-set; só o resumo de FIM DE TREINO
    (POST /activities/{id}/ai-summary/generate) é salvo no banco."""
    job_id = async_jobs.create_job(
        JOB_TYPE_EXERCISE_SUMMARY,
        request={"payload": {"exercise_id": exercise_id}, "user_id": identity.user_id},
        owner_user_id=identity.user_id,
    )
    async_jobs.invoke_async(
        "WORKOUT_AI_FUNCTION_NAME",
        {
            "job_id": job_id,
            "job_type": JOB_TYPE_EXERCISE_SUMMARY,
            "payload": {"exercise_id": exercise_id},
            "user_id": identity.user_id,
        },
    )
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "pending"})


@app.post("/activities/{activity_id}/ai-summary/generate", status_code=202)
def generate_activity_ai_summary_endpoint(
    activity_id: int,
    force: bool = False,
    identity: Identity = Depends(get_current_identity),
):
    """Assíncrono desde a Fase 3 da migração AWS (202 + job_id + polling via
    GET /jobs/{job_id}) — mesmo padrão/motivo dos dois endpoints acima. A
    leitura de `activity_detail`, a geração via OpenAI e a persistência em
    activities.ai_summary/ai_summary_generated_at
    (`workout_ai_generation_core.dispatch_activity_summary`, mesma lógica
    exata do endpoint síncrono anterior, "gera e já salva numa chamada só")
    rodam na Lambda `musclefit-workout-ai` — mesmo padrão de
    POST /cardio-recommendations/generate.

    Disparado pelo frontend ao finalizar uma sessão em /modo-treino;
    consultável depois em GET /activities/{id} (tela /registro-exercicios).

    IA-14: com a mesma entrada do resumo salvo, o job devolve o resumo
    existente sem chamar a OpenAI; `?force=true` regera mesmo assim."""
    payload: dict = {"activity_id": activity_id}
    if force:
        payload["force"] = True
    job_id = async_jobs.create_job(
        JOB_TYPE_ACTIVITY_SUMMARY,
        request={"payload": payload, "user_id": identity.user_id},
        owner_user_id=identity.user_id,
    )
    async_jobs.invoke_async(
        "WORKOUT_AI_FUNCTION_NAME",
        {
            "job_id": job_id,
            "job_type": JOB_TYPE_ACTIVITY_SUMMARY,
            "payload": payload,
            "user_id": identity.user_id,
        },
    )
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "pending"})


# Analytics router (app/analytics.py), dashboard_modules router e
# dashboard_intelligence router foram MOVIDOS para a Lambda
# `musclefit-engines` (ver app/main_engines.py e
# docs/aws-migration-plan.md) - todos os endpoints `/analytics/*` (motor de
# recuperacao/volume/progressao/ativacao muscular, sem OpenAI). Mesmos paths
# publicos; so o backend do API Gateway passou a apontar para a Lambda nova
# (ver infra/api_gateway.tf). Este app NAO inclui mais esses routers.

# BEGIN intervals integration router
# Endpoints adicionados de forma aditiva para cache/sync Intervals.icu.
# Não altera endpoints existentes usados pelo OpenClaw/MCP.
try:
    from app.intervals_integration import router as intervals_integration_router
    app.include_router(intervals_integration_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.intervals").exception(
        "Intervals integration router não foi carregado. Endpoints existentes permanecem disponíveis. Erro: %s",
        exc,
    )
# END intervals integration router

# BEGIN exercise mapping catalog router
# Catálogo oficial Amazfit/Zepp em MySQL, editável pelo front-end e consultável pela IA.
# Não altera endpoints existentes usados pelo OpenClaw/MCP.
try:
    from app.exercise_mapping_catalog import router as exercise_mapping_catalog_router
    from app.exercise_mapping_catalog import muscle_groups_router
    from app.exercise_mapping_catalog import muscle_contributions_router
    app.include_router(exercise_mapping_catalog_router)
    app.include_router(muscle_groups_router)
    app.include_router(muscle_contributions_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.exercise_mapping_catalog").exception(
        "Exercise mapping catalog router não foi carregado. Endpoints existentes permanecem disponíveis. Erro: %s",
        exc,
    )
# END exercise mapping catalog router

# BEGIN muscle volume classes router
# Fase 6, PR 1 (migração 057): CRUD dos macro-grupos de volume (Grandes,
# Pequenos, Outros e os que o admin criar), com metas por frequência, meta
# semanal e associação de grupos musculares. Leitura para qualquer usuário
# autenticado, escrita só admin. Nada na avaliação lê o cadastro ainda (PR 2).
try:
    from app.muscle_volume_classes import router as muscle_volume_classes_router
    app.include_router(muscle_volume_classes_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.muscle_volume_classes").exception(
        "Muscle volume classes router não foi carregado. Erro: %s", exc
    )
# END muscle volume classes router

# BEGIN exercise catalog admin router (Fase 5 da reestruturação — seção
# /admin/dominio-exercicios do frontend: CRUD de exercise_catalog, aliases,
# mapeamentos externos e equipment_types. Reaproveita
# muscle_contributions_router (já registrado acima) para contribuições
# musculares, não duplica.)
try:
    from app.exercise_catalog_admin import router as exercise_catalog_admin_router
    from app.exercise_catalog_admin import equipment_types_router
    from app.exercise_catalog_admin import external_mappings_router
    app.include_router(exercise_catalog_admin_router)
    app.include_router(equipment_types_router)
    app.include_router(external_mappings_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.exercise_catalog_admin").exception(
        "Exercise catalog admin router não foi carregado. Erro: %s", exc
    )
# END exercise catalog admin router

# BEGIN exercise cards router (cards ilustrados de exercicios)
# Geracao por IA (job_kind "exercise-card" na plan-generator), revisao,
# publicacao versionada no S3/CDN e leitura do card publicado. Rotas
# `/exercises/{id}/card-generations*` (admin; 503 com a flag
# EXERCISE_CARD_GENERATION_ENABLED desligada) e `/exercises/{id}/card`
# (autenticado). Mesmo prefixo do router do catalogo, sem colisao de path.
try:
    from app.exercise_cards import router as exercise_cards_router
    app.include_router(exercise_cards_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.exercise_cards").exception(
        "Exercise cards router não foi carregado. Erro: %s", exc
    )
# END exercise cards router

# Analytics com grupos musculares cruzados pelo catálogo Amazfit/Zepp.
from app.muscle_groups_mapped import router as muscle_groups_mapped_router
app.include_router(muscle_groups_mapped_router)
# Fonte canônica de classificação: exercise_zepp_mappings, referenciada via exercises.mapping_id.
from app.exercise_mapping_canonical import router as exercise_mapping_canonical_router
app.include_router(exercise_mapping_canonical_router)

# BEGIN body composition CRUD router
# Endpoints GET/POST/PUT/DELETE para bioimpedância (front-end + MCP/OpenClaw).
# O POST /body-composition (usado pelo MCP/OpenClaw) foi unificado neste
# router junto com o CRUD do front-end - mesmo contrato de antes.
try:
    from app.body_composition import router as body_composition_router
    app.include_router(body_composition_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.body_composition").exception(
        "Body composition router não foi carregado. Erro: %s", exc
    )
# END body composition CRUD router

# BEGIN body measurements CRUD router (Novo Dashboard F1.4, migracao 047)
# Medidas por fita metrica (cintura, quadril, bracos...) - entrada de dado do
# `/dashboard/corpo`. A engines so LE a tabela (body_service).
try:
    from app.body_measurements import router as body_measurements_router
    app.include_router(body_measurements_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.body_measurements").exception(
        "Body measurements router não foi carregado. Erro: %s", exc
    )
# END body measurements CRUD router

# BEGIN context events CRUD router (Novo Dashboard F2.1, migracao 048)
# Eventos de contexto manuais (alcool, viagem, doenca, lesao...) - entrada de
# dado do `/dashboard/contexto`. A engines so LE a tabela (context_service),
# e os derivados (troca de ficha/academia) sao calculados na leitura.
try:
    from app.context_events import router as context_events_router
    app.include_router(context_events_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.context_events").exception(
        "Context events router não foi carregado. Erro: %s", exc
    )
# END context events CRUD router

# BEGIN deload periods router (M97 D, migracao 094)
# "Semana de deload" (inicio/fim) do usuario. A regra mora no dono
# `app/domain/deload.py`; o `start` da sessao carimba `activities.deload_source`.
try:
    from app.deload_periods import router as deload_periods_router
    app.include_router(deload_periods_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.deload_periods").exception(
        "Deload periods router não foi carregado. Erro: %s", exc
    )
# END deload periods router

# BEGIN training goals router (M97 F3)
# Leitura sem banco: rótulos e defaults de cada objetivo de treino, montados
# pelos donos (`domain/training_goal.py`, `prescription_defaults`, `training_safety`).
try:
    from app.training_goals import router as training_goals_router
    app.include_router(training_goals_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.training_goals").exception(
        "Training goals router não foi carregado. Erro: %s", exc
    )
# END training goals router

# BEGIN daily checkins CRUD router (Novo Dashboard F2.2, migracao 050)
# Check-in diario subjetivo (disposicao, dor global, estresse, humor) - entrada
# de dado do `/dashboard/checkin`. A engines so LE a tabela (checkin_service),
# que alimenta os sinais subjetivos de recuperacao.
try:
    from app.daily_checkins import router as daily_checkins_router
    app.include_router(daily_checkins_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.daily_checkins").exception(
        "Daily checkins router não foi carregado. Erro: %s", exc
    )
# END daily checkins CRUD router

# BEGIN nutrition plans router (plano de nutricao, Lote L2b, migracoes 078/079)
# Plano nutricional do aluno (admin/PT: rascunho, preview, publicacao,
# recalculo das metas) e GET /nutrition/me/plan (o proprio usuario, so
# leitura). Regras, faixas e o calculo da meta sao do motor
# `domain/nutrition`; tabelas ausentes respondem 503 citando a migracao.
try:
    from app.nutrition_plans import router as nutrition_plans_router
    app.include_router(nutrition_plans_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.nutrition_plans").exception(
        "Nutrition plans router não foi carregado. Erro: %s", exc
    )
# END nutrition plans router

# BEGIN next set load rules router (regras de carga da proxima serie, PR R2,
# migracao 088). Conjunto de regras versionado: GET /published (qualquer
# autenticado) e rascunho/publicacao/historico/descricao/simulacao (admin).
# Validacao semantica, textos e simulacao sao do dono `domain/next_set_load.py`.
try:
    from app.next_set_load_rules_admin import router as next_set_load_rules_router
    app.include_router(next_set_load_rules_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.next_set_load_rules").exception(
        "Next set load rules router não foi carregado. Erro: %s", exc
    )
# END next set load rules router

# BEGIN nutrition foods router (plano de nutricao, Lote L3a, migracoes 080/081)
# Catalogo de alimentos: busca (FULLTEXT + nota do motor `food_text`), alimento
# do usuario privado (D-G), porcoes e promocao a publico pelo admin. TACO 4a ed.
# somente leitura (seed da 081). Tabelas ausentes respondem 503 citando a 080.
try:
    from app.nutrition_foods import router as nutrition_foods_router
    app.include_router(nutrition_foods_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.nutrition_foods").exception(
        "Nutrition foods router não foi carregado. Erro: %s", exc
    )
# END nutrition foods router

# BEGIN nutrition external foods router (plano de nutricao, Lote L3b; sem migracao)
# Codigo de barras (Open Food Facts, cache em food_items source='off', limite
# diario do motor) e busca/importacao do USDA FoodData Central. So a api-core
# recebe USDA_API_KEY (secrets_bootstrap); sem chave as rotas do USDA respondem
# 503. Mapeamento de nutrientes e regras: domain/nutrition/external_sources.
try:
    from app.nutrition_external_foods import router as nutrition_external_foods_router
    app.include_router(nutrition_external_foods_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.nutrition_external_foods").exception(
        "Nutrition external foods router não foi carregado. Erro: %s", exc
    )
# END nutrition external foods router

# BEGIN nutrition label photo router (plano de nutricao, Lote L6; sem migracao)
# Foto do rotulo: upload (JPEG reduzido pela tela, limite e tipo do motor
# `label_photo`) -> cota de IA (ai_usage_log, 085) -> S3 nutrition-labels/ ->
# job assincrono na musclefit-plan-generator (leitura pela IA + revisao do
# motor). O cadastro continua em POST /nutrition/foods (source=label_photo).
try:
    from app.nutrition_label_photo import router as nutrition_label_photo_router
    app.include_router(nutrition_label_photo_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.nutrition_label_photo").exception(
        "Nutrition label photo router não foi carregado. Erro: %s", exc
    )
# END nutrition label photo router

# BEGIN nutrition diary routers (plano de nutricao, Lote L4, migracoes 082/083)
# Diario de refeicoes: refeicoes com os nutrientes congelados por item (motor
# `meal_math`/`diary`), preview sem gravar, dia (meta, totais, agua/fome, kcal
# do treino informada x estimada, balanco) e semana (orcamento, banco e
# aderencia do `weekly_budget`). Tabelas ausentes respondem 503 citando a 082/083.
try:
    from app.nutrition_meals import router as nutrition_meals_router
    from app.nutrition_days import router as nutrition_days_router
    app.include_router(nutrition_meals_router)
    app.include_router(nutrition_days_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.nutrition_diary").exception(
        "Nutrition diary routers não foram carregados. Erro: %s", exc
    )
# END nutrition diary routers

# BEGIN nutrition meal parse router (plano de nutricao, Lote L5b; sem migracao)
# Leitura da refeicao por IA: POST /nutrition/meals/parse reserva a cota do dia
# (`nutrition_ai_quota`, tabela 085) e despacha o job para a Lambda
# musclefit-workout-ai (202 + polling em GET /jobs/{id}); GET
# /nutrition/meals/parse/quota mostra o uso do dia. Casamento e itens: motor
# `domain/nutrition/meal_parse`.
try:
    from app.nutrition_meal_parse import router as nutrition_meal_parse_router
    app.include_router(nutrition_meal_parse_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.nutrition_meal_parse").exception(
        "Nutrition meal parse router não foi carregado. Erro: %s", exc
    )
# END nutrition meal parse router

# BEGIN sleep metrics CRUD router
# Endpoints GET/POST/PUT/DELETE para entrada manual de dados de sono (indicadores físicos).
try:
    from app.sleep_metrics import router as sleep_metrics_router
    app.include_router(sleep_metrics_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.sleep_metrics").exception(
        "Sleep metrics router não foi carregado. Erro: %s", exc
    )
# END sleep metrics CRUD router

# BEGIN effort metrics router (M99)
# GET /effort-metrics: EPOC/PAI de 7 dias ESTIMADOS do tempo em zona do Intervals
# (motor `domain/cardio_load.py`). O CRUD manual saiu; a tabela fica como histórico.
try:
    from app.effort_metrics import router as effort_metrics_router
    app.include_router(effort_metrics_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.effort_metrics").exception(
        "Effort metrics router não foi carregado. Erro: %s", exc
    )
# END effort metrics router

# BEGIN cardio recommendations CRUD router
# Endpoints POST/GET/DELETE para log de auditoria das recomendações do agente de cardio.
# Registro histórico imutável (sem PUT) — permite múltiplas gerações no mesmo dia.
try:
    from app.cardio_recommendations import router as cardio_recommendations_router
    app.include_router(cardio_recommendations_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.cardio_recommendations").exception(
        "Cardio recommendations router não foi carregado. Erro: %s", exc
    )
# END cardio recommendations CRUD router

# dashboard_intelligence router e dashboard_modules router: ver comentario
# acima do antigo "BEGIN fitness analytics router" - ambos moveram para
# musclefit-engines junto com analytics_router.

# BEGIN gyms/gym_equipment router
# Academias e equipamentos, ligados a exercise_zepp_mappings por grupo
# muscular. Usado para variar equipamento na montagem de ficha e para
# resolver "equipamento ocupado" durante o treino.
try:
    from app.gyms import router as gyms_router
    app.include_router(gyms_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.gyms").exception(
        "Gyms router não foi carregado. Erro: %s", exc
    )
# END gyms/gym_equipment router

# BEGIN equipment_exercises router (PR-05: fonte canonica da relacao
# equipamento <-> exercicio; path top-level /equipment-exercises cai no
# catch-all da api-core no API Gateway - NAO usar /exercises/*)
try:
    from app.equipment_exercises import router as equipment_exercises_router
    app.include_router(equipment_exercises_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.equipment_exercises").exception(
        "Equipment exercises router não foi carregado. Erro: %s", exc
    )
# END equipment_exercises router

# BEGIN muscle group <-> equipment/exercise navigation router (Fase 4 da
# reestruturacao de dominio - leitura aditiva via
# vw_muscle_group_equipment_exercises, ver muscle_group_equipment.py)
try:
    from app.muscle_group_equipment import router as muscle_group_equipment_router
    app.include_router(muscle_group_equipment_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.muscle_group_equipment").exception(
        "Muscle group equipment router não foi carregado. Erro: %s", exc
    )
# END muscle group <-> equipment/exercise navigation router

# BEGIN workout plans router
# Planos de treino gerados via IA (mesmas premissas do RAG/prompts usados na
# geracao de ficha via Telegram/OpenClaw) + CRUD manual, consultavel pelo MCP.
try:
    from app.workout_plans import router as workout_plans_router
    app.include_router(workout_plans_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.workout_plans").exception(
        "Workout plans router não foi carregado. Erro: %s", exc
    )
# END workout plans router

# BEGIN exercise briefing router (M4)
# GET /workout-plans/{id}/exercises/{wpe_id}/briefing: tudo o que a tela de
# briefing do modo treino mostra antes da primeira serie, numa chamada so.
# Router proprio (nao dentro de workout_plans.py) porque agrega catalogo,
# equipamento, historico e trilha - dominios que workout_plans.py nao conhece.
try:
    from app.exercise_briefing import router as exercise_briefing_router
    app.include_router(exercise_briefing_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.exercise_briefing").exception(
        "Exercise briefing router não foi carregado. Erro: %s", exc
    )
# END exercise briefing router

# BEGIN async jobs router (GET /jobs/{job_id})
# Padrao 202 + job_id + polling (musclefit-plan-generator/musclefit-cardio-agent)
# - ver docs/aws-migration-plan.md, secao 1.2 e Fase 3.
try:
    from app.jobs import router as jobs_router
    app.include_router(jobs_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.jobs").exception(
        "Async jobs router não foi carregado. Erro: %s", exc
    )
# END async jobs router

# BEGIN workout plan templates router
# Modelos de Ficha de Treino Personalizados (Fase 1, sem IA): CRUD +
# atribuicao + preview de ativacao semanal para Admin/Personal Trainer
# (require_admin_or_pt). Plugado em POST /workout-plans/generate via
# workout_plan_templates_engine.py (import direto em workout_plans.py, ja
# registrado acima).
#
# me_router (GET /workout-plan-templates/assigned-to-me) e' registrado ANTES
# do router admin/PT abaixo: ambos compartilham o prefixo
# "/workout-plan-templates" e o router admin/PT tem uma rota dinamica
# "/{template_id}" que, sem tipo no PATH TEMPLATE (so na assinatura Python),
# casaria com "/assigned-to-me" primeiro se viesse antes na ordem de
# roteamento do Starlette (mesmo raciocinio ja aplicado a "/users-lookup"
# dentro do proprio router admin/PT).
try:
    from app.workout_plan_templates import me_router as workout_plan_templates_me_router
    app.include_router(workout_plan_templates_me_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.workout_plan_templates").exception(
        "Workout plan templates me_router não foi carregado. Erro: %s", exc
    )

try:
    from app.workout_plan_templates import router as workout_plan_templates_router
    app.include_router(workout_plan_templates_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.workout_plan_templates").exception(
        "Workout plan templates router não foi carregado. Erro: %s", exc
    )
# END workout plan templates router

# Catálogo de técnicas avançadas (tabela advanced_techniques, migração 031):
# GET /advanced-techniques — leitura para qualquer usuário autenticado.
from app.advanced_techniques import router as advanced_techniques_router
app.include_router(advanced_techniques_router)

# Capacidades de segurança por equipamento / override por par (migrações 032 e
# 033): GET/PUT /equipment/{id}/safety e /equipment-exercises/{id}/safety,
# restritos a admin. Paths top-level -> catch-all da api-core.
from app.training_safety_admin import router as training_safety_admin_router
app.include_router(training_safety_admin_router)

# Preferência do usuário por par exercício+equipamento, em estrelas (migração
# 035): GET/PUT/DELETE /me/equipment-exercise-preferences e, só leitura, GET
# /me/training-preferences (tela "Preferências de Treino") — sempre do usuário
# corrente. Paths top-level -> catch-all da api-core.
from app.equipment_exercise_preferences import router as equipment_exercise_preferences_router
from app.equipment_exercise_preferences import training_preferences_router
app.include_router(equipment_exercise_preferences_router)
app.include_router(training_preferences_router)

# Estado do Motor de Progressão v2 (migração 036, PR-10), somente leitura e
# sempre do usuário corrente: GET /progression/tracks e
# GET /progression/tracks/{id}/decisions. Em modo sombra (PROGRESSION_ENGINE)
# essas rotas são a única forma de ver o que o motor novo decidiria — nenhuma
# ficha, prescrição ou resposta existente muda.
from app.progression import router as progression_router
app.include_router(progression_router)

# BEGIN training types router
# Feature "Tipo de Treinamento" (meta semanal de volume por grupo muscular) —
# ver melhoria-eager-origami.md. CRUD Admin/Personal Trainer, associado
# opcionalmente a um Modelo de Treino via workout_plan_templates.training_type_id
# (persistido em workout_plan_templates.py).
try:
    from app.training_types import router as training_types_router
    app.include_router(training_types_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.training_types").exception(
        "Training types router não foi carregado. Erro: %s", exc
    )
# END training types router

# BEGIN devices router
# Pareamento de dispositivos (app Zepp OS): tokens revogaveis por dispositivo,
# geridos pelo proprio usuario (sessao) na tela de perfil; o token age em nome
# do dono do dispositivo (M40, ver security._device_identity).
try:
    from app.devices import router as devices_router
    app.include_router(devices_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.devices").exception(
        "Devices router não foi carregado. Erro: %s", exc
    )
# END devices router

# BEGIN auth router
# Multiusuario (Parte 1): login/logout/sessao/troca de senha. MCP/Telegram e
# o pareamento do relogio Zepp continuam usando o token mestre/token de
# dispositivo, resolvidos para o usuario admin via security.resolve_identity
# - nao dependem deste router nem sao afetados por ele.
try:
    from app.auth import ensure_admin_seed
    from app.auth import router as auth_router
    app.include_router(auth_router)

    # M5: "Esqueci minha senha" (rotas publicas com captcha + e-mail via SES).
    from app.password_reset import router as password_reset_router
    app.include_router(password_reset_router)

    @app.on_event("startup")
    def _seed_admin_user() -> None:
        ensure_admin_seed()
except Exception as exc:
    import logging
    logging.getLogger("fitness.auth").exception(
        "Auth router não foi carregado. Erro: %s", exc
    )
# END auth router

# BEGIN users router
# Multiusuario (Parte 1): CRUD de usuarios (admin gerencia todos; usuario
# regular gerencia o proprio perfil via /users/me).
try:
    from app.users import router as users_router
    from app.users import migrate_plaintext_intervals_keys
    app.include_router(users_router)

    @app.on_event("startup")
    def _migrate_plaintext_intervals_keys() -> None:
        # Parte 2: descriptografa/recriptografa qualquer
        # intervals_api_key_encrypted gravado em texto puro pela Parte 1
        # (ver docstring de migrate_plaintext_intervals_keys). Idempotente e
        # nunca lança - roda em todo startup sem custo relevante.
        migrate_plaintext_intervals_keys()
except Exception as exc:
    import logging
    logging.getLogger("fitness.users").exception(
        "Users router não foi carregado. Erro: %s", exc
    )
# END users router

# BEGIN local dev schema guards on startup
# Num banco local recem-criado (sem seed de producao), colunas que so sao
# adicionadas de forma lazy por `_ensure_schema()` de modulos especificos
# (ver `app/schema_guards.py::SCHEMA_GUARD_MODULES`) ainda nao
# existem - isso evita 500 em endpoints de LEITURA que dependem delas antes
# de qualquer endpoint de escrita do mesmo modulo ter sido chamado uma vez
# (ex.: GET /activities num banco vazio). Restrito a DB_BACKEND=pymysql (dev
# local) para nao alterar nada do comportamento de producao.
try:
    if os.environ.get("DB_BACKEND", "data-api") == "pymysql":
        from app.schema_guards import run_all_schema_guards

        @app.on_event("startup")
        def _run_local_dev_schema_guards() -> None:
            run_all_schema_guards()
except Exception as exc:
    import logging
    logging.getLogger("fitness.schema_guards").exception(
        "Schema guards de dev local nao puderam ser registrados. Erro: %s", exc
    )
# END local dev schema guards on startup

# BEGIN reports router
# Relatorio em Markdown (GET /reports/markdown): grupos musculares, sono/HRV/
# FC, corpo, cardio e ciclos do periodo, gerado na hora e so com dados (sem
# IA). Substituiu o relatorio PDF assincrono da Lambda musclefit-reports.
try:
    from app.reports import router as reports_router
    app.include_router(reports_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.reports").exception(
        "Reports router não foi carregado. Erro: %s", exc
    )
# END reports router

# BEGIN workouts analysis router
# Analise de evolucao de treinos via OpenAI para a tela /treinos - assincrona
# desde a Fase 3 (202 + job_id + polling via GET /jobs/{job_id}, geracao
# roda na Lambda musclefit-workout-ai via workouts_analysis_core.py), sem
# persistencia, reaproveitando os mesmos services usados pela secao
# "Evolucao dos Treinos" do Relatorio em PDF.
try:
    from app.workouts_analysis import router as workouts_analysis_router
    app.include_router(workouts_analysis_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.workouts_analysis").exception(
        "Workouts analysis router não foi carregado. Erro: %s", exc
    )
# END workouts analysis router

# BEGIN workout sessions router
# Gravacao incremental de treino ao vivo (serie a serie) pelo relogio Amazfit.
# Nao substitui POST /activities (importacao completa), que permanece intacto.
try:
    from app.workout_sessions import router as workout_sessions_router
    app.include_router(workout_sessions_router)
except Exception as exc:
    import logging
    logging.getLogger("fitness.workout_sessions").exception(
        "Workout sessions router não foi carregado. Erro: %s", exc
    )
# END workout sessions router

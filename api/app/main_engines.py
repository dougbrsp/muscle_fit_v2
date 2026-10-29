"""Entrypoint FastAPI da Lambda `musclefit-engines` (Lambda 2 da migracao —
ver docs/aws-migration-plan.md e infra/lambda_engines.tf).

Concentra os motores DETERMINISTICOS (sem nenhuma chamada a OpenAI) hoje
espalhados em `main.py`/`analytics.py`/`dashboard_modules.py`/
`dashboard_intelligence.py`: recuperacao muscular, volume de treino,
progressao de carga/reps, ativacao muscular por exercicio/atividade,
tendencias de bioimpedancia e os dashboards agregados que compoe esses
dados. Reaproveita OS MESMOS modulos de `app/intelligence/*` e `app/services.py`
usados por `musclefit-api-core` (mesmo pacote `app/` copiado nesta imagem —
ver `api/Dockerfile.engines` — sem duplicacao de codigo, mesmo padrao ja
usado por `api/Dockerfile.ai` para as Lambdas de IA assincronas).

Roteadores NAO incluidos aqui de proposito (motivo em cada caso):
- `workouts_analysis.py`, `reports.py`, `workout_plan_ai.py`,
  `cardio_agent_ai.py`, `exercise_set_evaluation_ai.py`,
  `exercise_summary_ai.py`, `insights_ai.py`, `workout_summary_ai.py`:
  chamam OpenAI diretamente — permanecem em `musclefit-api-core` (serao as
  Lambdas 3/4 futuramente, fora do escopo desta extracao).
- `workout_plans.py`: CRUD de planos + o endpoint assincrono
  `POST /workout-plans/generate` (que so cria job e invoca
  `musclefit-plan-generator`) — nao e motor em si, fica em
  `musclefit-api-core`.
- Todo o restante (catalogo, academias/equipamentos, usuarios, dispositivos,
  sessoes ao vivo etc.): CRUD puro, sem dependencia de `intelligence/`, fica
  em `musclefit-api-core`.

Paths publicos preservados EXATAMENTE como estavam em `musclefit-api-core`
(o frontend nao percebe diferenca) — so o backend do API Gateway muda para
apontar para esta Lambda nesses paths especificos (ver
`infra/api_gateway.tf`)."""

from __future__ import annotations

from .secrets_bootstrap import load_secrets

# `musclefit-engines` NAO tem acesso ao secret `musclefit/openai-api-key`
# (motores deterministicos, sem LLM — ver infra/lambda_engines.tf). Resolve
# so o que os modulos importados aqui realmente usam: API_TOKEN (auth de
# todos os endpoints via X-API-Token, ver security.py). Sem
# OPENAI_API_KEY_SECRET_ARN/SECRET_ENCRYPTION_KEY_SECRET_ARN configuradas
# nesta Lambda, load_secrets() simplesmente as ignora (no-op) - ver
# secrets_bootstrap.py.
load_secrets()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import get_conn

from .api_docs import api_docs_kwargs
from .fastapi_compat import fastapi_compat_kwargs
from .logging_config import configure_logging  # noqa: E402

# Eventos `log_event` (musclefit.events) e demais INFO da aplicacao em stdout,
# uma linha por evento - ver app/logging_config.py.
configure_logging()

app = FastAPI(
    title="Doug Fitness Engines API",
    version="1.0.0",
    description="Motores deterministicos (recuperacao, volume, progressao, ativacao muscular) extraidos de musclefit-api-core.",
    **api_docs_kwargs(),
    **fastapi_compat_kwargs(),
)

# Fase 3 (cookie httpOnly de sessão): allow_credentials=True é exigido para o
# navegador enviar/receber o cookie `musclefit_session` cross-origin
# (app.musclefit.com.br -> api.musclefit.com.br), e o CORS spec proíbe
# allow_origins=["*"] junto com credentials — mesma configuração de main.py,
# origens explícitas via CORS_ALLOWED_ORIGINS (env, separada por vírgula).
# SEG-17: métodos e headers também explícitos, em app/cors_config.py.
from .cors_config import cors_middleware_kwargs  # noqa: E402

app.add_middleware(CORSMiddleware, **cors_middleware_kwargs())

# PERF-01/02/04: memo POR REQUISICAO GET das leituras que a mesma chamada repete
# (Modelo, fuso, nomes de grupos, trilhas). Nunca global. Ver app/request_memo.py.
from .request_memo import RequestMemoMiddleware  # noqa: E402

app.add_middleware(RequestMemoMiddleware)

# SEG-06: mesmo corpo do 403 `must_change_password` do musclefit-api-core
# (`{"detail", "code"}`); sem isto o frontend recebia so `detail` aqui.
from .security import MustChangePasswordRequired, must_change_password_handler  # noqa: E402

app.add_exception_handler(MustChangePasswordRequired, must_change_password_handler)


@app.get("/health")
def health():
    return {"status": "ok", "service": "musclefit-engines"}


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


# BEGIN analytics router (app/analytics.py) — /analytics/summary,
# /analytics/training-trend, /analytics/exercise-progress,
# /analytics/rpe-distribution, /analytics/cardio,
# /analytics/muscle-activation-by-activity,
# /analytics/muscle-activation-preview, /analytics/muscle-activation-breakdown,
# /analytics/dashboard-* etc.
# Verificado sem import de openai_client/workout_plan_ai/cardio_agent_ai.
from .analytics import router as analytics_router

app.include_router(analytics_router)
# END analytics router

# BEGIN dashboard modules router (app/dashboard_modules.py) —
# /analytics/body-composition-trend, /analytics/training-volume,
# /analytics/training-volume-by-split-day, /analytics/muscle-recovery,
# /analytics/muscle-recovery-consecutive-check,
# /analytics/muscle-group-body-map.
from .dashboard_modules import router as dashboard_modules_router

app.include_router(dashboard_modules_router)
# END dashboard modules router

# BEGIN dashboard intelligence router (app/dashboard_intelligence.py) —
# /analytics/training-evolution,
# /analytics/cardio-agent-context.
from .dashboard_intelligence import router as dashboard_intelligence_router

app.include_router(dashboard_intelligence_router)
# END dashboard intelligence router

# BEGIN dashboard v3 router (app/analytics_dashboard.py) - Novo Dashboard
# (plano docs/dashboard/PLANO_NOVO_DASHBOARD_20260922.md, §10):
# /analytics/dashboard/exercises/{exercise_id}/progress e as demais rotas do
# hub novo, que chegam nas fases seguintes. Router SEPARADO de analytics.py:
# envelope semantico + `response_model` + janela no fuso do usuario.
from .analytics_dashboard import router as analytics_dashboard_router

app.include_router(analytics_dashboard_router)
# END dashboard v3 router

# BEGIN engines extra router (app/engines_extra.py) —
# /exercises/suggested-progression,
# /exercises/suggested-set-count-for-substitution. Movidos de main.py: sao
# motor puro (services.suggested_progression_for_mapping/
# suggested_set_count_for_substitution), sem CRUD nem OpenAI.
from .engines_extra import router as engines_extra_router

app.include_router(engines_extra_router)
# END engines extra router

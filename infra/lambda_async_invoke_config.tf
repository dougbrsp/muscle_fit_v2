# M70 - sem reentrega automatica nas Lambdas de IA invocadas de forma
# assincrona (`InvocationType=Event`, api/app/async_jobs.py::invoke_async).
#
# Sem `event_invoke_config`, a AWS reentrega o evento ate 2 vezes quando a
# invocacao termina em erro de funcao. Os handlers
# (app/lambda_plan_generator.py, lambda_cardio_agent.py, lambda_workout_ai.py)
# ja capturam toda excecao e gravam `failed` no job, entao a reentrega so
# acontecia nos casos que o handler nao pega - timeout, OOM, crash de init -
# justamente quando a OpenAI ja pode ter sido cobrada. Com 0 retries, falha
# fica falha; o usuario/admin tenta de novo explicitamente.
#
# Chamadores (todos via async_jobs.invoke_async, nome da funcao pela env var
# da musclefit-api-core em infra/lambda_api_core.tf):
#   - musclefit-plan-generator (PLAN_GENERATOR_FUNCTION_NAME): ficha
#     (workout_plans.py), periodizacao (workout_plan_templates.py), descricao
#     de execucao (exercise_description_autogen.py) e card ilustrado
#     (exercise_card_jobs.py);
#   - musclefit-cardio-agent (CARDIO_AGENT_FUNCTION_NAME):
#     cardio_recommendations.py;
#   - musclefit-workout-ai (WORKOUT_AI_FUNCTION_NAME): main.py (3 endpoints
#     do Modo Treino), workouts_analysis.py e a leitura da refeicao por IA da
#     nutricao (nutrition_meal_parse.py, L5b).
# FORA: musclefit-engines (so HTTP via API Gateway) e
# musclefit-intervals-sync (EventBridge, sem OpenAI - retry e desejavel).
#
# `maximum_event_age_in_seconds` fica no padrao (6 h): ele governa a fila de
# throttling, que a AWS reentrega independentemente de `maximum_retry_attempts`.
# Desde a concorrencia reservada (AWS-01, var.lambda_reserved_concurrency) quem
# enfileira e o teto DA FUNCAO, nao mais o limite de 10 da conta. Um evento
# enfileirado nem rodou (sem cobranca dupla por si), mas se ficar na fila alem
# do timeout da Lambda + 60 s o job vira failed/worker_lost no poll do dono
# (M73, api/app/async_jobs.py). Quando finalmente rodar, o handler reivindica o
# job antes de chamar a OpenAI (M81, `async_jobs.claim_job`): job ja fechado ou
# entrega duplicada nao roda nem cobra, e `mark_done`/`mark_failed` sao
# condicionais (nao sobrescrevem o worker_lost). Os tetos das 3 de IA tem folga
# e o alarme musclefit-lambda-throttles-reservadas (monitoring.tf) avisa se
# houver throttle.
#
# Sem qualifier: as funcoes nao publicam versao/alias e sao invocadas pelo
# nome (= $LATEST).
#
# Exige lambda:*FunctionEventInvokeConfig na role musclefit-gha-deploy - ver
# infra/iam-policies/musclefit-gha-deploy-m69-m70.json.

resource "aws_lambda_function_event_invoke_config" "musclefit_plan_generator" {
  function_name          = aws_lambda_function.musclefit_plan_generator.function_name
  maximum_retry_attempts = 0
}

resource "aws_lambda_function_event_invoke_config" "musclefit_cardio_agent" {
  function_name          = aws_lambda_function.musclefit_cardio_agent.function_name
  maximum_retry_attempts = 0
}

resource "aws_lambda_function_event_invoke_config" "musclefit_workout_ai" {
  function_name          = aws_lambda_function.musclefit_workout_ai.function_name
  maximum_retry_attempts = 0
}

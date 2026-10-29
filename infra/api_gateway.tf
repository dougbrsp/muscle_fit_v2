# API Gateway REST API (musclefit-api) com proxy integration para
# musclefit-api-core - ver docs/aws-migration-plan.md, secao 1.3 e Fase 3.

data "aws_acm_certificate" "api" {
  domain      = "api.musclefit.com.br"
  statuses    = ["ISSUED"]
  types       = ["AMAZON_ISSUED"]
  most_recent = true
}

resource "aws_api_gateway_rest_api" "musclefit_api" {
  name = "musclefit-api"

  # "*/*" faz o API Gateway decodificar o base64 que a extensao Lambda Web
  # Adapter usa para corpos de resposta binarios (foto de equipamento/logo de
  # academia via S3 em gyms.py, PDF de relatorio) - sem isso, REST API nunca
  # decodifica `isBase64Encoded: true` (a API HTTP/v2 faz isso
  # automaticamente, a REST API/v1 exige binary_media_types explicito) e o
  # corpo chega ao browser como texto base64 cru, corrompendo a imagem/PDF
  # (o browser cria um object URL valido a partir dos bytes errados, por
  # isso a miniatura aparece como "imagem quebrada" em vez de erro de rede -
  # achado 2026-09-11, telas Academias/Equipamentos). Respostas JSON
  # continuam OK porque a adapter so marca isBase64Encoded quando o corpo nao
  # e texto UTF-8 valido / o Content-Type nao e reconhecido como texto.
  binary_media_types = ["*/*"]

  # SEG-05/AWS-08: o host padrao <id>.execute-api.sa-east-1.amazonaws.com fica
  # desligado; so api.musclefit.com.br (domain name abaixo) responde. Nenhum
  # consumidor usa o execute-api desde o SEG-04 (plan-generator e cardio-agent
  # leem o banco direto; nenhuma Lambda tem URL da API no ambiente, conferido em
  # producao em 2026-10-11); o frontend usa DEFAULT_API_BASE_URL
  # (frontend/src/lib/config.ts) e o relogio Zepp usa a URL digitada no app do
  # celular (conferir antes da promocao: ver infra/api_gateway_borda.tf).
  # Mudar este valor so vale depois de um novo deployment: por isso ele entra
  # no hash de triggers do aws_api_gateway_deployment abaixo.
  disable_execute_api_endpoint = var.api_disable_execute_api_endpoint

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

resource "aws_api_gateway_resource" "proxy" {
  rest_api_id = aws_api_gateway_rest_api.musclefit_api.id
  parent_id   = aws_api_gateway_rest_api.musclefit_api.root_resource_id
  path_part   = "{proxy+}"
}

resource "aws_api_gateway_method" "proxy_any" {
  rest_api_id      = aws_api_gateway_rest_api.musclefit_api.id
  resource_id      = aws_api_gateway_resource.proxy.id
  http_method      = "ANY"
  authorization    = "NONE" # autenticacao e feita pelo proprio FastAPI via header X-API-Token (security.py) - igual ao on-premise
  api_key_required = false
}

resource "aws_api_gateway_integration" "proxy_any" {
  rest_api_id             = aws_api_gateway_rest_api.musclefit_api.id
  resource_id             = aws_api_gateway_resource.proxy.id
  http_method             = aws_api_gateway_method.proxy_any.http_method
  integration_http_method = "POST" # Lambda proxy integration e sempre invocada via POST, independente do metodo original
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.musclefit_api_core.invoke_arn
}

resource "aws_api_gateway_method" "root_any" {
  rest_api_id   = aws_api_gateway_rest_api.musclefit_api.id
  resource_id   = aws_api_gateway_rest_api.musclefit_api.root_resource_id
  http_method   = "ANY"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "root_any" {
  rest_api_id             = aws_api_gateway_rest_api.musclefit_api.id
  resource_id             = aws_api_gateway_rest_api.musclefit_api.root_resource_id
  http_method             = aws_api_gateway_method.root_any.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.musclefit_api_core.invoke_arn
}

resource "aws_lambda_permission" "apigw_invoke_api_core" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.musclefit_api_core.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.musclefit_api.execution_arn}/*/*"
}

# ---------------------------------------------------------------------------
# Rotas dos motores deterministicos (Lambda 2, musclefit-engines) - ver
# docs/aws-migration-plan.md e infra/lambda_engines.tf.
#
# O catch-all "{proxy+}" acima continua existindo e continua apontando para
# musclefit-api-core (nao mudou) - ele so deixa de ser o resolvido quando uma
# rota MAIS ESPECIFICA existir no mesmo path, que e exatamente o que os
# resources abaixo criam. Qualquer path que NAO tenha um resource explicito
# aqui (ex.: /exercises/history, /analytics/muscle-groups-mapped-table - este
# ultimo de app/muscle_groups_mapped.py, que NAO faz parte da extracao da
# Lambda 2, ver docs/aws-migration-plan.md) continua caindo no catch-all
# acima e sendo servido por musclefit-api-core, sem nenhuma mudanca.
#
# Por isso NAO existe um "/analytics/{proxy+}" generico apontando para
# musclefit-engines: isso capturaria TAMBEM os 4 paths de
# muscle_groups_mapped.py (que ficam em musclefit-api-core) e quebraria
# esses 4 endpoints. Cada path movido e enumerado explicitamente.

locals {
  # Paths de app/analytics.py + app/dashboard_modules.py +
  # app/dashboard_intelligence.py (todos com prefixo /analytics, mesmo
  # comportamento/mesmos paths de antes) - ver app/main_engines.py.
  engines_analytics_path_parts = [
    "summary",
    "training-trend",
    "exercise-progress",
    "rpe-distribution",
    "cardio",
    "muscle-groups-equivalent-sets-by-activity",
    "muscle-activation-by-activity",
    "muscle-groups-recent-exercise-volume",
    "muscle-groups-slot-comparison",
    "muscle-groups-daily-activation",
    "muscle-activation-preview",
    "muscle-activation-breakdown",
    "dashboard-muscle-status",
    "dashboard-muscle-drilldown",
    "dashboard-progression",
    "dashboard-progression-trend",
    "dashboard-progression-suggestion",
    "dashboard-progression-by-muscle-group",
    "dashboard-workout-sessions",
    "dashboard-recovery",
    "dashboard-cycle",
    "dashboard-stimulus-fatigue",
    "health", # /analytics/health (analytics.py) - health check do proprio router, NAO o /health da Lambda
    "body-composition-trend",
    "training-volume",
    "training-volume-by-split-day",
    "muscle-recovery",
    "muscle-recovery-consecutive-check",
    "muscle-group-body-map",
    "training-evolution",
    "cardio-agent-context",
    "methodology-realized", # M76 Lote 4b (E6): metodologia do realizado em 7 dias
    # Novo Dashboard (app/analytics_dashboard.py, plano secao 10): o unico
    # path com SEGMENTO DINAMICO no meio
    # (/analytics/dashboard/exercises/<exercise_id>/progress). O resource
    # dashboard abaixo e so o no pai; quem roteia as sub-rotas e o recurso
    # proxy declarado logo depois - sem ele, so /analytics/dashboard (exato)
    # chegaria a musclefit-engines e as sub-rotas cairiam no catch-all de
    # musclefit-api-core (404). Sem aspas duplas neste comentario: o parser
    # de tests/support/infra_engines_routes.py le TODA string entre aspas
    # dentro deste local.
    "dashboard",
  ]

  # Paths de app/engines_extra.py (movidos de main.py) - NAO tem prefixo
  # /analytics, entao precisam de um resource pai "/exercises" proprio
  # (compartilhado so como no de path - "/exercises/history",
  # "/exercises/logs" etc. continuam sem resource explicito e caem no
  # catch-all de musclefit-api-core normalmente).
  engines_exercise_path_parts = [
    "suggested-progression",
    "suggested-set-count-for-substitution",
  ]
}

resource "aws_api_gateway_resource" "analytics" {
  rest_api_id = aws_api_gateway_rest_api.musclefit_api.id
  parent_id   = aws_api_gateway_rest_api.musclefit_api.root_resource_id
  path_part   = "analytics"
}

resource "aws_api_gateway_resource" "engines_analytics_path" {
  for_each = toset(local.engines_analytics_path_parts)

  rest_api_id = aws_api_gateway_rest_api.musclefit_api.id
  parent_id   = aws_api_gateway_resource.analytics.id
  path_part   = each.value
}

# Sub-rotas do Novo Dashboard: tudo abaixo de /analytics/dashboard/ vai para
# musclefit-engines. Nao existe rota de outra Lambda sob esse prefixo (as v2
# sao "/analytics/dashboard-*", com HIFEN, resources irmaos), entao o
# "{proxy+}" aqui e seguro - ao contrario de um "/analytics/{proxy+}", que
# capturaria os paths de app/muscle_groups_mapped.py.
resource "aws_api_gateway_resource" "engines_dashboard_proxy" {
  rest_api_id = aws_api_gateway_rest_api.musclefit_api.id
  parent_id   = aws_api_gateway_resource.engines_analytics_path["dashboard"].id
  path_part   = "{proxy+}"
}

resource "aws_api_gateway_method" "engines_dashboard_proxy_any" {
  rest_api_id      = aws_api_gateway_rest_api.musclefit_api.id
  resource_id      = aws_api_gateway_resource.engines_dashboard_proxy.id
  http_method      = "ANY"
  authorization    = "NONE" # mesmo padrao do restante da API - autenticacao via X-API-Token/cookie (security.py)
  api_key_required = false
}

resource "aws_api_gateway_integration" "engines_dashboard_proxy_any" {
  rest_api_id             = aws_api_gateway_rest_api.musclefit_api.id
  resource_id             = aws_api_gateway_resource.engines_dashboard_proxy.id
  http_method             = aws_api_gateway_method.engines_dashboard_proxy_any.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.musclefit_engines.invoke_arn
}

resource "aws_api_gateway_method" "engines_analytics_any" {
  for_each = aws_api_gateway_resource.engines_analytics_path

  rest_api_id      = aws_api_gateway_rest_api.musclefit_api.id
  resource_id      = each.value.id
  http_method      = "ANY"
  authorization    = "NONE" # mesmo padrao do restante da API - autenticacao via X-API-Token (security.py)
  api_key_required = false
}

resource "aws_api_gateway_integration" "engines_analytics_any" {
  for_each = aws_api_gateway_method.engines_analytics_any

  rest_api_id             = aws_api_gateway_rest_api.musclefit_api.id
  resource_id             = aws_api_gateway_resource.engines_analytics_path[each.key].id
  http_method             = each.value.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.musclefit_engines.invoke_arn
}

resource "aws_api_gateway_resource" "exercises" {
  rest_api_id = aws_api_gateway_rest_api.musclefit_api.id
  parent_id   = aws_api_gateway_rest_api.musclefit_api.root_resource_id
  path_part   = "exercises"
}

resource "aws_api_gateway_resource" "engines_exercise_path" {
  for_each = toset(local.engines_exercise_path_parts)

  rest_api_id = aws_api_gateway_rest_api.musclefit_api.id
  parent_id   = aws_api_gateway_resource.exercises.id
  path_part   = each.value
}

resource "aws_api_gateway_method" "engines_exercise_any" {
  for_each = aws_api_gateway_resource.engines_exercise_path

  rest_api_id      = aws_api_gateway_rest_api.musclefit_api.id
  resource_id      = each.value.id
  http_method      = "ANY"
  authorization    = "NONE"
  api_key_required = false
}

resource "aws_api_gateway_integration" "engines_exercise_any" {
  for_each = aws_api_gateway_method.engines_exercise_any

  rest_api_id             = aws_api_gateway_rest_api.musclefit_api.id
  resource_id             = aws_api_gateway_resource.engines_exercise_path[each.key].id
  http_method             = each.value.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.musclefit_engines.invoke_arn
}

resource "aws_lambda_permission" "apigw_invoke_engines" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.musclefit_engines.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.musclefit_api.execution_arn}/*/*"
}

resource "aws_api_gateway_deployment" "musclefit_api" {
  rest_api_id = aws_api_gateway_rest_api.musclefit_api.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_rest_api.musclefit_api.binary_media_types,
      aws_api_gateway_rest_api.musclefit_api.disable_execute_api_endpoint,
      aws_api_gateway_resource.proxy.id,
      aws_api_gateway_method.proxy_any.id,
      aws_api_gateway_integration.proxy_any.id,
      aws_api_gateway_method.root_any.id,
      aws_api_gateway_integration.root_any.id,
      aws_api_gateway_resource.analytics.id,
      [for r in aws_api_gateway_resource.engines_analytics_path : r.id],
      [for m in aws_api_gateway_method.engines_analytics_any : m.id],
      [for i in aws_api_gateway_integration.engines_analytics_any : i.id],
      aws_api_gateway_resource.engines_dashboard_proxy.id,
      aws_api_gateway_method.engines_dashboard_proxy_any.id,
      aws_api_gateway_integration.engines_dashboard_proxy_any.id,
      aws_api_gateway_resource.exercises.id,
      [for r in aws_api_gateway_resource.engines_exercise_path : r.id],
      [for m in aws_api_gateway_method.engines_exercise_any : m.id],
      [for i in aws_api_gateway_integration.engines_exercise_any : i.id],
      # SEG-05 (access log): formato do log de acesso e role de CloudWatch da
      # conta. Um deployment novo (create_before_destroy) com o stage apontando
      # para ele a cada troca de um dos dois. Em 2026-10-11 o access log foi
      # ligado e nada gravou; junto com a trust sem condicao
      # (infra/api_gateway_borda.tf), este redeploy garante que o plano de dados
      # pegue a configuracao de log com a role ja valida.
      jsonencode(local.apigw_access_log_format),
      aws_api_gateway_account.musclefit.cloudwatch_role_arn,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.proxy_any,
    aws_api_gateway_integration.root_any,
    aws_api_gateway_integration.engines_analytics_any,
    aws_api_gateway_integration.engines_dashboard_proxy_any,
    aws_api_gateway_integration.engines_exercise_any,
  ]
}

resource "aws_api_gateway_stage" "staging" {
  rest_api_id   = aws_api_gateway_rest_api.musclefit_api.id
  deployment_id = aws_api_gateway_deployment.musclefit_api.id
  stage_name    = "staging"

  # AWS-08: log de acesso (uma linha JSON por requisicao, sem cabecalho, corpo
  # nem token) - formato e log group em infra/api_gateway_borda.tf. Exige o
  # role de CloudWatch na conta (aws_api_gateway_account), dai o depends_on.
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.apigw_access.arn
    format          = jsonencode(local.apigw_access_log_format)
  }

  depends_on = [aws_api_gateway_account.musclefit]
}

# Dominio customizado (2026-09-04): staging validado (Fase 3 completa,
# todos os fluxos testados), decisao explicita de antecipar o mapeamento do
# alias reservado `api.musclefit.com.br` (Fase 0, item 0.5) para este API
# Gateway agora, em vez de esperar a Fase 5. DNS: ALIAS A em infra/route53.tf
# (M44) para regional_domain_name/regional_zone_id deste recurso. Desde a
# delegacao dos NS ao Route 53 (2026-10-03), e esse ALIAS que vale; o CNAME
# manual que existia no Registro.br deixou de ser consultado.
resource "aws_api_gateway_domain_name" "api" {
  domain_name              = "api.musclefit.com.br"
  regional_certificate_arn = data.aws_acm_certificate.api.arn

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

resource "aws_api_gateway_base_path_mapping" "api" {
  api_id      = aws_api_gateway_rest_api.musclefit_api.id
  stage_name  = aws_api_gateway_stage.staging.stage_name
  domain_name = aws_api_gateway_domain_name.api.domain_name
}

output "api_gateway_invoke_url" {
  description = "URL de invocacao direta do stage staging. Com disable_execute_api_endpoint = true (SEG-05) ela responde 403: use https://api.musclefit.com.br."
  value       = aws_api_gateway_stage.staging.invoke_url
}

output "api_custom_domain_target" {
  description = "Dominio regional do API Gateway para api.musclefit.com.br. Informativo: e o alvo do ALIAS A de api em route53.tf (zona no Route 53 desde 2026-10-03, M44)."
  value       = aws_api_gateway_domain_name.api.regional_domain_name
}

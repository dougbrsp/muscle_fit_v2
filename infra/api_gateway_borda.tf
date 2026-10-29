# Borda da API (SEG-05 + AWS-08 + AWS-10, relatorio
# auditoria de 2026-10-08, arquivo privado):
#   1. throttling do stage (method settings "*/*");
#   2. log de acesso do API Gateway + log de execucao so de ERROR, 14 dias;
#   3. WAF regional (WAFv2 REGIONAL, sa-east-1) associado ao stage `staging`;
#   4. o endpoint execute-api padrao e desligado em infra/api_gateway.tf.
#
# Limiares medidos em producao (so leitura, 2026-10-11, janela de 14 dias,
# metricas AWS/ApiGateway + access log do uvicorn em /aws/lambda/musclefit-*):
#   - 16.304 requisicoes em 14 dias (~1.165/dia, ~35 mil/mes);
#   - pico legitimo por minuto: 60 (todas as origens somadas); por IP em 5 min:
#     115 (179.113.167.154, 2026-10-04 12:45 UTC); o resto fica abaixo de 85;
#   - pico total por minuto: 243 em 2026-10-07 13:00 UTC = UM scanner
#     (34.3.105.156, GCP) sondando /firebase-key.json, /.env, /keyfile.json:
#     191 req/5 min, todas 404;
#   - POST /auth/login: 42 em 14 dias; /auth/password-reset/request: 12;
#     por IP, nenhum POST de /auth/* passou de 2 em 5 min. GET /auth/me (chamado
#     a cada navegacao) chega a 19/5 min por IP, por isso a regra de auth so
#     conta POST.

locals {
  # --- throttling do stage (AWS-08) -----------------------------------------
  # Media do pico legitimo: 60 req/min = 1 req/s. Uma abertura de tela do app
  # dispara ate ~20-30 chamadas em paralelo (dashboard), dai o burst de 60.
  # 30 req/s sustentados e 30x o pico medido e ainda fica abaixo do que as
  # 10 execucoes Lambda da conta (AWS-01) aguentam com ~0,3 s por chamada:
  # acima disso o API Gateway devolve 429 antes de a Lambda ser estrangulada.
  apigw_throttle_rate_limit  = 30
  apigw_throttle_burst_limit = 60

  # --- WAF: limite por IP (rate-based, janela de 5 min) ---------------------
  # Geral: 500/5 min por IP = 4,3x o maior pico legitimo por IP (115). Bloqueia
  # rajada/flood de um IP so (o que esgota a concorrencia Lambda, AWS-01) sem
  # tocar o Modo Treino. O scanner de 07/10 (191/5 min) NAO e alvo desta regra.
  waf_rate_limit_geral_5min = 500
  # Auth: POST em /auth/* (login, logout, change-password, password-reset) e
  # /devices/pair. Legitimo: <= 2 por IP em 5 min. 20 cobre um usuario errando
  # a senha varias vezes e corta forca bruta (bcrypt ~0,3 s por tentativa
  # consome concorrencia Lambda). O minimo aceito pelo WAF e 10.
  waf_rate_limit_auth_5min = 20

  # Retencao: igual aos log groups das Lambdas (14 dias). O access log tem IP
  # do cliente (dado pessoal, LGPD): nao guardar alem do necessario.
  apigw_log_retention_days = 14

  # Uma linha JSON por requisicao. Sem cabecalhos, sem corpo, sem query string
  # (path de recurso + path real sem query): nada de X-API-Token, cookie ou
  # dado de treino no log. domainName prova quem ainda usa o host execute-api;
  # wafResponseCode mostra o que o WAF bloqueou.
  apigw_access_log_format = {
    requestId          = "$context.requestId"
    requestTime        = "$context.requestTime"
    ip                 = "$context.identity.sourceIp"
    domainName         = "$context.domainName"
    httpMethod         = "$context.httpMethod"
    resourcePath       = "$context.resourcePath"
    path               = "$context.path"
    status             = "$context.status"
    protocol           = "$context.protocol"
    responseLength     = "$context.responseLength"
    responseLatency    = "$context.responseLatency"
    integrationLatency = "$context.integrationLatency"
    integrationStatus  = "$context.integrationStatus"
    errorType          = "$context.error.responseType"
    wafResponseCode    = "$context.wafResponseCode"
    userAgent          = "$context.identity.userAgent"
  }
}

# ---------------------------------------------------------------------------
# Logs (AWS-08)
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "apigw_access" {
  name              = "/aws/apigateway/musclefit-api-access"
  retention_in_days = local.apigw_log_retention_days
}

# Log de EXECUCAO (logging_level = ERROR): o API Gateway cria este grupo
# sozinho, com retencao infinita, se ele nao existir. Criado aqui ANTES do
# method settings (depends_on) para nascer com 14 dias e ficar no Terraform.
resource "aws_cloudwatch_log_group" "apigw_execution" {
  name              = "API-Gateway-Execution-Logs_${aws_api_gateway_rest_api.musclefit_api.id}/staging"
  retention_in_days = local.apigw_log_retention_days
}

# Role que o API Gateway usa para escrever os dois grupos acima. E uma
# configuracao da CONTA na regiao (so existe esta REST API em sa-east-1).
# Nome no padrao musclefit-*-exec, que a role do pipeline ja pode criar.
#
# Trust SEM condicao, igual aos exemplos oficiais da AWS (AWS::ApiGateway::Account
# no CloudFormation User Guide; "Permissions for CloudWatch logging" no API
# Gateway Developer Guide). A primeira versao (SEG-05, 2026-10-11) tinha
# `aws:SourceAccount`, e o access log nao gravou NADA com trafego real: no
# CloudTrail so houve AssumeRole da sessao BackplaneAssumeRoleSession (plano de
# controle, que manda aws:SourceAccount: sonda welcome e validacao do stage, ate
# 14:58:37Z) e nenhum AssumeRole/CreateLogStream da entrega dos logs; o
# RoleLastUsed ficou parado em 14:58:37Z. A AWS nao documenta suporte a
# aws:SourceAccount/aws:SourceArn para esta role, e a entrega dos logs pode
# assumir a role sem essa chave. Quem pode assumir continua sendo so o servico
# apigateway.amazonaws.com, e so para escrever nos tres grupos da inline abaixo.
# Mudar a trust de role existente exige iam:UpdateAssumeRolePolicy na role de
# deploy (v4 da musclefit-gha-deploy-v2-iam, so neste ARN).
resource "aws_iam_role" "apigw_cloudwatch" {
  name = "musclefit-apigw-cloudwatch-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "apigateway.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Grupo-sonda do API Gateway. Ao gravar cloudwatchRoleArn (UpdateAccount), o
# API Gateway assume a role (sessao BackplaneAssumeRoleSession) e chama
# logs:CreateLogGroup em /aws/apigateway/welcome; se der AccessDenied, devolve
# 400 "The role ARN does not have required permissions configured". Foi a
# causa da falha do apply de 2026-10-11 (CloudTrail sa-east-1, 14:42:45 e
# 14:44:28 UTC; o AssumeRole do plano de controle passou, mas isso nao prova
# que a entrega dos logs consiga assumir a role: ver a trust acima). O grupo NAO e gerenciado aqui: a role do pipeline so cria log
# group em /aws/apigateway/musclefit-* (musclefit-gha-deploy-v2-compute), e
# quem cria este e o proprio API Gateway, com uma unica mensagem de boas-vindas
# (custo desprezivel, mesmo com retencao infinita).
locals {
  apigw_welcome_log_group_arn = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/apigateway/welcome"
}

# Inline e escopada aos tres grupos (acesso, execucao e a sonda welcome), em
# vez da managed AmazonAPIGatewayPushToCloudWatchLogs (escrita e leitura,
# inclusive GetLogEvents/FilterLogEvents, em qualquer log group).
resource "aws_iam_role_policy" "apigw_cloudwatch" {
  name = "musclefit-apigw-cloudwatch-logs"
  role = aws_iam_role.apigw_cloudwatch.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:DescribeLogStreams",
          "logs:PutLogEvents",
        ]
        Resource = [
          aws_cloudwatch_log_group.apigw_access.arn,
          "${aws_cloudwatch_log_group.apigw_access.arn}:*",
          aws_cloudwatch_log_group.apigw_execution.arn,
          "${aws_cloudwatch_log_group.apigw_execution.arn}:*",
          local.apigw_welcome_log_group_arn,
          "${local.apigw_welcome_log_group_arn}:*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = "logs:DescribeLogGroups"
        Resource = "*"
      },
    ]
  })
}

resource "aws_api_gateway_account" "musclefit" {
  cloudwatch_role_arn = aws_iam_role.apigw_cloudwatch.arn
  # No destroy, volta a conta ao estado de hoje (sem role de CloudWatch).
  reset_on_delete = true

  depends_on = [aws_iam_role_policy.apigw_cloudwatch]
}

# ---------------------------------------------------------------------------
# Throttling + log de execucao (AWS-08)
# ---------------------------------------------------------------------------

resource "aws_api_gateway_method_settings" "all" {
  rest_api_id = aws_api_gateway_rest_api.musclefit_api.id
  stage_name  = aws_api_gateway_stage.staging.stage_name
  method_path = "*/*"

  settings {
    throttling_rate_limit  = local.apigw_throttle_rate_limit
    throttling_burst_limit = local.apigw_throttle_burst_limit
    logging_level          = "ERROR"
    # Sem metricas detalhadas por metodo (custo de metrica custom) e sem data
    # trace (gravaria corpo e cabecalhos, inclusive token e dados de treino).
    metrics_enabled    = false
    data_trace_enabled = false
  }

  depends_on = [
    aws_api_gateway_account.musclefit,
    aws_cloudwatch_log_group.apigw_execution,
  ]
}

# ---------------------------------------------------------------------------
# WAF regional (AWS-10 / SEG-05)
# ---------------------------------------------------------------------------
#
# Custo (sa-east-1, preco de lista do WAF): Web ACL US$ 5,00 + 3 regras x
# US$ 1,00 + US$ 0,60 por milhao de requisicoes (~35 mil/mes = US$ 0,02).
# Total ~US$ 8,02/mes. CRS usa 700 WCU, bem abaixo de 1.500 (acima disso ha
# cobranca extra). Sem logging do WAF (custo por GB): a visibilidade vem das
# metricas por regra/rotulo no CloudWatch, das amostras (sampled requests, 3 h)
# e do campo wafResponseCode do access log.
#
# Fora de proposito: AWSManagedRulesKnownBadInputsRuleSet (+US$ 1/mes) foca
# Log4j/desserializacao Java/PROPFIND; o backend e Python/FastAPI. A semana do
# CRS em Count (11 a 18/10) nao mostrou sondagem que ele pegaria; reavaliar se
# os bloqueios do CRS mostrarem.

resource "aws_wafv2_web_acl" "api" {
  name = "musclefit-api"
  # A description do Web ACL so aceita ^[\w+=:#@/\-,\.][\w+=:#@/\-,\.\s]+[\w+=:#@/\-,\.]$
  # (sem parenteses, aspas, acentos): o apply de 2026-10-11 falhou por isso.
  description = "Borda da REST API musclefit-api em api.musclefit.com.br: limite por IP e CRS bloqueando, com SizeRestrictions_BODY e NoUserAgent_HEADER em Count."
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  # 1) Forca bruta / enumeracao: POST em /auth/* e /devices/pair, por IP.
  #    Bloqueia com 429 (a resposta do WAF nao tem cabecalho CORS: no navegador
  #    aparece como erro de rede, o que e aceitavel para quem passou de 20
  #    tentativas em 5 min).
  rule {
    name     = "limite-auth-por-ip"
    priority = 10

    action {
      block {
        custom_response {
          response_code = 429
        }
      }
    }

    statement {
      rate_based_statement {
        limit                 = local.waf_rate_limit_auth_5min
        aggregate_key_type    = "IP"
        evaluation_window_sec = 300

        scope_down_statement {
          and_statement {
            statement {
              byte_match_statement {
                search_string         = "POST"
                positional_constraint = "EXACTLY"
                field_to_match {
                  method {}
                }
                text_transformation {
                  priority = 0
                  type     = "NONE"
                }
              }
            }
            statement {
              or_statement {
                statement {
                  byte_match_statement {
                    search_string         = "/auth/"
                    positional_constraint = "STARTS_WITH"
                    field_to_match {
                      uri_path {}
                    }
                    text_transformation {
                      priority = 0
                      type     = "LOWERCASE"
                    }
                  }
                }
                statement {
                  byte_match_statement {
                    search_string         = "/devices/pair"
                    positional_constraint = "STARTS_WITH"
                    field_to_match {
                      uri_path {}
                    }
                    text_transformation {
                      priority = 0
                      type     = "LOWERCASE"
                    }
                  }
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "musclefit-api-limite-auth"
      sampled_requests_enabled   = true
    }
  }

  # 2) Flood de um IP so, qualquer rota (inclusive /ready e /health anonimos,
  #    que acordam o Aurora / ocupam concorrencia).
  rule {
    name     = "limite-geral-por-ip"
    priority = 20

    action {
      block {
        custom_response {
          response_code = 429
        }
      }
    }

    statement {
      rate_based_statement {
        limit                 = local.waf_rate_limit_geral_5min
        aggregate_key_type    = "IP"
        evaluation_window_sec = 300
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "musclefit-api-limite-geral"
      sampled_requests_enabled   = true
    }
  }

  # 3) Core Rule Set BLOQUEANDO (override_action none), com 2 sub-regras em
  #    Count (rule_action_override). Decisao do usuario em 2026-10-18.
  #
  #    Medido em producao, so leitura, de 2026-10-11 a 2026-10-18, com o CRS
  #    inteiro em Count (metricas AWS/WAFV2 por ManagedRuleGroupRule + access
  #    log do API Gateway):
  #    - ~6.500 requisicoes; nenhuma bloqueada por regra nenhuma;
  #    - o CRS contou 772, TODAS em SizeRestrictions_BODY. Batem dia a dia com
  #      POST /workout-plan-templates/preview-activation, POST
  #      /workout-plan-templates/methodology-volume-preview e PUT
  #      /workout-plan-templates/{id}/draft: o JSON da grade do editor de
  #      Modelo passa de 8 KB;
  #    - nenhuma outra sub-regra casou: nem existe metrica delas no CloudWatch;
  #    - nenhuma requisicao chegou sem User-Agent, mas o fluxo do Zepp nao
  #      rodou na semana;
  #    - nenhum upload de foto ou logo (POST .../photo, .../logo) desde 04/10:
  #      o multipart binario nao passou pelo CRS na janela.
  #
  #    Ficam em Count (rotulam e seguem, nao bloqueiam):
  #    - SizeRestrictions_BODY (corpo > 8 KB): bloquear quebraria o editor de
  #      Modelo; tambem casa upload multipart de foto (gyms.py/users.py, ate
  #      8 MB, read_limited_photo devolve 413 acima disso), importacao de
  #      treino/Zepp, pacotes do dashboard e a foto do rotulo (POST
  #      /nutrition/foods/label-photo, nutricao L6: JSON com a foto em base64
  #      SEM padding, ate ~2 MB - o alfabeto do base64 nao forma as assinaturas
  #      XSS/LFI/RFI/SSRF do corpo, que bytes de JPEG crus poderiam formar).
  #    - NoUserAgent_HEADER: o side service do Zepp (app-side/index.js) faz
  #      fetch sem User-Agent garantido e nao foi exercitado na janela; a
  #      sub-regra protege pouco (qualquer cliente manda um User-Agent falso).
  #
  #    Passam a bloquear com 403 (sem cabecalho CORS: no navegador aparece
  #    como erro de rede): CrossSiteScripting_*, GenericLFI_*, GenericRFI_*,
  #    EC2MetaDataSSRF_*, UserAgent_BadBots_HEADER, RestrictedExtensions_*,
  #    SizeRestrictions_QUERYSTRING (> 2 KB), _URIPATH (> 1 KB) e
  #    _Cookie_HEADER (> 10 KB). O CRS nao tem regra de SQLi.
  #
  #    O que observar depois do deploy (primeiros 7 dias):
  #    - CloudWatch AWS/WAFV2 BlockedRequests com WebACL=musclefit-api,
  #      ManagedRuleGroup=AWSManagedRulesCommonRuleSet e ManagedRuleGroupRule
  #      por sub-regra; SizeRestrictions_BODY passa a sair em CountedRequests;
  #    - no access log /aws/apigateway/musclefit-api-access, status 403 com
  #      wafResponseCode diferente de WAF_ALLOW: conferir path e userAgent;
  #    - o alarme musclefit-waf-api-blocked (Rule=ALL) agora soma o CRS; um
  #      falso positivo isolado de usuario NAO chega a 20 em 5 min e so
  #      aparece na consulta ao access log (wafResponseCode = WAF_BLOCK).
  #
  #    Versao do CRS sem `version` fixada, de proposito: segue a versao padrao
  #    da AWS (Version_1.23 em 2026-10-18, a da medicao). Quando a AWS troca
  #    a padrao, sub-regra nova ou alterada passa a bloquear sem PR. Fixar
  #    `version` aqui evita isso, ao custo de atualizar quando a versao
  #    expirar.
  #
  #    Reverter: voltar override_action para count {}; os rule_action_override
  #    podem ficar, sem efeito com o grupo em Count. Para soltar so uma
  #    sub-regra que casar trafego legitimo, acrescentar outro
  #    rule_action_override com action_to_use count para ela.
  rule {
    name     = "aws-crs"
    priority = 30

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"

        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {}
          }
        }

        rule_action_override {
          name = "NoUserAgent_HEADER"
          action_to_use {
            count {}
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "musclefit-api-aws-crs"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "musclefit-api"
    sampled_requests_enabled   = true
  }
}

# Associacao do Web ACL ao stage `staging`.
#
# Endereco `api_stage` (era `api`): o apply do SEG-05 (run 38149166830 do
# "Deploy producao") ASSOCIOU o ACL ao stage, mas falhou no read-after-create
# (`wafv2:GetWebACLForResource` sem permissao no ARN generico
# `regional/webacl/*/*`, corrigido na v3 da musclefit-gha-deploy-v2-rede-dados).
# O Terraform marcou `aws_wafv2_web_acl_association.api` como TAINTED, e um
# objeto tainted vira replace (-/+) no proximo apply: DisassociateWebACL +
# AssociateWebACL. Alem de deixar a API alguns segundos sem WAF, o Delete
# falharia (a role de deploy nao tem `wafv2:DisassociateWebACL`).
# Para nao tocar na AWS, sem `terraform untaint` manual:
#   - `removed` + `destroy = false`: o endereco antigo (tainted) sai do state
#     sem chamar a AWS (so "esquece");
#   - `import` no endereco novo: le a associacao que ja existe
#     (GetWebACLForResource) e grava no state como objeto normal.
# `moved` nao serve: preserva o status tainted. Plan esperado: 1 import,
# 1 "will no longer be managed", 0 to add/change/destroy nesta associacao.
# Depois do apply em producao os dois blocos viram no-op; podem sair num PR
# futuro. Exige a v3 da rede-dados (deploy) e a v5 da plan-leitura (plan)
# publicadas ANTES do merge.
removed {
  from = aws_wafv2_web_acl_association.api

  lifecycle {
    destroy = false
  }
}

import {
  to = aws_wafv2_web_acl_association.api_stage
  id = "${aws_wafv2_web_acl.api.arn},${aws_api_gateway_stage.staging.arn}"
}

resource "aws_wafv2_web_acl_association" "api_stage" {
  resource_arn = aws_api_gateway_stage.staging.arn
  web_acl_arn  = aws_wafv2_web_acl.api.arn
}

# Aviso quando o WAF bloquear de verdade: regras de limite por IP e, pela
# decisao de 2026-10-18, o CRS. Rule=ALL soma as duas fontes. 1 bloqueio
# isolado nao alarma; 20 em 5 min e rajada, forca bruta ou sondagem.
resource "aws_cloudwatch_metric_alarm" "waf_api_blocked" {
  alarm_name          = "musclefit-waf-api-blocked"
  alarm_description   = "WAF da API bloqueou >= 20 requisicoes em 5 min: limite por IP ou CRS. Ver BlockedRequests por Rule e por ManagedRuleGroupRule, amostras no console do WAF e wafResponseCode no access log."
  namespace           = "AWS/WAFV2"
  metric_name         = "BlockedRequests"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 20
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    WebACL = aws_wafv2_web_acl.api.name
    Region = "sa-east-1"
    Rule   = "ALL"
  }
}

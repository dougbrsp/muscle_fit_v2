# AWS-06 (varredura 2026-10-08): alarmes e SNS. Antes nao existia nenhum alarme
# (7,3% de 5XX e 33 erros do intervals-sync passaram sem aviso).

resource "aws_sns_topic" "alerts" {
  name = "musclefit-alerts"
}

# O assinante recebe um e-mail de confirmacao apos o apply e precisa clicar no link;
# ate la nenhum alerta chega.
resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alerts_email
}

resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "CloudWatchAlarmsPublish"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      },
      {
        Sid       = "BudgetsPublish"
        Effect    = "Allow"
        Principal = { Service = "budgets.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      },
      # Alerta de uso do root (alerta_root.tf): so as duas regras musclefit-root-*.
      {
        Sid       = "EventBridgeRootActivityPublish"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = [
              aws_cloudwatch_event_rule.root_signin.arn,
              aws_cloudwatch_event_rule.root_api.arn,
            ]
          }
        }
      },
    ]
  })
}

# 1) Throttles da api-core. Ate 06/10/2026 a cota de concorrencia da conta era 10 e
#    havia throttles diarios (12-74/dia); desde 07/10 a cota esta em 1.001 (AWS-01) e
#    nao houve mais throttle. Com a concorrencia reservada (var.lambda_reserved_concurrency,
#    AWS-01 item 2), a api-core deixa de disputar o pool da conta: throttle passa a
#    significar "estourou o teto DA FUNCAO" (rajada real ou abuso). Resposta: investigar
#    a origem antes de subir o teto em variables.tf.
resource "aws_cloudwatch_metric_alarm" "api_core_throttles" {
  alarm_name          = "musclefit-api-core-throttles"
  alarm_description   = "Lambda musclefit-api-core com throttling: atingiu a concorrencia reservada da funcao (var.lambda_reserved_concurrency) ou, antes dela, a cota da conta. Investigar origem (rajada/abuso) antes de subir o teto."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    FunctionName = aws_lambda_function.musclefit_api_core.function_name
  }
}

# 1b) Throttles das outras funcoes com reserva (engines + as 3 de IA), somados em um
#     alarme so: com reserva, throttle = a funcao estourou o PROPRIO teto. Para as 3 de
#     IA (InvocationType=Event) o invoke throttled vai para a fila da AWS sem rodar e o
#     job pode virar worker_lost (M73) - por isso ha folga nos tetos delas. intervals-sync
#     e migrations ficam fora: reserva 1 e serializacao intencional, throttle la e esperado.
#     Resposta: ver qual funcao (Throttles por FunctionName) e, se a demanda for legitima,
#     subir o teto no map em variables.tf.
resource "aws_cloudwatch_metric_alarm" "lambda_throttles_reservadas" {
  alarm_name          = "musclefit-lambda-throttles-reservadas"
  alarm_description   = "Alguma Lambda com reserva (engines, plan-generator, cardio-agent, workout-ai) estourou o proprio teto de concorrencia reservada (var.lambda_reserved_concurrency). Ver Throttles por FunctionName; subir o teto e mudar o map em variables.tf."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  metric_query {
    id          = "total"
    expression  = "SUM([engines, plan_generator, cardio_agent, workout_ai])"
    label       = "Throttles das funcoes com reserva"
    return_data = true
  }

  metric_query {
    id = "engines"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Throttles"
      period      = 300
      stat        = "Sum"
      dimensions = {
        FunctionName = aws_lambda_function.musclefit_engines.function_name
      }
    }
  }

  metric_query {
    id = "plan_generator"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Throttles"
      period      = 300
      stat        = "Sum"
      dimensions = {
        FunctionName = aws_lambda_function.musclefit_plan_generator.function_name
      }
    }
  }

  metric_query {
    id = "cardio_agent"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Throttles"
      period      = 300
      stat        = "Sum"
      dimensions = {
        FunctionName = aws_lambda_function.musclefit_cardio_agent.function_name
      }
    }
  }

  metric_query {
    id = "workout_ai"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Throttles"
      period      = 300
      stat        = "Sum"
      dimensions = {
        FunctionName = aws_lambda_function.musclefit_workout_ai.function_name
      }
    }
  }
}

# 2) Erros das Lambdas de IA (um alarme por funcao: o alerta ja diz qual).
locals {
  ai_lambda_names = {
    plan_generator = aws_lambda_function.musclefit_plan_generator.function_name
    cardio_agent   = aws_lambda_function.musclefit_cardio_agent.function_name
    workout_ai     = aws_lambda_function.musclefit_workout_ai.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "ai_lambda_errors" {
  for_each = local.ai_lambda_names

  alarm_name          = "${each.value}-errors"
  alarm_description   = "Erros na Lambda ${each.value} (geracao por IA, invocacao assincrona sem retry - M70)."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    FunctionName = each.value
  }
}

# 3) intervals-sync (EventBridge, roda de tempos em tempos): janela de 1 h.
resource "aws_cloudwatch_metric_alarm" "intervals_sync_errors" {
  alarm_name          = "musclefit-intervals-sync-errors"
  alarm_description   = "Erros no intervals-sync (33 erros em 09-20 a 09-30 passaram sem aviso)."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    FunctionName = aws_lambda_function.musclefit_intervals_sync.function_name
  }
}

# 4) 5XX do API Gateway como taxa (5xx/Count), so avaliada com >= 20 requisicoes em
#    15 min para nao alarmar por 1 erro em trafego minimo.
resource "aws_cloudwatch_metric_alarm" "apigw_5xx" {
  alarm_name          = "musclefit-apigw-5xx"
  alarm_description   = "Taxa de 5XX do API Gateway > 5% em 15 min (com pelo menos 20 requisicoes)."
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0.05
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  metric_query {
    id          = "taxa"
    expression  = "IF(contagem >= 20, erros / contagem, 0)"
    label       = "Taxa de 5XX"
    return_data = true
  }

  metric_query {
    id = "erros"
    metric {
      namespace   = "AWS/ApiGateway"
      metric_name = "5XXError"
      period      = 900
      stat        = "Sum"
      dimensions = {
        ApiName = aws_api_gateway_rest_api.musclefit_api.name
        Stage   = aws_api_gateway_stage.staging.stage_name
      }
    }
  }

  metric_query {
    id = "contagem"
    metric {
      namespace   = "AWS/ApiGateway"
      metric_name = "Count"
      period      = 900
      stat        = "Sum"
      dimensions = {
        ApiName = aws_api_gateway_rest_api.musclefit_api.name
        Stage   = aws_api_gateway_stage.staging.stage_name
      }
    }
  }
}

# 5) Aurora no teto de 1 ACU por 60 min: sinal de que o max_capacity (1) limita a carga.
resource "aws_cloudwatch_metric_alarm" "aurora_at_max_capacity" {
  alarm_name          = "musclefit-aurora-at-max-capacity"
  alarm_description   = "Aurora Serverless v2 no teto de capacidade (1 ACU) por 60 min."
  namespace           = "AWS/RDS"
  metric_name         = "ServerlessDatabaseCapacity"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 12
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    DBClusterIdentifier = aws_rds_cluster.musclefit_db.cluster_identifier
  }
}

# 6) ACUUtilization alta por 30 min.
resource "aws_cloudwatch_metric_alarm" "aurora_acu_utilization" {
  alarm_name          = "musclefit-aurora-acu-utilization"
  alarm_description   = "Aurora com ACUUtilization > 90% por 30 min."
  namespace           = "AWS/RDS"
  metric_name         = "ACUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 6
  threshold           = 90
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    DBClusterIdentifier = aws_rds_cluster.musclefit_db.cluster_identifier
  }
}

# 7) CloudFront: as metricas ficam em us-east-1 (alias do provider, ver cloudfront.tf)
#    e a acao de um alarme tem de ser um topico SNS da MESMA regiao; por isso um
#    topico proprio em us-east-1, com o mesmo assinante.
resource "aws_sns_topic" "alerts_us_east_1" {
  provider = aws.us_east_1
  name     = "musclefit-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email_us_east_1" {
  provider  = aws.us_east_1
  topic_arn = aws_sns_topic.alerts_us_east_1.arn
  protocol  = "email"
  endpoint  = var.alerts_email
}

resource "aws_sns_topic_policy" "alerts_us_east_1" {
  provider = aws.us_east_1
  arn      = aws_sns_topic.alerts_us_east_1.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "CloudWatchAlarmsPublish"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.alerts_us_east_1.arn
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      },
      # Alerta de uso do root (alerta_root.tf): so as duas regras musclefit-root-* de
      # us-east-1 (os eventos de us-east-2/us-west-2 chegam encaminhados a elas).
      {
        Sid       = "EventBridgeRootActivityPublish"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.alerts_us_east_1.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = [
              aws_cloudwatch_event_rule.root_signin_us_east_1.arn,
              aws_cloudwatch_event_rule.root_api_us_east_1.arn,
            ]
          }
        }
      },
    ]
  })
}

resource "aws_cloudwatch_metric_alarm" "cloudfront_5xx" {
  provider = aws.us_east_1

  alarm_name          = "musclefit-cloudfront-5xx"
  alarm_description   = "Taxa de 5XX do CloudFront musclefit-cdn > 5% por 15 min."
  namespace           = "AWS/CloudFront"
  metric_name         = "5xxErrorRate"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts_us_east_1.arn]
  ok_actions          = [aws_sns_topic.alerts_us_east_1.arn]

  dimensions = {
    DistributionId = aws_cloudfront_distribution.musclefit_cdn.id
    Region         = "Global"
  }
}

# 8) TODO ses-bounce: Reputation.BounceRate (AWS/SES) nao e garantida sem
#    configuration set com reputation metrics, e o dominio hoje nao usa um.
#    Adicionar o alarme junto com o configuration set (outro PR).

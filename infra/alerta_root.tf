# Alerta por e-mail de QUALQUER uso do usuario root (CIS AWS Foundations 3.3/4.3).
# O acesso humano e via IAM Identity Center; o root so deve aparecer em emergencia,
# entao todo evento dele vira e-mail no topico musclefit-alerts.
#
# Onde os eventos chegam (CloudTrail User Guide, "AWS Management Console sign-in
# events"): o ConsoleLogin do root e registrado em us-east-1, us-east-2 OU us-west-2,
# nunca em sa-east-1. Na conta (LookupEvents, 2026-10-10) os ConsoleLogin do root
# recentes estao em us-east-2 (14) e us-west-2 (2), nenhum em us-east-1.
# Chamadas de API do root chegam na regiao do endpoint chamado: servicos globais
# (IAM, Organizations, conta) em us-east-1; recursos do app em sa-east-1. A trilha
# musclefit-trail e multi-regiao, entao o bus default de cada regiao recebe os
# eventos de gerenciamento daquela regiao.
#
# Desenho:
#   - us-east-1 e sa-east-1: duas regras (sign-in e API) -> SNS musclefit-alerts da
#     MESMA regiao, com input_transformer gerando o texto em pt-BR.
#   - us-east-2 e us-west-2: uma regra que so encaminha os eventos do root para o bus
#     default de us-east-1 (o evento chega identico, com detail.awsRegion original) e
#     as regras de us-east-1 mandam o e-mail. Assim nao ha topico/assinatura nova para
#     confirmar e o texto do e-mail tem uma fonte so.
#   - Regioes fora dessas quatro nao sao cobertas por chamada de API (o login, que e
#     o primeiro passo de qualquer uso pelo console, e coberto em todas as regioes
#     onde ele pode cair). GuardDuty cobre o resto com RootCredentialUsage.
#
# Ruido: o bus default so recebe chamadas de ESCRITA do CloudTrail (as de leitura
# exigiriam o estado ENABLED_WITH_ALL_CLOUDTRAIL_MANAGEMENT_EVENTS, que nao usamos),
# e eventos com userIdentity.invokedBy (um servico AWS agindo em nome do root) ficam
# de fora, como no filtro do CIS. Um login do root gera 1 a 3 e-mails (ConsoleLogin
# e, conforme o fluxo, eventos de verificacao de credencial/MFA).
#
# Custo: eventos de servicos AWS no bus default sao gratis; entrega cross-region
# US$ 0,01/milhao (eventos do root: dezenas por ano); SNS e-mail: 1.000/mes gratis.
# Na pratica US$ 0,00/mes.

provider "aws" {
  alias  = "us_east_2"
  region = "us-east-2"

  default_tags {
    tags = {
      project    = "musclefit"
      managed_by = "terraform"
      created_by = "musclefit-gha-deploy"
    }
  }
}

provider "aws" {
  alias  = "us_west_2"
  region = "us-west-2"

  default_tags {
    tags = {
      project    = "musclefit"
      managed_by = "terraform"
      created_by = "musclefit-gha-deploy"
    }
  }
}

locals {
  root_account_id = data.aws_caller_identity.current.account_id

  # Sign-in no console (ConsoleLogin e eventos do fluxo de login do root).
  root_signin_pattern = jsonencode({
    "detail-type" = ["AWS Console Sign In via CloudTrail"]
    detail = {
      userIdentity = { type = ["Root"] }
    }
  })

  # Chamadas de API feitas pelo proprio root (sem servico AWS intermediando).
  root_api_pattern = jsonencode({
    "detail-type" = ["AWS API Call via CloudTrail"]
    detail = {
      userIdentity = {
        type      = ["Root"]
        invokedBy = [{ exists = false }]
      }
    }
  })

  # Encaminhamento de us-east-2/us-west-2: os dois tipos acima.
  root_forward_pattern = jsonencode({
    "detail-type" = ["AWS Console Sign In via CloudTrail", "AWS API Call via CloudTrail"]
    detail = {
      userIdentity = {
        type      = ["Root"]
        invokedBy = [{ exists = false }]
      }
    }
  })

  # Texto do e-mail. Cada linha entre aspas (exigencia do input_transformer para
  # texto multilinha). Horario em UTC, como vem do CloudTrail.
  root_signin_input_paths = {
    evento    = "$.detail.eventName"
    quando    = "$.detail.eventTime"
    regiao    = "$.detail.awsRegion"
    ip        = "$.detail.sourceIPAddress"
    agente    = "$.detail.userAgent"
    resultado = "$.detail.responseElements.ConsoleLogin"
    mfa       = "$.detail.additionalEventData.MFAUsed"
    conta     = "$.account"
    id        = "$.detail.eventID"
  }
  root_signin_input_template = <<-EOT
    "ALERTA MuscleFit: login do usuário ROOT na conta AWS <conta>."
    ""
    "Quem: usuário root (credencial de emergência)"
    "Quando (UTC): <quando>"
    "Evento: <evento>"
    "Resultado: <resultado> (Success = entrou; Failure = tentativa falhou)"
    "MFA usado: <mfa>"
    "IP de origem: <ip>"
    "User agent: <agente>"
    "Região registrada: <regiao>"
    "ID do evento no CloudTrail: <id>"
    ""
    "Se não foi você: troque a senha do root, confira o MFA e revise o CloudTrail (trilha musclefit-trail)."
  EOT

  root_api_input_paths = {
    evento  = "$.detail.eventName"
    servico = "$.detail.eventSource"
    quando  = "$.detail.eventTime"
    regiao  = "$.detail.awsRegion"
    ip      = "$.detail.sourceIPAddress"
    agente  = "$.detail.userAgent"
    erro    = "$.detail.errorCode"
    mfa     = "$.detail.userIdentity.sessionContext.attributes.mfaAuthenticated"
    conta   = "$.account"
    id      = "$.detail.eventID"
  }
  root_api_input_template = <<-EOT
    "ALERTA MuscleFit: chamada de API feita pelo usuário ROOT na conta AWS <conta>."
    ""
    "Quem: usuário root (credencial de emergência)"
    "Quando (UTC): <quando>"
    "Ação: <evento> em <servico>"
    "Código de erro (vazio = a chamada deu certo): <erro>"
    "Sessão com MFA: <mfa>"
    "IP de origem: <ip>"
    "User agent: <agente>"
    "Região: <regiao>"
    "ID do evento no CloudTrail: <id>"
    ""
    "Se não foi você: troque a senha do root, apague chaves de acesso do root se houver e revise o CloudTrail (trilha musclefit-trail)."
  EOT
}

# ---------------------------------------------------------------- us-east-1

resource "aws_cloudwatch_event_rule" "root_signin_us_east_1" {
  provider      = aws.us_east_1
  name          = "musclefit-root-signin"
  description   = "Login do usuario root no console -> e-mail (musclefit-alerts)."
  event_pattern = local.root_signin_pattern
}

resource "aws_cloudwatch_event_target" "root_signin_us_east_1" {
  provider  = aws.us_east_1
  rule      = aws_cloudwatch_event_rule.root_signin_us_east_1.name
  target_id = "musclefit-alerts"
  arn       = aws_sns_topic.alerts_us_east_1.arn

  input_transformer {
    input_paths    = local.root_signin_input_paths
    input_template = local.root_signin_input_template
  }
}

resource "aws_cloudwatch_event_rule" "root_api_us_east_1" {
  provider      = aws.us_east_1
  name          = "musclefit-root-api"
  description   = "Chamada de API (escrita) feita pelo usuario root -> e-mail (musclefit-alerts)."
  event_pattern = local.root_api_pattern
}

resource "aws_cloudwatch_event_target" "root_api_us_east_1" {
  provider  = aws.us_east_1
  rule      = aws_cloudwatch_event_rule.root_api_us_east_1.name
  target_id = "musclefit-alerts"
  arn       = aws_sns_topic.alerts_us_east_1.arn

  input_transformer {
    input_paths    = local.root_api_input_paths
    input_template = local.root_api_input_template
  }
}

# ---------------------------------------------------------------- sa-east-1
# O ConsoleLogin do root nao cai aqui hoje; a regra de sign-in fica por consistencia
# (custo zero) caso a AWS passe a registrar o login na regiao do app.

resource "aws_cloudwatch_event_rule" "root_signin" {
  name          = "musclefit-root-signin"
  description   = "Login do usuario root no console -> e-mail (musclefit-alerts)."
  event_pattern = local.root_signin_pattern
}

resource "aws_cloudwatch_event_target" "root_signin" {
  rule      = aws_cloudwatch_event_rule.root_signin.name
  target_id = "musclefit-alerts"
  arn       = aws_sns_topic.alerts.arn

  input_transformer {
    input_paths    = local.root_signin_input_paths
    input_template = local.root_signin_input_template
  }
}

resource "aws_cloudwatch_event_rule" "root_api" {
  name          = "musclefit-root-api"
  description   = "Chamada de API (escrita) feita pelo usuario root -> e-mail (musclefit-alerts)."
  event_pattern = local.root_api_pattern
}

resource "aws_cloudwatch_event_target" "root_api" {
  rule      = aws_cloudwatch_event_rule.root_api.name
  target_id = "musclefit-alerts"
  arn       = aws_sns_topic.alerts.arn

  input_transformer {
    input_paths    = local.root_api_input_paths
    input_template = local.root_api_input_template
  }
}

# ---------------------------------------------------------------- us-east-2 / us-west-2
# Encaminhamento para o bus default de us-east-1 (mesma conta: o bus de destino nao
# precisa de resource policy; a regra de origem precisa de uma role com PutEvents).

resource "aws_iam_role" "root_alert_forward" {
  name = "musclefit-root-alert-forward-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.root_account_id }
        ArnLike = {
          "aws:SourceArn" = [
            "arn:aws:events:us-east-2:${local.root_account_id}:rule/musclefit-root-forward",
            "arn:aws:events:us-west-2:${local.root_account_id}:rule/musclefit-root-forward",
          ]
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "root_alert_forward" {
  name = "put-events-us-east-1-default"
  role = aws_iam_role.root_alert_forward.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "events:PutEvents"
      Resource = "arn:aws:events:us-east-1:${local.root_account_id}:event-bus/default"
    }]
  })
}

resource "aws_cloudwatch_event_rule" "root_forward_us_east_2" {
  provider      = aws.us_east_2
  name          = "musclefit-root-forward"
  description   = "Eventos do usuario root -> bus default de us-east-1 (alerta por e-mail la)."
  event_pattern = local.root_forward_pattern
}

resource "aws_cloudwatch_event_target" "root_forward_us_east_2" {
  provider  = aws.us_east_2
  rule      = aws_cloudwatch_event_rule.root_forward_us_east_2.name
  target_id = "musclefit-us-east-1-default"
  arn       = "arn:aws:events:us-east-1:${local.root_account_id}:event-bus/default"
  role_arn  = aws_iam_role.root_alert_forward.arn
}

resource "aws_cloudwatch_event_rule" "root_forward_us_west_2" {
  provider      = aws.us_west_2
  name          = "musclefit-root-forward"
  description   = "Eventos do usuario root -> bus default de us-east-1 (alerta por e-mail la)."
  event_pattern = local.root_forward_pattern
}

resource "aws_cloudwatch_event_target" "root_forward_us_west_2" {
  provider  = aws.us_west_2
  rule      = aws_cloudwatch_event_rule.root_forward_us_west_2.name
  target_id = "musclefit-us-east-1-default"
  arn       = "arn:aws:events:us-east-1:${local.root_account_id}:event-bus/default"
  role_arn  = aws_iam_role.root_alert_forward.arn
}

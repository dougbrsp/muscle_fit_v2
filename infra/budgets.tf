# FIN-01 (varredura 2026-10-08): Budget trazido para o Terraform, com alerta de PREVISAO.
#
# O Budget `musclefit-monthly` foi criado a mao (limite US$ 30, so ACTUAL 80% e 100%).
# Em setembro gastou US$ 80,53 contra US$ 30 e ninguem foi avisado antes: os creditos
# da conta zeram o custo liquido no Cost Explorer, entao o gatilho tem de ser o uso
# bruto (include_credit = false). Importado em vez de recriado, para manter historico
# e assinante.
#
# Budgets e uma API global (endpoint us-east-1); o provider default resolve isso.

import {
  to = aws_budgets_budget.monthly
  id = "930413537709:musclefit-monthly"
}

resource "aws_budgets_budget" "monthly" {
  name         = "musclefit-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Uso bruto: creditos e reembolsos NAO abatem (o numero do Budget = custo real
  # antes dos creditos). Demais campos = padrao do console; se o plan do import
  # mostrar diff nesses booleanos, alinhar ao valor existente (nao ao desejado).
  cost_types {
    include_credit             = false
    include_discount           = true
    include_other_subscription = true
    include_recurring          = true
    include_refund             = false
    include_subscription       = true
    include_support            = true
    include_tax                = true
    include_upfront            = true
    use_amortized              = false
    use_blended                = false
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alerts_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alerts_email]
  }

  # Previsao: avisa ANTES de estourar. O forecast so funciona com ~5 semanas de
  # historico (conta de 2026-09-03); ate la a notificacao simplesmente nao dispara.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alerts_email]
  }
}

# Aurora e ~93% do custo: um Budget so do servico mostra a causa, nao so o total.
# Os 2 primeiros Budgets da conta sao gratuitos (o terceiro custaria ~US$ 0,10/dia).
resource "aws_budgets_budget" "monthly_rds" {
  name         = "musclefit-monthly-rds"
  budget_type  = "COST"
  limit_amount = var.rds_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "Service"
    values = ["Amazon Relational Database Service"]
  }

  cost_types {
    include_credit = false
    include_refund = false
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alerts_email]
  }
}

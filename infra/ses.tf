# Amazon SES - identidade de DOMINIO musclefit.com.br, usada pela Lambda
# musclefit-api-core para enviar o e-mail de "Esqueci minha senha" (melhoria
# M5) via boto3.client("ses").send_email, remetente no-reply@musclefit.com.br.
#
# POR QUE IDENTIDADE DE DOMINIO (e nao de e-mail): verificar o dominio inteiro
# uma vez cobre qualquer remetente @musclefit.com.br e, principalmente, permite
# Easy DKIM (assinatura d=musclefit.com.br), sem o qual Gmail/Outlook tratam o
# e-mail como suspeito. Identidade de e-mail avulsa nao teria DKIM alinhado.
#
# DNS: os registros da identidade (3 CNAMEs de Easy DKIM, MX e SPF do MAIL
# FROM) e o DMARC do dominio sao geridos em infra/route53.tf (melhoria M44),
# derivados dos atributos dos recursos abaixo. Desde 2026-10-03 a zona do
# Route 53 e o DNS autoritativo do dominio (NS delegados no Registro.br); antes
# disso esses mesmos registros estavam cadastrados a mao no Registro.br desde
# 2026-09-22 (identidade verificada, DKIM SUCCESS). Envio do "Esqueci minha
# senha" testado de ponta a ponta em 2026-10-05, ja com a zona nova.
# Se os CNAMEs de DKIM sumirem do DNS publicado, a identidade volta a
# "pending verification" e o SES recusa o envio (MessageRejected) - o
# `terraform apply` nao quebra por isso, so o envio.
#
# SANDBOX: a conta esta em sandbox do SES (`aws sesv2 get-account` em
# 2026-09-22: ProductionAccessEnabled=false, Max24HourSend=200, MaxSendRate=1).
# Em sandbox o SES so entrega para destinatarios TAMBEM verificados. Sair do
# sandbox e um pedido manual do dono da conta no console (SES > Account
# dashboard > Request production access) - nao e algo que passe por IaC.
#
# CUSTO: identidade e DKIM sao gratuitos; envio custa US$ 0,10 por 1.000
# e-mails (primeiros 3.000/mes gratis nos 12 meses iniciais). Reset de senha e
# volume desprezivel.

locals {
  ses_domain = "musclefit.com.br"

  # Subdominio MAIL FROM customizado: faz o Return-Path (envelope) sair como
  # bounce@mail.musclefit.com.br em vez de *.amazonses.com, alinhando SPF com o
  # dominio do From - exigencia para DMARC "pass" por SPF. DKIM ja alinha
  # sozinho; MAIL FROM e o segundo fator de alinhamento. Se o MX abaixo nao
  # estiver publicado, behavior_on_mx_failure = USE_DEFAULT_VALUE faz o SES
  # cair no dominio amazonses.com em vez de recusar o envio.
  ses_mail_from_domain = "mail.musclefit.com.br"
}

resource "aws_sesv2_email_identity" "musclefit_domain" {
  email_identity = local.ses_domain

  # Easy DKIM: o SES gera 3 pares de chaves e publica as chaves publicas em
  # <token>.dkim.amazonses.com; o dominio so precisa dos 3 CNAMEs (outputs
  # abaixo). RSA 2048 e o tamanho recomendado hoje.
  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }

  tags = {
    Name = "musclefit-ses-domain"
  }
}

resource "aws_sesv2_email_identity_mail_from_attributes" "musclefit_domain" {
  email_identity         = aws_sesv2_email_identity.musclefit_domain.email_identity
  mail_from_domain       = local.ses_mail_from_domain
  behavior_on_mx_failure = "USE_DEFAULT_VALUE"
}

# Rastreio de bounce/complaint: o SES ja aplica a suppression list da conta
# (BOUNCE + COMPLAINT, ver get-account). Sem topico SNS por enquanto - o
# volume de reset de senha nao justifica; adicionar
# aws_sesv2_configuration_set + event destination se o dominio comecar a
# enviar notificacoes em volume.

# Os outputs ses_dkim_cname_records / ses_mail_from_records / ses_dmarc_record,
# que existiam so para o cadastro manual no Registro.br, foram removidos no M44:
# os registros agora sao recursos em infra/route53.tf (fonte unica), nenhum
# workflow/script os consumia, e o ses_dmarc_record ja divergia do publicado
# (recomendava rua=mailto:dmarc@..., mas o DMARC real e "v=DMARC1; p=none").

output "ses_domain_identity_arn" {
  description = "ARN da identidade SES do dominio (Resource da policy ses:SendEmail da api-core)."
  value       = aws_sesv2_email_identity.musclefit_domain.arn
}

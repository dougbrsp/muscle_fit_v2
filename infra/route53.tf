# Route 53 - hosted zone publica musclefit.com.br (melhoria M44).
#
# ESTADO ATUAL (M44 encerrado em 2026-10-05): esta zona e o DNS AUTORITATIVO de
# musclefit.com.br. Delegacao publicada no Registro.br em 2026-10-03 17:40 BRT
# (NS ns-1272.awsdns-31.org, ns-1996.awsdns-57.co.uk, ns-366.awsdns-45.com,
# ns-911.awsdns-49.net), com o DS (DNSSEC) removido la antes da troca. O
# Registro.br continua sendo so o REGISTRADOR do dominio: registro DNS novo entra
# SOMENTE por PR neste arquivo, nunca no painel do Registro.br (la nao e mais
# consultado). Os comentarios abaixo descrevem a migracao e ficam como historico.
#
# POR QUE (historico): o DNS autoritativo de musclefit.com.br estava no Registro.br
# (NS e.sec.dns.br / f.sec.dns.br, DNSSEC ligado). O Registro.br nao tem ALIAS/ANAME,
# entao o apex musclefit.com.br nunca conseguiu apontar para o CloudFront (CNAME no
# apex e proibido pela RFC 1034). Com a zona no Route 53, o apex virou um ALIAS
# A/AAAA para a mesma distribuicao de app.musclefit.com.br (aliases declarados em
# cloudfront.tf, certificado us-east-1 cobre o apex como SAN).
#
# ESTE ARQUIVO SO CRIA A ZONA E OS REGISTROS. Na criacao (PR #338, producao no #339)
# nenhum resolvedor consultava a zona; ela passou a valer com a troca de NS feita a
# mao pelo usuario no painel do Registro.br em 2026-10-03, depois de desligar o
# DNSSEC la.
#
# INVENTARIO: os registros abaixo replicaram 1:1 o que estava publicado no
# Registro.br (confirmado pelo usuario em 2026-10-03), com 3 diferencas deliberadas:
#   - app/api deixaram de ser CNAME e viraram ALIAS (A, e AAAA no CloudFront - IPv6 ligado);
#   - apex ganhou ALIAS A/AAAA para o CloudFront (objetivo do M44);
#   - entraram os 3 CNAMEs de validacao do ACM, que NAO estavam publicados no
#     Registro.br e sem os quais a renovacao gerenciada dos dois certificados
#     (NotAfter 2027-03-19) falharia. Conferido em 2026-10-05: ambos ISSUED,
#     RenewalEligibility=ELIGIBLE.
#
# TTL: 300 s para todo registro nao-ALIAS. Foi proposital para a janela de migracao
# (qualquer correcao propaga em 5 min). Com a troca de NS estabilizada, MX/TXT/
# DKIM podem subir para 3600 sem impacto (custo de consulta e desprezivel nos dois).
# Registros ALIAS nao tem TTL proprio (herdam o do alvo).
#
# CUSTO: US$ 0,50/mes pela zona + US$ 0,40 por milhao de consultas padrao. Consultas
# a registros ALIAS que apontam para CloudFront/API Gateway sao gratuitas.
#
# DNSSEC: a zona NAO assina (sem aws_route53_hosted_zone_dnssec). Assinar exige KMS
# assimetrica em us-east-1 + KSK + publicar o DS no Registro.br - fica como melhoria
# futura opcional. Por isso o DS antigo foi REMOVIDO no Registro.br antes da troca
# de NS (com DS antigo + zona nao assinada, resolvedores validadores devolveriam
# SERVFAIL para o dominio inteiro).
#
# PERMISSAO DO PIPELINE: route53:CreateHostedZone exige Resource "*" (nao aceita ARN
# de zona). Desde a consolidacao AWS-13 (2026-10-09) vem do statement Route53Todos de
# iam-policies/consolidacao/musclefit-gha-deploy-v2-rede-dados.json; a zona ja existe
# e o AWS-13 recomenda retirar essa acao numa versao futura da policy.

locals {
  route53_zone_name = "musclefit.com.br"

  # TTL dos registros nao-ALIAS (ver cabecalho).
  route53_default_ttl = 300

  # CNAMEs de validacao DNS do ACM. Os certificados sao `data` sources (emitidos a
  # mao na Fase 0, fora do Terraform - ver cloudfront.tf e api_gateway.tf), por isso
  # os valores entram literais aqui em vez de virem de
  # aws_acm_certificate.domain_validation_options. Origem (lida em 2026-10-03 com
  # `aws acm describe-certificate`, campo DomainValidationOptions[].ResourceRecord):
  #   - arn:aws:acm:us-east-1:930413537709:certificate/4c2d79c0-61bb-4ee5-847f-1265513edd5a
  #     (app.musclefit.com.br + SAN musclefit.com.br, usado pelo CloudFront)
  #   - arn:aws:acm:sa-east-1:930413537709:certificate/d0d1b680-4a31-4844-823d-0076079a4cac
  #     (api.musclefit.com.br, usado pelo dominio customizado do API Gateway)
  # O ACM reaproveita o mesmo CNAME em toda renovacao enquanto o certificado existir;
  # so muda se o certificado for re-emitido (novo ARN) - nesse caso, atualizar aqui.
  acm_validation_records = {
    app = {
      name  = "_c202e372df5e97234335ac190c8040a0.app.musclefit.com.br"
      value = "_c78688208218d9fb88e5b7448c6ed5ce.jkddzztszm.acm-validations.aws"
    }
    apex = {
      name  = "_6b0a53bae1fdbd8a2a3d31e218ca50fd.musclefit.com.br"
      value = "_d7efd935b1e6f2ab9248d771545642cc.jkddzztszm.acm-validations.aws"
    }
    api = {
      name  = "_3bc245dbff8c72f88d09912e47e793e2.api.musclefit.com.br"
      value = "_c910784384a9022121d0a80e592d6200.jkddzztszm.acm-validations.aws"
    }
  }
}

resource "aws_route53_zone" "musclefit" {
  # O texto do `comment` e historico (os NS ja foram cadastrados no Registro.br em
  # 2026-10-03). NAO alterar: mudar o comment gera update in-place na zona
  # (route53:UpdateHostedZoneComment), que a role do pipeline pode nao ter.
  name    = local.route53_zone_name
  comment = "musclefit.com.br - zona publica (M44); NS a cadastrar no Registro.br"

  # force_destroy = false (default): o provider recusa apagar a zona com registros.
  # prevent_destroy: um plan que destrua/recrie a zona trocaria os 4 NS e derrubaria
  # o dominio inteiro (site, API e e-mail pessoal iCloud) ate o Registro.br ser
  # atualizado a mao.
  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name = "musclefit-public-zone"
  }
}

# ---------------------------------------------------------------------------
# Web: apex, app e api (ALIAS)
# ---------------------------------------------------------------------------

# Apex -> CloudFront (NOVO, objetivo do M44). A e AAAA porque a distribuicao tem
# is_ipv6_enabled = true. evaluate_target_health nao e suportado para CloudFront.
resource "aws_route53_record" "apex" {
  for_each = toset(["A", "AAAA"])

  zone_id = aws_route53_zone.musclefit.zone_id
  name    = local.route53_zone_name
  type    = each.value

  alias {
    name                   = aws_cloudfront_distribution.musclefit_cdn.domain_name
    zone_id                = aws_cloudfront_distribution.musclefit_cdn.hosted_zone_id
    evaluate_target_health = false
  }
}

# app -> CloudFront (ate 2026-10-03 era CNAME d2kf866t8pk1us.cloudfront.net no
# Registro.br).
resource "aws_route53_record" "app" {
  for_each = toset(["A", "AAAA"])

  zone_id = aws_route53_zone.musclefit.zone_id
  name    = "app.${local.route53_zone_name}"
  type    = each.value

  alias {
    name                   = aws_cloudfront_distribution.musclefit_cdn.domain_name
    zone_id                = aws_cloudfront_distribution.musclefit_cdn.hosted_zone_id
    evaluate_target_health = false
  }
}

# api -> dominio customizado REGIONAL do API Gateway (ate 2026-10-03 era CNAME
# d-u1fd3xctnj.execute-api.sa-east-1.amazonaws.com no Registro.br). So A: o endpoint
# regional do API Gateway (REST) e IPv4-only por padrao (ip_address_type nao
# configurado), entao AAAA nao teria para onde resolver.
resource "aws_route53_record" "api" {
  zone_id = aws_route53_zone.musclefit.zone_id
  name    = aws_api_gateway_domain_name.api.domain_name
  type    = "A"

  alias {
    name                   = aws_api_gateway_domain_name.api.regional_domain_name
    zone_id                = aws_api_gateway_domain_name.api.regional_zone_id
    evaluate_target_health = false
  }
}

# ---------------------------------------------------------------------------
# E-mail pessoal do usuario (iCloud Mail com dominio proprio) - NAO e do app,
# mas mora na mesma zona. Qualquer erro aqui derruba o e-mail pessoal dele.
# ---------------------------------------------------------------------------

resource "aws_route53_record" "apex_mx" {
  zone_id = aws_route53_zone.musclefit.zone_id
  name    = local.route53_zone_name
  type    = "MX"
  ttl     = local.route53_default_ttl
  records = [
    "10 mx01.mail.icloud.com.",
    "20 mx02.mail.icloud.com.",
  ]
}

# Um unico record set TXT no apex (Route 53 nao aceita dois record sets do mesmo
# nome+tipo): verificacao de dominio da Apple + SPF do iCloud. O provider poe as
# aspas; nao incluir aqui.
resource "aws_route53_record" "apex_txt" {
  zone_id = aws_route53_zone.musclefit.zone_id
  name    = local.route53_zone_name
  type    = "TXT"
  ttl     = local.route53_default_ttl
  records = [
    "apple-domain=NFliZpc4aWgplQvA",
    "v=spf1 include:icloud.com ~all",
  ]
}

# DKIM do iCloud Mail (assina os e-mails pessoais @musclefit.com.br).
resource "aws_route53_record" "icloud_dkim" {
  zone_id = aws_route53_zone.musclefit.zone_id
  name    = "sig1._domainkey.${local.route53_zone_name}"
  type    = "CNAME"
  ttl     = local.route53_default_ttl
  records = ["sig1.dkim.musclefit.com.br.at.icloudmailadmin.com"]
}

# DMARC do dominio (vale para iCloud e SES). Valor identico ao que estava publicado
# no Registro.br antes da delegacao - p=none, sem rua.
resource "aws_route53_record" "dmarc" {
  zone_id = aws_route53_zone.musclefit.zone_id
  name    = "_dmarc.${local.route53_zone_name}"
  type    = "TXT"
  ttl     = local.route53_default_ttl
  records = ["v=DMARC1; p=none"]
}

# ---------------------------------------------------------------------------
# Amazon SES (ses.tf): Easy DKIM + MAIL FROM mail.musclefit.com.br
# ---------------------------------------------------------------------------

# 3 CNAMEs de Easy DKIM, derivados dos tokens da propria identidade (em 2026-10-03:
# 4ktxzyhrmz3wjboxawrpzfujeqag2hh7, 5r7x7wx3y5g2ghkue3zya6xtzuca5alo,
# rnalwz6ggtaepvwa4ztf2z3z4fqnr6ru - iguais aos que estavam no Registro.br).
# for_each por token (e nao count por indice) para uma reordenacao da lista pela API
# nao gerar destroy/create. Se a identidade SES um dia for recriada, os tokens ficam
# "known after apply" e o for_each falha no plan: aplicar a identidade antes com
# -target e depois o resto.
resource "aws_route53_record" "ses_dkim" {
  for_each = toset(aws_sesv2_email_identity.musclefit_domain.dkim_signing_attributes[0].tokens)

  zone_id = aws_route53_zone.musclefit.zone_id
  name    = "${each.value}._domainkey.${local.route53_zone_name}"
  type    = "CNAME"
  ttl     = local.route53_default_ttl
  records = ["${each.value}.dkim.amazonses.com"]
}

resource "aws_route53_record" "ses_mail_from_mx" {
  zone_id = aws_route53_zone.musclefit.zone_id
  name    = aws_sesv2_email_identity_mail_from_attributes.musclefit_domain.mail_from_domain
  type    = "MX"
  ttl     = local.route53_default_ttl
  records = ["10 feedback-smtp.${var.aws_region}.amazonses.com."]
}

resource "aws_route53_record" "ses_mail_from_spf" {
  zone_id = aws_route53_zone.musclefit.zone_id
  name    = aws_sesv2_email_identity_mail_from_attributes.musclefit_domain.mail_from_domain
  type    = "TXT"
  ttl     = local.route53_default_ttl
  records = ["v=spf1 include:amazonses.com ~all"]
}

# ---------------------------------------------------------------------------
# ACM - validacao DNS para a renovacao gerenciada (NOVOS)
# ---------------------------------------------------------------------------

resource "aws_route53_record" "acm_validation" {
  for_each = local.acm_validation_records

  zone_id = aws_route53_zone.musclefit.zone_id
  name    = each.value.name
  type    = "CNAME"
  ttl     = local.route53_default_ttl
  records = [each.value.value]
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "route53_zone_id" {
  description = "ID da hosted zone publica musclefit.com.br."
  value       = aws_route53_zone.musclefit.zone_id
}

output "route53_name_servers" {
  description = "Os 4 name servers da zona - delegados no Registro.br desde 2026-10-03 (M44). Se a zona um dia for recriada, os NS mudam e precisam ser atualizados la (secao DNS > 'Alterar servidores DNS')."
  value       = aws_route53_zone.musclefit.name_servers
}

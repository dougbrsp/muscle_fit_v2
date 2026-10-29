# CloudFront (musclefit-cdn) na frente do bucket S3 do frontend estatico -
# ver docs/aws-migration-plan.md, secao 1.5, secao 0.4 e Fase 4.
#
# O certificado ACM do CloudFront tem que estar OBRIGATORIAMENTE em
# us-east-1, independente da regiao do resto da infra (exigencia propria do
# servico - ver secao 0.4). Provider alias dedicado e explicito, para nao
# arriscar alguem trocar a regiao default do modulo inteiro sem querer.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      project    = "musclefit"
      managed_by = "terraform"
      created_by = "musclefit-gha-deploy"
    }
  }
}

# Certificado ja emitido manualmente na Fase 0 (nao criar um novo aqui - ver
# docs/aws-migration-plan.md, secao 0.4): cobre app.musclefit.com.br e o
# apex musclefit.com.br como SAN na mesma emissao (ARN registrado em
# infra/README.md: arn:aws:acm:us-east-1:930413537709:certificate/4c2d79c0-61bb-4ee5-847f-1265513edd5a).
data "aws_acm_certificate" "frontend" {
  provider = aws.us_east_1

  domain      = "app.musclefit.com.br"
  statuses    = ["ISSUED"]
  types       = ["AMAZON_ISSUED"]
  most_recent = true
}

# Reescreve o URI da requisicao antes de bater na origem S3, para rotas
# estaticas de primeiro nivel (ex.: "/academias") resolverem para o objeto
# real "/academias/index.html" - ver cloudfront-functions/spa-index-rewrite.js
# para o porque disso ser necessario com OAC (sem isso, toda rota que nao
# seja a raiz exata cai no custom_error_response e serve a tela de login por
# engano, com 200 - bug descoberto em producao em 2026-09-07).
# Desde 2026-10-03 a mesma function tambem responde 301 do apex
# musclefit.com.br para https://app.musclefit.com.br (um behavior so aceita
# uma function por evento - ver o comentario de aliases abaixo).
resource "aws_cloudfront_function" "spa_index_rewrite" {
  name    = "musclefit-spa-index-rewrite"
  runtime = "cloudfront-js-2.0"
  comment = "Reescreve /rota -> /rota/index.html para o export estatico do Next.js"
  publish = true
  code    = file("${path.module}/cloudfront-functions/spa-index-rewrite.js")
}

# SEG-08/AWS-09: Content-Security-Policy-Report-Only nas respostas HTML do
# app (so registra violacoes no console do navegador; nao bloqueia nada) -
# ver cloudfront-functions/security-headers.js para a politica e a origem de
# cada fonte.
#
# Por que CloudFront Function (viewer-response) e nao
# aws_cloudfront_response_headers_policy: a role de deploy ja tem
# cloudfront:CreateFunction/UpdateFunction/PublishFunction/DescribeFunction/
# GetFunction em function/musclefit-* (policy fase5-planreadonly) e
# UpdateDistribution/GetDistributionConfig na musclefit-cdn (cards-cloudfront)
# - nenhuma permissao nova. Response headers policy exigiria
# cloudfront:CreateResponseHeadersPolicy/GetResponseHeadersPolicy/
# UpdateResponseHeadersPolicy/DeleteResponseHeadersPolicy, que a role nao tem.
# Trade-off aceito: function de viewer-response nao roda quando a origem
# responde >= 400, entao o fallback do custom_error_response (403/404 ->
# /index.html) sai sem o cabecalho; as rotas reais (reescritas pela
# spa_index_rewrite) saem com ele. Custo: US$ 0,10 por milhao de invocacoes
# (~100 mil req/mes no default behavior => ~US$ 0,01/mes).
resource "aws_cloudfront_function" "security_headers" {
  name    = "musclefit-security-headers"
  runtime = "cloudfront-js-2.0"
  comment = "CSP Report-Only nas respostas HTML do app (SEG-08/AWS-09)"
  publish = true
  code    = file("${path.module}/cloudfront-functions/security-headers.js")
}

# Origin Access Control - substitui a OAI legada. O bucket so aceita
# GetObject vindo desta distribuicao especifica (ver s3_frontend.tf).
resource "aws_cloudfront_origin_access_control" "musclefit_frontend_assets" {
  name                              = "musclefit-frontend-assets-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "musclefit_cdn" {
  comment             = "musclefit-cdn"
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"

  # Apex musclefit.com.br: alias desta mesma distribuicao (DNS de ambos: ALIAS
  # A/AAAA na hosted zone de infra/route53.tf, M44), mas desde 2026-10-03
  # (decisao do usuario) o apex NAO serve o app: a function
  # spa_index_rewrite responde 301 para https://app.musclefit.com.br +
  # caminho + query string. Motivo: a API (CORS das Lambdas) e o Turnstile so
  # aceitam a origem app., entao o login quebrava no apex - uma origem so, em
  # vez de liberar CORS/captcha para dois hosts. Substitui a decisao da Fase 0
  # (item 0.5) de servir o apex "sem redirect".
  #
  # Os ordered_cache_behavior de uploads (/equipment/*, /gyms/*,
  # /exercise-cards/*) ficam SEM function de proposito: sao imagens publicas
  # (sem login, sem CORS), o app nunca gera URL com o apex, e associar a
  # function ali custaria uma invocacao por imagem servida no app. tambem.
  # No apex esses caminhos continuam servindo o arquivo, o que e inofensivo.
  aliases = [
    "app.musclefit.com.br",
    "musclefit.com.br",
  ]

  origin {
    domain_name              = aws_s3_bucket.musclefit_frontend_assets.bucket_regional_domain_name
    origin_id                = "musclefit-frontend-assets"
    origin_access_control_id = aws_cloudfront_origin_access_control.musclefit_frontend_assets.id
  }

  # Segundo origin, bucket musclefit-uploads (foto de equipamento/logo de
  # academia e card ilustrado de exercicio publicado, servidos como arquivo
  # estatico) - ver infra/cloudfront_uploads.tf
  # para a motivacao completa (throttling do limite de concorrencia Lambda),
  # a OAC e a bucket policy deste origin.
  origin {
    domain_name              = aws_s3_bucket.musclefit_uploads.bucket_regional_domain_name
    origin_id                = "musclefit-uploads"
    origin_access_control_id = aws_cloudfront_origin_access_control.musclefit_uploads.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "musclefit-frontend-assets"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6" # AWS Managed-CachingOptimized

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa_index_rewrite.arn
    }

    # So no default behavior (HTML/JS/CSS do app). Os behaviors de upload
    # (imagens) ficam sem: CSP nao se aplica a imagem servida direto.
    function_association {
      event_type   = "viewer-response"
      function_arn = aws_cloudfront_function.security_headers.arn
    }
  }

  # Foto de equipamento/logo de academia (bucket musclefit-uploads) servida
  # direto como arquivo estatico, sem passar pela Lambda a cada leitura - ver
  # infra/cloudfront_uploads.tf. path_pattern casa com a S3 key literal
  # (ex.: "/equipment/82.png"), sem SPA function association (nao e HTML).
  ordered_cache_behavior {
    path_pattern           = "/equipment/*"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "musclefit-uploads"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
    cache_policy_id        = "658327ea-f89d-4fab-a63d-7e88639e58f6" # AWS Managed-CachingOptimized
  }

  ordered_cache_behavior {
    path_pattern           = "/gyms/*"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "musclefit-uploads"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
    cache_policy_id        = "658327ea-f89d-4fab-a63d-7e88639e58f6" # AWS Managed-CachingOptimized
  }

  # Cards ilustrados de exercicios PUBLICADOS (bucket musclefit-uploads,
  # prefixo exercise-cards/) - ver infra/cloudfront_uploads.tf. Cada versao
  # aprovada ganha uma key nova e imutavel
  # (exercise-cards/<exercise_id>/v<n>/card.webp, card.png, thumb.webp),
  # gravada com "Cache-Control: public, max-age=31536000, immutable". A
  # CachingOptimized RESPEITA o Cache-Control da origem (TTL min 1 s, default
  # 1 dia, max 1 ano), entao o objeto fica 1 ano na borda e nunca precisa de
  # invalidacao: trocar o card = publicar v<n+1> e apontar o catalogo para ela.
  # Mesma cache policy dos dois behaviors acima de proposito (cache policy
  # nova exigiria permissao nova na role de deploy).
  #
  # Os RASCUNHOS (exercise-card-drafts/, imagem paga ainda nao aprovada) NAO
  # tem behavior: sao servidos ao admin so por URL pre-assinada gerada pela
  # api-core. Um GET em /exercise-card-drafts/... cai no default_cache_behavior
  # (bucket do frontend, que nao tem o objeto -> fallback index.html do SPA), e
  # a bucket policy do musclefit-uploads tambem nao libera esse prefixo ao OAC.
  ordered_cache_behavior {
    path_pattern           = "/exercise-cards/*"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "musclefit-uploads"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
    cache_policy_id        = "658327ea-f89d-4fab-a63d-7e88639e58f6" # AWS Managed-CachingOptimized
  }

  # next build (output: 'export') gera arquivos estaticos por rota
  # (ex.: out/registro-exercicios/[activityId]/index.html nao existe - so as
  # rotas de generateStaticParams fixo). Uma URL direta para um ID real que
  # nao foi pre-renderizado nao tem objeto correspondente no bucket: o S3
  # (via OAC) responde 403 (nao 404, pois o bucket e privado) para a
  # requisicao do CloudFront. Ambos os codigos redirecionam para
  # /index.html com 200, deixando o roteamento client-side (Next router)
  # resolver o ID via navegacao - ver docs/aws-migration-plan.md, Fase 4.
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }

  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = data.aws_acm_certificate.frontend.arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  tags = {
    Name = "musclefit-cdn"
  }
}

# CloudFront servindo foto de equipamento/logo de academia como arquivo
# estatico direto do bucket musclefit-uploads, reaproveitando a MESMA
# distribuicao do frontend (aws_cloudfront_distribution.musclefit_cdn, ver
# cloudfront.tf) em vez de criar uma distribuicao nova.
#
# Motivacao: GET /equipment/{id}/photo e GET /gyms/{id}/logo (api/app/gyms.py)
# hoje passam pela Lambda musclefit-api-core a cada leitura. A conta AWS tem
# o limite de execucoes concorrentes de Lambda travado em 10
# (ConcurrentExecutions), compartilhado pelas 6 Lambdas do projeto - uma tela
# com varias fotos de equipamento dispara uma rajada de invocacoes
# concorrentes, estoura esse limite, e o excesso e throttled pela AWS (API
# Gateway devolve 500 ao navegador, confirmado via Throttles no CloudWatch
# correlacionado com picos de Invocations).
#
# Foto de equipamento/logo de academia e dado nao sensivel, compartilhado
# entre usuarios (catalogo) - diferente de foto de perfil de usuario
# (users.py/user-photo.ts), que continua 100% como esta hoje (autenticada,
# via Lambda, baixo volume, sem esse problema de rajada). NAO adicionar
# origin/behavior para foto de usuario aqui.
#
# URL publica final: o path da URL e literalmente a S3 key ja gravada em
# equipment.photo_path/gyms.logo_path (ex.: "equipment/82.png",
# "gyms/1.png") - https://app.musclefit.com.br/equipment/82.png, zero
# transformacao. Ver api/app/gyms.py::_row_jsonable (campos photo_url/
# logo_url) e infra/lambda_api_core.tf (CLOUDFRONT_DISTRIBUTION_ID, usado
# para invalidar cache no upload/delete de foto).
#
# Card ilustrado de exercicio publicado segue o mesmo mecanismo (catalogo,
# nao sensivel, lido no Modo Treino por todos os usuarios): key versionada e
# imutavel exercise-cards/<exercise_id>/v<n>/..., URL publica montada pela
# api-core a partir de PUBLIC_ASSETS_BASE_URL (infra/lambda_api_core.tf) -
# ex.: https://app.musclefit.com.br/exercise-cards/12/v1/card.webp. Sem
# invalidacao: cada versao tem key propria.

# OAC propria para o bucket de uploads (nao reaproveita a OAC do frontend -
# cada origin S3 com OAC tem a sua, mesmo padrao de cloudfront.tf).
resource "aws_cloudfront_origin_access_control" "musclefit_uploads" {
  name                              = "musclefit-uploads-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Bucket policy do musclefit-uploads: GetObject liberado ao principal do
# CloudFront (cloudfront.amazonaws.com), restrito a esta distribuicao via
# aws:SourceArn - mesmo padrao de s3_frontend.tf. Resource escopado SO a
# equipment/*, gyms/* e exercise-cards/* (NUNCA "/*" - reports/* no mesmo
# bucket guarda PDF de relatorio, que tem que continuar inacessivel
# publicamente). A Lambda que gravava esses PDFs (musclefit-reports) foi
# removida, mas os objetos legados continuam no bucket ate o usuario decidir
# apaga-los - a restricao segue valendo.
#
# Cards ilustrados de exercicios: SO o prefixo PUBLICADO (exercise-cards/,
# versao aprovada por um admin) entra aqui. O prefixo de RASCUNHOS
# (exercise-card-drafts/, imagem gerada pela IA ainda nao revisada, gravada
# pela musclefit-plan-generator) fica DE FORA de proposito: rascunho so e
# visto pelo admin, por URL pre-assinada de curta duracao gerada pela
# api-core (role musclefit-api-core-exec), nunca pela CDN. Mesmo motivo de
# users/* (foto de perfil) nao estar na lista.
data "aws_iam_policy_document" "musclefit_uploads_cloudfront" {
  statement {
    sid    = "AllowCloudFrontOACReadPhotos"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.musclefit_uploads.arn}/equipment/*",
      "${aws_s3_bucket.musclefit_uploads.arn}/gyms/*",
      # Cards publicados. NUNCA exercise-card-drafts/* (ver cabecalho).
      "${aws_s3_bucket.musclefit_uploads.arn}/exercise-cards/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.musclefit_cdn.arn]
    }
  }

  # FIN-04 (varredura 2026-10-08): so HTTPS. Os behaviors /equipment/*, /gyms/* e
  # /exercise-cards/* sao redirect-to-https (o CloudFront chega ao S3 no protocolo
  # do viewer); api-core e plan-generator usam boto3 (HTTPS), e as URLs
  # pre-assinadas de rascunho tambem sao https.
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.musclefit_uploads.arn,
      "${aws_s3_bucket.musclefit_uploads.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "musclefit_uploads" {
  bucket = aws_s3_bucket.musclefit_uploads.id
  policy = data.aws_iam_policy_document.musclefit_uploads_cloudfront.json
}

# O `origin` e os tres `ordered_cache_behavior` para este bucket ficam
# fisicamente dentro do resource "aws_cloudfront_distribution" "musclefit_cdn"
# em cloudfront.tf (Terraform nao permite declarar blocos adicionais para um
# resource ja definido em outro arquivo - precisa ser o mesmo bloco). Ver
# cloudfront.tf para o origin "musclefit-uploads" e os path_pattern
# "/equipment/*", "/gyms/*" e "/exercise-cards/*", que apontam para o
# bucket/OAC definidos aqui.
#
# Nao ha colisao de path_pattern com o frontend Next.js: nao existem rotas
# "/equipment", "/gyms" nem "/exercise-cards" no app (as telas reais sao
# "/equipamentos-exercicios", "/academias" e "/exercicios").
#
# Nota sobre custom_error_response da distribuicao (cloudfront.tf): 403/404
# sao redirecionados para /index.html com 200 (fallback de SPA). Isso vale
# tambem para estes tres behaviors - uma foto ausente (ex.: id sem upload,
# ou key errada) cai nesse mesmo fallback e devolve o HTML do frontend com
# 200 em vez de um 404 "limpo". Aceito propositalmente (decisao do time):
# nao vale a complexidade de um custom_error_response por path_pattern so
# para isso. Para os cards e inofensivo: a api-core so grava o ponteiro da
# versao publicada no catalogo DEPOIS dos uploads, e o frontend so monta a
# URL a partir desse ponteiro.

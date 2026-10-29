# Bucket S3 para o build estatico do frontend (Next.js output: 'export') -
# ver docs/aws-migration-plan.md, secao 1.5 e Fase 4. Privado: acesso somente
# via CloudFront com Origin Access Control (OAC), sem bucket publico e sem
# OAI legado (ver cloudfront.tf).

resource "aws_s3_bucket" "musclefit_frontend_assets" {
  bucket = "musclefit-frontend-assets"

  tags = {
    Name = "musclefit-frontend-assets"
  }
}

resource "aws_s3_bucket_versioning" "musclefit_frontend_assets" {
  bucket = aws_s3_bucket.musclefit_frontend_assets.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "musclefit_frontend_assets" {
  bucket = aws_s3_bucket.musclefit_frontend_assets.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "musclefit_frontend_assets" {
  bucket = aws_s3_bucket.musclefit_frontend_assets.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Politica de bucket: GetObject liberado somente para o principal do
# CloudFront (cloudfront.amazonaws.com), restrito via aws:SourceArn a esta
# distribuicao especifica - o padrao recomendado para OAC (substitui a
# antiga OAI + bucket policy por canned ACL).
#
# jsonencode() direto, e NAO data "aws_iam_policy_document": um data source
# que referencia um recurso com mudanca pendente (aqui, a distribuicao) tem a
# leitura adiada para o apply, e a policy vira "(known after apply)" no plan -
# o Terraform regravaria esta policy (mesmo conteudo) a cada mudanca na
# musclefit-cdn, e isso exige s3:PutBucketPolicy neste bucket, que a role de
# deploy nao tem. Com jsonencode o ARN da distribuicao (que nao muda num
# update) e conhecido no plan e a policy so aparece no diff quando muda de
# verdade. Conteudo identico ao do data source anterior.
#
# Desde o FIN-04 (2026-10-10) a role de deploy TEM s3:PutBucketPolicy neste bucket
# (versao v2 da managed policy musclefit-gha-deploy-v2-s3-borda), mas o motivo do jsonencode continua
# valendo: evita regravar a policy sem mudanca real.
resource "aws_s3_bucket_policy" "musclefit_frontend_assets" {
  bucket = aws_s3_bucket.musclefit_frontend_assets.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowCloudFrontOACRead"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.musclefit_frontend_assets.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.musclefit_cdn.arn
          }
        }
      },
      # FIN-04 (varredura 2026-10-08): so HTTPS. Todos os behaviors da musclefit-cdn
      # sao redirect-to-https, e o CloudFront fala com origin S3 no protocolo do
      # viewer, entao o OAC sempre chega por TLS; o `aws s3 sync` do deploy tambem.
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.musclefit_frontend_assets.arn,
          "${aws_s3_bucket.musclefit_frontend_assets.arn}/*",
        ]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })
}

# FIN-04: cada `aws s3 sync --delete` do deploy deixa versoes nao correntes e
# delete markers (eram >=987 versoes + 276 markers em 2026-10-08). O site nao
# precisa de historico longo: rollback e um novo deploy do commit anterior. 30 dias
# ainda permitem restaurar um arquivo a mao (s3api get-object --version-id) logo
# apos um deploy ruim. Remove tambem os delete markers que ficam sem versao e
# aborta multipart incompleto (o sync usa multipart em arquivos grandes).
#
# Este recurso SUBSTITUI a lifecycle configuration inteira do bucket (em
# 2026-10-10 nao havia nenhuma: NoSuchLifecycleConfiguration). Regra nova deste
# bucket entra como outro `rule` aqui, nunca em outro recurso ou no console.
resource "aws_s3_bucket_lifecycle_configuration" "musclefit_frontend_assets" {
  bucket = aws_s3_bucket.musclefit_frontend_assets.id

  depends_on = [aws_s3_bucket_versioning.musclefit_frontend_assets]

  rule {
    id     = "versoes-antigas-30d"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

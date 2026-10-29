# Bucket S3 para fotos de logo de academia/equipamento, substituindo o
# volume Docker local do ambiente on-premise (Lambda nao tem disco
# persistente entre invocacoes) - ver docs/aws-migration-plan.md, Fase 3,
# item 2. Padrao S3 de sempre: versionado, sem acesso publico, SSE.

resource "aws_s3_bucket" "musclefit_uploads" {
  bucket = "musclefit-uploads"

  tags = {
    Name = "musclefit-uploads"
  }
}

resource "aws_s3_bucket_versioning" "musclefit_uploads" {
  bucket = aws_s3_bucket.musclefit_uploads.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "musclefit_uploads" {
  bucket = aws_s3_bucket.musclefit_uploads.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "musclefit_uploads" {
  bucket = aws_s3_bucket.musclefit_uploads.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_iam_role_policy" "musclefit_api_core_uploads" {
  name = "musclefit-api-core-uploads"
  role = aws_iam_role.musclefit_api_core_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EquipmentPhotosReadWrite"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.musclefit_uploads.arn,
          "${aws_s3_bucket.musclefit_uploads.arn}/*",
        ]
      }
    ]
  })
}

# Permite api/app/gyms.py::_invalidate_cloudfront_paths invalidar o cache do
# CloudFront (distribuicao unica do projeto, ver infra/cloudfront.tf e
# infra/cloudfront_uploads.tf) apos upload/delete de foto de equipamento ou
# logo de academia - senao a foto trocada continuaria servindo a versao
# antiga cacheada (CachingOptimized, TTL longo) ate expirar.
resource "aws_iam_role_policy" "musclefit_api_core_cloudfront_invalidation" {
  name = "musclefit-api-core-cloudfront-invalidation"
  role = aws_iam_role.musclefit_api_core_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvalidatePhotoAndLogoPaths"
        Effect   = "Allow"
        Action   = ["cloudfront:CreateInvalidation"]
        Resource = aws_cloudfront_distribution.musclefit_cdn.arn
      }
    ]
  })
}

# M69 - rascunhos de card ilustrado expiram em 7 dias.
#
# `exercise-card-drafts/<generation_id>/` guarda `illustration.png` (crua da
# OpenAI) e `preview.png` (card composto, o que o admin revisa), ~2-3 MB cada,
# gravados por api/app/exercise_card_jobs.py. A aprovacao NAO depende do
# rascunho depois de publicar: ela le o `preview.png`, recodifica e grava uma
# copia imutavel em `exercise-cards/<exercise_id>/v<n>/` (fora deste prefixo,
# nunca expira). Rascunho com mais de 7 dias ainda nao aprovado vira
# irrecuperavel: a aprovacao responde 410 DRAFT_EXPIRED e o admin regenera
# (M72).
#
# Os 7 dias abaixo (`expiration.days`) sao ESPELHADOS por
# `DRAFT_TTL_DAYS` em api/app/exercise_card_storage.py: a api-core marca o
# rascunho como expirado pelo relogio (`draft_expired` no payload, aprovacao
# bloqueada) sem esperar o objeto sumir. Mudou um, muda o outro.
#
# O bucket e versionado (aws_s3_bucket_versioning acima), entao `expiration`
# so poe um delete marker; `noncurrent_version_expiration` apaga de fato a
# versao antiga 1 dia depois. Sem multipart no codigo (put_object), mas o
# abort fica por higiene. Delete markers orfaos que sobram nao custam nada.
#
# ATENCAO: este recurso SUBSTITUI a lifecycle configuration INTEIRA do bucket
# (a API do S3 nao tem "adicionar regra"). Qualquer regra futura do
# musclefit-uploads TEM que entrar como outro bloco `rule` neste mesmo
# recurso - um segundo aws_s3_bucket_lifecycle_configuration (ou regra criada
# no console) apagaria esta. Em 2026-10-04 o bucket nao tinha nenhuma
# (NoSuchLifecycleConfiguration).
#
# Exige s3:PutLifecycleConfiguration na role musclefit-gha-deploy: statement
# S3MusclefitUploads de infra/iam-policies/consolidacao/musclefit-gha-deploy-v2-s3-borda.json
# (origem: infra/iam-policies/musclefit-gha-deploy-m69-m70.json).
resource "aws_s3_bucket_lifecycle_configuration" "musclefit_uploads" {
  bucket = aws_s3_bucket.musclefit_uploads.id

  # Em bucket versionado o provider recomenda criar o lifecycle depois do
  # versionamento.
  depends_on = [aws_s3_bucket_versioning.musclefit_uploads]

  rule {
    id     = "exercise-card-drafts-expire-7d"
    status = "Enabled"

    filter {
      prefix = "exercise-card-drafts/"
    }

    expiration {
      days = 7
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  # FIN-04 (varredura 2026-10-08): versoes nao correntes do bucket inteiro (troca ou
  # exclusao de foto de equipamento/academia/perfil) expiram em 90 dias. O
  # versionamento continua sendo a rede de seguranca contra exclusao indevida
  # (SEG-01), agora com janela de recuperacao de 90 dias em vez de infinita. Regras
  # sobrepostas: nos prefixos exercise-card-drafts/ e nutrition-labels/ vale a mais
  # curta (1 dia, nas regras desses prefixos), que e o comportamento do S3 quando
  # duas expiracoes casam o mesmo objeto.
  rule {
    id     = "versoes-antigas-90d"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    expiration {
      expired_object_delete_marker = true
    }
  }

  # Nutricao L6 (plano de nutricao, secoes 9 e 12): a foto do rotulo
  # (`nutrition-labels/<user_id>/<uuid>.jpg`, gravada pela api-core em POST
  # /nutrition/foods/label-photo e lida uma vez pelo worker da
  # musclefit-plan-generator) expira em 30 dias. O alimento cadastrado a partir
  # dela NAO depende da foto (os valores ficam em food_items; a chave nao e
  # gravada). Os 30 dias sao ESPELHADOS por `LABEL_PHOTO_RETENTION_DAYS` em
  # api/app/domain/nutrition/label_photo.py (texto do erro "foto nao esta mais
  # disponivel"; teste de paridade). Mesmo desenho da regra dos cards acima:
  # `expiration` poe o delete marker e `noncurrent_version_expiration` apaga a
  # versao 1 dia depois (bucket versionado). Num rollback do codigo, MANTER esta
  # regra ate o prefixo esvaziar: sem ela as fotos ja gravadas nunca expiram (a
  # versoes-antigas-90d so atua em versao nao corrente).
  rule {
    id     = "nutrition-labels-expire-30d"
    status = "Enabled"

    filter {
      prefix = "nutrition-labels/"
    }

    expiration {
      days = 30
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

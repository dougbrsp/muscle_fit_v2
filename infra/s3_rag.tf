# Bucket S3 com o conteudo de RAG (metodologia de treino/progressao/tabela de
# exercicios, ver api/privado/rag/markdown/ - submodulo do repositorio privado
# muscle_fit_privado, M103 P0), buscado em runtime pelas 4
# Lambdas via app/rag_storage.py::ensure_rag_local() para /tmp - ver
# docs/aws-migration-plan.md e o comentario no topo de rag_storage.py.
#
# Decisao: conteudo em S3 com fetch em runtime (nao embutido na imagem), para
# poder atualizar a metodologia sem rebuildar/republicar as imagens das
# Lambdas. O Terraform e a fonte de verdade do conteudo do bucket: todo
# arquivo em api/privado/rag/markdown/**/*.md e publicado como objeto via
# aws_s3_object.musclefit_rag_markdown (for_each sobre fileset), com etag =
# filemd5(...) - qualquer mudanca de conteudo no repo e refletida no bucket
# no proximo apply, sem passo manual de upload.
#
# Mesmo padrao de s3_uploads.tf: sem acesso publico, SSE, versionado.

# M103 P0: sem o submodulo api/privado (checkout sem `submodules`, fork sem a
# deploy key, clone local sem `submodule update --init`) o fileset fica VAZIO
# e o for_each de aws_s3_object.musclefit_rag_markdown planejaria APAGAR todo o
# RAG do bucket de producao. A precondition no bucket (instancia unica, avaliada
# mesmo com o for_each vazio; nao muda o plano quando passa) faz o plan FALHAR.
# Os workflows terraform.yml e deploy-producao.yml tambem conferem o diretorio
# antes do plan.
locals {
  rag_markdown_dir   = "${path.module}/../api/privado/rag/markdown"
  rag_markdown_files = fileset(local.rag_markdown_dir, "**/*.md")
}

resource "aws_s3_bucket" "musclefit_rag" {
  bucket = "musclefit-rag"

  lifecycle {
    precondition {
      condition     = length(local.rag_markdown_files) > 0
      error_message = "api/privado/rag/markdown esta vazio ou ausente (submodulo muscle_fit_privado nao inicializado). O plan apagaria todo o RAG do S3. Inicialize o submodulo (checkout com submodules e a PRIVADO_DEPLOY_KEY, ou submodule update --init) antes do plan."
    }
  }

  tags = {
    Name = "musclefit-rag"
  }
}

resource "aws_s3_bucket_versioning" "musclefit_rag" {
  bucket = aws_s3_bucket.musclefit_rag.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "musclefit_rag" {
  bucket = aws_s3_bucket.musclefit_rag.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "musclefit_rag" {
  bucket = aws_s3_bucket.musclefit_rag.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# FIN-04 (varredura 2026-10-08): so HTTPS. Quem le o bucket sao as Lambdas, via
# boto3 (HTTPS por padrao), e quem escreve e o apply (aws_s3_object abaixo), tambem
# por HTTPS. Em 2026-10-10 o bucket nao tinha bucket policy (NoSuchBucketPolicy).
resource "aws_s3_bucket_policy" "musclefit_rag" {
  bucket = aws_s3_bucket.musclefit_rag.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.musclefit_rag.arn, "${aws_s3_bucket.musclefit_rag.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.musclefit_rag]
}

# FIN-04: cada mudanca de conteudo em api/privado/rag/markdown/ vira uma versao nao corrente.
# O repositorio ja guarda o historico do texto; 90 dias de versoes no bucket bastam
# para comparar o que as Lambdas leram num incidente recente. Sem delete marker
# relevante (o apply so apaga objeto quando um .md sai do repo), mas a regra limpa
# os que sobrarem. Este recurso SUBSTITUI a lifecycle configuration inteira do
# bucket (em 2026-10-10 nao havia nenhuma).
resource "aws_s3_bucket_lifecycle_configuration" "musclefit_rag" {
  bucket = aws_s3_bucket.musclefit_rag.id

  depends_on = [aws_s3_bucket_versioning.musclefit_rag]

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

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# Publica cada .md de api/privado/rag/markdown/ (submodulo; um nivel acima de
# infra/) como um objeto sob o prefixo "markdown/" - mesmo prefixo lido
# por default em RAG_S3_PREFIX (ver rag_storage.py::_env_config). Preserva a
# arvore, incluindo as subpastas de TIPOS_TREINO/ (nomes com espaco e ponto).
# A key e relativa a rag/markdown, sem o caminho do submodulo: as keys sao as
# mesmas de antes do M103 (so o `source` local mudou de lugar).
resource "aws_s3_object" "musclefit_rag_markdown" {
  for_each = local.rag_markdown_files

  bucket       = aws_s3_bucket.musclefit_rag.id
  key          = "markdown/${each.value}"
  source       = "${local.rag_markdown_dir}/${each.value}"
  etag         = filemd5("${local.rag_markdown_dir}/${each.value}")
  content_type = "text/markdown; charset=utf-8"
}

output "rag_bucket_name" {
  description = "Nome do bucket S3 com o conteudo de RAG - usado como RAG_S3_BUCKET nas 4 Lambdas."
  value       = aws_s3_bucket.musclefit_rag.bucket
}

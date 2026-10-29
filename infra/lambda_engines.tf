# Lambda musclefit-engines (Lambda 2 da migracao — motores deterministicos:
# recuperacao muscular, volume de treino, progressao de carga/reps e
# ativacao muscular, extraidos de musclefit-api-core — ver
# docs/aws-migration-plan.md e api/Dockerfile.engines/api/app/main_engines.py).
#
# NENHUMA chamada a OpenAI neste caminho: a role de execucao desta Lambda
# (musclefit-engines-exec) NAO tem `secretsmanager:GetSecretValue` sobre
# aws_secretsmanager_secret.openai_api_key nem sobre
# aws_secretsmanager_secret.secret_encryption_key (nenhum modulo
# incluido em app/main_engines.py usa criptografia de dado sensivel do
# usuario) — so o secret do token de API (autenticacao via X-API-Token,
# igual a todo o resto) e o secret master da Aurora.
#
# FastAPI + Lambda Web Adapter (igual a musclefit-api-core, NAO o padrao
# "sem Web Adapter" de musclefit-ai-generators): esta Lambda recebe trafego
# HTTP direto do API Gateway nos paths /analytics/* e /exercises/suggested-*
# (ver infra/api_gateway.tf). Nenhuma outra Lambda a invoca: o helper
# lambda-a-lambda (app/engines_client.py) nunca teve chamador e saiu no
# SEG-04 junto com as policies `*-invoke-engines` das roles de api-core e
# ai-generators.

variable "engines_image_tag" {
  description = "Tag da imagem em musclefit-engines (ECR) a publicar na Lambda. Atualizada a cada build/push manual (ver infra/README.md)."
  type        = string
  default     = "latest"
}

resource "aws_ecr_repository" "musclefit_engines" {
  name = "musclefit-engines"
  # AWS-14: tags imutaveis. O deploy-producao.yml publica repo:<sha do commit>;
  # uma tag nunca muda de imagem depois de publicada (rastreabilidade e
  # rollback por commit). `latest` fica congelada na ultima imagem publicada
  # antes desta mudanca e so serve para a CRIACAO de funcao pelo Terraform
  # (o deploy em seguida troca para o <sha>; ver lifecycle das Lambdas).
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "musclefit-engines"
  }
}

resource "aws_ecr_lifecycle_policy" "musclefit_engines" {
  repository = aws_ecr_repository.musclefit_engines.name

  policy = jsonencode({
    rules = [
      # AWS-14: protege a imagem `latest` congelada (image_uri de criacao das
      # Lambdas no Terraform). Imagem que casa com uma regra de prioridade
      # maior nao e expirada por regra de prioridade menor.
      {
        rulePriority = 1
        description  = "Preserva a imagem latest (criacao de funcao pelo Terraform)"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["latest"]
          countType      = "imageCountMoreThan"
          countNumber    = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Mantem so as 10 imagens mais recentes"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = { type = "expire" }
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# IAM: role propria, SEM acesso ao secret openai-api-key nem ao secret de
# criptografia — escopo minimo (motor deterministico, sem LLM, sem dado
# criptografado de usuario).
# ---------------------------------------------------------------------------

resource "aws_iam_role" "musclefit_engines_exec" {
  name = "musclefit-engines-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "musclefit_engines_logs" {
  role       = aws_iam_role.musclefit_engines_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "musclefit_engines_data_api" {
  name = "musclefit-engines-data-api"
  role = aws_iam_role.musclefit_engines_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RdsDataApi"
        Effect = "Allow"
        Action = [
          "rds-data:ExecuteStatement",
          "rds-data:BatchExecuteStatement",
          "rds-data:BeginTransaction",
          "rds-data:CommitTransaction",
          "rds-data:RollbackTransaction",
        ]
        Resource = aws_rds_cluster.musclefit_db.arn
      },
      {
        # Sem openai_api_key nem secret_encryption_key aqui — DIFERENTE de
        # aws_iam_role_policy.musclefit_api_core_data_api /
        # musclefit_ai_generators_data_api de proposito (ver comentario no
        # topo do arquivo).
        Sid    = "AuroraAndApiTokenSecrets"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          aws_rds_cluster.musclefit_db.master_user_secret[0].secret_arn,
          aws_secretsmanager_secret.api_token.arn,
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy" "musclefit_engines_rag" {
  name = "musclefit-engines-rag"
  role = aws_iam_role.musclefit_engines_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RagBucketList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.musclefit_rag.arn
      },
      {
        Sid      = "RagBucketRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.musclefit_rag.arn}/*"
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "musclefit_engines" {
  name              = "/aws/lambda/musclefit-engines"
  retention_in_days = 14
}

resource "aws_lambda_function" "musclefit_engines" {
  function_name = "musclefit-engines"
  role          = aws_iam_role.musclefit_engines_exec.arn

  package_type = "Image"
  image_uri    = "${aws_ecr_repository.musclefit_engines.repository_url}:${var.engines_image_tag}"

  # Mesmo teto de musclefit-api-core - parte deste trafego vem do API
  # Gateway (timeout de 29s do proprio API Gateway REST se aplica).
  timeout     = 29
  memory_size = 1024
  # Teto por funcao (AWS-01); valores e justificativa em variables.tf.
  reserved_concurrent_executions = var.lambda_reserved_concurrency["engines"]

  environment {
    variables = {
      AURORA_CLUSTER_ARN   = aws_rds_cluster.musclefit_db.arn
      AURORA_SECRET_ARN    = aws_rds_cluster.musclefit_db.master_user_secret[0].secret_arn
      AURORA_DATABASE      = var.db_name
      API_TOKEN_SECRET_ARN = aws_secretsmanager_secret.api_token.arn

      # Hidratacao de RAG em runtime - ver infra/s3_rag.tf e
      # app/rag_storage.py::ensure_rag_local(). musclefit-engines consome RAG
      # via intelligence/report_data_service.py (mesmo pacote app/ da imagem
      # musclefit-api-core, ver api/Dockerfile.engines).
      RAG_S3_BUCKET    = aws_s3_bucket.musclefit_rag.bucket
      RAG_S3_PREFIX    = "markdown/"
      RAG_MARKDOWN_DIR = "/tmp/rag"

      # Sessao via cookie httpOnly (Fase 3) - ver api/app/auth.py,
      # api/app/security.py e api/app/main_engines.py (CORS com
      # allow_credentials).
      SESSION_COOKIE_DOMAIN = ".musclefit.com.br"
      # Inclui a origem do Zepp porque /devices/pair precisa ser chamavel a
      # partir dela via navegador - ver comentario em api/app/main.py.
      CORS_ALLOWED_ORIGINS = "https://app.musclefit.com.br,https://zepp-os.zepp.com"
    }
  }

  depends_on = [aws_cloudwatch_log_group.musclefit_engines]

  tags = {
    Name = "musclefit-engines"
  }

  # AWS-14: o deploy-producao.yml publica cada imagem com a tag IMUTAVEL do
  # commit (repo:<sha>) e aponta a funcao para ela via update-function-code.
  # O Terraform so define image_uri na CRIACAO da funcao (var.*_image_tag,
  # default "latest": tag congelada e protegida no lifecycle do ECR). Sem
  # ignore_changes, o proximo apply veria repo:<sha> != repo:latest e
  # REVERTERIA a Lambda para a imagem congelada.
  lifecycle {
    ignore_changes = [image_uri]
  }
}

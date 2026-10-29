# Lambda musclefit-api-core: FastAPI inteiro via Lambda Web Adapter, imagem
# de container (ver docs/aws-migration-plan.md, secao 1.2 e Fase 3).

data "aws_caller_identity" "current" {}

variable "api_core_image_tag" {
  description = "Tag da imagem em musclefit-api-core (ECR) a publicar na Lambda. Atualizada a cada build/push manual (ver infra/README.md)."
  type        = string
  default     = "latest"
}

variable "openai_model_plan" {
  description = "Modelo OpenAI usado na geracao de plano de treino (workout_plan_ai.py)."
  type        = string
  # Era "gpt-4o" e nunca chegou a valer: o codigo lia OPENAI_WORKOUT_PLAN_MODEL
  # (que ninguem define) e caia no default "gpt-5.4-mini". O PR-16 fez
  # workout_plan_ai.py finalmente ler OPENAI_MODEL_PLAN, e este default passa a
  # ser o mesmo valor que ja estava EFETIVAMENTE em uso - a correcao da leitura
  # nao troca o modelo de producao.
  default = "gpt-5.4-mini"
}

variable "openai_model_cardio" {
  description = "Modelo OpenAI usado na recomendacao de cardio (cardio_agent_ai.py, le OPENAI_MODEL_CARDIO)."
  type        = string
  # Era "gpt-4o-mini" mas nunca valeu: o codigo lia OPENAI_CARDIO_AGENT_MODEL
  # (que ninguem define) e caia no default "gpt-5.4-mini". Agora o codigo le
  # esta env; o default passa a ser o modelo que JA estava efetivamente em uso.
  default = "gpt-5.4-mini"
}

resource "aws_iam_role" "musclefit_api_core_exec" {
  name = "musclefit-api-core-exec"

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

resource "aws_iam_role_policy_attachment" "musclefit_api_core_logs" {
  role       = aws_iam_role.musclefit_api_core_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "musclefit_api_core_data_api" {
  name = "musclefit-api-core-data-api"
  role = aws_iam_role.musclefit_api_core_exec.id

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
        Sid    = "AuroraSecret"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          aws_rds_cluster.musclefit_db.master_user_secret[0].secret_arn,
          aws_secretsmanager_secret.openai_api_key.arn,
          aws_secretsmanager_secret.api_token.arn,
          aws_secretsmanager_secret.secret_encryption_key.arn,
          aws_secretsmanager_secret.turnstile_secret_key.arn,
          aws_secretsmanager_secret.usda_api_key.arn,
        ]
      }
    ]
  })
}

# Envio do e-mail de "Esqueci minha senha" (M5) via SES - ver infra/ses.tf.
# Resource = SOMENTE a identidade de dominio musclefit.com.br (sem "*"), e a
# condition ses:FromAddress trava o remetente em no-reply@musclefit.com.br:
# mesmo com a identidade de dominio inteira verificada, esta Lambda nao
# consegue enviar como outro @musclefit.com.br. Sem destinatario restrito
# (ses:Recipients) porque o reset vai para o e-mail que o usuario cadastrou.
resource "aws_iam_role_policy" "musclefit_api_core_ses" {
  name = "musclefit-api-core-ses"
  role = aws_iam_role.musclefit_api_core_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SesSendPasswordReset"
        Effect = "Allow"
        Action = [
          "ses:SendEmail",
          "ses:SendRawEmail",
        ]
        Resource = aws_sesv2_email_identity.musclefit_domain.arn
        Condition = {
          StringEquals = {
            "ses:FromAddress" = local.password_reset_from_email
          }
        }
      }
    ]
  })
}

locals {
  # Contrato com a aplicacao (api/app, rotas de reset de senha): estes DOIS
  # nomes sao lidos pelo codigo como estao. Mudar aqui exige mudar la.
  password_reset_from_email = "no-reply@${local.ses_domain}"
  app_base_url              = "https://app.musclefit.com.br"
}

# ---------------------------------------------------------------------------
# Cards ilustrados de exercicios (geracao por IA + aprovacao humana) - UM
# lugar so para os valores. Os NOMES das env vars sao contrato com api/app
# (modulos exercise_card_* e public_assets.py); mudar aqui exige mudar la.
#
# - exercise_card_generation_enabled: liga/desliga as rotas de geracao na
#   api-core (desligada = 503 exercise_card_generation_disabled). LIGADA
#   ("true") em producao desde o PR6 (2026-10-04), depois do checkpoint de
#   calibracao visual em DEV. Para desligar: PR voltando SO este valor para
#   "false" (nenhuma outra mudanca de infra; cards ja publicados continuam
#   servidos pela CDN e visiveis no Modo Treino).
# - Limites: MAX_GENERATIONS_PER_DAY (429 ao estourar, contagem em tabela),
#   MAX_IN_FLIGHT (teto global de geracoes em andamento - a conta tem so 10
#   execucoes Lambda concorrentes para todas as funcoes), STALE_MINUTES
#   (geracao PENDING/GENERATING mais velha que isso vira FAILED WORKER_LOST).
# - exercise_card_image_env: modelo/qualidade/tamanho da OpenAI Images e o
#   timeout da chamada (150 s, ABAIXO dos 180 s da musclefit-plan-generator -
#   ver infra/lambda_ai_generators.tf). Vai para a api-core (grava o que foi
#   pedido na linha da geracao) e para o worker na plan-generator (quem chama
#   a OpenAI), via local.exercise_card_worker_env.
# - public_assets_base_url: base da URL publica dos arquivos servidos pela
#   CDN a partir do musclefit-uploads (cards publicados em
#   /exercise-cards/..., ver infra/cloudfront.tf). Em DEV o mesmo nome aponta
#   para o LocalStack (docker-compose.yml) - mesmo mecanismo, so o valor muda.
# ---------------------------------------------------------------------------
locals {
  exercise_card_generation_enabled = "true"

  exercise_card_image_env = {
    OPENAI_IMAGE_MODEL           = "gpt-image-1.5"
    OPENAI_IMAGE_QUALITY         = "medium"
    OPENAI_IMAGE_SIZE            = "1024x1536"
    OPENAI_IMAGE_TIMEOUT_SECONDS = "150"
  }

  public_assets_base_url = "https://app.musclefit.com.br"
}

resource "aws_iam_role_policy" "musclefit_api_core_rag" {
  name = "musclefit-api-core-rag"
  role = aws_iam_role.musclefit_api_core_exec.id

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

resource "aws_cloudwatch_log_group" "musclefit_api_core" {
  name              = "/aws/lambda/musclefit-api-core"
  retention_in_days = 14
}

resource "aws_lambda_function" "musclefit_api_core" {
  function_name = "musclefit-api-core"
  role          = aws_iam_role.musclefit_api_core_exec.arn

  package_type = "Image"
  image_uri    = "${aws_ecr_repository.musclefit_api_core.repository_url}:${var.api_core_image_tag}"

  timeout     = 29 # teto do API Gateway REST - ver docs/aws-migration-plan.md, secao 4, risco 1
  memory_size = 1024
  # Teto por funcao (AWS-01); valores e justificativa em variables.tf.
  reserved_concurrent_executions = var.lambda_reserved_concurrency["api-core"]

  environment {
    # As flags dos motores deterministicos vem de
    # `local.deterministic_engines_env` (infra/lambda_motores_deterministicos.tf),
    # que documenta por que esta Lambda recebe as DUAS: o hook do `finish` le
    # `PROGRESSION_ENGINE` e o congelamento da prescricao exige as duas.
    variables = merge({
      AURORA_CLUSTER_ARN               = aws_rds_cluster.musclefit_db.arn
      AURORA_SECRET_ARN                = aws_rds_cluster.musclefit_db.master_user_secret[0].secret_arn
      AURORA_DATABASE                  = var.db_name
      OPENAI_API_KEY_SECRET_ARN        = aws_secretsmanager_secret.openai_api_key.arn
      API_TOKEN_SECRET_ARN             = aws_secretsmanager_secret.api_token.arn
      SECRET_ENCRYPTION_KEY_SECRET_ARN = aws_secretsmanager_secret.secret_encryption_key.arn
      OPENAI_MODEL_PLAN                = var.openai_model_plan
      OPENAI_MODEL_CARDIO              = var.openai_model_cardio
      ASYNC_JOBS_TABLE                 = aws_dynamodb_table.musclefit_async_jobs.name
      PLAN_GENERATOR_FUNCTION_NAME     = aws_lambda_function.musclefit_plan_generator.function_name
      CARDIO_AGENT_FUNCTION_NAME       = aws_lambda_function.musclefit_cardio_agent.function_name
      WORKOUT_AI_FUNCTION_NAME         = aws_lambda_function.musclefit_workout_ai.function_name
      EQUIPMENT_PHOTOS_BUCKET          = aws_s3_bucket.musclefit_uploads.bucket
      # Usado por api/app/gyms.py::_invalidate_cloudfront_paths para
      # invalidar o cache do CloudFront quando uma foto de equipamento/logo
      # de academia e trocada ou removida (ver infra/cloudfront_uploads.tf).
      CLOUDFRONT_DISTRIBUTION_ID = aws_cloudfront_distribution.musclefit_cdn.id

      # Hidratacao de RAG em runtime (ver infra/s3_rag.tf e
      # app/rag_storage.py::ensure_rag_local()). RAG_MARKDOWN_DIR aponta pra
      # /tmp (unico disco gravavel de uma Lambda) em vez do default /app/rag
      # usado no ambiente on-premice (bind mount).
      RAG_S3_BUCKET    = aws_s3_bucket.musclefit_rag.bucket
      RAG_S3_PREFIX    = "markdown/"
      RAG_MARKDOWN_DIR = "/tmp/rag"

      # Sessao via cookie httpOnly (Fase 3) - ver api/app/auth.py,
      # api/app/security.py e api/app/main.py (CORS com allow_credentials).
      SESSION_COOKIE_DOMAIN = ".musclefit.com.br"
      # Inclui a origem do Zepp porque /devices/pair precisa ser chamavel a
      # partir dela via navegador - ver comentario em api/app/main.py.
      CORS_ALLOWED_ORIGINS = "https://app.musclefit.com.br,https://zepp-os.zepp.com"

      # "Esqueci minha senha" (M5) - ver infra/ses.tf e a policy
      # musclefit-api-core-ses acima. PASSWORD_RESET_FROM_EMAIL e o From do
      # boto3.client("ses").send_email; APP_BASE_URL monta o link de reset
      # que vai no corpo do e-mail. Nomes sao contrato com api/app.
      PASSWORD_RESET_FROM_EMAIL = local.password_reset_from_email
      APP_BASE_URL              = local.app_base_url
      # Captcha Cloudflare Turnstile no formulario de reset: a SECRET key nao
      # entra em texto aqui - so o ARN do secret (infra/secrets.tf), que
      # api/app/secrets_bootstrap.py resolve para TURNSTILE_SECRET_KEY no cold
      # start, igual a OPENAI_API_KEY/API_TOKEN/SECRET_ENCRYPTION_KEY.
      TURNSTILE_SECRET_KEY_SECRET_ARN = aws_secretsmanager_secret.turnstile_secret_key.arn
      # Nutricao (catalogo USDA FoodData Central, Lote L3b): mesmo mecanismo,
      # so o ARN (infra/secrets.tf); secrets_bootstrap.py resolve USDA_API_KEY.
      # SO a api-core recebe: as rotas do catalogo ficam aqui (plano de
      # nutricao, secao 8.1); engines/IA/intervals-sync nao chamam o USDA.
      USDA_API_KEY_SECRET_ARN = aws_secretsmanager_secret.usda_api_key.arn

      # Cards ilustrados de exercicios - ver o bloco de locals acima. Os
      # OPENAI_IMAGE_* entram pelo merge() com local.exercise_card_image_env.
      EXERCISE_CARD_GENERATION_ENABLED      = local.exercise_card_generation_enabled
      EXERCISE_CARD_MAX_GENERATIONS_PER_DAY = "100"
      EXERCISE_CARD_MAX_IN_FLIGHT           = "2"
      EXERCISE_CARD_STALE_MINUTES           = "10"
      PUBLIC_ASSETS_BASE_URL                = local.public_assets_base_url
    }, local.exercise_card_image_env, local.deterministic_engines_env)
  }

  depends_on = [aws_cloudwatch_log_group.musclefit_api_core]

  tags = {
    Name = "musclefit-api-core"
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

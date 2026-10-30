# Lambdas de IA assincronas (musclefit-plan-generator, musclefit-cardio-agent)
# - ver docs/aws-migration-plan.md, secao 1.2 e Fase 3. Mesma imagem
# (musclefit-ai-generators, ver api/Dockerfile.ai - SEM Lambda Web Adapter),
# funcoes distintas via image_config.command apontando pro handler certo.

variable "openai_model_periodization" {
  description = "Modelo OpenAI usado pelo Motor de Periodizacao (app/periodization, saida estruturada json_schema strict) na Lambda plan-generator."
  type        = string
  default     = "gpt-5.4-mini"
}

variable "openai_model_exercise_description" {
  description = "Modelo OpenAI usado na descricao de execucao dos exercicios (exercise_description_ai.py) na Lambda plan-generator - mesmo default do codigo."
  type        = string
  default     = "gpt-5.4-mini"
}

variable "openai_model_nutrition_vision" {
  description = "Modelo OpenAI da leitura da foto do rotulo nutricional (app/nutrition_label_ai.py, saida estruturada com imagem, job do Lote L6 de nutricao) na Lambda plan-generator - mesmo default do codigo (DEFAULT_MODEL, teste de paridade)."
  type        = string
  default     = "gpt-5.4-mini"
}

variable "ai_generators_image_tag" {
  description = "Tag da imagem em musclefit-ai-generators (ECR) a publicar nas Lambdas plan-generator/cardio-agent. Atualizada a cada build/push manual (ver infra/README.md)."
  type        = string
  default     = "latest"
}

resource "aws_ecr_repository" "musclefit_ai_generators" {
  name = "musclefit-ai-generators"
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
    Name = "musclefit-ai-generators"
  }
}

resource "aws_ecr_lifecycle_policy" "musclefit_ai_generators" {
  repository = aws_ecr_repository.musclefit_ai_generators.name

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
# IAM: role compartilhada pelas duas funcoes (mesmo escopo de acesso a
# dados/segredos de musclefit-api-core-exec + escrita no DynamoDB de jobs).
# ---------------------------------------------------------------------------

resource "aws_iam_role" "musclefit_ai_generators_exec" {
  name = "musclefit-ai-generators-exec"

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

resource "aws_iam_role_policy_attachment" "musclefit_ai_generators_logs" {
  role       = aws_iam_role.musclefit_ai_generators_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "musclefit_ai_generators_data_api" {
  name = "musclefit-ai-generators-data-api"
  role = aws_iam_role.musclefit_ai_generators_exec.id

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
        Sid    = "AuroraAndAppSecrets"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          aws_rds_cluster.musclefit_db.master_user_secret[0].secret_arn,
          aws_secretsmanager_secret.openai_api_key.arn,
          aws_secretsmanager_secret.anthropic_api_key.arn,
          # SEG-04: sem `api_token` - nenhuma das duas Lambdas autentica nem
          # chama a API HTTP (gyms/catalogo vem direto do banco).
          aws_secretsmanager_secret.secret_encryption_key.arn,
        ]
      },
      {
        Sid    = "AsyncJobsWrite"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:GetItem",
        ]
        Resource = aws_dynamodb_table.musclefit_async_jobs.arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "musclefit_ai_generators_rag" {
  name = "musclefit-ai-generators-rag"
  role = aws_iam_role.musclefit_ai_generators_exec.id

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

# Cards ilustrados de exercicios: o worker (job_kind "exercise-card" da
# musclefit-plan-generator) grava o RASCUNHO gerado pela OpenAI em
# exercise-card-drafts/<generation_id>/preview.png no bucket de uploads. So
# PutObject e so nesse prefixo: nada de Get/Delete/List, nada de
# exercise-cards/* (a PUBLICACAO - composicao do logo, WebP, thumbnail e upload
# da versao aprovada - roda na api-core, que ja tem leitura/escrita no bucket
# via musclefit-api-core-uploads em infra/s3_uploads.tf). O rascunho fica fora
# da CDN (ver infra/cloudfront_uploads.tf) e e servido ao admin por URL
# pre-assinada.
#
# A role e compartilhada com a musclefit-cardio-agent (que nao grava nada em
# S3); o escopo por prefixo limita o que uma das duas poderia escrever.
resource "aws_iam_role_policy" "musclefit_ai_generators_exercise_card_drafts" {
  name = "musclefit-ai-generators-exercise-card-drafts"
  role = aws_iam_role.musclefit_ai_generators_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ExerciseCardDraftsWrite"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.musclefit_uploads.arn}/exercise-card-drafts/*"
      }
    ]
  })
}

# Nutricao L6 (plano de nutricao, secao 12): o worker da leitura do rotulo
# (job `ai_usage.LABEL_PHOTO_JOB_TYPE` da musclefit-plan-generator) LE a foto
# que a api-core gravou em nutrition-labels/<user_id>/<uuid>.jpg no bucket de
# uploads. So GetObject e so nesse prefixo (o mesmo de
# `label_photo.LABEL_PHOTO_S3_PREFIX`, teste de paridade): nada de Put/Delete/
# List, nada de outro prefixo. A gravacao e da api-core
# (musclefit-api-core-uploads em infra/s3_uploads.tf ja cobre o bucket todo);
# a foto expira em 30 dias pela regra nutrition-labels-expire-30d.
#
# A role e compartilhada com a musclefit-cardio-agent (que nao le nada disso);
# o escopo por prefixo limita o que uma das duas poderia ler.
resource "aws_iam_role_policy" "musclefit_ai_generators_nutrition_labels" {
  name = "musclefit-ai-generators-nutrition-labels"
  role = aws_iam_role.musclefit_ai_generators_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "NutritionLabelPhotosRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.musclefit_uploads.arn}/nutrition-labels/*"
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "musclefit_plan_generator" {
  name              = "/aws/lambda/musclefit-plan-generator"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "musclefit_cardio_agent" {
  name              = "/aws/lambda/musclefit-cardio-agent"
  retention_in_days = 14
}

locals {
  # Provedor LLM por feature + ARN da chave da Anthropic: infra/llm_providers.tf.
  ai_generators_env = merge({
    AURORA_CLUSTER_ARN               = aws_rds_cluster.musclefit_db.arn
    AURORA_SECRET_ARN                = aws_rds_cluster.musclefit_db.master_user_secret[0].secret_arn
    AURORA_DATABASE                  = var.db_name
    OPENAI_API_KEY_SECRET_ARN        = aws_secretsmanager_secret.openai_api_key.arn
    SECRET_ENCRYPTION_KEY_SECRET_ARN = aws_secretsmanager_secret.secret_encryption_key.arn
    OPENAI_MODEL_PLAN                = var.openai_model_plan
    OPENAI_MODEL_CARDIO              = var.openai_model_cardio
    # Motor de Periodizacao (PR-16): so a plan-generator precisa deste modelo -
    # a api-core apenas cria o job e responde 202, sem falar com a OpenAI. Sem
    # esta env var (ou sem chave), a geracao falha de forma limpa (job `failed`
    # com mensagem de configuracao) e nada existente e afetado.
    OPENAI_MODEL_PERIODIZATION = var.openai_model_periodization
    ASYNC_JOBS_TABLE           = aws_dynamodb_table.musclefit_async_jobs.name
    # SEG-04: sem INTERNAL_API_BASE_URL nem API_TOKEN. A geracao de ficha
    # lia `/gyms` e `/exercise-mapping-catalog` por self-call HTTP na URL
    # execute-api com o token mestre; agora le direto do banco com as mesmas
    # funcoes de servico das rotas (`gyms.list_gym_items`,
    # `exercise_mapping_catalog.query_mapping_catalog`). Pre-requisito para
    # `disable_execute_api_endpoint` (AWS-08).

    # Hidratacao de RAG em runtime - ver infra/s3_rag.tf e
    # app/rag_storage.py::ensure_rag_local(). Consumido por
    # workout_plan_ai.py/abc_workout_ai.py (musclefit-plan-generator) e por
    # cardio_agent_ai.py (musclefit-cardio-agent), ambos via rag_chunking.py.
    RAG_S3_BUCKET    = aws_s3_bucket.musclefit_rag.bucket
    RAG_S3_PREFIX    = "markdown/"
    RAG_MARKDOWN_DIR = "/tmp/rag"
  }, local.llm_provider_env)

  # Cards ilustrados de exercicios - o que o worker (job_kind "exercise-card",
  # so na musclefit-plan-generator) precisa: o bucket onde grava o rascunho
  # (mesmo nome de env que a api-core ja usa para o musclefit-uploads) e o
  # grupo OPENAI_IMAGE_* (o worker e quem chama /v1/images/generations).
  # Entra na plan-generator por merge() e NAO em local.ai_generators_env pelo
  # mesmo motivo das flags dos motores deterministicos: esse local e
  # compartilhado com a musclefit-cardio-agent, que nao le nada disso.
  # Os valores de OPENAI_IMAGE_* vem de local.exercise_card_image_env
  # (infra/lambda_api_core.tf), a mesma fonte da api-core.
  exercise_card_worker_env = merge(
    { EQUIPMENT_PHOTOS_BUCKET = aws_s3_bucket.musclefit_uploads.bucket },
    local.exercise_card_image_env,
  )
}

resource "aws_lambda_function" "musclefit_plan_generator" {
  function_name = "musclefit-plan-generator"
  role          = aws_iam_role.musclefit_ai_generators_exec.arn

  package_type = "Image"
  image_uri    = "${aws_ecr_repository.musclefit_ai_generators.repository_url}:${var.ai_generators_image_tag}"

  image_config {
    command = ["app.lambda_plan_generator.handler"]
  }

  # Chamada a OpenAI para gerar uma ficha inteira pode levar bem mais que os
  # 29s do API Gateway (por isso o padrao assincrono) - teto generoso aqui,
  # nao ha timeout de API Gateway neste caminho (invocacao Event direta).
  #
  # 180 s (era 120) por causa do card ilustrado de exercicio: a OpenAI
  # documenta "ate 2 minutos" para imagem com prompt complexo. A chamada usa
  # OPENAI_IMAGE_TIMEOUT_SECONDS=150, de proposito ABAIXO deste teto: o
  # worker precisa ter tempo de marcar a geracao como FAILED e sair limpo. Se
  # a Lambda fosse morta por timeout, o invoke assincrono seria reentregue e
  # a imagem cobrada de novo. Ficha/periodizacao nao mudam de comportamento
  # (so ganham folga). A cardio-agent continua em 120 s.
  timeout     = 180
  memory_size = 1024
  # Teto por funcao (AWS-01); valores e justificativa em variables.tf.
  reserved_concurrent_executions = var.lambda_reserved_concurrency["plan-generator"]

  environment {
    # A GERACAO da ficha roda aqui (nao na api-core):
    # `plan_generation_core.dispatch_plan_generation` -> `generation/mode_a.py`
    # / `generation/mode_b.py`, os dois chamando `deterministic_enabled()`, que
    # exige as DUAS flags. Por isso o `merge()` e nao
    # `local.ai_generators_env`: esse local e compartilhado com a
    # musclefit-cardio-agent, que nao le flag nenhuma. Ver
    # infra/lambda_motores_deterministicos.tf. O card ilustrado de exercicio
    # entra pelo mesmo motivo via local.exercise_card_worker_env (acima).
    variables = merge(
      local.ai_generators_env,
      local.deterministic_engines_env,
      local.exercise_card_worker_env,
      {
        # exercise_description_ai.py (job_kind exercise-description) roda aqui.
        OPENAI_EXERCISE_DESCRIPTION_MODEL = var.openai_model_exercise_description
        # nutrition_label_ai.py (leitura da foto do rotulo, nutricao L6) roda aqui.
        OPENAI_NUTRITION_VISION_MODEL = var.openai_model_nutrition_vision
      },
    )
  }

  depends_on = [aws_cloudwatch_log_group.musclefit_plan_generator]

  tags = {
    Name = "musclefit-plan-generator"
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

resource "aws_lambda_function" "musclefit_cardio_agent" {
  function_name = "musclefit-cardio-agent"
  role          = aws_iam_role.musclefit_ai_generators_exec.arn

  package_type = "Image"
  image_uri    = "${aws_ecr_repository.musclefit_ai_generators.repository_url}:${var.ai_generators_image_tag}"

  image_config {
    command = ["app.lambda_cardio_agent.handler"]
  }

  timeout     = 120
  memory_size = 1024
  # Teto por funcao (AWS-01); valores e justificativa em variables.tf.
  reserved_concurrent_executions = var.lambda_reserved_concurrency["cardio-agent"]

  environment {
    variables = local.ai_generators_env
  }

  depends_on = [aws_cloudwatch_log_group.musclefit_cardio_agent]

  tags = {
    Name = "musclefit-cardio-agent"
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

# ---------------------------------------------------------------------------
# musclefit-api-core precisa poder: (1) invocar as duas Lambdas acima de
# forma assincrona, (2) ler/escrever na tabela de jobs.
# ---------------------------------------------------------------------------

resource "aws_iam_role_policy" "musclefit_api_core_async_jobs" {
  name = "musclefit-api-core-async-jobs"
  role = aws_iam_role.musclefit_api_core_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeAiGenerators"
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = [
          aws_lambda_function.musclefit_plan_generator.arn,
          aws_lambda_function.musclefit_cardio_agent.arn,
        ]
      },
      {
        Sid    = "AsyncJobsReadWrite"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
        ]
        Resource = aws_dynamodb_table.musclefit_async_jobs.arn
      }
    ]
  })
}

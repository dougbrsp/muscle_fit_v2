# Lambda musclefit-workout-ai — ver docs/aws-migration-plan.md, secao 1.2 e
# Fase 3. Extraida de musclefit-api-core: as 3 chamadas de IA sincronas do
# modo treino (POST /exercises/evaluate-set, POST /exercises/{id}/summarize,
# POST /activities/{id}/ai-summary/generate), que ate aqui chamavam a OpenAI
# diretamente dentro do request HTTP, sujeitas ao teto de 29s do API
# Gateway. Segue O MESMO padrao assincrono ja usado por
# musclefit-plan-generator/musclefit-cardio-agent (202 Accepted + job_id em
# musclefit-async-jobs, GET /jobs/{job_id} para polling).
#
# Decisao de onde fica o endpoint HTTP de entrada: OS 3 ENDPOINTS CONTINUAM
# EM musclefit-api-core (api/app/main.py) — nao migram para receber HTTP
# direto via API Gateway. Motivo: e exatamente o padrao ja estabelecido por
# plan-generator/cardio-agent (ver infra/api_gateway.tf — API Gateway so tem
# UMA integracao, `{proxy+}` -> musclefit-api-core; plan-generator/
# cardio-agent nunca foram alvo de rota de API Gateway, sao invocadas so via
# `lambda:InvokeFunction` `InvocationType=Event` por musclefit-api-core).
# Manter os 3 endpoints em api-core preserva esse desenho sem introduzir uma
# segunda forma de expor Lambda via API Gateway so para esta feature.
#
# Reusa a MESMA imagem de container `musclefit-ai-generators` (ECR
# compartilhado com plan-generator/cardio-agent — ver
# infra/lambda_ai_generators.tf e api/Dockerfile.ai, que ja empacota o
# `app/` inteiro e ja foi desenhado para funcoes distintas via
# `image_config.command`), so aponta para outro handler
# (`app.lambda_workout_ai.handler`). SEM Lambda Web Adapter, pelo mesmo
# motivo das outras duas: invocacao assincrona tipo Event, nao HTTP
# sincrono.

variable "openai_model_set_eval" {
  description = "Modelo OpenAI usado na avaliacao de serie em tempo real (exercise_set_evaluation_ai.py) - mantido identico ao ambiente on-premise."
  type        = string
  default     = "gpt-5.4-mini"
}

variable "openai_model_exercise_summary" {
  description = "Modelo OpenAI usado no resumo de fim de exercicio (exercise_summary_ai.py) - mantido identico ao ambiente on-premise."
  type        = string
  default     = "gpt-5.4-mini"
}

variable "openai_model_insights" {
  description = "Modelo OpenAI usado nos insights de pergunta livre (insights_ai.py) - mesmo default do codigo."
  type        = string
  default     = "gpt-5.4-mini"
}

variable "openai_model_reports" {
  description = "Modelo OpenAI usado na Analise por IA de /dashboard/treinos (report_ai.py) - mesmo default do codigo."
  type        = string
  default     = "gpt-5.4-mini"
}

variable "openai_model_workout_summary" {
  description = "Modelo OpenAI usado no resumo de fim de treino (workout_summary_ai.py) - mantido identico ao ambiente on-premise."
  type        = string
  default     = "gpt-5.4-mini"
}

# Nutricao L5b (plano de nutricao, secao 8.3): leitura da refeicao por IA
# (texto -> itens, saida estruturada strict), job `nutrition-meal-parse` nesta
# Lambda. Mesma familia "mini" das features de texto daqui; o codigo
# (nutrition_meal_parse_ai.py) usa o mesmo default se a env faltar.
variable "openai_model_nutrition_parse" {
  description = "Modelo OpenAI usado na leitura da refeicao por IA da nutricao (nutrition_meal_parse_ai.py) - mesmo default do codigo."
  type        = string
  default     = "gpt-5.4-mini"
}

# ---------------------------------------------------------------------------
# IAM: role PROPRIA desta Lambda (diferente de musclefit-ai-generators-exec,
# por pedido explicito - mesmo escopo de acesso a dados/segredos que
# plan-generator/cardio-agent precisam, mas isolada para nao acoplar o
# blast radius de IAM entre as duas funcoes de IA existentes e esta nova).
# ---------------------------------------------------------------------------

resource "aws_iam_role" "musclefit_workout_ai_exec" {
  name = "musclefit-workout-ai-exec"

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

resource "aws_iam_role_policy_attachment" "musclefit_workout_ai_logs" {
  role       = aws_iam_role.musclefit_workout_ai_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "musclefit_workout_ai_data_api" {
  name = "musclefit-workout-ai-data-api"
  role = aws_iam_role.musclefit_workout_ai_exec.id

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
          aws_secretsmanager_secret.api_token.arn,
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

resource "aws_iam_role_policy" "musclefit_workout_ai_rag" {
  name = "musclefit-workout-ai-rag"
  role = aws_iam_role.musclefit_workout_ai_exec.id

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

resource "aws_cloudwatch_log_group" "musclefit_workout_ai" {
  name              = "/aws/lambda/musclefit-workout-ai"
  retention_in_days = 14
}

resource "aws_lambda_function" "musclefit_workout_ai" {
  function_name = "musclefit-workout-ai"
  role          = aws_iam_role.musclefit_workout_ai_exec.arn

  package_type = "Image"
  # Mesma imagem/tag de plan-generator/cardio-agent (aws_ecr_repository.musclefit_ai_generators,
  # definida em infra/lambda_ai_generators.tf) - reaproveitada de proposito, ver
  # comentario no topo deste arquivo.
  image_uri = "${aws_ecr_repository.musclefit_ai_generators.repository_url}:${var.ai_generators_image_tag}"

  image_config {
    command = ["app.lambda_workout_ai.handler"]
  }

  # As 3 chamadas sao um unico turno de OpenAI cada (bem mais curtas que
  # geracao de plano/cardio), mas o mesmo teto generoso de plan-generator/
  # cardio-agent e mantido aqui: nao ha timeout de API Gateway neste caminho
  # (invocacao Event direta), e um teto curto so criaria falhas artificiais
  # sob latencia de rede/OpenAI ruim.
  timeout     = 60
  memory_size = 1024
  # Teto por funcao (AWS-01); valores e justificativa em variables.tf.
  reserved_concurrent_executions = var.lambda_reserved_concurrency["workout-ai"]

  environment {
    # Provedor LLM por feature + ARN da chave da Anthropic: infra/llm_providers.tf.
    variables = merge({
      AURORA_CLUSTER_ARN               = aws_rds_cluster.musclefit_db.arn
      AURORA_SECRET_ARN                = aws_rds_cluster.musclefit_db.master_user_secret[0].secret_arn
      AURORA_DATABASE                  = var.db_name
      OPENAI_API_KEY_SECRET_ARN        = aws_secretsmanager_secret.openai_api_key.arn
      API_TOKEN_SECRET_ARN             = aws_secretsmanager_secret.api_token.arn
      SECRET_ENCRYPTION_KEY_SECRET_ARN = aws_secretsmanager_secret.secret_encryption_key.arn
      OPENAI_SET_EVAL_MODEL            = var.openai_model_set_eval
      OPENAI_EXERCISE_SUMMARY_MODEL    = var.openai_model_exercise_summary
      OPENAI_WORKOUT_SUMMARY_MODEL     = var.openai_model_workout_summary
      OPENAI_INSIGHTS_MODEL            = var.openai_model_insights
      OPENAI_REPORTS_MODEL             = var.openai_model_reports
      OPENAI_NUTRITION_PARSE_MODEL     = var.openai_model_nutrition_parse
      ASYNC_JOBS_TABLE                 = aws_dynamodb_table.musclefit_async_jobs.name

      # Hidratacao de RAG em runtime - ver infra/s3_rag.tf e
      # app/rag_storage.py::ensure_rag_local(). Consumido por
      # exercise_set_evaluation_ai.py (le PROGRESSAO.md), via rag_chunking.py -
      # mesmo padrao das outras 3 Lambdas (musclefit-engines,
      # musclefit-plan-generator, musclefit-cardio-agent).
      RAG_S3_BUCKET    = aws_s3_bucket.musclefit_rag.bucket
      RAG_S3_PREFIX    = "markdown/"
      RAG_MARKDOWN_DIR = "/tmp/rag"
    }, local.llm_provider_env)
  }

  depends_on = [aws_cloudwatch_log_group.musclefit_workout_ai]

  tags = {
    Name = "musclefit-workout-ai"
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
# musclefit-api-core precisa poder invocar esta Lambda de forma assincrona
# (mesmo padrao de InvokeAiGenerators em infra/lambda_ai_generators.tf - a
# leitura/escrita em musclefit-async-jobs ja esta coberta por
# aws_iam_role_policy.musclefit_api_core_async_jobs, generica para a tabela
# toda, nao precisa duplicar aqui).
# ---------------------------------------------------------------------------

resource "aws_iam_role_policy" "musclefit_api_core_invoke_workout_ai" {
  name = "musclefit-api-core-invoke-workout-ai"
  role = aws_iam_role.musclefit_api_core_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeWorkoutAi"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.musclefit_workout_ai.arn
      }
    ]
  })
}

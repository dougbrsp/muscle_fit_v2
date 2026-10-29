# Lambda musclefit-migrations: runner de migracoes versionadas do banco
# (api/app/migrate.py, handler app.migrate.handler). Invocada de forma SINCRONA
# por .github/workflows/deploy-producao.yml (job backend-deploy, com
# RUN_DB_MIGRATIONS=true) antes do update-function-code das
# demais Lambdas - nenhuma migracao e aplicada manualmente.
#
# IMAGEM: reaproveita musclefit-ai-generators (api/Dockerfile.ai), e NAO a
# musclefit-api-core. Motivo: a imagem api-core roda uvicorn atras do Lambda
# Web Adapter (extensao em /opt/extensions que assume o loop do Runtime API) e
# nao instala awslambdaric, entao `image_config.command = ["app.migrate.handler"]`
# nela nao teria quem chamasse o handler. A imagem ai-generators ja e
# ENTRYPOINT ["python", "-m", "awslambdaric"] com o MESMO app/ e o MESMO
# requirements.txt - trocar so o command e o padrao ja usado por
# plan-generator/cardio-agent/workout-ai. Os arquivos de api/db/migrations/
# entram na imagem via COPY em api/Dockerfile.ai.
#
# Sem VPC: acesso ao banco e pelo RDS Data API (HTTPS), igual as demais.

resource "aws_iam_role" "musclefit_migrations_exec" {
  name = "musclefit-migrations-exec"

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

resource "aws_iam_role_policy_attachment" "musclefit_migrations_logs" {
  role       = aws_iam_role.musclefit_migrations_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Mesmo acesso a banco de musclefit-api-core-exec (Data API + secret do
# Aurora), sem os segredos de aplicacao (OpenAI, api token, chave de
# criptografia) - o runner nao precisa deles.
resource "aws_iam_role_policy" "musclefit_migrations_data_api" {
  name = "musclefit-migrations-data-api"
  role = aws_iam_role.musclefit_migrations_exec.id

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
        Sid      = "AuroraSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_rds_cluster.musclefit_db.master_user_secret[0].secret_arn]
      }
    ]
  })
}

# Retencao maior que as demais (14 dias): e o historico de quem mudou o schema.
resource "aws_cloudwatch_log_group" "musclefit_migrations" {
  name              = "/aws/lambda/musclefit-migrations"
  retention_in_days = 90
}

resource "aws_lambda_function" "musclefit_migrations" {
  function_name = "musclefit-migrations"
  role          = aws_iam_role.musclefit_migrations_exec.arn

  package_type = "Image"
  image_uri    = "${aws_ecr_repository.musclefit_ai_generators.repository_url}:${var.ai_generators_image_tag}"

  image_config {
    command = ["app.migrate.handler"]
  }

  # DDL em tabelas pequenas leva segundos; 300 s cobre retomada do Aurora e
  # varias migracoes num mesmo deploy. Cada statement do Data API tem teto
  # proprio de 45 s (ver riscos na PR-02).
  timeout     = 300
  memory_size = 512
  # Teto por funcao (AWS-01); valores e justificativa em variables.tf.
  reserved_concurrent_executions = var.lambda_reserved_concurrency["migrations"]

  environment {
    # So as variaveis do Data API. NUNCA DB_BACKEND/MYSQL_* aqui (regra
    # anti-vazamento dev/prod; o handler tambem recusa DB_BACKEND!=data-api).
    variables = {
      AURORA_CLUSTER_ARN = aws_rds_cluster.musclefit_db.arn
      AURORA_SECRET_ARN  = aws_rds_cluster.musclefit_db.master_user_secret[0].secret_arn
      AURORA_DATABASE    = var.db_name
    }
  }

  depends_on = [aws_cloudwatch_log_group.musclefit_migrations]

  tags = {
    Name = "musclefit-migrations"
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

# Lambda musclefit-intervals-sync + agendamento no EventBridge (melhoria M2).
#
# O QUE ESTA FUNCAO FAZ: busca atividades e wellness no Intervals.icu para
# cada usuario QUE CONFIGUROU a API do Intervals (`users.intervals_athlete_id`
# + `users.intervals_api_key_encrypted`), grava no cache
# (`intervals_activities`/`intervals_wellness`) e associa a FC das atividades
# `WeightTraining` aos treinos realizados, pelo horario
# (`activity_intervals_link`, migracao 039). Handler:
# `app.lambda_intervals_sync.handler` (`api/app/intervals_sync.py`).
#
# POR QUE UMA LAMBDA AGENDADA, E NAO UM BOTAO: ate esta entrega a
# sincronizacao so acontecia se alguem abrisse `/intervals-icu` e clicasse
# "Sincronizar" - ou seja, o dado de FC do treino dependia de o usuario
# lembrar. Agendada, a coleta acontece sozinha; a tela virou somente leitura.
#
# IMAGEM: reaproveita `musclefit-ai-generators` (api/Dockerfile.ai), como a
# musclefit-migrations - mesmo `app/`, mesmo requirements, ENTRYPOINT
# awslambdaric, so o `image_config.command` muda. A imagem api-core nao serve
# (uvicorn atras do Lambda Web Adapter, sem awslambdaric).
#
# SEM VPC: o acesso ao banco e pelo RDS Data API (HTTPS) e a chamada ao
# Intervals.icu e HTTPS publica - as duas saem pela rede gerenciada da Lambda,
# igual as demais funcoes deste projeto.
#
# SEGREDO: a funcao le `SECRET_ENCRYPTION_KEY_SECRET_ARN` para descriptografar
# a chave de API de CADA usuario (`app/crypto_utils.py`). A chave em claro so
# existe em memoria durante a chamada HTTP; nao e logada nem devolvida.
#
# CUSTO: uma invocacao por dia, com uma chamada HTTP por usuario configurado.
# Nada de varredura da base.

variable "intervals_sync_schedule_expression" {
  description = "Quando o sync do Intervals.icu roda (EventBridge). Default: 08:45 UTC = 05:45 BRT. ATENCAO: este horario tem DOIS propositos - ver o bloco de comentario acima do recurso da regra antes de mexer."
  type        = string
  default     = "cron(45 8 * * ? *)"
}

variable "intervals_sync_enabled" {
  description = "Liga/desliga o agendamento sem destruir a funcao. `false` mantem a Lambda (invocavel a mao para troubleshooting) e desabilita a regra."
  type        = bool
  default     = true
}

variable "intervals_sync_days" {
  description = "Janela em dias que cada execucao reprocessa. Curta de proposito: o job roda todo dia e 30 dias ja cobrem correcao retroativa sem gastar quota da API do Intervals.icu."
  type        = number
  default     = 30
}

# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------

resource "aws_iam_role" "musclefit_intervals_sync_exec" {
  name = "musclefit-intervals-sync-exec"

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

resource "aws_iam_role_policy_attachment" "musclefit_intervals_sync_logs" {
  role       = aws_iam_role.musclefit_intervals_sync_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Mesmo acesso a banco da api-core (Data API + secret do Aurora) mais UM
# segredo de aplicacao: a chave de criptografia, sem a qual nao da para ler a
# chave do Intervals de cada usuario. NAO recebe OPENAI_API_KEY nem o
# API_TOKEN: este job nao chama a OpenAI nem a propria API HTTP.
resource "aws_iam_role_policy" "musclefit_intervals_sync_data_api" {
  name = "musclefit-intervals-sync-data-api"
  role = aws_iam_role.musclefit_intervals_sync_exec.id

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
        Sid    = "AuroraAndEncryptionSecret"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          aws_rds_cluster.musclefit_db.master_user_secret[0].secret_arn,
          aws_secretsmanager_secret.secret_encryption_key.arn,
        ]
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# Funcao
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "musclefit_intervals_sync" {
  name              = "/aws/lambda/musclefit-intervals-sync"
  retention_in_days = 14
}

resource "aws_lambda_function" "musclefit_intervals_sync" {
  function_name = "musclefit-intervals-sync"
  role          = aws_iam_role.musclefit_intervals_sync_exec.arn

  package_type = "Image"
  image_uri    = "${aws_ecr_repository.musclefit_ai_generators.repository_url}:${var.ai_generators_image_tag}"

  image_config {
    command = ["app.lambda_intervals_sync.handler"]
  }

  # Uma chamada HTTP ao Intervals.icu por usuario (timeout de 30 s no cliente)
  # + upsert do periodo. 300 s cobre com folga a base atual e ainda da margem
  # para o Aurora retomar. Falha de um usuario nao consome o tempo dos outros:
  # cada um tem sua propria transacao e seu proprio try/except.
  timeout     = 300
  memory_size = 512
  # Teto por funcao (AWS-01); valores e justificativa em variables.tf.
  reserved_concurrent_executions = var.lambda_reserved_concurrency["intervals-sync"]

  environment {
    # Somente o necessario. NUNCA DB_BACKEND/MYSQL_* aqui (regra
    # anti-vazamento dev/prod; o handler tambem recusa DB_BACKEND != data-api).
    variables = {
      AURORA_CLUSTER_ARN               = aws_rds_cluster.musclefit_db.arn
      AURORA_SECRET_ARN                = aws_rds_cluster.musclefit_db.master_user_secret[0].secret_arn
      AURORA_DATABASE                  = var.db_name
      SECRET_ENCRYPTION_KEY_SECRET_ARN = aws_secretsmanager_secret.secret_encryption_key.arn
      INTERVALS_SYNC_DAYS              = tostring(var.intervals_sync_days)
    }
  }

  depends_on = [aws_cloudwatch_log_group.musclefit_intervals_sync]

  tags = {
    Name = "musclefit-intervals-sync"
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
# Agendamento (EventBridge). Primeira regra agendada do projeto.
#
# `aws_cloudwatch_event_rule` (EventBridge Rules) e nao `aws_scheduler_schedule`
# (EventBridge Scheduler): o Scheduler exigiria uma role propria so para
# invocar a funcao, e nada aqui precisa do que ele traz a mais (janela de
# flexibilidade, one-shot, timezone). Uma regra cron simples com
# `aws_lambda_permission` e o menor recurso que resolve.
#
# ---------------------------------------------------------------------------
# NAO "OTIMIZE" ESTE HORARIO SEM LER ISTO.
#
# 05:45 BRT (`cron(45 8 * * ? *)`) serve a DOIS propositos, nao a um:
#
#   1. SINCRONIZAR. O proposito original: trazer atividades e wellness do
#      Intervals.icu antes de o usuario abrir o app.
#
#   2. ACORDAR O BANCO. Desde que o Aurora passou a pausar por ociosidade
#      (`min_acu = 0` em aurora.tf), a primeira chamada ao banco depois da
#      pausa paga ~15 s de retomada. Este job e o despertador: ele bate no
#      banco as 05:45, o Aurora retoma enquanto ninguem esta esperando, e o
#      usuario - que abre o app por volta das 06:00 - encontra o banco ja
#      quente. NAO existe Lambda de keep-alive separada de proposito: este job
#      ja ia rodar de qualquer jeito, entao o despertador sai de graca.
#
# CONSEQUENCIAS DE MUDAR O HORARIO:
#   - Adiantar demais (ex.: voltar para 04:00 BRT): com timeout de pausa de
#     10 min, o banco dorme de novo logo depois do sync e no primeiro acesso o
#     usuario volta a pagar a retomada na cara. O intervalo entre o sync e o
#     primeiro acesso precisa ser MENOR que `var.seconds_until_auto_pause`.
#   - Atrasar para depois das 06:00: o despertador toca tarde, e o usuario
#     esbarra na retomada.
#   - Desligar a regra (`intervals_sync_enabled = false`): alem de parar a
#     sincronizacao, devolve a espera de retomada ao primeiro acesso do dia.
#
# Se o usuario mudar o horario de treino, mova este cron junto - ele deve
# cair ~15 min antes do primeiro acesso tipico.
#
# ATUALIZACAO 2026-10-08 (FIN-02): com `seconds_until_auto_pause = 600` (10 min,
# antes 1800) o efeito de despertador dura so 10 min apos o sync, entao ele NAO
# garante mais o primeiro acesso do dia. Alem disso, a medicao mostra que o
# primeiro acesso real do usuario ja ocorre a partir das 08 h UTC, antes deste
# cron (08:45 UTC), de modo que o despertador nunca cobriu esse acesso. O
# usuario aceitou esse custo (~15 s de retomada na primeira chamada) em troca da
# economia de compute. O cron NAO muda: continua sendo o sync do Intervals.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "musclefit_intervals_sync_daily" {
  name                = "musclefit-intervals-sync-daily"
  description         = "Dispara o sync do Intervals.icu uma vez por dia (somente usuarios com a API configurada)."
  schedule_expression = var.intervals_sync_schedule_expression
  state               = var.intervals_sync_enabled ? "ENABLED" : "DISABLED"

  tags = {
    Name = "musclefit-intervals-sync-daily"
  }
}

resource "aws_cloudwatch_event_target" "musclefit_intervals_sync_daily" {
  rule      = aws_cloudwatch_event_rule.musclefit_intervals_sync_daily.name
  target_id = "musclefit-intervals-sync"
  arn       = aws_lambda_function.musclefit_intervals_sync.arn

  # Evento vazio de proposito: sem `user_ids` o job roda para TODOS os
  # usuarios com Intervals configurado, e `days` vem de INTERVALS_SYNC_DAYS.
  # Os campos do evento existem para troubleshooting (invocacao pontual), nao
  # para o agendamento.
  input = jsonencode({})
}

resource "aws_lambda_permission" "musclefit_intervals_sync_events" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.musclefit_intervals_sync.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.musclefit_intervals_sync_daily.arn
}

variable "aws_region" {
  description = "Regiao AWS de todos os recursos regionais desta stack (Fase 0-1)."
  type        = string
  default     = "sa-east-1"
}

variable "vpc_cidr" {
  description = "CIDR da musclefit-vpc. So contem subnets privadas, sem NAT/IGW (ver docs/aws-migration-plan.md, secao 1.6)."
  type        = string
  default     = "10.20.0.0/16"
}

variable "private_subnet_cidrs" {
  description = "CIDRs das subnets privadas do Aurora, uma por AZ. sa-east-1 tem 3 AZs disponiveis; 2 bastam para o DB subnet group."
  type        = list(string)
  default     = ["10.20.1.0/24", "10.20.2.0/24"]
}

variable "db_name" {
  description = "Nome do schema inicial no cluster Aurora."
  type        = string
  default     = "musclefit"
}

variable "db_master_username" {
  description = "Usuario master do cluster. A senha e gerenciada automaticamente pelo RDS via Secrets Manager (manage_master_user_password) - nunca definida aqui."
  type        = string
  default     = "musclefit_admin"
}

variable "engine_version" {
  description = "Versao do Aurora MySQL. DEVE ser >= 3.07 para suportar RDS Data API (ver docs/aws-migration-plan.md, secao 1.4). Validar a versao disponivel mais recente com `aws rds describe-db-engine-versions --engine aurora-mysql` antes do primeiro apply."
  type        = string
  default     = "8.0.mysql_aurora.3.08.2"
}

variable "min_acu" {
  description = "Capacidade minima do Aurora Serverless v2, em ACUs. `0` LIGA o auto-pause (scale-to-zero); qualquer valor >= 0.5 desliga. Ver `seconds_until_auto_pause` e o bloco de comentario em aurora.tf."
  type        = number
  default     = 0
}

# FIN-02 (docs/auditoria/2026-10-08-varredura-codigo-aws-seguranca.md): reduzido de
# 1800 s (30 min) para 600 s (10 min) por decisao do usuario em 2026-10-08.
# Medicao: banco acordado 174 de 255 h em 10 dias, com 29 h sem nenhuma chamada;
# o trafego real do usuario concentra-se em 08-13 h e 18-22 h UTC. 10 min corta a
# cauda ociosa e ainda mantem a primeira chamada rapida dentro de uma sessao de
# treino. Economia estimada: US$ 3-6/mes. Custo aceito: a primeira chamada apos
# a pausa paga ~15 s de retomada, o que agora acontece apos 10 min de ociosidade.
variable "seconds_until_auto_pause" {
  description = "Segundos de OCIOSIDADE (zero conexoes de usuario) ate o Aurora pausar. So tem efeito com `min_acu = 0`; a AWS remove o atributo se o minimo for diferente de zero. Faixa aceita: 300 a 86400. Default 600 (10 min) - decisao do usuario em 2026-10-08 (FIN-02): corta a cauda ociosa mantendo a primeira chamada rapida dentro da sessao. Era 1800 (30 min)."
  type        = number
  default     = 600

  validation {
    condition     = var.seconds_until_auto_pause >= 300 && var.seconds_until_auto_pause <= 86400
    error_message = "seconds_until_auto_pause deve estar entre 300 (5 min) e 86400 (1 dia), limite da API do RDS."
  }
}

variable "max_acu" {
  description = "Capacidade maxima do Aurora Serverless v2, em ACUs."
  type        = number
  default     = 1
}

variable "deletion_protection" {
  description = "Protege o cluster contra terraform destroy / delete acidental. Recomendado true mesmo em projeto pessoal, pois o cluster passa a guardar dados reais migrados do ambiente local (ver Fase 1)."
  type        = bool
  default     = true
}

variable "backup_retention_days" {
  description = "Retencao de backups automaticos do cluster, em dias. Subiu de 1 para 7 (AWS-05, varredura 2026-10-08): o teto de 1 dia era do periodo free tier de 60 dias, ja encerrado. Custo ~US$ 0 (volume de ~136 MB; backup ate o tamanho do volume e gratis)."
  type        = number
  default     = 7
}

variable "alerts_email" {
  description = "E-mail que recebe os alertas operacionais (Budgets e alarmes CloudWatch via SNS musclefit-alerts). Sem default (dado pessoal, repositorio publico): no CI vem da variable do repositorio TF_VAR_alerts_email (env TF_VAR_alerts_email nos workflows de terraform); localmente, `export TF_VAR_alerts_email=...` ou terraform.tfvars (ignorado pelo .gitignore). A assinatura SNS precisa ser confirmada por e-mail apos o apply."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.alerts_email))
    error_message = "alerts_email precisa ser um e-mail (defina TF_VAR_alerts_email)."
  }
}

variable "monthly_budget_usd" {
  description = "Limite do Budget mensal musclefit-monthly, em USD (string, como exige o provider). Era 30 (criado a mao); o run-rate bruto e ~US$ 53/mes, hoje coberto por creditos (FIN-01, varredura 2026-10-08)."
  type        = string
  default     = "60"
}

variable "rds_budget_usd" {
  description = "Limite do Budget musclefit-monthly-rds (so Amazon RDS/Aurora, ~93% do custo), em USD."
  type        = string
  default     = "55"
}

variable "migration_allowed_cidr_blocks" {
  description = <<-EOT
    CIDRs autorizados a conectar na porta 3306 do Aurora - usado SOMENTE durante a
    migracao inicial de dados (Fase 1, item 2: mysqldump on-premise -> Aurora via
    conexao TCP temporaria). Deixar vazio ([]) fora da janela de migracao: o Data API
    nao depende dessa regra, so uma conexao TCP direta (ex.: de dentro da VPC via
    bastion/VPN) precisa dela. Nunca abrir para 0.0.0.0/0.
  EOT
  type        = list(string)
  default     = []
}

variable "api_disable_execute_api_endpoint" {
  description = <<-EOT
    SEG-05/AWS-08: desliga o host padrao <id>.execute-api.sa-east-1.amazonaws.com da
    REST API, deixando so api.musclefit.com.br (onde o WAF e as regras valem). Voltar
    para false e o rollback de uma linha se algum cliente (ex.: o relogio Zepp, cuja
    URL e digitada no app do celular) ainda usar o endereco padrao.
  EOT
  type        = bool
  default     = true
}

variable "lambda_reserved_concurrency" {
  description = <<-EOT
    Concorrencia reservada por funcao Lambda (reserved_concurrent_executions), AWS-01
    item (2) da varredura 2026-10-08. Cada funcao tem seu teto: uma rajada nas rotas de
    IA nao ocupa as vagas da api-core, e a cota alta da conta deixa de ser um teto de
    custo aberto. Base: CloudWatch ConcurrentExecutions (Maximum por dia) e Throttles
    (Sum), 2026-10-01 a 2026-10-15. Ate 06/10 a cota da conta era 10 (12-74 throttles
    por dia); desde 07/10 a cota esta em 1.001 e nao houve throttle em nenhuma funcao.

      api-core       50  max 21 em 14/10 (18 em 11/10), ja com a cota em 1.001 e 0
                         throttles: ~2,4x o observado.
      engines        20  invocada SO pelo API Gateway (nao pela api-core); max 6 em
                         02/10 (normalmente 5), 0 throttles: ~3x o observado.
      plan-generator  6  max 2 (24 invocacoes em 04/10, dia de cards). Folga para o
                         paralelismo real dos jobs assincronos: 2 cards em voo + ficha
                         + periodizacao + descricao = 5.
      cardio-agent    3  sem invocacao em 01-15/10; folga minima para jobs em paralelo.
      workout-ai     10  max 2 (25-41 invocacoes/dia); os 3 endpoints do Modo Treino e
                         a analise podem coincidir entre usuarios.
      intervals-sync  1  max 1; EventBridge agendado, execucao serial e intencional.
      migrations      1  max 1; migracao NUNCA deve rodar em paralelo (1 e trava, nao folga).

    Por que folga nas 3 de IA: sao invocadas com InvocationType=Event. Um invoke acima
    da reserva nao falha: vai para a fila de throttling da AWS (ate 6 h) sem rodar. O
    job fica `pending` alem do timeout da Lambda + 60 s (WORKER_LOST_GRACE_SECONDS),
    o poll do dono o fecha como failed/worker_lost (M73, api/app/async_jobs.py), o
    usuario tenta de novo e a execucao atrasada ainda chama a OpenAI (cobranca dupla).
    Reserva nao custa nada, entao o teto fica acima do paralelismo plausivel; o alarme
    musclefit-lambda-throttles-reservadas (monitoring.tf) avisa se alguma encostar.

    Soma 91. A AWS exige >= 100 execucoes NAO reservadas na conta, entao a cota
    L-B99A9384 precisa ser >= 191 (na pratica >= 200) antes do apply; com a cota antiga,
    em 10, o apply FALHAVA (InvalidParameterValueException). A cota esta APLICADA em
    1.001 (pedido de 2026-10-09; conferida em 2026-10-15 com `aws lambda
    get-account-settings`: ConcurrentExecutions 1001), entao as 91 reservadas deixam
    910 nao reservadas. -1 remove a reserva (volta a usar o pool nao reservado); 0
    bloquearia a funcao e so entra por decisao explicita (mudar a validacao abaixo).
  EOT
  type        = map(number)
  default = {
    "api-core"       = 50
    "engines"        = 20
    "plan-generator" = 6
    "cardio-agent"   = 3
    "workout-ai"     = 10
    "intervals-sync" = 1
    "migrations"     = 1
  }

  validation {
    condition = alltrue([
      for k in ["api-core", "engines", "plan-generator", "cardio-agent", "workout-ai", "intervals-sync", "migrations"] :
      contains(keys(var.lambda_reserved_concurrency), k)
    ])
    error_message = "lambda_reserved_concurrency precisa ter as 7 chaves: api-core, engines, plan-generator, cardio-agent, workout-ai, intervals-sync, migrations."
  }

  # -1 = sem reserva (pool nao reservado); >= 1 = teto. 0 bloquearia a funcao (nenhuma
  # execucao) e so entra por decisao explicita: mudar esta validacao junto com o valor.
  validation {
    condition     = alltrue([for v in values(var.lambda_reserved_concurrency) : (v == -1 || v >= 1) && floor(v) == v])
    error_message = "Cada valor deve ser inteiro: -1 (sem reserva) ou >= 1. 0 bloquearia a funcao e nao e aceito aqui."
  }
}

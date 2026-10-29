# Aurora Serverless v2 (MySQL-compatible) + RDS Data API, sem VPC nas Lambdas.
# Ver docs/aws-migration-plan.md, secao 1.4, para a justificativa completa.

resource "aws_rds_cluster" "musclefit_db" {
  cluster_identifier = "musclefit-db"
  engine             = "aurora-mysql"
  engine_mode        = "provisioned"
  engine_version     = var.engine_version
  database_name      = var.db_name

  master_username             = var.db_master_username
  manage_master_user_password = true # RDS cria e gerencia o secret no Secrets Manager - nunca senha estatica aqui

  db_subnet_group_name   = aws_db_subnet_group.musclefit.name
  vpc_security_group_ids = [aws_security_group.musclefit_db.id]

  enable_http_endpoint = true # RDS Data API

  # AUTO-PAUSE (scale-to-zero). `min_capacity = 0` e o unico interruptor: com ele
  # a instancia `db.serverless` pausa apos `seconds_until_auto_pause` sem NENHUMA
  # conexao de usuario, e a cobranca de compute vai a zero (storage e backup
  # continuam sendo cobrados). Qualquer valor >= 0.5 desliga o recurso e a AWS
  # descarta o atributo de timeout.
  #
  # POR QUE: o cluster vivia colado no piso 24/7 - media de 0,50 ACU, minimo
  # 0,50, maximo 1,00 em 8 dias de CloudWatch. 0,5 ACU x 730 h x US$ 0,25 =
  # US$ 91/mes de compute quase todo ocioso, para um app de um usuario so.
  # A metrica `DatabaseConnections` mostra o banco com ZERO conexoes por blocos
  # longos (ex.: 20h de 25/09 as 07h de 26/09, BRT), entao ha ociosidade real a
  # colher.
  #
  # PRE-REQUISITO ATENDIDO: auto-pause exige Aurora MySQL >= 3.08.0. Este
  # cluster roda 8.0.mysql_aurora.3.08.2 (`var.engine_version`). NAO baixe a
  # versao sem desligar o auto-pause junto.
  #
  # O QUE IMPEDIRIA A PAUSA (nenhum destes existe aqui - conferido por leitura
  # na conta, e a lista precisa continuar vazia): RDS Proxy associado, replica
  # de leitura em tier de failover 0 ou 1, instancia provisionada no cluster,
  # replicacao binlog, Aurora Global Database, integracao zero-ETL com Redshift,
  # ou qualquer cliente que deixe conexao aberta (sessao de terminal, GUI de
  # banco esquecida aberta). Health checks internos da Aurora nao contam.
  #
  # CONSEQUENCIA ACEITA: a primeira chamada depois da pausa leva ~15 s para o
  # banco retomar (>= 30 s se ficar mais de 24 h pausado, quando a Aurora entra
  # em sono profundo) e o RDS Data API responde `DatabaseResumingException`
  # nesse intervalo. Ver a secao de riscos da PR que introduziu este bloco.
  serverlessv2_scaling_configuration {
    min_capacity             = var.min_acu
    max_capacity             = var.max_acu
    seconds_until_auto_pause = var.seconds_until_auto_pause
  }

  storage_type = "aurora" # Standard - volume baixo demais para justificar I/O-Optimized (ver secao 4, risco/decisao correspondente)

  backup_retention_period = var.backup_retention_days
  preferred_backup_window = "06:00-07:00" # UTC ~= 03:00-04:00 America/Sao_Paulo, fora do uso pessoal

  copy_tags_to_snapshot     = true
  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "musclefit-db-final"

  enabled_cloudwatch_logs_exports = ["error", "slowquery", "audit"]

  tags = {
    Name = "musclefit-db"
  }
}

resource "aws_rds_cluster_instance" "writer" {
  identifier         = "musclefit-db-instance-1"
  cluster_identifier = aws_rds_cluster.musclefit_db.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.musclefit_db.engine
  engine_version     = aws_rds_cluster.musclefit_db.engine_version

  db_subnet_group_name = aws_db_subnet_group.musclefit.name
  publicly_accessible  = false # NUNCA true - Data API/bastion sao os unicos caminhos de acesso (ver README de infra)

  tags = {
    Name = "musclefit-db-instance-1"
  }
}

# AWS-18 (varredura 2026-10-08): o log de erro exportado pelo cluster nasceu sem
# retencao (cresce para sempre; 1,3 MB em 2026-10-10). O grupo e criado pelo
# proprio RDS quando o export "error" e ligado, por isso entra por import.
#
# 30 dias, e nao os 14 das Lambdas: erro de banco e raro e costuma ser investigado
# depois (comparar com um deploy ou uma migracao da semana anterior), e o volume e
# irrelevante (centavos). Ao aplicar, eventos com mais de 30 dias deixam de existir.
#
# "slowquery" e "audit" continuam em enabled_cloudwatch_logs_exports, mas nao geram
# grupo: o cluster usa default.aurora-mysql8.0, que nao liga slow_query_log nem
# auditoria. Se um parameter group ligar algum deles, declarar o grupo aqui (com
# import, se o RDS criar antes) e a retencao no mesmo PR.
#
# Permissao: logs:PutRetentionPolicy/TagResource/ListTagsForResource em
# log-group:/aws/rds/cluster/musclefit-db/* (versao v2 da managed
# musclefit-gha-deploy-v2-compute, FIN-04/AWS-18). Sem ela ate o plan falha
# (o import le as tags do grupo). Depois do primeiro apply o bloco import e no-op
# e pode ser removido.
import {
  to = aws_cloudwatch_log_group.musclefit_db_error
  id = "/aws/rds/cluster/musclefit-db/error"
}

resource "aws_cloudwatch_log_group" "musclefit_db_error" {
  name              = "/aws/rds/cluster/${aws_rds_cluster.musclefit_db.cluster_identifier}/error"
  retention_in_days = 30
}

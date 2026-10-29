# musclefit-vpc: so existe para dar ao Aurora um DB subnet group valido.
# As Lambdas (Fase 3+) rodam FORA da VPC e falam com o banco via RDS Data API
# (HTTPS, fora do path de rede da VPC) - por isso nao ha NAT Gateway nem
# Internet Gateway aqui (ver docs/aws-migration-plan.md, secao 1.6).

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "musclefit" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "musclefit-vpc"
  }
}

resource "aws_subnet" "private" {
  count             = length(var.private_subnet_cidrs)
  vpc_id            = aws_vpc.musclefit.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "musclefit-private-${data.aws_availability_zones.available.names[count.index]}"
  }
}

resource "aws_db_subnet_group" "musclefit" {
  name       = "musclefit-db-subnet-group"
  subnet_ids = aws_subnet.private[*].id

  tags = {
    Name = "musclefit-db-subnet-group"
  }
}

# Sem regras de ingresso por padrao - o Data API nao passa pela rede da VPC.
# Regra de 3306 e adicionada dinamicamente so durante a janela de migracao
# (var.migration_allowed_cidr_blocks), nunca aberta por padrao.
resource "aws_security_group" "musclefit_db" {
  name        = "musclefit-db-sg"
  description = "Security group do cluster Aurora musclefit-db. Sem ingresso publico; 3306 so liberado durante migracao pontual."
  vpc_id      = aws_vpc.musclefit.id

  tags = {
    Name = "musclefit-db-sg"
  }
}

resource "aws_vpc_security_group_ingress_rule" "mysql_migration_window" {
  count             = length(var.migration_allowed_cidr_blocks)
  security_group_id = aws_security_group.musclefit_db.id
  cidr_ipv4         = var.migration_allowed_cidr_blocks[count.index]
  from_port         = 3306
  to_port           = 3306
  ip_protocol       = "tcp"
  description       = "Acesso temporario para migracao de dados (Fase 1) - remover apos o corte"
}

resource "aws_vpc_security_group_egress_rule" "allow_all_outbound" {
  security_group_id = aws_security_group.musclefit_db.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "Egresso padrao - o cluster nao inicia conexoes de saida sensiveis"
}

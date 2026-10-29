# AWS-02 (varredura 2026-10-08): trilha de auditoria e deteccao.
# Hoje so existe o Event history do console (90 dias, sem validacao de integridade).
# Custo: 1a copia de eventos de gerenciamento e gratuita; S3 < US$ 0,5/mes;
# Access Analyzer (ACCOUNT) gratis; GuardDuty ~US$ 2-5/mes apos 30 dias de trial.

locals {
  cloudtrail_bucket = "musclefit-cloudtrail-${data.aws_caller_identity.current.account_id}"
  cloudtrail_name   = "musclefit-trail"
  cloudtrail_arn    = "arn:aws:cloudtrail:${var.aws_region}:${data.aws_caller_identity.current.account_id}:trail/${local.cloudtrail_name}"
}

resource "aws_s3_bucket" "cloudtrail" {
  bucket = local.cloudtrail_bucket

  tags = {
    Name = "musclefit-cloudtrail"
  }
}

resource "aws_s3_bucket_public_access_block" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256" # SSE-S3: sem custo de KMS
    }
  }
}

# Sem versionamento: os logs sao append-only (chave unica por entrega) e o
# validador de digest do CloudTrail ja detecta alteracao/remocao.
resource "aws_s3_bucket_lifecycle_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    id     = "expira-logs-365-dias"
    status = "Enabled"

    filter {}

    expiration {
      days = 365
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  # jsonencode (e nao data source) para o ARN ser conhecido no plan - mesmo motivo
  # da policy do bucket do site (ver infra/README.md).
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AWSCloudTrailAclCheck"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.cloudtrail.arn
        Condition = { StringEquals = { "aws:SourceArn" = local.cloudtrail_arn } }
      },
      {
        Sid       = "AWSCloudTrailWrite"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.cloudtrail.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl"  = "bucket-owner-full-control"
            "aws:SourceArn" = local.cloudtrail_arn
          }
        }
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.cloudtrail.arn, "${aws_s3_bucket.cloudtrail.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.cloudtrail]
}

resource "aws_cloudtrail" "musclefit" {
  name                          = local.cloudtrail_name
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true

  # So eventos de gerenciamento (a 1a copia e gratis). Sem data events (S3/Lambda
  # cobrados por evento) e sem CloudWatch Logs por ora.
  event_selector {
    read_write_type           = "All"
    include_management_events = true
  }

  depends_on = [aws_s3_bucket_policy.cloudtrail]
}

# Achados de acesso externo (IAM roles, S3, KMS, Lambda...). Gratuito no tipo ACCOUNT.
resource "aws_accessanalyzer_analyzer" "musclefit" {
  analyzer_name = "musclefit-account"
  type          = "ACCOUNT"
}

resource "aws_guardduty_detector" "musclefit" {
  enable                       = true
  finding_publishing_frequency = "SIX_HOURS"
}

# Protecoes extras sao pagas por volume: desligadas explicitamente (detectores novos
# podem vir com S3 data events ligado por padrao). Fica so a analise basica
# (CloudTrail de gerenciamento, VPC Flow Logs e DNS).
resource "aws_guardduty_detector_feature" "desligadas" {
  for_each = toset([
    "S3_DATA_EVENTS",
    "EKS_AUDIT_LOGS",
    "EBS_MALWARE_PROTECTION",
    "RDS_LOGIN_EVENTS",
    "LAMBDA_NETWORK_LOGS",
  ])

  detector_id = aws_guardduty_detector.musclefit.id
  name        = each.key
  status      = "DISABLED"

  # Drift perpetuo em RDS_LOGIN_EVENTS (corrigido em 2026-10-09): a API passou a
  # devolver a feature com additional_configuration RDS_DATA_RISK (DISABLED).
  # No provider 5.100 (ultimo 5.x) esse bloco e ForceNew e nao Computed, entao o
  # valor lido da API contra o config vazio gerava "must be replaced" em todo plan.
  # Nao da para declarar o bloco: o validador do 5.100 (SDK guardduty v1.54.5) nao
  # conhece "RDS_DATA_RISK". O que importa aqui e o `status` (DISABLED), que continua
  # gerenciado; nenhuma destas features declara configuracao adicional. Rever ao
  # migrar para o provider 6.x (o SDK atual ja tem RDS_DATA_RISK): declarar o bloco
  # com status DISABLED e remover o ignore.
  lifecycle {
    ignore_changes = [additional_configuration]
  }
}

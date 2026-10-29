# Tabela do padrao assincrono 202 + job_id + polling (musclefit-plan-generator
# / musclefit-cardio-agent) - ver docs/aws-migration-plan.md, secao 1.2.

resource "aws_dynamodb_table" "musclefit_async_jobs" {
  name         = "musclefit-async-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Name = "musclefit-async-jobs"
  }
}

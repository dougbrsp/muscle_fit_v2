terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Backend criado manualmente no bootstrap da Fase 0 (docs/aws-migration-plan.md, secao 0.6).
  # `terraform init` deve importar esses recursos, nunca recria-los.
  backend "s3" {
    bucket         = "musclefit-tfstate-930413537709"
    key            = "fase1/terraform.tfstate"
    region         = "sa-east-1"
    dynamodb_table = "musclefit-tfstate-lock"
    encrypt        = true
  }
}

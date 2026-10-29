# Repositorio ECR para a imagem de container da Lambda musclefit-api-core
# (Lambda de imagem, nao zip - ver docs/aws-migration-plan.md, secao 1.2).

resource "aws_ecr_repository" "musclefit_api_core" {
  name = "musclefit-api-core"
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
    Name = "musclefit-api-core"
  }
}

resource "aws_ecr_lifecycle_policy" "musclefit_api_core" {
  repository = aws_ecr_repository.musclefit_api_core.name

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

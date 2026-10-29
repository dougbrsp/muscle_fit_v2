output "vpc_id" {
  value = aws_vpc.musclefit.id
}

output "db_cluster_arn" {
  description = "ARN do cluster - usado como resourceArn nas chamadas RDS Data API (boto3 rds-data)."
  value       = aws_rds_cluster.musclefit_db.arn
}

output "db_cluster_endpoint" {
  description = "Endpoint do writer - so relevante para conexao TCP direta (migracao/bastion), nao usado pelas Lambdas."
  value       = aws_rds_cluster.musclefit_db.endpoint
}

output "db_master_user_secret_arn" {
  description = "ARN do secret gerenciado pelo RDS com as credenciais master - usado como secretArn nas chamadas RDS Data API."
  value       = aws_rds_cluster.musclefit_db.master_user_secret[0].secret_arn
}

output "db_security_group_id" {
  value = aws_security_group.musclefit_db.id
}

output "frontend_assets_bucket" {
  description = "Nome do bucket S3 do build estatico do frontend - alvo do sync/upload em deploys futuros (ex.: aws s3 sync out/ s3://<bucket>)."
  value       = aws_s3_bucket.musclefit_frontend_assets.bucket
}

output "cloudfront_domain_name" {
  description = "Dominio da distribuicao CloudFront (dxxxxx.cloudfront.net). Informativo: e o alvo dos ALIAS app/apex em route53.tf (zona no Route 53 desde 2026-10-03, M44)."
  value       = aws_cloudfront_distribution.musclefit_cdn.domain_name
}

output "cloudfront_distribution_id" {
  description = "ID da distribuicao CloudFront - usado em 'aws cloudfront create-invalidation' apos cada deploy do frontend."
  value       = aws_cloudfront_distribution.musclefit_cdn.id
}

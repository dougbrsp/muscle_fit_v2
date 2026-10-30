#!/usr/bin/env bash
# Bootstrap dos recursos AWS espelhados no LocalStack para desenvolvimento
# local. Executado automaticamente pelo LocalStack via a convencao
# /etc/localstack/init/ready.d/ (roda uma vez, quando o container fica
# "ready"). Usa `awslocal` (wrapper da AWS CLI ja embutido na imagem
# localstack/localstack, apontado para o proprio LocalStack).
#
# Nomes de recursos e configuracao copiados EXATAMENTE do Terraform
# (infra/s3_uploads.tf, infra/s3_rag.tf, infra/dynamodb.tf, infra/secrets.tf)
# - nao inventados. Este script NAO aplica Terraform nem toca em AWS real:
# roda só dentro do container LocalStack, contra o endpoint local.
#
# Idempotente: cada `awslocal` abaixo tolera o recurso ja existir (checagem
# previa ou `|| true`), entao rodar o script mais de uma vez (ex.: restart do
# container sem `docker compose down -v`) nao falha.

set -euo pipefail

echo "[bootstrap] Iniciando criacao de recursos no LocalStack..."

# -----------------------------------------------------------------------------
# S3 - buckets (infra/s3_uploads.tf, infra/s3_rag.tf)
# -----------------------------------------------------------------------------
for bucket in musclefit-uploads musclefit-rag; do
  if awslocal s3api head-bucket --bucket "$bucket" >/dev/null 2>&1; then
    echo "[bootstrap] Bucket S3 '$bucket' ja existe, pulando."
  else
    echo "[bootstrap] Criando bucket S3 '$bucket'..."
    awslocal s3api create-bucket --bucket "$bucket" \
      --create-bucket-configuration LocationConstraint="${AWS_DEFAULT_REGION:-sa-east-1}" \
      || awslocal s3api create-bucket --bucket "$bucket"
  fi
done

# -----------------------------------------------------------------------------
# S3 - leitura anonima SO dos cards ilustrados PUBLICADOS
# (musclefit-uploads/exercise-cards/*). Equivalente de DEV da CDN de producao:
# la, a bucket policy (infra/cloudfront_uploads.tf) libera GetObject desse
# prefixo so para a distribuicao musclefit-cdn (behavior /exercise-cards/*);
# aqui o navegador le direto do LocalStack pela URL de
# PUBLIC_ASSETS_BASE_URL (docker-compose.yml). Os RASCUNHOS
# (exercise-card-drafts/*) ficam FORA de proposito, como em producao: sao
# abertos so por URL pre-assinada. Nenhum outro prefixo do bucket e liberado.
#
# ATENCAO: o LocalStack Community NAO aplica bucket policy (verificado em
# 2026-10-03 com localstack/localstack:3: GET anonimo responde 200 em
# exercise-cards/, exercise-card-drafts/ e equipment/, com ou sem esta
# policy). Ela fica aqui para declarar o MESMO escopo de producao e para o
# caso de a emulacao passar a aplica-la - mas, em DEV, NAO prove que o
# rascunho e privado. Essa garantia e de producao: bucket policy do OAC sem
# exercise-card-drafts/* + nenhum behavior para esse prefixo.
# Idempotente: put-bucket-policy substitui a policy inteira a cada boot.
# -----------------------------------------------------------------------------
echo "[bootstrap] Liberando GET anonimo em 'musclefit-uploads/exercise-cards/*'..."
awslocal s3api put-bucket-policy --bucket musclefit-uploads --policy '{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DevPublicReadPublishedExerciseCards",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::musclefit-uploads/exercise-cards/*"
    }
  ]
}'

# -----------------------------------------------------------------------------
# DynamoDB - tabela musclefit-async-jobs (infra/dynamodb.tf)
# hash key: job_id (S) | billing mode: PAY_PER_REQUEST | TTL: atributo "ttl"
# -----------------------------------------------------------------------------
TABLE_NAME="musclefit-async-jobs"
if awslocal dynamodb describe-table --table-name "$TABLE_NAME" >/dev/null 2>&1; then
  echo "[bootstrap] Tabela DynamoDB '$TABLE_NAME' ja existe, pulando create."
else
  echo "[bootstrap] Criando tabela DynamoDB '$TABLE_NAME'..."
  awslocal dynamodb create-table \
    --table-name "$TABLE_NAME" \
    --attribute-definitions AttributeName=job_id,AttributeType=S \
    --key-schema AttributeName=job_id,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST
  awslocal dynamodb wait table-exists --table-name "$TABLE_NAME"
fi

# TTL: sempre tenta habilitar (update-time-to-live e idempotente - reenviar a
# mesma config com Enabled=true novamente nao falha).
echo "[bootstrap] Habilitando TTL (atributo 'ttl') em '$TABLE_NAME'..."
awslocal dynamodb update-time-to-live \
  --table-name "$TABLE_NAME" \
  --time-to-live-specification "Enabled=true,AttributeName=ttl" \
  >/dev/null 2>&1 || echo "[bootstrap] TTL ja habilitado ou nao pode ser reaplicado, ignorando."

# -----------------------------------------------------------------------------
# Secrets Manager - 3 secrets (infra/secrets.tf)
# Valores placeholder de dev, lidos do proprio ambiente do container
# LocalStack (repassados via docker-compose.yml a partir do .env do host).
# -----------------------------------------------------------------------------
create_or_update_secret() {
  local name="$1"
  local value="$2"

  if awslocal secretsmanager describe-secret --secret-id "$name" >/dev/null 2>&1; then
    echo "[bootstrap] Secret '$name' ja existe, atualizando valor..."
    awslocal secretsmanager put-secret-value --secret-id "$name" --secret-string "$value" >/dev/null
  else
    echo "[bootstrap] Criando secret '$name'..."
    awslocal secretsmanager create-secret --name "$name" --secret-string "$value" >/dev/null
  fi
}

create_or_update_secret "musclefit/openai-api-key" "${OPENAI_API_KEY:-dev-placeholder}"
# Provedor Anthropic por feature (app/llm_anthropic.py). Sem chave no .env, o
# secret nasce com o placeholder (o Secrets Manager nao aceita valor vazio).
create_or_update_secret "musclefit/claude-api-key" "${ANTHROPIC_API_KEY:-dev-placeholder}"
create_or_update_secret "musclefit/api-token" "${API_TOKEN:-dev-local-token}"
create_or_update_secret "musclefit/secret-encryption-key" "${SECRET_ENCRYPTION_KEY:-dev-placeholder}"

# -----------------------------------------------------------------------------
# SES - identidade do remetente de "Esqueci minha senha" (M5). Em producao a
# identidade e verificada pelo Terraform (PR de infra); aqui basta marcar o
# endereco de PASSWORD_RESET_FROM_EMAIL como verificado para o `send_email`
# do LocalStack aceitar. Os e-mails enviados ficam em GET /_aws/ses.
# Idempotente: verify-email-identity repetido nao falha.
# -----------------------------------------------------------------------------
SES_FROM="${PASSWORD_RESET_FROM_EMAIL:-no-reply@musclefit.local}"
echo "[bootstrap] Verificando identidade SES '$SES_FROM'..."
awslocal ses verify-email-identity --email-address "$SES_FROM" \
  || echo "[bootstrap] SES indisponivel nesta imagem do LocalStack, ignorando (e-mail de reset nao sera aceito)."

echo "[bootstrap] Concluido: buckets S3 (+ leitura publica de exercise-cards/*), tabela DynamoDB, secrets e identidade SES prontos no LocalStack."

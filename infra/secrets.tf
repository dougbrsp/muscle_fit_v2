# Secrets usados pelas Lambdas (Fase 3).
#
# AWS-03, etapa 5 (2026-10-10): o Terraform gerencia SO o container de cada
# segredo (nome, descricao, tags), NUNCA o valor. Nao existe
# aws_secretsmanager_secret_version neste repositorio, e nao deve voltar a
# existir: o refresh de um _version chama GetSecretValue e grava o
# secret_string no state, o que obriga a role de plan (musclefit-gha-plan,
# assumivel por qualquer PR) a ter GetSecretValue. O valor e posto pelo dono
# da conta com `aws secretsmanager put-secret-value`
# (docs/runbooks/rotacao-segredos.md). Guarda: scripts/test_iam_gha_plan.py.
#
# As Lambdas recebem so o ARN do container (`*_SECRET_ARN`) e
# api/app/secrets_bootstrap.py le o AWSCURRENT no cold start.
#
# musclefit/openai-api-key: usado por plan-generator/cardio-agent/workout-ai/
# api-core (chamada direta, sem Bedrock - ver docs/aws-migration-plan.md).
resource "aws_secretsmanager_secret" "openai_api_key" {
  name        = "musclefit/openai-api-key"
  description = "Chave da API OpenAI, usada por plan-generator/cardio-agent (chamada direta, sem Bedrock - ver docs/aws-migration-plan.md secao 1.10)."
}

# musclefit/api-token: token mestre X-API-Token (equivalente ao API_TOKEN do
# ambiente on-premise).
resource "aws_secretsmanager_secret" "api_token" {
  name        = "musclefit/api-token"
  description = "Token mestre X-API-Token do backend (equivalente ao API_TOKEN on-premise)."
}

# musclefit/secret-encryption-key: chave usada por app/crypto_utils.py para
# criptografar credenciais de terceiros (ex.: API key do Intervals.icu)
# guardadas no banco.
resource "aws_secretsmanager_secret" "secret_encryption_key" {
  name        = "musclefit/secret-encryption-key"
  description = "Chave de criptografia (app/crypto_utils.py) para segredos de usuario guardados no banco (ex.: Intervals.icu API key)."
}

# musclefit/turnstile-secret-key: secret key do widget Cloudflare Turnstile
# (captcha) usada pelo backend para validar o token do formulario de "Esqueci
# minha senha" (melhoria M5) em https://challenges.cloudflare.com/turnstile/v0/siteverify.
# A SITE key (publica) NAO fica aqui: vai no build do frontend via variable do
# GitHub NEXT_PUBLIC_TURNSTILE_SITE_KEY (.github/workflows/deploy-producao.yml).
#
# Este secret JA EXISTIA na conta, criado a mao pelo
# usuario em 2026-09-22 com o valor real (ARN abaixo, sufixo -PGU7U1). Por isso:
#   - o bloco `import` traz o secret existente para o state no primeiro apply
#     (terraform >= 1.5; o pipeline roda `terraform apply` sem -target, entao
#     o import e processado junto). Depois de importado, o bloco vira no-op e
#     pode ser removido em um PR futuro;
#   - NAO existe aws_secretsmanager_secret_version aqui (nem nos outros, ver
#     o topo): o valor e gerenciado 100% fora do IaC (put-secret-value pelo
#     usuario, JA FEITO);
#   - a `description` abaixo e IGUAL, byte a byte, a que o usuario digitou no
#     console (describe-secret em 2026-09-22). NAO reescrever: a role de
#     deploy musclefit-gha-deploy NAO tem `secretsmanager:UpdateSecret` (a
#     consolidada `musclefit-gha-deploy-v2-rede-dados` so da
#     Create/Describe/Tag/Delete/GetResourcePolicy/GetSecretValue em
#     `musclefit/*`; ver infra/iam-policies/consolidacao/), e a role de plan
#     musclefit-gha-plan (`musclefit-gha-plan-leitura`, v3) so le metadado
#     (Describe/GetResourcePolicy). Qualquer diff em description faria o apply
#     falhar com AccessDeniedException. Se um dia quiser mudar o texto, mude
#     primeiro no console e depois aqui.
# Efeito no plan quando o import rodou (historico): 1 import + 1 update
# in-place SO de tags (o secret manual nao tinha tags; o provider aplica
# default_tags via TagResource, hoje coberto pela
# `musclefit-gha-deploy-v2-rede-dados`). Nada e recriado, nenhuma
# versao/valor e tocado.
# A Lambda recebe o ARN em TURNSTILE_SECRET_KEY_SECRET_ARN e
# api/app/secrets_bootstrap.py hidrata TURNSTILE_SECRET_KEY no cold start.
import {
  to = aws_secretsmanager_secret.turnstile_secret_key
  id = "arn:aws:secretsmanager:sa-east-1:930413537709:secret:musclefit/turnstile-secret-key-PGU7U1"
}

resource "aws_secretsmanager_secret" "turnstile_secret_key" {
  name        = "musclefit/turnstile-secret-key"
  description = "Segredo do widget do Cloudflare de verificação se é humano" # igual ao console; ver comentario acima (sem UpdateSecret no pipeline)
}

# musclefit/usda-api-key: chave da API do USDA FoodData Central (catalogo de
# alimentos do modulo de nutricao). Quem usa e a api-core, nas rotas
# /nutrition/foods/usda/* do Lote L3b (docs/nutricao/PLANO_NUTRICAO_2026-10-14.md,
# secao 8.1), que respondem 503 quando a chave falta. Chave gratuita do
# api.data.gov (https://api.data.gov/signup/), a mesma que o FDC pede.
#
# Mesmo caso do Turnstile acima: o secret JA EXISTIA, criado a mao pelo
# usuario em 2026-10-17 com o valor real (ARN abaixo, sufixo -cMHVbT, KMS
# padrao aws/secretsmanager, sem tags). Por isso:
#   - `import` em vez de create (create falharia com ResourceExistsException);
#   - NENHUM aws_secretsmanager_secret_version (regra do topo do arquivo);
#   - `description` IGUAL, byte a byte, a do console (describe-secret em
#     2026-10-17). O pipeline nao tem secretsmanager:UpdateSecret: qualquer
#     diff aqui faria o apply falhar logo apos o import.
# Plan esperado: 1 import + update in-place SO de tags (default_tags do
# provider via TagResource, coberto pela `musclefit-gha-deploy-v2-rede-dados`).
# A role de plan le o metadado pelo statement SecretsMetadados
# (Describe/GetResourcePolicy em secret:musclefit/*), sem GetSecretValue.
# A api-core recebe o ARN em USDA_API_KEY_SECRET_ARN e
# api/app/secrets_bootstrap.py hidrata USDA_API_KEY no cold start.
import {
  to = aws_secretsmanager_secret.usda_api_key
  id = "arn:aws:secretsmanager:sa-east-1:930413537709:secret:musclefit/usda-api-key-cMHVbT"
}

resource "aws_secretsmanager_secret" "usda_api_key" {
  name        = "musclefit/usda-api-key"
  description = "Chave de API de acesso ao USDA" # igual ao console; ver comentario acima (sem UpdateSecret no pipeline)
}

# AWS-03, etapa 5: os 3 aws_secretsmanager_secret_version que nasceram com o
# placeholder "replace_me" + ignore_changes (2026-09-03) saem do state SEM
# destruir nada. `destroy = false` (Terraform >= 1.7, versions.tf) faz o apply
# so esquecer o recurso: nenhuma chamada a AWS, a versao do placeholder
# (hoje AWSPREVIOUS) e a versao real (AWSCURRENT) continuam intactas, e as
# Lambdas, que dependem so do ARN do container, nao mudam. Plan esperado:
# 3 "will no longer be managed by Terraform", 0 to add/change/destroy.
# Depois do apply em producao estes blocos viram no-op; podem ficar como
# registro ou sair num PR futuro.
removed {
  from = aws_secretsmanager_secret_version.openai_api_key

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_secretsmanager_secret_version.api_token

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_secretsmanager_secret_version.secret_encryption_key

  lifecycle {
    destroy = false
  }
}

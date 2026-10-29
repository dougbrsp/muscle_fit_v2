# MUSCLE_FIT — Documentação descritiva da aplicação (versão AWS)

> Documento de contexto gerado em 2026-09-12 a partir do repositório `C:\muscle_fit` (branch `main`, commit `0a5cd5c`) e de consultas somente leitura à conta AWS.
> Finalidade: servir de contexto para análise por outra LLM. O conteúdo descreve o estado atual do sistema, sem propostas de alteração.
> Atualização 2026-10-09 (COD-05/M57): saíram as rotas `/analytics/muscle-groups`, `-trend`, `-table` (com `MUSCLE_GROUP_CASE`), `/analytics/dashboard-overview`, `/analytics/dashboard-engine-audit`, `POST /exercise-mapping-catalog/import` e `/equipment-exercise-mappings` (GET/POST/DELETE). Citações dessas rotas nas seções de frontend abaixo são histórico (nenhuma tela as chama).
> Itens marcados como "não verificado" não puderam ser confirmados na fonte.

## Sumário

1. Parte I — Infraestrutura, CI/CD e bancos de dados
2. Parte II — Backend (`api/app`)
3. Parte III — Frontend (`frontend/`)

---

# Parte I — Infraestrutura, CI/CD e bancos de dados

## MuscleFit (versão AWS) — Infraestrutura, CI/CD e Bancos de Dados

Documento descritivo do estado atual. Fonte primária: repositório `C:\muscle_fit` (branch `main`, último commit lido `0a5cd5c`). Fatos marcados como **confirmado em produção** foram lidos da conta AWS `930413537709` em modo somente leitura (`aws sts get-caller-identity`, `aws lambda list-functions`, `aws rds-data execute-statement` com `SHOW TABLES`/`SHOW CREATE TABLE`/`SELECT` sobre `information_schema` e `schema_migrations`, etc.). Onde não houve confirmação, consta "não verificado".

---

### 1. Visão geral da arquitetura

#### 1.1 Conta, região e domínios

| Item | Valor |
|---|---|
| Conta AWS | `930413537709` |
| Região principal | `sa-east-1` (todos os recursos regionais: API Gateway, Lambdas, Aurora, DynamoDB, S3, ECR, Secrets Manager, CloudWatch Logs) |
| Região secundária | `us-east-1` — apenas o certificado ACM do CloudFront (`data.aws_acm_certificate.frontend`, provider alias `aws.us_east_1`) |
| Domínio raiz | `musclefit.com.br` — o Registro.br é só o **registrador**; desde 2026-10-03 o DNS autoritativo é a hosted zone pública do Route 53, gerida por `infra/route53.tf` (M44, encerrado em 2026-10-05). Histórico das decisões em `docs/aws-migration-plan.md`, item 0.3 |
| Frontend | `https://app.musclefit.com.br` e `https://musclefit.com.br` (apex) — ambos aliases da mesma distribuição CloudFront `EOA1PMADLM7RO` (`d2kf866t8pk1us.cloudfront.net`) |
| API | `https://api.musclefit.com.br` — `aws_api_gateway_domain_name`, alvo regional `d-u1fd3xctnj.execute-api.sa-east-1.amazonaws.com`, mapeado ao stage `staging` da REST API `vaercx9o3g` (`musclefit-api`) |
| URL de invocação direta do stage | `https://vaercx9o3g.execute-api.sa-east-1.amazonaws.com/staging`: **desligada** pelo SEG-05 (`disable_execute_api_endpoint`, `var.api_disable_execute_api_endpoint`), responde 403. Sem uso interno desde o SEG-04. Borda da API (WAF `musclefit-api`, throttling 30 rps/60 burst, access log 14 d) em `infra/api_gateway_borda.tf` |
| Registros DNS | Todos em `infra/route53.tf`: apex `musclefit.com.br` e `app` → ALIAS A/AAAA para o CloudFront `d2kf866t8pk1us.cloudfront.net`; `api` → ALIAS A para o domínio regional `d-u1fd3xctnj.execute-api.sa-east-1.amazonaws.com`; MX/SPF/DMARC e DKIM `sig1` do iCloud; 3 DKIM do SES e MAIL FROM `mail.`; 3 CNAMEs de validação do ACM. Apex resolvendo. Registro DNS novo entra **só por PR** em `infra/route53.tf`, nunca no painel do Registro.br |
| Hosted zone Route 53 | `musclefit.com.br` pública (`aws_route53_zone.musclefit`, `prevent_destroy`; ID em `terraform output route53_zone_id`), criada pelo PR #338 e promovida no #339. Delegação publicada no Registro.br em 2026-10-03 17:40 BRT: `ns-1272.awsdns-31.org`, `ns-1996.awsdns-57.co.uk`, `ns-366.awsdns-45.com`, `ns-911.awsdns-49.net`. DNSSEC desligado (DS removido no Registro.br antes da troca); DNSSEC no Route 53 fica como melhoria opcional. A zona anterior `Z0922149C7W1FJLN2G91` (2026-09-03/04) foi apagada e não tem relação com a atual |
| Perfis AWS CLI locais | `musclefit` (provider `login_session`) e `musclefit-tf` (`credential_process` que reutiliza a mesma sessão); ambos resolvem para `arn:aws:iam::930413537709:root` |

#### 1.2 Componentes

| Componente | Recurso | Papel |
|---|---|---|
| CDN | CloudFront `musclefit-cdn` (`EOA1PMADLM7RO`) | Serve o export estático do Next.js a partir do bucket `musclefit-frontend-assets` (OAC) e as fotos públicas de equipamento/academia a partir do bucket `musclefit-uploads` (OAC) nos paths `/equipment/*` e `/gyms/*` |
| API | API Gateway REST `musclefit-api` (`vaercx9o3g`), regional, stage `staging` | Proxy integration (`AWS_PROXY`) para `musclefit-api-core` no catch-all `{proxy+}` e na raiz; rotas explícitas para `musclefit-engines` em 36 paths |
| Lambda HTTP | `musclefit-api-core` (FastAPI + Lambda Web Adapter), `musclefit-engines` (FastAPI + Lambda Web Adapter) | CRUD/catálogo/autenticação/uploads e motores determinísticos, respectivamente |
| Lambdas assíncronas | `musclefit-plan-generator`, `musclefit-cardio-agent`, `musclefit-workout-ai` (imagem `musclefit-ai-generators`, `awslambdaric`), `musclefit-reports` (imagem própria com WeasyPrint, `awslambdaric`) | Invocadas por `musclefit-api-core` via `lambda:InvokeFunction` `InvocationType=Event`; gravam resultado em DynamoDB |
| Estado de jobs | DynamoDB `musclefit-async-jobs` (PK `job_id`, TTL) | Padrão 202 + `job_id` + polling em `GET /jobs/{job_id}` |
| Banco relacional | Aurora Serverless v2 MySQL-compatible `musclefit-db` (0,5–1 ACU), RDS Data API habilitada | Acesso exclusivamente via Data API (HTTPS); VPC sem NAT/IGW; nenhuma Lambda em VPC |
| Segredos | Secrets Manager `musclefit/openai-api-key`, `musclefit/api-token`, `musclefit/secret-encryption-key`, secret master gerado pelo RDS `rds!cluster-b3317b3e-...` | Lidos no cold start (`secrets_bootstrap.py`) e pelo Data API |
| Objetos | S3 `musclefit-frontend-assets`, `musclefit-uploads`, `musclefit-rag`, `musclefit-tfstate-930413537709` | Frontend, fotos/PDFs, RAG markdown, state do Terraform |
| Registro de imagens | ECR `musclefit-api-core`, `musclefit-engines`, `musclefit-ai-generators`, `musclefit-reports` | Uma imagem por Dockerfile; tag `latest` |
| Logs | CloudWatch Log Groups `/aws/lambda/<função>` com retenção de 14 dias | — |
| IA generativa | OpenAI (`https://api.openai.com/v1/responses`) via `urllib` (`openai_client.py`) | Bedrock não é usado |
| Cliente externo | Relógio Amazfit/Zepp (origem CORS `https://zepp-os.zepp.com`) | Pareamento via `/devices/pair` com token mestre; depois token por dispositivo |

#### 1.3 Fluxo de uma requisição (browser → banco)

1. O browser carrega `https://app.musclefit.com.br/<rota>`. O CloudFront executa a função `musclefit-spa-index-rewrite` no evento `viewer-request`, que reescreve o URI para o objeto real no S3 (`/<rota>/index.html`, ou `/<prefixo>/placeholder/index.html` para as 4 rotas dinâmicas). O objeto é lido de `musclefit-frontend-assets` via OAC; respostas 403/404 da origem são convertidas em `/index.html` com status 200 (`custom_error_response`).
2. O JavaScript do frontend (Next.js 14 `output: 'export'`, `trailingSlash: true`) chama `https://api.musclefit.com.br` diretamente (`frontend/src/lib/config.ts`, `API_BASE_URL`), com `credentials: 'include'`. Não há proxy `/api/*` no frontend.
3. `api.musclefit.com.br` resolve (CNAME) para o custom domain regional do API Gateway. O API Gateway REST (`binary_media_types = ["*/*"]`, `authorization = NONE`) encaminha por `AWS_PROXY` para `musclefit-api-core` (catch-all) ou `musclefit-engines` (36 paths explícitos).
4. Na Lambda, o Lambda Web Adapter (`/opt/extensions/lambda-adapter`, versão 0.9.1) converte o evento em requisição HTTP para o `uvicorn` local na porta 8000 (`app.main:app` ou `app.main_engines:app`).
5. O FastAPI resolve a identidade em `security.resolve_identity` a partir do header `X-API-Token` ou do cookie `musclefit_session` (token mestre → token de dispositivo → sessão em `user_sessions`).
6. O acesso a dados passa por `app/db.py`: cada "conexão lógica" abre `BeginTransaction` no RDS Data API (`boto3` `rds-data`, `resourceArn`=ARN do cluster, `secretArn`=secret master gerado pelo RDS, `database=musclefit`) e executa `ExecuteStatement` com parâmetros nomeados.
7. Endpoints de IA/relatório respondem `202 {job_id}` após `PutItem` em `musclefit-async-jobs` e `Invoke(Event)` na Lambda correspondente; o frontend faz polling em `GET /jobs/{job_id}` (ou `GET /reports/{id}` no caso de relatórios).
8. Fotos de equipamento/academia são servidas diretamente pelo CloudFront a partir de `musclefit-uploads` (`https://app.musclefit.com.br/equipment/<id>.<ext>`, `/gyms/<id>.<ext>`); fotos de usuário e PDFs de relatório são servidos pela Lambda (`s3.get_object` → `Response`).

#### 1.4 Relação com o ambiente on-premise

`README.md` descreve dois ambientes apartados: o on-premise (`openclaw-dev-agent`, Docker Compose, MySQL `fitness` no container `fitness-mysql`) e o AWS (este repositório). A carga inicial do Aurora foi feita por `mysqldump` do on-premise via RDS Data API (Fase 1). A ferramenta `tools/onprem-sync` (seção 9) espelha dados on-premise → Aurora sob demanda.

---

### 2. Terraform (`infra/`)

#### 2.1 Backend de state, providers e versões

| Arquivo | Conteúdo |
|---|---|
| `versions.tf` | `required_version = ">= 1.7"`; provider `hashicorp/aws` `~> 5.0`; backend `s3`: bucket `musclefit-tfstate-930413537709`, key `fase1/terraform.tfstate`, região `sa-east-1`, `dynamodb_table = "musclefit-tfstate-lock"`, `encrypt = true` |
| `.terraform.lock.hcl` | `hashicorp/aws` versão **5.100.0** (constraint `~> 5.0`) |
| `provider.tf` | Provider `aws` padrão, `region = var.aws_region`, `default_tags`: `project=musclefit`, `managed_by=terraform`, `created_by=musclefit-gha-deploy` |
| `cloudfront.tf` (trecho) | Provider `aws` alias `us_east_1`, `region = "us-east-1"`, mesmas `default_tags` |
| `infra/.terraform/` | Contém `terraform.tfstate` local de backend (1593 bytes) e diretório `providers/` vazio; ignorado pelo `.gitignore` (`**/.terraform/*`) |

#### 2.2 Variáveis (`variables.tf` e variáveis declaradas em outros arquivos)

| Variável | Arquivo | Tipo | Default |
|---|---|---|---|
| `aws_region` | `variables.tf` | string | `"sa-east-1"` |
| `vpc_cidr` | `variables.tf` | string | `"10.20.0.0/16"` |
| `private_subnet_cidrs` | `variables.tf` | list(string) | `["10.20.1.0/24", "10.20.2.0/24"]` |
| `db_name` | `variables.tf` | string | `"musclefit"` |
| `db_master_username` | `variables.tf` | string | `"musclefit_admin"` |
| `engine_version` | `variables.tf` | string | `"8.0.mysql_aurora.3.08.2"` |
| `min_acu` | `variables.tf` | number | `0.5` |
| `max_acu` | `variables.tf` | number | `1` |
| `deletion_protection` | `variables.tf` | bool | `true` |
| `backup_retention_days` | `variables.tf` | number | `1` |
| `migration_allowed_cidr_blocks` | `variables.tf` | list(string) | `[]` |
| `api_core_image_tag` | `lambda_api_core.tf` | string | `"latest"` |
| `openai_model_plan` | `lambda_api_core.tf` | string | `"gpt-4o"` |
| `openai_model_cardio` | `lambda_api_core.tf` | string | `"gpt-4o-mini"` |
| `ai_generators_image_tag` | `lambda_ai_generators.tf` | string | `"latest"` |
| `engines_image_tag` | `lambda_engines.tf` | string | `"latest"` |
| `reports_image_tag` | `lambda_reports.tf` | string | `"latest"` |
| `openai_model_set_eval` | `lambda_workout_ai.tf` | string | `"gpt-5.4-mini"` |
| `openai_model_exercise_summary` | `lambda_workout_ai.tf` | string | `"gpt-5.4-mini"` |
| `openai_model_workout_summary` | `lambda_workout_ai.tf` | string | `"gpt-5.4-mini"` |

Não existe arquivo `*.tfvars` versionado (`.gitignore` exclui `*.tfvars`).

#### 2.3 Outputs

| Output | Arquivo | Valor |
|---|---|---|
| `vpc_id` | `outputs.tf` | `aws_vpc.musclefit.id` |
| `db_cluster_arn` | `outputs.tf` | `aws_rds_cluster.musclefit_db.arn` |
| `db_cluster_endpoint` | `outputs.tf` | endpoint writer |
| `db_master_user_secret_arn` | `outputs.tf` | `master_user_secret[0].secret_arn` |
| `db_security_group_id` | `outputs.tf` | `aws_security_group.musclefit_db.id` |
| `frontend_assets_bucket` | `outputs.tf` | `musclefit-frontend-assets` |
| `cloudfront_domain_name` | `outputs.tf` | `d2kf866t8pk1us.cloudfront.net` |
| `cloudfront_distribution_id` | `outputs.tf` | `EOA1PMADLM7RO` |
| `api_gateway_invoke_url` | `api_gateway.tf` | `aws_api_gateway_stage.staging.invoke_url` |
| `api_custom_domain_target` | `api_gateway.tf` | `aws_api_gateway_domain_name.api.regional_domain_name` |
| `rag_bucket_name` | `s3_rag.tf` | `musclefit-rag` |

#### 2.4 `vpc.tf`

| Tipo | Nome lógico | Nome físico / atributos |
|---|---|---|
| `data.aws_availability_zones` | `available` | `state = "available"` |
| `aws_vpc` | `musclefit` | `musclefit-vpc`, CIDR `10.20.0.0/16`, DNS support/hostnames habilitados |
| `aws_subnet` | `private[count]` | 2 subnets (`10.20.1.0/24`, `10.20.2.0/24`), uma por AZ (`names[count.index]`), tag `musclefit-private-<az>` |
| `aws_db_subnet_group` | `musclefit` | `musclefit-db-subnet-group` |
| `aws_security_group` | `musclefit_db` | `musclefit-db-sg`; sem regras de ingresso por padrão |
| `aws_vpc_security_group_ingress_rule` | `mysql_migration_window[count]` | TCP 3306 para cada CIDR em `var.migration_allowed_cidr_blocks` (default vazio → 0 regras) |
| `aws_vpc_security_group_egress_rule` | `allow_all_outbound` | `0.0.0.0/0`, protocolo `-1` |

Não há Internet Gateway, NAT Gateway, route tables customizadas, VPC endpoints nem subnets públicas. Nenhuma Lambda está associada à VPC.

#### 2.5 `aurora.tf`

| Tipo | Nome lógico | Atributos |
|---|---|---|
| `aws_rds_cluster` | `musclefit_db` | `cluster_identifier = "musclefit-db"`, `engine = "aurora-mysql"`, `engine_mode = "provisioned"`, `engine_version = var.engine_version`, `database_name = "musclefit"`, `master_username = "musclefit_admin"`, `manage_master_user_password = true`, `db_subnet_group_name`, `vpc_security_group_ids`, `enable_http_endpoint = true` (Data API), `serverlessv2_scaling_configuration { min 0.5 / max 1 }`, `storage_type = "aurora"`, `backup_retention_period = 1`, `preferred_backup_window = "06:00-07:00"` (UTC), `copy_tags_to_snapshot = true`, `deletion_protection = true`, `skip_final_snapshot = false`, `final_snapshot_identifier = "musclefit-db-final"`, `enabled_cloudwatch_logs_exports = ["error","slowquery","audit"]` |
| `aws_rds_cluster_instance` | `writer` | `identifier = "musclefit-db-instance-1"`, `instance_class = "db.serverless"`, `publicly_accessible = false` |

**Confirmado em produção** (`describe-db-clusters`): `EngineVersion = 8.0.mysql_aurora.3.08.2`, `Status = available`, `HttpEndpointEnabled = true`, `ServerlessV2ScalingConfiguration = {MinCapacity 0.5, MaxCapacity 1.0}`, `BackupRetentionPeriod = 1`, `DeletionProtection = true`, `StorageType = null` na resposta da API. `SELECT VERSION()` retorna `8.0.39`; `@@character_set_database = utf8mb4`; `@@collation_database = utf8mb4_0900_ai_ci`.

Identificadores registrados em `docs/aws-migration-plan.md` (Fase 1): ARN do cluster `arn:aws:rds:sa-east-1:930413537709:cluster:musclefit-db`; endpoint writer `musclefit-db.cluster-cxamesiqocyy.sa-east-1.rds.amazonaws.com`; secret master `arn:aws:secretsmanager:sa-east-1:930413537709:secret:rds!cluster-b3317b3e-2be1-41f8-9c18-132e4847112f-Yy88bJ`. Não há réplica de leitura, Multi-AZ, parameter group customizado, `aws_rds_cluster_parameter_group`, Performance Insights ou auto-pause declarados no Terraform (o plano menciona "auto-pause habilitado" como intenção; nenhum atributo correspondente existe em `aurora.tf`).

#### 2.6 `dynamodb.tf`

| Tipo | Nome lógico | Atributos |
|---|---|---|
| `aws_dynamodb_table` | `musclefit_async_jobs` | `name = "musclefit-async-jobs"`, `billing_mode = "PAY_PER_REQUEST"`, `hash_key = "job_id"` (tipo `S`), TTL habilitado no atributo `ttl`; sem sort key, sem GSI/LSI, sem streams, sem PITR declarado |

A tabela `musclefit-tfstate-lock` (chave `LockID`, `PAY_PER_REQUEST`) existe na conta, foi criada manualmente na Fase 0 e não é gerenciada por este Terraform. **Confirmado em produção**: `list-tables` retorna `musclefit-async-jobs` e `musclefit-tfstate-lock`.

#### 2.7 ECR (`ecr.tf`, `lambda_ai_generators.tf`, `lambda_engines.tf`, `lambda_reports.tf`)

| Nome lógico | Repositório | Arquivo | Tag mutability | Scan on push | Lifecycle |
|---|---|---|---|---|---|
| `aws_ecr_repository.musclefit_api_core` | `musclefit-api-core` | `ecr.tf` | `MUTABLE` | sim | Mantém as 10 imagens mais recentes (`imageCountMoreThan 10`, `tagStatus any`, `expire`) |
| `aws_ecr_repository.musclefit_ai_generators` | `musclefit-ai-generators` | `lambda_ai_generators.tf` | `MUTABLE` | sim | idem |
| `aws_ecr_repository.musclefit_engines` | `musclefit-engines` | `lambda_engines.tf` | `MUTABLE` | sim | idem |
| `aws_ecr_repository.musclefit_reports` | `musclefit-reports` | `lambda_reports.tf` | `MUTABLE` | sim | idem |

URI de registro: `930413537709.dkr.ecr.sa-east-1.amazonaws.com/<repo>`. **Confirmado em produção**: os 4 repositórios existem.

#### 2.8 `secrets.tf`

| Nome lógico | Nome do secret | Valor |
|---|---|---|
| `aws_secretsmanager_secret.openai_api_key` | `musclefit/openai-api-key` | fora do Terraform (`put-secret-value`) |
| `aws_secretsmanager_secret.api_token` | `musclefit/api-token` | fora do Terraform (`put-secret-value`) |
| `aws_secretsmanager_secret.secret_encryption_key` | `musclefit/secret-encryption-key` | fora do Terraform (`put-secret-value`) |
| `aws_secretsmanager_secret.turnstile_secret_key` (`import`) | `musclefit/turnstile-secret-key` | fora do Terraform (`put-secret-value`) |

O Terraform gerencia só o container de cada segredo. Até o AWS-03 (etapa 5, 2026-10-10) os 3 primeiros tinham também um `aws_secretsmanager_secret_version` com o placeholder `"replace_me"` e `ignore_changes = [secret_string]`; eles saíram do state por blocos `removed { lifecycle { destroy = false } }` em `secrets.tf`, sem tocar nas versões da AWS, para que a role de plan não precise de `secretsmanager:GetSecretValue`. Os valores reais foram definidos fora do Terraform (`aws secretsmanager put-secret-value`, `docs/runbooks/rotacao-segredos.md`). Não há KMS key customizada (criptografia com a chave padrão do serviço), nem rotação declarada. **Confirmado em produção**: `list-secrets` retorna `rds!cluster-b3317b3e-2be1-41f8-9c18-132e4847112f`, `musclefit/secret-encryption-key`, `musclefit/openai-api-key`, `musclefit/api-token`. O secret `musclefit/intervals-api-key` citado no plano (seção 1.7) não existe na conta nem no Terraform; as chaves do Intervals.icu são armazenadas por usuário em `users.intervals_api_key_encrypted` (Fernet, `crypto_utils.py`).

#### 2.9 `api_gateway.tf`

| Tipo | Nome lógico | Atributos |
|---|---|---|
| `data.aws_acm_certificate` | `api` | `domain = "api.musclefit.com.br"`, `statuses = ["ISSUED"]`, `types = ["AMAZON_ISSUED"]`, `most_recent = true` (região padrão `sa-east-1`) |
| `aws_api_gateway_rest_api` | `musclefit_api` | `name = "musclefit-api"`, `binary_media_types = ["*/*"]`, `endpoint_configuration.types = ["REGIONAL"]` |
| `aws_api_gateway_resource` | `proxy` | `path_part = "{proxy+}"` sob a raiz |
| `aws_api_gateway_method` | `proxy_any` | `ANY`, `authorization = "NONE"`, `api_key_required = false` |
| `aws_api_gateway_integration` | `proxy_any` | `AWS_PROXY`, `integration_http_method = "POST"`, `uri = musclefit_api_core.invoke_arn` |
| `aws_api_gateway_method` | `root_any` | `ANY` na raiz, `authorization = "NONE"` |
| `aws_api_gateway_integration` | `root_any` | `AWS_PROXY` → `musclefit_api_core` |
| `aws_lambda_permission` | `apigw_invoke_api_core` | `AllowAPIGatewayInvoke`, principal `apigateway.amazonaws.com`, `source_arn = <execution_arn>/*/*` |
| `aws_api_gateway_resource` | `analytics` | `path_part = "analytics"` |
| `aws_api_gateway_resource` | `engines_analytics_path[for_each]` | 36 recursos filhos de `/analytics` (lista abaixo) |
| `aws_api_gateway_method` / `_integration` | `engines_analytics_any[for_each]` | `ANY`, `NONE`, `AWS_PROXY` → `musclefit_engines.invoke_arn` |
| `aws_api_gateway_resource` | `exercises` | `path_part = "exercises"` |
| `aws_api_gateway_resource` | `engines_exercise_path[for_each]` | `suggested-progression`, `suggested-set-count-for-substitution` |
| `aws_api_gateway_method` / `_integration` | `engines_exercise_any[for_each]` | `ANY`, `NONE`, `AWS_PROXY` → `musclefit_engines` |
| `aws_lambda_permission` | `apigw_invoke_engines` | idem, para `musclefit-engines` |
| `aws_api_gateway_deployment` | `musclefit_api` | `triggers.redeployment = sha1(jsonencode([...ids...]))`, `create_before_destroy = true`, `depends_on` das 4 integrations |
| `aws_api_gateway_stage` | `staging` | `stage_name = "staging"`; sem `access_log_settings`, sem `method_settings`, sem cache, sem throttling customizado, sem X-Ray declarado |
| `aws_api_gateway_domain_name` | `api` | `api.musclefit.com.br`, `regional_certificate_arn = data.aws_acm_certificate.api.arn`, `REGIONAL` |
| `aws_api_gateway_base_path_mapping` | `api` | mapeia o stage `staging` no domínio, sem base path |

Paths `/analytics/*` roteados para `musclefit-engines` (`local.engines_analytics_path_parts`, 36 itens): `summary`, `training-trend`, `exercise-progress`, `rpe-distribution`, `cardio`, `muscle-groups-equivalent-sets-by-activity`, `muscle-activation-by-activity`, `muscle-groups-recent-exercise-volume`, `muscle-groups-daily-activation`, `muscle-activation-preview`, `muscle-activation-breakdown`, `dashboard-muscle-status`, `dashboard-muscle-drilldown`, `dashboard-progression`, `dashboard-progression-trend`, `dashboard-progression-suggestion`, `dashboard-workout-sessions`, `dashboard-recovery`, `dashboard-cycle`, `dashboard-stimulus-fatigue`, `dashboard-alerts`, `health`, `body-composition-trend`, `training-volume`, `training-volume-by-split-day`, `muscle-recovery`, `muscle-recovery-consecutive-check`, `muscle-group-body-map`, `dashboard-home`, `training-evolution`, `cardio-agent-context`.

Qualquer outro path (inclusive `/analytics/muscle-groups-mapped-*`, `/exercises/history`, `/exercises/logs`) cai no catch-all `{proxy+}` → `musclefit-api-core`. Não há authorizer (Lambda, Cognito ou IAM), API key, usage plan, request validator ou modelo declarado. Desde o SEG-05 o stage tem WAF regional `musclefit-api` (limite por IP + CRS bloqueando, com `SizeRestrictions_BODY` e `NoUserAgent_HEADER` em Count; decisão de 2026-10-18), throttling e access log (`infra/api_gateway_borda.tf`). A autenticação é feita pelo FastAPI (`security.py`). **Confirmado em produção**: REST API `vaercx9o3g` `musclefit-api` `REGIONAL`; domain name `api.musclefit.com.br` → `d-u1fd3xctnj.execute-api.sa-east-1.amazonaws.com`.

#### 2.10 `cloudfront.tf`, `cloudfront_uploads.tf` e `cloudfront-functions/spa-index-rewrite.js`

| Tipo | Nome lógico | Atributos |
|---|---|---|
| `provider aws` | alias `us_east_1` | `region = "us-east-1"` |
| `data.aws_acm_certificate` | `frontend` | provider `aws.us_east_1`, `domain = "app.musclefit.com.br"`, `ISSUED`, `AMAZON_ISSUED`, `most_recent` (ARN registrado: `arn:aws:acm:us-east-1:930413537709:certificate/4c2d79c0-61bb-4ee5-847f-1265513edd5a`, SAN com o apex) |
| `aws_cloudfront_function` | `spa_index_rewrite` | `name = "musclefit-spa-index-rewrite"`, `runtime = "cloudfront-js-2.0"`, `publish = true`, `code = file(cloudfront-functions/spa-index-rewrite.js)` |
| `aws_cloudfront_function` | `security_headers` | `name = "musclefit-security-headers"`, `runtime = "cloudfront-js-2.0"`, `publish = true`, `code = file(cloudfront-functions/security-headers.js)`; acrescenta `Content-Security-Policy-Report-Only` às respostas `text/html` (SEG-08/AWS-09; só registra no console, não bloqueia) |
| `aws_cloudfront_origin_access_control` | `musclefit_frontend_assets` | `musclefit-frontend-assets-oac`, tipo `s3`, `signing_behavior = always`, `sigv4` |
| `aws_cloudfront_origin_access_control` | `musclefit_uploads` (em `cloudfront_uploads.tf`) | `musclefit-uploads-oac`, tipo `s3`, `always`, `sigv4` |
| `aws_cloudfront_distribution` | `musclefit_cdn` | `comment = "musclefit-cdn"`, `enabled`, `is_ipv6_enabled = true`, `default_root_object = "index.html"`, `aliases = ["app.musclefit.com.br", "musclefit.com.br"]`, `price_class` não declarado (default `PriceClass_All`), `restrictions.geo_restriction = none`, `viewer_certificate { acm_certificate_arn, ssl_support_method = sni-only, minimum_protocol_version = TLSv1.2_2021 }`; sem `logging_config`, sem `web_acl_id`, sem response headers policy |

Origins e behaviors da distribuição:

| Origin id | Domínio | OAC |
|---|---|---|
| `musclefit-frontend-assets` | `aws_s3_bucket.musclefit_frontend_assets.bucket_regional_domain_name` | `musclefit-frontend-assets-oac` |
| `musclefit-uploads` | `aws_s3_bucket.musclefit_uploads.bucket_regional_domain_name` | `musclefit-uploads-oac` |

| Behavior | path_pattern | Origin | Métodos | Cache policy | Function association |
|---|---|---|---|---|---|
| default | — | `musclefit-frontend-assets` | GET, HEAD (cached GET, HEAD) | `658327ea-f89d-4fab-a63d-7e88639e58f6` (AWS Managed `CachingOptimized`) | `viewer-request` → `musclefit-spa-index-rewrite`; `viewer-response` → `musclefit-security-headers` |
| ordered 1 | `/equipment/*` | `musclefit-uploads` | GET, HEAD | `CachingOptimized` | nenhuma |
| ordered 2 | `/gyms/*` | `musclefit-uploads` | GET, HEAD | `CachingOptimized` | nenhuma |

Todos os behaviors usam `viewer_protocol_policy = "redirect-to-https"` e `compress = true`. `custom_error_response`: `403 → /index.html (200)` e `404 → /index.html (200)` — aplicáveis à distribuição inteira, incluindo os behaviors de fotos.

Código de `spa-index-rewrite.js` (comportamento):

```javascript
var DYNAMIC_ROUTE_PREFIXES = ['/registro-exercicios/', '/planos-treino/', '/tipos-treinamento/', '/modelos-treino/'];
function handler(event) {
  var request = event.request; var uri = request.uri;
  for (var i = 0; i < DYNAMIC_ROUTE_PREFIXES.length; i++) {
    var prefix = DYNAMIC_ROUTE_PREFIXES[i];
    if (uri.indexOf(prefix) === 0) {
      var rest = uri.slice(prefix.length); var firstSegment = rest.split('/')[0];
      if (firstSegment !== '' && firstSegment !== 'placeholder') { request.uri = prefix + 'placeholder/index.html'; return request; }
      break;
    }
  }
  if (uri.endsWith('/')) { request.uri += 'index.html'; }
  else if (!uri.includes('.')) { request.uri += '/index.html'; }
  return request;
}
```

Regras: (a) sob um dos 4 prefixos dinâmicos, qualquer primeiro segmento diferente de vazio e de `placeholder` é reescrito para `<prefixo>placeholder/index.html`; (b) URI terminando em `/` recebe `index.html`; (c) URI sem `.` recebe `/index.html`; (d) URIs com `.` (assets, fotos) passam inalterados.

`cloudfront_uploads.tf` também declara `data.aws_iam_policy_document.musclefit_uploads_cloudfront` e `aws_s3_bucket_policy.musclefit_uploads`: `s3:GetObject` para `cloudfront.amazonaws.com` em `arn:aws:s3:::musclefit-uploads/equipment/*` e `.../gyms/*`, condição `AWS:SourceArn = <arn da distribuição>`. O prefixo `reports/` e `users/` não estão na policy.

**Confirmado em produção**: distribuição `EOA1PMADLM7RO`, domínio `d2kf866t8pk1us.cloudfront.net`, aliases `app.musclefit.com.br` e `musclefit.com.br`, comment `musclefit-cdn`.

#### 2.11 Buckets S3 (`s3_frontend.tf`, `s3_uploads.tf`, `s3_rag.tf`)

| Bucket | Nome lógico | Versionamento | SSE | Public access block | Bucket policy | Quem acessa |
|---|---|---|---|---|---|---|
| `musclefit-frontend-assets` | `aws_s3_bucket.musclefit_frontend_assets` | Enabled | AES256 | todos os 4 flags `true` | `AllowCloudFrontOACRead`: `s3:GetObject` em `/*` para `cloudfront.amazonaws.com` com `AWS:SourceArn` da distribuição | CloudFront (leitura); role `musclefit-gha-deploy` (`aws s3 sync --delete` no CI) |
| `musclefit-uploads` | `aws_s3_bucket.musclefit_uploads` | Enabled | AES256 | todos `true` | `AllowCloudFrontOACReadPhotos`: `s3:GetObject` em `equipment/*` e `gyms/*` (definida em `cloudfront_uploads.tf`) | CloudFront (fotos públicas); `musclefit-api-core-exec` e `musclefit-reports-exec` (`Get/Put/DeleteObject`, `ListBucket` no bucket inteiro) |
| `musclefit-rag` | `aws_s3_bucket.musclefit_rag` | Enabled | AES256 | todos `true` | nenhuma bucket policy | As 6 roles de execução Lambda (`s3:ListBucket` no bucket, `s3:GetObject` em `/*`); Terraform publica os objetos |
| `musclefit-tfstate-930413537709` | não gerenciado | Enabled (plano) | SSE-S3 (plano) | bloqueado (plano) | não verificado | Terraform (backend), role `musclefit-gha-deploy` |

`s3_rag.tf` declara `aws_s3_object.musclefit_rag_markdown` com `for_each = local.rag_markdown_files` (`fileset("${path.module}/../api/privado/rag/markdown", "**/*.md")`, submódulo privado `muscle_fit_privado` desde o M103 P0, com `precondition` no bucket que falha o plan se o fileset vier vazio), `key = "markdown/${each.value}"`, `etag = filemd5(...)`, `content_type = "text/markdown; charset=utf-8"`. Arquivos publicados (24): `ABDOMEN.md`, `AGACHAMENTO.md`, `ANTEBRACO.md`, `BICEPS.md`, `BIOIMPEDANCIA.md`, `CARDIO.md`, `COSTAS.md`, `HABILIDADES_TREINO.md`, `HIPERTROFIA.md`, `PANTURRILHA.md`, `PEITO.md`, `PERNA.md`, `POSTERIOR_COXA.md`, `PROGRESSAO.md`, `TABELA_EXERCICIOS.md`, `TECNICAS_AVANCADAS.md`, `TRAPEZIO.md`, `TRICEPS.md`, `TIPOS_TREINO/1. Full Body/FULLBODY.md`, `TIPOS_TREINO/2. ABC/COMO_MONTAR_ABC.md`, `TIPOS_TREINO/2. ABC/TREINO_A.md`, `TIPOS_TREINO/2. ABC/TREINO_ABC_PERFEITO.md`, `TIPOS_TREINO/2. ABC/TREINO_B.md`, `TIPOS_TREINO/2. ABC/TREINO_C.md`.

`s3_uploads.tf` também declara duas inline policies na role `musclefit-api-core-exec`: `musclefit-api-core-uploads` (S3 no bucket `musclefit-uploads`) e `musclefit-api-core-cloudfront-invalidation` (`cloudfront:CreateInvalidation` na distribuição). Nenhum bucket tem lifecycle rule, CORS, logging ou replication declarados. **Confirmado em produção**: `list-buckets` retorna exatamente os 4 buckets acima.

#### 2.12 Lambdas

##### Tabela-resumo

| Função | Arquivo TF | Imagem ECR : tag | Dockerfile | Entrypoint efetivo | Memória | Timeout | Role | Invocação |
|---|---|---|---|---|---|---|---|---|
| `musclefit-api-core` | `lambda_api_core.tf` | `musclefit-api-core:latest` | `api/Dockerfile` | CMD `uvicorn app.main:app --host 0.0.0.0 --port 8000` + Lambda Web Adapter | 1024 MB | 29 s | `musclefit-api-core-exec` | API Gateway (`{proxy+}` e raiz) |
| `musclefit-engines` | `lambda_engines.tf` | `musclefit-engines:latest` | `api/Dockerfile.engines` | CMD `uvicorn app.main_engines:app ...` + Lambda Web Adapter | 1024 MB | 29 s | `musclefit-engines-exec` | API Gateway (36 paths); permissão IAM para invocação direta por `api-core`/`ai-generators` (sem call site) |
| `musclefit-plan-generator` | `lambda_ai_generators.tf` | `musclefit-ai-generators:latest` | `api/Dockerfile.ai` | `image_config.command = ["app.lambda_plan_generator.handler"]` via `awslambdaric` | 1024 MB | 120 s | `musclefit-ai-generators-exec` | `Invoke(Event)` por `api-core` |
| `musclefit-cardio-agent` | `lambda_ai_generators.tf` | `musclefit-ai-generators:latest` | `api/Dockerfile.ai` | `image_config.command = ["app.lambda_cardio_agent.handler"]` | 1024 MB | 120 s | `musclefit-ai-generators-exec` | `Invoke(Event)` por `api-core` |
| `musclefit-workout-ai` | `lambda_workout_ai.tf` | `musclefit-ai-generators:latest` | `api/Dockerfile.ai` | `image_config.command = ["app.lambda_workout_ai.handler"]` | 1024 MB | 60 s | `musclefit-workout-ai-exec` | `Invoke(Event)` por `api-core` |
| `musclefit-reports` | `lambda_reports.tf` | `musclefit-reports:latest` | `api/Dockerfile.reports` | CMD da imagem `["app.lambda_reports.handler"]` (sem `image_config`) | 2048 MB | 300 s | `musclefit-reports-exec` | `Invoke(Event)` por `api-core` |

Atributos comuns: `package_type = "Image"`, arquitetura `x86_64` (default; imagens buildadas com `--platform linux/amd64`), sem `vpc_config`, sem `reserved_concurrent_executions`, sem `provisioned_concurrency`, sem `ephemeral_storage` customizado (default 512 MB), sem `dead_letter_config`, sem `tracing_config` (X-Ray), sem `layers`, sem `aws_lambda_event_source_mapping`, sem `aws_lambda_function_event_invoke_config`. Cada função tem `depends_on` no seu `aws_cloudwatch_log_group` `/aws/lambda/<nome>` com `retention_in_days = 14`. **Confirmado em produção** (`list-functions`): as 6 funções existem com as memórias/timeouts acima, `PackageType Image`, `x86_64`, `LastModified 2026-09-12T17:50:xx` (todas atualizadas pelo mesmo run do pipeline). Limite da conta (`get-account-settings`): `ConcurrentExecutions = 10`, `UnreservedConcurrentExecutions = 10`.

##### Variáveis de ambiente por função (definidas no Terraform)

| Variável | api-core | engines | plan-generator | cardio-agent | workout-ai | reports | Valor |
|---|---|---|---|---|---|---|---|
| `AURORA_CLUSTER_ARN` | x | x | x | x | x | x | ARN do cluster |
| `AURORA_SECRET_ARN` | x | x | x | x | x | x | `master_user_secret[0].secret_arn` |
| `AURORA_DATABASE` | x | x | x | x | x | x | `musclefit` |
| `API_TOKEN_SECRET_ARN` | x | x | — | — | x | x | ARN de `musclefit/api-token` (fora de plan-generator/cardio-agent desde o SEG-04) |
| `OPENAI_API_KEY_SECRET_ARN` | x | — | x | x | x | x | ARN de `musclefit/openai-api-key` |
| `SECRET_ENCRYPTION_KEY_SECRET_ARN` | x | — | x | x | x | x | ARN de `musclefit/secret-encryption-key` |
| `OPENAI_MODEL_PLAN` | x | — | x | x | — | — | `gpt-5.4-mini` |
| `OPENAI_MODEL_CARDIO` | x | — | x | x | — | — | `gpt-5.4-mini` |
| `OPENAI_SET_EVAL_MODEL` | — | — | — | — | x | — | `gpt-5.4-mini` |
| `OPENAI_EXERCISE_SUMMARY_MODEL` | — | — | — | — | x | — | `gpt-5.4-mini` |
| `OPENAI_WORKOUT_SUMMARY_MODEL` | — | — | — | — | x | — | `gpt-5.4-mini` |
| `ASYNC_JOBS_TABLE` | x | — | x | x | x | x | `musclefit-async-jobs` |
| `PLAN_GENERATOR_FUNCTION_NAME` | x | — | — | — | — | — | `musclefit-plan-generator` |
| `CARDIO_AGENT_FUNCTION_NAME` | x | — | — | — | — | — | `musclefit-cardio-agent` |
| `WORKOUT_AI_FUNCTION_NAME` | x | — | — | — | — | — | `musclefit-workout-ai` |
| `REPORTS_FUNCTION_NAME` | x | — | — | — | — | — | `musclefit-reports` |
| `EQUIPMENT_PHOTOS_BUCKET` | x | — | — | — | — | x | `musclefit-uploads` |
| `CLOUDFRONT_DISTRIBUTION_ID` | x | — | — | — | — | — | `EOA1PMADLM7RO` |
| `RAG_S3_BUCKET` | x | x | x | x | x | x | `musclefit-rag` |
| `RAG_S3_PREFIX` | x | x | x | x | x | x | `markdown/` |
| `RAG_MARKDOWN_DIR` | x | x | x | x | x | x | `/tmp/rag` |
| `SESSION_COOKIE_DOMAIN` | x | x | — | — | — | — | `.musclefit.com.br` |
| `CORS_ALLOWED_ORIGINS` | x | x | — | — | — | — | `https://app.musclefit.com.br,https://zepp-os.zepp.com` |

`plan-generator` e `cardio-agent` compartilham `local.ai_generators_env`. Desde o SEG-04 (2026-10-09) esse local não tem `INTERNAL_API_BASE_URL` nem `API_TOKEN_SECRET_ARN`: a geração de ficha lê academias e catálogo direto do banco (`gyms.list_gym_items`, `exercise_mapping_catalog.query_mapping_catalog`).

##### Roles de execução e policies

Todas as roles têm `assume_role_policy` para `lambda.amazonaws.com` e o managed policy `arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole` anexado.

| Role | Inline policy | Conteúdo |
|---|---|---|
| `musclefit-api-core-exec` | `musclefit-api-core-data-api` | `rds-data:ExecuteStatement/BatchExecuteStatement/BeginTransaction/CommitTransaction/RollbackTransaction` no cluster; `secretsmanager:GetSecretValue` nos 4 secrets (master, openai, api-token, encryption-key) |
| | `musclefit-api-core-rag` | `s3:ListBucket` em `musclefit-rag`, `s3:GetObject` em `musclefit-rag/*` |
| | `musclefit-api-core-uploads` | `s3:GetObject/PutObject/DeleteObject/ListBucket` em `musclefit-uploads` e `/*` |
| | `musclefit-api-core-cloudfront-invalidation` | `cloudfront:CreateInvalidation` na distribuição |
| | `musclefit-api-core-async-jobs` | `lambda:InvokeFunction` em `plan-generator` e `cardio-agent`; `dynamodb:PutItem/GetItem/UpdateItem` em `musclefit-async-jobs` |
| | `musclefit-api-core-invoke-workout-ai` | `lambda:InvokeFunction` em `musclefit-workout-ai` |
| | `musclefit-api-core-invoke-reports` | `lambda:InvokeFunction` em `musclefit-reports` |
| `musclefit-engines-exec` | `musclefit-engines-data-api` | `rds-data:*` (5 ações) no cluster; `secretsmanager:GetSecretValue` apenas no secret master e em `musclefit/api-token` |
| | `musclefit-engines-rag` | leitura de `musclefit-rag` |
| `musclefit-ai-generators-exec` (plan-generator e cardio-agent) | `musclefit-ai-generators-data-api` | `rds-data:*`; `GetSecretValue` em master, openai e encryption-key (sem api-token desde o SEG-04); `dynamodb:PutItem/UpdateItem/GetItem` em `musclefit-async-jobs` |
| | `musclefit-ai-generators-rag` | leitura de `musclefit-rag` |
| `musclefit-workout-ai-exec` | `musclefit-workout-ai-data-api` | `rds-data:*`; `GetSecretValue` nos 4 secrets; DynamoDB `PutItem/UpdateItem/GetItem` |
| | `musclefit-workout-ai-rag` | leitura de `musclefit-rag` |
| `musclefit-reports-exec` | `musclefit-reports-data-api` | `rds-data:*`; `GetSecretValue` nos 4 secrets; DynamoDB `PutItem/UpdateItem/GetItem` |
| | `musclefit-reports-rag` | leitura de `musclefit-rag` |
| | `musclefit-reports-uploads` | `s3:GetObject/PutObject/DeleteObject/ListBucket` em `musclefit-uploads` e `/*` |

Nenhuma role tem permissão `cloudfront:*` além de `api-core-exec`; nenhuma tem `bedrock:*`, `ses:*`, `sns:*` ou `sqs:*`. As Lambdas de IA e `api-core` fazem chamadas HTTPS de saída para `api.openai.com` (internet nativa, sem VPC). `lambda_engines.tf` menciona nos comentários um `aws_lambda_permission.engines_invoke_from_core`; nenhum recurso com esse nome existe no arquivo (a permissão de invocação lambda-a-lambda é concedida pelas inline policies das roles chamadoras).

#### 2.13 `iam_gha_backend_deploy.tf` (removido em 2026-10-10)

O arquivo (e os irmãos `iam_gha_eventbridge.tf`, `iam_gha_ses.tf`, `iam_gha_route53.tf`) modelava, atrás de flags `manage_gha_*` com default `false` (`count = 0`, sem estado), policies da role `musclefit-gha-deploy` que nunca foram geridas pelo Terraform. Desde a consolidação AWS-13 (aplicada em 2026-10-09) a role tem só as 5 managed `musclefit-gha-deploy-v2-*` de `infra/iam-policies/consolidacao/`, sem inline; os arquivos viraram documentação morta e saíram do repositório (plan sem mudanças). Ver `infra/iam-policies/consolidacao/README.md`.

#### 2.14 `infra/iam-policies/*.json` (políticas aplicadas manualmente à role `musclefit-gha-deploy`)

| Arquivo | Tipo na conta | Statements (resumo) |
|---|---|---|
| `musclefit-gha-deploy-fase3-engines.json` | inline (`musclefit-gha-deploy-fase3-engines`) | ECR repo manage em `repository/musclefit-engines`; IAM role lifecycle/policies em `role/musclefit-engines-exec`; `iam:PassRole` para `lambda.amazonaws.com`; `iam:Put/Get/DeleteRolePolicy` em `musclefit-api-core-exec` e `musclefit-ai-generators-exec`; `lambda:*` (Create/Delete/Get/UpdateCode/UpdateConfiguration/ListVersions/Tag/Permission) em `function:musclefit-engines`; `logs:*` em `log-group:/aws/lambda/musclefit-engines*` |
| `musclefit-gha-deploy-fase3-workout-ai.json` | não presente como inline (ver lista abaixo) | IAM lifecycle em `role/musclefit-workout-ai-exec` (inclui `iam:UpdateAssumeRolePolicy`); `PassRole`; inline policies em `musclefit-api-core-exec`; `lambda:*` em `function:musclefit-workout-ai`; `lambda:UpdateFunctionConfiguration/GetFunctionConfiguration` em `function:musclefit-api-core`; `logs:*` em `/aws/lambda/musclefit-workout-ai:*` |
| `musclefit-gha-deploy-fase4-frontend.json` | inline (`musclefit-gha-deploy-fase4-frontend`) | `s3:ListBucket` em `musclefit-frontend-assets`; `s3:PutObject/GetObject/DeleteObject` em `/*`; `cloudfront:CreateInvalidation/GetInvalidation` em `distribution/EOA1PMADLM7RO` |
| `musclefit-gha-deploy-fase5-rag.json` | managed (`musclefit-gha-deploy-fase5-rag`) | bucket manage em `musclefit-rag` (Create/Delete/Versioning/PublicAccessBlock/Encryption/Tagging/Policy + vários `Get*`); objetos `Put/Get/Delete/Tagging` em `/*`; inline policies nas 4 roles exec (`api-core`, `engines`, `ai-generators`, `workout-ai`) |
| `musclefit-gha-deploy-fase5-reports.json` | managed (`musclefit-gha-deploy-fase5-reports`) | ECR repo manage em `repository/musclefit-reports`; IAM lifecycle em `role/musclefit-reports-exec`; `PassRole`; inline policies em `musclefit-api-core-exec`; `lambda:*` em `function:musclefit-reports`; `UpdateFunctionConfiguration` em `musclefit-api-core`; `logs:*` em `/aws/lambda/musclefit-reports:*` |

**Confirmado em produção** — policies da role `musclefit-gha-deploy`: inline `musclefit-gha-deploy-fase0`, `-fase1`, `-fase3`, `-fase3-engines`, `-fase3-uploads`, `-fase4-frontend`; managed anexadas `musclefit-gha-deploy-fase5-planreadonly`, `-fase5-reports`, `-fase5-rag`. As políticas `fase0`, `fase1`, `fase3`, `fase3-uploads` e `fase5-planreadonly` não têm arquivo correspondente em `infra/iam-policies/`. O conteúdo de `fase3-workout-ai.json` não aparece como policy separada na role (não verificado em qual policy essas permissões foram incorporadas).

#### 2.15 Arquivos `tfplan-*` presentes em `infra/` (binários, não abertos)

`tfplan-cf-dynroute`, `tfplan-cf-function`, `tfplan-domain`, `tfplan-ecr`, `tfplan-ecr2`, `tfplan-fase3`, `tfplan-fase3b`, `tfplan-fase3c`, `tfplan-fix1`, `tfplan-frontend`. Todos correspondem ao padrão `tfplan*` do `.gitignore` (não versionados).

#### 2.16 Recursos fora do Terraform (bootstrap manual, Fase 0)

| Recurso | Identificador |
|---|---|
| Bucket de state | `musclefit-tfstate-930413537709` |
| Tabela de lock | `musclefit-tfstate-lock` (`LockID`, `PAY_PER_REQUEST`) |
| Provedor OIDC | `token.actions.githubusercontent.com` |
| Role de deploy | `arn:aws:iam::930413537709:role/musclefit-gha-deploy` — trust policy aceita `sub` nos formatos `repo:dougbrsp/muscle_fit:ref:refs/heads/main`, `pull_request` e `environment:production` (com e sem IDs numéricos do repo, conforme comentário em `backend-deploy.yml` e plano Fase 7) |
| Certificado ACM API | `arn:aws:acm:sa-east-1:930413537709:certificate/d0d1b680-4a31-4844-823d-0076079a4cac` (`api.musclefit.com.br`) |
| Certificado ACM frontend | `arn:aws:acm:us-east-1:930413537709:certificate/4c2d79c0-61bb-4ee5-847f-1265513edd5a` (`app.musclefit.com.br` + apex) |
| Budget | `musclefit-monthly`, US$ 30/mês, alertas 80%/100% para o e-mail do responsável pela conta |
| Cost Anomaly Detection | monitor `musclefit-cost-monitor` (dimensão `SERVICE`), assinatura `musclefit-cost-anomaly-alerts`, limiar US$ 10, frequência diária |
| Plano da conta | `PAID` (upgrade via `aws freetier upgrade-account-plan`), créditos válidos até 2027-03-03 (plano Fase 1) |
| Valores dos secrets | populados via CLI |
| Registros DNS | Nenhum fora do Terraform desde 2026-10-03: zona no Route 53 via `infra/route53.tf` (M44). O Registro.br guarda só a delegação (NS) do domínio |

Itens do plano de migração que não existem no Terraform nem na conta (segundo leitura e inventário): Lambda Authorizer `musclefit-authorizer`, WAF `musclefit-waf`, Lambda `musclefit-intervals-sync`, EventBridge Scheduler, CloudWatch Dashboard `musclefit-overview`, X-Ray, secret `musclefit/intervals-api-key`, `musclefit/aurora-credentials`.

---

### 3. CI/CD (`.github/workflows/`)

Todos os workflows usam `aws-actions/configure-aws-credentials@v4` com `role-to-assume: arn:aws:iam::930413537709:role/musclefit-gha-deploy`, `aws-region: sa-east-1`, `permissions: id-token: write, contents: read`. Não há chaves de acesso estáticas.

#### 3.0 Workflows de PR (CI) — o que roda e o rótulo `ci-completo`

Desde 2026-10-10 (economia de GitHub Actions): `ci-develop.yml` e `ci-docker-images.yml` disparam em PR para `develop` **e** para `main` (`types: opened, synchronize, reopened, labeled`) e começam por um job `changes` que lê o diff do PR.

- PR para `develop`: só a área tocada (backend → `lint backend` + `backend unit`; frontend → `lint frontend` + `frontend build`; imagens Lambda só se mudar `api/Dockerfile*`, `api/requirements*`, `.dockerignore`, `infra/lambda_*.tf` ou `.github/scripts/*`). Integração com MySQL e smoke compose + LocalStack não rodam.
- Rótulo `ci-completo` no PR para `develop`: suíte inteira; adicionar o rótulo dispara a rodada.
- PR para `main` (promoção): suíte inteira dos dois workflows, sem filtro.
- Job pulado = *skipping*. Sem job agregador (sem branch protection no plano Free); o critério de merge avalia todos os checks (`pass` ou `skipping`).
- `concurrency` com `cancel-in-progress: true` em todos os workflows de PR; `deploy-producao.yml` e `db-migrations.yml` mantêm `backend-deploy` sem cancelamento.
- Detalhes e tabela por job: `docs/local-dev.md`, "O que o CI roda em cada PR".

#### 3.1 `terraform.yml` — "Terraform (infra)"

| Item | Valor |
|---|---|
| Gatilhos | `pull_request` com paths `infra/**` ou o próprio workflow; `push` em `main` com os mesmos paths |
| Permissões extras | `pull-requests: write` |
| Job `plan` | `if: github.event_name == 'pull_request'`; steps: checkout → credenciais OIDC → `hashicorp/setup-terraform@v3` (`~> 1.7`) → `terraform fmt -check -recursive` → `terraform init` → `terraform validate` → `terraform plan -no-color -out=tfplan`. Não há step que comente o plan na PR |
| Job `apply` | `if: push && ref == refs/heads/main`; `environment: production`; steps: checkout → OIDC → setup-terraform → `terraform init` → `terraform apply -auto-approve` |
| Working dir | `infra` |

#### 3.2 `frontend-deploy.yml` — "Frontend Deploy (S3 + CloudFront)"

| Item | Valor |
|---|---|
| Gatilhos | `push` em `main` com paths `frontend/**` ou o workflow; `workflow_dispatch` |
| Job `deploy` | sem `environment`; steps: checkout → `actions/setup-node@v4` (Node 20, cache npm em `frontend/package-lock.json`) → `npm ci` → `npm run build` (`next build`, `output: 'export'` gera `frontend/out/`; sem `NEXT_PUBLIC_API_BASE_URL`, usa o default `https://api.musclefit.com.br`) → OIDC → `aws s3 sync frontend/out/ s3://musclefit-frontend-assets --delete` → `aws cloudfront create-invalidation --distribution-id EOA1PMADLM7RO --paths "/*"` |

#### 3.3 `backend-deploy.yml` — "Backend Deploy (ECR + Lambda)"

| Item | Valor |
|---|---|
| Gatilhos | `push` em `main` com paths `api/**` ou o workflow; `workflow_dispatch` |
| `concurrency` | `group: backend-deploy`, `cancel-in-progress: false` |
| `env` | `AWS_REGION=sa-east-1`, `AWS_ACCOUNT_ID=930413537709`, `ECR_REGISTRY=930413537709.dkr.ecr.sa-east-1.amazonaws.com`, `IMAGE_TAG=latest` |
| Job `build` | matriz (`fail-fast: false`) com 4 entradas: `Dockerfile`→`musclefit-api-core`, `Dockerfile.engines`→`musclefit-engines`, `Dockerfile.ai`→`musclefit-ai-generators`, `Dockerfile.reports`→`musclefit-reports`. Steps: checkout → OIDC → `aws ecr get-login-password \| docker login` → `docker build --platform linux/amd64 --provenance=false --sbom=false -f <Dockerfile> -t $ECR_REGISTRY/<repo>:latest .` (working dir `api`) → `docker push` |
| Job `deploy` | `needs: build`; sem `environment`. Step único: para cada par função:repositório (`musclefit-api-core:musclefit-api-core`, `musclefit-engines:musclefit-engines`, `musclefit-plan-generator:musclefit-ai-generators`, `musclefit-cardio-agent:musclefit-ai-generators`, `musclefit-workout-ai:musclefit-ai-generators`, `musclefit-reports:musclefit-reports`) executa `aws lambda update-function-code --function-name <fn> --image-uri $ECR_REGISTRY/<repo>:latest --publish`; em seguida, para cada função, `aws lambda wait function-updated` e `aws lambda get-function-configuration --query '[FunctionName,LastUpdateStatus,State]'` |
| Ordem | as 4 imagens são sempre buildadas e publicadas (sem build seletivo); os 6 `update-function-code` só começam após as 4 imagens estarem no ECR |
| O que não toca | `image_config`, memória, timeout, env vars (responsabilidade do Terraform); `terraform apply` não é executado por este workflow |

Sequência de deploy completa de uma mudança que altere `api/**` e `infra/**` no mesmo push: `terraform.yml` (apply, com gate `production`) e `backend-deploy.yml` rodam em paralelo, sem dependência declarada entre eles.

---

### 4. Imagens de container (`api/`)

#### 4.1 Dependências Python (`api/requirements*.in` → locks com hashes)

- `api/requirements.in`: dependências **diretas** de produção, editadas à mão (fastapi, uvicorn[standard], pydantic, boto3, PyMySQL, cryptography, python-multipart, Pillow, bcrypt, tzdata).
- `api/requirements.txt`: lock compilado por pip-tools com **todas** as transitivas e hashes (M47); instalado com `pip install --require-hashes` por `Dockerfile`, `Dockerfile.engines` e os `.dev`.
- `api/requirements-ai.txt`: o mesmo lock + `awslambdaric==4.0.4` (M49), usado só por `Dockerfile.ai`.
- `api/requirements-dev.txt`: o mesmo lock + pytest/httpx, usado pelo CI.
- Recompilar: `sh api/scripts/lock-requirements.sh` (roda dentro da imagem base das Lambdas, Linux/Python 3.12). Imagem base fixada por digest em todos os Dockerfiles (M48); Dependabot semanal em `.github/dependabot.yml`.

#### 4.2 Dockerfiles

| Aspecto | `Dockerfile` (api-core) | `Dockerfile.engines` | `Dockerfile.ai` (plan-generator, cardio-agent, workout-ai) | `Dockerfile.reports` |
|---|---|---|---|---|
| Base | `public.ecr.aws/docker/library/python:3.12-slim@sha256:…` (digest, M48; espelho ECR Public, AWS-12) | idem | idem | `python:3.12-slim` |
| ENV | `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`, `PORT=8000`, `AWS_LWA_READINESS_CHECK_PATH=/health` | idem | `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1` | idem ao `.ai` |
| `WORKDIR` | `/app` | `/app` | `/var/task` | `/var/task` |
| Pacotes apt | `libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info fonts-dejavu-core` | `libffi-dev` | nenhum | mesma lista do `Dockerfile` |
| Lambda Web Adapter | `COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter` | idem | não | não |
| pip | `pip install --no-cache-dir --require-hashes -r requirements.txt` | idem | `... --require-hashes -r requirements-ai.txt` | `... -r requirements.txt awslambdaric` |
| Código | `COPY app /app/app` | `COPY app /app/app` | `COPY app /var/task/app` | `COPY app /var/task/app` |
| `EXPOSE` | 8000 | 8000 | — | — |
| `ENTRYPOINT` | (default da imagem) | (default) | `["python", "-m", "awslambdaric"]` | `["python", "-m", "awslambdaric"]` |
| `CMD` | `["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]` | `["uvicorn", "app.main_engines:app", "--host", "0.0.0.0", "--port", "8000"]` | `["app.lambda_plan_generator.handler"]` (sobrescrito por `image_config.command` no Terraform) | `["app.lambda_reports.handler"]` |
| Modo Lambda | HTTP via Web Adapter (readiness em `/health`) | HTTP via Web Adapter | handler `awslambdaric` (Event) | handler `awslambdaric` (Event) |

O diretório `api/app/` inteiro é copiado nas 4 imagens (inclusive `app/intelligence/*`, `app/templates/report.html`). Não há `.dockerignore` em `api/` (não encontrado na listagem).

---

### 5. Banco de dados Aurora — schema

#### 5.1 Fontes de schema encontradas

| Fonte | Natureza | Cobertura |
|---|---|---|
| Aurora em produção (`SHOW TABLES` + `SHOW CREATE TABLE`/`SHOW CREATE VIEW` via RDS Data API, 2026-09-12) | **Autoritativa** — DDL completo | 46 tabelas + 1 view |
| Tabela `schema_migrations` (lida em produção) | Histórico de 42 migrações aplicadas (v1 2026-07-07 a v42 2026-09-10) | — |
| `api/app/*.py` (`_ensure_schema`/`_ensure_*_columns`) | `CREATE TABLE IF NOT EXISTS` e `ALTER TABLE` aditivos executados em runtime para `cardio_recommendations`, `devices`, `manual_effort_metrics`, `reports`, `sleep_metrics`, `intervals_activities`, `intervals_wellness`, e colunas de `activities`, `exercises`, `exercise_sets`, `workout_plans`, `workout_plan_exercises` | Parcial |
| `tools/onprem-sync/backups/<ts>/restore.sql` e `data/*.json` | Listas de colunas por tabela (sem tipos) das 41 tabelas no escopo do sync | Parcial (não versionado) |
| `api/app/models.py` | Modelos Pydantic de entrada (não descrevem o schema) | — |
| `docs/aws-migration-plan.md` | Registra que o schema foi carregado por `mysqldump --no-data --routines --triggers` do container `fitness-mysql` com `DEFINER=` removido; a view foi recriada manualmente | — |

Não existe arquivo `.sql` de schema, diretório de migrations nem ferramenta de migração versionada em `C:\muscle_fit`. O `001_schema.sql` e `mysql/init/*.sql` referenciados em comentários pertencem ao repositório on-premise. A migração v42 (`users.is_advanced_user`) foi aplicada manualmente no Aurora (plano, Fase 7).

#### 5.2 Características globais

- Engine `InnoDB`, charset `utf8mb4`, collation `utf8mb4_0900_ai_ci`, exceto `intervals_activities`, `intervals_wellness` e `exercise_mappings_backup_20260714` (`utf8mb4_unicode_ci`).
- View `vw_muscle_group_equipment_exercises` com `DEFINER=musclefit_admin@%`, `SQL SECURITY DEFINER`.
- Estimativas de `TABLE_ROWS` (information_schema, 2026-09-12): `activities` 56, `exercise_sets` 1395, `exercises` 411, `gym_equipment` 408, `exercise_aliases` 411, `intervals_activities` 243, `workout_plan_exercises` 243, `user_sessions` 50, `users` 3; `activity_hr_zones`, `daily_biometrics`, `exercise_mappings`, `muscle_recovery_coefficients`, `weekly_summaries` com 0.
- `schema_migrations` (42 linhas) — últimas entradas: v37 índice `idx_activities_user_date`; v38 `workout_plan_templates.volume_mode` + `slot_items.item_kind=equipment`/`equipment_id`; v39–v41 colunas de técnica avançada/aquecimento/série de ajuste/pico de contração; v42 `users.is_advanced_user`.

#### 5.3 Tabelas (DDL lido em produção)

Convenção: `PK` chave primária; `UK` chave única; `IDX` índice; `FK` chave estrangeira. Tipos `timestamp` com `DEFAULT CURRENT_TIMESTAMP` são indicados como `ts-default`; com `ON UPDATE CURRENT_TIMESTAMP` como `ts-update`.

##### `users` (AUTO_INCREMENT=5)

| Coluna | Tipo | Null | Default/Extra |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| username | varchar(80) | não | |
| password_hash | varchar(255) | não | bcrypt |
| full_name | varchar(160) | sim | |
| email | varchar(160) | sim | |
| photo_path | varchar(255) | sim | S3 key `users/<id>.<ext>` |
| birth_date | date | sim | |
| sex | varchar(20) | sim | |
| role | enum('admin','regular','personal_trainer') | não | 'regular' |
| is_active | tinyint(1) | não | 1 |
| is_advanced_user | tinyint(1) | não | 0 |
| intervals_athlete_id | varchar(80) | sim | |
| intervals_api_key_encrypted | varchar(255) | sim | token Fernet |
| failed_login_attempts | int | não | 0 |
| locked_until | timestamp | sim | |
| must_change_password | tinyint(1) | não | 0 |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_users_username(username)`.

##### `user_sessions` (AUTO_INCREMENT=56)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| user_id | bigint | não | |
| token_hash | char(64) | não | SHA-256 do token |
| created_at | timestamp | não | ts-default |
| expires_at | timestamp | não | |
| last_seen_at | timestamp | sim | |
| revoked_at | timestamp | sim | |

PK `id`; UK `uq_user_sessions_token_hash(token_hash)`; IDX `idx_user_sessions_user(user_id)`; FK `fk_user_sessions_user` → `users(id)` ON DELETE CASCADE.

##### `devices` (AUTO_INCREMENT=10)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| user_id | bigint | não | |
| device_name | varchar(160) | não | |
| platform | varchar(80) | não | 'zepp' |
| token_hash | char(64) | não | |
| paired_at | timestamp | não | ts-default |
| last_seen_at | timestamp | sim | |
| revoked_at | timestamp | sim | |
| is_active | tinyint(1) | não | 1 |

PK `id`; UK `uq_devices_token_hash(token_hash)`; IDX `idx_devices_active(is_active)`. Sem FK para `users` no DDL.

##### `sources` (AUTO_INCREMENT=122)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| name | varchar(120) | não | |
| type | varchar(60) | não | |
| description | text | sim | |
| created_at | timestamp | não | ts-default |

PK `id`; UK `name(name)`.

##### `imports` (AUTO_INCREMENT=103)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| source_id | bigint | sim | |
| import_type | varchar(80) | não | |
| external_id | varchar(255) | sim | |
| status | varchar(40) | não | 'success' |
| payload_hash | char(64) | sim | |
| raw_payload | json | sim | |
| error_message | text | sim | |
| imported_at | timestamp | não | ts-default |

PK `id`; UK `uq_import_external(source_id, external_id, import_type)`; IDX `idx_imports_imported_at`; FK `fk_imports_sources` → `sources(id)`.

##### `activities` (AUTO_INCREMENT=218)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| user_id | bigint | não | |
| source_id | bigint | sim | |
| gym_id | bigint | sim | |
| training_type_id | bigint | sim | |
| import_id | bigint | sim | |
| external_id | varchar(255) | sim | |
| local_date | date | não | |
| started_at | datetime | sim | |
| ended_at | datetime | sim | |
| activity_type | varchar(60) | não | 'musculacao' |
| name | varchar(255) | não | |
| description | text | sim | |
| duration_seconds | int | sim | |
| calories_kcal | decimal(10,2) | sim | |
| avg_hr_bpm | decimal(6,2) | sim | |
| max_hr_bpm | decimal(6,2) | sim | |
| avg_rpe | decimal(4,2) | sim | |
| notes | text | sim | |
| conducted_by | varchar(60) | sim | |
| workout_plan_id | bigint | sim | |
| session_status | varchar(40) | sim | |
| raw_payload | json | sim | |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |
| ai_summary | text | sim | |
| ai_summary_generated_at | timestamp | sim | |

PK `id`; UK `uq_activity_external(source_id, external_id)`; IDX `idx_activities_date(local_date)`, `idx_activities_type(activity_type)`, `fk_activities_imports(import_id)`, `idx_activities_gym_id`, `idx_activities_session_status`, `idx_activities_workout_plan_id`, `idx_activities_user(user_id)`, `idx_activities_training_type_id`, `idx_activities_user_date(user_id, local_date)`; FKs: `gym_id`→`gyms(id)` SET NULL; `import_id`→`imports(id)`; `source_id`→`sources(id)`; `training_type_id`→`training_types(id)` SET NULL; `user_id`→`users(id)` RESTRICT; `workout_plan_id`→`workout_plans(id)` SET NULL.

##### `activity_hr_zones` (sem AUTO_INCREMENT registrado, 0 linhas)

| Coluna | Tipo | Null |
|---|---|---|
| id | bigint | não (AUTO_INCREMENT) |
| activity_id | bigint | não |
| zone_number | int | não |
| zone_name | varchar(80) | sim |
| min_bpm | int | sim |
| max_bpm | int | sim |
| duration_seconds | int | sim |
| percent_time | decimal(6,2) | sim |

PK `id`; UK `uq_activity_zone(activity_id, zone_number)`; FK `fk_hr_zones_activity` → `activities(id)` CASCADE.

##### `exercises` (AUTO_INCREMENT=524)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| activity_id | bigint | não | |
| sequence | int | não | |
| original_name | varchar(255) | não | |
| sets_count | int | não | 0 |
| reps_total | int | sim | |
| volume_kg | decimal(12,2) | sim | |
| min_weight_kg | decimal(8,2) | sim | |
| best_weight_kg | decimal(8,2) | sim | |
| avg_rpe | decimal(4,2) | sim | |
| notes | text | sim | |
| muscle_pain | tinyint | sim | |
| joint_pain | tinyint(1) | sim | |
| source_row_hash | char(64) | sim | |
| raw_sets_text | varchar(120) | sim | |
| raw_reps_text | varchar(255) | sim | |
| raw_load_text | varchar(255) | sim | |
| raw_rpe_text | varchar(255) | sim | |
| created_at | timestamp | não | ts-default |
| mapping_id | bigint unsigned | sim | |
| workout_plan_exercise_id | bigint | sim | |
| equipment_id | bigint | sim | |

PK `id`; UK `uq_exercise_source_hash(source_row_hash)`; IDX `idx_exercises_activity_sequence(activity_id, sequence)`, `idx_exercises_mapping_id`, `idx_exercises_equipment_id`, `idx_exercises_workout_plan_exercise_id`; FKs: `activity_id`→`activities(id)` CASCADE; `equipment_id`→`gym_equipment(id)` SET NULL; `mapping_id`→`exercise_catalog(id)` SET NULL; `workout_plan_exercise_id`→`workout_plan_exercises(id)` SET NULL.

##### `exercise_sets` (AUTO_INCREMENT=1910)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| exercise_id | bigint | não | |
| set_number | int | não | |
| set_type | varchar(40) | não | 'working' |
| reps | int | sim | |
| weight_kg | decimal(8,2) | sim | |
| rpe | decimal(4,2) | sim | |
| rest_seconds | int | sim | |
| hr_avg_bpm | decimal(6,2) | sim | |
| hr_max_bpm | decimal(6,2) | sim | |
| muscle_pain | tinyint | sim | |
| joint_pain | tinyint(1) | sim | |
| notes | text | sim | |
| raw_reps_text | varchar(255) | sim | |
| raw_load_text | varchar(255) | sim | |
| raw_rpe_text | varchar(255) | sim | |

PK `id`; UK `uq_exercise_set_number(exercise_id, set_number)`; IDX `idx_sets_rpe(rpe)`; FK `fk_sets_exercise` → `exercises(id)` CASCADE.

##### `exercise_catalog` (AUTO_INCREMENT=123)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint unsigned | não | AUTO_INCREMENT |
| name | varchar(255) | não | |
| body_position | varchar(120) | sim | |
| movement | varchar(120) | sim | |
| trajectory | varchar(120) | sim | |
| biomechanical_pattern | varchar(160) | sim | |
| primary_joint | varchar(160) | sim | |
| resistance_vector | varchar(120) | sim | |
| keywords | text | sim | |
| description | text | sim | |
| is_active | tinyint(1) | não | 1 |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |
| is_unilateral | tinyint(1) | não | 0 |
| is_compound | tinyint(1) | sim | |
| execution_description, execution_description_model, execution_description_prompt_version, execution_description_generated_at, execution_description_reviewed_by, execution_description_reviewed_at | text / varchar(80) / varchar(40) / datetime / bigint / datetime | sim | — migração 039 (descrição de execução, M4) |
| execution_description_generation_started_at | datetime | sim | — migração 042 (reserva da geração) |
| card_generation_id | bigint | sim | — migração 064: geração PUBLICADA vigente (ref. lógica a `exercise_card_generations.id`, sem FK) |
| card_version | int | sim | — migração 064: versão vigente do card ilustrado; monta a URL `exercise-cards/{id}/v{n}/…`. Só é gravada DEPOIS dos 3 uploads (NULL = sem card) |
| card_published_at | datetime | sim | — migração 064 |

PK `id`; UK `uq_exercise_catalog_name(name)`. As linhas com "— migração NNN" vieram depois do DDL lido em 2026-09-12, por migração versionada (`api/db/migrations/`); são todas anuláveis, no fim da tabela (`ALGORITHM=INSTANT`).

##### `exercise_card_generations` (migração 064 — cards ilustrados)

Histórico completo das gerações de card por IA (máquina de estados) — a versão ativa é apontada por `exercise_catalog.card_generation_id/card_version/card_published_at` (ponteiro único, sem 2ª tabela de "imagens publicadas").

| Coluna | Tipo | Null | Default / nota |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| exercise_id | bigint unsigned | não | ref. LÓGICA a `exercise_catalog(id)` — **sem FK** (a 028 recusa FK nova para o catálogo) |
| card_type | varchar(40) | não | 'exercise_execution' |
| status | varchar(16) | não | `PENDING`, `GENERATING`, `GENERATED`, `APPROVING`, `PUBLISHED`, `SUPERSEDED`, `REJECTED`, `FAILED` (VARCHAR, não ENUM) |
| version | int | sim | só na publicação (v1, v2, …) |
| parent_generation_id | bigint | sim | geração que esta regenerou |
| idempotency_key | char(36) | não | UUID do cliente (replay do POST) |
| job_id | varchar(36) | sim | job do DynamoDB (diagnóstico) |
| provider / model / quality / size | varchar(20) 'openai' / varchar(60) / varchar(10) / varchar(12) | não / sim / sim / sim | o que foi pedido à OpenAI |
| prompt_version / text_level | varchar(40) / varchar(10) 'minimal' | não | |
| prompt_text / request_instruction / reject_reason | text | sim | |
| preview_s3_key / card_s3_key / card_png_s3_key / thumb_s3_key | varchar(255) | sim | rascunho e os 3 arquivos publicados |
| width / height / bytes / tokens_in / tokens_out | int | sim | |
| cost_usd | decimal(8,4) | sim | |
| error_code / error_message | varchar(40) / text | sim | |
| created_by / approved_by | bigint | não / sim | ids de `users`, **sem FK** (o card pertence ao catálogo) |
| created_at / updated_at | datetime | não | CURRENT_TIMESTAMP |
| started_at / generated_at / approved_at / published_at / rejected_at / failed_at | datetime | sim | |

PK `id`; UK `uq_ecg_exercise_version(exercise_id, version)` (várias versões NULL convivem), `uq_ecg_exercise_idem(exercise_id, idempotency_key)`; IDX `idx_ecg_exercise_status(exercise_id, status)`, `idx_ecg_created_by_created(created_by, created_at)`, `idx_ecg_status_started(status, started_at)`. InnoDB, `utf8mb4_0900_ai_ci`.

##### `exercise_zepp_mappings` (AUTO_INCREMENT=108)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| exercise_name | varchar(255) | não | |
| amazfit_name | varchar(255) | não | |
| equipment | varchar(120) | sim | |
| body_position | varchar(120) | sim | |
| movement | varchar(120) | sim | |
| trajectory | varchar(120) | sim | |
| biomechanical_pattern | varchar(160) | sim | |
| primary_joint | varchar(160) | sim | |
| resistance_vector | varchar(120) | sim | |
| primary_group_id | bigint | sim | |
| secondary_group_id | bigint | sim | |
| secondary_participation | decimal(3,2) | sim | |
| keywords | text | sim | |
| description | text | sim | |
| mapping_status | varchar(40) | não | 'confirmed' |
| source | varchar(160) | não | 'manual' |
| is_active | tinyint(1) | não | 1 |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_exercise_zepp_mappings_exercise_name(exercise_name)`; IDX `idx_..._amazfit_name`, `idx_..._status`, `idx_..._primary_group_id`, `idx_..._secondary_group_id`; FULLTEXT `ft_exercise_zepp_mappings_search(exercise_name, amazfit_name, keywords, description)`; FKs `primary_group_id`/`secondary_group_id` → `muscle_groups(id)`.

##### `exercise_aliases` (AUTO_INCREMENT=567)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| exercise_id | bigint unsigned | não | |
| alias | varchar(255) | não | |
| source | varchar(160) | sim | |
| created_at | timestamp | não | ts-default |

PK `id`; UK `uq_exercise_aliases_exercise_alias(exercise_id, alias)`; IDX `idx_exercise_aliases_exercise`; FK → `exercise_catalog(id)` CASCADE.

##### `exercise_external_mappings` (AUTO_INCREMENT=214)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| exercise_id | bigint unsigned | não | |
| provider | varchar(30) | não | |
| external_id | varchar(255) | sim | |
| external_name | varchar(255) | não | |
| mapping_status | varchar(30) | não | 'CONFIRMED' |
| source | varchar(255) | sim | |
| notes | varchar(500) | sim | |
| is_active | tinyint(1) | não | 1 |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_eem_exercise_provider_name(exercise_id, provider, external_name)`; IDX `idx_exm_exercise_provider_active(exercise_id, provider, is_active)`, `idx_exm_provider_external_name(provider, external_name)`; FK `fk_exm_exercise` → `exercise_catalog(id)` CASCADE.

##### `exercise_legacy_id_map`

| Coluna | Tipo | Null |
|---|---|---|
| old_exercise_id | bigint unsigned | não |
| canonical_exercise_id | bigint unsigned | não |

PK `old_exercise_id`; IDX `fk_elim_canonical`; FK → `exercise_catalog(id)`.

##### `exercise_muscle_contributions` (AUTO_INCREMENT=752)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| exercise_id | bigint unsigned | não | |
| muscle_group_id | bigint | não | |
| role | varchar(20) | não | |
| stimulus_weight | decimal(3,2) | não | |
| fatigue_weight | decimal(3,2) | não | |
| is_active | tinyint(1) | não | 1 |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_emc_exercise_group(exercise_id, muscle_group_id)`; IDX `idx_emc_muscle_group`; FKs `exercise_id`→`exercise_catalog(id)` CASCADE, `muscle_group_id`→`muscle_groups(id)`.

##### `muscle_groups` (AUTO_INCREMENT=57)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| name | varchar(120) | não | |
| primary_muscles | varchar(255) | sim | |
| secondary_muscles | varchar(255) | sim | |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `name(name)`.

##### `muscle_group_body_map` (AUTO_INCREMENT=129)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| muscle_group_id | bigint | não | |
| slugs | varchar(255) | não | |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_muscle_group_body_map_group_id(muscle_group_id)`; FK → `muscle_groups(id)`.

##### `muscle_recovery_coefficients` (0 linhas)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| muscle_group_id | bigint | não | |
| user_id | bigint | não | |
| coefficient | decimal(3,2) | não | 1.00 |
| valid_observations | int | não | 0 |
| confidence | varchar(20) | não | 'LOW' |
| last_adjusted_at | timestamp | sim | |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `(user_id, muscle_group_id)`; IDX `idx_mrc_muscle_group_id`; FKs `muscle_group_id`→`muscle_groups(id)`, `user_id`→`users(id)` CASCADE.

##### `equipment_types` (AUTO_INCREMENT=17)

| Coluna | Tipo | Null |
|---|---|---|
| id | bigint unsigned | não (AUTO_INCREMENT) |
| code | varchar(50) | não |
| name | varchar(100) | não |
| description | varchar(255) | sim |

PK `id`; UK `code(code)`, `name(name)`.

##### `equipment` (AUTO_INCREMENT=86)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| name | varchar(160) | não | |
| notes | text | sim | |
| photo_path | varchar(500) | sim | S3 key `equipment/<id>.<ext>` |
| is_active | tinyint(1) | não | 1 |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |
| equipment_type_id | bigint unsigned | sim | |
| manufacturer | varchar(100) | sim | |
| model | varchar(150) | sim | |
| location | varchar(150) | sim | |

PK `id`; UK `uq_equipment_name(name)`; IDX `idx_equipment_type`; FK `fk_equipment_type` → `equipment_types(id)`.

##### `equipment_exercises` (AUTO_INCREMENT=378)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| equipment_id | bigint | não | |
| exercise_id | bigint unsigned | não | |
| is_preferred | tinyint(1) | não | 0 |
| is_active | tinyint(1) | não | 1 |
| notes | varchar(500) | sim | |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_equipment_exercises_equipment_exercise(equipment_id, exercise_id)`; IDX `idx_eqex_equipment_active(equipment_id, is_active)`, `idx_eqex_exercise_active(exercise_id, is_active)`; FKs `equipment_id`→`equipment(id)` CASCADE, `exercise_id`→`exercise_catalog(id)` CASCADE.

##### `equipment_exercise_mappings` (AUTO_INCREMENT=242)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| equipment_id | bigint | não | |
| mapping_id | bigint unsigned | não | |
| is_active | tinyint(1) | não | 1 |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_equipment_exercise_mappings(equipment_id, mapping_id)`; IDX `idx_eem_mapping`; FKs `equipment_id`→`equipment(id)` CASCADE, `mapping_id`→`exercise_catalog(id)` CASCADE.

##### `gyms` (AUTO_INCREMENT=47)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| owner_user_id | bigint | não | |
| name | varchar(120) | não | |
| usual_weekdays | json | sim | |
| is_recurring | tinyint(1) | não | 1 |
| notes | text | sim | |
| logo_path | varchar(500) | sim | S3 key `gyms/<id>.<ext>` |
| opens_at | time | sim | |
| closes_at | time | sim | |
| color_palette | json | sim | |
| is_active | tinyint(1) | não | 1 |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_gyms_name(name)`; IDX `idx_gyms_owner`; FK `fk_gyms_owner` → `users(id)` RESTRICT.

##### `gym_equipment_catalog` (AUTO_INCREMENT=191)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| gym_id | bigint | não | |
| equipment_id | bigint | não | |
| is_active | tinyint(1) | não | 1 |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_gym_equipment_catalog(gym_id, equipment_id)`; IDX `idx_gec_gym`, `idx_gec_equipment`; FKs `equipment_id`→`equipment(id)` CASCADE, `gym_id`→`gyms(id)` CASCADE.

##### `gym_equipment` (AUTO_INCREMENT=823) — cache materializado por `gym_equipment_sync.py`

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| gym_id | bigint | não | |
| mapping_id | bigint unsigned | não | |
| equipment_id | bigint | não | |
| is_active | tinyint(1) | não | 1 |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_gym_equipment_gym_mapping_equipment(gym_id, mapping_id, equipment_id)`; IDX `idx_gym_equipment_mapping`, `idx_gym_equipment_gym`, `idx_gym_equipment_equipment`; FKs `equipment_id`→`equipment(id)` CASCADE, `gym_id`→`gyms(id)` CASCADE, `mapping_id`→`exercise_catalog(id)` CASCADE.

##### `training_types` (AUTO_INCREMENT=6)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| name | varchar(160) | não | |
| description | text | sim | |
| rag_folder | varchar(255) | sim | |
| is_active | tinyint(1) | não | 1 |
| created_by_user_id | bigint | não | |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; IDX `idx_tt_created_by`, `idx_tt_is_active`; FK `fk_tt_created_by` → `users(id)` RESTRICT.

##### `training_type_muscle_group_targets` (AUTO_INCREMENT=301)

| Coluna | Tipo | Null |
|---|---|---|
| id | bigint | não (AUTO_INCREMENT) |
| training_type_id | bigint | não |
| muscle_group_id | bigint | não |
| min_weekly_sets | decimal(6,2) | não |
| target_weekly_sets | decimal(6,2) | não |
| max_weekly_sets | decimal(6,2) | não |

PK `id`; UK `uq_ttmgt_type_group(training_type_id, muscle_group_id)`; IDX `idx_ttmgt_muscle_group`; FKs `muscle_group_id`→`muscle_groups(id)` RESTRICT, `training_type_id`→`training_types(id)` CASCADE.

##### `workout_plan_templates` (AUTO_INCREMENT=24)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| name | varchar(160) | não | |
| description | text | sim | |
| template_type | varchar(40) | não | 'full_body' |
| training_type_id | bigint | sim | |
| volume_mode | varchar(20) | não | 'percent_budget' |
| is_active | tinyint(1) | não | 1 |
| created_by_user_id | bigint | não | |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; IDX `idx_wpt_created_by`, `idx_wpt_is_active`, `idx_wpt_training_type`; FKs `created_by_user_id`→`users(id)` RESTRICT, `training_type_id`→`training_types(id)` SET NULL.

##### `workout_plan_template_slots` (AUTO_INCREMENT=832)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| template_id | bigint | não | |
| sequence | tinyint | não | |
| name | varchar(120) | não | |
| weekday | tinyint | sim | |
| slot_type | enum('normal','descanso') | não | 'normal' |
| priority_min_pct | tinyint unsigned | sim | |
| priority_max_pct | tinyint unsigned | sim | |

PK `id`; UK `uq_wpts_template_sequence(template_id, sequence)`; FK → `workout_plan_templates(id)` CASCADE.

##### `workout_plan_template_slot_items` (AUTO_INCREMENT=4179)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| slot_id | bigint | não | |
| item_order | tinyint | não | |
| item_kind | enum('muscle_group','exercise','cardio','equipment') | não | |
| muscle_group_id | bigint | sim | |
| mapping_id | bigint unsigned | sim | |
| equipment_id | bigint | sim | |
| sets_count | int | sim | 3 |
| peak_contraction_seconds | int | sim | |
| reps_target | varchar(80) | sim | |
| rpe_target | varchar(80) | sim | |
| prioritize | tinyint(1) | não | 0 |
| priority_min_pct | tinyint unsigned | sim | |
| priority_max_pct | tinyint unsigned | sim | |
| cardio_duration_minutes | int | sim | |
| advanced_technique | varchar(20) | sim | |
| advanced_technique_applications | int | sim | |
| advanced_technique_duration_seconds | int | sim | |
| warmup_sets_count | int | sim | |
| warmup_reps | int | sim | |
| warmup_rest_seconds | int | sim | |
| feeler_set_sets_count | int | sim | |
| feeler_set_reps | int | sim | |
| feeler_set_rest_seconds | int | sim | |

PK `id`; IDX `idx_wptsi_slot`, `idx_wptsi_muscle_group`, `idx_wptsi_mapping`, `idx_wptsi_equipment`; FKs `equipment_id`→`equipment(id)` SET NULL, `mapping_id`→`exercise_catalog(id)` SET NULL, `muscle_group_id`→`muscle_groups(id)` RESTRICT, `slot_id`→`workout_plan_template_slots(id)` CASCADE.

##### `workout_plan_template_assignments` (AUTO_INCREMENT=15)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| template_id | bigint | não | |
| user_id | bigint | não | |
| assigned_by_user_id | bigint | não | |
| assigned_at | timestamp | não | ts-default |

PK `id`; UK `uq_wpta_template_user(template_id, user_id)`; IDX `idx_wpta_user`, `fk_wpta_assigned_by`; FKs `assigned_by_user_id`→`users(id)` RESTRICT, `template_id`→`workout_plan_templates(id)` CASCADE, `user_id`→`users(id)` CASCADE.

##### `workout_plans` (AUTO_INCREMENT=106)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| user_id | bigint | não | |
| plan_date | date | sim | |
| plan_time | time | sim | |
| gym_id | bigint | sim | |
| name | varchar(255) | não | |
| plan_type | varchar(80) | não | 'full_body' |
| split_day | varchar(1) | sim | |
| template_id | bigint | sim | |
| template_slot_sequence | tinyint | sim | |
| goal | text | sim | |
| status | varchar(40) | não | 'planned' |
| raw_payload | json | sim | |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; IDX `idx_workout_plans_date`, `idx_workout_plans_status`, `idx_workout_plans_gym_id`, `idx_workout_plans_user`, `fk_wp_template`, `idx_wp_user_template(user_id, template_id)`; FKs `gym_id`→`gyms(id)`, `user_id`→`users(id)` RESTRICT, `template_id`→`workout_plan_templates(id)` SET NULL.

##### `workout_plan_exercises` (AUTO_INCREMENT=848)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| plan_id | bigint | não | |
| sequence | int | não | |
| exercise_name | varchar(255) | não | |
| amazfit_name | varchar(255) | sim | |
| mapping_id | bigint unsigned | sim | |
| muscle_primary | varchar(120) | sim | |
| muscle_secondary | varchar(120) | sim | |
| equipment_name | varchar(160) | sim | |
| preferred_equipment_id | bigint | sim | |
| sets_count | int | sim | |
| reps_target | varchar(80) | sim | |
| load_target | varchar(80) | sim | |
| rpe_target | varchar(80) | sim | |
| rest_seconds | int | sim | |
| warmup | tinyint(1) | não | 0 |
| advanced_technique | varchar(20) | sim | |
| advanced_technique_applications | int | sim | |
| advanced_technique_duration_seconds | int | sim | |
| warmup_sets_count | int | sim | |
| warmup_reps | int | sim | |
| warmup_rest_seconds | int | sim | |
| feeler_set_sets_count | int | sim | |
| feeler_set_reps | int | sim | |
| feeler_set_rest_seconds | int | sim | |
| peak_contraction_seconds | int | sim | |
| substitution_options | json | sim | |
| notes | text | sim | |

PK `id`; UK `uq_plan_sequence(plan_id, sequence)`; IDX `idx_wpe_mapping`, `idx_wpe_pref_equipment`; FKs `plan_id`→`workout_plans(id)` CASCADE, `mapping_id`→`exercise_catalog(id)` SET NULL, `preferred_equipment_id`→`gym_equipment(id)` SET NULL.

##### `body_composition` (AUTO_INCREMENT=11)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| user_id | bigint | não | |
| local_date | date | não | |
| measured_at | datetime | sim | |
| weight_kg | decimal(6,2) | sim | |
| bmi | decimal(5,2) | sim | |
| body_fat_percent | decimal(5,2) | sim | |
| fat_mass_kg | decimal(6,2) | sim | |
| fat_free_mass_kg | decimal(6,2) | sim | |
| muscle_mass_kg | decimal(6,2) | sim | |
| skeletal_muscle_percent | decimal(5,2) | sim | |
| body_water_percent | decimal(5,2) | sim | |
| visceral_fat_rating | decimal(5,2) | sim | |
| bone_mass_kg | decimal(5,2) | sim | |
| bmr_kcal | decimal(8,2) | sim | |
| subcutaneous_fat_percent | decimal(5,2) | sim | |
| protein_percent | decimal(5,2) | sim | |
| metabolic_age | int | sim | |
| source | varchar(120) | sim | |
| notes | text | sim | |
| raw_payload | json | sim | |
| created_at | timestamp | não | ts-default |

PK `id`; UK `uq_body_composition_user_date_source(user_id, local_date, source)`; IDX `idx_body_composition_date`, `idx_body_composition_user`; FK → `users(id)` RESTRICT.

##### `daily_biometrics` (0 linhas)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| user_id | bigint | não | |
| local_date | date | não | |
| weight_kg | decimal(6,2) | sim | |
| resting_hr_bpm | decimal(6,2) | sim | |
| sleep_minutes | int | sim | |
| steps | int | sim | |
| calories_active_kcal | decimal(10,2) | sim | |
| calories_total_kcal | decimal(10,2) | sim | |
| notes | text | sim | |
| raw_payload | json | sim | |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `local_date(local_date)`; IDX `idx_daily_biometrics_date`, `idx_daily_biometrics_user`; FK → `users(id)` RESTRICT.

##### `sleep_metrics` (AUTO_INCREMENT=40)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| user_id | bigint | não | |
| local_date | date | não | |
| duration_hours | decimal(4,2) | sim | |
| regularity_percent | decimal(5,2) | sim | |
| deep_sleep_hours | decimal(4,2) | sim | |
| rem_sleep_hours | decimal(4,2) | sim | |
| awake_count | int | sim | |
| awake_hours | decimal(4,2) | sim | |
| notes | text | sim | |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_sleep_metrics_user_date(user_id, local_date)`. Sem FK.

##### `manual_effort_metrics` (AUTO_INCREMENT=41)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| user_id | bigint | não | |
| local_date | date | não | |
| epoc_training_load | int | sim | |
| pai_score | int | sim | |
| notes | text | sim | |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_manual_effort_metrics_user_date(user_id, local_date)`. Sem FK.

##### `intervals_activities` (AUTO_INCREMENT=14892, collation `utf8mb4_unicode_ci`)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| user_id | bigint | não | |
| external_id | varchar(191) | não | |
| activity_date | date | não | |
| start_time | datetime | sim | |
| name | varchar(255) | sim | |
| sport_type | varchar(80) | sim | |
| moving_time_seconds | int | sim | |
| elapsed_time_seconds | int | sim | |
| distance_meters | decimal(12,2) | sim | |
| calories_kcal | decimal(10,2) | sim | |
| avg_hr_bpm | decimal(6,2) | sim | |
| max_hr_bpm | decimal(6,2) | sim | |
| training_load | decimal(10,2) | sim | |
| intensity | decimal(10,2) | sim | |
| raw_json | json | sim | |
| synced_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_intervals_activities_user_external(user_id, external_id)`; IDX `idx_intervals_activities_date`, `idx_intervals_activities_sport_type`. Sem FK.

##### `intervals_wellness` (collation `utf8mb4_unicode_ci`)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| wellness_date | date | não | |
| user_id | bigint | não | |
| fitness_ctl | decimal(10,2) | sim | |
| fatigue_atl | decimal(10,2) | sim | |
| form_tsb | decimal(10,2) | sim | |
| ramp_rate | decimal(10,2) | sim | |
| weight_kg | decimal(10,2) | sim | |
| resting_hr_bpm | decimal(6,2) | sim | |
| hrv_ms | decimal(10,2) | sim | |
| sleep_seconds | int | sim | |
| raw_json | json | sim | |
| synced_at | timestamp | não | ts-default, ts-update |
| readiness_score | decimal(6,2) | sim | |
| daily_effort_load | int | sim | |

PK `(user_id, wellness_date)`. Sem FK.

##### `cardio_recommendations` (AUTO_INCREMENT=43)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| user_id | bigint | não | |
| reference_date | date | não | |
| workout_plan_id | bigint | sim | |
| generated_at | timestamp | não | ts-default |
| model | varchar(120) | sim | |
| context_snapshot | json | sim | |
| recommendation_markdown | mediumtext | não | |
| created_at | timestamp | não | ts-default |

PK `id`; IDX `idx_cardio_recommendations_date`, `idx_cardio_recommendations_plan`; FK `fk_cardio_recommendations_workout_plan` → `workout_plans(id)` SET NULL. Sem FK para `users`.

##### `reports` (AUTO_INCREMENT=20)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| user_id | bigint | não | |
| status | enum('generating','done','failed') | não | 'generating' |
| period_days | int | não | 90 |
| requested_at | timestamp | não | ts-default |
| completed_at | timestamp | sim | |
| model | varchar(120) | sim | |
| pdf_path | varchar(255) | sim | S3 key `reports/<id>.pdf` |
| error_message | text | sim | |
| context_snapshot | json | sim | |
| created_at | timestamp | não | ts-default |

PK `id`; IDX `idx_reports_user`, `idx_reports_status`. Sem FK.

##### `weekly_summaries` (0 linhas)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| week_start_date | date | não | |
| week_end_date | date | não | |
| workouts_count | int | não | 0 |
| total_sets | int | sim | |
| total_reps | int | sim | |
| total_volume_kg | decimal(14,2) | sim | |
| high_rpe_sets | int | sim | |
| summary_text | text | sim | |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `week_start_date(week_start_date)`; IDX `idx_weekly_summaries_week_start`.

##### `schema_migrations`

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| version | int | não | |
| name | varchar(255) | não | |
| applied_at | timestamp | não | ts-default |

PK `version`.

##### `exercise_mappings` (legada, 0 linhas)

| Coluna | Tipo | Null | Default |
|---|---|---|---|
| id | bigint | não | AUTO_INCREMENT |
| original_name | varchar(255) | não | |
| amazfit_name | varchar(255) | não | |
| mapped_name | varchar(255) | sim | |
| muscle_primary | varchar(120) | sim | |
| muscle_secondary | varchar(120) | sim | |
| confidence | decimal(5,2) | sim | |
| is_user_confirmed | tinyint(1) | não | 0 |
| notes | text | sim | |
| created_at | timestamp | não | ts-default |
| updated_at | timestamp | não | ts-default, ts-update |

PK `id`; UK `uq_mapping_original_amazfit(original_name, amazfit_name)`.

##### `exercise_mappings_backup_20260714` (backup ad-hoc, AUTO_INCREMENT=47, collation `utf8mb4_unicode_ci`)

Colunas: `id` bigint AI; `exercise_name` varchar(255) NN; `amazfit_name` varchar(255) NN; `equipment`, `body_position`, `movement`, `trajectory` varchar(120); `biomechanical_pattern`, `primary_joint` varchar(160); `resistance_vector`, `primary_group` varchar(120); `secondary_group` varchar(160); `primary_muscles`, `secondary_muscles` varchar(255); `keywords`, `description` text; `mapping_status` varchar(40) NN default 'confirmed'; `source` varchar(160) NN default 'manual'; `is_active` tinyint(1) NN default 1; `created_at`, `updated_at` timestamp. PK `id`; UK `uq_exercise_zepp_mappings_exercise_name(exercise_name)`; IDX `idx_..._amazfit_name`, `idx_..._primary_group`, `idx_..._status`; FULLTEXT `ft_exercise_zepp_mappings_search(exercise_name, amazfit_name, keywords, description, primary_muscles, secondary_muscles)`.

##### `exercises_backup_before_mapping_sync` (backup ad-hoc, AUTO_INCREMENT=45)

Colunas: `id` bigint AI; `activity_id` bigint NN; `sequence` int NN; `original_name` varchar(255) NN; `mapped_name` varchar(255); `muscle_primary`, `muscle_secondary` varchar(120); `sets_count` int NN default 0; `reps_total` int; `volume_kg` decimal(12,2); `min_weight_kg`, `best_weight_kg` decimal(8,2); `avg_rpe` decimal(4,2); `notes` text; `source_row_hash` char(64); `raw_sets_text` varchar(120); `raw_reps_text`, `raw_load_text`, `raw_rpe_text` varchar(255); `created_at` timestamp; `mapping_id` bigint; `canonical_exercise_name`, `canonical_amazfit_name`, `canonical_primary_group`, `canonical_secondary_group` varchar(255); `canonical_primary_muscles`, `canonical_secondary_muscles` text; `canonical_mapping_status` varchar(64); `canonical_mapping_source` varchar(255); `canonical_confidence` decimal(6,2); `canonical_resolved_at` datetime. PK `id`; UK `uq_exercise_source_hash(source_row_hash)`; IDX `idx_exercises_activity_sequence(activity_id, sequence)`, `idx_exercises_mapped_name`, `idx_exercises_mapping_id`, `idx_exercises_canonical_primary_group`. Sem FK.

##### View `vw_muscle_group_equipment_exercises`

```sql
CREATE ALGORITHM=UNDEFINED DEFINER=`musclefit_admin`@`%` SQL SECURITY DEFINER VIEW `vw_muscle_group_equipment_exercises` AS
select mg.id AS muscle_group_id, mg.name AS muscle_group_name, eq.id AS equipment_id, eq.name AS equipment_name,
       ec.id AS exercise_id, ec.name AS exercise_name, emc.role, emc.stimulus_weight, emc.fatigue_weight
from exercise_muscle_contributions emc
join muscle_groups mg on mg.id = emc.muscle_group_id
join exercise_catalog ec on ec.id = emc.exercise_id and ec.is_active = 1
join equipment_exercises eqex on eqex.exercise_id = ec.id and eqex.is_active = 1
join equipment eq on eq.id = eqex.equipment_id and eq.is_active = 1
where emc.is_active = 1
```

#### 5.4 Migrações executadas em runtime pelo backend (idempotentes)

| Módulo | Função | O que executa |
|---|---|---|
| `cardio_recommendations.py` | `_ensure_schema` | `CREATE TABLE IF NOT EXISTS cardio_recommendations` (versão inicial sem `user_id`/`workout_plan_id`); `ALTER ... ADD COLUMN workout_plan_id` (guardado por consulta a `information_schema.COLUMNS`); `ADD CONSTRAINT fk_cardio_recommendations_workout_plan` (erros "duplicate"/"already exists"/"errno: 121" ignorados); `ADD COLUMN user_id` (erro 1060 casado por texto); backfill `user_id` para o admin; `MODIFY COLUMN user_id BIGINT NOT NULL` quando não há NULL |
| `devices.py` | `_ensure_schema` | `CREATE TABLE IF NOT EXISTS devices`; `ADD COLUMN user_id`; backfill; `MODIFY ... NOT NULL` |
| `manual_effort_metrics.py` | `_ensure_schema` | `CREATE TABLE IF NOT EXISTS` (com `local_date UNIQUE`); `ADD COLUMN user_id`; backfill; `MODIFY NOT NULL`; `DROP INDEX local_date`; `ADD UNIQUE KEY uq_manual_effort_metrics_user_date (user_id, local_date)` |
| `sleep_metrics.py` | `_ensure_schema` | mesmo padrão de `manual_effort_metrics` (`uq_sleep_metrics_user_date`) |
| `reports.py` | `_ensure_schema` | `CREATE TABLE IF NOT EXISTS reports` (flag de módulo `_schema_done`) |
| `intervals_integration.py` | `_ensure_schema` | `CREATE TABLE IF NOT EXISTS intervals_activities` e `intervals_wellness` (collation `utf8mb4_unicode_ci`); `ADD COLUMN readiness_score`, `daily_effort_load`, `user_id` em ambas; backfill; `MODIFY user_id NOT NULL`; `intervals_wellness`: `DROP PRIMARY KEY, ADD PRIMARY KEY (user_id, wellness_date)`; `intervals_activities`: `DROP INDEX uq_intervals_activities_external_id`, `ADD UNIQUE KEY uq_intervals_activities_user_external (user_id, external_id)` |
| `services.py` | `_ensure_set_hr_columns` | `exercise_sets.hr_avg_bpm/hr_max_bpm` |
| `services.py` | `_ensure_workout_plans_time_column`, `_ensure_workout_plans_split_day_column` | `workout_plans.plan_time`, `workout_plans.split_day` |
| `services.py` | `_ensure_plan_link_columns` | `workout_plan_exercises.mapping_id` (FK `exercise_catalog`), `preferred_equipment_id` (FK `gym_equipment`) |
| `workout_sessions.py` | `_ensure_schema` | `activities.session_status`, `exercise_sets.hr_*`, `exercise_sets.muscle_pain/joint_pain`, `activities.workout_plan_id` (FK), `exercises.workout_plan_exercise_id` (FK) |
| `auth.py` | `ensure_admin_seed` (startup) | `INSERT INTO users (username='doug', role='admin', must_change_password=1)` se não existir; senha temporária aleatória impressa no log |

Todas as guardas usam `information_schema` ou casamento textual da mensagem de erro (`is_duplicate_column_error`: "Duplicate column name"; `is_duplicate_key_error`: "Duplicate entry"; `is_unknown_key_error`: "check that column/key exists"/"Can't DROP"), pois o Data API não expõe o código numérico do MySQL.

---

### 6. Camada de acesso a dados

#### 6.1 `api/app/db.py` — shim RDS Data API

| Elemento | Comportamento |
|---|---|
| Configuração | `_config_from_env()` lê `AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN`, `AURORA_DATABASE` (obrigatórias; `DataApiError` se ausentes) e `AWS_REGION`/`AWS_DEFAULT_REGION` (fallback `"sa-east-1"`) |
| `get_conn()` | Retorna `Connection`; o construtor cria `boto3.client("rds-data")` e chama `begin_transaction` imediatamente (toda conexão lógica tem `transactionId` desde o início) |
| `transaction()` | Context manager: `yield conn`; `commit()` no sucesso, `rollback()` em exceção, `close()` no `finally` |
| `Connection.commit()/rollback()` | `commit_transaction`/`rollback_transaction` e zera `transaction_id` |
| `Connection.close()` | Se ainda houver transação aberta, faz `rollback()` |
| `Connection.__enter__/__exit__` | `with conn:` → commit sem exceção, rollback com exceção, depois `close()` |
| `Cursor.execute(sql, params)` | `_translate_sql` → `execute_statement(resourceArn, secretArn, database, sql, includeResultMetadata=True, parameters?, transactionId?)`; popula `_rows` (lista de dict `label/name → valor`), `description` (7-tupla DBAPI com `typeName` no lugar do type_code), `lastrowid` (`generatedFields[0].longValue`), `rowcount` (`numberOfRecordsUpdated`, ou `len(rows)` se ausente). Exceções são reempacotadas em `DataApiError` com o SQL traduzido na mensagem |
| `Cursor.fetchone()/fetchall()/__iter__` | Operam sobre o buffer local |
| `Cursor.close()` | Limpa o buffer local |
| `Cursor.__exit__` | Não chama `close()` (buffer permanece após `with conn.cursor() as cur:`) |
| `_translate_sql` | Um passe sobre o texto: `%%` → `%`; `%s` → `:p0`, `:p1`, ... (sequencial); erro se número de placeholders ≠ número de parâmetros. Sem parâmetros, só desfaz `%%` |
| `_to_data_api_param(name, value)` | `None` → `isNull`; `bool` → `booleanValue`; `int` → `longValue`; `float` → `doubleValue`; `Decimal` → `stringValue` + `typeHint DECIMAL`; `datetime` → `"%Y-%m-%d %H:%M:%S"` + `TIMESTAMP`; `date` → ISO + `DATE`; `time` → `"%H:%M:%S"` + `TIME`; `bytes/bytearray` → `blobValue`; `str` → `stringValue`; qualquer outro objeto → `json.dumps(..., default=str)` + `typeHint JSON` |
| `_decode_field` | Devolve o primeiro de `stringValue/longValue/doubleValue/booleanValue/blobValue`; `arrayValue` bruto; `isNull` → `None` |
| O que não existe | `executemany`, streaming/paginação de resultados, pool de conexões, retry/backoff, timeout configurável do cliente boto3, autocommit sem transação, `callproc`, conversão de `DATETIME`/`DECIMAL` de volta para tipos Python (valores chegam como string), `conn.ping()`, `SET` de sessão automático |
| Helpers de erro | `is_duplicate_column_error`, `is_duplicate_key_error`, `is_unknown_key_error` (casamento textual) |

Limites do Data API registrados no código/documentação: 65.536 caracteres de texto SQL por `ExecuteStatement`; 1 MB de resposta por chamada (`UnsupportedResultException: The result exceeds the size limit 1 MB`, ocorrido em `VolumeService._fetch_full_history_sets` e corrigido com `JSON_EXTRACT(a.raw_payload, '$.workout_plan_id')`); estado de sessão (`SET ...`) só persiste dentro de uma transação.

#### 6.2 `api/app/secrets_bootstrap.py`

`load_secrets()` percorre a lista `[("OPENAI_API_KEY","OPENAI_API_KEY_SECRET_ARN"), ("API_TOKEN","API_TOKEN_SECRET_ARN"), ("SECRET_ENCRYPTION_KEY","SECRET_ENCRYPTION_KEY_SECRET_ARN")]`; para cada par em que a env var `*_SECRET_ARN` existe e a env var alvo ainda não está definida, chama `secretsmanager.get_secret_value(SecretId=arn)` e grava `SecretString` em `os.environ[env_name]`. Falhas individuais são ignoradas (`continue`). É chamado no topo de `main.py`, `main_engines.py`, `lambda_plan_generator.py`, `lambda_cardio_agent.py`, `lambda_workout_ai.py` e `lambda_reports.py`, antes dos imports de negócio. O cliente `openai_client.call_openai` trata `OPENAI_API_KEY` vazio ou igual a `"replace_me"` como não configurado.

#### 6.3 `spikes/data-api-shim/`

`rds_data_shim.py` (11,6 KB) é a versão original do shim, com `Connection(resource_arn, secret_arn, database, region)` explícitos; `test_spike.py` executa 7 cenários contra o cluster real usando uma tabela `spike_test_table` criada e removida pelo próprio script. Resultado registrado em `docs/spike-data-api.md`: 7/7 aprovados em 2026-09-03. `db.py` difere do spike por ler configuração de env vars, suportar `with conn:` e expor `Cursor.description`/`Cursor.close()`.

#### 6.4 DynamoDB — `async_jobs.py` e `jobs.py`

| Função | Operação | Detalhe |
|---|---|---|
| `create_job(job_type, request)` | `put_item` | Item: `job_id` (uuid4 str), `job_type`, `status="pending"`, `created_at` (epoch s), `ttl = created_at + 21600` (6 h), `request` (JSON string, opcional) |
| `mark_done(job_id, result)` | `update_item` | `SET status='done', result=<JSON string>, finished_at=<epoch>` |
| `mark_failed(job_id, error)` | `update_item` | `SET status='failed', error=<str>, finished_at=<epoch>` |
| `get_job(job_id)` | `get_item` | Retorna `{job_id, job_type, status, created_at, finished_at, result? (parse JSON), error?}` |
| `invoke_async(env_var_name, payload)` | `lambda.invoke(FunctionName=os.environ[env_var_name], InvocationType="Event", Payload=json)` | — |

Tabela: `os.environ["ASYNC_JOBS_TABLE"]` via `boto3.resource("dynamodb").Table(...)`. Endpoint de leitura: `GET /jobs/{job_id}` (`jobs.py`, autenticado; 404 quando ausente/expirado).

Valores de `job_type` usados e Lambda destino:

| `job_type` | Origem (endpoint) | Env var da função | Payload do evento |
|---|---|---|---|
| `plan-generator` | `POST /workout-plans/generate` | `PLAN_GENERATOR_FUNCTION_NAME` | `{job_id, payload (WorkoutPlanGenerateIn), user_id}` |
| `cardio-agent` | `POST /cardio-recommendations/generate` | `CARDIO_AGENT_FUNCTION_NAME` | `{job_id, payload, user_id}` |
| `workout-ai-evaluate-set` | `POST /exercises/evaluate-set` | `WORKOUT_AI_FUNCTION_NAME` | `{job_id, job_type, payload, user_id}` |
| `workout-ai-exercise-summary` | `POST /exercises/{id}/summarize` | `WORKOUT_AI_FUNCTION_NAME` | idem |
| `workout-ai-activity-summary` | `POST /activities/{id}/ai-summary/generate` | `WORKOUT_AI_FUNCTION_NAME` | idem |
| `insights` | `POST /insights/generate` | `WORKOUT_AI_FUNCTION_NAME` | idem |
| `workouts_analysis` | `POST /workouts-analysis/generate` | `WORKOUT_AI_FUNCTION_NAME` | idem |
| `report_generation` | `POST /reports/generate` | `REPORTS_FUNCTION_NAME` | `{job_id, report_id, user_id, days}` |

Os handlers das 4 Lambdas assíncronas seguem o mesmo esqueleto: ler `job_id` (retorna sem gravar se ausente), executar o `dispatch_*` correspondente, `mark_done(result)` ou `mark_failed(str(exc.detail or exc))`. `lambda_reports.handler` também depende de `reports_core.run_report_generation`, que atualiza a linha `reports` (`status`, `pdf_path`, `context_snapshot`, `model`, `completed_at` ou `error_message` truncado em 2000 caracteres).

#### 6.5 `engines_client.py` (removido)

Removido no SEG-04 (2026-10-09): nunca teve chamador. As policies `musclefit-api-core-invoke-engines` e `musclefit-ai-generators-invoke-engines` e a env `ENGINES_FUNCTION_NAME` saíram junto.

---

### 7. Armazenamento de objetos usado pelo backend

| Uso | Módulo | Bucket (env) | Prefixo/Key | Content-Type | Leitura | Invalidação CloudFront |
|---|---|---|---|---|---|---|
| Foto de equipamento | `gyms.py::_save_photo("equipment", id, "equipment", file)` | `EQUIPMENT_PHOTOS_BUCKET` (`musclefit-uploads`) | `equipment/<id><ext>` (`ext` ∈ `.jpg .png .webp .gif` derivado do `content_type`) | `file.content_type` | Pública via CloudFront `https://app.musclefit.com.br/equipment/<id><ext>` (campo `photo_url`); endpoint `GET /equipment/{id}/photo` (via Lambda) mantido | `create_invalidation` em `/<key>` novas e antigas (`CallerReference = gyms-photo-<ms>-<hex8>`) |
| Logo de academia | `gyms.py::_save_photo("gyms", id, "gyms", file)` | idem | `gyms/<id><ext>` | idem | Pública via CloudFront `/gyms/<id><ext>` (`logo_url`); `GET /gyms/{id}/logo` mantido | idem |
| Foto de usuário | `users.py` → `gyms._save_photo("users", id, "users", file)` | idem | `users/<id><ext>` | idem | Somente via Lambda `GET /users/{id}/photo` (`_read_photo` → `s3.get_object` → `Response`); prefixo não coberto pela bucket policy do CloudFront | `_invalidate_cloudfront_paths` é chamado também para essa key (path não servido pelo CloudFront) |
| PDF de relatório | `reports_core.py::put_report_pdf` | idem | `reports/<report_id>.pdf` | `application/pdf` | `GET /reports/{id}/pdf` via Lambda (`Response` com `Content-Disposition: attachment; filename="relatorio-<id>.pdf"`) | nenhuma |
| Card ilustrado — rascunho (pago, ainda não aprovado) | `exercise_card_storage.put_draft` (worker `musclefit-plan-generator`) | `EQUIPMENT_PHOTOS_BUCKET` (`musclefit-uploads`) | `exercise-card-drafts/<generation_id>/illustration.png` (ilustração crua da OpenAI) e `preview.png` (card composto = o que o admin revisa e o que é publicado) | `image/png`, `Cache-Control: private, no-store` | **Fora da CDN**: só por URL pré-assinada de 15 min gerada pela api-core (`presign_draft_url`; em DEV assinada para `S3_PRESIGN_ENDPOINT_URL`) | — |
| Card ilustrado — publicado | `exercise_card_storage.put_published` (aprovação, api-core) | idem | `exercise-cards/<exercise_id>/v<n>/card.webp` (q85), `card.png` (master) e `thumb.webp` (400 px) — path novo por versão, nunca sobrescrito | `image/webp` / `image/png`, `Cache-Control: public, max-age=31536000, immutable` | Pública via CloudFront (`/exercise-cards/*`), URL montada por `public_assets.url_for` (`PUBLIC_ASSETS_BASE_URL`, default `https://app.musclefit.com.br`; em DEV o LocalStack) | nenhuma (URL imutável por versão) |
| RAG markdown | `rag_storage.py::ensure_rag_local()` | `RAG_S3_BUCKET` (`musclefit-rag`), `RAG_S3_PREFIX` (`markdown/`) | download de todos os objetos sob o prefixo para `RAG_MARKDOWN_DIR` (`/tmp/rag`), em paralelo (`ThreadPoolExecutor`, até 8 workers), gravando `.part` e renomeando; marcador `.synced` + flag de módulo `_SYNCED`; rejeita chaves com `..` ou absolutas; qualquer exceção é logada e o diretório local é devolvido | — | leitura apenas | — |

Regras de upload de foto: tamanho máximo `MAX_PHOTO_BYTES = 8 MiB`; tipos aceitos `image/jpeg`, `image/png`, `image/webp`, `image/gif`; antes do `put_object`, `list_objects_v2(Prefix="<subdir>/<id>.")` e `delete_object` de todas as keys antigas. `_delete_photo` faz `delete_object` + invalidação. `_read_photo` mapeia extensão → content type (default `application/octet-stream`) e devolve 404 em `NoSuchKey`. Todas as chamadas S3 usam `boto3.client("s3", region_name=AWS_REGION|AWS_DEFAULT_REGION|"sa-east-1")`; o cliente CloudFront é criado sem região explícita.

---

### 8. `tools/onprem-sync`

#### 8.1 O que é

Ferramenta CLI Python (`sync.py` + pacote `syncmod/`, ~2.400 linhas) que espelha os dados do MySQL on-premise (`fitness`, container Docker `fitness-mysql`, MySQL 8.4) no Aurora (`musclefit`, cluster `musclefit-db`), de forma unidirecional (on-premise é a fonte; linhas só no Aurora são apagadas; IDs preservados). Adicionada no PR #36. O README registra que `--apply` nunca foi executado contra o Aurora real até a data do documento; os 8 diretórios em `backups/` (2026-09-12, `20260912T110807Z` a `20260912T154335Z`) demonstram execuções de backup (que ocorrem em `--backup-only` e no início de `--apply`); não verificado se algum `--apply` completou.

#### 8.2 Configuração (`sync_config.json`)

| Chave | Valor |
|---|---|
| `onprem.container` / `database` / `mysql_user` / `password_env` | `fitness-mysql` / `fitness` / `root` / `MYSQL_ROOT_PASSWORD` (lido dentro do container) |
| `aurora.region` / `cluster_arn` / `cluster_identifier` / `secret_arn` / `database` / `profile` | `sa-east-1` / `arn:aws:rds:sa-east-1:930413537709:cluster:musclefit-db` / `musclefit-db` / `null` (descoberto via `rds:DescribeDBClusters` → `MasterUserSecret.SecretArn`) / `musclefit` / `musclefit-tf` |
| `session.time_zone` | `+00:00` (fixado nos dois lados e verificado) |
| `excluded_tables` | `schema_migrations`, `exercise_mappings`, `exercise_mappings_backup_20260714`, `exercises_backup_before_mapping_sync`, `user_sessions` |
| `batch` | `max_rows_per_batch 200`, `max_bytes_per_batch 700000`, `max_bytes_per_read_page 700000`, `max_rows_per_read_page 500` |
| `encoding_probes` | `gyms.id=1.name`, `gyms.id=11.name`, `muscle_groups.id=1.name` (comparação `HEX()`) |
| `collation_exceptions` | `intervals_activities`, `intervals_wellness`, `exercise_mappings_backup_20260714` → `utf8mb4_unicode_ci` |

Precedência de perfil AWS: `--aws-profile` > `AWS_PROFILE` > `aurora.profile`.

#### 8.3 Modos e sequência

| Comando | Efeito |
|---|---|
| `python sync.py` (dry-run, padrão) | Lê schema/dados do on-premise (via `docker exec -i fitness-mysql sh -c "exec mysql --default-character-set=utf8mb4 --raw -N -B ..."`), preflight de paridade de schema com o Aurora (tabelas e colunas iguais; aviso de collation), lê o Aurora, calcula e imprime o plano (INSERT/UPDATE/DELETE por tabela, `AUTO_INCREMENT` a realinhar, colunas divergentes). Não escreve |
| `--onprem-only` | Inventário, ordem topológica e prévia de statements; não usa credencial AWS |
| `--backup-only` | Gera `backups/<UTC ts>/manifest.json`, `data/<tabela>.json` (formato `canonical-string-rows/v1`, com `columns`, `pk_columns`, `rows`) e `restore.sql` (`DELETE FROM` + `INSERT` por tabela, `FOREIGN_KEY_CHECKS=0`, transação) |
| `--apply` | preflight → leitura → backup obrigatório → confirmação interativa digitando `ESPELHAR musclefit` (aborta sem TTY) → `begin_transaction` com `SET SESSION time_zone` e `SET SESSION FOREIGN_KEY_CHECKS = 0` (ambos verificados por `SELECT @@SESSION...`) → DELETEs em ordem topológica inversa → UPDATEs e INSERTs em ordem direta (`batch_execute_statement`, lotes por bytes) → `FOREIGN_KEY_CHECKS = 1` verificado → `commit_transaction` → `ALTER TABLE ... AUTO_INCREMENT = n` fora da transação → verificação pós-sync (contagem, SHA-256 do conteúdo canônico por tabela, FKs órfãs por relação, soma de `OCTET_LENGTH`/`CHAR_LENGTH` e sondas `HEX`) |
| `--restore-from DIR` | Diff backup × Aurora, confirmação `RESTAURAR musclefit`, aplica pelo mesmo caminho transacional |
| `--tables a,b` | Restringe o escopo |
| `--skip-verify`, `--config`, `--backup-dir`, `--aws-profile` | Opções auxiliares |

Códigos de saída: `0` sucesso, `1` erro, `2` sync aplicado com divergência na verificação, `3` sessão AWS ausente/expirada (`AuroraCredentialsError`, detectado por marcadores como `ExpiredToken`, `InvalidClientTokenId`, `NoCredentialsError`, `MissingDependencyException` com `crt`).

#### 8.4 Mecanismos internos

- `syncmod/schema.py`: modelo `Schema/Table/Column/ForeignKey` construído a partir de `information_schema` do on-premise; leitura canônica com `CAST(col AS CHAR)` (exceto colunas de texto); JSON re-serializado com chaves ordenadas para comparação; `topological_order` derivada das FKs (auto-referência e FKs fora do escopo ignoradas); `Column.data_api_param` reconverte texto → parâmetro tipado (`longValue`, `DECIMAL`, `TIMESTAMP`, `DATE`, `TIME`, `JSON`, `stringValue`).
- `syncmod/aurora.py`: `AuroraClient` sobre `boto3 rds-data`; toda operação (inclusive leitura) roda dentro de transação com prelude de sessão; retry com backoff exponencial (1 s → 20 s, 5 tentativas) para mensagens transitórias (`communications link failure`, `is resuming after being auto-paused`, `throttl`, `too many connections`, ...); erros de tamanho (`exceeded size limit`, `result set too large`) reduzem a página pela metade; leitura em duas fases (PKs ordenadas, depois linhas completas por `WHERE (pk) IN (...)` sem `ORDER BY`).
- `syncmod/onprem.py`: `OnpremReader` recusa qualquer statement cujo início não seja `select/set/show/use/--`.
- `syncmod/applier.py`: `_chunk_by_size` dimensiona lotes por bytes medidos (`stringValue` UTF-8 + 40 bytes por parâmetro); `fix_auto_increment` exige transação fechada.
- `syncmod/verify.py`: verificação de FK órfã usa apenas colunas numéricas (imune a collation).
- `tests/selftest.py`: sobe container `onprem-sync-selftest-mysql` via `docker run`, carrega schema real (`mysqldump --no-data`) e substitui o cliente `rds-data` por `tests/fake_data_api.py` (PyMySQL); 6 cenários / 32 verificações.
- `sync.py::EXPECTED_TIERS`: lista humana de 12 camadas (usada só para aviso, não para ordenar).
- `.gitignore` local exclui `backups/` (contém dados pessoais e hashes de senha).

Escopo atual medido (`manifest.json` de 2026-09-12T15:43Z): 41 tabelas, contagens por tabela registradas (ex.: `exercise_sets` 1228, `exercises` 358, `gym_equipment` 412, `exercise_aliases` 421, `intervals_activities` 359, `workout_plan_exercises` 189, `users` 3). O README cita "56 FKs" e "5.088 linhas".

---

### 9. Configuração e variáveis de ambiente lidas pelo backend

| Variável | Onde é lida | Default no código | Definida no Terraform em | Valor literal |
|---|---|---|---|---|
| `AURORA_CLUSTER_ARN` | `db.py` | obrigatória | 6 Lambdas | ARN do cluster |
| `AURORA_SECRET_ARN` | `db.py` | obrigatória | 6 Lambdas | secret master RDS |
| `AURORA_DATABASE` | `db.py` | obrigatória | 6 Lambdas | `musclefit` |
| `AWS_REGION` / `AWS_DEFAULT_REGION` | `db.py`, `async_jobs.py`, `gyms.py`, `reports_core.py`, `secrets_bootstrap.py` | `sa-east-1` | injetada pelo runtime Lambda | `sa-east-1` |
| `ASYNC_JOBS_TABLE` | `async_jobs.py` | obrigatória | api-core, plan-generator, cardio-agent, workout-ai, reports | `musclefit-async-jobs` |
| `OPENAI_API_KEY_SECRET_ARN` → `OPENAI_API_KEY` | `secrets_bootstrap.py`; `openai_client.py`, `cardio_agent_ai.py`, `exercise_set_evaluation_ai.py`, `exercise_summary_ai.py`, `insights_ai.py`, `workout_plan_ai.py`, `workout_summary_ai.py` | `""` | todas exceto engines | segredo |
| `API_TOKEN_SECRET_ARN` → `API_TOKEN` | `secrets_bootstrap.py`; `security.py` | `""` | api-core, engines, workout-ai, reports | segredo |
| `SECRET_ENCRYPTION_KEY_SECRET_ARN` → `SECRET_ENCRYPTION_KEY` | `secrets_bootstrap.py`; `crypto_utils.py` (chave Fernet) | erro na primeira cifragem se ausente | todas exceto engines | segredo |
| `OPENAI_MODEL_PLAN` | `workout_plan_ai.py` (depois de `OPENAI_WORKOUT_PLAN_MODEL`) | `gpt-5.4-mini` | api-core (sem leitor lá), plan-generator, cardio-agent | `gpt-5.4-mini` |
| `OPENAI_MODEL_CARDIO` | `cardio_agent_ai.py` (antes de `OPENAI_CARDIO_AGENT_MODEL`) | `gpt-5.4-mini` | api-core (sem leitor lá), plan-generator, cardio-agent | `gpt-5.4-mini` |
| `OPENAI_WORKOUT_PLAN_MODEL` | `workout_plan_ai.py` (override local, vence `OPENAI_MODEL_PLAN`) | `gpt-5.4-mini` | não definida | — |
| `OPENAI_CARDIO_AGENT_MODEL` | `cardio_agent_ai.py` (nome legado, só se `OPENAI_MODEL_CARDIO` faltar) | `gpt-5.4-mini` | não definida | — |
| `OPENAI_SET_EVAL_MODEL` | `exercise_set_evaluation_ai.py` | `gpt-5.4-mini` | workout-ai | `gpt-5.4-mini` |
| `OPENAI_EXERCISE_SUMMARY_MODEL` | `exercise_summary_ai.py` | `gpt-5.4-mini` | workout-ai | `gpt-5.4-mini` |
| `OPENAI_WORKOUT_SUMMARY_MODEL` | `workout_summary_ai.py` | `gpt-5.4-mini` | workout-ai | `gpt-5.4-mini` |
| `OPENAI_INSIGHTS_MODEL` | `insights_ai.py` | `gpt-5.4-mini` | não definida | — |
| `OPENAI_REPORTS_MODEL` | `report_ai.py` | `gpt-5.4-mini` | não definida | — |
| `PLAN_GENERATOR_FUNCTION_NAME`, `CARDIO_AGENT_FUNCTION_NAME`, `WORKOUT_AI_FUNCTION_NAME`, `REPORTS_FUNCTION_NAME` | `async_jobs.invoke_async` (nome da env var passado pelo chamador) | obrigatórias no uso | api-core | nomes das funções |
| `EQUIPMENT_PHOTOS_BUCKET` | `gyms.py`, `reports_core.py` | 503 / `RuntimeError` se ausente | api-core, reports | `musclefit-uploads` |
| `CLOUDFRONT_DISTRIBUTION_ID` | `gyms.py` | pula invalidação com warning | api-core | `EOA1PMADLM7RO` |
| `RAG_S3_BUCKET` | `rag_storage.py` | `None` (modo local) | 6 Lambdas | `musclefit-rag` |
| `RAG_S3_PREFIX` | `rag_storage.py` | `markdown/` | 6 Lambdas | `markdown/` |
| `RAG_MARKDOWN_DIR` | `rag_storage.py`, `rag_chunking.py`, `exercise_set_evaluation_ai.py`, `workout_plan_ai.py` | `/app/rag` | 6 Lambdas | `/tmp/rag` |
| `SESSION_COOKIE_DOMAIN` | `security.py` | `.musclefit.com.br` | api-core, engines | `.musclefit.com.br` |
| `SESSION_COOKIE_SECURE` | `security.py` | não definida → detecta `https` pelo scheme ou `X-Forwarded-Proto` | não definida | — |
| `CORS_ALLOWED_ORIGINS` | `main.py`, `main_engines.py` | `https://app.musclefit.com.br` | api-core, engines | `https://app.musclefit.com.br,https://zepp-os.zepp.com` |
| `USE_TEMPLATE_VOLUME_ENGINE_V2` | `plan_generation_core.py` | `"true"` | não definida | — |
| `PORT`, `AWS_LWA_READINESS_CHECK_PATH` | Lambda Web Adapter | — | Dockerfile/Dockerfile.engines | `8000`, `/health` |
| `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED` | Python | — | 4 Dockerfiles | `1` |

Parâmetros de sessão/autenticação (`auth.py`, `security.py`): cookie `musclefit_session` (`httponly`, `samesite=lax`, `path=/`, `domain` conforme env, `secure` conforme detecção, `max_age = 30 dias`); `SESSION_TTL_DAYS = 30`; `MAX_FAILED_ATTEMPTS = 5`; `LOCKOUT_MINUTES = 15`; `MIN_PASSWORD_LENGTH = 8`; `ADMIN_SEED_USERNAME = "doug"`; tokens de sessão e de dispositivo armazenados como SHA-256. CORS: `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]`.

Frontend: `NEXT_PUBLIC_API_BASE_URL` (default `https://api.musclefit.com.br`), não definida no workflow.

---

### 10. Limites, quotas e parâmetros operacionais declarados

| Parâmetro | Valor | Onde |
|---|---|---|
| Lambda `musclefit-api-core` / `musclefit-engines` timeout | 29 s (teto da integração REST do API Gateway) | `lambda_api_core.tf`, `lambda_engines.tf` |
| Lambda `plan-generator` / `cardio-agent` timeout | 120 s | `lambda_ai_generators.tf` |
| Lambda `workout-ai` timeout | 60 s | `lambda_workout_ai.tf` |
| Lambda `reports` timeout / memória | 300 s / 2048 MB | `lambda_reports.tf` |
| Memória das demais Lambdas | 1024 MB | arquivos TF |
| Concorrência da conta (`sa-east-1`) | `ConcurrentExecutions = 10`, `UnreservedConcurrentExecutions = 10` (confirmado); sem reserva por função | `get-account-settings`; comentários em `gyms.py`/`cloudfront_uploads.tf` |
| Retenção de logs | 14 dias, 6 log groups | `aws_cloudwatch_log_group.*` |
| ECR lifecycle | 10 imagens por repositório | 4 `aws_ecr_lifecycle_policy` |
| Aurora ACU | mín 0,5 / máx 1,0 | `aurora.tf`, confirmado |
| Aurora backup | retenção 1 dia; janela `06:00-07:00` UTC; snapshot final `musclefit-db-final`; `deletion_protection = true` | `aurora.tf` |
| Aurora logs exportados | `error`, `slowquery`, `audit` | `aurora.tf` |
| Data API — SQL literal | 65.536 caracteres por statement | `docs/spike-data-api.md` |
| Data API — resposta | 1 MB por chamada (`ExecuteStatement`) | plano Fase 3; `syncmod/aurora.py` |
| DynamoDB TTL de jobs | 6 h (`_JOB_TTL_SECONDS = 21600`) | `async_jobs.py` |
| Upload de foto | 8 MiB; JPEG/PNG/WEBP/GIF | `gyms.py` |
| OpenAI HTTP timeout | 90 s (`call_openai`, default) | `openai_client.py` |
| Relatório | período default 90 dias (`REPORT_PERIOD_DAYS`), aceito 7–365 dias; contexto de cardio limitado a 30 dias (`CARDIO_CONTEXT_MAX_DAYS`); `error_message` truncado em 2000 caracteres | `reports.py`, `reports_core.py`, `intelligence/report_data_service.py` |
| Sessão | 30 dias; 5 tentativas → bloqueio 15 min; senha mínima 8 | `auth.py` |
| RAG download | até 8 threads; marcador `.synced` em `/tmp/rag` | `rag_storage.py` |
| CloudFront | `CachingOptimized` (managed), TLS mínimo `TLSv1.2_2021`, SNI, IPv6, sem price class explícita | `cloudfront.tf` |
| API Gateway | `binary_media_types = ["*/*"]`; sem throttling/usage plan customizado (defaults da conta) | `api_gateway.tf` |
| onprem-sync | lotes de 200 linhas / 700 KB; páginas de leitura de 500 linhas / 700 KB; 5 tentativas com backoff 1–20 s | `sync_config.json`, `syncmod/aurora.py` |
| Terraform | `>= 1.7`; provider AWS `5.100.0`; state em `fase1/terraform.tfstate` | `versions.tf`, `.terraform.lock.hcl` |
| Budget | US$ 30/mês (alertas 80/100%); anomalia US$ 10 diária | plano Fase 0 |
| Cold start `musclefit-reports` | ≈ 13 s medido, registrado nas pendências da Fase 7 (matplotlib/fontconfig gravando em `$HOME`) | `docs/aws-migration-plan.md` |
| Frontend | Next.js `14.2.35`, React 18, Mantine 7, TanStack Query 5, `output: 'export'`, `trailingSlash: true`, `images.unoptimized` | `frontend/package.json`, `next.config.mjs` |

---

### 11. Inventário confirmado na conta AWS (2026-09-12, somente leitura)

| Serviço | Recursos encontrados |
|---|---|
| Lambda | `musclefit-api-core` (1024/29), `musclefit-engines` (1024/29), `musclefit-plan-generator` (1024/120), `musclefit-cardio-agent` (1024/120), `musclefit-workout-ai` (1024/60), `musclefit-reports` (2048/300); todas `Image`, `x86_64` |
| CloudFront | `EOA1PMADLM7RO` → `d2kf866t8pk1us.cloudfront.net`, aliases `app.musclefit.com.br`, `musclefit.com.br` |
| API Gateway | REST `vaercx9o3g` `musclefit-api` (REGIONAL); domain `api.musclefit.com.br` → `d-u1fd3xctnj.execute-api.sa-east-1.amazonaws.com` |
| ECR | `musclefit-reports`, `musclefit-api-core`, `musclefit-ai-generators`, `musclefit-engines` |
| DynamoDB | `musclefit-async-jobs`, `musclefit-tfstate-lock` |
| S3 | `musclefit-frontend-assets`, `musclefit-rag`, `musclefit-tfstate-930413537709`, `musclefit-uploads` |
| RDS | cluster `musclefit-db`, `8.0.mysql_aurora.3.08.2`, `available`, Data API habilitada, 0,5–1 ACU, retenção 1 dia, deletion protection |
| Secrets Manager | `rds!cluster-b3317b3e-2be1-41f8-9c18-132e4847112f`, `musclefit/secret-encryption-key`, `musclefit/openai-api-key`, `musclefit/api-token` |
| IAM (role `musclefit-gha-deploy`) | inline: `fase0`, `fase1`, `fase3`, `fase3-engines`, `fase3-uploads`, `fase4-frontend`; managed: `fase5-planreadonly`, `fase5-reports`, `fase5-rag` |
| Banco `musclefit` | 46 tabelas + 1 view; `schema_migrations` em v42 |

Não verificados nesta leitura: conteúdo das policies inline `fase0/fase1/fase3/fase3-uploads` e da managed `fase5-planreadonly`; trust policy literal da role `musclefit-gha-deploy`; configurações de PITR/Streams da tabela DynamoDB; configuração de logging do API Gateway stage; existência de alarmes CloudWatch; valores de `StorageType` do cluster (API retornou `null`).

Atualização 2026-10-05 (M44): o DNS do apex, que esta leitura não verificou, está resolvido — `musclefit.com.br` responde por ALIAS A/AAAA para o CloudFront, a partir da hosted zone do Route 53 gerida por `infra/route53.tf` (delegação no Registro.br desde 2026-10-03). Certificados ACM conferidos no mesmo dia: `app.musclefit.com.br` + apex (us-east-1) e `api.musclefit.com.br` (sa-east-1) `ISSUED`, `RenewalEligibility=ELIGIBLE`, vencem em 2027-03-19 e renovam sozinhos pelos CNAMEs de validação publicados na zona.

---

# Parte II — Backend (`api/app`)

## MuscleFit (AWS) — Documentação descritiva do backend (`api/app`)

Documento gerado por leitura direta do código em `C:\muscle_fit\api\app` (≈33.259 linhas Python em 84 arquivos `.py`, incluindo `intelligence/`). Descreve o estado atual do código, sem juízo de valor. Onde algo não pôde ser confirmado, está marcado como "não verificado".

Convenções deste documento:

- Paths de módulo são relativos a `api/app/` (ex.: `services.py`, `intelligence/volume_service.py`).
- "Data API" refere-se ao shim `db.py` (RDS Data API sobre Aurora Serverless v2); não é detalhado aqui, apenas citado como é consumido.
- Termos `PRIMARY`/`SECONDARY`/`STABILIZER` referem-se a `exercise_muscle_contributions.role`.

---

### 1. Mapa de módulos

#### 1.1 Entrypoints Lambda

| Entrypoint | Lambda (nome usado em comentários/env) | Como é invocado | O que carrega |
|---|---|---|---|
| `main.py` (`app = FastAPI(...)`, title "Doug Fitness API") | `musclefit-api-core` | HTTP via API Gateway + Lambda Web Adapter | Rotas `@app.*` (19) + todos os routers de CRUD/IA assíncrona (ver 1.3). Não inclui `analytics.py`, `dashboard_modules.py`, `dashboard_intelligence.py`, `engines_extra.py`. |
| `main_engines.py` (`app = FastAPI(...)`, title "Doug Fitness Engines API") | `musclefit-engines` | HTTP via API Gateway (mesmos paths públicos; o Gateway roteia `/analytics/*`, `/exercises/suggested-progression`, `/exercises/suggested-set-count-for-substitution` para esta Lambda) | `/health`, `/ready` próprios + routers `analytics`, `dashboard_modules`, `dashboard_intelligence`, `engines_extra`. Chama `load_secrets()` (resolve só `API_TOKEN`). |
| `lambda_workout_ai.py` (`handler(event, context)`) | `musclefit-workout-ai` (imagem `musclefit-ai-generators`) | `lambda:InvokeFunction` `InvocationType=Event` disparado por `async_jobs.invoke_async("WORKOUT_AI_FUNCTION_NAME", ...)` | `workout_ai_generation_core.dispatch_workout_ai_generation` (job types: `workout-ai-evaluate-set`, `workout-ai-exercise-summary`, `workout-ai-activity-summary`, `workouts_analysis`, `insights`). Grava resultado em DynamoDB via `async_jobs.mark_done/mark_failed`. |
| `lambda_plan_generator.py` (`handler`) | `musclefit-plan-generator` | `InvocationType=Event` via `invoke_async("PLAN_GENERATOR_FUNCTION_NAME", ...)` | Valida `WorkoutPlanGenerateIn(**event["payload"])` e chama `plan_generation_core.dispatch_plan_generation`. |
| `lambda_cardio_agent.py` (`handler`) | `musclefit-cardio-agent` | `InvocationType=Event` via `invoke_async("CARDIO_AGENT_FUNCTION_NAME", ...)` | `cardio_generation_core.dispatch_cardio_generation` (gera via OpenAI e persiste em `cardio_recommendations`). |
| `lambda_reports.py` (`handler`) | `musclefit-reports` (imagem dedicada com WeasyPrint) | `InvocationType=Event` via `invoke_async("REPORTS_FUNCTION_NAME", ...)`; evento `{"job_id","report_id","user_id","days"}` | `reports_core.run_report_generation`. Marca `reports.status` e o job DynamoDB. |

Todos os entrypoints chamam `secrets_bootstrap.load_secrets()` antes de importar módulos de negócio. Em cada `handler` assíncrono: sem `job_id` no evento → `logger.error` e retorno silencioso; qualquer exceção → `logger.exception` + `async_jobs.mark_failed(job_id, _error_message(exc))`, onde `_error_message` devolve `exc.detail` (str ou dict convertido em str) se existir, senão `str(exc)`.

#### 1.2 Tabela de módulos (um por linha)

| Arquivo | Linhas | Responsabilidade | Principais funções/classes | Dependências internas |
|---|---|---|---|---|
| `main.py` | 703 | App FastAPI `musclefit-api-core`; CORS; rotas core (`/activities`, `/exercises/*`, `/sets/high-rpe`, `/exercise-mappings`) e endpoints 202 de IA do modo treino/insights; inclusão dos routers | 19 handlers `@app.*`; blocos `try/except` de `include_router`; eventos `startup` (`_seed_admin_user`, `_migrate_plaintext_intervals_keys`) | `secrets_bootstrap`, `async_jobs`, `db.get_conn`, `models`, `security`, `services`, `workout_ai_generation_core` + todos os routers |
| `main_engines.py` | 135 | App FastAPI `musclefit-engines` | `/health`, `/ready`; `include_router` de 4 routers | `analytics`, `dashboard_modules`, `dashboard_intelligence`, `engines_extra`, `db` |
| `lambda_workout_ai.py` | 62 | Handler Lambda IA modo treino/insights/análise | `handler`, `_error_message` | `async_jobs`, `workout_ai_generation_core` |
| `lambda_plan_generator.py` | 57 | Handler Lambda geração de plano | `handler` | `async_jobs`, `models.WorkoutPlanGenerateIn`, `plan_generation_core` |
| `lambda_cardio_agent.py` | 52 | Handler Lambda cardio | `handler` | `async_jobs`, `cardio_generation_core` |
| `lambda_reports.py` | 73 | Handler Lambda relatório PDF | `handler` | `async_jobs`, `reports_core` |
| `secrets_bootstrap.py` | 50 | Carrega `OPENAI_API_KEY`, `API_TOKEN`, `SECRET_ENCRYPTION_KEY` do Secrets Manager para `os.environ` | `load_secrets`, `_SECRETS` | boto3 |
| `db.py` | 371 | Shim DB-API sobre RDS Data API: `Connection`, `Cursor`, `get_conn`, `transaction`, `DataApiError`, `is_duplicate_column_error`, `is_duplicate_key_error`, `is_unknown_key_error` | ver 8.1 | boto3 `rds-data` |
| `security.py` | 240 | Identidade/autenticação: cookie `musclefit_session`, token mestre, token de dispositivo, sessão; guards | `Identity`, `resolve_identity`, `get_current_identity`, `require_token`, `require_master_token`, `require_admin`, `require_admin_or_pt`, `device_token_matches`, `token_is_valid`, `session_cookie_domain`, `session_cookie_secure`, `_admin_user_id`, `_session_identity` | `db.transaction` |
| `auth.py` | 280 | Router `/auth` (login/logout/me/change-password); bcrypt; seed admin | `LoginIn`, `ChangePasswordIn`, `hash_password`, `verify_password`, `ensure_admin_seed`, `_set_session_cookie`, `_clear_session_cookie`, `_public_user` | `db`, `security` |
| `users.py` | 372 | Router `/users` (CRUD admin + `/me` + foto) | `_public_row`, `_validate_email`, `_encrypt_intervals_api_key`, `migrate_plaintext_intervals_keys`, `require_session_identity`, `_require_self_or_admin` | `auth`, `crypto_utils`, `db`, `gyms._save_photo/_read_photo/_delete_photo`, `security` |
| `crypto_utils.py` | 94 | Fernet para `users.intervals_api_key_encrypted` | `encrypt_secret`, `decrypt_secret`, `is_encrypted`, `_get_fernet` | cryptography |
| `devices.py` | 148 | Router de pareamento Zepp (token mestre) | `DevicePairIn`, `_ensure_schema`, `pair_device`, `list_devices`, `revoke_device` | `db`, `security._admin_user_id`, `security.require_master_token` |
| `models.py` | 421 | Modelos Pydantic compartilhados | ver seção 4 | pydantic |
| `services.py` | 2326 | Regras/CRUD de atividades, exercícios, séries, planos, body composition, mapeamentos legados, body map; resolução canônica de ids | `upsert_activity`, `update_activity`, `delete_activity`, `latest_activities`, `activity_detail`, `exercise_summary_context`, `save_activity_ai_summary`, `list_exercise_logs`, `update_exercise_log`, `delete_exercise_log`, `high_rpe_sets`, `exercise_history`, `get_last_session_sets`, `suggested_progression_for_mapping`, `suggested_set_count_for_substitution`, `upsert_body_composition`, `upsert_mapping`, `resolve_plan_muscle_slugs`, `list_muscle_group_body_map`, `upsert_muscle_group_body_map`, `list_workout_plans`, `get_workout_plan`, `create_workout_plan`, `update_workout_plan`, `delete_workout_plan`, `_replace_plan_exercises`, `_enrich_plan_exercises_with_progression`, `_apply_effective_max_sets_fallback`, `resolve_mapping_id`, `resolve_gym_id`, `resolve_equipment_id`, `is_gym_equipment_pair_active`, `_resolve_canonical_exercise_id`, `compute_exercise_stats`, `make_external_id`, `row_hash`, `source_id`, `to_json`, `from_json_list`, `from_json_dict`, `_format_plan_time`, `_ensure_*` (ALTERs aditivos) | `db.transaction`, `exercise_mapping_canonical.match_mapping`; imports locais de `progression_service`, `workout_plan_ai`, `workout_sessions` |
| `progression_service.py` | 299 | Motor determinístico de decisão de progressão (próxima carga/reps) | `suggest_load_and_reps`, `_find_most_recent_entry`, `_parse_reps_range`, `_trend_to_progression`, `get_is_advanced_user` (re-export) | `user_flags` |
| `analytics.py` | 1934 | Router `/analytics` (22 rotas): agregações SQL legadas + hub `/dashboard-*` | `_connect`, `_fetch_all`, `_fetch_one`, `_safe_days`, `_period_expr`, `compute_training_trend`, `compute_rolling_weekly_trend`, `_build_muscle_status_items`, `_fetch_exercise_set_rows`, `_build_progression_sessions`, `_resolve_mapping_id_by_name`, `_recovery_phase`, `_resolve_latest_assigned_template_id` | `db`, `models`, `security`; imports locais de `intelligence.*`, `services`, `training_types`, `workout_plan_templates_engine` |
| `dashboard_modules.py` | 90 | Router `/analytics` (7 rotas): body-composition-trend, training-volume, split-day, muscle-recovery, consecutive-check, body-map | handlers | `services`, `intelligence` (`BodyCompositionTrendService`, `MuscleRecoveryService`, `VolumeService`), `security` |
| `dashboard_intelligence.py` | 37 | Router `/analytics` (3 rotas): dashboard-home, training-evolution, cardio-agent-context | handlers | `intelligence` (`CardioAgentContextService`, `DashboardIntelligenceLayer`), `security` |
| `engines_extra.py` | 59 | Router (2 rotas) `/exercises/suggested-progression`, `/exercises/suggested-set-count-for-substitution` | handlers | `services`, `security` |
| `async_jobs.py` | 139 | Padrão 202 + job_id + polling em DynamoDB; disparo Event de Lambdas | `create_job`, `mark_done`, `mark_failed`, `get_job`, `invoke_async`, `_JOB_TTL_SECONDS` | boto3 |
| `jobs.py` | 21 | Router `/jobs/{job_id}` | `get_job_status` | `async_jobs`, `security` |
| `openai_client.py` | 64 | Cliente HTTP OpenAI Responses API compartilhado | `call_openai(prompt, model, timeout)`, `OpenAIGenerationError` | urllib |
| `workout_plan_ai.py` | 1613 | Motor Full Body via IA + utilitários compartilhados de geração de ficha | `gather_ficha_context`, `_load_gyms`, `_load_exercise_catalog`, `resolve_gym_name`, `project_recovery_for_date`, `build_fullbody_slots`, `build_mandatory_rotator_slots`, `get_recent_fullbody_exercise_names_by_group`, `get_latest_intervals_wellness`, `format_intervals_wellness_section`, `load_rag_text`, `build_prompt`, `call_openai`, `reconcile_with_gym_catalog`, `deduplicate_by_group_and_name`, `assign_warmup_flags`, `normalize_load_target`, `assign_progression_indicators`, `cap_rpe_target_for_reduced_dose`, `_extract_json`, `_apply_slot_bounds`, `generate_plan_draft`, `_strip_amazfit_fields`, `_trim_*` | `services`, `db`, `intelligence.muscle_recovery_service`, `intelligence.volume_service.DENSITY_WINDOW_HOURS`, `openai_client`, `progression_service`, `rag_storage`; locais: `abc_workout_template`, `rag_chunking`, `intelligence.cardio_context_service` |
| `abc_workout_template.py` | 734 | Template fixo ABC (`ABC_TEMPLATE`) + gerador determinístico | `ABC_TEMPLATE`, `load_muscle_group_names`, `resolve_muscle_primary_names`, `_status_ids_and_names`, `_matches_group`, `next_abc_split_day`, `generate_abc_plan_draft` | `abc_volume_reinforcement`, `db`, `progression_service`, `workout_plan_ai` |
| `abc_workout_ai.py` | 877 | Gerador ABC via IA (papéis fixos, IA escolhe exercício) com fallback determinístico | `_build_role_definitions`, `get_recent_abc_exercise_names_by_sequence`, `build_abc_prompt`, `_extract_json`, `_apply_role_bounds`, `_deduplicate_exercise_names`, `generate_abc_plan_draft_ai`, `enforce_recent_equipment_rotation`, `_generate_abc_plan_draft_ai_inner`, `MANDATORY_ISOLATOR_ROLES` | `abc_volume_reinforcement`, `abc_workout_template`, `db`, `progression_service`, `rag_chunking`, `workout_plan_ai` |
| `abc_volume_reinforcement.py` | 272 | Reforço de mínimo de 9 séries equivalentes por grupo (fluxo ABC) | `_reinforce_target_groups_minimum_sets`, constantes `MIN_EQUIVALENT_SETS_PER_GROUP=9.0`, `MAX_NEW_EXERCISES_PER_GROUP=3`, `MAX_SETS_PER_EXERCISE_REINFORCEMENT=9`, `TARGET_MAX_EXERCISES_PER_DAY=12` | `intelligence.muscle_contribution_resolver` |
| `plan_generation_core.py` | 141 | Dispatch da geração de plano (template equipment_series / v2 / v1, ABC, Full Body) | `dispatch_plan_generation`, `USE_TEMPLATE_VOLUME_ENGINE_V2` | `abc_workout_ai`, `models`, `workout_plan_ai`, `workout_plan_templates_engine`, locais `workout_plan_templates_engine_equipment`, `workout_plan_templates_engine_v2` |
| `workout_plans.py` | 93 | Router `/workout-plans` (CRUD + PNG mapa + generate 202) | handlers | `async_jobs`, `services`, `models`, `muscle_map`, `security` |
| `workout_plan_templates.py` | 912 | Router `/workout-plan-templates` (CRUD, atribuição, preview) + `me_router` | `_validate_template_payload`, `_list_templates`, `_get_template`, `_replace_template_slots`, `_attach_volume_coverage_warnings`, `_build_cycle`, `_compute_slot_activation`, `_compute_slot_activation_equipment_series`, `WorkoutPlanTemplatePreviewIn` | `db`, `models`, `security`; local `intelligence.template_volume_planner`, `intelligence.volume_service` |
| `workout_plan_templates_engine.py` | 860 | Motor v1 de geração a partir de modelo (IA escolhe 1–3 exercícios por grupo) | `get_template_if_assigned`, `resolve_next_slot`, `_empty_rest_day_draft`, `build_template_prompt`, `_reinforce_template_target_groups`, `generate_plan_draft_from_template` | `db`; locais `workout_plan_templates`, `workout_plan_ai`, `rag_chunking`, `training_types`, `abc_volume_reinforcement`, `intelligence.muscle_contribution_resolver`, `progression_service` |
| `workout_plan_templates_engine_v2.py` | 485 | Motor v2 (orçamento percentual por Tipo de Treinamento) — orquestração Fases 1–5 | `generate_plan_draft_from_template_v2`, `_ai_selection_to_exercises`, `_to_final_exercise_shape`, `_budget_to_prompt_dict` | `db`; locais `intelligence.candidate_exercise_provider`, `template_volume_planner`, `template_volume_domain`, `template_ai_selector_v2`, `template_volume_validator`, `workout_plan_ai`, `workout_plan_templates_engine`, `rag_chunking`, `training_types`, `progression_service` |
| `workout_plan_templates_engine_equipment.py` | 546 | Motor `volume_mode='equipment_series'` (100% determinístico) | `generate_plan_draft_from_equipment_template`, `_resolve_item_equipment`, `_find_active_gym_equipment`, `_load_gym_equipment_rows`, `_recommend_for_group`, `_load_*` | `db`; locais `services`, `workout_plan_ai`, `intelligence.template_volume_domain`, `intelligence.volume_service`, `workout_plan_templates_engine`, `progression_service` |
| `training_types.py` | 381 | Router `/training-types` (CRUD) + leituras internas | `_validate_training_type_payload`, `_validate_rag_folder`, `_list_training_types`, `_get_training_type`, `_replace_training_type_targets`, `resolve_active_training_type_for_user`, `get_training_type_muscle_group_targets` | `db`, `models`, `rag_chunking.list_training_type_rag_folders`, `security` |
| `cardio_agent_ai.py` | 145 | Prompt e chamada OpenAI do Agente de Cardio | `SYSTEM_PROMPT`, `call_openai`, `generate_cardio_recommendation`, `CardioAgentGenerationError` | `intelligence.cardio_context_service`, `openai_client` |
| `cardio_generation_core.py` | 77 | Dispatch: gera + persiste recomendação | `dispatch_cardio_generation` | `cardio_agent_ai`, `cardio_recommendations` |
| `cardio_recommendations.py` | 298 | Router `/cardio-recommendations` (log imutável) | `_ensure_schema`, `_insert_recommendation`, handlers | `async_jobs`, `db`, `security` |
| `workout_ai_generation_core.py` | 143 | Dispatch dos jobs de IA do modo treino/insights/análise | `dispatch_workout_ai_generation`, `dispatch_evaluate_set`, `dispatch_exercise_summary`, `dispatch_activity_summary`, `JOB_TYPE_*` | `exercise_set_evaluation_ai`, `exercise_summary_ai`, `insights_core`, `services`, `workout_summary_ai`, `workouts_analysis_core` |
| `exercise_set_evaluation_ai.py` | 255 | IA: avaliação de uma série (modo treino) | `build_prompt`, `call_openai`, `parse_evaluation_response`, `parse_suggestion`, `evaluate_set`, `load_progressao_rag_text`, `SET_EVALUATION_CATEGORIES` | `rag_storage` |
| `exercise_summary_ai.py` | 126 | IA: resumo de fim de exercício | `build_prompt`, `call_openai`, `summarize_exercise` | — |
| `workout_summary_ai.py` | 129 | IA: resumo de fim de treino (persistido) | `build_prompt`, `call_openai`, `generate_workout_summary` | — |
| `insights_ai.py` | 99 | IA: insight livre `/insights` | `build_prompt`, `call_openai`, `generate_insight` | `workout_plan_ai.load_rag_text` |
| `insights_core.py` | 38 | Dispatch do job `insights` | `dispatch_insights`, `JOB_TYPE_INSIGHTS` | `insights_ai` |
| `workouts_analysis.py` | 47 | Router `/workouts-analysis/generate` (202) | handler | `async_jobs`, `models`, `security`, `workouts_analysis_core` |
| `workouts_analysis_core.py` | 48 | Dispatch do job `workouts_analysis` | `dispatch_workouts_analysis` | `report_ai`, `intelligence.dashboard_aggregator_service`, `intelligence.report_data_service` |
| `report_ai.py` | 242 | Prompts OpenAI das 5 análises do relatório | `analyze_training_evolution`, `analyze_body_composition`, `analyze_cardio`, `analyze_volume_distribution`, `analyze_overall_evolution`, `_COMMON_RULES`, `_rag_section`, `ReportGenerationError` | `openai_client` |
| `reports.py` | 263 | Router `/reports` | `_ensure_schema`, `generate_report`, `list_reports`, `get_report`, `get_report_pdf`, `delete_report` | `async_jobs`, `db`, `reports_core`, `security` |
| `reports_core.py` | 204 | Geração do relatório (dados + 5 IA + PDF + S3) | `run_report_generation`, `put_report_pdf`, `get_report_pdf_bytes`, `delete_report_pdf`, `report_pdf_key` | `report_ai`, `db`, `intelligence.report_data_service`, `report_pdf_builder` |
| `report_pdf_builder.py` | 365 | Gráficos matplotlib + Jinja2 + WeasyPrint | `ReportPdfBuilder.build`, `_line_chart`, `_horizontal_bar_chart`, `_build_*_chart(s)`, `_markdown_to_html`, `_split_inline_list_items` | `templates/report.html` |
| `rag_chunking.py` | 690 | RAG seletivo por grupo muscular (chunks `## PREFIXO-NNN — título`) | `select_rag_text`, `parse_chunks`, `select_chunks_for_groups`, `select_table_rows_for_groups`, `discover_additional_files`, `discover_training_type_files`, `list_training_type_rag_folders`, `load_rag_folder_text`, constantes `MUSCLE_GROUP_ROOTS`, `FILE_TOPIC_ROOTS`, `KNOWN_FILES`, `EXCLUDED_FILES` | `rag_storage` |
| `rag_storage.py` | 192 | Sincroniza `rag/markdown` de S3 para `RAG_MARKDOWN_DIR` (uma vez por container) | `ensure_rag_local`, `_sync_from_s3` | boto3 |
| `intervals_client.py` | 140 | Cliente HTTP Intervals.icu (Basic Auth) | `fetch_activities`, `fetch_wellness`, `fetch_activities_and_wellness`, `IntervalsClientError` | urllib |
| `intervals_integration.py` | 1180 | Router `/integrations/intervals` (cache MySQL + sync + dashboards ao vivo) | `_ensure_schema`, `_upsert_activities`, `_upsert_wellness`, `_get_user_intervals_credentials`, `_fetch_live_activities_and_wellness`, `_derive_form_tsb`, agregadores `_daily_activity`, `_wellness_trend`, `_recent_activities`, `_group_by_day`, `_group_by_type`, classificadores `_is_strength_activity`/`_is_cardio_activity` | `intervals_client`, `crypto_utils`, `db`, `security` |
| `gyms.py` | 1443 | Router gyms/equipment/gym-equipment/gym-equipment-catalog; fotos em S3 + CloudFront | `_require_identity`, `_require_admin_identity`, `_save_photo`, `_read_photo`, `_delete_photo`, `_invalidate_cloudfront_paths`, `_assert_gym_writable`, `_assert_gym_equipment_catalog_writable`, handlers | `security`, `db`, `gym_equipment_sync` |
| `gym_equipment_sync.py` | 82 | Recalcula cache `gym_equipment` a partir de `gym_equipment_catalog` × `equipment_exercises` | `sync_gym_equipment_for_gym`, `sync_gym_equipment_for_equipment` | — |
| `muscle_group_equipment.py` | 169 | Router `/muscle-groups/{id}/equipment...` (leitura via view `vw_muscle_group_equipment_exercises`) | 3 handlers | `db`, `security` |
| `muscle_groups_mapped.py` | 346 | Router `/analytics/muscle-groups-mapped-*` (agregação Python por grupo canônico) | `load_rows`, `aggregate`, `metric_value`, `period_label`, `schema` | `exercise_mapping_canonical`, `security` |
| `exercise_mapping_canonical.py` | 546 | Router `/mapping-canonical`; matching canônico de nomes; reclassificação | `fix_text`, `fix_obj`, `normalize`, `q`, `query`, `execute`, `ensure_canonical_columns`, `load_mapping_rows`, `match_mapping`, `reclassify_exercises`, `CanonicalAssociationRequest` | `db.get_conn` (local), `security` |
| `exercise_mapping_catalog.py` | 1314 | Routers `/exercise-mapping-catalog`, `/muscle-groups`, `/muscle-contributions` | `_clean_item`, `_upsert_item` (legado `exercise_zepp_mappings`), `_write_catalog_item`, `_select_catalog_item`, `_upsert_role_contribution`, `_upsert_amazfit_external_mapping`, `_find_or_create_muscle_group`, handlers | `security`, `db` |
| `exercise_catalog_admin.py` | 838 | Routers `/exercises` (catálogo admin), `/equipment-types`, `/exercise-external-mappings` | handlers CRUD | `security`, `db` |
| `muscle_map.py` | 60 | PNG de mapa corporal (mapa v1) | `render_muscle_map_png` | `body_map_render` |
| `body_map_render.py` | 389 | Mapa corporal v1 (arte + máscaras por slug, só Pillow) | `render_body_map`, `fit_body_map`, `RegionStyle`, `PRIMARY_STYLE`, `SECONDARY_STYLE`, `styles_for_roles`, `load_body_map_assets` | PIL |
| `body_composition.py` | 140 | Router `/body-composition` | handlers | `db`, `models`, `security`, `services.upsert_body_composition` |
| `sleep_metrics.py` | 278 | Router `/sleep-metrics` | `_ensure_schema`, handlers | `db`, `security` |
| `manual_effort_metrics.py` | 265 | Router `/manual-effort-metrics` | `_ensure_schema`, handlers | `db`, `security` |
| `workout_sessions.py` | 594 | Router de sessão ao vivo (`/workout-sessions/*`) | `_ensure_schema`, `SessionStartIn`, `SessionExerciseIn`, `SessionSetIn`, `SessionFinishIn`, `SessionMetaIn`, `_get_live_session`, `_get_finishable_activity`, `_refresh_exercise_aggregates`, `_refresh_activity_avg_rpe`, handlers | `db`, `security`, `services` |
| `intelligence/__init__.py` | 23 | Re-exporta os services | — | todos os services |
| `intelligence/base.py` | 93 | `IntelligenceServiceBase`: `fetch_all`, `fetch_one`, `get_latest_intervals_wellness`, `_admin_user_id`, `_jsonable`, `clamp` | — | `db.get_conn` (local) |
| `intelligence/volume_service.py` | 2790 | Motor único de volume/ativação/dose por sessão | `VolumeService` (≈20 métodos), `classify_weekly_volume_status`, `sum_projected_sets_from_pending_slots`, constantes | `base`; locais `muscle_recovery_service`, `muscle_contribution_resolver`, `training_types`, `services` |
| `intelligence/muscle_recovery_service.py` | 691 | Modelo de recuperação v2 ("fullbody-no-reps-v1") | `MuscleRecoveryService` (funções puras + `get_recovery_overview`, `get_consecutive_day_recovery_checks`, `get_global_readiness_adjustment`) | `base`, `volume_service` |
| `intelligence/progression_service.py` | 318 | Classificação de tendência (e1RM) + queries de progressão histórica | `classify_progression_trend`, `ProgressionService.get_recent_progression`, `get_recent_sessions` | `base` |
| `intelligence/body_composition_trend_service.py` | 77 | Bioimpedância: tendência | `BodyCompositionTrendService.get_body_trend` | `base`, `normalization_service` |
| `intelligence/training_load_service.py` | 134 | Resumo semanal | `TrainingLoadService.get_weekly_summary` | `base`, `load_conventions`, `set_types` |
| `intelligence/cardio_context_service.py` | 266 | Contexto agregado do Agente de Cardio | `CardioAgentContextService.build_context` + `_get_*` | `base`, `body_composition_trend_service`, `muscle_recovery_service`, `training_load_service`; local `cardio_recommendations._ensure_schema` |
| `intelligence/dashboard_aggregator_service.py` | 139 | Payloads `/dashboard-home` e `/training-evolution` | `DashboardAggregatorService.build_home_payload`, `build_training_evolution_payload`, `_compute_readiness` | `analytics.compute_rolling_weekly_trend`, demais services |
| `intelligence/dashboard_intelligence_layer.py` | 14 | Fachada sobre o aggregator | `DashboardIntelligenceLayer` | `dashboard_aggregator_service` |
| `intelligence/dashboard_alert_service.py` | 435 | 5 tipos de alerta do dashboard | `DashboardAlertService.get_alerts`, `_check_volume_alerts`, `_check_fatigue_alerts`, `_check_stagnation_alerts`, `_check_asymmetry_alert` | `base`, `progression_service`, `volume_service`; local `training_types` |
| `intelligence/exercise_recommendation_service.py` | 28 | Foco por menor volume; alternativas | `recommend_focus_groups`, `recommend_alternatives` | `exercise_similarity_service`, `volume_service` |
| `intelligence/exercise_similarity_service.py` | 35 | Busca por similaridade em `exercise_mappings` (legada) | `find_similar_exercises` | `base` |
| `intelligence/intervals_correlation_service.py` | 248 | Correlação musculação × `intervals_activities`/`intervals_wellness` | `get_training_correlation`, `_match_activity_for_day` | `base` |
| `intelligence/muscle_contribution_resolver.py` | 100 | Resolução de contribuições por `mapping_id` e acúmulo `sets × stimulus_weight` | `_resolve_muscle_contributions_for_mappings`, `_accumulate_exercise_contribution` | local `volume_service` |
| `intelligence/normalization_service.py` | 74 | Normalização de texto (mojibake), percentuais e JSON | `NormalizationService` | — |
| `intelligence/report_data_service.py` | 77 | Payload do relatório + contexto RAG | `ReportDataService.build_report_payload`, `build_rag_context`, `CARDIO_CONTEXT_MAX_DAYS=30` | `rag_chunking.select_rag_text`, `body_composition_trend_service`, `cardio_context_service`, `dashboard_aggregator_service` |
| `intelligence/candidate_exercise_provider.py` | 91 | Matriz de candidatos por grupo (motor v2, Fase 3) | `build_candidate_matrix`, `_candidates_for_group` | local `muscle_contribution_resolver` |
| `intelligence/template_ai_selector_v2.py` | 169 | Prompt/parsing da IA do motor v2 (Fase 4) | `build_v2_selection_prompt`, `parse_v2_selection_response`, `select_exercises_for_slot_v2` | local `workout_plan_ai` (`call_openai`, `_extract_json`) |
| `intelligence/template_volume_domain.py` | 264 | Dataclasses do motor v2 (Fase 1) | `WeeklyMuscleTarget`, `SlotMuscleAllocation`, `MuscleActivationVector`, `MuscleSlotBudget`, `GenerationVolumeSummary`, `TemplateCycleContext`, `UNDER/OVER_ALLOCATION_THRESHOLD_PCT` | — |
| `intelligence/template_volume_planner.py` | 318 | Planner de orçamento (Fase 2) | `build_template_cycle_context`, `build_template_volume_plan`, `validate_template_volume_coverage`, `TemplateVolumeEngineNotApplicable`, `_load_projected_recovery`, `_recovery_state_from_projection` | `template_volume_domain`; locais `db`, `workout_plan_ai`, `muscle_recovery_service`, `training_types` |
| `intelligence/template_volume_validator.py` | 799 | Validação/reparo determinístico (Fase 5) | `calculate_slot_activation`, `detect_deviations`, `repair_slot_exercises`, `split_concentrated_exercises`, `enforce_minimum_sets_floor`, `order_exercises_for_session`, `greedy_deterministic_selection`, `_balanced_set_distribution`, `_split_evenly`, `_stimulus_weight_for_group` | `template_volume_domain`; local `volume_service` |
| `templates/report.html` | — | Template Jinja2 do PDF | ver seção 11 | — |

#### 1.3 Inclusão de routers em `main.py`

Ordem real de inclusão e proteção por `try/except`:

| Ordem | Router(s) | Origem | Protegido por `try/except Exception` | Logger em falha |
|---|---|---|---|---|
| 1 | `intervals_integration.router` | `app.intervals_integration` | sim | `fitness.intervals` |
| 2 | `exercise_mapping_catalog.router`, `muscle_groups_router`, `muscle_contributions_router` | `app.exercise_mapping_catalog` | sim | `fitness.exercise_mapping_catalog` |
| 3 | `exercise_catalog_admin.router`, `equipment_types_router`, `external_mappings_router` | `app.exercise_catalog_admin` | sim | `fitness.exercise_catalog_admin` |
| 4 | `muscle_groups_mapped.router` | `app.muscle_groups_mapped` | **não** (import direto) | — |
| 5 | `exercise_mapping_canonical.router` | `app.exercise_mapping_canonical` | **não** | — |
| 6 | `body_composition.router` | sim | `fitness.body_composition` |
| 7 | `sleep_metrics.router` | sim | `fitness.sleep_metrics` |
| 8 | `manual_effort_metrics.router` | sim | `fitness.manual_effort_metrics` |
| 9 | `cardio_recommendations.router` | sim | `fitness.cardio_recommendations` |
| 10 | `gyms.router` | sim | `fitness.gyms` |
| 11 | `muscle_group_equipment.router` | sim | `fitness.muscle_group_equipment` |
| 12 | `workout_plans.router` | sim | `fitness.workout_plans` |
| 13 | `jobs.router` | sim | `fitness.jobs` |
| 14 | `workout_plan_templates.me_router` (antes do router admin/PT por causa de `/{template_id}`) | sim | `fitness.workout_plan_templates` |
| 15 | `workout_plan_templates.router` | sim | `fitness.workout_plan_templates` |
| 16 | `training_types.router` | sim | `fitness.training_types` |
| 17 | `devices.router` | sim | `fitness.devices` |
| 18 | `auth.router` + `@app.on_event("startup") _seed_admin_user` (chama `ensure_admin_seed`) | sim | `fitness.auth` |
| 19 | `users.router` + `@app.on_event("startup") _migrate_plaintext_intervals_keys` | sim | `fitness.users` |
| 20 | `reports.router` | sim | `fitness.reports` |
| 21 | `workouts_analysis.router` | sim | `fitness.workouts_analysis` |
| 22 | `workout_sessions.router` | sim | `fitness.workout_sessions` |

Em cada bloco protegido a falha é registrada com `logging.getLogger(...).exception("... router não foi carregado. ... Erro: %s", exc)` e o app segue sem aquele router. Os imports nesses blocos usam o prefixo absoluto `app.<módulo>`; o restante de `main.py` usa imports relativos (`from .services import ...`).

`main.py` contém um comentário indicando que `analytics`, `dashboard_modules`, `dashboard_intelligence` e os endpoints `GET /exercises/suggested-progression` e `GET /exercises/suggested-set-count-for-substitution` foram movidos para `main_engines.py`; nenhum deles é incluído no app core.

---

### 2. Contrato HTTP completo

Contagem de decorators de rota por arquivo (grep `@router.|@app.|@me_router.|@muscle_groups_router.|@muscle_contributions_router.|@equipment_types_router.|@external_mappings_router.`): total **218**.

| Arquivo | Rotas | Arquivo | Rotas |
|---|---|---|---|
| `analytics.py` | 27 | `gyms.py` | 27 |
| `main.py` | 19 | `exercise_mapping_catalog.py` | 18 |
| `exercise_catalog_admin.py` | 17 | `workout_plan_templates.py` | 13 |
| `intervals_integration.py` | 10 | `users.py` | 9 |
| `training_types.py` | 8 | `dashboard_modules.py` | 7 |
| `workout_plans.py` | 7 | `body_composition.py` | 5 |
| `cardio_recommendations.py` | 5 | `manual_effort_metrics.py` | 5 |
| `reports.py` | 5 | `sleep_metrics.py` | 5 |
| `workout_sessions.py` | 5 | `auth.py` | 4 |
| `muscle_groups_mapped.py` | 4 | `dashboard_intelligence.py` | 3 |
| `devices.py` | 3 | `exercise_mapping_canonical.py` | 3 |
| `muscle_group_equipment.py` | 3 | `engines_extra.py` | 2 |
| `main_engines.py` | 2 | `jobs.py` | 1 |
| `workouts_analysis.py` | 1 | | |

Distribuição por Lambda HTTP: `musclefit-engines` = `analytics` (27) + `dashboard_modules` (7) + `dashboard_intelligence` (3) + `engines_extra` (2) + `main_engines` (2) = **41**; `musclefit-api-core` = **177**.

Legenda de autenticação usada abaixo:

- `ID` = `Depends(get_current_identity)` (header `X-API-Token` OU cookie `musclefit_session`; resolve para `Identity`).
- `ADMIN` = `Depends(require_admin)`.
- `ADMIN|PT` = `Depends(require_admin_or_pt)`.
- `MASTER` = `Depends(require_master_token)` (somente token mestre).
- `mID` = resolução manual `_require_identity(x_api_token, musclefit_session)` (mesma lógica de `resolve_identity`, mas os parâmetros são declarados como `Header`/`Cookie` na assinatura e a checagem é chamada no corpo).
- `mADMIN` = `_require_admin_identity(...)` manual.
- `SESSION` = `Depends(require_session_identity)` (exige `identity.source == "session"`).

#### 2.1 `main.py` (`musclefit-api-core`, 19 rotas)

| Método | Path | Query/Path | Corpo | Resposta | Auth | Erros | Função |
|---|---|---|---|---|---|---|---|
| GET | `/health` | — | — | `{"status":"ok"}` | nenhuma | — | `health` |
| GET | `/ready` | — | — | `{"status":"ready","db":bool}` (executa `SELECT 1 AS ok`) | nenhuma | exceção do Data API propaga (500) | `ready` |
| POST | `/activities` | — | `ActivityIn` | `{"status":"ok","activity_id","external_id","exercises","sets"}` | ID | — | `create_or_replace_activity` → `services.upsert_activity(payload, user_id)` |
| GET | `/activities` | `limit` (1–200, def 10), `offset` (≥0), `date_from`, `date_to`, `gym_id`, `conducted_by`, `q`, `session_status` | — | `{"items":[...]}` | ID | — | `list_activities` → `latest_activities` |
| GET | `/activities/{activity_id}` | path int | — | dict da atividade com `exercises[].sets[]` | ID | 404 "Atividade não encontrada." | `get_activity` → `activity_detail` |
| PUT | `/activities/{activity_id}` | path | `ActivityIn` | `{"status":"ok", ...upsert}` | ID | 404 | `update_activity_by_id` → `update_activity` |
| DELETE | `/activities/{activity_id}` | path | — | `{"status":"ok","activity_id"}` | ID | 404 | `delete_activity_by_id` |
| GET | `/exercises/history` | `name` (obrig.), `limit` (1–200, def 30) | — | `{"items":[...]}` | ID | — | `exercise_history` |
| GET | `/exercises/last-session-sets` | `mapping_id` (obrig.), `exclude_activity_id` (obrig.), `equipment_id` | — | `{"item": {activity_id, local_date, sets[], record_scope, record: {reps, weight_kg} \| null, e1rm_kg} \| null}` | ID | — | `get_last_session_sets` (recorde/e1RM: `domain/equipment_record.py`) |
| GET | `/exercises/logs` | `limit` (1–500, def 100), `date_from`, `date_to` | — | `{"items":[...]}` | ID | — | `list_exercise_logs` |
| PUT | `/exercises/logs/{exercise_id}` | path | `ExerciseLogUpdateIn` (adiciona `workout_plan_exercise_id_provided` via `model_fields_set`) | `{"status":"ok","exercise_id","activity_id","sets"}` | ID | 404 "Exercício não encontrado." | `update_exercise_log` |
| DELETE | `/exercises/logs/{exercise_id}` | path | — | `{"status":"ok","exercise_id","activity_id","deleted_activity":false}` | ID | 404 | `delete_exercise_log` |
| GET | `/sets/high-rpe` | `min_rpe` (float, def 9.0), `limit` (1–200, def 50) | — | `{"items":[...]}` | ID | — | `high_rpe_sets` |
| POST | `/exercise-mappings` | — | `ExerciseMappingIn` | `{"status":"ok","id"}` | ADMIN (via `dependencies=[Depends(require_admin)]`) | — | `upsert_mapping` |
| POST | `/insights/generate` | — | `InsightsGenerateIn` | 202 `{"job_id","status":"pending"}` | ID | — | cria job `insights`, `invoke_async("WORKOUT_AI_FUNCTION_NAME")` |
| POST | `/exercises/evaluate-set` | — | `SetEvaluationIn` | 202 `{"job_id","status":"pending"}` | ID | — | job `workout-ai-evaluate-set` |
| POST | `/exercises/{exercise_id}/summarize` | path | — | 202 | ID | — | job `workout-ai-exercise-summary` |
| POST | `/activities/{activity_id}/ai-summary/generate` | path | — | 202 | ID | — | job `workout-ai-activity-summary` |

#### 2.2 `auth.py` (prefixo `/auth`, 4 rotas)

| Método | Path | Corpo | Resposta | Auth | Erros | Função |
|---|---|---|---|---|---|---|
| POST | `/auth/login` | `LoginIn {username, password}` | `{"user": {id, username, full_name, role, must_change_password, intervals_configured}}` + `Set-Cookie musclefit_session` | nenhuma | 401 "Usuário ou senha inválidos." (user inexistente, inativo, senha errada); 423 "Conta temporariamente bloqueada..." (`locked_until > now`); 423 "Conta bloqueada por excesso de tentativas. Tente novamente em 15 minutos." (5ª falha) | `login` |
| POST | `/auth/logout` | — (header `X-API-Token` opcional ou cookie) | `{"status":"ok"}` + cookie apagado | token cru do header ou cookie; 401 se ausente | 401 "Token inválido." | `logout` (revoga `user_sessions.revoked_at` por `token_hash`) |
| GET | `/auth/me` | — | `_public_user` + `source` | ID | 404 "Usuário não encontrado." | `me` |
| POST | `/auth/change-password` | `ChangePasswordIn {current_password, new_password (min 8)}` | `{"status":"ok"}` | ID e `identity.source == "session"` | 403 (source ≠ session), 404, 401 "Senha atual incorreta." | `change_password` (seta `must_change_password=0`) |

#### 2.3 `users.py` (prefixo `/users`, 9 rotas)

| Método | Path | Query/Path | Corpo (dict livre) | Resposta | Auth | Erros |
|---|---|---|---|---|---|---|
| GET | `/users` | — | — | `{"items":[_public_row]}` | ADMIN | — |
| POST | `/users` | — | `username` (obrig.), `password` (≥8), `role` ∈ {admin, regular} (def regular), `full_name`, `email`, `photo_path`, `birth_date`, `sex`, `intervals_athlete_id`, `is_advanced_user`, `intervals_api_key` (criptografada com Fernet) | `{"status":"ok","item"}` | ADMIN | 400 (username/password/role/email), 409 "username já está em uso." |
| GET | `/users/me` | — | — | `{"item"}` | SESSION | 404 |
| PUT | `/users/me` | — | `full_name, email, photo_path, birth_date, sex, intervals_athlete_id, is_advanced_user, intervals_api_key` (todos via `COALESCE`) | `{"status":"ok","item"}` | SESSION | 400 email, 404 |
| PUT | `/users/{user_id}` | path | acima + `role`, `is_active` | `{"status":"ok","item"}` | ADMIN | 400 role/email, 404 |
| DELETE | `/users/{user_id}` | path | — | `{"status":"ok","id","is_active":false}` (soft: `is_active=0`) | ADMIN | 400 "Você não pode desativar a própria conta.", 404 |
| POST | `/users/{user_id}/photo` | path | multipart `file` | `{"status":"ok","item"}` | ID + `_require_self_or_admin` | 403, 404, 400 (formato/tamanho via `_save_photo`) |
| GET | `/users/{user_id}/photo` | path | — | bytes da imagem (S3) | ID + self/admin | 403, 404 "Este usuário não possui foto." |
| DELETE | `/users/{user_id}/photo` | path | — | `{"status":"ok","id","has_photo":false}` | ID + self/admin | 403, 404 |

`_public_row` remove `password_hash` e `intervals_api_key_encrypted`, converte `photo_path` em `has_photo`, força `is_advanced_user` para bool e adiciona `intervals_configured`.

#### 2.4 `devices.py` (3 rotas, router com `dependencies=[Depends(require_master_token)]`)

| Método | Path | Query | Corpo | Resposta | Erros |
|---|---|---|---|---|---|
| POST | `/devices/pair` | — | `DevicePairIn {device_name: str, platform: str = "zepp"}` | `{"status":"ok","device_id","device_name","platform","device_token"}` (token cru retornado uma única vez) | — |
| GET | `/devices` | `include_revoked: bool = False` | — | `{"items":[id, device_name, platform, paired_at, last_seen_at, revoked_at, is_active]}` | — |
| DELETE | `/devices/{device_id}` | path | — | `{"status":"ok","device_id","revoked":true}` | 404 "Dispositivo não encontrado ou já revogado." |

Cada handler chama `_ensure_schema()` (CREATE TABLE IF NOT EXISTS `devices`, ADD COLUMN `user_id`, backfill para admin e `MODIFY user_id NOT NULL` quando não há nulos).

#### 2.5 `jobs.py` (1 rota)

| Método | Path | Resposta | Auth | Erros |
|---|---|---|---|---|
| GET | `/jobs/{job_id}` | `{job_id, job_type, status, created_at, finished_at, result?, error?}` | ID (no router e no handler) | 404 "Job não encontrado (ou expirado - jobs somem após algumas horas)." |

Não há verificação de que o job pertence ao `user_id` da identidade (o `user_id` fica só em `request`).

#### 2.6 `body_composition.py` (prefixo `/body-composition`, 5 rotas)

| Método | Path | Query | Corpo | Resposta | Auth | Erros |
|---|---|---|---|---|---|---|
| POST | `` | — | `BodyCompositionIn` | `{"status":"ok","id": lastrowid}` (upsert `ON DUPLICATE KEY` por chave única, não verificada aqui — presumivelmente `(user_id, local_date)`) | ID | — |
| GET | `` | `limit` (int, def 200, cap 500) | — | `{"items":[...]}` filtrado por `user_id` | ID | — |
| GET | `/{entry_id}` | path | — | row | ID | 404 |
| PUT | `/{entry_id}` | path | dict com campos permitidos (`local_date, measured_at, weight_kg, bmi, body_fat_percent, fat_mass_kg, fat_free_mass_kg, muscle_mass_kg, skeletal_muscle_percent, body_water_percent, visceral_fat_rating, bone_mass_kg, bmr_kcal, subcutaneous_fat_percent, protein_percent, metabolic_age, source, notes`) | `{"status":"ok","id"}` | ID | 400 "Nenhum campo válido para atualizar.", 404, 500 |
| DELETE | `/{entry_id}` | path | — | `{"status":"deleted","id"}` | ID | 404, 500 |

#### 2.7 `sleep_metrics.py` (prefixo `/sleep-metrics`, 5 rotas) e `manual_effort_metrics.py` (prefixo `/manual-effort-metrics`, 5 rotas)

Mesma estrutura nos dois módulos:

| Método | Path | Query | Corpo | Resposta | Auth | Erros |
|---|---|---|---|---|---|---|
| POST | `` | — | dict; `local_date` obrigatório; campos permitidos: sleep = `local_date, duration_hours, regularity_percent, deep_sleep_hours, rem_sleep_hours, awake_count, awake_hours, notes`; effort = `local_date, epoc_training_load, pai_score, notes` | `{"status":"ok","id"}` (upsert `ON DUPLICATE KEY UPDATE` por `(user_id, local_date)`) | ID | 400, 500 |
| GET | `` | `days` (1–3650, opcional), `limit` (1–1000, def 200) | — | `{"items"}` | ID | — |
| GET | `/{entry_id}` | path | — | row | ID | 404 |
| PUT | `/{entry_id}` | path | dict | `{"status":"ok","id"}` | ID | 400, 404, 500 |
| DELETE | `/{entry_id}` | path | — | `{"status":"deleted","id"}` | ID | 404, 500 |

`_ensure_schema` em ambos: CREATE TABLE IF NOT EXISTS, ADD COLUMN `user_id`, backfill para admin, `MODIFY user_id NOT NULL`, `DROP INDEX local_date`, `ADD UNIQUE KEY uq_<tabela>_user_date (user_id, local_date)`.

#### 2.8 `cardio_recommendations.py` (prefixo `/cardio-recommendations`, 5 rotas)

| Método | Path | Query | Corpo | Resposta | Auth | Erros |
|---|---|---|---|---|---|---|
| POST | `` | — | dict: `reference_date` (obrig.), `recommendation_markdown` (obrig.), `workout_plan_id`, `model`, `context_snapshot` | `{"status":"ok","id"}` | ID | 400, 500 |
| POST | `/generate` | — | dict opcional: `days` (def 14), `proposed_workout`, `reference_date`, `reference_time`, `workout_plan_id` | 202 `{"job_id","status":"pending"}` | ID | — |
| GET | `` | `days` (1–3650), `limit` (1–1000, def 200), `workout_plan_id` | — | `{"items"}` | ID | — |
| GET | `/{entry_id}` | path | — | row | ID | 404 |
| DELETE | `/{entry_id}` | path | — | `{"status":"deleted","id"}` | ID | 404, 500 |

#### 2.9 `workout_plans.py` (7 rotas, router com `dependencies=[Depends(get_current_identity)]`)

| Método | Path | Query | Corpo | Resposta | Erros |
|---|---|---|---|---|---|
| GET | `/workout-plans` | `status`, `limit` (1–200, def 20), `has_advanced_technique: bool` | — | `{"items"}` | — |
| GET | `/workout-plans/{plan_id}` | path | — | plano com `exercises[]` enriquecidos com progressão | 404 |
| POST | `/workout-plans` | — | `WorkoutPlanIn` | plano criado (`get_workout_plan`) | — |
| PUT | `/workout-plans/{plan_id}` | path | `WorkoutPlanIn` | plano atualizado | 404 |
| DELETE | `/workout-plans/{plan_id}` | path | — | `{"status":"ok","plan_id"}` | 404 |
| GET | `/workout-plans/{plan_id}/muscle-map.png` | path | — | `image/png` | 404 |
| POST | `/workout-plans/generate` | — | `WorkoutPlanGenerateIn` | 202 `{"job_id","status":"pending"}` (job_type `plan-generator`, `invoke_async("PLAN_GENERATOR_FUNCTION_NAME")`) | — |

#### 2.10 `workout_plan_templates.py` (13 rotas)

`me_router` (prefixo `/workout-plan-templates`, sem guard extra):

| Método | Path | Resposta | Auth |
|---|---|---|---|
| GET | `/workout-plan-templates/assigned-to-me` | `{"items":[{id,name,description,template_type,slots:[{sequence,name,slot_type}]}]}` | ID |

`router` (prefixo `/workout-plan-templates`, `dependencies=[Depends(require_admin_or_pt)]`):

| Método | Path | Query/Path | Corpo | Resposta | Erros |
|---|---|---|---|---|---|
| GET | `` | `is_active: bool?` | — | `{"items"}` (com `assignments_count`, `created_by_username`) | — |
| GET | `/users-lookup` | `search?` | — | `{"items":[id, username, full_name]}` (`is_active=1`, LIMIT 50) | — |
| GET | `/{template_id}` | path | — | template completo (slots + items + assignments) | 404 |
| POST | `` | — | `WorkoutPlanTemplateIn` | template + `volume_coverage_warnings` | 422 (validações) |
| POST | `/{template_id}/duplicate` | path | — | cópia (`"{name} (cópia)"`, `is_active=True`) | 404 |
| PUT | `/{template_id}` | path | `WorkoutPlanTemplateIn` | template | 404, 422 |
| PATCH | `/{template_id}/status` | path | `{"is_active": bool}` | template | 404 |
| DELETE | `/{template_id}` | path | — | `{"status":"ok","template_id"}` | 404 |
| GET | `/{template_id}/assignments` | path | — | `{"items"}` | 404 |
| POST | `/{template_id}/assignments` | path | `WorkoutPlanTemplateAssignIn {user_id}` | lista de assignments | 404 (template/usuário), 409 "Este modelo já está atribuído a este usuário." |
| DELETE | `/{template_id}/assignments/{user_id}` | path | — | `{"status":"ok",...}` | 404 |
| POST | `/preview-activation` | — | `WorkoutPlanTemplatePreviewIn {slots, volume_mode}` | `{"cycle_length","days":[{day, slot_sequence, slot_name, slot_type, groups[]}]}` (7 dias, slot = `cycle[(day-1) % cycle_length]`) | — |

Validações de `_validate_template_payload` (422): ≥1 slot; `sequence` únicos 1..N; faixa de prioridade do slot ambos ou nenhum, 0–100, min ≤ max; slot `descanso` só aceita `cardio`; máx. 3 `prioritize` por slot; campos de técnica avançada/warmup/feeler/pico só em `item_kind='equipment'`; `muscle_group` exige `muscle_group_id` e proíbe `mapping_id`, proibido em `volume_mode='equipment_series'`, `priority_min/max_pct` 0–100 e min ≤ max; `exercise` rejeitado; `equipment` só em `equipment_series`, exige `muscle_group_id`, `equipment_id`, `mapping_id`, `sets_count ≥ 1`; `cardio` proíbe `muscle_group_id`/`mapping_id` e exige `cardio_duration_minutes > 0`; outros `item_kind` → 422.

#### 2.11 `training_types.py` (prefixo `/training-types`, 8 rotas, `dependencies=[Depends(require_admin_or_pt)]`)

| Método | Path | Query/Path | Corpo | Resposta | Erros |
|---|---|---|---|---|---|
| GET | `` | `is_active?` | — | `{"items"}` (com `muscle_groups_count`) | — |
| GET | `/rag-folders` | — | — | `{"items":[nomes de subpastas de TIPOS_TREINO]}` | — |
| GET | `/{training_type_id}` | path | — | tipo + `targets[]` | 404 |
| POST | `` | — | `TrainingTypeIn` | tipo | 422 (`rag_folder` inválido; grupo duplicado; `0 <= min <= target <= max`) |
| POST | `/{training_type_id}/duplicate` | path | — | cópia | 404 |
| PUT | `/{training_type_id}` | path | `TrainingTypeIn` | tipo | 404, 422 |
| PATCH | `/{training_type_id}/status` | path | `{"is_active"}` | tipo | 404 |
| DELETE | `/{training_type_id}` | path | — | `{"status":"ok","training_type_id"}` | 404 |

Targets com `min=target=max=0` não são persistidos.

#### 2.12 `workout_sessions.py` (5 rotas, router `dependencies=[Depends(get_current_identity)]`)

| Método | Path | Corpo | Resposta | Erros |
|---|---|---|---|---|
| POST | `/workout-sessions/start` | `SessionStartIn` | `{"status":"ok","session_id","external_id":"zepp-session-<uuid>","gym_id","training_type_id","started_at"}`; grava `activities.session_status='in_progress'`, `source` tipo `wearable` | 404 plano não pertence ao usuário |
| PATCH | `/workout-sessions/{session_id}/meta` | `SessionMetaIn` (`exclude_unset`) | `{"status":"ok","session_id","updated_fields"}` | 404 |
| POST | `/workout-sessions/{session_id}/exercises` | `SessionExerciseIn` | `{"status":"ok","session_id","exercise_id","sequence","mapping_id","equipment_id","workout_plan_exercise_id"}` | 404, 409 "Este treino já foi finalizado." |
| POST | `/workout-sessions/{session_id}/exercises/{exercise_id}/sets` | `SessionSetIn` | `{"status":"ok","session_id","exercise_id","set_number"}` (recalcula agregados) | 404, 409 |
| POST | `/workout-sessions/{session_id}/finish` | `SessionFinishIn` | `{"status":"ok","session_id","session_status":"done","ended_at","duration_seconds","exercises","workout_plan_marked_done"}` | 404, 409 |

#### 2.13 `reports.py` (prefixo `/reports`, 5 rotas)

| Método | Path | Corpo | Resposta | Auth | Erros |
|---|---|---|---|---|---|
| POST | `/generate` | dict opcional `{"start_date": "AAAA-MM-DD"}` | 202 `{"id","job_id","status":"generating"}` | ID | 400 (`start_date` inválida, no futuro, período fora de 7–365 dias), 500 |
| GET | `` | — | `{"items"}` (campos leves; filtrado por `user_id`) | ID | — |
| GET | `/{report_id}` | — | linha completa (admin vê qualquer; regular só as suas) com `context_snapshot` decodificado | ID | 404 |
| GET | `/{report_id}/pdf` | — | `application/pdf` (`Content-Disposition: attachment; filename="relatorio-{id}.pdf"`) | ID (admin qualquer) | 404 "PDF do relatório não disponível." / "Arquivo do relatório não encontrado." |
| DELETE | `/{report_id}` | — | `{"status":"deleted","id"}` (apaga S3 best-effort) | ID (só dono) | 404, 500 |

#### 2.14 `workouts_analysis.py` (1 rota)

| Método | Path | Corpo | Resposta | Auth |
|---|---|---|---|---|
| POST | `/workouts-analysis/generate` | `WorkoutsAnalysisGenerateIn {days: 7–365, def 90}` ou nulo | 202 `{"job_id","status":"pending"}` (job `workouts_analysis`) | ID |

#### 2.15 `intervals_integration.py` (prefixo `/integrations/intervals`, 10 rotas, todas ID)

| Método | Path | Query | Corpo | Resposta | Erros |
|---|---|---|---|---|---|
| GET | `/health` | — | — | `{"status":"ok","namespace","database":true,"tables",...}` | — |
| GET | `/status` | — | — | `{"configured": bool, "message"}` | — |
| POST | `/sync` | `days` (1–3650, def 90) | — | `{"status":"ok","range","intervals":{activities_received,wellness_received},"fitness_api":{activities,wellness}}` | 400 sem credenciais; 500 falha de descriptografia; status do Intervals (401) ou 502 |
| POST | `/activities` | — | lista ou dict com `items/activities/data` | `{"status":"ok","received","upserted","skipped"}` | — |
| POST | `/wellness` | — | lista ou dict com `items/wellness/data` | idem | — |
| GET | `/summary` | `days` | — | `{"days","activities":{...},"wellness":{...},"latest_sync"}` | — |
| GET | `/activities` | `days`, `limit` (1–1000, def 100) | — | `{"items"}` | — |
| GET | `/wellness` | `days`, `limit` | — | `{"items"}` | — |
| GET | `/dashboard` | `days` | — | atividades ao vivo do Intervals (também gravadas no cache `intervals_activities`, M53); `wellness_trend[]` lido do cache `intervals_wellness` após refresh curto (≤7 dias) e throttled (5 min) do usuário: `range, activity_count, wellness_count, wellness_source, wellness_refresh, activities_cache, daily_activity[], wellness_trend[], recent_activities[]` | como `/sync` |
| GET | `/cardio-dashboard` | `days` | — | `{"days","range","source":"intervals_icu_direct","token_used","classification","totals","daily","by_type","wellness","cardio_activities","excluded_strength_activities","ignored_activities"}` | como `/sync` |

#### 2.16 `gyms.py` (24 rotas, auth manual)

| Método | Path | Query | Corpo (dict) | Resposta | Auth | Erros |
|---|---|---|---|---|---|---|
| GET | `/gyms` | `active_only` (def true) | — | `{"items"}` (sem filtro por dono) | mID | — |
| GET | `/gyms/{gym_id}` | — | — | `{"item"}` | mID | 404 |
| POST | `/gyms` | — | `name` (obrig.), `is_recurring` (def true), `usual_weekdays` (JSON), `notes` | `{"status":"ok","item"}` (`INSERT ... ON DUPLICATE KEY UPDATE` por nome; grava `owner_user_id`) | mID | 400 |
| PUT | `/gyms/{gym_id}` | — | `name`, `opens_at`, `closes_at` (admin-only, `HH:MM[:SS]`), `usual_weekdays`, `is_recurring`, `notes`, `is_active` | `{"status":"ok","item"}` | mID; mADMIN se houver campo admin-only; `_assert_gym_writable` (dono ou admin; 404 caso contrário) | 400 formato hora, 404 |
| DELETE | `/gyms/{gym_id}` | — | — | `{"status":"ok","id","is_active":false}` | mID + `_assert_gym_writable` | 404 |
| POST | `/gyms/{gym_id}/logo` | — | multipart `file` | `{"status":"ok","item"}` | mADMIN | 404, 400 |
| GET | `/gyms/{gym_id}/logo` | — | — | imagem | mID | 404 |
| DELETE | `/gyms/{gym_id}/logo` | — | — | `{"status":"ok","id","has_logo":false}` | mADMIN | 404 |
| GET | `/equipment` | `query`, `active_only` | — | `{"items"}` | mID | — |
| POST | `/equipment` | — | `name` (obrig.), `notes`, `equipment_type_id`, `manufacturer`, `model`, `location` | `{"status":"ok","item"}` (upsert por nome) | mADMIN | 400 |
| PUT | `/equipment/{equipment_id}` | — | idem + `is_active` | `{"status":"ok","item"}` | mADMIN | 404 |
| DELETE | `/equipment/{equipment_id}` | — | — | soft delete | mADMIN | 404 |
| POST | `/equipment/{equipment_id}/photo` | — | multipart | `{"status":"ok","item"}` | mADMIN | 404, 400 |
| GET | `/equipment/{equipment_id}/photo` | — | — | imagem | mID | 404 |
| DELETE | `/equipment/{equipment_id}/photo` | — | — | `{"status":"ok","id","has_photo":false}` | mADMIN | 404 |
| GET | `/gym-equipment` | `gym_id`, `mapping_id`, `primary_group`, `active_only` | — | `{"items"}` (join `exercise_catalog`, `muscle_groups` via `exercise_muscle_contributions`, `equipment`) | mID | — |
| POST | `/gym-equipment` | — | `gym_id`, `mapping_id`, `equipment_id` (obrig.) | `{"status":"ok","item"}` | mID | 400, 404 equipment |
| PUT | `/gym-equipment/{link_id}` | — | `equipment_id`, `is_active` | `{"status":"ok","item"}` | mID | 404 |
| DELETE | `/gym-equipment/{link_id}` | — | — | soft delete | mID | 404 |
| GET | `/gym-equipment/recommend` | `gym_id` (obrig.), `mapping_id`, `primary_group`, `exclude_equipment_id` | — | `{"ok","gym_id","primary_group","excluded_equipment_id","has_equipment_for_group","recommended","alternatives","items"}` | mID | 400 "Informe mapping_id ou primary_group.", 404 mapping, 422 sem grupo |
| GET | `/gym-equipment/recommend-all` | `gym_id` | — | `{"ok","gym_id","groups":{nome:{has_equipment_for_group,recommended,alternatives}},"groups_by_id":{id:{...}}}` | mID | — |
| GET | `/gym-equipment-catalog` | `gym_id`, `equipment_id`, `active_only` | — | `{"items"}` | mID | — |
| POST | `/gym-equipment-catalog` | — | `gym_id`, `equipment_id` | `{"status":"ok","item"}` + `sync_gym_equipment_for_gym` | mID + `_assert_gym_equipment_catalog_writable` (dono ou admin; 403) | 400, 404, 403 |
| DELETE | `/gym-equipment-catalog/{link_id}` | — | — | soft delete + sync | mID + dono/admin | 404, 403 |

#### 2.17 `muscle_group_equipment.py` (prefixo `/muscle-groups`, 3 rotas, ID)

| Método | Path | Resposta | Erros |
|---|---|---|---|
| GET | `/muscle-groups/{muscle_group_id}/equipment` | `{"muscle_group_id","items":[equipment_id, equipment_name, exercise_count, max_stimulus]}` (view, `role='PRIMARY'`) | 404 |
| GET | `/muscle-groups/{muscle_group_id}/equipment/{equipment_id}/exercises` | `{"muscle_group_id","equipment_id","items":[exercise_id, exercise_name, role, stimulus_weight, fatigue_weight]}` | 404 |
| GET | `/muscle-groups/{muscle_group_id}/exercises` | `{"muscle_group_id","items":[exercise_id, exercise_name, max_stimulus, max_fatigue]}` | 404 |

#### 2.18 `exercise_mapping_catalog.py` (17 rotas)

`router` prefixo `/exercise-mapping-catalog`:

| Método | Path | Query/Path | Corpo | Resposta | Auth | Erros |
|---|---|---|---|---|---|---|
| GET | `/health` | — | — | `{"status","read_table":"exercise_catalog","write_table":"exercise_catalog","purpose"}` | mID | — |
| GET | `` | `query`, `primary_group`, `amazfit_name`, `active_only` (def true), `limit` (1–1000, def 100), `offset` | — | `{"query","primary_group","active_only","limit","offset","total","items"}` (lê `exercise_catalog`; `equipment` sempre `None`) | mID | — |
| GET | `/groups` | — | — | `{"items":[primary_group, count]}` | mID | — |
| GET | `/{mapping_id}` | path | — | item (`_select_catalog_item`) | mID | 404 |
| POST | `` | — | dict aceito por `_clean_item` (`exercise_name`, `amazfit_name` obrig.; `equipment`, `body_position`, `movement`, `trajectory`, `biomechanical_pattern`, `primary_joint`, `resistance_vector`, `primary_group`, `secondary_group`, `keywords`, `description`, `mapping_status` def confirmed, `source` def manual, `is_active`) | `{"status":"ok","item"}` (grava em `exercise_catalog` + `exercise_external_mappings` AMAZFIT + `exercise_muscle_contributions` PRIMARY 1.00 / SECONDARY 0.50) | mADMIN | 400 |
| PUT | `/{mapping_id}` | path | idem | `{"status":"ok","item"}` | mADMIN | 404, 400 |
| DELETE | `/{mapping_id}` | path | — | `{"status":"ok","id","is_active":false}` — executa `UPDATE exercise_zepp_mappings SET is_active=0` (tabela legada) | mADMIN | 404 |
| GET | `/export/markdown` | — | — | `{"markdown","count"}` (tabela markdown de até 1000 itens) | mID | — |
| GET | `/{mapping_id}/aliases` | path | — | `{"items":[id, mapping_id(=exercise_id), alias, source, created_at]}` | mID | 404 |
| POST | `/{mapping_id}/aliases` | path | `{"alias","source"}` | `{"status":"ok","item"}` | mADMIN | 400, 404, 409 |
| DELETE | `/aliases/{alias_id}` | path | — | `{"status":"ok","id"}` | mADMIN | 404 |

`muscle_groups_router` prefixo `/muscle-groups`:

| Método | Path | Corpo | Resposta | Auth |
|---|---|---|---|---|
| GET | `` (`query?`) | — | `{"items":[id, name, primary_muscles, secondary_muscles, created_at, updated_at]}` | mID |
| POST | `` | `{"name","primary_muscles","secondary_muscles"}` | `{"status":"ok","item"}` (upsert por nome) | mADMIN |
| PUT | `/{muscle_group_id}` | idem | `{"status":"ok","item"}` | mADMIN (404) |

`muscle_contributions_router` prefixo `/muscle-contributions`:

| Método | Path | Corpo | Resposta | Auth | Erros |
|---|---|---|---|---|---|
| GET | `/{mapping_id}` | — | `{"items":[id, exercise_id, muscle_group_id, muscle_group_name, role, stimulus_weight, fatigue_weight, is_active]}` | mID | 404 |
| PUT | `/{mapping_id}/{muscle_group_id}` | `{"role": PRIMARY\|SECONDARY\|STABILIZER, "stimulus_weight": 0–2, "fatigue_weight": 0–2}` | `{"status":"ok","item"}` | mADMIN | 422, 404 |
| DELETE | `/{mapping_id}/{muscle_group_id}` | — | `{"status":"ok","mapping_id","muscle_group_id","is_active":false}` (soft) | mADMIN | 404 |

#### 2.19 `exercise_catalog_admin.py` (17 rotas)

`router` prefixo `/exercises`:

| Método | Path | Query/Path | Corpo | Resposta | Auth | Erros |
|---|---|---|---|---|---|---|
| GET | `` | `query`, `active_only`, `limit` (1–1000, def 200), `offset` | — | `{"query","active_only","limit","offset","total","items"}` (`SELECT ec.*`) | mID | — |
| POST | `` | — | `name` (obrig.), `body_position`, `movement`, `trajectory`, `biomechanical_pattern`, `primary_joint`, `resistance_vector`, `keywords`, `description`, `is_active` (def true), `is_unilateral` (def false), `is_compound` (nullable) | `{"status":"ok","item"}` | mADMIN | 400, 409 "Já existe um exercício com esse nome." |
| PUT | `/{exercise_id}` | path | idem (parcial: só chaves presentes) | `{"status":"ok","item"}` | mADMIN | 404, 400, 409 |
| GET | `/{exercise_id}` | path | — | `{"exercise","aliases","external_mappings","muscle_contributions","equipment_links","legacy_ids_merged"}` | mID | 404 |
| GET | `/{exercise_id}/aliases` | path | — | `{"items"}` | mID | 404 |
| POST | `/{exercise_id}/aliases` | path | `{"alias","source"}` | `{"status":"ok","item"}` | mADMIN | 400, 404, 409 |
| DELETE | `/aliases/{alias_id}` | path | — | `{"status":"ok","id"}` | mADMIN | 404 |
| GET | `/{exercise_id}/external-mappings` | path | — | `{"items"}` | mID | 404 |
| POST | `/{exercise_id}/external-mappings` | path | `provider`, `external_name` (obrig.), `external_id`, `mapping_status` (def CONFIRMED), `source`, `notes`, `is_active` | `{"status":"ok","item"}` | mADMIN | 400, 404 |
| PUT | `/external-mappings/{mapping_row_id}` | path | idem | `{"status":"ok","item"}` | mADMIN | 404 |
| DELETE | `/external-mappings/{mapping_row_id}` | path | — | `{"status":"ok","id"}` | mADMIN | 404 |

`external_mappings_router` prefixo `/exercise-external-mappings`:

| Método | Path | Query | Resposta | Auth |
|---|---|---|---|---|
| GET | `` | `query`, `provider`, `active_only`, `limit`, `offset` | lista flat com `exercise_name`, `exercise_is_active` | mID |
| GET | `/providers` | — | `{"items":[provider, count]}` | mID |

`equipment_types_router` prefixo `/equipment-types`:

| Método | Path | Corpo | Resposta | Auth | Erros |
|---|---|---|---|---|---|
| GET | `` | — | `{"items"}` | mID | — |
| POST | `` | `code` (upper), `name`, `description` | `{"status":"ok","item"}` | mADMIN | 400, 409 |
| PUT | `/{type_id}` | idem | `{"status":"ok","item"}` | mADMIN | 404, 409 |
| DELETE | `/{type_id}` | — | `{"status":"ok","id"}` | mADMIN | 409 "Tipo em uso por N equipamento(s)...", 404 |

#### 2.20 `exercise_mapping_canonical.py` (prefixo `/mapping-canonical`, 3 rotas, router `dependencies=[Depends(get_current_identity)]`)

| Método | Path | Query | Corpo | Resposta | Auth extra |
|---|---|---|---|---|---|
| GET | `/unmapped` | `limit` (1–1000, def 100) | — | `{"ok":true,"rows":[id, original_name]}` (exercícios com `mapping_id` nulo ou apontando para catálogo inativo) | — |
| GET | `/options` | `q?` | — | `{"ok","source":"exercise_catalog","items":[...≤1000]}` | — |
| POST | `/associate` | — | `CanonicalAssociationRequest {alias_name, target_mapping_id}` | `{"ok","operation":"created\|updated","alias_name","alias_mapping_id","target_mapping_id","target_exercise_name","target_amazfit_name","target_primary_group","reclassification"}`; erros de negócio retornam `{"ok":false,"error"}` com HTTP 200 | ADMIN |

`CLAUDE.md` do projeto on-premise cita `GET /mapping-canonical/resolve`; esse endpoint não existe neste código.

#### 2.21 `muscle_groups_mapped.py` (prefixo `/analytics`, 4 rotas, ID)

| Método | Path | Query | Resposta |
|---|---|---|---|
| GET | `/analytics/muscle-groups-mapped-health` | `days` (1–2000, def 180) | `{"ok","source","days","set_count","groups","meta"}` |
| GET | `/analytics/muscle-groups-mapped-debug` | `days` | `aggregate("weekly","sets",...)` |
| GET | `/analytics/muscle-groups-mapped-trend` | `period` ∈ daily/weekly/monthly/quarterly/yearly, `metric` ∈ volume/sets/reps/load, `days`, `include_secondary` (def true), `secondary_factor` (0–1, def 0.5) | dict com `rows/data/trend_rows/table/table_rows/groups/unmapped/...` |
| GET | `/analytics/muscle-groups-mapped-table` | idem | idem com `rows=table_rows` |

Estas 4 rotas ficam em `musclefit-api-core` (o router é incluído em `main.py`), embora o prefixo seja `/analytics`. Cada chamada executa `ensure_canonical_columns()` e `reclassify_exercises(limit=20000, only_missing=True)` antes de ler.

#### 2.22 `analytics.py` (prefixo `/analytics`, 22 rotas, todas ID) — `musclefit-engines`

| Método | Path | Query | Resposta / origem |
|---|---|---|---|
| GET | `/health` | — | `{"status","namespace","database","compatibility"}` |
| GET | `/summary` | `days` (1–3650, def 90) | `{"days","totals":{activities, strength_activities, cardio_activities, sets_count, reps_total, volume_kg, avg_rpe, high_rpe_sets},"latest_activities":[10]}` |
| GET | `/training-trend` | `period` ∈ day/week/month/year (def week), `days` (def 365) | `compute_training_trend` |
| GET | `/exercise-progress` | `name` (obrig.), `days` (def 365) | linhas por (data, atividade, exercício) com `LIKE` em `original_name`/nome AMAZFIT |
| GET | `/rpe-distribution` | `days` (def 90) | `FLOOR(rpe)` × `sets_count` |
| GET | `/cardio` | `period`, `days` | atividades cuja `activity_type`/`name` casam cardio/caminhada/walk/bike/bicicleta/corrida/run |
| GET | `/muscle-groups-equivalent-sets-by-activity` | `limit` (1–2000, def 400) | `VolumeService.get_equivalent_sets_by_activity` |
| GET | `/muscle-activation-by-activity` | `activity_id` (obrig.) | `{"activity_id","items","training_type_targets"}` |
| GET | `/muscle-groups-recent-exercise-volume` | `limit`, `date_from`, `date_to` | `VolumeService.get_recent_exercise_volume` |
| GET | `/muscle-groups-daily-activation` | `days` (1–90, def 7) | `VolumeService.get_daily_muscle_activation` |
| POST | `/muscle-activation-preview` | corpo `MuscleActivationPreviewIn` | `{"items"}` (`get_muscle_activation_for_exercises`) |
| POST | `/muscle-activation-breakdown` | corpo `MuscleActivationPreviewIn` | `{"items"}` (`get_muscle_activation_breakdown_by_exercise`) |
| GET | `/dashboard-muscle-status` | `days` (def 14), `training_type_id?` | `_build_muscle_status_items` |
| GET | `/dashboard-muscle-drilldown` | `muscle_group_id` (obrig.), `days` (def 90) | `overview, readiness, top_exercises, latest_progression, weekly_series` |
| GET | `/dashboard-progression` | `exercise_name?`, `mapping_id?`, `days` (def 180) | sessões com e1RM + `items[].evolution` (`exposure_service.consecutive_comparisons` → `compare_exposures`) (400 se nenhum dos dois) |
| GET | `/dashboard-progression-trend` | idem | `exposure_service.progression_verdict` → `classify_exercise_progression` (mesma regra da home/drill-down, desde 2026-10-06); `classification`/`reason` deprecados |
| GET | `/dashboard-progression-suggestion` | `exercise_name?`, `mapping_id?` | `services.suggested_progression_for_mapping` (400/404) |
| GET | `/dashboard-workout-sessions` | `days` (def 90), `limit` (1–1000, def 200) | `get_workout_sessions_timeline` |
| GET | `/dashboard-recovery` | `days` (def 14) | `get_recovery_overview` + `recovery_phase` |
| GET | `/dashboard-cycle` | — | posição no ciclo do template atribuído (ver 5.11) |
| GET | `/dashboard-stimulus-fatigue` | `muscle_group_id?`, `days` (def 90), `activity_id?`, `limit` (1–100, def 20) | `get_stimulus_fatigue_ratio_by_exercise` |
| GET | `/dashboard-alerts` | `days` (def 30), `training_type_id?` | `DashboardAlertService.get_alerts` |

#### 2.23 `dashboard_modules.py` (prefixo `/analytics`, 7 rotas, router `dependencies=[Depends(get_current_identity)]`)

| Método | Path | Query | Resposta |
|---|---|---|---|
| GET | `/analytics/body-composition-trend` | `days` (def 90) | `BodyCompositionTrendService.get_body_trend` |
| GET | `/analytics/training-volume` | `days` (def 30) | `{"overview": get_volume_breakdown(days), "trend": get_volume_trend(clamp(days,7,180))}` |
| GET | `/analytics/training-volume-by-split-day` | `days` (def 180, clamp 7–180) | `{"days","items"}` |
| GET | `/analytics/muscle-recovery` | `days` (def 14) | `{"days","items": get_recovery_overview(limit=24),"global_readiness_adjustment"}` |
| GET | `/analytics/muscle-recovery-consecutive-check` | `days` (def 7) | `{"days","items"}` |
| GET | `/analytics/muscle-group-body-map` | — | `{"items":[{muscle_group, slugs[]}]}` |
| PUT | `/analytics/muscle-group-body-map` | corpo `{"muscle_group","slugs":[...]}` | `{"muscle_group","slugs"}`; entrada inválida retorna `{"error": ...}` com HTTP 200 |

#### 2.24 `dashboard_intelligence.py` (prefixo `/analytics`, 3 rotas, ID)

| Método | Path | Query | Resposta |
|---|---|---|---|
| GET | `/analytics/dashboard-home` | `days` (def 90) | `DashboardAggregatorService.build_home_payload` |
| GET | `/analytics/training-evolution` | `days` (def 90) | `build_training_evolution_payload` |
| GET | `/analytics/cardio-agent-context` | `days` (1–90, def 14) | `CardioAgentContextService.build_context` |

#### 2.25 `engines_extra.py` (2 rotas, ID)

| Método | Path | Query | Resposta | Erros |
|---|---|---|---|---|
| GET | `/exercises/suggested-progression` | `mapping_id` (obrig.) | dict de `suggested_progression_for_mapping` | 404 "Mapeamento de exercício não encontrado." |
| GET | `/exercises/suggested-set-count-for-substitution` | `original_mapping_id`, `original_sets_count` (≥1), `new_mapping_id` | dict de `suggested_set_count_for_substitution` | 404 |

#### 2.26 `main_engines.py` (2 rotas)

| Método | Path | Resposta |
|---|---|---|
| GET | `/health` | `{"status":"ok","service":"musclefit-engines"}` |
| GET | `/ready` | `{"status":"ready","db": bool}` |

#### 2.27 `exercise_cards.py` (prefixo `/exercises`, 7 rotas) — cards ilustrados de exercícios

Fluxo completo em 6.6. Toda rota `card-generations` exige **admin** (403) e a flag `EXERCISE_CARD_GENERATION_ENABLED` (503 `exercise_card_generation_disabled` quando `false`); `GET /card` é de qualquer autenticado e NÃO depende da flag (desligar a geração não esconde o que já foi aprovado). Erros de negócio com `detail = {error: <código>, message, ...}`.

| Método | Path | Auth | Body | Resposta | Erros |
|---|---|---|---|---|---|
| POST | `/exercises/{id}/card-generations` | admin | `{idempotency_key, instruction?, text_level?}` | 202 `{generation}` (replay da mesma chave: 200) | 404 `exercise_not_found`; 409 `exercise_card_generation_in_progress` / `idempotency_key_conflict`; 422 `invalid_idempotency_key` / `invalid_text_level` (o prompt V2 só aceita `minimal`) / `instruction_too_long`; 429 `exercise_card_daily_limit` / `exercise_card_global_in_flight_limit`; 502 `exercise_card_dispatch_failed`; 503 |
| GET | `/exercises/{id}/card-generations?limit=20` | admin | — | `{items}` (sem `prompt_text`) | 503 |
| GET | `/exercises/{id}/card-generations/{gid}` | admin | — | `{generation}` (sweep do exercício antes; `WHERE id AND exercise_id` anti-IDOR) | 404 `exercise_card_generation_not_found` |
| POST | `/exercises/{id}/card-generations/{gid}/approve` | admin | — | `{generation (PUBLISHED), exercise_card}` | 409 `not_reviewable`; 410 `DRAFT_EXPIRED` (rascunho com mais de 7 dias — `draft_expired` no payload; a geração continua GENERATED, só rejeitar/regenerar); 502 `image_processing_failed` / `s3_read_failed` / `s3_upload_failed` (a geração volta a GENERATED) |
| POST | `/exercises/{id}/card-generations/{gid}/reject` | admin | `{reason?}` | `{generation (REJECTED)}` | 409 `not_reviewable`; 422 `reason_too_long` |
| POST | `/exercises/{id}/card-generations/{gid}/regenerate` | admin | `{idempotency_key, instruction?, text_level?}` | 202 `{generation (filha), parent}` | 409 `not_regenerable`; demais como no POST de criação |
| GET | `/exercises/{id}/card` | autenticado | — | `{exercise_id, exercise_card: {url, thumbnail_url, png_url, version, published_at} ou null}` | 404 `exercise_not_found` |

`generation` = `status, version, parent_generation_id, model, quality, size, prompt_version, text_level, request_instruction, reject_reason, preview_url (+ expires_at, só GENERATED/APPROVING), card{…} (só PUBLISHED/SUPERSEDED), width/height/bytes, tokens, cost_usd, error_code/message, job_id, created_by/approved_by, timestamps`.

**Modo Treino (campos aditivos, PR5):** `GET /workout-plans/{id}` devolve `exercise_card: {url, thumbnail_url, version} | null` em cada item de `exercises[]`, e `GET /workout-plans/{plan_id}/exercises/{wpe_id}/briefing` devolve o mesmo campo no bloco `exercise`. O card é sempre o do exercício **canônico** (`COALESCE(exercise_legacy_id_map.canonical_exercise_id, mapping_id)`, o mesmo JOIN que já existia em `services.get_workout_plan` e o `_canonical_exercise_id` do briefing) e sai de `exercise_catalog.card_version` lido no SELECT que já existia — zero JOIN/consulta nova. URLs montadas por `exercise_card_storage.training_card` → `public_urls` → `public_assets.url_for` (nunca literal de domínio). `card_version` NULL (exercício sem card, item em texto livre) → `null`. Valor inválido também vira `null`: a leitura da ficha nunca quebra por causa do card. `GET /exercises/{id}/card` resolve só o id que recebe (não consulta `exercise_legacy_id_map`).

---


### 3. Autenticação e autorização

#### 3.1 Fontes de identidade e ordem de resolução (`security.resolve_identity`)

Entrada: uma string de token (do header `X-API-Token` ou do cookie `musclefit_session`). Ordem:

1. Token vazio → `HTTPException 401 "Token inválido."`.
2. Se `token == os.environ["API_TOKEN"]` (e a env var não é vazia) → busca `_admin_user_id()` (`SELECT id FROM users WHERE role='admin' AND is_active=1 ORDER BY id ASC LIMIT 1`); se existir, retorna `Identity(user_id=admin_id, role="admin", source="master_token")`. Se não houver admin, continua para os passos seguintes.
3. `device_token_matches(token)`: calcula `sha256(token)` e procura em `devices` (`token_hash = %s AND is_active = 1 AND revoked_at IS NULL`); se encontrado, faz `UPDATE devices SET last_seen_at = CURRENT_TIMESTAMP` e retorna `True`. Qualquer exceção (inclusive tabela inexistente) → `False`. Com match → `Identity(user_id=admin_id, role="admin", source="device")` (novamente exige admin existente).
4. `_session_identity(token)`: `sha256(token)` contra `user_sessions s JOIN users u` com `s.revoked_at IS NULL AND s.expires_at > NOW()`; exige `u.is_active`; atualiza `s.last_seen_at`; retorna `Identity(user_id, role=u.role, source="session")`. Exceções → `None`.
5. Nenhum match → 401.

`get_current_identity(x_api_token: Header, musclefit_session: Cookie alias "musclefit_session")` chama `resolve_identity(x_api_token or musclefit_session)`; o header tem prioridade sobre o cookie.

`Identity` é um `dataclass` com `user_id: int`, `role: str` (`'admin' | 'regular'` no comentário; o código também trata `'personal_trainer'` em `require_admin_or_pt`), `source: str` (`'master_token' | 'device' | 'session'`).

#### 3.2 Guards

| Guard | Regra | Erro |
|---|---|---|
| `require_token(x_api_token: Header)` | `API_TOKEN` vazio → 500 "API_TOKEN não configurado no servidor."; `token_is_valid` (mestre ou dispositivo) | 401 |
| `require_master_token` | `API_TOKEN` vazio ou igual a `"change_api_token"` → 500; token deve ser exatamente o mestre | 401 |
| `require_admin` | `identity.role != "admin"` → 403 "Acesso restrito a administradores." | 403 |
| `require_admin_or_pt` | `role not in ("admin", "personal_trainer")` → 403 "Acesso restrito a administradores e personal trainers." | 403 |
| `users.require_session_identity` | `identity.source != "session"` → 403 | 403 |
| `gyms._require_identity` / `exercise_catalog_admin._require_identity` / `exercise_mapping_catalog._require_identity` | chamam `security.resolve_identity(x_api_token or musclefit_session)` manualmente dentro do handler | 401 |
| `gyms._require_admin_identity` (e homônimos em `exercise_catalog_admin`, `exercise_mapping_catalog`) | identidade + `role == "admin"` | 403 |
| `gyms._require_token` e `exercise_mapping_catalog._require_token` | funções definidas; nenhuma rota destes módulos as chama (uso externo não verificado). `exercise_mapping_catalog._require_token` compara apenas com o token mestre; `gyms._require_token` usa `security.token_is_valid` | 500/401 |

`require_master_token` é usado apenas por `devices.py`. `require_token` não é referenciado por nenhum router (não verificado uso fora de `api/app`).

Em `users.py`, `_ALLOWED_ROLES = {"admin", "regular"}` — o papel `personal_trainer` não é aceito por `POST /users` nem `PUT /users/{id}` (não verificado se existe outra via de gravação).

#### 3.3 Cookie de sessão `musclefit_session`

- Nome: `SESSION_COOKIE_NAME = "musclefit_session"` (`security.py`).
- Emissão em `POST /auth/login` (`_set_session_cookie`): `max_age = 30 dias` (`SESSION_TTL_DAYS=30`), `path="/"`, `domain=session_cookie_domain()`, `secure=session_cookie_secure(request)`, `httponly=True`, `samesite="lax"`. O valor é o token cru (`secrets.token_urlsafe(32)`); no banco fica `sha256(token)` em `user_sessions.token_hash`, com `expires_at = now + 30 dias` (`now = datetime.utcnow()`).
- `session_cookie_domain()`: `SESSION_COOKIE_DOMAIN` (default `.musclefit.com.br`); string vazia → `None` (sem atributo Domain).
- `session_cookie_secure(request)`: se `SESSION_COOKIE_SECURE` definido, usa `in ("1","true","yes")`; senão `True` se `request.url.scheme == "https"` ou se o primeiro valor de `X-Forwarded-Proto` for `https`.
- Logout: `UPDATE user_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE token_hash=%s AND revoked_at IS NULL` e `delete_cookie` com os mesmos atributos.
- O corpo de `/auth/login` não retorna o token.

#### 3.4 Login, lockout e seed

- `login`: chama `ensure_admin_seed()` antes de tudo. Constantes: `MAX_FAILED_ATTEMPTS=5`, `LOCKOUT_MINUTES=15`, `MIN_PASSWORD_LENGTH=8`. Em senha errada incrementa `failed_login_attempts`; na 5ª falha grava `locked_until = now + 15 min` e zera o contador. Sucesso: insere sessão e zera `failed_login_attempts`/`locked_until`.
- Hash de senha: `bcrypt.hashpw(raw, bcrypt.gensalt())`; verificação `bcrypt.checkpw` (erros de tipo/valor → `False`).
- `ensure_admin_seed()`: se não existir `users.username='doug'`, insere com `role='admin'`, `is_active=1`, `must_change_password=1`, `full_name='Doug'` e senha temporária `secrets.token_urlsafe(12)`; a senha temporária é escrita em `logger.warning` e `print`. Idempotente; exceções são logadas e engolidas.

#### 3.5 Token mestre e tokens de dispositivo

- Token mestre: env `API_TOKEN` (carregado do Secrets Manager por `secrets_bootstrap` se `API_TOKEN_SECRET_ARN` estiver definido). Resolve sempre para o admin seed. Comparado em tempo constante (`security.master_token_matches`, `hmac.compare_digest`, SEG-04). Nenhuma Lambda o usa mais para chamar a própria API. Rotação: `docs/runbooks/rotacao-segredos.md`.
- Dispositivos (`devices.py`): `POST /devices/pair` gera `secrets.token_urlsafe(32)`, grava `sha256` em `devices.token_hash`, `user_id = _admin_user_id()`; revogação seta `is_active=0, revoked_at=CURRENT_TIMESTAMP`. Tokens de dispositivo resolvem para o admin (`source="device"`).

#### 3.6 Papéis e flags

- `role`: `admin`, `regular`, `personal_trainer` (este último aceito por `require_admin_or_pt` nos routers de modelos de ficha e tipos de treinamento).
- `users.is_advanced_user` (TINYINT): lido por `progression_state.load_user_flags(conn, user_id)` (`None` → `False`; D11: só elegibilidade de técnica; `user_flags.py` foi apagado no COD-08). Texto abaixo é histórico: Usado em `progression_service.suggest_load_and_reps` (ignora gatilho de redução por RPE 10 precoce) e em `VolumeService.get_muscle_group_overview` (RPE 10 em qualquer série não dispara `rpe_anticipated`).
- `users.must_change_password`: exposto em `_public_user`; zerado em `change_password`.

#### 3.7 Isolamento multi-tenant (como `user_id` é aplicado)

- As funções de `services.py` que tocam `activities`, `exercises`, `exercise_sets`, `body_composition`, `workout_plans` aceitam `user_id: int | None`. Quando não é `None`, o SQL adiciona `AND a.user_id = %s`/`WHERE ... AND user_id = %s`; quando `None`, não filtra. Os routers passam sempre `identity.user_id`.
- Escritas gravam `user_id` a partir da identidade (`upsert_activity`, `upsert_body_composition`, `create_workout_plan`, `workout_sessions.start_session`, `_insert_recommendation`, `sleep_metrics`, `manual_effort_metrics`, `reports`).
- Recurso de outro usuário → funções retornam `None`/`False` e o router responde 404 (não 403).
- Services de `intelligence/*` aceitam `user_id` opcional: quando `None`, as queries rodam sem filtro. Chamadores atuais: rotas de `analytics.py`/`dashboard_*` passam `identity.user_id`; em `DashboardAggregatorService.build_training_evolution_payload`, `compute_rolling_weekly_trend(weekly_trend_days)` é chamado sem `user_id`; `IntervalsCorrelationService._fetch_strength_activities(days)` não filtra por usuário; `template_volume_planner._load_projected_recovery` obtém recuperação via self-call com token mestre (usuário admin).
- Tabelas compartilhadas sem filtro por usuário: `gyms` (leitura), `equipment`, `gym_equipment`, `equipment_exercise_mappings`, `equipment_exercises`, `gym_equipment_catalog`, `exercise_catalog`, `exercise_aliases`, `exercise_external_mappings`, `exercise_muscle_contributions`, `muscle_groups`, `muscle_group_body_map`, `exercise_mappings`, `exercise_zepp_mappings`, `workout_plan_templates`, `training_types`, `equipment_types`.
- Regras de dono em `gyms.py`: escrita em `gyms` exige `owner_user_id == identity.user_id` ou admin (`_assert_gym_writable`, 404); `POST/DELETE /gym-equipment-catalog` idem (403); campos `name/opens_at/closes_at` de gym são admin-only. `POST/PUT/DELETE /gym-equipment` exigem apenas identidade.
- `reports`: `GET /reports/{id}` e `/pdf` permitem admin ler de qualquer usuário; `DELETE` só do dono.
- `GET /jobs/{job_id}` não compara o `user_id` do job com a identidade.

#### 3.8 CORS

`main.py` e `main_engines.py` configuram `CORSMiddleware` de forma idêntica: `allow_origins` = `CORS_ALLOWED_ORIGINS` separado por vírgula (default `"https://app.musclefit.com.br"`), com `strip()` e remoção de vazios; `allow_credentials=True`; `allow_methods=["*"]`; `allow_headers=["*"]`.

#### 3.9 Criptografia de segredos em repouso (`crypto_utils.py`)

Fernet com chave `SECRET_ENCRYPTION_KEY` (lida em tempo de chamada; instância recriada se a chave mudar; ausência → `RuntimeError` com instrução de geração). `encrypt_secret(None)` → `ValueError`; `decrypt_secret` converte `InvalidToken` em `ValueError`; `is_encrypted` tenta descriptografar e devolve bool. Uso: `users.intervals_api_key_encrypted` (gravação em `users.py`, leitura em `intervals_integration._get_user_intervals_credentials`). `users.migrate_plaintext_intervals_keys()` (startup) recriptografa linhas cujo valor não é token Fernet válido; nunca lança.

---

### 4. Modelos Pydantic

Todos em `models.py` salvo indicação. `X?` significa `Optional[X]` com default `None`.

#### 4.1 Atividades e séries

| Modelo | Campos |
|---|---|
| `ExerciseSetIn` | `set_number: int`; `set_type: str = "working"`; `reps: int?`; `weight_kg: Decimal?`; `rpe: Decimal?`; `rest_seconds: int?`; `hr_avg_bpm: Decimal?`; `hr_max_bpm: Decimal?`; `notes: str?` |
| `ExerciseIn` | `sequence: int`; `original_name: str`; `mapping_id: int?`; `mapped_name: str?`; `muscle_primary: str?`; `muscle_secondary: str?` (hints para `match_mapping`); `equipment_id: int?` (id de `gym_equipment`); `equipment_name: str?`; `notes: str?`; `muscle_pain: int? (ge=0, le=10)`; `joint_pain: bool?`; `sets: list[ExerciseSetIn] = []` |
| `HrZoneIn` | `zone_number: int`; `zone_name: str?`; `min_bpm: int?`; `max_bpm: int?`; `duration_seconds: int?`; `percent_time: Decimal?` |
| `ActivityIn` | `source_name: str = "openclaw"`; `external_id: str?`; `local_date: date`; `started_at: datetime?`; `ended_at: datetime?`; `activity_type: str = "musculacao"`; `name: str`; `description: str?`; `duration_seconds: int?`; `calories_kcal: Decimal?`; `avg_hr_bpm: Decimal?`; `max_hr_bpm: Decimal?`; `notes: str?`; `gym_id: int?`; `gym_name: str?`; `training_type_id: int?`; `conducted_by: str?`; `exercises: list[ExerciseIn] = []`; `hr_zones: list[HrZoneIn] = []`; `raw_payload: dict?` |
| `ExerciseLogUpdateIn` | `local_date: date`; `original_name: str`; `started_at: datetime?`; `mapping_id: int?`; `mapped_name/muscle_primary/muscle_secondary: str?`; `gym_id: int?`; `gym_name: str?`; `conducted_by: str?`; `equipment_id: int?`; `equipment_name: str?`; `workout_plan_exercise_id: int?` (presença detectada via `model_fields_set`); `muscle_pain: int? (0–10)`; `joint_pain: bool?`; `sets: list[ExerciseSetIn] = []` |
| `BodyCompositionIn` | `local_date: date`; `measured_at: datetime?`; `weight_kg, bmi, body_fat_percent, fat_mass_kg, fat_free_mass_kg, muscle_mass_kg, skeletal_muscle_percent, body_water_percent, visceral_fat_rating, bone_mass_kg, bmr_kcal, subcutaneous_fat_percent, protein_percent: Decimal?`; `metabolic_age: int?`; `source: str = "manual"`; `notes: str?`; `raw_payload: dict?` |
| `ExerciseMappingIn` | `original_name: str`; `amazfit_name: str`; `mapped_name: str?`; `muscle_primary: str?`; `muscle_secondary: str?`; `confidence: Decimal?`; `is_user_confirmed: bool = False`; `notes: str?` |

#### 4.2 Planos de treino

| Modelo | Campos |
|---|---|
| `WorkoutPlanExerciseIn` | `sequence: int`; `exercise_name: str`; `amazfit_name: str?`; `mapping_id: int?`; `preferred_equipment_id: int?`; `muscle_primary/muscle_secondary: str?`; `equipment_name: str?`; `sets_count: int?`; `reps_target: str?`; `load_target: str?`; `rpe_target: str?`; `rest_seconds: int?`; `warmup: bool = False`; `substitution_options: list[str]?`; `notes: str?`; `advanced_technique: str?`; `advanced_technique_applications: int?`; `advanced_technique_duration_seconds: int?`; `warmup_sets_count: int?`; `warmup_reps: int?`; `warmup_rest_seconds: int?`; `feeler_set_sets_count: int?`; `feeler_set_reps: int?`; `feeler_set_rest_seconds: int?`; `peak_contraction_seconds: int?` |
| `WorkoutPlanIn` | `plan_date: date?`; `plan_time: time?`; `name: str`; `plan_type: str = "full_body"`; `split_day: str?`; `goal: str?`; `status: str = "planned"`; `gym_id: int?`; `gym_name: str?`; `raw_payload: dict?`; `template_id: int?`; `template_slot_sequence: int?`; `exercises: list[WorkoutPlanExerciseIn] = []` |
| `MuscleActivationExerciseIn` | `mapping_id: int?`; `sets_count: int?` |
| `MuscleActivationPreviewIn` | `exercises: list[MuscleActivationExerciseIn] = []` |
| `WorkoutPlanGenerateIn` | `gym_id: int?`; `gym_name: str?`; `plan_date: date?`; `plan_time: time?`; `plan_type: str = "full_body"`; `split_day: str?`; `goal: str?`; `min_rest_seconds: int = 30`; `max_rest_seconds: int = 90`; `template_id: int?`; `template_slot_sequence: int?` |

#### 4.3 Modelos de ficha e tipos de treinamento

| Modelo | Campos |
|---|---|
| `WorkoutPlanTemplateSlotItemIn` | `item_order: int`; `item_kind: str`; `muscle_group_id: int?`; `mapping_id: int?` (FK `exercise_catalog`); `equipment_id: int?`; `sets_count: int? = 3`; `reps_target: str?`; `rpe_target: str?`; `prioritize: bool = False`; `cardio_duration_minutes: int?`; `priority_min_pct: int?`; `priority_max_pct: int?`; `advanced_technique: Literal["rest_pause","drop_set","fst7","gvt","forced_reps","negative_reps","partial_reps","cheat_reps"]?`; `advanced_technique_applications: int? (1–20)`; `advanced_technique_duration_seconds: int? (1–600)`; `warmup_sets_count: int? (1–10)`; `warmup_reps: int? (1–100)`; `warmup_rest_seconds: int? (1–600)`; `feeler_set_sets_count: int? (1–10)`; `feeler_set_reps: int? (1–100)`; `feeler_set_rest_seconds: int? (1–600)`; `peak_contraction_seconds: int? (1–60)` |
| `WorkoutPlanTemplateSlotIn` | `sequence: int`; `name: str`; `weekday: int?`; `slot_type: str = "normal"`; `priority_min_pct: int?`; `priority_max_pct: int?`; `items: list[WorkoutPlanTemplateSlotItemIn] = []` |
| `WorkoutPlanTemplateIn` | `name: str`; `description: str?`; `template_type: str = "full_body"`; `training_type_id: int?`; `volume_mode: Literal["percent_budget","equipment_series"] = "percent_budget"`; `is_active: bool = True`; `slots: list[WorkoutPlanTemplateSlotIn] = []` |
| `WorkoutPlanTemplateAssignIn` | `user_id: int` |
| `WorkoutPlanTemplatePreviewIn` (em `workout_plan_templates.py`) | `slots: list[WorkoutPlanTemplateSlotIn] = []`; `volume_mode: Literal[...] = "percent_budget"` |
| `TrainingTypeMuscleGroupTargetIn` | `muscle_group_id: int`; `min_weekly_sets: float`; `target_weekly_sets: float`; `max_weekly_sets: float` |
| `TrainingTypeIn` | `name: str`; `description: str?`; `rag_folder: str?`; `is_active: bool = True`; `targets: list[TrainingTypeMuscleGroupTargetIn] = []` |

#### 4.4 IA / jobs

| Modelo | Campos |
|---|---|
| `InsightsGenerateIn` | `question: str`; `context: str = ""` |
| `WorkoutsAnalysisGenerateIn` | `days: int = 90 (ge=7, le=365)` |
| `PreviousSetIn` | `set_number: int?`; `reps: int?`; `weight_kg: float?`; `rpe: float?` |
| `SetEvaluationIn` | `exercise_name: str`; `reps_target: str?`; `load_target: str?`; `rpe_target: str?`; `reps: int?`; `weight_kg: float?`; `rpe: float?`; `set_number: int?`; `sets_count: int?`; `previous_sets_this_session: list[PreviousSetIn] = []`; `muscle_pain: int? (0–10)`; `joint_pain: bool?`; `notes: str?` |

#### 4.5 Modelos definidos fora de `models.py`

| Módulo | Modelo | Campos |
|---|---|---|
| `auth.py` | `LoginIn` | `username: str`; `password: str` |
| `auth.py` | `ChangePasswordIn` | `current_password: str`; `new_password: str (min_length=8)` |
| `devices.py` | `DevicePairIn` | `device_name: str`; `platform: str = "zepp"` |
| `exercise_mapping_canonical.py` | `CanonicalAssociationRequest` | `alias_name: str`; `target_mapping_id: int` |
| `workout_sessions.py` | `SessionStartIn` | `name: str = "Treino (Zepp)"`; `workout_plan_id: int?`; `gym_id: int?`; `gym_name: str?`; `training_type_id: int?`; `conducted_by: str?`; `local_date: date?`; `started_at: datetime?`; `notes: str?`; `source_name: str = "amazfit-zepp"`; `activity_type: str?` |
| `workout_sessions.py` | `SessionExerciseIn` | `original_name: str`; `sequence: int?`; `mapping_id: int?`; `equipment_id: int?`; `equipment_name: str?`; `workout_plan_exercise_id: int?`; `notes: str?`; `muscle_pain: int? (0–10)`; `joint_pain: bool?` |
| `workout_sessions.py` | `SessionSetIn` | `set_number: int?`; `set_type: str = "working"`; `reps: int?`; `weight_kg: Decimal?`; `rpe: Decimal?`; `rest_seconds: int?`; `hr_avg_bpm: Decimal?`; `hr_max_bpm: Decimal?`; `notes: str?`; `muscle_pain: int? (0–10)`; `joint_pain: bool?` |
| `workout_sessions.py` | `SessionFinishIn` | `ended_at: datetime?`; `duration_seconds: int?`; `calories_kcal: Decimal?`; `avg_hr_bpm: Decimal?`; `max_hr_bpm: Decimal?`; `notes: str?` |
| `workout_sessions.py` | `SessionMetaIn` | `name: str?`; `workout_plan_id: int?`; `gym_id: int?`; `gym_name: str?`; `training_type_id: int?`; `conducted_by: str?`; `local_date: date?`; `started_at: datetime?` |

Nenhum modelo declara `@validator`/`@field_validator`; as únicas validações são `Field(ge/le/min_length)` e `Literal`. Diversos endpoints recebem `Dict[str, Any]` livre (users, gyms, equipment, catálogos, cardio, sleep, effort, body composition PUT, status de template/tipo).

Dataclasses do motor v2 (não Pydantic): ver 5.11.

---


### 5. Motores determinísticos

#### 5.1 `services.compute_exercise_stats(sets)`

Entrada: lista de dicts com `reps`, `weight_kg`, `rpe`. Saída: `sets_count = len(sets)`; `reps_total = Σ reps` (`None` se lista vazia); `volume_kg = Σ reps×weight_kg` só para séries com ambos (senão `None`); `min_weight_kg`/`best_weight_kg` = min/max dos pesos não nulos; `avg_rpe` = média dos RPE não nulos. Aritmética em `Decimal`.

#### 5.2 Resolução canônica e de vínculos (`services.py`)

- `_resolve_canonical_exercise_id(exercise_id, cur=None)`: `SELECT canonical_exercise_id FROM exercise_legacy_id_map WHERE old_exercise_id=%s`; sem linha retorna o próprio id; `None` → `None`. Aceita cursor externo; sem ele abre `transaction()` própria.
- `resolve_mapping_id(original_name, mapped_name_hint, explicit_mapping_id, cur)`: com id explícito → canônico dele; senão `exercise_mapping_canonical.match_mapping(original_name, mapped_name_hint)` (exceção → `None`) → canônico do `match["id"]`.
- `resolve_gym_id(cur, gym_id_explicit, gym_name_hint)`: id explícito; senão `gyms` ativos com `LOWER(name) = LOWER(hint)`; senão `LOWER(name) LIKE LOWER('%hint%')`; senão `None`.
- `resolve_equipment_id(cur, mapping_id, gym_id, equipment_id_explicit, equipment_name_hint)`:
  1. id explícito → retorna.
  2. sem `gym_id` ou `mapping_id` → `None`.
  3. `mapping_id` canônico; grupo primário = `exercise_muscle_contributions` `role='PRIMARY' AND is_active=1 ORDER BY id ASC LIMIT 1` → nome em `muscle_groups`; sem grupo → `None`.
  4. Com hint: `gym_equipment` ativo da academia cujo `exercise_catalog` (resolvido por `exercise_legacy_id_map` sobre `ge.mapping_id`) tem PRIMARY no grupo e `LOWER(eq.name) = LOWER(hint)`; depois `LIKE %hint%`.
  5. Sem hint (ou sem match): `gym_equipment` do grupo/academia agrupado por `ge.id` com `LEFT JOIN exercises e ON e.equipment_id = ge.id LEFT JOIN activities a`, `ORDER BY (MAX(COALESCE(a.ended_at, a.started_at, a.created_at)) IS NULL) DESC, MAX(...) ASC, eq.name ASC LIMIT 1` (nunca usado primeiro, depois o menos recente).
- `is_gym_equipment_pair_active(gym_id, mapping_id, equipment_id)`: bool; `COALESCE(ge_elm.canonical_exercise_id, ge.mapping_id) = canonical(mapping_id) AND ge.equipment_id = %s AND ge.is_active = 1`.

#### 5.3 `upsert_activity` (POST/PUT `/activities`)

1. `source_id(conn, source_name, "api")`: find-or-create em `sources` (`description = "Fonte criada automaticamente: {name}"`).
2. `ext_id = external_id` ou `sha256(f"{source_name}|{local_date}|{name}|{json(exercises)}")`.
3. `payload_hash = sha256(json(activity, sort_keys=True))`; `INSERT INTO imports (source_id, import_type='activity', external_id, status='success', payload_hash, raw_payload) ON DUPLICATE KEY UPDATE ...`; lê `imports.id`.
4. Busca `activities` por `(source_id, external_id[, user_id])`; se existir, `DELETE FROM activities WHERE id=%s`.
5. `avg_rpe` da atividade = média dos `avg_rpe` por exercício (via `compute_exercise_stats`).
6. `INSERT INTO activities` (`gym_id` via `resolve_gym_id`, `training_type_id`, `conducted_by`, `raw_payload = CAST(json(raw_payload or activity) AS JSON)`).
7. `activity_hr_zones` para cada zona.
8. Para cada exercício: `mapping_id = resolve_mapping_id(original_name, mapped_name, mapping_id)`, `equipment_id = resolve_equipment_id(...)`, `INSERT INTO exercises` com stats, `muscle_pain`, `joint_pain`, `source_row_hash = sha256(f"{ext_id}|{sequence}|{original_name}|{json(sets)}")`; para cada série `_ensure_set_hr_columns(cur)` (checa `information_schema` uma vez por processo) e `INSERT INTO exercise_sets`.
9. Retorna `{activity_id, external_id, exercises, sets}`.

`update_activity`: localiza a linha (com `user_id` se informado), reaproveita `external_id` e `sources.name` (fallback `"frontend"`), chama `upsert_activity(activity, user_id or existing.user_id)`. `delete_activity`: apaga `exercise_sets` (JOIN), `exercises`, `activity_hr_zones`, `activities`.

`latest_activities`: filtros opcionais (`user_id`, datas, `gym_id`, `conducted_by`, `q` LIKE em `name`, `session_status`), JOIN `gyms`, `training_types`, `workout_plans` e subquery `SUM(exercises.volume_kg)`; `ORDER BY local_date DESC, id DESC LIMIT/OFFSET`.

`activity_detail`: `a.*` + nomes; exercícios com `mapped_name` (primeiro `exercise_external_mappings` AMAZFIT ativo), `exercise_name`/`exercise_description` (`exercise_catalog`), `muscle_primary`/`muscle_secondary` (primeira contribuição PRIMARY/SECONDARY), `equipment_catalog_id`, `equipment_name`, `equipment_has_photo`, `equipment_photo_url = 'https://app.musclefit.com.br/' + photo_path`, `planned_mapping_id`, `planned_exercise_name`; séries por exercício (N+1 queries).

`list_exercise_logs`: mesma forma por exercício; com qualquer filtro de `user_id`/data usa `LIMIT 2000` em vez de `limit`; séries `set_number, reps, weight_kg, rpe` por exercício (N+1).

`get_last_session_sets(mapping_id, exclude_activity_id, user_id, equipment_id)`: exercício mais recente (`ORDER BY local_date DESC, a.id DESC`) com mapping canônico igual, atividade diferente; com `equipment_id`, exige `ge.equipment_id = (SELECT equipment_id FROM gym_equipment WHERE id = %s)`; retorna `{activity_id, local_date, sets}` ou `None`.

`high_rpe_sets`: séries com `rpe >= min_rpe`. `exercise_history`: `e.original_name LIKE %name%`.

#### 5.4 `update_exercise_log` e `delete_exercise_log`

`update_exercise_log`: chama `workout_sessions._ensure_schema()`. Preserva `mapping_id` quando o payload não traz `mapping_id` nem `mapped_name`; preserva `equipment_id` quando não traz `equipment_id` nem `equipment_name`; `muscle_pain`/`joint_pain` preservados quando `None`; `workout_plan_exercise_id` só muda se `workout_plan_exercise_id_provided`. `UPDATE activities SET local_date=%s, gym_id=COALESCE(%s,gym_id), conducted_by=COALESCE(...), started_at=COALESCE(...)`; atualiza agregados do exercício; `DELETE FROM exercise_sets` e reinsere (`set_number = st.set_number or index`); `_recompute_activity_avg_rpe` (`ROUND(AVG(avg_rpe),2)`).

`delete_exercise_log`: apaga séries e exercício, recalcula `avg_rpe` da atividade, retorna `deleted_activity: False` (a atividade permanece mesmo sem exercícios).

#### 5.5 Progressão determinística — `progression_service.suggest_load_and_reps`

Entrada: `exercise_name`, `reps_target` (ex. `"8-12"`, regex `(\d+)\s*-\s*(\d+)`), `recent_exercise_logs` (formato de `list_exercise_logs`, mais recente primeiro), `equipment_name?`, `mapping_id?`, `is_advanced_user`.

Seleção da entrada (`_find_most_recent_entry`): se `mapping_id` e houver entradas com esse `mapping_id`, prefere a primeira com `equipment_name` igual (normalizado lower/strip), senão a primeira; sem match por id, compara `original_name` normalizado (primeiro com equipamento igual, depois qualquer). `mapped_name` nunca é comparado.

Decisão, com `sets` = séries com `weight_kg` não nulo, `current_load = sets[-1].weight_kg`, `last_reps = sets[-1].reps`, `previous_rpes = rpes[:-1]`, `last_rpe = rpes[-1]`:

| Ordem | Condição | `suggested_load_kg` | `suggested_reps` | `progression_note` |
|---|---|---|---|---|
| 1 | sem entrada ou sem séries com peso | `None` | `None` | "Sem histórico registrado — comece conservador dentro da faixa {reps_target}." (`trend="unknown"`) |
| 2 | nenhuma série com RPE | `round(current_load,2)` | `last_reps` | "Sem RPE registrado na última sessão — mantenha a carga e repetições." (`trend="same"`) |
| 3 | todas as reps das séries `working` ≥ `reps_hi` (o RPE das séries anteriores à última não entra — D4 revisada, 2026-09-14) | `current_load` + o incremento percentual do legado (`_adjust` em `progression_service.py`; sem passo do equipamento — divergência C3 do M76, sai no Lote 1a) | `reps_lo or last_reps` | "Teto de repetições atingido em todas as séries — pronto para progressão de carga." |
| 4 | `last_rpe ≤ 8` | `current_load` | `min(last_reps + 1, reps_hi)` | "RPE baixo na última sessão — pronto para progressão de carga." (a nota diz "progressão de carga" com a carga mantida — achado C11, tratado no Lote 1a) |
| 5 | demais | `current_load` | `last_reps` | "Mantenha a carga da última sessão." |

`trend`: `unknown` se carga sugerida ou última é `None`; `up`/`down` pela carga; se igual, compara reps; senão `same`. `progression`: `up→increased`, `down→decreased`, `same→same`, `unknown→None`. Saída: `suggested_load_kg`, `suggested_reps`, `progression_note`, `last_load_kg`, `last_reps`, `trend`, `progression`.

`services.suggested_progression_for_mapping(mapping_id, user_id)`: resolve id canônico; busca `exercise_catalog.name` e nome AMAZFIT (sem linha → `None`); `list_exercise_logs(limit=300, user_id)`; entrada = primeira com `mapping_id` canônico igual, senão `_find_most_recent_entry(name)`; `reps_target = f"{min(reps)}-{max(reps)+2}"` ou `"8-12"`; `rpe_target` pela média arredondada dos RPE: `≤7 → "7-8"`, `==8 → "8-9"`, senão `"9-10"`; sem RPE `"8-9"`; chama `suggest_load_and_reps(name, reps_target, logs, mapping_id=canônico, is_advanced_user=...)`. Retorna `reps_target`, `load_target = normalize_load_target(suggested_load_kg) or ""`, `rpe_target`, `progression_note`, `has_history`, `progression`, `last_load_kg`, `last_reps`, `trend`, `last_date`.

`services.suggested_set_count_for_substitution(original_mapping_id, original_sets_count, new_mapping_id)`: ids canônicos; grupo primário do original = contribuição `PRIMARY` ativa de maior `stimulus_weight` (desempate `id ASC`); sem PRIMARY → `None`. Peso do novo para o mesmo grupo = maior `stimulus_weight` ativo (qualquer role). Sem contribuição ou peso ≤ 0 → `suggested_sets_count=None`, `reason="no_contribution_to_original_primary_group"`. Caso normal: `max(1, round(original_sets × w_original / w_new))`. Saída inclui `original_primary_muscle_group`, `original_stimulus_weight`, `new_stimulus_weight`, `original_sets_count`.

#### 5.6 Volume — `intelligence/volume_service.VolumeService`

Constantes de módulo: `OUTROS_GROUP="Outros"`, `DENSITY_WINDOW_HOURS=72.0`, `LOAD_REFERENCE_WINDOW_DAYS=28`, `MIN_REFERENCE_OBSERVATIONS=2`, `NEW_EXERCISE_OCCURRENCE_THRESHOLD=3`, `RPE_TARGET_NUMBER_RE=(\d+(?:[.,]\d+)?)`, `ON_TARGET_TOLERANCE_PCT=0.05`, `DASHBOARD_FALLBACK_MIN_SETS=6.0`, `DASHBOARD_FALLBACK_TARGET_SETS=9.0`, `DASHBOARD_FALLBACK_MAX_SETS=27.0`.

Acesso a dados via `IntelligenceServiceBase.fetch_all` (abre `get_conn()`, executa, converte `Decimal→float` e `date/datetime→isoformat`, fecha a conexão sem commit).

**Fontes**

- `_fetch_window_sets(days, user_id)`: uma linha por `exercise_sets` de atividades com `local_date >= DATE_SUB(CURDATE(), INTERVAL days DAY)`: `set_id, exercise_id, set_number, reps, weight_kg, rpe, set_type, activity_id, local_date, activity_name, activity_type, duration_seconds`, `trained_at = COALESCE(a.started_at, a.ended_at, a.created_at)`, `primary_group`, `secondary_group`, ids, `mapping_id = exercise_catalog.id` (via `exercise_legacy_id_map`), `secondary_participation` = `stimulus_weight` do primeiro SECONDARY. `ORDER BY exercise_id, set_number`.
- `_fetch_full_history_sets(user_id)`: mesmo shape sem filtro de dias, mais `raw_payload_workout_plan_id = JSON_UNQUOTE(JSON_EXTRACT(a.raw_payload,'$.workout_plan_id'))`, `original_name`, `muscle_pain`, `joint_pain`, `mapping_id` canônico.
- `_fetch_plan_rpe_targets(plan_ids)`: lookup `(plan_id, ("mapping", mapping_id))` e `(plan_id, ("name", exercise_name.lower()))` → `rpe_target`. `_parse_rpe_target` = maior número do texto.
- `_fetch_contributions_by_mapping(mapping_ids)`: `SELECT exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight FROM exercise_muscle_contributions WHERE exercise_id IN (...) AND is_active = 1`.
- `_resolve_contributions(mapping_id, contributions_by_mapping, primary, secondary, primary_id, secondary_id, secondary_participation)`: com linhas explícitas → lista `{muscle_group_id, role, stimulus_weight, fatigue_weight, muscle_group_name=None}`; sem linhas → `_group_contributions`: primário (peso 1.0, role PRIMARY) + secundário distinto (peso `secondary_participation` ou 0.50, role SECONDARY); só secundário → tratado como PRIMARY; nenhum → `("Outros", PRIMARY, None)`; em fallback `stimulus_weight == fatigue_weight`.
- `_exercise_identity(row)`: `("mapping", mapping_id)` ou `("name", original_name.strip().lower())`. `_session_sort_key(row)`: `trained_at` parseado ou `local_date` 00:00 ou `datetime.min`.

**`_compute_session_dose_context(all_rows)`**

1. `occurrence_rank[(identity, activity_id)]`: posição cronológica (1-based) da sessão entre as sessões daquela identidade.
2. `best_e1rm_by_identity_session`: maior `MuscleRecoveryService.estimated_1rm(weight, reps, rpe)` entre séries com os três valores.
3. `reference_e1rm_by_identity_session`: mediana dos melhores e1RM de sessões da mesma identidade com `session_date − 28 dias ≤ other_date < session_date`; exige ≥ 2 valores, senão `None`.
4. `performance_ratio_by_session` (desde 2026-10-06, `intelligence/exercise_performance.py`): razão da ÚLTIMA sessão de cada exercício contra a última exposição de outra sessão na janela de `exposure_lookback_days`, pela fonte única `dashboard/comparability` (exposições montadas por `exposure_service.assemble_exposures`, só séries `working`). Chave diferente (exercício+equipamento+convenção) → sem entrada. `REGRESSING` → magnitude ≤ 1 (razão de e1RM quando decisivo — série do e1RM com ≤ 12 reps nos dois lados —, senão equivalente Epley na mesma carga das reps emparelhadas de topo: `(1 + (reps_atual_méd + RIR)/30) / (1 + (reps_anterior_méd + RIR)/30)`, RIR = max(0, 10 − RPE médio), sem RPE em algum lado → RIR 0 nos dois); demais status → magnitude com piso 1,0.
4. `plan_id_by_activity` (`_extract_workout_plan_id` aceita escalar, dict ou string JSON) e `plan_rpe_lookup`.

**`get_muscle_group_overview(days=14, limit=8, user_id)`**

1. `raw_sets = _fetch_window_sets`; `all_rows = _fetch_full_history_sets`; `is_advanced_user = _is_advanced_user(user_id)`; `context`.
2. `contributions_by_mapping` para todos os `mapping_id` de `all_rows`; lookup `muscle_groups id→name` se houver contribuições explícitas.
3. `isolated_rpe10_set_ids`: por exercício com ≥ 2 séries (janela), última série RPE ≥ 10 e todas as anteriores sem RPE ou ≤ 9.
4. Loop 1 — janela, por série e por contribuição (nome = `muscle_group_name` ou lookup por id ou `Outros`):
   - brutos: `activity_ids`, `sets_count += 1`, `reps_total += reps or 0`, `volume_kg += (reps or 0) × weight`, somas de peso e RPE, `last_trained_at` (maior `local_date` como string), `last_trained_at_ts` (maior `trained_at`).
   - ponderados: PRIMARY → `direct_sets += 1`, sessão direta, `last_direct_training_at`, `direct_rpe_*`; SECONDARY → `indirect_sets += 1`, `indirect_rpe_*`; STABILIZER → `stabilizer_sets += 1`, `indirect_rpe_*`; sempre `stimulus_equivalent_sets += stimulus_weight`, `fatigue_equivalent_sets += fatigue_weight`, `participation_sessions`, `last_participation_at`.
   - `recent_sets` com `effort_points(rpe or 7.0)`, `repetition_factor(reps or 8)`, `set_type_factor`, `participation=fatigue_weight`, `rpe10_isolated_last_set`.
5. Loop 2 — histórico completo agrupado por `(exercise_id, activity_id)`:
   - `is_new = occurrence_rank ≤ 3` (default rank 1); `load_spike_f = load_spike_factor(current_e1rm, reference_e1rm)`; `exercise_f = exercise_factor(is_new)`; `performance_ratio = performance_ratio_by_session[(exercise_id, activity_id)]` ou `None` (sem par comparável). A referência de 28 dias só alimenta `load_spike_f`.
   - `excessive_rep_drop`: primeira e última série (por `set_number`) distintas, ambas com reps e peso, mesmo peso, `first.reps > 0`, `1 − last.reps/first.reps > 0.30`.
   - `rpe_anticipated` (só se o plano da atividade tem `rpe_target`): alguma série com `rpe ≥ rpe_target + 1.0`, ignorando série com RPE ≥ 10 quando (`is_advanced_user`) ou (é a última série e todas as anteriores sem RPE ou ≤ 9).
   - Para cada contribuição cujo grupo já existe em `groups` (grupos só fora da janela são ignorados): `rpe10_count` = séries com RPE ≥ 10; `dose_here = Σ_séries [effort_points(rpe or 7.0) × fatigue_weight × repetition_factor(reps or 8) × set_type_factor(set_type or "working") × load_spike_f × exercise_f] + failure_penalty(rpe10_count) × fatigue_weight`; acumula `dose_by_activity`, `max_participation_by_activity = max(fatigue_weight)`, `activity_sort_key`. Se role PRIMARY: `performance_ratios_by_activity.append`, `rpe_anticipated_by_activity`, `rep_drop_by_activity`, `muscle_pain_by_activity` (máximo), `joint_pain_by_activity`.
6. Por grupo: `days_since_trained = (today − date(last_trained_at)).days`; `hours_since_trained = max((now − last_trained_at_ts)/3600, 0)`; `sets_last_day` (séries com mesma data da mais recente em `recent_sets`); `last_set_rpe`; sessão mais recente = maior `activity_sort_key` → `session_dose`, `session_participation` (default 1.0), `performance_ratio` (média), `rpe_anticipated`, `excessive_rep_drop`, `muscle_pain`, `joint_pain`; `dose_last_72h` = soma de `dose_by_activity` das sessões com `sort_key ≥ now − 72h`; `activity_dose_history` (cronológico: `activity_id, trained_at, local_date, dose (3 casas), participation`); `factors = {session_dose, dose_last_72h, avg_rpe_used, recent_sets[:5], rpe10_isolated_note?}`.
7. `results.sort(key=(volume_kg, sets_count), reverse=True)[:limit]`.

Campos de saída: `muscle_group, muscle_group_id, sessions, sets_count, reps_total, volume_kg, avg_weight_kg, avg_rpe, last_trained_at, last_trained_at_ts, hours_since_trained, days_since_trained, sets_last_day, last_set_rpe, session_dose, session_participation, dose_last_72h, performance_ratio, rpe_anticipated, excessive_rep_drop, muscle_pain, joint_pain, factors, activity_dose_history, direct_sets, indirect_sets, stabilizer_sets, stimulus_equivalent_sets, fatigue_equivalent_sets, direct_sessions, participation_sessions, last_direct_training_at, last_participation_at, direct_rpe_avg, indirect_rpe_avg`.

**Demais métodos**

| Método | Algoritmo |
|---|---|
| `get_equivalent_sets_by_activity(limit=400, user_id)` | Por exercício (`COUNT(es.id)` como `sets_count`, mapping canônico, `ORDER BY local_date DESC LIMIT`); `Σ sets_count × stimulus_weight` por `(activity_id, muscle_group_name)` com contribuições de `_resolve_muscle_contributions_for_mappings`; só `> 0`; saída `{activity_id, local_date, muscle_group, equivalent_sets}`. |
| `get_muscle_activation_by_activity(activity_id, user_id)` | Exercícios da atividade com `mapping_id IS NOT NULL` (`sets_count` de `exercises.sets_count`); `avg_weight_kg` por exercício = `AVG(weight_kg)` de séries com peso; `_build_muscle_activation_rows`. |
| `_build_muscle_activation_rows(rows, contributions)` | Para cada contribuição com nome: prefixo `primary` (PRIMARY), `stabilizer` (STABILIZER), `secondary` (demais); `{p}_series += sets_count`; `{p}_weighted += sets_count × stimulus_weight`; com peso, `{p}_weight_sum += avg_weight × sets_count`, `{p}_sets_with_weight += sets_count`. Saída por grupo: `primary_series` (int), `primary_avg_weight_kg`, `primary_equivalent`, idem secondary/stabilizer, `total_equivalent`; ordenado por `total_equivalent` desc. |
| `get_training_type_weekly_targets_for_activity(activity_id, user_id)` | (1) `activities.workout_plan_id` → `_resolve_targets_from_plan_template_slots`: `raw_payload` do plano, `_apply_effective_max_sets_fallback`, `template_slot_targets[]` → `{muscle_group_id, muscle_group_name, sets_count, max_weekly_sets = effective_max_sets}`; (2) `activities.training_type_id` → `training_types._get_training_type` → `{..., sets_count = target_weekly_sets, max_weekly_sets}`; (3) `None`. |
| `get_muscle_activation_for_exercises(exercises)` | Itens com `mapping_id` e `sets_count` → `_build_muscle_activation_rows` com `avg_weight_kg=None`. |
| `get_muscle_activation_breakdown_by_exercise(exercises)` | Um item de saída por entrada (mesma ordem): `primary_activation = Σ PRIMARY sets×w`; `secondary`/`stabilizer` = listas `{muscle_group_name, activation}` desc; entrada inválida → zeros. |
| `get_recent_exercise_volume(user_id, limit, date_from, date_to)` | Por exercício: `muscle_group = COALESCE(NULLIF(pg.name,''), NULLIF(sg.name,''), 'Outros')`, `equipment_name`, `sets_count`, `volume_kg = Σ COALESCE(reps,0)×COALESCE(weight,0)`, `avg_rpe`, `last_weight_kg` (série de maior `set_number`); `LIMIT 5000` se há datas, senão `limit or 400`. Agrega por `(activity_id, muscle_group, equipment_name)`: soma sets/volume, RPE ponderado por `sets_count`, `last_weight_kg` do maior `exercise_id`. Ordena por `(local_date, activity_id, muscle_group)` desc. |
| `get_daily_muscle_activation(days=7, user_id)` | Por exercício (`e.sets_count`, `had_rpe_9_10 = MAX(rpe BETWEEN 9 AND 10)`); por `(local_date, muscle_group)`: `sets_count = Σ sets_count × stimulus_weight` (todos os roles), `had_rpe_9_10` OR; só `> 0`; ordena por data e nome. |
| `get_lowest_volume_groups(days=14, limit=3, user_id)` | `COUNT(es.id)` e `volume_kg` por `COALESCE(primary, secondary, 'Outros')`, `ORDER BY sets_count ASC, volume_kg ASC LIMIT`. |
| `get_volume_breakdown(days=30, user_id)` | `get_muscle_group_overview(days, limit=32)`; por linha `estimated_recovery_hours = calculate_recovery_hours(session_dose, session_participation)` se dose > 0; `volume_status = "excesso"` se `avg_rpe ≥ 8.8 and sets_count ≥ 18`, `"subtreino"` se `sets_count ≤ 6 or days_since_trained ≥ 10`, senão `"ideal"`. Retorna `{days, items}`. |
| `get_volume_by_split_day(days=180, user_id)` | `days = clamp(days, 7, 180)`; `JOIN workout_plans wp ON wp.id = a.workout_plan_id` com `plan_type='abc'` e `split_day IN ('A','B','C')`; `period = DATE_FORMAT(DATE_SUB(local_date, INTERVAL WEEKDAY(local_date) DAY), '%Y-%m-%d')`; `volume_kg = SUM(COALESCE(weight,0) × COALESCE(reps,0) × COALESCE(rpe,7.0))`; `GROUP BY period, split_day`. |
| `get_volume_trend(days=90, user_id)` | Por `DATE_FORMAT(local_date,'%Y-%m-%d')` e grupo `COALESCE(primary, secondary, 'Outros')`: `sets_count`, `reps_total`, `volume_kg`, `avg_weight_kg`, `avg_rpe`. `{days, items}`. |
| `get_rolling_weekly_trend_by_muscle_group(days, user_id)` | `bucket_index = FLOOR(DATEDIFF(CURDATE(), local_date)/7)`; filtro `LOWER(activity_type) LIKE '%muscul%' OR '%forÃ§a%' OR '%strength%'` (literal `forÃ§a` no código); por série: bucket legado de grupo único (`sets_count`, `reps_total`, `volume_kg`, `rpe`) + por contribuição (`direct_sets` PRIMARY, `indirect_sets` SECONDARY/STABILIZER, `stimulus_equivalent_sets += stimulus_weight`); `period = f"D-{i*7} a D-{i*7+6}"`, `period_start/end`; ordenado por `bucket_index` desc, nome asc. |
| `get_planned_effective_sets(user_id, date_from, date_to)` | `workout_plans` com `COALESCE(plan_date, DATE(created_at)) BETWEEN`; soma `raw_payload.template_slot_targets[].sets_count` por `muscle_group_id` (ou nome); `plans_count` = planos distintos; desc. |
| `get_top_exercises_for_muscle_group(muscle_group_id, days, user_id, limit=5)` | Séries da janela cujas contribuições têm o grupo; por `mapping_id` (ou `("exercise", exercise_id)`): `sets_count += 1`, `stimulus_equivalent_sets += w`, `last_trained_at`; nome = `COALESCE(ec.name, nome AMAZFIT, 'Exercício sem nome')`, sem mapping → "Exercício não mapeado"; desc por estímulo; `[:limit]`. |
| `get_progression_signal_counts(user_id, days=30)` | Histórico completo → `exercise_performance.progression_status_counts`: para cada exercício com a última sessão dentro de `now − days`, `classify_exercise_progression` (fonte única) sobre as exposições da janela de `exposure_lookback_days` que termina nela; PROGRESSING → `increased`, REGRESSING → `decreased`, STABLE/PLATEAU_SUSPECTED → `same`, INSUFFICIENT_DATA/NOT_COMPARABLE → `insufficient_data`. Sem banda própria (antes ±1% de e1RM bruto). |
| `get_workout_sessions_timeline(user_id, days, limit=200)` | Janela; por atividade: `exercise_ids`, `sets_count`, RPE médio, `Σ stimulus_weight` e `Σ fatigue_weight` de todas as contribuições de todas as séries; ordena por `(local_date, activity_id)` desc; `[:limit]`. |
| `get_stimulus_fatigue_ratio_by_exercise(days=90, user_id, muscle_group_id, activity_id, limit=20)` | `activity_id` → histórico completo filtrado em Python; senão janela; ignora séries sem mapping; contribuições filtradas por grupo se informado; por mapping: `sets_count`, `stimulus`, `fatigue`, `ratio = round(stimulus/fatigue, 3)` ou `None`; desc por estímulo. |
| `get_exercise_session_history(mapping_id, user_id, days=180)` | Séries da janela com o mapping; por sessão: `best_load_kg`/`best_reps` do set de maior e1RM (`>=`), `avg_rpe`, `e1rm`; cronológico ASC. |

Funções puras do módulo:

- `classify_weekly_volume_status(actual, min_sets, target_sets, max_sets)`: `tol = target×0.05`; `actual < min → BELOW_MIN`; `< target−tol → BETWEEN_MIN_TARGET`; `≤ target+tol → ON_TARGET`; `≤ max → BETWEEN_TARGET_MAX`; senão `ABOVE_MAX`.
- `sum_projected_sets_from_pending_slots(slots, pending_sequences)`: soma `sets_count` dos itens `item_kind == 'muscle_group'` dos slots cujo `sequence` está pendente, por `muscle_group_id`.

`intelligence/muscle_contribution_resolver.py`:

- `_resolve_muscle_contributions_for_mappings(mapping_ids)`: `VolumeService()._fetch_contributions_by_mapping`; mapping sem linhas → `[]` (sem fallback); com linhas → `_resolve_contributions` + nome via `SELECT id, name FROM muscle_groups`.
- `_accumulate_exercise_contribution(exercise, contributions, totals)`: `totals[muscle_group_id] += sets_count × stimulus_weight` (ignora sem mapping/sets).

#### 5.7 Recuperação muscular — `intelligence/muscle_recovery_service.MuscleRecoveryService`

Constante de módulo `DENSITY_REFERENCE_DOSE = 7.5`. Funções puras (estáticas):

| Função | Definição |
|---|---|
| `effort_points(rpe)` | RPE clamp [6.5, 10.0]; interpolação linear entre `(6.5,0.15),(7.0,0.30),(7.5,0.45),(8.0,0.60),(8.5,0.72),(9.0,0.85),(9.5,0.95),(10.0,1.05)` |
| `repetition_factor(reps)` | `≤4 → 0.90`; `≤20 → 1.00`; `≤30 → 1.05`; senão `1.10` |
| `set_type_factor(set_type)` | `warmup 0.10`, `working 1.00`, `backoff 0.95`, `dropset 1.15`, `feeler 0.20`, `rest_pause 1.10`, `drop_set 1.15`, `forced_rep 1.05`; desconhecido `1.00` |
| `estimate_rir(rpe)` | `max(0, 10 − rpe)` |
| `estimated_1rm(load, reps, rpe)` | `load × (1 + (reps + rir)/30)`; `0.0` se ausentes ou ≤ 0 |
| `load_spike_factor(current, reference)` | sem valor → 1.00; `inc = current/reference − 1`; `≤0.05 → 1.00`; `≤0.10 → 1.03`; senão `1.06` |
| `exercise_factor(is_new)` | `1.05` / `1.00` |
| `participation(role, secondary_participation)` | primary 1.00; secondary = valor ou 0.30; outros 0.00 (não usado no cálculo principal, que usa `fatigue_weight`) |
| `failure_penalty(n)` | `0.25 × max(0, n − 1)` |
| `recovery_floor_hours(p)` | `clamp(18 × p, 0, 18)` |
| `calculate_recovery_hours(dose, p)` | `clamp(14 + 4×dose + 1.2×max(0, dose−3)², floor(p), 72)` |
| `apply_personal_coefficient(h, c, floor=18)` | `c = clamp(c or 1.00, 0.90, 1.10)`; `clamp(h×c, floor, 72)` |
| `compute_global_readiness_factor(tsb)` | `None → 1.0`; `clamp(1 − tsb/150, 0.90, 1.15)` |
| `apply_global_readiness_factor(h, tsb, floor=18)` | `tsb None → h`; `clamp(h × factor, floor, 72)` |
| `calculate_time_recovery(elapsed, estimated)` | `estimated ≤ 0 → 100`; `clamp(elapsed/estimated × 100, 0, 100)` |
| `calculate_density_penalty(dose72, ref=7.5)` | `ratio = dose72/ref`; `≤1.10 → 0`; `≤1.25 → 10`; senão `20` |
| `calculate_adjusted_readiness(t, pen)` | `clamp(t − pen, 0, 100)` |
| `count_negative_signals(ratio, anticipated, drop, pain)` | soma booleana: `ratio < 0.95`, `anticipated`, `drop`, `pain ≥ 4` |
| `classify_readiness(adj, ratio, anticipated, drop, pain, joint)` | `joint` → NO_DIRECT_WORK; `pain > 6` → NO_DIRECT_WORK; `adj < 55` ou `ratio < 0.90` ou negativos ≥ 2 → NO_DIRECT_WORK; `adj < 75` ou negativos == 1 → REDUCED_DOSE; senão READY |
| `reconcile_recovery_pct(adj, status, joint, pain, neg, ratio)` | NO_DIRECT_WORK: severo (joint ou pain > 6) → `min(adj, 20)`; `neg ≥ 2` ou `ratio < 0.90` → `min(adj, 45)`; senão `adj`; depois `min(…, 54)`. REDUCED_DOSE: `clamp(adj, 55, 74)`. READY: `adj`. Retorna `(valor, round(max(0, adj − valor), 1))` |

`_RECOMMENDATIONS`:

| status | volume_multiplier | direct_sets_min | direct_sets_max | maximum_rpe | allow_failure_on_last_set | allow_progression | allow_indirect_work |
|---|---|---|---|---|---|---|---|
| READY | 1.00 | 2 | 3 | 10 | True | True | True |
| REDUCED_DOSE | 0.75 | 1 | 2 | 9 | False | False | False |
| NO_DIRECT_WORK | 0.00 | 0 | 0 | 8 | False | False | True |

`_STATUS_META`: READY → `("green","ideal")`; REDUCED_DOSE → `("yellow","recovering")`; NO_DIRECT_WORK → `("red","avoid")`. Status desconhecido cai em NO_DIRECT_WORK nos dois lookups.

Orquestração:

- `_get_personal_coefficient(muscle_group_id, user_id)`: `None` id → 1.00; `effective_user = user_id or _admin_user_id()`; `SELECT coefficient FROM muscle_recovery_coefficients WHERE user_id=%s AND muscle_group_id=%s`; default 1.00.
- `_get_global_tsb(user_id)`: `get_latest_intervals_wellness(user_id)` (`SELECT * FROM intervals_wellness [WHERE user_id] ORDER BY wellness_date DESC LIMIT 1`); exceção → `(None, None)` com `logger.exception`.
- `get_recovery_overview(days=14, limit=24, user_id)`: para cada linha de `get_muscle_group_overview(days, limit, user_id)`:
  - `has_session = session_dose > 0`; `participation = session_participation or 1.0`; `floor = recovery_floor_hours(participation)`.
  - `estimated_hours = apply_global_readiness_factor(apply_personal_coefficient(calculate_recovery_hours(dose, participation), coef, floor), tsb, floor)`.
  - `elapsed = hours_since_trained` ou `max(days_since_trained, 0) × 24`.
  - com sessão: `time_recovery`, `density_penalty(dose_last_72h)`, `adjusted`, `readiness_status`, `negative_signals`, `reconcile_recovery_pct`, `remaining_hours = round(max(estimated − elapsed, 0), 2)`; sem sessão: 100 / 0 / READY / 0.
  - `explanation[]` (pt-BR): "A última sessão gerou dose {x}.", "Já decorreram {h} horas (estimativa de recuperação: {e}h).", penalidade de densidade, performance < 0.95, RPE antecipado, queda de repetições, dor muscular > 0, dor articular; sem sessão: "Nenhuma sessão recente registrada para este grupo na janela analisada."
  - Adiciona à linha: `recovery_pct` (1 casa), `estimated_recovery_hours`, `remaining_hours`, `status`, `guidance`, `readiness_status`, `session_dose` (3 casas), `dose_last_72h`, `density_penalty`, `signal_penalty`, `performance_ratio` (3 casas), `recommendation`, `explanation`, `last_direct_training_at`, `last_participation_at`.
- `get_global_readiness_adjustment(user_id)`: `{form_tsb (2 casas), factor (3 casas), wellness_date, stale, max_age_days}` ou `None` (sem wellness).
- **Idade máxima do wellness (M52)**: `intelligence/wellness_freshness.WELLNESS_MAX_AGE_DAYS = 3`, contada no fuso do usuário (`users.timezone`, que `get_latest_intervals_wellness` traz no mesmo SELECT via JOIN). Último wellness com mais de 3 dias → `tsb=None` (fator **neutro**, idêntico a "sem Intervals"), e o ajuste exposto vem com `stale=true`, `factor=1.0`, o TSB ignorado e a data do último wellness. Com dado fresco nada muda. O prompt de geração de ficha (`format_intervals_wellness_section`) omite a seção inteira quando o dado está velho. `/dashboard/recovery` mostra o aviso em amarelo no bloco "Ajuste global de fadiga".
- `get_consecutive_day_recovery_checks(days=7, user_id)`: `get_muscle_group_overview(days, limit=64)`; para cada grupo agrupa `activity_dose_history` por data (a partir de `today − days`): soma dose, máxima participação, primeiro e último `trained_at`; para cada par de datas com diferença de 1 dia: `elapsed = first_ts(D2) − last_ts(D1)` em horas (`timestamp_precision="exact"`) ou `24` (`"day_fallback"`); `required_hours` = dose D1 → horas → coeficiente → TSB atual; saída `{muscle_group_id, muscle_group, date_from, date_to, elapsed_hours, required_hours, insufficient_recovery, timestamp_precision}`.

#### 5.8 Projeção de recuperação — `workout_plan_ai.project_recovery_for_date(items, plan_date, plan_time)`

`target = datetime.combine(date.fromisoformat(plan_date), time.fromisoformat(plan_time) or 00:00)`; `plan_date` inválido ou ausente → `now`. `elapsed_to_target = max((target − now)/3600, 0)`.

Para cada item:

1. Se `elapsed_to_target > 0`: `performance_ratio=None`, `rpe_anticipated=False`, `excessive_rep_drop=False`, `muscle_pain=None`, `joint_pain=False`. Senão usa os valores do item.
2. Se `session_dose > 0 and estimated_recovery_hours > 0`: `time_recovery = calculate_time_recovery(hours_since_trained + elapsed_to_target, estimated_recovery_hours)`; `dose72 = 0 if elapsed_to_target ≥ 72 else dose_last_72h`; `adjusted = calculate_adjusted_readiness(time_recovery, density_penalty(dose72))`. Senão `adjusted = 100`.
3. `projected_readiness_status = classify_readiness(adjusted, ...)`.
4. Saída: item + `projected_recovery_pct` (1 casa), `projected_readiness_status`, `available_for_plan_date = status != "NO_DIRECT_WORK"`.

Não reaplica `reconcile_recovery_pct`.

#### 5.9 Motor Full Body — `workout_plan_ai.generate_plan_draft`

1. **Contexto** (`gather_ficha_context(gym_id, user_id)`):
   - `recent_exercise_logs = _trim_recent_exercise_logs(services.list_exercise_logs(limit=300, user_id))` → `{mapping_id, original_name, equipment_name, sets:[{reps, weight_kg, rpe}]}`.
   - `high_rpe_sets = _trim_high_rpe_sets(services.high_rpe_sets(min_rpe=9, limit=15, user_id))` → `{original_name, local_date, reps, weight_kg, rpe}`.
   - `muscle_recovery = _trim_muscle_recovery(MuscleRecoveryService().get_recovery_overview(days=14, limit=24, user_id))` (remove `factors`, `activity_dose_history`).
   - `gyms = _load_gyms()` (direto do banco, `gyms.list_gym_items`; SEG-04).
   - com `gym_id`: `gym_equipment_recommendation = _trim_gym_equipment_recommendation(equipment_recommender.recommend_all(conn, gym_id, user_id))`; sem: `exercise_catalog = _trim_exercise_catalog(_load_exercise_catalog())` (direto do banco, `exercise_mapping_catalog.query_mapping_catalog`, limite 500; SEG-04) (remove `created_at, updated_at, mapping_status, source, is_active, primary_group_id, secondary_group_id`).
2. `gym_name = resolve_gym_name(gym_id, gym_name, gyms)` (nome informado, senão nome do gym pelo id).
3. `project_recovery_for_date`; `unavailable_groups` (NO_DIRECT_WORK), `reduced_dose_groups`, `reduced_dose_group_ids`.
4. **Slots** (`build_fullbody_slots(muscle_recovery, gym_equipment_recommendation, target_size=10)`):
   - exclui `projected_readiness_status == NO_DIRECT_WORK`; com recomendação, exclui grupos cujo `groups[nome].has_equipment_for_group` é falso/ausente;
   - ordena por `hours_since_trained` desc (ausente/inválido = infinito) e corta em 10;
   - `dose_level = REDUCED_DOSE` se projetado REDUCED_DOSE, senão READY; `sets_count_baseline = {READY: 4, REDUCED_DOSE: 2}`;
   - `category` por `FULLBODY_CATEGORY_BY_GROUP_ID`: {4, 13, 8, 2, 53, 51, 9} → Membros Inferiores; {52, 12} → Peitoral; {6, 7} → Costas; {10} → Ombros; {5, 14, 3, 50} → Braços e Antebraços; {1} → Abdômen; sem id usa `FULLBODY_CATEGORY_BY_GROUP` por nome;
   - sort estável por `FULLBODY_CATEGORY_ORDER = [Membros Inferiores, Peitoral, Costas, Ombros, Braços e Antebraços, Abdômen]` (não mapeado = posição 6);
   - `sequence` 1..N. Lista vazia → `WorkoutPlanGenerationError("Nenhum grupo muscular disponivel para montar a ficha Full Body nesta data ...")`.
5. **Rotadores obrigatórios** (`build_mandatory_rotator_slots`): para cada categoria presente em ordem (Costas, Peitoral) → slot `{muscle_group: nome vivo de id 56 ou "Manguito rotador", muscle_group_id: 56, dose_level: REDUCED_DOSE se 56 ∈ reduced_dose_group_ids ou nome em reduced_dose_groups, sets_count_baseline, category, fixed_exercise_name: "Rotação Externa do Ombro na Polia" (Costas) / "Rotação Interna do Ombro na Polia" (Peitoral), mandatory_rotator: True, sequence: 0}`. Não aplica exclusão por NO_DIRECT_WORK.
6. `recent_exercises_by_group = get_recent_fullbody_exercise_names_by_group(gym_id, limit_plans=4, plan_type="full_body", user_id)`: ids dos últimos 4 `workout_plans` (`ORDER BY created_at DESC`, filtro opcional `gym_id`, `user_id`) → `workout_plan_exercises.muscle_primary → [exercise_name]` do mais recente ao mais antigo.
7. `rag_text = select_rag_text(sorted({slot.muscle_group}))`; `intervals_wellness_section = format_intervals_wellness_section(get_latest_intervals_wellness(user_id))`.
8. `build_prompt` → `call_openai` → `_extract_json` (regex `\{.*\}` com DOTALL; `JSONDecodeError` → `WorkoutPlanGenerationError`).
9. `draft.setdefault("plan_type", plan_type)`, `setdefault("goal", goal)`, `plan_date`, `gym_id`, `gym_name`.
10. **Bounds** (`_apply_slot_bounds(slot, ai_exercises_by_sequence[slot.sequence])`):
    - `exercise_name = fixed_exercise_name` ou nome da IA ou `f"Exercício ({muscle_group})"`;
    - `sets_count`: int da IA dentro de `[max(1, baseline−1), baseline+1]`, senão baseline (`baseline or 3`);
    - `user_lo = max(15, min(min_rest, max_rest))`, `user_hi = min(240, max(min_rest, max_rest))`, fallback = `round((lo+hi)/2)`; `rest_seconds` int da IA fora de [15, 240] → fallback; depois clamp `[user_lo, user_hi]`;
    - `rpe_target` vazio em REDUCED_DOSE → `"8-9"`;
    - devolve `sequence, exercise_name, amazfit_name, muscle_primary = slot.muscle_group, muscle_group_id, muscle_secondary, equipment_name, sets_count, reps_target, load_target, rpe_target, rest_seconds, warmup=False, substitution_options (lista ou []), notes`.
11. `deduplicate_by_group_and_name`: mantém a primeira ocorrência por `muscle_primary` e por nome (lower); o restante vai para `excluded_due_to_duplicate`.
12. Anexa exercícios dos rotadores (`_apply_slot_bounds(slot, None)`).
13. `reconcile_with_gym_catalog` (ver 5.10).
14. `assign_warmup_flags`: em ordem, `warmup = muscle_primary não está no conjunto ativado`; adiciona primário e secundário ao conjunto. Rotadores forçados `warmup=False`.
15. `normalize_load_target`: extrai o primeiro número (`(\d+(?:[.,]\d+)?)`), inteiro → `"40kg"`, decimal → 2 casas sem zeros à direita com vírgula (`"42,5kg"`); sem número → `None`.
16. Renumera `sequence`.
17. `assign_progression_indicators(exercises, recent_exercise_logs, is_advanced_user)`: `suggest_load_and_reps` por exercício → `progression`, `progression_note`, `suggested_load_kg`, `suggested_reps`. `load_target` da IA é mantido (normalizado).
18. `cap_rpe_target_for_reduced_dose`: para `muscle_primary` em REDUCED_DOSE, cada número do `rpe_target` > 9 vira `"9"`.
19. Draft: `exercises`, `excluded_due_to_recovery = []`, `excluded_due_to_duplicate`, `adjusted_due_to_catalog_mismatch`, `excluded_due_to_no_equipment`, `reduced_dose_groups`, `generation_summary` (da IA), `raw_payload = {prompt_context_keys, unavailable_groups, reduced_dose_groups, slots, mandatory_rotator_slots, generation_summary, raw_ai_text}`.

#### 5.10 `reconcile_with_gym_catalog(exercises, gym_equipment_recommendation)`

Sem recomendação → `(exercises, [], [])`.

1. `groups` (por nome) e `groups_by_id` (chaves convertidas para int quando `str(key).lstrip("-").isdigit()`).
2. `_resolve_group_info`: `groups_by_id[muscle_group_id]` se existir; senão `groups[muscle_primary]` com `logger.warning` quando encontrado.
3. `_candidate_matches(candidate, name)` (normalização `unicodedata.normalize("NFC", s.strip().casefold())`): igual a `mapping_exercise_name`, ou a algum `mapping_aliases`, ou a `equipment_name`.
4. `_select_match`: com vários matches, prefere o candidato cujo `equipment_name` é igual ao `equipment_name` original do exercício; senão o primeiro.
5. Pré-reserva: `reserved = {nome canônico de todo exercício com match}`; `used = reserved`.
6. Loop:
   - sem `group_info` → mantém como está;
   - `candidates = [recommended, *alternatives]` vazio → `excluded_due_to_no_equipment`;
   - com match → grava `exercise_name = mapping_exercise_name`, `amazfit_name = mapping_amazfit_name`, `equipment_name`, `mapping_id`; adiciona a `used`;
   - sem match → `fallback` = primeiro candidato cujo nome não está em `used` (senão `candidates[0]`); grava os mesmos campos; `adjusted.append({**exercise, "original_exercise_name": nome_anterior})`.
7. Retorna `(kept, adjusted, excluded_due_to_no_equipment)`.

#### 5.11 Motor ABC

**Template** (`abc_workout_template.ABC_TEMPLATE`). `muscle_group_id` entre colchetes; `sets_count` 3 salvo indicação:

| Letra | Nome | Itens (sequence: exercício [id] reps) |
|---|---|---|
| A | "Treino A — Costas, Deltoide Posterior, Bíceps, Antebraço e Lombar" | 1 Pulley Frente [6] 8-12; 2 Remada Máquina Fechada [6] 10-15; 3 Remada Pronada Aberta [6] 8-12; 4 Remada alta na máquina [7] 8-12; 5 Crucifixo Inverso Máquina [10] 8-12; 6 Rosca Direta Barra reta ou W [5] 8-12; 7 Rosca Scott Máquina [5] 12-15; 8 Rosca inversa com barra reta [50] 12-15; 9 Flexão de punho com halteres [3] 12-15; 10 Banco Romano / Extensão Lombar [9] 10-15; 11 Rotação Externa do Ombro na Polia [56] 12-15 (`mandatory_rotator`) |
| B | "Treino B — Coxas e Panturrilhas" | 1 Agachamento Livre [4] 6-12; 2 Leg Press 45º [4] 6-10; 3 Cadeira Extensora [4] 15-20; 4 Mesa Flexora [13] 6-10 (4 séries); 5 Cadeira Flexora [13] 15-20 (4 séries); 6 Adução de quadril sentada na máquina [2] 12-15; 7 Abdução de quadril sentada na máquina [8] 12-15; 8 Flexão de quadril em pé na máquina [54] 12-15; 9 Elevação tibial na máquina [53] 15-20; 10 Panturrilha Sentada [51] 15-20; 11 Panturrilha em Pé [51] 15-20 |
| C | "Treino C — Peitoral, Deltoide Lateral, Tríceps e Abdômen" | 1 Supino Máquina [52] 6-10; 2 Dumbbell Press Inclinado 30º [12] 8-12; 3 Crucifixo Máquina / Peck Deck [52] 15-20; 4 Elevação Diagonal / Plano Escapular [10] 12-15 (5 séries); 5 Tríceps Pulley / Polia Alta [14] 6-10; 6 Tríceps Testa na Polia [14] 12-15; 7 Abdominal na Máquina [1] 6-12; 8 Rotação Interna do Ombro na Polia [56] 12-15 (`mandatory_rotator`) |

Cada item traz `notes` (descrição do papel) e `warmup: False`. `MUSCLE_GROUP_ID_UNCHANGED_IN_2026_08_MIGRATION = {1,3,5,8,9,12,14,50,51,53,54,55,56}` é declarado e não é referenciado no arquivo.

`load_muscle_group_names()`: `SELECT id, name FROM muscle_groups` (exceção → `{}`). `resolve_muscle_primary_names` sobrescreve `muscle_primary` pelo nome vivo.

`next_abc_split_day(user_id)`: último `workout_plans` com `plan_type='abc' AND split_day IS NOT NULL` (`ORDER BY created_at DESC`, filtro `user_id` opcional); `A→B`, `B→C`, `C→A`; nada → `"A"`.

**`generate_abc_plan_draft`** (determinístico):

1. `template_rest_seconds = round((min_rest + max_rest)/2)`; `split_day` normalizado; ausente → `next_abc_split_day`; inválido → `ValueError`.
2. `deepcopy` dos itens; nomes vivos; `gather_ficha_context`; projeção.
3. Por item: grupo NO_DIRECT_WORK (por id ou nome) e não `mandatory_rotator` → `excluded_due_to_recovery`; REDUCED_DOSE → `sets_count = min(2, atual)` (atual 0/None → 1 ou 2 conforme expressão `min(2, current_sets) if current_sets else 1` com `current_sets = sets_count or 2`), `rpe_target = "8-9"`, nota "Dose reduzida por recuperação parcial do grupo: sem falha, sem progressão, RPE máx. 8-9.".
4. `reconcile_with_gym_catalog`; `rest_seconds` ausente → `template_rest_seconds`.
5. `role_by_muscle_group_id = {id: {sets_count_baseline, reps_target_default}}`; alvos = ids dos exercícios mantidos exceto `mandatory_rotator`; `_reinforce_target_groups_minimum_sets(...)`.
6. `suggest_load_and_reps` por exercício → `suggested_load_kg`, `suggested_reps`, `progression_note`, `progression` (não altera `load_target`).
7. Renumera; `generation_summary` montado em texto (seleção automática/manual, removidos, dose reduzida, grupos abaixo de 9 séries).
8. Retorna `name, plan_type="abc", split_day, goal="Treino {X} — ciclo ABC por alças musculares", generation_summary, exercises, excluded_due_to_recovery, adjusted_due_to_catalog_mismatch, excluded_due_to_no_equipment, reduced_dose_groups, insufficient_target_volume_groups, plan_date, gym_id, gym_name` (sem `raw_payload`).

**`abc_workout_ai.generate_abc_plan_draft_ai`**:

1. Normaliza `split_day` (ausente → ciclo; inválido → `ValueError` propagado).
2. `_generate_abc_plan_draft_ai_inner`; `ValueError` propaga; qualquer outra exceção → `logger.warning("abc_workout_ai: geracao via IA falhou (%s) — caindo para ABC_TEMPLATE deterministico.")` e `generate_abc_plan_draft(...)`.

`_generate_abc_plan_draft_ai_inner`:

1. `roles = _build_role_definitions(split_day, nomes)`: por item `{sequence, muscle_primary, muscle_group_id, role_description = notes, reps_lo, reps_hi, reps_target_default, sets_count_baseline, fallback_exercise_name, mandatory_isolator_posterior = sequence ∈ MANDATORY_ISOLATOR_ROLES[split_day]}` (`{"B": {4, 5}}`); rotadores ganham `fixed_exercise_name` e `mandatory_rotator`.
2. Contexto, `gym_name`, projeção; papéis em NO_DIRECT_WORK sem `fixed_exercise_name` → `excluded_due_to_recovery`; nenhum ativo → `WorkoutPlanGenerationError`.
3. `reduced_dose_sequences`, `reduced_dose_groups`.
4. `recent_exercise_names_by_sequence = get_recent_abc_exercise_names_by_sequence(split_day, gym_id, limit_plans=4, user_id)` (últimos 4 `abc` com mesmo `split_day`, gym e usuário; `sequence → [nomes]`).
5. `rag_text = select_rag_text(grupos ativos)`; `build_abc_prompt`; `call_openai` (de `workout_plan_ai`); `_extract_json` (cópia local idêntica).
6. `ai_exercises_by_sequence` por `role_sequence`; `_apply_role_bounds` por papel: nome fixo/IA/fallback; `reps_target` da IA só é aceito se for faixa válida contida em `[reps_lo, reps_hi]` do papel, senão `reps_target_default`; `sets_count` ±1 do baseline; `rest_seconds` clamp duplo como Full Body; `notes = f"{role_description} {nota_ia}"`; `warmup=False`.
7. `_deduplicate_exercise_names`: colisão de nome (lower) em sequence posterior → `fallback_exercise_name` do papel.
8. Cap REDUCED_DOSE (`sets_count = min(2, sets or 2)`, `rpe_target="8-9"`, nota).
9. `reconcile_with_gym_catalog`.
10. `enforce_recent_equipment_rotation`: para cada exercício (exceto rotadores) cujo nome normalizado é igual a `recent[sequence][0]` e cujo grupo tem ≥ 2 `mapping_exercise_name` distintos: troca pelo primeiro candidato com nome diferente do mais recente e não usado na ficha (senão diferente do mais recente); grava `exercise_name/amazfit_name/equipment_name/mapping_id`; registra em `adjusted_due_to_repeat_equipment`.
11. Reforço de volume (alvos sem rotator) com `role_by_muscle_group_id` = papéis ativos.
12. Progressão por exercício com `load_target = normalize_load_target(suggested_load_kg)`.
13. `assign_warmup_flags`; `cap_rpe_target_for_reduced_dose`; renumera.
14. Retorna o mesmo shape do determinístico + `adjusted_due_to_repeat_equipment` e `raw_payload = {generation_summary, raw_ai_text}`; `generation_summary` = prefixo fixo + resumo da IA + frases de exclusão, dose, troca de equipamento e grupos abaixo de 9.

**`abc_volume_reinforcement._reinforce_target_groups_minimum_sets`** (constantes `MIN_EQUIVALENT_SETS_PER_GROUP=9.0`, `MAX_NEW_EXERCISES_PER_GROUP=3`, `MAX_SETS_PER_EXERCISE_REINFORCEMENT=9`, `TARGET_MAX_EXERCISES_PER_DAY=12`):

1. Sem recomendação de academia → retorna sem alterar.
2. `contributions` para mappings dos exercícios e de todos os candidatos dos grupos-alvo; `totals[group_id] = Σ sets × stimulus_weight`.
3. Para cada grupo-alvo em ordem de id, pulando REDUCED_DOSE e NO_DIRECT_WORK:
   - **Fase 1**: enquanto `totals < 9`, `adicionados < 3` e `total_exercicios < 12`: primeiro candidato com `mapping_id` e nome não usados → novo exercício `{sequence 0, exercise_name, amazfit_name, equipment_name, mapping_id, muscle_primary (nome), muscle_group_id, sets_count = baseline do papel or 3, reps_target = default do papel or "8-12", rpe_target "8-9", rest_seconds = ponto médio, notes "Exercício adicionado automaticamente para atingir o mínimo de 9 séries equivalentes ...", warmup False}`; atualiza totals.
   - **Fase 2**: se `totals < 9` e há exercícios com `muscle_group_id == grupo`: repete `ceil(9 − totals)` vezes: escolhe o de menor `sets_count` entre os com `sets_count < 9`, `+1`, `totals += stimulus_weight PRIMARY desse exercício para o grupo (default 1.0)`; para se todos atingiram 9.
   - Ainda `< 9` → `insufficient_target_volume_groups.append({muscle_primary, muscle_group_id, equivalent_sets_achieved, equivalent_sets_target: 9.0, exercises_added, reason: "catalogo_esgotado"})`.

#### 5.12 Modelos de ficha (templates)

**Dispatch** (`plan_generation_core.dispatch_plan_generation(payload, user_id)`):

1. `template_id` informado:
   - `get_template_if_assigned(template_id, user_id)`: `_get_template` + `is_active` + `user_id` presente em `assignments`; senão 404 "Modelo não encontrado ou não atribuído a este usuário.".
   - `template_slot_sequence` informado → slot com esse `sequence` (ausente → 422 "Slot informado não existe neste modelo."); senão `resolve_next_slot` (nenhum slot → 422 "Este modelo não tem nenhum slot preenchido.").
   - `volume_mode == "equipment_series"` → `generate_plan_draft_from_equipment_template`.
   - `USE_TEMPLATE_VOLUME_ENGINE_V2` (env, default `"true"`) e `training_type_id` → `generate_plan_draft_from_template_v2`.
   - senão → `generate_plan_draft_from_template` (v1).
   - `WorkoutPlanGenerationError` em qualquer ramo → `HTTPException(502, str(exc))`.
2. `plan_type == "abc"` → `generate_abc_plan_draft_ai` (`ValueError` → 422).
3. senão → `generate_plan_draft` (`WorkoutPlanGenerationError` → 502).

`resolve_next_slot(template, user_id, explicit_slot_sequence=None)`: slots ordenados por `sequence`; sem slots → `None`; explícito → slot com o valor; senão `SELECT template_slot_sequence FROM workout_plans WHERE user_id AND template_id AND template_slot_sequence IS NOT NULL ORDER BY id DESC LIMIT 1`; sem registro ou `sequence` inexistente no template atual → primeiro slot; senão próximo com wraparound.

`_empty_rest_day_draft` (slot `descanso`): draft com `exercises=[]`, `is_rest_day=True`, `cardio_minutes` do primeiro item cardio, `template_slot_targets=[]`, `generation_summary="Dia de descanso conforme o modelo de treino atribuído."`, `raw_payload={template_id, slot_id, slot_sequence}`.

**v1** (`workout_plan_templates_engine.generate_plan_draft_from_template`):

1. Descanso → rest draft. `template_rest_seconds` = ponto médio.
2. Contexto, `gym_name`, projeção; conjuntos de ids/nomes NO_DIRECT_WORK e REDUCED_DOSE.
3. Itens do slot ordenados por `item_order` e depois `prioritize` primeiro (sort estável).
4. Nomes dos grupos (`muscle_groups WHERE id IN`).
5. Para cada item: `cardio` soma `cardio_duration_minutes`; `muscle_group` com nome: NO_DIRECT_WORK → `excluded_due_to_recovery {muscle_primary, muscle_group_id, reason:"NO_DIRECT_WORK"}`; senão `target_groups.append({muscle_group, muscle_group_id, target_sets = sets_count or 3, reps_target, rpe_target, prioritize, dose_level})`. Outros kinds ignorados. Sem alvos → `WorkoutPlanGenerationError`.
6. RAG: `rag_folder` do `training_type` do template; `select_rag_text(grupos, rag_folder, has_training_type)`; `ValueError` → `logger.warning` e nova chamada sem `rag_folder`.
7. `recent_exercises_by_group` com `plan_type=None`; seção Intervals; `build_template_prompt`; `call_openai`; `_extract_json`.
8. Filtro dos itens da IA: exige `exercise_name` e `muscle_primary` normalizado igual a um `target_group`; monta exercício (`mapping_id=None`, `sets_count` int or 3, `reps_target`/`rpe_target` da IA ou do grupo ou `"8-12"`/`"8-9"`, `rest_seconds` int ou ponto médio).
9. Cap REDUCED_DOSE: orçamento 2 séries por grupo; exercício consome `max(1, min(sets, orçamento))`; orçamento esgotado → exercício descartado; `rpe_target="8-9"` e nota.
10. Dedupe por nome normalizado.
11. `reconcile_with_gym_catalog`; `_reinforce_template_target_groups` (mesmo algoritmo 5.11 com alvo `target_sets`, pulando REDUCED_DOSE, novos exercícios com `sets_count=3`, `reps_target`/`rpe_target` do grupo, `substitution_options: []`; constantes importadas de `abc_volume_reinforcement`).
12. `rest_seconds` ausente → ponto médio; `assign_warmup_flags`; `normalize_load_target`; renumera; `assign_progression_indicators`; `cap_rpe_target_for_reduced_dose`.
13. `template_slot_targets = [{muscle_group_id, muscle_group_name, sets_count = item.sets_count or 0}]` para itens `muscle_group` do slot com nome.
14. Retorno: `name = f"{template.name} — {slot.name}"`, `plan_type = template_type or "full_body"`, `goal = description`, datas/gym, `template_id`, `template_slot_id`, `template_slot_sequence`, `template_slot_targets`, `is_rest_day=False`, `cardio_minutes`, `exercises`, listas de exclusão/ajuste, `reduced_dose_groups`, `insufficient_target_volume_groups`, `generation_summary` (IA ou texto padrão), `raw_payload {template_id, slot_id, slot_sequence, unavailable_group_ids, reduced_dose_group_ids, recent_exercises_by_group, target_groups, generation_summary, raw_ai_text}`.

**v2 — Fase 1, domínio** (`intelligence/template_volume_domain.py`, dataclasses):

| Dataclass | Campos / comportamento |
|---|---|
| `WeeklyMuscleTarget` (frozen) | `muscle_group_id, muscle_group_name, weekly_min, weekly_target, weekly_max`; `__post_init__` exige `0 ≤ min ≤ target ≤ max` (`ValueError`) |
| `SlotMuscleAllocation` (frozen) | `slot_id, slot_sequence, slot_name, muscle_group_id, priority_min_pct?, priority_max_pct?, reps_target?, rpe_target?, prioritize=False`; `has_configured_range()` |
| `MuscleActivationVector` | `by_role: {group_id: {PRIMARY, SECONDARY, STABILIZER}}`; `add(group, role, amount)` (role desconhecido → SECONDARY); `total(group)`; `muscle_group_ids()`; `empty()` |
| `MuscleSlotBudget` | `muscle_group_id, muscle_group_name, weekly_min, weekly_target, weekly_max, slot_pct_min, slot_pct_max (frações), nominal_slot_min, nominal_slot_max, recovery_status, recovery_multiplier, reps_target?, rpe_target?, prioritize=False`; `nominal_slot_mid = (min+max)/2`; `effective_slot_min/max = nominal × recovery_multiplier`; `is_hard_excluded = status == "NO_DIRECT_WORK"`; `deviation_status(total)`: excluído → `BALANCED` se `total ≤ 0` senão `OVER_ALLOCATED`; `total < eff_min → UNDER_ALLOCATED`; `total > eff_max → OVER_ALLOCATED`; senão `BALANCED` |
| `GenerationVolumeSummary` | `entries: list[Entry]`, `warnings: list[str]`; `Entry(muscle_group_id, muscle_group_name, weekly_target, slot_min, slot_max, planned_activation, deviation_status, recovery_status, cycle_planned_total=0.0, weekly_volume_deficit=0.0, note=None)`; `as_dict()` arredonda para 2 casas |
| `TemplateCycleContext` | `template_id, training_type_id, slots, muscle_targets, muscle_slot_allocations, recovery_state`; `slot_by_id`, `ordered_slot_ids`, `allocations_for_group`, `recovery_status_for` (default READY), `recovery_multiplier_for` (default 1.0) |

Constantes: `MUSCLE_ROLES = ("PRIMARY","SECONDARY","STABILIZER")`, `UNDER_ALLOCATION_THRESHOLD_PCT = 90`, `OVER_ALLOCATION_THRESHOLD_PCT = 110`.

**v2 — Fase 2, planner** (`template_volume_planner.py`):

- `build_template_cycle_context(template, user_id, gym_id, plan_date, plan_time)`: sem `training_type_id` → `TemplateVolumeEngineNotApplicable`; metas via `training_types.get_training_type_muscle_group_targets(training_type_id)` (`{group_id: {min, target, max}}`); nomes; `muscle_targets` (grupos sem nome são ignorados); `muscle_slot_allocations` a partir dos itens `muscle_group`; recuperação `_load_projected_recovery` = `project_recovery_for_date(MuscleRecoveryService().get_recovery_overview(user_id), plan_date, plan_time)` (sem `user_id` → `WorkoutPlanGenerationError`, SEG-04); `recovery_state[group] = {status: projected_readiness_status or READY, multiplier: _RECOMMENDATIONS[status].volume_multiplier}`.
- `build_template_volume_plan(context, slot_id)`: itens `muscle_group` do slot; `configured_min_sum`/`max_sum` dos percentuais presentes; `unconfigured_count` (qualquer lado ausente); `fallback_min_pct = max(0, 100 − configured_min_sum)/unconfigured_count` (idem max); por item com meta: `pct = item ou fallback`; `nominal_min = weekly_target × pct_min/100`; `nominal_max = weekly_target × pct_max/100`; `MuscleSlotBudget` com status/multiplier do contexto. Grupos sem meta são omitidos.
- `validate_template_volume_coverage(template)`: sem `training_type_id` → `[]`; soma `priority_min_pct`/`priority_max_pct` (só itens com ambos) por grupo em todos os slots; ignora grupos sem meta; `sum_max < 90 → UNDER_ALLOCATED`; `sum_min > 110 → OVER_ALLOCATED`; senão `BALANCED`; saída `{muscle_group_id, muscle_group_name, sum_pct_min, sum_pct_max, status}`. Chamado por `workout_plan_templates._attach_volume_coverage_warnings` (exceções → `[]`).

**v2 — Fase 3, candidatos** (`candidate_exercise_provider.build_candidate_matrix(group_ids, recommendation)`): por grupo `[recommended, *alternatives]` de `groups_by_id`; uma chamada `_resolve_muscle_contributions_for_mappings` para todos os mappings; item `{mapping_id, exercise_name (mapping_exercise_name), amazfit_name, equipment_id, equipment_name, primary_group_id, muscle_contributions:[{muscle_group_id, muscle_group_name, role, stimulus_weight}]}`.

**v2 — Fase 4, seletor IA** (`template_ai_selector_v2.py`): `select_exercises_for_slot_v2` monta prompt (seção 6), `call_openai` de `workout_plan_ai`, `parse_v2_selection_response(raw, matrix, default_rest_seconds = round((min+max)/2))`: `_extract_json`; `exercises` lista; descarta itens não-dict ou `mapping_id` fora da matriz; `sets_count = max(1, int or 1)`; `preferred_equipment_id` int ou `None`; `reps_target`/`rpe_target` strings ou `None`; `rest_seconds` int ou default.

**v2 — Fase 5, validador** (`template_volume_validator.py`). Constantes: `MAX_REPAIR_ITERATIONS=10`, `MAX_SETS_PER_EXERCISE_V2=6`, `PREFERRED_SETS_PER_EXERCISE_RANGE_V2=(3,4)`, `PREFERRED_MAX_SETS_PER_EXERCISE_V2=4`, `MIN_PREFERRED_SETS_PER_EXERCISE_V2=2`.

| Função | Algoritmo |
|---|---|
| `calculate_slot_activation(exercises, names_by_id)` | `VolumeService.get_muscle_activation_for_exercises` (itens com mapping e sets); mapeia nome (NFC+casefold) → id; `{group_id: {primary, secondary, stabilizer, total}}` |
| `detect_deviations(budgets, activation)` | por budget: `activation_total`, `effective_slot_min/max`, `status = deviation_status(total)`, `recovery_status` |
| `repair_slot_exercises(exercises, budgets, matrix, names, max_iterations=10, max_sets=6)` | até 10 iterações; recalcula ativação; para cada grupo (ordem por id, pulando excluídos): **UNDER** → primeiro exercício com `primary_group_id == grupo` e `sets < 6` recebe +1 (`increase_sets`); senão primeiro candidato da matriz não usado é adicionado com `sets_count=2`, reps/RPE do budget, `rest_seconds=None` (`add_exercise`); senão log `deficit_catalog_exhausted {deficit = eff_min − total}` (sem `changed`). **OVER** → exercícios do grupo com `sets > 2`: −1 no de maior `sets` (`decrease_sets`); senão, com > 1 exercício do grupo, remove o de menor `sets` (`remove_exercise_avoid_single_set`); senão −1 no único se `sets > 1`. Para quando nenhuma mudança ocorre. Retorna `(exercises, repair_log)` |
| `_split_evenly(total, k)` | `divmod`, partes maiores primeiro |
| `_balanced_set_distribution(total, max_parts)` | menor `k ∈ [1, max_parts]` com `3k ≤ total ≤ 4k` → `_split_evenly`; senão menor `k` com `2k ≤ total ≤ 4k`; senão `_split_evenly(total, max_parts)` e `forced_single = alguma parte < 2` |
| `_stimulus_weight_for_group(mapping, group, matrix)` | contribuição PRIMARY do candidato para o grupo; senão a primeira para o grupo; senão `1.0` |
| `split_concentrated_exercises(exercises, budgets, matrix, names, preferred_max=4)` | percorre exercícios ordenados por `(primary_group_id, mapping_id)`; ignora sem budget/excluídos/`sets ≤ 4`/sem alternativas não usadas; `distribution = _balanced_set_distribution(sets, 1 + n_alternativas)`; `< 2` partes → ignora; `statuses_before` de todos os budgets; para cada combinação (`itertools.combinations(alternatives, len(partes−1))`): original recebe a maior parte; cada alternativa recebe `max(1, round(part × w_original / w_alternativa))` séries (com reps/RPE/rest do original ou budget); se `statuses_after != statuses_before` reverte e tenta a próxima; senão confirma, marca mappings usados e loga `split_concentrated_exercise {from_mapping_id, to_mapping_id, sets_moved, forced_single_set?, note?}`. Nenhuma combinação válida → mantém original |
| `enforce_minimum_sets_floor(exercises, budgets, matrix, names, max_iterations=10)` | até 10 iterações; para cada exercício com `sets < 2`, grupo com budget não excluído: (1) irmão do mesmo grupo com `sets > 2` (maior primeiro) cede 1 série se os status não mudarem (`raise_floor_borrow`); (2) eleva a 2 se status não mudarem (`raise_floor_bump`); (3) com irmão, remove se o status do próprio grupo não mudar (`raise_floor_remove_orphan`); (4) log `forced_single_set` uma vez por mapping |
| `order_exercises_for_session(exercises, budgets)` | sort estável: exercícios cujo `primary_group_id` pertence a budget com `prioritize` primeiro |
| `greedy_deterministic_selection(budgets, matrix, names, max_sets=6)` | por budget (ordem id) não excluído e com `effective_slot_max > 0`: primeiro candidato não usado; `w` = `stimulus_weight` PRIMARY do candidato para o grupo (default 1.0); `mid = nominal_slot_mid × recovery_multiplier`; `sets = max(1, min(6, round(mid / w)))`; depois `repair_slot_exercises` |

**v2 — Fase 6, orquestração** (`workout_plan_templates_engine_v2.generate_plan_draft_from_template_v2`):

1. Descanso → rest draft; `template_rest_seconds` = ponto médio.
2. `context = build_template_cycle_context(...)`; `all_budgets = build_template_volume_plan(context, slot["id"])`; vazio → `WorkoutPlanGenerationError`.
3. `excluded_due_to_recovery` = budgets excluídos; `active_budgets` restantes (vazio → erro); `reduced_dose_groups`.
4. `ficha_context = gather_ficha_context(gym_id, user_id)`; `gym_name`; `candidate_matrix = build_candidate_matrix(ids ativos, recommendation)`.
5. RAG com `rag_folder` (fallback em `ValueError`); `recent_exercises_by_group` com `plan_type=None`.
6. `muscle_budgets_prompt = [_budget_to_prompt_dict(b)]` → `{muscle_group_id, muscle_group_name, weekly_target, slot_effective_sets_min, slot_effective_sets_max, slot_effective_sets_mid, reps_target, rpe_target, recovery_status, prioritize}`.
7. `try`: `select_exercises_for_slot_v2` → `_ai_selection_to_exercises` (descarta mapping sem grupo ativo; mesmo `mapping_id` repetido soma `sets_count`; `equipment_id = preferred_equipment_id` só se igual ao do candidato; `sets_count or 3`, reps/RPE da IA ou budget ou `"8-12"`/`"8-9"`) → `repair_slot_exercises`; `except WorkoutPlanGenerationError`: `used_ai=False`, `greedy_deterministic_selection`.
8. `split_concentrated_exercises`; `enforce_minimum_sets_floor`; `order_exercises_for_session`; logs concatenados em `repair_log`.
9. `_to_final_exercise_shape` (`muscle_primary` = nome do grupo, `muscle_secondary=None`, `sets_count or 1`, `load_target=None`, `rest_seconds or template_rest`, `warmup=False`, `substitution_options=[]`, `notes=""`).
10. `reconcile_with_gym_catalog`; `assign_warmup_flags`; renumera; `assign_progression_indicators`; `load_target = normalize_load_target(suggested_load_kg)`; `cap_rpe_target_for_reduced_dose`.
11. `final_activation = calculate_slot_activation(final)`; `GenerationVolumeSummary` com entry por budget ativo (`cycle_planned_total = total`, `weekly_volume_deficit = max(0, eff_min − total)`, nota fixa); warnings para excluídos, `deficit_catalog_exhausted` e `forced_single_set`.
12. `template_slot_targets = [{muscle_group_id, muscle_group_name, sets_count = round(nominal_slot_mid, 2), effective_max_sets = round(effective_slot_max, 2)}]` para `all_budgets`.
13. Retorno: shape v1 com `cardio_minutes=None`, `excluded_due_to_duplicate=[]`, `insufficient_target_volume_groups` derivado de `deficit_catalog_exhausted`, `generation_summary` textual ("Seleção via IA" / "Seleção via fallback determinístico (IA indisponível)"), `raw_payload = {template_id, slot_id, slot_sequence, engine_version: "v2", used_ai, repair_log, volume_planning}`.

**equipment_series** (`workout_plan_templates_engine_equipment.generate_plan_draft_from_equipment_template`):

1. Descanso → rest draft. Itens `item_kind == "equipment"` ordenados por `item_order`; nenhum → `WorkoutPlanGenerationError`.
2. Nomes dos grupos; `gather_ficha_context`; `gym_name`; `project_recovery_for_date(ficha_context.muscle_recovery)`; `recovery_status_by_group`.
3. Sem `gym_id` → warning e itens mantidos. Por item `_resolve_item_equipment`:
   - sem gym → `{mapping_id, equipment_id, equipment_name None, preferred_equipment_id None}`;
   - `services.is_gym_equipment_pair_active` → `_find_active_gym_equipment` (`gym_equipment.id` + `equipment.name`);
   - senão `services.resolve_equipment_id(cur, mapping_id, gym_id)` → `_load_gym_equipment_rows` + warning "equipamento original indisponível na academia; substituído automaticamente...";
   - senão `_recommend_for_group` = `equipment_recommender.recommend_for_group(...)["recommended"]` (direto, por `user_id`) (pode trocar `mapping_id`) + warning;
   - senão mantém com warning "nenhum equipamento disponível na academia...".
4. Nomes de exercício (`exercise_catalog`) e AMAZFIT (`exercise_external_mappings`).
5. Exercício final: `sets_count = item.sets_count`, `reps_target or "8-12"`, `rpe_target or "8"`, `rest_seconds = ponto médio`, `equipment_id` (catálogo), `preferred_equipment_id` (`gym_equipment.id`), 10 campos de técnica/aquecimento/ajuste/pico copiados, `recovery_status` do grupo.
6. `assign_warmup_flags`; renumera; `assign_progression_indicators`; `load_target = normalize(suggested_load_kg)`.
7. Ativação via `VolumeService().get_muscle_activation_for_exercises`; `GenerationVolumeSummary` com `weekly_target/slot_min/slot_max = 0.0`, `deviation_status="BALANCED"`, `planned_activation = cycle_planned_total = total_equivalent`, nota fixa; warning para grupos NO_DIRECT_WORK (exercício mantido) + warnings de resolução.
8. `template_slot_targets = [{muscle_group_id, muscle_group_name, sets_count = Σ sets dos itens do grupo, effective_max_sets: None}]`.
9. Retorno com listas de exclusão vazias, `raw_payload {template_id, slot_id, slot_sequence, engine_version: "equipment_series", used_ai: False, repair_log: [], volume_planning}`.
10. Exceção que não seja `WorkoutPlanGenerationError` → `WorkoutPlanGenerationError(f"Falha ao gerar ficha via modo 'equipment_series': {exc}")`.

**Preview de template** (`workout_plan_templates.preview_activation`): ciclo = slots por `sequence`; para dias 1..7 usa `cycle[(day−1) % len]`; `equipment_series` → `_compute_slot_activation_equipment_series` (itens `equipment` → `get_muscle_activation_for_exercises`; exceção → `[]`); `percent_budget` → `_compute_slot_activation` (itens `exercise` via motor de ativação + itens `muscle_group` como `primary_equivalent += sets_count × 1.0`); descanso → `[]`.

#### 5.13 Persistência e leitura de planos (`services.py`)

- `create_workout_plan(payload, user_id)` / `update_workout_plan(plan_id, payload, user_id)`: `_ensure_workout_plans_time_column` e `_ensure_workout_plans_split_day_column` (ALTER aditivos checados via `information_schema`, uma vez por processo); `gym_id = resolve_gym_id`; INSERT/UPDATE de `workout_plans` (`user_id, plan_date, plan_time, gym_id, name, plan_type, split_day, goal, status, raw_payload (json), template_id, template_slot_sequence`); `_replace_plan_exercises`; retorna `get_workout_plan`.
- `_replace_plan_exercises(cur, plan_id, exercises, gym_id)`: `_ensure_plan_link_columns` (ALTERs de `mapping_id` com FK `exercise_catalog` e `preferred_equipment_id` com FK `gym_equipment`); linhas existentes indexadas por `sequence`; `DELETE` das sequences ausentes do payload; para cada exercício: `name_changed_on_edit = linha existe e exercise_name difere`; `explicit_mapping_id = None if name_changed_on_edit else ex.mapping_id`; `mapping_id = resolve_mapping_id(exercise_name, amazfit_name, explicit_mapping_id)`; `preferred_equipment_id = None if name_changed_on_edit else ex.preferred_equipment_id`; ausente com `gym_id` e `mapping_id` → `resolve_equipment_id(cur, mapping_id, gym_id, None, equipment_name)`; `UPDATE` (mantém `id`) ou `INSERT` com 25 colunas (inclui `substitution_options` JSON e os 10 campos de técnica).
- `delete_workout_plan`: `DELETE FROM workout_plans WHERE id [AND user_id]`.
- `list_workout_plans(status, limit, user_id, has_advanced_technique)`: `COUNT(wpe.id) AS exercises_count`; filtro `EXISTS` sobre `advanced_technique`, `warmup_reps`, `feeler_set_reps`, `peak_contraction_seconds`; `plan_time` formatado `HH:MM`.
- `get_workout_plan(plan_id, user_id)`: `raw_payload = from_json_dict`; `_apply_effective_max_sets_fallback` (em `try/except` silencioso): copia `volume_planning.entries[].slot_max` para `template_slot_targets[].effective_max_sets` quando este é `None`, casando por `muscle_group_id`; exercícios com `workout_plan_exercise_id`, `exercise_description` (catálogo canônico), `substitution_options = from_json_list`; `_enrich_plan_exercises_with_progression`: `list_exercise_logs(300, user_id)`, `plan_type == "abc"` → `suggest_load_and_reps` por exercício; demais → `assign_progression_indicators`; exceção → `logger.exception("Falha ao recalcular progressao do plano id=%s na leitura")`.
- `resolve_plan_muscle_slugs(exercises)`: `muscle_primary` → intensidade 2; `muscle_secondary` ausente do dict → 1; `SELECT mg.name, mgbm.slugs FROM muscle_group_body_map JOIN muscle_groups`; slugs CSV; intensidade máxima por slug.

#### 5.14 Tendência de progressão — `intelligence/progression_service.py`

Constantes: `MIN_SESSIONS_FOR_TREND=3`, `PROGRESS_E1RM_THRESHOLD_PCT=0.03`, `REGRESS_E1RM_THRESHOLD_PCT=-0.03`, `RPE_STAGNATION_DELTA=0.75`, `_SAME_LOAD_TOLERANCE_PCT=0.02`.

`classify_progression_trend(sessions)` (sessões ASC com `e1rm`, `avg_rpe`, `best_load_kg`, `best_reps`):

1. `valid` = sessões com `e1rm` truthy; `< 3` → `DADOS_INSUFICIENTES` ("É preciso pelo menos 3 sessões com carga, repetições e RPE registrados para avaliar a tendência (encontradas: n).").
2. `latest = valid[-1]`, `previous = valid[-2]`, `reference = valid[:-1]`; `pct_change = (latest.e1rm − mean(reference))/mean`; `rpe_delta = latest.avg_rpe − mean(reference RPEs)`.
3. `_approx_equal(a, b)`: `|a−b| ≤ max(|a|, |b|, 1.0) × 0.02`. `same_load_more_reps`: cargas aproximadamente iguais e `latest.best_reps > previous.best_reps`. `same_reps_more_load`: reps iguais e `latest.load > previous.load × 1.02`.
4. `pct ≥ 0.03` ou algum dos dois sinais → `PROGREDINDO` (razões concatenadas com " e ").
5. `pct ≤ −0.03` → `REGREDINDO`.
6. `rpe_delta ≥ 0.75` → `ESTAGNADO`.
7. senão `ESTAVEL`.

Saída: `classification`, `reason`, `sessions_considered`, `e1rm_change_pct` (1 casa).

`ProgressionService.get_recent_progression(limit=5, user_id)`: agrupado por `COALESCE(exercise_catalog.name, e.original_name)` em 60 dias; `MAX(best_weight_kg)`, `ROUND(AVG(volume_kg),2)`, `ROUND(AVG(avg_rpe),2)`, `COUNT(DISTINCT a.id) AS sessions`, `HAVING sessions >= 2`, `ORDER BY best_weight_kg DESC, avg_volume_kg DESC`.

`get_recent_sessions(days=30, user_id)`: atividades de força (`activity_type LIKE muscul/forÃ§a/strength`) com exercícios (`COUNT(es.id)`, médias de peso/reps/RPE, grupos primário/secundário), agrupadas em Python em `[{date, activity_type, exercises[]}]`.

`analytics._build_progression_sessions(rows)`: mesmo agrupamento de `get_exercise_session_history`, com `activity_name` e `sets_count`.

#### 5.15 Bioimpedância — `BodyCompositionTrendService`

- `get_physical_summary` foi apagado no L0 da nutrição (2026-10-15): não tinha chamador.
- `get_body_trend(days=90, user_id)`: linhas ASC na janela; `< 2` → `trend={"status":"insufficient_data"}`; senão `weight/muscle/fat/water_velocity_per_day = (último − primeiro)/days` (4 casas).

#### 5.16 Carga de treino — `TrainingLoadService`

- `get_weekly_summary(user_id)`: desde segunda-feira corrente: `completed_workouts = COUNT(DISTINCT a.id)`, `total_sets`, `total_volume_kg`, `avg_rpe`, `high_rpe_sets = SUM(rpe >= 9)`; `planned_workouts` = `workout_plans` com `plan_date` na semana e `status NOT IN ('cancelled','done')`; `latest_workouts` (5 atividades); `next_workout` = primeiro plano com `plan_date ≥ CURDATE()` e `status IN ('planned','draft')`.
- `get_daily_activity_summary` foi apagado no L0 da nutrição (2026-10-15): não tinha chamador.

#### 5.17 Dashboards e alertas

**`DashboardAggregatorService`**

- `build_home_payload` e `_compute_readiness` (com `GET /analytics/dashboard-home`) foram apagados no M76 Lote 2c (#403, 2026-10-07): sem consumidor. O único método público do serviço é `build_training_evolution_payload`.
- `build_training_evolution_payload(days=90, user_id)` (leituras em paralelo via `parallel_reads.run_parallel`): `weekly_summary`, `volume_overview (days, limit 8)`, `recent_records` (`get_recent_records`, regra única `session_records.record_events`; substituiu `progression_highlights` no M76 Lote 3b), `exercise_progression (days)`, `recovery (14, 6)`, `intervals_correlation (days)`, `weekly_trend = analytics.compute_rolling_weekly_trend(min(days, 84), user_id)`, `weekly_trend_by_muscle_group (min(days, 84), user_id)`, `sessions = get_recent_sessions(min(days, 30))`.

**`IntervalsCorrelationService.get_training_correlation(days=90, user_id)`**: atividades de força (`LIKE muscul/força/forca/strength`, sem filtro de usuário); `intervals_activities` com `sport_type='WeightTraining'` (filtro `user_id` opcional); sem WeightTraining → `available=False`; indexa por data; `_match_activity_for_day`: 1 candidato → ele; vários → menor `|start_time − started_at|` (quando ambos existem) senão maior `moving_time_seconds`; `matched_workouts` com wellness do dia (`sleep_seconds, hrv_ms, resting_hr_bpm`); `unmatched_workouts_count`; médias em dias de treino vs. demais dias com wellness.

**`DashboardAlertService.get_alerts(user_id, days=30, training_type_id)`**

Constantes: `ROLLING_WINDOW_DAYS=14`, `LOW_VOLUME_CRITICAL_RATIO=0.5`, `EXCESSIVE_VOLUME_CRITICAL_RATIO=1.5`, `FATIGUE_PERFORMANCE_RATIO_WARNING=0.92`, `FATIGUE_PERFORMANCE_RATIO_CRITICAL=0.85`, `STAGNATION_LOOKBACK_DAYS=180`, `STAGNATION_TOP_EXERCISES_PER_GROUP=3`, `SIBLING_MUSCLE_GROUP_IDS=(6, 7)`, `ASYMMETRY_WARNING_THRESHOLD_PCT=0.30`, `ASYMMETRY_CRITICAL_THRESHOLD_PCT=0.55`.

1. `_resolve_targets`: `training_type_id` explícito (`source="explicit"`) → senão `resolve_active_training_type_for_user` (`"assigned_template"`) → senão `{}` (`"fallback_generic"`); `_target_for_group` fallback `{min 6, target 9, max 27}`.
2. `_check_volume_alerts`: `get_rolling_weekly_trend_by_muscle_group(days=14)`; `current = stimulus_equivalent_sets` do período `"D-0 a D-6"`, `previous` de `"D-7 a D-13"` (exclui `Outros`); `current < min e previous < min` → `VOLUME_BAIXO` (CRITICAL se ambos < min×0.5, senão WARNING); `elif current > max` → `VOLUME_EXCESSIVO` (CRITICAL se > max×1.5).
3. `_check_fatigue_alerts`: `get_muscle_group_overview(days, limit=100)`; `performance_ratio < 0.92` e (`rpe_anticipated` ou `excessive_rep_drop`) → `FADIGA` (CRITICAL se < 0.85).
4. `_check_stagnation_alerts`: para cada grupo do overview, top-3 exercícios (`get_top_exercises_for_muscle_group`), `get_exercise_session_history(days=180)`, `classify_progression_trend`; `ESTAGNADO → WARNING`, `REGREDINDO → CRITICAL`; mantém o pior por grupo → `ESTAGNACAO`.
5. `_check_asymmetry_alert`: overview; `actual = stimulus_equivalent_sets × 7/days` para grupos 6 e 7; `pct = actual/target`; `|pct_a − pct_b| ≥ 0.30` → `ASSIMETRIA` (CRITICAL ≥ 0.55), `muscle_group_id=None`, nome do grupo defasado na mensagem; nenhum dos dois no overview → nada.
6. Ordena por severidade (CRITICAL, WARNING, INFO). Retorna `{days, training_type_id, training_type_source, alerts}`. Cada alerta: `{alert_type, severity, muscle_group_id, muscle_group, message, details}`.

**Funções de `analytics.py`**

- `compute_training_trend(period, days, user_id)`: `_period_expr`: day `'%Y-%m-%d'`, week `'%x-W%v'`, month `'%Y-%m'`, year `'%Y'`; atividades de força; `workouts`, `sets_count`, `reps_total`, `volume_kg`, `avg_rpe`, `high_rpe_sets`.
- `compute_rolling_weekly_trend(days, user_id)`: mesmos agregados por `bucket_index = FLOOR(DATEDIFF(CURDATE(), local_date)/7)`, `ORDER BY bucket_index DESC`, `period = "D-{i*7} a D-{i*7+6}"`.
- `_build_muscle_status_items(user_id, days, training_type_id)`: overview `limit=100`; `actual_weekly = stimulus_equivalent_sets × 7/days`; metas do training type (explícito → atribuído → fallback 6/9/27); `status = classify_weekly_volume_status`; itens com `actual_effective_sets`, `actual_effective_sets_in_window`, `min/target/max_weekly_sets`, `status`, `target_source`, `sessions`, `last_trained_at`, `avg_rpe`, `direct/indirect/stabilizer_sets`; ordenado por `actual_effective_sets` desc.
- `dashboard_muscle_drilldown`: overview e recovery com `limit=100` em `days`; top 5 exercícios; `latest_progression = suggested_progression_for_mapping(top[0].mapping_id)`; `weekly_series` = rolling trend filtrado por nome.
- `_recovery_phase(item)` (`RECOVERY_PHASE_RECENT_HOURS=12.0`): sem `last_trained_at` ou `remaining_hours` `None`/≤ 0 → `READY`; `hours_since_trained < 12` → `TREINADO`; senão `RECUPERANDO`.
- `dashboard_cycle`: `_resolve_latest_assigned_template_id` (atribuição mais recente com template ativo); sem template → `{"has_template": False, "message": "Nenhum modelo de treino atribuído a você."}`; `get_template_if_assigned`; sem slots → payload vazio com `training_type_source="unavailable"`; `cycle_length_days = 7 × n_slots`; `done_sequences`: percorre `workout_plans.template_slot_sequence` (`ORDER BY id DESC`) adicionando até encontrar repetição ou atingir `n_slots`; `next = resolve_next_slot`; status por slot `next`/`done`/`pending`; `pending_sequences = todos − done`; training type do template (`"template"`) ou atribuído (`"assigned_template"`) ou `"unavailable"`; com meta: `actual = stimulus_equivalent_sets × 7/cycle_length_days` (overview em `cycle_length_days`, limit 100), `projected = sum_projected_sets_from_pending_slots`, grupos = união de metas/actual/projected (nomes faltantes via `muscle_groups`), `status = classify_weekly_volume_status(actual + projected, ...)` quando há meta; ordenado por `actual + projected` desc.
- `_resolve_mapping_id_by_name(name)`: `SELECT id FROM exercise_catalog WHERE LOWER(name) LIKE %name% ORDER BY id LIMIT 1`.

#### 5.18 Outros motores

- **`muscle_groups_mapped.py`**: `load_rows(days, user_id)` executa `ensure_canonical_columns()` e `reclassify_exercises(limit=20000, only_missing=True)` (escritas em `exercises.mapping_id`), descobre colunas via `SHOW COLUMNS` (`schema()`), lê séries com grupo canônico primário/secundário. `metric_value`: `sets → 1.0`; `reps → reps`; `load → weight`; `volume → reps×weight` (se 0 → reps). `aggregate(period, metric, days, include_secondary, secondary_factor, user_id)`: primário (fora de `outros/other/others`) contribui `value × 1.0`; secundário (se `include_secondary`) `value × secondary_factor`; linha sem primário válido → `unmapped[exercise_name] += 1`; `period_label`: `daily` ISO, `monthly` `YYYY-MM`, `quarterly` `YYYY-Qn`, `yearly` `YYYY`, demais ISO week `YYYY-Www`; saída `total_sets`, `mapped_sets`, `mapped_ratio`, `unmapped` (top 100), `trend_rows`, `table_rows` (com `rows/data/table/groups` duplicados), aplicando `fix_obj`.
- **`exercise_mapping_canonical.py`**:
  - `fix_text`: até 4 tentativas de re-decodificar (`latin1`/`cp1252 → utf-8`) quando o texto contém `Ã`, `Â`, `â€` ou `�`, seguido de substituições fixas.
  - `normalize`: `fix_text` → NFKD sem diacríticos → lower → não alfanumérico vira espaço → colapsa espaços.
  - `load_mapping_rows()`: `exercise_catalog` ativos com grupo primário (exclui "Outros"); `strong_keys` = partes (split por `,;/|`) normalizadas com `len ≥ 3` de nome, aliases (`exercise_aliases`) e nomes AMAZFIT; `weak_keys` = partes de `keywords`.
  - `match_mapping(original_name, mapped_name, mappings)`: needles = normalize(mapped_name), normalize(original_name); para cada mapping: strong igual → retorna imediatamente `confidence 100, match_type "strong_exact"`; strong com `len(key) ≥ 5` contido/contendo → `80 + min(len)/10` (`strong_contains`); weak igual → 70 (`keyword_exact`); weak `len ≥ 6` contido → `40 + min(len)/10` (`keyword_contains`); retorna o de maior score (score comparado ×100).
  - `reclassify_exercises(limit, only_missing)`: `ensure_canonical_columns` (ADD COLUMN `mapping_id BIGINT NULL` se ausente; `CREATE INDEX idx_exercises_mapping_id` com erro ignorado); lê `exercises` (`WHERE mapping_id IS NULL` se `only_missing`, `ORDER BY id DESC LIMIT`); para cada: `UPDATE exercises SET mapping_id = match.id` ou `NULL`; retorna contadores e exemplos.
  - `canonical_associate`: upsert em `exercise_aliases` (por `alias`) com `source = f"manual_alias:{target}"`, depois `reclassify_exercises(limit=100000, only_missing=False)`.
- **`gym_equipment_sync.py`**:
  - `sync_gym_equipment_for_gym(conn, gym_id)`: `INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active) SELECT gec.gym_id, eem.exercise_id, gec.equipment_id, 1 FROM gym_equipment_catalog gec JOIN equipment_exercises eem ON eem.equipment_id = gec.equipment_id AND eem.is_active = 1 WHERE gec.gym_id = %s AND gec.is_active = 1 ON DUPLICATE KEY UPDATE is_active = 1`; depois `UPDATE gym_equipment ge LEFT JOIN (mesmo cruzamento) valid ... SET ge.is_active = 0 WHERE ge.gym_id = %s AND valid.gym_id IS NULL AND ge.is_active = 1`. Nunca apaga.
  - `sync_gym_equipment_for_equipment(conn, equipment_id)`: gyms com o equipamento ativo em `gym_equipment_catalog` → sync de cada um.
- **Recomendação de equipamento** (`gyms.py`):
  - `/gym-equipment/recommend`: grupo pelo `primary_group` ou PRIMARY do `mapping_id`; `has_equipment_for_group = COUNT(gym_equipment ativos do grupo) > 0`; candidatos agrupados por `ge.id` com `last_used_date = MAX(a.local_date)`, `last_used_at = MAX(COALESCE(a.ended_at, a.started_at, a.created_at))` (via `exercises.equipment_id = ge.id`), `mapping_aliases_raw = GROUP_CONCAT(DISTINCT ea.alias ORDER BY ea.alias SEPARATOR '␟')`; `ORDER BY (last_used_at IS NULL) DESC, last_used_at ASC, eq.name ASC`; `exclude_equipment_id` exclui `ge.id`; `recommended = items[0]`, `alternatives = items[1:]`.
  - `/gym-equipment/recommend-all`: `all_groups` = grupos distintos com contribuição PRIMARY ativa em exercício ativo; mesma ordenação por grupo; `groups[nome]` e `groups_by_id[id]` com o mesmo payload.
  - O histórico de uso usado para ordenação não filtra por usuário.
- **Mapa corporal** (`muscle_map.render_muscle_map_png(slug_intensity, scale=2.0)`, desde 2026-10-03 sobre o mapa corporal v1 de `body_map_render.py` / `app/assets/bodymap/v1/`): PNG RGB de largura `210 × scale` (420 px no padrão), fundo `#141414`, margem `6 × scale`; frente e costas recortadas pelas caixas do manifest, lado a lado; intensidade ≥ 2 = `PRIMARY_STYLE` (#FF9500 forte com brilho), 1 = `SECONDARY_STYLE` (#FF9500 a 45 %), 0/ausente = sem destaque; slug sem região (`knees`, `head`) é ignorado e `abductors` → `adductor` (alias do manifest); PNG `optimize=True`. Fórmula: `tinta = cor × (K0 + K1 × clamp((L − lo)/(hi − lo)))`, resto da arte × 0,82 (mesma da web). Regiões: 16 slugs do banco + `neck`.
- **Sessão ao vivo** (`workout_sessions.py`):
  - `_refresh_exercise_aggregates(cur, exercise_id)`: `compute_exercise_stats` das séries; `MAX(muscle_pain)`, `MAX(joint_pain)` das séries; `UPDATE exercises SET ... muscle_pain = COALESCE(rollup, muscle_pain), joint_pain = COALESCE(rollup, joint_pain)`.
  - `_refresh_activity_avg_rpe`: `AVG(avg_rpe)` dos exercícios.
  - `add_session_exercise`: `sequence` default `MAX + 1`; `mapping_id`/`equipment_id` resolvidos; `sets_count=0`; `source_row_hash = f"zepp-live-{uuid}"`.
  - `add_session_set`: `set_number` default `MAX + 1`; insere e recalcula agregados.
  - `finish_session`: `ended_at` = payload, ou `now` se `session_status == 'in_progress'`, senão `None`; `duration = ended_at − started_at` se não informado; `UPDATE activities ... session_status='done'` com `COALESCE`; se `workout_plan_id` → `UPDATE workout_plans SET status='done'`.
  - `_get_live_session` e `_get_finishable_activity` aceitam `session_status` `in_progress` ou `NULL`; `done` → 409.
- **`intelligence/exercise_similarity_service.find_similar_exercises(query, limit=5)`**: sobre `exercise_mappings`: `similarity_score = 100` se nome igual, `92` se `LIKE %q%`, senão `LEAST(85, COALESCE(confidence, 75))`; `WHERE LOWER(COALESCE(mapped_name, original_name)) LIKE %q%`.
- **`intelligence/normalization_service.NormalizationService`**: `normalize_text` (mesma lógica de `fix_text` com substituições adicionais e `strip`), `normalize_percent`, `normalize_json_payload` (dict, string JSON → dict, senão `{}`).
- **(removida em SEG-02, 2026-10-08) `services.rag_summary(limit)`**: texto markdown "# Fitness DB - resumo recente" com seção por atividade (tipo, RPE médio, calorias, observações) e linha por exercício (`sequence. original_name / mapped_name: N séries, reps total, melhor carga, RPE médio`).


### 6. Camada de IA

#### 6.1 Provedor, cliente e modelos

Provedor único: **OpenAI Responses API** (`POST https://api.openai.com/v1/responses`). Não há uso de Bedrock nem de SDK OpenAI; todas as chamadas usam `urllib.request`.

Corpo enviado em todas as chamadas: `{"model": <modelo>, "input": <prompt como string única>}`. Não são enviados `temperature`, `max_output_tokens`, `instructions`, mensagens com papéis (`system`/`user`), `response_format`/JSON schema nem ferramentas. O "system prompt" de cada fluxo é texto concatenado no início da string `input`.

Headers: `Authorization: Bearer {OPENAI_API_KEY}`, `Content-Type: application/json`.

Parsing da resposta (idêntico em todos os clientes): se `data["output_text"]` existe e é truthy, retorna-o; senão concatena com `"\n"` todo `output[*].content[*].text` não vazio.

Implementações:

| Cliente | Onde | Timeout | Chave | Erros |
|---|---|---|---|---|
| `openai_client.call_openai(prompt, model, timeout=90)` | compartilhado | 90 s (default) | lê `OPENAI_API_KEY` em tempo de chamada; vazio ou `"replace_me"` → `OpenAIGenerationError(USER_MESSAGE_UNAVAILABLE)` com `diagnostic="OPENAI_API_KEY nao configurada no backend."` | SEG-12: o texto da exceção é sempre uma das 4 frases genéricas (`USER_MESSAGE_*`), escolhida pelo tipo da falha (`HTTPError` pelo status, `URLError` → indisponível, timeout, resposta inválida); status, corpo truncado e request id ficam em `diagnostic`/`request_id` e vão só para o log (`llm.error`) |
| `workout_plan_ai.call_openai(prompt)` | wrapper | 90 s | via cliente compartilhado | reempacota em `WorkoutPlanGenerationError(OpenAIGenerationError)` |
| `cardio_agent_ai.call_openai(prompt)` | wrapper | 90 s | via compartilhado | `CardioAgentGenerationError` |
| `report_ai._call_openai(prompt)` | wrapper; antes aplica `re.sub(r"\n{3,}", "\n\n", prompt)` | 90 s | via compartilhado | `ReportGenerationError` |
| `insights_ai.call_openai(prompt)` | cópia local | 90 s | constante de módulo `OPENAI_API_KEY = os.environ.get(...)` lida no import | `HTTPError` → `InsightsGenerationError`; `URLError` não é capturado |
| `exercise_set_evaluation_ai.call_openai(prompt)` | cópia local | 60 s | constante de módulo | `SetEvaluationError` (só `HTTPError`) |
| `exercise_summary_ai.call_openai(prompt)` | cópia local | 60 s | constante de módulo | `ExerciseSummaryError` (só `HTTPError`) |
| `workout_summary_ai.call_openai(prompt)` | cópia local | 60 s | constante de módulo | `WorkoutSummaryError` (só `HTTPError`) |

Os módulos com constante de módulo leem `OPENAI_API_KEY` no momento do import; nas Lambdas o import ocorre depois de `secrets_bootstrap.load_secrets()`. `workout_plan_ai.py` e `cardio_agent_ai.py` também declaram `OPENAI_API_KEY = os.environ.get(...)` como constante, mas não a usam (usam o cliente compartilhado).

Modelos (todos com default `"gpt-5.4-mini"`):

| Env var | Módulo | Fluxos |
|---|---|---|
| `OPENAI_WORKOUT_PLAN_MODEL` | `workout_plan_ai.OPENAI_MODEL` | Full Body, ABC via IA, template v1, seletor v2 (todos usam `workout_plan_ai.call_openai`) |
| `OPENAI_MODEL_CARDIO` (legado: `OPENAI_CARDIO_AGENT_MODEL`) | `cardio_agent_ai` | Agente de Cardio |
| `OPENAI_REPORTS_MODEL` | `report_ai` (via `os.getenv`) | 5 análises do relatório e análise de treinos (`/workouts-analysis`) |
| `OPENAI_INSIGHTS_MODEL` | `insights_ai` | `/insights` |
| `OPENAI_SET_EVAL_MODEL` | `exercise_set_evaluation_ai` | avaliação de série |
| `OPENAI_EXERCISE_SUMMARY_MODEL` | `exercise_summary_ai` | resumo de exercício |
| `OPENAI_WORKOUT_SUMMARY_MODEL` | `workout_summary_ai` | resumo de treino |

`abc_workout_ai.OPENAI_MODEL_NOTE` é uma string informativa sem uso funcional.

#### 6.2 Fluxo assíncrono 202 + job_id

**Armazenamento** (`async_jobs.py`): tabela DynamoDB cujo nome vem de `ASYNC_JOBS_TABLE` (`boto3.resource("dynamodb").Table`); região `AWS_REGION` → `AWS_DEFAULT_REGION` → `"sa-east-1"`. Item:

| Atributo | Valor |
|---|---|
| `job_id` (PK) | `uuid4` string |
| `job_type` | string |
| `status` | `pending` → `done` ou `failed` |
| `request` | `json.dumps(request, ensure_ascii=False, default=str)` (quando informado) |
| `result` | JSON string (em `done`) |
| `error` | string (em `failed`) |
| `created_at` / `finished_at` | epoch segundos |
| `ttl` | `created_at + 6 h` (`_JOB_TTL_SECONDS = 21600`) |

Funções: `create_job(job_type, request)` (`put_item`); `mark_done(job_id, result)` / `mark_failed(job_id, error)` (`update_item` com `SET #status, #result|#error, finished_at`); `get_job(job_id)` → `{job_id, job_type, status, created_at, finished_at, result (json.loads; falha → None) se done, error se failed}` ou `None`; `invoke_async(function_name_env_var, payload)`: `boto3.client("lambda").invoke(FunctionName=os.environ[function_name_env_var], InvocationType="Event", Payload=json)`.

**Endpoints que criam jobs**

| Endpoint | `job_type` | Env var da função | Evento enviado | Handler | Dispatch |
|---|---|---|---|---|---|
| `POST /workout-plans/generate` | `plan-generator` | `PLAN_GENERATOR_FUNCTION_NAME` | `{job_id, payload (WorkoutPlanGenerateIn mode=json), user_id}` | `lambda_plan_generator.handler` | `plan_generation_core.dispatch_plan_generation(WorkoutPlanGenerateIn(**payload), user_id)` |
| `POST /cardio-recommendations/generate` | `cardio-agent` | `CARDIO_AGENT_FUNCTION_NAME` | `{job_id, payload (dict), user_id}` | `lambda_cardio_agent.handler` | `cardio_generation_core.dispatch_cardio_generation` |
| `POST /exercises/evaluate-set` | `workout-ai-evaluate-set` | `WORKOUT_AI_FUNCTION_NAME` | `{job_id, job_type, payload, user_id}` | `lambda_workout_ai.handler` | `workout_ai_generation_core.dispatch_evaluate_set(payload)` |
| `POST /exercises/{id}/summarize` | `workout-ai-exercise-summary` | `WORKOUT_AI_FUNCTION_NAME` | `payload={"exercise_id"}` | idem | `dispatch_exercise_summary` |
| `POST /activities/{id}/ai-summary/generate` | `workout-ai-activity-summary` | `WORKOUT_AI_FUNCTION_NAME` | `payload={"activity_id"}` | idem | `dispatch_activity_summary` |
| `POST /workouts-analysis/generate` | `workouts_analysis` | `WORKOUT_AI_FUNCTION_NAME` | `payload={"days"}` | idem | `workouts_analysis_core.dispatch_workouts_analysis` |
| `POST /insights/generate` | `insights` | `WORKOUT_AI_FUNCTION_NAME` | `payload={"question","context"}` | idem | `insights_core.dispatch_insights` |
| `POST /reports/generate` | `report_generation` | `REPORTS_FUNCTION_NAME` | `{job_id, report_id, user_id, days}` | `lambda_reports.handler` | `reports_core.run_report_generation(report_id, user_id, days)` |

Os endpoints retornam `JSONResponse(202, {"job_id", "status": "pending"})`, exceto `/reports/generate`, que retorna `{"id", "job_id", "status": "generating"}` (o decorator declara `status_code=202`).

**Estados e polling**: o cliente consulta `GET /jobs/{job_id}` (404 se inexistente/expirado). Para relatórios, o estado consultado pelo frontend é a linha `reports` via `GET /reports/{id}` (`generating` → `done`/`failed`); o job DynamoDB é atualizado em paralelo.

**Dispatches** (exceções `HTTPException` são usadas para sinalizar erros e viram `str(detail)` em `mark_failed`):

- `dispatch_workout_ai_generation(job_type, payload, user_id)`: roteia pelos 5 `job_type`; desconhecido → `HTTPException(400)`.
- `dispatch_evaluate_set(payload)`: `evaluate_set(payload)`; `SetEvaluationError` → 503.
- `dispatch_exercise_summary(payload, user_id)`: exige `exercise_id` (400); `services.exercise_summary_context(exercise_id, user_id)` (404); monta `{exercise_name = original_name, reps_target, load_target, rpe_target (do workout_plan_exercise vinculado), sets, muscle_pain, joint_pain, notes}` → `summarize_exercise`; `ExerciseSummaryError` → 503.
- `dispatch_activity_summary(payload, user_id)`: exige `activity_id` (400); `activity_detail` (404); `generate_workout_summary(detail)` (503); `save_activity_ai_summary` grava `activities.ai_summary` e `ai_summary_generated_at = NOW()` (404 se nada atualizado); retorna `{summary, generated_at}`.
- `dispatch_workouts_analysis(payload, user_id)`: `days = payload.days or 90`; `DashboardAggregatorService().build_training_evolution_payload(days, user_id)`; `ReportDataService().build_rag_context(training)`; `report_ai.analyze_training_evolution(training, days, rag_context)` (`ReportGenerationError` → 503); retorna `{model: report_ai.OPENAI_MODEL, insight, training}`.
- `dispatch_insights(payload, user_id)`: `generate_insight(question, context)` (`InsightsGenerationError` → 503; `question` vazio → erro "question é obrigatório.").
- `dispatch_cardio_generation(payload, user_id)`: `_ensure_schema()`; `days = payload.days or 14`; `reference_date = payload.reference_date or date.today().isoformat()`; se `workout_plan_id`, valida `workout_plans.id AND user_id` (404); `generate_cardio_recommendation(days, proposed_workout, reference_date, reference_time, user_id)` (`CardioAgentGenerationError` → 503); `_insert_recommendation({reference_date, workout_plan_id, model, context_snapshot, recommendation_markdown}, user_id)` (exceção → 500); retorna `{id, model, reference_date, workout_plan_id, recommendation_markdown}`.
- `dispatch_plan_generation`: ver 5.12; o resultado (draft) é gravado no job; não persiste o plano.

#### 6.3 Self-calls HTTP e invocação entre Lambdas

- Não há mais self-call HTTP nem invocação síncrona entre Lambdas. `workout_plan_ai._internal_get` e `engines_client.py` foram removidos no SEG-04 (2026-10-09): `gather_ficha_context` lê academias (`_load_gyms` → `gyms.list_gym_items`) e catálogo (`_load_exercise_catalog` → `exercise_mapping_catalog.query_mapping_catalog`, limite 500) direto do banco, com as mesmas funções de serviço das rotas `GET /gyms` e `GET /exercise-mapping-catalog`; `template_volume_planner._load_projected_recovery` exige `user_id`. A recomendação de equipamento e a recuperação já eram chamadas diretas por `user_id` (PR-08/PR-09).

#### 6.4 Prompts (transcrição)

A transcrição dos prompts de sistema (Full Body, ABC, Modelo v1, Motor v2, agente de cardio, avaliação de série,
resumos, insights e relatório) mora no repositório **privado** `muscle_fit_privado`, montado em `api/privado`:
`api/privado/docs/PROMPTS_TRANSCRICAO.md` (M103 P1). Desde o M103 P1, a prosa dos prompts migra para
`api/privado/prompts/<feature>/<PROMPT_VERSION>.md`, lida por `app/prompt_store.py`; o builder de cada feature
segue no código público (ver `docs/motores/FONTES_DA_VERDADE.md` §73.1).

#### 6.5 RAG

**Origem** (`rag_storage.ensure_rag_local()`):
- `RAG_S3_BUCKET` ausente → retorna `Path(RAG_MARKDOWN_DIR)` (default `/app/rag`) sem sincronizar.
- Presente: uma vez por container (flag `_SYNCED` + marcador `.synced` no diretório, protegido por `threading.Lock`), lista objetos em `s3://{RAG_S3_BUCKET}/{RAG_S3_PREFIX}` (default `markdown/`, barra final garantida), ignora chaves terminadas em `/`, rejeita chaves com `..`/absolutas ou que resolvam fora do diretório (qualquer rejeição → sincronização considerada incompleta), baixa em paralelo (`ThreadPoolExecutor`, até 8 workers; grava `.part` e renomeia). Marcador só é escrito se todos os downloads tiverem sucesso. Zero objetos → warning, sem marcador. Qualquer exceção → warning; retorna o diretório.
- `boto3.client("s3")` é criado sem região explícita.

**Consumo**:

| Consumidor | Função | Conteúdo |
|---|---|---|
| Full Body, ABC IA, template v1, v2, relatório, workouts-analysis | `rag_chunking.select_rag_text(target_groups, rag_folder=None, has_training_type=False)` | seletivo (abaixo) |
| Insights | `workout_plan_ai.load_rag_text()` | todos os `.md` da raiz, integrais |
| Avaliação de série | `load_progressao_rag_text()` | só `PROGRESSAO.md` |
| Tipos de treinamento (validação) | `list_training_type_rag_folders()` | nomes de subpastas |

**`select_rag_text`** (partes unidas por `"\n\n---\n\n"`):
1. `PROGRESSAO.md` integral (`"## Arquivo: PROGRESSAO.md\n\n..."`; erro de leitura → warning e omitido).
2. `HIPERTROFIA.md`: `parse_chunks` do arquivo inteiro → `select_chunks_for_groups`.
3. `FULLBODY.md` e `ANTEBRACO.md`: `parse_chunks` a partir do marcador `"# 3. Chunks RAG"` (sem marcador → lista vazia → fallback arquivo inteiro).
4. `TABELA_EXERCICIOS.md`: tabela markdown filtrada pela coluna "Grupamento Muscular" (normalizada); sem linhas → texto "(nenhuma linha relevante para os grupos musculares desta ficha)"; falha de parsing → arquivo inteiro.
5. `discover_additional_files()`: demais `*.md` da raiz fora de `KNOWN_FILES = {PROGRESSAO.md, HIPERTROFIA.md, FULLBODY.md, ANTEBRACO.md, TABELA_EXERCICIOS.md}` e `EXCLUDED_FILES = {BIOIMPEDANCIA.md, CARDIO.md}`, em ordem alfabética, cada um via `parse_chunks` + seleção (sem chunks → arquivo inteiro).
6. Se `rag_folder`: `load_rag_folder_text(rag_folder)` (valida contra `list_training_type_rag_folders()`, senão `ValueError`; concatena `*.md` da subpasta `TIPOS_TREINO/{folder}` por nome). Senão, se `not has_training_type`: todos os `*.md` de `TIPOS_TREINO/**` recursivamente (ordenados por caminho relativo) com seleção por chunks. Com `has_training_type=True` e sem folder: nada adicional.

`parse_chunks(text, source_file)`: cabeçalho `^## ([A-Z]{2,10}\d?-\d+) — (.+)$`; conteúdo até o próximo cabeçalho de chunk ou próximo cabeçalho nível 1 (`^# \S.*$`); `tags` = itens de `**Tags:**` + `**Aliases:**` (split por vírgula, sem crases/asteriscos).

`select_chunks_for_groups(chunks, target_groups)`: `wanted_roots` = raízes de `MUSCLE_GROUP_ROOTS` contidas no nome normalizado (lower, sem acento) de cada grupo; `chunk_roots` = raízes contidas nas tags normalizadas, ou `FILE_TOPIC_ROOTS[source_file]` se nenhuma; chunk sem raízes → incluído sempre; com raízes → incluído se interseção com `wanted_roots`.

`MUSCLE_GROUP_ROOTS = ("peito","peitoral","costas","dorsal","ombro","deltoide","biceps","triceps","perna","quadriceps","posterior de coxa","panturrilha","gluteo","antebraco","abdomen","abdominal","core","trapezio","lombar","coxa","quadril","manguito")`.

`FILE_TOPIC_ROOTS`: `ABDOMEN.md {abdomen, abdominal, core}`, `AGACHAMENTO.md {coxa, quadriceps, gluteo, posterior de coxa}`, `BICEPS.md {biceps}`, `COSTAS.md {costas, dorsal}`, `PANTURRILHA.md {panturrilha}`, `PEITO.md {peito, peitoral}`, `PERNA.md {coxa, quadriceps, posterior de coxa, gluteo, panturrilha}`, `POSTERIOR_COXA.md {posterior de coxa, coxa}`, `TRAPEZIO.md {trapezio}`, `TRICEPS.md {triceps}`.

Rótulos no texto: `"## Arquivo: {nome} (RAG seletivo)\n\n{chunks unidos por ---}"`, ou `"(nenhum chunk relevante para os grupos musculares desta ficha)"`; fallback `"## Arquivo: {nome}\n\n{texto}"` com `logger.warning` ("rag_chunking: fallback para arquivo inteiro de ...").

`list_training_type_rag_folders()`: subpastas imediatas de `TIPOS_TREINO`, ordenadas por inteiro líder (`^\s*(\d+)`) e depois alfabeticamente; erro de listagem → `[]`.

O conteúdo dos arquivos RAG não está em `api/app` e não foi verificado neste documento.

#### 6.6 Cards ilustrados de exercícios (Estratégia B)

Card de execução em retrato 2:3 (1024×1536) para o "Como executar" do Modo Treino, gerado por IA **com aprovação humana obrigatória** antes de publicar. Desde a Estratégia B (#349) **a IA só ilustra**: a OpenAI devolve as duas cenas (posição inicial e movimento), sem texto, logo, número, rótulo nem moldura; **a aplicação compõe** o card — logo oficial, título, rótulos "1 Posição inicial"/"2 Movimento" e bloco "MÚSCULOS" (principais/secundários do catálogo) — de forma determinística. O que o admin revisa é exatamente o que é publicado.

Módulos (separação prompt/serviço/rotas igual à da descrição de execução): `exercise_card_prompt.py` (`PROMPT_VERSION = "MUSCLEFIT_EXERCISE_CARD_V2_ILLUSTRATION"`; `load_context` lê catálogo, aliases, músculos por papel, equipamentos e `execution_description`; só `text_level = minimal`), `openai_client.generate_image` (`/v1/images/generations`, `urllib`, `background="transparent"`; envs `OPENAI_IMAGE_MODEL` = `gpt-image-1.5`, `OPENAI_IMAGE_QUALITY` = `medium`, `OPENAI_IMAGE_SIZE` = `1024x1536`, `OPENAI_IMAGE_TIMEOUT_SECONDS` = 150; retry só 429/5xx rápidos, nunca após timeout), `exercise_card_composer.py` (Pillow puro, `LAYOUT_VERSION = "CARD_LAYOUT_V1"`, fontes Oswald + Inter em `api/app/assets/fonts/`, logo `api/app/assets/branding/musclefit-logo.png`, tokens de `frontend/src/theme.ts`; extrai as cenas pelo alfa ou pela distância de cor ao fundo e as encaixa na mesma escala), `exercise_card_processor.py` (`encode_card`: valida, tira metadados, WebP q85 + PNG + miniatura 400 px — sem recompor), `exercise_card_storage.py` (chaves, `put_*` com `Cache-Control`, presign, `public_urls`, `training_card`), `exercise_card_service.py` (estados, SQL, limites, sweep, serialize, `ExerciseCardError(code, http_status)`), `exercise_card_jobs.py` (dispatch + `run_generation_job`), `exercise_cards.py` (rotas, 2.27), eventos `EVENT_EXERCISE_CARD_*` em `observability.py` (nunca loga prompt/instrução).

Fluxo:

1. **Admin gera** (`/dominio-exercicios`, `POST …/card-generations`): transação cria a geração `PENDING` (no máximo 1 viva por exercício; teto global `EXERCISE_CARD_MAX_IN_FLIGHT` = 2 por causa do limite de 10 Lambdas concorrentes; `EXERCISE_CARD_MAX_GENERATIONS_PER_DAY` = 100 por admin por dia UTC, tentativas rejeitadas/falhas contam → 429), cria o job no DynamoDB e invoca `musclefit-plan-generator` com `job_kind = "exercise-card"` (timeout da Lambda 180 s).
2. **Worker**: transição atômica `PENDING → GENERATING` (`UPDATE … WHERE status='PENDING'`; `rowcount = 0` = redelivery → sai sem chamar a OpenAI); gera a ilustração; grava `exercise-card-drafts/{gid}/illustration.png`; compõe o card; grava `preview.png`; `GENERATED` com largura/altura/bytes/tokens/custo. Falha de composição → `FAILED CARD_COMPOSITION_FAILED` (a ilustração paga fica guardada; `exercise_card_calibration.py --compose-only` recompõe sem custo).
3. **Admin revisa** o preview por URL pré-assinada (15 min) e: **aprova** (`APPROVING` → lê o preview → `encode_card` → 3 uploads imutáveis em `exercise-cards/{id}/v{n}/` → transação: anterior `SUPERSEDED`, esta `PUBLISHED` com `version = n`, ponteiro `exercise_catalog.card_generation_id/card_version/card_published_at`); **rejeita** (`REJECTED` com motivo); ou **regenera** (pai `GENERATED` vira `REJECTED(regenerated)` e nasce a filha `PENDING` com `parent_generation_id` e a instrução, na mesma transação; o motivo humano entra na revisão do prompt).
4. **Sweep** preguiçoso no POST/GET: `PENDING/GENERATING` mais velhas que `EXERCISE_CARD_STALE_MINUTES` (10) → `FAILED WORKER_LOST`; `APPROVING` > 2 min → `GENERATED PUBLISH_INTERRUPTED`.
5. **Publicação** = `card_version` gravado só DEPOIS dos 3 uploads: quem lê o ponteiro sabe que os objetos existem. A CloudFront serve `/exercise-cards/*` com cache imutável (versão nova = path novo; 403/404 viram `index.html` 200, por isso o frontend confia no ponteiro e nunca em `onError`).
6. **Modo Treino** lê o ponteiro (2.27, "Modo Treino") e mostra o card (Parte III, 4.4 / 5.2 / 6.7 / 8).

Flag `EXERCISE_CARD_GENERATION_ENABLED` (api-core; `false` em produção até a decisão de ligar) exposta em `/auth/me` como `features.exercise_cards`. Sem card publicado nenhum, o Modo Treino recebe só `exercise_card: null` e nada aparece.


### 7. Integrações externas

#### 7.1 Intervals.icu

**Cliente** (`intervals_client.py`, sem SDK, `urllib.request`):

| Item | Valor |
|---|---|
| Base URL | `INTERVALS_ICU_BASE_URL` (default `https://intervals.icu/api/v1`, `rstrip("/")`) |
| User-Agent | `INTERVALS_ICU_USER_AGENT` (default `Mozilla/5.0 DougFitnessDashboard/1.0`) |
| Autenticação | `Authorization: Basic base64("API_KEY:{api_key}")`; `Accept: application/json` |
| Endpoints | `GET /athlete/{quote(athlete_id)}/activities?oldest=YYYY-MM-DD&newest=YYYY-MM-DD`; `GET /athlete/{athlete_id}/wellness?oldest=...&newest=...` |
| Timeout | 30 s |
| Erros | HTTP 401 → `IntervalsClientError("Chave de API do Intervals.icu inválida ou expirada.", 401)`; outro HTTP → `IntervalsClientError(f"Intervals.icu retornou erro HTTP {code}: {body[:500]}", code)`; `URLError` → `IntervalsClientError("Falha de rede ao consultar Intervals.icu: {reason}")` (status `None`); corpo não JSON → `IntervalsClientError` |
| Normalização | corpo vazio → `[]`; lista → só dicts; dict → primeira lista em `items/activities/wellness/data`, senão `[dict]` |

`fetch_activities_and_wellness` chama as duas requisições sequencialmente.

**Credenciais**: `users.intervals_athlete_id` (texto) e `users.intervals_api_key_encrypted` (Fernet). `_get_user_intervals_credentials(user_id)`: ausência de qualquer um → `HTTPException(400, "Configure sua chave do Intervals.icu no perfil antes de sincronizar.")`; `decrypt_secret` com `ValueError` → `HTTPException(500, "Não foi possível descriptografar a chave do Intervals.icu configurada. Reconfigure-a no seu perfil.")`. `_fetch_live_activities_and_wellness(user_id, days)`: intervalo `[today − days, today]`; `IntervalsClientError` → `HTTPException(status_code or 502 (se < 400 usa 502), str(exc))`.

**Cache MySQL** (`intervals_integration._ensure_schema`, executado uma vez por processo): `CREATE TABLE IF NOT EXISTS intervals_activities` e `intervals_wellness`; `ADD COLUMN readiness_score DECIMAL(6,2)` e `daily_effort_load INT` em `intervals_wellness`; `ADD COLUMN user_id` nas duas; backfill `user_id` com admin; quando não há nulos, `MODIFY user_id NOT NULL` e troca de chave: `intervals_wellness` → `PRIMARY KEY (user_id, wellness_date)`; `intervals_activities` → remove `uq_intervals_activities_external_id` e cria `uq_intervals_activities_user_external (user_id, external_id)`. Erros "Duplicate column" são ignorados via `is_duplicate_column_error`.

**Mapeamento de campos** — `_upsert_activities(user_id, items)` (`INSERT ... ON DUPLICATE KEY UPDATE ... synced_at = CURRENT_TIMESTAMP`, uma execução por item, dentro de `with _connect() as conn` com commit ao sair):

| Coluna | Chaves do payload (primeira não vazia) | Conversão |
|---|---|---|
| `external_id` | `id, activity_id, activityId, file_id, fileId, filename, fit_file, icu_activity_id`; ausente → `sha256(json compacto do item)` | str |
| `activity_date` | `start_date_local, startDateLocal, start_date, startDate, start_time, startTime, date, day` | `_to_date` (10 primeiros caracteres); ausente → item contado em `skipped` |
| `start_time` | `start_date_local, startDateLocal, start_date, startDate, start_time, startTime` | `_to_datetime` (`Z` removido, `T`→espaço, 19 chars) |
| `name` | `name, title, description` | default `"Intervals activity"` |
| `sport_type` | `type, sport, sport_type, sportType, activity_type, activityType` | default `"activity"` |
| `moving_time_seconds` | `moving_time, movingTime, moving_time_seconds, duration, duration_seconds` | int |
| `elapsed_time_seconds` | `elapsed_time, elapsedTime, elapsed_time_seconds` | int |
| `distance_meters` | `distance, distance_meters, distanceMeters` | float |
| `calories_kcal` | `calories, calories_kcal, caloriesKcal` | float |
| `avg_hr_bpm` | `average_heartrate, avg_hr, avgHr, avg_heartrate, averageHeartRate` | float |
| `max_hr_bpm` | `max_heartrate, max_hr, maxHr, maxHeartRate` | float |
| `training_load` | `icu_training_load, training_load, load, icuTrainingLoad` | float |
| `intensity` | `intensity, icu_intensity, icuIntensity` | float |
| `raw_json` | item inteiro | `CAST(json AS JSON)` |

`_upsert_wellness(user_id, items)`:

| Coluna | Chaves | Conversão |
|---|---|---|
| `wellness_date` | `id, date, day, local_date, wellness_date` | `_to_date`; ausente → `skipped` |
| `fitness_ctl` | `ctl, fitness, fitness_ctl` | float |
| `fatigue_atl` | `atl, fatigue, fatigue_atl` | float |
| `form_tsb` | `tsb, form, form_tsb`; ausente → `ctl − atl` (`_derive_form_tsb`) | float |
| `ramp_rate` | `rampRate, ramp_rate, ramp` | float |
| `weight_kg` | `weight, weight_kg, weightKg` | float |
| `resting_hr_bpm` | `restingHR, resting_hr, resting_hr_bpm, restingHeartRate` | float |
| `hrv_ms` | `hrv, hrv_ms` (somente rMSSD; `hrvSDNN` **não** é fallback — sem rMSSD fica nulo) | float |
| `sleep_seconds` | `sleepSecs, sleep, sleep_seconds, sleep_time, sleepTime` | int |
| `readiness_score` | `readiness, readiness_score, readinessScore` | float |
| `daily_effort_load` | `ctlLoad, atlLoad, daily_effort_load` | int |
| `raw_json` | item | JSON |

Retorno de ambos: `{received, upserted, skipped}`.

**Rotas e fontes** (detalhes em 2.15):

- `POST /sync`: busca ao vivo e grava no cache com `user_id` da identidade.
- `POST /activities` e `POST /wellness`: recebem payloads prontos (lista ou dict com `items`/`activities|wellness`/`data`) e gravam no cache.
- `GET /summary`, `/activities`, `/wellness`: leem o cache filtrando `user_id` e janela `days`.
- `GET /dashboard`: atividades ao vivo; wellness do **cache** (fonte única com `GET /wellness` / Histórico de Indicadores físicos). Antes de ler, `_refresh_wellness_cache` busca no Intervals.icu os últimos `min(days, 7)` dias e grava via `intervals_store.upsert_wellness` (mesmo upsert do job), pulando se o usuário teve wellness gravado há < 5 min (`synced_at`). Melhor-esforço: sem credencial, falha do Intervals.icu ou do banco → loga e serve o cache (`wellness_refresh` = `refreshed|throttled|not_configured|failed`).
- `GET /dashboard` (M53): as atividades buscadas ao vivo também são gravadas em `intervals_activities` por `_store_live_activities`, com o MESMO upsert do job (`intervals_store.upsert_activities`) e a MESMA associação de FC com os treinos (`intervals_sync.link_heart_rate`), na mesma transação. Grava só o que falta no cache + os últimos 7 dias (`ACTIVITIES_CACHE_REWRITE_DAYS`), nada além de 90 dias (`ACTIVITIES_CACHE_MAX_DAYS`), throttle de 5 min por `synced_at`. Melhor-esforço: falha → log `intervals_activities_cache_failed` e a tela segue (`activities_cache` = `stored|unchanged|throttled|failed`).
- `GET /cardio-dashboard`: lê o Intervals.icu ao vivo (não o cache).

Agregações ao vivo:

- `_daily_activity`: por data (`start_date_local...`): `activities`, `minutes += round(seg/60)`, `calories`, `distanceKm`, `load`, `avgHr` médio (só > 0).
- `_wellness_trend` (só `/cardio-dashboard`): `{date, fitness, fatigue, form (derivado se ausente), restingHr, hrv (somente rMSSD: `hrv, hrv_ms`), rampRate}` ordenado por data; `_pick_number` retorna `0.0` quando ausente.
- `_cached_wellness_trend` (`/dashboard`): mesmo shape lido de `intervals_wellness`; ausente → `null` (tela mostra "-").
- `_recent_activities`: 30 mais recentes `{date, name, sport, minutes, calories, avgHr, load}`.
- Classificação para `/cardio-dashboard` (texto `sport + name` normalizado sem espaços/hífen/underscore, lower): força se contém algum de `weighttraining, strengthtraining, strength, weights, musculacao, musculação, treinodeforca, treinodeforça, gym`; cardio (se não força) se contém algum de `virtualrun, walk, walking, caminhada, run, running, corrida, ride, virtualride, bike, biking, cycling, bicycle, bicicleta, indoorcycling, spinning, hike, hiking, trail, treadmill, elliptical, cardio, row, rowing, swim, swimming`; demais → ignoradas.
- `_group_by_day` (cardio): `activities, minutes, distanceKm, calories, load, avgIntensity, avgHr, maxHr`; `_group_by_type`: mesmo por `sport_type`, ordenado por `minutes` desc; `totals` inclui `strength_excluded`, `ignored_other`, médias de FC/intensidade e `max_hr_bpm`. Resposta inclui campos fixos `"source": "intervals_icu_direct"`, `"token_used": "users.intervals_api_key_encrypted"` e `classification` com exemplos.

**Consumidores internos do cache**: `IntelligenceServiceBase.get_latest_intervals_wellness` (TSB para recuperação muscular); `CardioAgentContextService._get_intervals_wellness`; `IntervalsCorrelationService`; `workout_plan_ai.get_latest_intervals_wellness` (seção do prompt); `report_pdf_builder._build_cardio_chart`. Nenhum job agendado de sincronização existe em `api/app` (a sincronização ocorre só via `POST /sync` ou pelos POSTs de ingestão).

#### 7.2 App Zepp OS / Amazfit

- **Pareamento** (`devices.py`): `POST /devices/pair` exige token mestre; o token de dispositivo retornado passa a autenticar os demais endpoints e resolve para o admin (ver 3.5). `last_seen_at` é atualizado a cada resolução de identidade bem-sucedida.
- **Sessão ao vivo** (`workout_sessions.py`, ver 2.12 e 5.18): `start` cria `activities` com `external_id = "zepp-session-{uuid4.hex}"`, `source_name` default `"amazfit-zepp"` (tipo `wearable` em `sources`), `session_status='in_progress'`; exercícios e séries são gravados incrementalmente com recálculo de agregados; `finish` marca `done` e, se houver `workout_plan_id`, `workout_plans.status='done'`. Esses endpoints também são usados pela tela web `/registro-exercicios` (comentário em `_get_live_session`).
- **Mapa corporal PNG** (`GET /workout-plans/{id}/muscle-map.png`) destinado ao app do relógio.
- **Nomes Amazfit**: `exercise_external_mappings` com `provider='AMAZFIT'`; lidos em diversas queries como `mapped_name`/`amazfit_name`; removidos dos prompts por `_strip_amazfit_fields` (exceto no motor v2, cuja matriz de candidatos inclui `amazfit_name`).
- `_ensure_schema` de `workout_sessions` adiciona (se ausentes) `activities.session_status`, `exercise_sets.hr_avg_bpm/hr_max_bpm`, `exercise_sets.muscle_pain/joint_pain`, `activities.workout_plan_id` (FK `workout_plans ON DELETE SET NULL`), `exercises.workout_plan_exercise_id` (FK `workout_plan_exercises ON DELETE SET NULL`).

#### 7.3 AWS

| Serviço | Módulo | Uso | Configuração |
|---|---|---|---|
| RDS Data API (Aurora) | `db.py` | todo acesso a dados (seção 8) | `AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN`, `AURORA_DATABASE`, região |
| Secrets Manager | `secrets_bootstrap.py` | `get_secret_value` para `OPENAI_API_KEY`, `API_TOKEN`, `SECRET_ENCRYPTION_KEY` | ARNs em `OPENAI_API_KEY_SECRET_ARN`, `API_TOKEN_SECRET_ARN`, `SECRET_ENCRYPTION_KEY_SECRET_ARN` |
| DynamoDB | `async_jobs.py` | estado dos jobs assíncronos | `ASYNC_JOBS_TABLE` |
| Lambda (Invoke) | `async_jobs.invoke_async` (Event) | disparo das Lambdas de IA/relatório | `PLAN_GENERATOR_FUNCTION_NAME`, `CARDIO_AGENT_FUNCTION_NAME`, `WORKOUT_AI_FUNCTION_NAME`, `REPORTS_FUNCTION_NAME` |
| S3 — uploads | `gyms.py` (`_save_photo`, `_read_photo`, `_delete_photo`), `users.py` (reusa), `reports_core.py` | fotos `gyms/{id}{ext}`, `equipment/{id}{ext}`, `users/{id}{ext}`; PDFs `reports/{id}.pdf` | `EQUIPMENT_PHOTOS_BUCKET` (ausente em `gyms.py` → `HTTPException(503, {"error": "photo_storage_unavailable", ...})`; em `reports_core.py` → `RuntimeError`) |
| S3 — RAG | `rag_storage.py` | download de `markdown/` para `RAG_MARKDOWN_DIR` | `RAG_S3_BUCKET`, `RAG_S3_PREFIX` |
| CloudFront | `gyms._invalidate_cloudfront_paths` | `create_invalidation` dos paths `/{key}` após upload/delete de foto/logo (`CallerReference = "gyms-photo-{ms}-{uuid8}"`); falha → `logger.warning`; sem `CLOUDFRONT_DISTRIBUTION_ID` → warning e nada | `CLOUDFRONT_DISTRIBUTION_ID` |

Detalhes de upload de foto (`_save_photo(table, row_id, subdir, file)`): tipos aceitos `image/jpeg→.jpg`, `image/png→.png`, `image/webp→.webp`, `image/gif→.gif` (senão 400 "Formato de imagem não suportado. Use JPEG, PNG, WEBP ou GIF."); limite `MAX_PHOTO_BYTES = 8 MiB` (400 "Imagem muito grande (máx. 8MB)."); apaga objetos com prefixo `{subdir}/{row_id}.`; `put_object` com `ContentType`; invalida CloudFront para chaves antigas e nova. `_read_photo` retorna `Response` com media type pela extensão (`NoSuchKey` → 404). URLs públicas de foto/logo são montadas como `https://app.musclefit.com.br/{path}` (`PUBLIC_ASSETS_DOMAIN` em `gyms.py`; o mesmo literal aparece em SQL de `services.py`, `gyms.recommend-all` e `exercise_catalog_admin.get_exercise_detail`). Foto de usuário não recebe URL pública (`has_photo` apenas).

Região boto3: `AWS_REGION` → `AWS_DEFAULT_REGION` → `"sa-east-1"` em `db.py`, `async_jobs.py`, `secrets_bootstrap.py`, `gyms.py`, `reports_core.py`; `rag_storage.py` e `boto3.client("cloudfront")` não passam região.

#### 7.4 `secrets_bootstrap.load_secrets()`

`_SECRETS = [("OPENAI_API_KEY", "OPENAI_API_KEY_SECRET_ARN"), ("API_TOKEN", "API_TOKEN_SECRET_ARN"), ("SECRET_ENCRYPTION_KEY", "SECRET_ENCRYPTION_KEY_SECRET_ARN")]`. Para cada par em que a env do ARN existe e a env de destino ainda não está definida: `secretsmanager.get_secret_value(SecretId=arn)`; exceção → ignora aquele segredo (sem log); `SecretString` truthy → `os.environ[nome] = valor`. Nenhum par pendente → retorna sem importar boto3. Chamado no topo de `main.py`, `main_engines.py` e dos 4 handlers Lambda, antes dos imports de negócio.

#### 7.5 OpenAI

Ver seção 6.1.


### 8. Acesso a dados a partir dos módulos de negócio

#### 8.1 Camada `db.py` (como é consumida)

- Não há ORM nem pool; cada `get_conn()` cria `boto3.client("rds-data")` e abre uma transação real (`begin_transaction`) no construtor de `Connection`.
- `Cursor.execute(sql, params)`: `_translate_sql` troca cada `%s` por `:p0, :p1, ...` e `%%` por `%` (sem params, só troca `%%`); número de placeholders diferente do número de parâmetros → `DataApiError`. Chama `execute_statement(..., includeResultMetadata=True, transactionId=...)`; falha → `DataApiError(f"execute_statement falhou para SQL: {sql!r} -- {exc}")`.
- Conversão de parâmetros (`_to_data_api_param`): `None → isNull`; `bool → booleanValue`; `int → longValue`; `float → doubleValue`; `Decimal → stringValue + typeHint DECIMAL`; `datetime → "%Y-%m-%d %H:%M:%S" + TIMESTAMP`; `date → isoformat + DATE`; `time → "%H:%M:%S" + TIME`; `bytes → blobValue`; `str → stringValue`; outros (dict/list) → `json.dumps + typeHint JSON`.
- Resultado: linhas como `dict` (nome = `label` ou `name`); valores decodificados de `stringValue/longValue/doubleValue/booleanValue/blobValue/arrayValue` (decimais, datas e JSON chegam como string). `lastrowid = generatedFields[0].longValue`; `rowcount = numberOfRecordsUpdated` ou nº de linhas; `description` em DBAPI com `typeName`.
- Não existe `executemany`; nenhum módulo o usa. Loops de inserção executam um `execute_statement` por linha.
- `Connection.commit()`/`rollback()` encerram a transação; `close()` faz rollback se ainda houver transação aberta; `with conn:` faz commit sem exceção e rollback com exceção.
- `transaction()`: context manager que faz `commit` ao sair, `rollback` em exceção, `close` sempre.
- Helpers de erro por texto: `is_duplicate_column_error` ("Duplicate column name"), `is_duplicate_key_error` ("Duplicate entry"), `is_unknown_key_error` ("check that column/key exists" ou "Can't DROP").

#### 8.2 Padrões de uso por módulo

| Padrão | Módulos |
|---|---|
| `with transaction() as conn: with conn.cursor() as cur:` (commit automático) | `services.py`, `security.py`, `auth.py`, `users.py`, `devices.py`, `workout_sessions.py`, `workout_plan_templates.py`, `training_types.py`, `abc_workout_template.py`, `abc_workout_ai.py`, `workout_plan_ai.py`, `workout_plan_templates_engine*.py`, `intelligence/template_volume_planner.py` |
| `conn = _connect()` / `get_conn()` + `try ... conn.commit() ... finally conn.close()` | `cardio_recommendations.py`, `body_composition.py`, `sleep_metrics.py`, `manual_effort_metrics.py`, `reports.py`, `reports_core.py`, `cardio_generation_core.py` |
| `with _connect() as conn:` (usa `Connection.__enter__/__exit__`, commit ao sair) | `gyms.py`, `exercise_mapping_catalog.py`, `exercise_catalog_admin.py`, `intervals_integration.py`, `analytics.py` (`_fetch_all`), `muscle_group_equipment.py`, `exercise_mapping_canonical.query/execute` |
| `get_conn()` sem commit (somente leitura; `close` faz rollback) | `intelligence/base.IntelligenceServiceBase.fetch_all`, `main.ready`, `main_engines.ready` |

`_connect()` em `analytics.py`, `gyms.py`, `intervals_integration.py`, `muscle_group_equipment.py`, `exercise_mapping_catalog.py`, `exercise_catalog_admin.py`, `cardio_recommendations.py`, `body_composition.py`, `sleep_metrics.py`, `manual_effort_metrics.py` converte `DataApiError` (ou qualquer exceção, em `reports.py`) em `HTTPException(503, {"error": "database_unavailable", ...})`. Como `get_conn()` só lê env vars e abre transação, o 503 cobre ausência de config e falha de `begin_transaction` (esta última é `ClientError` do boto3, não `DataApiError`; tratamento nesse caso não verificado fora de `reports.py`).

Cada `transaction()`/`get_conn()` é uma transação Data API independente; funções que abrem várias conexões em sequência (ex.: `create_workout_plan` → `get_workout_plan`; `VolumeService` com vários `fetch_all`) executam em transações distintas.

#### 8.3 Migrações em tempo de execução (DDL no código)

| Módulo / função | DDL |
|---|---|
| `devices._ensure_schema` (a cada request dos 3 endpoints) | `CREATE TABLE IF NOT EXISTS devices`; `ADD COLUMN user_id`; backfill admin; `MODIFY user_id NOT NULL` |
| `cardio_recommendations._ensure_schema` (1x por processo) | `CREATE TABLE IF NOT EXISTS cardio_recommendations`; `ADD COLUMN workout_plan_id` + índice; `ADD CONSTRAINT fk_cardio_recommendations_workout_plan`; `ADD COLUMN user_id`; backfill; `NOT NULL` |
| `sleep_metrics._ensure_schema` / `manual_effort_metrics._ensure_schema` (1x) | `CREATE TABLE IF NOT EXISTS`; `user_id`; backfill; `NOT NULL`; `DROP INDEX local_date`; `ADD UNIQUE KEY uq_*_user_date` |
| `intervals_integration._ensure_schema` (1x) | ver 7.1 |
| `reports._ensure_schema` (1x) | `CREATE TABLE IF NOT EXISTS reports` |
| `workout_sessions._ensure_schema` (1x) | ver 7.2 |
| `services._ensure_set_hr_columns`, `_ensure_workout_plans_time_column`, `_ensure_workout_plans_split_day_column`, `_ensure_plan_link_columns` (1x cada) | `ADD COLUMN` condicionais via `information_schema.COLUMNS` |
| `exercise_mapping_canonical.ensure_canonical_columns` (a cada chamada) | `ADD COLUMN exercises.mapping_id` se ausente; `CREATE INDEX idx_exercises_mapping_id` (erro ignorado) |
| `exercise_mapping_catalog._ensure_schema` | no-op (só seta flag) |

"1x por processo" = flag global de módulo (`_schema_done`/`_schema_ready`/`_*_ready`), reiniciada em cada cold start de Lambda.

#### 8.4 Matriz módulo × tabelas

L = lê, E = escreve (INSERT/UPDATE/DELETE), D = DDL.

| Módulo | Tabelas |
|---|---|
| `security.py` | `devices` (L, E `last_seen_at`), `users` (L), `user_sessions` (L, E `last_seen_at`) |
| `auth.py` | `users` (L, E), `user_sessions` (E) |
| `users.py` | `users` (L, E); S3 |
| `devices.py` | `devices` (L, E, D), `users` (L) |
| `main.py` | via `services`; `SELECT 1` |
| `services.py` | `sources` (L, E), `imports` (E, L), `activities` (L, E), `activity_hr_zones` (E), `exercises` (L, E), `exercise_sets` (L, E, D), `exercise_legacy_id_map` (L), `exercise_catalog` (L), `exercise_external_mappings` (L), `exercise_muscle_contributions` (L), `muscle_groups` (L, E em `_find_or_create_muscle_group_id`), `gyms` (L), `gym_equipment` (L), `equipment` (L), `training_types` (L), `workout_plans` (L, E, D), `workout_plan_exercises` (L, E, D), `body_composition` (E), `exercise_mappings` (E), `muscle_group_body_map` (L, E), `information_schema.COLUMNS` (L) |
| `workout_sessions.py` | `activities` (L, E, D), `exercises` (L, E, D), `exercise_sets` (L, E, D), `workout_plans` (L, E `status`), `sources`, `gyms`, `gym_equipment` e catálogo via `services` |
| `analytics.py` | `activities`, `exercises`, `exercise_sets`, `exercise_legacy_id_map`, `exercise_external_mappings`, `exercise_catalog`, `workout_plans`, `workout_plan_template_assignments`, `workout_plan_templates`, `muscle_groups` (L); demais via `intelligence`/`services`/`training_types` |
| `dashboard_modules.py` / `dashboard_intelligence.py` / `engines_extra.py` | via services |
| `intelligence/base.py` | `intervals_wellness` (L), `users` (L) |
| `intelligence/volume_service.py` | `activities`, `exercises`, `exercise_sets`, `exercise_legacy_id_map`, `exercise_catalog`, `exercise_muscle_contributions`, `muscle_groups`, `workout_plan_exercises`, `workout_plans`, `gym_equipment`, `equipment`, `exercise_external_mappings` (L); `training_types`/`training_type_muscle_group_targets` via `training_types._get_training_type` |
| `intelligence/muscle_recovery_service.py` | `muscle_recovery_coefficients` (L), `intervals_wellness` (L), `users` (L) |
| `intelligence/progression_service.py` | `activities`, `exercises`, `exercise_sets`, `exercise_legacy_id_map`, `exercise_catalog`, `exercise_muscle_contributions`, `muscle_groups` (L) |
| `intelligence/body_composition_trend_service.py` | `body_composition` (L) |
| `intelligence/training_load_service.py` | `activities`, `exercises`, `exercise_sets`, `workout_plans`, `daily_biometrics` (L) |
| `intelligence/cardio_context_service.py` | `activities`, `exercises`, `exercise_sets`, `exercise_legacy_id_map`, `exercise_muscle_contributions`, `muscle_groups`, `intervals_wellness`, `manual_effort_metrics`, `sleep_metrics`, `cardio_recommendations` (L; D indireto via `_ensure_schema`) |
| `intelligence/intervals_correlation_service.py` | `activities`, `intervals_activities`, `intervals_wellness` (L) |
| `intelligence/exercise_similarity_service.py` | `exercise_mappings` (L) |
| `intelligence/template_volume_planner.py` | `muscle_groups` (L); `training_type_muscle_group_targets` via `training_types`; self-call `/analytics/muscle-recovery` |
| `intelligence/muscle_contribution_resolver.py`, `candidate_exercise_provider.py`, `template_volume_validator.py` | via `VolumeService` |
| `progression_state.py` | `users.is_advanced_user` (L, `load_user_flags`) |
| `workout_plan_ai.py` | `workout_plans`, `workout_plan_exercises` (L); via `services`, `MuscleRecoveryService`, `CardioAgentContextService`; self-calls `/gyms`, `/gym-equipment/recommend-all`, `/exercise-mapping-catalog` |
| `abc_workout_template.py` | `muscle_groups`, `workout_plans` (L) |
| `abc_workout_ai.py` | `workout_plans`, `workout_plan_exercises` (L) |
| `workout_plan_templates.py` | `workout_plan_templates` (L, E), `workout_plan_template_slots` (L, E), `workout_plan_template_slot_items` (L, E via cascade), `workout_plan_template_assignments` (L, E), `users` (L), `muscle_groups` (L) |
| `workout_plan_templates_engine.py` | `workout_plans` (L), `muscle_groups` (L), `exercise_catalog` (L; função `_load_exercise_catalog_names` definida, sem chamada no arquivo) |
| `workout_plan_templates_engine_v2.py` | `muscle_groups` (L; `_load_muscle_group_names` definida, sem chamada no arquivo) |
| `workout_plan_templates_engine_equipment.py` | `muscle_groups`, `exercise_catalog`, `exercise_external_mappings`, `gym_equipment`, `equipment`, `exercise_legacy_id_map` (L); self-call `/gym-equipment/recommend` |
| `training_types.py` | `training_types` (L, E), `training_type_muscle_group_targets` (L, E), `users`, `muscle_groups`, `workout_plan_template_assignments`, `workout_plan_templates` (L) |
| `cardio_recommendations.py` / `cardio_generation_core.py` | `cardio_recommendations` (L, E, D), `workout_plans` (L), `users` (L) |
| `body_composition.py` | `body_composition` (L, E) |
| `sleep_metrics.py` | `sleep_metrics` (L, E, D), `users` (L) |
| `manual_effort_metrics.py` | `manual_effort_metrics` (L, E, D), `users` (L) |
| `reports.py` / `reports_core.py` | `reports` (L, E, D), `users` (L); S3 |
| `intervals_integration.py` | `intervals_activities` (L, E, D), `intervals_wellness` (L, E, D), `users` (L), `information_schema.STATISTICS` (L) |
| `gyms.py` | `gyms` (L, E), `equipment` (L, E), `gym_equipment` (L, E), `equipment_exercise_mappings` (L, E), `equipment_exercises` (E), `gym_equipment_catalog` (L, E), `exercise_catalog`, `exercise_muscle_contributions`, `muscle_groups`, `exercise_external_mappings`, `exercise_aliases`, `exercises`, `activities` (L) |
| `gym_equipment_sync.py` | `gym_equipment` (E), `gym_equipment_catalog`, `equipment_exercises` (L) |
| `muscle_group_equipment.py` | view `vw_muscle_group_equipment_exercises` (L), `muscle_groups` (L) |
| `muscle_groups_mapped.py` | `activities`, `exercises`, `exercise_sets`, `exercise_legacy_id_map`, `exercise_catalog`, `exercise_muscle_contributions`, `muscle_groups`, `exercise_external_mappings` (L); `exercises.mapping_id` (E via reclassificação); `SHOW COLUMNS` |
| `exercise_mapping_canonical.py` | `exercises` (L, E `mapping_id`, D), `exercise_catalog`, `exercise_muscle_contributions`, `muscle_groups`, `exercise_external_mappings` (L), `exercise_aliases` (L, E), `exercise_legacy_id_map` (L) |
| `exercise_mapping_catalog.py` | `exercise_catalog` (L, E), `exercise_external_mappings` (L, E), `exercise_muscle_contributions` (L, E), `muscle_groups` (L, E), `exercise_aliases` (L, E), `exercise_zepp_mappings` (E em `DELETE /{id}`) |
| `exercise_catalog_admin.py` | `exercise_catalog` (L, E), `exercise_aliases` (L, E), `exercise_external_mappings` (L, E), `exercise_muscle_contributions` (L), `equipment_exercises` (L), `equipment` (L), `equipment_types` (L, E), `exercise_legacy_id_map` (L) |
| `async_jobs.py` / `jobs.py` | DynamoDB `ASYNC_JOBS_TABLE` |
| `rag_storage.py` | S3 (RAG) + disco local |

#### 8.5 Consolidação do catálogo canônico (`exercise_legacy_id_map`)

Tabela com colunas `old_exercise_id → canonical_exercise_id`. Comentário em `services._resolve_canonical_exercise_id` descreve 93 linhas cobrindo todo o espaço de ids antigo, com 14 ids absorvidos e mapeamento identidade para os demais (contagem não verificada no banco).

Formas de aplicação:

1. **Python** `services._resolve_canonical_exercise_id(id, cur)` — chamado por: `resolve_mapping_id` (escrita de `exercises.mapping_id` e `workout_plan_exercises.mapping_id` em `upsert_activity`, `update_exercise_log`, `_replace_plan_exercises`, `workout_sessions.add_session_exercise`), `resolve_equipment_id`, `is_gym_equipment_pair_active`, `get_last_session_sets`, `suggested_progression_for_mapping`, `suggested_set_count_for_substitution` (ambos os ids).
2. **SQL** `LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id` + `COALESCE(elm.canonical_exercise_id, e.mapping_id)` — em `services.activity_detail`, `list_exercise_logs`, `high_rpe_sets`, `exercise_history`, `get_last_session_sets`, `get_workout_plan` (sobre `wpe.mapping_id`); `resolve_equipment_id` e `is_gym_equipment_pair_active` (sobre `ge.mapping_id`); `VolumeService` (todas as queries sobre `exercises`: `_fetch_window_sets`, `_fetch_full_history_sets`, `get_equivalent_sets_by_activity`, `get_muscle_activation_by_activity`, `get_recent_exercise_volume`, `get_daily_muscle_activation`, `get_lowest_volume_groups`, `get_volume_trend`, `get_rolling_weekly_trend_by_muscle_group`); `ProgressionService`; `CardioAgentContextService._get_recent_workouts`; `analytics.py` (`exercise-progress`, `_fetch_exercise_set_rows`); `muscle_groups_mapped.load_rows`; `exercise_mapping_canonical.unmapped`; `workout_plan_templates_engine_equipment._find_active_gym_equipment` (ambos os lados).
3. **Leitura reversa**: `exercise_catalog_admin.get_exercise_detail` lista `old_exercise_id` com `canonical_exercise_id = id AND old_exercise_id != id` (`legacy_ids_merged`).
4. **Filtro por ativos**: `exercise_mapping_canonical.load_mapping_rows` só considera `exercise_catalog.is_active = 1`, então `reclassify_exercises` grava apenas ids ativos.

Queries sem o mapa: `gyms.py` (`/gym-equipment`, `/gym-equipment/recommend`, `/recommend-all` fazem `JOIN exercise_catalog zm ON zm.id = ge.mapping_id` diretamente); `analytics.analytics_summary`, `training-trend`, `rpe-distribution`, `cardio` (não usam mapping); `VolumeService._fetch_plan_rpe_targets` (`workout_plan_exercises.mapping_id` cru comparado com identidade canônica); `workout_plan_templates` (itens `mapping_id` referenciam `exercise_catalog` diretamente); `exercise_catalog_admin` e `exercise_mapping_catalog` operam por `exercise_catalog.id`.

Grupo muscular primário/secundário é resolvido em todo o backend por subquery em `exercise_muscle_contributions` com `role` e `is_active = 1`, `ORDER BY id ASC LIMIT 1` (exceto `suggested_set_count_for_substitution`, que ordena por `stimulus_weight DESC`).


### 9. Variáveis de ambiente

Levantadas por grep de `os.environ`, `os.getenv` e `_env_first` em `api/app`, mais nomes passados indiretamente (`crypto_utils._ENV_VAR`, `secrets_bootstrap._SECRETS`, argumentos de `invoke_async`). "Import" = lida como constante no carregamento do módulo; "chamada" = lida a cada uso.

| Variável | Módulo(s) | Default | Momento | Uso |
|---|---|---|---|---|
| `AURORA_CLUSTER_ARN` | `db.py` | obrigatória (`DataApiError`) | chamada | ARN do cluster para Data API |
| `AURORA_SECRET_ARN` | `db.py` | obrigatória | chamada | secret de credenciais do Aurora |
| `AURORA_DATABASE` | `db.py` | obrigatória | chamada | nome do schema |
| `AWS_REGION` | `db.py`, `async_jobs.py`, `secrets_bootstrap.py`, `gyms.py`, `reports_core.py` | → `AWS_DEFAULT_REGION` → `"sa-east-1"` | chamada | região boto3 |
| `AWS_DEFAULT_REGION` | mesmos | `"sa-east-1"` | chamada | fallback de região |
| `API_TOKEN` | `security.py` (3 funções, via `master_token_matches`) | `""` | chamada | token mestre |
| `API_TOKEN_SECRET_ARN` | `secrets_bootstrap.py` | — | startup | ARN do segredo de `API_TOKEN` |
| `OPENAI_API_KEY` | `openai_client.py` (chamada); `insights_ai`, `exercise_set_evaluation_ai`, `exercise_summary_ai`, `workout_summary_ai`, `workout_plan_ai`, `cardio_agent_ai` (import) | `""` | ver coluna | chave OpenAI; `""` ou `"replace_me"` → erro de "não configurada" |
| `OPENAI_API_KEY_SECRET_ARN` | `secrets_bootstrap.py` | — | startup | ARN do segredo |
| `SECRET_ENCRYPTION_KEY` | `crypto_utils.py` | ausente → `RuntimeError` | chamada | chave Fernet |
| `SECRET_ENCRYPTION_KEY_SECRET_ARN` | `secrets_bootstrap.py` | — | startup | ARN do segredo |
| `OPENAI_WORKOUT_PLAN_MODEL` | `workout_plan_ai.py` | `"gpt-5.4-mini"` | import | modelo Full Body/ABC/templates |
| `OPENAI_MODEL_CARDIO` (legado: `OPENAI_CARDIO_AGENT_MODEL`) | `cardio_agent_ai.py` | `"gpt-5.4-mini"` | import | modelo cardio |
| `OPENAI_REPORTS_MODEL` | `report_ai.py` | `"gpt-5.4-mini"` | import | modelo relatório/análise de treinos |
| `OPENAI_INSIGHTS_MODEL` | `insights_ai.py` | `"gpt-5.4-mini"` | import | modelo insights |
| `OPENAI_SET_EVAL_MODEL` | `exercise_set_evaluation_ai.py` | `"gpt-5.4-mini"` | import | modelo avaliação de série |
| `OPENAI_EXERCISE_SUMMARY_MODEL` | `exercise_summary_ai.py` | `"gpt-5.4-mini"` | import | modelo resumo de exercício |
| `OPENAI_WORKOUT_SUMMARY_MODEL` | `workout_summary_ai.py` | `"gpt-5.4-mini"` | import | modelo resumo de treino |
| `ASYNC_JOBS_TABLE` | `async_jobs.py` | obrigatória (`KeyError`) | chamada | tabela DynamoDB |
| `PLAN_GENERATOR_FUNCTION_NAME` | `async_jobs.invoke_async` (de `workout_plans.py`) | obrigatória (`KeyError`) | chamada | Lambda de plano |
| `CARDIO_AGENT_FUNCTION_NAME` | idem (de `cardio_recommendations.py`) | obrigatória | chamada | Lambda de cardio |
| `WORKOUT_AI_FUNCTION_NAME` | idem (de `main.py`, `workouts_analysis.py`) | obrigatória | chamada | Lambda de IA do modo treino |
| `REPORTS_FUNCTION_NAME` | idem (de `reports.py`) | obrigatória | chamada | Lambda de relatório |
| `USE_TEMPLATE_VOLUME_ENGINE_V2` | `plan_generation_core.py` | `"true"` (compara `strip().lower() == "true"`) | import | habilita motor v2 |
| `RAG_MARKDOWN_DIR` | `rag_storage.py` (chamada); `rag_chunking.py`, `workout_plan_ai.py`, `exercise_set_evaluation_ai.py` (import) | `"/app/rag"` | ver coluna | diretório local do RAG |
| `RAG_S3_BUCKET` | `rag_storage.py` | ausente → sem sincronização | chamada | bucket do RAG |
| `RAG_S3_PREFIX` | `rag_storage.py` | `"markdown/"` | chamada | prefixo S3 |
| `EQUIPMENT_PHOTOS_BUCKET` | `gyms.py`, `reports_core.py` | ausente → 503 (gyms) / `RuntimeError` (reports) | chamada | bucket de fotos e PDFs |
| `CLOUDFRONT_DISTRIBUTION_ID` | `gyms.py` | ausente → warning e sem invalidação | chamada | invalidação CloudFront |
| `CORS_ALLOWED_ORIGINS` | `main.py`, `main_engines.py` | `"https://app.musclefit.com.br"` | import | origens CORS (CSV) |
| `SESSION_COOKIE_DOMAIN` | `security.py` | `".musclefit.com.br"` (`""` → sem Domain) | chamada | domínio do cookie |
| `SESSION_COOKIE_SECURE` | `security.py` | ausente → detecta https | chamada | força flag Secure (`1/true/yes`) |
| `INTERVALS_ICU_BASE_URL` | `intervals_client.py` | `"https://intervals.icu/api/v1"` | chamada | base da API Intervals |
| `INTERVALS_ICU_USER_AGENT` | `intervals_client.py` | `"Mozilla/5.0 DougFitnessDashboard/1.0"` | chamada | User-Agent |

Valores literais que não vêm de env: `PUBLIC_ASSETS_DOMAIN = "app.musclefit.com.br"` (`gyms.py`) e o mesmo domínio em SQL de `services.py`, `gyms.py` e `exercise_catalog_admin.py`; `ADMIN_SEED_USERNAME = "doug"` (`auth.py`); `"change_api_token"` tratado como token não configurado em `require_master_token`, `gyms._require_token` e `exercise_mapping_catalog._require_token`.


### 10. Tratamento de erros e logging

#### 10.1 Exceções HTTP

- Não há `exception_handler` customizado nem middleware de erro registrado (grep por `exception_handler`/`add_exception_handler` sem resultados). Erros não capturados seguem o comportamento padrão do FastAPI/Starlette (500).
- Todas as respostas de erro deliberadas usam `raise HTTPException(status_code=..., detail=...)`. Contagem de `raise HTTPException` por arquivo (total 257 em 27 arquivos): `gyms.py` 39, `workout_plan_templates.py` 30, `exercise_mapping_catalog.py` 23, `exercise_catalog_admin.py` 22, `users.py` 16, `analytics.py` 10, `auth.py` 10, `reports.py` 10, `manual_effort_metrics.py` 9, `sleep_metrics.py` 9, `workout_ai_generation_core.py` 9, `plan_generation_core.py` 8, `security.py` 8, `training_types.py` 8, `workout_sessions.py` 8, `body_composition.py` 7, `cardio_recommendations.py` 7, `main.py` 5, `intervals_integration.py` 4, `workout_plans.py` 4, `cardio_generation_core.py` 3, `engines_extra.py` 2, `muscle_group_equipment.py` 2, `devices.py` 1, `insights_core.py` 1, `jobs.py` 1, `workouts_analysis_core.py` 1.
- `detail` é, na maioria, string em português; em `_connect()` de vários módulos é dict `{"error": "database_unavailable", "message"?, "exception": str(exc)}`; em `gyms._s3_bucket` é `{"error": "photo_storage_unavailable", "message"}`.

Convenções de status observadas:

| Status | Uso |
|---|---|
| 400 | campos obrigatórios ausentes/formato inválido em corpos `dict` (username, name, alias, local_date, start_date, hora, e-mail); "Informe exercise_name ou mapping_id."; job_type desconhecido; sem credenciais Intervals |
| 401 | "Token inválido."; "Usuário ou senha inválidos."; "Senha atual incorreta."; status 401 repassado do Intervals.icu |
| 403 | papel insuficiente; `source != session`; foto de outro usuário; `gym_equipment_catalog` sem posse |
| 404 | recurso inexistente ou pertencente a outro usuário (padrão de não revelar existência) |
| 409 | duplicidade detectada por `is_duplicate_key_error` (username, nome de exercício, alias, tipo de equipamento, atribuição de template); treino já finalizado; tipo de equipamento em uso |
| 422 | validação de template/training type; slot inexistente; `split_day` inválido (via `ValueError`); `muscle-contributions` role/peso; mapping sem grupo |
| 423 | conta bloqueada por tentativas de login |
| 500 | `API_TOKEN` não configurado; falha de descriptografia Intervals; exceções genéricas convertidas em CRUDs (`detail=str(exc)`) de `body_composition`, `sleep_metrics`, `manual_effort_metrics`, `cardio_recommendations`, `reports` |
| 502 | `WorkoutPlanGenerationError` na geração de plano; erro do Intervals.icu sem status ≥ 400 |
| 503 | banco indisponível (`_connect`); bucket de fotos não configurado; falhas de IA (`SetEvaluationError`, `ExerciseSummaryError`, `WorkoutSummaryError`, `InsightsGenerationError`, `ReportGenerationError`, `CardioAgentGenerationError`) dentro dos dispatches |

Respostas de erro com HTTP 200: `exercise_mapping_canonical.canonical_associate` retorna `{"ok": False, "error": ...}`; `dashboard_modules.upsert_muscle_group_body_map` retorna `{"error": "muscle_group e slugs (lista) são obrigatórios"}`.

#### 10.2 Hierarquia de exceções próprias

| Exceção | Base | Módulo |
|---|---|---|
| `DataApiError` | `Exception` | `db.py` |
| `OpenAIGenerationError` | `RuntimeError` | `openai_client.py` |
| `WorkoutPlanGenerationError` | `OpenAIGenerationError` | `workout_plan_ai.py` |
| `CardioAgentGenerationError` | `OpenAIGenerationError` | `cardio_agent_ai.py` |
| `ReportGenerationError` | `OpenAIGenerationError` | `report_ai.py` |
| `InsightsGenerationError` | `RuntimeError` | `insights_ai.py` |
| `SetEvaluationError` | `RuntimeError` | `exercise_set_evaluation_ai.py` |
| `ExerciseSummaryError` | `RuntimeError` | `exercise_summary_ai.py` |
| `WorkoutSummaryError` | `RuntimeError` | `workout_summary_ai.py` |
| `IntervalsClientError` | `Exception` (atributo `status_code`) | `intervals_client.py` |
| `TemplateVolumeEngineNotApplicable` | `Exception` | `intelligence/template_volume_planner.py` (levantada em `build_template_cycle_context`; o dispatch só chama o v2 quando há `training_type_id`, e nenhum `except` específico para ela foi encontrado) |

#### 10.3 Fluxos assíncronos

- Handlers Lambda capturam `Exception` genérica e gravam `mark_failed(job_id, detail ou str(exc))`; o erro não é relançado (a invocação `Event` termina com sucesso do ponto de vista do Lambda).
- `HTTPException` levantada dentro de dispatch é serializada via `.detail`.
- `run_report_generation` marca `reports.status='failed'` com mensagem truncada em 2000 caracteres e relança; o handler então marca o job.

#### 10.4 Degradação silenciosa (captura e continuação)

| Local | Comportamento |
|---|---|
| `security.device_token_matches`, `_session_identity`, `_admin_user_id` | exceção → `False`/`None` |
| `auth.ensure_admin_seed` | `logger.exception` e retorno |
| `users.migrate_plaintext_intervals_keys` | `logger.exception`, retorna contagem |
| `secrets_bootstrap.load_secrets` | segredo ilegível ignorado sem log |
| `main.py` inclusão de routers | `logger.exception` e router omitido |
| `services.get_workout_plan` / `_enrich_plan_exercises_with_progression` | `except Exception: pass` / `logger.exception` |
| `MuscleRecoveryService._get_global_tsb` | `logger.exception`, TSB neutro |
| `workout_plan_ai.get_latest_intervals_wellness` | exceção → `None` |
| `abc_workout_template.load_muscle_group_names` | exceção → `{}` |
| `abc_workout_ai.generate_abc_plan_draft_ai` | `logger.warning` e fallback determinístico |
| `workout_plan_templates_engine_v2` | `WorkoutPlanGenerationError` da IA → seleção gulosa |
| `workout_plan_templates._attach_volume_coverage_warnings` e preview `equipment_series` | exceção → `[]` |
| `rag_chunking` / `rag_storage` / `ReportDataService.build_rag_context` | `logger.warning` e fallback (arquivo inteiro, vazio ou `""`) |
| `gyms._invalidate_cloudfront_paths` | `logger.warning(..., exc_info=True)` |
| `reports_core.delete_report_pdf` | `logger.exception` |
| `reports.get_report_pdf` | falha S3 → `logger.warning` + 404 |
| `exercise_mapping_canonical.resolve_mapping_id` (via `match_mapping`) | exceção → `None` |
| `intervals_integration._ensure_schema` | `DROP INDEX` com `except Exception: pass` |

#### 10.5 Logging

- Não há `logging.basicConfig`, `setLevel` nem configuração de handlers em `api/app` (grep sem resultados); o nível e o destino dependem do runtime (não verificado).
- Loggers nomeados:

| Nome | Módulo(s) |
|---|---|
| `fitness.auth` | `auth.py`, `main.py` |
| `fitness.users` | `users.py`, `main.py` |
| `fitness.reports` | `reports.py`, `reports_core.py`, `main.py` |
| `fitness.intelligence` | `intelligence/base.py`, `intelligence/muscle_recovery_service.py` |
| `fitness.intervals`, `fitness.exercise_mapping_catalog`, `fitness.exercise_catalog_admin`, `fitness.body_composition`, `fitness.sleep_metrics`, `fitness.manual_effort_metrics`, `fitness.cardio_recommendations`, `fitness.gyms`, `fitness.muscle_group_equipment`, `fitness.workout_plans`, `fitness.jobs`, `fitness.workout_plan_templates`, `fitness.training_types`, `fitness.devices`, `fitness.workouts_analysis`, `fitness.workout_sessions` | `main.py` (falha de include) |
| `fitness.workout_plan_templates_engine`, `fitness.workout_plan_templates_engine_v2` | warnings de `rag_folder` inválido |
| `musclefit.lambda_workout_ai`, `musclefit.lambda_plan_generator`, `musclefit.lambda_cardio_agent`, `musclefit.lambda_reports` | handlers Lambda |
| `__name__` | `services`, `gyms`, `workout_plan_ai`, `abc_workout_ai`, `rag_chunking`, `rag_storage`, `intelligence.report_data_service` |

- Mensagens relevantes: "Evento sem job_id, nada a gravar: %r"; "Falha ao processar job de IA do modo treino (job_id=%s, job_type=%s)"; "Falha ao gerar plano de treino (job_id=%s)"; "Falha ao gerar recomendacao de cardio (job_id=%s)"; "Falha ao gerar relatório (job_id=%s, report_id=%s)"; "=== SENHA TEMPORARIA DO ADMIN 'doug': {senha} — TROQUE NO PRIMEIRO LOGIN ===" (warning + `print`); "reconcile_with_gym_catalog: exercicio '%s' resolvido por NOME de grupo ..."; "abc_workout_ai: geracao via IA falhou (%s) — caindo para ABC_TEMPLATE deterministico."; "rag_chunking: fallback para arquivo inteiro de %s (%s) ..."; "rag_storage: falha ao sincronizar RAG de s3://%s/%s para %s: %s"; "CLOUDFRONT_DISTRIBUTION_ID não configurado; pulando invalidação para %s"; "musclefit-engines retornou FunctionError: %s".
- Não há logging estruturado, correlação de request id nem métricas no código.


### 11. Templates e geração de PDF

#### 11.1 Arquivos

`api/app/templates/` contém um único arquivo: `report.html` (Jinja2, ~5,9 KB). Não há outros templates no backend. É usado exclusivamente por `report_pdf_builder.ReportPdfBuilder`, que roda na Lambda `musclefit-reports`.

#### 11.2 `ReportPdfBuilder.build(report_row, payload, analyses) -> bytes`

Dependências: `matplotlib` (backend `"Agg"` forçado no import), `jinja2.Environment(FileSystemLoader(templates), autoescape=select_autoescape(["html"]))`, `weasyprint.HTML(string=html, base_url=templates).write_pdf()`. Não acessa banco nem IA.

Entradas:
- `report_row`: `{id, user_id, period_days, user_name}` (montado em `reports_core`).
- `payload`: `ReportDataService.build_report_payload` (`days`, `generated_at`, `training`, `body_composition`, `cardio`).
- `analyses`: `{training_evolution, body_composition, cardio, volume_distribution, overall_evolution}` (markdown da IA).

Contexto passado ao template:

| Variável | Origem |
|---|---|
| `user_name` | `report_row.user_name or "Usuário"` |
| `period_start`, `period_end` | `_period_range(days, generated_at)`: `end = date(generated_at)` (inválido → agora), `start = end − days` |
| `generated_at` | `payload.generated_at` |
| `generated_at_display` | `date.today().isoformat()` |
| `weekly_volume_chart` | `_build_weekly_volume_chart(training)` |
| `progression_highlights` | `training.progression_highlights` |
| `body_composition_charts` | `_build_body_composition_charts(body_composition)` |
| `body_composition_current` | último item de `body_composition.items` (não usado no template) |
| `cardio_chart` | `_build_cardio_chart(cardio)` |
| `volume_overview` | `training.volume_overview` |
| `volume_bar_chart`, `volume_trend_chart` | `_build_volume_distribution_charts(training)` |
| `analysis_training`, `analysis_body_composition`, `analysis_cardio`, `analysis_volume`, `analysis_overall` | `_markdown_to_html(analyses[...])` |

`days = int(payload.days or report_row.period_days or 90)`.

#### 11.3 Gráficos (PNG base64, `dpi=150`, `bbox_inches="tight"`)

Paleta `PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]`; texto de eixos `#333333`; `_style_axes`: ticks tamanho 8, spines superior/direita ocultas, esquerda/inferior `#cccccc`, grid horizontal `#e6e6e6` 0.6.

| Função | Gráfico |
|---|---|
| `_line_chart(labels, series, ylabel)` | `figsize (7.2, 3.3)`; linha por série (`marker "o"`, `markersize 3`, `linewidth 1.8`, cor da paleta); rótulos X rotacionados 35°; legenda só com > 1 série; retorna `None` se não há labels ou nenhum valor não nulo |
| `_horizontal_bar_chart(labels, values, xlabel)` | `figsize (7.2, max(2.2, 0.45 × n))`; barras com cores ciclando a paleta; eixo Y invertido; grid X; `None` sem labels |
| `_build_weekly_volume_chart(training)` | linha única "Volume (kg)" sobre `training.weekly_trend` (`period` × `volume_kg`), na ordem recebida |
| `_build_body_composition_charts(body)` | até 3 gráficos de linha (`weight_kg` "Peso (kg)", `body_fat_percent` "% Gordura", `muscle_mass_kg` "Massa Magra (kg)") sobre `body.items` por `local_date`; omite os sem dados; lista `{title, image_base64}` |
| `_build_cardio_chart(cardio)` | `intervals_wellness` invertido (cronológico); 3 séries "CTL (Aptidão)", "ATL (Fadiga)", "TSB (Forma)"; ylabel "Carga (Intervals.icu)" |
| `_build_volume_distribution_charts(training)` | `bar_chart`: barras horizontais de `stimulus_equivalent_sets` por `muscle_group` ("Séries equivalentes de estímulo"); `trend_chart`: a partir de `weekly_trend_by_muscle_group`, períodos na ordem de primeira aparição, `volume_kg` por grupo; ranking por variação (`max − min`); 4 grupos com maior variação (`_TOP_VARIATION_GROUPS = 4`) + série "Outros" somando os demais; ylabel "Volume (kg)" |

#### 11.4 Conversão markdown → HTML (`_markdown_to_html`)

Sem biblioteca de markdown.

1. Texto vazio → `""`.
2. Divide em blocos por linha em branco (`\n\s*\n`).
3. Bloco em que todas as linhas começam com `"- "` → `<ul><li>...</li></ul>`.
4. Senão, percorre linhas: `"## "` ou `"# "` → `<h3>`; demais acumuladas num parágrafo (unidas por espaço). Ao fechar o parágrafo, `_split_inline_list_items` verifica o padrão `(?:^|\s)-\s+(?=[A-ZÀ-Ú0-9])`; com ≥ 3 ocorrências e todos os itens com ≥ 8 caracteres, gera `<p>preâmbulo</p><ul>...</ul>`; senão `<p>...</p>`.
5. `_inline_markdown`: `html.escape` e depois `**x**` → `<strong>x</strong>`.

Não trata `###`, listas numeradas, itálico, links ou tabelas. O HTML resultante é inserido no template com o filtro `| safe`.

#### 11.5 Estrutura de `templates/report.html`

- CSS inline: `@page { size: A4; margin: 2cm 1.6cm; @bottom-center { content: "Doug Fitness — Relatório de Evolução"; font-size 8px; color #999999 } }`; fonte `"DejaVu Sans", Arial, sans-serif` 11.5px, `line-height 1.65`; `h1` 22px e `h2` 16px em `#2a78d6` (`h2` com borda inferior 2px e `page-break-after: avoid`); `.section { page-break-inside: avoid }`; tabelas 9.5px com cabeçalho `#f0f4fa` e zebra `#fafafa`; `.analysis-block` com fundo `#f7f9fc` e borda esquerda 3px `#2a78d6`; `.empty-note` itálico `#888888`.
- Cabeçalho: `<h1>Relatório de Evolução</h1>` + "Aluno: {{ user_name }}", "Período analisado: {{ period_start }} a {{ period_end }}", "Gerado em: {{ generated_at_display }}".
- Seções:

| # | Título | Conteúdo |
|---|---|---|
| 1 | Evolução dos Treinos | `weekly_volume_chart` ou "Sem dados suficientes de volume semanal no período."; `<h3>Destaques de Progressão</h3>` com tabela Exercício / Maior carga (kg) / Volume médio (kg) / RPE médio / Sessões (`exercise_name, best_weight_kg, avg_volume_kg, avg_rpe, sessions`; nulos como "-") ou "Sem destaques de progressão no período."; `analysis_training` |
| 2 | Recomposição Corporal | um `<img>` por item de `body_composition_charts` ou "Sem dados de bioimpedância suficientes no período." (`{% for %}...{% else %}`); `analysis_body_composition` |
| 3 | Cardio e Recuperação | `cardio_chart` ou "Sem dados sincronizados do Intervals.icu suficientes no período."; `analysis_cardio` |
| 4 | Volumes por Grupamento | `volume_bar_chart` ou "Sem dados de volume por grupamento no período."; `volume_trend_chart` (se houver); tabela Grupamento / Séries diretas / Séries indiretas / Séries equivalentes / RPE direto / Sessões com trabalho direto (`muscle_group, direct_sets, indirect_sets, stimulus_equivalent_sets, direct_rpe_avg, direct_sessions`) quando há `volume_overview`; `analysis_volume` |
| 5 | Análise Geral da Evolução | `analysis_overall` |

Imagens são embutidas como `data:image/png;base64,...`.

#### 11.6 Armazenamento e entrega do PDF

- `reports_core.put_report_pdf(report_id, bytes)`: `s3.put_object(Bucket=EQUIPMENT_PHOTOS_BUCKET, Key="reports/{id}.pdf", Body, ContentType="application/pdf")`; a key é gravada em `reports.pdf_path`.
- `GET /reports/{id}/pdf`: exige `status == "done"` e `pdf_path`; `get_report_pdf_bytes(key)` → `Response(media_type="application/pdf", Content-Disposition: attachment; filename="relatorio-{id}.pdf")`.
- `DELETE /reports/{id}`: remove a linha e chama `delete_report_pdf` (best-effort).
- `reports.context_snapshot` guarda `{**payload, "analyses": analyses}` (sem o texto RAG).

#### 11.7 Outro artefato gerado: PNG de mapa muscular

`muscle_map.render_muscle_map_png` (Pillow) gera o PNG servido por `GET /workout-plans/{plan_id}/muscle-map.png` — ver 5.18. Não usa templates.


---

# Parte III — Frontend (`frontend/`)

## 03 — Frontend MuscleFit (versão AWS)

Documento descritivo do frontend em `C:\muscle_fit\frontend` (código em `frontend/src`, 34.048 linhas TS/TSX contadas por `wc -l` em `src/**/*.ts{,x}`; 27.738 delas em `src/app`, 6.240 em `src/lib` + `src/components` + `src/theme.ts`). Descreve o código como ele está em 2026-09-12. Identificadores técnicos mantidos no original. Onde algo não pôde ser confirmado no repositório, está marcado como "não verificado".

Convenções deste documento:

- Caminhos relativos a `frontend/` salvo indicação contrária.
- "API" = backend Fitness API consumido via `API_BASE_URL` (ver seção 3.2).
- Tabelas de endpoints usam `método path?query` exatamente como montado no código.

---

### 1. Stack e configuração

#### 1.1 `package.json`

```json
{
  "name": "musclefit-frontend",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev -H 0.0.0.0 -p 3000",
    "build": "next build",
    "typecheck": "tsc --noEmit",
    "lint": "next lint"
  }
}
```

Dependências (range declarado no `package.json` → versão efetivamente instalada em `node_modules` lida do `package.json` de cada pacote):

| Pacote | Range declarado | Instalada | Uso no código |
|---|---|---|---|
| `next` | `14.2.35` (fixo) | 14.2.35 | App Router, `output: 'export'` |
| `react` | `^18.3.1` | 18.3.1 | — |
| `react-dom` | `^18.3.1` | 18.3.1 | — |
| `@mantine/core` | `^7.17.0` | 7.17.8 | Biblioteca de UI (todos os componentes visuais) |
| `@mantine/hooks` | `^7.17.0` | 7.17.8 | `useDisclosure`, `useMediaQuery`, `useDebouncedValue` |
| `@mantine/notifications` | `^7.17.0` | 7.17.8 | `notifications.show` nos builders de modelo/tipo de treinamento; `<Notifications position="top-right" />` em `providers.tsx` |
| `@tabler/icons-react` | `^3.31.0` | 3.46.0 | Ícones |
| `@tanstack/react-query` | `^5.66.0` | 5.102.8 | `useQuery`, `useMutation`, `useQueries`, `useQueryClient` |
| `recharts` | `^3.10.1` | 3.10.1 | Gráficos (`LineChart`, `BarChart`, `Treemap`, `Cell`, `Legend`, `Tooltip`, `ResponsiveContainer`) |
| `react-markdown` | `^10.1.0` | 10.1.0 | Renderização do markdown da IA (`/agente-cardio`, `/insights`, `/treinos`, `/planos-treino/[id]`) |
| `@hello-pangea/dnd` | `^18.0.1` | 18.0.1 | Drag-and-drop de linhas em `planos-treino/ExercisesTable.tsx` |

DevDependencies:

| Pacote | Range | Instalada |
|---|---|---|
| `typescript` | `^5.7.3` | 5.9.3 |
| `eslint` | `^8.57.1` | 8.57.1 |
| `eslint-config-next` | `14.2.35` | 14.2.35 |
| `postcss` | `^8.5.1` | 8.5.28 |
| `postcss-preset-mantine` | `^1.17.0` | 1.18.0 |
| `postcss-simple-vars` | `^7.0.1` | 7.0.1 |
| `@types/node` | `^20.17.0` | não verificado |
| `@types/react` | `^18.3.18` | não verificado |
| `@types/react-dom` | `^18.3.5` | não verificado |

Não há Tailwind, Zustand, Redux, axios, zod, react-hook-form nem Storybook no `package.json`.

#### 1.2 `next.config.mjs`

```js
const nextConfig = {
  output: 'export',
  reactStrictMode: true,
  poweredByHeader: false,
  images: { unoptimized: true },
  trailingSlash: true,
};
export default nextConfig;
```

- `output: 'export'`: build gera HTML/JS estático em `out/`; não existe servidor Node, nem `rewrites`, nem route handlers em `src/app/api` (o diretório `src/app/api` não existe). O comentário no arquivo diz que o navegador chama a API (`api.musclefit.com.br`) diretamente.
- `images: { unoptimized: true }`: `next/image` sem otimizador. O código não usa `next/image`; usa `<img>` nativo (com `// eslint-disable-next-line @next/next/no-img-element`) e `Image` do Mantine para o logo.
- `trailingSlash: true`: cada rota vira `<rota>/index.html`.
- Não há `basePath`, `assetPrefix`, `headers`, `redirects`, `env` nem `experimental`.

#### 1.3 Publicação do build (o que o frontend assume)

- `npm run build` produz `out/` (ignorado no `.gitignore`). Estrutura observada em `out/`: `404.html`, `index.html` (login), `index.txt`, `_next/`, `logo.jpg` e um diretório por rota (`academias/`, `agente-cardio/`, `cardio/`, `dashboard/{ciclo,engine,musculos,progressao,recovery,treinos}/`, `dominio-exercicios/`, `equipamentos-exercicios/`, `evolucao-corporal/`, `exercicios/`, `grupos-musculares/`, `indicadores-fisicos/`, `insights/`, `intervals-icu/`, `modelos-treino/`, `modo-treino/`, `perfil/`, `planos-treino/`, `recuperacao-muscular/`, `registro-exercicios/`, `relatorios/`, `tipos-treinamento/`, `treinos/`, `usuarios/`, `volume-grupamentos/`).
- Rotas dinâmicas geram exatamente um diretório físico `placeholder/` (ex.: `out/registro-exercicios/placeholder/`, `out/planos-treino/placeholder/`, `out/modelos-treino/placeholder/`, `out/tipos-treinamento/placeholder/`), conforme `generateStaticParams()` (seção 2.3).
- O comentário em `src/lib/use-dynamic-route-param.ts` afirma que uma CloudFront Function `spa-index-rewrite` (arquivo `infra/cloudfront-functions/spa-index-rewrite.js` existe no repositório) serve `.../placeholder/index.html` para qualquer id na URL. O conteúdo dessa função não foi lido neste documento (fora do escopo do frontend).
- Comentários em `EquipmentPhoto.tsx`/`GymLogo.tsx` afirmam que fotos de equipamento e logos de academia são servidas como arquivos estáticos via CloudFront + S3 (bucket `musclefit-uploads`), recebidas prontas nos campos `photo_url`/`logo_url` da API.

#### 1.4 `tsconfig.json`

```json
{
  "compilerOptions": {
    "target": "ES2017",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules", "out"]
}
```

Alias único: `@/*` → `./src/*`. Existe `tsconfig.tsbuildinfo` na raiz (ignorado via `*.tsbuildinfo`).

#### 1.5 `postcss.config.cjs`

```js
module.exports = {
  plugins: {
    'postcss-preset-mantine': {},
    'postcss-simple-vars': {
      variables: {
        'mantine-breakpoint-xs': '36em',
        'mantine-breakpoint-sm': '48em',
        'mantine-breakpoint-md': '62em',
        'mantine-breakpoint-lg': '75em',
        'mantine-breakpoint-xl': '88em',
      },
    },
  },
};
```

Nenhum arquivo `.module.css` existe em `src/`; as variáveis de breakpoint não são referenciadas em CSS do projeto. A responsividade é feita por props do Mantine (`cols={{ base, sm, md, lg }}`, `hiddenFrom`, `visibleFrom`) e por `useMediaQuery('(max-width: 48em)')`.

#### 1.6 `src/theme.ts`

Biblioteca de UI: **Mantine 7** (`createTheme`). Tokens exportados:

```ts
export const BRAND = {
  bg: '#0A0A0A',
  bgElevated: '#141414',
  bgSurface: '#1C1C1C',
  border: '#2A2A2A',
  orange: '#FF9500',
  red: '#E8451C',
  textPrimary: '#F5F5F5',
  textMuted: '#E0E0E0',
  gradient: { from: '#FF9500', to: '#E8451C', deg: 135 },
} as const;
```

Escalas de cor registradas no tema:

- `brand` (10 tons): `#fff2e0`, `#ffe0b8`, `#ffc989`, `#ffb156`, `#ff9d2e`, `#ff9500`, `#f58600`, `#da7300`, `#c26300`, `#a85200`.
- `brandRed` (10 tons): `#fdeae5`, `#f9c9ba`, `#f4a68c`, `#ef825d`, `#eb6538`, `#e8541f`, `#e8451c`, `#c9370f`, `#b32e0c`, `#9c2405`.

Demais campos de `createTheme`:

| Campo | Valor |
|---|---|
| `primaryColor` | `'brand'` |
| `fontFamily` | `"'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"` |
| `headings.fontFamily` | idem |
| `headings.fontWeight` | `'700'` |
| `defaultRadius` | `'md'` |
| `black` | `BRAND.bg` (`#0A0A0A`) |
| `white` | `BRAND.textPrimary` (`#F5F5F5`) |
| `components.Button.defaultProps.radius` | `'md'` |

Não há `@font-face` nem `<link>` para a fonte Inter no projeto; a fonte é usada apenas se estiver instalada no sistema, caindo no fallback da pilha.

#### 1.7 `src/app/globals.css`

```css
html, body { padding: 0; margin: 0; background: #0a0a0a; color: #f5f5f5; }
* { box-sizing: border-box; }
.brand-gradient-text {
  background: linear-gradient(135deg, #ff9500 0%, #e8451c 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
```

`.brand-gradient-text` é usada no título "Entrar" do login, no texto "MuscleFit" do header do app-shell e no título "Modo de treino" do header full-screen.

#### 1.8 `public/`

Único arquivo: `public/logo.jpg` (110.891 bytes). Usado como favicon (`<link rel="icon" href="/logo.jpg" />`), no header do app-shell (`<Image src="/logo.jpg" w={32} h={32} />`) e na tela de login (`w={180}`). Não há `manifest.json`, `robots.txt`, service worker nem ícones PWA.

#### 1.9 `next-env.d.ts`

Conteúdo padrão do Next (`/// <reference types="next" />` e `/// <reference types="next/image-types/global" />`). Está listado no `.gitignore` do frontend.

#### 1.10 `.gitignore` do frontend

```
node_modules
.next
out
.env*.local
npm-debug.log*
.DS_Store
*.tsbuildinfo
next-env.d.ts
```

Não existe nenhum arquivo `.env*` em `frontend/` no momento da leitura.

---

### 2. Roteamento

#### 2.1 Árvore de `src/app`

```
src/app/
  layout.tsx                 (RootLayout, Server Component)
  page.tsx                   (LoginPage → <Suspense><LoginClient/></Suspense>)
  LoginClient.tsx            ('use client')
  providers.tsx              ('use client': QueryClientProvider + MantineProvider + Notifications)
  globals.css
  (app)/
    layout.tsx               ('use client': AuthenticatedLayout → AppShellLayout)
    academias/               page.tsx, AcademiasClient.tsx
    agente-cardio/           page.tsx ('use client', página inteira)
    cardio/                  page.tsx ('use client')
    dashboard/               page.tsx ('use client'), ciclo/, engine/, musculos/, progressao/, recovery/, treinos/
    dominio-exercicios/      page.tsx, DominioExerciciosClient.tsx, components/ (10 arquivos)
    equipamentos-exercicios/ page.tsx, EquipamentosExerciciosClient.tsx
    evolucao-corporal/       page.tsx ('use client')
    exercicios/              page.tsx ('use client')
    grupos-musculares/       page.tsx, GruposMuscularesClient.tsx
    indicadores-fisicos/     page.tsx ('use client'), BioimpedanciaSection, RecoveryMetricsSection, SleepMetricsSection, ManualEffortMetricsSection
    insights/                page.tsx ('use client')
    intervals-icu/           page.tsx ('use client')
    modelos-treino/          page.tsx, WorkoutPlanTemplatesListClient.tsx, AssignTemplateModal.tsx, WeeklyActivationPreviewTable.tsx, [id]/page.tsx, [id]/WorkoutPlanTemplateBuilderClient.tsx
    perfil/                  page.tsx (Suspense), PerfilClient.tsx
    planos-treino/           page.tsx, PlanosTreinoClient.tsx, GenerateDraftModal.tsx, GenerationWarnings.tsx, MuscleActivationPreview.tsx, PlanMuscleMap.tsx, ExercisesTable.tsx, [id]/page.tsx, [id]/PlanDetailClient.tsx
    recuperacao-muscular/    page.tsx ('use client')
    registro-exercicios/     page.tsx ('use client'), [activityId]/page.tsx, [activityId]/WorkoutDetailClient.tsx
    relatorios/              page.tsx ('use client')
    tipos-treinamento/       page.tsx, TrainingTypesListClient.tsx, [id]/page.tsx, [id]/TrainingTypeBuilderClient.tsx
    treinos/                 page.tsx ('use client')
    usuarios/                page.tsx, UsuariosClient.tsx
    volume-grupamentos/      page.tsx, VolumeGrupamentosClient.tsx
  (fullscreen)/
    layout.tsx               ('use client': FullscreenAuthenticatedLayout, sem AppShell)
    modo-treino/             page.tsx, WorkoutModeClient.tsx + 11 componentes co-localizados
```

Não existem arquivos `loading.tsx`, `error.tsx`, `not-found.tsx`, `template.tsx`, `route.ts`, `middleware.ts` nem diretório `src/app/api`. O 404 é o padrão do Next (`out/404.html`).

#### 2.2 Layouts

| Arquivo | Tipo | Comportamento |
|---|---|---|
| `src/app/layout.tsx` | Server Component | Importa `@mantine/core/styles.css`, `@mantine/notifications/styles.css`, `./globals.css`. `metadata = { title: 'MuscleFit', description: 'MuscleFit — AI Powered Workout' }`; `viewport = { width: 'device-width', initialScale: 1 }`. Renderiza `<html lang="pt-BR" {...mantineHtmlProps}>`, `<ColorSchemeScript defaultColorScheme="dark" />`, `<link rel="icon" href="/logo.jpg" />` e `<Providers>{children}</Providers>`. |
| `src/app/providers.tsx` | Client | `const [queryClient] = useState(() => new QueryClient())` (sem `defaultOptions`); `<MantineProvider theme={theme} defaultColorScheme="dark">`; `<Notifications position="top-right" />`. |
| `src/app/(app)/layout.tsx` | Client | `AuthenticatedLayout`: `useEffect` chama `apiFetch('/auth/me')`; em sucesso `setChecked(true)`; em erro `router.replace('/')`. Enquanto `!checked` renderiza `<Center style={{minHeight:'100vh', background: BRAND.bg}}><Loader color="brand"/></Center>`; depois `<AppShellLayout>{children}</AppShellLayout>`. |
| `src/app/(fullscreen)/layout.tsx` | Client | `FullscreenAuthenticatedLayout`: mesma guarda (`/auth/me` → `checked` ou `router.replace('/')`), mas renderiza `<>{children}</>` sem `AppShellLayout`. |

Os route groups `(app)` e `(fullscreen)` não aparecem na URL.

#### 2.3 Tabela de rotas

Colunas: path público; arquivo `page.tsx`; se é Server (S) ou Client (C); componente cliente principal; `generateStaticParams`; uso de `useDynamicRouteParam`; `Suspense`; título `<Title order={1}>` renderizado.

| Path | page.tsx | S/C | Componente principal | generateStaticParams | useDynamicRouteParam | Suspense | Título na tela |
|---|---|---|---|---|---|---|---|
| `/` | `src/app/page.tsx` | S | `LoginClient` | — | — | sim (`fallback={null}`, por `useSearchParams`) | "Entrar" |
| `/dashboard` | `(app)/dashboard/page.tsx` | C (página inteira) | `DashboardPage` (default export) | — | — | — | "Dashboard" |
| `/dashboard/treinos` | `(app)/dashboard/treinos/page.tsx` | S | `DashboardTreinosClient` | — | — | sim | "Treinos" (order 2) |
| `/dashboard/progressao` | `.../progressao/page.tsx` | S | `DashboardProgressaoClient` | — | — | sim | "Progressão" (order 2) |
| `/dashboard/recovery` | `.../recovery/page.tsx` | S | `DashboardRecoveryClient` | — | — | sim | "Recuperação" (order 2) |
| `/dashboard/musculos` | `.../musculos/page.tsx` | S | `DashboardMusculosClient` | — | — | sim | "Grupos musculares" (order 2) |
| `/dashboard/ciclo` | `.../ciclo/page.tsx` | S | `DashboardCicloClient` | — | — | não (não usa `useSearchParams`) | "Ciclo de treino" (order 2) |
| `/dashboard/engine` | `.../engine/page.tsx` | S | `DashboardEngineClient` | — | — | sim | "Motor de geração" (order 2) |
| `/registro-exercicios` | `(app)/registro-exercicios/page.tsx` | C | `RegistroExerciciosPage` | — | — | — | "Registro de exercícios" |
| `/registro-exercicios/[activityId]` | `.../[activityId]/page.tsx` | S | `WorkoutDetailClient` | `[{ activityId: 'placeholder' }]` | sim (`params.activityId`) | — | nome da atividade (order 2 em `ActivitySummary`) |
| `/modo-treino` | `(fullscreen)/modo-treino/page.tsx` | S | `WorkoutModeClient` | — | — | — | "Modo de treino" (header próprio) |
| `/recuperacao-muscular` | `(app)/recuperacao-muscular/page.tsx` | C | `RecuperacaoMuscularPage` | — | — | — | "Recuperação muscular" |
| `/agente-cardio` | `(app)/agente-cardio/page.tsx` | C | `AgenteCardioPage` | — | — | — | "Agente de Cardio" |
| `/insights` | `(app)/insights/page.tsx` | C | `InsightsPage` | — | — | — | "Insights" |
| `/cardio` | `(app)/cardio/page.tsx` | C | `CardioPage` | — | — | — | "Cardio" |
| `/indicadores-fisicos` | `(app)/indicadores-fisicos/page.tsx` | C | `IndicadoresFisicosPage` (4 seções) | — | — | — | "Indicadores Físicos" |
| `/intervals-icu` | `(app)/intervals-icu/page.tsx` | C | `IntervalsIcuPage` | — | — | — | "Intervals.icu" |
| `/evolucao-corporal` | `(app)/evolucao-corporal/page.tsx` | C | `EvolucaoCorporalPage` | — | — | — | "Evolução corporal" |
| `/exercicios` | `(app)/exercicios/page.tsx` | C | `ExerciciosPage` | — | — | — | "Exercícios" |
| `/grupos-musculares` | `(app)/grupos-musculares/page.tsx` | S | `GruposMuscularesClient` | — | — | — | "Grupos musculares" |
| `/volume-grupamentos` | `(app)/volume-grupamentos/page.tsx` | S | `VolumeGrupamentosClient` | — | — | — | "Volume por grupamento" |
| `/treinos` | `(app)/treinos/page.tsx` | C | `TreinosPage` | — | — | — | "Treinos" |
| `/relatorios` | `(app)/relatorios/page.tsx` | C | `RelatoriosPage` | — | — | — | "Relatórios" |
| `/planos-treino` | `(app)/planos-treino/page.tsx` | S | `PlanosTreinoClient` | — | — | — | "Planos de treino" |
| `/planos-treino/[id]` | `.../[id]/page.tsx` | S | `PlanDetailClient` | `[{ id: 'placeholder' }]` | sim (`params.id`) | — | nome do plano |
| `/academias` | `(app)/academias/page.tsx` | S | `AcademiasClient` | — | — | — | "Academias" |
| `/equipamentos-exercicios` | `(app)/equipamentos-exercicios/page.tsx` | S | `EquipamentosExerciciosClient` | — | — | — | "Equipamentos x Exercícios" |
| `/dominio-exercicios` | `(app)/dominio-exercicios/page.tsx` | S | `DominioExerciciosClient` | — | — | — | "Domínio de Exercícios" |
| `/tipos-treinamento` | `(app)/tipos-treinamento/page.tsx` | S | `TrainingTypesListClient` | — | — | — | "Tipos de treinamento" |
| `/tipos-treinamento/[id]` | `.../[id]/page.tsx` | S | `TrainingTypeBuilderClient` | `[{ id: 'placeholder' }]` | sim (`params.id`) | — | "Novo tipo de treinamento" / "Editando: {name}" |
| `/modelos-treino` | `(app)/modelos-treino/page.tsx` | S | `WorkoutPlanTemplatesListClient` | — | — | — | "Modelos de treino" |
| `/modelos-treino/[id]` | `.../[id]/page.tsx` | S | `WorkoutPlanTemplateBuilderClient` | `[{ id: 'placeholder' }]` | sim (`params.id`) | — | "Novo modelo de treino" / "Editando: {name}" |
| `/usuarios` | `(app)/usuarios/page.tsx` | S | `UsuariosClient` | — | — | — | "Usuários" |
| `/perfil` | `(app)/perfil/page.tsx` | S | `PerfilClient` | — | — | sim (por `useSearchParams` de `?troque-senha=1`) | "Meu perfil" |

Total: 34 paths (1 público + 33 autenticados; destes, 4 dinâmicos). Nas rotas dinâmicas o valor `'novo'` é tratado como "criação" em `/tipos-treinamento/[id]` e `/modelos-treino/[id]` (`isNew = routeId === 'novo'`); em `/planos-treino/[id]` e `/registro-exercicios/[activityId]` o segmento é sempre convertido com `Number(...)`.

#### 2.4 `src/lib/use-dynamic-route-param.ts`

Código completo (sem os comentários):

```ts
'use client';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';

export const STATIC_EXPORT_PLACEHOLDER = 'placeholder';

export function useDynamicRouteParam(paramValue: string | undefined): string | null {
  const isReal = !!paramValue && paramValue !== STATIC_EXPORT_PLACEHOLDER;
  const [resolved, setResolved] = useState<string | null>(isReal ? (paramValue as string) : null);
  const pathname = usePathname();

  useEffect(() => {
    if (isReal) {
      setResolved(paramValue as string);
      return;
    }
    if (typeof window === 'undefined') return;
    const segments = window.location.pathname.split('/').filter(Boolean);
    setResolved(segments.length ? segments[segments.length - 1] : null);
  }, [isReal, paramValue, pathname]);

  return resolved;
}
```

Fatos sobre o hook:

- Recebe o valor de `useParams()` do chamador (`params.id` ou `params.activityId`).
- Valor inicial do estado: o próprio `paramValue` se ele existir e for diferente de `'placeholder'`; senão `null`.
- O `useEffect` roda com dependências `[isReal, paramValue, pathname]`. `pathname` vem de `usePathname()` e entra só como dependência; a leitura do id real é feita de `window.location.pathname`, tomando o último segmento não vazio.
- Retorna `null` no primeiro render quando o param é o placeholder; os consumidores tratam `null` como "ainda resolvendo": `PlanDetailClient` e `WorkoutDetailClient` convertem para `Number.NaN` e desabilitam a query (`enabled: Number.isFinite(planId)` / `isValidId`); os builders de tipo/modelo mantêm `initialized=false` e mostram `<Loader />` até o efeito de hidratação rodar.
- O comentário do arquivo documenta o motivo: em export estático o payload de Flight embutido carrega `["id","placeholder","d"]`, e `useParams().id` devolve `"placeholder"` mesmo com outro id na barra de endereços.

#### 2.5 Menu de navegação (`src/components/AppShellLayout.tsx`)

`AppShell` do Mantine com `header={{ height: 64 }}`, `navbar={{ width: 240, breakpoint: 'sm', collapsed: { mobile: !navbarOpened, desktop: false } }}`, `padding="lg"`. Header: `Burger` (`hiddenFrom="sm"`), logo 32×32, texto "MuscleFit" com `.brand-gradient-text`, e à direita um `Menu` com `Avatar` (inicial do nome) + nome (`visibleFrom="sm"`) contendo "Meu perfil" (`/perfil`) e "Sair" (vermelho, `handleLogout`).

Itens da navbar, na ordem exata do JSX (todos `NavLink` `variant="light" color="brand"` com `styles={{ root: { borderRadius: 8, fontWeight: 600 } }}`; `active` por `pathname?.startsWith(href)`):

| # | Label | href | Ícone | Condição de exibição |
|---|---|---|---|---|
| 1 | Dashboard (item pai com submenu, `defaultOpened={pathname?.startsWith('/dashboard')}`, `childrenOffset={18}`, `onClick` com `stopPropagation`) | — | `IconLayoutDashboard` | sempre |
| 1.1 | Visão geral | `/dashboard` | `IconGauge` | sempre; `active` por igualdade (`pathname === href || pathname === href + '/'`) |
| 1.2 | Treinos | `/dashboard/treinos` | `IconHistory` | idem |
| 1.3 | Progressão | `/dashboard/progressao` | `IconChartHistogram` | idem |
| 1.4 | Recuperação | `/dashboard/recovery` | `IconHeartRateMonitor` | idem |
| 1.5 | Grupos musculares | `/dashboard/musculos` | `IconBodyScan` | idem |
| 1.6 | Ciclo de treino | `/dashboard/ciclo` | `IconRepeat` | idem |
| 1.7 | Motor de geração | `/dashboard/engine` | `IconEngine` | idem |
| 2 | Registro de exercícios | `/registro-exercicios` | `IconClipboardList` | sempre |
| 3 | Modo Treino | `/modo-treino` | `IconPlayerPlay` | sempre |
| 4 | Recuperação muscular | `/recuperacao-muscular` | `IconHeartRateMonitor` | sempre |
| 5 | Agente de Cardio | `/agente-cardio` | `IconActivity` | sempre |
| 6 | Insights | `/insights` | `IconMessageChatbot` | sempre |
| 7 | Cardio | `/cardio` | `IconHeartbeat` | `hasIntervalsConfigured` |
| 8 | Indicadores Físicos | `/indicadores-fisicos` | `IconReportMedical` | sempre |
| 9 | Intervals.icu | `/intervals-icu` | `IconRun` | `hasIntervalsConfigured` |
| 10 | Evolução corporal | `/evolucao-corporal` | `IconScale` | sempre |
| 11 | Exercícios | `/exercicios` | `IconBarbell` | sempre |
| 12 | Grupos musculares | `/grupos-musculares` | `IconVectorTriangle` | sempre |
| 13 | Volume por grupamento | `/volume-grupamentos` | `IconChartBar` | sempre |
| 14 | Treinos | `/treinos` | `IconTrendingUp` | sempre |
| — | Relatórios | `/relatorios` | `IconFileAnalytics` | **comentado no JSX** (bloco `{/* Relatórios desativado temporariamente no menu (rota/backend seguem ativos) ... */}`); a rota continua acessível por URL |
| 15 | Planos de treino | `/planos-treino` | `IconNotes` | sempre |
| 16 | Academias | `/academias` | `IconBuildingWarehouse` | sempre |
| 17 | Equipamentos x Exercícios | `/equipamentos-exercicios` | `IconPlugConnected` | sempre |
| 18 | Domínio de Exercícios | `/dominio-exercicios` | `IconDatabase` | sempre |
| 19 | Tipos de treinamento | `/tipos-treinamento` | `IconTarget` | `canManageTrainingTypes` |
| 20 | Modelos de treino | `/modelos-treino` | `IconTemplate` | `canManageWorkoutPlanTemplates` (= `canManageTrainingTypes`) |
| 21 | Usuários | `/usuarios` | `IconUsers` | `isAdmin` |

Flags derivadas da query `['current-user']` (`GET /auth/me`, `staleTime: 60_000`, `retry: false`):

```ts
const canManageTrainingTypes =
  !currentUserQuery.isSuccess || user?.role === 'admin' || user?.role === 'personal_trainer';
const canManageWorkoutPlanTemplates = canManageTrainingTypes;
const isAdmin = !currentUserQuery.isSuccess || user?.role === 'admin';
const hasIntervalsConfigured = !currentUserQuery.isSuccess || user?.intervals_configured === true;
```

Enquanto `/auth/me` não resolveu, todas as flags são `true` (itens visíveis). O `Stack` da navbar tem `onClick={closeNavbar}` (fecha o drawer mobile ao clicar em qualquer item); o item pai "Dashboard" cancela esse fechamento com `event.stopPropagation()`.

`handleLogout`: `await apiFetch('/auth/logout', { method: 'POST' })` dentro de `try/catch` (erro ignorado), depois `clearSession()` e `router.replace('/')` no `finally`.

---

### 3. Camada de dados (`src/lib`)

#### 3.1 Inventário de `src/lib`

| Arquivo | Linhas | Papel |
|---|---|---|
| `api.ts` | 128 | `ApiError`, `apiFetch<T>`, `apiFetchBlob`, interceptor de 401 |
| `auth.ts` | 88 | `SessionUser`, `LoginResult`, `AuthError`, `login()`, `clearSession()` |
| `config.ts` | 10 | `API_BASE_URL` |
| `async-job-poll.ts` | 84 | `pollAsyncJob`, `dispatchAndPollJob`, constantes de polling |
| `use-dynamic-route-param.ts` | 62 | hook descrito em 2.4 |
| `dashboard-filters.ts` | 112 | `useDashboardFilters` (estado na URL), `DASHBOARD_DAYS_OPTIONS`, `dashboardDaysSelectData` |
| `dashboard-format.ts` | 33 | `fmtDate`, `fmtDecimal`, `fmtDuration`, `fmtKg` |
| `muscle-readiness.ts` | 28 | `readinessLabel`, `readinessColor`, `readinessIcon` |
| `advanced-techniques.ts` | 94 | descrição, `set_type` e substantivo de aplicação por técnica avançada |
| `plan-muscle-map.ts` | 47 | `fetchPlanMuscleMapUrl` (blob autenticado + cache em memória) |
| `user-photo.ts` | 48 | `fetchUserPhotoUrl` (blob autenticado + cache em memória) |
| `workout-mode-load-suggestions.ts` | 87 | fórmulas de carga de aquecimento/ajuste/técnica |
| `workout-mode-types.ts` | 496 | tipos do modo treino, localStorage da sessão, `fetchWorkoutAiResult` (polling 500 ms) |
| `workout-mode-exercise-card.ts` | 119 | card ilustrado do exercício EFETIVO no modo treino: `resolveExerciseCardSource` (ficha × `GET /exercises/{id}/card`), hook `useEffectiveExerciseCard`, `collectCardThumbnailUrls` + `preloadImages` (pré-carga das miniaturas) |
| `analytics-types.ts` | 424 | tipos de analytics, `AsyncJob<T>`, recomendações de cardio, contexto do agente de cardio |
| `dashboard-v2-types.ts` | 363 | tipos das 6 sub-rotas `/dashboard/*` |
| `cardio-dashboard-types.ts` | 106 | tipos de `/cardio` |
| `insights-types.ts` | 38 | tipos de `/insights` |
| `intervals-types.ts` | 107 | tipos de `/intervals-icu` + `sumBy`, `averageOf` |
| `reports-types.ts` | 49 | tipos de `/relatorios` |
| `gyms-types.ts` | 112 | tipos de `/academias` + `WEEKDAYS`, `weekdayLabels`, `toTimeInputValue` |
| `users-admin-types.ts` | 80 | tipos de `/usuarios` + `USER_ROLES`, `ROLE_OPTIONS`, `SEX_OPTIONS`, `roleLabel` |
| `training-types-types.ts` | 78 | tipos de `/tipos-treinamento` |
| `equipment-exercise-mappings-types.ts` | 117 | tipos de `/equipamentos-exercicios` |
| `exercise-catalog-admin-types.ts` | 289 | tipos de `/dominio-exercicios` |
| `mappings-types.ts` | 129 | tipos do router `/mapping-canonical` |
| `muscle-groups-types.ts` | 223 | tipos de `/grupos-musculares` |
| `physical-indicators-types.ts` | 226 | tipos e conversores de `/indicadores-fisicos` |
| `training-volume-types.ts` | 162 | tipos de `/volume-grupamentos` |
| `workout-log-types.ts` | 303 | tipos de `/registro-exercicios*` + helpers de data/formatação |
| `workout-plans-types.ts` | 239 | tipos de `/planos-treino*` + constantes de opções |
| `workout-plan-templates-types.ts` | 320 | tipos de `/modelos-treino*` + constantes de opções |
| `workouts-analysis-types.ts` | 237 | tipos de `/treinos` (payload `training` e `training-evolution`) |

Não há hooks customizados além de `useDynamicRouteParam` e `useDashboardFilters` em `src/lib`; `usePhotoLightbox` fica em `(fullscreen)/modo-treino/PhotoLightbox.tsx`.

#### 3.2 `config.ts`

```ts
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, '') ??
  'https://api.musclefit.com.br';
```

Única leitura de `process.env` em `src/`. Barras finais são removidas. O comentário do arquivo afirma que o token mestre da API (`API_TOKEN`) nunca entra no frontend.

#### 3.3 `api.ts` — cliente HTTP

```ts
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.name = 'ApiError'; this.status = status; }
}
let redirectingToLogin = false;
function sanitizeNextPath(path: string): string | null {
  if (!path || !path.startsWith('/') || path.startsWith('//')) return null;
  if (path === '/') return null;
  return path;
}
function redirectToLogin(): void {
  if (typeof window === 'undefined') return;
  if (window.location.pathname === '/') return;
  if (redirectingToLogin) return;
  redirectingToLogin = true;
  clearSession();
  const currentPath = `${window.location.pathname}${window.location.search}`;
  const next = sanitizeNextPath(currentPath);
  window.location.href = next ? `/?next=${encodeURIComponent(next)}` : '/';
}
```

`apiFetch<T>(path, init = {})`:

1. `isFormData = typeof FormData !== 'undefined' && init.body instanceof FormData`.
2. `headers = { ...(init.body && !isFormData ? { 'Content-Type': 'application/json' } : {}), ...init.headers }`. Não envia `X-API-Token` nem `Authorization`.
3. `fetch(`${API_BASE_URL}${path}`, { ...init, headers, credentials: 'include' })`. Exceção de rede/CORS → `throw new ApiError('Não foi possível conectar à API. Verifique sua conexão e tente novamente.', 0)`.
4. `response.status === 401` → `redirectToLogin()` e `throw new ApiError('Sessão expirada. Faça login novamente.', 401)`.
5. `!response.ok` → tenta `response.json()`; se `body.detail` for string, é a mensagem; senão `` `Erro ao consultar ${path} (HTTP ${response.status}).` ``; lança `ApiError(detail, status)`.
6. `status === 204` → `undefined as T`.
7. Senão `(await response.json()) as T`.

`apiFetchBlob(path, init = {})`: `fetch` com `credentials: 'include'` (sem header de conteúdo), mesma mensagem de falha de rede (status 0), mesmo tratamento de 401; `!ok` → `ApiError(`Erro ao consultar ${path} (HTTP ${status}).`, status)` (não lê `detail`); retorna `response.blob()`. Usado por `/perfil` (foto) e `/relatorios` (PDF).

Credencial: cookie httpOnly `musclefit_session` emitido por `POST /auth/login` (segundo comentários de `auth.ts`/`config.ts`), enviado por `credentials: 'include'`. Nenhum token é lido ou escrito em JS. O frontend não envia `X-API-Token`.

Tratamento de erro nas telas: padrão recorrente `function errorMessage(error, fallback) { return error instanceof ApiError ? error.message : fallback; }` e `isForbidden(error) = error instanceof ApiError && error.status === 403`, redefinidos localmente em vários arquivos (não há utilitário compartilhado). Algumas telas usam `error instanceof Error ? error.message : '...'`. Fallback textual mais comum: `'Não foi possível consultar a Fitness API.'`. Em `/registro-exercicios` e no detalhe, o erro do modal de cabeçalho é exibido via `String(mutation.error)`.

#### 3.4 `auth.ts`

```ts
export interface SessionUser {
  id: number; username: string; full_name: string | null; role: string;
  must_change_password: boolean; intervals_configured: boolean;
}
export interface LoginResult { user: SessionUser; }
export class AuthError extends Error { status: number; }  // name = 'AuthError'
```

`login(username, password)`: `fetch(`${API_BASE_URL}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ username, password }) })`. Falha de rede → `AuthError('Não foi possível conectar à API. Verifique sua conexão e tente novamente.', 0)`. `!ok` → `body.detail` se string, senão `'Usuário ou senha inválidos.'`. Não persiste nada.

`clearSession()`: remove de `localStorage` as chaves legadas `'musclefit.session_token'` e `'musclefit.session_user'` em `try/catch`. Chamado por `redirectToLogin()` e pelo logout do app-shell.

#### 3.5 Padrão 202 + `job_id`

`async-job-poll.ts`:

```ts
export const ASYNC_JOB_POLL_INTERVAL_MS = 2000;
export const ASYNC_JOB_POLL_TIMEOUT_MS = 90000;
export type PollAsyncJobOptions = { intervalMs?: number; timeoutMs?: number };
export async function pollAsyncJob<TResult>(jobId, options = {}) {
  // loop: GET /jobs/{jobId}
  //   'done'   → return job.result
  //   'failed' → throw new Error(job.error || 'Falha ao gerar a análise. Tente novamente.')
  //   Date.now() >= deadline → throw new Error('Tempo esgotado aguardando a análise da IA. Tente novamente em instantes.')
  //   senão sleep(intervalMs)
}
export async function dispatchAndPollJob<TResult>(path, init = {}, options?) {
  const { job_id } = await apiFetch<{ job_id: string; status: string }>(path, { ...init, method: init.method ?? 'POST' });
  return pollAsyncJob<TResult>(job_id, options);
}
```

O deadline é checado depois de cada `GET` e antes do `sleep`. Consumidores: `/insights`, `/treinos`, `/planos-treino/[id]`.

`workout-mode-types.ts` tem implementação própria: `WORKOUT_AI_POLL_INTERVAL_MS = 500`, `WORKOUT_AI_POLL_TIMEOUT_MS = 20000`, mensagens `'Falha ao gerar resposta de IA.'` e `'Tempo esgotado aguardando resposta de IA.'`; função `fetchWorkoutAiResult<TResult>(path, init)`.

Outras telas usam `useQuery` com `refetchInterval: (query) => query.state.data?.status === 'pending' ? 2000 : false` (sem teto): `/agente-cardio`, `GenerateDraftModal`, `ModeSelectScreen`. `/relatorios` usa `refetchInterval` 5000 ms em `GET /reports/{id}` enquanto `status === 'generating'`. Tabela consolidada na seção 4.9.

#### 3.6 React Query

- `providers.tsx`: `const [queryClient] = useState(() => new QueryClient())`, sem `defaultOptions` (valem os defaults da v5: `staleTime: 0`, `retry: 3` em queries, `refetchOnWindowFocus: true`, mutações sem retry).
- Overrides existentes no código:

| Query | Opção |
|---|---|
| `['current-user']` | `staleTime: 60_000`, `retry: false` |
| `['muscle-group-body-map']` | `staleTime: 5 * 60_000` |
| `['mapping-canonical-options', 'dashboard-progressao']`, `['mapping-canonical-options', 'all']` | `staleTime: 5 * 60_000` |
| `['user-photo', id, version]`, `['plan-muscle-map', id]` | `staleTime: Infinity` |
| `['muscle-recovery', days]` | `refetchInterval: 60_000`, `refetchIntervalInBackground: true` |
| `['activity-detail-workout-mode', sessionId]` | `refetchOnMount: 'always'` |
| `['exercise-catalog-admin-muscle-groups']`, `['exercise-catalog-admin-equipment-catalog']` | `staleTime: 60_000` |
| `['dashboard-progression-suggestion', id]`, `['users-admin']`, `['training-types']`, `['training-type', id]`, `['workout-plan-templates']`, `['workout-plan-template', id]` | `retry: false` |
| `['workout-mode-substitute-recommend', ...]` | `enabled: false` (disparo manual via `refetch()`) |
| jobs (`['cardio-job', id]`, `['plan-generation-job', id]`, `['workout-mode-plan-generation-job', id]`, `['report-status', id]`) | `refetchInterval` condicional |

- Invalidações após mutação são feitas com `queryClient.invalidateQueries({ queryKey: [prefixo] })`. Não há atualizações otimistas (`setQueryData` não é usado).
- Não há `persistQueryClient`, devtools, prefetch nem hydrate.
- A lista completa de chaves por tela está na seção 4 (coluna "Chamada"/"Evento") e resumida abaixo:

| Chave | Tela |
|---|---|
| `['current-user']` | AppShellLayout, DominioExercicios, EquipamentosExercicios, Academias, Usuarios, TrainingTypesList, WorkoutPlanTemplatesList |
| `['analytics-summary', d]`, `['analytics-training-trend', d]`, `['analytics-muscle-groups-table', d]`, `['analytics-rpe-distribution', d]` | `/dashboard` |
| `['dashboard-workout-sessions', d]` | `/dashboard/treinos` |
| `['dashboard-progression', m, d]`, `['dashboard-progression-trend', m, d]`, `['dashboard-progression-suggestion', m]` | `/dashboard/progressao` |
| `['dashboard-recovery', d]` | `/dashboard/recovery` |
| `['dashboard-muscle-status', d, tt]`, `['dashboard-muscle-drilldown', g, d]`, `['dashboard-stimulus-fatigue', g, d]` | `/dashboard/musculos` |
| `['dashboard-cycle']` | `/dashboard/ciclo` |
| `['workout-plans', 'engine-audit']`, `['dashboard-engine-audit', p]` | `/dashboard/engine` |
| `['gyms','active']`, `['activities', {...filtros}]`, `['workout-plans','planned']`, `['training-types','active']` | `/registro-exercicios`, `NewWorkoutModal` |
| `['activity-detail', id]`, `['muscle-activation-by-activity', id]`, `['mapping-catalog-active']`, `['mapping-catalog-groups']`, `['gym-equipment-options', gym, group]`, `['workout-plan-detail', planId]` | `/registro-exercicios/[activityId]` |
| `['muscle-recovery', d]` | `/recuperacao-muscular` |
| `['cardio-recommendations-history']`, `['cardio-agent-context', d]`, `['cardio-job', job]` | `/agente-cardio` |
| `['intervals-icu-status']`, `['cardio-dashboard', d]`, `['intervals-icu-dashboard', d]` | `/cardio`, `/intervals-icu` |
| `['body-composition']`, `['intervals-wellness', d]`, `['sleep-metrics']`, `['manual-effort-metrics']` | `/indicadores-fisicos` |
| `['body-composition-trend', d]` | `/evolucao-corporal` |
| `['analytics-exercise-progress', q]` | `/exercicios` |
| `['muscle-groups-mapped-trend', ...]`, `['muscle-groups-mapped-table', ...]`, `['exercise-mapping-catalog-groups']`, `['exercise-mapping-catalog', ...]`, `['mapping-canonical-options']` | `/grupos-musculares` |
| `['muscle-groups-recent-exercise-volume', 400]`, `['muscle-groups-equivalent-sets-by-activity', 400]`, `['training-volume', n]`, `['muscle-groups-recent-exercise-volume-range', from, to]` | `/volume-grupamentos` |
| `['training-evolution', d]`, `['training-trend-week', d]`, `['rpe-distribution', d]`, `['muscle-groups-daily-activation', 7]`, `['muscle-recovery-consecutive-check', 7]` | `/treinos` |
| `['reports']`, `['report-status', id]` | `/relatorios` |
| `['workout-plans']`, `['gyms']`, `['workout-plan-templates-assigned-to-me']`, `['plan-generation-job', job]`, `['muscle-activation-preview', payload]`, `['muscle-activation-breakdown', json]`, `['workout-plan', id]`, `['cardio-recommendations-for-plan', id]`, `['plan-muscle-map', id]` | `/planos-treino*` |
| `['equipment-catalog']`, `['gym-equipment-catalog', gym]`, `['gym-equipment-recommend-all', gym]` | `/academias` |
| `['equipment-catalog', termo]`, `['equipment-exercise-mappings', eq, group]`, `['mapping-canonical-options', search]`, `['muscle-groups-catalog']`, `['muscle-group-equipment', g]`, `['exercise-mapping-catalog-by-group', nome]`, `['equipment-exercise-mappings-by-equipment', eq]`, `['exercise-catalog-admin-equipment-types']`, `['exercise-aliases', id]`, `['equipment-photo']` (só invalidada) | `/equipamentos-exercicios` |
| `['exercise-catalog-admin-exercises', ...]`, `['exercise-catalog-admin-exercise-detail', id]`, `['exercise-external-mappings-flat', ...]`, `['exercise-external-mapping-providers']`, `['exercise-catalog-options-for-mapping', s]`, `['exercise-catalog-admin-muscle-groups-crud']` | `/dominio-exercicios` |
| `['training-type-rag-folders']`, `['training-types-for-templates']`, `['workout-plan-template-assignments', id]`, `['workout-plan-template-users-lookup', s]`, `['workout-plan-template-preview-activation', payload]`, `['equipment-exercise-mappings-for-group', nome]` | `/tipos-treinamento*`, `/modelos-treino*` |
| `['users-admin']`, `['user-photo', id, v]`, `['my-profile']` | `/usuarios`, `/perfil` |
| `['workout-mode-gyms']`, `['workout-mode-resume-candidate']`, `['workout-mode-existing-plans']`, `['workout-mode-advanced-plans']`, `['workout-plan-detail-workout-mode', id]`, `['workout-mode-plan-gym-equipment', gym]`, `['suggested-set-count-for-substitution', ...]`, `['suggested-progression', m]`, `['last-session-sets', m, s, eq]`, `['workout-mode-freeform-gym-equipment', gym, g]`, `['workout-mode-substitute-catalog', gym, g]` | `/modo-treino` |

#### 3.7 `dashboard-filters.ts`

```ts
export const DASHBOARD_DAYS_OPTIONS = [7, 14, 30, 90, 180] as const;
export const DEFAULT_DASHBOARD_DAYS = 14;
export type DashboardFilters = { days: number; trainingTypeId: number | null; exerciseMappingId: number | null; exerciseName: string | null; planId: number | null };
```

`useDashboardFilters()` lê `useSearchParams()`: `days` aceito só se estiver em `DASHBOARD_DAYS_OPTIONS` (senão 14); `trainingTypeId`/`exerciseMappingId`/`planId` via `Number(raw)` finito ou `null`; `exerciseName` texto cru. `setFilters(patch)` faz merge, remove `days` quando 14, remove chaves nulas/vazias e chama `router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false })`. `dashboardDaysSelectData(allowed)` → `[{ value: '7', label: 'Últimos 7 dias' }, ...]`. Parâmetros de URL: `?days=`, `?trainingTypeId=`, `?exerciseMappingId=`, `?exerciseName=`, `?planId=`. Nenhuma tela escreve `trainingTypeId` (é apenas lido em `/dashboard/musculos`).

#### 3.8 Formatação e datas

`dashboard-format.ts`:

- `fmtDate(value)`: `'-'` se vazio; `new Date(`${value.slice(0,10)}T00:00:00`).toLocaleDateString('pt-BR')`; inválido → a string ISO de 10 caracteres.
- `fmtDecimal(value, digits = 1)`: `'-'` se nulo/não finito; `toLocaleString('pt-BR', { maximumFractionDigits: digits })`.
- `fmtDuration(seconds)`: `` `${Math.round(seconds / 60)} min` ``.
- `fmtKg(value)`: `` `${fmtDecimal(value)} kg` ``.

`physical-indicators-types.ts`: `formatDateBR` (regex `^(\d{4})-(\d{2})-(\d{2})$` → `dd/mm/yyyy`), `todayLocalDate()` = `new Date().toISOString().slice(0, 10)` (data UTC), `formatNumberOrDash(v, digits = 1)`, `decimalHoursToHHMM(v)` (arredonda minutos; negativo → 0), `hhmmToDecimalHours(s)` (regex `^(\d{1,2}):(\d{2})$`; minutos ≥ 60 → `null`; arredonda a 2 casas).

`workout-log-types.ts`: `formatNumber(v, digits = 1)`; `periodToDateFrom(periodDays)` (`'all'` → `null`; senão hoje − N dias em `toISOString().slice(0,10)`); `buildStartedAt(localDate, time)` → `` `${localDate}T${HH:MM:00}` `` (sem fuso); `extractTime(startedAt)` via `/T(\d{2}:\d{2})/`; `formatSets(sets)` → `"1) 10x 20kg @RPE 8 | 2) ..."`.

Funções de data definidas localmente em telas: `/treinos` `toLocalIsoDate` (ano/mês/dia locais), `buildLast7Days()`, `formatDayMonth`; `/volume-grupamentos` `parseDateKey` (`T12:00:00`), `startOfDay`, `addDays`, `startOfWeek` (segunda-feira), `startOfMonth`, `endOfDay`, `daysBetweenInclusive`, `inRange`, `toDateKey`, `monthKeyOf`, `lastDayOfMonthKey`, `weekKeyOf` (usa `toISOString().slice(0,10)` da segunda-feira local às 00:00); `/recuperacao-muscular` `formatLastTrainedDate`, `formatHoursToHHMM`; `/relatorios` `toIsoDate` (`toISOString`), `addDays`; `/agente-cardio` e `/relatorios` `formatDateTime` (`toLocaleString('pt-BR', {day, month, year, hour, minute})`). Não há biblioteca de datas (dayjs/date-fns) nem conversão explícita para `America/Sao_Paulo`; datas dependem do fuso do navegador ou de `toISOString()` (UTC), conforme a função.

#### 3.9 `muscle-readiness.ts`

| `readiness_status` | `readinessLabel` | `readinessColor` | `readinessIcon` |
|---|---|---|---|
| `'READY'` | `'Pronto'` | `'green'` | `IconBarbell` |
| `'REDUCED_DOSE'` | `'Dose reduzida'` | `'yellow'` | `IconBarbellOff` |
| outro (`'NO_DIRECT_WORK'`) | `'Sem trabalho direto'` | `'red'` | `IconBed` |

As telas usam a cor como `var(--mantine-color-${cor}-6)`.

#### 3.10 `advanced-techniques.ts`

- `getAdvancedTechniqueLabel(value)`: label de `ADVANCED_TECHNIQUE_OPTIONS` ou o próprio valor.
- `EXTRA_REPS_TECHNIQUES = ['forced_reps', 'negative_reps', 'partial_reps', 'cheat_reps']`; `isExtraRepsTechnique(value)`.
- Descrições (`getAdvancedTechniqueDescription`):
  - `rest_pause`: "Após a falha, pause 5–15s e retome com a mesma carga para mais repetições. Repita por mini-blocos."
  - `drop_set`: "Ao atingir a falha, reduza a carga (~20%) sem descanso e continue até nova falha. Repita a cada drop."
  - `fst7`: "7 séries até a falha com 30s de intervalo, alongando a musculatura durante a pausa para expandir a fáscia."
  - `gvt`: "10 séries de 10 repetições com carga moderada e 1 min de intervalo, focando em volume total (German Volume Training)."
  - `forced_reps`: "Após a falha, receba ajuda mínima de um parceiro (ou mão oposta) para completar 2–3 repetições extras."
  - `negative_reps`: "O parceiro realiza a fase concêntrica; você controla apenas a descida (fase excêntrica) de forma lenta."
  - `partial_reps`: "Após a falha em amplitude completa, continue com repetições em amplitude reduzida."
  - `cheat_reps`: "Use impulso corporal controlado para vencer o ponto cego, controlando a fase excêntrica. Contraindicado para iniciantes."
- `getAdvancedTechniqueSetType`: `rest_pause`→`'rest_pause'`; `drop_set`→`'drop_set'`; `fst7`/`gvt`→`'working'`; família extra reps→`'forced_rep'`; default `'working'`.
- `getAdvancedTechniqueApplicationNoun`: `rest_pause`→`'Mini-bloco'`; `drop_set`→`'Drop'`; `fst7`/`gvt`→`'Série'`; default `'Repetição extra'`.

#### 3.11 `workout-mode-load-suggestions.ts`

```ts
const WEIGHT_STEP_KG = 2.5;
function roundToStep(v) { return Math.round(v / WEIGHT_STEP_KG) * WEIGHT_STEP_KG; }
export const WEIGHT_DRAG_STEP_KG = 0.25;
export function computeWarmupLoad(ref) { return roundToStep(ref * 0.2); }
export function computeSecondWarmupLoad(ref) { return roundToStep(ref * 0.4); }
export function computeFeelerSetLoad(ref, idx /* 1-based */) { return roundToStep(ref * (0.5 + 0.2 * (idx - 1))); }
export function computeTechniqueApplicationLoad(technique, ref, idx /* 1-based */) {
  if (technique === 'drop_set') return roundToStep(ref * Math.pow(0.8, idx));
  return roundToStep(ref);
}
```

#### 3.12 `plan-muscle-map.ts` e `user-photo.ts`

Ambos usam `Map`s em escopo de módulo (cache de Object URL + `inFlight` de promises), sem revogação:

- `fetchPlanMuscleMapUrl(planId)`: `fetch(`${API_BASE_URL}/workout-plans/${planId}/muscle-map.png`, { credentials: 'include' })`; `!ok`/exceção → `null`; cache por `planId`. Não passa por `apiFetch` (401 não redireciona).
- `fetchUserPhotoUrl(userId, version = 0)`: `fetch(`${API_BASE_URL}/users/${userId}/photo`, { credentials: 'include' })`; chave `` `${userId}:${version}` ``; `!ok`/exceção → `null`. Não passa por `apiFetch`.

#### 3.13 Storage local

| Chave | Storage | Conteúdo | Onde |
|---|---|---|---|
| `musclefit.workout-mode-session` | `localStorage` | JSON de `StoredWorkoutModeSession` | `loadStoredSession` (valida `typeof session_id === 'number'`, `try/catch`), `saveStoredSession` (sem `try/catch`), `clearStoredSession`; `WorkoutModeClient`, `PlanExecutionScreen.persistSession` |
| `musclefit.workout-mode-per-set-ai-enabled` | `localStorage` | `'true'`/`'false'`; ausente → `false` | `loadPerSetAiPreference`/`savePerSetAiPreference` |
| `musclefit.session_token` | `localStorage` (legado) | só removida | `clearSession()` |
| `musclefit.session_user` | `localStorage` (legado) | só removida | `clearSession()` |

Não há `sessionStorage`, IndexedDB nem leitura de cookies em JS. Filtros de telas (exceto `/dashboard/*`, que usam a URL) vivem só em `useState`.

#### 3.14 Tipos por arquivo

`?` = opcional; `|null` = nulável.

##### `analytics-types.ts`

- `AnalyticsTotals`: `activities, strength_activities, cardio_activities, sets_count, reps_total, volume_kg: number; avg_rpe: number|null; high_rpe_sets: number`.
- `AnalyticsActivity`: `id, local_date, activity_type, name; duration_seconds?, calories_kcal?, avg_hr_bpm?, max_hr_bpm?, avg_rpe?`.
- `AnalyticsSummary`: `days; totals; latest_activities`.
- `TrainingTrendItem`: `period, period_start, period_end; workouts, sets_count, reps_total, volume_kg; avg_rpe|null; high_rpe_sets`. `TrainingTrendResponse`: `period: 'day'|'week'|'month'|'year'; days; items`.
- `RpeDistributionItem`: `rpe, sets_count`. `RpeDistributionResponse`: `days; items`.
- `MuscleGroupSetRow` (uma linha por série): `local_date, activity_name, activity_type, muscle_group, original_name; mapped_name?; set_number, reps, weight_kg, rpe, volume_kg: number|null`. `MuscleGroupsTableResponse`: `days, limit, classification, items`.
- `MuscleRecoveryRecentSet`: `exercise_id; trained_at?; reps?; rpe?; set_type; role: 'primary'|'secondary'; participation, effort_points, repetition_factor, set_type_factor`.
- `MuscleRecoveryFactors`: `session_dose, dose_last_72h; avg_rpe_used?; recent_sets; rpe10_isolated_note?`.
- `MuscleRecoveryReadinessStatus = 'READY'|'REDUCED_DOSE'|'NO_DIRECT_WORK'`.
- `MuscleRecoveryRecommendation`: `volume_multiplier, direct_sets_min, direct_sets_max, maximum_rpe; allow_failure_on_last_set, allow_progression, allow_indirect_work: boolean`.
- `MuscleRecoveryItem`: `muscle_group; sessions?, sets_count?, volume_kg?, avg_weight_kg?, avg_rpe?, last_trained_at?, last_trained_at_ts?, days_since_trained?; recovery_pct, estimated_recovery_hours, remaining_hours; status: 'green'|'yellow'|'red'; guidance: 'ideal'|'recovering'|'avoid'; readiness_status; session_dose, dose_last_72h, density_penalty; performance_ratio?; recommendation; explanation: string[]; factors?`.
- `GlobalReadinessAdjustment`: `form_tsb, factor; wellness_date?`. `MuscleRecoveryResponse`: `days; items; global_readiness_adjustment?`.
- `MuscleGroupBodyMapItem`: `muscle_group; slugs: string[]`; `MuscleGroupBodyMapResponse`: `items`.
- `MuscleActivationRow`: `muscle_group; primary_series, primary_equivalent, secondary_series, secondary_equivalent, stabilizer_series, stabilizer_equivalent, total_equivalent; primary_avg_weight_kg, secondary_avg_weight_kg, stabilizer_avg_weight_kg: number|null`.
- `MuscleActivationBreakdownItem`: `mapping_id|null; primary_activation; secondary: {muscle_group_name, activation}[]; stabilizer: {...}[]`.
- `CardioRecommendation`: `id; reference_date; workout_plan_id|null; generated_at; model|null; context_snapshot: unknown; recommendation_markdown; created_at`. `CardioRecommendationsResponse`: `items`.
- `AsyncJobStatus = 'pending'|'done'|'failed'`. `CardioGenerationResult`: `id, model, reference_date, workout_plan_id, recommendation_markdown`. `AsyncJob<TResult>`: `job_id; job_type; status; created_at|null; finished_at|null; result?; error?`.
- `BodyCompositionTrendItem`: `local_date; weight_kg, bmi, body_fat_percent, muscle_mass_kg, body_water_percent, visceral_fat_rating, fat_free_mass_kg, subcutaneous_fat_percent, protein_percent, metabolic_age: number|null`. `BodyCompositionTrend`: `status?: 'insufficient_data'; weight_velocity_per_day?, muscle_velocity_per_day?, fat_velocity_per_day?, water_velocity_per_day?`. `BodyCompositionTrendResponse`: `days; items; trend`.
- `ExerciseProgressItem`: `local_date, activity_name, original_name; mapped_name?; sets_count, reps_total; best_weight_kg|null; volume_kg; avg_rpe|null; high_rpe_sets`. `ExerciseProgressResponse`: `name; days; items`.
- `MuscleGroupsDailyActivationItem`: `local_date; muscle_group; sets_count; had_rpe_9_10: boolean`. Response `days; items`.
- `MuscleRecoveryConsecutiveCheckItem`: `muscle_group_id?; muscle_group; date_from; date_to; elapsed_hours; required_hours; insufficient_recovery; timestamp_precision: 'exact'|'day_fallback'`. Response `days; items`.
- Agente de cardio: `CardioAgentRecentWorkout` (`activity_id, local_date, activity_name?, activity_type?, duration_seconds?, activity_avg_rpe?, muscle_groups: string[], exercises_count, sets_count, reps_total, volume_kg, avg_rpe?`), `CardioAgentIntervalsWellnessDay` (`wellness_date; fitness_ctl?, fatigue_atl?, form_tsb?, ramp_rate?, weight_kg?, resting_hr_bpm?, hrv_ms?, sleep_seconds?, readiness_score?, daily_effort_load?, synced_at?`), `CardioAgentManualEffortMetric` (`id, local_date, epoc_training_load?, pai_score?, notes?, created_at?, updated_at?`), `CardioAgentSleepMetric` (`id, local_date, duration_hours?, regularity_percent?, deep_sleep_hours?, rem_sleep_hours?, awake_count?, awake_hours?, notes?, created_at?, updated_at?`), `CardioAgentPastRecommendation` (`id, reference_date, generated_at, model?, recommendation_markdown`), `CardioAgentContextResponse` (`days; body_composition; recent_workouts; muscle_recovery: MuscleRecoveryItem[]; intervals_wellness; manual_effort_metrics; sleep_metrics; weekly_training_load: WeeklySummary; past_recommendations`).

##### `dashboard-v2-types.ts`

- `MuscleVolumeStatus = 'BELOW_MIN'|'BETWEEN_MIN_TARGET'|'ON_TARGET'|'BETWEEN_TARGET_MAX'|'ABOVE_MAX'`; `TrainingTypeSource = 'explicit'|'assigned_template'|'fallback_generic'`; `MuscleTargetSource = 'training_type'|'generic_fallback'`.
- `MuscleStatusItem`: `muscle_group_id|null, muscle_group, actual_effective_sets, actual_effective_sets_in_window, min_weekly_sets, target_weekly_sets, max_weekly_sets, status, target_source, sessions, last_trained_at|null, avg_rpe|null, direct_sets, indirect_sets, stabilizer_sets`. `DashboardMuscleStatusResponse`: `days, training_type_id, training_type_source, items`.
- `MuscleDrilldownOverview|null` (`muscle_group_id, muscle_group, sessions, sets_count, direct_sets, indirect_sets, stabilizer_sets, stimulus_equivalent_sets, avg_rpe, last_trained_at, last_direct_training_at, days_since_trained`); `MuscleDrilldownReadiness|null` (`recovery_pct, status, guidance, remaining_hours, explanation`); `MuscleDrilldownTopExercise` (`mapping_id, exercise_name, sets_count, stimulus_equivalent_sets, last_trained_at`); `MuscleDrilldownProgression|null` (`reps_target, load_target, rpe_target, progression_note, has_history, progression: 'increased'|'decreased'|null`); `MuscleDrilldownWeeklyPoint` (`period, period_start, period_end, muscle_group, sets_count, reps_total, volume_kg, avg_rpe, direct_sets, indirect_sets, stimulus_equivalent_sets`); `DashboardMuscleDrilldownResponse` (`muscle_group_id, days, overview, readiness, top_exercises, latest_progression, weekly_series`).
- `StimulusFatigueRow` (`mapping_id, exercise_name, sets_count, stimulus_equivalent_sets, fatigue_equivalent_sets, ratio|null`); `DashboardStimulusFatigueResponse` (`muscle_group_id, activity_id, days, items`).
- `ProgressionSessionItem` (`activity_id, local_date, activity_name, sets_count, best_load_kg, best_reps, avg_rpe, e1rm`); `DashboardProgressionResponse` (`exercise_name, mapping_id, days, items`); `ProgressionTrendClassification = 'PROGREDINDO'|'ESTAVEL'|'ESTAGNADO'|'REGREDINDO'|'DADOS_INSUFICIENTES'`; `DashboardProgressionTrendResponse` (`exercise_name, mapping_id, days, classification, reason, sessions_considered, e1rm_change_pct`); `DashboardProgressionSuggestionResponse` (`mapping_id, reps_target, load_target, rpe_target, progression_note, has_history, progression, last_load_kg, last_reps, trend: 'up'|'down'|'same'|'unknown', last_date`).
- `WorkoutSessionItem` (`activity_id, local_date, name, activity_type, duration_seconds, trained_at, exercises_count, sets_count, avg_rpe, stimulus_equivalent_sets, fatigue_equivalent_sets`); `DashboardWorkoutSessionsResponse`.
- `RecoveryStatusColor`, `RecoveryReadinessStatus`, `RecoveryGuidance`, `RecoveryPhase = 'TREINADO'|'RECUPERANDO'|'READY'`; `DashboardRecoveryItem` (`muscle_group_id, muscle_group, session_dose, hours_since_trained, days_since_trained, recovery_pct, estimated_recovery_hours, remaining_hours, status, guidance, readiness_status, explanation, last_trained_at, last_direct_training_at, last_participation_at, recovery_phase`); `DashboardRecoveryResponse`.
- `CycleSlotStatus = 'done'|'next'|'pending'`; `CycleSlot` (`sequence, name, slot_type, weekday|null, status`); `CycleNextSlot`; `CycleTrainingTypeSource = 'template'|'assigned_template'|'unavailable'`; `CycleCoverageItem` (`muscle_group_id, muscle_group, min/target/max_weekly_sets|null, actual_weekly_sets, projected_weekly_sets, status|null`); `DashboardCycleResponse = {has_template:false; message} | {has_template:true; template_id; template_name; cycle_length_days; slots; next_slot; coverage; training_type_source}`.
- `EngineAuditDetailLevel = 'full'|'partial_v1'|'legacy_targets_only'|'unavailable'`; `EngineAuditVolumeRow` (`muscle_group_id, muscle_group, weekly_target, slot_min, slot_max, slot_target_sets?, generated, deviation_status, recovery_status`); `EngineAuditPipelineStage` (`stage: 'ai_selection'|'volume_validation'|'repair'|'revalidation'|'final_activation'; label; [key]: unknown`); `DashboardEngineAuditResponse` (`plan_id, plan_name, template_id, template_slot_sequence, engine_version, audit_detail_level, generation_summary, volume_table, pipeline, reduced_dose_groups: string[], excluded_due_to_recovery, excluded_due_to_duplicate, adjusted_due_to_catalog_mismatch, excluded_due_to_no_equipment, insufficient_target_volume_groups: unknown[]`).

##### `cardio-dashboard-types.ts`

`CardioClassification` (`included_cardio_examples, excluded_strength_examples: string[]`); `CardioTotals` (`activities, strength_excluded, ignored_other, minutes, distance_km, calories, training_load; avg_hr_bpm, max_hr_bpm, avg_intensity|null`); `CardioDailyPoint` (`date, activities, minutes, distanceKm, calories, load; avgIntensity, avgHr, maxHr|null`); `CardioByType` (`type, activities, minutes, distanceKm, calories, load, avgHr, maxHr`); `CardioWellnessPoint` (`date, fitness, fatigue, form, restingHr, hrv, rampRate`); `CardioActivity` (`external_id, activity_date, start_time, name, sport_type; moving_time_seconds, distance_meters, calories_kcal, avg_hr_bpm, max_hr_bpm, training_load, intensity|null`); `CardioDashboard` (`days; range {days, oldest, newest}; source; classification; totals; daily; by_type; wellness; cardio_activities; excluded_strength_activities; ignored_activities`).

##### `insights-types.ts`

`InsightsGenerateRequest = { question: string; context?: string }`; `InsightsGenerateResponse = { model: string; insight: string }`.

##### `intervals-types.ts`

`IntervalsStatus` (`configured, message`); `IntervalsSyncResult` (`status; range; intervals {activities_received, wellness_received}; fitness_api {activities {received, upserted, skipped}; wellness {…}}`); `IntervalsDailyActivity` (`date, activities, minutes, calories, distanceKm, load, avgHr|null`); `IntervalsWellnessTrendPoint` (`date, fitness|null, fatigue|null, form|null, restingHr|null, hrv|null, rampRate|null`); `IntervalsRecentActivity` (`date, name, sport, minutes, calories, avgHr|null, load|null`); `IntervalsDashboard` (`range, activity_count, wellness_count, daily_activity, wellness_trend, recent_activities`). `sumBy(items, selector)`; `averageOf(values)` → média dos números finitos **maiores que 0**, ou `null`.

##### `reports-types.ts`

`ReportStatus = 'generating'|'done'|'failed'`; `ReportSummary` (`id, user_id, status, period_days; requested_at, completed_at, model, pdf_path, error_message, created_at: string|null`); `ReportDetail = ReportSummary & { context_snapshot }`; `ReportsListResponse`; `GenerateReportPayload = { start_date? }`; `GenerateReportResponse = { id, job_id, status }`.

##### `gyms-types.ts`

`ApiListResponse<T> = { items: T[] }`; `Gym` (`id, owner_user_id|null, name, usual_weekdays: number[]|null, is_recurring, notes?, opens_at|null, closes_at|null, has_logo, logo_url|null, is_active, created_at, updated_at`); `Equipment` (`id, name, notes?, equipment_type_id?, manufacturer?, model?, location?, has_photo, photo_url|null, is_active, updated_at`); `GymEquipmentCatalogLink` (`id, gym_id, gym_name, equipment_id, equipment_name, has_photo, photo_url, is_active, created_at, updated_at`); `RecommendItem` (`id, mapping_id, equipment_id, equipment_name, photo_url, mapping_exercise_name, mapping_amazfit_name, primary_group, last_used_date, last_used_at, mapping_aliases: string[]`); `RecommendGroup` (`has_equipment_for_group, recommended|null, alternatives`); `RecommendAllResponse` (`ok, gym_id, groups: Record<string, RecommendGroup>, groups_by_id?`). `WEEKDAYS` 1 Segunda … 7 Domingo; `weekdayLabels(days)` → `'Sem frequência padrão definida'` ou labels ordenados unidos por `', '`; `toTimeInputValue(v)` → `v.slice(0,5)` ou `''`.

##### `users-admin-types.ts`

`USER_ROLES = ['admin', 'regular']`; `ROLE_OPTIONS` (`admin` "Administrador", `regular` "Usuário comum"); `roleLabel(role)`; `UserAdminItem` (`id, username, full_name, email, birth_date, sex, role, is_active, must_change_password?, intervals_athlete_id, has_photo, intervals_configured`); `UserAdminListResponse`; `UserAdminMutationResponse = {status, item}`; `CreateUserPayload` (`username, password, role, full_name?, email?`); `UpdateUserPayload` (`full_name?, email?, birth_date?, sex?, role?, is_active?`); `SEX_OPTIONS` (`M` Masculino, `F` Feminino, `other` "Outro / prefiro não informar").

##### `training-types-types.ts`

`TrainingTypeListItem` (`id, name, description, rag_folder, is_active, created_by_user_id, created_by_username, created_at, updated_at, muscle_groups_count`); `TrainingTypeTarget` (`muscle_group_id, muscle_group_name, min_weekly_sets, target_weekly_sets, max_weekly_sets`); `TrainingTypeDetail` (`id, name, description, rag_folder, is_active, created_by_user_id, created_at, updated_at, targets`); `TrainingTypeTargetPayload`; `TrainingTypePayload` (`name, description, rag_folder, is_active, targets`); `MuscleGroupOption` (`id, name, primary_muscles, secondary_muscles`); `TrainingTypeRagFoldersResponse = { items: string[] }`.

##### `equipment-exercise-mappings-types.ts`

`EquipmentExerciseMapping` (`id, equipment_id, equipment_name, has_photo, photo_url, mapping_id, mapping_exercise_name, mapping_amazfit_name, primary_group, secondary_group, is_active, created_at, updated_at`); `MuscleGroupEquipmentItem` (`equipment_id, equipment_name, exercise_count, max_stimulus`); `MuscleGroupEquipmentResponse` (`muscle_group_id, items`); `EquipmentExerciseMappingCreateResponse` (`status:'ok'; item {id, equipment_id, mapping_id, is_active: number|boolean, created_at, updated_at}`); `EquipmentExerciseMappingDeleteResponse` (`status, id, is_active:false`); `EquipmentCreateOrUpdateResponse` (`status; item {id, name, notes, equipment_type_id, manufacturer, model, location, has_photo, photo_url, is_active, updated_at}`); `EquipmentDeleteResponse`; `EquipmentPhotoDeleteResponse` (`status, id, has_photo:false`).

##### `exercise-catalog-admin-types.ts`

`ExerciseCatalogItem` (`id, name, body_position, movement, trajectory, biomechanical_pattern, primary_joint, resistance_vector, keywords, description: string|null; is_active, is_unilateral: boolean|number; is_compound: boolean|number|null; created_at?, updated_at?`); `ExerciseListResponse` (`query, active_only, limit, offset, total, items`); `ExerciseUpsertPayload` (todos opcionais); `ExerciseUpsertResponse`; `ExerciseAlias` (`id, exercise_id, alias, source, created_at`); `ExerciseAliasCreatePayload` (`alias, source?`); create/delete responses; `ExerciseExternalMapping` (`id, exercise_id, provider, external_id, external_name, mapping_status, source, notes, is_active, created_at, updated_at`); `ExerciseExternalMappingCreatePayload` (`provider, external_id?, external_name, mapping_status?, source?, notes?, is_active?`); `ExerciseExternalMappingUpdatePayload = Partial<…>`; upsert/delete responses; `ExerciseExternalMappingFlatItem` (+ `exercise_name, exercise_is_active`); `ExerciseExternalMappingFlatListResponse` (`query, provider, active_only, limit, offset, total, items`); `ExerciseExternalMappingProviderCount` (`provider, count`) e response; `ExerciseMuscleContribution` (`id, exercise_id, muscle_group_id, muscle_group_name, role: 'PRIMARY'|'SECONDARY'|'STABILIZER', stimulus_weight, fatigue_weight, is_active`); `ExerciseEquipmentLink` (`id, equipment_id, equipment_name, photo_url, is_preferred, is_active, notes`); `ExerciseDetailResponse` (`exercise, aliases, external_mappings, muscle_contributions, equipment_links, legacy_ids_merged: number[]`); `MuscleContributionUpsertPayload` (`role, stimulus_weight, fatigue_weight`); upsert/deactivate responses; `MuscleGroupOption` (`id, name`); `MuscleGroupItem` (`id, name, primary_muscles, secondary_muscles, created_at?, updated_at?`); `MuscleGroupListResponse`; `MuscleGroupUpsertPayload`; `MuscleGroupUpsertResponse`; `EquipmentType` (`id, code, name, description`); `EquipmentTypeUpsertPayload`; upsert/delete responses.

##### `mappings-types.ts`

`MappingCanonicalHealthResponse`, `MappingCanonicalMatch`, `MappingCanonicalResolveResponse`, `MappingCanonicalReclassifyResponse` (`ok, error?, processed, updated, unresolved, mapping_count, examples, unresolved_examples`), `MappingCanonicalOptionItem` (`id, exercise_name, amazfit_name, primary_group, secondary_group, primary_muscles, secondary_muscles`), `MappingCanonicalOptionsResponse` (`ok, source, items`), `MappingCanonicalAssociateRequest` (`alias_name, target_mapping_id`), `MappingCanonicalAssociateResponse` (`ok, error?, operation?: 'created'|'updated', alias_name?, alias_mapping_id?, target_mapping_id?, target_exercise_name?, target_amazfit_name?, target_primary_group?, reclassification?`). Reexporta `UnmappedExerciseItem`/`UnmappedExercisesResponse`. Só `MappingCanonicalOptionsResponse` é importado por telas (`/dashboard/progressao`, `/equipamentos-exercicios`, `ExercisesTable`).

##### `muscle-groups-types.ts`

`MuscleGroupTrendItem`/`MuscleGroupsTrendResponse` (endpoint `/analytics/muscle-groups-trend`, não importado por telas); `ExerciseMappingCatalogItem` (`id, exercise_name, amazfit_name, equipment, body_position, movement, trajectory, biomechanical_pattern, primary_joint, resistance_vector, primary_group_id, primary_group, secondary_group_id, secondary_group, keywords, description, mapping_status, source, is_active, created_at?, updated_at?`); `ExerciseMappingCatalogListResponse` (`query, primary_group, active_only, limit, offset, total, items`); `ExerciseMappingCatalogGroupItem` (`primary_group, count`) e response; mutation/deactivate responses; `ExerciseMappingCatalogPayload` (`exercise_name, amazfit_name` + opcionais); `ExerciseMappingCatalogExportResponse` (`markdown, count`); `UnmappedExerciseItem` (`id, original_name`); `UnmappedExercisesResponse` (`ok, rows`); `MuscleGroupsMappedPeriod = 'daily'|'weekly'|'monthly'|'quarterly'|'yearly'`; `MuscleGroupsMappedMetric = 'sets'|'volume'|'reps'|'load'`; `MappedUnmappedRow` (`exercise, sets`); `MuscleGroupsMappedTrendRow` (`period, group, muscle_group, value, sets, exercise_count, exercises, source`); `MuscleGroupsMappedTableRow` (`group, muscle_group, value, sets, period_count, exercise_count, exercises, source`); `MuscleGroupsMappedPayload` (`ok, source, metric, period, days, total_sets, mapped_sets, mapped_ratio, unmapped, trend_rows, table_rows`); `CanonicalMappingOption`/`CanonicalMappingOptionsResponse`/`CanonicalAssociateRequest`/`CanonicalAssociateResponse` (mesmos campos das versões `MappingCanonical*`).

##### `physical-indicators-types.ts`

`BodyCompositionEntry` (`id, local_date, measured_at?, weight_kg?, bmi?, body_fat_percent?, fat_mass_kg?, fat_free_mass_kg?, muscle_mass_kg?, skeletal_muscle_percent?, body_water_percent?, visceral_fat_rating?, bone_mass_kg?, bmr_kcal?, subcutaneous_fat_percent?, protein_percent?, metabolic_age?, source?, notes?`; numéricos como `number|string|null`); `BodyCompositionFormState = Omit<…,'id'|'source'>`; `emptyBodyCompositionForm()` (data `todayLocalDate()`, demais `null`); `IntervalsWellnessEntry` (`wellness_date, fitness_ctl, fatigue_atl, form_tsb, ramp_rate, weight_kg, resting_hr_bpm, hrv_ms, sleep_seconds, readiness_score, daily_effort_load, synced_at?`); `IntervalsWellnessResponse` (`days, limit, items`); `SleepMetricsEntry` (`id, local_date, duration_hours?, regularity_percent?, deep_sleep_hours?, rem_sleep_hours?, awake_count?, awake_hours?, notes?`); `SLEEP_TIME_FIELDS`; `SleepFormState` (horas como string `hh:mm`); `emptySleepForm`, `sleepEntryToFormState`, `sleepFormToApiPayload`; `ManualEffortEntry` (`id, local_date, epoc_training_load?, pai_score?, notes?`); `ManualEffortFormState`; `emptyManualEffortForm`.

##### `training-volume-types.ts`

`RecentExerciseVolumeItem` (`activity_id, local_date, muscle_group, equipment_name, sets_count, volume_kg, avg_rpe, last_weight_kg`); `RecentExerciseVolumeResponse` (`limit, date_from, date_to, items`); `WorkoutPeriodKey = 't3'|'t2'|'t1'|'t0'`; `WorkoutPeriodMetrics` (`activity_id, local_date, volume_kg, avg_rpe`); `MuscleGroupComparativeRow` (`muscle_group; byPeriod: Record<WorkoutPeriodKey, WorkoutPeriodMetrics|null>`); `TrendDirection = 'up'|'down'|'stable'|'none'`; `EquivalentSetsByActivityRow` (`activity_id, local_date, muscle_group, equivalent_sets`); `EquivalentSetsByActivityResponse`; `WorkoutSetsMetrics` (`sets_count`); `ComparativeSetsRow`; `MuscleGroupWeeklyComparativeRow = MuscleGroupComparativeRow`; `WeekSetMetrics` (`sets_count, last_weight, volume`); `EquipmentComparativeRow` (`muscle_group, equipment_label, isTotal, isFirstOfGroup, groupSize, byWeek`); `TrainingVolumeGranularity = 'd7'|'week'|'month'`; `TrainingVolumeOverviewItem`; `TrainingVolumeTrendItem` (`period, muscle_group, sets_count, reps_total, volume_kg, avg_weight_kg?, avg_rpe?`); `TrainingVolumeResponse` (`overview {days, items}; trend {days, items}`); `HeatmapViewMode = 'session'|'week'|'month'`; `MuscleHeatmapCell` (`muscle_group, sets_count, avg_weight_kg, volume_kg`).

##### `workout-log-types.ts`

`ApiList<T>`; `conductedByOptions` (`personal` "Personal trainer", `dougfitness` "DougFitness (ficha da IA)"); `WorkoutListItem` (`id, local_date, activity_type, name, duration_seconds?, calories_kcal?, avg_hr_bpm?, max_hr_bpm?, avg_rpe?, notes?, gym_id?, gym_name?, training_type_id?, training_type_name?, conducted_by?, session_status?, workout_plan_id?, workout_plan_name?, total_volume_kg?`); `Gym`/`GymResponse` (`id, name`); `TrainingType`/`TrainingTypeResponse`; `WorkoutPlanListItem` (`id, name, plan_date?`) e response; `WorkoutHeaderForm` (strings `name, local_date, started_at_time, gym_id, training_type_id, conducted_by, workout_plan_id`); `sessionStatusLabels` (`in_progress` "Em andamento"/yellow, `done` "Finalizado"/green); `ExerciseSetRow`; `ExerciseLogRow` (`id, activity_id, sequence, original_name, mapping_id?, mapped_name?, muscle_primary?, muscle_secondary?, equipment_id?, equipment_catalog_id?, equipment_name?, equipment_has_photo?, equipment_photo_url?, workout_plan_exercise_id?, planned_mapping_id?, planned_exercise_name?, exercise_description?, muscle_pain?, joint_pain?, sets_count?, volume_kg?, avg_rpe?, sets`); `ActivityDetail` (`id, source_id?, external_id?, local_date, started_at?, ended_at?, activity_type, name, notes?, gym_id?, gym_name?, training_type_id?, training_type_name?, conducted_by?, duration_seconds?, calories_kcal?, avg_hr_bpm?, max_hr_bpm?, avg_rpe?, session_status?, workout_plan_id?, workout_plan_name?, total_volume_kg?, ai_summary?, ai_summary_generated_at?, exercises`); `MappingItem`/`MappingResponse`; `MuscleGroupOption` (`primary_group, count`)/`MuscleGroupsResponse`; `GymEquipmentLink` (`id, gym_id, gym_name?, mapping_id, mapping_exercise_name?, mapping_amazfit_name?, primary_group?, secondary_group?, equipment_id, equipment_name, has_photo?, photo_url?, is_active?`)/`GymEquipmentResponse`; `WorkoutPlanExercise` (com os 10 campos avançados); `WorkoutPlanDetail` (`id, name, plan_date?, plan_type?, gym_id?, gym_name?, exercises`); `TrainingTypeWeeklyTarget` (`muscle_group_id, muscle_group_name, sets_count, max_weekly_sets?`); `MuscleActivationResponse` (`activity_id, items, training_type_targets?`); `ExerciseFormState`.

##### `workout-plans-types.ts`

`WORKOUT_PLAN_STATUS_OPTIONS` (`planned` Planejado, `completed` Concluído, `skipped` Pulado); `PLAN_TYPE_OPTIONS` (`full_body` "Full Body (IA)", `abc` "ABC por alças musculares"); `SPLIT_DAY_OPTIONS` (`''` "Automático (próxima letra do ciclo)", A, B, C). `ExerciseProgression = 'increased'|'decreased'|'same'|null`; `WorkoutPlanExercise` (`workout_plan_exercise_id?, sequence, exercise_name, amazfit_name?, mapping_id?, preferred_equipment_id?, muscle_group_id?, muscle_primary?, muscle_secondary?, equipment_name?, sets_count?, reps_target?, load_target?, rpe_target?, rest_seconds?, warmup?, substitution_options?, notes?, exercise_description?, progression?, suggested_load_kg?, suggested_reps?, progression_note?` + `advanced_technique?, advanced_technique_applications?, advanced_technique_duration_seconds?, warmup_sets_count?, warmup_reps?, warmup_rest_seconds?, feeler_set_sets_count?, feeler_set_reps?, feeler_set_rest_seconds?, peak_contraction_seconds?`); `ExerciseCardSummary` (`url, thumbnail_url, version` — card ilustrado publicado) e `WorkoutPlanExercise.exercise_card?: ExerciseCardSummary | null` (read-only, nunca vai no save; `ExerciseBriefingResponse.exercise.exercise_card` usa o mesmo tipo); `WorkoutPlanExerciseInput` (sem os campos avançados, `warmup: boolean` obrigatório); `WorkoutPlanListItem` (`id, plan_date, plan_time, gym_id, gym_name, name, plan_type, split_day, goal, status, created_at, updated_at, exercises_count`); `WorkoutPlan` (+ `template_id, template_slot_sequence, raw_payload, exercises`); `WorkoutPlanInput`; `WorkoutPlanGenerateInput` (`gym_id?, gym_name?, plan_date?, plan_time?, plan_type, split_day?, goal?, min_rest_seconds, max_rest_seconds, template_id?, template_slot_sequence?`); `InsufficientTargetVolumeGroup`; `WorkoutPlanDraft` (`name, plan_type, split_day?, goal?, generation_summary?, template_id?, template_slot_sequence?, template_slot_targets?, exercises, excluded_due_to_recovery?, excluded_due_to_duplicate?, adjusted_due_to_catalog_mismatch?, adjusted_due_to_repeat_equipment?, excluded_due_to_no_equipment?, reduced_dose_groups?, insufficient_target_volume_groups?, plan_date?, gym_id?, gym_name?, raw_payload?`); `MuscleActivationPreviewInput`; `SuggestedProgressionResponse` (`reps_target, load_target, rpe_target, progression_note, has_history, progression?`). O payload enviado em `POST`/`PUT /workout-plans` pelas telas não inclui os campos avançados dos exercícios.

##### `workout-plan-templates-types.ts`

`WorkoutPlanTemplateItemKind = 'muscle_group'|'cardio'|'equipment'`; `AdvancedTechnique` (8 literais); `WorkoutPlanTemplateSlotItem` (`id?, slot_id?, item_order, item_kind, muscle_group_id, mapping_id, equipment_id, sets_count, reps_target, rpe_target, prioritize, cardio_duration_minutes, priority_min_pct, priority_max_pct, advanced_technique, advanced_technique_applications (1–20), advanced_technique_duration_seconds (1–600), warmup_sets_count (1–10), warmup_reps (1–100), warmup_rest_seconds (1–600), feeler_set_sets_count (1–10), feeler_set_reps (1–100), feeler_set_rest_seconds (1–600), peak_contraction_seconds (1–60)` — faixas registradas em comentários); `WorkoutPlanTemplateSlotType = 'normal'|'descanso'`; `WorkoutPlanTemplateSlot`; `WorkoutPlanTemplateAssignment`; `VolumeMode = 'percent_budget'|'equipment_series'`; `WorkoutPlanTemplateListItem`; `VolumeCoverageStatus = 'UNDER_ALLOCATED'|'BALANCED'|'OVER_ALLOCATED'`; `VolumeCoverageWarning` (`muscle_group_id, muscle_group_name, sum_pct_min, sum_pct_max, status`); `WorkoutPlanTemplateDetail` (+ `slots, assignments, volume_coverage_warnings?`); payloads de item/slot/template; `UserLookupItem`; `WorkoutPlanTemplateAssignPayload`; `AssignedTemplateSummary` (`id, name, description, template_type, slots {sequence, name, slot_type}[]`); `PreviewActivationPayload` (`slots, volume_mode?`); `PreviewActivationGroup`; `PreviewActivationDay` (`day, slot_sequence, slot_name, slot_type, groups`); `PreviewActivationResponse` (`cycle_length, days, message?`). Constantes: `TEMPLATE_TYPE_OPTIONS` (`full_body` "Full Body", `abc` "ABC (alças musculares)"), `SLOT_TYPE_OPTIONS`, `ITEM_KIND_OPTIONS` (só `muscle_group` e `cardio`), `VOLUME_MODE_OPTIONS`, `VOLUME_MODE_DESCRIPTION`, `ADVANCED_TECHNIQUE_OPTIONS` (Rest-pause, Drop-set, FST-7, GVT (German Volume Training), Repetições forçadas, Negativas, Parciais, Cheat reps), `WEEKDAY_OPTIONS` ('1'..'7'), `VOLUME_COVERAGE_STATUS_LABEL`, `VOLUME_COVERAGE_STATUS_COLOR` (gray/green/orange).

##### `workouts-analysis-types.ts`

`WorkoutsAnalysisGenerateRequest = { days? }`; `LatestWorkoutSummary`; `NextWorkoutSummary`; `WeeklySummary` (`completed_workouts, planned_workouts, total_sets, total_volume_kg, avg_rpe, high_rpe_sets, latest_workouts, next_workout`); `VolumeOverviewItem` (campos de volume, dose, participação, séries diretas/indiretas/estabilizadoras, equivalentes de estímulo/fadiga, `factors: unknown`, `activity_dose_history: unknown`); `MuscleRecoveryItem = VolumeOverviewItem & { recovery_pct, estimated_recovery_hours, remaining_hours, status, guidance, readiness_status }` (tipo homônimo ao de `analytics-types.ts`, definido separadamente); `ProgressionHighlight`; `IntervalsMatchedWorkout`; `IntervalsWellnessRow`; `IntervalsWellnessAverage`; `IntervalsCorrelation` (`available, matched_workouts, unmatched_workouts_count, wellness_by_training_day, wellness_avg_training_days, wellness_avg_rest_days`); `WeeklyTrendItem`; `WeeklyTrendByMuscleGroupItem`; `RecentSessionExercise`; `RecentSession`; `WorkoutsAnalysisTraining` (`days, weekly_summary, volume_overview, progression_highlights, recovery, intervals_correlation, weekly_trend, weekly_trend_by_muscle_group, sessions`); `WorkoutsAnalysisGenerateResponse` (`model, insight, training`); `TrainingEvolutionResponse = WorkoutsAnalysisTraining`.

##### `workout-mode-types.ts`

Reexporta tipos de `workout-log-types` e `workout-plans-types`. Próprios: `WorkoutModeEquipmentOverride` (`mapping_id, equipment_link_id, exercise_name, equipment_name`); `WarmupState`; `AdvancedLocalStageProgress` (`warmupDone, feelerDone, techniqueSkipped`); `MidExerciseExtra` (`localId` negativo, `exercise_name, mapping_id, equipment_link_id, equipment_name, muscle_primary?, muscle_secondary?, sets_count, reps_target?, load_target?, rpe_target?, rest_seconds?, originalWorkoutPlanExerciseId|null`); `StoredWorkoutModeSession` (`session_id; source: 'plan'|'freeform'; workout_plan_id?; gym_id?; conducted_by?; skipped_exercise_ids?; cancelled_exercise_ids?; jump_target_exercise_id?; equipment_overrides?; warmup_state?; mid_exercise_extras?; advanced_stage_progress?; advanced_mode?`); `SetEvaluationResult` (`model, category, note, raw_text, suggested_next_reps?, suggested_next_load_kg?, suggested_next_rpe?`); `PreviousSetInput`; `SetEvaluationInput`; `ExerciseSummaryResult` (`model, summary`); `WorkoutAiSummaryResult` (`summary, generated_at`); `SuggestedProgressionResponse`; `SuggestedSetCountForSubstitutionResponse` (`suggested_sets_count|null, reason: 'no_contribution_to_original_primary_group'|null, original_primary_muscle_group, original_stimulus_weight, new_stimulus_weight, original_sets_count`); `PreviousSessionSet`; `LastSessionSetsResponse` (`item {activity_id, local_date, sets}|null`); `GymEquipmentRecommendItem`; `GymEquipmentRecommendResponse` (`ok, gym_id, primary_group, has_equipment_for_group, recommended, alternatives, items`); `WorkoutModePlanExercise`. Helpers: `buildPreviousSessionSetsMap`, `parseNumericTarget` (troca `,` por `.`, primeiro número por `/\d+(\.\d+)?/`), `isSyntheticPlanExerciseId` (`id < 0`), `findLoggedExerciseForPlanItem` (por `workout_plan_exercise_id` real, senão por `mapping_id`), `buildMuscleMapItems` (primário `intensity 2`, secundário `intensity 1` se diferente), `MUSCLE_MAP_COLORS = ['#facc15', '#ef4444']`, `MUSCLE_MAP_LEGEND` (`#ef4444` Principal, `#facc15` Secundário).

---

### 4. Matriz tela × endpoint

Legenda de "Evento": `load` = `useQuery` disparada ao montar (ou quando `enabled` fica verdadeiro); `mutação` = `useMutation` disparada por ação do usuário; `polling` = refetch periódico; `derivado` = query dependente de outra seleção. Todos os paths são relativos a `API_BASE_URL`. Em todas as chamadas o método padrão do `apiFetch` é `GET` quando não indicado.

#### 4.1 Autenticação e shell

| Tela/Componente | Chamada | Evento |
|---|---|---|
| `/` (`LoginClient`) | `GET /auth/me` | load (para pular login se já houver sessão) |
| `/` (`LoginClient`) | `POST /auth/login` body `{username, password}` (via `login()`, `fetch` direto) | submit do form |
| `(app)/layout.tsx`, `(fullscreen)/layout.tsx` | `GET /auth/me` | load (guarda de rota) |
| `AppShellLayout` | `GET /auth/me` (query `['current-user']`) | load |
| `AppShellLayout` | `POST /auth/logout` | clique em "Sair" |

#### 4.2 Dashboards

| Tela | Chamada | Evento |
|---|---|---|
| `/dashboard` | `GET /analytics/summary?days={days}` | load; `days` ∈ {30,90,180,365}, default `'90'` |
| `/dashboard` | `GET /analytics/training-trend?period=week&days={days}` | load |
| `/dashboard` | `GET /analytics/muscle-groups-table?days={days}` | load |
| `/dashboard` | `GET /analytics/rpe-distribution?days={days}` | load |
| `/dashboard/treinos` | `GET /analytics/dashboard-workout-sessions?days={days}` | load; `days` ∈ {30,90,180}, default 90 (se `?days` da URL não estiver na lista) |
| `/dashboard/progressao` | `GET /mapping-canonical/options` | load (`staleTime` 5 min) |
| `/dashboard/progressao` | `GET /analytics/dashboard-progression?mapping_id={id}&days={days}` | derivado (`enabled: mappingId != null`) |
| `/dashboard/progressao` | `GET /analytics/dashboard-progression-trend?mapping_id={id}&days={days}` | derivado |
| `/dashboard/progressao` | `GET /analytics/dashboard-progression-suggestion?mapping_id={id}` | derivado; `retry: false` |
| `/dashboard/recovery` | `GET /analytics/dashboard-recovery?days={days}` | load |
| `/dashboard/musculos` | `GET /analytics/dashboard-muscle-status?days={days}[&training_type_id={id}]` | load |
| `/dashboard/musculos` | `GET /analytics/dashboard-muscle-drilldown?muscle_group_id={id}&days={days}` | derivado (clique na linha) |
| `/dashboard/musculos` | `GET /analytics/dashboard-stimulus-fatigue?muscle_group_id={id}&days={days}` | derivado (clique na linha) |
| `/dashboard/musculos`, `/recuperacao-muscular`, `MuscleMapDisclosure` | `GET /analytics/muscle-group-body-map` (dentro de `MuscleBodyMap`) | load (`staleTime` 5 min) |
| `/dashboard/ciclo` | `GET /analytics/dashboard-cycle` | load (sem parâmetros) |
| `/dashboard/engine` | `GET /workout-plans?limit=50` | load |
| `/dashboard/engine` | `GET /analytics/dashboard-engine-audit?plan_id={planId}` | derivado (`?planId` na URL) |

#### 4.3 Registro de exercícios

| Tela | Chamada | Evento |
|---|---|---|
| `/registro-exercicios` | `GET /gyms?active_only=true` | load |
| `/registro-exercicios` | `GET /activities?limit=200[&q=][&date_from=YYYY-MM-DD][&gym_id=][&conducted_by=]` | load / a cada mudança de filtro |
| `/registro-exercicios` | `POST /workout-sessions/start` body `{name, source_name:'registro-manual', local_date, started_at, gym_id, training_type_id, conducted_by, workout_plan_id}` | mutação (modal "Novo treino"); em sucesso `router.push('/registro-exercicios/{session_id}')` |
| `/registro-exercicios` | `DELETE /activities/{id}` | mutação (modal de confirmação) |
| `NewWorkoutModal` (usado aqui e no detalhe) | `GET /gyms?active_only=true`, `GET /workout-plans?status=planned&limit=100`, `GET /training-types?is_active=true` | load quando `opened` |
| `/registro-exercicios/[activityId]` | `GET /activities/{id}` | load (`enabled: isValidId`) |
| idem | `GET /analytics/muscle-activation-by-activity?activity_id={id}` | load |
| idem | `GET /exercise-mapping-catalog?query=&limit=500&active_only=true` | load |
| idem | `GET /exercise-mapping-catalog/groups` | load |
| idem | `GET /gym-equipment?gym_id={gym}&primary_group={grupo}&active_only=true` | derivado (após escolher grupo) |
| idem | `GET /workout-plans/{workout_plan_id}` | derivado (`enabled: Boolean(activity.workout_plan_id)`) |
| idem | `PUT /exercises/logs/{exerciseId}` body `{local_date, started_at, original_name, mapping_id, mapped_name, muscle_primary, muscle_secondary, gym_id, conducted_by, equipment_id, workout_plan_exercise_id, muscle_pain, joint_pain, sets[]}` | mutação (editar exercício) |
| idem | `POST /workout-sessions/{id}/exercises` body `{original_name, mapping_id, equipment_id, workout_plan_exercise_id, muscle_pain, joint_pain}` seguido de N × `POST /workout-sessions/{id}/exercises/{exercise_id}/sets` body `{set_number, reps, weight_kg, rpe}` | mutação (lançar exercício; uma chamada por série, sequencial) |
| idem | `DELETE /exercises/logs/{exerciseId}` | mutação |
| idem | `PATCH /workout-sessions/{id}/meta` body `{name, local_date, started_at, gym_id, training_type_id, conducted_by, workout_plan_id}` | mutação (editar cabeçalho) |
| idem | `POST /workout-sessions/{id}/finish` body `'{}'` | mutação ("Finalizar treino") |

#### 4.4 Modo Treino (`/modo-treino`)

| Componente | Chamada | Evento |
|---|---|---|
| `ModeSelectScreen` | `GET /gyms?active_only=true` | load |
| `ModeSelectScreen` | `GET /activities?session_status=in_progress&limit=1` | load quando `subMode === 'select'` |
| `ModeSelectScreen` | `POST /workout-plans/generate` body `{gym_id, gym_name, plan_type, split_day, goal, plan_date, min_rest_seconds, max_rest_seconds}` → `{job_id}` | mutação ("Gerar ficha") |
| `ModeSelectScreen` | `GET /jobs/{job_id}` | polling `refetchInterval` 2000 ms enquanto `status === 'pending'`; sem teto |
| `ModeSelectScreen` | `POST /workout-plans` (salvar rascunho) e depois `POST /workout-sessions/start` body `{name, workout_plan_id, gym_id, gym_name}` | mutação ("Confirmar e começar treino") |
| `ModeSelectScreen` | `GET /workout-plans?status=planned&limit=50` | load quando `subMode === 'existing-list'` |
| `ModeSelectScreen` | `GET /workout-plans?status=planned&has_advanced_technique=true&limit=50` | load quando `subMode === 'advanced-list'` |
| `ModeSelectScreen` | `POST /workout-sessions/start` body `{name, workout_plan_id, gym_id}` | mutação (ficha existente / avançada) |
| `ModeSelectScreen` | `POST /workout-sessions/start` body `{name:'Treino avulso', gym_id, gym_name, conducted_by}` | mutação (treino avulso) |
| `PlanExecutionScreen` | `GET /activities/{session_id}` | load, `refetchOnMount: 'always'`; invalidada após cada série |
| `PlanExecutionScreen` | `GET /workout-plans/{workout_plan_id}` | load |
| `PlanExecutionScreen` | `GET /gym-equipment?gym_id={gym}&active_only=true` | load (`enabled: gym_id`) |
| `PlanExecutionScreen` | `GET /exercises/suggested-set-count-for-substitution?original_mapping_id=&original_sets_count=&new_mapping_id=` | derivado, uma query por override ativo (`useQueries`) |
| `PlanExecutionScreen` | `GET /exercises/suggested-progression?mapping_id={id}` | derivado (só com override ativo); também chamado imperativamente no fluxo "trocar equipamento (séries restantes)" |
| `PlanExecutionScreen`, `FreeformScreen` | `GET /exercises/last-session-sets?mapping_id={id}&exclude_activity_id={session}[&equipment_id={link}]` | derivado |
| `PlanExecutionScreen` | `POST /workout-sessions/{id}/exercises` body `{original_name, mapping_id, equipment_id, workout_plan_exercise_id}` | mutação (só na 1ª série de cada exercício) |
| `PlanExecutionScreen`, `FreeformScreen` | `POST /workout-sessions/{id}/exercises/{exercise_id}/sets` body `{set_number, set_type?, reps, weight_kg, rpe, rest_seconds, muscle_pain, joint_pain, notes}` | mutação (cada série) |
| `PlanExecutionScreen`, `FreeformScreen` | `POST /exercises/evaluate-set` → 202 `{job_id}` → `GET /jobs/{id}` | após cada série, só se `perSetAiEnabled`; polling 500 ms, teto 20 s |
| `PlanExecutionScreen`, `FreeformScreen` | `POST /exercises/{exercise_id}/summarize` → 202 → `GET /jobs/{id}` | após a última série do exercício (plan) / ao encerrar exercício (freeform); polling 500 ms, teto 20 s |
| `PlanExecutionScreen`, `FreeformScreen` | `POST /workout-sessions/{id}/finish` body `'{}'` | mutação ("Encerrar treino") |
| `WorkoutModeClient` | `POST /activities/{session_id}/ai-summary/generate` → 202 → `GET /jobs/{id}` | após `onFinish`; polling 500 ms, teto 20 s |
| `FreeformScreen` | `GET /exercise-mapping-catalog/groups` | load |
| `FreeformScreen` | `GET /gym-equipment?gym_id={gym}&primary_group={grupo}&active_only=true` | derivado |
| `FreeformScreen` | `GET /exercises/suggested-progression?mapping_id={id}` (`.catch(() => null)`) e `POST /workout-sessions/{id}/exercises` body `{original_name, mapping_id, equipment_id}` | mutação (escolher exercício) |
| `EquipmentSubstituteModal` | `GET /gym-equipment?gym_id={gym}&primary_group={grupo}&active_only=true` | load quando aberto |
| `EquipmentSubstituteModal` | `GET /gym-equipment/recommend?gym_id={gym}&mapping_id={id}[&exclude_equipment_id={link}]` | `enabled: false`; `refetch()` no botão "Sugerir substituto" |
| `PlanExecutionScreen` (`useEffectiveExerciseCard`) | `GET /exercises/{id}/card` | derivado, só quando a troca mudou o EXERCÍCIO (override/exercício já lançado/continuação sintética com `mapping_id` diferente do item); `staleTime` 30 min, `retry: false` (erro = sem card). Sem troca, o card vem de `exercise_card` do `GET /workout-plans/{id}` (zero requisição) |
| `PlanExecutionScreen` | miniaturas `exercise_card.thumbnail_url` (CDN, `new Image()`) | ao chegar a ficha, em segundo plano (§66 da especificação); a imagem cheia só baixa ao abrir o "Como executar" |
| `ExerciseBriefingScreen` | `GET /workout-plans/{plan_id}/exercises/{wpe_id}/briefing` | load (`staleTime` 5 min); `exercise.exercise_card` alimenta a miniatura |

#### 4.5 Recuperação, cardio, insights, indicadores

| Tela | Chamada | Evento |
|---|---|---|
| `/recuperacao-muscular` | `GET /analytics/muscle-recovery?days={days}` | load; `days` ∈ {7,14,30,90}, default `'14'`; `refetchInterval: 60_000`, `refetchIntervalInBackground: true` |
| `/agente-cardio` | `GET /cardio-recommendations?limit=200` | load |
| `/agente-cardio` | `GET /analytics/cardio-agent-context?days={days}` | load; `days` ∈ {7,14,30}, default `'14'` |
| `/agente-cardio` | `POST /cardio-recommendations/generate` body `{days}` → `{job_id}` | mutação |
| `/agente-cardio` | `GET /jobs/{job_id}` | polling 2000 ms enquanto `pending`; sem teto; ao `done` invalida `['cardio-recommendations-history']` |
| `/agente-cardio` | `DELETE /cardio-recommendations/{id}` | mutação (modal) |
| `/insights` | `GET /analytics/summary?days=`, `GET /analytics/training-trend?period=day&days=`, `GET /analytics/muscle-groups?days=`, `GET /analytics/rpe-distribution?days=` (em `Promise.all`) | mutação ("Carregar dados analíticos"); resultado vira `JSON.stringify(..., null, 2)` no textarea de contexto |
| `/insights` | `POST /insights/generate` body `{question, context}` → `dispatchAndPollJob` (`GET /jobs/{id}`, 2000 ms, teto 90 s) | mutação ("Gerar insight") |
| `/cardio` | `GET /integrations/intervals/status` | load |
| `/cardio` | `GET /integrations/intervals/cardio-dashboard?days={days}` | derivado (`enabled: configured`); `days` ∈ {30,90,180,365,1095}, default `'90'` |
| `/intervals-icu` | `GET /integrations/intervals/status` | load |
| `/intervals-icu` | `GET /integrations/intervals/dashboard?days={days}` | derivado (`enabled: configured`); mesmas opções de `days` |
| `/intervals-icu` | `POST /integrations/intervals/sync?days={days}` (síncrono) | mutação ("Sincronizar"); invalida `['intervals-icu-dashboard']` e `['intervals-wellness']` |
| `/indicadores-fisicos` (Bioimpedância) | `GET /body-composition`; `POST /body-composition`; `PUT /body-composition/{id}`; `DELETE /body-composition/{id}` | load / mutações |
| `/indicadores-fisicos` (Recuperação) | `GET /integrations/intervals/wellness?days={days}&limit={min(days*2,1000)}` | load; `days` ∈ {30,90,180,365}, default `'90'` |
| `/indicadores-fisicos` (Sono) | `GET /sleep-metrics`; `POST /sleep-metrics`; `PUT /sleep-metrics/{id}`; `DELETE /sleep-metrics/{id}` | load / mutações (payload via `sleepFormToApiPayload`) |
| `/indicadores-fisicos` (Esforço) | `GET /manual-effort-metrics`; `POST /manual-effort-metrics`; `PUT /manual-effort-metrics/{id}`; `DELETE /manual-effort-metrics/{id}` | load / mutações |
| `/evolucao-corporal` | `GET /analytics/body-composition-trend?days={days}` | load; `days` ∈ {7,30,90,180,365}, default `'90'` |
| `/exercicios` | `GET /analytics/exercise-progress?name={encodeURIComponent(query)}&days=1095` | load; `query` inicial `'Puxada'`; `enabled: query.trim().length > 0` |

#### 4.6 Grupos musculares, volume, treinos, relatórios

| Tela | Chamada | Evento |
|---|---|---|
| `/grupos-musculares` | `GET /analytics/muscle-groups-mapped-trend?period=&metric=&days=&include_secondary=&secondary_factor=0.5` | load; defaults `period='weekly'`, `metric='volume'`, `days='180'`, `include_secondary=false` |
| `/grupos-musculares` | `GET /analytics/muscle-groups-mapped-table?…` (mesmos params) | load |
| `/grupos-musculares` | `GET /exercise-mapping-catalog/groups` | load |
| `/grupos-musculares` | `GET /exercise-mapping-catalog?query=&primary_group=&active_only=&limit=200` | load / filtros |
| `/grupos-musculares` | `POST /exercise-mapping-catalog`; `PUT /exercise-mapping-catalog/{id}`; `DELETE /exercise-mapping-catalog/{id}` | mutações |
| `/grupos-musculares` | `GET /exercise-mapping-catalog/export/markdown` | mutação ("Exportar Markdown"; gera `Blob` `text/markdown` e clica em `<a download="mapeamentos-amazfit-zepp.md">`) |
| `/grupos-musculares` | `GET /mapping-canonical/options` | load quando o modal de associação abre |
| `/grupos-musculares` | `POST /mapping-canonical/associate` body `{alias_name, target_mapping_id}` | mutação; lança `Error` se `response.ok === false` |
| `/volume-grupamentos` | `GET /analytics/muscle-groups-recent-exercise-volume?limit=400` | load |
| `/volume-grupamentos` | `GET /analytics/muscle-groups-equivalent-sets-by-activity?limit=400` | load |
| `/volume-grupamentos` | `GET /analytics/training-volume?days={14|21|70}` | load; `days` por granularidade (`d7`→14, `week`→21, `month`→70) |
| `/volume-grupamentos` | `GET /analytics/muscle-groups-recent-exercise-volume?date_from=&date_to=` | derivado (heatmap em modo `week`/`month`) |
| `/treinos` | `GET /analytics/training-evolution?days={days}` | load; `days` ∈ {30,60,90,180}, default `'90'` |
| `/treinos` | `GET /analytics/training-trend?period=week&days={days}` | load |
| `/treinos` | `GET /analytics/rpe-distribution?days={days}` | load |
| `/treinos` | `GET /analytics/muscle-groups-daily-activation?days=7` | load (fixo 7) |
| `/treinos` | `GET /analytics/muscle-recovery-consecutive-check?days=7` | load (fixo 7) |
| `/treinos` | `POST /workouts-analysis/generate` body `{days}` → `dispatchAndPollJob` (2000 ms, teto 90 s) | mutação ("Gerar análise") |
| `/relatorios` | `GET /reports` | load |
| `/relatorios` | `POST /reports/generate` body `{start_date?}` → `{id, job_id, status}` | mutação |
| `/relatorios` | `GET /reports/{id}` | polling `refetchInterval` 5000 ms enquanto `status === 'generating'`; sem teto; ao sair de `generating` invalida `['reports']` |
| `/relatorios` | `GET /reports/{id}/pdf` via `apiFetchBlob` | clique em "PDF" (cria Object URL, `<a download="relatorio-{id}.pdf">`, `click()`, `revokeObjectURL`) |
| `/relatorios` | `DELETE /reports/{id}` | mutação (modal) |

#### 4.7 Planos de treino

| Tela | Chamada | Evento |
|---|---|---|
| `/planos-treino` | `GET /workout-plans?limit=100` | load |
| `/planos-treino` | `DELETE /workout-plans/{id}` | mutação |
| `GenerateDraftModal` | `GET /gyms?active_only=true` | load quando aberto |
| `GenerateDraftModal` | `GET /workout-plan-templates/assigned-to-me` | load quando aberto |
| `GenerateDraftModal` | `POST /workout-plans/generate` body `{gym_id, plan_date, plan_time, plan_type, split_day, goal, min_rest_seconds, max_rest_seconds, template_id, template_slot_sequence}` → `{job_id}` | mutação |
| `GenerateDraftModal` | `GET /jobs/{job_id}` | polling 2000 ms enquanto `pending`; sem teto |
| `GenerateDraftModal` | `POST /workout-plans` body `WorkoutPlanInput` (com `raw_payload` composto no cliente) | mutação ("Salvar ficha"); em sucesso `router.push('/planos-treino/{id}')` |
| `ExercisesTable` (rascunho e detalhe) | `GET /mapping-canonical/options` | load (`staleTime` 5 min) |
| `ExercisesTable` | `POST /analytics/muscle-activation-breakdown` body `{exercises: {mapping_id, sets_count}[]}` | derivado, `useDebouncedValue` 500 ms sobre o payload serializado; `enabled` se houver algum `mapping_id` com `sets_count > 0` |
| `ExercisesTable` | `GET /exercises/suggested-progression?mapping_id={id}` | imperativo ao selecionar exercício no `Autocomplete` (`onOptionSubmit`) |
| `MuscleActivationPreview` | `POST /analytics/muscle-activation-preview` body `{exercises}` (só itens com `mapping_id`) | derivado |
| `/planos-treino/[id]` | `GET /workout-plans/{id}` | load (`enabled: Number.isFinite(planId)`) |
| `/planos-treino/[id]` | `GET /gyms?active_only=true` | load |
| `/planos-treino/[id]` | `PUT /workout-plans/{id}` body `WorkoutPlanInput` (`raw_payload` = o já salvo) | mutação ("Salvar alterações"); invalida `['plan-muscle-map', id]` |
| `/planos-treino/[id]` | `GET /cardio-recommendations?workout_plan_id={id}&limit=1` | load |
| `/planos-treino/[id]` | `POST /cardio-recommendations/generate` body `{days: 14, proposed_workout, reference_date, workout_plan_id}` → `dispatchAndPollJob` (2000 ms, 90 s) | mutação |
| `/planos-treino/[id]` | `DELETE /cardio-recommendations/{id}` | mutação |
| `/planos-treino/[id]` | `DELETE /workout-plans/{id}` | mutação; em sucesso `router.push('/planos-treino')` |
| `PlanMuscleMap` | `GET /workout-plans/{id}/muscle-map.png` (`fetch` com `credentials: 'include'`, blob) | load (`staleTime: Infinity`) |

#### 4.8 Cadastros (academias, equipamentos, domínio, tipos, modelos, usuários, perfil)

| Tela | Chamada | Evento |
|---|---|---|
| `/academias` | `GET /gyms?active_only=true`; `GET /equipment?active_only=true` | load |
| `/academias` | `GET /gym-equipment-catalog?gym_id={id}&active_only=true`; `GET /gym-equipment/recommend-all?gym_id={id}` | derivado (academia ativa = selecionada ou primeira da lista) |
| `/academias` | `POST /gyms` body `{name, usual_weekdays, is_recurring}`; `PUT /gyms/{id}` body `{usual_weekdays}` / `{is_recurring}` / `{name, opens_at, closes_at}`; `DELETE /gyms/{id}` | mutações |
| `/academias` | `POST /gyms/{id}/logo` (FormData `file`); `DELETE /gyms/{id}/logo` | mutações |
| `/academias` | `POST /gym-equipment-catalog` body `{gym_id, equipment_id}`; `DELETE /gym-equipment-catalog/{id}` | mutações (checkbox) |
| `/equipamentos-exercicios` | `GET /equipment?active_only=true`; `GET /equipment?active_only=true&query={termo}` (debounce 300 ms) | load / busca |
| `/equipamentos-exercicios` | `GET /equipment-exercise-mappings?active_only=true[&equipment_id=][&primary_group=]` (duas queries: filtrada e sem filtro) | load |
| `/equipamentos-exercicios` | `GET /mapping-canonical/options[?q=]` | load quando o modal de nova associação abre |
| `/equipamentos-exercicios` | `POST /equipment-exercise-mappings` body `{equipment_id, mapping_id}`; `DELETE /equipment-exercise-mappings/{id}` | mutações |
| `/equipamentos-exercicios` (em massa) | `GET /muscle-groups`; `GET /muscle-groups/{id}/equipment`; `GET /exercise-mapping-catalog?active_only=true&limit=500&primary_group={nome}`; `GET /equipment-exercise-mappings?active_only=true&equipment_id={id}` | load / derivados |
| `/equipamentos-exercicios` (em massa) | N × `POST /equipment-exercise-mappings` e M × `DELETE /equipment-exercise-mappings/{id}` sequenciais, com progresso `done/total` | mutação ("Salvar (N)") |
| `/equipamentos-exercicios` (catálogo) | `GET /equipment-types`; `POST /equipment` / `PUT /equipment/{id}` body `{name, notes, equipment_type_id, manufacturer, model, location}`; `DELETE /equipment/{id}`; `POST /equipment/{id}/photo` (FormData); `DELETE /equipment/{id}/photo` | load / mutações |
| `/equipamentos-exercicios` (popover de apelidos) | `GET /exercises/{id}/aliases`; `POST /exercises/{id}/aliases` body `{alias, source:'manual'}`; `DELETE /exercises/aliases/{aliasId}` | load quando aberto / mutações |
| `/dominio-exercicios` (Exercícios) | `GET /exercises?query=&active_only=&limit=500`; `POST /exercises` body `{name}`; `GET /exercises/{id}`; `PUT /exercises/{id}` | load / mutações |
| `/dominio-exercicios` (drawer) | `POST /exercises/{id}/aliases`; `DELETE /exercises/aliases/{id}`; `POST /exercises/{id}/external-mappings`; `PUT /exercises/external-mappings/{id}`; `DELETE /exercises/external-mappings/{id}`; `GET /muscle-groups`; `PUT /muscle-contributions/{exerciseId}/{muscleGroupId}` body `{role, stimulus_weight, fatigue_weight}`; `DELETE /muscle-contributions/{exerciseId}/{muscleGroupId}`; `GET /equipment?active_only=true`; `POST /equipment-exercise-mappings`; `GET /equipment-exercise-mappings?equipment_id=&mapping_id=&active_only=true` seguido de `DELETE /equipment-exercise-mappings/{id}` | mutações |
| `/dominio-exercicios` (Mapeamentos externos) | `GET /exercise-external-mappings?query=&active_only=&limit=50&offset=[&provider=]`; `GET /exercise-external-mappings/providers`; `GET /exercises?query=&active_only=true&limit=200` (select do modal); `POST /exercises/{id}/external-mappings`; `PUT`/`DELETE /exercises/external-mappings/{id}` | load / mutações |
| `/dominio-exercicios` (Grupos musculares) | `GET /muscle-groups`; `POST /muscle-groups`; `PUT /muscle-groups/{id}` | load / mutações (sem DELETE) |
| `/dominio-exercicios` (Tipos de equipamento) | `GET /equipment-types`; `POST /equipment-types`; `PUT /equipment-types/{id}`; `DELETE /equipment-types/{id}` | load / mutações |
| `/tipos-treinamento` | `GET /training-types` (após `['current-user']`); `PATCH /training-types/{id}/status` body `{is_active}`; `POST /training-types/{id}/duplicate`; `DELETE /training-types/{id}` | load / mutações |
| `/tipos-treinamento/[id]` | `GET /training-types/{id}`; `GET /muscle-groups`; `GET /training-types/rag-folders`; `POST /training-types` ou `PUT /training-types/{id}` body `TrainingTypePayload` | load / mutação |
| `/modelos-treino` | `GET /workout-plan-templates` (após `['current-user']`); `PATCH /workout-plan-templates/{id}/status`; `POST /workout-plan-templates/{id}/duplicate`; `DELETE /workout-plan-templates/{id}` | load / mutações |
| `AssignTemplateModal` | `GET /workout-plan-templates/{id}/assignments`; `GET /workout-plan-templates/users-lookup?search=`; N × `POST /workout-plan-templates/{id}/assignments` body `{user_id}`; `DELETE /workout-plan-templates/{id}/assignments/{user_id}` | load quando aberto / mutações |
| `/modelos-treino/[id]` | `GET /workout-plan-templates/{id}`; `GET /muscle-groups`; `GET /training-types`; `POST /workout-plan-templates` ou `PUT /workout-plan-templates/{id}` | load / mutação |
| `WeeklyActivationPreviewTable` (2 instâncias) | `POST /workout-plan-templates/preview-activation` body `{slots, volume_mode}` | derivado, `useDebouncedValue` 400 ms; `enabled` se houver slot não-descanso com itens |
| `EquipmentSeriesItemCard` | `GET /equipment-exercise-mappings?active_only=true&primary_group={nome}` | derivado (por grupo do item) |
| `/usuarios` | `GET /users` (após `['current-user']`, `retry: false`); `POST /users`; `PUT /users/{id}`; `DELETE /users/{id}`; `POST /users/{id}/photo` (FormData); `DELETE /users/{id}/photo` | load / mutações |
| `UserAvatar` | `GET /users/{id}/photo` (blob, `fetchUserPhotoUrl`) | load se `hasPhoto` |
| `/perfil` | `GET /users/me`; `PUT /users/me` body `{full_name, email, birth_date, sex, intervals_athlete_id, intervals_api_key}`; `GET /users/{id}/photo` (via `apiFetchBlob`); `POST /users/{id}/photo` (FormData); `POST /auth/change-password` body `{current_password, new_password}` | load / mutações |

#### 4.9 Resumo do fluxo 202 + `job_id`

| Tela | Endpoint que retorna 202 | Como faz polling | Intervalo | Parada | Timeout |
|---|---|---|---|---|---|
| `/agente-cardio` | `POST /cardio-recommendations/generate` | `useQuery(['cardio-job', jobId])` com `refetchInterval` | 2000 ms | `status !== 'pending'` (`done`/`failed`) | nenhum |
| `GenerateDraftModal` (`/planos-treino`) | `POST /workout-plans/generate` | `useQuery(['plan-generation-job', jobId])` | 2000 ms | idem | nenhum |
| `ModeSelectScreen` (`/modo-treino`) | `POST /workout-plans/generate` | `useQuery(['workout-mode-plan-generation-job', jobId])` | 2000 ms | idem | nenhum |
| `/insights` | `POST /insights/generate` | `dispatchAndPollJob` | 2000 ms | `done` retorna `result`; `failed` lança | 90 000 ms |
| `/treinos` | `POST /workouts-analysis/generate` | `dispatchAndPollJob` | 2000 ms | idem | 90 000 ms |
| `/planos-treino/[id]` | `POST /cardio-recommendations/generate` | `dispatchAndPollJob` | 2000 ms | idem | 90 000 ms |
| `/modo-treino` | `POST /exercises/evaluate-set`, `POST /exercises/{id}/summarize`, `POST /activities/{id}/ai-summary/generate` | `fetchWorkoutAiResult` | 500 ms | idem | 20 000 ms |
| `/relatorios` | `POST /reports/generate` (retorna `{id, job_id, status:'generating'}`) | `useQuery(['report-status', id])` em `GET /reports/{id}` (não usa `/jobs`) | 5000 ms | `status !== 'generating'` | nenhum |

#### 4.10 Endpoints binários / arquivos

| Recurso | Como é carregado |
|---|---|
| Foto de equipamento | `<img src={photo_url}>` direto (URL pública CloudFront) em `EquipmentPhoto` e nos `<img>` do modo treino |
| Logo de academia | `<img src={logo_url}>` direto em `GymLogo` |
| Foto de usuário (próprio) | `apiFetchBlob('/users/{id}/photo')` → Object URL, revogada no cleanup do efeito |
| Foto de usuário (admin) | `fetchUserPhotoUrl(id, version)` → Object URL cacheada em memória |
| Mapa muscular do plano | `fetchPlanMuscleMapUrl(planId)` → Object URL cacheada em memória |
| PDF do relatório | `apiFetchBlob('/reports/{id}/pdf')` → `<a download>` temporário |
| Markdown do catálogo | `GET /exercise-mapping-catalog/export/markdown` (JSON `{markdown}`) → `Blob` local |

---

### 5. Componentes

#### 5.1 `src/components` (compartilhados)

| Arquivo | Linhas | Responsabilidade | Props | Estado interno / dados | Usa |
|---|---|---|---|---|---|
| `AppShellLayout.tsx` | 443 | Shell autenticado (header + navbar + `AppShell.Main`), menu descrito em 2.5, logout | `children` | `useDisclosure` (navbar mobile), `useQuery(['current-user'])` | `AppShell`, `Burger`, `Image`, `Menu`, `NavLink`, `ScrollArea`, `Avatar` |
| `EquipmentPhoto.tsx` | 38 | Miniatura de foto de equipamento; `<img>` com `objectFit: 'cover'`, `borderRadius: 8`; sem `photoUrl` renderiza `IconPhoto` (`size - 4`) | `photoUrl?: string\|null`, `alt: string`, `size = 32` | nenhum (sem fetch, sem cache manual) | — |
| `GymLogo.tsx` | 28 | Idem para logo de academia; placeholder `IconBuildingWarehouse` | `logoUrl?`, `alt`, `size = 32` | nenhum | — |
| `MuscleActivationTable.tsx` | 154 | Tabela "Grupo Muscular / Ativação primária / Secundária / Estabilizadora / Total / [Meta] / Avaliação" | `rows: MuscleActivationRow[]`, `isLoading?`, `isError?`, `emptyMessage?`, `errorMessage?`, `targetSetsByGroup?: Record<string, MuscleActivationTarget>\|null` | nenhum; regras em 6.4 | `Table.ScrollContainer minWidth={600}`, `ThemeIcon`, `Tooltip` |
| `MuscleBodyMap.tsx` | 119 | Mapa corporal v1 (arte aprovada em 2026-10-03, `public/bodymap/v1/`): frente e costas lado a lado (até 480 px), cada região destacada tingida em `colors[intensity - 1]`; legenda; texto do(s) item(ns) da região tocada | `items: MuscleMapEntry[]` (`muscle_group, intensity, tooltip`), `colors: string[]`, `bodyColor?` (ignorado: a arte já tem cor), `legend: {color,label}[]` | `selectedSlug`; `useQuery(['muscle-group-body-map'])` → `GET /analytics/muscle-group-body-map` para mapear grupo → `slugs` | `bodymap/BodyMapFigure.tsx` (SVG: `clipPath` + `feColorMatrix`), `bodymap/bodymap.ts` (lógica pura), `bodymap/regions.v1.ts` (gerado por `scripts/generate-bodymap-regions.mjs`) |
| `NewWorkoutModal.tsx` | 226 | Modal de criação/edição de cabeçalho de treino | `opened`, `onClose`, `onSubmit(form: WorkoutHeaderForm): Promise<void>`, `isSubmitting?`, `errorMessage?`, `title = 'Novo treino'`, `submitLabel = 'Criar treino'`, `initialValues?` | `form` (reinicializado ao abrir com `initialValues` ou `defaultHeaderForm()` — nome `Treino dd/mm/aaaa`, data de hoje em UTC); 3 `useQuery` habilitadas quando `opened` (gyms, planos `planned`, training types ativos); `useMediaQuery('(max-width: 48em)')` → `fullScreen` | Campos: Nome (obrigatório), Data (`type="date"`), Hora (`type="time"`), Academia, Conduzido por, Tipo de treinamento, Ficha planejada; `canSubmit = name && local_date` |
| `UserAvatar.tsx` | 35 | `Avatar` com foto autenticada ou `IconUser` | `userId`, `hasPhoto?`, `version = 0`, `size = 32`, `alt = ''` | `useQuery(['user-photo', userId, version], enabled: userId && hasPhoto, staleTime: Infinity)` → `fetchUserPhotoUrl` | — |
| `charts/chartTheme.ts` | 43 | Tokens para recharts | — | `CHART_GRID_STROKE = BRAND.border`; `CHART_AXIS_TICK = { fill: BRAND.textMuted, fontSize: 12 }`; `CHART_TOOLTIP_CONTENT_STYLE = { background: BRAND.bgSurface, border: '1px solid #2A2A2A', borderRadius: 12, color: BRAND.textPrimary, fontSize: 13 }`; `CHART_TOOLTIP_LABEL_STYLE`; `CHART_TOOLTIP_ITEM_STYLE`; `CHART_COLORS = { primary: '#FF9500', secondary: '#E8451C', compare: '#8B93A6', positive: '#2f9e44', warning: '#FF9500', negative: '#E8451C' }`; `CHART_LEGEND_STYLE = { color: BRAND.textMuted, fontSize: 12 }` | — |
| `dashboard/DashboardPeriodSelect.tsx` | 39 | `Select` de período (`allowDeselect={false}`, `w={190}`) | `value`, `onChange(days)`, `allowed = DASHBOARD_DAYS_OPTIONS`, `label = 'Período'`, `width = 190` | — | `dashboardDaysSelectData` |
| `dashboard/MuscleStatusBadge.tsx` | 98 | `MuscleStatusBadge` (Badge por status) e `MuscleVolumeBar` (`Progress` + texto `realizado / alvo (máx X)`) | `status`; `actual, min, target, max, status` | exporta `MUSCLE_STATUS_ORDER`, `MUSCLE_STATUS_LABELS`, `MUSCLE_STATUS_HEX = ['#e03131','#f08c00','#2f9e44','#0ca678','#f76707']`, `MUSCLE_STATUS_LEGEND`; cores Mantine `red/orange/green/teal/orange` | — |
| `dashboard/RecoveryPhaseTimeline.tsx` | 59 | `Progress` de `hoursSinceTrained / estimatedRecoveryHours` + texto `Treinado → Recuperando → Pronto` (fase atual em maiúsculas) + `faltam Xh`/`concluída` | `phase`, `hoursSinceTrained`, `estimatedRecoveryHours`, `remainingHours`, `status` | — | — |
| `dashboard/TrendBadge.tsx` | 60 | Seta ↑/↓/–/⊘ da sessão (Progrediu/Regrediu/Estável/Não comparável) com tooltip das razões | `evolution` (`ProgressionSessionEvolution\|null`) | status vem pronto do backend (`compare_exposures`); `null` → "—"; cores `CHART_COLORS.positive/negative`, `BRAND.textMuted` | — |

#### 5.2 Componentes co-localizados por tela

##### `/dashboard` e sub-rotas

- `dashboard/page.tsx` (`DashboardPage`): `MetricTile` interno (Paper com título/valor/subtítulo/ícone); 4 tiles ("Treinos", "Volume total", "RPE médio", "RPE alto" com subtítulo "Séries com RPE >= 9"); `LineChart` volume semanal (`dataKey="volume"`, `CHART_COLORS.primary`) e RPE médio (`domain={[0,10]}`, `CHART_COLORS.secondary`); tabela de tendência; `BarChart` volume por grupo (top 8, eixo X rotacionado `-25`) + tabela agregada no cliente (6.1); `BarChart` distribuição de RPE (`CHART_COLORS.secondary`); tabela "Últimas atividades".
- `DashboardTreinosClient`: tabela "Timeline de sessões" (`minWidth 860`) + blocos "Comparação por treino" (agrupamento por `name` no cliente, só grupos com ≥ 2 sessões, ordenados por tamanho desc; dentro do grupo ASC por `local_date` e `activity_id`); `TrendBadge` sobre `stimulus_equivalent_sets`.
- `DashboardProgressaoClient`: `Select` grupo (local, derivado de `primary_group` das opções), `Select` exercício (`?exerciseMappingId`, `?exerciseName` na URL), `DashboardPeriodSelect`; veredito com `StatusBadge domain="exerciseProgression"` (mesmo status da home/drill-down), razões via `statusReasonsText` e linha "Comparado em: equipamento · convenção · N de M exposições"; `LineChart` e1RM × melhor carga (`unit=" kg"`); tabela com `TrendBadge` de `items[].evolution`.
- `DashboardRecoveryClient`: `STATUS_META` (`green` "Pronto", `yellow` "Recuperando", `red` "Evitar"); tabela com `RecoveryPhaseTimeline`; `Math.round(recovery_pct)%`; badge com tooltip = `explanation.join('\n')`.
- `DashboardMusculosClient`: tabela clicável (linha selecionada com `background: BRAND.bgSurface`), `MuscleStatusBadge`, `MuscleVolumeBar`, `MuscleBodyMap` (`intensity = MUSCLE_STATUS_ORDER.indexOf(status) + 1`, cores `MUSCLE_STATUS_HEX`); painel de detalhe com prontidão, última progressão, top exercícios e tabela estímulo × fadiga.
- `DashboardCicloClient`: `SlotCard` (Paper `flex: '1 1 170px'`, borda laranja quando `status === 'next'`); `SLOT_STATUS_META` (`done` green "Concluído", `next` orange "Próximo", `pending` gray "Pendente"); `WEEKDAY_LABELS` 1..7; tabela de cobertura com `MuscleStatusBadge`.
- `DashboardEngineClient`: `Select` de plano (`?planId`), `DETAIL_LEVEL_META` (4 níveis com cor/label/hint), tabela de volume (colunas variam com `audit_detail_level === 'full'`), `Timeline` do pipeline (`stageDetails` extrai `actions`/`warnings`), `ExclusionList` para 5 listas.

##### `/registro-exercicios` e detalhe

- `registro-exercicios/page.tsx`: filtros Buscar / Período (`all,7,14,30,90`) / Academia / Condutor; paginação client-side `PAGE_SIZE = 20` sobre lote de `FETCH_LIMIT = 200`; reset de página em `useEffect` ao mudar filtros; modal de exclusão (`fullScreen` no mobile); `NewWorkoutModal`.
- `WorkoutDetailClient` (953 linhas): `ActivitySummary` (nome, data + hora via `extractTime`, academia, condutor, badges de status/ficha, carga total, RPE médio, nº exercícios); card "Resumo do treino (IA)" se `ai_summary`; card "Exercícios da ficha vinculada" com badge "Já lançado" (match por `workout_plan_exercise_id` ou `mapping_id`) e botão "Lançar este" (`prefillFromPlanExercise`); formulário em 3 passos (Grupo muscular → Equipamento → Exercício canônico) com `equipmentOptions` deduplicados por `equipment_id` e `exerciseOptionsForEquipment` deduplicados por nome; `Select` "Associar a exercício da ficha"; `NumberInput` dor muscular 0–10 e `Switch` dor articular; lista dinâmica de séries (`reps` 1–100, `weight_kg` 0–1000 step 0.5, `rpe` 0–10 step 0.5; default `{reps: 10, weight_kg: 20, rpe: null}`); tabela "Exercícios lançados" com badge "Trocado" quando `planned_mapping_id !== mapping_id`; `MuscleActivationTable` com `targetSetsByGroup` derivado de `training_type_targets` (`{ sets: sets_count, maxSets: max_weekly_sets }`); `canFinish = session_status !== 'done'`.

##### `/modo-treino` (`(fullscreen)/modo-treino`)

| Arquivo | Linhas | Responsabilidade | Props | Estado / regras |
|---|---|---|---|---|
| `WorkoutModeClient.tsx` | 211 | Orquestrador: header sticky (`rgba(10,10,10,0.9)`, `backdropFilter: blur(10px)`, `paddingTop: env(safe-area-inset-top)`), toggle "IA por série" (só com sessão), botão sair (modal), decide `ModeSelectScreen`/`PlanExecutionScreen`/`FreeformScreen` pelo `session.source`; modal "Treino concluído" com resumo de IA | — | `session` (de `loadStoredSession()`), `hydrated`, `perSetAiEnabled` (de `loadPerSetAiPreference()`), `completion {open, loading, error, summary}`; `handleWorkoutFinished(sessionId)`: `clearStoredSession()`, chama `POST /activities/{id}/ai-summary/generate`; ao fechar navega para `/registro-exercicios` |
| `ModeSelectScreen.tsx` | 673 | Tela inicial com 4 cards (Nova ficha com IA / Ficha existente / Treino avulso / Fazer treino avançado) e card "Treino em andamento encontrado" (`GET /activities?session_status=in_progress&limit=1`, botão "Retomar") | `onSessionStarted(session)` | `subMode: 'select'\|'ai-form'\|'ai-draft'\|'existing-list'\|'freeform-setup'\|'advanced-list'`; form IA: academia, tipo (`PLAN_TYPE_OPTIONS`), letra ABC (se `abc`), data, objetivo (se não `abc`), descanso mín/máx (`NumberInput` 15–240 step 5, defaults 30/90); rascunho listado em cards `sequence. nome — setsxreps · carga`; confirmação salva plano (`status: 'planned'`) e inicia sessão; avulso: academia + "Conduzido por"; `advanced_mode: true` no submodo avançado |
| `PlanExecutionScreen.tsx` | 1395 | Execução guiada por ficha (detalhes em 6.7) | `session`, `perSetAiEnabled`, `onFinish(sessionId)` | Estados: `overrides`, `warmup`, `advancedProgress`, `midExerciseExtras`, `skippedIds`, `cancelledIds`, `priorityId`, `resting`, `restIsExerciseTransition`, `extendMode`, `evaluation`, `exerciseSummary`, modais (skip/substitute/finish), `swapRemainingMode`, `swappingEquipment`, `nextSyntheticIdRef` (começa em -1000, decrementa) |
| `FreeformScreen.tsx` | 586 | Treino avulso em fases `pick-group → pick-equipment → pick-exercise → logging → exercise-done`; também usado dentro do fluxo por ficha como "Adicionar exercício avulso" (`title`, `onReturnToPlan`) | `sessionId`, `gymId?`, `perSetAiEnabled`, `onFinish`, `title = 'Treino avulso'`, `onReturnToPlan?` | `loggedSets` local (não relê a atividade), `suggested` (progressão do exercício escolhido), `evaluation`, `exerciseSummary`; `DEFAULT_REST_SECONDS = 60` enviado como `rest_seconds` de cada série; grupos em `SimpleGrid cols={2}`; equipamentos com `EquipmentPhoto size={72}` |
| `SetLoggerCard.tsx` | 665 | Card de registro de série; exporta também `SetEvaluationCard` e `ExerciseSummaryCard` | ver tabela abaixo | `reps`, `weight`, `rpe`, `musclePain`, `jointPain`, `notes`, `moreOptionsOpen`; efeito repopula `reps/weight/rpe` e zera dor/notas quando mudam `exerciseName`, `setLabel`, `defaultReps`, `defaultWeight`, `defaultRpe` |
| `RestTimer.tsx` | 168 | Cronômetro de descanso baseado em timestamp absoluto (`endAtRef`), `setInterval` de 1 s + recálculo em `visibilitychange`; botões ±15 s; "Pular descanso"; ao zerar: `navigator.vibrate([200,100,200])`, beep Web Audio (senoide 880 Hz, ganho 0.2 → 0.001 em 0.35 s) e `onFinish(false)` | `resetKey: string`, `initialSeconds`, `onFinish(skipped: boolean)` | `total`, `remaining`, `finishedRef`; `adjust(delta)` nunca deixa `endAt` no passado; `progressValue = (total - remaining) / total * 100` |
| `EquipmentSubstituteModal.tsx` | 242 | Troca de equipamento/exercício: botão "Sugerir substituto" (`GET /gym-equipment/recommend`, `enabled: false` + `refetch()`) + lista do catálogo da academia filtrada pelo grupo, excluindo `currentEquipmentLinkId` | `opened`, `onClose`, `gymId?`, `mappingId?`, `primaryGroup?`, `currentEquipmentLinkId?`, `onSelect(choice)`, `submitting?` | `fullScreen` no mobile; `EquipmentSubstituteChoice = {mapping_id, equipment_link_id, exercise_name, equipment_name}` |
| `SkipExerciseModal.tsx` | 81 | Modal "Fazer qual exercício agora?" listando pendentes (foto 64×64 ou `IconPhoto`) | `opened`, `onClose`, `items: SkipExerciseModalItem[]`, `onSelect(workoutPlanExerciseId)` | — |
| `FinishExerciseModal.tsx` | 49 | Modal "Encerrar exercício": "Ir para o próximo da lista" e "Trocar de equipamento (séries restantes)"; texto com `remainingSets` | `opened`, `onClose`, `remainingSets`, `onGoToNext`, `onSwapEquipment` | — |
| `NextExercisePreviewCard.tsx` | 129 | Card "A seguir" na transição entre exercícios: foto, nome, badges, descrição, "Planejado: N séries · reps · carga · RPE", botões Continuar / Pular / Cancelar / Encerrar treino | `planExercise`, `equipmentPhotoUrl?`, `equipmentName?`, `onContinue`, `onSkip`, `onCancel`, `onFinish`, `finishing?` | — |
| `MuscleMapDisclosure.tsx` | 39 | `Collapse` (aberto por padrão) com `MuscleBodyMap` usando `MUSCLE_MAP_COLORS`/`MUSCLE_MAP_LEGEND`; título "Conecte-se com os seus músculos" | `items: MuscleMapEntry[]` | retorna `null` se `items` vazio |
| `PhotoLightbox.tsx` | 44 | `usePhotoLightbox()` (`photo`, `open`, `close`) e `PhotoLightbox` (`Modal size="xl"` com `<img maxHeight: 80vh; objectFit: contain>`) | `photo: LightboxPhoto\|null`, `onClose` | também importado por `/equipamentos-exercicios` e `/modelos-treino/[id]` |
| `ExerciseCardViewer.tsx` | 111 | "Como executar" (cards ilustrados, PR5): o card 2:3 INTEIRO do exercício (logo, título, cenas e músculos já compostos na imagem). `Modal fullScreen` no celular (padrão do `EquipmentSubstituteModal`), centrado no desktop (`size="lg"`, imagem ≤ 75vh); `<img>` nativo com `object-fit: contain`, miniatura de fundo no mesmo enquadramento enquanto a cheia carrega; botão "Fechar" grande embaixo (com `env(safe-area-inset-bottom)`); `touch-action: manipulation` (sem zoom por toque duplo no iOS, pinça nativa preservada); fundo `BRAND.bg` | `card: ExerciseCardSummary\|null`, `opened`, `onClose`, `exerciseName`, `withinPortal = true` (false só em teste) | sem `onError`: só abre com `exercise_card` não nulo |
| `ExerciseBriefingScreen.tsx` | 350 | Briefing antes da 1ª série (M4); com card publicado, miniatura clicável (≤ 220 px, "Toque para ampliar") no topo da seção "Como executar", acima do texto de execução, abrindo o `ExerciseCardViewer` | `planId`, `workoutPlanExerciseId`, `exerciseName`, `positionLabel?`, `onStart`, `exerciseCard?` (card do exercício efetivo quando a troca mudou o exercício; ausente = o do briefing) | sem card e sem texto, a seção "Como executar" não aparece |

Props de `SetLoggerCard` (todas listadas): `exerciseName`, `description?`, `equipmentName?`, `equipmentPhotoUrl?`, `muscleGroup?`, `muscleSecondaryGroup?`, `setLabel`, `repsTarget?`, `loadTarget?`, `rpeTarget?`, `defaultReps`, `defaultWeight`, `defaultRpe`, `previousSessionSet?`, `progression?`, `progressionNote?`, `saving`, `saveLabel = 'Salvar série'`, `onSave(values: SetLoggerValues)`, `onSubstituteClick?`, `onFinishExercise?`, `setType?`, `peakContractionSeconds?`, `techniqueDescription?`, `variant = 'default'` (`'default'|'advancedWarmup'|'advancedFeeler'|'advancedDropSet'|'advancedRestPause'|'advancedWorking'`), `onSkip?`, `staticLoadKg?`, `staticRestSeconds?`, `upcomingTechnique?`, `upcomingTechniqueApplications?`, `exerciseCard?` (cards, PR5: com card publicado, `ActionIcon` "Como executar" — `IconInfoCircle`, 44 px, `color="brand"` — no cabeçalho, antes de trocar/encerrar, abrindo o `ExerciseCardViewer`; sem card, nenhum ícone nem espaço reservado. Os botões do cabeçalho ficam num `Group gap={8}` para o nome do exercício manter a largura).

Variantes de renderização do `SetLoggerCard`:

- `advancedWarmup` / `advancedFeeler`: título grande colorido (`orange.6` com `IconFlame` / `blue.6` com `IconTool`), `SteppedField` Repetições (step 1, min 1) e Carga (step `WEIGHT_DRAG_STEP_KG` = 0.25, `decimalScale 2`, sufixo " kg"), botões "Concluir" (verde, h 64) e "Pular série" (amarelo). `rpe` enviado como `null`.
- `advancedDropSet` / `advancedRestPause`: título verde com `IconArrowUpRight`, alerta indigo com `techniqueDescription`, número estático `staticLoadKg` em `fz={56}`, para rest-pause "Pausa: Ns"; botões "Concluir"/"Pular"; envia `reps: defaultReps`, `weight_kg: staticLoadKg ?? defaultWeight`, `rpe: null`.
- `default` / `advancedWorking`: `Badge` com `setLabel`, texto "Alvo: reps · carga · RPE"; em `advancedWorking` badge "Ao final: Drop-set (N drops)" / "Rest-pause (N mini-blocos)" ou badge laranja "Após a falha: N repetições (técnica)" para `EXTRA_REPS_TECHNIQUES`; alerta violeta de pico de contração; alerta de progressão (verde "Carga ajustada para cima…" / vermelho "…para baixo…"); `SteppedField` Reps (step 1, min 0), Carga (step 2.5 no `default`, 0.25 no `advancedWorking`, `decimalScale 2`), RPE (step 0.5, `decimalScale 1`); texto "Última vez (série N): …" se `previousSessionSet`; "Mais opções (dor / anotações)" em `Collapse`; `MuscleMapDisclosure`; botão salvar (`disabled={reps == null || weight == null}`).

`SteppedField`: botões `ActionIcon size={44} radius="xl"` −/+ ao redor de `NumberInput hideControls w={84}`; `onChange(Math.max(min, Number(((value ?? 0) - step).toFixed(2))))`.

`SetEvaluationCard`: rótulo e cor por direção da categoria do motor (`set-engine-status.ts`, JSON gerado: aumentar verde, manter azul, reduzir laranja; desconhecida cinza) e corte de carga separado do de RPE; estados loading ("Avaliando série com IA..."), erro ("Avaliação de IA indisponível para esta série."), resultado (badge `category` + `note`). `ExerciseSummaryCard`: loading "Resumindo exercício com IA...", erro "Resumo de IA indisponível para este exercício.", resultado "Resumo do exercício" + `summary`.

##### `/planos-treino`

| Arquivo | Responsabilidade | Detalhes |
|---|---|---|
| `PlanosTreinoClient.tsx` (196) | Lista de planos (`Data`, `Nome` como link para `/planos-treino/{id}`, `Tipo` + badge `split_day`, `Academia`, `Exercícios`, `Status`, ações) e modal de exclusão | `statusColor`: `completed`→green, `skipped`→gray, senão `brand` |
| `GenerateDraftModal.tsx` (367) | Modal `size="90%"`: formulário de geração → rascunho editável → salvar | Estado: `gymId`, `planType='full_body'`, `splitDay=''`, `planDate`, `planTime`, `goal`, `minRest=30`, `maxRest=90` (`NumberInput` 15–240), `templateId=''`, `templateSlotSequence='__auto__'`, `jobId`, `draft`, `draftName`, `draftExercises`, `status='planned'`. Select de modelo aparece só se `assigned-to-me` retornar itens; com modelo selecionado o "Tipo de plano" fica `disabled`. No salvamento monta `raw_payload = { generation_summary, excluded_due_to_recovery, excluded_due_to_no_equipment, reduced_dose_groups, insufficient_target_volume_groups, adjusted_due_to_catalog_mismatch, generation_context: draft.raw_payload }` |
| `ExercisesTable.tsx` (566) | Tabela editável de exercícios com drag-and-drop (`DragDropContext`/`Droppable droppableId="exercises-table"`/`Draggable draggableId="exercise-{index}"`), setas ↑/↓, `Autocomplete` de exercício (dados de `GET /mapping-canonical/options`, `limit={20}`, filtro por nome/Amazfit/grupo), campos Grupo primário, Séries (`NumberInput` 1–20), Reps, Carga (placeholder `"{suggested_load_kg} kg (sugerido)"`), RPE, Descanso (15–240), Aquec. (checkbox), badges de config. avançada (Aquecimento/blue, Série de ajuste/grape, técnica/orange, Pico de contração/teal), Notas, Progressão (`Carga subiu`/`Carga caiu`/`Mantida`), remover; sub-linha de ativação por exercício (`primary_activation`, secundários, estabilizadores) | `addExercise()` adiciona `{sequence, exercise_name:'', muscle_primary:'', sets_count: 3, reps_target:'', warmup:false}`; `moveExercise` reindexa `sequence` 1..N; `applySuggestedProgression` sobrescreve reps/carga/RPE/nota/progressão a partir de `GET /exercises/suggested-progression` (usa `exercisesRef` para não perder edições concorrentes); digitação livre limpa `mapping_id` quando o texto deixa de bater com o nome canônico vinculado |
| `GenerationWarnings.tsx` (80) | Alertas: resumo da geração (brand), removidos por recuperação (yellow), removidos por falta de equipamento (yellow), dose reduzida (yellow, `join(', ')`), volume-alvo não atingido (orange, `achieved.toFixed(1)/target — reason`) | props `draft: Pick<WorkoutPlanDraft, ...>` |
| `MuscleActivationPreview.tsx` (96) | Tabela de ativação agregada do rascunho (`POST /analytics/muscle-activation-preview`), 1 casa decimal, total em negrito | Só exercícios com `mapping_id` |
| `PlanMuscleMap.tsx` (49) | `<img>` do PNG autenticado do plano; `null` durante loading; placeholder `IconVectorTriangle` + "Mapa muscular indisponível para esta ficha." | `fetchPlanMuscleMapUrl` |
| `[id]/PlanDetailClient.tsx` (443) | Edição de plano salvo: nome, status, data, hora, academia, objetivo; `GenerationWarnings` a partir de `raw_payload`; `ExercisesTable`; `MuscleActivationPreview`; card "Recomendação de cardio" (`ReactMarkdown` sem `components` customizados) com gerar/gerar novamente/excluir; card "Mapa muscular (app do relógio)" com `PlanMuscleMap`; modais de exclusão do plano e da recomendação | `hydrated` evita re-hidratar após salvar; `proposed_workout` = `` `${name}: ${exercícios.join(', ')}` `` |

##### `/academias` (`AcademiasClient.tsx`, 844)

Seções: "Nova academia" (nome, checkbox "Academia avulsa", checkboxes de dias `WEEKDAYS`); grid de academias (`SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}`; card selecionado com borda `BRAND.orange`; badges de dia clicáveis que fazem `PUT /gyms/{id}` com `usual_weekdays`; badge "Avulsa (clique para tornar recorrente)"; ícone editar só para `isAdmin`; ícone desativar para todos); "Equipamentos desta academia" (tabela Possui/Foto/Equipamento com checkbox → `POST`/`DELETE /gym-equipment-catalog`; remoção pede confirmação em modal); "Prévia de variação por grupo muscular" (`recommend-all`: grupo, recomendado, alternativos; badge vermelho "sem equipamento"); modais: desativar academia, editar academia (nome, logo com upload/remoção, "Abre às"/"Fecha às" `type="time"`), remover logo, remover equipamento. Input de arquivo oculto `accept="image/jpeg,image/png,image/webp,image/gif"`.

##### `/equipamentos-exercicios` (`EquipamentosExerciciosClient.tsx`, 1588)

Três `Tabs` (`keepMounted={false}`): **Associações** (filtros por equipamento e grupo primário; tabela Foto/Equipamento/Exercício/Amazfit/Grupo primário/Grupo secundário/Apelidos/remover; `ExerciseAliasesPopover` interno com `Popover width={300}`; modal "Nova associação" com `Select` de equipamento e `Select` de exercício canônico com busca server-side `?q=`); **Associação em massa** (3 painéis: 1. Grupo muscular — lista clicável; 2. Equipamento — busca `useDebouncedValue` 300 ms, badge "já associado" quando o equipamento está em `GET /muscle-groups/{id}/equipment`; 3. Exercícios — checklist com rascunho local `massDraft: Set<number>`, contadores "N a associar, M a remover", botões Descartar/Salvar, progresso `done/total`; efeito reseta o rascunho quando `massLinksSignature`, `massEquipmentId` ou `massGroupId` mudam); **Catálogo de equipamentos** (busca, tabela Foto/Equipamento/Tipo/Fabricante-modelo/Localização/ações; modal criar/editar com Nome, Tipo (`GET /equipment-types`), Fabricante, Modelo, Localização, foto — upload só após salvar; modais remover foto e excluir). `EquipmentPhotoThumb` interno abre `PhotoLightbox` (`cursor: zoom-in`). Botões de escrita ficam `disabled` com tooltip "Ação restrita a administradores" quando `!isAdmin`; erros 403 viram "Ação restrita a administradores.".

##### `/dominio-exercicios`

- `DominioExerciciosClient.tsx` (93): `Tabs defaultValue="exercicios"` com 4 abas: Exercícios (`ExercisesPanel`), Tipos de equipamento (`EquipmentTypesPanel`), Mapeamentos externos (`ExternalMappingsPanel`), Grupos musculares (`MuscleGroupsPanel`); passa `isAdmin = role === 'admin'`.
- `ExercisesPanel.tsx` (260): busca (Enter/botão), `Switch` "Somente ativos" (default `true`), `limit=500`; tabela Nome/Movimento/Padrão biomecânico/Flags (badges "Composto" grape, "Unilateral" cyan)/Status/ação "Editar" ou "Ver"; modal "Novo exercício" (só nome; em sucesso abre o drawer do criado).
- `ExerciseDetailDrawer.tsx` (298): `Drawer position="right" size="lg"`; campos Nome, Posição corporal, Movimento, Trajetória, Padrão biomecânico, Articulação principal, Vetor de resistência, Palavras-chave, Descrição, switches Ativo/Unilateral/Composto (todos `disabled={!isAdmin}`); alerta "Ids legados fundidos"; seções `AliasesSection`, `ExternalMappingsSection`, `MuscleContributionsSection`, `EquipmentLinksSection` (todas recebem `detailQueryKey` para invalidar).
- `AliasesSection.tsx` (150): badges de alias com botão remover; input "Novo apelido" (Enter/Adicionar, `source: 'manual'`); `ConfirmDeleteModal`.
- `ExternalMappingsSection.tsx` (445): lista com edição inline (External ID, Nome externo obrigatório, Status `CONFIRMED`/`PROVISIONAL`/`WEAK_APPROXIMATION`, Fonte, Notas, Ativo); criação inline com `Select` de provider (`PROVIDER_SUGGESTIONS = [{AMAZFIT}]`, `searchable`); `statusColor`: CONFIRMED green, PROVISIONAL yellow, WEAK_APPROXIMATION orange, outro gray; itens inativos com `opacity 0.55`.
- `MuscleContributionsSection.tsx` (344): lista `grupo · badge role · "estímulo X · fadiga Y"`; edição inline só do `role`; adicionar com Select de grupo (exclui já atribuídos), Role, `NumberInput` Estímulo/Fadiga (0–2, step 0.05, 2 casas); `defaultWeightsForRole`: PRIMARY {1,1}, SECONDARY {0.5,0.5}, STABILIZER {0.25,0.25}; `roleColor`: PRIMARY blue, SECONDARY orange, STABILIZER gray.
- `EquipmentLinksSection.tsx` (224): lista com `EquipmentPhoto size={36}`, badge "preferido" (grape); adicionar via Select de equipamento (exclui já vinculados); remoção resolve o `link_id` com `GET /equipment-exercise-mappings?equipment_id=&mapping_id=&active_only=true` antes do `DELETE` (lança `ApiError('Associação não encontrada em equipment-exercise-mappings.', 404)` se não achar).
- `ExternalMappingsPanel.tsx` (654): listagem flat paginada (`PAGE_SIZE = 50`, `offset`, botões ◀ ▶, texto "X–Y de Z"), filtros busca/provider (`GET .../providers` → `"PROVIDER (count)"`)/"Só ativos" (default `false`), edição inline por linha, modal de criação com `Select` de exercício (busca server-side `limit=200`), Provider (texto, enviado em maiúsculas), External ID, Nome externo, Status, Fonte, Notas.
- `MuscleGroupsPanel.tsx` (230): tabela Nome/Músculos primários/Músculos secundários/Editar; modal criar/editar (texto livre); invalida também `['exercise-catalog-admin-muscle-groups']`.
- `EquipmentTypesPanel.tsx` (281): tabela Código/Nome/Descrição/ações; modal com Código (enviado em maiúsculas), Nome, Descrição; `ConfirmDeleteModal`.
- `ConfirmDeleteModal.tsx` (58): `Modal size="sm" centered` com mensagem, alerta de erro (403 → "Ação restrita a administradores."), botões Cancelar/Excluir (vermelho).

##### `/grupos-musculares` (`GruposMuscularesClient.tsx`, 1227)

Controles no topo: Granularidade (`daily|weekly|monthly|quarterly|yearly`, default `weekly`), Métrica (`sets|volume|reps|load`, default `volume`), Janela (30/90/180/365, default 180), checkbox "Incluir grupos secundários" (`secondary_factor` fixo `0.5`). 5 KPIs (Séries analisadas, Séries classificadas, Cobertura canônica `mapped_ratio*100` com 1 casa, Grupos na tabela, `{Métrica} total`). "Tendência por grupo muscular": `MultiSelect` de grupos (vazio = 6 maiores por total acumulado; botão "Mostrar 6 mais pressionados"), `LineChart` uma linha por grupo com `MUSCLE_GROUP_LINE_COLORS = [primary, secondary, '#4DA3FF', '#8B93A6', '#2f9e44', '#B98CFF']` cicladas por índice, tabela "Pressão no período mais recente" (posição, atual, anterior, variação % com badge red se `> 5`, teal se `< -5`, gray senão ou "Sem base"). "Tabela completa por grupo muscular": `BarChart` top 10 + tabela (Grupo, métrica, Séries, Exercícios, Nomes classificados). "Catálogo de mapeamento canônico": alerta amarelo sobre leitura em `exercise_catalog` e escrita em `exercise_zepp_mappings`; busca/grupo/ativos; tabela com Editar/Inativar; "Exportar Markdown"; modal de criação/edição com 13 campos (`exercise_name` e `amazfit_name` obrigatórios). "Exercícios sem classificação canônica" (campo `unmapped` do `-mapped-table`) com botão "Associar" → modal com `Select` de exercício canônico (label `nome · Amazfit: X · grupo`).

##### `/volume-grupamentos` (`VolumeGrupamentosClient.tsx`, 1570)

3 KPIs (Grupos treinados T-0 `x / y`, Volume na última sessão, RPE médio T-0); `BarChart` "Volume por grupo: T-0 vs T-1" (top 10); `LineChart` "Volume total: últimas 4 sessões"; `Tabs` Comparativos / Tendência / Mapa de calor. Comparativos: 4 tabelas (por sessão T-3..T-0 volume+RPE; séries equivalentes T-3..T-0; semanal S-3..S-0; por equipamento expansível por grupo) com `TrendValue` (setas) e `Sparkline` (mini `LineChart` 100×32). Tendência: `Select` granularidade `D-7 | Semana x Semana | Mês x Mês`, `BarChart` repetições atual × anterior, tabela "Variação por grupamento" com badge `Aumentou`/`Diminuiu`/`Estável`. Mapa de calor: `Treemap` do recharts (`dataKey="sets_count"`, `content={<HeatmapCellContent />}`, `Tooltip content={<HeatmapTooltip />}`), modos `session` (Select de sessão), `week` (`type="date"`), `month` (`type="month"`). Regras numéricas em 6.5.

##### `/indicadores-fisicos`

4 seções em `Stack` separadas por `Divider`: `BioimpedanciaSection` (466: 2 `LineChart` — peso/massa muscular em kg e gordura/água em % — tabela com 15 colunas + ações, modal com 15 `NumberInput` + data + observações, `key={editing?.id ?? 'new'}` para resetar o form), `RecoveryMetricsSection` (249: 7 cards de valor mais recente — CTL, ATL, TSB, Rampa, Preparo, Carga diária de esforço, HRV — 2 `LineChart` (CTL/ATL/TSB e HRV), tabela de histórico; somente leitura), `SleepMetricsSection` (317: campos `hh:mm` como texto, tabela, modal), `ManualEffortMetricsSection` (292: EPOC e PAI inteiros, tabela, modal). Todas usam `fullScreen={isMobile}` nos modais.

##### Outras telas autocontidas

- `/agente-cardio` (643): `Select` janela (7/14/30), botão "Gerar recomendação"; 3 painéis de gráfico (`WELLNESS_SERIES` CTL/ATL/TSB com cores `BRAND.orange`/`BRAND.red`/`#2f9e44`; `SLEEP_SERIES` duração/sono profundo; `EFFORT_SERIES` EPOC/PAI), texto "Rampa: X · PAI (manual): Y" do item mais recente; tabela "Recuperação muscular (14 dias)" com `Progress` + ícone de prontidão; resultado gerado em `ReactMarkdown` com `markdownComponents` (h1→Title 3, h2→Title 4, h3→Title 5, p com `whiteSpace: 'pre-wrap'`, ul/ol via `Text component="ul"`); histórico em `Accordion` paginado (`HISTORY_PAGE_SIZE = 20`) com botão excluir e modal de confirmação.
- `/insights` (219): `Select` janela (30/90/180/365, default 90), botão "Carregar dados analíticos", `Textarea` pergunta (default "Analise meu histórico recente de treino e sugira melhorias práticas."), `Textarea` contexto (`minRows={12}`, `fontFamily: monospace`), botão "Gerar insight"; `isUnavailable` quando `ApiError.status === 503` (título "IA indisponível no momento"); resultado em `ReactMarkdown` com `List`/`List.Item`.
- `/cardio` (615): `ActivitiesTable` interno paginado (`PAGE_SIZE = 20`, reset ao mudar lista); 4 KPIs; alerta "Como a classificação cardio funciona"; `LineChart` minutos+carga, `BarChart` minutos por modalidade, `LineChart` fitness/fadiga/forma; tabelas por dia, por modalidade, wellness, atividades; `Accordion` com "Atividades de força excluídas" e "Atividades não classificadas". Sem integração configurada mostra alerta com link para `/perfil`.
- `/intervals-icu` (502): botões "Atualizar" (`refetch`) e "Sincronizar"; alerta de resultado da sincronização; 4 KPIs calculados no cliente (`sumBy`/`averageOf`); 4 `LineChart` (minutos, carga, CTL/ATL/TSB, ramp rate); 3 tabelas.
- `/evolucao-corporal` (277): 4 `MetricTile` (Peso atual, Massa muscular com `formatVelocity(muscle_velocity_per_day, 'kg')`, Gordura corporal, IMC); 2 `LineChart`; tabela.
- `/exercicios` (210): busca (`search` inicial `'Puxada'`, aplicada em Enter/botão); 2 `LineChart` (melhor carga + volume; RPE médio `domain [0,10]`); tabela.
- `/recuperacao-muscular` (398): `Select` janela; alerta de ajuste global por TSB; `BarChart` com `Cell` por status (`RECOVERY_COLORS = [BRAND.red, BRAND.orange, '#2f9e44']`); tabela de 12 colunas (Grupo, Última data, Recuperação, Indicador `Progress`, Horas restantes `HH:MM`, Bom para, RPE médio, Status badge, Leitura, Prontidão ícone, Recomendação, Explicação tooltip); `MuscleBodyMap` com `RECOVERY_LEGEND` (Recuperado/Recuperando/Evitar).
- `/treinos` (1055): KPIs do resumo semanal; `LineChart` "Evolução por semana ISO"; `BarChart` RPE; seção "Cardio & Recuperação (Intervals)" (4 cards + `BarChart` FC média por dia); tabela de recuperação; seção de análise por IA (botão "Gerar análise", `ReactMarkdown`); tabelas do payload da IA (tendência semanal móvel, volume por grupo, destaques de progressão, recuperação snapshot); grade "Ativação muscular (últimos 7 dias)" com ícones por célula (regras em 6.6).
- `/relatorios` (408): `TextInput type="date"` "Data inicial" com `min`/`max` (hoje−365 … hoje−7; default hoje−90), botão "Gerar novo relatório", alertas de progresso/falha, tabela (Solicitado em, Concluído em, Período, Status badge, Modelo, ações PDF/Excluir), modal de exclusão.
- `/perfil` (`PerfilClient.tsx`, 404): `Avatar size={72}` com foto via blob, "Trocar foto" (`accept="image/*"`), form Nome completo/Email/Data de nascimento/Sexo/Athlete ID/API Key (`PasswordInput`, label muda se `intervals_configured`), "Salvar perfil"; card "Trocar senha" (`MIN_PASSWORD_LENGTH = 8`; borda `#F5A623` quando `?troque-senha=1` e ainda não trocou; `scrollIntoView` ao carregar); texto informando que contas só são desativadas por admin. Invalida `['my-profile']` e `['current-user']` ao salvar.
- `/usuarios` (`UsuariosClient.tsx`, 642): guarda local (`!isAdmin || forbidden` → alerta "Acesso restrito a administradores."); tabela com `UserAvatar`, badge "você" para o próprio, papel (`roleLabel`), status Ativo/Desativado, editar/desativar (desativar desabilitado para si e para já desativados); modal criar (username, senha ≥ 8, nome, email, papel default `regular`); modal editar (foto trocar/remover com `photoVersions` para invalidar cache, nome, email, nascimento, sexo, papel e switch "Conta ativa" — ambos `disabled` ao editar a si mesmo; alerta informando que não há reset de senha por admin); modal de desativação.
- `/tipos-treinamento` (`TrainingTypesListClient.tsx`, 267 / `TrainingTypeBuilderClient.tsx`, 412): lista com `Switch` de status, Editar/Duplicar/Excluir; builder com Nome, Ativo, Descrição, "Pasta de RAG" (`GET /training-types/rag-folders`), tabela de metas por grupo (`NumberInput` mín/alvo/máx, min 0), Select "Adicionar grupo muscular..." (exclui já adicionados); validação client-side `0 <= min <= alvo <= máx` (linhas zeradas ignoradas); notificações Mantine em sucesso/erro; após criar faz `router.replace('/tipos-treinamento/{id}')`.
- `/modelos-treino` (`WorkoutPlanTemplatesListClient.tsx`, 329 / `AssignTemplateModal.tsx`, 181 / `WeeklyActivationPreviewTable.tsx`, 173 / `[id]/WorkoutPlanTemplateBuilderClient.tsx`, 1483): lista (Nome/Tipo/Modo badge teal para `equipment_series`/Atribuições/Status switch/Editar-Atribuir-Duplicar-Excluir); builder descrito em 6.8; duas instâncias de `WeeklyActivationPreviewTable` (uma com `metric="primary_equivalent"` e título "Preview de ativação semanal (grupos primários)", outra com o total); `AssignTemplateModal` com `MultiSelect` de usuários (`users-lookup?search=`) e lista de atribuições com "Remover" inline.

---

### 6. Estado e regras de negócio no cliente

Esta seção lista cálculos, limiares, ordenações e defaults implementados no frontend.

#### 6.1 `/dashboard` (visão geral)

- Agregação de `GET /analytics/muscle-groups-table` (uma linha por série) por `muscle_group || 'Sem grupo'`: `sets += 1` quando `set_number != null`; `volume += Number(volume_kg || 0)`; `avgRpe = rpeSum / rpeCount` (só linhas com `rpe != null`, senão `null`). Ordenado por `volume` desc. Gráfico usa os 8 primeiros.
- Gráfico de RPE semanal filtra itens com `avg_rpe != null`; eixo Y `domain={[0, 10]}`.
- `formatKg`: `Math.round(n).toLocaleString('pt-BR') + ' kg'`. `formatNumber(v, digits = 0)` com `minimumFractionDigits = maximumFractionDigits = digits`.
- Default de janela: `'90'`; opções 30/90/180/365.
- Tile "RPE alto": subtítulo literal "Séries com RPE >= 9" (valor vem de `totals.high_rpe_sets`).

#### 6.2 Defaults de janelas e listas

| Tela | Opções | Default |
|---|---|---|
| `/dashboard` | 30, 90, 180, 365 | 90 |
| `/dashboard/*` (exceto treinos/ciclo/engine) | 7, 14, 30, 90, 180 (`?days`) | 14 |
| `/dashboard/treinos` | 30, 90, 180 | 90 |
| `/recuperacao-muscular` | 7, 14, 30, 90 | 14 |
| `/agente-cardio` | 7, 14, 30 | 14 (seção de recuperação sempre rotulada "14 dias") |
| `/insights` | 30, 90, 180, 365 | 90 |
| `/cardio`, `/intervals-icu` | 30, 90, 180, 365, 1095 | 90 |
| `/indicadores-fisicos` (Recuperação) | 30, 90, 180, 365 | 90; `limit = min(days*2, 1000)` |
| `/evolucao-corporal` | 7, 30, 90, 180, 365 | 90 |
| `/exercicios` | fixo `days=1095` | busca inicial "Puxada" |
| `/grupos-musculares` | 30, 90, 180, 365 | 180; período `weekly`; métrica `volume` |
| `/volume-grupamentos` | granularidade d7/week/month → 14/21/70 dias; `limit=400` | `d7`; heatmap `session` |
| `/treinos` | 30, 60, 90, 180 | 90; grade de ativação fixa 7 dias |
| `/relatorios` | data inicial entre hoje−365 e hoje−7 | hoje−90 |
| `/registro-exercicios` | Todo período, 7, 14, 30, 90 | Todo período; `limit=200`, página 20 |
| `/agente-cardio` histórico | `limit=200`, página 20 | — |
| `/cardio` tabelas de atividades | página 20 | — |
| `/planos-treino` | `limit=100` | — |
| `/dashboard/engine` | `limit=50` | — |
| `/dominio-exercicios` exercícios | `limit=500`, "Somente ativos" | ativos = `true` |
| `/dominio-exercicios` mapeamentos externos | `limit=50` com `offset` | "Só ativos" = `false` |
| `/grupos-musculares` catálogo | `limit=200` | ativos = `true` |
| Geração de ficha | descanso mín/máx 15–240 s | 30 / 90 |

#### 6.3 `/recuperacao-muscular`

- Query com `refetchInterval: 60_000` (também em background) e um `setInterval` local de 60 s que atualiza `nowMs`.
- `elapsedSinceFetchHours = max((nowMs - dataUpdatedAt) / 3_600_000, 0)`.
- `getLiveRemainingHours(rem) = max(rem - elapsedSinceFetchHours, 0)`.
- `getLiveRecoveryPct(est, rem, fallback)`: se `!est || est <= 0` → `clamp(fallback, 0, 100)`; senão `clamp(((est - liveRemaining) / est) * 100, 0, 100)`. O `status` (cor) vem sempre de `item.status`, não deste número.
- Cores: `colorForStatus`: `green`→green, `yellow`→yellow, outro→red. Hex para barras/mapa: `RECOVERY_COLORS = [BRAND.red, BRAND.orange, '#2f9e44']` (índices vermelho/laranja/verde); `hexForStatus`: green→`#2f9e44`, yellow→`#FF9500`, outro→`#E8451C`.
- Mapa corporal: `intensity` red=1, yellow=2, green=3; tooltip `"{grupo}: {Recuperado|Recuperando|Evitar} ({pct}%)"`.
- Gráfico ordenado por `pct` desc; tabela ordenada por `muscle_group.localeCompare(..., 'pt-BR')`.
- `formatHoursToHHMM(h)`: `floor(max(h,0) * 60)` minutos → `HH:MM`.
- `formatGoodForDate(remaining, readiness)`: se `remaining <= 0` ou não finito → `'Já disponível'` quando `readiness === 'READY'`, senão `'Aguardando reavaliação'`; caso contrário `new Date(Date.now() + remaining*3600000).toLocaleString('pt-BR', {day, month, hour, minute})`.
- `recommendationLabel`: se `direct_sets_max <= 0` → `"Sem trabalho direto (indireto permitido, RPE máx {maximum_rpe})"`; senão `"{min}[-{max}] série(s) diretas · RPE máx X"` + `" · sem falha"` se `!allow_failure_on_last_set` + `" · sem progressão"` se `!allow_progression`.
- Badge status: texto `verde`/`amarelo`/`vermelho`; "Leitura": `ideal`→Ideal, `recovering`→Recuperando, outro→Evitar.
- Alerta de ajuste global: `pctChange = Math.round((factor - 1) * 100)`; cor orange se > 0, green se < 0, blue se 0; texto `"Ajuste por fadiga geral (Intervals.icu): {sem ajuste | N% a mais | N% a menos} no tempo de recuperação · TSB {form_tsb}"`.
- Tooltip de fatores: `explanation` + dose da última sessão/72 h (2 casas) + RPE médio usado + série mais recente (`effort_points × repetition_factor × set_type_factor`, 2 casas) + `rpe10_isolated_note`.
- `formatLastTrainedDate(value, ts)`: prioriza `ts`; regex `^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::\d{2})?)?$` → `dd/mm/yyyy[ HH:MM]`; fallback `new Date(value)`.

#### 6.4 `MuscleActivationTable` (avaliação de ativação)

- `formatActivationCell(series, equivalent)`: `'-'` se `series === 0`; senão `"{series} séries × {Math.round(equivalent/series*100)}%"`.
- Com meta (`targetSetsByGroup[grupo]`, `sets > 0`): `actual = total_equivalent`, `ratio = actual / sets`:
  - `maxSets > 0 && actual > maxSets` → `IconAlertTriangle` orange, "Passou do teto (a/max séries equivalentes, teto do modelo)";
  - `ratio >= 0.9` → `IconMoodHappy` green "Atendendo ao modelo";
  - `ratio >= 0.5` → `IconMoodEmpty` yellow "Abaixo do modelo";
  - senão `IconMoodSad` red "Muito abaixo do modelo".
- Sem meta: `total_equivalent < 6` → red "Ativação baixa"; `< 9` → yellow "Ativação moderada"; senão green "Ativação boa".
- Coluna "Meta": `"{sets} (máx {maxSets})"` ou `"{sets}"`.
- Em `/registro-exercicios/[activityId]` a meta vem de `training_type_targets` (`sets = sets_count`, `maxSets = max_weekly_sets`).

#### 6.5 `/volume-grupamentos`

- Comparativo por sessão (T-3..T-0): agrupa `recent-exercise-volume` por grupo (`|| 'Outros'`) e `activity_id`; soma `volume_kg`; RPE médio ponderado por `sets_count` (`Σ avg_rpe*sets / Σ sets`); ordena sessões por `local_date` desc e `activity_id` desc; pega 4 (`t0` mais recente). Linhas ordenadas por nome.
- Séries equivalentes: mesmo esquema com `equivalent_sets` do backend.
- Semanal (S-3..S-0): bucket `weekKeyOf(local_date)` (segunda-feira); 4 semanas mais recentes por grupo.
- Por equipamento: 4 semanas mais recentes por grupo; agregação `volume`/`sets_count` por (grupo, `equipment_name || 'Sem equipamento'`, semana); `last_weight` = `last_weight_kg` do item com maior chave `"{local_date}_{activity_id padded 10}"`; linhas de equipamento ordenadas por total de séries desc e nome; linha "Total" soma séries, **soma** os `last_weight` dos equipamentos e soma volume; linhas de equipamento só aparecem com o grupo expandido.
- KPIs: `groupsWithT0` (grupos com T-0), soma de volume T-0, RPE médio T-0 = média simples dos `avg_rpe` não nulos de T-0.
- Gráfico T-0 × T-1: ordenado por T-0 desc, top 10. Gráfico total: soma de todos os grupos por período.
- `getTrendDirection(current, previous, epsilon = 0.0001)`: `none` se algum nulo; `stable` se `|delta| <= epsilon`; senão `up`/`down`. RPE usa `epsilon={0.05}`. Cores: up `green.6` ↑, down `red.6` ↓, stable `BRAND.orange` →, none texto muted.
- `formatSets`: 0 casas se inteiro, senão 1. `formatRpe`: 1 casa fixa. `formatKg` 0 casas por padrão.
- `Sparkline`: mostra "sem dados" se nenhum valor > 0; nulos viram 0.
- `buildWindow(granularity)`: `d7` = hoje−6..hoje vs 7 dias anteriores; `week` = segunda-feira corrente..hoje vs mesmo número de dias antes da segunda; `month` = dia 1..hoje vs início do mês anterior até `min(span, dias do mês anterior)`. Itens de `trend.items` classificados por `parseDateKey(item.period)` em atual/anterior; `reps_delta`, `volume_delta`; ordenação por reps atuais desc, depois volume atual desc. Badge `getTrendBadge(volume_delta)`: > 0 green "Aumentou", < 0 red "Diminuiu", 0 gray "Estável".
- Heatmap: modo `session` usa os dados já carregados filtrando por `local_date|activity_id` (default = sessão mais recente); `week` = `[refDate-6, refDate]` (default = data mais recente disponível ou hoje); `month` = mês inteiro (`lastDayOfMonthKey`). Células por grupo: soma séries e volume; `avg_weight_kg = volume/sets`; ordenadas por séries desc. Tamanho do retângulo = `sets_count`; cor = gradiente linear por volume entre min e max: baixo `#e00d02`, meio `#e0d502` (ratio 0.5), alto `#05ab10`; `null` → `#5C5C5C`; min == max → cor baixa. Rótulo só se `width > 50 && height > 28`.

#### 6.6 `/treinos` (grade de ativação 7 dias)

- `MIN_ACTIVATION_SETS = 9`; célula "ativada" quando `sets_count >= 9 && had_rpe_9_10`.
- Dias: `buildLast7Days()` (hoje−6..hoje, datas locais). Grupos ordenados por `localeCompare`.
- `activationCount < 2` → ícone orange (baixa frequência, "abaixo do recomendado (mínimo 2x)"); `activationCount > 2` → ícone blue (alta frequência).
- Pares de `muscle-recovery-consecutive-check` mapeados para índices da janela (descartados se fora). Célula ativa: par com `insufficient_recovery` → ícone `IconFlame` red `filled` com texto "Apenas ~Xh de intervalo, recomendado ~Yh…"; par suficiente → `IconInfoCircle` blue; senão `IconCheck` green. Horas com `Math.round`. Grupo com qualquer par insuficiente ganha ícone red na coluna do nome.
- Seção Cardio: `avgHr` = média de `avg_hr_bpm` dos pareados não nulos; `avgDuration` = média de `moving_time_seconds` de todos os pareados; horas de sono `seconds/3600` com 1 casa; duração `round(s/60) min`.
- Tabelas de recuperação: status → cor por mapa `{green, yellow, red}` (fallback gray); `READINESS_LABEL` (`READY` Pronto, `REDUCED_DOSE` Dose reduzida, `NO_DIRECT_WORK` "Evitar trabalho direto"); `GUIDANCE_LABEL` (ideal Ideal, recovering Recuperando, avoid Evitar).
- `isUnavailable` = erro `ApiError` com status 503 (mensagem alternativa de IA indisponível).

#### 6.7 Modo Treino — `PlanExecutionScreen`

- Fila: exercícios da ficha (em `advanced_mode`, só os que têm `advanced_technique`, `warmup_reps`, `feeler_set_reps` ou `peak_contraction_seconds` não nulos) + continuações sintéticas (`sequence: 9999`), excluindo `cancelledIds`, ordenada por `sequence`.
- `reorderPendingBySkip`: só posições pendentes são reordenadas: primeiro o `priorityId`, depois não pulados na ordem original, depois pulados. `priorityId` é limpo quando o item deixa de estar pendente.
- Progresso por item: `loggedCount` = séries gravadas do exercício casado (`findLoggedExerciseForPlanItem`); `workingSetsTarget = overrideAdjusted ?? sets_count ?? 1`. Fora do modo avançado `done = loggedCount >= workingSetsTarget`. No modo avançado `targetCount = working + warmupTarget + feelerTarget + aplicações da técnica` (técnica só conta se não for da família "extra reps"), onde `warmupTarget = warmup_sets_count || 1` se `warmup_reps != null`, `feelerTarget = feeler_set_sets_count || 1` se `feeler_set_reps != null`; `done = loggedCount + min(warmupDone, warmupTarget) + min(feelerDone, feelerTarget) >= targetCount`. O `targetCount` exposto no item continua sendo `workingSetsTarget`.
- Meta ajustada por substituição: uma query `suggested-set-count-for-substitution` por override com `mapping_id` diferente do original; `suggested_sets_count` substitui a meta; badge "Meta ajustada: N série(s) (era M)".
- Valores do override: exercício/equipamento do override > do exercício já logado > da ficha. Alvos de reps/carga/RPE vêm de `suggested-progression` do novo `mapping_id` quando há override.
- Defaults do `SetLoggerCard`: reps = `round(suggested_next_reps)` da avaliação de IA (só se a avaliação foi gerada para o mesmo `exerciseKey`) > reps da última série > `parseNumericTarget(reps_target)` > 10; carga = `suggested_next_load_kg` > última série > `parseNumericTarget(load_target)` > 20; RPE = sugestão > última série > `parseNumericTarget(rpe_target)` (sem fallback numérico). No modo avançado a série de trabalho usa `defaultRpe = 10`.
- Aquecimento genérico (fora do modo avançado): exibido quando o exercício ainda não tem séries, tem `workout_plan_exercise_id` e o `muscle_primary` (lowercase/trim) não está entre os primários/secundários de exercícios já concluídos na sessão. Duas séries de 15 reps com `computeWarmupLoad(defaultWeight)` (20%) e `computeSecondWarmupLoad` (40%), cada uma "Concluí"/"Pular"; não gravadas no backend.
- Sub-etapas do modo avançado, na ordem: aquecimento (`warmupDone < warmup_sets_count||1`) → série de ajuste (`feelerDone < feeler_set_sets_count||1`) → séries de trabalho (até `sets_count||1` séries gravadas excluindo `set_type` `warmup`/`feeler`) → aplicações da técnica (`persistidas + puladas < advanced_technique_applications`, só se técnica não é extra-reps). Aquecimento/ajuste só incrementam contadores locais (sem rede); aplicações e trabalho gravam via `POST .../sets`. Cargas sugeridas: aquecimento 20%, ajuste `computeFeelerSetLoad(ref, index)`, técnica `computeTechniqueApplicationLoad`. `set_type` da aplicação por `getAdvancedTechniqueSetType`; `rest_seconds` da aplicação = `advanced_technique_duration_seconds ?? rest_seconds`.
- "Última série do exercício": fora do modo avançado `setNumber >= targetCount`; no avançado `setNumber >= sets_count||1 + aplicações` (técnica não extra-reps). Na última série dispara o resumo de exercício (incondicional) e a transição mostra `NextExercisePreviewCard`; zerar o timer nessa transição não avança (só "Continuar" ou pular).
- Avaliação por série só com `perSetAiEnabled`.
- Descanso: `rest_seconds || 60` (`DEFAULT_REST_SECONDS = 60`).
- Troca antes da 1ª série: grava override. Troca com séries já feitas ("séries restantes"): cria `MidExerciseExtra` com `sets_count = targetCount - loggedCount` com piso `MIN_SETS_COUNT` (aplicado em `buildSyntheticContinuation`, núcleo do Modo Treino), `localId` a partir de −1000 decrescente, cancela o original, prioriza o novo; ao criar o exercício no backend envia `workout_plan_exercise_id` do original.
- Toda mudança de skip/cancel/prioridade/override/aquecimento/extras/progresso avançado é persistida no `localStorage` (`musclefit.workout-mode-session`).
- `RestTimer`: ±15 s por clique; skip chama `onFinish(true)`.
- Steps de entrada: reps 1; carga 2.5 (normal) ou 0.25 (variantes avançadas); RPE 0.5; dor muscular 0–10.
- Card ilustrado (cards, PR5): é sempre o do exercício EFETIVO — mesmo `mapping_id` de nome/descrição (`resolveEffectiveMappingId`: override > exercício já lançado, se diferente do item > item da ficha). Mesmo exercício do item (inclusive troca só de EQUIPAMENTO) → `exercise_card` da ficha; troca de EXERCÍCIO ou continuação sintética → `GET /exercises/{id}/card` (React Query, cache). O resultado vai para os 3 `SetLoggerCard` (ícone "Como executar") e, só quando a troca mudou o exercício, para o `ExerciseBriefingScreen` (senão ele usa o card do próprio briefing). Ao chegar a ficha, as miniaturas dos cards são pré-carregadas em segundo plano. Sem card publicado (todo exercício enquanto a flag estiver desligada), nada muda na tela.

#### 6.8 Builder de modelo de treino (`/modelos-treino/[id]`)

- Novos itens: `sets_count = 3` para `muscle_group`/`equipment`; `cardio_duration_minutes = 20` para cardio. Novo slot: nome `"Dia {n}"`, tipo `normal`.
- `slotsToPayload`: `sequence` = índice+1; `item_order` = índice+1; zera campos irrelevantes por `item_kind` (ex.: `priority_*_pct` só para `muscle_group`; campos avançados e `mapping_id`/`equipment_id` só para `equipment`; `cardio_duration_minutes` só para cardio).
- `validateSlots` (mensagens exibidas em `notifications`): ≥ 1 slot; slot `descanso` sem itens de grupo/equipamento; no máximo 3 itens `prioritize` por slot; `muscle_group` proibido em `equipment_series` e exige grupo; `equipment` só em `equipment_series`, exige grupo+equipamento+exercício e `sets_count >= 1`; cardio com duração > 0; `priority_min_pct`/`priority_max_pct` ambos ou nenhum, e `min <= max`; com `training_type_id` preenchido, itens `muscle_group` exigem min% e max%. Nome vazio → "Informe um nome para o modelo de treino.".
- Troca de `volume_mode` remove itens incompatíveis (com notificação amarela contando quantos) e, ao ir para `equipment_series`, zera `training_type_id` (o Select de tipo de treinamento só aparece em `percent_budget`).
- Com `training_type_id` preenchido em `percent_budget`, a coluna Séries mostra "—" com tooltip (campo não editável).
- Atalho "Min % / Max % (lote)" (só `percent_budget`): no `onBlur` aplica o valor a todos os itens `muscle_group` do slot; não é enviado ao backend.
- `computeSlotTotalRestSeconds` (só `equipment_series`): `Σ (warmup_sets_count × warmup_rest_seconds) + (feeler_set_sets_count × feeler_set_rest_seconds) + (advanced_technique_applications × advanced_technique_duration_seconds)`; `formatRestSeconds` → `Ns`, `Nmin` ou `Nmin Ss`.
- Pico de contração: checkbox liga com 2 s; `NumberInput` 1–60.
- Limites dos inputs avançados: aplicações 1–20; pausa 1–600 s; séries de aquecimento/ajuste 1–10; reps 1–100; descanso 1–600 s. Min/Max % 0–100.
- `WeeklyActivationPreviewTable`: soma por grupo nos dias do payload (`metric` `total_equivalent` ou `primary_equivalent`), só grupos com valor > 0 em algum dia, ordenados por nome; valores com 1 casa, `'-'` para 0/nulo.

#### 6.9 Outras regras

- Tipos de treinamento: validação `0 <= min <= alvo <= máx` por linha não zerada; linhas 0/0/0 enviadas mesmo assim.
- `/planos-treino` `ExercisesTable`: `sequence` reindexado 1..N após mover/remover; ao escolher exercício no catálogo sobrescreve reps/carga/RPE/nota/progressão pela sugestão; digitar texto que não bate com o nome canônico vinculado zera `mapping_id`. Payload de ativação por exercício com debounce 500 ms; preview agregado só com `mapping_id`.
- `GenerateDraftModal`: `split_day` enviado só se `plan_type === 'abc'` e letra escolhida; `template_slot_sequence` só se modelo escolhido e slot ≠ `__auto__`; objetivo só exibido para não-ABC.
- `/grupos-musculares`: grupos visíveis = selecionados ou 6 primeiros por total acumulado; variação `((latest - previous)/previous)*100` se `previous > 0`; badge red > 5, teal < −5; `secondary_factor = 0.5`; `formatMetric`: `load`/`volume` em kg (0 casas), demais inteiros; cobertura `mapped_ratio*100` com 1 casa.
- `/dashboard/progressao`: trocar grupo limpa exercício se ele não pertencer ao grupo.
- `/dashboard/treinos`: agrupamento por nome só com ≥ 2 sessões.
- `TrendBadge`: só desenha `items[].evolution` do backend (`sessionEvolutionView`); sem regra no frontend.
- `MuscleVolumeBar`: `scale = max(max, actual, 1)`; `pct = min(actual/scale*100, 100)`.
- `RecoveryPhaseTimeline`: `total = est > 0 ? est : max(elapsed, 1)`; `pct = min(elapsed/total*100, 100)`.
- `/intervals-icu`: totais somados de `daily_activity`; FC média por `averageOf` (ignora ≤ 0); `toFixed(0)`.
- `/cardio`: minutos `round(seconds/60)`; km `meters/1000` com 2 casas; tabelas por dia/wellness exibidas em ordem reversa (mais recente primeiro).
- `/agente-cardio`: listas do contexto invertidas para ordem cronológica nos gráficos; `fmt(v, decimals=1)` com `toFixed`, `'—'` para nulo; página do histórico ajustada para a última válida quando a lista encolhe.
- `/indicadores-fisicos`: listas da API (mais recente primeiro) invertidas para os gráficos; sono convertido `hh:mm` ↔ horas decimais.
- `/relatorios`: data inválida quando `start > hoje−7` ou `< hoje−365` (comparação de strings ISO); botão desabilitado; PDF só com `status === 'done'`; excluir oculto enquanto `generating`.
- `/academias`: academia ativa = selecionada ou primeira da lista; marcar "avulsa" zera dias; clique em badge de dia alterna o dia via `PUT`.
- `/equipamentos-exercicios` (massa): rascunho inicial = vínculos existentes do equipamento restritos aos exercícios do grupo; salvar faz POSTs e depois DELETEs sequenciais; ao terminar (sucesso ou erro) invalida as queries.
- `EquipmentLinksSection`: remoção busca o id em `equipment-exercise-mappings` antes do `DELETE`.
- `MuscleContributionsSection`: pesos iniciais por role PRIMARY 1/1, SECONDARY 0.5/0.5, STABILIZER 0.25/0.25; limites 0–2.
- `/usuarios`: senha inicial ≥ 8; próprio usuário não altera papel nem ativo e não pode ser desativado pela tabela; `photoVersions` incrementa após upload/remoção.
- `/perfil`: nova senha ≥ 8 (validação local); `intervals_api_key` enviada como `null` se vazia.
- `/registro-exercicios/[activityId]`: série nova default 10 reps × 20 kg; ao lançar exercício novo as séries são enviadas uma a uma e, se uma falha, a mensagem indica quantas foram salvas; `canFinish = session_status !== 'done'`.
- `NewWorkoutModal`: nome default `"Treino dd/mm/aaaa"` com data de `toISOString()` (UTC).
- Badges de status de mapeamento externo: CONFIRMED green, PROVISIONAL yellow, WEAK_APPROXIMATION orange, outro gray; valores fora das 3 opções são editados como CONFIRMED.
- `SetEvaluationCard` categorias (set-eval-v4): INCREASE_LOAD green, HOLD blue, DECREASE_LOAD orange, desconhecida gray.

---

### 7. Autenticação no cliente

- **Login** (`/`): ao montar, `GET /auth/me`; sucesso → `router.replace(nextPath)`. Submit → `login()` (`POST /auth/login`); sucesso mostra alerta "Login realizado com sucesso" e redireciona: `must_change_password` → `/perfil?troque-senha=1`, senão `nextPath`. `nextPath = sanitizeNextPath(searchParams.get('next'))`: aceita só valor que começa com `/` e não com `//`; senão `/dashboard`. Erro `AuthError` → mensagem do erro; outro → "Erro inesperado ao tentar entrar. Tente novamente.".
- **Sessão**: cookie httpOnly; o frontend não guarda token. Verificação via `GET /auth/me` (não `/users/me`) nos dois layouts autenticados, no app-shell e nas telas que checam papel (query `['current-user']`). `/perfil` usa `GET /users/me`.
- **Guarda de rota**: `(app)/layout.tsx` e `(fullscreen)/layout.tsx` renderizam só um `Loader` até `/auth/me` responder; qualquer erro (401, rede, 5xx) → `router.replace('/')`. Não há middleware (export estático).
- **401**: `apiFetch`/`apiFetchBlob` chamam `redirectToLogin()` uma vez por carregamento de página (flag de módulo), limpam chaves legadas e fazem `window.location.href = '/?next={path+search}'` (ou `/`); em `/` não redireciona. `fetchUserPhotoUrl`/`fetchPlanMuscleMapUrl` e `login()` não passam por esse tratamento.
- **Logout**: `POST /auth/logout` (erro ignorado), `clearSession()`, `router.replace('/')`. O cache do React Query não é limpo explicitamente.
- **Troca de senha**: `/perfil`, `POST /auth/change-password`; com `?troque-senha=1` exibe alerta "Troca de senha obrigatória" e rola até o card. Nenhuma guarda impede navegar para outras telas sem trocar a senha.
- **Papéis na UI** (`SessionUser.role`):

| Flag | Onde | Efeito |
|---|---|---|
| `role === 'admin' \|\| 'personal_trainer'` (ou `/auth/me` não resolvido) | AppShell | exibe "Tipos de treinamento" e "Modelos de treino" |
| `role === 'admin'` (ou não resolvido) | AppShell | exibe "Usuários" |
| `intervals_configured === true` (ou não resolvido) | AppShell | exibe "Cardio" e "Intervals.icu" |
| `role === 'admin' \|\| 'personal_trainer'` | `/tipos-treinamento`, `/modelos-treino` (listas) | query só após `/auth/me`; não autorizado → alerta "Acesso restrito a administradores e personal trainers."; 403 também exibe alerta |
| `role === 'admin'` | `/usuarios` | idem com "Acesso restrito a administradores." |
| `role === 'admin'` | `/dominio-exercicios`, `/equipamentos-exercicios` | botões de escrita `disabled` com tooltip "Ação restrita a administradores"; campos do drawer `disabled`; 403 → "Ação restrita a administradores." |
| `role === 'admin'` | `/academias` | só o ícone "Editar academia" (nome/logo/horário) depende de admin; criar, alterar dias/recorrência, desativar e marcar equipamentos aparecem para todos |
| `intervals_configured` (via `GET /integrations/intervals/status` → `configured`) | `/cardio`, `/intervals-icu` | sem configuração mostra alerta com link para `/perfil` |
| `role === 'admin'` | `/perfil` | badge "Administrador" vs "Usuário" |

- `is_advanced_user` não existe no tipo `SessionUser` nem é referenciado em `src/` (busca por `is_advanced` sem ocorrências no frontend; não há flag de "usuário avançado" na UI). O "Fazer treino avançado" do modo treino é liberado para qualquer usuário e depende só das fichas retornadas por `has_advanced_technique=true`.
- As telas `/planos-treino`, `/registro-exercicios`, `/modo-treino`, dashboards e indicadores não verificam papel.

---

### 8. Estilo e identidade visual

- **Biblioteca**: Mantine 7.17 (core, hooks, notifications). Não há Tailwind, CSS Modules, styled-components ou emotion próprios; estilos inline via prop `style`/`styles` e props de sistema do Mantine (`c`, `fw`, `p`, `w`, etc.).
- **Esquema de cor**: somente escuro. `ColorSchemeScript defaultColorScheme="dark"` e `MantineProvider defaultColorScheme="dark"`; não há alternância para claro. `globals.css` força `background: #0a0a0a; color: #f5f5f5`.
- **Paleta** (`BRAND`): fundo `#0A0A0A`, elevado `#141414` (Paper/cards/header/navbar), superfície `#1C1C1C` (tooltips, cards internos), borda `#2A2A2A`, laranja `#FF9500`, vermelho `#E8451C`, texto `#F5F5F5`, texto secundário `#E0E0E0`; gradiente 135° laranja→vermelho (título do login, "MuscleFit", "Modo de treino", botão Entrar `gradient={{ from: 'brand.5', to: 'brandRed.6', deg: 135 }}`). Cor primária Mantine `brand` (tom 5 = `#ff9500`).
- **Cores semânticas adicionais em uso**: `#2f9e44` (positivo/verde), `#8B93A6` (série de comparação), `#4DA3FF`, `#B98CFF`, `#e03131/#f08c00/#0ca678/#f76707` (status de volume), `#facc15`/`#ef4444` (mapa muscular do modo treino), gradiente do heatmap `#e00d02/#e0d502/#05ab10`, `#F5A623` (borda de troca de senha), `rgba(255,149,0,0.12)` (radial do login), `rgba(10,10,10,0.9)` (header do modo treino); além das cores nomeadas do Mantine (`green`, `red`, `yellow`, `orange`, `teal`, `blue`, `grape`, `cyan`, `indigo`, `violet`, `gray`).
- **Tipografia**: pilha `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif` (sem carregamento de fonte); headings peso 700. Títulos de página `Title order={1}` (sub-rotas do dashboard `order={2}`).
- **Padrão de cartão**: `Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}` repetido em todas as telas; KPIs com texto `size="xs" tt="uppercase" fw={600}` + valor `size="xl" fw={700}`.
- **Layout**: `AppShell` header 64 px, navbar 240 px colapsável abaixo de `sm`, `padding="lg"`. Modo treino sem shell, `Container size="sm"`, `minHeight: 100dvh`, respeita `env(safe-area-inset-top/bottom)`; botões grandes (h 56/64, `radius="xl"`, ActionIcon 44 px).
- **Responsividade**: breakpoints Mantine xs 36em, sm 48em, md 62em, lg 75em, xl 88em. Uso de `Grid.Col span={{ base: 12, sm/md/lg }}`, `SimpleGrid cols={{ base, sm, md, lg }}`, `hiddenFrom="sm"`/`visibleFrom="sm"`, `useMediaQuery('(max-width: 48em)')` para modais `fullScreen` no mobile (registro, indicadores, academias, equipamentos, usuários, modelos, tipos, substituição de equipamento). Tabelas em `Table.ScrollContainer minWidth={…}` (500–1100 px) com rolagem horizontal.
- **Gráficos**: recharts 3 com tokens de `chartTheme.ts`; tipos usados: `LineChart` (maioria), `BarChart` (com `Cell` em recuperação), `Treemap` (heatmap), sparkline `LineChart` 100×32 sem eixos; `ResponsiveContainer width="100%"` com alturas 220–360; grid `strokeDasharray="4 4"` sem linhas verticais (progressão usa `"3 3"`).
- **Ícones**: `@tabler/icons-react` exclusivamente; um trecho de `/treinos` usa emojis na legenda textual (🟠 🔵 🔴 ℹ️).
- **Mapa corporal**: `MuscleBodyMap` v1 — arte raster `public/bodymap/v1/bodymap-base(@768).webp` + regiões SVG por slug (`regions.json`, fonte canônica compartilhada com o backend), colorização por `feColorMatrix` com a mesma fórmula do compositor Pillow.
- **Card ilustrado de exercício** (Modo Treino, cards PR5): imagem 2:3 pronta, composta no backend com os tokens de `BRAND` (Oswald/Inter, logo oficial — nada é desenhado por cima no frontend); exibida inteira (`object-fit: contain`) sobre `BRAND.bg` no `ExerciseCardViewer`, e como miniatura (≤ 220 px, borda `BRAND.border`, raio 12) no briefing. Atalho: `ActionIcon` 44 px `IconInfoCircle` "Como executar".
- **Markdown**: `react-markdown` com mapeamento de elementos para `Title`/`Text`/`List` em `/agente-cardio`, `/insights`, `/treinos`; `/planos-treino/[id]` renderiza sem mapeamento.
- **Notificações**: `Notifications position="top-right"`, usadas só nos builders de tipo de treinamento e de modelo de treino; demais telas usam `Alert` inline.
- **Idioma**: textos da UI em português; `lang="pt-BR"`; números e datas com `toLocaleString('pt-BR')`.

---

### 9. Variáveis de ambiente

| Variável | Onde é lida | Default | Uso |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `src/lib/config.ts` | `'https://api.musclefit.com.br'` | Base de todas as chamadas (`apiFetch`, `apiFetchBlob`, `login`, `fetchUserPhotoUrl`, `fetchPlanMuscleMapUrl`); barras finais removidas |

Não há outras leituras de `process.env` em `src/` nem `env` em `next.config.mjs`. Não existe arquivo `.env*` em `frontend/`. Por ser `NEXT_PUBLIC_*`, o valor é embutido no bundle no momento do `next build`. O script `dev` usa `-H 0.0.0.0 -p 3000`.

---

### 10. Testes e lint

- Não há diretório de testes, nem arquivos `*.test.*`/`*.spec.*` em `src/`, nem dependências de Jest, Vitest, Testing Library ou Playwright no `package.json`.
- Lint: script `"lint": "next lint"` com `eslint 8.57.1` e `eslint-config-next 14.2.35` instalados; não existe arquivo de configuração ESLint (`.eslintrc*`/`eslint.config.*`) em `frontend/`, portanto a configuração efetiva é não verificada (o `next lint` pede para criar uma na primeira execução). O código contém diretivas `eslint-disable-next-line` para `@next/next/no-img-element`, `react-hooks/exhaustive-deps` e `no-constant-condition`.
- Tipagem: script `"typecheck": "tsc --noEmit"` com `strict: true`. O comando não foi executado nesta documentação. Existem usos de `any` em `HeatmapCellContent(props: any)` e `HeatmapTooltip({ active, payload }: any)` em `VolumeGrupamentosClient.tsx`.
- Não há Prettier, Husky, lint-staged nem configuração de CI dentro de `frontend/`.

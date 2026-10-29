# Role de `terraform plan` só leitura: `musclefit-gha-plan` (AWS-03)

Origem: AWS-03 + SEG-16 do relatório da auditoria de 2026-10-08 (arquivo privado). Hoje o job `plan` de `.github/workflows/terraform.yml`, disparado por `pull_request`, assume `musclefit-gha-deploy`, a role de **escrita** (5 managed `musclefit-gha-deploy-v2-*`, ver `../consolidacao/README.md`). Num PR, o workflow que roda é o do próprio PR: quem tem push no repositório edita o YAML e executa qualquer ação da role de deploy (`lambda:UpdateFunctionCode`, `iam:PutRolePolicy` nas `-exec`, `s3:PutObject` no state), contornando o gate `environment: production` do `deploy-producao.yml`.

Objetivo: jobs de `pull_request` assumem uma role nova, `musclefit-gha-plan`, só leitura; a role de deploy deixa de aceitar `pull_request` e fica restrita a `main` e ao environment `production`.

## Status: AWS-03 concluído em 2026-10-10

Todas as etapas da "Ordem de execução" foram executadas. O texto abaixo fica como registro de como se chegou aqui; onde ele diz "não existe", "em andamento" ou "ainda não publicada", vale este resumo.

- **Passo A** (etapa 2): `musclefit-gha-plan` criada, com a `musclefit-gha-plan-leitura` anexada.
- **v2 da `musclefit-gha-plan-leitura`**: `s3:ListBucket` nos 4 buckets gerenciados (ver "Achado do 1º plan com a role").
- **PR 2** (#490): `terraform.yml` assume `musclefit-gha-plan` nos PRs.
- **Passo B** (etapa 4): a trust de `musclefit-gha-deploy` não aceita mais `pull_request`; ficou só `ref:refs/heads/main` e `environment:production`.
- **Etapa 5** (#493): apply tirou os 3 `aws_secretsmanager_secret_version` do state e a **v3** da `musclefit-gha-plan-leitura`, sem `GetSecretValue`, foi publicada. O plan de conferência com a v3 saiu `No changes`.

Pendência fora do AWS-03: a decisão sobre expirar as versões não correntes do tfstate (ver "Versões antigas do tfstate e decisão pendente").

## Estado na conta (lido em 2026-10-10, só Get/List/Describe/Lookup/Simulate)

- `musclefit-gha-plan`: **não existe** (`GetRole` → `NoSuchEntity`). **Atualização 2026-10-10:** criada pelo passo A do runbook (etapa 2, concluída), com a `musclefit-gha-plan-leitura` anexada.
- Trust de `musclefit-gha-deploy` (cópia em `musclefit-gha-deploy-trust-atual-2026-10-10.json`): `aud = sts.amazonaws.com` e `sub` em `StringLike` com 6 valores: `ref:refs/heads/main`, `pull_request` e `environment:production`, cada um nas formas `repo:dougbrsp/muscle_fit:...` e `repo:dougbrsp@33515327/muscle_fit@1356252176:...`.
- **O GitHub emite a forma com IDs.** Todas as 95 sessões OIDC dos últimos 3 dias no CloudTrail (`AssumeRoleWithWebIdentity`, `subjectFromWebIdentityToken`) vieram como `repo:dougbrsp@33515327/muscle_fit@1356252176:<contexto>`. Uma trust só com `repo:dougbrsp/muscle_fit:pull_request` **não funcionaria**; as duas formas entram, como na role de deploy.
- Provedor OIDC `token.actions.githubusercontent.com` já existe. Cota de roles 21/1000.
- Backend do state: bucket `musclefit-tfstate-930413537709` (SSE-S3 `AES256`, sem KMS, sem bucket policy, versionado), chave `fase1/terraform.tfstate`, lock na tabela DynamoDB `musclefit-tfstate-lock`.
- Access Analyzer `musclefit-account` já tem um achado `ACTIVE` para `musclefit-gha-deploy` (principal federado do GitHub). A role nova vai gerar um achado igual: é esperado; arquivar com a mesma justificativa.

## O que o `plan` realmente lê (CloudTrail, não suposição)

`LookupEvents` por `AccessKeyId` das 3 sessões `pull_request` mais recentes (2026-10-10 18:30, 18:39 e 18:43 UTC) em `sa-east-1`, `us-east-1`, `us-east-2` e `us-west-2`: ~480 eventos por plan, **todos `readOnly: true`**. A lista completa, traduzida para ação IAM, está em `OBSERVADAS` de `scripts/test_iam_gha_plan.py`. Resumo:

| Serviço | Chamadas |
|---|---|
| API Gateway | `GET` em rest API, resources, methods, integrations, stage, deployment, domain name, base path mapping |
| Lambda | `GetFunction`, `GetPolicy`, `GetFunctionEventInvokeConfig`, `ListVersionsByFunction` |
| S3 (config de bucket) | `GetBucket{Acl,Cors,Logging,ObjectLockConfiguration,Policy,PublicAccessBlock,RequestPayment,Tagging,Versioning,Website}`, `GetAccelerateConfiguration`, `GetBucket{Encryption,Lifecycle,Replication}` |
| Secrets Manager | `DescribeSecret`, `GetResourcePolicy` e **`GetSecretValue` (9 chamadas = 3 segredos × 3 plans)** |
| IAM, Route 53, CloudFront, ACM, Budgets | só `Get*`/`List*`/`Describe*` (em `us-east-1`) |
| EC2, RDS, DynamoDB, ECR, EventBridge, CloudWatch, Logs, SNS, SES, CloudTrail, GuardDuty, Access Analyzer | só `Describe*`/`Get*`/`List*` de configuração |
| `us-east-2`, `us-west-2` | só `sts:GetCallerIdentity` (as regras do alerta de root, PR #486, entraram no state depois) |

O CloudTrail de gerenciamento não registra eventos de dados. Esses vêm do código do backend `s3` e do provider: `s3:ListBucket` + `s3:GetObject` no state; `dynamodb:GetItem` do digest MD5 do state na tabela de lock (lido mesmo com `-lock=false`); `s3:GetObject`/`GetObjectTagging` nos objetos do `musclefit-rag` (`aws_s3_object.musclefit_rag_markdown`).

### Respostas às perguntas do AWS-03

- **`secretsmanager:GetSecretValue`: precisava, em 3 segredos, até a etapa 5 (a v3 tira).** Não há `data` de segredo, mas o refresh dos 3 `aws_secretsmanager_secret_version` (`openai_api_key`, `api_token`, `secret_encryption_key` em `infra/secrets.tf`) chama `GetSecretValue`, e o provider grava o valor no state, mesmo com `ignore_changes`. O `turnstile-secret-key` (sem `_version`) e o `rds!cluster-*` (o Aurora só expõe o ARN) **não** precisam e ficam de fora. Ver "Risco residual" e a etapa 4.
- **`kms:Decrypt`: não precisa.** Os segredos usam a chave AWS `aws/secretsmanager`, cuja key policy já autoriza principals da conta via Secrets Manager, e o state usa SSE-S3. Nenhuma ação `kms:` na role.
- **Lock: `-lock=false` no plan.** Com lock, o plan faz `PutItem`/`DeleteItem` na `musclefit-tfstate-lock`, que são escritas. Sem lock, a role só precisa de `dynamodb:GetItem` naquela tabela, para o digest. Um plan concorrente com um apply pode sair desatualizado; isso é informativo, porque o apply refaz o próprio plan sob lock.
- **Leitura do bucket de tfstate:** `s3:ListBucket` no bucket e `s3:GetObject` **só** em `fase1/terraform.tfstate`, sem `GetObjectVersion`: as versões antigas não ficam legíveis.

## Permissões: policy enxuta, não `ReadOnlyAccess`

`musclefit-gha-plan-leitura.json`, uma managed policy do cliente. **O arquivo no repositório é a v3** (16 statements, 4.061 de 6.144 caracteres, 66%), **publicada em 2026-10-10** (passo C da etapa 5) e versão padrão na conta. Até então a conta rodava a v2 (17 statements, 4.447 caracteres). A v1, publicada no passo A, tinha 16 statements e 4.196 caracteres. Ver "Achado do 1º plan com a role" e "Etapa 5". Zero `Put`, `Create`, `Delete`, `Update`, `Attach`, `Detach`, `Pass`, `Tag`, `Invoke` ou `Start`; verbos só `Get`, `List`, `Describe`, `View` e `apigateway:GET`. Recursos escopados a `musclefit-*` onde a API aceita recurso; o resto (EC2, RDS, Route 53, CloudFront, ACM, CloudTrail, GuardDuty, Access Analyzer, alarmes e `DescribeLogGroups`) fica em `*`, porque são metadados de configuração e várias dessas ações não aceitam ARN.

Por que não a `arn:aws:iam::aws:policy/ReadOnlyAccess` (v190, lida em 2026-10-10): são 3.012 ações, e entre elas há leitura de **dados**, não de configuração. Por exemplo, `s3:Get*` lê as fotos de `musclefit-uploads`; `dynamodb:Get*`/`Query`/`Scan` lê os jobs; `logs:Get*`, `FilterLogEvents` e `StartQuery` leem logs com dados de usuário; `rds:Download*` baixa logs do banco; `ecr:BatchGetImage` baixa imagens; `ssm:Get*` e `sqs:Receive*` também estão lá, além de ações com custo como `polly:SynthesizeSpeech` e `bedrock:CountTokens`. A role de plan precisa ser segura nas mãos de **qualquer** pessoa com push, porque o PR pode trocar o YAML e usar a role para qualquer coisa que ela permita. Compensaria só se o custo de manter a lista fosse alto, e não é: um recurso novo de serviço já coberto não muda nada, e um serviço novo no Terraform faz o plan falhar com `AccessDenied` explícito, que se resolve com uma nova versão desta policy (ver "Como ampliar").

Provas, todas só leitura:

1. `accessanalyzer:ValidatePolicy` (`IDENTITY_POLICY`): **0 achados**. A trust gera só `SUGGESTION CONFIRM_AUDIENCE_CLAIM_TYPE` e `WARNING SPECIFIC_GITHUB_REPO_AND_BRANCH_RECOMMENDED`. O segundo é genérico do validador: `pull_request` não tem branch no `sub`, e a trust usa `StringEquals` sem curinga.
2. `iam:SimulateCustomPolicy` com o documento: 70 de 71 leituras observadas `allowed`, e 30 de 30 proibidas `implicitDeny`. Entre as proibidas estão `s3:PutObject`/`DeleteObject` no state, `GetObject` em `musclefit-uploads` e `frontend-assets`, `dynamodb:PutItem`/`DeleteItem` no lock, `GetItem`/`Scan` nos jobs, `GetSecretValue` no turnstile e no `rds!cluster`, `logs:GetLogEvents`/`FilterLogEvents`/`StartQuery`, `ecr:BatchGetImage`/`GetDownloadUrlForLayer`/`GetAuthorizationToken`, `rds:DownloadDBLogFilePortion`, `rds-data:ExecuteStatement`, `kms:Decrypt`, `ssm:GetParameter`, `lambda:UpdateFunctionCode`/`InvokeFunction`, `iam:PassRole`/`PutRolePolicy`/`UpdateAssumeRolePolicy` e `sts:AssumeRole` na de deploy. O único "não" das leituras é `sns:GetSubscriptionAttributes` em ARN de assinatura, um defeito conhecido do simulador: ele nega até o statement idêntico da role de deploy (`musclefit-alerts:*`, testado), e o CloudTrail mostra a chamada passando de verdade.
3. `python -m pytest -q scripts/test_iam_gha_plan.py` (7 testes, local, sem AWS): só verbos de leitura, nenhum verbo de escrita, cobertura de todas as chamadas observadas, dados sensíveis fora, tamanho, a trust de plan só com `pull_request`, e a trust-alvo da de deploy sem `pull_request`.

### Risco residual (aceito na etapa 1, eliminado na etapa 5)

> **Correção de 2026-10-10 (etapa 5), medida:** o parágrafo abaixo superestimou o state e subestimou a permissão. Li as **124 versões** de `fase1/terraform.tfstate` (`ListObjectVersions` e `GetObject` por versão, comparando sem imprimir o valor). Em todas as que têm os `_version`, o `secret_string` é `replace_me`, e **nenhuma** versão, atual ou antiga, contém valor real. O motivo: o provider lê a versão pelo `version_id` gravado no create (`terraform-20260903...`, o placeholder, hoje `AWSPREVIOUS`), não o `AWSCURRENT`. O risco real era a **permissão**: o `GetSecretValue` da v1 e da v2 vale para o segredo inteiro, sem condição de versão. Um PR que troque o YAML lê o `AWSCURRENT` (a chave real) com a role de plan. A etapa 5 tira os `_version` do state para que a v3 possa tirar essa permissão.

Texto original: **Ler o state é ler os 3 segredos.** O state em `fase1/terraform.tfstate` contém, em texto puro, o `secret_string` lido no refresh dos 3 `aws_secretsmanager_secret_version`: a chave da OpenAI, o token mestre e a chave de criptografia. Qualquer role capaz de rodar um `plan` lê o state, então a role de plan dá, a quem tem push, acesso a esses 3 valores. É **o mesmo** que acontece hoje (a role de deploy também lê), mas sem a escrita. O `GetSecretValue` nos 3 ARNs não aumenta a exposição enquanto o state tiver os valores. A etapa 4 fecha o risco tirando os 3 `_version` do Terraform.

## Ovo e galinha: decisão pela opção (b), runbook do dono da conta

O workflow não pode apontar para uma role que não existe. Avaliei as duas opções:

**(a) A role nasce no Terraform (apply da `main`).** A `musclefit-gha-deploy-v2-iam` atual (v2, padrão, 2 de 5 versões) **não permite**. Todas as escritas de IAM dela estão em `role/musclefit-*-exec` (`musclefit-gha-plan` não casa), e ela não tem `iam:CreatePolicy`. Seria preciso publicar uma **v3 da `musclefit-gha-deploy-v2-iam`** com o statement abaixo (policy inline na role de plan, porque sem `CreatePolicy` não há managed), registrado em `ACOES_NOVAS` de `scripts/iam_consolidacao.py` no mesmo padrão do alerta de root:

```python
# scripts/iam_consolidacao.py, ACOES_NOVAS (so se a opcao (a) for escolhida)
*[(acao, f"arn:aws:iam::{CONTA}:role/musclefit-gha-plan", None, "PR AWS-03")
  for acao in ("iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:TagRole",
               "iam:UntagRole", "iam:UpdateRole", "iam:UpdateRoleDescription",
               "iam:UpdateAssumeRolePolicy", "iam:ListRolePolicies",
               "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole",
               "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy")],
```

São 14 tuplas, cerca de 500 caracteres a mais na `v2-iam` (cabe com folga). Publicação: `aws iam create-policy-version --policy-arn arn:aws:iam::930413537709:policy/musclefit-gha-deploy-v2-iam --policy-document file://infra/iam-policies/consolidacao/musclefit-gha-deploy-v2-iam.json --set-as-default`, ficando em 3 de 5 versões. Passos manuais: publicar a versão, **promover para a `main` e rodar o apply** (exige aval de produção) antes de o PR do workflow funcionar, e depois trocar a trust da de deploy, que a própria role de deploy não pode alterar e nunca deveria poder. Custo de segurança: a role de deploy passaria a poder reescrever a policy e a trust da role de plan. Um pipeline da `main` comprometido poderia dar escrita à role de plan e com isso abrir a conta para qualquer PR, uma persistência que hoje não existe.

**(b) A role nasce por runbook, executado pelo dono da conta. RECOMENDADA.** São 3 comandos numa sessão, com os documentos deste diretório. Não amplia a role de deploy (ela continua sem poder tocar em `musclefit-gha-plan`, que não casa com `musclefit-*-exec`) e não depende de promoção para produção. Fica no mesmo padrão da própria `musclefit-gha-deploy`: identidades do pipeline são bootstrap fora do Terraform que elas executam. Nos dois caminhos, a troca da trust da role de deploy é manual. Total: **2 sessões manuais curtas em (b)**, contra 2 sessões mais uma promoção para produção em (a).

## Ordem de execução

| # | O quê | Quem | Autorização |
|---|---|---|---|
| 1 | **Este PR** (documentos, teste, runbook) → `develop`. Nenhum `.tf` muda; o plan do próprio PR roda ainda com a role de deploy e sai sem mudanças. | Tech Lead mescla com CI verde | permissão de develop |
| 2 | **Runbook, passo A**: criar a policy, a role e o attachment. **Concluído em 2026-10-10.** | Dono da conta (ou o Tech Lead com autorização explícita, credencial SSO de admin, nunca a role do pipeline) | **exceção AWS pontual, aval do usuário** |
| 3 | **PR 2** (workflow), **concluído em 2026-10-10** (PR #490, branch `feat/aws-03-pr2-workflow-plan-role`, depois da v2 da `musclefit-gha-plan-leitura`, ver "Achado do 1º plan com a role"): `terraform.yml` passa a assumir `musclefit-gha-plan`, usa `terraform plan -lock=false` e deixa de ter `pull-requests: write` (sem uso, achado do AWS-03). O plan do próprio PR 2 prova a role nova. Mescla na `develop`; a promoção para `main` pode ir no próximo lote, porque o workflow só roda em `pull_request` e os PRs para a `develop` já usam o YAML do próprio PR. | Agente especialista + Tech Lead | permissão de develop; a promoção segue a regra de produção |
| 4 | **Runbook, passo B**: tirar `pull_request` da trust de `musclefit-gha-deploy`, só depois do PR 2 na `develop` e de um plan verde com a role nova. **Concluído em 2026-10-10.** | Dono da conta | **exceção AWS pontual, aval do usuário** |
| 5 | **Etapa 5** (seção "Etapa 5" abaixo): blocos `removed { lifecycle { destroy = false } }` para os 3 `aws_secretsmanager_secret_version` e a **v3 da `musclefit-gha-plan-leitura`**, sem o statement `SecretsValorSoDosTresComVersaoNoTerraform`. Ordem: PR na `develop`, promoção, apply e, só então, publicação da v3. **Concluído em 2026-10-10** (PR #493, apply, v3 publicada, plan de conferência `No changes`). | Agente + dono da conta (versão da policy) | develop + promoção com aval; versão de policy com aval |

### Runbook (Git Bash na raiz do repositório, perfil SSO de administrador)

```bash
ACC=930413537709
DIR=infra/iam-policies/gha-plan
aws sts get-caller-identity   # conferir que NAO e a role do pipeline
```

**Passo A: criar (etapa 2).** Nada existente muda.

```bash
aws iam create-policy \
  --policy-name musclefit-gha-plan-leitura \
  --policy-document file://$DIR/musclefit-gha-plan-leitura.json \
  --description "terraform plan em PR: so leitura (AWS-03)" \
  --tags Key=project,Value=musclefit Key=managed_by,Value=manual-aws-03

aws iam create-role \
  --role-name musclefit-gha-plan \
  --assume-role-policy-document file://$DIR/musclefit-gha-plan-trust.json \
  --max-session-duration 3600 \
  --description "GitHub Actions pull_request: terraform plan so leitura (AWS-03)" \
  --tags Key=project,Value=musclefit Key=managed_by,Value=manual-aws-03

aws iam attach-role-policy --role-name musclefit-gha-plan \
  --policy-arn arn:aws:iam::$ACC:policy/musclefit-gha-plan-leitura
```

Conferência (leituras devem dar `allowed`, escritas `implicitDeny`):

```bash
aws iam simulate-principal-policy --policy-source-arn arn:aws:iam::$ACC:role/musclefit-gha-plan \
  --action-names s3:GetObject \
  --resource-arns arn:aws:s3:::musclefit-tfstate-930413537709/fase1/terraform.tfstate \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table
aws iam simulate-principal-policy --policy-source-arn arn:aws:iam::$ACC:role/musclefit-gha-plan \
  --action-names s3:PutObject dynamodb:PutItem lambda:UpdateFunctionCode iam:PassRole \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table
```

Rollback do passo A (ninguém depende da role até o PR 2):

```bash
aws iam detach-role-policy --role-name musclefit-gha-plan --policy-arn arn:aws:iam::$ACC:policy/musclefit-gha-plan-leitura
aws iam delete-role --role-name musclefit-gha-plan
aws iam delete-policy --policy-arn arn:aws:iam::$ACC:policy/musclefit-gha-plan-leitura
```

**Passo B: restringir a role de deploy (etapa 4).** Pré-requisitos: PR 2 mesclado na `develop`, um plan de PR verde com `musclefit-gha-plan`, e nenhum `Deploy produção` rodando.

```bash
aws iam update-assume-role-policy --role-name musclefit-gha-deploy \
  --policy-document file://$DIR/musclefit-gha-deploy-trust-sem-pull-request.json
aws iam get-role --role-name musclefit-gha-deploy --query 'Role.AssumeRolePolicyDocument'
```

Validação: o próximo `Deploy produção` (sub `ref:refs/heads/main` e `environment:production`) e o `db-migrations.yml` (`environment:production`) seguem verdes. No CloudTrail, nenhuma `AssumeRoleWithWebIdentity` de `musclefit-gha-deploy` com `:pull_request` depois da troca.

Rollback do passo B: `aws iam update-assume-role-policy --role-name musclefit-gha-deploy --policy-document file://$DIR/musclefit-gha-deploy-trust-atual-2026-10-10.json`.

### PR 2: mudança exata em `.github/workflows/terraform.yml`

```diff
 permissions:
-  id-token: write # necessario para o OIDC assumir a role musclefit-gha-deploy
+  id-token: write # OIDC assume a role SO LEITURA musclefit-gha-plan (AWS-03)
   contents: read
-  pull-requests: write # para comentar o plan na PR
...
-          role-to-assume: arn:aws:iam::930413537709:role/musclefit-gha-deploy
+          role-to-assume: arn:aws:iam::930413537709:role/musclefit-gha-plan
...
-        run: terraform plan -no-color -out=tfplan
+        run: terraform plan -no-color -lock=false -out=tfplan
```

**Por que não vai junto com este PR:** o plan do próprio PR falharia no `AssumeRoleWithWebIdentity` (a role ainda não existe) e, mesclado, quebraria o plan de todos os PRs de `infra/` até o passo A. A troca de trust (passo B) também não pode ir antes, porque sem o PR 2 os plans perderiam a role.

### Achado do 1º plan com a role (PR #490, 2026-10-10): falta `s3:ListBucket` nos 4 buckets

O job `plan` do PR 2 assumiu `musclefit-gha-plan` (log: `Authenticated as assumedRoleId AROA5RIHX5GWXRN2TQ7HG:GitHubActions`, o `RoleId` dessa role; CloudTrail: `assumed-role/musclefit-gha-plan/GitHubActions`) e saiu **verde, mas errado**: `Plan: 51 to add, 8 to change, 47 to destroy`, com `aws_s3_bucket.{cloudtrail,musclefit_frontend_assets,musclefit_rag,musclefit_uploads} has been deleted` e todos os recursos filhos (lifecycle, policy, public access block, SSE, versioning, os 28 objetos do RAG) como `must be replaced`. Nenhuma escrita: é só o plan, e a role não escreve.

Causa: o refresh de `aws_s3_bucket` (provider `hashicorp/aws` 5.100.0) começa com `HeadBucket`, que exige `s3:ListBucket` no bucket. A v1 só tinha `s3:ListBucket` no bucket do state. `HeadBucket` é evento de dados, por isso não apareceu no levantamento por CloudTrail de gerenciamento, e o 403 sem corpo é lido pelo provider como bucket inexistente, em vez de `AccessDenied`. O CloudTrail de gerenciamento da sessão não tem nenhum `AccessDenied` (só `TooManyRequestsException` do API Gateway, com retry).

Correção (v2): statement `S3HeadBucketDosBucketsGerenciados`, `s3:ListBucket` só nos 4 ARNs exatos (`musclefit-cloudtrail-930413537709`, `musclefit-frontend-assets`, `musclefit-rag`, `musclefit-uploads`), sem curinga. `ValidatePolicy`: 0 achados; `SimulateCustomPolicy`: `allowed` nos 4, `implicitDeny` em `musclefit-outro`. Custo de segurança: quem tem push passa a poder **listar as chaves** (nomes) desses buckets, inclusive `musclefit-uploads`; o conteúdo dos objetos continua negado (`GetObject` só no state e no RAG). Não há alternativa para o plan: sem `HeadBucket` o provider não lê o bucket.

Lição para "Como ampliar": um `plan` verde não prova a role; conferir que o resumo é `No changes` e que nenhum recurso aparece como `has been deleted`.

## Etapa 5: o valor dos segredos sai do Terraform (2026-10-10)

**Mudança no código.** `infra/secrets.tf` deixa de declarar os 3 `aws_secretsmanager_secret_version` (`openai_api_key`, `api_token` e `secret_encryption_key`) e ganha 3 blocos `removed { from = ...; lifecycle { destroy = false } }`. O Terraform passa a gerenciar só o container: nome, descrição e tags. Nenhuma variável TF, GitHub secret ou `random_*` alimentava esses valores. O `secret_string` era o literal `"replace_me"` com `ignore_changes`, e o valor real foi posto à mão com `put-secret-value` em 2026-09-03. Por isso não há variável nem secret do GitHub a remover.

**Por que é seguro.** Nenhum recurso depende dos `_version`. As 5 Lambdas e as policies das roles `-exec` referenciam `aws_secretsmanager_secret.<nome>.arn` (o container), e `api/app/secrets_bootstrap.py` lê o `AWSCURRENT` pelo ARN. Com `destroy = false`, o apply só esquece o recurso no state, sem chamada à AWS. As versões na conta ficam como estão. Em cada segredo (`ListSecretVersionIds`, 2026-10-10), `terraform-20260903...` (o placeholder) está em `AWSPREVIOUS` e a versão real, de 2026-09-03 23:37 UTC, em `AWSCURRENT`. Quanto à versão do Terraform, `versions.tf` exige `>= 1.7` e os workflows usam `~> 1.7`; `removed` com `lifecycle { destroy }` existe desde a 1.7.

**Plan esperado.** O plan deste PR ainda roda com a v2, que tem `GetSecretValue`. Ele deve mostrar os 3 recursos como `will no longer be managed by Terraform` (`destroy = false`), **0 to add, 0 to change, 0 to destroy**, nenhum `must be replaced` e nenhuma Lambda alterada.

**Guarda** (`scripts/test_iam_gha_plan.py`):

- nenhum `aws_secretsmanager_secret_version` nem `random_*` em `infra/**/*.tf`;
- os 3 `removed` com `destroy = false`;
- nenhum `GetSecretValue` na policy de plan, em recurso nenhum.

### Ordem pós-merge

**Executada em 2026-10-10:** A (PR #493 na `develop`), B (promoção e apply), C (v3 publicada) e D (plan de conferência com a v3: `No changes`).

| # | O quê | Quem | Autorização |
|---|---|---|---|
| A | Mesclar o PR na `develop` com CI verde e o resumo do plan conferido (só remoções do state). | Tech Lead | permissão de develop |
| B | Promover para a `main` e rodar **Deploy produção** com o estágio (1) Terraform. O apply tira os 3 itens do state, sem chamada à AWS. | Tech Lead | **aval de produção do usuário** |
| C | Só depois de B verde: publicar a **v3** da `musclefit-gha-plan-leitura`. | Dono da conta, ou o Tech Lead com credencial SSO de admin | **exceção AWS pontual, aval do usuário** |
| D | Rodar o plan de um PR de `infra/` (novo ou reexecutado) e conferir `No changes` com a v3. | Tech Lead | nenhuma |

**Por que C não pode vir antes de B.** Enquanto os 3 `_version` estiverem no state, o refresh de todo plan chama `GetSecretValue`. Com a v3 publicada antes do apply, o plan de qualquer PR de `infra/` falharia com `AccessDenied`. O apply roda com a role de deploy, que mantém `GetSecretValue` (`v2-rede-dados`), então B não depende de C.

Passo C, no Git Bash na raiz do repositório, com o perfil SSO de administrador. A policy fica com 3 de 5 versões.

```bash
aws sts get-caller-identity   # conferir que NAO e a role do pipeline
aws iam create-policy-version \
  --policy-arn arn:aws:iam::930413537709:policy/musclefit-gha-plan-leitura \
  --policy-document file://infra/iam-policies/gha-plan/musclefit-gha-plan-leitura.json \
  --set-as-default
aws iam simulate-principal-policy --policy-source-arn arn:aws:iam::930413537709:role/musclefit-gha-plan \
  --action-names secretsmanager:GetSecretValue \
  --resource-arns "$(aws secretsmanager describe-secret --secret-id musclefit/openai-api-key --query ARN --output text)" \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table   # implicitDeny
```

Rollback do passo C: `aws iam set-default-policy-version --policy-arn arn:aws:iam::930413537709:policy/musclefit-gha-plan-leitura --version-id v2`.

### Versões antigas do tfstate e decisão pendente

O bucket `musclefit-tfstate-930413537709` é versionado e **não tem lifecycle** (`GetBucketLifecycleConfiguration` retorna `NoSuchLifecycleConfiguration`, 2026-10-10). Há 124 versões de `fase1/terraform.tfstate`, a mais antiga de 2026-09-03 22:01 UTC. Depois do apply, as versões antigas continuam com os 3 `_version`. Medi nesta etapa que, nelas, o `secret_string` é sempre o placeholder `replace_me`, então **as versões antigas não guardam o valor real** destes 3 segredos. Elas guardam o resto do state: ARNs, configuração e topologia de rede.

Quem lê versões antigas: só quem tem `s3:GetObjectVersion`. A role de plan não tem. A role de deploy também não: o statement do state em `musclefit-gha-deploy-v2-s3-borda` tem `GetObject`, `PutObject` e `ListBucket`, mas não `GetObjectVersion`, e nenhuma das 5 `v2-*` tem `s3:*` ou `s3:Get*`. Hoje, só um administrador da conta lê.

**Decisão pendente do usuário:** expirar as versões não correntes do tfstate. A proposta do relatório de auditoria (seção de lifecycle do S3) é de 90 dias, mantendo `newer_noncurrent_versions = 20`. Trade-off: menos histórico para recuperar um state corrompido, em troca de menos cópias antigas do state e custo menor (cerca de 40 MB hoje, valor irrelevante). O bucket é bootstrap fora do Terraform, então a regra seria aplicada por runbook do dono da conta (`put-bucket-lifecycle-configuration`), e não por PR. Esta etapa **não** depende dessa decisão.

## Como ampliar a role de plan

Quando um recurso de um serviço novo entrar no Terraform e o plan do PR falhar com `AccessDenied` na role `musclefit-gha-plan`:

1. Acrescente a ação de **leitura** em `musclefit-gha-plan-leitura.json`, escopada como as demais, e o par em `OBSERVADAS` de `scripts/test_iam_gha_plan.py`.
2. Rode `python -m pytest -q scripts/test_iam_gha_plan.py`: escrita, `GetObject` fora do state e do RAG ou qualquer `GetSecretValue` falham na guarda.
3. O dono da conta publica `aws iam create-policy-version --policy-arn arn:aws:iam::930413537709:policy/musclefit-gha-plan-leitura --policy-document file://infra/iam-policies/gha-plan/musclefit-gha-plan-leitura.json --set-as-default` (no máximo 5 versões; na 6ª, apagar antes a não padrão mais antiga).

O PR que precisar da leitura nova fica com o plan vermelho até o passo 3, o que é o comportamento desejado: a role de plan nunca se amplia sozinha.

### v4 (SEG-05, WAF da API): pendente de aval

Statement novo `WafMusclefit`: `wafv2:GetWebACL`, `GetWebACLForResource` e `ListTagsForResource` só em `arn:aws:wafv2:sa-east-1:930413537709:regional/webacl/musclefit-*/*` (refresh do `aws_wafv2_web_acl.api` e da associação ao stage). O resto que o PR acrescenta ao state (role e log groups do API Gateway, `/account`, alarme) já é coberto por `iam:Get*` em `role/musclefit-*`, `apigateway:GET`, `logs:Describe*/ListTags*` e `cloudwatch:DescribeAlarms`. O plan **do próprio PR** passa sem a v4 (os recursos ainda não existem no state, nada é lido); os PRs **depois do apply** falhariam no refresh do Web ACL sem ela. Publicar junto com as v2 do SEG-05 (comando em `../consolidacao/README.md`).

### v5 (SEG-05, correção pós-apply): pendente de aval

O apply do SEG-05 (run 38149166830, 2026-10-11) mostrou que `wafv2:GetWebACLForResource` é avaliado contra o ARN **genérico** `arn:aws:wafv2:sa-east-1:930413537709:regional/webacl/*/*`, não contra `musclefit-*/*` (a chamada recebe o ARN do stage; o nome do ACL só vem na resposta). É o recurso que a AWS documenta em "Permissions for `GetWebACLForResource`" (WAF Developer Guide, API Gateway REST API). A v5 tira a ação do `WafMusclefit` (que continua com `GetWebACL` e `ListTagsForResource` só em `musclefit-*/*`; o WAF confere `GetWebACL` no ACL devolvido) e cria:

```json
{
  "Sid": "WafGetWebACLForResource",
  "Effect": "Allow",
  "Action": "wafv2:GetWebACLForResource",
  "Resource": "arn:aws:wafv2:sa-east-1:930413537709:regional/webacl/*/*"
}
```

18 statements, 4.397 de 6.144 caracteres (72%). Continua só leitura (a guarda `test_iam_gha_plan.py` confere; `PROIBIDAS` ganhou `GetWebACL`/`ListTagsForResource` em ACL fora de `musclefit-*` e `GetWebACLForResource` fora de `sa-east-1`). Depois dela a policy fica com **5 de 5 versões**: a próxima publicação exige `aws iam delete-policy-version --version-id v1` antes.

**Precisa estar publicada antes do plan do PR `fix/seg05-iam-getwebaclforresource` ficar verde**: o PR troca a associação tainted por `removed { destroy = false }` + `import` (`infra/api_gateway_borda.tf`), e o `import` chama `GetWebACLForResource` no plan. Sem a v5, o plan desse PR e o de qualquer PR de `infra/` depois do apply falham com `AccessDenied`.

```bash
aws iam create-policy-version --policy-arn arn:aws:iam::930413537709:policy/musclefit-gha-plan-leitura \
  --policy-document file://infra/iam-policies/gha-plan/musclefit-gha-plan-leitura.json --set-as-default
```

Rollback: `aws iam set-default-policy-version --policy-arn arn:aws:iam::930413537709:policy/musclefit-gha-plan-leitura --version-id v4`.

## Impacto em produção

- **Este PR (etapa 1): nenhum.** Nenhum `.tf`, workflow, Lambda ou dado muda. O plan do PR roda com a role de deploy, como hoje, e deve sair `No changes`.
- Passo A: cria 1 role e 1 policy, sem anexar nada a recursos existentes. Custo US$ 0. Gera um achado esperado no Access Analyzer.
- PR 2: só o job de plan em PRs muda de identidade. Deploy, migrações e apply não mudam.
- Etapa 5: o apply só tira 3 itens do state. Nenhuma chamada à AWS, e nenhum segredo, valor ou Lambda muda. A v3 (passo C) só remove leitura da role de plan.
- Passo B: a role de deploy deixa de aceitar `pull_request`. O `deploy-producao.yml` (sub `ref:refs/heads/main` / `environment:production`, conferido no CloudTrail) e o `db-migrations.yml` (`environment:production`) não são afetados.

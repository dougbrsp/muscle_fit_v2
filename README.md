# MuscleFit — versão AWS

Este repositório contém a **versão AWS** do MuscleFit: infraestrutura (IaC), backend adaptado para Lambda e um frontend com nova identidade visual, todos publicados em `sa-east-1`.

## Relação com o ambiente local

O ambiente local (Docker Compose, MySQL, FastAPI, Next.js) roda em outro repositório e **continua em produção, sem depender deste repositório de forma alguma**. Os dois ambientes são propositalmente apartados:

- **Local** (`openclaw-dev-agent`): ambiente de uso diário, roda em containers on-premise. Serve como **referência funcional** durante a construção da versão AWS (regras de negócio, schema, motores de recuperação/progressão/volume, prompts de IA) — o código de lá não é copiado 1:1 para cá, é adaptado (Lambda Web Adapter, RDS Data API em vez de conexão MySQL direta, Bedrock em vez de OpenAI direto — ver `docs/aws-migration-plan.md`).
- **AWS** (este repositório): construído do zero seguindo o plano de migração, com **nova identidade visual no frontend**. Alimentado inicialmente por uma migração de dados pontual (`mysqldump` do ambiente local → Aurora, Fase 1 do plano) — depois disso, os dois bancos são independentes; não há sincronização contínua entre local e AWS.

Enquanto a versão AWS não estiver em produção estável, o ambiente local permanece a fonte de verdade e **não deve ficar indisponível** por causa de trabalho feito aqui.

## Documentação

- [`docs/aws-migration-plan.md`](docs/aws-migration-plan.md) — plano de migração faseado (arquitetura, custos, riscos, nomenclatura de recursos). Documento mestre deste repositório, atualizado conforme cada fase avança.

## Infraestrutura

- `infra/` — IaC (Terraform). Backend de state, hosted zone, certificados ACM e a role de deploy (`musclefit-gha-deploy`, OIDC do GitHub Actions) já foram provisionados manualmente como bootstrap da Fase 0 — ver o plano de migração para os identificadores exatos (Zone ID, ARNs). O IaC deste diretório deverá **importar** esses recursos, não recriá-los.

## Deploy

CI/CD via GitHub Actions, assumindo a role `arn:aws:iam::930413537709:role/musclefit-gha-deploy` via OIDC (sem chaves de acesso estáticas), restrita à branch `main` deste repositório.

### Fluxo develop → main → produção

**Merge para `main` não publica nada.** Nenhum workflow de deploy roda em `push`. Em PRs continuam automáticos só os checks (`ci-develop.yml`, `ci-docker-images.yml`, `ci-cloudfront-functions.yml` e o `plan` de `terraform.yml` quando `infra/**` muda).

**O que o CI roda em cada PR (economia de Actions, 2026-10-10):**

- **PR para `develop`:** só a área tocada. Mudou backend (`api/**`, `docker-compose.yml`, `localstack/**`, `dev/**`, `nginx/**`; o RAG mora no submódulo privado `api/privado`, coberto por `api/**`) → lint backend + `backend unit`. Mudou frontend (`frontend/**`, `infra/cloudfront-functions/**`) → lint frontend + `frontend build`. A integração com MySQL (`backend tests`, ~10 min) e o smoke compose + LocalStack **não rodam**. As 3 imagens Lambda (`ci-docker-images.yml`) só são buildadas se o PR mexer em `api/Dockerfile*`, `api/requirements*`, `.dockerignore`, `infra/lambda_*.tf` ou `.github/scripts/*`.
- **Rótulo `ci-completo`:** num PR para `develop`, roda a suíte inteira (integração, smoke compose e as 3 imagens), como na promoção. Adicionar o rótulo dispara a rodada na hora (`gh pr edit <n> --add-label ci-completo`); ele vale para os pushes seguintes enquanto estiver no PR. Use quando o PR mexe em SQL/migração, repositórios, Data API, compose ou nas Lambdas.
- **PR para `main` (promoção):** sempre a suíte inteira de `ci-develop.yml` e de `ci-docker-images.yml`, sem filtro de área.
- Job pulado aparece como *skipping*, nunca pendente. Não há job agregador: o repositório não tem branch protection nem check obrigatório (plano Free), e o critério de merge avalia todos os checks (`pass` ou `skipping`). Um push novo cancela a rodada anterior do mesmo PR (`concurrency`).

Publicar em produção é um passo manual e explícito, feito pelo workflow **Deploy produção** (`.github/workflows/deploy-producao.yml`):

1. Promova `develop → main` (PR + merge) normalmente.
2. Dispare o deploy a partir da `main`:
   - GitHub → **Actions** → **Deploy produção** → **Run workflow** → branch `main` → **Run workflow**; ou
   - `gh workflow run deploy-producao.yml --ref main`
3. Os estágios rodam **em sequência** (`needs`). Se um falha, os seguintes não rodam:
   1. **terraform apply** (`infra/`, job com `environment: production`);
   2. **backend**: build + push das 4 imagens no ECR (`linux/amd64`, tag `latest`), depois `update-function-code` da Lambda `musclefit-migrations` (**sempre**), `migrate` (só se a variável `RUN_DB_MIGRATIONS=true`) e `update-function-code` das 6 Lambdas da aplicação — 7 funções atualizadas em todo deploy de backend;
   3. **frontend**: `next build` (export) → `aws s3 sync --delete` → invalidação do CloudFront escopada ao frontend (nunca `/*`; fotos e cards não são invalidados — ver `infra/README.md`, "Invalidação do CloudFront no deploy do frontend").
4. Confira o **resumo** do run (o que rodou, o resultado de cada estágio e o SHA publicado).

O workflow falha no job `guard` se for disparado de outra ref que não `refs/heads/main`. Ele usa o mesmo grupo de `concurrency` (`backend-deploy`) de `db-migrations.yml`, então um deploy e uma invocação de migração nunca se sobrepõem.

**Rodar só um estágio**: todos vêm ligados por padrão. Desmarque os que não quer (`run_terraform`, `run_backend`, `run_frontend`) na tela do Run workflow, ou pela CLI:

```bash
# só frontend
gh workflow run deploy-producao.yml --ref main -f run_terraform=false -f run_backend=false
# só terraform apply
gh workflow run deploy-producao.yml --ref main -f run_backend=false -f run_frontend=false
```

Um estágio pulado não bloqueia os seguintes. Uma falha ou um cancelamento bloqueia. Pular o apply quando a promoção mudou `infra/**` pode publicar código que depende de infra ainda não aplicada (ex.: rota nova no frontend antes da CloudFront Function `infra/cloudfront-functions/spa-index-rewrite.js`); na dúvida, rode tudo.

**Rollback**: reverta o commit na `develop`, promova para a `main` e dispare o **Deploy produção** de novo.

#### Primeira promoção depois desta mudança

- `workflow_dispatch` só aparece para workflows que **existem na branch padrão** ("This event will only trigger a workflow run if the workflow file exists on the default branch", [docs do GitHub](https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#workflow_dispatch)). O **Deploy produção** só fica disparável depois que a promoção que o traz chega à `main`.
- Um run de `push` usa os arquivos de workflow **do próprio commit enviado** (`GITHUB_SHA` = "Tip commit pushed to the ref", [mesma página, evento `push`](https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#push)). Por isso o push dessa promoção **já roda com as versões novas**, sem gatilho `push`: nada é publicado automaticamente, nem mesmo nesse merge.

Sequência da primeira promoção (a que também leva a Lambda `musclefit-migrations` à `main`):

1. Merge `develop → main` (nada é publicado).
2. **Deploy produção** com todos os estágios e `RUN_DB_MIGRATIONS` ainda desligada: o apply cria `musclefit-migrations`, o backend publica as imagens, atualiza o código de `musclefit-migrations` e das 6 Lambdas (com o aviso "migrações desligadas"), depois vem o frontend.
3. **DB Migrations (manual)** (`db-migrations.yml`, branch `main`) com `action=status`, depois `action=dry-run`. Revise o resumo.

> **Nota:** a função `musclefit-migrations` é criada pelo `terraform apply` com a imagem `musclefit-ai-generators:latest` que **já existe** no ECR (anterior ao commit, sem `app/migrate.py` na primeira vez) e só recebe a imagem do commit no estágio de backend de todo deploy. Rode `status`/`dry-run` **somente depois** de um deploy de backend concluído; antes disso a invocação falha com `Runtime.ImportModuleError`. Um deploy só com `run_terraform` não atualiza o código da função.
4. Só então defina a variável do repositório `RUN_DB_MIGRATIONS=true`. A partir daí o estágio de backend roda `migrate` antes de atualizar as Lambdas. Se precisar aplicar já, rode `db-migrations.yml` com `action=migrate` ou dispare de novo o **Deploy produção** só com o backend.

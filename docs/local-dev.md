# Ambiente de desenvolvimento local (Docker Compose + LocalStack)

Este documento descreve como rodar o backend (`api/`) e seus recursos AWS
(S3, DynamoDB, Secrets Manager) inteiramente na sua maquina, via Docker
Compose + LocalStack.

**Este ambiente NAO faz deploy e NAO se conecta a nenhum recurso AWS real.**
Tudo roda localmente: o "S3", o "DynamoDB" e o "Secrets Manager" que a API
enxerga aqui sao emulados pelo LocalStack, dentro de um container Docker na
sua maquina. Nenhuma credencial real da AWS e necessaria nem usada.

## Pre-requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado
  e rodando (inclui Docker Compose v2, `docker compose`).
- Opcional para os alvos de conveniencia: `make` (disponivel via Git Bash/WSL
  no Windows) **ou** PowerShell (use `scripts/dev.ps1`, equivalente sem
  depender de `make`).

## Submodulo privado (`api/privado`: RAG e prompts)

Desde o M103 P0 o RAG (`rag/markdown`) e, nos PRs seguintes, os prompts de
sistema moram no repositorio **privado** `dougbrsp/muscle_fit_privado`, montado
como submodulo git em `api/privado/` (URL relativa `../muscle_fit_privado.git`
no `.gitmodules`: resolve para https no clone local e para ssh no CI). Sem o
submodulo, o compose monta um diretorio vazio em `/var/task/rag`, a guarda e os
testes de RAG falham (de proposito) e o `terraform plan` falha na precondition
de `infra/s3_rag.tf`.

Clone novo:

```
git clone --recurse-submodules https://github.com/dougbrsp/muscle_fit_v2.git
```

Clone existente, depois de cada `git pull`/`merge` que mude o ponteiro:

```
git submodule update --init api/privado
ls api/privado/rag/markdown/*.md   # confere que o RAG veio
```

**Worktrees** (`git worktree add` ou os worktrees dos agentes em
`.claude/worktrees/`): o submodulo NAO vem junto. Em cada worktree novo rode
`git submodule update --init api/privado` dentro dele (o clone do submodulo e
por worktree, guardado em `.git/worktrees/<nome>/modules`). Sem isso os testes
unitarios de RAG e a guarda falham no worktree.

Mudar o RAG: PR no `muscle_fit_privado` (merge na `main` de la) e depois PR
aqui com o bump do ponteiro:

```
git submodule update --remote api/privado
git add api/privado && git commit -m "chore(privado): bump do RAG"
```

O CI usa a deploy key **somente leitura** `PRIVADO_DEPLOY_KEY` (secret de
Actions e de Dependabot) pela action `.github/actions/checkout-privado`. PR de
fork nao recebe o secret: os jobs que dependem do submodulo sao pulados. O RAG
nunca entra nas imagens Docker (allowlists dos `api/Dockerfile*.dockerignore`,
conferido no CI pelo passo "RAG privado fora da imagem").

**Prompts (M103 P1):** a prosa dos prompts de sistema mora em
`api/privado/prompts/<feature>/<PROMPT_VERSION>.md` e e lida por
`app/prompt_store.py` (falha alto sem o template). Diferente do RAG, os templates
ENTRAM nas imagens (`COPY privado/prompts`, so essa pasta do submodulo; o build
de producao e o `ci-docker-images.yml` fazem o checkout do submodulo) e o compose
de DEV os monta read-only no mesmo caminho (`/app/privado/prompts`,
`/var/task/privado/prompts`). Mudar um prompt = PR no privado + bump do ponteiro
aqui (o bump dispara o build das imagens). Testes e snapshots com texto de prompt
moram em `api/privado/tests/` e rodam no `backend unit`; no log publico a falha
sai sem o texto (diff local: `LLM_PROMPT_SNAPSHOT_DIFF=1` e `PRIVADO_TB=1`).

## Subindo o ambiente

1. Copie o arquivo de exemplo de variaveis de ambiente (e inicialize o
   submodulo privado, secao acima):

   ```
   cp .env.example .env
   ```

   O `.env.example` ja vem com uma chave `SECRET_ENCRYPTION_KEY` valida e
   valores padrao seguros para dev local. Ajuste `OPENAI_API_KEY` se for
   testar as rotas que chamam a OpenAI (geracao de plano de treino, resumos,
   cardio agent etc.) - sem ela, o `POST` continua respondendo 202 e o job
   termina `failed` com a mensagem generica de IA indisponivel (visivel em
   `GET /jobs/{id}`; SEG-12: o motivo real, "OPENAI_API_KEY nao configurada no
   backend.", fica so no log, evento `llm.error`), mas o resto da API funciona
   normalmente.

2. Suba os containers:

   ```
   docker compose up -d
   # ou: make up
   # ou (PowerShell puro): .\scripts\dev.ps1 up
   ```

   Isso sobe 7 servicos:
   - `mysql` (MySQL 8.0): aplica automaticamente, na primeira subida, todas
     as migrations em `api/db/migrations/` (schema completo, portado do
     ambiente on-premise legado).
   - `localstack` (LocalStack 3): ao ficar pronto, roda
     `localstack/init/ready.d/00-bootstrap.sh`, que cria os buckets S3
     (`musclefit-uploads`, `musclefit-rag`), a tabela DynamoDB
     (`musclefit-async-jobs`, com TTL habilitado) e os 3 secrets no Secrets
     Manager (`musclefit/openai-api-key`, `musclefit/api-token`,
     `musclefit/secret-encryption-key`) com valores placeholder de dev.
   - `api`: a FastAPI de `musclefit-api-core` (`app/main.py`), com hot-reload
     (`uvicorn --reload`) a partir do volume `./api/app`. NAO publica porta
     no host - ver `nginx` abaixo.
   - `api-engines`: a FastAPI de `musclefit-engines` (`app/main_engines.py`),
     mesmo hot-reload. Tambem NAO publica porta no host.
   - `nginx`: proxy reverso que escuta em `http://localhost:8000` (a mesma
     porta/URL de sempre) e decide, path a path, se a requisicao vai para
     `api` ou `api-engines` - ver secao "Arquitetura de 2 backends + proxy"
     abaixo. Na porta interna 9001 (nao publicada) e tambem o endpoint da API
     Lambda `Invoke` usado pelos jobs assincronos.
   - `lambda-ai`: as 3 Lambdas assincronas
     (`musclefit-plan-generator`, `musclefit-cardio-agent`,
     `musclefit-workout-ai`), rodando o handler real na imagem de producao - ver secao "Jobs assincronos" abaixo.

### Arquitetura de 2 backends + proxy (paridade com producao)

Em producao, o backend NAO e um monolito: sao 6 Lambdas atras de um unico
API Gateway (`infra/api_gateway.tf`), que roteia por path. A maioria dos
paths (catch-all `{proxy+}`) vai para a Lambda `musclefit-api-core`
(`app/main.py`, `api/Dockerfile`). Um conjunto especifico de paths sob
`/analytics/*` e `/exercises/*` (motores deterministicos - recuperacao
muscular, volume, progressao, ativacao muscular) vai para uma segunda
Lambda, `musclefit-engines` (`app/main_engines.py`, `api/Dockerfile.engines`).
As Lambdas assincronas (job async + polling) tambem rodam
localmente - ver secao "Jobs assincronos" abaixo.

Para o ambiente Docker Compose ter a MESMA paridade (e nao devolver 404 em
paths que em producao so existem em `musclefit-engines`, ex.:
`/analytics/muscle-recovery`), existem 2 backends locais:

- `api` (imagem `api/Dockerfile.dev`, baseada em `api/Dockerfile` de
  producao) - roda `app.main:app`.
- `api-engines` (imagem `api/Dockerfile.engines.dev`, baseada em
  `api/Dockerfile.engines` de producao) - roda `app.main_engines:app`.

Um proxy `nginx` (`nginx/local-dev.conf`) na frente dos dois replica, a
mao, a mesma prioridade de rota mais especifica do API Gateway: paths
exatos listados em `nginx/local-dev.conf` vao para `api-engines`, todo o
resto (catch-all) vai para `api`.

O nginx resolve `api`/`api-engines` (e `lambda-ai` em
`nginx/lambda-local.conf`) pelo DNS do Docker a cada requisicao (`resolver
127.0.0.11 valid=10s` + host em variavel no `proxy_pass`). Reiniciar/recriar
um desses servicos NAO exige mais reiniciar o `nginx`: antes o IP era fixado no
start e o proxy respondia 502 quando o container voltava com outro IP. Se o
servico estiver fora do ar, a requisicao responde 502 na hora (o nginx sobe
mesmo assim).

**Isso e TRANSPARENTE para quem so usa `docker compose up` / o frontend com
`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`**: a porta e a URL
continuam sendo exatamente as mesmas de antes (8000) - so quem esta por
tras dela mudou, exatamente como em producao o frontend fala com um unico
dominio (`api.musclefit.com.br`) sem saber quantas Lambdas existem atras do
API Gateway.

**ATENCAO - manter sincronizado com o Terraform**: a lista de paths
roteados para `api-engines` em `nginx/local-dev.conf` foi transcrita a mao
de `infra/api_gateway.tf` (locals `engines_analytics_path_parts` e
`engines_exercise_path_parts`). Sempre que esses locals mudarem no
Terraform, `nginx/local-dev.conf` precisa ser atualizado manualmente na
mesma PR, ou o ambiente local volta a divergir de producao (path novo
caindo no catch-all errado, ou vice-versa).

**Diferenca conhecida: o DEV nao tem WAF.** Em producao o Web ACL
`musclefit-api` (`infra/api_gateway_borda.tf`) fica na frente do API Gateway:
limite por IP e o Core Rule Set da AWS bloqueando com 403 (sem CORS: no
navegador aparece como erro de rede) XSS, LFI/RFI, SSRF de metadados EC2,
robos conhecidos, extensoes restritas e query > 2 KB / URI > 1 KB / cookie
> 10 KB. So `SizeRestrictions_BODY` (corpo > 8 KB) e `NoUserAgent_HEADER`
ficam em Count. Um fluxo que passa no DEV pode levar 403 em producao: PR
que cria upload, corpo com HTML/URL/texto livre novo ou query string longa
leva smoke desse fluxo depois da promocao, conferindo `wafResponseCode` no
access log `/aws/apigateway/musclefit-api-access`.

### Conteudo de RAG (`api/privado/rag/markdown`) em DEV

Em producao cada Lambda que le RAG hidrata o conteudo do bucket
`musclefit-rag` para `/tmp/rag` na primeira invocacao
(`app/rag_storage.py::ensure_rag_local`, env `RAG_S3_BUCKET`/`RAG_S3_PREFIX`/
`RAG_MARKDOWN_DIR`). Localmente nao ha S3 de verdade para isso: o mesmo
conteudo, versionado no submodulo privado em `api/privado/rag/markdown/`
(secao "Submodulo privado" acima), e montado **read-only** em
`/var/task/rag` com `RAG_MARKDOWN_DIR: /var/task/rag` nos tres servicos que
rodam o pacote `app/`: `api`, `api-engines` e `lambda-ai`.

Sem isso o servico cai no default `/app/rag`, que nao existe no container, e
**tudo que le RAG degrada para vazio sem erro** - foi assim que
`GET /training-types/rag-folders` passou a devolver `[]` so em DEV e o campo
"fonte de RAG" do modal de Periodizacao aparecia vazio. O mapa de servicos x
Lambdas e verificado pelo CI em
`api/tests/unit/test_paridade_rag_dev_producao.py`: se uma Lambda nova ganhar
`RAG_MARKDOWN_DIR` em `infra/` sem o servico local equivalente, o teste falha.

### Jobs assincronos (Lambdas de IA)

Em producao, `musclefit-api-core` responde 202 + `job_id`, grava o job na
tabela DynamoDB `musclefit-async-jobs` (`app/async_jobs.py::create_job`) e
dispara a Lambda da funcao com `lambda.invoke(InvocationType="Event")`
(`invoke_async`). A Lambda roda o handler, grava `done`/`failed`
(`mark_done`/`mark_failed`) e o frontend faz polling em `GET /jobs/{id}`.

| Rota (api-core)                                   | Env var no api-core            | Funcao                     | Imagem de producao        |
|---------------------------------------------------|--------------------------------|----------------------------|---------------------------|
| `POST /workout-plans/generate`                    | `PLAN_GENERATOR_FUNCTION_NAME` | `musclefit-plan-generator` | `api/Dockerfile.ai`       |
| `POST /cardio-recommendations/generate`           | `CARDIO_AGENT_FUNCTION_NAME`   | `musclefit-cardio-agent`   | `api/Dockerfile.ai`       |
| `POST /exercises/evaluate-set`, `/exercises/{id}/summarize`, `/activities/{id}/ai-summary/generate`, `/workouts-analysis/generate`, `/insights/generate`, `/nutrition/meals/parse` (nutrição L5b) | `WORKOUT_AI_FUNCTION_NAME` | `musclefit-workout-ai` | `api/Dockerfile.ai` |
| `GET /workout-plans/{id}/exercises/{id}/briefing` (so quando o exercicio ainda nao tem descricao de execucao) | `PLAN_GENERATOR_FUNCTION_NAME` | `musclefit-plan-generator` (`job_kind="exercise-description"`) | `api/Dockerfile.ai` |
| `POST /exercises/{id}/card-generations`, `.../{gid}/regenerate` (cards ilustrados; polling no recurso `GET /exercises/{id}/card-generations/{gid}`, nao em `/jobs`) | `PLAN_GENERATOR_FUNCTION_NAME` | `musclefit-plan-generator` (`job_kind="exercise-card"`) | `api/Dockerfile.ai` |

A ultima linha e a unica que NAO devolve `job_id` ao frontend: o briefing
responde a tela na hora (com `execution_description: null`) e o job existe so
para o texto aparecer na proxima abertura. Ninguem faz polling nele. Ver
`app/exercise_description_autogen.py`.

No compose local o fluxo e o MESMO, com o mesmo codigo:

```
browser -> nginx:8000 -> api (create_job -> DynamoDB LocalStack)
                           \-> boto3 lambda.invoke(Event) -> AWS_ENDPOINT_URL_LAMBDA=http://nginx:9001
                                 nginx:9001 --todas as funcoes--> lambda-ai:9001
                                   emulador responde 202 e roda, num subprocesso,
                                   app.lambda_<funcao>.handler(event, context)
                                   -> mark_done/mark_failed (DynamoDB LocalStack)
browser -> GET /jobs/{id} -> done | failed
```

- `api` recebe `ASYNC_JOBS_TABLE` e os 3 `*_FUNCTION_NAME` com os nomes reais
  de producao, mais `AWS_ENDPOINT_URL_LAMBDA=http://nginx:9001`. O botocore
  da precedencia a `AWS_ENDPOINT_URL_<SERVICO>` sobre `AWS_ENDPOINT_URL`, entao
  so o cliente Lambda sai do LocalStack; DynamoDB/S3/Secrets continuam nele.
- `lambda-ai` e construido a partir do Dockerfile de PRODUCAO
  (`api/Dockerfile.ai`, sem altera-lo), entao tem exatamente as dependencias
  da Lambda real (ex.: NAO tem as libs apt do WeasyPrint, igual producao).
  O compose so troca o entrypoint pelo emulador
  `dev/lambda-local/lambda_invoke_emulator.py` (montado por volume, fora de
  qualquer build context), que implementa o protocolo HTTP `Invoke` do boto3
  (`Event` -> 202 imediato; `RequestResponse` -> espera o retorno) e chama o
  handler real. O mapeamento nome -> handler -> timeout fica em
  `LAMBDA_LOCAL_FUNCTIONS` no `docker-compose.yml`, transcrito de
  `infra/lambda_ai_generators.tf` e `infra/lambda_workout_ai.tf` - **manter
  sincronizado** se uma funcao nova for
  criada (mesma regra do `nginx/local-dev.conf`).
- Hot-reload: `./api/app` e montado na imagem e cada invocacao roda num
  processo Python novo, entao alteracoes no codigo valem na proxima invocacao
  sem reiniciar container.
- Diferencas assumidas (config de runtime, nao codigo): banco via
  `DB_BACKEND=pymysql` do `.env` (em producao, Data API); RAG montado de
  `./api/privado/rag/markdown` (submodulo privado) em `RAG_MARKDOWN_DIR` (em producao, hidratado de S3 para
  `/tmp/rag`); sem retries automaticos de invocacao Event (os handlers ja
  capturam toda excecao e gravam `failed`); timeout mata o subprocesso e o job
  fica como estava, igual a Lambda real.
- Regra anti-vazamento preservada: o emulador NAO define
  `AWS_LAMBDA_FUNCTION_NAME`/`AWS_EXECUTION_ENV`, ou seja, nao se passa por
  runtime Lambda, e `app/migrate.py::_guard_backend_in_lambda` continua
  intacta. No sentido inverso, o emulador se recusa a subir se detectar essas
  variaveis (nunca roda dentro de uma Lambda real). Nada disso esta em
  `infra/`, nos Dockerfiles ou nos workflows de deploy.
- Sem `OPENAI_API_KEY` no `.env`, os jobs de IA terminam `failed` com a
  mensagem generica de IA indisponivel - o `POST` continua 202. O motivo
  ("OPENAI_API_KEY nao configurada no backend.") aparece so no log
  (`docker compose logs lambda-ai`, evento `llm.error`).

Alternativas avaliadas e descartadas:

- **Lambda Runtime Interface Emulator (RIE)**: so expoe
  `/2015-03-31/functions/function/invocations` (nome fixo `function`), um
  RIE atende uma unica funcao, trata `Event` de forma sincrona (o `POST`
  ficaria preso durante toda a geracao) e define `AWS_LAMBDA_FUNCTION_NAME`,
  o que colocaria `DB_BACKEND=pymysql` num ambiente que se apresenta como
  Lambda - justamente o que a guarda anti-vazamento proibe.
- **Lambdas no LocalStack**: imagem de container em Lambda e recurso Pro; no
  community so zip, o que exigiria empacotar as deps (o WeasyPrint precisa de
  libs apt, inviavel em zip), montar o socket do Docker no LocalStack e
  redeploy a cada mudanca de codigo. Tambem define as variaveis de runtime
  Lambda.

Smoke de ponta a ponta (tambem roda no CI, job `backend smoke test`):

```
bash scripts/smoke-async-jobs.sh
```

Faz `POST` nas funcoes assincronas, exige 202 + `job_id` e faz polling de
`GET /jobs/{id}` ate `done`/`failed` (falha se ficar `pending`). Semeia um
usuario admin descartavel so se o banco nao tiver nenhum (o token mestre
resolve para o admin).

Pre-requisito do LocalStack: o healthcheck do servico `localstack` so fica
`healthy` quando `/_localstack/init/ready` reporta os scripts de `ready.d`
concluidos e sem `ERROR` (antes, `/_localstack/health` ficava verde mesmo com o
bootstrap falhando). `localstack/init/ready.d/00-bootstrap.sh` precisa estar
versionado como executavel (`100755`): no Linux/CI, sem o bit de execucao o
LocalStack falha com `Permission denied` e a tabela `musclefit-async-jobs` nao
e criada. No Windows o bind mount mascara isso.

Diagnostico: `docker compose logs -f lambda-ai` mostra cada
invocacao (`Event recebido`, `fim exit=... duracao=...`) e o traceback do
handler quando o job falha.

### Cards ilustrados: checkpoint de calibracao visual (so DEV)

Antes de ligar a feature em producao, o prompt e o layout sao calibrados com
imagens reais. Estrategia B (2026-10-03): a OpenAI gera SO a ilustracao (duas
cenas, sem texto, fundo transparente) e o worker compoe o card final
(`api/app/exercise_card_composer.py`: titulo, rotulos, musculos, silhueta do
mapa corporal v1 no rodape e logo, com as fontes de `api/app/assets/fonts/` e
o mapa de `api/app/assets/bodymap/v1/`) logo depois; o preview que o admin revisa
ja e o card final, e a aprovacao so publica. O script
`api/scripts/exercise_card_calibration.py` roda o fluxo REAL (cria a geracao
pelo mesmo servico da rota -> executa o worker do `job_kind="exercise-card"`
em processo -> baixa ilustracao e card do LocalStack), um exercicio por vez,
com a OpenAI DE VERDADE (~US$ 0,05 por imagem em `gpt-image-1.5`/`medium`).
Nao aprova nada.

```
# OPENAI_API_KEY real no .env; compose no ar
docker compose exec api python scripts/exercise_card_calibration.py --exercises 53 22 18 112 58 1 --background transparent
docker compose exec api python scripts/exercise_card_calibration.py --exercises 12 --instruction "pegada mais aberta"
# Iterar o LAYOUT de graca: recompoe a partir das ilustracoes ja pagas (sem OpenAI, sem gravar nada)
docker compose exec api python scripts/exercise_card_calibration.py --compose-only 41 42 43 44
```

- **Onde ficam as imagens:** `api/scripts/calibration-out/<AAAAMMDD-HHMMSS>/`
  no host (fora do controle de versao), por geracao:
  `...-g<gid>-illustration.png` (a ilustracao crua da OpenAI) e
  `...-g<gid>-card.png` (o card composto, igual ao preview); `prancha.png` com
  os cards lado a lado; no modo de geracao tambem o prompt usado
  (`.prompt.txt`) e `summary.csv` (exercicio, status, latencia, custo, bytes,
  erro). A mesma tabela, com p50/p95 de latencia e custo total, sai no
  terminal.
- **`--compose-only <gids>`:** le `exercise-card-drafts/{gid}/illustration.png`
  do LocalStack e o catalogo atual (musculos e slugs do mapa corporal) e
  recompoe com o compositor do checkout, no layout atual (`CARD_LAYOUT_V2`,
  com a silhueta) - e assim que os cards do checkpoint passam do V1 para o V2
  sem pagar a OpenAI de novo. Edite medidas/cores/fontes em
  `exercise_card_composer.py` (secao LAYOUT) e rode de novo - custo zero.
  Geracoes anteriores a Estrategia B nao tem ilustracao guardada
  (`NO_ILLUSTRATION`).
- **`--background transparent|opaque`:** fundo pedido a OpenAI (padrao
  `transparent`, o mesmo do worker; `opaque` so para comparar - o compositor
  recorta por distancia de cor, pior nas bordas).
- **Recusa rodar fora do DEV:** exige `DB_BACKEND=pymysql`, `AWS_ENDPOINT_URL`
  apontando para o LocalStack, nenhuma variavel de runtime Lambda e (so para
  gerar) `OPENAI_API_KEY` configurada. Nada disso existe em `infra/`.
- **Flag:** `EXERCISE_CARD_GENERATION_ENABLED` ja vem `true` no compose de DEV
  (em producao, `false` ate o fim do checkpoint). Para desligar no DEV:
  `EXERCISE_CARD_GENERATION_ENABLED=false` no `.env` e
  `docker compose up -d api`. Com a flag desligada as rotas
  `/exercises/{id}/card-generations*` respondem 503 e o script lista cada
  exercicio como `NOT_CREATED` / `exercise_card_generation_disabled`, sem custo.
- **Limites valem aqui tambem:** 100 geracoes/dia por admin e 2 em andamento
  (`EXERCISE_CARD_MAX_GENERATIONS_PER_DAY`, `EXERCISE_CARD_MAX_IN_FLIGHT` no
  `.env` para ajustar em DEV).
- **Revisar/aprovar:** pela API (`POST /exercises/{id}/card-generations/{gid}/approve`
  ou `/reject`) ou pela tela de admin quando o PR4 chegar. O id da geracao esta
  no nome do arquivo (`...-g<gid>-card.png`). Geracoes do prompt V1 (antes da
  Estrategia B) respondem 409 `not_reviewable` na aprovacao: rejeite ou
  regenere.
- **Smoke sem custo:** `bash scripts/smoke-async-jobs.sh` cobre o contrato dos
  cards; o caminho assincrono completo do card so roda quando a `lambda-ai`
  NAO tem `OPENAI_API_KEY` (CI) - com chave real ele e pulado.

3. Confira a saude dos containers:

   ```
   docker compose ps
   curl http://localhost:8000/ready
   ```

   `/ready` executa um `SELECT 1` contra o MySQL - se retornar
   `{"status": "ready", "db": true}`, a API esta de pe e conectada ao banco.
   `/health` (`{"status": "ok"}`) so confirma que o processo uvicorn subiu,
   sem checar dependencias.

   No boot (apenas quando `DB_BACKEND=pymysql`, ou seja, dev local - nunca em
   producao), a API tambem roda automaticamente os guards `_ensure_schema()`
   dos modulos listados em `app/schema_guards.py` (hoje nenhum: a lista esta
   vazia).

   **Depois de subir um banco novo, rode `python -m app.migrate`** (secao
   "Migracoes versionadas") - o mesmo passo que roda na Aurora antes do deploy.
   O schema que antes vinha de guards de runtime agora vem de migracoes
   versionadas, sem DDL em runtime:
   - `cardio_recommendations` (`025_cardio_recommendations_schema.py`);
   - colunas de sessoes de treino e fichas (`026_sessoes_fichas_schema.py`:
     `activities.workout_plan_id`, `exercises.workout_plan_exercise_id` e as
     demais verificadas por ela);
   - `sleep_metrics`, `manual_effort_metrics`, `intervals_activities`,
     `intervals_wellness`, `reports` e `devices.user_id`
     (`027_integracoes_metricas_schema.py`).

   Sem isso, as rotas de cardio, sessoes de treino, `GET /activities`, sono,
   esforco manual, Intervals.icu, relatorios e dispositivos respondem 500
   (tabela/coluna inexistente).

## Resetando o banco

Para descartar todos os dados e recomeçar do zero (reaplica as migrations do
zero):

```
docker compose down -v
docker compose up -d
# ou: make db-reset
# ou (PowerShell): .\scripts\dev.ps1 db-reset
```

`-v` remove o volume nomeado do MySQL - **isso apaga todos os dados locais**.

## Trazer dados de produção

`scripts/prod_to_dev.py` copia os dados **atuais** de produção de **um único
usuário** (decisão de 2026-10-07: o usuário 1, dono da conta) para o MySQL
local do compose, junto com todo o catálogo global que as telas precisam.
Produção é só lida (RDS Data API, apenas `SELECT`); o destino só pode ser o
MySQL do compose.

Pré-requisitos: compose no ar e migrado (`python -m app.migrate`), `.env` de
DEV na raiz, `boto3` e `PyMySQL` no Python do host, credenciais AWS válidas:

```
aws login --profile musclefit
```

Uso (da raiz do repositório):

```
# 1. só lê produção e grava o SQL (não toca o MySQL local)
python scripts/prod_to_dev.py --user-id 1 --dry-run

# 2. backup do DEV + substituição dos dados (pede "SIM"; --yes pula a pergunta)
python scripts/prod_to_dev.py --user-id 1

# desfazer: restaura o backup feito no passo 2
python scripts/prod_to_dev.py --restore backups/dev-AAAAMMDD-HHMM.sql
```

O SQL gerado vai para `backups/prod-user1-AAAAMMDD-HHMM.sql` (ou `--out`) e o
backup do DEV para `backups/dev-AAAAMMDD-HHMM.sql` (`mysqldump` dentro do
container `mysql`). A pasta `backups/` está no `.gitignore`: os dois arquivos
contêm dados pessoais e nunca vão para o git. Restauração manual equivalente:
`docker compose exec -T mysql mysql -uroot -p<MYSQL_ROOT_PASSWORD> fitness < backups/dev-....sql`.

**O que é copiado**

- Catálogo/global, inteiro: exercícios (`exercise_catalog`, aliases,
  contribuições musculares, mapeamentos externos/Zepp/legado, cards gerados),
  equipamentos e tipos, compatibilidade com técnicas, academias e seus
  equipamentos, grupos musculares, mapa corporal, macro-grupos e faixas,
  técnicas avançadas, tipos de treino e metas, `sources`, `weekly_summaries`.
- Do usuário (`user_id = N`): atividades, Intervals, biometria/sono/composição,
  medidas, check-ins, alertas do dashboard, dispositivos, planos de treino,
  progressão, relatórios, recomendações de cardio, preferências, atribuições
  de Modelo, logs de geração - e os filhos (exercícios, séries, zonas de FC,
  exercícios do plano) e os `imports` referenciados pelas atividades.
- Modelos de treino: os criados pelo usuário e os que ele usa (plano,
  atribuição, geração), com versões, slots, itens, rascunho e intenção
  metodológica. Modelos de outros usuários não vêm.

**O que é limpo/anonimizado**

- `user_sessions`, `password_reset_tokens` e `password_reset_attempts` não são
  copiadas e ficam vazias no DEV (é preciso logar de novo).
- `users`: só a linha do usuário pedido, com o `password_hash` real (a senha de
  produção funciona no DEV); `intervals_api_key_encrypted` = NULL,
  `failed_login_attempts` = 0, `locked_until` = NULL.
- `devices.token_hash`: hash aleatório (o token real do relógio não autentica).
- Id de outro usuário em qualquer coluna copiada (ex.: `gyms.owner_user_id`,
  `training_types.created_by_user_id`, quem atribuiu um Modelo): NULL quando a
  coluna aceita; senão o usuário placeholder `999999999`
  (`usuario-anonimizado`, sem e-mail, inativo, login impossível).
- Fechamento de FKs (as do schema + as referências sem FK listadas em
  `SOFT_REFERENCES`): referência a linha que não veio vira NULL ou derruba a
  linha órfã; o resumo final mostra quantas.

**Travas**

- A migração de produção precisa ser idêntica à do repositório e à do MySQL
  local (`schema_migration_files`, nome e checksum); se divergir, aborta.
- Tabela de produção sem classificação no script aborta (tabela nova exige
  decidir se é global, do usuário ou excluída).
- Destino: só `127.0.0.1`/`localhost`/`mysql`, banco do `.env`
  (`MYSQL_DATABASE`), nunca com `DB_BACKEND=dataapi` (ambiente ou `.env`),
  nunca ARN/host AWS; depois de conectar, recusa Aurora e confere que o
  servidor é o mesmo container `mysql` do compose (o do backup).
- A carga é uma transação só: erro no meio faz rollback.
- O script fica em `scripts/` (fora de `api/`): não é importado pelo app e não
  entra em imagem Docker. Testes: `api/tests/unit/test_prod_to_dev.py`.

**Fotos e cards no DEV**: arquivos do S3 não são copiados; as keys ficam como
em produção. Fotos de equipamento cuja URL o backend monta com
`app.musclefit.com.br` aparecem (vêm da CDN de produção, só leitura). Fotos
servidas pela API (perfil, academia, foto de equipamento por endpoint) e os
cards de exercício (base `PUBLIC_ASSETS_BASE_URL` = LocalStack) ficam sem
imagem, porque o bucket do LocalStack está vazio. Para ver os cards publicados,
dá para apontar `PUBLIC_ASSETS_BASE_URL=https://app.musclefit.com.br` no `.env`
e recriar o container `api` - lembrando de voltar ao valor do LocalStack antes
de gerar cards novos no DEV.

## Seed com dados reais de producao (opcional, contem PII)

Existe um dump real de producao em
`tools/onprem-sync/backups/<timestamp>/restore.sql` (o mais recente é
aplicado automaticamente pelo script). **Esse dump contem dados reais de
usuarios: nomes, e-mails, hashes de senha.** Por isso ele NUNCA é aplicado
automaticamente - é preciso pedir explicitamente:

```
docker compose exec -e SEED_WITH_PROD_DUMP=1 api python scripts/bootstrap_local_db.py
# ou: make seed
# ou (PowerShell): .\scripts\dev.ps1 seed
```

Sem `SEED_WITH_PROD_DUMP=1`, o script so imprime instrucoes e nao faz nada -
seguro de rodar sempre. Use o seed apenas em ambiente local confiavel, nunca
em maquina compartilhada. **Este seed nao e mais necessario apenas para
"destravar" leituras basicas** (isso agora e automatico no boot da API, ver
secao "Subindo o ambiente" acima) - ele continua util para quem quer dados de
exemplo realistas (activities, exercises, users etc.) em vez de um banco
vazio.

**Aplicacao best-effort**: antes de aplicar o dump, o script roda os mesmos
guards `_ensure_schema()` que a API roda automaticamente no boot
(`app/schema_guards.py`; `activities.workout_plan_id` agora vem da migracao
026, aplicada pelo proprio script antes dos guards) para cobrir colunas que so existiam em producao
porque foram criadas de forma lazy, na primeira chamada a um endpoint. Depois
aplica o dump statement a statement (cada um na sua transacao, com
`FOREIGN_KEY_CHECKS=0` garantido na sessao) e **continua mesmo se alguns
falharem**.

### Drift de schema Aurora <-> local (colunas descartadas no seed)

O dump e gerado a partir da Aurora de producao e cada `INSERT` cita a lista
explicita de colunas. O schema local diverge (ou divergia) da Aurora em
alguns pontos:

- `gyms.color_palette`: coluna morta de uma feature descartada que existia na
  Aurora e era removida pelo antigo guard de `app/gyms.py`. Ja nao existe na
  Aurora nem em dev (information_schema de 2026-09-14) e o guard saiu (027).
  Se aparecer num dump antigo, **continua sendo descartada no seed**;
- `equipment.equipment_type_id`, `manufacturer`, `model`, `location` e a
  tabela `equipment_types`: so existiam na Aurora; hoje sao criadas pela
  migracao `022_equipment_types_parity.py` (runner `python -m app.migrate`,
  ver "Migracoes versionadas" abaixo). Sem a 022 aplicada, essas colunas sao
  descartadas e `equipment_types` e pulada.

Antes da reescrita descrita abaixo, esses `INSERT`s falhavam inteiros com
"Unknown column": um banco local seedado ficava com `gyms` e `equipment`
**vazias** e com as tabelas filhas (`gym_equipment_catalog`, `gym_equipment`,
`equipment_exercises`...) apontando para pais inexistentes.

Comportamento atual (`api/scripts/seed_sql_rewrite.py`, testado em
`api/tests/unit/test_seed_sql_rewrite.py`):

- antes de executar cada `INSERT`, o script consulta (uma vez, com cache)
  `information_schema.COLUMNS` do banco alvo e **descarta as colunas do dump
  que nao existem localmente**, junto com os valores correspondentes em todas
  as tuplas - o resto da linha e importado;
- `INSERT` em tabela inexistente localmente e **pulado**;
- `INSERT` sem drift e executado com o texto original, sem alteracao;
- ao final e impresso um resumo por tabela: statements aplicados, falhados,
  pulados e colunas descartadas. Nenhum valor de linha vai para o log
  (mensagens de erro do MySQL tem os trechos entre aspas mascarados).

A decisao e feita consultando o schema real, nao por lista fixa: quando o
schema local ganha colunas/tabelas (ex.: migracao 022), elas deixam de ser
descartadas sem nenhuma mudanca no script. Uma coluna nova aparecendo em
"colunas descartadas" no resumo indica um gap novo Aurora <-> local - a
correcao e uma migracao numerada nova.

**Reexecutar o seed** e seguro: o dump faz `DELETE FROM` em cada tabela antes
dos `INSERT`s, entao as tabelas do dump sao reconstruidas (dados locais
criados nessas tabelas sao perdidos, como sempre). Se alguma linha ainda
assim ja existir, o "Duplicate entry" e contado como falha inofensiva no
resumo. Opcionalmente, `SEED_INSERT_IGNORE=1` troca `INSERT` por
`INSERT IGNORE`; nao e o padrao porque o `IGNORE` do MySQL tambem rebaixa a
warning erros de conversao/truncamento (a linha entraria alterada sem aviso).

### Repovoando um banco local afetado (gyms/equipment vazias)

Ordem canonica (sempre esta, nesta sequencia):

```
# 1. recria o container api se o docker-compose.yml mudou (ex.: montagem
#    ./api/db:/app/db:ro, necessaria para o runner enxergar as migracoes)
docker compose up -d api
# 2. aplica as migracoes versionadas pendentes (ex.: 022 equipment_types)
docker compose exec api python -m app.migrate
# 3. aplica o seed com o dump de producao
docker compose exec -e SEED_WITH_PROD_DUMP=1 api python scripts/bootstrap_local_db.py
```

(`make seed` / `.\scripts\dev.ps1 seed` equivalem ao passo 3.) O proprio seed
tambem chama o runner antes do dump, como rede de seguranca idempotente; se
ele falhar ali, o seed so imprime um AVISO e segue - por isso o passo 2
explicito, cujo erro fica visivel. Os volumes `./api/app` e `./api/scripts`
sao montados no container `api`: nao precisa rebuild de imagem.

Para partir de um banco totalmente limpo (apaga TODOS os dados locais), faca
`docker compose down -v` e `docker compose up -d` antes dos passos 2 e 3.

**Nota sobre `exercise_legacy_id_map`**: portada em
`api/db/migrations/021_exercise_legacy_id_map.sql` (tabela real de producao,
criada por uma migracao paralela no legado nunca capturada em nenhum dos 20
arquivos numerados originais - ver comentario em
`005_exercise_domain_admin.sql`). A tabela existe localmente mas fica
**vazia** por padrao: nao ha como reconstruir o historico real de
consolidacao de exercicios duplicados (old_exercise_id -> canonical_exercise_id)
sem acesso ao dump de producao. Isso nao quebra nada - todo call-site no
backend consome essa tabela via `LEFT JOIN` com fallback (`COALESCE(elm.canonical_exercise_id,
<id>)`) ou via SELECT direto com fallback explicito para o proprio id quando
nao ha linha - mas funcionalidades que dependem de fato de mapeamentos de
exercicios legados/duplicados consolidados (ex.: unificar historico de um
exercicio que foi absorvido por outro) nao terao esse comportamento
espelhado localmente: o ambiente local se comporta como se nenhum exercicio
tivesse duplicata historica consolidada.

## Banco de dados: schema e nome do banco

As migrations em `api/db/migrations/` foram portadas do ambiente on-premise
legado (`fitness-platform/mysql/init/`, 20 arquivos originais, renumerados
sequencialmente `001_..020_` na ordem correta de aplicacao/dependencia - a
ordem original tinha prefixos duplicados/ambíguos, ex. `002`/`002b`,
`003`/variante, `009`/variante, resolvidos por análise de dependência e
mtime). Elas fazem `CREATE DATABASE IF NOT EXISTS fitness` / `USE fitness;`
de forma fixa (herdado do legado) - por isso `MYSQL_DATABASE` no `.env`
**precisa** ser `fitness` para a API (que usa `MYSQL_DATABASE` para escolher
o schema ao conectar) apontar para onde as tabelas realmente foram criadas.
Nao ha ORM nem schema versionado alem desses arquivos SQL; guardas
idempotentes no proprio codigo da aplicacao cobrem colunas adicionadas depois
da ultima migration numerada.

## Migracoes versionadas (`python -m app.migrate`)

A partir da `022`, mudancas de schema entram como migracoes numeradas em
`api/db/migrations/` aplicadas pelo runner `api/app/migrate.py` - o mesmo
codigo que roda em producao na Lambda `musclefit-migrations`, invocada pelo
estagio backend de `deploy-producao.yml` (workflow manual "Deploy produção")
antes de atualizar as demais Lambdas.

Nota de producao: a funcao `musclefit-migrations` e criada pelo `terraform
apply` com a imagem `musclefit-ai-generators:latest` ja existente no ECR e
recebe a imagem do commit no estagio backend de TODO deploy (sempre, mesmo sem
`RUN_DB_MIGRATIONS`; so o `migrate` fica atras dessa variavel). Por isso
`status`/`dry-run` via `db-migrations.yml` so fazem sentido depois de um deploy
de backend concluido.

- `NNN_descricao.sql`: statements idempotentes, executados um a um (sem
  `PREPARE`/`EXECUTE`/`SET @var`/`USE`/`DELIMITER`).
- `NNN_descricao.py`: `def up(db)` com `db.execute()`, `db.query()` e os helpers
  `db.table_exists()`, `db.column_exists()`, `db.index_exists()`,
  `db.fk_exists()` para ALTERs condicionais.
- Arquivos `001..021` sao **baseline**: ja aplicados pelo init do MySQL (e, na
  Aurora, pelo dump). O runner so os registra, sem executar.
- Controle em `schema_migration_files` (checksum de cada arquivo) e lock em
  `schema_migration_lock`. Editar uma migracao ja aplicada faz o runner falhar:
  a correcao e sempre uma migracao nova.

**Importante:** o init do container MySQL so executa os `.sql`; migracoes
`.py` (ex.: `022_equipment_types_parity.py`) sao ignoradas por ele. Depois de
subir um banco novo (`docker compose up` / `db-reset`), rode o runner:

```
# dentro do container api (DB_BACKEND=pymysql e MYSQL_* ja vem do .env;
# api/db e montado read-only em /app/db pelo docker-compose.yml)
docker compose exec api python -m app.migrate --status
docker compose exec api python -m app.migrate --dry-run
docker compose exec api python -m app.migrate
```

Ou do host, apontando para o MySQL local (porta 3306 publicada), com
`PyMySQL` instalado no Python do host:

```
cd api
DB_BACKEND=pymysql MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 \
  MYSQL_USER=<usuario> MYSQL_PASSWORD=<senha> MYSQL_DATABASE=fitness \
  python -m app.migrate
```

(PowerShell: defina as mesmas variaveis com `$env:DB_BACKEND = "pymysql"` etc.
antes de `python -m app.migrate`.) `--migrations-dir` ou `MIGRATIONS_DIR`
sobrescrevem o diretorio padrao. O seed com dump (`bootstrap_local_db.py`) ja
roda o runner antes de importar os dados (best-effort); a ordem recomendada
para repovoar o banco esta em "Repovoando um banco local afetado" acima.

`DB_BACKEND=pymysql` e **so** para dev local/CI: nunca coloque essa variavel em
Terraform/Lambdas (a Lambda de migracao recusa rodar com ela).

## Contexto de build: imagens de producao x dev (ignore files por Dockerfile)

Todas as imagens do backend usam o mesmo contexto `api/`. Para que arquivos
de dev (`tests/`, `scripts/`, `.env*`, `__pycache__`, dumps `*.sql`) nunca
entrem nas imagens das Lambdas, cada Dockerfile de **producao** tem um ignore
file proprio em **allowlist** ao lado dele:

| Dockerfile (producao) | Ignore file | Liberado no contexto |
|---|---|---|
| `api/Dockerfile` | `api/Dockerfile.dockerignore` | `requirements.txt` (lock com hashes), `app/`, `vendor/aws-lambda-web-adapter/` |
| `api/Dockerfile.engines` | `api/Dockerfile.engines.dockerignore` | `requirements.txt` (lock com hashes), `app/`, `vendor/aws-lambda-web-adapter/` |
| `api/Dockerfile.ai` | `api/Dockerfile.ai.dockerignore` | `requirements-ai.txt` (lock com hashes + awslambdaric), `app/`, `db/migrations/` |

O BuildKit (builder padrao do `docker build` desde o Docker Engine 23 e do
Docker Compose v2) usa `<Dockerfile>.dockerignore` com precedencia sobre um
`.dockerignore` da raiz do contexto
([docs](https://docs.docker.com/build/concepts/context/#filename-and-location)).
Por isso **nao existe `api/.dockerignore` global**: os builds de dev
(`Dockerfile.dev`, `Dockerfile.engines.dev`) continuam recebendo o contexto
`api/` inteiro, sem mudanca.

**Regra:** arquivo novo necessario na imagem de producao (ex.: um novo
`COPY` em um Dockerfile de producao, ou um arquivo fora de `app/`) precisa
ser liberado na allowlist do ignore file correspondente - senao o build
falha com "not found" ou o arquivo simplesmente nao entra na imagem. Mesmo
dentro de `app/`, `__pycache__`, `*.pyc`, `.env*`, `*.sql` e `backups/` ficam
sempre fora (so `db/migrations/*.sql` e liberado, e so em `Dockerfile.ai`).

## Apontando o frontend local para esta API

O frontend (`frontend/`, Next.js com `output: "export"`) nao entra no
docker-compose (é so um site estatico depois do build). Para rodar contra a
API local:

```
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

(ou defina `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` num `.env.local`
dentro de `frontend/` - ver `frontend/src/lib/config.ts` para o default de
producao que esse valor sobrepoe).

### DEV no desktop e no celular (Tailscale): marcador `{hostname}`

O DEV que tambem e aberto pelo celular via Tailscale deve usar, no
`frontend/.env.local`:

```
NEXT_PUBLIC_API_BASE_URL=http://{hostname}:8000
```

No navegador, `{hostname}` vira o `window.location.hostname` da pagina:
`http://localhost:8000` quando o app e aberto em `http://localhost:3000` no
desktop, e `http://<maquina>.<tailnet>.ts.net:8000` quando aberto pelo nome
Tailscale no celular. No servidor/prerender (sem `window`) vira `localhost`.

Por que: o cookie de sessao (`musclefit_session`, SameSite=Lax, sem `Domain`
em dev) e emitido pelo host da API. Com a URL fixa no nome `ts.net`, o desktop
aberto em `localhost:3000` faz um login cross-site: `POST /auth/login` responde
200, mas o cookie nao volta no `/auth/me`, que responde 401, e o app fica em
loop na tela de login. Com o marcador, pagina e API ficam sempre no mesmo host.

Sem o marcador o valor e usado como esta (comportamento anterior). Producao
nao define a variavel e usa a URL fixa `https://api.musclefit.com.br`
(`resolveApiBaseUrl`/`getApiBaseUrl` em `frontend/src/lib/config.ts`). Depois
de mudar o `.env.local`, reinicie o `next dev` (o valor e lido em build-time).

### "Esqueci minha senha" (M5): e-mail via SES emulado + captcha Turnstile

O fluxo publico de redefinicao (`POST /auth/password-reset/request` e
`/confirm`, `api/app/password_reset.py`) usa o MESMO codigo em DEV e em
producao: e-mail por `boto3.client("ses")` e captcha Cloudflare Turnstile
(`api/app/captcha.py`). O que muda e so o ambiente:

| Variavel | DEV (`.env.example`) | Producao (Lambda `musclefit-api-core`, PR de infra) |
|---|---|---|
| `PASSWORD_RESET_FROM_EMAIL` | `no-reply@musclefit.local` (verificado no LocalStack pelo `00-bootstrap.sh`) | remetente com identidade SES verificada |
| `APP_BASE_URL` | `http://localhost:3000` | `https://app.musclefit.com.br` |
| `TURNSTILE_SECRET_KEY` | chave secreta de TESTE da Cloudflare (`1x0000000000000000000000000000000AA`, sempre aprova) | resolvida de `TURNSTILE_SECRET_KEY_SECRET_ARN` (Secrets Manager) por `secrets_bootstrap.py` |
| `NEXT_PUBLIC_TURNSTILE_SITE_KEY` | site key de TESTE (`1x00000000000000000000AA`), em build-time | site key real, injetada no `next build` do deploy |

Sem `TURNSTILE_SECRET_KEY` a rota de solicitacao responde **503** (fail-closed,
sem bypass de dev); sem `PASSWORD_RESET_FROM_EMAIL`/`APP_BASE_URL` ela continua
respondendo 202 e loga o evento `password_reset.email_not_configured`.

Para o widget aparecer no `npm run dev`, coloque
`NEXT_PUBLIC_TURNSTILE_SITE_KEY=1x00000000000000000000AA` no `frontend/.env.local`
(junto com `NEXT_PUBLIC_API_BASE_URL`). O e-mail nao sai da sua maquina: o
LocalStack guarda as mensagens aceitas e o link `/redefinir-senha?token=...`
pode ser lido em:

```
curl -s http://localhost:4566/_aws/ses | python -m json.tool
```

Depois de subir um banco novo, rode `python -m app.migrate` (a 043 cria
`password_reset_tokens`/`password_reset_attempts`). Rate limit por IP e por
conta vive nessa segunda tabela (janela de 15 min), sem Redis.

Numa stack criada ANTES do M5 o container do LocalStack ainda roda sem `ses`
na lista `SERVICES` (docker-compose.yml): recrie-o uma vez com
`docker compose up -d --force-recreate localstack` para o `send_email` parar
de responder 500 e o bootstrap verificar o remetente.

### `next dev` x producao nas rotas dinamicas

Em producao o frontend e o `out/` do `next build` (`output: "export"`) servido
por S3 + CloudFront. Rotas dinamicas (`/metas-ativacao/[id]`,
`/modelos-treino/[id]`, `/planos-treino/[id]`,
`/registro-exercicios/[activityId]`) so geram `.../placeholder/index.html`, e
a CloudFront Function `infra/cloudfront-functions/spa-index-rewrite.js` serve
esse objeto para qualquer id (`novo`, `123`...); o id real e lido no navegador
(`frontend/src/lib/use-dynamic-route-param.ts`). Ela tambem responde 301 de
`/tipos-treinamento/...` para `/metas-ativacao/...`.

O `next dev` nao tem essa funcao. Por isso `frontend/next.config.mjs` desliga
`output: "export"` **somente** na fase `PHASE_DEVELOPMENT_SERVER`: com export
ligado, o dev server respondia 500 (`missing param ... in
"generateStaticParams()"`) em "Novo"/editar. No dev o id real chega direto por
`useParams()` e o redirect legado e feito no cliente (`LegacyPathRedirect`).
`next build` continua com export - o artefato publicado nao muda.

### Validando o comportamento de producao localmente

O `next dev` e conveniente, mas nao e a referencia. Para testar o artefato real:

```
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run preview:prod   # ou: make preview-prod
```

Isso roda `next build` e serve `out/` em http://127.0.0.1:4000 com
`scripts/serve-export.mjs`, que imita a borda: executa a CloudFront Function
real (carregada do arquivo em `infra/`, sem copia da regra), busca a key exata
como a origem S3 via OAC e aplica o `custom_error_response` (403/404 ->
`/index.html` com 200). Nao ha hot reload: rode de novo apos mudar codigo.

`npm run smoke:export` (ou `make smoke-export`, que builda antes) faz um smoke
HTTP sobre o `out/` ja buildado: rotas dinamicas servem o objeto
`placeholder` correto (e seus chunks JS existem) e os redirects legados
respondem 301. O mesmo smoke roda no CI (`frontend build (next export)`).

## Testes automatizados

Os testes rodam no host, fora dos containers. O backend usa pytest
(`pip install --require-hashes -r requirements-dev.txt` a partir de `api/`;
esse lock e Linux/Python 3.12, entao no host Windows use
`pip install -r requirements-dev.in`):
`python -m pytest tests/unit` nao precisa de banco, e
`python -m pytest tests/integration` precisa de um MySQL 8 acessivel. Por
padrao os testes de integracao usam o MySQL do compose (`127.0.0.1:3306`,
`root`/`musclefit-root`); outro servidor e configurado com `TEST_MYSQL_HOST`,
`TEST_MYSQL_PORT`, `TEST_MYSQL_USER` e `TEST_MYSQL_PASSWORD`. A fixture de
sessao recria **so** schemas descartaveis derivados de `TEST_MYSQL_DATABASE`
(default `musclefit_test`; o nome precisa terminar em `_test`) e aplica
`api/db/migrations/*.sql` neles. O banco `fitness` de dev nunca e tocado.

Ciclo de vida desses schemas (`api/tests/support/schema_lifecycle.py`):

- o nome base ganha um token unico por execucao
  (`musclefit_run<token>_test`), herdado pelos schemas auxiliares
  (`musclefit_run<token>_p028_test`, ...), para que duas execucoes simultaneas
  nao disputem o mesmo schema (`TEST_MYSQL_UNIQUE_SCHEMA=0` desliga, util para
  inspecionar o schema depois da suite);
- ao final da sessao - inclusive com testes falhando ou Ctrl-C - a fixture
  autouse `schema_lifecycle_guard` derruba todos os schemas daquela execucao;
- no inicio da sessao a mesma fixture varre schemas de teste orfaos de
  execucoes anteriores. Uma execucao viva se anuncia com um lock nomeado do
  MySQL (`GET_LOCK`) e por isso nunca e varrida por outra rodando em paralelo;
- o filtro de selecao (`is_disposable_test_schema`) exige prefixo do stem +
  sufixo `_test` e tem denylist explicita: `fitness`, `musclefit`, `mysql`,
  `information_schema`, `performance_schema` e `sys` nunca sao selecionados.
  `api/tests/unit/test_schema_lifecycle_filtro.py` guarda essa regra. `DB_BACKEND=pymysql` e forcado apenas
dentro do processo de teste (`api/tests/conftest.py`), nunca na aplicacao ou
na infra. Fixtures de catalogo reutilizaveis (grupos musculares, exercicios,
contribuicoes, equipamentos, academia) ficam em
`api/tests/integration/conftest.py`. O frontend usa vitest:
`cd frontend && npm test` roda `src/**/*.test.ts`. Atalhos: `make test`,
`make test-unit`, `make test-integration` e `make test-frontend`. No CI
(`.github/workflows/ci-develop.yml`) a suite roda filtrada por area (ver
abaixo).

### O que o CI roda em cada PR (rotulo `ci-completo`)

Desde 2026-10-10 o `ci-develop.yml` comeca por um job `changes`, que le os
arquivos do PR (diff do merge commit de teste contra a base, sem action de
terceiro) e decide o que roda:

| Job | PR para `develop` | PR para `develop` com rotulo `ci-completo` | PR para `main` (promocao) |
|---|---|---|---|
| `lint backend` + `backend unit` | se mudou `api/**`, `docker-compose.yml`, `localstack/**`, `dev/**`, `nginx/**` (inclui o submodulo `api/privado`), `.gitmodules`, `.env.example`, `scripts/smoke-async-jobs.sh` ou o workflow | sempre | sempre |
| `lint frontend` + `frontend build` (vitest, build, smoke do export) | se mudou `frontend/**`, `infra/cloudfront-functions/**` ou o workflow | sempre | sempre |
| `backend tests` (integracao + dataapi, MySQL) | nao roda | sempre | sempre |
| `backend smoke test` (compose + LocalStack + jobs assincronos) | nao roda | sempre | sempre |
| `ci-docker-images.yml` (3 imagens Lambda) | se mudou `api/Dockerfile*`, `api/requirements*`, um `.dockerignore`, `infra/lambda_*.tf`, `.github/scripts/*` ou o workflow | sempre | sempre |

- **Quando pedir `ci-completo`:** PR que mexe em SQL, migracao, repositorio,
  shim da Data API, compose/LocalStack, handlers de Lambda ou dependencias.
  `gh pr edit <n> --add-label ci-completo` dispara a rodada completa na hora
  (evento `labeled`) e o rotulo continua valendo nos pushes seguintes. Remover
  o rotulo nao dispara nada; o proximo push volta ao modo filtrado.
- **Promocao `develop -> main`:** roda sempre tudo. A `main` continua
  protegida pela suite inteira; o que a develop deixou de rodar e pego ali.
- **Veredito:** job pulado aparece como *skipping*, nunca pendente. Nao ha
  job agregador: o ruleset de `main`/`develop` (repositorio publico desde
  2026-10-29) exige PR e bloqueia force-push e exclusao, mas nao tem check
  obrigatorio, entao o agregador nao protegeria nada; o environment
  `production` exige reviewer. O criterio de merge avalia todos os checks
  (`pass` ou `skipping`).
- **Concorrencia:** um push novo no mesmo PR cancela a rodada anterior em
  todos os workflows de PR (`ci-develop`, `ci-docker-images`, `terraform`,
  `ci-deploy-lambda-coverage`, `ci-cloudfront-functions`). O
  `deploy-producao.yml` e o `db-migrations.yml` mantem o grupo proprio
  `backend-deploy` sem cancelamento.
- **Branch protection:** o repositorio e privado no plano gratuito, entao a
  API de protecao de branch/rulesets responde 403 e nao ha check obrigatorio
  configurado; o criterio de merge e o CI verde (`pass` ou `skipping`).

## Smoke das imagens Lambda de producao

As 3 imagens de producao (`api/Dockerfile`, `api/Dockerfile.engines` e
`api/Dockerfile.ai`) so sao publicadas no ECR pelo
estagio backend de `deploy-producao.yml`, disparado manualmente na `main`
(merge na `main` nao publica). Para um erro de COPY, de dependencia ou
de import de handler nao aparecer so no deploy, o workflow
`.github/workflows/ci-docker-images.yml` builda as 3 (linux/amd64, igual ao
deploy, sem push e sem credencial AWS) e roda
`.github/scripts/smoke-lambda-image.sh` na promocao para `main`, em PR com o
rotulo `ci-completo` e em PR contra `develop` que mexa na imagem (Dockerfile,
locks, `.dockerignore`, `infra/lambda_*.tf`, `.github/scripts/*`; ver
"O que o CI roda em cada PR"). O smoke:

- importa, dentro da imagem e com `--network none`, **todos** os handlers que o
  Terraform configura para aquela imagem (`image_config.command` em
  `infra/lambda_*.tf`, lido por `.github/scripts/lambda_image_handlers.py`; sem
  `command`, usa o CMD da imagem: handler do awslambdaric ou `uvicorn
  modulo:app` nas imagens com Lambda Web Adapter);
- falha se a imagem contiver `tests/`, `scripts/`, `requirements-dev.txt`,
  `.env*`, `conftest.py`, `backups/`/`onprem-sync/`/`tools/` ou dumps;
- confere a extensao do Lambda Web Adapter (presente em api-core/engines,
  ausente nas imagens awslambdaric);
- confere que o Pillow da imagem codifica WebP (`PIL.features.check("webp")`
  + encode real em memoria) e, na api-core (`Dockerfile`) e no worker
  (`Dockerfile.ai`, que compoe o card), que o logo oficial dos cards
  ilustrados de exercicios (`app/assets/branding/musclefit-logo.png`) esta na
  imagem, RGBA e com largura >= 1024 px, que as fontes de `app/assets/fonts/`
  (Oswald + Inter) carregam e renderizam `ÁÂÃÀÇÉÊÍÓÔÕÚáâãàçéêíóôõú` sem cair no
  glifo `.notdef`, que o mapa corporal v1 (`app/assets/bodymap/v1/`) esta
  inteiro (sha256 de cada arquivo = `manifest.json`) e renderiza
  (`app.body_map_render` e o `muscle-map.png`), e que o compositor monta um
  card de verdade, com a silhueta do rodape;
- so `Dockerfile.ai`: `/var/task/db/migrations` identico a `api/db/migrations`,
  `app.migrate` resolvendo esse diretorio e validando as migracoes nao-baseline,
  e `app.migrate.handler` invocado pelo Lambda Runtime Interface Emulator com
  `DB_BACKEND=pymysql`, que precisa **recusar** executar (`MigrationError`). A
  base e `python:3.12-slim` + `awslambdaric` (nao `public.ecr.aws/lambda/python`),
  entao o RIE nao vem na imagem: o script baixa o binario oficial pinado
  (versao + sha256, com `curl --retry` para 5xx transitorio do GitHub
  Releases) e o copia so para um container descartavel. A invocacao passa por
  `.github/scripts/rie_invoke.py` (funcao `rie_invoke` do script): espera o
  RIE abrir a porta e repete **so** falha transitoria de transporte
  (502/503/504 sem `errorType` no corpo, conexao recusada/resetada; ate 5
  tentativas em ~30 s). Qualquer resposta do handler/runtime (inclusive o 502
  com `errorType` que o RIE devolve quando o init falha) e definitiva, e em
  falha o `docker logs` do container e impresso.

Para rodar localmente (Docker, Git Bash no Windows ou bash no Linux/macOS, a
partir da raiz do repo; nao usa o `docker compose` nem os containers
`muscle_fit-*`):

```bash
docker build --platform linux/amd64 --provenance=false --sbom=false \
  -f api/Dockerfile.ai -t mfsmoke-ai:local api
.github/scripts/smoke-lambda-image.sh Dockerfile.ai mfsmoke-ai:local
docker image rm mfsmoke-ai:local
```

Troque `Dockerfile.ai` por `Dockerfile` ou `Dockerfile.engines` para as
outras imagens. Os containers criados pelo smoke
sao removidos ao final, mesmo em caso de falha.

## Recursos AWS emulados (LocalStack)

| Recurso | Nome | Origem (Terraform) |
|---|---|---|
| S3 bucket | `musclefit-uploads` (bucket policy de GET anonimo declarada so em `exercise-cards/*`, espelho do escopo da CDN de producao; o LocalStack Community nao aplica bucket policy, entao em DEV todo prefixo responde a GET anonimo) | `infra/s3_uploads.tf`, `infra/cloudfront_uploads.tf` |
| S3 bucket | `musclefit-rag` | `infra/s3_rag.tf` |
| DynamoDB table | `musclefit-async-jobs` (hash key `job_id`, PAY_PER_REQUEST, TTL no atributo `ttl`) | `infra/dynamodb.tf` |
| Secrets Manager | `musclefit/openai-api-key` | `infra/secrets.tf` |
| Secrets Manager | `musclefit/api-token` | `infra/secrets.tf` |
| Secrets Manager | `musclefit/secret-encryption-key` | `infra/secrets.tf` |
| SES identidade | e-mail de `PASSWORD_RESET_FROM_EMAIL` (default `no-reply@musclefit.local`) | identidade SES do PR de infra (M5) |

Para uso local do dia a dia, normalmente nao e preciso que a API busque esses
segredos no Secrets Manager: basta definir `OPENAI_API_KEY`, `API_TOKEN` e
`SECRET_ENCRYPTION_KEY` diretamente no `.env` (que e o que `.env.example` ja
faz) - `api/app/secrets_bootstrap.py` so tenta o Secrets Manager quando as
env vars `*_SECRET_ARN` estao definidas (nao estao neste ambiente local).

Para inspecionar os recursos manualmente (chamadas de FORA do compose, da sua
maquina):

```
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION=sa-east-1 \
  aws --endpoint-url=http://localhost:4566 s3 ls
```

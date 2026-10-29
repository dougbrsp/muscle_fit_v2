---
name: promover-producao
description: Ciclo de promocao develop -> main -> deploy de producao (PR, checks pelo exit status, merge, workflow deploy-producao.yml so das partes que mudaram, verificacao). So com permissao de producao dada NESTA conversa para ESTA promocao.
disable-model-invocation: true
---

# Promover a develop para producao

## Trava (ler antes de qualquer comando)

Producao esta CONGELADA. So execute esta skill se o usuario deu permissao de producao
**nesta conversa** para **esta** promocao. A permissao vale para uma promocao so: a proxima
precisa de nova permissao. Sem ela, pare aqui e pergunte. A develop continua liberada.

Bloqueio do classificador do auto mode NAO e recusa do usuario: explique o que foi bloqueado
e por que, e peca confirmacao no chat.

## 1. O que vai junto

```bash
git fetch origin
git log --oneline origin/main..origin/develop
git diff --name-only origin/main...origin/develop
```

- Classifique: migracao (`api/db/migrations/`)? backend (`api/`)? frontend (`frontend/`)?
  terraform (`infra/`)? workflows (`.github/`)? replay de progressao pendente?
- Confira que o DEV local esta atualizado com essa develop (skill `atualizar-dev`):
  DEV > PROD, nunca o contrario.
- Confira que o ultimo PR mesclado na develop foi criado da ponta dela (nada ficou para tras).
- Informe ao usuario a lista antes de seguir.

## 2. PR develop -> main

```bash
gh pr create --base main --head develop --title "Promocao: <resumo>" --body-file <arquivo>
```

Corpo: lista dos PRs/commits que entram + secao **"Impacto em produção"** (o que muda para o
usuario, migracao, replay, risco, rollback). Promocao em lote: acumule na develop e promova
uma vez (economia de GitHub Actions).

## 3. Checks pelo exit status

```bash
gh pr checks <n> --watch            # sem pipe
# ou
gh pr checks <n> --json name,bucket --jq 'all(.[]; .bucket=="pass" or .bucket=="skipping")'
```

Nunca `gh pr checks <n> | tail ...` antes de mesclar (o pipe engole o exit status; o #430
entrou com plan vermelho assim; o hook `guarda_comandos.py` bloqueia). "no checks reported"
e normal quando nada em `infra/`/`.github/` mudou. Integracao/smoke rodam neste PR
develop -> main.

## 4. Merge na main

```bash
gh pr merge <n> --merge
```

## 5. Deploy so das partes que mudaram

```bash
gh workflow run deploy-producao.yml --ref main \
  -f run_terraform=<true|false> -f run_backend=<true|false> -f run_frontend=<true|false>
```

A migracao roda no job de backend, antes das Lambdas. Replay de progressao
(`db-migrations.yml`, `action=replay`) so se o lote pedir: agrupe UM so por promocao.

## 6. Acompanhar e verificar

```bash
gh run list --workflow deploy-producao.yml --limit 1
gh run watch <run-id> --exit-status
```

- Lambdas (leitura): `aws lambda list-functions --profile musclefit` (versao/LastModified).
- API: `curl -s https://api.musclefit.com.br/health`.
- Frontend: pagina afetada em `https://app.musclefit.com.br/<pagina>/` - compare o hash do
  chunk da pagina antes e depois do deploy.
- Reporte ao usuario: PR, run, o que foi verificado e o resultado.

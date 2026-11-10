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
gh pr checks <n> --watch            # so espera, sem pipe e sem decidir
gh pr view <n> --json headRefOid --jq .headRefOid   # deve ser o SHA do ultimo push
# depois (decisao, sem pipe; pode ser encadeada ao merge com &&):
gh pr checks <n> --json bucket --jq 'if length > 0 and all(.[]; .bucket == "pass" or .bucket == "skipping") then "VERDE" else error("checks nao verdes") end'
```

So VERDE (exit 0) libera o merge. `--json bucket` sem `error()` sai com 0 mesmo com check
pendente ou vermelho (medido nos PRs #109 e #34), por isso nao serve de decisao. Se o head
divergir do SHA do ultimo push (o que o `git push` informou, ou `git rev-parse HEAD` da
branch publicada), pare: nao decida nem mescle. Logo apos um push, inclusive o que poe
`ci-completo`, a lista pode ainda nao ter os checks do commit novo (precaucao, nao medida):
espere com `--watch` e confira o head antes de decidir. Rode o comando de decisao
imediatamente antes do merge e confira que a lista inclui os checks que os caminhos do PR
disparam; na duvida, espere e rode de novo.

PR sem nenhum check ("no checks reported", exit 1) nao e verde. So libera o merge se
`gh pr diff <n> --name-only` nao tocar nenhum caminho dos `paths` dos workflows
(`.github/workflows/*.yml` e a autoridade; o CLAUDE.md resume a lista). Na duvida, trate
como "toca caminho" e investigue. Integracao/smoke rodam neste PR develop -> main, entao
ele sempre dispara CI: lista vazia aqui e anomalia, pare e investigue.

Nunca `gh pr checks <n> | tail ...` antes de mesclar: o pipe engole o exit status (o #430
entrou com plan vermelho assim; o hook `guarda_comandos.py` bloqueia). Encadear
`<comando de decisao> && gh pr merge <n> --merge` e permitido, porque o `&&` so segue com
exit 0; pipe (`|`) continua proibido em qualquer um dos dois comandos.

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

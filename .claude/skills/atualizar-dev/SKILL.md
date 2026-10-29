---
name: atualizar-dev
description: Atualiza o DEV local (C:\muscle_fit, Docker + next dev) depois de merges na develop, antes de pedir teste ao usuario. Receita testada - merge da origin/develop, containers, migracao, frontend com .next limpo, verificacao.
disable-model-invocation: true
---

# Atualizar o DEV local

Use depois de mesclar PR(s) na `develop` e ANTES de pedir ao usuario para testar.
DEV > PROD, nunca o contrario: o DEV precisa ter tudo o que vai para producao.

Tudo roda no checkout principal `C:\muscle_fit` (nao em worktree). O script
`scripts/atualizar-backend.sh` desta skill faz os passos 1, 2 e a verificacao do backend.

## 1. Codigo

```bash
cd /c/muscle_fit
git fetch origin && git merge --no-edit origin/develop
git submodule update --init api/privado
git diff --quiet origin/develop develop && echo "develop local = origin/develop"
ls api/privado/rag/markdown/*.md >/dev/null && echo "RAG privado presente"
```

- M103 P0: o RAG mora no submodulo privado `api/privado` (repositorio
  `muscle_fit_privado`). O `submodule update` leva o RAG ao commit que a develop
  aponta; sem ele o compose monta um diretorio vazio em `/var/task/rag` e as
  telas/IA de DEV ficam sem RAG em silencio. Ver `docs/local-dev.md`.

- `git merge --ff-only` FALHA aqui: a develop local acumula commits de merge. Use o merge normal.
- Se `git diff --quiet` acusar diferenca, pare e investigue antes de seguir.
- Anote o que o merge tocou: `git diff --name-only ORIG_HEAD HEAD` (backend? `frontend/`?
  migracao em `api/db/migrations/`? `requirements*.txt`, Dockerfile, `docker-compose.yml`?).

## 2. Backend

```bash
docker compose restart api api-engines
# o merge tocou api/? reconstrua TAMBEM a lambda-ai (ver abaixo):
docker compose up -d --build lambda-ai
# mudou requirements*.txt, Dockerfile*, docker-compose.yml? entao, em vez do restart:
# docker compose up -d --build
docker compose exec -T api python -m app.migrate
```

- `api` e `api-engines` montam o codigo de `api/` por volume: o `restart` basta.
- `lambda-ai` (equivalente da `musclefit-plan-generator`: geracao de ficha assincrona,
  periodizacao, cards, descricao de exercicio) e uma imagem `Dockerfile.ai` que COPIA o
  codigo no build e NAO monta `api/`. So o `restart` deixa o container com o codigo velho:
  em 10/2026 ele ficou 4 dias assim e a geracao de ficha rodou sem o M97 (descansos e
  cargas de hipertrofia num Modelo de forca). Sempre que o merge tocar `api/`, rode
  `docker compose up -d --build lambda-ai` e confira que ele subiu
  (`docker compose ps lambda-ai`).

## 3. Frontend (sempre que o merge tocou `frontend/`)

1. Parar o `next dev` da porta 3000 E o processo pai (PowerShell):
   ```powershell
   $c = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
   foreach ($x in $c) {
     $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($x.OwningProcess)"
     Stop-Process -Id $x.OwningProcess -Force
     if ($p.ParentProcessId) { Stop-Process -Id $p.ParentProcessId -Force -ErrorAction SilentlyContinue }
   }
   ```
2. Apagar o cache PELO BASH (o PowerShell bloqueia `Remove-Item` sob `C:\muscle_fit`):
   ```bash
   rm -rf /c/muscle_fit/frontend/.next
   ```
3. Subir de novo (PowerShell):
   ```powershell
   Start-Process npx.cmd -ArgumentList 'next','dev','-H','0.0.0.0','-p','3000' -WindowStyle Hidden -WorkingDirectory C:\muscle_fit\frontend
   ```
4. Conferir as paginas tocadas COM barra final (sem ela o `trailingSlash` responde 308):
   `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/<pagina>/`

## 4. Dependencia nova no frontend

```bash
cd /c/muscle_fit/frontend && npm install
git checkout -- package-lock.json   # o npm do Windows remove as entradas @emnapi do lock
```

Nunca `npm ci` com o `next dev` do usuario no ar (apaga o `node_modules` em uso).

## 5. Verificar e so entao pedir o teste

- `curl -s http://localhost:8000/health` responde ok.
- `docker compose logs --since 5m api api-engines lambda-ai` sem erro/traceback.
- Checagem funcional do que mudou (rota nova responde, pagina abre, numero esperado aparece).
- Informe ao usuario: commits que entraram, o que foi reiniciado/migrado, o que foi conferido,
  e o que ele deve testar.

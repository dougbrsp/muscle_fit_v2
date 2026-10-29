#!/usr/bin/env bash
# Passos 1-2 da skill atualizar-dev + verificacao do backend.
# Uso (Git Bash):  bash .claude/skills/atualizar-dev/scripts/atualizar-backend.sh [--build]
#   --build  usa `docker compose up -d --build` (mudou requirements/Dockerfile/compose)
# O frontend fica descrito no SKILL.md (precisa de PowerShell para parar/subir o next dev).
set -euo pipefail

REPO=/c/muscle_fit
cd "$REPO"

echo "== 1. codigo"
git fetch origin
git merge --no-edit origin/develop
if ! git diff --quiet origin/develop develop; then
  echo "ERRO: develop local difere de origin/develop depois do merge. Investigue antes de seguir." >&2
  exit 1
fi
# M103 P0: RAG no submodulo privado api/privado (muscle_fit_privado).
git submodule update --init api/privado
if ! ls api/privado/rag/markdown/*.md >/dev/null 2>&1; then
  echo "ERRO: api/privado/rag/markdown vazio depois do submodule update (acesso ao muscle_fit_privado?)." >&2
  exit 1
fi
echo "arquivos tocados pelo merge:"
git diff --name-only ORIG_HEAD HEAD | sed 's/^/  /' || true
if git diff --name-only ORIG_HEAD HEAD | grep -q '^frontend/'; then
  echo "ATENCAO: o merge tocou frontend/ -> faca o passo 3 do SKILL.md (parar next dev, rm -rf .next, subir)."
fi

echo "== 2. backend"
if [ "${1:-}" = "--build" ]; then
  docker compose up -d --build
else
  docker compose restart api api-engines
  # lambda-ai (Dockerfile.ai) COPIA o codigo no build e nao monta api/: so o
  # restart a deixaria com o codigo velho (a geracao de ficha roda nela).
  if git diff --name-only ORIG_HEAD HEAD | grep -q '^api/'; then
    echo "o merge tocou api/ -> reconstruindo lambda-ai"
    docker compose up -d --build lambda-ai
  fi
fi
docker compose exec -T api python -m app.migrate

echo "== verificacao"
for i in $(seq 1 30); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS http://localhost:8000/health && echo
if docker compose logs --since 5m api api-engines lambda-ai 2>&1 | grep -E "Traceback|ERROR" ; then
  echo "ATENCAO: ha erro nos logs acima." >&2
  exit 1
fi
echo "backend ok. Falta: frontend (se tocado) e a checagem funcional do que mudou."

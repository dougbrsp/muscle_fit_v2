#!/bin/sh
# Recompila os locks com hashes (M47) a partir dos *.in, DENTRO da mesma
# imagem base das Lambdas (Linux, Python 3.12): o resolvedor do pip-tools so
# fixa as dependencias do ambiente em que roda (ex.: uvloop so existe fora do
# Windows), entao compilar no host Windows geraria um lock errado.
#
# Uso (de qualquer diretorio):
#   sh api/scripts/lock-requirements.sh            # mantem as versoes atuais
#   sh api/scripts/lock-requirements.sh --upgrade  # sobe as transitivas
#
# Saida: api/requirements.txt (Dockerfile e os .dev),
# api/requirements-engines.txt (Dockerfile.engines, sem o SDK anthropic),
# api/requirements-ai.txt (Dockerfile.ai) e api/requirements-dev.txt (CI).
# Os tres ultimos usam requirements.txt como constraint: mesmas versoes.
# api/requirements-lint.txt (so o ruff, sem dependencias) e o lock do job de
# lint do CI; independe dos demais.
set -eu
API_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Mesmo digest e mesma origem (ECR Public, AWS-12) do FROM dos Dockerfiles (M48).
BASE_IMAGE="public.ecr.aws/docker/library/python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"
PIP_TOOLS_VERSION="7.6.2"
EXTRA="${1:-}"

# Volume de cache: o pip-tools baixa TODOS os arquivos de cada versao para
# calcular os hashes (~1,6 GB na primeira vez); o cache torna as proximas rapidas.
MSYS_NO_PATHCONV=1 docker run --rm -v "$API_DIR:/work" -w /work \
  -v musclefit-pip-lock-cache:/root/.cache \
  -e CUSTOM_COMPILE_COMMAND="sh api/scripts/lock-requirements.sh" \
  "$BASE_IMAGE" sh -c "
    set -e
    pip install --quiet --root-user-action=ignore pip-tools==$PIP_TOOLS_VERSION
    OPTS='--quiet --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url $EXTRA'
    pip-compile \$OPTS --output-file requirements.txt requirements.in
    pip-compile \$OPTS --constraint requirements.txt --output-file requirements-ai.txt requirements-ai.in
    pip-compile \$OPTS --constraint requirements.txt --output-file requirements-engines.txt requirements-engines.in
    pip-compile \$OPTS --constraint requirements.txt --output-file requirements-dev.txt requirements-dev.in
    pip-compile \$OPTS --output-file requirements-lint.txt requirements-lint.in
  "

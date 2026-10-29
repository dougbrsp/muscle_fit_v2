#!/usr/bin/env bash
# M82 - prova barata e SEM credencial do caminho que o deploy-producao.yml usa
# para a imagem base Python:
#   escolher-base-python.sh --pull  +  builder `default` (driver docker)
#   +  docker buildx build --build-context "<FROM>=docker-image://<origem>"
# Nenhuma outra CI exercita essa combinacao: o ci-docker-images.yml builda com
# driver docker-container (setup-buildx-action) e o compose do ci-develop.yml
# usa additional_contexts. Se a chave do contexto nomeado nao casar com o FROM,
# o BuildKit IGNORA o contexto sem avisar e puxa do FROM original
# (public.ecr.aws); esta prova reprova nesse caso.
#
# O que faz:
#   1. roda escolher-base-python.sh --pull na ordem padrao da CI (espelho
#      primeiro), lendo todos os api/Dockerfile*;
#   2. gera um Dockerfile minimo num diretorio temporario: o MESMO FROM dos
#      api/Dockerfile* + `RUN true`;
#   3. builda com `docker buildx build --builder default --platform
#      linux/amd64 --provenance=false --sbom=false --progress=plain
#      --build-context "$CTX"` (mesmo driver e mesmas flags do deploy);
#   4. confere no log:
#      - o build terminou e usou o driver docker;
#      - a base foi resolvida PELO CONTEXTO a partir da origem escolhida
#        ("[context <FROM>] load metadata for <origem>@<digest>");
#      - quando a origem escolhida nao e o public.ecr.aws, nenhuma linha cita
#        public.ecr.aws fora do rotulo "[context <FROM>]" (ou seja, nenhuma
#        resolucao/requisicao ao ECR Public).
#
# Uso (a partir da raiz do repo):
#   .github/scripts/provar-contexto-base.sh [build_context]
#   build_context opcional substitui o devolvido pelo escolher-base-python.sh;
#   serve so para provar localmente o caso negativo (ex.: uma chave que nao
#   casa com o FROM), que tem que REPROVAR.
set -uo pipefail

RAIZ_SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
falha() { echo "::error::M82: $*" >&2; exit 1; }

saidas=$("$RAIZ_SCRIPTS/escolher-base-python.sh" --pull) || falha "escolher-base-python.sh --pull nao achou origem para a base"
valor() { printf '%s\n' "$saidas" | sed -n "s/^$1=//p"; }
REF=$(valor ref)
ORIGEM=$(valor origem)
REGISTRO=$(valor registro)
CTX="${1:-$(valor build_context)}"

dir=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/m82-contexto.XXXXXX")
printf 'FROM %s\nRUN true\n' "$REF" > "$dir/Dockerfile"
TAG="m82-prova-contexto:$$"
log="$dir/build.log"

echo "Dockerfile minimo:"; sed 's/^/  /' "$dir/Dockerfile"
echo "--build-context $CTX"
docker buildx build --builder default --platform linux/amd64 \
  --provenance=false --sbom=false --progress=plain \
  --build-context "$CTX" -t "$TAG" "$dir" > "$log" 2>&1
rc=$?
echo "----- log do build (exit $rc) -----"
cat "$log"
echo "-----------------------------------"

erros=()
[ "$rc" -eq 0 ] || erros+=("o build falhou (exit $rc)")
grep -q 'using docker driver' "$log" || erros+=("o build nao usou o driver docker (builder default), que e o do deploy")
if ! grep -qF "[context $REF] load metadata for $ORIGEM" "$log"; then
  erros+=("o log nao mostra a base resolvida pelo contexto nomeado a partir de $ORIGEM; a chave do contexto casou com o FROM? (esperado: \"[context $REF] load metadata for $ORIGEM\")")
fi
if [ "$REGISTRO" != "public.ecr.aws" ]; then
  ecr=$(grep -F 'public.ecr.aws' "$log" | grep -vF "[context $REF]" || true)
  if [ -n "$ecr" ]; then
    erros+=("o log cita public.ecr.aws fora do contexto, mas a origem escolhida e $REGISTRO: $(printf '%s' "$ecr" | head -n 1)")
  fi
fi

docker image rm "$TAG" > /dev/null 2>&1 || true
rm -rf "$dir"

if [ "${#erros[@]}" -gt 0 ]; then
  for e in "${erros[@]}"; do echo "::error::M82: $e" >&2; done
  [ -z "${GITHUB_STEP_SUMMARY:-}" ] || echo "### Caminho do deploy (M82): REPROVADO (${#erros[@]} problema(s); ver log)" >> "$GITHUB_STEP_SUMMARY"
  exit 1
fi
echo "OK: driver docker + --build-context resolveu a base por $ORIGEM, sem requisicao ao public.ecr.aws fora do contexto"
[ -z "${GITHUB_STEP_SUMMARY:-}" ] || {
  echo "### Caminho do deploy (M82): OK"
  echo ""
  echo "- driver docker (builder \`default\`) + \`--build-context\` resolveu o FROM por \`$ORIGEM\`"
} >> "$GITHUB_STEP_SUMMARY"

#!/usr/bin/env bash
# M82 - escolhe DE ONDE vem a imagem base Python dos api/Dockerfile*, sempre
# com o MESMO digest, para que o build nao dependa so do ECR Public.
#
# Por que (medido em 16 e 17/10/2026): todos os api/Dockerfile* fazem
# `FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:...`. O pull
# ANONIMO do ECR Public tem cota por IP de origem (500 GB/mes, nao ajustavel)
# e os runners do GitHub compartilham IPs: os jobs `build + smoke Dockerfile*`
# (ci-docker-images.yml) e `backend smoke test` (ci-develop.yml) cairam com
# `429 Too Many Requests - toomanyrequests: Data limit exceeded` em "failed to
# resolve source metadata" nos PRs #549, #552, #553 e #556, e so passavam ao
# reexecutar 15 a 45 minutos depois. O deploy de producao tambem puxa anonimo:
# o login no ECR Public falha com "AccessDeniedException ... not authorized to
# perform: sts:GetServiceBearerToken" (run 38973794316, 16/10), porque a policy
# musclefit-gha-deploy-v2-compute (v4) so libera sts:GetServiceBearerToken com
# a condicao `sts:AWSServiceName = ecr-public.amazonaws.com`, que a chamada
# real nao satisfaz (simulate-principal-policy: implicitDeny sem contexto,
# allowed so com o contexto informado). Corrigir isso e uma nova versao da
# v2-compute, com aval do usuario, fora do M82.
#
# O que faz:
#   1. le a referencia do FROM dos Dockerfiles pedidos (padrao: TODOS os
#      api/Dockerfile*, sem os .dockerignore) e exige que seja UMA so, fixada
#      por digest, de um repositorio .../library/python. Um bump do Dependabot
#      no FROM propaga sozinho. Na CI a chamada sem argumento e a guarda de
#      paridade: Dockerfiles divergentes reprovam o PR aqui, com mensagem
#      clara. No deploy cada perna da matriz passa SO o Dockerfile que vai
#      construir, para que um Dockerfile alheio a ela nao derrube o deploy;
#   2. testa as origens na ordem, sempre <repositorio>@<digest> (padrao:
#      mirror.gcr.io/library/python, public.ecr.aws/docker/library/python,
#      docker.io/library/python):
#        - padrao: `docker buildx imagetools inspect` (so o indice; leve) e
#          confere que o digest devolvido e o do FROM;
#        - com --pull: `docker pull --platform <plataforma>` (deploy: a imagem
#          fica no daemon e o `docker build` com driver docker a reaproveita);
#      com poucas novas tentativas e espera curta. "Data limit exceeded" e
#      cota mensal: nao adianta insistir, passa direto para a proxima origem;
#   3. devolve a PRIMEIRA que responde. Nenhuma respondeu: falha.
#
# Conteudo identico: o digest e o endereco do conteudo. O cliente confere o
# sha256 do indice, dos manifestos e das camadas que baixa por digest, entao
# um espelho nao consegue entregar bytes diferentes sem o pull falhar. So
# muda o registro de onde os bytes vem; os Dockerfiles (e o DEV local e a
# producao) continuam com o mesmo FROM.
#
# Como o build usa a origem escolhida: contexto nomeado do BuildKit, que
# troca a imagem do FROM so naquele build:
#   docker buildx build --build-context "<ref>=docker-image://<origem>" ...
#   docker/build-push-action  ->  build-contexts: <build_context>
#   compose                   ->  build.additional_contexts (override da CI)
#
# Saidas (em $GITHUB_OUTPUT quando definido; sempre tambem em stdout, uma
# `chave=valor` por linha; mensagens de progresso vao para stderr):
#   ref            referencia exata do FROM (chave do contexto nomeado)
#   digest         sha256:<64 hex>
#   origem         <repositorio>@<digest> escolhido
#   registro       host da origem escolhida (para logs/resumo)
#   build_context  <ref>=docker-image://<origem>
# Resumo no $GITHUB_STEP_SUMMARY quando definido.
#
# Uso (a partir da raiz do repo):
#   .github/scripts/escolher-base-python.sh [--pull] [caminho ...]
#   cada caminho e um Dockerfile ou um diretorio (le os Dockerfile* dele, sem
#   os .dockerignore); sem caminho: api (todos os api/Dockerfile*).
#   ex.: CI     -> .github/scripts/escolher-base-python.sh
#        deploy -> .github/scripts/escolher-base-python.sh --pull api/Dockerfile.ai
# Ambiente (opcional):
#   BASE_PYTHON_ORIGENS      repositorios candidatos, separados por espaco, na
#                            ordem de preferencia (sem tag nem digest)
#   BASE_PYTHON_TENTATIVAS   tentativas por origem (padrao 3)
#   BASE_PYTHON_ESPERA       espera base entre tentativas, em segundos; cresce
#                            linear: 1x, 2x, ... (padrao 5)
#   BASE_PYTHON_TIMEOUT      limite de cada tentativa, em segundos (padrao 60;
#                            com --pull, 180: um registro travado nao prende o
#                            job; o pull da base leva segundos)
#   BASE_PYTHON_PLATAFORMA   plataforma do --pull (padrao linux/amd64: as
#                            Lambdas sao x86_64)
set -euo pipefail

MODO=inspect
CAMINHOS=()
for arg in "$@"; do
  case "$arg" in
    --pull) MODO=pull ;;
    -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    -*) echo "::error::opcao desconhecida: $arg" >&2; exit 2 ;;
    *) CAMINHOS+=("$arg") ;;
  esac
done
[ "${#CAMINHOS[@]}" -gt 0 ] || CAMINHOS=(api)

ORIGENS="${BASE_PYTHON_ORIGENS:-mirror.gcr.io/library/python public.ecr.aws/docker/library/python docker.io/library/python}"
TENTATIVAS="${BASE_PYTHON_TENTATIVAS:-3}"
ESPERA="${BASE_PYTHON_ESPERA:-5}"
PLATAFORMA="${BASE_PYTHON_PLATAFORMA:-linux/amd64}"
if [ "$MODO" = pull ]; then TIMEOUT="${BASE_PYTHON_TIMEOUT:-180}"; else TIMEOUT="${BASE_PYTHON_TIMEOUT:-60}"; fi

log() { echo "$*" >&2; }
falha() { echo "::error::M82: $*" >&2; exit 1; }

# --- 1. referencia do FROM, igual em todos os Dockerfiles pedidos ------------
dockerfiles=()
for c in "${CAMINHOS[@]}"; do
  if [ -d "$c" ]; then
    antes=${#dockerfiles[@]}
    for f in "${c%/}"/Dockerfile*; do
      [ -f "$f" ] || continue
      case "$f" in *.dockerignore) continue ;; esac
      dockerfiles+=("$f")
    done
    [ "${#dockerfiles[@]}" -gt "$antes" ] || falha "nenhum Dockerfile em $c/"
  elif [ -f "$c" ]; then
    dockerfiles+=("$c")
  else
    falha "caminho inexistente: $c"
  fi
done

# Imagens externas de cada FROM: pula flags (--platform=...) e ignora
# referencias a estagios declarados antes no mesmo arquivo (FROM x AS nome).
# `COPY/ADD --from=<imagem externa>` nao e coberto por este script (o
# adaptador Lambda foi vendorizado para nao precisar disso): so avisa.
refs_por_arquivo=""
for f in "${dockerfiles[@]}"; do
  achados=$(awk '
    { sub(/\r$/, "") }
    toupper($1) == "FROM" {
      i = 2
      while (i <= NF && substr($i, 1, 2) == "--") i++
      if (i > NF) next
      img = $i
      if (!(tolower(img) in estagios)) print "FROM " img
      if (toupper($(i + 1)) == "AS" && i + 2 <= NF) estagios[tolower($(i + 2))] = 1
    }
    toupper($1) == "COPY" || toupper($1) == "ADD" {
      for (i = 2; i <= NF && substr($i, 1, 2) == "--"; i++) {
        if (substr($i, 1, 7) != "--from=") continue
        v = substr($i, 8)
        if (!(tolower(v) in estagios) && v !~ /^[0-9]+$/) print "COPYFROM " v
      }
    }' "$f")
  while read -r tipo r; do
    case "$tipo" in
      FROM) refs_por_arquivo+="$f $r"$'\n' ;;
      COPYFROM) echo "::warning::M82: $f usa --from=$r (imagem externa fora do espelho; o pull dela segue direto)" >&2 ;;
    esac
  done <<< "$achados"
  case "$refs_por_arquivo" in *"$f "*) ;; *) falha "$f nao tem FROM com imagem externa" ;; esac
done

distintas=$(printf '%s' "$refs_por_arquivo" | awk 'NF { print $2 }' | sort -u)
if [ "$(printf '%s\n' "$distintas" | grep -c .)" -ne 1 ]; then
  log "FROM por arquivo:"
  printf '%s' "$refs_por_arquivo" | sed 's/^/  /' >&2
  falha "os Dockerfiles lidos (${CAMINHOS[*]}) nao usam UMA mesma imagem base. Alinhe o FROM (mesma referencia e digest) em todos eles."
fi
REF="$distintas"

# Formato estrito: tambem impede que um FROM malicioso injete algo nas saidas
# (elas viram argumento de build e variavel de ambiente nos workflows).
padrao='^([a-z0-9][a-z0-9._/-]*)(:[A-Za-z0-9._-]+)?@(sha256:[0-9a-f]{64})$'
if ! [[ "$REF" =~ $padrao ]]; then
  falha "FROM '$REF' precisa estar fixado por digest (<repositorio>[:tag]@sha256:<64 hex>)"
fi
REPO="${BASH_REMATCH[1]}"
DIGEST="${BASH_REMATCH[3]}"
case "$REPO" in
  */library/python|python|library/python) ;;
  *) falha "FROM '$REF' nao e a Docker Official Image python (.../library/python); as origens alternativas so valem para ela" ;;
esac
log "FROM de ${#dockerfiles[@]} Dockerfile(s) (${dockerfiles[*]}): $REF"

# --- 2. testar as origens -----------------------------------------------------
com_timeout() {
  if command -v timeout >/dev/null 2>&1; then timeout "$TIMEOUT" "$@"; else "$@"; fi
}

testar() { # $1 = <repositorio>@<digest>; saida do comando em $SAIDA
  if [ "$MODO" = pull ]; then
    SAIDA=$(com_timeout docker pull -q --platform "$PLATAFORMA" "$1" 2>&1) || return 1
  else
    SAIDA=$(com_timeout docker buildx imagetools inspect "$1" 2>&1) || return 1
    devolvido=$(printf '%s\n' "$SAIDA" | awk '$1 == "Digest:" { print $2; exit }')
    if [ "$devolvido" != "$DIGEST" ]; then
      SAIDA="digest devolvido '$devolvido' difere do FROM ($DIGEST)"
      return 1
    fi
  fi
}

ORIGEM=""
falhas=""
for repo in $ORIGENS; do
  alvo="$repo@$DIGEST"
  resumo=""
  for tentativa in $(seq 1 "$TENTATIVAS"); do
    log "testando $alvo ($MODO, tentativa $tentativa/$TENTATIVAS)"
    SAIDA=""
    if testar "$alvo"; then ORIGEM="$alvo"; break 2; fi
    resumo=$(printf '%s\n' "$SAIDA" | grep -v '^[[:space:]]*$' | tail -n 3 | tr '\n' ' ')
    log "  falhou: $resumo"
    if printf '%s' "$SAIDA" | grep -qi 'data limit exceeded'; then
      log "  cota de dados estourada (nao e transitorio): proxima origem"
      break
    fi
    if [ "$tentativa" -lt "$TENTATIVAS" ]; then
      log "  aguardando $((ESPERA * tentativa))s"
      sleep $((ESPERA * tentativa))
    fi
  done
  falhas+="- \`$repo\`: ${resumo:-sem resposta}"$'\n'
  echo "::warning::M82: imagem base indisponivel em $repo (${resumo:-sem resposta})" >&2
done

if [ -z "$ORIGEM" ]; then
  [ -z "${GITHUB_STEP_SUMMARY:-}" ] || {
    echo "### Imagem base Python (M82): nenhuma origem respondeu"
    echo ""
    printf '%s' "$falhas"
  } >> "$GITHUB_STEP_SUMMARY"
  falha "nenhuma origem respondeu para $DIGEST (testadas: $ORIGENS). Reexecute o job mais tarde ou acrescente uma origem com o mesmo digest em BASE_PYTHON_ORIGENS."
fi

REGISTRO="${ORIGEM%%/*}"
BUILD_CONTEXT="$REF=docker-image://$ORIGEM"
log "origem escolhida: $ORIGEM"

# --- 3. saidas e resumo ---------------------------------------------------------
saidas="ref=$REF
digest=$DIGEST
origem=$ORIGEM
registro=$REGISTRO
build_context=$BUILD_CONTEXT"
printf '%s\n' "$saidas"
[ -z "${GITHUB_OUTPUT:-}" ] || printf '%s\n' "$saidas" >> "$GITHUB_OUTPUT"
[ -z "${GITHUB_STEP_SUMMARY:-}" ] || {
  echo "### Imagem base Python (M82)"
  echo ""
  echo "- FROM dos Dockerfiles: \`$REF\`"
  echo "- origem usada no build (mesmo digest): \`$ORIGEM\` ($MODO)"
  if [ -n "$falhas" ]; then
    echo "- origens que falharam antes:"
    printf '%s' "$falhas" | sed 's/^/  /'
  fi
} >> "$GITHUB_STEP_SUMMARY"

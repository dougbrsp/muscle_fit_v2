#!/usr/bin/env bash
# Invoca a Lambda musclefit-migrations (api/app/migrate.py) de forma SINCRONA e
# falha se ela der erro. Compartilhado por:
#   - .github/workflows/deploy-producao.yml (job backend-deploy, action=migrate, antes do update das Lambdas)
#   - .github/workflows/db-migrations.yml   (workflow_dispatch: status | dry-run | migrate | replay-dry-run | replay)
#
# Uso: scripts/invoke-migrations.sh <status|dry-run|migrate|replay-dry-run|replay>
#
# Migracoes de schema (runner de api/app/migrate.py):
#   status          somente leitura
#   dry-run         plano, sem escrita
#   migrate         aplica
#
# Replay das trilhas da progressao (api/app/replay_progression.py; passo 1 de
# docs/refatoracao-motores/ATIVACAO_MOTORES_DETERMINISTICOS.md). NAO faz parte
# de nenhum deploy: e operacao pontual de ativacao, disparada a mao pelo
# workflow db-migrations.yml.
#   replay-dry-run  calcula tudo sobre o banco real e faz rollback (nada gravado)
#   replay          grava (transacao unica: tudo ou nada)
#
# Requer credenciais AWS ja configuradas (OIDC), aws CLI e jq.
# Nao atualiza o codigo da funcao - so invoca.
set -euo pipefail

ACTION="${1:-dry-run}"
FN="${MIGRATIONS_FUNCTION_NAME:-musclefit-migrations}"

# Payload por acao. O replay usa a MESMA acao do handler
# ("replay-progression") com `dry_run` booleano; sao duas entradas aqui para
# que "gravar" seja escolha explicita na interface do workflow, e nunca o
# default de um campo omitido.
case "$ACTION" in
  status | dry-run | migrate) PAYLOAD="{\"action\":\"$ACTION\"}" ;;
  replay-dry-run) PAYLOAD='{"action":"replay-progression","dry_run":true}' ;;
  replay) PAYLOAD='{"action":"replay-progression","dry_run":false}' ;;
  *)
    echo "::error::action invalida: '$ACTION' (esperado status, dry-run, migrate, replay-dry-run ou replay)"
    exit 2
    ;;
esac

# Sem retry do CLI: um read-timeout reenviaria a invocacao. O runner e
# idempotente e tem lock, mas uma segunda execucao concorrente so geraria ruido.
# Vale igual para o replay: ele tambem e idempotente, mas duas invocacoes
# simultaneas escreveriam nas mesmas tabelas.
export AWS_MAX_ATTEMPTS=1

WORKDIR="$(mktemp -d)"
RESPONSE="$WORKDIR/response.json"
META="$WORKDIR/invoke.json"
LOG_GROUP="/aws/lambda/$FN"

# O log do Actions e o step summary serao publicos (M103 L3): sem `--log-type Tail`
# (o tail traz o log da Lambda, com o payload inteiro) e sem imprimir a resposta
# completa nem linhas por usuario. So status, contagens e nomes de migracao; o
# detalhe fica no CloudWatch Logs ($LOG_GROUP), onde o handler ja registra o
# resultado completo.
aws lambda invoke \
  --function-name "$FN" \
  --invocation-type RequestResponse \
  --cli-binary-format raw-in-base64-out \
  --payload "$PAYLOAD" \
  --cli-read-timeout 330 \
  --no-cli-pager \
  --output json \
  "$RESPONSE" >"$META"

FUNCTION_ERROR="$(jq -r '.FunctionError // empty' "$META")"
STATUS="$(jq -r '.status // empty' "$RESPONSE" 2>/dev/null || true)"
IS_REPLAY=false
if [ "$ACTION" = "replay-dry-run" ] || [ "$ACTION" = "replay" ]; then IS_REPLAY=true; fi

# Mensagem de erro publicavel: o tipo sempre; o texto so quando e MigrationError
# do runner numa acao de schema (cita arquivo, checksum, lock ou acao, nunca dado
# de usuario). Erro do driver/banco (ex.: "Duplicate entry '<valor>'" num backfill)
# e qualquer erro do replay podem citar dados de usuario: so o tipo, e o detalhe
# fica no CloudWatch Logs.
ERROR_TYPE="$(jq -r '.errorType // empty' "$RESPONSE" 2>/dev/null || true)"
ERROR_MSG=""
if [ -n "$FUNCTION_ERROR" ] && [ "$IS_REPLAY" = false ] && [ "$ERROR_TYPE" = "MigrationError" ]; then
  ERROR_MSG="$(jq -r '.errorMessage // empty' "$RESPONSE" 2>/dev/null | head -n 1 | cut -c1-300 || true)"
fi

MISMATCH=""
if [ "$ACTION" = "status" ] && [ "$STATUS" = "ok" ]; then
  MISMATCH="$(jq -r '[.migrations[] | select(.state | test("mismatch")) | .filename] | join(", ")' "$RESPONSE")"
fi

# Resumo publicavel (log e step summary): sem user_id, fingerprint ou payload.
summary() {
  echo "## musclefit-migrations - action \`$ACTION\`"
  echo
  if [ -n "$FUNCTION_ERROR" ] || [ "$STATUS" != "ok" ]; then
    echo "**FALHOU** (FunctionError='$FUNCTION_ERROR', status='$STATUS', errorType='$ERROR_TYPE')"
    if [ -n "$ERROR_MSG" ]; then
      echo
      echo "Erro: \`$ERROR_MSG\`"
    fi
    echo
    echo "Detalhes no CloudWatch Logs: log group \`$LOG_GROUP\` (regiao ${AWS_REGION:-sa-east-1})."
  elif [ "$IS_REPLAY" = true ]; then
    echo "- dry-run (nada gravado): \`$(jq -r '.dry_run' "$RESPONSE")\`"
    echo "- duracao: $(jq -r '.duration_ms' "$RESPONSE") ms (orcamento de $(jq -r '.time_budget_seconds' "$RESPONSE") s)"
    echo "- usuarios processados: $(jq -r '.users | length' "$RESPONSE")"
    echo "- totais: $(jq -r '.totals | "sessoes \(.sessions), trilhas \(.tracks), grupos \(.groups), decisoes \(.decisions)"' "$RESPONSE")"
    echo "- avisos: $(jq -r '.warnings | length' "$RESPONSE") (texto no CloudWatch Logs \`$LOG_GROUP\`)"
    echo
    echo "Fingerprint e linhas por usuario ficam so no CloudWatch Logs (o handler registra o resultado completo)."
  elif [ "$ACTION" = "status" ]; then
    echo "| estado | quantidade |"
    echo "|---|---|"
    jq -r '.migrations | group_by(.state)[] | "| \(.[0].state) | \(length) |"' "$RESPONSE"
    echo
    echo "Pendentes/divergentes:"
    jq -r '.migrations[] | select(.state != "baseline" and .state != "applied") | "- `\(.filename)`: \(.state)"' "$RESPONSE"
  else
    echo "- dry-run: \`$(jq -r '.dry_run' "$RESPONSE")\`"
    echo "- baselines registradas nesta execucao: $(jq -r '.baselined | length' "$RESPONSE")"
    echo "- ja aplicadas: $(jq -r '.already_applied' "$RESPONSE")"
    echo
    echo "| migracao | statements | ms |"
    echo "|---|---|---|"
    jq -r '.applied[] | "| `\(.filename)` | \(.statements | length) | \(.execution_ms) |"' "$RESPONSE"
    echo
    jq -r '.warnings[]? | "- AVISO: \(.)"' "$RESPONSE"
  fi
}

summary
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  summary >>"$GITHUB_STEP_SUMMARY"
fi

if [ -n "$FUNCTION_ERROR" ] || [ "$STATUS" != "ok" ]; then
  echo "::error::$FN falhou (action=$ACTION, FunctionError='$FUNCTION_ERROR', status='$STATUS', errorType='$ERROR_TYPE'). Detalhes no CloudWatch Logs: $LOG_GROUP"
  exit 1
fi
if [ -n "$MISMATCH" ]; then
  echo "::error::checksum divergente em migracao aplicada: $MISMATCH"
  exit 1
fi
echo "ok: $FN action=$ACTION"

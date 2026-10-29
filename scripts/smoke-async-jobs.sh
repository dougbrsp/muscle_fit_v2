#!/usr/bin/env bash
# Smoke de ponta a ponta dos jobs assincronos no Docker Compose de DEV
# (api-core -> DynamoDB/LocalStack -> Invoke Event -> lambda-local ->
# handler real -> mark_done/mark_failed -> GET /jobs/{id}).
# Ver docs/local-dev.md, "Jobs assincronos".
#
# Cobre as 3 funcoes assincronas de producao:
#   musclefit-plan-generator  POST /workout-plans/generate
#   musclefit-workout-ai      POST /exercises/evaluate-set e POST /nutrition/meals/parse
#   musclefit-cardio-agent    POST /cardio-recommendations/generate
#
# O relatorio deixou de ser job assincrono (Lambda musclefit-reports): virou
# `GET /reports/markdown`, sincrono na api-core. Como ele le o banco inteiro
# do usuario pelo caminho de producao, fica checado no fim deste script
# (200, text/markdown e Content-Disposition attachment).
#
# Criterio: POST responde 202 com job_id (nunca 500) e o job chega a um estado
# terminal (`done` ou `failed` com mensagem) dentro do prazo - nunca fica
# `pending` para sempre. Sem OPENAI_API_KEY (CI), os jobs que chamam a OpenAI
# terminam `failed`, o que ja prova o caminho inteiro. Um caso deterministico
# (modelo com slot vazio) exige a mensagem exata do handler, provando leitura
# de banco dentro da funcao.
#
# SEG-12 (#472): a falta de chave e erro de CONFIGURACAO; o usuario ve so a
# frase generica de "IA indisponivel" (`USER_MESSAGE_UNAVAILABLE`, duplicada
# abaixo de proposito) e o motivo real ("OPENAI_API_KEY nao configurada") vai
# SO para o log, no evento `llm.error`. Sem chave, o smoke prova os dois
# lados: (1) a mensagem do job/geracao e generica e nunca cita
# OPENAI_API_KEY; (2) `docker compose logs lambda-ai` tem o `llm.error` da
# feature com o diagnostico de chave ausente.
#
# PR-18: a geracao de ficha exige um Modelo de Treino. As recusas (payload sem
# `template_id` e modelo inexistente) sao SINCRONAS - 422/404 na propria
# resposta, sem criar job - e estao cobertas por `post_expect_http`.
#
# Cards ilustrados de exercicios (`job_kind="exercise-card"` na plan-generator):
# contrato SEM CUSTO - `/auth/me` com `features.exercise_cards`, 404/503 em
# exercicio inexistente (conforme a flag), `GET /exercises/{id}/card`. O caminho
# assincrono completo (202 -> job -> worker -> geracao FAILED com
# `OPENAI_GENERATION_FAILED` e a frase generica, polling no RECURSO, mais o
# `llm.error` da feature `exercise_card` no log) so roda quando a `lambda-ai`
# NAO tem OPENAI_API_KEY (o CI): o worker falha antes de qualquer chamada de
# rede. Com chave real (DEV) esse trecho e PULADO - geraria uma imagem paga.
#
# Uso:
#   bash scripts/smoke-async-jobs.sh
# Variaveis:
#   API_BASE_URL   (default http://localhost:8000)
#   API_TOKEN      (default dev-local-token, o do .env.example)
#   COMPOSE        comando compose p/ semear um admin se faltar
#                  (default "docker compose"; vazio = nao semeia)
#   JOB_TIMEOUT    segundos maximos por job (default 180)

set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_TOKEN="${API_TOKEN:-dev-local-token}"
COMPOSE="${COMPOSE-docker compose}"
JOB_TIMEOUT="${JOB_TIMEOUT:-180}"

PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "python3 e necessario"; exit 2; }

# json_field <campo>: le JSON do stdin e imprime o campo (vazio se ausente).
json_field() {
  "$PY" -c 'import json,sys
try:
    v = json.load(sys.stdin).get(sys.argv[1])
except Exception:
    v = None
print("" if v is None else v)' "$1"
}

now_ms() { "$PY" -c 'import time; print(int(time.time()*1000))'; }

# json_path <a.b.c>: como json_field, mas com caminho pontilhado (indice de
# lista numerico) e booleano em minusculas.
json_path() {
  "$PY" -c 'import json,sys
v = None
try:
    v = json.load(sys.stdin)
    for part in sys.argv[1].split("."):
        v = v[int(part)] if isinstance(v, list) else v.get(part)
except Exception:
    v = None
print("" if v is None else (str(v).lower() if isinstance(v, bool) else v))' "$1"
}

new_uuid() { "$PY" -c 'import uuid; print(uuid.uuid4())'; }

# O token mestre resolve para o usuario admin (app/security.py). Banco recem
# criado pelas migrations nao tem usuario: semeia um admin descartavel so se
# nao existir nenhum (nunca altera usuario existente).
if [ -n "$COMPOSE" ]; then
  echo "== garantindo um usuario admin para o token mestre"
  $COMPOSE exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e "
    INSERT INTO users (username, password_hash, role, is_active)
    SELECT '"'"'smoke-admin'"'"', '"'"'!'"'"', '"'"'admin'"'"', 1
    FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM users WHERE role = '"'"'admin'"'"' AND is_active = 1);"' 2>/dev/null
fi

failures=0

# Copia EXPLICITA de `USER_MESSAGE_UNAVAILABLE` (api/app/openai_client.py): e
# o texto que o usuario ve quando a IA esta indisponivel - inclusive por falta
# de chave. Mudou la, muda aqui (o smoke falha e avisa).
LLM_USER_MESSAGE_UNAVAILABLE="O serviço de IA está indisponível no momento. Tente novamente em instantes."
# Diagnostico de chave ausente, que so pode aparecer no LOG (`llm.error`).
LLM_DIAGNOSTIC_MISSING_KEY="OPENAI_API_KEY nao configurada"

# Estado final do ultimo `post_and_poll` (para checagens extras do chamador).
LAST_JOB_STATUS=""
LAST_JOB_ERROR=""

lambda_ai_sem_chave_openai() {
  [ -n "$COMPOSE" ] || return 1
  $COMPOSE exec -T lambda-ai sh -c '[ -z "${OPENAI_API_KEY:-}" ] || [ "$OPENAI_API_KEY" = replace_me ]' \
    >/dev/null 2>&1
}

SEM_CHAVE_OPENAI=0
if lambda_ai_sem_chave_openai; then SEM_CHAVE_OPENAI=1; fi
echo "== lambda-ai sem OPENAI_API_KEY: $([ "$SEM_CHAVE_OPENAI" = 1 ] && echo sim || echo nao)"

# expect_no_key_leak <descricao> <mensagem>: SEG-12 - nenhuma mensagem
# exibida ao usuario cita a chave.
expect_no_key_leak() {
  if [[ "$2" == *OPENAI_API_KEY* ]]; then
    echo "   FALHA ($1): mensagem ao usuario cita OPENAI_API_KEY: $2"
    failures=$((failures + 1))
  fi
}

# expect_llm_error_logged <feature>: sem chave, o `llm.error` da feature tem
# de estar no log da lambda-ai com o diagnostico de chave ausente.
expect_llm_error_logged() {
  local feature="$1" logs matches
  logs=$($COMPOSE logs --no-color lambda-ai 2>/dev/null || true)
  # Sem `grep -q` no fim do pipe: com pipefail, a saida antecipada dele pode
  # dar SIGPIPE nos greps anteriores e virar falso negativo.
  matches=$(printf '%s\n' "$logs" | grep -F '"event": "llm.error"' | grep -F "\"feature\": \"$feature\"" \
    | grep -F "$LLM_DIAGNOSTIC_MISSING_KEY" || true)
  if [ -n "$matches" ]; then
    echo "   log: llm.error ($feature) com \"$LLM_DIAGNOSTIC_MISSING_KEY\""
  else
    echo "   FALHA: sem llm.error de $feature com \"$LLM_DIAGNOSTIC_MISSING_KEY\" em '$COMPOSE logs lambda-ai'"
    failures=$((failures + 1))
  fi
}

# post_and_poll <descricao> <path> <json> [trecho obrigatorio no erro]
post_and_poll() {
  local label="$1" path="$2" body="$3" expect_error="${4:-}"
  local resp http_code start_ms end_ms job_id status job deadline err
  resp="$(mktemp)"
  LAST_JOB_STATUS=""
  LAST_JOB_ERROR=""

  echo "== $label: POST $path"
  start_ms=$(now_ms)
  http_code=$(curl -sS -o "$resp" -w '%{http_code}' -X POST "$API_BASE_URL$path" \
    -H "X-API-Token: $API_TOKEN" -H 'Content-Type: application/json' -d "$body")
  end_ms=$(now_ms)
  echo "   HTTP $http_code em $((end_ms - start_ms)) ms: $(cat "$resp")"

  if [ "$http_code" != "202" ]; then
    echo "   FALHA: esperado 202"
    failures=$((failures + 1)); return
  fi
  job_id=$(json_field job_id < "$resp")
  if [ -z "$job_id" ]; then
    echo "   FALHA: resposta sem job_id"
    failures=$((failures + 1)); return
  fi

  deadline=$(( $(date +%s) + JOB_TIMEOUT ))
  status="pending"
  job=""
  while [ "$(date +%s)" -lt "$deadline" ]; do
    job=$(curl -sS -H "X-API-Token: $API_TOKEN" "$API_BASE_URL/jobs/$job_id" || true)
    status=$(printf '%s' "$job" | json_field status)
    if [ "$status" = "done" ] || [ "$status" = "failed" ]; then break; fi
    sleep 1
  done

  echo "   job $job_id -> ${status:-?} ($(( $(now_ms) - start_ms )) ms ate estado final)"
  LAST_JOB_STATUS="$status"
  case "$status" in
    done)
      if [ -n "$expect_error" ]; then
        echo "   FALHA: esperado failed contendo '$expect_error'"
        failures=$((failures + 1))
      fi
      ;;
    failed)
      err=$(printf '%s' "$job" | json_field error)
      LAST_JOB_ERROR="$err"
      echo "   erro: $err"
      expect_no_key_leak "$label" "$err"
      if [ -z "$err" ]; then
        echo "   FALHA: failed sem mensagem de erro"
        failures=$((failures + 1))
      elif [ -n "$expect_error" ] && [[ "$err" != *"$expect_error"* ]]; then
        echo "   FALHA: erro nao contem '$expect_error'"
        failures=$((failures + 1))
      fi
      ;;
    *)
      echo "   FALHA: job nao terminou em ${JOB_TIMEOUT}s (status=${status:-?}): $job"
      failures=$((failures + 1))
      ;;
  esac
}

# post_expect_http <descricao> <path> <json> <http esperado> [trecho no corpo]
# Para as recusas SINCRONAS (PR-18): nenhum job e criado, entao nao ha o que
# pollar - o que se verifica e o status e a mensagem da propria resposta.
post_expect_http() {
  local label="$1" path="$2" body="$3" expected="$4" expect_text="${5:-}"
  local resp http_code

  resp="$(mktemp)"
  echo "== $label: POST $path"
  http_code=$(curl -sS -o "$resp" -w '%{http_code}' -X POST "$API_BASE_URL$path" \
    -H "X-API-Token: $API_TOKEN" -H 'Content-Type: application/json' -d "$body")
  echo "   HTTP $http_code: $(cat "$resp")"
  if [ "$http_code" != "$expected" ]; then
    echo "   FALHA: esperado $expected"
    failures=$((failures + 1)); return
  fi
  if [ -n "$expect_text" ] && ! grep -q "$expect_text" "$resp"; then
    echo "   FALHA: resposta nao contem '$expect_text'"
    failures=$((failures + 1))
  fi
}

# PR-18: nenhuma ficha e gerada sem um Modelo de Treino. As duas recusas
# abaixo acontecem NO REQUEST SINCRONO (nao viram job), e e isso que o smoke
# precisa provar - antes o `template_id` inexistente so falhava dentro da
# Lambda, segundos depois, como job `failed`.
post_expect_http "plan-generator (sem modelo no payload)" \
  /workout-plans/generate '{"gym_id": null}' 422 "template_id"
post_expect_http "plan-generator (modelo inexistente: 404 sincrono)" \
  /workout-plans/generate '{"template_id": 999999999}' 404 "Modelo não encontrado"

# Caminho assincrono completo da plan-generator: modelo REAL atribuido ao
# usuario do token. O modelo tem um slot VAZIO de proposito - o handler le o
# modelo no banco e termina o job com uma mensagem deterministica, sem
# depender da OpenAI. Prova a cadeia inteira (202 -> DynamoDB -> Invoke ->
# handler -> leitura de banco -> mark_failed -> GET /jobs/{id}).
seed_template_id() {
  local me template
  me=$(curl -sS -H "X-API-Token: $API_TOKEN" "$API_BASE_URL/auth/me" || true)
  SMOKE_USER_ID=$(printf '%s' "$me" | json_field id)
  [ -n "$SMOKE_USER_ID" ] || { echo "   FALHA: nao consegui descobrir o usuario do token: $me"; return 1; }
  template=$(curl -sS -X POST "$API_BASE_URL/workout-plan-templates" \
    -H "X-API-Token: $API_TOKEN" -H 'Content-Type: application/json' \
    -d '{"name": "Smoke PR-18", "slots": [{"sequence": 1, "name": "A", "items": []}]}' || true)
  SMOKE_TEMPLATE_ID=$(printf '%s' "$template" | json_field id)
  [ -n "$SMOKE_TEMPLATE_ID" ] || { echo "   FALHA ao criar o modelo do smoke: $template"; return 1; }
  curl -sS -o /dev/null -X POST "$API_BASE_URL/workout-plan-templates/$SMOKE_TEMPLATE_ID/assignments" \
    -H "X-API-Token: $API_TOKEN" -H 'Content-Type: application/json' \
    -d "{\"user_id\": $SMOKE_USER_ID}" || true
}

echo "== semeando um Modelo de Treino atribuido (PR-18)"
if seed_template_id; then
  echo "   modelo $SMOKE_TEMPLATE_ID atribuido ao usuario $SMOKE_USER_ID"
  post_and_poll "plan-generator (modelo atribuido)" \
    /workout-plans/generate "{\"template_id\": $SMOKE_TEMPLATE_ID}" "grupo muscular"
else
  failures=$((failures + 1))
fi
post_and_poll "workout-ai (evaluate-set)" \
  /exercises/evaluate-set '{"exercise_name": "Supino reto", "reps": 10, "weight_kg": 40}'
if [ "$SEM_CHAVE_OPENAI" = 1 ] && [ -n "$LAST_JOB_STATUS" ]; then
  # Sem chave: `failed` com EXATAMENTE a frase generica (SEG-12) e o motivo
  # real so no log.
  if [ "$LAST_JOB_STATUS" != "failed" ] || [ "$LAST_JOB_ERROR" != "$LLM_USER_MESSAGE_UNAVAILABLE" ]; then
    echo "   FALHA: sem chave, esperado failed com \"$LLM_USER_MESSAGE_UNAVAILABLE\""
    failures=$((failures + 1))
  fi
  expect_llm_error_logged set_evaluation
fi
post_and_poll "cardio-agent" \
  /cardio-recommendations/generate '{}'
if [ "$SEM_CHAVE_OPENAI" = 1 ] && [ -n "$LAST_JOB_STATUS" ]; then
  # O cardio-agent tem a sua propria frase generica (cardio_generation_core):
  # basta terminar `failed` com mensagem (checado acima) e o log provar o motivo.
  if [ "$LAST_JOB_STATUS" != "failed" ]; then
    echo "   FALHA: sem chave, esperado failed"
    failures=$((failures + 1))
  fi
  expect_llm_error_logged cardio_agent
fi

# Nutricao L5b: leitura da refeicao por IA (job `nutrition-meal-parse` na
# workout-ai, cota do L5a no `ai_usage_log`). Sem chave: `failed` com a frase
# generica, o `llm.error` da feature e a linha do `ai_usage_log` FECHADA (o
# worker fecha antes de marcar o job), nunca `pending`.
SMOKE_HOJE=$("$PY" -c 'import datetime; print(datetime.date.today().isoformat())')
post_and_poll "workout-ai (leitura da refeicao)" \
  /nutrition/meals/parse "{\"text\": \"arroz, feijao e um bife grelhado\", \"local_date\": \"$SMOKE_HOJE\", \"meal_slot\": \"lunch\"}"
if [ "$SEM_CHAVE_OPENAI" = 1 ] && [ -n "$LAST_JOB_STATUS" ]; then
  if [ "$LAST_JOB_STATUS" != "failed" ] || [ "$LAST_JOB_ERROR" != "$LLM_USER_MESSAGE_UNAVAILABLE" ]; then
    echo "   FALHA: sem chave, esperado failed com \"$LLM_USER_MESSAGE_UNAVAILABLE\""
    failures=$((failures + 1))
  fi
  expect_llm_error_logged meal_parse
  if [ -n "$COMPOSE" ]; then
    pendentes=$($COMPOSE exec -T mysql sh -c 'mysql -N -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e "
      SELECT COUNT(*) FROM ai_usage_log WHERE feature = '"'"'meal_parse'"'"' AND status = '"'"'pending'"'"';"' \
      2>/dev/null | tr -d '\r' || true)
    echo "   ai_usage_log meal_parse pendentes: ${pendentes:-?}"
    if [ "$pendentes" != "0" ]; then
      echo "   FALHA: a linha do ai_usage_log da leitura ficou pendente"
      failures=$((failures + 1))
    fi
  fi
fi

# Cards ilustrados de exercicios - ver cabecalho. Nunca chama a OpenAI real.
CARD_MISSING_EXERCISE=999999999

# request <metodo> <path> [json] -> imprime "<http_code> <arquivo do corpo>"
request() {
  local method="$1" path="$2" body="${3:-}" out code
  out="$(mktemp)"
  if [ -n "$body" ]; then
    code=$(curl -sS -o "$out" -w '%{http_code}' -X "$method" "$API_BASE_URL$path" \
      -H "X-API-Token: $API_TOKEN" -H 'Content-Type: application/json' -d "$body")
  else
    code=$(curl -sS -o "$out" -w '%{http_code}' -X "$method" "$API_BASE_URL$path" -H "X-API-Token: $API_TOKEN")
  fi
  echo "$code $out"
}

card_fail() { echo "   FALHA: $*"; failures=$((failures + 1)); }

smoke_exercise_cards() {
  local flag code body exercise_id key gid replay_gid status deadline gen err error_code created

  echo "== cards ilustrados: GET /auth/me (features.exercise_cards)"
  read -r code body <<<"$(request GET /auth/me)"
  flag=$(json_path features.exercise_cards < "$body")
  echo "   HTTP $code, features.exercise_cards=${flag:-?}"
  if [ "$code" != "200" ] || { [ "$flag" != "true" ] && [ "$flag" != "false" ]; }; then
    card_fail "/auth/me sem features.exercise_cards booleano: $(cat "$body")"; return
  fi

  echo "== cards ilustrados: exercicio inexistente (sincrono, sem job)"
  read -r code body <<<"$(request POST "/exercises/$CARD_MISSING_EXERCISE/card-generations" \
    "{\"idempotency_key\": \"$(new_uuid)\"}")"
  echo "   POST card-generations -> HTTP $code: $(cat "$body")"
  if [ "$flag" = "true" ]; then
    { [ "$code" = "404" ] && grep -q exercise_not_found "$body"; } || card_fail "esperado 404 exercise_not_found"
  else
    { [ "$code" = "503" ] && grep -q exercise_card_generation_disabled "$body"; } \
      || card_fail "esperado 503 exercise_card_generation_disabled (flag desligada)"
  fi
  read -r code body <<<"$(request GET "/exercises/$CARD_MISSING_EXERCISE/card")"
  echo "   GET card -> HTTP $code"
  [ "$code" = "404" ] || card_fail "esperado 404 em GET /exercises/{inexistente}/card"

  if [ "$flag" != "true" ]; then
    echo "   flag desligada: caminho assincrono do card pulado"; return
  fi
  if [ "$SEM_CHAVE_OPENAI" != 1 ]; then
    echo "   caminho assincrono do card PULADO: lambda-ai com OPENAI_API_KEY (geraria imagem paga) ou sem compose"
    return
  fi

  read -r code body <<<"$(request GET "/exercises?limit=1&active_only=false")"
  exercise_id=$(json_path items.0.id < "$body")
  created=""
  if [ -z "$exercise_id" ]; then
    read -r code body <<<"$(request POST /exercises '{"name": "Smoke card ilustrado"}')"
    exercise_id=$(json_path item.id < "$body")
    created="(criado pelo smoke)"
  fi
  [ -n "$exercise_id" ] || { card_fail "nao consegui um exercicio para o card: $(cat "$body")"; return; }

  key=$(new_uuid)
  echo "== cards ilustrados: POST /exercises/$exercise_id/card-generations $created"
  read -r code body <<<"$(request POST "/exercises/$exercise_id/card-generations" "{\"idempotency_key\": \"$key\"}")"
  echo "   HTTP $code: $(head -c 300 "$body")"
  [ "$code" = "202" ] || { card_fail "esperado 202"; return; }
  gid=$(json_path generation.id < "$body")
  [ -n "$(json_path generation.job_id < "$body")" ] || card_fail "geracao sem job_id"

  read -r code body <<<"$(request POST "/exercises/$exercise_id/card-generations" "{\"idempotency_key\": \"$key\"}")"
  replay_gid=$(json_path generation.id < "$body")
  echo "   replay da mesma chave -> HTTP $code (generation $replay_gid)"
  { [ "$code" = "200" ] && [ "$replay_gid" = "$gid" ]; } || card_fail "replay deveria ser 200 com a mesma geracao"

  deadline=$(( $(date +%s) + JOB_TIMEOUT ))
  status=""
  gen=""
  while [ "$(date +%s)" -lt "$deadline" ]; do
    gen=$(curl -sS -H "X-API-Token: $API_TOKEN" "$API_BASE_URL/exercises/$exercise_id/card-generations/$gid" || true)
    status=$(printf '%s' "$gen" | json_path generation.status)
    case "$status" in FAILED|GENERATED) break ;; esac
    sleep 1
  done
  err=$(printf '%s' "$gen" | json_path generation.error_message)
  error_code=$(printf '%s' "$gen" | json_path generation.error_code)
  echo "   geracao $gid -> ${status:-?} (${error_code:-?}): $err"
  # Falta de chave (prova a cadeia 202 -> job -> worker -> banco): FAILED com
  # OPENAI_GENERATION_FAILED e a frase generica; o motivo real so no log.
  if [ "$status" != "FAILED" ] || [ "$error_code" != "OPENAI_GENERATION_FAILED" ] \
      || [ "$err" != "$LLM_USER_MESSAGE_UNAVAILABLE" ]; then
    card_fail "esperado FAILED/OPENAI_GENERATION_FAILED com \"$LLM_USER_MESSAGE_UNAVAILABLE\" (falta de OPENAI_API_KEY)"
  fi
  expect_no_key_leak "card" "$err"
  expect_llm_error_logged exercise_card

  read -r code body <<<"$(request GET "/exercises/$exercise_id/card")"
  echo "   GET /exercises/$exercise_id/card -> HTTP $code: $(cat "$body")"
  [ "$code" = "200" ] || card_fail "esperado 200 em GET /exercises/{id}/card"
}
smoke_exercise_cards

# Relatorio em Markdown (sincrono): 200, content-type text/markdown e anexo.
get_expect_markdown() {
  local path="$1" body headers http_code ctype disposition
  body="$(mktemp)"; headers="$(mktemp)"
  echo "== relatorio markdown: GET $path"
  http_code=$(curl -sS -o "$body" -D "$headers" -w '%{http_code}' \
    -H "X-API-Token: $API_TOKEN" "$API_BASE_URL$path")
  ctype=$(grep -i '^content-type:' "$headers" | tr -d '\r' | cut -d' ' -f2- || true)
  disposition=$(grep -i '^content-disposition:' "$headers" | tr -d '\r' | cut -d' ' -f2- || true)
  echo "   HTTP $http_code | content-type: ${ctype:-?} | content-disposition: ${disposition:-?}"
  if [ "$http_code" != "200" ]; then
    echo "   FALHA: esperado 200: $(head -c 500 "$body")"
    failures=$((failures + 1)); return
  fi
  if [[ "$ctype" != text/markdown* ]]; then
    echo "   FALHA: content-type nao e text/markdown"
    failures=$((failures + 1))
  fi
  if [[ "$disposition" != attachment*relatorio-musclefit-*.md* ]]; then
    echo "   FALHA: Content-Disposition sem attachment/nome relatorio-musclefit-*.md"
    failures=$((failures + 1))
  fi
  if ! grep -q '^# Relat' "$body" || grep -q 'puderam ser lidas agora' "$body"; then
    echo "   FALHA: corpo sem cabecalho ou com secao indisponivel: $(head -c 800 "$body")"
    failures=$((failures + 1))
  fi
}
get_expect_markdown "/reports/markdown"
get_expect_markdown "/reports/markdown?window=180d"

if [ "$failures" -gt 0 ]; then
  echo "SMOKE JOBS ASSINCRONOS: $failures falha(s)"
  exit 1
fi
echo "SMOKE JOBS ASSINCRONOS: OK"

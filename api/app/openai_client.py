from __future__ import annotations

import base64
import binascii
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .observability import log_event


_RESPONSES_URL = "https://api.openai.com/v1/responses"
_IMAGES_URL = "https://api.openai.com/v1/images/generations"

# Classificacao do erro (secao 56 da especificacao dos cards ilustrados). E o
# `error_code` gravado na geracao e o que decide se vale tentar de novo.
OPENAI_TIMEOUT = "OPENAI_TIMEOUT"
OPENAI_RATE_LIMIT = "OPENAI_RATE_LIMIT"
OPENAI_INVALID_REQUEST = "OPENAI_INVALID_REQUEST"
OPENAI_GENERATION_FAILED = "OPENAI_GENERATION_FAILED"

# ---------------------------------------------------------------------------
# Mensagem ao usuario x diagnostico (SEG-12, follow-up do #465)
# ---------------------------------------------------------------------------
#
# `str(exc)` de `OpenAIGenerationError` chega ao `detail` dos 502/503 das
# features de IA, ao `error` dos jobs assincronos (DynamoDB -> tela) e ao
# `error_message` dos cards. Por isso o texto das excecoes levantadas aqui e
# SEMPRE uma destas mensagens genericas em pt-BR, escolhida pelo tipo da
# falha. O diagnostico (status HTTP, corpo de erro truncado, request id da
# OpenAI) fica nos atributos `diagnostic`/`request_id` da excecao e vai para o
# log no evento `llm.error` - nunca para a resposta.
USER_MESSAGE_UNAVAILABLE = "O serviço de IA está indisponível no momento. Tente novamente em instantes."
USER_MESSAGE_TIMEOUT = "A IA demorou demais para responder. Tente novamente."
USER_MESSAGE_INVALID_RESPONSE = "A IA devolveu uma resposta inválida. Tente novamente."
USER_MESSAGE_RATE_LIMIT = "O limite de uso da IA foi atingido no momento. Aguarde alguns minutos e tente novamente."

EVENT_LLM_ERROR = "llm.error"

# Teto do diagnostico que vai para o log (o corpo de erro da OpenAI e curto; o
# teto so impede que uma pagina HTML de proxy encha o log).
DIAGNOSTIC_MAX_CHARS = 500

# Chave/token que a OpenAI possa ecoar no corpo de erro ("Incorrect API key
# provided: sk-...") sao mascarados antes do log.
_SECRET_PATTERN = re.compile(r"(sk-[A-Za-z0-9_\-*]{4,}|Bearer\s+\S+)")


# Timeouts do CLIENTE para as chamadas de TEXTO que rodam na Lambda
# `musclefit-workout-ai` (`infra/lambda_workout_ai.tf`, `timeout = 60`): avaliacao
# de serie, resumo de exercicio, resumo de treino, insights e analise de treinos.
# Tem de ser MENOR que o da Lambda - se o cliente esperar mais, a Lambda morre
# antes de gravar o job como `failed` e o usuario fica sem mensagem. 50 s +
# folga de ~10 s para parse, escrita do job e cold start. Se o timeout da Lambda
# mudar, mude aqui (um unico lugar). Lambdas de 120/180 s (plan-generator,
# cardio-agent) mantem os 90 s proprios de cada chamador.
LLM_TIMEOUT_LIVE_SECONDS = 50
LLM_TIMEOUT_LONG_SECONDS = 50

# Retry das chamadas de texto: SO em 429/5xx (resposta de erro que o servidor
# devolveu antes de gerar), SO nos primeiros segundos, e o orcamento TOTAL
# (tentativas + esperas) nunca passa do `timeout` pedido - cada nova tentativa
# recebe apenas o que sobrou. Timeout/erro de rede NAO repete (pode ja ter sido
# gerado e cobrado). Mesmo desenho de `generate_image`.
TEXT_RETRY_WINDOW_SECONDS = 10.0
TEXT_MAX_ATTEMPTS = 3
TEXT_RETRY_BACKOFF_SECONDS = (1.0, 2.0)
TEXT_RETRY_MIN_ATTEMPT_SECONDS = 10.0

EVENT_LLM_USAGE = "llm.usage"
EVENT_LLM_OUTPUT_INCOMPLETE = "llm.output_incomplete"

# ---------------------------------------------------------------------------
# Teto de saida (`max_output_tokens`) e chave de cache (`prompt_cache_key`)
# ---------------------------------------------------------------------------
#
# Os dois sao parametros do corpo do endpoint Responses (conferido no SDK
# oficial `openai` 3.28.0, `types/responses/response_create_params.py`):
# `max_output_tokens: int` e `prompt_cache_key: str`. Este cliente nao usa o
# SDK (urllib), entao eles vao como chaves do JSON, ao lado de `model`/`input`.
#
# `max_output_tokens` conta a saida VISIVEL e os tokens de RACIOCINIO. Estourou,
# a OpenAI responde `status: "incomplete"` com
# `incomplete_details.reason == "max_output_tokens"` e o texto cortado; aqui
# isso vira `OpenAIGenerationError` (ver `_post`): o chamador cai no mesmo
# caminho de falha de sempre e nada cortado e gravado.
#
# Criterio do teto (unico para todas as features):
#   teto = max(LLM_OUTPUT_CAP_FLOOR_TOKENS,
#              LLM_OUTPUT_CAP_HEADROOM_FACTOR * maior saida observada),
#   arredondado para cima ao multiplo de LLM_OUTPUT_CAP_ROUNDING_TOKENS.
# O piso existe porque o DEV nao tem amostra de `reasoning_tokens` das
# features curtas (so o tamanho do texto gravado): ele absorve raciocinio nao
# medido. Nas features de 50 s o timeout do cliente ja limita a saida bem
# antes do teto; o teto e rede de seguranca de CUSTO (laco degenerado), nao
# um limite de conteudo - nenhuma resposta observada chega perto dele.
LLM_OUTPUT_CAP_HEADROOM_FACTOR = 5
LLM_OUTPUT_CAP_FLOOR_TOKENS = 16_384
LLM_OUTPUT_CAP_ROUNDING_TOKENS = 1_024

# Maior saida observada por feature, em tokens (medida em 2026-10-09).
# `periodization`: `template_generation_logs.tokens_out` do DEV (7 geracoes,
#   max 6003 - JA inclui raciocinio).
# `cardio_agent`, `workout_summary`, `exercise_description`: maior texto
#   gravado no DEV (`cardio_recommendations`, n=26, 4241 chars;
#   `activities.ai_summary`, n=42, 894 chars;
#   `exercise_catalog.execution_description`, n=100, 969 chars), a ~3 chars
#   por token em pt-BR.
# `set_evaluation`, `exercise_summary`, `insights`, `workouts_analysis`: sem
#   saida gravada no DEV; estimativa por caso do relatorio de 2026-10-08
#   (`docs/ia/EVALS.md`, saida/rodada / casos), dobrada por seguranca.
# `meal_parse` (nutricao L5b; chave = `ai_usage.AiFeature.MEAL_PARSE.value`,
#   paridade em `test_nutrition_meal_parse_ai.py`): maior `output_tokens` do
#   eval pago de 2026-10-18 (9 casos x 8 rodadas, `docs/ia/EVALS.md`; ate 5
#   itens, ~85 tokens por item). Uma refeicao no teto de itens do diario daria
#   ~2,6 mil, ainda abaixo do piso.
# `label_photo` (nutricao L6, `ai_usage.AiFeature.LABEL_PHOTO`, teste de
#   paridade): maior `output_tokens` (com raciocinio) do eval pago da leitura
#   do rotulo em 2026-10-18 (`tests/eval/casos/nutrition_label.json`, 4
#   rodadas x 9 chamadas, prompts v1 e v2, gpt-5.4-mini: 213 a 256).
LLM_OUTPUT_OBSERVED_MAX_TOKENS: dict[str, int] = {
    "set_evaluation": 600,
    "exercise_summary": 500,
    "workout_summary": 300,
    "exercise_description": 330,
    "insights": 1_600,
    "workouts_analysis": 3_000,
    "cardio_agent": 1_420,
    "periodization": 6_003,
    "meal_parse": 426,
    "label_photo": 256,
}


def _output_cap(observed_tokens: int) -> int:
    raw = max(LLM_OUTPUT_CAP_FLOOR_TOKENS, LLM_OUTPUT_CAP_HEADROOM_FACTOR * observed_tokens)
    step = LLM_OUTPUT_CAP_ROUNDING_TOKENS
    return -(-raw // step) * step


# Teto por `feature` (rotulo do `llm.usage`). Feature fora daqui segue SEM
# teto: hoje so `workout_plan` (geradores legados Modo A/B), que nao tem caso
# de eval nem saida medida - sem observacao nao ha como garantir folga.
LLM_MAX_OUTPUT_TOKENS: dict[str, int] = {
    feature: _output_cap(observed) for feature, observed in LLM_OUTPUT_OBSERVED_MAX_TOKENS.items()
}

# Features cujo prompt comeca por um PREFIXO cacheavel (instrucoes fixas ->
# RAG -> dados variaveis; IA-05, #442). So elas mandam `prompt_cache_key`: a
# chave agrupa no mesmo servidor de cache as chamadas com o mesmo prefixo e
# sobe o acerto do cache automatico. As outras tem prompt curto ou sem prefixo
# comum e nao ganham nada com ela.
# `cardio_agent` desde o `cardio-agent-v3` (M98 C3a): instrucoes + RAG de
# `CARDIO.md` (selecao fixa) vem antes dos dados.
PROMPT_CACHE_KEY_FEATURES = frozenset({"insights", "workouts_analysis", "periodization", "cardio_agent"})


def prompt_cache_key(feature: Optional[str], prompt_version: Optional[str]) -> Optional[str]:
    """`<feature>:<prompt_version>` - estavel enquanto o prompt nao muda e
    trocada junto com o `PROMPT_VERSION` (prefixo novo = cache novo). Nunca
    leva usuario nem dado do pedido: o prefixo e o mesmo para todo mundo."""
    if feature not in PROMPT_CACHE_KEY_FEATURES or not prompt_version:
        return None
    return f"{feature}:{prompt_version}"


# ---------------------------------------------------------------------------
# Retencao na OpenAI (IA-17; politica em docs/ia/RETENCAO_DADOS_OPENAI.md)
# ---------------------------------------------------------------------------
#
# `store` e parametro do corpo do endpoint Responses (conferido no SDK oficial
# `openai` 3.28.0, `types/responses/response_create_params.py`): "Whether to
# store the generated model response for later retrieval via API. Defaults to
# true when omitted. If set to true, response data will be stored for at
# least 30 days". Nenhuma feature reabre uma resposta pela API
# (`previous_response_id`, `GET /responses/{id}`, `background`): toda resposta
# e lida na hora e o que importa e gravado no NOSSO banco. Guardar a resposta
# na OpenAI nao serve a nada e so prolonga a vida de dado de saude fora daqui.
# Vale para TODA chamada de texto (com ou sem `feature`). O endpoint de
# imagens nao tem o parametro.
LLM_STORE_RESPONSES = False


def _request_body(
    body: dict, *, feature: Optional[str], prompt_version: Optional[str]
) -> dict:
    """Corpo final: o do chamador + `store=false` (sempre) +
    `max_output_tokens`/`prompt_cache_key` quando a feature tem."""
    extra: dict = {"store": LLM_STORE_RESPONSES}
    cap = LLM_MAX_OUTPUT_TOKENS.get(feature or "")
    if cap:
        extra["max_output_tokens"] = cap
    key = prompt_cache_key(feature, prompt_version)
    if key:
        extra["prompt_cache_key"] = key
    return {**body, **extra}


def classify_http_status(status: Optional[int]) -> str:
    """Codigo do erro a partir do status HTTP da OpenAI. 4xx (fora 408/429)
    e pedido que nao vai passar tentando de novo: parametro invalido, chave
    sem permissao, organizacao nao verificada (exigida pela familia
    gpt-image) ou recusa da moderacao."""
    if status == 429:
        return OPENAI_RATE_LIMIT
    if status == 408:
        return OPENAI_TIMEOUT
    if status is not None and 400 <= status < 500:
        return OPENAI_INVALID_REQUEST
    return OPENAI_GENERATION_FAILED


def user_message_for(code: str) -> str:
    """Mensagem ao usuario para uma falha HTTP/rede com este `code`. 4xx fora
    408/429 (pedido invalido, chave sem permissao) e configuracao nossa: para
    quem usa, o servico esta indisponivel."""
    if code == OPENAI_RATE_LIMIT:
        return USER_MESSAGE_RATE_LIMIT
    if code == OPENAI_TIMEOUT:
        return USER_MESSAGE_TIMEOUT
    return USER_MESSAGE_UNAVAILABLE


def redact_diagnostic(text: object) -> str:
    """Diagnostico seguro para o log: chave/token mascarados e truncado em
    `DIAGNOSTIC_MAX_CHARS`."""
    return _SECRET_PATTERN.sub("***", str(text or ""))[:DIAGNOSTIC_MAX_CHARS]


class OpenAIGenerationError(RuntimeError):
    """Erro genérico de geração via OpenAI (chamada HTTP falhou, resposta
    inesperada, etc). Módulos que chamam `call_openai` podem propagar esta
    exceção diretamente ou envolvê-la numa exceção mais específica própria
    (ex: `CardioAgentGenerationError`, `WorkoutPlanGenerationError`), desde
    que herdem dela para manter o mesmo contrato de erro observável.

    `status` (HTTP, quando houve resposta) e `code` (`OPENAI_*`) sao
    opcionais e so por palavra-chave: as subclasses e os chamadores antigos
    continuam construindo com a mensagem apenas, e `str(exc)` nao muda.
    Sem `code` explicito, ele e derivado do `status` (sem status ->
    `OPENAI_GENERATION_FAILED`).

    `diagnostic`/`request_id` (SEG-12) carregam o detalhe tecnico da falha
    (corpo de erro truncado, request id da OpenAI) para o LOG; a mensagem
    (`str(exc)`) e a que o usuario ve e, nas falhas levantadas por este
    modulo, e sempre uma das `USER_MESSAGE_*`."""

    def __init__(
        self,
        message: str = "",
        *,
        status: Optional[int] = None,
        code: Optional[str] = None,
        diagnostic: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code or classify_http_status(status)
        self.diagnostic = redact_diagnostic(diagnostic) if diagnostic else None
        self.request_id = request_id

    @property
    def retryable(self) -> bool:
        """Transitorio do lado da OpenAI (429/5xx). Timeout NAO entra: o
        servidor pode ter gerado - e cobrado - a imagem que nao chegou."""
        return self.status == 429 or (self.status is not None and 500 <= self.status < 600)


def _api_key() -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key or api_key == "replace_me":
        raise OpenAIGenerationError(
            USER_MESSAGE_UNAVAILABLE, diagnostic="OPENAI_API_KEY nao configurada no backend."
        )
    return api_key


def _request_id(exc: urllib.error.HTTPError) -> Optional[str]:
    headers = getattr(exc, "headers", None)
    try:
        value = headers.get("x-request-id") if headers is not None else None
    except Exception:  # noqa: BLE001 - header ilegivel nao esconde o status
        value = None
    return str(value) if value else None


def _http_failure(exc: urllib.error.HTTPError, service: str) -> "OpenAIGenerationError":
    """Resposta de erro da OpenAI: mensagem generica pelo tipo (`status`);
    corpo truncado e request id so no diagnostico."""
    code = classify_http_status(exc.code)
    return OpenAIGenerationError(
        user_message_for(code),
        status=exc.code,
        code=code,
        diagnostic=f"{service} respondeu {exc.code}: {_http_error_detail(exc)}",
        request_id=_request_id(exc),
    )


def log_failure(exc: OpenAIGenerationError, **fields) -> None:
    """Evento `llm.error`: o diagnostico da falha, SO no log. Nunca o prompt,
    a resposta gerada nem a chave (o diagnostico ja sai mascarado)."""
    log_event(
        EVENT_LLM_ERROR,
        code=getattr(exc, "code", None),
        status=getattr(exc, "status", None),
        request_id=getattr(exc, "request_id", None),
        diagnostic=getattr(exc, "diagnostic", None) or redact_diagnostic(exc),
        **fields,
    )


def _post_once(body: dict, timeout: float) -> dict:
    """UMA tentativa no endpoint Responses; devolve o JSON cru da resposta.

    `TimeoutError` (estouro do `timeout` na leitura do socket) tambem vira
    `OpenAIGenerationError`: para quem chama, um timeout e apenas mais uma
    falha de geracao, com mensagem legivel no job.
    """
    request = urllib.request.Request(
        _RESPONSES_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise _http_failure(exc, "OpenAI") from exc
    except urllib.error.URLError as exc:
        raise OpenAIGenerationError(
            USER_MESSAGE_UNAVAILABLE, diagnostic=f"Erro de rede ao chamar OpenAI: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise OpenAIGenerationError(
            USER_MESSAGE_TIMEOUT, code=OPENAI_TIMEOUT, diagnostic=f"Timeout de {timeout:g}s ao chamar OpenAI."
        ) from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OpenAIGenerationError(
            USER_MESSAGE_INVALID_RESPONSE, diagnostic="OpenAI devolveu resposta que nao e JSON."
        ) from exc


def _emit_usage(
    data: Optional[dict],
    *,
    feature: Optional[str],
    model: Optional[str],
    user_id,
    started: float,
    status: str,
    attempts: int,
    prompt_version: Optional[str] = None,
) -> None:
    """Evento `llm.usage` (FinOps): tokens e latencia, NUNCA prompt nem
    resposta. `log_event` omite campos `None` e nunca levanta excecao.
    `prompt_version` (IA-11) diz qual versao do prompt gerou a chamada: e o
    que permite comparar custo/qualidade antes e depois de mudar um prompt."""
    usage = (data or {}).get("usage")
    usage = usage if isinstance(usage, dict) else {}
    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    input_details = input_details if isinstance(input_details, dict) else {}
    output_details = output_details if isinstance(output_details, dict) else {}
    log_event(
        EVENT_LLM_USAGE,
        provider=PROVIDER_OPENAI,
        feature=feature,
        model=str((data or {}).get("model") or model or "") or None,
        input_tokens=_as_int(usage.get("input_tokens")),
        cached_tokens=_as_int(input_details.get("cached_tokens")),
        output_tokens=_as_int(usage.get("output_tokens")),
        reasoning_tokens=_as_int(output_details.get("reasoning_tokens")),
        latency_ms=int(round((_monotonic() - started) * 1000)),
        user_id=user_id,
        status=status,
        attempts=attempts,
        prompt_version=prompt_version,
    )


def _post(
    body: dict,
    timeout: float,
    *,
    feature: Optional[str] = None,
    user_id=None,
    prompt_version: Optional[str] = None,
) -> dict:
    """POST no endpoint Responses com retry limitado e o evento `llm.usage`.

    Corpo compartilhado por `call_openai` e `call_openai_structured` - mesma
    autenticacao, mesmo endpoint, mesmo tratamento de erro. Retry so em
    429/5xx (ver `TEXT_*`), com orcamento total <= `timeout`; a 1a tentativa
    usa o `timeout` inteiro, como sempre.
    """
    body = _request_body(body, feature=feature, prompt_version=prompt_version)
    started = _monotonic()
    attempt = 0
    while True:
        attempt += 1
        remaining = timeout if attempt == 1 else timeout - (_monotonic() - started)
        try:
            data = _post_once(body, remaining)
            break
        except OpenAIGenerationError as exc:
            elapsed = _monotonic() - started
            backoff = TEXT_RETRY_BACKOFF_SECONDS[min(attempt, len(TEXT_RETRY_BACKOFF_SECONDS)) - 1]
            can_retry = (
                exc.retryable
                and attempt < TEXT_MAX_ATTEMPTS
                and elapsed < TEXT_RETRY_WINDOW_SECONDS
                and timeout - elapsed - backoff >= TEXT_RETRY_MIN_ATTEMPT_SECONDS
            )
            log_failure(
                exc,
                provider=PROVIDER_OPENAI,
                feature=feature,
                prompt_version=prompt_version,
                user_id=user_id,
                model=body.get("model"),
                attempt=attempt,
                will_retry=can_retry,
            )
            if not can_retry:
                _emit_usage(
                    None,
                    feature=feature,
                    model=body.get("model"),
                    user_id=user_id,
                    started=started,
                    status="timeout" if exc.code == OPENAI_TIMEOUT else "error",
                    attempts=attempt,
                    prompt_version=prompt_version,
                )
                raise
            _sleep(backoff)
    incomplete = isinstance(data, dict) and data.get("status") == "incomplete"
    _emit_usage(
        data,
        feature=feature,
        model=body.get("model"),
        user_id=user_id,
        started=started,
        status="incomplete" if incomplete else "ok",
        attempts=attempt,
        prompt_version=prompt_version,
    )
    if incomplete:
        details = data.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        # So metadados: nunca o prompt nem o texto cortado.
        log_event(
            EVENT_LLM_OUTPUT_INCOMPLETE,
            provider=PROVIDER_OPENAI,
            feature=feature,
            prompt_version=prompt_version,
            user_id=user_id,
            reason=reason,
            max_output_tokens=body.get("max_output_tokens"),
            output_tokens=_as_int(usage.get("output_tokens")),
        )
        # Sem retry (a 2a tentativa seria cobrada e tenderia a cortar igual) e
        # sem devolver o texto parcial: o chamador segue o caminho de falha
        # que ja tinha para erro HTTP/timeout.
        raise OpenAIGenerationError(
            USER_MESSAGE_INVALID_RESPONSE,
            diagnostic=(
                f"OpenAI devolveu resposta incompleta ({reason or 'motivo nao informado'}); "
                "a saida cortada foi descartada."
            ),
        )
    return data


def _output_text(data: dict) -> str:
    """`output_text` quando presente, senao a concatenacao de
    `output[*].content[*].text` - parsing historico, preservado."""
    if data.get("output_text"):
        return data["output_text"]

    texts = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("text"):
                texts.append(content["text"])
    return "\n".join(texts)


def _refusal(data: dict) -> Optional[str]:
    """Texto da recusa do modelo, quando houver (`content[*].type ==
    'refusal'`). Com saida estruturada, a recusa vem NO LUGAR do JSON: sem
    esta checagem, o erro apareceria como "JSON invalido" e esconderia o
    motivo real."""
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "refusal" and content.get("refusal"):
                return str(content["refusal"])
    return None


@dataclass(frozen=True)
class StructuredResult:
    """Saida de `call_openai_structured`: o JSON ja parseado + o que o log de
    auditoria precisa (`template_generation_logs`, secao 7.3)."""

    data: dict
    model: str
    tokens_in: Optional[int]
    tokens_out: Optional[int]


# ---------------------------------------------------------------------------
# Provedor por feature (Anthropic opcional; implementacao em `llm_anthropic`)
# ---------------------------------------------------------------------------
#
# Sem `LLM_PROVIDER_<FEATURE>=anthropic`, as tres `call_openai*` seguem
# exatamente o caminho de sempre (mesmo corpo HTTP, mesmo endpoint): o modulo
# `llm_anthropic` so e importado para ler a env, e o SDK `anthropic` nem isso.
PROVIDER_OPENAI = "openai"
EVENT_LLM_FALLBACK = "llm.fallback"


def _dispatch(feature, timeout, *, user_id, prompt_version, openai_call, anthropic_call):
    """Roda na Anthropic quando a feature esta ligada nela; se falhar com um
    erro elegivel e o fallback estiver ligado, refaz na OpenAI (modelo OpenAI
    da feature) com o tempo que sobrou - nunca menos que o minimo de uma
    tentativa, para a Lambda ainda conseguir gravar a falha."""
    from . import llm_anthropic

    if llm_anthropic.provider_for(feature) != llm_anthropic.PROVIDER_ANTHROPIC:
        return openai_call(timeout)
    started = _monotonic()
    try:
        return anthropic_call(timeout)
    except OpenAIGenerationError as exc:
        fallback = llm_anthropic.fallback_provider_for(feature)
        remaining = timeout - (_monotonic() - started)
        if (
            fallback != PROVIDER_OPENAI
            or not llm_anthropic.fallback_eligible(exc)
            or remaining < TEXT_RETRY_MIN_ATTEMPT_SECONDS
        ):
            raise
        log_event(
            EVENT_LLM_FALLBACK,
            feature=feature,
            from_provider=llm_anthropic.PROVIDER_ANTHROPIC,
            to_provider=PROVIDER_OPENAI,
            code=exc.code,
            status=exc.status,
            prompt_version=prompt_version,
            user_id=user_id,
        )
        return openai_call(remaining)


def call_openai(
    prompt: str,
    model: str,
    timeout: float = 90,
    *,
    feature: Optional[str] = None,
    user_id=None,
    prompt_version: Optional[str] = None,
    cache_prefix: Optional[str] = None,
) -> str:
    """Chama a API Responses da OpenAI (https://api.openai.com/v1/responses)
    com o prompt e o modelo informados, retornando o texto de saída.

    Extraído de `cardio_agent_ai.py`/`workout_plan_ai.py` (que tinham cópias
    praticamente idênticas desta função) para eliminar a duplicação -
    mantém exatamente o mesmo comportamento de autenticação (header
    `Authorization: Bearer {OPENAI_API_KEY}`), timeout e parsing de resposta
    (tenta `output_text` primeiro, senão percorre `output[*].content[*].text`).

    `feature`/`user_id`/`prompt_version` (opcionais) rotulam o evento
    `llm.usage`. Eles mesmos nao vao no corpo; `feature` (com a versao)
    so decide o `max_output_tokens` e o `prompt_cache_key` que o acompanham
    (`LLM_MAX_OUTPUT_TOKENS`, `PROMPT_CACHE_KEY_FEATURES`). `user_id` nunca
    sai do processo.

    A leitura de `OPENAI_API_KEY` do ambiente e a validação de "não
    configurada" são feitas aqui (`_api_key`), e a falha propaga
    `OpenAIGenerationError` - cada módulo chamador continua livre para
    envolver numa exceção própria que herde dela.

    `cache_prefix` (opcional): o começo ESTÁVEL do prompt (instruções + RAG).
    Não vai para a OpenAI (que usa o `prompt_cache_key`); no provedor
    Anthropic vira o bloco com `cache_control`. Com `LLM_PROVIDER_<FEATURE>`
    = `anthropic` a chamada vai para `llm_anthropic` (ver `_dispatch`).
    """

    def _openai(t: float) -> str:
        return _output_text(
            _post(
                {"model": model, "input": prompt},
                t,
                feature=feature,
                user_id=user_id,
                prompt_version=prompt_version,
            )
        )

    def _anthropic(t: float) -> str:
        from . import llm_anthropic

        return llm_anthropic.call_text(
            prompt, t, feature=feature, user_id=user_id, prompt_version=prompt_version, cache_prefix=cache_prefix
        )

    return _dispatch(
        feature, timeout, user_id=user_id, prompt_version=prompt_version, openai_call=_openai, anthropic_call=_anthropic
    )


def call_openai_structured(
    prompt: str,
    model: str,
    schema: dict,
    *,
    schema_name: str,
    timeout: float = 90,
    feature: Optional[str] = None,
    user_id=None,
    prompt_version: Optional[str] = None,
    cache_prefix: Optional[str] = None,
) -> StructuredResult:
    """Saida ESTRUTURADA (`text.format={type:"json_schema", strict:true}`) da
    API Responses: o modelo e obrigado pelo servidor da OpenAI a devolver um
    JSON que casa com `schema`, em vez de "texto que parece JSON".

    Usada pelo Motor de Periodizacao (PLANO_20260912.md secao 7.1, passo 6) -
    o unico lugar onde a LLM permanece depois da refatoracao, e justamente
    porque a saida ainda passa pelo validador deterministico
    (`app/periodization/validation.py`) e pela edicao humana antes de virar
    modelo de treino.

    `timeout` default de 90 s, MENOR que os 120 s da Lambda `plan-generator`
    (secao 7.3): o estouro precisa virar job `failed` com mensagem, nao morte
    da Lambda sem resposta.

    Erros (todos `OpenAIGenerationError`): falha HTTP, timeout, recusa do
    modelo, resposta vazia ou JSON que nao parseia.

    `cache_prefix` e o provedor por feature: ver `call_openai`.
    """

    def _anthropic(t: float) -> StructuredResult:
        from . import llm_anthropic

        return llm_anthropic.call_structured(
            prompt, schema, t, feature=feature, user_id=user_id, prompt_version=prompt_version,
            cache_prefix=cache_prefix,
        )

    return _dispatch(
        feature,
        timeout,
        user_id=user_id,
        prompt_version=prompt_version,
        openai_call=lambda t: _openai_structured(
            prompt, model, schema, schema_name=schema_name, timeout=t, feature=feature,
            user_id=user_id, prompt_version=prompt_version,
        ),
        anthropic_call=_anthropic,
    )


def _openai_structured(
    prompt: str,
    model: str,
    schema: dict,
    *,
    schema_name: str,
    timeout: float,
    feature: Optional[str],
    user_id,
    prompt_version: Optional[str],
) -> StructuredResult:
    data = _post(
        {
            "model": model,
            "input": prompt,
            "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
        },
        timeout,
        feature=feature,
        user_id=user_id,
        prompt_version=prompt_version,
    )

    try:
        parsed = _structured_payload(data)
    except OpenAIGenerationError as exc:
        log_failure(exc, feature=feature, prompt_version=prompt_version, user_id=user_id, model=model)
        raise

    usage = data.get("usage") or {}
    return StructuredResult(
        data=parsed,
        model=str(data.get("model") or model),
        tokens_in=_as_int(usage.get("input_tokens")),
        tokens_out=_as_int(usage.get("output_tokens")),
    )


# ---------------------------------------------------------------------------
# Entrada de IMAGEM (nutricao L6, foto do rotulo) - mesmo endpoint Responses
# ---------------------------------------------------------------------------
#
# Formato conferido na documentacao oficial ("Images and vision", Responses
# API): a mensagem `user` leva em `content` um `input_text` e um ou mais
# `{"type": "input_image", "image_url": "data:<tipo>;base64,<...>", "detail":
# "high"}`. Os tokens da imagem ja vem somados em `usage.input_tokens`.
# Nada aqui loga a imagem (nem o prompt): o `llm.usage` so leva tokens.

STRUCTURED_FORMAT_JSON_SCHEMA = "json_schema"
STRUCTURED_FORMAT_JSON_OBJECT = "json_object"
STRUCTURED_FORMATS = (STRUCTURED_FORMAT_JSON_SCHEMA, STRUCTURED_FORMAT_JSON_OBJECT)
IMAGE_DETAILS = ("low", "high", "auto")


@dataclass(frozen=True)
class ImageInput:
    """Uma imagem para `call_openai_structured_vision` (bytes + tipo + detail)."""

    data: bytes
    content_type: str
    detail: str

    def data_url(self) -> str:
        return f"data:{self.content_type};base64,{base64.b64encode(self.data).decode('ascii')}"


def _image_part(image: ImageInput) -> dict:
    if not image.data:
        raise ValueError("imagem vazia")
    if image.detail not in IMAGE_DETAILS:
        raise ValueError(f"detail invalido: {image.detail!r} (use {', '.join(IMAGE_DETAILS)})")
    return {"type": "input_image", "image_url": image.data_url(), "detail": image.detail}


def call_openai_structured_vision(
    prompt: str,
    images: list[ImageInput],
    model: str,
    schema: dict,
    *,
    schema_name: str,
    timeout: float = 90,
    feature: Optional[str] = None,
    user_id=None,
    prompt_version: Optional[str] = None,
    response_format: str = STRUCTURED_FORMAT_JSON_SCHEMA,
) -> StructuredResult:
    """Irma de `call_openai_structured` com IMAGEM na entrada (mensagem `user`
    com `input_text` + `input_image`). Mesmo `_post` (retry 429/5xx, teto de
    saida da `feature`, `store=false`, `llm.usage`) e o mesmo parse
    (`_structured_payload`: recusa, vazio e JSON invalido viram erro).

    `response_format`: `json_schema` (strict, padrao) ou `json_object` - a
    alternativa da decisao D-E do plano de nutricao, caso o modelo nao aceite
    saida estruturada com imagem; com `json_object` o `schema` nao vai no corpo
    (o prompt precisa pedir JSON) e quem chama valida a forma (Pydantic).
    `call_openai_structured` fica intacta.

    Provedor por feature: ver `call_openai`. Na Anthropic a imagem vai num
    bloco `image` base64 (`detail` nao tem equivalente) e a saida e sempre
    `json_schema`."""
    if response_format not in STRUCTURED_FORMATS:
        raise ValueError(f"response_format invalido: {response_format!r}")
    if not images:
        raise ValueError("call_openai_structured_vision exige ao menos uma imagem")

    def _anthropic(t: float) -> StructuredResult:
        from . import llm_anthropic

        return llm_anthropic.call_structured(
            prompt, schema, t, feature=feature, user_id=user_id, prompt_version=prompt_version, images=images
        )

    return _dispatch(
        feature,
        timeout,
        user_id=user_id,
        prompt_version=prompt_version,
        openai_call=lambda t: _openai_structured_vision(
            prompt, images, model, schema, schema_name=schema_name, timeout=t, feature=feature,
            user_id=user_id, prompt_version=prompt_version, response_format=response_format,
        ),
        anthropic_call=_anthropic,
    )


def _openai_structured_vision(
    prompt: str,
    images: list[ImageInput],
    model: str,
    schema: dict,
    *,
    schema_name: str,
    timeout: float,
    feature: Optional[str],
    user_id,
    prompt_version: Optional[str],
    response_format: str,
) -> StructuredResult:
    if response_format == STRUCTURED_FORMAT_JSON_SCHEMA:
        text_format = {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}
    else:
        text_format = {"type": "json_object"}
    data = _post(
        {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}, *(_image_part(i) for i in images)],
                }
            ],
            "text": {"format": text_format},
        },
        timeout,
        feature=feature,
        user_id=user_id,
        prompt_version=prompt_version,
    )

    try:
        parsed = _structured_payload(data)
    except OpenAIGenerationError as exc:
        log_failure(exc, feature=feature, prompt_version=prompt_version, user_id=user_id, model=model)
        raise

    usage = data.get("usage") or {}
    return StructuredResult(
        data=parsed,
        model=str(data.get("model") or model),
        tokens_in=_as_int(usage.get("input_tokens")),
        tokens_out=_as_int(usage.get("output_tokens")),
    )


def _structured_payload(data: dict) -> dict:
    """JSON da saida estruturada. Recusa, vazio ou JSON invalido viram
    `OpenAIGenerationError` com a mensagem de resposta invalida; o motivo
    exato fica no diagnostico."""

    def _invalid(diagnostic: str) -> OpenAIGenerationError:
        return OpenAIGenerationError(USER_MESSAGE_INVALID_RESPONSE, diagnostic=diagnostic)

    refusal = _refusal(data)
    if refusal:
        raise _invalid(f"OpenAI recusou a geracao: {refusal}")
    text = _output_text(data)
    if not text.strip():
        raise _invalid("OpenAI devolveu resposta vazia.")
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise _invalid(f"OpenAI devolveu JSON invalido: {exc}") from exc
    if not isinstance(parsed, dict):
        raise _invalid("OpenAI devolveu um JSON que nao e objeto.")
    return parsed


def _as_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Imagens (cards ilustrados de exercicios) - POST /v1/images/generations
# ---------------------------------------------------------------------------
#
# Irma de `call_openai`: mesmo `urllib`, mesma chave, sem SDK. Quem chama e o
# worker assincrono da Lambda `musclefit-plan-generator` (timeout 180 s), nunca
# a api-core (API Gateway corta em 29 s e a OpenAI documenta "ate 2 minutos").
#
# Nada aqui loga o prompt nem a chave.

# Defaults = contrato de envs do PR1 (infra/lambda_*.tf, docker-compose.yml).
DEFAULT_IMAGE_MODEL = "gpt-image-1.5"
DEFAULT_IMAGE_QUALITY = "medium"
DEFAULT_IMAGE_SIZE = "1024x1536"
DEFAULT_IMAGE_TIMEOUT_SECONDS = 150.0

# Retry SO em 429/5xx, SO enquanto o tempo decorrido desde a 1a tentativa for
# menor que esta janela, e NUNCA depois de timeout - cada tentativa que chega
# a gerar e cobrada. Orcamento: a ultima tentativa comeca antes de
# 20 s + 2 s de espera e dura no maximo o timeout (150 s) -> < 172 s, abaixo
# dos 180 s da Lambda, que entao sempre consegue gravar a falha.
IMAGE_RETRY_WINDOW_SECONDS = 20.0
IMAGE_MAX_ATTEMPTS = 3
IMAGE_RETRY_BACKOFF_SECONDS = (1.0, 2.0)

# Preco por imagem em USD, por (modelo, qualidade, tamanho) - tabela publica
# da OpenAI verificada em 2026-10-03 (Anexo A do plano dos cards). Combinacao
# fora da tabela -> custo desconhecido (None), nunca um chute.
IMAGE_PRICES_USD: dict[tuple[str, str, str], Decimal] = {
    ("gpt-image-1.5", "low", "1024x1536"): Decimal("0.013"),
    ("gpt-image-1.5", "medium", "1024x1536"): Decimal("0.05"),
    ("gpt-image-1.5", "high", "1024x1536"): Decimal("0.20"),
    ("gpt-image-1-mini", "low", "1024x1536"): Decimal("0.006"),
    ("gpt-image-1-mini", "medium", "1024x1536"): Decimal("0.015"),
    ("gpt-image-1-mini", "high", "1024x1536"): Decimal("0.052"),
}

# `background` aceito por `generate_image` (parametro da OpenAI Images; o
# transparente exige PNG/WebP - aqui e sempre PNG).
IMAGE_BACKGROUNDS = ("transparent", "opaque", "auto")

# Pontos de injecao para os testes (relogio e espera sem dormir de verdade).
_monotonic = time.monotonic
_sleep = time.sleep


@dataclass(frozen=True)
class ImageSettings:
    model: str
    quality: str
    size: str
    timeout_seconds: float


@dataclass(frozen=True)
class ImageResult:
    """Saida de `generate_image`: os bytes do PNG + o que a geracao grava
    (`exercise_card_generations`: model/quality/size/tokens/cost_usd)."""

    image: bytes
    model: str
    quality: str
    size: str
    tokens_in: Optional[int]
    tokens_out: Optional[int]
    cost_usd: Optional[Decimal]
    attempts: int
    elapsed_seconds: float


def _env_text(name: str, default: str) -> str:
    value = (os.environ.get(name) or "").strip()
    return value or default


def image_settings() -> ImageSettings:
    """Configuracao vigente, lida do ambiente A CADA chamada (OPENAI_IMAGE_*)."""
    try:
        timeout = float(_env_text("OPENAI_IMAGE_TIMEOUT_SECONDS", str(DEFAULT_IMAGE_TIMEOUT_SECONDS)))
    except ValueError:
        timeout = DEFAULT_IMAGE_TIMEOUT_SECONDS
    return ImageSettings(
        model=_env_text("OPENAI_IMAGE_MODEL", DEFAULT_IMAGE_MODEL),
        quality=_env_text("OPENAI_IMAGE_QUALITY", DEFAULT_IMAGE_QUALITY),
        size=_env_text("OPENAI_IMAGE_SIZE", DEFAULT_IMAGE_SIZE),
        timeout_seconds=timeout if timeout > 0 else DEFAULT_IMAGE_TIMEOUT_SECONDS,
    )


def image_cost_usd(model: str, quality: str, size: str) -> Optional[Decimal]:
    return IMAGE_PRICES_USD.get((model, quality, size))


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    """`error.code: error.message` do corpo JSON da OpenAI, curto. O corpo de
    erro nao ecoa o prompt; mesmo assim so os dois campos sobem."""
    try:
        raw = exc.read().decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 - corpo ilegivel nao pode esconder o status
        raw = ""
    try:
        error = (json.loads(raw) or {}).get("error") or {}
    except (ValueError, AttributeError):
        error = {}
    if isinstance(error, dict) and (error.get("message") or error.get("code")):
        detail = f"{error.get('code') or error.get('type') or 'erro'}: {error.get('message') or ''}".strip()
    else:
        detail = raw.strip()
    return detail[:300]


def _post_image(body: dict, timeout: float) -> dict:
    """UMA tentativa. Toda falha sai como `OpenAIGenerationError` ja
    classificada (`status`/`code`)."""
    request = urllib.request.Request(
        _IMAGES_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise _http_failure(exc, "OpenAI Images") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise OpenAIGenerationError(
                USER_MESSAGE_TIMEOUT,
                code=OPENAI_TIMEOUT,
                diagnostic=f"Timeout de {timeout:g}s ao chamar OpenAI Images.",
            ) from exc
        raise OpenAIGenerationError(
            USER_MESSAGE_UNAVAILABLE, diagnostic=f"Erro de rede ao chamar OpenAI Images: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise OpenAIGenerationError(
            USER_MESSAGE_TIMEOUT, code=OPENAI_TIMEOUT, diagnostic=f"Timeout de {timeout:g}s ao chamar OpenAI Images."
        ) from exc
    except OSError as exc:
        # Conexao derrubada no meio da leitura: a imagem pode ter sido gerada
        # (e cobrada) - sem status, sem retry.
        raise OpenAIGenerationError(
            USER_MESSAGE_UNAVAILABLE, diagnostic=f"Conexao interrompida com a OpenAI Images: {exc}"
        ) from exc
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OpenAIGenerationError(
            USER_MESSAGE_INVALID_RESPONSE, diagnostic="OpenAI Images devolveu resposta que nao e JSON."
        ) from exc
    if not isinstance(data, dict):
        raise OpenAIGenerationError(
            USER_MESSAGE_INVALID_RESPONSE, diagnostic="OpenAI Images devolveu um JSON que nao e objeto."
        )
    return data


def _decode_image(data: dict) -> bytes:
    items = data.get("data")
    first = items[0] if isinstance(items, list) and items else None
    encoded = first.get("b64_json") if isinstance(first, dict) else None
    if not encoded or not isinstance(encoded, str):
        raise OpenAIGenerationError(
            USER_MESSAGE_INVALID_RESPONSE, diagnostic="OpenAI Images devolveu resposta sem imagem (b64_json ausente)."
        )
    try:
        image = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise OpenAIGenerationError(
            USER_MESSAGE_INVALID_RESPONSE, diagnostic="OpenAI Images devolveu b64_json invalido."
        ) from exc
    if not image:
        raise OpenAIGenerationError(USER_MESSAGE_INVALID_RESPONSE, diagnostic="OpenAI Images devolveu imagem vazia.")
    return image


def generate_image(
    prompt: str,
    *,
    model: Optional[str] = None,
    quality: Optional[str] = None,
    size: Optional[str] = None,
    timeout: Optional[float] = None,
    moderation: Optional[str] = None,
    background: Optional[str] = None,
) -> ImageResult:
    """Gera UMA imagem PNG (`n=1`, `output_format="png"`).

    Parametros omitidos vem de `image_settings()` (envs `OPENAI_IMAGE_*`).
    NAO envia `response_format`: os modelos gpt-image sempre devolvem
    `b64_json` e recusam esse parametro com 400. `moderation` so vai no corpo
    quando informado (`"auto"`/`"low"`); `background` tambem
    (`"transparent"`/`"opaque"`/`"auto"` - os cards pedem `"transparent"`,
    ver `exercise_card_jobs.ILLUSTRATION_BACKGROUND`). Valor fora da lista
    nem chama a API (`OPENAI_INVALID_REQUEST`).

    Erros: `OpenAIGenerationError` com `code` em `OPENAI_TIMEOUT`,
    `OPENAI_RATE_LIMIT`, `OPENAI_INVALID_REQUEST` ou
    `OPENAI_GENERATION_FAILED` (e `status` HTTP quando houve resposta).
    """
    if not str(prompt or "").strip():
        raise OpenAIGenerationError("Prompt de imagem vazio.", code=OPENAI_INVALID_REQUEST)
    if background is not None and background not in IMAGE_BACKGROUNDS:
        raise OpenAIGenerationError(
            f"background invalido: {background!r} (use {', '.join(IMAGE_BACKGROUNDS)}).", code=OPENAI_INVALID_REQUEST
        )
    settings = image_settings()
    chosen_model = model or settings.model
    chosen_quality = quality or settings.quality
    chosen_size = size or settings.size
    chosen_timeout = float(timeout) if timeout else settings.timeout_seconds

    body: dict = {
        "model": chosen_model,
        "prompt": prompt,
        "size": chosen_size,
        "quality": chosen_quality,
        "n": 1,
        "output_format": "png",
    }
    if moderation:
        body["moderation"] = moderation
    if background:
        body["background"] = background

    started = _monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            data = _post_image(body, chosen_timeout)
            break
        except OpenAIGenerationError as exc:
            elapsed = _monotonic() - started
            will_retry = exc.retryable and attempt < IMAGE_MAX_ATTEMPTS and elapsed < IMAGE_RETRY_WINDOW_SECONDS
            log_failure(exc, feature="exercise_card", model=chosen_model, attempt=attempt, will_retry=will_retry)
            if not will_retry:
                raise
            _sleep(IMAGE_RETRY_BACKOFF_SECONDS[min(attempt, len(IMAGE_RETRY_BACKOFF_SECONDS)) - 1])

    try:
        image = _decode_image(data)
    except OpenAIGenerationError as exc:
        log_failure(exc, feature="exercise_card", model=chosen_model, attempt=attempt, will_retry=False)
        raise
    effective_quality = str(data.get("quality") or chosen_quality)
    effective_size = str(data.get("size") or chosen_size)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return ImageResult(
        image=image,
        model=chosen_model,
        quality=effective_quality,
        size=effective_size,
        tokens_in=_as_int(usage.get("input_tokens")),
        tokens_out=_as_int(usage.get("output_tokens")),
        cost_usd=image_cost_usd(chosen_model, effective_quality, effective_size),
        attempts=attempt,
        elapsed_seconds=round(_monotonic() - started, 3),
    )

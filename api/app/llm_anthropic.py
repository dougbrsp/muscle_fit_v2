"""Provedor Anthropic (Claude) do cliente LLM unico, ligado POR FEATURE.

`openai_client.call_openai*` continua sendo a unica porta de entrada das
features de texto. Este modulo decide, por `feature`, qual provedor atende e,
quando e a Anthropic, faz a chamada pelo SDK oficial `anthropic` com o mesmo
contrato observavel do caminho OpenAI: as mesmas excecoes
(`OpenAIGenerationError`, mensagens `USER_MESSAGE_*` ao usuario, diagnostico
mascarado so no log), o mesmo retry (so 429/5xx/529, dentro da janela de 10 s,
nunca depois de timeout), o mesmo teto de saida por feature e os mesmos
eventos `llm.usage`/`llm.error`/`llm.output_incomplete` (com `provider`).

Configuracao (env, lida A CADA chamada; `<FEATURE>` = rotulo `feature` do
`llm.usage` em maiusculas, ex. `SET_EVALUATION`, `MEAL_PARSE`):

- `LLM_PROVIDER_<FEATURE>`: `openai` (padrao) ou `anthropic`. Sem a env, nada
  muda: o corpo HTTP da OpenAI e o mesmo de antes.
- `ANTHROPIC_MODEL_<FEATURE>`: modelo Claude (padrao em `DEFAULT_ANTHROPIC_MODEL`).
- `ANTHROPIC_EFFORT_<FEATURE>`: `low`/`medium`/`high`/`xhigh`/`max`
  (padrao em `DEFAULT_ANTHROPIC_EFFORT`).
- `LLM_FALLBACK_PROVIDER_<FEATURE>` ou `LLM_FALLBACK_PROVIDER` (global): `openai`
  liga o fallback (ver `fallback_eligible`). Vazio = sem fallback.
- `ANTHROPIC_API_KEY`: populada no cold start por `secrets_bootstrap` a partir
  de `ANTHROPIC_API_KEY_SECRET_ARN` (`musclefit/claude-api-key`).

O SDK so e importado quando uma feature esta ligada na Anthropic: com os
padroes, a imagem nem carrega o pacote.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Final, Optional, Sequence

from . import llm_anthropic_schema, openai_client
from .observability import log_event
from .openai_client import (
    EVENT_LLM_OUTPUT_INCOMPLETE,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_OUTPUT_CAP_FLOOR_TOKENS,
    OPENAI_TIMEOUT,
    TEXT_MAX_ATTEMPTS,
    TEXT_RETRY_BACKOFF_SECONDS,
    TEXT_RETRY_MIN_ATTEMPT_SECONDS,
    TEXT_RETRY_WINDOW_SECONDS,
    USER_MESSAGE_INVALID_RESPONSE,
    USER_MESSAGE_TIMEOUT,
    USER_MESSAGE_UNAVAILABLE,
    ImageInput,
    OpenAIGenerationError,
    StructuredResult,
    TextResult,
    classify_http_status,
    log_failure,
    user_message_for,
)

# ---------------------------------------------------------------------------
# Provedores e padroes por feature
# ---------------------------------------------------------------------------

PROVIDER_OPENAI: Final[str] = "openai"
PROVIDER_ANTHROPIC: Final[str] = "anthropic"
PROVIDERS: Final[tuple[str, ...]] = (PROVIDER_OPENAI, PROVIDER_ANTHROPIC)
DEFAULT_PROVIDER: Final[str] = PROVIDER_OPENAI

ENV_PROVIDER_PREFIX: Final[str] = "LLM_PROVIDER_"
ENV_MODEL_PREFIX: Final[str] = "ANTHROPIC_MODEL_"
ENV_EFFORT_PREFIX: Final[str] = "ANTHROPIC_EFFORT_"
ENV_FALLBACK_PREFIX: Final[str] = "LLM_FALLBACK_PROVIDER_"
ENV_FALLBACK_GLOBAL: Final[str] = "LLM_FALLBACK_PROVIDER"
ENV_API_KEY: Final[str] = "ANTHROPIC_API_KEY"

# IDs exatos, sem sufixo de data.
ANTHROPIC_MODEL_SONNET: Final[str] = "claude-sonnet-5-5"
ANTHROPIC_MODEL_HAIKU: Final[str] = "claude-haiku-5-5"

# Features de texto com rotulo `feature` (mesmas chaves do teto de saida, mais
# o gerador legado `workout_plan`, que nao tem teto medido).
LLM_FEATURES: Final[tuple[str, ...]] = (*LLM_MAX_OUTPUT_TOKENS.keys(), "workout_plan")

# Features curtas e de alto volume: Haiku e esforco baixo. As demais
# (raciocinio): Sonnet e esforco medio.
LIGHT_FEATURES: Final[frozenset[str]] = frozenset({"set_evaluation", "exercise_summary", "workout_summary"})

EFFORT_LOW: Final[str] = "low"
EFFORT_MEDIUM: Final[str] = "medium"
EFFORTS: Final[tuple[str, ...]] = ("low", "medium", "high", "xhigh", "max")

DEFAULT_ANTHROPIC_MODEL: Final[dict[str, str]] = {
    f: (ANTHROPIC_MODEL_HAIKU if f in LIGHT_FEATURES else ANTHROPIC_MODEL_SONNET) for f in LLM_FEATURES
}
DEFAULT_ANTHROPIC_EFFORT: Final[dict[str, str]] = {
    f: (EFFORT_LOW if f in LIGHT_FEATURES else EFFORT_MEDIUM) for f in LLM_FEATURES
}

# `max_tokens` da Anthropic conta o raciocinio (thinking) e a saida, como o
# `max_output_tokens` da OpenAI: o mesmo teto por feature serve (piso de
# 16 384, folga de 5x a maior saida observada). Feature sem teto medido
# (`workout_plan`) usa o piso.
DEFAULT_MAX_TOKENS: Final[int] = LLM_OUTPUT_CAP_FLOOR_TOKENS

# Codigos novos (`OpenAIGenerationError.code`).
ANTHROPIC_REFUSAL: Final[str] = "ANTHROPIC_REFUSAL"
ANTHROPIC_BILLING: Final[str] = "ANTHROPIC_BILLING"
ANTHROPIC_AUTH: Final[str] = "ANTHROPIC_AUTH"
ANTHROPIC_NOT_CONFIGURED: Final[str] = "ANTHROPIC_NOT_CONFIGURED"
ANTHROPIC_CONNECTION: Final[str] = "ANTHROPIC_CONNECTION"
ANTHROPIC_SCHEMA_UNSUPPORTED: Final[str] = "ANTHROPIC_SCHEMA_UNSUPPORTED"

# Features que mandam `cache_control` (cache de 5 min) no prefixo estavel.
# Diferente do `prompt_cache_key` da OpenAI (gratis: so agrupa o cache
# automatico), na Anthropic a ESCRITA no cache custa mais que a entrada
# (`ai_usage.ANTHROPIC_CACHE_WRITE_5M_MULTIPLIER`) e so se paga se o mesmo
# prefixo for LIDO de novo dentro de 5 min (`ModelPrice.cached_input_cents`).
# A conta de equilibrio (dezenas de chamadas/dia por feature e versao de
# prompt) esta em docs/motores/FONTES_DA_VERDADE.md §75. O volume de producao
# de 17 a 30/10 (docs/ia/EVAL_ANTHROPIC_2026-10-30.md) foi de 1 chamada em 14
# dias para insights, cardio_agent e periodization, e 0 para
# workouts_analysis: o cache nunca seria lido e cada chamada pagaria a escrita
# no prefixo. Por isso, VAZIO. As features de `PROMPT_CACHE_KEY_FEATURES`
# continuam passando o `cache_prefix`: religar e por a feature aqui (precisa
# ser subconjunto de `PROMPT_CACHE_KEY_FEATURES`), com o volume medido.
ANTHROPIC_CACHE_CONTROL_FEATURES: Final[frozenset[str]] = frozenset()

EVENT_LLM_FALLBACK: Final[str] = "llm.fallback"
EVENT_LLM_CONFIG_INVALID: Final[str] = "llm.config_invalid"

# `stop_reason` que significam saida cortada (mesmo tratamento do
# `status: incomplete` da OpenAI).
INCOMPLETE_STOP_REASONS: Final[frozenset[str]] = frozenset({"max_tokens", "model_context_window_exceeded"})
STOP_REASON_REFUSAL: Final[str] = "refusal"

# Midia aceita no bloco `image` (base64).
SUPPORTED_IMAGE_TYPES: Final[frozenset[str]] = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})

# Texto do 400 de credito esgotado (a API tambem usa o 402 `billing_error`).
_BILLING_MESSAGE = re.compile(r"credit balance|usage limits?|billing", re.IGNORECASE)


def feature_env(prefix: str, feature: Optional[str]) -> str:
    return prefix + re.sub(r"[^A-Z0-9]", "_", (feature or "").upper())


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _invalid(name: str, value: str) -> None:
    log_event(EVENT_LLM_CONFIG_INVALID, env=name, value=value[:40])


def provider_for(feature: Optional[str]) -> str:
    """Provedor da feature. Sem feature, sem env ou valor invalido: OpenAI."""
    if not feature:
        return DEFAULT_PROVIDER
    name = feature_env(ENV_PROVIDER_PREFIX, feature)
    value = _env(name).lower()
    if not value:
        return DEFAULT_PROVIDER
    if value not in PROVIDERS:
        _invalid(name, value)
        return DEFAULT_PROVIDER
    return value


def anthropic_model_for(feature: Optional[str]) -> str:
    return _env(feature_env(ENV_MODEL_PREFIX, feature)) or DEFAULT_ANTHROPIC_MODEL.get(
        feature or "", ANTHROPIC_MODEL_SONNET
    )


def anthropic_effort_for(feature: Optional[str]) -> str:
    name = feature_env(ENV_EFFORT_PREFIX, feature)
    value = _env(name).lower()
    if value in EFFORTS:
        return value
    if value:
        _invalid(name, value)
    return DEFAULT_ANTHROPIC_EFFORT.get(feature or "", EFFORT_MEDIUM)


def fallback_provider_for(feature: Optional[str]) -> Optional[str]:
    """`openai` quando o fallback esta ligado (env da feature vence a global)."""
    name = feature_env(ENV_FALLBACK_PREFIX, feature)
    value = _env(name).lower() or _env(ENV_FALLBACK_GLOBAL).lower()
    if not value:
        return None
    if value != PROVIDER_OPENAI:
        _invalid(name, value)
        return None
    return value


# ---------------------------------------------------------------------------
# Erros
# ---------------------------------------------------------------------------

# Falhas que justificam refazer na OpenAI (quando o fallback esta ligado):
# credito/billing, chave (ausente, invalida, sem permissao), qualquer resposta
# de erro HTTP (429 e 5xx/529 ja depois do retry; 4xx de configuracao, como
# modelo inexistente ou schema recusado), erro de conexao antes da resposta,
# schema que nao cabe no subconjunto e recusa. NAO entram: timeout (a
# Anthropic pode ter gerado e cobrado, e o orcamento de tempo foi gasto) e
# resposta gerada mas inutil (cortada ou JSON invalido: ja foi cobrada; segue
# o caminho de falha de sempre, como na OpenAI).
_FALLBACK_CODES: Final[frozenset[str]] = frozenset(
    {
        ANTHROPIC_REFUSAL,
        ANTHROPIC_BILLING,
        ANTHROPIC_AUTH,
        ANTHROPIC_NOT_CONFIGURED,
        ANTHROPIC_CONNECTION,
        ANTHROPIC_SCHEMA_UNSUPPORTED,
    }
)


def fallback_eligible(exc: BaseException) -> bool:
    if not isinstance(exc, OpenAIGenerationError) or getattr(exc, "provider", None) != PROVIDER_ANTHROPIC:
        return False
    if exc.code in _FALLBACK_CODES:
        return True
    return exc.status is not None and exc.code != OPENAI_TIMEOUT


def _error(message: str, *, code: Optional[str] = None, status: Optional[int] = None,
           diagnostic: Optional[str] = None, request_id: Optional[str] = None) -> OpenAIGenerationError:
    exc = OpenAIGenerationError(message, status=status, code=code, diagnostic=diagnostic, request_id=request_id)
    exc.provider = PROVIDER_ANTHROPIC  # type: ignore[attr-defined]
    return exc


def _api_key() -> str:
    key = _env(ENV_API_KEY)
    if not key or key == "replace_me":
        raise _error(
            USER_MESSAGE_UNAVAILABLE, code=ANTHROPIC_NOT_CONFIGURED,
            diagnostic="ANTHROPIC_API_KEY nao configurada no backend.",
        )
    return key


def _from_sdk_error(exc: BaseException, timeout: float) -> OpenAIGenerationError:
    """Classes tipadas do SDK -> `OpenAIGenerationError` com mensagem generica."""
    import anthropic

    if isinstance(exc, anthropic.APITimeoutError):
        return _error(USER_MESSAGE_TIMEOUT, code=OPENAI_TIMEOUT, diagnostic=f"Timeout de {timeout:g}s ao chamar Anthropic.")
    if isinstance(exc, anthropic.APIConnectionError):
        return _error(
            USER_MESSAGE_UNAVAILABLE, code=ANTHROPIC_CONNECTION,
            diagnostic=f"Erro de rede ao chamar Anthropic: {type(exc).__name__}",
        )
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        kind = getattr(exc, "type", None)
        message = getattr(exc, "message", "") or ""
        if status == 402 or kind == "billing_error" or (status == 400 and _BILLING_MESSAGE.search(message)):
            code = ANTHROPIC_BILLING
            user_message = USER_MESSAGE_UNAVAILABLE
        elif isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            code = ANTHROPIC_AUTH
            user_message = USER_MESSAGE_UNAVAILABLE
        else:
            code = classify_http_status(status)
            user_message = user_message_for(code)
        return _error(
            user_message, status=status, code=code,
            diagnostic=f"Anthropic respondeu {status} ({kind or 'sem tipo'}): {message}",
            request_id=getattr(exc, "request_id", None),
        )
    return _error(USER_MESSAGE_UNAVAILABLE, diagnostic=f"Falha inesperada no SDK da Anthropic: {type(exc).__name__}")


# ---------------------------------------------------------------------------
# Pedido
# ---------------------------------------------------------------------------

_client_cache: dict[str, Any] = {}


def _client(api_key: str):
    """Cliente do SDK com `max_retries=0`: o retry e o deste modulo (mesma
    politica do caminho OpenAI; o do SDK repetiria tambem o timeout)."""
    client = _client_cache.get(api_key)
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, max_retries=0)
        _client_cache.clear()
        _client_cache[api_key] = client
    return client


def _text_blocks(prompt: str, *, feature: Optional[str], prompt_version: Optional[str],
                 cache_prefix: Optional[str]) -> list[dict]:
    """Prompt em um bloco, ou em dois com `cache_control` no prefixo estavel
    (instrucoes + RAG) nas features de `ANTHROPIC_CACHE_CONTROL_FEATURES`.
    Prefixo que nao bate com o comeco do prompt e ignorado."""
    cacheable = (
        feature in ANTHROPIC_CACHE_CONTROL_FEATURES
        and prompt_version
        and cache_prefix
        and len(cache_prefix) < len(prompt)
        and prompt.startswith(cache_prefix)
    )
    if not cacheable:
        return [{"type": "text", "text": prompt}]
    return [
        {"type": "text", "text": cache_prefix, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": prompt[len(cache_prefix):]},
    ]


def _image_block(image: ImageInput) -> dict:
    import base64

    if not image.data:
        raise ValueError("imagem vazia")
    if image.content_type not in SUPPORTED_IMAGE_TYPES:
        raise ValueError(f"tipo de imagem nao aceito pela Anthropic: {image.content_type!r}")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": image.content_type,
            "data": base64.standard_b64encode(image.data).decode("ascii"),
        },
    }


def build_request(
    prompt: str,
    *,
    feature: Optional[str],
    prompt_version: Optional[str] = None,
    schema: Optional[dict] = None,
    images: Sequence[ImageInput] = (),
    cache_prefix: Optional[str] = None,
) -> dict:
    """Argumentos de `messages.create`. Sem `temperature`/`top_p`/`top_k` nem
    prefill (400 nesses modelos); thinking adaptativo com esforco por feature."""
    content = [*(_image_block(i) for i in images)]
    content += _text_blocks(prompt, feature=feature, prompt_version=prompt_version, cache_prefix=cache_prefix)
    output_config: dict = {"effort": anthropic_effort_for(feature)}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    return {
        "model": anthropic_model_for(feature),
        "max_tokens": LLM_MAX_OUTPUT_TOKENS.get(feature or "") or DEFAULT_MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "output_config": output_config,
        "messages": [{"role": "user", "content": content}],
    }


# ---------------------------------------------------------------------------
# Resposta
# ---------------------------------------------------------------------------


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class _Usage:
    input_tokens: Optional[int]
    cached_tokens: Optional[int]
    cache_write_tokens: Optional[int]
    output_tokens: Optional[int]
    reasoning_tokens: Optional[int]


def usage_of(message: Any) -> _Usage:
    """`input_tokens` no MESMO sentido da OpenAI: a entrada inteira (nao
    cacheada + lida do cache + escrita no cache); `cached_tokens` = lida do
    cache. E o que `ai_usage.estimate_cost_usd` espera."""
    usage = _get(message, "usage")
    if usage is None:
        return _Usage(None, None, None, None, None)
    base = _as_int(_get(usage, "input_tokens"))
    read = _as_int(_get(usage, "cache_read_input_tokens"))
    write = _as_int(_get(usage, "cache_creation_input_tokens"))
    total = None if base is None else base + (read or 0) + (write or 0)
    details = _get(usage, "output_tokens_details")
    return _Usage(
        input_tokens=total,
        cached_tokens=read,
        cache_write_tokens=write,
        output_tokens=_as_int(_get(usage, "output_tokens")),
        reasoning_tokens=_as_int(_get(details, "thinking_tokens")) if details is not None else None,
    )


def output_text(message: Any) -> str:
    """Concatenacao dos blocos `type == "text"` (nunca `content[0]`: a
    resposta pode comecar por blocos de thinking)."""
    parts = []
    for block in _get(message, "content") or []:
        if _get(block, "type") == "text" and _get(block, "text"):
            parts.append(str(_get(block, "text")))
    return "".join(parts)


def _emit_usage(message: Any, *, feature, model, user_id, started, status, attempts, prompt_version) -> None:
    usage = usage_of(message)
    log_event(
        openai_client.EVENT_LLM_USAGE,
        provider=PROVIDER_ANTHROPIC,
        feature=feature,
        model=str(_get(message, "model") or model or "") or None,
        input_tokens=usage.input_tokens,
        cached_tokens=usage.cached_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        latency_ms=int(round((openai_client._monotonic() - started) * 1000)),
        user_id=user_id,
        status=status,
        attempts=attempts,
        prompt_version=prompt_version,
    )


def _create(kwargs: dict, timeout: float) -> Any:
    client = _client(_api_key())
    try:
        return client.messages.create(**kwargs, timeout=timeout)
    except OpenAIGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 - toda falha do SDK vira o erro do contrato
        raise _from_sdk_error(exc, timeout) from exc


def send(
    kwargs: dict,
    timeout: float,
    *,
    feature: Optional[str] = None,
    user_id=None,
    prompt_version: Optional[str] = None,
) -> Any:
    """`messages.create` com o retry do cliente (429/5xx/529, janela de 10 s,
    orcamento total <= `timeout`), `llm.usage`, recusa e saida cortada."""
    started = openai_client._monotonic()
    attempt = 0
    model = kwargs.get("model")
    while True:
        attempt += 1
        remaining = timeout if attempt == 1 else timeout - (openai_client._monotonic() - started)
        try:
            message = _create(kwargs, remaining)
            break
        except OpenAIGenerationError as exc:
            elapsed = openai_client._monotonic() - started
            backoff = TEXT_RETRY_BACKOFF_SECONDS[min(attempt, len(TEXT_RETRY_BACKOFF_SECONDS)) - 1]
            can_retry = (
                exc.retryable
                and attempt < TEXT_MAX_ATTEMPTS
                and elapsed < TEXT_RETRY_WINDOW_SECONDS
                and timeout - elapsed - backoff >= TEXT_RETRY_MIN_ATTEMPT_SECONDS
            )
            log_failure(
                exc, provider=PROVIDER_ANTHROPIC, feature=feature, prompt_version=prompt_version,
                user_id=user_id, model=model, attempt=attempt, will_retry=can_retry,
            )
            if not can_retry:
                _emit_usage(
                    None, feature=feature, model=model, user_id=user_id, started=started,
                    status="timeout" if exc.code == OPENAI_TIMEOUT else "error", attempts=attempt,
                    prompt_version=prompt_version,
                )
                raise
            openai_client._sleep(backoff)
    stop_reason = _get(message, "stop_reason")
    incomplete = stop_reason in INCOMPLETE_STOP_REASONS
    refused = stop_reason == STOP_REASON_REFUSAL
    _emit_usage(
        message, feature=feature, model=model, user_id=user_id, started=started,
        status="incomplete" if incomplete else ("refusal" if refused else "ok"), attempts=attempt,
        prompt_version=prompt_version,
    )
    if refused:
        details = _get(message, "stop_details")
        category = _get(details, "category") if details is not None else None
        exc = _error(
            USER_MESSAGE_INVALID_RESPONSE, code=ANTHROPIC_REFUSAL,
            diagnostic=f"Anthropic recusou a geracao (categoria: {category or 'nao informada'}).",
            request_id=_get(message, "id"),
        )
        log_failure(exc, provider=PROVIDER_ANTHROPIC, feature=feature, prompt_version=prompt_version,
                    user_id=user_id, model=model, attempt=attempt, will_retry=False)
        raise exc
    if incomplete:
        # So metadados: nunca o prompt nem o texto cortado.
        log_event(
            EVENT_LLM_OUTPUT_INCOMPLETE,
            provider=PROVIDER_ANTHROPIC,
            feature=feature,
            prompt_version=prompt_version,
            user_id=user_id,
            reason=stop_reason,
            max_output_tokens=kwargs.get("max_tokens"),
            output_tokens=usage_of(message).output_tokens,
        )
        raise _error(
            USER_MESSAGE_INVALID_RESPONSE,
            diagnostic=f"Anthropic devolveu resposta incompleta ({stop_reason}); a saida cortada foi descartada.",
        )
    return message


# ---------------------------------------------------------------------------
# Portas usadas por `openai_client.call_openai*`
# ---------------------------------------------------------------------------


def call_text(
    prompt: str, timeout: float, *, feature: Optional[str], user_id=None,
    prompt_version: Optional[str] = None, cache_prefix: Optional[str] = None,
) -> str:
    return call_text_result(
        prompt, timeout, feature=feature, user_id=user_id, prompt_version=prompt_version, cache_prefix=cache_prefix
    ).text


def call_text_result(
    prompt: str, timeout: float, *, feature: Optional[str], user_id=None,
    prompt_version: Optional[str] = None, cache_prefix: Optional[str] = None,
) -> TextResult:
    """Texto + modelo que respondeu (eco do servidor) + modelo pedido."""
    kwargs = build_request(prompt, feature=feature, prompt_version=prompt_version, cache_prefix=cache_prefix)
    message = send(kwargs, timeout, feature=feature, user_id=user_id, prompt_version=prompt_version)
    return TextResult(
        text=output_text(message), model=str(_get(message, "model") or kwargs["model"]),
        requested_model=kwargs["model"],
    )


def call_structured(
    prompt: str, schema: dict, timeout: float, *, feature: Optional[str], user_id=None,
    prompt_version: Optional[str] = None, images: Sequence[ImageInput] = (),
    cache_prefix: Optional[str] = None, optional_first: Sequence[tuple[str, ...]] = (),
) -> StructuredResult:
    """Saida estruturada (`output_config.format` json_schema) com o schema
    adaptado (`llm_anthropic_schema`); a volta tem a forma do schema original.
    `optional_first`: ver `llm_anthropic_schema.adapt_schema`."""
    try:
        adapted = llm_anthropic_schema.adapt_schema(schema, optional_first)
    except llm_anthropic_schema.SchemaAdaptationError as exc:
        error = _error(USER_MESSAGE_UNAVAILABLE, code=ANTHROPIC_SCHEMA_UNSUPPORTED, diagnostic=str(exc))
        log_failure(error, provider=PROVIDER_ANTHROPIC, feature=feature, prompt_version=prompt_version, user_id=user_id)
        raise error from exc
    kwargs = build_request(
        prompt, feature=feature, prompt_version=prompt_version, schema=adapted.schema,
        images=images, cache_prefix=cache_prefix,
    )
    message = send(kwargs, timeout, feature=feature, user_id=user_id, prompt_version=prompt_version)
    model = str(_get(message, "model") or kwargs["model"])
    try:
        parsed = _structured_payload(output_text(message))
    except OpenAIGenerationError as exc:
        log_failure(exc, provider=PROVIDER_ANTHROPIC, feature=feature, prompt_version=prompt_version,
                    user_id=user_id, model=model)
        raise
    usage = usage_of(message)
    return StructuredResult(
        data=llm_anthropic_schema.restore(parsed, schema, adapted.optional_paths),
        model=model,
        tokens_in=usage.input_tokens,
        tokens_out=usage.output_tokens,
    )


def _structured_payload(text: str) -> dict:
    import json

    if not text.strip():
        raise _error(USER_MESSAGE_INVALID_RESPONSE, diagnostic="Anthropic devolveu resposta vazia.")
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise _error(USER_MESSAGE_INVALID_RESPONSE, diagnostic=f"Anthropic devolveu JSON invalido: {exc}") from exc
    if not isinstance(parsed, dict):
        raise _error(USER_MESSAGE_INVALID_RESPONSE, diagnostic="Anthropic devolveu um JSON que nao e objeto.")
    return parsed


__all__ = [
    "ANTHROPIC_AUTH",
    "ANTHROPIC_BILLING",
    "ANTHROPIC_CONNECTION",
    "ANTHROPIC_MODEL_HAIKU",
    "ANTHROPIC_MODEL_SONNET",
    "ANTHROPIC_NOT_CONFIGURED",
    "ANTHROPIC_REFUSAL",
    "ANTHROPIC_SCHEMA_UNSUPPORTED",
    "ANTHROPIC_CACHE_CONTROL_FEATURES",
    "DEFAULT_ANTHROPIC_EFFORT",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_PROVIDER",
    "EFFORTS",
    "EVENT_LLM_FALLBACK",
    "LIGHT_FEATURES",
    "LLM_FEATURES",
    "PROVIDERS",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_OPENAI",
    "anthropic_effort_for",
    "call_text_result",
    "anthropic_model_for",
    "build_request",
    "call_structured",
    "call_text",
    "fallback_eligible",
    "fallback_provider_for",
    "feature_env",
    "output_text",
    "provider_for",
    "send",
    "usage_of",
]

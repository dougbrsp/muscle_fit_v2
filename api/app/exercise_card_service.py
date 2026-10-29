"""Cards ilustrados de exercicios - maquina de estados, SQL, limites, sweep e
serializacao (tabela `exercise_card_generations`, migracao 064).

Separacao igual a da descricao de execucao: o PROMPT mora em
`exercise_card_prompt.py`, a COMPOSICAO do card em `exercise_card_composer.py`
e a codificacao da publicacao em `exercise_card_processor.py` (Pillow puro),
o S3 em `exercise_card_storage.py`, o WORKER em `exercise_card_jobs.py` e as
ROTAS em `exercise_cards.py`. Este modulo e a unica fonte das transicoes de
estado.

Maquina de estados
------------------

    PENDING --(worker reserva)--> GENERATING --(OpenAI + composicao + rascunho no S3)--> GENERATED
    GENERATED --(aprovar: lock)--> APPROVING --(codifica + 3 uploads + transacao)--> PUBLISHED
    PUBLISHED --(outra versao publicada)--> SUPERSEDED
    GENERATED --(rejeitar | regenerar)--> REJECTED
    PENDING/GENERATING --(erro | sweep WORKER_LOST)--> FAILED
    APPROVING --(erro de processamento/upload | sweep PUBLISH_INTERRUPTED)--> GENERATED

- **Viva** = PENDING, GENERATING ou APPROVING: no maximo UMA por exercicio.
  A checagem e feita na transacao, com a linha do exercicio em
  `exercise_catalog` travada (`SELECT ... FOR UPDATE`) - a mesma trava
  serializa as aprovacoes do exercicio, o que torna seguro calcular
  `version = max + 1` antes dos uploads. Sem coluna gerada (decisao da 064).
- **Em andamento (global)** = PENDING ou GENERATING (as que ocupam uma Lambda):
  teto `EXERCISE_CARD_MAX_IN_FLIGHT` por causa do limite de 10 Lambdas
  concorrentes da conta.
- **Limite diario** = linhas criadas pelo admin no dia UTC corrente, qualquer
  status (tentativa conta, como na periodizacao).
- `version` so nasce na publicacao; `exercise_catalog.card_version` (o
  ponteiro que o app usa para montar a URL) so e gravado DEPOIS dos 3
  uploads. Falha no meio deixa objetos orfaos num path que ninguem aponta.
- **O que o admin revisa e o que e publicado** (Estrategia B): o preview de
  uma geracao e o card ja COMPOSTO pelo worker (ilustracao + titulo +
  rotulos + musculos + logo); a aprovacao so codifica (WebP/PNG/thumb), sem
  recompor. Geracoes do prompt V1 (a IA desenhava o card inteiro; o logo
  entrava na aprovacao) nao sao mais aprovaveis: 409 `not_reviewable` -
  rejeite ou regenere.

Relogio: tudo em UTC sem fuso (`utcnow()`), gravado EXPLICITAMENTE pelo
codigo - nunca `CURRENT_TIMESTAMP`/`NOW()` do banco. Assim o sweep compara
carimbos do mesmo relogio, seja quem gravou a api-core (Lambda, UTC), o
worker ou o script de calibracao rodando num host com outro fuso. Na
serializacao o carimbo sai com `Z`.

SQL cru com `%s`, um statement por `execute`, compativel com os dois
backends de `app/db.py` (PyMySQL e RDS Data API). `rowcount` dos UPDATE
condicionais e confiavel nos dois: o UPDATE sempre muda `status`, entao
"linhas encontradas" (FOUND_ROWS / Data API) = "linhas alteradas".
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Optional, Sequence

from . import exercise_card_prompt as card_prompt
from . import exercise_card_storage as storage
from . import openai_client, public_assets
from .db import is_duplicate_key_error, transaction
from .observability import (
    EVENT_EXERCISE_CARD_PUBLISH_FAILED,
    EVENT_EXERCISE_CARD_PUBLISHED,
    EVENT_EXERCISE_CARD_REGENERATED,
    EVENT_EXERCISE_CARD_REJECTED,
    EVENT_EXERCISE_CARD_REQUESTED,
    EVENT_EXERCISE_CARD_SWEPT,
    log_event,
)

logger = logging.getLogger("musclefit.exercise_cards")

TABLE = "exercise_card_generations"

# ---------------------------------------------------------------------------
# Estados
# ---------------------------------------------------------------------------

STATUS_PENDING = "PENDING"
STATUS_GENERATING = "GENERATING"
STATUS_GENERATED = "GENERATED"
STATUS_APPROVING = "APPROVING"
STATUS_PUBLISHED = "PUBLISHED"
STATUS_SUPERSEDED = "SUPERSEDED"
STATUS_REJECTED = "REJECTED"
STATUS_FAILED = "FAILED"

LIVE_STATUSES = (STATUS_PENDING, STATUS_GENERATING, STATUS_APPROVING)
IN_FLIGHT_STATUSES = (STATUS_PENDING, STATUS_GENERATING)
REGENERABLE_STATUSES = (STATUS_GENERATED, STATUS_REJECTED, STATUS_FAILED, STATUS_PUBLISHED, STATUS_SUPERSEDED)
PREVIEW_STATUSES = (STATUS_GENERATED, STATUS_APPROVING)
CARD_STATUSES = (STATUS_PUBLISHED, STATUS_SUPERSEDED)

# Prompts cujo preview NAO e o card composto (V1: a IA desenhava o card
# inteiro e o logo entrava na aprovacao). Aprovar um deles publicaria um card
# sem logo -> 409 `not_reviewable`. So existiam 4 no DEV (checkpoint rodada 1).
LEGACY_UNCOMPOSED_PROMPT_VERSIONS = ("MUSCLEFIT_EXERCISE_CARD_V1",)

# `reject_reason` gravado no pai GENERATED quando o admin regenera direto do
# preview. E um CODIGO interno, nao um motivo escrito por humano: nunca vai
# para o prompt da filha (`human_reject_reason`).
REJECT_REASON_REGENERATED = "regenerated"

# ---------------------------------------------------------------------------
# `error_code` gravado na linha (os `OPENAI_*` vem de `openai_client`, os de
# S3 de `exercise_card_storage` e o de imagem de `exercise_card_processor`)
# ---------------------------------------------------------------------------

# Worker: a composicao do card (`exercise_card_composer`) falhou depois da
# OpenAI - a ilustracao paga fica em `exercise-card-drafts/{gid}/illustration.png`.
# Mesmo valor de `exercise_card_composer.CARD_COMPOSITION_FAILED` (teste),
# repetido aqui para nao importar Pillow na api-core.
ERROR_CARD_COMPOSITION_FAILED = "CARD_COMPOSITION_FAILED"
ERROR_WORKER_LOST = "WORKER_LOST"
ERROR_PUBLISH_INTERRUPTED = "PUBLISH_INTERRUPTED"
ERROR_DISPATCH_FAILED = "DISPATCH_FAILED"
ERROR_EXERCISE_NOT_FOUND = "EXERCISE_NOT_FOUND"
ERROR_PROMPT_INVALID = "PROMPT_INVALID"
ERROR_INTERNAL = "INTERNAL_ERROR"

# ---------------------------------------------------------------------------
# Codigos HTTP (`detail.error` da resposta)
# ---------------------------------------------------------------------------

CODE_DISABLED = "exercise_card_generation_disabled"  # 503
CODE_EXERCISE_NOT_FOUND = "exercise_not_found"  # 404
CODE_GENERATION_NOT_FOUND = "exercise_card_generation_not_found"  # 404
CODE_IN_PROGRESS = "exercise_card_generation_in_progress"  # 409
CODE_NOT_REVIEWABLE = "not_reviewable"  # 409
CODE_NOT_REGENERABLE = "not_regenerable"  # 409
CODE_IDEMPOTENCY_CONFLICT = "idempotency_key_conflict"  # 409
CODE_DAILY_LIMIT = "exercise_card_daily_limit"  # 429
CODE_GLOBAL_IN_FLIGHT_LIMIT = "exercise_card_global_in_flight_limit"  # 429
CODE_INVALID_IDEMPOTENCY_KEY = "invalid_idempotency_key"  # 422
CODE_INVALID_TEXT_LEVEL = "invalid_text_level"  # 422
CODE_INSTRUCTION_TOO_LONG = "instruction_too_long"  # 422
CODE_REASON_TOO_LONG = "reason_too_long"  # 422
CODE_IMAGE_PROCESSING_FAILED = "image_processing_failed"  # 502
CODE_S3_UPLOAD_FAILED = "s3_upload_failed"  # 502
CODE_S3_READ_FAILED = "s3_read_failed"  # 502
# M72: o rascunho passou de `storage.DRAFT_TTL_DAYS` (lifecycle do S3). Mesmo
# valor do `error_code` gravado na linha (`storage.DRAFT_EXPIRED`).
CODE_DRAFT_EXPIRED = storage.DRAFT_EXPIRED  # 410
MESSAGE_DRAFT_EXPIRED = (
    f"O rascunho expirou (mais de {storage.DRAFT_TTL_DAYS} dias). Gere o card de novo."
)
CODE_DISPATCH_FAILED = "exercise_card_dispatch_failed"  # 502

# ---------------------------------------------------------------------------
# Configuracao (envs do PR1; lidas a cada chamada)
# ---------------------------------------------------------------------------

ENV_ENABLED = "EXERCISE_CARD_GENERATION_ENABLED"
ENV_MAX_PER_DAY = "EXERCISE_CARD_MAX_GENERATIONS_PER_DAY"
ENV_MAX_IN_FLIGHT = "EXERCISE_CARD_MAX_IN_FLIGHT"
ENV_STALE_MINUTES = "EXERCISE_CARD_STALE_MINUTES"

DEFAULT_MAX_PER_DAY = 100
DEFAULT_MAX_IN_FLIGHT = 2
DEFAULT_STALE_MINUTES = 10
# Uma aprovacao e um request so (codificacao + 3 uploads, poucos segundos, dentro
# dos 29 s do API Gateway). APPROVING com mais que isto = request morreu.
APPROVING_STALE_MINUTES = 2

DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 50
MAX_ERROR_MESSAGE_CHARS = 500
# Mesmos tetos do prompt: o que e gravado e exatamente o que o modelo le.
MAX_INSTRUCTION_CHARS = card_prompt.MAX_INSTRUCTION_CHARS
MAX_REASON_CHARS = card_prompt.MAX_REJECT_REASON_CHARS


def generation_enabled() -> bool:
    """Flag da feature (`/auth/me` -> `features.exercise_cards`). Ausente ou
    qualquer valor fora de 1/true/yes/on = desligada (producao ate o fim do
    checkpoint)."""
    return (os.environ.get(ENV_ENABLED) or "").strip().lower() in ("1", "true", "yes", "on")


def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int((os.environ.get(name) or "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


def max_generations_per_day() -> int:
    return _env_positive_int(ENV_MAX_PER_DAY, DEFAULT_MAX_PER_DAY)


def max_in_flight() -> int:
    return _env_positive_int(ENV_MAX_IN_FLIGHT, DEFAULT_MAX_IN_FLIGHT)


def stale_minutes() -> int:
    return _env_positive_int(ENV_STALE_MINUTES, DEFAULT_STALE_MINUTES)


def utcnow() -> datetime:
    """Agora em UTC, sem fuso e sem microssegundos (DATETIME do MySQL). Os
    testes congelam o relogio trocando ESTA funcao."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


# ---------------------------------------------------------------------------
# Erro tipado
# ---------------------------------------------------------------------------


class ExerciseCardError(Exception):
    """Erro de negocio com o status HTTP e o codigo estavel da resposta
    (`detail = {"error": code, "message": ..., "generation_id"?}`)."""

    def __init__(self, code: str, http_status: int, message: str, *, generation_id: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.http_status = int(http_status)
        self.message = message
        self.generation_id = generation_id

    def detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.generation_id is not None:
            detail["generation_id"] = int(self.generation_id)
        return detail


def require_enabled() -> None:
    if not generation_enabled():
        raise ExerciseCardError(CODE_DISABLED, 503, "Geração de cards ilustrados desativada neste ambiente.")


# ---------------------------------------------------------------------------
# Validacao da entrada
# ---------------------------------------------------------------------------


def normalize_idempotency_key(value: Any) -> str:
    """UUID canonico (36 chars, minusculo) - cabe no CHAR(36) da coluna."""
    if not isinstance(value, str) or not value.strip():
        raise ExerciseCardError(CODE_INVALID_IDEMPOTENCY_KEY, 422, "idempotency_key (UUID) é obrigatório.")
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        raise ExerciseCardError(CODE_INVALID_IDEMPOTENCY_KEY, 422, "idempotency_key precisa ser um UUID.") from None


def normalize_text_level(value: Any, default: Optional[str] = None) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default or card_prompt.DEFAULT_TEXT_LEVEL
    if not isinstance(value, str):
        raise ExerciseCardError(CODE_INVALID_TEXT_LEVEL, 422, "text_level inválido.")
    try:
        return card_prompt.normalize_text_level(value)
    except card_prompt.ExerciseCardPromptError:
        raise ExerciseCardError(
            CODE_INVALID_TEXT_LEVEL, 422, f"text_level inválido (use {', '.join(card_prompt.TEXT_LEVELS)})."
        ) from None


def _optional_text(value: Any, *, limit: int, code: str, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExerciseCardError(code, 422, f"{field} precisa ser texto.")
    text = value.strip()
    if not text:
        return None
    if len(text) > limit:
        raise ExerciseCardError(code, 422, f"{field} com {len(text)} caracteres; o máximo é {limit}.")
    return text


def normalize_instruction(value: Any) -> Optional[str]:
    return _optional_text(value, limit=MAX_INSTRUCTION_CHARS, code=CODE_INSTRUCTION_TOO_LONG, field="instruction")


def normalize_reason(value: Any) -> Optional[str]:
    return _optional_text(value, limit=MAX_REASON_CHARS, code=CODE_REASON_TOO_LONG, field="reason")


def _clip(message: Any) -> Optional[str]:
    text = str(message or "").strip()
    return text[:MAX_ERROR_MESSAGE_CHARS] if text else None


# ---------------------------------------------------------------------------
# SQL (cursor do chamador)
# ---------------------------------------------------------------------------

COLUMNS: tuple[str, ...] = (
    "id",
    "exercise_id",
    "card_type",
    "status",
    "version",
    "parent_generation_id",
    "idempotency_key",
    "job_id",
    "provider",
    "model",
    "quality",
    "size",
    "prompt_version",
    "text_level",
    "prompt_text",
    "request_instruction",
    "reject_reason",
    "preview_s3_key",
    "card_s3_key",
    "card_png_s3_key",
    "thumb_s3_key",
    "width",
    "height",
    "bytes",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "error_code",
    "error_message",
    "created_by",
    "approved_by",
    "created_at",
    "updated_at",
    "started_at",
    "generated_at",
    "approved_at",
    "published_at",
    "rejected_at",
    "failed_at",
)
_SELECT_ALL = ", ".join(COLUMNS)
# Listagem: sem `prompt_text` (alguns KB por linha, so interessa no detalhe).
_SELECT_LIST = ", ".join(column for column in COLUMNS if column != "prompt_text")


def _in(values: Sequence[str]) -> str:
    return ", ".join(["%s"] * len(values))


def lock_exercise(cursor, exercise_id: int) -> bool:
    """Trava a linha do exercicio no catalogo ate o fim da transacao. Serializa
    criacao/regeneracao/publicacao do MESMO exercicio (a ordem de lock e
    sempre catalogo -> geracoes). `False` = exercicio nao existe."""
    cursor.execute("SELECT id FROM exercise_catalog WHERE id = %s FOR UPDATE", (int(exercise_id),))
    return cursor.fetchone() is not None


def exercise_exists(cursor, exercise_id: int) -> bool:
    cursor.execute("SELECT id FROM exercise_catalog WHERE id = %s", (int(exercise_id),))
    return cursor.fetchone() is not None


def fetch_generation(
    cursor, generation_id: int, *, exercise_id: Optional[int] = None, include_prompt: bool = True
) -> Optional[dict[str, Any]]:
    """A linha, ou None. Com `exercise_id`, so se a geracao for DAQUELE
    exercicio (anti-IDOR: gid de outro exercicio = inexistente)."""
    columns = _SELECT_ALL if include_prompt else _SELECT_LIST
    if exercise_id is None:
        cursor.execute(f"SELECT {columns} FROM {TABLE} WHERE id = %s", (int(generation_id),))
    else:
        cursor.execute(
            f"SELECT {columns} FROM {TABLE} WHERE id = %s AND exercise_id = %s",
            (int(generation_id), int(exercise_id)),
        )
    row = cursor.fetchone()
    return dict(row) if row else None


def find_by_idempotency_key(cursor, exercise_id: int, idempotency_key: str) -> Optional[dict[str, Any]]:
    cursor.execute(
        f"SELECT {_SELECT_ALL} FROM {TABLE} WHERE exercise_id = %s AND idempotency_key = %s",
        (int(exercise_id), idempotency_key),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def live_generation(cursor, exercise_id: int) -> Optional[dict[str, Any]]:
    """A geracao viva (PENDING/GENERATING/APPROVING) do exercicio, se houver."""
    cursor.execute(
        f"SELECT id, status FROM {TABLE} WHERE exercise_id = %s AND status IN ({_in(LIVE_STATUSES)}) "
        "ORDER BY id LIMIT 1",
        (int(exercise_id), *LIVE_STATUSES),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def count_in_flight(cursor) -> int:
    cursor.execute(
        f"SELECT COUNT(*) AS n FROM {TABLE} WHERE status IN ({_in(IN_FLIGHT_STATUSES)})", IN_FLIGHT_STATUSES
    )
    return int((cursor.fetchone() or {}).get("n") or 0)


def day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def count_created_today(cursor, user_id: int, now: datetime) -> int:
    """Geracoes criadas pelo admin desde 00:00 UTC, qualquer status (indice
    `idx_ecg_created_by_created`)."""
    cursor.execute(
        f"SELECT COUNT(*) AS n FROM {TABLE} WHERE created_by = %s AND created_at >= %s",
        (int(user_id), day_start(now)),
    )
    return int((cursor.fetchone() or {}).get("n") or 0)


def next_version(cursor, exercise_id: int) -> int:
    cursor.execute(f"SELECT MAX(version) AS v FROM {TABLE} WHERE exercise_id = %s", (int(exercise_id),))
    current = (cursor.fetchone() or {}).get("v")
    return int(current or 0) + 1


def sweep(cursor, *, now: datetime, exercise_id: Optional[int] = None) -> dict[str, int]:
    """Fecha o que morreu no meio (lazy, chamado no POST e no GET):

    - PENDING/GENERATING sem progresso ha mais de `EXERCISE_CARD_STALE_MINUTES`
      (Lambda morta, invoke perdido) -> FAILED `WORKER_LOST`. O worker atrasado
      que chegar depois perde a reserva (`rowcount=0`) e NAO chama a OpenAI;
    - APPROVING ha mais de 2 min (request da aprovacao morreu) -> GENERATED
      `PUBLISH_INTERRUPTED`: o rascunho pago continua, o admin aprova de novo.

    Sem `exercise_id` varre todos os exercicios (POST: o teto global so e
    justo sem orfas de outros exercicios ocupando vaga)."""
    scope_sql = ""
    scope_params: tuple[Any, ...] = ()
    if exercise_id is not None:
        scope_sql = " AND exercise_id = %s"
        scope_params = (int(exercise_id),)

    cursor.execute(
        f"""
        UPDATE {TABLE}
        SET status = %s, error_code = %s, error_message = %s, failed_at = %s, updated_at = %s
        WHERE status IN ({_in(IN_FLIGHT_STATUSES)}) AND COALESCE(started_at, created_at) < %s{scope_sql}
        """,
        (
            STATUS_FAILED,
            ERROR_WORKER_LOST,
            "Geração sem resposta do worker dentro do prazo.",
            now,
            now,
            *IN_FLIGHT_STATUSES,
            now - timedelta(minutes=stale_minutes()),
            *scope_params,
        ),
    )
    worker_lost = max(0, int(cursor.rowcount or 0))

    cursor.execute(
        f"""
        UPDATE {TABLE}
        SET status = %s, error_code = %s, error_message = %s, updated_at = %s
        WHERE status = %s AND updated_at < %s{scope_sql}
        """,
        (
            STATUS_GENERATED,
            ERROR_PUBLISH_INTERRUPTED,
            "Publicação interrompida; o rascunho continua disponível para aprovar de novo.",
            now,
            STATUS_APPROVING,
            now - timedelta(minutes=APPROVING_STALE_MINUTES),
            *scope_params,
        ),
    )
    publish_interrupted = max(0, int(cursor.rowcount or 0))
    return {"worker_lost": worker_lost, "publish_interrupted": publish_interrupted}


def insert_generation(
    cursor,
    *,
    exercise_id: int,
    user_id: int,
    idempotency_key: str,
    text_level: str,
    instruction: Optional[str],
    parent_generation_id: Optional[int],
    now: datetime,
    settings: Optional[openai_client.ImageSettings] = None,
) -> int:
    chosen = settings or openai_client.image_settings()
    cursor.execute(
        f"""
        INSERT INTO {TABLE} (
            exercise_id, card_type, status, parent_generation_id, idempotency_key, provider,
            model, quality, size, prompt_version, text_level, request_instruction,
            created_by, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            int(exercise_id),
            card_prompt.CARD_TYPE,
            STATUS_PENDING,
            None if parent_generation_id is None else int(parent_generation_id),
            idempotency_key,
            "openai",
            chosen.model,
            chosen.quality,
            chosen.size,
            card_prompt.PROMPT_VERSION,
            text_level,
            instruction,
            int(user_id),
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def set_job_id(cursor, generation_id: int, job_id: str) -> None:
    cursor.execute(f"UPDATE {TABLE} SET job_id = %s WHERE id = %s", (str(job_id), int(generation_id)))


def reserve_for_generation(cursor, generation_id: int, now: datetime) -> bool:
    """PENDING -> GENERATING atomico. `False` = redelivery do invoke (ou linha
    ja varrida): o worker sai SEM chamar a OpenAI."""
    cursor.execute(
        f"UPDATE {TABLE} SET status = %s, started_at = %s, updated_at = %s WHERE id = %s AND status = %s",
        (STATUS_GENERATING, now, now, int(generation_id), STATUS_PENDING),
    )
    return int(cursor.rowcount or 0) > 0


def mark_generated(
    cursor,
    generation_id: int,
    *,
    now: datetime,
    prompt_text: str,
    preview_s3_key: str,
    model: str,
    quality: str,
    size: str,
    width: Optional[int],
    height: Optional[int],
    byte_count: int,
    tokens_in: Optional[int],
    tokens_out: Optional[int],
    cost_usd: Optional[Decimal],
) -> bool:
    """GENERATING -> GENERATED. `False` = a linha nao estava mais GENERATING
    (varrida por WORKER_LOST no meio): o resultado e descartado."""
    cursor.execute(
        f"""
        UPDATE {TABLE}
        SET status = %s, prompt_text = %s, prompt_version = %s, preview_s3_key = %s,
            model = %s, quality = %s, size = %s, width = %s, height = %s, bytes = %s,
            tokens_in = %s, tokens_out = %s, cost_usd = %s,
            error_code = NULL, error_message = NULL, generated_at = %s, updated_at = %s
        WHERE id = %s AND status = %s
        """,
        (
            STATUS_GENERATED,
            prompt_text,
            card_prompt.PROMPT_VERSION,
            preview_s3_key,
            model,
            quality,
            size,
            width,
            height,
            int(byte_count),
            tokens_in,
            tokens_out,
            cost_usd,
            now,
            now,
            int(generation_id),
            STATUS_GENERATING,
        ),
    )
    return int(cursor.rowcount or 0) > 0


def mark_failed(
    cursor,
    generation_id: int,
    *,
    now: datetime,
    code: str,
    message: Any,
    prompt_text: Optional[str] = None,
    from_statuses: Sequence[str] = IN_FLIGHT_STATUSES,
) -> bool:
    """-> FAILED, so a partir de `from_statuses` (nunca sobrescreve uma linha
    que outro caminho ja levou adiante). `prompt_text` fica gravado quando ja
    existia (diagnostico de moderacao/recusa)."""
    cursor.execute(
        f"""
        UPDATE {TABLE}
        SET status = %s, error_code = %s, error_message = %s,
            prompt_text = COALESCE(%s, prompt_text), failed_at = %s, updated_at = %s
        WHERE id = %s AND status IN ({_in(from_statuses)})
        """,
        (STATUS_FAILED, str(code)[:40], _clip(message), prompt_text, now, now, int(generation_id), *from_statuses),
    )
    return int(cursor.rowcount or 0) > 0


def human_reject_reason(cursor, parent_generation_id: Optional[int]) -> Optional[str]:
    """Motivo de rejeicao do pai que pode ir para o prompt da filha: SO texto
    escrito por humano. O codigo interno `regenerated` (pai GENERATED que
    virou REJECTED porque o admin regenerou direto do preview) NUNCA vai -
    o pedido humano dessa rodada esta em `request_instruction` da filha."""
    if not parent_generation_id:
        return None
    cursor.execute(f"SELECT reject_reason FROM {TABLE} WHERE id = %s", (int(parent_generation_id),))
    row = cursor.fetchone() or {}
    return filter_human_reason(row.get("reject_reason"))


def filter_human_reason(reason: Any) -> Optional[str]:
    text = str(reason or "").strip()
    if not text or text.lower() == REJECT_REASON_REGENERATED:
        return None
    return text


# ---------------------------------------------------------------------------
# Operacoes (cada uma abre as proprias transacoes)
# ---------------------------------------------------------------------------


def _sweep_tx(*, now: datetime, exercise_id: Optional[int] = None) -> None:
    """Sweep na PROPRIA transacao, commitada antes da operacao: um 409/429 da
    operacao seguinte nao desfaz a limpeza."""
    with transaction() as conn:
        with conn.cursor() as cursor:
            counts = sweep(cursor, now=now, exercise_id=exercise_id)
    for rule, count in (("WORKER_LOST", counts["worker_lost"]), ("PUBLISH_INTERRUPTED", counts["publish_interrupted"])):
        if count:
            log_event(EVENT_EXERCISE_CARD_SWEPT, rule=rule, count=count, exercise_id=exercise_id)


def load_generation(generation_id: int, *, include_prompt: bool = True) -> Optional[dict[str, Any]]:
    with transaction() as conn:
        with conn.cursor() as cursor:
            return fetch_generation(cursor, generation_id, include_prompt=include_prompt)


def _check_limits(cursor, *, exercise_id: int, user_id: int, now: datetime) -> None:
    live = live_generation(cursor, exercise_id)
    if live:
        raise ExerciseCardError(
            CODE_IN_PROGRESS,
            409,
            "Já existe uma geração em andamento para este exercício.",
            generation_id=int(live["id"]),
        )
    ceiling = max_in_flight()
    if count_in_flight(cursor) >= ceiling:
        raise ExerciseCardError(
            CODE_GLOBAL_IN_FLIGHT_LIMIT,
            429,
            f"Já há {ceiling} geração(ões) de card em andamento. Aguarde alguma terminar.",
        )
    daily = max_generations_per_day()
    if count_created_today(cursor, user_id, now) >= daily:
        raise ExerciseCardError(CODE_DAILY_LIMIT, 429, f"Limite de {daily} gerações de card por dia atingido.")


def _replayed(existing: dict[str, Any], *, parent_generation_id: Optional[int]) -> dict[str, Any]:
    """Replay da MESMA chave: devolve a existente - desde que seja do mesmo
    tipo de pedido (criar x regenerar o mesmo pai). Chave reaproveitada para
    outro pedido e erro do cliente."""
    existing_parent = existing.get("parent_generation_id")
    same = (existing_parent is None and parent_generation_id is None) or (
        existing_parent is not None and parent_generation_id is not None and int(existing_parent) == int(parent_generation_id)
    )
    if not same:
        raise ExerciseCardError(
            CODE_IDEMPOTENCY_CONFLICT,
            409,
            "idempotency_key já usada em outro pedido deste exercício.",
            generation_id=int(existing["id"]),
        )
    return existing


def _replay_after_duplicate(exercise_id: int, idempotency_key: str, parent_generation_id: Optional[int]) -> dict[str, Any]:
    """Dois POSTs com a mesma chave ao mesmo tempo: o 2o INSERT bate no
    `UNIQUE (exercise_id, idempotency_key)`; a transacao dele ja foi desfeita
    e a existente e lida numa transacao nova."""
    with transaction() as conn:
        with conn.cursor() as cursor:
            existing = find_by_idempotency_key(cursor, exercise_id, idempotency_key)
    if not existing:
        raise ExerciseCardError(CODE_IDEMPOTENCY_CONFLICT, 409, "Conflito de idempotency_key; tente de novo.")
    return _replayed(existing, parent_generation_id=parent_generation_id)


def create_generation(
    *,
    exercise_id: int,
    user_id: int,
    idempotency_key: Any,
    instruction: Any = None,
    text_level: Any = None,
) -> tuple[dict[str, Any], bool]:
    """Cria a geracao PENDING (o disparo do job e de `exercise_card_jobs`).
    Devolve `(linha, criada)`; `criada=False` = replay da mesma chave."""
    require_enabled()
    key = normalize_idempotency_key(idempotency_key)
    level = normalize_text_level(text_level)
    wanted = normalize_instruction(instruction)
    now = utcnow()
    _sweep_tx(now=now)

    try:
        with transaction() as conn:
            with conn.cursor() as cursor:
                if not lock_exercise(cursor, exercise_id):
                    raise ExerciseCardError(CODE_EXERCISE_NOT_FOUND, 404, "Exercício não encontrado.")
                existing = find_by_idempotency_key(cursor, exercise_id, key)
                if existing:
                    return _replayed(existing, parent_generation_id=None), False
                _check_limits(cursor, exercise_id=exercise_id, user_id=user_id, now=now)
                generation_id = insert_generation(
                    cursor,
                    exercise_id=exercise_id,
                    user_id=user_id,
                    idempotency_key=key,
                    text_level=level,
                    instruction=wanted,
                    parent_generation_id=None,
                    now=now,
                )
                row = fetch_generation(cursor, generation_id)
    except ExerciseCardError:
        raise
    except Exception as exc:
        if is_duplicate_key_error(exc):
            return _replay_after_duplicate(exercise_id, key, None), False
        raise

    log_event(
        EVENT_EXERCISE_CARD_REQUESTED,
        generation_id=row["id"],
        exercise_id=int(exercise_id),
        user_id=int(user_id),
        text_level=level,
        has_instruction=bool(wanted),
        model=row.get("model"),
        quality=row.get("quality"),
        size=row.get("size"),
    )
    return row, True


def regenerate_generation(
    *,
    exercise_id: int,
    parent_generation_id: int,
    user_id: int,
    idempotency_key: Any,
    instruction: Any = None,
    text_level: Any = None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Filha PENDING de `parent_generation_id`. Pai GENERATED vira REJECTED
    (`regenerated`) NA MESMA transacao em que a filha nasce. Mesmos limites,
    flag e idempotencia do criar. `text_level` omitido = o do pai (se ainda
    for aceito; senao o padrao).
    Devolve `(filha, pai, criada)`."""
    require_enabled()
    key = normalize_idempotency_key(idempotency_key)
    wanted = normalize_instruction(instruction)
    requested_level = None if text_level is None else normalize_text_level(text_level)
    now = utcnow()
    _sweep_tx(now=now)

    try:
        with transaction() as conn:
            with conn.cursor() as cursor:
                if not lock_exercise(cursor, exercise_id):
                    raise ExerciseCardError(CODE_EXERCISE_NOT_FOUND, 404, "Exercício não encontrado.")
                parent = fetch_generation(cursor, parent_generation_id, exercise_id=exercise_id)
                if not parent:
                    raise ExerciseCardError(CODE_GENERATION_NOT_FOUND, 404, "Geração não encontrada.")
                existing = find_by_idempotency_key(cursor, exercise_id, key)
                if existing:
                    return _replayed(existing, parent_generation_id=parent["id"]), parent, False
                if parent["status"] not in REGENERABLE_STATUSES:
                    raise ExerciseCardError(
                        CODE_NOT_REGENERABLE,
                        409,
                        f"Geração em {parent['status']} não pode ser regenerada.",
                        generation_id=int(parent["id"]),
                    )
                _check_limits(cursor, exercise_id=exercise_id, user_id=user_id, now=now)
                parent_status = parent["status"]
                # Pai V1 pode ter `standard`/`full`, que a versao atual nao aceita.
                inherited_level = parent.get("text_level")
                if inherited_level not in card_prompt.TEXT_LEVELS:
                    inherited_level = card_prompt.DEFAULT_TEXT_LEVEL
                if parent_status == STATUS_GENERATED:
                    cursor.execute(
                        f"""
                        UPDATE {TABLE}
                        SET status = %s, reject_reason = %s, rejected_at = %s, updated_at = %s
                        WHERE id = %s AND status = %s
                        """,
                        (STATUS_REJECTED, REJECT_REASON_REGENERATED, now, now, int(parent["id"]), STATUS_GENERATED),
                    )
                    if int(cursor.rowcount or 0) == 0:
                        raise ExerciseCardError(
                            CODE_NOT_REGENERABLE, 409, "A geração mudou de estado; atualize e tente de novo."
                        )
                generation_id = insert_generation(
                    cursor,
                    exercise_id=exercise_id,
                    user_id=user_id,
                    idempotency_key=key,
                    text_level=requested_level or inherited_level,
                    instruction=wanted,
                    parent_generation_id=int(parent["id"]),
                    now=now,
                )
                child = fetch_generation(cursor, generation_id)
                parent = fetch_generation(cursor, parent["id"])
    except ExerciseCardError:
        raise
    except Exception as exc:
        if is_duplicate_key_error(exc):
            child = _replay_after_duplicate(exercise_id, key, int(parent_generation_id))
            return child, load_generation(int(parent_generation_id)) or {}, False
        raise

    log_event(
        EVENT_EXERCISE_CARD_REGENERATED,
        generation_id=child["id"],
        parent_generation_id=parent["id"],
        parent_status=parent_status,
        exercise_id=int(exercise_id),
        user_id=int(user_id),
        text_level=child.get("text_level"),
        has_instruction=bool(wanted),
    )
    return child, parent, True


def get_generation(*, exercise_id: int, generation_id: int) -> dict[str, Any]:
    require_enabled()
    _sweep_tx(now=utcnow(), exercise_id=exercise_id)
    with transaction() as conn:
        with conn.cursor() as cursor:
            row = fetch_generation(cursor, generation_id, exercise_id=exercise_id)
    if not row:
        raise ExerciseCardError(CODE_GENERATION_NOT_FOUND, 404, "Geração não encontrada.")
    return row


def list_generations(*, exercise_id: int, limit: int = DEFAULT_LIST_LIMIT) -> list[dict[str, Any]]:
    require_enabled()
    size = max(1, min(int(limit or DEFAULT_LIST_LIMIT), MAX_LIST_LIMIT))
    _sweep_tx(now=utcnow(), exercise_id=exercise_id)
    with transaction() as conn:
        with conn.cursor() as cursor:
            if not exercise_exists(cursor, exercise_id):
                raise ExerciseCardError(CODE_EXERCISE_NOT_FOUND, 404, "Exercício não encontrado.")
            cursor.execute(
                f"SELECT {_SELECT_LIST} FROM {TABLE} WHERE exercise_id = %s ORDER BY id DESC LIMIT %s",
                (int(exercise_id), size),
            )
            return [dict(row) for row in (cursor.fetchall() or [])]


def reject_generation(*, exercise_id: int, generation_id: int, user_id: int, reason: Any = None) -> dict[str, Any]:
    """GENERATED -> REJECTED com o motivo (texto humano, opcional)."""
    require_enabled()
    cleaned = normalize_reason(reason)
    now = utcnow()
    _sweep_tx(now=now, exercise_id=exercise_id)
    with transaction() as conn:
        with conn.cursor() as cursor:
            row = fetch_generation(cursor, generation_id, exercise_id=exercise_id, include_prompt=False)
            if not row:
                raise ExerciseCardError(CODE_GENERATION_NOT_FOUND, 404, "Geração não encontrada.")
            cursor.execute(
                f"""
                UPDATE {TABLE}
                SET status = %s, reject_reason = %s, rejected_at = %s, updated_at = %s
                WHERE id = %s AND exercise_id = %s AND status = %s
                """,
                (STATUS_REJECTED, cleaned, now, now, int(generation_id), int(exercise_id), STATUS_GENERATED),
            )
            if int(cursor.rowcount or 0) == 0:
                raise ExerciseCardError(
                    CODE_NOT_REVIEWABLE,
                    409,
                    f"Só uma geração GENERATED pode ser rejeitada (atual: {row['status']}).",
                    generation_id=int(generation_id),
                )
            row = fetch_generation(cursor, generation_id)
    log_event(
        EVENT_EXERCISE_CARD_REJECTED,
        generation_id=int(generation_id),
        exercise_id=int(exercise_id),
        user_id=int(user_id),
        has_reason=bool(cleaned),
    )
    return row


def _revert_approving(generation_id: int, *, code: str, message: Any) -> None:
    """APPROVING -> GENERATED depois de falha de processamento/upload. Best
    effort: se nem isto gravar, o sweep (2 min) devolve sozinho."""
    try:
        now = utcnow()
        with transaction() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {TABLE}
                    SET status = %s, error_code = %s, error_message = %s, updated_at = %s
                    WHERE id = %s AND status = %s
                    """,
                    (STATUS_GENERATED, str(code)[:40], _clip(message), now, int(generation_id), STATUS_APPROVING),
                )
    except Exception:  # noqa: BLE001 - o sweep cobre
        logger.exception("Falha ao devolver a geracao %s de APPROVING para GENERATED", generation_id)


def _publish_failure(
    generation_id: int,
    exercise_id: int,
    *,
    code: str,
    http_code: str,
    message: Any,
    http_status: int = 502,
) -> ExerciseCardError:
    _revert_approving(generation_id, code=code, message=message)
    log_event(
        EVENT_EXERCISE_CARD_PUBLISH_FAILED,
        generation_id=int(generation_id),
        exercise_id=int(exercise_id),
        error_code=code,
    )
    return ExerciseCardError(http_code, http_status, str(message), generation_id=int(generation_id))


def draft_expires_at(row: dict[str, Any]) -> Optional[datetime]:
    """Quando o rascunho (`exercise-card-drafts/{gid}/`) deixa de ser
    aprovavel: `generated_at` + `storage.DRAFT_TTL_DAYS` (a mesma regra do
    lifecycle em `infra/s3_uploads.tf`). Calculado pelo RELOGIO, nao pela
    existencia do objeto - em DEV (LocalStack, sem lifecycle) o comportamento
    e o mesmo de producao. None fora da revisao (GENERATED/APPROVING) ou sem
    rascunho gravado."""
    generated_at = row.get("generated_at")
    if row.get("status") not in PREVIEW_STATUSES or not row.get("preview_s3_key"):
        return None
    if not isinstance(generated_at, datetime):
        return None
    if generated_at.tzinfo is not None:
        generated_at = generated_at.astimezone(timezone.utc).replace(tzinfo=None)
    return generated_at + timedelta(days=storage.DRAFT_TTL_DAYS)


def is_draft_expired(row: dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    """Rascunho expirado: passou de `draft_expires_at`, OU a aprovacao ja
    encontrou o objeto apagado (`error_code = DRAFT_EXPIRED`, corrida perto
    do limite - o S3 pode apagar uns segundos antes do relogio da api)."""
    if row.get("status") not in PREVIEW_STATUSES:
        return False
    if row.get("error_code") == storage.DRAFT_EXPIRED:
        return True
    expires_at = draft_expires_at(row)
    return expires_at is not None and (now or utcnow()) >= expires_at


def approve_generation(
    *,
    exercise_id: int,
    generation_id: int,
    user_id: int,
    s3_client: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Publica a geracao GENERATED (sincrono, na api-core):

    1. transacao A: catalogo travado, GENERATED -> APPROVING (o lock da
       aprovacao) e `version = max + 1` - seguro porque a trava do catalogo +
       "no maximo uma APPROVING por exercicio" serializam as publicacoes;
    2. sem transacao aberta: le o preview (o card JA COMPOSTO, com logo, que
       o admin revisou), codifica (`processor.encode_card`: WebP, PNG,
       thumbnail - sem recompor nada) e grava os 3 objetos da versao;
    3. transacao B: anterior PUBLISHED -> SUPERSEDED, esta -> PUBLISHED, e o
       ponteiro do catalogo (`card_version` so agora, depois dos uploads).

    Falha em 2 devolve a geracao a GENERATED e responde 502 - exceto rascunho
    expirado (M72: pelo relogio, antes do passo 1, ou objeto inexistente no
    passo 2), que responde 410 `DRAFT_EXPIRED`. Devolve
    `(geracao, exercise_card)`."""
    from . import exercise_card_processor as processor  # Pillow so quando aprova

    require_enabled()
    now = utcnow()
    _sweep_tx(now=now, exercise_id=exercise_id)

    with transaction() as conn:
        with conn.cursor() as cursor:
            if not lock_exercise(cursor, exercise_id):
                raise ExerciseCardError(CODE_EXERCISE_NOT_FOUND, 404, "Exercício não encontrado.")
            row = fetch_generation(cursor, generation_id, exercise_id=exercise_id, include_prompt=False)
            if not row:
                raise ExerciseCardError(CODE_GENERATION_NOT_FOUND, 404, "Geração não encontrada.")
            if row["status"] != STATUS_GENERATED:
                raise ExerciseCardError(
                    CODE_NOT_REVIEWABLE,
                    409,
                    f"Só uma geração GENERATED pode ser aprovada (atual: {row['status']}).",
                    generation_id=int(generation_id),
                )
            if row.get("prompt_version") in LEGACY_UNCOMPOSED_PROMPT_VERSIONS:
                raise ExerciseCardError(
                    CODE_NOT_REVIEWABLE,
                    409,
                    "Geração de uma versão antiga do card (sem composição): rejeite ou gere novamente.",
                    generation_id=int(generation_id),
                )
            if is_draft_expired(row, now=now):
                # Nada muda na linha: continua GENERATED (rejeitar/regenerar
                # seguem valendo) e o S3 nem e tocado.
                raise ExerciseCardError(
                    CODE_DRAFT_EXPIRED, 410, MESSAGE_DRAFT_EXPIRED, generation_id=int(generation_id)
                )
            cursor.execute(
                f"SELECT id FROM {TABLE} WHERE exercise_id = %s AND status = %s AND id <> %s LIMIT 1",
                (int(exercise_id), STATUS_APPROVING, int(generation_id)),
            )
            other = cursor.fetchone()
            if other:
                raise ExerciseCardError(
                    CODE_IN_PROGRESS,
                    409,
                    "Outra versão deste exercício está sendo publicada.",
                    generation_id=int(other["id"]),
                )
            cursor.execute(
                f"UPDATE {TABLE} SET status = %s, updated_at = %s WHERE id = %s AND status = %s",
                (STATUS_APPROVING, now, int(generation_id), STATUS_GENERATED),
            )
            if int(cursor.rowcount or 0) == 0:
                raise ExerciseCardError(
                    CODE_NOT_REVIEWABLE, 409, "A geração mudou de estado; atualize e tente de novo."
                )
            version = next_version(cursor, exercise_id)

    started = time.monotonic()
    preview_key = row.get("preview_s3_key")
    try:
        if not preview_key:
            raise storage.ExerciseCardStorageError("Geração sem rascunho gravado.", code=storage.S3_READ_FAILED)
        png = storage.read_draft(preview_key, client=s3_client)
    except storage.ExerciseCardStorageError as exc:
        if exc.code == storage.DRAFT_EXPIRED:
            # Objeto apagado pelo lifecycle antes do relogio da api marcar
            # (corrida perto dos 7 dias). Volta a GENERATED com o error_code,
            # que o `serialize` le como `draft_expired`.
            raise _publish_failure(
                generation_id, exercise_id, code=storage.DRAFT_EXPIRED, http_code=CODE_DRAFT_EXPIRED,
                message=MESSAGE_DRAFT_EXPIRED, http_status=410,
            ) from exc
        raise _publish_failure(
            generation_id, exercise_id, code=exc.code, http_code=CODE_S3_READ_FAILED,
            message=f"Não foi possível ler o rascunho: {exc}",
        ) from exc

    try:
        card = processor.encode_card(png)
    except processor.ImageProcessingError as exc:
        raise _publish_failure(
            generation_id, exercise_id, code=exc.code, http_code=CODE_IMAGE_PROCESSING_FAILED,
            message=f"Imagem recusada no processamento ({exc.reason}): {exc}",
        ) from exc

    try:
        keys = storage.put_published(
            exercise_id,
            version,
            card_webp=card.card_webp,
            card_png=card.card_png,
            thumb_webp=card.thumb_webp,
            client=s3_client,
        )
    except storage.ExerciseCardStorageError as exc:
        raise _publish_failure(
            generation_id, exercise_id, code=exc.code, http_code=CODE_S3_UPLOAD_FAILED,
            message=f"Falha ao publicar no S3: {exc}",
        ) from exc

    published_at = utcnow()
    superseded_id: Optional[int] = None
    try:
        with transaction() as conn:
            with conn.cursor() as cursor:
                lock_exercise(cursor, exercise_id)
                cursor.execute(
                    f"SELECT id FROM {TABLE} WHERE exercise_id = %s AND status = %s ORDER BY id DESC LIMIT 1",
                    (int(exercise_id), STATUS_PUBLISHED),
                )
                previous = cursor.fetchone()
                superseded_id = int(previous["id"]) if previous else None
                cursor.execute(
                    f"UPDATE {TABLE} SET status = %s, updated_at = %s WHERE exercise_id = %s AND status = %s",
                    (STATUS_SUPERSEDED, published_at, int(exercise_id), STATUS_PUBLISHED),
                )
                cursor.execute(
                    f"""
                    UPDATE {TABLE}
                    SET status = %s, version = %s, card_s3_key = %s, card_png_s3_key = %s, thumb_s3_key = %s,
                        approved_by = %s, approved_at = %s, published_at = %s, updated_at = %s,
                        error_code = NULL, error_message = NULL
                    WHERE id = %s AND status = %s
                    """,
                    (
                        STATUS_PUBLISHED,
                        int(version),
                        keys.card_webp,
                        keys.card_png,
                        keys.thumb_webp,
                        int(user_id),
                        published_at,
                        published_at,
                        published_at,
                        int(generation_id),
                        STATUS_APPROVING,
                    ),
                )
                if int(cursor.rowcount or 0) == 0:
                    # Varrida para GENERATED (PUBLISH_INTERRUPTED) no meio: os
                    # objetos ficam orfaos num path que ninguem aponta.
                    raise ExerciseCardError(
                        CODE_NOT_REVIEWABLE, 409, "A aprovação expirou no meio; aprove de novo.",
                        generation_id=int(generation_id),
                    )
                cursor.execute(
                    """
                    UPDATE exercise_catalog
                    SET card_generation_id = %s, card_version = %s, card_published_at = %s
                    WHERE id = %s
                    """,
                    (int(generation_id), int(version), published_at, int(exercise_id)),
                )
                row = fetch_generation(cursor, generation_id)
    except ExerciseCardError:
        raise
    except Exception as exc:
        _revert_approving(generation_id, code=ERROR_INTERNAL, message=type(exc).__name__)
        log_event(
            EVENT_EXERCISE_CARD_PUBLISH_FAILED,
            generation_id=int(generation_id),
            exercise_id=int(exercise_id),
            error_code=ERROR_INTERNAL,
        )
        raise

    log_event(
        EVENT_EXERCISE_CARD_PUBLISHED,
        generation_id=int(generation_id),
        exercise_id=int(exercise_id),
        version=int(version),
        approved_by=int(user_id),
        superseded_generation_id=superseded_id,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return row, card_payload(exercise_id, version, published_at)


# ---------------------------------------------------------------------------
# Card publicado (qualquer autenticado)
# ---------------------------------------------------------------------------


def card_payload(exercise_id: int, version: int, published_at: Any) -> dict[str, Any]:
    urls = storage.public_urls(storage.published_keys(exercise_id, version))
    return {
        "url": urls["url"],
        "thumbnail_url": urls["thumbnail_url"],
        "png_url": urls["png_url"],
        "version": int(version),
        "published_at": iso(published_at),
    }


def published_card(exercise_id: int) -> Optional[dict[str, Any]]:
    """Card vigente do exercicio (ponteiro em `exercise_catalog`), ou None.
    Exercicio inexistente -> 404. Nao depende da flag: desligar a GERACAO
    nao esconde o que ja foi aprovado."""
    with transaction() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, card_version, card_published_at FROM exercise_catalog WHERE id = %s",
                (int(exercise_id),),
            )
            row = cursor.fetchone()
    if not row:
        raise ExerciseCardError(CODE_EXERCISE_NOT_FOUND, 404, "Exercício não encontrado.")
    if row.get("card_version") is None:
        return None
    return card_payload(exercise_id, int(row["card_version"]), row.get("card_published_at"))


# ---------------------------------------------------------------------------
# Serializacao
# ---------------------------------------------------------------------------


def iso(value: Any) -> Optional[str]:
    """DATETIME (UTC sem fuso) -> ISO 8601 com `Z`."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.replace(microsecond=0).isoformat() + "Z"
    return str(value)


def _number(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def _int_or_none(value: Any) -> Optional[int]:
    return None if value is None else int(value)


_TIMESTAMPS = (
    "created_at",
    "updated_at",
    "started_at",
    "generated_at",
    "approved_at",
    "published_at",
    "rejected_at",
    "failed_at",
)


def serialize(
    row: dict[str, Any],
    *,
    include_prompt: bool = True,
    presign: Optional[Callable[[str], Any]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """`generation` da API. `preview_url` (+ `preview_expires_at`) so em
    GENERATED/APPROVING - URL pre-assinada de 15 min do rascunho, que fica
    FORA da CDN. `card` so em PUBLISHED/SUPERSEDED. `prompt_text` so com
    `include_prompt` (detalhe; a listagem nao traz). Nunca as chaves S3.
    `draft_expires_at`/`draft_expired` (M72): validade do rascunho em
    GENERATED/APPROVING; expirado nao assina preview (a imagem ja nao existe
    ou vai deixar de existir)."""
    status = row.get("status")
    data: dict[str, Any] = {
        "id": int(row["id"]),
        "exercise_id": int(row["exercise_id"]),
        "card_type": row.get("card_type"),
        "status": status,
        "version": _int_or_none(row.get("version")),
        "parent_generation_id": _int_or_none(row.get("parent_generation_id")),
        "idempotency_key": row.get("idempotency_key"),
        "job_id": row.get("job_id"),
        "provider": row.get("provider"),
        "model": row.get("model"),
        "quality": row.get("quality"),
        "size": row.get("size"),
        "prompt_version": row.get("prompt_version"),
        "text_level": row.get("text_level"),
        "request_instruction": row.get("request_instruction"),
        "reject_reason": row.get("reject_reason"),
        "preview_url": None,
        "preview_expires_at": None,
        "draft_expires_at": iso(draft_expires_at(row)),
        "draft_expired": is_draft_expired(row, now=now),
        "card": None,
        "width": _int_or_none(row.get("width")),
        "height": _int_or_none(row.get("height")),
        "bytes": _int_or_none(row.get("bytes")),
        "tokens_in": _int_or_none(row.get("tokens_in")),
        "tokens_out": _int_or_none(row.get("tokens_out")),
        "cost_usd": _number(row.get("cost_usd")),
        "error_code": row.get("error_code"),
        "error_message": row.get("error_message"),
        "created_by": _int_or_none(row.get("created_by")),
        "approved_by": _int_or_none(row.get("approved_by")),
    }
    for column in _TIMESTAMPS:
        data[column] = iso(row.get(column))
    if include_prompt:
        data["prompt_text"] = row.get("prompt_text")

    preview_key = row.get("preview_s3_key")
    if status in PREVIEW_STATUSES and preview_key and not data["draft_expired"]:
        try:
            signed = (presign or storage.presign_draft_url)(preview_key)
            data["preview_url"] = signed.url
            data["preview_expires_at"] = iso(signed.expires_at)
        except Exception:  # noqa: BLE001 - sem preview, mas a resposta sai
            logger.warning("Falha ao assinar o preview da geracao %s", row.get("id"), exc_info=True)

    if status in CARD_STATUSES and row.get("card_s3_key"):
        data["card"] = {
            "url": public_assets.url_for(row["card_s3_key"]),
            "png_url": public_assets.url_for(row["card_png_s3_key"]) if row.get("card_png_s3_key") else None,
            "thumbnail_url": public_assets.url_for(row["thumb_s3_key"]) if row.get("thumb_s3_key") else None,
            "version": _int_or_none(row.get("version")),
        }
    return data

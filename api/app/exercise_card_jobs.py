"""Execucao assincrona dos cards ilustrados: o disparo (api-core) e o worker
(`job_kind="exercise-card"` na Lambda `musclefit-plan-generator`).

Por que em outra Lambda: a OpenAI documenta "ate 2 minutos" por imagem e o
API Gateway corta a api-core em 29 s. E o mesmo padrao do M4
(`exercise_description_autogen.py`): job no DynamoDB (`async_jobs.create_job`)
+ `lambda:Invoke` `Event` (`async_jobs.invoke_async`) na MESMA funcao, mesma
imagem, mesma IAM. O frontend acompanha o RECURSO
(`GET /exercises/{id}/card-generations/{gid}`), nao `GET /jobs/{id}` (que nao
valida dono); o `job_id` fica gravado na geracao para diagnostico.

Garantias do worker:

- **Sem cobranca dupla na redelivery.** O invoke assincrono pode entregar o
  mesmo evento de novo. O worker so trabalha se ganhar a reserva atomica
  PENDING -> GENERATING (`UPDATE ... WHERE status='PENDING'`); `rowcount=0`
  = outra entrega (ou o sweep) chegou antes -> sai SEM chamar a OpenAI.
- **Nunca morre por timeout.** A chamada a OpenAI tem 150 s
  (`OPENAI_IMAGE_TIMEOUT_SECONDS`) contra 180 s da Lambda, com retry so em
  429/5xx dentro de 20 s (`openai_client.generate_image`): sempre sobra tempo
  para gravar FAILED. Nenhuma transacao fica aberta durante a chamada.
- **Toda falha vira FAILED com `error_code`** (`OPENAI_*`, `S3_*`,
  `EXERCISE_NOT_FOUND`, `PROMPT_INVALID`, `CARD_COMPOSITION_FAILED`,
  `INTERNAL_ERROR`) e o job no DynamoDB termina `failed` (o handler da
  Lambda chama `mark_failed` quando este modulo levanta; `mark_done` quando
  devolve).

Estrategia B (2026-10-03): a OpenAI devolve SO a ilustracao (prompt V2, fundo
transparente) e o worker compoe o card final aqui mesmo, logo depois:

    generate_image -> grava `illustration.png` (crua, paga)
                   -> `exercise_card_composer.compose_card` (titulo, rotulos,
                      musculos, logo - o contexto do catalogo ja esta em memoria)
                   -> grava `preview.png` (o card COMPOSTO)
                   -> GENERATED com largura/altura/bytes do card composto

O preview e exatamente o que o admin revisa e o que a aprovacao publica.
Falha na composicao -> FAILED `CARD_COMPOSITION_FAILED`, com a ilustracao
paga guardada no S3 (o script de calibracao recompoe com `--compose-only`).

Nada aqui loga o prompt, a instrucao do admin nem a chave.
"""

from __future__ import annotations

import io
import logging
import time
from typing import Any, Optional

from . import async_jobs
from . import exercise_card_prompt as card_prompt
from . import exercise_card_service as svc
from . import exercise_card_storage as storage
from . import openai_client
from .db import transaction
from .observability import (
    EVENT_EXERCISE_CARD_FAILED,
    EVENT_EXERCISE_CARD_GENERATED,
    EVENT_EXERCISE_CARD_REDELIVERY_SKIPPED,
    EVENT_EXERCISE_CARD_STARTED,
    log_event,
)

logger = logging.getLogger("musclefit.exercise_cards.jobs")

JOB_TYPE = "exercise-card"
JOB_KIND = "exercise-card"
# Env var da api-core com o nome real da Lambda (a mesma de
# `POST /workout-plans/generate` e da descricao de execucao).
GENERATOR_FUNCTION_ENV_VAR = "PLAN_GENERATOR_FUNCTION_NAME"
# Fundo pedido a OpenAI para a ilustracao (`background` da Images API). Sem
# env de proposito: o compositor e o prompt V2 assumem transparente; o script
# de calibracao (so DEV) troca esta constante em processo para comparar.
ILLUSTRATION_BACKGROUND = "transparent"


class ExerciseCardJobError(RuntimeError):
    """Falha do worker com mensagem SO de codigo (vai para o `error` do job e
    para o log do handler; o detalhe fica em `error_message` da geracao)."""


# ---------------------------------------------------------------------------
# Disparo (api-core)
# ---------------------------------------------------------------------------


def dispatch(generation: dict[str, Any]) -> dict[str, Any]:
    """Cria o job, grava o `job_id` na geracao e invoca a Lambda. Falha ->
    geracao FAILED `DISPATCH_FAILED` e `ExerciseCardError` 502 (nada foi
    cobrado; o admin tenta de novo com outra chave ou regenera). Devolve a
    linha relida."""
    generation_id = int(generation["id"])
    exercise_id = int(generation["exercise_id"])
    job_id: Optional[str] = None
    try:
        # M65: dono do job = quem pediu a geracao (`created_by`, o admin
        # autenticado no POST); o painel acompanha pela geracao, nao pelo job.
        job_id = async_jobs.create_job(
            JOB_TYPE,
            request={"generation_id": generation_id, "exercise_id": exercise_id},
            owner_user_id=int(generation["created_by"]),
        )
        with transaction() as conn:
            with conn.cursor() as cursor:
                svc.set_job_id(cursor, generation_id, job_id)
        async_jobs.invoke_async(
            GENERATOR_FUNCTION_ENV_VAR,
            {"job_id": job_id, "job_kind": JOB_KIND, "generation_id": generation_id, "exercise_id": exercise_id},
        )
    except Exception as exc:  # noqa: BLE001 - IAM, env var, DynamoDB, Lambda: tudo vira DISPATCH_FAILED
        logger.warning("Falha ao enfileirar o card generation_id=%s: %s", generation_id, type(exc).__name__)
        _fail_quietly(generation_id, code=svc.ERROR_DISPATCH_FAILED, message=f"Falha ao enfileirar: {type(exc).__name__}")
        if job_id:
            try:
                async_jobs.mark_failed(job_id, "dispatch_failed")
            except Exception:  # noqa: BLE001 - o job expira sozinho (TTL 6 h)
                logger.warning("Falha ao marcar o job %s como failed", job_id)
        log_event(
            EVENT_EXERCISE_CARD_FAILED,
            generation_id=generation_id,
            exercise_id=exercise_id,
            job_id=job_id,
            stage="dispatch",
            error_code=svc.ERROR_DISPATCH_FAILED,
        )
        raise svc.ExerciseCardError(
            svc.CODE_DISPATCH_FAILED,
            502,
            "Não foi possível enfileirar a geração do card. Tente de novo.",
            generation_id=generation_id,
        ) from exc
    return svc.load_generation(generation_id) or generation


def _fail_quietly(generation_id: int, *, code: str, message: Any, prompt_text: Optional[str] = None) -> bool:
    try:
        with transaction() as conn:
            with conn.cursor() as cursor:
                return svc.mark_failed(
                    cursor, generation_id, now=svc.utcnow(), code=code, message=message, prompt_text=prompt_text
                )
    except Exception:  # noqa: BLE001 - linha fica viva; o sweep fecha como WORKER_LOST
        logger.exception("Falha ao gravar FAILED na geracao %s", generation_id)
        return False


# ---------------------------------------------------------------------------
# Worker (Lambda plan-generator)
# ---------------------------------------------------------------------------


class _StepFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _png_size(image: bytes) -> tuple[Optional[int], Optional[int]]:
    """Dimensoes pelo cabecalho (sem decodificar). Ilegivel -> (None, None):
    a validacao de verdade e a da aprovacao."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image)) as probe:
            return int(probe.width), int(probe.height)
    except Exception:  # noqa: BLE001
        return None, None


def _compose(illustration: bytes, context: card_prompt.ExerciseCardContext) -> bytes:
    """Card final a partir da ilustracao. Import tardio: Pillow + fontes so
    no worker de cards (a api-core importa este modulo para o disparo).
    Qualquer falha - inclusive import, asset ausente ou bug - vira
    `_StepFailure(CARD_COMPOSITION_FAILED)`: a linha nunca fica presa."""
    try:
        from . import exercise_card_composer as composer
    except Exception as exc:  # noqa: BLE001
        logger.exception("Compositor indisponivel")
        raise _StepFailure(svc.ERROR_CARD_COMPOSITION_FAILED, f"Compositor indisponivel: {type(exc).__name__}") from exc
    try:
        return composer.compose_card(illustration, composer.content_from_context(context), composer.load_card_assets())
    except composer.CardCompositionError as exc:
        raise _StepFailure(svc.ERROR_CARD_COMPOSITION_FAILED, f"Composicao do card falhou ({exc.reason}): {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - bug do compositor tambem e falha de composicao
        logger.exception("Erro inesperado ao compor o card")
        raise _StepFailure(svc.ERROR_CARD_COMPOSITION_FAILED, f"Composicao do card falhou: {type(exc).__name__}") from exc


def run_generation_job(payload: dict[str, Any]) -> dict[str, Any]:
    """O outro lado do disparo: roda DENTRO da plan-generator
    (`app/lambda_plan_generator.py`) - ou em processo, pelo script de
    calibracao de DEV. `payload`: `{"generation_id", "exercise_id"?, "job_id"?}`.

    Devolve o resumo (o handler grava com `mark_done`); falha -> geracao
    FAILED e `ExerciseCardJobError` (o handler grava com `mark_failed`)."""
    generation_id = int((payload or {}).get("generation_id") or 0)
    if generation_id <= 0:
        raise ValueError("Evento de card sem generation_id.")
    job_id = (payload or {}).get("job_id")

    with transaction() as conn:
        with conn.cursor() as cursor:
            reserved = svc.reserve_for_generation(cursor, generation_id, svc.utcnow())
            row = svc.fetch_generation(cursor, generation_id)

    if not reserved:
        status = row.get("status") if row else None
        log_event(
            EVENT_EXERCISE_CARD_REDELIVERY_SKIPPED,
            generation_id=generation_id,
            exercise_id=row.get("exercise_id") if row else None,
            job_id=job_id,
            status=status,
        )
        return {"generation_id": generation_id, "status": "skipped", "reason": "not_pending", "generation_status": status}

    exercise_id = int(row["exercise_id"])
    log_event(EVENT_EXERCISE_CARD_STARTED, generation_id=generation_id, exercise_id=exercise_id, job_id=job_id)

    prompt_text: Optional[str] = None
    stage = "prompt"
    try:
        with transaction() as conn:
            with conn.cursor() as cursor:
                context = card_prompt.load_context(cursor, exercise_id)
                parent_reason = svc.human_reject_reason(cursor, row.get("parent_generation_id"))
        if context is None:
            raise _StepFailure(svc.ERROR_EXERCISE_NOT_FOUND, f"Exercício {exercise_id} não existe mais no catálogo.")
        try:
            prompt_text = card_prompt.build_prompt(
                context,
                row.get("text_level") or card_prompt.DEFAULT_TEXT_LEVEL,
                instruction=row.get("request_instruction"),
                parent_reject_reason=parent_reason,
            )
        except card_prompt.ExerciseCardPromptError as exc:
            raise _StepFailure(svc.ERROR_PROMPT_INVALID, str(exc)) from exc

        stage = "openai"
        started = time.monotonic()
        result = openai_client.generate_image(
            prompt_text,
            model=row.get("model"),
            quality=row.get("quality"),
            size=row.get("size"),
            background=ILLUSTRATION_BACKGROUND,
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        # A ilustracao foi paga: fica guardada ANTES de qualquer outra etapa
        # que possa falhar (recompor nao custa nada; gerar de novo custa).
        stage = "illustration_upload"
        storage.put_draft(generation_id, result.image, filename=storage.DRAFT_ILLUSTRATION)

        stage = "compose"
        compose_started = time.monotonic()
        card_png = _compose(result.image, context)
        compose_ms = int((time.monotonic() - compose_started) * 1000)

        stage = "draft_upload"
        preview_key = storage.put_draft(generation_id, card_png)

        stage = "store"
        width, height = _png_size(card_png)
        with transaction() as conn:
            with conn.cursor() as cursor:
                stored = svc.mark_generated(
                    cursor,
                    generation_id,
                    now=svc.utcnow(),
                    prompt_text=prompt_text,
                    preview_s3_key=preview_key,
                    model=result.model,
                    quality=result.quality,
                    size=result.size,
                    width=width,
                    height=height,
                    byte_count=len(card_png),
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    cost_usd=result.cost_usd,
                )
    except openai_client.OpenAIGenerationError as exc:
        _fail(generation_id, exercise_id, job_id, stage=stage, code=exc.code, message=str(exc), prompt_text=prompt_text)
    except storage.ExerciseCardStorageError as exc:
        _fail(generation_id, exercise_id, job_id, stage=stage, code=exc.code, message=str(exc), prompt_text=prompt_text)
    except _StepFailure as exc:
        _fail(generation_id, exercise_id, job_id, stage=stage, code=exc.code, message=str(exc), prompt_text=prompt_text)
    except Exception as exc:  # noqa: BLE001 - nada pode deixar a linha presa em GENERATING
        logger.exception("Erro inesperado no card generation_id=%s (stage=%s)", generation_id, stage)
        _fail(
            generation_id, exercise_id, job_id, stage=stage, code=svc.ERROR_INTERNAL,
            message=f"{type(exc).__name__}", prompt_text=prompt_text,
        )

    if not stored:
        # O sweep levou a linha para FAILED (WORKER_LOST) enquanto a OpenAI
        # respondia: o resultado e descartado (o rascunho fica orfao).
        log_event(
            EVENT_EXERCISE_CARD_REDELIVERY_SKIPPED,
            generation_id=generation_id,
            exercise_id=exercise_id,
            job_id=job_id,
            status="discarded_after_sweep",
        )
        return {"generation_id": generation_id, "status": "discarded"}

    log_event(
        EVENT_EXERCISE_CARD_GENERATED,
        generation_id=generation_id,
        exercise_id=exercise_id,
        job_id=job_id,
        model=result.model,
        quality=result.quality,
        size=result.size,
        latency_ms=latency_ms,
        compose_ms=compose_ms,
        layout_version=_layout_version(),
        attempts=result.attempts,
        bytes=len(card_png),
        illustration_bytes=len(result.image),
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
    )
    return {
        "generation_id": generation_id,
        "exercise_id": exercise_id,
        "status": svc.STATUS_GENERATED,
        "latency_ms": latency_ms,
        "cost_usd": None if result.cost_usd is None else float(result.cost_usd),
    }


def _layout_version() -> str:
    from .exercise_card_composer import LAYOUT_VERSION

    return LAYOUT_VERSION


def _fail(
    generation_id: int,
    exercise_id: int,
    job_id: Optional[str],
    *,
    stage: str,
    code: str,
    message: str,
    prompt_text: Optional[str],
) -> None:
    """Grava FAILED, emite o evento (so codigo e etapa) e levanta o erro do
    job com mensagem sem detalhe externo."""
    _fail_quietly(generation_id, code=code, message=message, prompt_text=prompt_text)
    log_event(
        EVENT_EXERCISE_CARD_FAILED,
        generation_id=generation_id,
        exercise_id=exercise_id,
        job_id=job_id,
        stage=stage,
        error_code=code,
    )
    raise ExerciseCardJobError(f"Card generation {generation_id} falhou: {code}") from None

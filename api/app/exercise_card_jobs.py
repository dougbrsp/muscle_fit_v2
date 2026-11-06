"""Execucao assincrona dos cards de exercicio: o disparo (api-core) e o worker
(`job_kind="exercise-card"` na Lambda `musclefit-plan-generator`).

Por que em outra Lambda: cada foto leva de dezenas de segundos a ~2 minutos
e o API Gateway corta a api-core em 29 s. E o mesmo padrao do M4
(`exercise_description_autogen.py`): job no DynamoDB (`async_jobs.create_job`)
+ `lambda:Invoke` `Event` (`async_jobs.invoke_async`) na MESMA funcao, mesma
imagem, mesma IAM. O frontend acompanha o RECURSO
(`GET /exercises/{id}/card-generations/{gid}`), nao `GET /jobs/{id}` (que nao
valida dono); o `job_id` fica gravado na geracao para diagnostico.

V3 (cards fotorrealistas, 2026-11-06) - DUAS chamadas sequenciais a OpenAI:

    fotos de referencia do admin (`exercise_card_references`, ate 3, S3)
    cena 1: generate_image(prompt da posicao inicial, referencias)
            -> grava `scene1.png` (crua, paga)
    orcamento: ainda cabe o pior caso da cena 2 + composicao?
            -> nao: FAILED `TIME_BUDGET_EXCEEDED` com o custo da cena 1
    cena 2: generate_image(prompt da posicao final, [cena 1 em JPEG, referencias])
            -> grava `scene2.png` (crua, paga)
    `exercise_card_composer.compose_card_v3` -> grava `preview.png` (o card)
    GENERATED com o custo e os tokens SOMADOS das duas chamadas

Garantias do worker:

- **Sem cobranca dupla na redelivery.** O worker so trabalha se ganhar a
  reserva atomica PENDING -> GENERATING (`UPDATE ... WHERE status='PENDING'`);
  `rowcount=0` = outra entrega (ou o sweep) chegou antes -> sai SEM chamar a
  OpenAI.
- **Nunca morre por timeout.** Cada chamada tem o pior caso
  `openai_client.image_call_worst_case_seconds(timeout)` (janela de retry +
  esperas + timeout). A cena 2 so comeca se o tempo ja gasto + esse pior caso +
  `POST_PROCESS_RESERVE_SECONDS` couber em `JOB_TIME_BUDGET_SECONDS`, que fica
  `JOB_TIME_BUDGET_MARGIN_SECONDS` abaixo do timeout da Lambda
  (`async_jobs.LAMBDA_TIMEOUT_SECONDS`, teste de paridade): sempre sobra tempo
  para gravar FAILED. Nenhuma transacao fica aberta durante as chamadas.
- **O que foi pago fica registrado.** Falha depois da cena 1 grava FAILED com
  modelo, tokens e custo da(s) chamada(s) ja feita(s) (`svc.mark_failed`), e
  as fotos cruas ja geradas ficam no S3 (o script de calibracao recompoe com
  `--compose-only`).
- **Toda falha vira FAILED com `error_code`** (`OPENAI_*`, `S3_*`,
  `EXERCISE_NOT_FOUND`, `PROMPT_INVALID`, `CARD_COMPOSITION_FAILED`,
  `TIME_BUDGET_EXCEEDED`, `INTERNAL_ERROR`) e o job no DynamoDB termina
  `failed` (o handler da Lambda chama `mark_failed` quando este modulo
  levanta; `mark_done` quando devolve).

Nada aqui loga o prompt, a instrucao do admin, as imagens nem a chave.
"""

from __future__ import annotations

import io
import logging
import time
from decimal import Decimal
from typing import Any, Optional, Sequence

from . import async_jobs
from . import exercise_card_prompt as card_prompt
from . import exercise_card_references as refs
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
# Lambda que roda o worker (a de `async_jobs.JOB_TYPE_LAMBDA[JOB_TYPE]`).
WORKER_FUNCTION = "musclefit-plan-generator"

# Orcamento de tempo do job (V3). Com o timeout padrao de 150 s por chamada:
# pior caso de cada chamada = 20 + 1 + 2 + 150 = 173 s; duas = 346 s + reserva
# de 25 s = 371 s <= 400 s <= 420 s da Lambda - 20 s de margem (teste).
JOB_TIME_BUDGET_SECONDS = 400
JOB_TIME_BUDGET_MARGIN_SECONDS = 20
# Composicao + 3 uploads (scene2, preview) + gravar no banco, com folga.
POST_PROCESS_RESERVE_SECONDS = 25

# Fundo pedido a OpenAI em cada foto: academia OPACA (o compositor nao
# recorta). A referencia oficial do edits confirma `opaque` para o
# gpt-image-2.5-sunburst/-flare.
SCENE_BACKGROUND = "opaque"
# A cena 1 volta como 1a imagem do edits da cena 2: re-encodada em memoria
# para JPEG (um PNG 1024x1536 tem 2-4 MB; o multipart sai de sa-east-1). O
# `scene1.png` gravado no S3 continua PNG.
SCENE_REFERENCE_JPEG_QUALITY = 92

# Relogio do orcamento (os testes injetam um falso).
_monotonic = time.monotonic


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


# ---------------------------------------------------------------------------
# Soma do que foi pago nas chamadas do job
# ---------------------------------------------------------------------------


def sum_costs(results: Sequence[openai_client.ImageResult]) -> Optional[Decimal]:
    """Custo total; `None` se QUALQUER chamada tiver custo desconhecido (sem
    total parcial apresentado como total) ou se nao houve chamada."""
    costs = [result.cost_usd for result in results]
    if not costs or any(cost is None for cost in costs):
        return None
    return sum(costs, Decimal(0))


def sum_tokens(values: Sequence[Optional[int]]) -> Optional[int]:
    """Soma dos tokens conhecidos; `None` so se nenhum for conhecido."""
    known = [int(value) for value in values if value is not None]
    return sum(known) if known else None


def _paid_fields(results: Sequence[openai_client.ImageResult]) -> dict[str, Any]:
    """Colunas de custo para `svc.mark_failed`/`mark_generated` a partir das
    chamadas ja feitas (vazio se nenhuma: a linha fica como estava)."""
    if not results:
        return {}
    last = results[-1]
    return {
        "model": last.model,
        "quality": last.quality,
        "size": last.size,
        "tokens_in": sum_tokens([result.tokens_in for result in results]),
        "tokens_out": sum_tokens([result.tokens_out for result in results]),
        "cost_usd": sum_costs(results),
    }


def _fail_quietly(
    generation_id: int,
    *,
    code: str,
    message: Any,
    prompt_text: Optional[str] = None,
    paid: Sequence[openai_client.ImageResult] = (),
) -> bool:
    try:
        with transaction() as conn:
            with conn.cursor() as cursor:
                return svc.mark_failed(
                    cursor, generation_id, now=svc.utcnow(), code=code, message=message, prompt_text=prompt_text,
                    **_paid_fields(paid),
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


def scene_reference_jpeg(png: bytes) -> bytes:
    """A foto da cena 1 como imagem de entrada da cena 2: JPEG RGB qualidade
    `SCENE_REFERENCE_JPEG_QUALITY`, sem metadados, so em memoria. Ilegivel ->
    `_StepFailure(OPENAI_GENERATION_FAILED)` (a OpenAI devolveu algo que nao
    e imagem)."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(png)) as image:
            image.load()
            rgb = image.convert("RGB")
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=SCENE_REFERENCE_JPEG_QUALITY)
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001 - qualquer falha do Pillow
        raise _StepFailure(
            openai_client.OPENAI_GENERATION_FAILED, f"Foto da cena 1 ilegível: {type(exc).__name__}"
        ) from exc


def _compose(scene_start: bytes, scene_end: bytes, context: card_prompt.ExerciseCardContext) -> bytes:
    """Card final a partir das duas fotos. Import tardio: Pillow + fontes so
    no worker de cards (a api-core importa este modulo para o disparo).
    Qualquer falha - inclusive import, asset ausente ou bug - vira
    `_StepFailure(CARD_COMPOSITION_FAILED)`: a linha nunca fica presa."""
    try:
        from . import exercise_card_composer as composer
    except Exception as exc:  # noqa: BLE001
        logger.exception("Compositor indisponivel")
        raise _StepFailure(svc.ERROR_CARD_COMPOSITION_FAILED, f"Compositor indisponivel: {type(exc).__name__}") from exc
    try:
        return composer.compose_card_v3(
            scene_start, scene_end, composer.content_from_context(context), composer.load_card_assets()
        )
    except composer.CardCompositionError as exc:
        raise _StepFailure(svc.ERROR_CARD_COMPOSITION_FAILED, f"Composicao do card falhou ({exc.reason}): {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - bug do compositor tambem e falha de composicao
        logger.exception("Erro inesperado ao compor o card")
        raise _StepFailure(svc.ERROR_CARD_COMPOSITION_FAILED, f"Composicao do card falhou: {type(exc).__name__}") from exc


def _read_references(
    generation_id: int, rows: Sequence[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[bytes]]:
    """Bytes das fotos de referencia ATIVAS lidas no banco, pulando as que
    sumiram do S3 entre a leitura das linhas e agora (admin excluiu a foto
    durante a geracao: soft-delete + remocao em melhor esforco). Devolve as
    linhas EFETIVAS (alinhadas aos bytes: ids e notas so das fotos lidas).

    Havia fotos e NENHUMA foi lida -> `ExerciseCardStorageError(S3_READ_FAILED)`:
    IAM ou bucket quebrado nao pode virar uma geracao silenciosa sem as fotos.
    Algumas faltaram -> segue com as encontradas e loga um aviso so com ids e
    contagens (nunca a key S3)."""
    if not rows:
        return [], []
    by_key = {str(ref["s3_key"]): ref for ref in rows}
    present = storage.get_reference_bytes_present(list(by_key))
    if not present:
        raise storage.ExerciseCardStorageError(
            f"Nenhuma das {len(rows)} foto(s) de referência pôde ser lida do S3.", code=storage.S3_READ_FAILED
        )
    effective = [by_key[key] for key, _image in present]
    if len(effective) < len(rows):
        found = {int(ref["id"]) for ref in effective}
        logger.warning(
            "Fotos de referencia ausentes no S3 puladas: generation_id=%s lidas=%s esperadas=%s ids_ausentes=%s",
            generation_id,
            len(effective),
            len(rows),
            [int(ref["id"]) for ref in rows if int(ref["id"]) not in found],
        )
    return effective, [image for _key, image in present]


def _budget_allows_second_call(job_started: float, timeout: float) -> bool:
    elapsed = _monotonic() - job_started
    needed = openai_client.image_call_worst_case_seconds(timeout) + POST_PROCESS_RESERVE_SECONDS
    return elapsed + needed <= JOB_TIME_BUDGET_SECONDS


def run_generation_job(payload: dict[str, Any]) -> dict[str, Any]:
    """O outro lado do disparo: roda DENTRO da plan-generator
    (`app/lambda_plan_generator.py`) - ou em processo, pelo script de
    calibracao de DEV. `payload`: `{"generation_id", "exercise_id"?, "job_id"?}`.

    Devolve o resumo (o handler grava com `mark_done`); falha -> geracao
    FAILED e `ExerciseCardJobError` (o handler grava com `mark_failed`)."""
    job_started = _monotonic()
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
    paid: list[openai_client.ImageResult] = []
    reference_ids: list[int] = []
    stage = "prompt"
    try:
        with transaction() as conn:
            with conn.cursor() as cursor:
                context = card_prompt.load_context(cursor, exercise_id)
                parent_reason = svc.human_reject_reason(cursor, row.get("parent_generation_id"))
                reference_rows = refs.active_reference_rows(cursor, exercise_id)
        if context is None:
            raise _StepFailure(svc.ERROR_EXERCISE_NOT_FOUND, f"Exercício {exercise_id} não existe mais no catálogo.")
        stage = "reference_read"
        reference_rows, reference_images = _read_references(generation_id, reference_rows)
        reference_ids = [int(ref["id"]) for ref in reference_rows]

        stage = "prompt"
        try:
            prompts = card_prompt.build_scene_prompts(
                context,
                row.get("text_level") or card_prompt.DEFAULT_TEXT_LEVEL,
                reference_count=len(reference_images),
                reference_notes=[ref.get("note") for ref in reference_rows],
                instruction=row.get("request_instruction"),
                parent_reject_reason=parent_reason,
            )
        except card_prompt.ExerciseCardPromptError as exc:
            raise _StepFailure(svc.ERROR_PROMPT_INVALID, str(exc)) from exc
        prompt_text = prompts.joined()
        timeout = openai_client.image_settings().timeout_seconds
        image_options = {
            "model": row.get("model"),
            "quality": row.get("quality"),
            "size": row.get("size"),
            "timeout": timeout,
            "background": SCENE_BACKGROUND,
        }

        stage = "openai_scene1"
        started = _monotonic()
        scene_start = openai_client.generate_image(prompts.start, reference_images=reference_images, **image_options)
        latency_scene1_ms = int((_monotonic() - started) * 1000)
        paid.append(scene_start)

        # Cada foto foi paga: fica guardada ANTES de qualquer outra etapa que
        # possa falhar (recompor nao custa nada; gerar de novo custa).
        stage = "scene1_upload"
        storage.put_draft_scene(generation_id, card_prompt.SCENE_START, scene_start.image)

        stage = "time_budget"
        if not _budget_allows_second_call(job_started, timeout):
            raise _StepFailure(
                svc.ERROR_TIME_BUDGET_EXCEEDED,
                f"Sem tempo para a cena 2 dentro de {JOB_TIME_BUDGET_SECONDS} s (cena 1 levou {latency_scene1_ms} ms).",
            )

        stage = "scene1_reference"
        scene_start_jpeg = scene_reference_jpeg(scene_start.image)

        stage = "openai_scene2"
        started = _monotonic()
        scene_end = openai_client.generate_image(
            prompts.end, reference_images=[scene_start_jpeg, *reference_images], **image_options
        )
        latency_scene2_ms = int((_monotonic() - started) * 1000)
        paid.append(scene_end)

        stage = "scene2_upload"
        storage.put_draft_scene(generation_id, card_prompt.SCENE_END, scene_end.image)

        stage = "compose"
        compose_started = _monotonic()
        card_png = _compose(scene_start.image, scene_end.image, context)
        compose_ms = int((_monotonic() - compose_started) * 1000)

        stage = "draft_upload"
        preview_key = storage.put_draft(generation_id, card_png)

        stage = "store"
        width, height = _png_size(card_png)
        totals = _paid_fields(paid)
        with transaction() as conn:
            with conn.cursor() as cursor:
                stored = svc.mark_generated(
                    cursor,
                    generation_id,
                    now=svc.utcnow(),
                    prompt_text=prompt_text,
                    preview_s3_key=preview_key,
                    width=width,
                    height=height,
                    byte_count=len(card_png),
                    **totals,
                )
    except openai_client.OpenAIGenerationError as exc:
        _fail(generation_id, exercise_id, job_id, stage=stage, code=exc.code, message=str(exc),
              prompt_text=prompt_text, paid=paid, reference_ids=reference_ids)
    except storage.ExerciseCardStorageError as exc:
        _fail(generation_id, exercise_id, job_id, stage=stage, code=exc.code, message=str(exc),
              prompt_text=prompt_text, paid=paid, reference_ids=reference_ids)
    except _StepFailure as exc:
        _fail(generation_id, exercise_id, job_id, stage=stage, code=exc.code, message=str(exc),
              prompt_text=prompt_text, paid=paid, reference_ids=reference_ids)
    except Exception as exc:  # noqa: BLE001 - nada pode deixar a linha presa em GENERATING
        logger.exception("Erro inesperado no card generation_id=%s (stage=%s)", generation_id, stage)
        _fail(
            generation_id, exercise_id, job_id, stage=stage, code=svc.ERROR_INTERNAL,
            message=f"{type(exc).__name__}", prompt_text=prompt_text, paid=paid, reference_ids=reference_ids,
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

    latency_ms = latency_scene1_ms + latency_scene2_ms
    log_event(
        EVENT_EXERCISE_CARD_GENERATED,
        generation_id=generation_id,
        exercise_id=exercise_id,
        job_id=job_id,
        model=totals["model"],
        quality=totals["quality"],
        size=totals["size"],
        scene_count=len(paid),
        reference_count=len(reference_ids),
        reference_ids=reference_ids,
        endpoint_scene1=scene_start.endpoint,
        endpoint_scene2=scene_end.endpoint,
        latency_ms=latency_ms,
        latency_ms_scene1=latency_scene1_ms,
        latency_ms_scene2=latency_scene2_ms,
        compose_ms=compose_ms,
        layout_version=_layout_version(),
        attempts_scene1=scene_start.attempts,
        attempts_scene2=scene_end.attempts,
        bytes=len(card_png),
        scene_bytes=len(scene_start.image) + len(scene_end.image),
        tokens_in=totals["tokens_in"],
        tokens_out=totals["tokens_out"],
        cost_usd=totals["cost_usd"],
    )
    return {
        "generation_id": generation_id,
        "exercise_id": exercise_id,
        "status": svc.STATUS_GENERATED,
        "latency_ms": latency_ms,
        "cost_usd": None if totals["cost_usd"] is None else float(totals["cost_usd"]),
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
    paid: Sequence[openai_client.ImageResult] = (),
    reference_ids: Sequence[int] = (),
) -> None:
    """Grava FAILED (com o que ja foi pago), emite o evento (so codigos,
    etapa e numeros) e levanta o erro do job com mensagem sem detalhe
    externo."""
    _fail_quietly(generation_id, code=code, message=message, prompt_text=prompt_text, paid=paid)
    totals = _paid_fields(paid)
    log_event(
        EVENT_EXERCISE_CARD_FAILED,
        generation_id=generation_id,
        exercise_id=exercise_id,
        job_id=job_id,
        stage=stage,
        error_code=code,
        scene_count=len(paid),
        reference_ids=list(reference_ids),
        cost_usd=totals.get("cost_usd"),
    )
    raise ExerciseCardJobError(f"Card generation {generation_id} falhou: {code}") from None

"""Rotas dos cards ilustrados de exercicios (`prefix="/exercises"`).

    POST /exercises/{id}/card-generations                   admin  202 {generation} (replay: 200)
    GET  /exercises/{id}/card-generations?limit=20          admin  {items} (sem prompt_text)
    GET  /exercises/{id}/card-generations/{gid}             admin  {generation}
    POST /exercises/{id}/card-generations/{gid}/approve     admin  {generation, exercise_card}
    POST /exercises/{id}/card-generations/{gid}/reject      admin  {generation}
    POST /exercises/{id}/card-generations/{gid}/regenerate  admin  202 {generation, parent} (replay: 200)
    GET  /exercises/{id}/card                               autenticado  {exercise_id, exercise_card | null}

Regras (detalhes em `exercise_card_service.py`):

- toda rota `card-generations` exige admin (403) e a flag
  `EXERCISE_CARD_GENERATION_ENABLED` (503 `exercise_card_generation_disabled`
  - em producao, ate o fim do checkpoint, e o que todas respondem);
- `GET /card` (o card publicado) e de qualquer usuario autenticado e NAO
  depende da flag: desligar a geracao nao esconde o que ja foi aprovado;
- erro de negocio sai como `{"detail": {"error": <codigo>, "message": ...,
  "generation_id"?}}`, no mesmo formato das rotas de admin do catalogo;
- anti-IDOR: `{gid}` de outro exercicio responde 404, igual a inexistente.

Sem colisao com `exercise_catalog_admin.py` (mesmo prefixo): todas as rotas
daqui tem o segmento fixo `card-generations`/`card` depois de `{id}`, que la
nao existe.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from . import exercise_card_jobs as jobs
from . import exercise_card_service as svc
from .security import Identity, get_current_identity, require_admin

router = APIRouter(prefix="/exercises", tags=["exercise-cards"])


def _http_error(exc: svc.ExerciseCardError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.detail())


@router.post("/{exercise_id}/card-generations")
def create_card_generation(
    exercise_id: int,
    payload: Dict[str, Any] = Body(default_factory=dict),
    identity: Identity = Depends(require_admin),
):
    """`{idempotency_key, instruction?, text_level?}` -> 202 com a geracao
    PENDING (o worker roda na plan-generator). Mesma chave de novo -> 200 com
    a existente, sem novo disparo."""
    try:
        row, created = svc.create_generation(
            exercise_id=exercise_id,
            user_id=identity.user_id,
            idempotency_key=payload.get("idempotency_key"),
            instruction=payload.get("instruction"),
            text_level=payload.get("text_level"),
        )
        if created:
            row = jobs.dispatch(row)
    except svc.ExerciseCardError as exc:
        raise _http_error(exc) from None
    return JSONResponse(status_code=202 if created else 200, content={"generation": svc.serialize(row)})


@router.get("/{exercise_id}/card-generations")
def list_card_generations(
    exercise_id: int,
    limit: int = Query(default=svc.DEFAULT_LIST_LIMIT, ge=1, le=svc.MAX_LIST_LIMIT),
    identity: Identity = Depends(require_admin),
):
    try:
        rows = svc.list_generations(exercise_id=exercise_id, limit=limit)
    except svc.ExerciseCardError as exc:
        raise _http_error(exc) from None
    return {"items": [svc.serialize(row, include_prompt=False) for row in rows]}


@router.get("/{exercise_id}/card-generations/{generation_id}")
def get_card_generation(
    exercise_id: int,
    generation_id: int,
    identity: Identity = Depends(require_admin),
):
    """O recurso que o frontend consulta durante a geracao (polling)."""
    try:
        row = svc.get_generation(exercise_id=exercise_id, generation_id=generation_id)
    except svc.ExerciseCardError as exc:
        raise _http_error(exc) from None
    return {"generation": svc.serialize(row)}


@router.post("/{exercise_id}/card-generations/{generation_id}/approve")
def approve_card_generation(
    exercise_id: int,
    generation_id: int,
    identity: Identity = Depends(require_admin),
):
    try:
        row, card = svc.approve_generation(
            exercise_id=exercise_id, generation_id=generation_id, user_id=identity.user_id
        )
    except svc.ExerciseCardError as exc:
        raise _http_error(exc) from None
    return {"generation": svc.serialize(row), "exercise_card": card}


@router.post("/{exercise_id}/card-generations/{generation_id}/reject")
def reject_card_generation(
    exercise_id: int,
    generation_id: int,
    payload: Dict[str, Any] = Body(default_factory=dict),
    identity: Identity = Depends(require_admin),
):
    """`{reason?}` - o motivo (texto humano) vai para o prompt de uma
    regeneracao futura a partir desta geracao."""
    try:
        row = svc.reject_generation(
            exercise_id=exercise_id,
            generation_id=generation_id,
            user_id=identity.user_id,
            reason=payload.get("reason"),
        )
    except svc.ExerciseCardError as exc:
        raise _http_error(exc) from None
    return {"generation": svc.serialize(row)}


@router.post("/{exercise_id}/card-generations/{generation_id}/regenerate")
def regenerate_card_generation(
    exercise_id: int,
    generation_id: int,
    payload: Dict[str, Any] = Body(default_factory=dict),
    identity: Identity = Depends(require_admin),
):
    """`{idempotency_key, instruction?, text_level?}` -> 202 com a filha
    PENDING e o pai (GENERATED vira REJECTED `regenerated`)."""
    try:
        child, parent, created = svc.regenerate_generation(
            exercise_id=exercise_id,
            parent_generation_id=generation_id,
            user_id=identity.user_id,
            idempotency_key=payload.get("idempotency_key"),
            instruction=payload.get("instruction"),
            text_level=payload.get("text_level"),
        )
        if created:
            child = jobs.dispatch(child)
    except svc.ExerciseCardError as exc:
        raise _http_error(exc) from None
    return JSONResponse(
        status_code=202 if created else 200,
        content={"generation": svc.serialize(child), "parent": svc.serialize(parent) if parent else None},
    )


@router.get("/{exercise_id}/card")
def get_exercise_card(
    exercise_id: int,
    identity: Identity = Depends(get_current_identity),
):
    """Card publicado vigente - Modo Treino/briefing de qualquer usuario."""
    try:
        card = svc.published_card(exercise_id)
    except svc.ExerciseCardError as exc:
        raise _http_error(exc) from None
    return {"exercise_id": int(exercise_id), "exercise_card": card}

"""CRUD de eventos de contexto (`/context-events`, Novo Dashboard F2.1 -
plano §10 "api-core: entrada", §14 migracao 048).

Vive na api-core, e nao na engines, pela mesma razao da 047: e ENTRADA DE
DADO DO USUARIO, nao motor deterministico. A engines so LE a tabela
(`intelligence/dashboard/context_service.py`), e por isso nenhum path novo
aparece em `/analytics/*` para este CRUD - o catch-all da api-core ja cobre
`/context-events` (nada a acrescentar em `infra/api_gateway.tf` nem em
`nginx/local-dev.conf`; o teste de paridade so guarda os paths da engines).

Regras:
- `user_id` vem SEMPRE da identidade (ownership em toda query; nunca do
  payload) - o usuario B nao le, nao altera nem apaga o evento do A (IDOR
  testado em `tests/integration/dataapi/test_context_events_crud.py`).
- POST e INSERT, nao upsert: dois eventos do mesmo tipo no mesmo dia sao
  legitimos (duas dores diferentes, dois voos). Ao contrario da 047, onde
  "cintura do dia" e unica por definicao.
- Escrita so aceita `source='manual'`. Os derivados (`plan_change`,
  `gym_change` vindos de `workout_plan_template_assignments` e
  `activities.gym_id`) sao calculados NA LEITURA pela engines e nao existem
  como linha - ver `context_service`. Gravar um derivado por aqui criaria
  dupla verdade e duplicaria a cada execucao.
- Validacao: `event_type` no vocabulario da 048, `intensity` em 1..5 ou
  ausente (nunca 0), `end_local_date >= local_date`.
- Janela de deploy (tabela ausente): leitura devolve vazio com limitacao
  declarada; escrita responde 503 citando a 048. Deteccao em
  `app/context_events_schema.py` (cache so do positivo).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .context_events_schema import (
    EVENT_TYPES,
    INTENSITY_MAX,
    INTENSITY_MIN,
    MIGRATION,
    TABLE,
    TABLE_MISSING_LIMITATION,
    event_type_label,
    has_context_events_table,
    normalize_event_type,
)
from .db import get_conn
from .db_helpers import connect_or_503
from .security import Identity, get_current_identity

router = APIRouter(prefix="/context-events", tags=["context-events"])

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

MANUAL_SOURCE = "manual"

_COLUMNS = (
    "id, event_type, local_date, end_local_date, intensity, notes, source, created_at, updated_at"
)


def normalize_intensity(value: Optional[int]) -> Optional[int]:
    """1..5, ou `None`. Zero NAO e aceito: "sem gradacao" e ausencia, nao o
    menor grau possivel - a mesma regra de `null != 0` do resto do hub."""
    if value is None:
        return None
    number = int(value)
    if number < INTENSITY_MIN or number > INTENSITY_MAX:
        raise ValueError(f"intensity deve estar entre {INTENSITY_MIN} e {INTENSITY_MAX} (ou ausente)")
    return number


class ContextEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    local_date: date
    end_local_date: Optional[date] = None
    intensity: Optional[int] = None
    notes: Optional[str] = Field(default=None, max_length=255)

    @field_validator("event_type")
    @classmethod
    def _type_in_vocabulary(cls, value: str) -> str:
        return normalize_event_type(value)

    @field_validator("intensity")
    @classmethod
    def _intensity_in_range(cls, value: Optional[int]) -> Optional[int]:
        return normalize_intensity(value)

    @model_validator(mode="after")
    def _end_after_start(self) -> "ContextEventIn":
        if self.end_local_date is not None and self.end_local_date < self.local_date:
            raise ValueError("end_local_date não pode ser antes de local_date")
        return self


class ContextEventPatch(BaseModel):
    """PUT parcial: so os campos enviados mudam. `end_local_date`,
    `intensity` e `notes` aceitam `null` explicito para APAGAR o valor - os
    tres sao opcionais por natureza."""

    model_config = ConfigDict(extra="forbid")

    event_type: Optional[str] = None
    local_date: Optional[date] = None
    end_local_date: Optional[date] = None
    intensity: Optional[int] = None
    notes: Optional[str] = Field(default=None, max_length=255)

    @field_validator("event_type")
    @classmethod
    def _type_in_vocabulary(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else normalize_event_type(value)

    @field_validator("intensity")
    @classmethod
    def _intensity_in_range(cls, value: Optional[int]) -> Optional[int]:
        return normalize_intensity(value)


def _connect():
    return connect_or_503(get_conn)


def _table_missing() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "context_events_table_missing",
            "migration": MIGRATION,
            "message": TABLE_MISSING_LIMITATION,
        },
    )


def _public(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    intensity = out.get("intensity")
    out["intensity"] = None if intensity is None else int(intensity)
    out["event_type_label"] = event_type_label(out.get("event_type"))
    out["derived"] = str(out.get("source")) != MANUAL_SOURCE
    return out


def _fetch_by_id(cur: Any, entry_id: int, user_id: int) -> Optional[dict[str, Any]]:
    cur.execute(f"SELECT {_COLUMNS} FROM {TABLE} WHERE id = %s AND user_id = %s", (entry_id, user_id))
    return cur.fetchone()


@router.get("")
def list_context_events(
    event_type: Optional[str] = Query(default=None),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    identity: Identity = Depends(get_current_identity),
):
    """Eventos do proprio usuario, mais recentes primeiro. Tabela ausente
    (antes da 048) -> `items: []` com a limitacao declarada, nunca 500.

    O filtro por data compara com o INTERVALO do evento (`local_date` ..
    `end_local_date`): uma viagem de 10 dias aparece em qualquer janela que
    toque qualquer um desses dias, nao so no dia em que comecou."""
    if event_type is not None:
        event_type = event_type.strip().lower()
        if event_type not in EVENT_TYPES:
            raise HTTPException(status_code=422, detail=f"event_type inválido: {event_type!r}")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from não pode ser depois de date_to")

    where = ["user_id = %s"]
    params: list[Any] = [identity.user_id]
    if event_type:
        where.append("event_type = %s")
        params.append(event_type)
    if date_from:
        where.append("COALESCE(end_local_date, local_date) >= %s")
        params.append(date_from)
    if date_to:
        where.append("local_date <= %s")
        params.append(date_to)

    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_context_events_table(cur):
                return {
                    "items": [],
                    "limit": limit,
                    "offset": offset,
                    "has_more": False,
                    "limitations": [TABLE_MISSING_LIMITATION],
                }
            cur.execute(
                f"SELECT {_COLUMNS} FROM {TABLE} WHERE {' AND '.join(where)} "
                "ORDER BY local_date DESC, id DESC LIMIT %s OFFSET %s",
                (*params, limit + 1, offset),
            )
            rows = cur.fetchall() or []
    has_more = len(rows) > limit
    return {
        "items": [_public(row) for row in rows[:limit]],
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
        "limitations": [],
    }


@router.post("")
def create_context_event(
    payload: ContextEventIn,
    identity: Identity = Depends(get_current_identity),
):
    """INSERT (nunca upsert): o mesmo tipo no mesmo dia pode acontecer duas
    vezes e as duas sao verdade."""
    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_context_events_table(cur):
                raise _table_missing()
            cur.execute(
                f"INSERT INTO {TABLE} "
                "(user_id, event_type, local_date, end_local_date, intensity, notes, source) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    identity.user_id,
                    payload.event_type,
                    payload.local_date,
                    payload.end_local_date,
                    payload.intensity,
                    payload.notes,
                    MANUAL_SOURCE,
                ),
            )
            cur.execute("SELECT LAST_INSERT_ID() AS id")
            new_id = int((cur.fetchone() or {}).get("id") or 0)
            row = _fetch_by_id(cur, new_id, identity.user_id) if new_id else None
    if row is None:  # pragma: no cover - defesa: o INSERT acabou de escrever
        raise HTTPException(status_code=500, detail="Evento não encontrado após gravar.")
    return {"status": "created", "item": _public(row)}


@router.get("/{entry_id}")
def get_context_event(
    entry_id: int,
    identity: Identity = Depends(get_current_identity),
):
    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_context_events_table(cur):
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
            row = _fetch_by_id(cur, entry_id, identity.user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Registro não encontrado.")
    return _public(row)


@router.put("/{entry_id}")
def update_context_event(
    entry_id: int,
    payload: ContextEventPatch,
    identity: Identity = Depends(get_current_identity),
):
    updates = dict(payload.model_dump(exclude_unset=True))
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo válido para atualizar.")
    for required in ("event_type", "local_date"):
        if required in updates and updates[required] is None:
            raise HTTPException(status_code=422, detail=f"{required} não pode ser nulo.")

    set_clause = ", ".join(f"{column} = %s" for column in updates)
    values = [*updates.values(), entry_id, identity.user_id]

    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_context_events_table(cur):
                raise _table_missing()
            current = _fetch_by_id(cur, entry_id, identity.user_id)
            if current is None:
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
            # Coerencia do INTERVALO com o estado FINAL da linha (o PUT e
            # parcial: mudar so o fim tem que ser checado contra o inicio que
            # ja esta gravado).
            final_start = updates.get("local_date", current.get("local_date"))
            final_end = updates.get("end_local_date", current.get("end_local_date"))
            if final_end is not None and final_start is not None and final_end < final_start:
                raise HTTPException(
                    status_code=422, detail="end_local_date não pode ser antes de local_date."
                )
            cur.execute(f"UPDATE {TABLE} SET {set_clause} WHERE id = %s AND user_id = %s", values)
            row = _fetch_by_id(cur, entry_id, identity.user_id)
    return {"status": "ok", "item": _public(row) if row else None}


@router.delete("/{entry_id}")
def delete_context_event(
    entry_id: int,
    identity: Identity = Depends(get_current_identity),
):
    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_context_events_table(cur):
                raise _table_missing()
            cur.execute(f"DELETE FROM {TABLE} WHERE id = %s AND user_id = %s", (entry_id, identity.user_id))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
    return {"status": "deleted", "id": entry_id}


__all__ = [
    "ContextEventIn",
    "ContextEventPatch",
    "normalize_intensity",
    "router",
]

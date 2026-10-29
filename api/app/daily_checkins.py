"""CRUD do check-in diario (`/daily-checkins`, Novo Dashboard F2.2 - plano
§10 "api-core: entrada", §14 migracao 050).

Vive na api-core, e nao na engines, pela mesma razao da 047/048: e ENTRADA DE
DADO DO USUARIO, nao motor deterministico. A engines so LE a tabela
(`intelligence/dashboard/checkin_service.py`), e por isso nenhum path novo
aparece em `/analytics/*` para este CRUD - o catch-all da api-core ja cobre
`/daily-checkins` (nada a acrescentar em `infra/api_gateway.tf` nem em
`nginx/local-dev.conf`; o teste de paridade so guarda os paths da engines).

Regras:
- `user_id` vem SEMPRE da identidade (ownership em toda query; nunca do
  payload) - o usuario B nao le, nao altera nem apaga o check-in do A (IDOR
  testado em `tests/integration/dataapi/test_daily_checkins_crud.py`).
- POST e UPSERT por `(user_id, local_date)`: "como voce esta hoje" tem UMA
  resposta. Responder de novo CORRIGE, nao acrescenta - ao contrario da 048,
  onde dois eventos do mesmo tipo no mesmo dia sao dois fatos.
- Escore ausente permanece ausente. `energy=None` e "nao respondido", nunca
  0 nem 3: o `recovery_signals_service` ignora escore ausente em vez de
  inventar um valor neutro. Por isso o PUT e PARCIAL e aceita `null`
  EXPLICITO para APAGAR um escore ja gravado, e o UPSERT do POST so escreve
  as colunas enviadas (um POST que manda so `soreness` nao apaga o `energy`
  que o usuario respondeu de manha).
- Validacao: cada escore em 1..5 ou ausente (nunca 0); `notes` <= 255.
- Janela de deploy (tabela ausente): leitura devolve vazio com limitacao
  declarada; escrita responde 503 citando a 050. Deteccao em
  `app/daily_checkins_schema.py` (cache so do positivo).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .daily_checkins_schema import (
    MIGRATION,
    NO_CHECKIN_LIMITATION,
    SCORE_FIELDS,
    SCORE_LABELS,
    SCORE_MAX,
    SCORE_MIN,
    TABLE,
    TABLE_MISSING_LIMITATION,
    has_daily_checkins_table,
    normalize_score,
)
from .db import get_conn
from .db_helpers import connect_or_503
from .security import Identity, get_current_identity

router = APIRouter(prefix="/daily-checkins", tags=["daily-checkins"])

DEFAULT_LIMIT = 90
MAX_LIMIT = 365

_COLUMNS = "id, local_date, " + ", ".join(SCORE_FIELDS) + ", notes, created_at, updated_at"


class DailyCheckinIn(BaseModel):
    """POST: upsert do dia. Todo escore e opcional - check-in parcial e
    legitimo -, mas ao menos UM campo tem de vir: um POST vazio criaria uma
    linha que nao diz nada e ocuparia o UNIQUE do dia."""

    model_config = ConfigDict(extra="forbid")

    local_date: date
    energy: Optional[int] = None
    soreness: Optional[int] = None
    stress: Optional[int] = None
    mood: Optional[int] = None
    notes: Optional[str] = Field(default=None, max_length=255)

    @field_validator(*SCORE_FIELDS)
    @classmethod
    def _score_in_range(cls, value: Optional[int], info) -> Optional[int]:
        return normalize_score(value, field=info.field_name)

    @model_validator(mode="after")
    def _at_least_one_answer(self) -> "DailyCheckinIn":
        answered = [name for name in SCORE_FIELDS if getattr(self, name) is not None]
        if not answered and not (self.notes or "").strip():
            raise ValueError(
                "Check-in vazio: responda ao menos um entre "
                + ", ".join(SCORE_FIELDS)
                + " (ou escreva uma nota)."
            )
        return self


class DailyCheckinPatch(BaseModel):
    """PUT parcial: so os campos enviados mudam. Todos aceitam `null`
    explicito para APAGAR o valor (um escore respondido por engano deve poder
    voltar a "nao respondido", nao virar 0)."""

    model_config = ConfigDict(extra="forbid")

    energy: Optional[int] = None
    soreness: Optional[int] = None
    stress: Optional[int] = None
    mood: Optional[int] = None
    notes: Optional[str] = Field(default=None, max_length=255)

    @field_validator(*SCORE_FIELDS)
    @classmethod
    def _score_in_range(cls, value: Optional[int], info) -> Optional[int]:
        return normalize_score(value, field=info.field_name)


def _connect():
    return connect_or_503(get_conn)


def _table_missing() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "daily_checkins_table_missing",
            "migration": MIGRATION,
            "message": TABLE_MISSING_LIMITATION,
        },
    )


def _public(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for name in SCORE_FIELDS:
        value = out.get(name)
        out[name] = None if value is None else int(value)
    out["answered"] = [name for name in SCORE_FIELDS if out.get(name) is not None]
    out["score_labels"] = dict(SCORE_LABELS)
    return out


def _fetch_by_id(cur: Any, entry_id: int, user_id: int) -> Optional[dict[str, Any]]:
    cur.execute(f"SELECT {_COLUMNS} FROM {TABLE} WHERE id = %s AND user_id = %s", (entry_id, user_id))
    return cur.fetchone()


def _fetch_by_date(cur: Any, user_id: int, local_date: date) -> Optional[dict[str, Any]]:
    cur.execute(
        f"SELECT {_COLUMNS} FROM {TABLE} WHERE user_id = %s AND local_date = %s",
        (user_id, local_date),
    )
    return cur.fetchone()


@router.get("")
def list_daily_checkins(
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    identity: Identity = Depends(get_current_identity),
):
    """Check-ins do proprio usuario, mais recentes primeiro. Tabela ausente
    (antes da 050) -> `items: []` com a limitacao declarada, nunca 500."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from não pode ser depois de date_to")

    where = ["user_id = %s"]
    params: list[Any] = [identity.user_id]
    if date_from:
        where.append("local_date >= %s")
        params.append(date_from)
    if date_to:
        where.append("local_date <= %s")
        params.append(date_to)

    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_daily_checkins_table(cur):
                return {
                    "items": [],
                    "limit": limit,
                    "offset": offset,
                    "has_more": False,
                    "table_available": False,
                    "limitations": [TABLE_MISSING_LIMITATION],
                }
            cur.execute(
                f"SELECT {_COLUMNS} FROM {TABLE} WHERE {' AND '.join(where)} "
                "ORDER BY local_date DESC LIMIT %s OFFSET %s",
                (*params, limit + 1, offset),
            )
            rows = cur.fetchall() or []
    has_more = len(rows) > limit
    return {
        "items": [_public(row) for row in rows[:limit]],
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
        "table_available": True,
        "limitations": [] if rows else [NO_CHECKIN_LIMITATION],
    }


@router.post("")
def create_daily_checkin(
    payload: DailyCheckinIn,
    identity: Identity = Depends(get_current_identity),
):
    """UPSERT por `(user_id, local_date)`: repetir o check-in do dia CORRIGE.

    So as colunas ENVIADAS entram no `ON DUPLICATE KEY UPDATE` - um POST que
    manda so `soreness` a tarde nao apaga o `energy` respondido de manha. Para
    APAGAR um escore existe o PUT com `null` explicito."""
    sent = payload.model_dump(exclude_unset=True)
    columns = [name for name in (*SCORE_FIELDS, "notes") if name in sent]
    insert_columns = ["user_id", "local_date", *columns]
    values = [identity.user_id, payload.local_date, *(sent[name] for name in columns)]
    placeholders = ", ".join(["%s"] * len(insert_columns))
    updates = ", ".join(f"{name} = VALUES({name})" for name in columns)

    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_daily_checkins_table(cur):
                raise _table_missing()
            existing = _fetch_by_date(cur, identity.user_id, payload.local_date)
            cur.execute(
                f"INSERT INTO {TABLE} ({', '.join(insert_columns)}) VALUES ({placeholders}) "
                f"ON DUPLICATE KEY UPDATE {updates}",
                tuple(values),
            )
            row = _fetch_by_date(cur, identity.user_id, payload.local_date)
    if row is None:  # pragma: no cover - defesa: o upsert acabou de escrever
        raise HTTPException(status_code=500, detail="Check-in não encontrado após gravar.")
    return {"status": "updated" if existing else "created", "item": _public(row)}


@router.get("/{entry_id}")
def get_daily_checkin(
    entry_id: int,
    identity: Identity = Depends(get_current_identity),
):
    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_daily_checkins_table(cur):
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
            row = _fetch_by_id(cur, entry_id, identity.user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Registro não encontrado.")
    return _public(row)


@router.put("/{entry_id}")
def update_daily_checkin(
    entry_id: int,
    payload: DailyCheckinPatch,
    identity: Identity = Depends(get_current_identity),
):
    """PUT parcial. `local_date` NAO e editavel: mover um check-in de dia
    seria afirmar que a pessoa se sentia assim em outro dia. Errou a data?
    Apague e registre no dia certo."""
    updates = dict(payload.model_dump(exclude_unset=True))
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo válido para atualizar.")

    set_clause = ", ".join(f"{column} = %s" for column in updates)
    values = [*updates.values(), entry_id, identity.user_id]

    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_daily_checkins_table(cur):
                raise _table_missing()
            if _fetch_by_id(cur, entry_id, identity.user_id) is None:
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
            cur.execute(f"UPDATE {TABLE} SET {set_clause} WHERE id = %s AND user_id = %s", values)
            row = _fetch_by_id(cur, entry_id, identity.user_id)
    return {"status": "ok", "item": _public(row) if row else None}


@router.delete("/{entry_id}")
def delete_daily_checkin(
    entry_id: int,
    identity: Identity = Depends(get_current_identity),
):
    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_daily_checkins_table(cur):
                raise _table_missing()
            cur.execute(f"DELETE FROM {TABLE} WHERE id = %s AND user_id = %s", (entry_id, identity.user_id))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
    return {"status": "deleted", "id": entry_id}


__all__ = [
    "DailyCheckinIn",
    "DailyCheckinPatch",
    "SCORE_MAX",
    "SCORE_MIN",
    "router",
]

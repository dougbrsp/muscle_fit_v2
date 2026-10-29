"""CRUD de medidas corporais por fita metrica (`/body-measurements`,
Novo Dashboard F1.4 - plano §10 "api-core: entrada", §14 migracao 047).

Vive na api-core (mesma Lambda do CRUD de bioimpedancia,
`app/body_composition.py`, cujo padrao este modulo segue). A engines so LE a
tabela (`intelligence/dashboard/body_service.py`).

Regras:
- `user_id` vem SEMPRE da identidade (ownership em toda query; nunca do
  payload) - o usuario B nao le, nao altera nem apaga a medida do A (IDOR
  testado em `tests/integration/dataapi/test_body_measurements_crud.py`).
- POST e um UPSERT por `(user_id, local_date, site)`: registrar a cintura do
  mesmo dia duas vezes corrige a primeira, nao duplica.
- Validacao: `site` no vocabulario de `domain/body_measurement_sites.py` (047 + 075), `value_cm` entre 10 e 300,
  `local_date` entre `LOCAL_DATE_MIN` e hoje (UTC) + `LOCAL_DATE_MAX_AHEAD_DAYS`,
  `protocol_ok` booleano.
- Janela de deploy (tabela ausente): leitura devolve vazio com limitacao
  declarada; escrita responde 503 citando a 047. Deteccao em
  `app/body_measurements_schema.py` (cache so do positivo).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .body_measurements_schema import (
    MIGRATION,
    TABLE,
    TABLE_MISSING_LIMITATION,
    has_body_measurements_table,
)
from .db import get_conn
from .domain import body_measurement_sites as _sites
from .db_helpers import connect_or_503
from .security import Identity, get_current_identity

router = APIRouter(prefix="/body-measurements", tags=["body-measurements"])

# Vocabulario e rotulos dos locais: dono unico em `domain/body_measurement_sites.py`
# (ENUM das migracoes 047 + 075; o teste de plano da 075 compara os dois lados).
MEASUREMENT_SITES = _sites.MEASUREMENT_SITES
MEASUREMENT_SITE_LABELS = _sites.SITE_LABELS

VALUE_CM_MIN = Decimal("10.0")
VALUE_CM_MAX = Decimal("300.0")

# Janela de datas aceitas. Piso fixo: nada antes de 2000 e medida real. Teto:
# hoje + 1 dia. O modulo nao conhece o fuso do usuario (validador do modelo, sem
# identidade), entao "hoje" e o de UTC; o dia de folga cobre quem ja virou o dia
# a leste de UTC. O frontend espelha so como UX (`dashboard-body.ts`).
LOCAL_DATE_MIN = date(2000, 1, 1)
LOCAL_DATE_MAX_AHEAD_DAYS = 1

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

_COLUMNS = "id, local_date, site, value_cm, protocol_ok, notes, source, created_at, updated_at"


def normalize_site(value: str) -> str:
    """`site` no vocabulario do dono (047 + 075; minusculo, sem espacos) ou `ValueError`."""
    normalized = str(value).strip().lower()
    if normalized not in MEASUREMENT_SITES:
        raise ValueError(f"site inválido: {value!r}; use um de {', '.join(MEASUREMENT_SITES)}")
    return normalized


def normalize_value_cm(value: Decimal) -> Decimal:
    """`value_cm` em [10, 300], arredondado a 0,1 cm (DECIMAL(5,1))."""
    if value < VALUE_CM_MIN or value > VALUE_CM_MAX:
        raise ValueError(f"value_cm deve estar entre {VALUE_CM_MIN} e {VALUE_CM_MAX} cm")
    return value.quantize(Decimal("0.1"))


def local_date_max(today: Optional[date] = None) -> date:
    """Ultima data aceita: hoje (UTC) + `LOCAL_DATE_MAX_AHEAD_DAYS`."""
    base = today if today is not None else datetime.now(timezone.utc).date()
    return base + timedelta(days=LOCAL_DATE_MAX_AHEAD_DAYS)


def normalize_local_date(value: date, *, today: Optional[date] = None) -> date:
    """`local_date` em [`LOCAL_DATE_MIN`, hoje + 1 dia] ou `ValueError` (422)."""
    upper = local_date_max(today)
    if value < LOCAL_DATE_MIN or value > upper:
        raise ValueError(
            f"local_date deve estar entre {LOCAL_DATE_MIN.isoformat()} e {upper.isoformat()}"
        )
    return value


class BodyMeasurementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_date: date
    site: str
    value_cm: Decimal
    protocol_ok: bool = True
    notes: Optional[str] = Field(default=None, max_length=255)
    source: str = Field(default="manual", max_length=40)

    @field_validator("local_date")
    @classmethod
    def _local_date_in_range(cls, value: date) -> date:
        return normalize_local_date(value)

    @field_validator("site")
    @classmethod
    def _site_in_vocabulary(cls, value: str) -> str:
        return normalize_site(value)

    @field_validator("value_cm")
    @classmethod
    def _value_in_range(cls, value: Decimal) -> Decimal:
        return normalize_value_cm(value)


class BodyMeasurementPatch(BaseModel):
    """PUT parcial: so os campos enviados mudam."""

    model_config = ConfigDict(extra="forbid")

    local_date: Optional[date] = None
    site: Optional[str] = None
    value_cm: Optional[Decimal] = None
    protocol_ok: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=255)
    source: Optional[str] = Field(default=None, max_length=40)

    @field_validator("local_date")
    @classmethod
    def _local_date_in_range(cls, value: Optional[date]) -> Optional[date]:
        return None if value is None else normalize_local_date(value)

    @field_validator("site")
    @classmethod
    def _site_in_vocabulary(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else normalize_site(value)

    @field_validator("value_cm")
    @classmethod
    def _value_in_range(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        return None if value is None else normalize_value_cm(value)


def _connect():
    return connect_or_503(get_conn)


def _table_missing() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "body_measurements_table_missing",
            "migration": MIGRATION,
            "message": TABLE_MISSING_LIMITATION,
        },
    )


def _public(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["value_cm"] = float(out["value_cm"]) if out.get("value_cm") is not None else None
    out["protocol_ok"] = bool(int(out.get("protocol_ok") or 0))
    out["site_label"] = MEASUREMENT_SITE_LABELS.get(str(out.get("site")), str(out.get("site")))
    return out


def _fetch_by_id(cur: Any, entry_id: int, user_id: int) -> Optional[dict[str, Any]]:
    cur.execute(f"SELECT {_COLUMNS} FROM {TABLE} WHERE id = %s AND user_id = %s", (entry_id, user_id))
    return cur.fetchone()


def _fetch_by_key(cur: Any, user_id: int, local_date: date, site: str) -> Optional[dict[str, Any]]:
    cur.execute(
        f"SELECT {_COLUMNS} FROM {TABLE} WHERE user_id = %s AND local_date = %s AND site = %s",
        (user_id, local_date, site),
    )
    return cur.fetchone()


@router.get("")
def list_body_measurements(
    site: Optional[str] = Query(default=None),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    identity: Identity = Depends(get_current_identity),
):
    """Medidas do proprio usuario, mais recentes primeiro. Tabela ausente
    (antes da 047) -> `items: []` com a limitacao declarada, nunca 500."""
    if site is not None:
        site = site.strip().lower()
        if site not in MEASUREMENT_SITES:
            raise HTTPException(status_code=422, detail=f"site inválido: {site!r}")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from não pode ser depois de date_to")

    where = ["user_id = %s"]
    params: list[Any] = [identity.user_id]
    if site:
        where.append("site = %s")
        params.append(site)
    if date_from:
        where.append("local_date >= %s")
        params.append(date_from)
    if date_to:
        where.append("local_date <= %s")
        params.append(date_to)

    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_body_measurements_table(cur):
                return {
                    "items": [],
                    "limit": limit,
                    "offset": offset,
                    "has_more": False,
                    "limitations": [TABLE_MISSING_LIMITATION],
                }
            cur.execute(
                f"SELECT {_COLUMNS} FROM {TABLE} WHERE {' AND '.join(where)} "
                "ORDER BY local_date DESC, site ASC, id DESC LIMIT %s OFFSET %s",
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
def create_or_update_body_measurement(
    payload: BodyMeasurementIn,
    identity: Identity = Depends(get_current_identity),
):
    """Upsert por `(user_id, local_date, site)`."""
    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_body_measurements_table(cur):
                raise _table_missing()
            existing = _fetch_by_key(cur, identity.user_id, payload.local_date, payload.site)
            cur.execute(
                f"INSERT INTO {TABLE} (user_id, local_date, site, value_cm, protocol_ok, notes, source) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE value_cm = VALUES(value_cm), protocol_ok = VALUES(protocol_ok), "
                "notes = VALUES(notes), source = VALUES(source)",
                (
                    identity.user_id,
                    payload.local_date,
                    payload.site,
                    str(payload.value_cm),
                    1 if payload.protocol_ok else 0,
                    payload.notes,
                    payload.source,
                ),
            )
            row = _fetch_by_key(cur, identity.user_id, payload.local_date, payload.site)
    if row is None:  # pragma: no cover - defesa: o upsert acabou de escrever
        raise HTTPException(status_code=500, detail="Medida não encontrada após gravar.")
    return {"status": "updated" if existing else "created", "item": _public(row)}


@router.get("/{entry_id}")
def get_body_measurement(
    entry_id: int,
    identity: Identity = Depends(get_current_identity),
):
    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_body_measurements_table(cur):
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
            row = _fetch_by_id(cur, entry_id, identity.user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Registro não encontrado.")
    return _public(row)


@router.put("/{entry_id}")
def update_body_measurement(
    entry_id: int,
    payload: BodyMeasurementPatch,
    identity: Identity = Depends(get_current_identity),
):
    updates = {key: value for key, value in payload.model_dump(exclude_unset=True).items()}
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo válido para atualizar.")
    if "value_cm" in updates and updates["value_cm"] is not None:
        updates["value_cm"] = str(updates["value_cm"])
    if "protocol_ok" in updates and updates["protocol_ok"] is not None:
        updates["protocol_ok"] = 1 if updates["protocol_ok"] else 0
    for required in ("local_date", "site", "value_cm", "protocol_ok", "source"):
        if required in updates and updates[required] is None:
            raise HTTPException(status_code=422, detail=f"{required} não pode ser nulo.")

    set_clause = ", ".join(f"{column} = %s" for column in updates)
    values = [*updates.values(), entry_id, identity.user_id]

    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_body_measurements_table(cur):
                raise _table_missing()
            if _fetch_by_id(cur, entry_id, identity.user_id) is None:
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
            try:
                cur.execute(f"UPDATE {TABLE} SET {set_clause} WHERE id = %s AND user_id = %s", values)
            except Exception as exc:  # noqa: BLE001 - colisao com o UNIQUE vira 409, nao 500
                if "Duplicate" in str(exc) or "1062" in str(exc):
                    raise HTTPException(
                        status_code=409,
                        detail="Já existe uma medida deste local nesta data; edite aquela ou apague-a.",
                    ) from exc
                raise
            row = _fetch_by_id(cur, entry_id, identity.user_id)
    return {"status": "ok", "item": _public(row) if row else None}


@router.delete("/{entry_id}")
def delete_body_measurement(
    entry_id: int,
    identity: Identity = Depends(get_current_identity),
):
    with _connect() as conn:
        with conn.cursor() as cur:
            if not has_body_measurements_table(cur):
                raise _table_missing()
            cur.execute(f"DELETE FROM {TABLE} WHERE id = %s AND user_id = %s", (entry_id, identity.user_id))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
    return {"status": "deleted", "id": entry_id}


__all__ = [
    "MEASUREMENT_SITES",
    "MEASUREMENT_SITE_LABELS",
    "BodyMeasurementIn",
    "BodyMeasurementPatch",
    "VALUE_CM_MAX",
    "VALUE_CM_MIN",
    "normalize_site",
    "normalize_value_cm",
    "router",
]

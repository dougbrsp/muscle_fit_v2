"""Cadastro administrativo das capacidades de seguranca (PR-07 da refatoracao
dos motores; PLANO_20260912.md secao 9).

    GET/PUT /equipment/{equipment_id}/safety            default do EQUIPAMENTO
    GET/PUT /equipment-exercises/{link_id}/safety       override do PAR

Paths top-level: caem no catch-all `{proxy+}` da api-core (sem Terraform).
Autorizacao: GET e PUT restritos a admin, mesmo criterio das rotas de escrita
do catalogo global de equipamentos (`gyms.py`, `equipment_exercises.py`).

Contrato de escrita (PUT), igual nas duas rotas:
- campo AUSENTE do corpo -> nao e tocado; `null` explicito -> limpa (volta a
  "desconhecido"/"herda");
- `techniques`: objeto `{code: true | false | null}`. `true`/`false` grava a
  marcacao (linha com `is_compatible` 1/0); `null` apaga a linha ("sem
  marcacao": no equipamento = incompativel por D3; no par = herda o default).
  Codigo fora do objeto nao e tocado. Codigos com `true`/`false` sao validados
  contra o catalogo ativo (`advanced_techniques.ensure_valid_codes`).

Sem DDL em runtime: colunas e tabelas vem das migracoes 032 e 033.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .advanced_techniques import ensure_valid_codes
from .db import transaction
from .domain import training_safety as ts
from .security import Identity, require_admin

router = APIRouter(tags=["training-safety"], dependencies=[Depends(require_admin)])

SAFETY_NOTES_MAX_LENGTH = 2000
TECHNIQUE_CODE_MAX_LENGTH = 20  # advanced_techniques.code VARCHAR(20)
LOAD_INCREMENT_MAX_KG = Decimal("999.99")  # DECIMAL(5,2)


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


class _SafetyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safety_notes: Optional[str] = Field(default=None, max_length=SAFETY_NOTES_MAX_LENGTH)
    techniques: Optional[Dict[str, Optional[bool]]] = None

    @field_validator("safety_notes")
    @classmethod
    def _blank_notes_is_null(cls, value: Optional[str]) -> Optional[str]:
        return (value or "").strip() or None

    @field_validator("techniques")
    @classmethod
    def _technique_codes(cls, value):
        for code in value or {}:
            if not code or len(code) > TECHNIQUE_CODE_MAX_LENGTH:
                raise ValueError(f"código de técnica inválido: {code!r}")
        return value


class EquipmentSafetyIn(_SafetyIn):
    max_safe_rpe: Optional[int] = Field(default=None, ge=ts.MIN_SAFE_RPE, le=ts.MAX_SAFE_RPE, strict=True)
    # Menor incremento de carga TOTAL (D12; halteres: soma dos dois).
    load_increment_kg: Optional[Decimal] = Field(default=None, gt=0, le=LOAD_INCREMENT_MAX_KG, decimal_places=2)


class EquipmentExerciseSafetyIn(_SafetyIn):
    max_safe_rpe_override: Optional[int] = Field(default=None, ge=ts.MIN_SAFE_RPE, le=ts.MAX_SAFE_RPE, strict=True)


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def _marks(cur: Any, table: str, owner_column: str, owner_id: int) -> dict[str, bool]:
    cur.execute(
        f"SELECT technique_code, is_compatible FROM {table} WHERE {owner_column} = %s ORDER BY technique_code",
        (owner_id,),
    )
    return {row["technique_code"]: bool(int(row["is_compatible"] or 0)) for row in cur.fetchall() or []}


def _to_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def _to_int(value: Any) -> Optional[int]:
    return None if value is None else int(value)


def _catalog_requirements(cur: Any) -> dict[str, str]:
    cur.execute("SELECT code, failure_requirement FROM advanced_techniques WHERE is_active = 1")
    return {row["code"]: row["failure_requirement"] for row in cur.fetchall() or []}


def read_equipment_safety(conn: Any, equipment_id: int) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, equipment_type_id, max_safe_rpe, load_increment_kg, safety_notes FROM equipment WHERE id = %s",
            (equipment_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Equipamento não encontrado.")
        marks = _marks(cur, "equipment_technique_compatibility", "equipment_id", equipment_id)
        requirements = _catalog_requirements(cur)
    resolved = ts.build_training_safety(
        equipment_id=equipment_id,
        equipment_max_safe_rpe=row["max_safe_rpe"],
        equipment_techniques=marks,
        technique_failure_requirements=requirements,
    )
    return {
        "equipment_id": int(row["id"]),
        "equipment_name": row["name"],
        "equipment_type_id": _to_int(row["equipment_type_id"]),
        "max_safe_rpe": _to_int(row["max_safe_rpe"]),
        "load_increment_kg": _to_float(row["load_increment_kg"]),
        "safety_notes": row["safety_notes"],
        "techniques": marks,
        # o que o resolvedor devolve para este equipamento sem override de par
        "effective": {
            "max_safe_rpe": resolved.max_safe_rpe,
            "failure_allowed": resolved.failure_allowed,
            "safety_source": resolved.safety_source,
            "allowed_advanced_techniques": [item.to_dict() for item in resolved.allowed_techniques],
            "blocked_advanced_techniques": [item.to_dict() for item in resolved.blocked_techniques],
        },
    }


def read_equipment_exercise_safety(conn: Any, link_id: int) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT eqex.id, eqex.equipment_id, eqex.exercise_id, eqex.is_active,
                   eqex.max_safe_rpe_override, eqex.safety_notes,
                   eq.name AS equipment_name, eq.max_safe_rpe AS equipment_max_safe_rpe,
                   ec.name AS exercise_name
            FROM equipment_exercises eqex
            JOIN equipment eq ON eq.id = eqex.equipment_id
            JOIN exercise_catalog ec ON ec.id = eqex.exercise_id
            WHERE eqex.id = %s
            """,
            (link_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Vínculo equipamento/exercício não encontrado.")
        equipment_marks = _marks(cur, "equipment_technique_compatibility", "equipment_id", int(row["equipment_id"]))
        override_marks = _marks(cur, "equipment_exercise_technique_overrides", "equipment_exercise_id", link_id)
        requirements = _catalog_requirements(cur)
    resolved = ts.build_training_safety(
        exercise_id=int(row["exercise_id"]),
        equipment_id=int(row["equipment_id"]),
        equipment_exercise_id=link_id,
        equipment_max_safe_rpe=row["equipment_max_safe_rpe"],
        override_max_safe_rpe=row["max_safe_rpe_override"],
        equipment_techniques=equipment_marks,
        override_techniques=override_marks,
        technique_failure_requirements=requirements,
    )
    return {
        "equipment_exercise_id": link_id,
        "equipment_id": int(row["equipment_id"]),
        "equipment_name": row["equipment_name"],
        "exercise_id": int(row["exercise_id"]),
        "exercise_name": row["exercise_name"],
        "is_active": bool(int(row["is_active"] or 0)),
        "max_safe_rpe_override": _to_int(row["max_safe_rpe_override"]),
        "safety_notes": row["safety_notes"],
        "techniques": override_marks,
        "inherited": {"max_safe_rpe": _to_int(row["equipment_max_safe_rpe"]), "techniques": equipment_marks},
        "resolved": resolved.to_dict(),
    }


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------


def _update_columns(cur: Any, table: str, row_id: int, payload: BaseModel, columns: tuple[str, ...]) -> None:
    provided = [column for column in columns if column in payload.model_fields_set]
    if not provided:
        return
    assignments = ", ".join(f"{column} = %s" for column in provided)
    cur.execute(
        f"UPDATE {table} SET {assignments} WHERE id = %s",
        (*[getattr(payload, column) for column in provided], row_id),
    )


def _write_marks(
    cur: Any, table: str, owner_column: str, owner_id: int, techniques: Optional[Dict[str, Optional[bool]]], user_id: int
) -> None:
    for code, compatible in sorted((techniques or {}).items()):
        if compatible is None:
            cur.execute(f"DELETE FROM {table} WHERE {owner_column} = %s AND technique_code = %s", (owner_id, code))
            continue
        cur.execute(
            f"""
            INSERT INTO {table} ({owner_column}, technique_code, is_compatible, updated_by_user_id)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              is_compatible = VALUES(is_compatible),
              updated_by_user_id = VALUES(updated_by_user_id)
            """,
            (owner_id, code, 1 if compatible else 0, user_id),
        )


def _ensure_codes(payload: _SafetyIn) -> None:
    ensure_valid_codes(code for code, compatible in (payload.techniques or {}).items() if compatible is not None)


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


@router.get("/equipment/{equipment_id}/safety")
def get_equipment_safety(equipment_id: int):
    with transaction() as conn:
        return read_equipment_safety(conn, equipment_id)


@router.put("/equipment/{equipment_id}/safety")
def put_equipment_safety(equipment_id: int, payload: EquipmentSafetyIn, identity: Identity = Depends(require_admin)):
    _ensure_codes(payload)
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM equipment WHERE id = %s", (equipment_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Equipamento não encontrado.")
            _update_columns(cur, "equipment", equipment_id, payload, ("max_safe_rpe", "load_increment_kg", "safety_notes"))
            _write_marks(
                cur, "equipment_technique_compatibility", "equipment_id", equipment_id, payload.techniques, identity.user_id
            )
        return read_equipment_safety(conn, equipment_id)


@router.get("/equipment-exercises/{link_id}/safety")
def get_equipment_exercise_safety(link_id: int):
    with transaction() as conn:
        return read_equipment_exercise_safety(conn, link_id)


@router.put("/equipment-exercises/{link_id}/safety")
def put_equipment_exercise_safety(
    link_id: int, payload: EquipmentExerciseSafetyIn, identity: Identity = Depends(require_admin)
):
    _ensure_codes(payload)
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM equipment_exercises WHERE id = %s", (link_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Vínculo equipamento/exercício não encontrado.")
            _update_columns(cur, "equipment_exercises", link_id, payload, ("max_safe_rpe_override", "safety_notes"))
            _write_marks(
                cur,
                "equipment_exercise_technique_overrides",
                "equipment_exercise_id",
                link_id,
                payload.techniques,
                identity.user_id,
            )
        return read_equipment_exercise_safety(conn, link_id)

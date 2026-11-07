"""Teto de carga do equipamento por usuario x par exercicio+equipamento
(migracao 110, `user_equipment_load_ceilings`).

O usuario marca no Modo Treino que atingiu a carga maxima do equipamento; os
motores (`domain/next_set_load.py`, `domain/progression_engine.py`) nao
sugerem carga acima dela. Este modulo e SO repositorio + rotas: nenhuma regra
de carga mora aqui (o teto entra nos motores como parametro opcional).

    GET    /me/equipment-load-ceiling?mapping_id=&gym_equipment_id=  -> teto do par (ou null)
    PUT    /me/equipment-load-ceiling   {mapping_id, gym_equipment_id, max_load_kg}
    DELETE /me/equipment-load-ceiling?mapping_id=&gym_equipment_id=  -> idempotente

Autorizacao = usuario corrente, SEMPRE: `user_id` vem da identidade resolvida
pelo token e nao existe em path, query nem corpo (corpo com campo extra = 422).
O par e resolvido NO SERVIDOR (`exercise_legacy_id_map` + `gym_equipment` +
`equipment_exercises`), como a convencao de carga.

Leitura tolerante: tabela ausente (codigo antes da migracao 110) = sem teto
(comportamento anterior). As rotas de escrita respondem 503 ate a migracao.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import request_memo
from .db import transaction
from .load_conventions import equipment_exercises_join_sql
from .security import Identity, get_current_identity

logger = logging.getLogger(__name__)

CEILINGS_TABLE = "user_equipment_load_ceilings"
# Limite da coluna DECIMAL(6,2) da migracao 110 (validacao de entrada, nao regra
# de carga). Um centesimo abaixo do maximo da coluna: o `quantize(0.01)` nunca
# arredonda para fora do DECIMAL(6,2).
MAX_CEILING_COLUMN_KG = 9999.98
CEILINGS_MEMO = "equipment_load_ceilings"

router = APIRouter(prefix="/me/equipment-load-ceiling", tags=["equipment-load-ceiling"])

# O par parte do `gym_equipment` (que e do exercicio `ge.mapping_id`) e confere
# que o `mapping_id` pedido e o dele (cru ou canonico): equipamento de outro
# exercicio nao resolve vinculo. Parametros do WHERE: (mapping_id, mapping_id, ge.id).
_PAIR_JOIN = (
    "FROM gym_equipment ge\n"
    "LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = ge.mapping_id\n"
    + equipment_exercises_join_sql(canonical_expr="COALESCE(elm.canonical_exercise_id, ge.mapping_id)")
)
# Canonico com canonico (como `equipment_pair.is_pair_coherent`): o exercicio do
# `gym_equipment` (id legado ou canonico) e o do `mapping_id` pedido (idem).
_PAIR_WHERE = (
    "COALESCE(elm.canonical_exercise_id, ge.mapping_id) = "
    "COALESCE((SELECT m2.canonical_exercise_id FROM exercise_legacy_id_map m2 WHERE m2.old_exercise_id = %s LIMIT 1), %s) "
    "AND ge.id = %s"
)


class LoadCeilingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # nunca aceitar user_id do cliente

    mapping_id: int = Field(gt=0, strict=True)
    gym_equipment_id: int = Field(gt=0, strict=True)
    max_load_kg: float = Field(gt=0, le=MAX_CEILING_COLUMN_KG)


def is_missing_ceilings_table_error(exc: Exception) -> bool:
    text = str(exc)
    return CEILINGS_TABLE in text and "doesn't exist" in text


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def resolve_pair_link(cursor: Any, mapping_id: int, gym_equipment_id: int) -> Optional[int]:
    """`equipment_exercises.id` do par (exercicio do catalogo + equipamento da
    academia), ou None quando o par nao existe."""
    cursor.execute(
        f"SELECT eqex.id AS link_id\n{_PAIR_JOIN}WHERE {_PAIR_WHERE}",
        (int(mapping_id), int(mapping_id), int(gym_equipment_id)),
    )
    row = cursor.fetchone() or {}
    return int(row["link_id"]) if row.get("link_id") is not None else None


def pair_ceiling(cursor: Any, user_id: int, mapping_id: int, gym_equipment_id: int) -> Optional[Decimal]:
    """Teto do usuario para o par, ou None. Tabela ausente = None."""
    try:
        cursor.execute(
            f"SELECT c.max_load_kg AS max_load_kg\n{_PAIR_JOIN}"
            f"JOIN {CEILINGS_TABLE} c ON c.equipment_exercise_id = eqex.id AND c.user_id = %s\n"
            f"WHERE {_PAIR_WHERE}",
            (int(user_id), int(mapping_id), int(mapping_id), int(gym_equipment_id)),
        )
    except Exception as exc:  # noqa: BLE001 - so o 1146 desta tabela e tolerado
        if not is_missing_ceilings_table_error(exc):
            raise
        logger.warning("%s ausente (migracao 110 pendente): sem teto de carga", CEILINGS_TABLE)
        return None
    row = cursor.fetchone() or {}
    return _decimal(row.get("max_load_kg"))


def load_user_ceilings(conn: Any, user_id: int) -> dict[tuple[int, int], Decimal]:
    """(exercise_id, equipment_id) -> teto do usuario. UM SELECT por chamada,
    qualquer que seja o numero de pares (mesma chave das trilhas). Memo por
    requisicao GET (`prescribe_pairs` e `suggest_for_pairs` leem na mesma
    chamada). So o 1146 desta tabela (migracao 110 pendente) e esperado; qualquer
    outra falha de leitura = {} (sem teto, para nao derrubar a prescricao) com
    `logger.error` registrando que o teto foi IGNORADO."""
    return request_memo.memoize((CEILINGS_MEMO, int(user_id)), lambda: _read_user_ceilings(conn, user_id))


def _read_user_ceilings(conn: Any, user_id: int) -> dict[tuple[int, int], Decimal]:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ee.exercise_id AS exercise_id, ee.equipment_id AS equipment_id, c.max_load_kg AS max_load_kg\n"
                f"FROM {CEILINGS_TABLE} c\n"
                "JOIN equipment_exercises ee ON ee.id = c.equipment_exercise_id\n"
                "WHERE c.user_id = %s",
                (int(user_id),),
            )
            rows = cur.fetchall() or []
    except Exception as exc:  # noqa: BLE001
        if is_missing_ceilings_table_error(exc):
            logger.warning("%s ausente (migracao 110 pendente): sem teto de carga", CEILINGS_TABLE)
        else:
            logger.error("equipment_load_ceiling.load_failed: teto ignorado (sem teto)", exc_info=exc)
        return {}
    result: dict[tuple[int, int], Decimal] = {}
    for row in rows:
        value = _decimal(row.get("max_load_kg"))
        if value is not None:
            result[(int(row["exercise_id"]), int(row["equipment_id"]))] = value
    return result


def _item(max_load_kg: Optional[Decimal]) -> dict[str, Any]:
    return {"max_load_kg": float(max_load_kg) if max_load_kg is not None else None}


def _unavailable(exc: Exception) -> HTTPException:
    logger.error("Tabela indisponivel em %s; respondendo 503", __name__, exc_info=exc)  # SEG-12: texto so no log
    return HTTPException(
        status_code=503,
        detail={
            "error": "equipment_load_ceiling_unavailable",
            "message": "Teto de carga do equipamento indisponível: migração 110 pendente neste ambiente.",
        },
    )


def _link_or_404(cursor: Any, mapping_id: int, gym_equipment_id: int) -> int:
    link_id = resolve_pair_link(cursor, mapping_id, gym_equipment_id)
    if link_id is None:
        raise HTTPException(status_code=404, detail="Vínculo exercício x equipamento não encontrado.")
    return link_id


@router.get("")
def get_equipment_load_ceiling(
    mapping_id: int = Query(gt=0),
    gym_equipment_id: int = Query(gt=0),
    identity: Identity = Depends(get_current_identity),
):
    with transaction() as conn:
        with conn.cursor() as cur:
            ceiling = pair_ceiling(cur, identity.user_id, mapping_id, gym_equipment_id)
    return {"ok": True, **_item(ceiling)}


@router.put("")
def put_equipment_load_ceiling(payload: LoadCeilingIn, identity: Identity = Depends(get_current_identity)):
    value = Decimal(str(payload.max_load_kg)).quantize(Decimal("0.01"))
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                link_id = _link_or_404(cur, payload.mapping_id, payload.gym_equipment_id)
                cur.execute(
                    f"""
                    INSERT INTO {CEILINGS_TABLE} (user_id, equipment_exercise_id, max_load_kg)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE max_load_kg = VALUES(max_load_kg)
                    """,
                    (identity.user_id, link_id, value),
                )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        if is_missing_ceilings_table_error(exc):
            raise _unavailable(exc) from exc
        raise
    return {"ok": True, **_item(value)}


@router.delete("")
def delete_equipment_load_ceiling(
    mapping_id: int = Query(gt=0),
    gym_equipment_id: int = Query(gt=0),
    identity: Identity = Depends(get_current_identity),
):
    """Idempotente: desmarcar o que nao existe devolve `deleted: false`."""
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                link_id = resolve_pair_link(cur, mapping_id, gym_equipment_id)
                deleted = False
                if link_id is not None:
                    cur.execute(
                        f"DELETE FROM {CEILINGS_TABLE} WHERE user_id = %s AND equipment_exercise_id = %s",
                        (identity.user_id, link_id),
                    )
                    deleted = bool(cur.rowcount)
    except Exception as exc:  # noqa: BLE001
        if is_missing_ceilings_table_error(exc):
            raise _unavailable(exc) from exc
        raise
    return {"ok": True, "max_load_kg": None, "deleted": deleted}

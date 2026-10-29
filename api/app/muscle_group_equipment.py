"""Endpoints de leitura de navegacao grupo muscular -> equipamento ->
exercicio, Fase 4 da reestruturacao de dominio.

Fonte de dados: a view `vw_muscle_group_equipment_exercises`
(mysql/init/002_reestruturacao.sql), que ja cruza
`exercise_muscle_contributions` (contribuicao muscular por exercicio) x
`exercise_catalog` (catalogo novo, sem contaminacao Amazfit) x
`equipment_exercises` (associacao equipamento -> exercicio, sucessora
semantica de `equipment_exercise_mappings`) x `equipment`, todos filtrados
por `is_active = 1`.

Estes 3 endpoints sao aditivos: nao substituem nem alteram nenhum contrato
existente (`/gym-equipment/recommend*`, `/exercise-mapping-catalog/*`
continuam intactos). Servem para navegar o novo modelo de dados
diretamente: "quais equipamentos trabalham este grupo?", "quais exercicios
esse equipamento permite nesse grupo?" e "quais exercicios existem para
este grupo, independente de equipamento?".
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from .db import get_conn
from .db_helpers import connect_or_503, DB_UNAVAILABLE_MYSQL
from .security import Identity, get_current_identity

router = APIRouter(prefix="/muscle-groups", tags=["muscle-group-equipment"])


def _connect():
    return connect_or_503(get_conn, DB_UNAVAILABLE_MYSQL)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def _rows_jsonable(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{key: _jsonable(value) for key, value in row.items()} for row in rows]


def _fetch_all(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return _rows_jsonable(cursor.fetchall())


def _ensure_muscle_group_exists(muscle_group_id: int) -> None:
    rows = _fetch_all("SELECT id FROM muscle_groups WHERE id = %s", (muscle_group_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="muscle_group_id não encontrado.")


@router.get("/{muscle_group_id}/equipment")
def list_equipment_for_muscle_group(
    muscle_group_id: int,
    identity: Identity = Depends(get_current_identity),
):
    """Lista os equipamentos que trabalham o grupo muscular informado como
    grupo PRINCIPAL (role='PRIMARY'), via `vw_muscle_group_equipment_exercises`
    (DISTINCT por equipamento), ordenados por relevância: quantidade de
    exercícios distintos que o equipamento permite para esse grupo
    (`exercise_count`) e, em caso de empate, pelo maior `stimulus_weight`
    observado (`max_stimulus`). Participação SECONDARY/STABILIZER é
    consequência do exercício, não deve por si só associar um equipamento
    a um grupo muscular aqui."""
    _ensure_muscle_group_exists(muscle_group_id)

    rows = _fetch_all(
        """
        SELECT
            equipment_id,
            equipment_name,
            COUNT(DISTINCT exercise_id) AS exercise_count,
            MAX(stimulus_weight) AS max_stimulus
        FROM vw_muscle_group_equipment_exercises
        WHERE muscle_group_id = %s AND role = 'PRIMARY'
        GROUP BY equipment_id, equipment_name
        ORDER BY exercise_count DESC, max_stimulus DESC, equipment_name ASC
        """,
        (muscle_group_id,),
    )

    return {"muscle_group_id": muscle_group_id, "items": rows}


@router.get("/{muscle_group_id}/equipment/{equipment_id}/exercises")
def list_exercises_for_muscle_group_equipment(
    muscle_group_id: int,
    equipment_id: int,
    identity: Identity = Depends(get_current_identity),
):
    """Lista os exercícios do grupo muscular informado possíveis naquele
    equipamento específico, via `vw_muscle_group_equipment_exercises`
    filtrada por `muscle_group_id`, `equipment_id` e `role='PRIMARY'` —
    só entram exercícios em que este é o grupo principal; participação
    secundária/estabilizadora é consequência do exercício e não deve
    aparecer aqui como se fosse uma opção para treinar o grupo."""
    _ensure_muscle_group_exists(muscle_group_id)

    rows = _fetch_all(
        """
        SELECT DISTINCT
            exercise_id,
            exercise_name,
            role,
            stimulus_weight,
            fatigue_weight
        FROM vw_muscle_group_equipment_exercises
        WHERE muscle_group_id = %s AND equipment_id = %s AND role = 'PRIMARY'
        ORDER BY stimulus_weight DESC, exercise_name ASC
        """,
        (muscle_group_id, equipment_id),
    )

    return {
        "muscle_group_id": muscle_group_id,
        "equipment_id": equipment_id,
        "items": rows,
    }


@router.get("/{muscle_group_id}/exercises")
def list_exercises_for_muscle_group(
    muscle_group_id: int,
    identity: Identity = Depends(get_current_identity),
):
    """Lista todos os exercícios em que o grupo muscular informado é o
    grupo PRINCIPAL (role='PRIMARY'), independente de equipamento (DISTINCT
    por exercício, já que o mesmo exercício pode aparecer em mais de um
    equipamento na view). Participação secundária/estabilizadora não entra
    aqui — é consequência do exercício, não deve apontar diretamente para
    o grupo."""
    _ensure_muscle_group_exists(muscle_group_id)

    rows = _fetch_all(
        """
        SELECT
            exercise_id,
            exercise_name,
            MAX(stimulus_weight) AS max_stimulus,
            MAX(fatigue_weight) AS max_fatigue
        FROM vw_muscle_group_equipment_exercises
        WHERE muscle_group_id = %s AND role = 'PRIMARY'
        GROUP BY exercise_id, exercise_name
        ORDER BY max_stimulus DESC, exercise_name ASC
        """,
        (muscle_group_id,),
    )

    return {"muscle_group_id": muscle_group_id, "items": rows}

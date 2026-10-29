"""Preferencia do usuario por PAR exercicio+equipamento, em estrelas (PR-08b;
PLANO_20260912.md secoes 8.2 e 9). Decisao do usuario em 2026-09-17: a nota e
de um exercicio NAQUELE equipamento, nao do equipamento como um todo.

    GET    /me/equipment-exercise-preferences                          -> lista do usuario corrente
    PUT    /me/equipment-exercise-preferences/{equipment_exercise_id}  {"stars": 1-5}
    DELETE /me/equipment-exercise-preferences/{equipment_exercise_id}  -> volta a "sem avaliacao" (= 3 no ranking)
    GET    /me/training-preferences?gym_id=                             -> pares da academia + nota do usuario

`/me/training-preferences` alimenta a tela "Preferencias de Treino" (2026-10-13):
projeta, SO LEITURA, os pares exercicio+equipamento disponiveis numa academia
(`gym_equipment` ativo com vinculo em `equipment_exercises`) com a
nota do usuario (`stars` null = sem avaliacao). Nao ordena por nota nem
ranqueia: o ranking e o neutro de 3 estrelas moram em
`domain/equipment_ranking.py`; a gravacao continua no PUT/DELETE acima.

`equipment_exercise_id` = `equipment_exercises.id` (mesma ancora dos overrides
de seguranca do par).

Autorizacao = usuario corrente, SEMPRE: o `user_id` vem da identidade
resolvida pelo token/sessao e nao existe em path, query nem corpo (campo
extra no corpo e recusado com 422). Qualquer usuario logado avalia os proprios
pares; nao exige admin. Paths top-level -> catch-all da api-core, sem
Terraform de rota.

Sem DDL em runtime: a tabela vem da migracao 035. Tabela ausente (codigo
publicado antes da migracao) -> 503 explicito, nunca 500 generico.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .date_helpers import isoformat_or_value
from .db import transaction
from .domain.equipment_ranking import DEFAULT_STARS, MAX_STARS, MIN_STARS
from .equipment_recommender import (
    PREFERENCES_TABLE,
    PRIMARY_GROUP_LEFT_JOIN,
    PUBLIC_ASSETS_DOMAIN,
    is_missing_preferences_table_error,
)
from .security import Identity, get_current_identity

router = APIRouter(prefix="/me/equipment-exercise-preferences", tags=["equipment-exercise-preferences"])
training_preferences_router = APIRouter(prefix="/me/training-preferences", tags=["equipment-exercise-preferences"])


class EquipmentExercisePreferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # nunca aceitar user_id do cliente

    stars: int = Field(ge=MIN_STARS, le=MAX_STARS, strict=True)


logger = logging.getLogger(__name__)


def _unavailable(exc: Exception) -> HTTPException:
    # SEG-12: o texto da excecao vai para o log, nunca para o corpo do 503.
    logger.error("Tabela indisponivel em %s; respondendo 503", __name__, exc_info=exc)
    return HTTPException(
        status_code=503,
        detail={
            "error": "equipment_exercise_preferences_unavailable",
            "message": "Preferências de exercício por equipamento indisponíveis: migração 035 pendente neste ambiente.",
        },
    )


_iso = isoformat_or_value  # COD-06: corpo em `app/date_helpers.py`


def _item(row: dict[str, Any]) -> dict[str, Any]:
    exercise_id = int(row["exercise_id"])
    return {
        "equipment_exercise_id": int(row["equipment_exercise_id"]),
        "equipment_id": int(row["equipment_id"]),
        "equipment_name": row.get("equipment_name"),
        "exercise_id": exercise_id,
        "mapping_id": exercise_id,  # mesmo id: `gym_equipment.mapping_id` aponta para `exercise_catalog.id`
        "exercise_name": row.get("exercise_name"),
        "stars": int(row["stars"]),
        "updated_at": _iso(row.get("updated_at")),
    }


def _read(cur: Any, user_id: int, equipment_exercise_id: int | None = None) -> list[dict[str, Any]]:
    where = "p.user_id = %s"
    params: list[Any] = [user_id]
    if equipment_exercise_id is not None:
        where += " AND p.equipment_exercise_id = %s"
        params.append(equipment_exercise_id)
    cur.execute(
        f"""
        SELECT p.equipment_exercise_id, ee.equipment_id, eq.name AS equipment_name,
               ee.exercise_id, ec.name AS exercise_name, p.stars, p.updated_at
        FROM {PREFERENCES_TABLE} p
        JOIN equipment_exercises ee ON ee.id = p.equipment_exercise_id
        JOIN equipment eq ON eq.id = ee.equipment_id
        JOIN exercise_catalog ec ON ec.id = ee.exercise_id
        WHERE {where}
        ORDER BY eq.name ASC, ec.name ASC, p.equipment_exercise_id ASC
        """,
        tuple(params),
    )
    return [_item(row) for row in cur.fetchall() or []]


@router.get("")
def list_equipment_exercise_preferences(identity: Identity = Depends(get_current_identity)):
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                items = _read(cur, identity.user_id)
    except Exception as exc:  # noqa: BLE001
        if is_missing_preferences_table_error(exc):
            raise _unavailable(exc) from exc
        raise
    return {"ok": True, "default_stars": DEFAULT_STARS, "items": items}


@router.put("/{equipment_exercise_id}")
def put_equipment_exercise_preference(
    equipment_exercise_id: int,
    payload: EquipmentExercisePreferenceIn,
    identity: Identity = Depends(get_current_identity),
):
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM equipment_exercises WHERE id = %s", (equipment_exercise_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Vínculo exercício x equipamento não encontrado.")
                cur.execute(
                    f"""
                    INSERT INTO {PREFERENCES_TABLE} (user_id, equipment_exercise_id, stars)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE stars = VALUES(stars)
                    """,
                    (identity.user_id, equipment_exercise_id, payload.stars),
                )
                items = _read(cur, identity.user_id, equipment_exercise_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        if is_missing_preferences_table_error(exc):
            raise _unavailable(exc) from exc
        raise
    return {"ok": True, "item": items[0]}


@router.delete("/{equipment_exercise_id}")
def delete_equipment_exercise_preference(
    equipment_exercise_id: int, identity: Identity = Depends(get_current_identity)
):
    """Idempotente: apagar o que nao existe devolve `deleted: false`."""
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {PREFERENCES_TABLE} WHERE user_id = %s AND equipment_exercise_id = %s",
                    (identity.user_id, equipment_exercise_id),
                )
                deleted = bool(cur.rowcount)
    except Exception as exc:  # noqa: BLE001
        if is_missing_preferences_table_error(exc):
            raise _unavailable(exc) from exc
        raise
    return {"ok": True, "equipment_exercise_id": equipment_exercise_id, "deleted": deleted}


# ---------------------------------------------------------------------------
# GET /me/training-preferences (tela "Preferencias de Treino")
# ---------------------------------------------------------------------------

# Academia da ficha mais recente do usuario que tem academia (a "vigente":
# fichas sao geradas a partir do Modelo atribuido, e o Modelo nao guarda
# academia). So academias ativas, para o seletor nao abrir numa academia que
# nem aparece em `GET /gyms`.
_DEFAULT_GYM_SQL = """
    SELECT wp.gym_id
    FROM workout_plans wp
    JOIN gyms g ON g.id = wp.gym_id AND g.is_active = 1
    WHERE wp.user_id = %s AND wp.gym_id IS NOT NULL
    ORDER BY wp.plan_date DESC, wp.id DESC
    LIMIT 1
"""

# Base = candidatos do recomendador (`equipment_recommender._load_candidates`:
# `gym_equipment` ativo da academia), com duas diferencas deliberadas:
# (1) so pares que existem em `equipment_exercises` (a ancora da nota: sem ela
# nao ha o que gravar); (2) grupo primario por LEFT JOIN (mesmo fragmento do
# dono, variante LEFT): exercicio ainda sem grupo tambem pode ser avaliado,
# embora o recomendador nao o sugira. A ordem e so de exibicao (alfabetica),
# nao de ranking.
_TRAINING_PREFERENCES_SQL = f"""
    SELECT
        ee.id AS equipment_exercise_id,
        ge.mapping_id AS exercise_id, ec.name AS exercise_name,
        pg.id AS muscle_group_id, pg.name AS primary_group,
        ge.equipment_id, eq.name AS equipment_name,
        (CASE WHEN eq.photo_path IS NOT NULL THEN CONCAT('https://{PUBLIC_ASSETS_DOMAIN}/', eq.photo_path) ELSE NULL END) AS photo_url,
        p.stars
    FROM gym_equipment ge
    JOIN equipment_exercises ee ON ee.equipment_id = ge.equipment_id AND ee.exercise_id = ge.mapping_id
    JOIN exercise_catalog ec ON ec.id = ge.mapping_id
    JOIN equipment eq ON eq.id = ge.equipment_id
    {PRIMARY_GROUP_LEFT_JOIN}
    LEFT JOIN {PREFERENCES_TABLE} p ON p.equipment_exercise_id = ee.id AND p.user_id = %s
    WHERE ge.gym_id = %s AND ge.is_active = 1
    ORDER BY pg.name ASC, ec.name ASC, eq.name ASC, ee.id ASC
"""


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _training_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "equipment_exercise_id": int(row["equipment_exercise_id"]),
        "exercise_id": int(row["exercise_id"]),
        "exercise_name": row.get("exercise_name"),
        "muscle_group_id": _optional_int(row.get("muscle_group_id")),
        "primary_group": row.get("primary_group"),
        "equipment_id": int(row["equipment_id"]),
        "equipment_name": row.get("equipment_name"),
        "photo_url": row.get("photo_url"),
        "stars": _optional_int(row.get("stars")),  # null = sem avaliacao (o neutro e do dono)
    }


def _default_gym_id(cur: Any, user_id: int) -> int | None:
    cur.execute(_DEFAULT_GYM_SQL, (user_id,))
    row = cur.fetchone()
    return _optional_int(row.get("gym_id")) if row else None


@training_preferences_router.get("")
def list_training_preferences(
    gym_id: int | None = Query(default=None, ge=1),
    identity: Identity = Depends(get_current_identity),
):
    """Pares exercicio+equipamento da academia com a nota do usuario corrente.

    Sem `gym_id`, usa `default_gym_id` (academia da ficha mais recente); sem
    nenhuma das duas, devolve lista vazia. Academia inexistente ou inativa -> 404.
    """
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                default_gym_id = _default_gym_id(cur, identity.user_id)
                target_gym_id = gym_id if gym_id is not None else default_gym_id
                items: list[dict[str, Any]] = []
                if target_gym_id is not None:
                    cur.execute("SELECT id FROM gyms WHERE id = %s AND is_active = 1", (target_gym_id,))
                    if not cur.fetchone():
                        raise HTTPException(status_code=404, detail="Academia não encontrada.")
                    cur.execute(_TRAINING_PREFERENCES_SQL, (identity.user_id, target_gym_id))
                    items = [_training_item(row) for row in cur.fetchall() or []]
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        if is_missing_preferences_table_error(exc):
            raise _unavailable(exc) from exc
        raise
    return {
        "ok": True,
        "gym_id": target_gym_id,
        "default_gym_id": default_gym_id,
        "default_stars": DEFAULT_STARS,
        "items": items,
    }

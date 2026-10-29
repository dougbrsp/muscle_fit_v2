"""Deload (M97 D): CRUD do período (`/deload-periods`) e leituras de deload
que os motores e o Modo Treino consomem.

A REGRA (fator de carga, RPE-alvo, reps, séries, duração/validação do
período, supressão de insights e os predicados SQL) mora no dono
`app/domain/deload.py` (mapa `docs/motores/FONTES_DA_VERDADE.md` §67). Este
módulo só lê/grava as linhas e chama o dono.

Rotas (api-core, catch-all do API Gateway; nada novo em `/analytics/*`):

    GET  /deload-periods                -> períodos do usuário (recentes primeiro) + o de hoje
    GET  /deload-periods/current        -> deload ativo HOJE (fuso do usuário) e as constantes do dono
    POST /deload-periods                -> cria (início default = hoje; fim default = uma semana)
    POST /deload-periods/{id}/cancel    -> cancela (idempotente; as sessões já carimbadas ficam)

Regras de dado:
- `user_id` SEMPRE da identidade (ownership em toda query; IDOR = 404);
- no máximo um período não cancelado que toque o intervalo pedido: outro
  sobreposto -> 409 (`deload_period_overlap`); a checagem roda com a linha
  do usuário travada (`SELECT ... FOR UPDATE`), então dois POST simultâneos
  não passam juntos;
- cancelar não apaga: grava `cancelled_at`. Sessões iniciadas dentro do
  período continuam carimbadas (`activities.deload_source='period'`): a origem
  é congelada no `start`.

Leituras tolerantes (`*_safe`): antes da migração 094 (ordem de deploy) as
leituras de deload devolvem "sem deload" e logam, nunca derrubam o `start`, o
briefing nem a visão geral.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from .db import transaction
from .domain import deload as deload_owner
from .domain.training_goal import goal_value, sql_goal_is
from .security import Identity, get_current_identity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/deload-periods", tags=["deload"], dependencies=[Depends(get_current_identity)])

TABLE = "deload_periods"
MIGRATION = "094_deload.py"

_COLUMNS = "id, user_id, starts_on, ends_on, created_at, cancelled_at"

LIST_LIMIT = 50


# ---------------------------------------------------------------------------
# Leituras (consumidas pelo Modo Treino, pela progressão e pelos insights)
# ---------------------------------------------------------------------------


def active_period_on(cur: Any, user_id: int, day: date) -> Optional[dict[str, Any]]:
    """Período NÃO cancelado do usuário que cobre `day` (o mais recente)."""
    cur.execute(
        f"SELECT {_COLUMNS} FROM {TABLE} dp WHERE dp.user_id = %s AND {deload_owner.sql_period_overlaps('dp')} "
        "ORDER BY dp.starts_on DESC, dp.id DESC LIMIT 1",
        (int(user_id), day, day),
    )
    return cur.fetchone()


def active_period_on_safe(cur: Any, user_id: int, day: date) -> Optional[dict[str, Any]]:
    try:
        return active_period_on(cur, user_id, day)
    except Exception:  # noqa: BLE001 - antes da 094 (ordem de deploy): sem deload
        logger.exception("deload.active_period_read_failed")
        return None


def session_deload_source_safe(cur: Any, user_id: int, session_id: Any) -> Optional[str]:
    """Origem do deload de uma sessão do usuário (`None` = normal/ausente)."""
    if session_id is None:
        return None
    try:
        cur.execute(
            f"SELECT a.{deload_owner.DELOAD_SOURCE_COLUMN} AS deload_source FROM activities a "
            "WHERE a.id = %s AND a.user_id = %s",
            (int(session_id), int(user_id)),
        )
        row = cur.fetchone() or {}
    except Exception:  # noqa: BLE001 - antes da 094: sem deload
        logger.exception("deload.session_source_read_failed")
        return None
    return deload_owner.deload_source_value(row.get("deload_source"))


TRACK_WORKING_LOAD_SQL = f"""
SELECT t.working_load_kg
FROM progression_tracks t
WHERE t.user_id = %s AND t.exercise_id = %s
  AND t.equipment_id = (SELECT ge.equipment_id FROM gym_equipment ge WHERE ge.id = %s)
  AND {sql_goal_is("t")}
ORDER BY t.last_executed_at DESC, t.id DESC
LIMIT 1
"""


def track_working_load_safe(cur: Any, user_id: int, mapping_id: Any, gym_equipment_id: Any, training_goal: Any):
    """Carga de trabalho da trilha do par (a mais recente entre os contextos
    de fadiga) no objetivo do item. Sem par/trilha/falha -> `None`."""
    if mapping_id is None or gym_equipment_id is None:
        return None
    try:
        cur.execute(TRACK_WORKING_LOAD_SQL, (int(user_id), int(mapping_id), int(gym_equipment_id), goal_value(training_goal)))
        row = cur.fetchone() or {}
    except Exception:  # noqa: BLE001 - contexto opcional
        logger.exception("deload.track_read_failed")
        return None
    return row.get("working_load_kg")


def apply_session_deload_to_pair_suggestion(
    conn: Any,
    suggestion: dict[str, Any],
    user_id: int,
    *,
    mapping_id: Any,
    gym_equipment_id: Any,
    training_goal: Any,
) -> dict[str, Any]:
    """Sugestão do PAR TROCADO no Modo Treino numa sessão de deload (M97-D2,
    2026-10-26): o overlay do dono (`domain/deload.deload_pair_suggestion`
    sobre `pair_deload_prescription`) com a base do PAR NOVO (faixa %1RM da
    sugestão em força com e1RM, senão a trilha do par novo no objetivo do item,
    senão a carga sugerida). Quem chama já decidiu que a sessão é de deload.
    Falha: a sugestão de sempre (fail-open, logado)."""
    from .workout_plan_ai import normalize_load_target

    try:
        prescription = pair_deload_prescription_for(
            conn, suggestion, user_id, mapping_id=mapping_id, gym_equipment_id=gym_equipment_id,
            training_goal=training_goal,
        )
        return deload_owner.deload_pair_suggestion(suggestion, prescription, format_load=normalize_load_target)
    except Exception:  # noqa: BLE001 - fail-open: a sugestão de sempre, nunca 500 na troca
        logger.exception("deload.pair_suggestion_overlay_failed")
        return suggestion


def pair_deload_prescription_for(
    conn: Any,
    suggestion: Mapping[str, Any],
    user_id: int,
    *,
    mapping_id: Any,
    gym_equipment_id: Any,
    training_goal: Any,
) -> deload_owner.DeloadPrescription:
    """Base ÚNICA do deload do par trocado (1ª série pela sugestão do par e
    séries seguintes pela regra da próxima série): trilha do par novo no
    objetivo do item + a sugestão do motor para o par novo, pelo dono
    (`pair_deload_prescription`)."""
    goal = goal_value(training_goal)
    with conn.cursor() as cur:
        working = track_working_load_safe(cur, user_id, mapping_id, gym_equipment_id, goal)
    return deload_owner.pair_deload_prescription(track_working_load_kg=working, suggestion=suggestion, training_goal=goal)


SESSION_PLAN_ITEM_GOAL_SQL = """
SELECT wpe.training_goal, wpe.prescription_json
FROM activities a
JOIN workout_plan_exercises wpe ON wpe.workout_plan_id = a.workout_plan_id
WHERE a.id = %s AND a.user_id = %s AND wpe.id = %s
"""


def session_plan_item_goal_safe(cur: Any, user_id: int, session_id: Any, workout_plan_exercise_id: Any) -> Optional[str]:
    """Objetivo RESOLVIDO do item da ficha da sessão (o do snapshot congelado,
    senão a coluna da 093), para a troca de par no Modo Treino: o servidor não
    confia só no objetivo mandado pelo cliente. Item fora da ficha da sessão,
    sem item ou falha -> `None` (quem chama usa o do pedido)."""
    from .domain.effective_intensity import plan_item_goal

    if session_id is None or workout_plan_exercise_id is None:
        return None
    try:
        cur.execute(SESSION_PLAN_ITEM_GOAL_SQL, (int(session_id), int(user_id), int(workout_plan_exercise_id)))
        row = cur.fetchone()
    except Exception:  # noqa: BLE001 - contexto opcional: o objetivo do pedido
        logger.exception("deload.session_item_goal_read_failed")
        return None
    if not row:
        return None
    return plan_item_goal(row.get("prescription_json"), row.get("training_goal"))


def _session_load_plan_for(anchor_kg: Any, item: Mapping[str, Any]) -> dict[str, Any]:
    """Cargas de aquecimento/ajuste sobre a âncora de deload (dono
    `domain/session_loads`); sem técnica, sem cargas de aplicação."""
    from .domain.session_loads import session_load_plan

    return session_load_plan(
        anchor_kg,
        load_increment_kg=item.get("load_increment_kg"),
        feeler_sets_count=item.get("feeler_set_sets_count"),
        application_loads_kg=None,
    )


def apply_session_deload_to_plan(conn: Any, plan: dict[str, Any], user_id: int, session_id: Any) -> dict[str, Any]:
    """Ficha COMO A SESSÃO `session_id` A EXECUTA (fix M97, 2026-10-26).

    Sessão de deload (período ou avulsa, inclusive ligada pelo PATCH meta
    antes da 1ª série): cada item sai com o overlay do dono
    (`domain/deload.deload_plan_item` sobre `item_deload_prescription`, a
    MESMA base da regra da próxima série: trilha do par no objetivo do item,
    faixa %1RM congelada em força), então a 1ª série já vem com a carga, as
    reps, as séries e o RPE-alvo do deload e sem técnica. A ficha congelada não
    muda (overlay de leitura). Sessão normal, sem `session_id` ou falha de
    leitura: a ficha de sempre, intocada (nenhuma leitura além da origem)."""
    if session_id is None or not plan:
        return plan
    with conn.cursor() as cur:
        source = session_deload_source_safe(cur, user_id, session_id)
        if not deload_owner.is_deload_session(source):
            return plan
        try:
            items = _deload_items(cur, plan, user_id)
        except Exception:  # noqa: BLE001 - fail-open: a ficha de sempre, nunca 500 no Modo Treino
            logger.exception("deload.plan_overlay_failed")
            return plan
    return {**plan, "exercises": items, "deload": deload_status(source, None)}


def _deload_items(cur: Any, plan: Mapping[str, Any], user_id: int) -> list[dict[str, Any]]:
    from .workout_plan_ai import normalize_load_target

    items = []
    for item in plan.get("exercises") or []:
        snapshot = item.get("prescription") or {}
        goal = goal_value(snapshot.get("training_goal") or item.get("training_goal"))
        working = track_working_load_safe(cur, user_id, item.get("mapping_id"), item.get("preferred_equipment_id"), goal)
        prescription = deload_owner.item_deload_prescription(
            track_working_load_kg=working,
            snapshot={**snapshot, "training_goal": goal},
            plan_row=item,
            step_kg=item.get("load_increment_kg"),
        )
        items.append(
            deload_owner.deload_plan_item(
                item, prescription, format_load=normalize_load_target, session_load_plan=_session_load_plan_for
            )
        )
    return items


def deload_status(source: Optional[str], period: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Bloco `deload` das respostas do Modo Treino: ativo, origem e período."""
    return {
        "active": deload_owner.is_deload_session(source, period_active=period is not None),
        "source": source if source is not None else (deload_owner.DeloadSource.PERIOD.value if period else None),
        "period": _public(period) if period else None,
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class DeloadPeriodIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_on: Optional[date] = None
    ends_on: Optional[date] = None


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    out = {key: row.get(key) for key in ("id", "starts_on", "ends_on", "created_at", "cancelled_at")}
    out["cancelled"] = row.get("cancelled_at") is not None
    return out


def _today(conn: Any, user_id: int) -> date:
    from .intelligence.dashboard.time_windows import user_today_for

    return user_today_for(conn, user_id)


def _constants() -> dict[str, Any]:
    """Números do dono, prontos para a tela (o frontend não escreve número)."""
    return {
        "load_factor": float(deload_owner.DELOAD_LOAD_FACTOR),
        "target_rpe": deload_owner.DELOAD_TARGET_RPE,
        "sets_reduction_fraction": float(deload_owner.DELOAD_SETS_REDUCTION_FRACTION),
        "min_sets": deload_owner.DELOAD_MIN_SETS,
        "period_default_days": deload_owner.DELOAD_PERIOD_DEFAULT_DAYS,
        "period_max_days": deload_owner.DELOAD_PERIOD_MAX_DAYS,
    }


@router.get("")
def list_deload_periods(identity: Identity = Depends(get_current_identity)):
    with transaction() as conn:
        today = _today(conn, identity.user_id)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUMNS} FROM {TABLE} WHERE user_id = %s ORDER BY starts_on DESC, id DESC LIMIT %s",
                (identity.user_id, LIST_LIMIT),
            )
            rows = cur.fetchall() or []
            current = active_period_on(cur, identity.user_id, today)
    return {
        "items": [_public(row) for row in rows],
        "current": _public(current) if current else None,
        "today": today,
        "constants": _constants(),
    }


@router.get("/current")
def current_deload(identity: Identity = Depends(get_current_identity)):
    """Deload ativo HOJE (fuso do usuário) e os números do dono."""
    with transaction() as conn:
        today = _today(conn, identity.user_id)
        with conn.cursor() as cur:
            current = active_period_on(cur, identity.user_id, today)
    return {"today": today, **deload_status(None, current), "constants": _constants()}


@router.post("")
def create_deload_period(payload: DeloadPeriodIn, identity: Identity = Depends(get_current_identity)):
    with transaction() as conn:
        today = _today(conn, identity.user_id)
        try:
            starts_on, ends_on = deload_owner.validate_period(payload.starts_on or today, payload.ends_on, today=today)
        except deload_owner.InvalidDeloadPeriod as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with conn.cursor() as cur:
            # Serializa a checagem de sobreposição por usuário.
            cur.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (identity.user_id,))
            cur.execute(
                f"SELECT {_COLUMNS} FROM {TABLE} dp WHERE dp.user_id = %s AND {deload_owner.sql_period_overlaps('dp')} "
                "ORDER BY dp.starts_on ASC LIMIT 1",
                (identity.user_id, ends_on, starts_on),
            )
            overlapping = cur.fetchone()
            if overlapping:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "deload_period_overlap",
                        "message": "Já existe um período de deload ativo nessas datas.",
                        "period": _public(overlapping),
                    },
                )
            cur.execute(
                f"INSERT INTO {TABLE} (user_id, starts_on, ends_on) VALUES (%s, %s, %s)",
                (identity.user_id, starts_on, ends_on),
            )
            new_id = cur.lastrowid
            cur.execute(f"SELECT {_COLUMNS} FROM {TABLE} WHERE id = %s AND user_id = %s", (new_id, identity.user_id))
            row = cur.fetchone()
    if not row:  # pragma: no cover - defesa: o INSERT acabou de escrever
        raise HTTPException(status_code=500, detail="Período de deload não encontrado após gravar.")
    return {"status": "created", "item": _public(row)}


@router.post("/{period_id}/cancel")
def cancel_deload_period(period_id: int, identity: Identity = Depends(get_current_identity)):
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM {TABLE} WHERE id = %s AND user_id = %s", (period_id, identity.user_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Período de deload não encontrado.")
            if row.get("cancelled_at") is None:
                cur.execute(
                    f"UPDATE {TABLE} SET cancelled_at = CURRENT_TIMESTAMP WHERE id = %s AND user_id = %s",
                    (period_id, identity.user_id),
                )
                cur.execute(f"SELECT {_COLUMNS} FROM {TABLE} WHERE id = %s AND user_id = %s", (period_id, identity.user_id))
                row = cur.fetchone() or row
    return {"status": "cancelled", "item": _public(row)}


__all__ = [
    "active_period_on",
    "active_period_on_safe",
    "deload_status",
    "router",
    "session_deload_source_safe",
    "track_working_load_safe",
]

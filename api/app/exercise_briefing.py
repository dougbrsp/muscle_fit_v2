"""Briefing de UM exercicio da ficha (M4): tudo o que a tela mostra ANTES de
iniciar o exercicio, numa unica chamada.

    GET /workout-plans/{plan_id}/exercises/{workout_plan_exercise_id}/briefing

Uma chamada, e nao cinco, de proposito: a tela abre na academia, no celular,
entre a lista e a primeira serie. Cinco requisicoes (ficha, catalogo,
equipamento, historico, trilha) seriam cinco invocacoes da Lambda `api-core` -
que compartilha um limite de concorrencia baixo com as outras cinco Lambdas do
projeto (o mesmo motivo que tirou `GET /equipment/{id}/photo` do caminho das
fotos) - e cinco chances de spinner no meio da tela.

Leitura pura no caminho da RESPOSTA: nenhum INSERT/UPDATE e nenhum DDL entra
no que a tela espera. A UNICA escrita do endpoint acontece DEPOIS de tudo
montado, e e um `UPDATE` de uma linha que reserva a geracao da descricao de
execucao quando ela ainda nao existe
(`app/exercise_description_autogen.dispatch_if_missing`): a resposta continua
saindo com `execution_description: null`, o texto aparece na proxima abertura,
e qualquer falha desse disparo e engolida - nunca vira 500 nem atraso
perceptivel. Nenhuma chamada a OpenAI acontece neste processo.

Autorizacao pelo usuario corrente, sempre: a ficha e carregada por `services.get_workout_plan(...,
user_id=...)`, que ja filtra por dono; ficha de outro usuario responde 404.

Degradacao, item por item - nada aqui pode impedir o usuario de treinar:
- exercicio sem `execution_description` (ainda nao gerada) -> campo `null`
  na resposta de hoje + geracao enfileirada em segundo plano para a proxima;
- equipamento sem foto, ou exercicio sem equipamento resolvido -> `photo_url`
  `null` / `equipment` `null` (a tela tem fallback visual);
- exercicio sem historico -> `last_execution` `null` e `performance` sem
  comparacao (`comparable: false`);
- exercicio sem card ilustrado publicado -> `exercise.exercise_card` `null`
  (a tela nao mostra a miniatura nem o atalho "Como executar");
- tabelas do motor de progressao ausentes no ambiente -> `track` `null`
  (nunca 500).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from . import deload_periods, exercise_card_storage, exercise_description_autogen, services
from .db import transaction
from .domain import exercise_briefing as briefing
from .domain.deload import deload_allows_technique, item_deload_prescription
from .domain.training_goal import goal_value, sql_goal_is, sql_goal_join
from .domain.training_history import DEFAULT_SESSION_WINDOW, TrainingHistoryRepository
from .gyms import PUBLIC_ASSETS_DOMAIN
from .security import Identity, get_current_identity

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workout-plans"], dependencies=[Depends(get_current_identity)])

# Quantas sessoes concluidas para tras a busca pela "ultima execucao deste
# exercicio" varre. 21 e a janela padrao do motor de progressao
# (`DEFAULT_SESSION_WINDOW`) e cobre com folga varias semanas de treino: se o
# exercicio nao aparece em 21 sessoes, ele e novo o suficiente para a tela
# dizer "primeira vez" em vez de comparar com algo de tres meses atras.
HISTORY_WINDOW = DEFAULT_SESSION_WINDOW


def _catalog_row(cursor, exercise_id: int) -> Optional[dict[str, Any]]:
    """Linha do catalogo pelo id CANONICO (ja resolvido pelo chamador)."""
    cursor.execute(
        """
        SELECT id, name, description, execution_description, execution_description_model,
               execution_description_prompt_version, execution_description_reviewed_at,
               is_unilateral, is_compound, card_version
        FROM exercise_catalog WHERE id = %s
        """,
        (int(exercise_id),),
    )
    return cursor.fetchone()


def _canonical_exercise_id(cursor, mapping_id: Optional[int]) -> Optional[int]:
    """`wpe.mapping_id` -> id canonico (a ficha pode guardar um id legado
    fundido; e o mesmo COALESCE que `services.get_workout_plan` usa)."""
    if not mapping_id:
        return None
    cursor.execute(
        "SELECT canonical_exercise_id FROM exercise_legacy_id_map WHERE old_exercise_id = %s",
        (int(mapping_id),),
    )
    row = cursor.fetchone()
    return int(row["canonical_exercise_id"]) if row and row.get("canonical_exercise_id") else int(mapping_id)


def _muscles(cursor, exercise_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT emc.muscle_group_id, mg.name AS muscle_group_name, emc.role, emc.stimulus_weight
        FROM exercise_muscle_contributions emc
        JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
        WHERE emc.exercise_id = %s AND emc.is_active = 1
        ORDER BY FIELD(emc.role, 'PRIMARY', 'SECONDARY', 'STABILIZER'), mg.name
        """,
        (int(exercise_id),),
    )
    return [
        {
            "muscle_group_id": int(row["muscle_group_id"]),
            "name": row["muscle_group_name"],
            "role": row["role"],
            "stimulus_weight": float(row["stimulus_weight"]) if row.get("stimulus_weight") is not None else None,
        }
        for row in (cursor.fetchall() or [])
    ]


def _equipment(cursor, gym_equipment_id: Optional[int], fallback_name: Optional[str]) -> Optional[dict[str, Any]]:
    """Equipamento da ficha (`gym_equipment.id`) traduzido para o catalogo
    global, com a URL publica da foto.

    A foto ja existe desde a migracao 001 (`equipment.photo_path`) e e servida
    como arquivo estatico pelo CloudFront - o mesmo `photo_url` de `gyms.py` e
    do `equipment_recommender.py`. Sem foto -> `photo_url` `null`; a tela usa
    o placeholder de `EquipmentPhoto`.

    Sem `gym_equipment_id` (equipamento nao resolvido nesta academia) devolve,
    no maximo, o NOME que a ficha carrega - melhor que nada na tela.
    """
    if not gym_equipment_id:
        return {"gym_equipment_id": None, "equipment_id": None, "name": fallback_name, "photo_url": None} if fallback_name else None
    cursor.execute(
        f"""
        SELECT ge.id AS gym_equipment_id, eq.id AS equipment_id, eq.name,
               (CASE WHEN eq.photo_path IS NOT NULL THEN CONCAT('https://{PUBLIC_ASSETS_DOMAIN}/', eq.photo_path)
                     ELSE NULL END) AS photo_url
        FROM gym_equipment ge
        JOIN equipment eq ON eq.id = ge.equipment_id
        WHERE ge.id = %s
        """,
        (int(gym_equipment_id),),
    )
    row = cursor.fetchone()
    if not row:
        return {"gym_equipment_id": int(gym_equipment_id), "equipment_id": None, "name": fallback_name, "photo_url": None}
    return {
        "gym_equipment_id": int(row["gym_equipment_id"]),
        "equipment_id": int(row["equipment_id"]),
        "name": row["name"] or fallback_name,
        "photo_url": row["photo_url"],
    }


def _track(
    cursor, user_id: int, exercise_id: Optional[int], equipment_id: Optional[int], training_goal: Any = None
) -> Optional[dict[str, Any]]:
    """Fase da trilha do par (exercicio x equipamento) NO OBJETIVO do item
    (M97 F1; ausente = default) - o contexto de "onde estamos na
    progressao". Ambiente sem a migracao 036 -> `None`."""
    if not exercise_id or not equipment_id:
        return None
    try:
        cursor.execute(
            f"""
            SELECT t.load_status, t.working_load_kg, t.sets_count, t.technique_code, t.last_executed_at,
                   g.phase, g.phase_status, g.reps_min, g.reps_max
            FROM progression_tracks t
            LEFT JOIN progression_group_states g
                   ON g.user_id = t.user_id AND g.muscle_group_id = t.muscle_group_id
                  AND {sql_goal_join("g", "t")}
            WHERE t.user_id = %s AND t.exercise_id = %s AND t.equipment_id = %s AND {sql_goal_is("t")}
            ORDER BY t.last_executed_at DESC, t.id DESC
            LIMIT 1
            """,
            (int(user_id), int(exercise_id), int(equipment_id), goal_value(training_goal)),
        )
        row = cursor.fetchone()
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao ler a trilha de progressao para o briefing")
        return None
    if not row:
        return None
    return {
        "load_status": row.get("load_status"),
        "working_load_kg": float(row["working_load_kg"]) if row.get("working_load_kg") is not None else None,
        "sets_count": int(row["sets_count"]) if row.get("sets_count") is not None else None,
        "technique_code": row.get("technique_code"),
        "phase": row.get("phase"),
        "phase_status": row.get("phase_status"),
        "reps_min": int(row["reps_min"]) if row.get("reps_min") is not None else None,
        "reps_max": int(row["reps_max"]) if row.get("reps_max") is not None else None,
        "last_executed_at": row["last_executed_at"].isoformat()
        if hasattr(row.get("last_executed_at"), "isoformat")
        else row.get("last_executed_at"),
    }


def _last_execution(
    conn, user_id: int, exercise_id: Optional[int], workout_plan_exercise_id: int, training_goal: Any = None
):
    """Ultima execucao deste exercicio NO OBJETIVO do item (M97 F1; ausente =
    default), ou `None`. Falha de leitura do historico degrada para `None`:
    o briefing sem historico ainda serve."""
    if not exercise_id:
        return None
    try:
        # M97 D: sessao de deload nao e "ultima execucao" (sem execucao para
        # a progressao: `SessionRecord.progression_view`).
        sessions = [
            session.progression_view(training_goal)
            for session in TrainingHistoryRepository(conn).fetch_last_completed_sessions(user_id, HISTORY_WINDOW)
        ]
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao ler o historico para o briefing")
        return None
    found = briefing.find_last_execution(
        sessions, exercise_id=exercise_id, workout_plan_exercise_id=workout_plan_exercise_id
    )
    if not found:
        return None
    session, execution = found
    return briefing.summarize_last_execution(
        execution,
        local_date=session.local_date,
        activity_id=session.activity_id,
        equipment_name=None,
    )


def _performance(
    conn, user_id: int, exercise_id: Optional[int], equipment_id: Optional[int], training_goal: Any = None
) -> dict[str, Any]:
    """Status de DESEMPENHO do exercicio pela regra unica do dashboard
    (`comparability`), sobre as exposicoes da janela `exposure_lookback_days`
    - a mesma janela do bloco de progressao do dashboard -, no objetivo do
    item (M97 F1; ausente = default).

    Imports locais: o pacote `intelligence` e pesado e so este caminho da
    api-core precisa dele (mesmo padrao de `analytics`/`equipment_record`).
    Falha de leitura degrada para "sem comparacao" - o briefing nunca cai por
    isso."""
    if not exercise_id:
        return briefing.performance_unavailable()
    try:
        from datetime import timedelta

        from .intelligence.dashboard.exposure_service import MAX_EXPOSURES, get_exposures_between
        from .intelligence.dashboard.metrics_registry import THRESHOLDS
        from .intelligence.dashboard.time_windows import user_timezone, user_today

        today = user_today(user_timezone(conn, user_id))
        exposures = get_exposures_between(
            conn,
            int(exercise_id),
            user_id=user_id,
            date_from=today - timedelta(days=int(THRESHOLDS["exposure_lookback_days"])),
            date_to=today,
            limit=MAX_EXPOSURES,
            training_goal=training_goal,
        )
        return briefing.summarize_performance(exposures, equipment_id=equipment_id)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao classificar o desempenho para o briefing")
        return briefing.performance_unavailable()


@router.get("/workout-plans/{plan_id}/exercises/{workout_plan_exercise_id}/briefing")
def get_exercise_briefing(
    plan_id: int,
    workout_plan_exercise_id: int,
    session_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
) -> dict[str, Any]:
    """Tudo o que a tela de briefing precisa, montado no backend.

    M97 D: `deload` informa se a sessao e de deload (`session_id` da sessao em
    andamento: a origem gravada no `start`; sem ele, um periodo ativo HOJE no
    fuso do usuario) e, se for, a prescricao de deload do item (dono
    `domain/deload.py`, sobre a carga de trabalho da trilha)."""
    plan = services.get_workout_plan(plan_id, user_id=identity.user_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plano de treino não encontrado.")

    item = next(
        (
            exercise
            for exercise in plan.get("exercises") or []
            if int(exercise.get("workout_plan_exercise_id") or 0) == int(workout_plan_exercise_id)
        ),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Exercício não encontrado nesta ficha.")

    prescription = item.get("prescription") or {}
    # M97 F1: o objetivo de contexto e o da linha da ficha em execucao
    # (carimbado pelo servidor no congelamento; ausente = default).
    goal = goal_value(item.get("training_goal"))

    with transaction() as conn:
        with conn.cursor() as cursor:
            exercise_id = _canonical_exercise_id(cursor, item.get("mapping_id"))
            catalog = _catalog_row(cursor, exercise_id) if exercise_id else None
            muscles = _muscles(cursor, exercise_id) if exercise_id else []
            equipment = _equipment(cursor, item.get("preferred_equipment_id"), item.get("equipment_name"))
            track = _track(cursor, identity.user_id, exercise_id, (equipment or {}).get("equipment_id"), goal)
            deload = _deload(conn, cursor, identity.user_id, session_id)
        last_execution = _last_execution(conn, identity.user_id, exercise_id, workout_plan_exercise_id, goal)
        performance = _performance(conn, identity.user_id, exercise_id, (equipment or {}).get("equipment_id"), goal)

    # Desempenho (regra do dashboard) e ajuste de hoje (motor) sao coisas
    # diferentes e saem em campos diferentes; `trend` fica so por
    # compatibilidade e e DERIVADO do desempenho.
    if deload["active"]:
        deload["prescription"] = item_deload_prescription(
            track_working_load_kg=(track or {}).get("working_load_kg"),
            snapshot={**prescription, "training_goal": goal},
            plan_row=item,
            step_kg=item.get("load_increment_kg"),
        ).to_dict()
    else:
        deload["prescription"] = None

    # Fix M97 (2026-10-26): a sessao de deload nao oferece tecnica (dono
    # `domain/deload.deload_allows_technique`).
    technique = prescription.get("advanced_technique", item.get("advanced_technique"))
    technique_applications = item.get("advanced_technique_applications")
    if deload["active"] and not deload_allows_technique(technique):
        technique, technique_applications = None, None

    adjustment = briefing.describe_adjustment(prescription)
    trend = briefing.trend_from_performance(performance, adjustment)

    execution_description = (catalog or {}).get("execution_description")
    if catalog is not None and not str(execution_description or "").strip():
        # Rede de seguranca para exercicio que entrou no catalogo depois do
        # backfill: enfileira e segue. Nao espera, nao chama a OpenAI aqui,
        # nao levanta - e a resposta abaixo sai com `null` do mesmo jeito.
        exercise_description_autogen.dispatch_if_missing(exercise_id, owner_user_id=identity.user_id)

    return {
        "plan_id": int(plan_id),
        "workout_plan_exercise_id": int(workout_plan_exercise_id),
        "sequence": item.get("sequence"),
        "exercise": {
            "id": exercise_id,
            # O nome da FICHA manda: e o que o usuario viu na lista, e o
            # catalogo pode ter sido renomeado depois que a ficha foi criada.
            "name": item.get("exercise_name") or (catalog or {}).get("name"),
            "catalog_name": (catalog or {}).get("name"),
            "description": (catalog or {}).get("description"),
            "execution_description": execution_description,
            "execution_description_reviewed": bool((catalog or {}).get("execution_description_reviewed_at")),
            "is_unilateral": bool((catalog or {}).get("is_unilateral")),
            # Card ilustrado publicado do exercicio CANONICO (o mesmo de
            # `services.get_workout_plan`): `{url, thumbnail_url, version}` ou
            # `None` - sem card, a tela nao mostra nada.
            "exercise_card": exercise_card_storage.training_card(
                (catalog or {}).get("id"), (catalog or {}).get("card_version")
            ),
        },
        "equipment": equipment,
        "muscles": muscles,
        "prescription": {
            "sets_count": prescription.get("sets_count", item.get("sets_count")),
            "reps_target": prescription.get("reps_target", item.get("reps_target")),
            "load_target": prescription.get("load_target", item.get("load_target")),
            "suggested_load_kg": prescription.get("suggested_load_kg"),
            # Fix M97 (2026-10-26): faixa de carga de FORCA por %1RM congelada
            # pelo motor (`progression_engine.e1rm_load_range`); None fora dela.
            "strength_load": prescription.get("strength_load"),
            "rpe_target": prescription.get("rpe_target", item.get("rpe_target")),
            "effective_rpe_target": prescription.get("effective_rpe_target"),
            "effective_rpe_ceiling": prescription.get("effective_rpe_ceiling"),
            "failure_allowed": prescription.get("failure_allowed"),
            "advanced_technique": technique,
            "advanced_technique_applications": technique_applications,
            "rest_seconds": item.get("rest_seconds"),
            "status": item.get("prescription_status"),
            "warnings": prescription.get("warnings") or [],
            # M76 (Lote 1a, C7): cargas das series que nao sao de trabalho e
            # passo do equipamento, calculados no backend
            # (`domain/session_loads`, via `services._expose_frozen_prescriptions`).
            "load_increment_kg": item.get("load_increment_kg"),
            "load_anchor_kg": prescription.get("load_anchor_kg"),
            "warmup_loads_kg": prescription.get("warmup_loads_kg") or [],
            "feeler_load_kg": prescription.get("feeler_load_kg"),
            "feeler_loads_kg": prescription.get("feeler_loads_kg") or [],
            "application_loads_kg": prescription.get("application_loads_kg") or [],
        },
        "last_execution": last_execution,
        # Status de desempenho de `comparability` (mesmos codigos de
        # `/dashboard/exercicios`) e o ajuste de hoje do motor, separados.
        "performance": performance,
        "adjustment": adjustment,
        # LEGADO (clientes em cache): derivado de `performance`.
        "trend": trend,
        "track": track,
        "deload": deload,
        "requires_user_choice": bool(item.get("requires_user_choice")),
    }


def _deload(conn, cursor, user_id: int, session_id: Optional[int]) -> dict[str, Any]:
    """Deload do contexto do briefing (M97 D): a origem gravada na sessao em
    andamento ou, sem sessao, o periodo ativo hoje (fuso do usuario).
    Leituras tolerantes: falha = sem deload."""
    if session_id is not None:
        return deload_periods.deload_status(
            deload_periods.session_deload_source_safe(cursor, user_id, session_id), None
        )
    from .intelligence.dashboard.time_windows import user_today_for

    today = user_today_for(conn, user_id)
    return deload_periods.deload_status(None, deload_periods.active_period_on_safe(cursor, user_id, today))

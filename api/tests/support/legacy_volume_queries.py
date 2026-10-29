"""Copias LITERAIS das queries que `VolumeService._fetch_window_sets` e
`VolumeService._fetch_full_history_sets` usavam ANTES da correcao de
desempenho do drill-down (`fix/drilldown-timeout-producao`).

Servem de ORACULO nos testes de equivalencia: a correcao tirou as
subconsultas correlacionadas (avaliadas uma vez POR SERIE) de dentro da
query de series e passou a resolver grupo primario/secundario uma vez por
EXERCICIO do catalogo, em Python. Como o objetivo e exclusivamente
desempenho, as linhas devolvidas precisam continuar identicas - e o teste
compara a saida nova contra a query antiga executada no mesmo banco.

Nada aqui e importado pela aplicacao.
"""

from __future__ import annotations

from typing import Any

WINDOW_SETS_SQL = """
            SELECT
              es.id AS set_id,
              es.exercise_id,
              es.set_number,
              es.reps,
              es.weight_kg,
              es.rpe,
              es.set_type,
              a.id AS activity_id,
              a.local_date,
              a.name AS activity_name,
              a.activity_type,
              a.duration_seconds,
              COALESCE(a.started_at, a.ended_at, a.created_at) AS trained_at,
              NULLIF(pg.name, '') AS primary_group,
              NULLIF(sg.name, '') AS secondary_group,
              pg.id AS primary_group_id,
              sg.id AS secondary_group_id,
              zm.id AS mapping_id,
              (
                SELECT emc_sec.stimulus_weight
                FROM exercise_muscle_contributions emc_sec
                WHERE emc_sec.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                  AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
                ORDER BY emc_sec.id ASC LIMIT 1
              ) AS secondary_participation
            FROM activities a
            JOIN exercises e ON e.activity_id = a.id
            JOIN exercise_sets es ON es.exercise_id = e.id
            LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
            LEFT JOIN exercise_catalog zm ON zm.id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
            LEFT JOIN muscle_groups pg ON pg.id = (
              SELECT emc_pri.muscle_group_id
              FROM exercise_muscle_contributions emc_pri
              WHERE emc_pri.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
              ORDER BY emc_pri.id ASC LIMIT 1
            )
            LEFT JOIN muscle_groups sg ON sg.id = (
              SELECT emc_sec.muscle_group_id
              FROM exercise_muscle_contributions emc_sec
              WHERE emc_sec.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
              ORDER BY emc_sec.id ASC LIMIT 1
            )
            WHERE a.local_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
              {user_filter}
            ORDER BY es.exercise_id ASC, es.set_number ASC
"""

FULL_HISTORY_SETS_SQL = """
            SELECT
              es.id AS set_id,
              es.exercise_id,
              es.set_number,
              es.reps,
              es.weight_kg,
              es.rpe,
              es.set_type,
              a.id AS activity_id,
              a.local_date,
              COALESCE(a.started_at, a.ended_at, a.created_at) AS trained_at,
              CAST(COALESCE(elm.canonical_exercise_id, e.mapping_id) AS SIGNED) AS mapping_id,
              e.original_name,
              e.muscle_pain,
              e.joint_pain,
              NULLIF(pg.name, '') AS primary_group,
              NULLIF(sg.name, '') AS secondary_group,
              pg.id AS primary_group_id,
              sg.id AS secondary_group_id,
              (
                SELECT emc_sec.stimulus_weight
                FROM exercise_muscle_contributions emc_sec
                WHERE emc_sec.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                  AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
                ORDER BY emc_sec.id ASC LIMIT 1
              ) AS secondary_participation
            FROM activities a
            JOIN exercises e ON e.activity_id = a.id
            JOIN exercise_sets es ON es.exercise_id = e.id
            LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
            LEFT JOIN exercise_catalog zm ON zm.id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
            LEFT JOIN muscle_groups pg ON pg.id = (
              SELECT emc_pri.muscle_group_id
              FROM exercise_muscle_contributions emc_pri
              WHERE emc_pri.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
              ORDER BY emc_pri.id ASC LIMIT 1
            )
            LEFT JOIN muscle_groups sg ON sg.id = (
              SELECT emc_sec.muscle_group_id
              FROM exercise_muscle_contributions emc_sec
              WHERE emc_sec.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
              ORDER BY emc_sec.id ASC LIMIT 1
            )
            {user_filter}
            ORDER BY es.exercise_id ASC, es.set_number ASC
"""


def fetch_window_sets_legacy(service, days: int, user_id: int | None = None) -> list[dict[str, Any]]:
    user_filter = "AND a.user_id = %s" if user_id is not None else ""
    params: list[Any] = [days]
    if user_id is not None:
        params.append(user_id)
    return service.fetch_all(WINDOW_SETS_SQL.format(user_filter=user_filter), tuple(params))


def fetch_full_history_sets_legacy(service, user_id: int | None = None) -> list[dict[str, Any]]:
    user_filter = "WHERE a.user_id = %s" if user_id is not None else ""
    params: tuple = (user_id,) if user_id is not None else ()
    return service.fetch_all(FULL_HISTORY_SETS_SQL.format(user_filter=user_filter), params)

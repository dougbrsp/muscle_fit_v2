from __future__ import annotations

from datetime import timedelta
from typing import Any

from ..load_conventions import (
    exercise_tonnage_joins_sql,
    load_convention_select_expr,
    sql_tonnage_excluded_sets,
    sql_tonnage_sum,
)
from ..set_types import sql_not_preparatory, sql_set_confirmed
from .base import IntelligenceServiceBase


_ES_NOT_PREPARATORY = sql_not_preparatory("es.set_type")

# M63: mesmos JOINs/regra do `volume_kg` global de `analytics.py` - series
# `assisted`/`bodyweight` fora de reps x kg (M58).
_TONNAGE_JOINS = exercise_tonnage_joins_sql()


class TrainingLoadService(IntelligenceServiceBase):
    @staticmethod
    def _load_convention_expr() -> str:
        """`eqex.load_convention` quando a 045 ja rodou, senao `NULL` (= `total`).
        Deteccao cacheada por schema (`load_conventions`): em regime nao custa
        statement."""
        from ..db import get_conn

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                return load_convention_select_expr(cur)
        finally:
            conn.close()

    def get_weekly_summary(self, user_id: int | None = None) -> dict[str, Any]:
        # user_id opcional (None preserva o comportamento historico sem
        # filtro, ver ressalva em VolumeService._fetch_window_sets).
        # Corrigido na Fase 1 do vazamento multiusuario (ver plano
        # mighty-coalescing-pond.md).
        convention = self._load_convention_expr()
        # Semana civil (segunda a domingo) e "hoje" no fuso do USUARIO (M76
        # Lote 2b): `WEEKDAY(CURDATE())` usava o dia UTC do Aurora, e entre 21h
        # e 24h de Brasilia a semana (e o "proximo treino") ja era a do dia
        # seguinte - no domingo a noite, a semana seguinte inteira.
        today = self._user_today(user_id)
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=7)
        activities_user_filter = "AND a.user_id = %s" if user_id is not None else ""
        activities_params: list[Any] = [week_start]
        if user_id is not None:
            activities_params.append(user_id)
        totals = self.fetch_one(
            f"""
            SELECT
              COUNT(DISTINCT a.id) AS completed_workouts,
              COUNT(es.id) AS total_sets,
              {sql_tonnage_sum(convention)} AS total_volume_kg,
              {sql_tonnage_excluded_sets(convention)} AS tonnage_excluded_sets,
              ROUND(AVG(es.rpe), 2) AS avg_rpe,
              SUM(CASE WHEN es.rpe >= 9 THEN 1 ELSE 0 END) AS high_rpe_sets
            FROM activities a
            LEFT JOIN exercises e ON e.activity_id = a.id
            -- Resumo semanal de treino (contagem/volume/RPE), nao modelo de
            -- dose: aquecimento/ajuste sao EXCLUIDOS (nao fatorados) para
            -- bater com os demais totais do dashboard (analytics.py). A
            -- ponderacao por set_type fica no VolumeService/recuperacao.
            LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_NOT_PREPARATORY} AND {sql_set_confirmed('es.reps_pending')}
            {_TONNAGE_JOINS}
            WHERE a.local_date >= %s
              {activities_user_filter}
            """,
            tuple(activities_params),
        )

        plans_user_filter = "AND user_id = %s" if user_id is not None else ""
        plans_params: list[Any] = [week_start, week_end]
        if user_id is not None:
            plans_params.append(user_id)
        plans = self.fetch_one(
            f"""
            SELECT COUNT(*) AS planned_workouts
            FROM workout_plans
            WHERE plan_date >= %s
              AND plan_date < %s
              AND status NOT IN ('cancelled', 'done')
              {plans_user_filter}
            """,
            tuple(plans_params),
        )

        latest_workouts_user_filter = "WHERE user_id = %s" if user_id is not None else ""
        latest_workouts_params: tuple = (user_id,) if user_id is not None else ()
        latest_workouts = self.fetch_all(
            f"""
            SELECT id, local_date, name, activity_type, avg_rpe, duration_seconds
            FROM activities
            {latest_workouts_user_filter}
            ORDER BY local_date DESC, id DESC
            LIMIT 5
            """,
            latest_workouts_params,
        )

        next_workout_user_filter = "AND user_id = %s" if user_id is not None else ""
        next_workout_params: list[Any] = [today]
        if user_id is not None:
            next_workout_params.append(user_id)
        next_workout = self.fetch_one(
            f"""
            SELECT id, plan_date, name, plan_type, status
            FROM workout_plans
            WHERE plan_date >= %s
              AND status IN ('planned', 'draft')
              {next_workout_user_filter}
            ORDER BY plan_date ASC, id ASC
            LIMIT 1
            """,
            tuple(next_workout_params),
        )

        return {
            "completed_workouts": int(totals.get("completed_workouts") or 0),
            "planned_workouts": int(plans.get("planned_workouts") or 0),
            "total_sets": int(totals.get("total_sets") or 0),
            "total_volume_kg": float(totals.get("total_volume_kg") or 0),
            "tonnage_excluded_sets": int(totals.get("tonnage_excluded_sets") or 0),
            "avg_rpe": totals.get("avg_rpe"),
            "high_rpe_sets": int(totals.get("high_rpe_sets") or 0),
            "latest_workouts": latest_workouts,
            "next_workout": next_workout or None,
        }

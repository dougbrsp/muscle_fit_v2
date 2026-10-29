from __future__ import annotations

from datetime import date
from typing import Any

from ..domain.set_measures import sql_if_rep_based
from ..set_types import sql_not_preparatory, sql_set_confirmed
from .base import IntelligenceServiceBase
from .body_composition_trend_service import BodyCompositionTrendService
from .muscle_recovery_service import MuscleRecoveryService
from .training_load_service import TrainingLoadService

_ES_NOT_PREPARATORY = sql_not_preparatory("es.set_type")

# Mesmo filtro de "atividade de musculacao" usado em analytics.py
# (compute_training_trend / compute_rolling_weekly_trend / muscle_groups) -
# reaproveitado aqui para nao inventar uma nova heuristica de classificacao.
_STRENGTH_ACTIVITY_FILTER = """
    LOWER(a.activity_type) LIKE '%%muscul%%'
    OR LOWER(a.activity_type) LIKE '%%força%%'
    OR LOWER(a.activity_type) LIKE '%%strength%%'
"""


class CardioAgentContextService(IntelligenceServiceBase):
    """Agrega, num unico payload, todo o contexto fisiologico/de treino que
    o agente de IA de cardio (/agente-cardio) precisa para decidir SE e
    QUANTO cardio fazer. Reaproveita os servicos/tabelas ja existentes
    (recuperacao muscular, carga semanal, bioimpedancia, Intervals.icu,
    EPOC/PAI de 7 dias estimado, sono) sem duplicar logica de calculo - so agrega."""

    def __init__(self) -> None:
        self.body_trend_service = BodyCompositionTrendService()
        self.muscle_recovery_service = MuscleRecoveryService()
        self.training_load_service = TrainingLoadService()

    def _get_recent_workouts(
        self, days: int, user_id: int | None = None, since: date | None = None
    ) -> list[dict[str, Any]]:
        """Treinos de musculacao dos ultimos `days` dias, agregados por
        atividade: duracao, grupos musculares distintos (via
        exercise_muscle_contributions -> muscle_groups, Fase 4 da
        reestruturacao - mesmo padrao de subquery PRIMARY/SECONDARY usado
        por volume_service.py - NAO a heuristica de keyword-regex de
        analytics.py, que e um fallback textual menos preciso), sets,
        reps, volume e RPE medio.

        `user_id` opcional (Fase 3 do plano de multi-tenancy): quando
        informado, filtra `activities.user_id` (coluna NOT NULL); quando
        `None`, preserva o comportamento historico sem filtro.

        `since` (M98 C3a, `cardio-agent-v3`): primeiro dia LOCAL da janela,
        ja resolvido no fuso do usuario; substitui o `CURDATE()` do banco
        (dia UTC). Ausente = comportamento de sempre (v2, relatorio)."""
        where_clause = _STRENGTH_ACTIVITY_FILTER
        # M98 C4: sem `since`, a janela tambem e contada do "hoje" do usuario
        # (`_days_ago`, fuso do perfil), nunca do `CURDATE()` (dia UTC).
        start = since if since is not None else self._days_ago(user_id, days)
        params: list[Any] = [start.isoformat()]
        date_condition = "a.local_date >= %s"
        if user_id is not None:
            where_clause = f"a.user_id = %s AND ({_STRENGTH_ACTIVITY_FILTER})"
            params.append(user_id)
        else:
            where_clause = f"({_STRENGTH_ACTIVITY_FILTER})"

        rows = self.fetch_all(
            f"""
            SELECT
                a.id AS activity_id,
                a.local_date,
                a.name AS activity_name,
                a.activity_type,
                a.duration_seconds,
                a.avg_rpe AS activity_avg_rpe,
                GROUP_CONCAT(DISTINCT pg.name ORDER BY pg.name SEPARATOR ', ') AS primary_muscle_groups,
                GROUP_CONCAT(DISTINCT sg.name ORDER BY sg.name SEPARATOR ', ') AS secondary_muscle_groups,
                COUNT(DISTINCT e.id) AS exercises_count,
                COUNT(es.id) AS sets_count,
                COALESCE(SUM(COALESCE(es.reps, 0)), 0) AS reps_total,
                COALESCE(SUM({sql_if_rep_based('COALESCE(es.reps, 0) * COALESCE(es.weight_kg, 0)')}), 0) AS volume_kg,
                ROUND(AVG(es.rpe), 2) AS avg_rpe
            FROM activities a
            JOIN exercises e ON e.activity_id = a.id
            -- Aquecimento/ajuste fora de series/volume/RPE do contexto.
            LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_NOT_PREPARATORY} AND {sql_set_confirmed('es.reps_pending')}
            LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
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
            WHERE {date_condition}
              AND {where_clause}
            GROUP BY a.id, a.local_date, a.name, a.activity_type, a.duration_seconds, a.avg_rpe
            ORDER BY a.local_date DESC, a.id DESC
            """,
            tuple(params),
        )

        for row in rows:
            primary = row.pop("primary_muscle_groups", None)
            secondary = row.pop("secondary_muscle_groups", None)
            groups: list[str] = []
            for chunk in (primary, secondary):
                if not chunk:
                    continue
                for name in chunk.split(", "):
                    if name and name not in groups:
                        groups.append(name)
            row["muscle_groups"] = groups

        return rows

    def _get_intervals_wellness(self, days: int, user_id: int | None = None) -> list[dict[str, Any]]:
        """Mesma query de `intervals_cached_wellness`
        (intervals_integration.py) - replicada aqui (em vez de chamar a
        funcao do router diretamente) porque aquela funcao esta acoplada a
        dependencias do FastAPI (Header/Query) e a autenticacao por token
        HTTP, que nao fazem sentido numa chamada interna servico-a-servico.

        `user_id` opcional (Multiusuario Parte 2): `intervals_wellness.user_id`
        agora e NOT NULL. Quando informado, filtra por ele; quando `None`
        (rota `/analytics/cardio-agent-context` hoje so autentica com
        `require_token`, sem `Identity` disponivel), devolve os dados de
        TODOS os usuarios - correto apenas enquanto so o admin tiver
        Intervals.icu configurado, mesma ressalva documentada em
        `IntelligenceServiceBase.get_latest_intervals_wellness`."""
        if user_id is not None:
            return self.fetch_all(
                """
                SELECT
                    wellness_date,
                    fitness_ctl,
                    fatigue_atl,
                    form_tsb,
                    ramp_rate,
                    weight_kg,
                    resting_hr_bpm,
                    hrv_ms,
                    sleep_seconds,
                    readiness_score,
                    daily_effort_load,
                    synced_at
                FROM intervals_wellness
                WHERE user_id = %s AND wellness_date >= %s
                ORDER BY wellness_date DESC
                """,
                (user_id, self._days_ago(user_id, days).isoformat()),
            )

        return self.fetch_all(
            """
            SELECT
                wellness_date,
                fitness_ctl,
                fatigue_atl,
                form_tsb,
                ramp_rate,
                weight_kg,
                resting_hr_bpm,
                hrv_ms,
                sleep_seconds,
                readiness_score,
                daily_effort_load,
                synced_at
            FROM intervals_wellness
            WHERE wellness_date >= %s
            ORDER BY wellness_date DESC
            """,
            (self._days_ago(user_id, days).isoformat(),),
        )

    def _get_effort_estimates(self, days: int, user_id: int | None = None) -> list[dict[str, Any]]:
        """EPOC/PAI de 7 dias ESTIMADOS (M99) dos dias com valor na mesma
        janela de `_days_ago` (hoje e os `days` dias anteriores), do mais
        recente para o mais antigo. O número vem pronto do motor
        `domain/cardio_load.py` (via `effort_estimates`); sem `user_id` não há
        de quem ler o Intervals -> lista vazia."""
        if user_id is None:
            return []
        from ..effort_estimates import load_recent

        return load_recent(self.fetch_all, int(user_id), self._user_today(user_id), days + 1)

    def _get_sleep_metrics(self, days: int, user_id: int | None = None) -> list[dict[str, Any]]:
        """`user_id` opcional (Fase 3 do plano de multi-tenancy): quando
        informado, filtra `sleep_metrics.user_id` (coluna NOT NULL); quando
        `None`, preserva o comportamento historico sem filtro."""
        if user_id is not None:
            return self.fetch_all(
                """
                SELECT id, local_date, duration_hours, regularity_percent,
                       deep_sleep_hours, rem_sleep_hours, awake_count, awake_hours,
                       notes, created_at, updated_at
                FROM sleep_metrics
                WHERE user_id = %s AND local_date >= %s
                ORDER BY local_date DESC, id DESC
                """,
                (user_id, self._days_ago(user_id, days).isoformat()),
            )
        return self.fetch_all(
            """
            SELECT id, local_date, duration_hours, regularity_percent,
                   deep_sleep_hours, rem_sleep_hours, awake_count, awake_hours,
                   notes, created_at, updated_at
            FROM sleep_metrics
            WHERE local_date >= %s
            ORDER BY local_date DESC, id DESC
            """,
            (self._days_ago(user_id, days).isoformat(),),
        )

    def _get_past_recommendations(self, limit: int = 10, user_id: int | None = None) -> list[dict[str, Any]]:
        # Tabela garantida pela migracao 025 (sem DDL no caminho da requisicao).
        # `user_id` opcional (Fase 3 do plano de multi-tenancy): quando
        # informado, filtra `cardio_recommendations.user_id` (coluna NOT
        # NULL); quando `None`, preserva o comportamento historico sem
        # filtro.
        if user_id is not None:
            return self.fetch_all(
                """
                SELECT id, reference_date, generated_at, model, recommendation_markdown
                FROM cardio_recommendations
                WHERE user_id = %s
                ORDER BY generated_at DESC, id DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
        return self.fetch_all(
            """
            SELECT id, reference_date, generated_at, model, recommendation_markdown
            FROM cardio_recommendations
            ORDER BY generated_at DESC, id DESC
            LIMIT %s
            """,
            (limit,),
        )

    def build_context(self, days: int = 14, user_id: int | None = None) -> dict[str, Any]:
        return {
            "days": days,
            "body_composition": self.body_trend_service.get_body_trend(days=30, user_id=user_id),
            "recent_workouts": self._get_recent_workouts(days=days, user_id=user_id),
            # Janela e limite: os do modelo (M76 B3).
            "muscle_recovery": self.muscle_recovery_service.get_recovery_overview(user_id=user_id),
            "intervals_wellness": self._get_intervals_wellness(days=days, user_id=user_id),
            "effort_metrics_estimated": self._get_effort_estimates(days=days, user_id=user_id),
            "sleep_metrics": self._get_sleep_metrics(days=days, user_id=user_id),
            "weekly_training_load": self.training_load_service.get_weekly_summary(user_id=user_id),
            "past_recommendations": self._get_past_recommendations(limit=10, user_id=user_id),
        }

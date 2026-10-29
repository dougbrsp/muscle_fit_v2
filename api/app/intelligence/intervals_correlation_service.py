from __future__ import annotations

from datetime import datetime
from typing import Any

from .base import IntelligenceServiceBase

# Filtro de atividade de forca/musculacao no mesmo padrao ja usado em
# analytics.py (LOWER(activity_type) LIKE '%%muscul%%' etc.) - nao ha um
# enum fechado no schema, so o default 'musculacao' e variantes textuais.
_STRENGTH_ACTIVITY_TYPE_FILTER = """
    LOWER(a.activity_type) LIKE '%%muscul%%'
    OR LOWER(a.activity_type) LIKE '%%força%%'
    OR LOWER(a.activity_type) LIKE '%%forca%%'
    OR LOWER(a.activity_type) LIKE '%%strength%%'
"""


class IntervalsCorrelationService(IntelligenceServiceBase):
    """Correlaciona atividades de musculacao (`activities`) com o cache do
    Intervals.icu (`intervals_activities`/`intervals_wellness`).

    Hoje NAO existe nenhuma FK entre essas tabelas: elas vivem no mesmo
    schema MySQL mas sao completamente desconectadas. O casamento aqui e
    feito por data (`a.local_date == ia.activity_date`, restrito a
    `sport_type = 'WeightTraining'`). No caso raro de mais de uma sessao
    WeightTraining no mesmo dia para a mesma atividade de musculacao, o
    desempate e feito em Python (nao em SQL): primeiro por menor diferenca
    entre `a.started_at` e `ia.start_time` quando ambos existem, senao pelo
    candidato com maior `moving_time_seconds`.
    """

    def _fetch_strength_activities(self, days: int, user_id: int | None = None) -> list[dict[str, Any]]:
        """`user_id` opcional (None preserva o comportamento historico sem
        filtro). Desde o Novo Dashboard F0.1 `get_training_correlation`
        propaga o `user_id` recebido: as sessoes de forca de UM usuario nao
        podem ser cruzadas com o Intervals.icu de outro."""
        user_filter = " AND a.user_id = %s" if user_id is not None else ""
        params: tuple = (days, user_id) if user_id is not None else (days,)
        return self.fetch_all(
            f"""
            SELECT a.id, a.local_date, a.started_at, a.name
            FROM activities a
            WHERE a.local_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
              AND ({_STRENGTH_ACTIVITY_TYPE_FILTER}){user_filter}
            ORDER BY a.local_date ASC, a.id ASC
            """,
            params,
        )

    def _fetch_weighttraining_activities(self, days: int, user_id: int | None = None) -> list[dict[str, Any]]:
        """`user_id` opcional (Multiusuario Parte 2), mesma ressalva das
        demais queries deste arquivo contra `intervals_activities`/
        `intervals_wellness`."""
        if user_id is not None:
            return self.fetch_all(
                """
                SELECT
                  external_id,
                  activity_date,
                  start_time,
                  name,
                  sport_type,
                  moving_time_seconds,
                  avg_hr_bpm,
                  max_hr_bpm,
                  calories_kcal,
                  training_load
                FROM intervals_activities
                WHERE user_id = %s AND activity_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                  AND sport_type = 'WeightTraining'
                ORDER BY activity_date ASC, start_time ASC
                """,
                (user_id, days),
            )

        return self.fetch_all(
            """
            SELECT
              external_id,
              activity_date,
              start_time,
              name,
              sport_type,
              moving_time_seconds,
              avg_hr_bpm,
              max_hr_bpm,
              calories_kcal,
              training_load
            FROM intervals_activities
            WHERE activity_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
              AND sport_type = 'WeightTraining'
            ORDER BY activity_date ASC, start_time ASC
            """,
            (days,),
        )

    def _fetch_wellness(self, days: int, user_id: int | None = None) -> list[dict[str, Any]]:
        """`user_id` opcional (Multiusuario Parte 2) - mesma ressalva de
        `CardioAgentContextService._get_intervals_wellness`: sem `user_id`
        (rota `/analytics/dashboard-*` hoje so autentica com
        `require_token`), devolve dados de todos os usuarios."""
        if user_id is not None:
            return self.fetch_all(
                """
                SELECT wellness_date, sleep_seconds, hrv_ms, resting_hr_bpm
                FROM intervals_wellness
                WHERE user_id = %s AND wellness_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                ORDER BY wellness_date ASC
                """,
                (user_id, days),
            )

        return self.fetch_all(
            """
            SELECT wellness_date, sleep_seconds, hrv_ms, resting_hr_bpm
            FROM intervals_wellness
            WHERE wellness_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            ORDER BY wellness_date ASC
            """,
            (days,),
        )

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    @classmethod
    def _match_activity_for_day(
        cls, strength_activity: dict[str, Any], candidates: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        strength_started_at = cls._parse_dt(strength_activity.get("started_at"))
        if strength_started_at is not None:
            with_start_time = [
                candidate for candidate in candidates if cls._parse_dt(candidate.get("start_time")) is not None
            ]
            if with_start_time:
                return min(
                    with_start_time,
                    key=lambda candidate: abs(
                        (cls._parse_dt(candidate["start_time"]) - strength_started_at).total_seconds()
                    ),
                )

        return max(candidates, key=lambda candidate: float(candidate.get("moving_time_seconds") or 0))

    @staticmethod
    def _avg(rows: list[dict[str, Any]], field: str) -> float | None:
        values = [float(row[field]) for row in rows if row.get(field) is not None]
        return round(sum(values) / len(values), 2) if values else None

    def get_training_correlation(self, days: int = 90, user_id: int | None = None) -> dict[str, Any]:
        strength_activities = self._fetch_strength_activities(days, user_id=user_id)
        weighttraining_activities = self._fetch_weighttraining_activities(days, user_id=user_id)

        if not weighttraining_activities:
            return {
                "available": False,
                "matched_workouts": [],
                "unmatched_workouts_count": 0,
                "wellness_by_training_day": [],
                "wellness_avg_training_days": {},
                "wellness_avg_rest_days": {},
            }

        by_day: dict[str, list[dict[str, Any]]] = {}
        for item in weighttraining_activities:
            by_day.setdefault(str(item["activity_date"])[:10], []).append(item)

        wellness_rows = self._fetch_wellness(days, user_id=user_id)
        wellness_by_date = {str(row["wellness_date"])[:10]: row for row in wellness_rows}

        matched_workouts: list[dict[str, Any]] = []
        matched_day_keys: set[str] = set()
        matched_external_ids: set[str] = set()

        for activity in strength_activities:
            day_key = str(activity["local_date"])[:10]
            candidates = by_day.get(day_key) or []
            match = self._match_activity_for_day(activity, candidates)
            if match is None:
                continue

            matched_day_keys.add(day_key)
            if match.get("external_id"):
                matched_external_ids.add(match["external_id"])

            wellness = wellness_by_date.get(day_key) or {}
            matched_workouts.append(
                {
                    "activity_id": activity["id"],
                    "local_date": day_key,
                    "name": activity.get("name"),
                    "intervals_external_id": match.get("external_id"),
                    "moving_time_seconds": match.get("moving_time_seconds"),
                    "avg_hr_bpm": match.get("avg_hr_bpm"),
                    "max_hr_bpm": match.get("max_hr_bpm"),
                    "calories_kcal": match.get("calories_kcal"),
                    "training_load": match.get("training_load"),
                    "sleep_seconds": wellness.get("sleep_seconds"),
                    "hrv_ms": wellness.get("hrv_ms"),
                    "resting_hr_bpm": wellness.get("resting_hr_bpm"),
                }
            )

        # Sessoes WeightTraining registradas no Intervals que nao puderam
        # ser ligadas a nenhuma atividade de musculacao do Doug Fitness no
        # mesmo dia (sinal de qualidade de dados, nao de erro de calculo).
        unmatched_workouts_count = sum(
            1
            for item in weighttraining_activities
            if item.get("external_id") not in matched_external_ids
        )

        wellness_by_training_day = [
            row for day_key, row in wellness_by_date.items() if day_key in matched_day_keys
        ]
        wellness_rest_days = [
            row for day_key, row in wellness_by_date.items() if day_key not in matched_day_keys
        ]

        wellness_avg_training_days = {
            "sleep_seconds": self._avg(wellness_by_training_day, "sleep_seconds"),
            "hrv_ms": self._avg(wellness_by_training_day, "hrv_ms"),
            "resting_hr_bpm": self._avg(wellness_by_training_day, "resting_hr_bpm"),
            "days": len(wellness_by_training_day),
        }
        wellness_avg_rest_days = {
            "sleep_seconds": self._avg(wellness_rest_days, "sleep_seconds"),
            "hrv_ms": self._avg(wellness_rest_days, "hrv_ms"),
            "resting_hr_bpm": self._avg(wellness_rest_days, "resting_hr_bpm"),
            "days": len(wellness_rest_days),
        }

        return {
            "available": True,
            "matched_workouts": matched_workouts,
            "unmatched_workouts_count": unmatched_workouts_count,
            "wellness_by_training_day": wellness_by_training_day,
            "wellness_avg_training_days": wellness_avg_training_days,
            "wellness_avg_rest_days": wellness_avg_rest_days,
        }

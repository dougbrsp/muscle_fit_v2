from __future__ import annotations

from typing import Any

from .base import IntelligenceServiceBase
from .normalization_service import NormalizationService


class BodyCompositionTrendService(IntelligenceServiceBase):
    @staticmethod
    def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        normalized["body_water_percent"] = NormalizationService.normalize_percent(row.get("body_water_percent"))
        normalized["body_fat_percent"] = NormalizationService.normalize_percent(row.get("body_fat_percent"))
        normalized["subcutaneous_fat_percent"] = NormalizationService.normalize_percent(row.get("subcutaneous_fat_percent"))
        normalized["protein_percent"] = NormalizationService.normalize_percent(row.get("protein_percent"))
        return normalized

    def get_body_trend(self, days: int = 90, user_id: int | None = None) -> dict[str, Any]:
        # user_id opcional (None preserva o comportamento historico sem
        # filtro, ver ressalva em VolumeService._fetch_window_sets).
        user_filter = "AND user_id = %s" if user_id is not None else ""
        params: list[Any] = [days]
        if user_id is not None:
            params.append(user_id)
        rows = self.fetch_all(
            f"""
            SELECT
              local_date,
              weight_kg,
              bmi,
              body_fat_percent,
              muscle_mass_kg,
              body_water_percent,
              visceral_fat_rating,
              fat_free_mass_kg,
              subcutaneous_fat_percent,
              protein_percent,
              metabolic_age
            FROM body_composition
            WHERE local_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
              {user_filter}
            ORDER BY local_date ASC, id ASC
            """,
            tuple(params),
        )

        items = [self._normalize_row(row) for row in rows]
        if len(items) < 2:
            return {
                "days": days,
                "items": items,
                "trend": {"status": "insufficient_data"},
            }

        first = items[0]
        last = items[-1]

        def velocity(key: str) -> float | None:
            start = first.get(key)
            end = last.get(key)
            if start is None or end is None:
                return None
            if days <= 0:
                return None
            return round((float(end) - float(start)) / days, 4)

        return {
            "days": days,
            "items": items,
            "trend": {
                "weight_velocity_per_day": velocity("weight_kg"),
                "muscle_velocity_per_day": velocity("muscle_mass_kg"),
                "fat_velocity_per_day": velocity("body_fat_percent"),
                "water_velocity_per_day": velocity("body_water_percent"),
            },
        }

from __future__ import annotations

from .dashboard_aggregator_service import DashboardAggregatorService


class DashboardIntelligenceLayer:
    def __init__(self) -> None:
        self.dashboard_aggregator_service = DashboardAggregatorService()

    def get_training_evolution(self, days: int = 90, user_id: int | None = None) -> dict[str, object]:
        return self.dashboard_aggregator_service.build_training_evolution_payload(days=days, user_id=user_id)

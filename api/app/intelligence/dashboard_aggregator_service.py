from __future__ import annotations

from typing import Any

from ..analytics import compute_rolling_weekly_trend
from ..parallel_reads import run_parallel
from .intervals_correlation_service import IntervalsCorrelationService
from .muscle_recovery_service import MuscleRecoveryService
from .progression_service import ProgressionService
from .training_load_service import TrainingLoadService
from .volume_service import VolumeService


WEEKLY_TREND_WEEKS = 12
WEEKLY_TREND_MAX_DAYS = WEEKLY_TREND_WEEKS * 7

# Janela para `sessions` (composicao completa de exercicios por sessao).
# Menor que WEEKLY_TREND_MAX_DAYS de proposito, para nao inflar demais o
# payload enviado a IA - 30 dias ja cobre o ciclo de treino mais recente.
SESSIONS_MAX_DAYS = 30


class DashboardAggregatorService:
    def __init__(self) -> None:
        self.training_load_service = TrainingLoadService()
        self.volume_service = VolumeService()
        self.muscle_recovery_service = MuscleRecoveryService()
        self.progression_service = ProgressionService()
        self.intervals_correlation_service = IntervalsCorrelationService()

    def build_training_evolution_payload(self, days: int = 90, user_id: int | None = None) -> dict[str, Any]:
        """Payload agregado para o redesign de `/treinos`: resumo semanal,
        volume por grupamento, destaques de progressao, recuperacao e a
        correlacao (nova) com dados do Intervals.icu nos dias de treino de
        forca."""
        weekly_trend_days = min(days, WEEKLY_TREND_MAX_DAYS)
        sessions_days = min(days, SESSIONS_MAX_DAYS)

        # PERF-05: as leituras abaixo nao dependem umas das outras e cada uma
        # abre as proprias conexoes (`fetch_all`/`get_conn`): rodam em paralelo
        # (`parallel_reads.run_parallel`). As leituras do MESMO servico ficam na
        # MESMA tarefa (os servicos guardam cache na instancia), na ordem de antes.
        def _volume() -> tuple[Any, Any]:
            volume_overview = self.volume_service.get_muscle_group_overview(days=days, limit=8, user_id=user_id)
            weekly_trend_by_muscle_group = self.volume_service.get_rolling_weekly_trend_by_muscle_group(
                weekly_trend_days, user_id=user_id
            )
            return volume_overview, weekly_trend_by_muscle_group

        def _progression() -> tuple[Any, Any, Any]:
            # `recent_records` (M76 Lote 3b): recordes dos ultimos 60 dias pela
            # regra unica (`session_records.record_events`), por exercicio +
            # equipamento + convencao. Substitui `progression_highlights` (MAIOR
            # carga por NOME de exercicio, misturando equipamentos). O status de
            # progressao por exercicio vem da regra unica (`comparability`) em
            # `exercise_progression` - e o que a IA de `/dashboard/treinos` usa.
            recent_records = self.progression_service.get_recent_records(user_id=user_id)
            exercise_progression = self.progression_service.get_exercise_progression(days=days, user_id=user_id)
            sessions = self.progression_service.get_recent_sessions(days=sessions_days, user_id=user_id)
            return recent_records, exercise_progression, sessions

        (
            weekly_summary,
            (volume_overview, weekly_trend_by_muscle_group),
            (recent_records, exercise_progression, sessions),
            recovery,
            intervals_correlation,
            weekly_trend,
        ) = run_parallel(
            [
                lambda: self.training_load_service.get_weekly_summary(user_id=user_id),
                _volume,
                _progression,
                lambda: self.muscle_recovery_service.get_recovery_overview(days=14, limit=6, user_id=user_id),
                lambda: self.intervals_correlation_service.get_training_correlation(days=days, user_id=user_id),
                # F0.1 (Novo Dashboard): `user_id` propagado tambem aqui - era a unica
                # chamada deste agregador sem filtro de usuario, e `weekly_trend`
                # somava as series de TODOS os usuarios (vazamento entre contas).
                lambda: compute_rolling_weekly_trend(weekly_trend_days, user_id=user_id),
            ]
        )

        return {
            "days": days,
            "weekly_summary": weekly_summary,
            "volume_overview": volume_overview,
            "recent_records": recent_records,
            "exercise_progression": exercise_progression,
            "recovery": recovery,
            "intervals_correlation": intervals_correlation,
            "weekly_trend": weekly_trend,
            "weekly_trend_by_muscle_group": weekly_trend_by_muscle_group,
            "sessions": sessions,
        }

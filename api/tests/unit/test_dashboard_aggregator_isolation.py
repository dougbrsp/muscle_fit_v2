"""Isolamento por usuario no `DashboardAggregatorService` (F0.1, plano §15):
`build_training_evolution_payload` propaga `user_id`
para TODAS as chamadas de servico - inclusive `compute_rolling_weekly_trend`,
que ate este PR era chamada sem filtro (o `weekly_trend` de
`/analytics/training-evolution` somava as series de todos os usuarios).

Sem banco: todos os servicos sao dubles que registram os kwargs recebidos.
Formulas nao sao testadas aqui (nao mudaram). `build_home_payload` saiu com
`/analytics/dashboard-home` no M76 Lote 2c (sem consumidor).
"""

from __future__ import annotations


from app.intelligence import dashboard_aggregator_service as module
from app.intelligence.dashboard_aggregator_service import DashboardAggregatorService
from app.intelligence.intervals_correlation_service import IntervalsCorrelationService

USER_ID = 7


class _Recorder:
    """Servico falso: qualquer metodo devolve `retorno[nome]` e grava a chamada."""

    def __init__(self, calls: list, name: str, retorno: dict):
        self._calls = calls
        self._name = name
        self._retorno = retorno

    def __getattr__(self, method: str):
        def _call(*args, **kwargs):
            self._calls.append((f"{self._name}.{method}", args, kwargs))
            return self._retorno.get(method, [])

        return _call


def _service(calls: list, monkeypatch) -> DashboardAggregatorService:
    service = DashboardAggregatorService.__new__(DashboardAggregatorService)
    service.training_load_service = _Recorder(calls, "load", {"get_weekly_summary": {}})
    service.volume_service = _Recorder(calls, "volume", {})
    service.muscle_recovery_service = _Recorder(calls, "recovery", {})
    service.progression_service = _Recorder(calls, "progression", {})
    service.intervals_correlation_service = _Recorder(calls, "intervals", {"get_training_correlation": {}})

    def _fake_trend(days, user_id=None):
        calls.append(("analytics.compute_rolling_weekly_trend", (days,), {"user_id": user_id}))
        return []

    monkeypatch.setattr(module, "compute_rolling_weekly_trend", _fake_trend)
    return service


def _assert_todas_com_user_id(calls: list) -> None:
    assert calls, "nenhuma chamada registrada"
    sem_user = [name for name, _args, kwargs in calls if kwargs.get("user_id") != USER_ID]
    assert sem_user == [], f"chamadas sem user_id={USER_ID}: {sem_user}"


def test_build_training_evolution_payload_propaga_user_id_para_todas_as_chamadas(monkeypatch):
    calls: list = []
    payload = _service(calls, monkeypatch).build_training_evolution_payload(days=90, user_id=USER_ID)
    _assert_todas_com_user_id(calls)
    assert {name for name, _a, _k in calls} == {
        "load.get_weekly_summary",
        "volume.get_muscle_group_overview",
        "progression.get_recent_records",
        "progression.get_exercise_progression",
        "recovery.get_recovery_overview",
        "intervals.get_training_correlation",
        "analytics.compute_rolling_weekly_trend",
        "volume.get_rolling_weekly_trend_by_muscle_group",
        "progression.get_recent_sessions",
    }
    assert payload["days"] == 90


def test_weekly_trend_recebe_user_id_e_janela_limitada(monkeypatch):
    calls: list = []
    _service(calls, monkeypatch).build_training_evolution_payload(days=365, user_id=USER_ID)
    trend = [c for c in calls if c[0] == "analytics.compute_rolling_weekly_trend"]
    assert trend == [("analytics.compute_rolling_weekly_trend", (module.WEEKLY_TREND_MAX_DAYS,), {"user_id": USER_ID})]


def test_compute_rolling_weekly_trend_filtra_por_usuario_no_sql(monkeypatch):
    """A funcao real, com `user_id`, precisa emitir `a.user_id = %s` e passar o
    id nos parametros (sem banco: `_fetch_all` e substituido)."""
    from app import analytics

    captured: list = []
    monkeypatch.setattr(analytics, "_fetch_all", lambda sql, params=(): captured.append((sql, params)) or [])
    # M63: a deteccao da coluna da 045 abriria conexao - sem banco aqui.
    monkeypatch.setattr(analytics, "_load_convention_expr", lambda: "NULL")
    analytics.compute_rolling_weekly_trend(84, user_id=USER_ID)
    sql, params = captured[0]
    assert "a.user_id = %s" in sql
    assert USER_ID in params


def test_correlacao_intervals_filtra_sessoes_de_forca_por_usuario(monkeypatch):
    """`IntervalsCorrelationService._fetch_strength_activities` era a unica
    query do agregador sem `user_id` mesmo com o id em maos."""
    captured: list = []

    def _fake_fetch_all(self, sql, params=()):
        captured.append((sql, params))
        return []

    monkeypatch.setattr(IntervalsCorrelationService, "fetch_all", _fake_fetch_all)
    IntervalsCorrelationService().get_training_correlation(days=30, user_id=USER_ID)
    strength = [(sql, params) for sql, params in captured if "FROM activities a" in sql]
    assert strength, "query de sessoes de forca nao foi emitida"
    sql, params = strength[0]
    assert "a.user_id = %s" in sql
    assert params == (30, USER_ID)


def test_sem_user_id_continua_aceito_para_compatibilidade(monkeypatch):
    """`user_id=None` segue permitido nos agregadores legados (rotas que ainda
    autenticam so com token mestre); o novo pacote `intelligence/dashboard`
    e que recusa `None` via `require_user_id`."""
    calls: list = []
    _service(calls, monkeypatch).build_training_evolution_payload(days=30, user_id=None)
    assert all(kwargs.get("user_id") is None for _n, _a, kwargs in calls)

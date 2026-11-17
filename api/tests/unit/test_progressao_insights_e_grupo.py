"""Progressao: insights, corpo e desempenho por grupo so consomem o dono.

Regra (16/11/2026): conjuntos de status, contagem de comparaveis, fracao
"progredindo ou estavel", primeira x ultima exposicao da sequencia, variacao
de e1RM por grupo e limiar de RPE alto vem de `comparability`,
`exposure_service` e `metrics_registry`. Mapa: `docs/motores/FONTES_DA_VERDADE.md`.
"""

from __future__ import annotations

import inspect
import pytest

from app import main, services, workout_plan_ai
from app.intelligence import muscle_group_performance as mgp
from app.intelligence import training_load_service
from app.intelligence.dashboard import body_service, insight_rules
from app.intelligence.dashboard.comparability import GROUP_E1RM_OUTLIER_REASON, group_e1rm_changes, progression_share
from app.intelligence.dashboard.exposure_service import RUN_SPAN_BASIS
from app.intelligence.dashboard.metrics_registry import HIGH_RPE_MIN, ExerciseProgressionStatus
from tests.unit.test_dashboard_insight_rules import _ctx

S = ExerciseProgressionStatus


@pytest.mark.parametrize(
    "module, names",
    [
        (insight_rules, ("_COMPARABLE_STATUSES", "_PROGRESS_OK_STATUSES", "paired_top_reps")),
        (body_service, ("_COMPARABLE_STATUSES", "_PROGRESS_OK_STATUSES")),
        (mgp, ("OUTLIER_CHANGE_PCT", "_is_outlier", "build_key", "keys_match", "all_equipment_missing")),
    ],
)
def test_consumidores_sem_conjuntos_nem_primitivas_proprias(module, names):
    for name in names:
        assert not hasattr(module, name), f"{module.__name__}.{name}"


def test_plato_le_run_span_do_lote():
    """A regra usa `_run_span` (dono) e ignora `_comparable_run`."""
    first = {"best_load_kg": 50.0, "e1rm": 80.0, "comparability_key": {"equipment_id": 3, "load_convention": "total"}}
    last = {"best_load_kg": 55.0, "e1rm": None, "comparability_key": {"equipment_id": 3, "load_convention": "total"}}
    item = {
        "exercise_id": 5,
        "name": "Supino",
        "status": S.PLATEAU_SUSPECTED.value,
        "comparable_exposures": 4,
        "last_local_date": "2026-11-10",
        "_comparable_run": [],
        "_run_span": {
            "first": first,
            "last": last,
            "paired": {"previous_sum": 30, "current_sum": 31, "effort_adjusted": False},
            "e1rm_missing": True,
            "basis": RUN_SPAN_BASIS,
        },
    }
    ctx = _ctx(progression=[item])
    (insight,) = insight_rules.rule_exercise_plateau_suspected(ctx)
    evidence = {e["metric"]: e for e in insight["evidence"]}
    assert (evidence["best_load_kg"]["from_value"], evidence["best_load_kg"]["to_value"]) == (50.0, 55.0)
    assert (evidence["paired_top_reps"]["from_value"], evidence["paired_top_reps"]["to_value"]) == (30, 31)
    assert insight["comparison"]["basis"] == RUN_SPAN_BASIS
    assert insight["comparison"]["keys"]["equipment_id"] == 3
    assert insight["data_quality"].value == "MEDIUM"


def test_recomposicao_do_corpo_igual_ao_progression_share():
    items = [{"status": s.value} for s in (S.PROGRESSING, S.STABLE, S.REGRESSING, S.PLATEAU_SUSPECTED, S.INSUFFICIENT_DATA)]
    share = progression_share(items)
    serie = body_service.performance_series(items)
    assert (serie["comparable_exercises"], serie["progressing_or_stable"]) == (share["comparable"], share["progressing_or_stable"])
    assert (share["comparable"], share["progressing_or_stable"], share["share"]) == (4, 2, 0.5)


def test_variacao_por_grupo_igual_ao_dono():
    rows = [
        {"period": p, "exercise_id": 22, "exercise_name": "Hack", "equipment_id": 50, "load_convention": "total",
         "gym_id": 1, "equipment_type_code": "MACHINE", "muscle_group_id": 4, "stimulus_weight": 1.0, "best_e1rm_kg": v}
        for p, v in (("previous", 160.0), ("current", 190.0))
    ]
    (dono,) = group_e1rm_changes(rows)
    (item,) = mgp.build_group_performance(rows)[4]["performance_exercises"]
    for campo in ("exercise_id", "equipment_id", "load_convention", "previous_e1rm_kg", "current_e1rm_kg",
                  "change_pct", "stimulus_weight", "outlier", "excluded_reason"):
        assert item[campo] == dono[campo], campo
    assert mgp.OUTLIER_CHANGE == GROUP_E1RM_OUTLIER_REASON


def test_limiar_de_rpe_alto_vem_do_registro():
    assert inspect.signature(services.high_rpe_sets).parameters["min_rpe"].default is None
    assert inspect.signature(main.get_high_rpe_sets).parameters["min_rpe"].default is None
    assert "HIGH_RPE_MIN" in inspect.getsource(services.high_rpe_sets)
    assert "high_rpe_sets(limit=15, user_id=user_id)" in inspect.getsource(workout_plan_ai)
    assert "min_rpe=9" not in inspect.getsource(workout_plan_ai)
    src = inspect.getsource(training_load_service)
    assert "es.rpe >= 9" not in src and "es.rpe >= {int(HIGH_RPE_MIN)}" in src
    assert HIGH_RPE_MIN == 9


def test_high_rpe_sets_sem_limiar_usa_o_registro(monkeypatch):
    capturado = {}

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            capturado["params"] = params

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self):
            return _Cur()

    class _Tx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(services, "transaction", lambda: _Tx())
    services.high_rpe_sets(limit=5, user_id=1)
    assert capturado["params"][0] == float(HIGH_RPE_MIN)
    services.high_rpe_sets(7.5, 5, user_id=1)
    assert capturado["params"][0] == 7.5

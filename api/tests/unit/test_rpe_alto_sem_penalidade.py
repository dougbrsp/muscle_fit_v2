"""Decisao do usuario (2026-09-14, complemento da D4 revisada / PR #70): RPE
alto (>= 9, inclusive RPE 10, em qualquer serie e acima do alvo do plano) nao
penaliza prontidao muscular, blocking_signals nem volume_status. A contagem
`high_rpe_sets` continua so informativa. (O readiness da home e o alerta de
fadiga paralelos sairam no M76 Lote 2c, junto com `/analytics/dashboard-home`
e `/analytics/dashboard-alerts`.)

Tudo sem banco: servicos substituidos por stubs em memoria."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.intelligence.muscle_recovery_service import MuscleRecoveryService
from app.intelligence.volume_service import VolumeService

class _Stub:
    def __init__(self, **methods):
        for name, value in methods.items():
            setattr(self, name, lambda *a, _v=value, **k: _v)


# ---------------------------------------------------------------------------
# Prontidao muscular (MuscleRecoveryService.get_recovery_overview)
# ---------------------------------------------------------------------------


def _recovery_item(monkeypatch, **row_overrides):
    session_at = (datetime.now() - timedelta(hours=60)).replace(microsecond=0)
    row = {
        "muscle_group": "Peitoral",
        "muscle_group_id": 3,
        "session_dose": 2.0,
        "session_participation": 1.0,
        "dose_last_72h": 2.0,
        "hours_since_trained": 60.0,
        "days_since_trained": 2,
        "performance_ratio": None,
        "rpe_anticipated": False,
        "excessive_rep_drop": False,
        "muscle_pain": None,
        "joint_pain": False,
        "avg_rpe": 9.5,
        "last_set_rpe": 10.0,
        "last_session_at": session_at.isoformat(),
        "factors": {},
    }
    row.update(row_overrides)
    service = MuscleRecoveryService.__new__(MuscleRecoveryService)
    service.volume_service = _Stub(get_muscle_group_overview=[row])
    monkeypatch.setattr(service, "_get_global_tsb", lambda user_id=None: (None, None))
    monkeypatch.setattr(service, "_get_personal_coefficient", lambda group_id, user_id=None: 1.0)
    (item,) = service.get_recovery_overview(days=14, limit=24, user_id=1)
    return item


def test_prontidao_ignora_rpe_anticipated_legado(monkeypatch):
    """Antes: rpe_anticipated=True com tempo cumprido -> REDUCED_DOSE,
    recovery_pct 74, signal_penalty 26, max_rpe 9, volume_multiplier 0,75 e
    RPE_ANTICIPATED em blocking_signals. Agora READY/100/sem sinais."""
    legacy = _recovery_item(monkeypatch, rpe_anticipated=True)
    clean = _recovery_item(monkeypatch)

    for key in ("readiness_status", "recovery_pct", "signal_penalty", "max_rpe", "volume_multiplier", "blocking_signals"):
        assert legacy[key] == clean[key], key
    assert legacy["readiness_status"] == "READY"
    assert legacy["recovery_pct"] == 100.0
    assert legacy["blocking_signals"] == []
    assert all("RPE" not in line for line in legacy["explanation"])


# ---------------------------------------------------------------------------
# volume_status (VolumeService.get_volume_breakdown)
# ---------------------------------------------------------------------------


PEITORAL_ID = 3
# Meta de Ativacao do Peitoral: min 8 / alvo 12 / max 16 series equivalentes/semana.
PEITORAL_TARGET = {"min": 8.0, "target": 12.0, "max": 16.0}


def _breakdown(monkeypatch, *, equivalent_in_window, avg_rpe, days=14, targets=None, training_type_id=7, sets_count=20, group_id=PEITORAL_ID, days_since_trained=1):
    from app import training_types

    monkeypatch.setattr(
        training_types, "resolve_active_training_type_for_user", lambda user_id: training_type_id
    )
    monkeypatch.setattr(
        training_types,
        "get_training_type_muscle_group_targets",
        lambda tt_id: ({PEITORAL_ID: PEITORAL_TARGET} if targets is None else targets),
    )
    service = VolumeService()
    row = {
        "muscle_group": "Peitoral",
        "muscle_group_id": group_id,
        "avg_rpe": avg_rpe,
        "sets_count": sets_count,
        "stimulus_equivalent_sets": equivalent_in_window,
        "days_since_trained": days_since_trained,
        "session_dose": 0,
    }
    service.get_muscle_group_overview = lambda days, limit, user_id=None: [dict(row)]
    (item,) = service.get_volume_breakdown(days=days, user_id=1)["items"]
    return item


@pytest.mark.parametrize("avg_rpe", [6.5, 10.0])
@pytest.mark.parametrize(
    ("equivalent_in_window", "expected"),
    [
        (0.0, "subtreino"),  # 0/semana
        (15.8, "subtreino"),  # 7,9/semana: abaixo do min 8
        (16.0, "ideal"),  # 8,0/semana: igual ao min (inclusivo)
        (16.2, "ideal"),  # 8,1/semana: acima do min
        (24.0, "ideal"),  # 12/semana: entre min e max
        (32.0, "ideal"),  # 16/semana: igual ao max (inclusivo)
        (32.2, "excesso"),  # 16,1/semana: acima do max (#73)
    ],
)
def test_volume_status_pela_meta_min_max_sem_rpe(monkeypatch, avg_rpe, equivalent_in_window, expected):
    # sets_count fisico alto (20) e irrelevante: so a meta em series equivalentes decide.
    item = _breakdown(monkeypatch, equivalent_in_window=equivalent_in_window, avg_rpe=avg_rpe)
    assert item["volume_status"] == expected
    assert (item["min_weekly_sets"], item["max_weekly_sets"]) == (8.0, 16.0)


@pytest.mark.parametrize(("sets_count", "days_since_trained"), [(2, 1), (20, 30)])
def test_regra_fixa_de_series_e_dias_nao_define_subtreino(monkeypatch, sets_count, days_since_trained):
    """Antes: sets_count <= 6 OU days_since_trained >= 10 -> 'subtreino'.
    Agora so a meta: 12 eq/semana (entre min 8 e max 16) -> 'ideal'; a
    recencia continua exposta no payload."""
    item = _breakdown(
        monkeypatch, equivalent_in_window=24.0, avg_rpe=8.0, sets_count=sets_count, days_since_trained=days_since_trained
    )
    assert item["volume_status"] == "ideal"
    assert item["days_since_trained"] == days_since_trained


@pytest.mark.parametrize("avg_rpe", [6.5, 9.8])
@pytest.mark.parametrize(
    ("equivalent_in_window", "expected"),
    [
        (30.0, "ideal"),  # 15/semana: abaixo do max 16
        (32.0, "ideal"),  # 16/semana: igual ao max (nao e excesso)
        (32.2, "excesso"),  # 16,1/semana: acima do max
    ],
)
def test_volume_status_excesso_pela_meta_de_ativacao_sem_rpe(monkeypatch, avg_rpe, equivalent_in_window, expected):
    item = _breakdown(monkeypatch, equivalent_in_window=equivalent_in_window, avg_rpe=avg_rpe)
    assert item["volume_status"] == expected
    assert item["weekly_equivalent_sets"] == round(equivalent_in_window * 7 / 14, 2)
    assert item["max_weekly_sets"] == 16.0
    assert item["volume_target_training_type_id"] == 7


@pytest.mark.parametrize(
    "scenario",
    [
        {"training_type_id": None},  # usuario sem training_type resolvido
        {"targets": {}},  # training_type sem meta para nenhum grupo
        {"group_id": None},  # grupo sem id (ex.: 'Outros')
    ],
    ids=["sem-training-type", "sem-meta-do-grupo", "grupo-sem-id"],
)
@pytest.mark.parametrize(
    ("equivalent_in_window", "sets_count", "days_since_trained"),
    [
        (200.0, 200, 1),  # 100 eq/semana, RPE 10: antes 'excesso' (>= 18 fixo, #73)
        (0.0, 0, 1),  # 0 series: antes 'subtreino' (sets_count <= 6)
        (0.0, 0, 30),  # 30 dias sem treinar: antes 'subtreino' (days_since >= 10)
    ],
    ids=["volume-alto", "zero-series", "30-dias-sem-treinar"],
)
def test_volume_status_sem_meta_e_sempre_neutro(monkeypatch, scenario, equivalent_in_window, sets_count, days_since_trained):
    item = _breakdown(
        monkeypatch,
        equivalent_in_window=equivalent_in_window,
        avg_rpe=10.0,
        sets_count=sets_count,
        days_since_trained=days_since_trained,
        **scenario,
    )
    assert item["volume_status"] == "ideal"
    assert item["min_weekly_sets"] is None and item["max_weekly_sets"] is None
    assert item["volume_target_training_type_id"] is None


def test_volume_status_sem_usuario_nao_consulta_metas(monkeypatch):
    service = VolumeService()
    service.get_muscle_group_overview = lambda days, limit, user_id=None: [
        {"muscle_group": "Peitoral", "muscle_group_id": PEITORAL_ID, "sets_count": 40, "stimulus_equivalent_sets": 80.0, "days_since_trained": 1, "session_dose": 0},
        {"muscle_group": "Costas", "muscle_group_id": 6, "sets_count": 0, "stimulus_equivalent_sets": 0.0, "days_since_trained": 30, "session_dose": 0},
    ]
    assert service._resolve_user_weekly_targets(None) == (None, {})
    items = service.get_volume_breakdown(days=14, user_id=None)["items"]
    assert [item["volume_status"] for item in items] == ["ideal", "ideal"]

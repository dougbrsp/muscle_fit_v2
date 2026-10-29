"""Testes unitarios (sem banco) da projecao do contrato de prontidao - PR-09.

Rodar: cd api && python -m pytest tests/unit/test_recovery_projection.py
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.intelligence.muscle_recovery_service import MuscleRecoveryService
from app.intelligence.recovery_projection import (
    project_recovery_items,
    project_recovery_state,
)

NOW = datetime(2026, 9, 13, 8, 0)
SESSION_AT = "2026-09-11T16:00:00"


def _item(**overrides):
    # Tempo de recuperacao ja cumprido (40h decorridas de 30h estimadas) e
    # densidade baixa: qualquer rebaixamento vem dos sinais.
    base = {
        "muscle_group": "Peito",
        "muscle_group_id": 3,
        "session_dose": 3.0,
        "dose_last_72h": 3.0,
        "estimated_recovery_hours": 30.0,
        "hours_since_trained": 40.0,
        "performance_ratio": None,
        "rpe_anticipated": False,
        "excessive_rep_drop": False,
        "muscle_pain": None,
        "joint_pain": False,
        "last_session_at": SESSION_AT,
        "readiness_status": "READY",
        "recovery_pct": 100.0,
    }
    base.update(overrides)
    return base


def _codes(result):
    return [signal["code"] for signal in result["blocking_signals"]]


def test_same_day_later_keeps_joint_pain_blocking():
    # Bug G3: alvo hoje mais tarde (plan_time) zerava joint_pain.
    result = project_recovery_state(_item(joint_pain=True), NOW, NOW.replace(hour=18))

    assert result["projected_readiness_status"] == "NO_DIRECT_WORK"
    assert result["available_for_plan_date"] is False
    assert result["projected_recovery_pct"] <= 20
    assert result["max_rpe"] == 8
    assert result["allow_failure"] is False
    assert result["volume_multiplier"] == 0.0
    assert result["projected_for"] == "2026-09-13T18:00"
    assert result["blocking_signals"] == [
        {"code": "JOINT_PAIN", "value": True, "source_session_at": SESSION_AT}
    ]


def test_plus_48h_keeps_pain_and_performance_signals():
    # Antes o 2o sinal era rpe_anticipated=True (RPE_ANTICIPATED); removido em
    # 2026-09-14, o par de sinais agora usa a queda de repeticoes.
    item = _item(muscle_pain=5, excessive_rep_drop=True)
    result = project_recovery_state(item, NOW, NOW + timedelta(hours=48))

    assert "performance_signals_reset" not in result
    # 2 sinais negativos (dor >= 4 + queda de reps) -> NO_DIRECT_WORK.
    assert result["projected_readiness_status"] == "NO_DIRECT_WORK"
    assert result["projected_recovery_pct"] <= 45
    assert _codes(result) == ["MUSCLE_PAIN", "EXCESSIVE_REP_DROP"]
    assert result["blocking_signals"][0]["value"] == 5


def test_rpe_anticipated_do_snapshot_e_ignorado_na_projecao():
    """2026-09-14: um snapshot legado com rpe_anticipated=True nao rebaixa o
    grupo. Antes: REDUCED_DOSE (1 sinal), recovery_pct 74, RPE_ANTICIPATED."""
    for hours in (2, 48):
        result = project_recovery_state(_item(rpe_anticipated=True), NOW, NOW + timedelta(hours=hours))
        assert result["projected_readiness_status"] == "READY"
        assert result["projected_recovery_pct"] == 100.0
        assert result["projected_signal_penalty"] == 0.0
        assert result["blocking_signals"] == []


def test_plus_48h_muscle_pain_only_is_reduced_dose_and_reconciled():
    result = project_recovery_state(_item(muscle_pain=5), NOW, NOW + timedelta(hours=48))

    assert result["projected_readiness_status"] == "REDUCED_DOSE"
    assert 55 <= result["projected_recovery_pct"] <= 74
    assert result["projected_signal_penalty"] > 0
    assert result["max_rpe"] == 9
    assert result["allow_failure"] is False
    assert result["volume_multiplier"] == 0.75
    assert result["projected_recommendation"]["maximum_rpe"] == 9


def test_plus_80h_keeps_pain_and_performance_signals():
    """M76 B3 (decisão 2 do usuário, 2026-10-06): sinais de desempenho
    persistem até a próxima sessão do grupo, como no retrato do dashboard.
    Antes eram zerados a partir de 72h à frente e sobrava só a dor."""
    item = _item(muscle_pain=5, performance_ratio=0.85, excessive_rep_drop=True)
    result = project_recovery_state(item, NOW, NOW + timedelta(hours=80))

    assert result["projected_readiness_status"] == "NO_DIRECT_WORK"
    assert _codes(result) == ["MUSCLE_PAIN", "PERFORMANCE_DROP", "EXCESSIVE_REP_DROP"]


def test_plus_80h_joint_pain_still_blocks():
    result = project_recovery_state(_item(joint_pain=True), NOW, NOW + timedelta(hours=80))

    assert result["projected_readiness_status"] == "NO_DIRECT_WORK"
    assert _codes(result) == ["JOINT_PAIN"]


def test_plus_80h_performance_signal_still_counts():
    """Antes: READY (sinais zerados ≥72h). Agora a queda de desempenho da
    última sessão continua valendo para qualquer distância do alvo."""
    item = _item(performance_ratio=0.85, excessive_rep_drop=True)
    result = project_recovery_state(item, NOW, NOW + timedelta(hours=80))

    assert result["projected_readiness_status"] == "NO_DIRECT_WORK"
    assert _codes(result) == ["PERFORMANCE_DROP", "EXCESSIVE_REP_DROP"]
    assert result["allow_failure"] is False


def test_retrato_e_projecao_tem_a_mesma_regra_de_sinais():
    """Mesmo grupo, mesmo snapshot: o estado projetado a 1h, a 71h, a 72h e a
    10 dias é o mesmo do retrato quando só os sinais decidem (tempo já
    cumprido). Não existe mais limiar de expiração própria da projeção."""
    item = _item(performance_ratio=0.92)
    retrato = project_recovery_state(item, NOW, NOW)["projected_readiness_status"]
    assert retrato == "REDUCED_DOSE"
    for hours in (1, 71, 72, 240):
        projetado = project_recovery_state(item, NOW, NOW + timedelta(hours=hours))
        assert projetado["projected_readiness_status"] == retrato, hours
        assert _codes(projetado) == ["PERFORMANCE_DROP"]


def test_low_readiness_signal_and_density_kept_under_72h():
    # 5h decorridas + 10h a frente = 15h de 30h -> 50% -> NO_DIRECT_WORK.
    item = _item(hours_since_trained=5.0, dose_last_72h=9.0)
    result = project_recovery_state(item, NOW, NOW + timedelta(hours=10))

    # densidade 9/7.5 = 1.2 -> penalidade 10 mantida (< 72h).
    assert result["projected_adjusted_readiness"] == 40.0
    assert result["projected_readiness_status"] == "NO_DIRECT_WORK"
    assert _codes(result) == ["LOW_READINESS"]
    assert result["blocking_signals"][0]["value"] == 40.0


def test_target_not_in_future_is_not_a_projection():
    result = project_recovery_state(_item(performance_ratio=0.92), NOW, NOW - timedelta(hours=3))

    assert result["projected_for"] is None
    assert result["projected_readiness_status"] == "REDUCED_DOSE"
    assert _codes(result) == ["PERFORMANCE_DROP"]
    assert result["blocking_signals"][0]["value"] == 0.92


def test_projection_preserves_snapshot_fields():
    item = _item(joint_pain=True)
    [result] = project_recovery_items([item], NOW, NOW + timedelta(hours=2))

    assert result["readiness_status"] == "READY"
    assert result["recovery_pct"] == 100.0
    assert result["signals_policy"] == "signals_persist_until_superseded"
    assert "blocking_signals" not in item


def test_blocking_signals_thresholds_match_classifier():
    signals = MuscleRecoveryService.build_blocking_signals(
        adjusted_readiness=80.0,
        performance_ratio=0.95,
        excessive_rep_drop=True,
        muscle_pain=3,
        joint_pain=None,
        source_session_at=None,
    )
    assert signals == [{"code": "EXCESSIVE_REP_DROP", "value": True, "source_session_at": None, "exercises": []}]

    status = MuscleRecoveryService.classify_readiness(80.0, 0.95, False, 3, None)
    assert status == "READY"
    assert MuscleRecoveryService.build_blocking_signals(80.0, 0.95, False, 3, None) == []


def test_queda_de_reps_leva_exercicio_e_reps_ate_a_projecao():
    """Lote 1/P2: o sinal EXCESSIVE_REP_DROP carrega o exercicio e as reps da
    1a e da ultima serie (insumo do banner do Lote 6), tambem na projecao."""
    detalhe = {"exercise_id": 7, "exercise_name": "Abdominal", "first_reps": 15, "last_reps": 9}
    item = _item(excessive_rep_drop=True, rep_drop_details=[detalhe], last_session_at="2026-10-03T18:00:00")
    result = project_recovery_state(item, NOW, NOW + timedelta(hours=48))
    [signal] = [s for s in result["blocking_signals"] if s["code"] == "EXCESSIVE_REP_DROP"]
    assert signal["exercises"] == [detalhe]
    assert signal["source_session_at"] == "2026-10-03T18:00:00"

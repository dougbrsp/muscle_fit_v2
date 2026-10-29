"""Caracterizacao de MuscleRecoveryService.classify_readiness /
count_negative_signals / reconcile_recovery_pct
(api/app/intelligence/muscle_recovery_service.py).

Congela o comportamento ATUAL. Onde o comportamento atual e questionavel, o
caso traz o comentario "comportamento atual" - se um PR mudar isso de
proposito, atualize o teste junto (e explique no PR)."""

from __future__ import annotations

import pytest

from app.intelligence.muscle_recovery_service import MuscleRecoveryService as MRS


def classify(
    adjusted=100.0,
    perf=None,
    rep_drop=False,
    muscle_pain=None,
    joint_pain=None,
):
    return MRS.classify_readiness(adjusted, perf, rep_drop, muscle_pain, joint_pain)


# ---------------------------------------------------------------------------
# count_negative_signals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("perf", "rep_drop", "muscle_pain", "expected"),
    [
        (None, False, None, 0),
        (0.95, False, 3.9, 0),  # limites: 0.95 e dor < 4 nao contam
        (0.9499, False, None, 1),
        (None, True, None, 1),
        (None, False, 4, 1),
        # Antes: 4 (com rpe_anticipated). RPE alto deixou de ser sinal
        # negativo em 2026-09-14 -> maximo de 3 sinais.
        (0.5, True, 10, 3),
    ],
)
def test_count_negative_signals(perf, rep_drop, muscle_pain, expected):
    assert MRS.count_negative_signals(perf, rep_drop, muscle_pain) == expected


def test_rpe_anticipated_nao_e_mais_parametro_de_sinal():
    """2026-09-14: RPE alto (acima do alvo do plano) nao conta como sinal
    negativo nem entra na assinatura dos classificadores."""
    import inspect

    for fn in (MRS.count_negative_signals, MRS.classify_readiness, MRS.build_blocking_signals, MRS.build_readiness_contract):
        assert "rpe_anticipated" not in inspect.signature(fn).parameters, fn.__name__


# ---------------------------------------------------------------------------
# classify_readiness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # Sem nenhum sinal e tempo/densidade completos.
        ({}, "READY"),
        # Limiares de adjusted_readiness: <55 veta, <75 reduz.
        ({"adjusted": 75.0}, "READY"),
        ({"adjusted": 74.9}, "REDUCED_DOSE"),
        ({"adjusted": 55.0}, "REDUCED_DOSE"),
        ({"adjusted": 54.9}, "NO_DIRECT_WORK"),
        # Dor articular veta sempre, independente do resto.
        ({"joint_pain": True}, "NO_DIRECT_WORK"),
        ({"joint_pain": False}, "READY"),
        # Dor muscular: > 6 veta direto; 4..6 conta como UM sinal negativo.
        ({"muscle_pain": 7}, "NO_DIRECT_WORK"),
        ({"muscle_pain": 6.5}, "NO_DIRECT_WORK"),
        # comportamento atual: dor 6 (e 4, 5) so reduz dose, nao bloqueia.
        ({"muscle_pain": 6}, "REDUCED_DOSE"),
        ({"muscle_pain": 4}, "REDUCED_DOSE"),
        ({"muscle_pain": 3}, "READY"),
        # Performance: < 0.90 veta; [0.90, 0.95) e um sinal negativo.
        ({"perf": 0.89}, "NO_DIRECT_WORK"),
        ({"perf": 0.90}, "REDUCED_DOSE"),
        ({"perf": 0.95}, "READY"),
        ({"perf": 1.2}, "READY"),
        # Um sinal isolado reduz; dois sinais vetam.
        # (Antes havia {"rpe_anticipated": True} -> REDUCED_DOSE e
        # {"rpe_anticipated": True, "rep_drop": True} -> NO_DIRECT_WORK; o
        # sinal foi removido em 2026-09-14 - o par de sinais agora usa perf.)
        ({"rep_drop": True}, "REDUCED_DOSE"),
        ({"perf": 0.92, "rep_drop": True}, "NO_DIRECT_WORK"),
        ({"perf": 0.92, "muscle_pain": 5}, "NO_DIRECT_WORK"),
    ],
)
def test_classify_readiness(kwargs, expected):
    assert classify(**kwargs) == expected


def test_classify_readiness_sinais_nulos_sao_neutros():
    # RPE/dor/performance ausentes (None) nunca contam como sinal negativo.
    assert classify(adjusted=80.0, perf=None, muscle_pain=None, joint_pain=None) == "READY"


# ---------------------------------------------------------------------------
# reconcile_recovery_pct
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("adjusted", "status", "joint_pain", "muscle_pain", "signals", "perf", "expected_value", "expected_penalty"),
    [
        # NO_DIRECT_WORK por causa grave -> teto 20.
        (100.0, "NO_DIRECT_WORK", True, None, 0, None, 20.0, 80.0),
        (30.0, "NO_DIRECT_WORK", None, 7, 0, None, 20.0, 10.0),
        (15.0, "NO_DIRECT_WORK", True, None, 0, None, 15.0, 0.0),
        # NO_DIRECT_WORK por combinacao de sinais / performance -> teto 45.
        (90.0, "NO_DIRECT_WORK", None, None, 2, None, 45.0, 45.0),
        (60.0, "NO_DIRECT_WORK", None, None, 0, 0.85, 45.0, 15.0),
        # NO_DIRECT_WORK so por tempo/densidade -> nao mexe.
        (40.0, "NO_DIRECT_WORK", None, None, 0, None, 40.0, 0.0),
        # Rede de seguranca: status NDW sem causa aparente ainda fica < 55.
        (80.0, "NO_DIRECT_WORK", None, None, 0, None, 54.0, 26.0),
        # REDUCED_DOSE -> [55, 74].
        (90.0, "REDUCED_DOSE", None, None, 1, None, 74.0, 16.0),
        (60.0, "REDUCED_DOSE", None, None, 0, None, 60.0, 0.0),
        # comportamento atual: valor abaixo de 55 e ELEVADO para 55 e a
        # penalidade fica 0 (max(0, adjusted - capped)).
        (40.0, "REDUCED_DOSE", None, None, 0, None, 55.0, 0.0),
        # READY nao altera.
        (88.8, "READY", None, None, 0, None, 88.8, 0.0),
        # comportamento atual: status desconhecido cai no ramo READY (sem teto).
        (99.0, "STATUS_INEXISTENTE", True, None, 3, 0.5, 99.0, 0.0),
    ],
)
def test_reconcile_recovery_pct(
    adjusted, status, joint_pain, muscle_pain, signals, perf, expected_value, expected_penalty
):
    value, penalty = MRS.reconcile_recovery_pct(adjusted, status, joint_pain, muscle_pain, signals, perf)
    assert value == pytest.approx(expected_value)
    assert penalty == pytest.approx(expected_penalty)


def test_reconcile_nunca_contradiz_classify_readiness():
    """Propriedade mantida: o numero final sempre cabe na faixa do status."""
    grid = [
        (adjusted, perf, drop, pain, joint)
        for adjusted in (10.0, 54.9, 55.0, 74.9, 75.0, 100.0)
        for perf in (None, 0.85, 0.92, 1.0)
        for drop in (False, True)
        for pain in (None, 3, 5, 8)
        for joint in (None, True)
    ]
    for adjusted, perf, drop, pain, joint in grid:
        status = MRS.classify_readiness(adjusted, perf, drop, pain, joint)
        signals = MRS.count_negative_signals(perf, drop, pain)
        value, _ = MRS.reconcile_recovery_pct(adjusted, status, joint, pain, signals, perf)
        if status == "NO_DIRECT_WORK":
            assert value < 55
        elif status == "REDUCED_DOSE":
            assert 55 <= value < 75
        else:
            assert value >= 75


def test_recommendations_volume_multiplier_por_status():
    # Consumido pelo planner v2 como multiplicador de dose.
    assert MRS._RECOMMENDATIONS["READY"]["volume_multiplier"] == 1.0
    assert MRS._RECOMMENDATIONS["REDUCED_DOSE"]["volume_multiplier"] == 0.75
    assert MRS._RECOMMENDATIONS["NO_DIRECT_WORK"]["volume_multiplier"] == 0.0

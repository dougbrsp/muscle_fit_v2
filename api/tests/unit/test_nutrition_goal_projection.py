"""Projeção até o alvo de peso ou %G (N1; dono `domain/nutrition/phases.py`)."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from app.domain.nutrition import labels as lb
from app.domain.nutrition import phases as ph
from app.domain.nutrition import strategy as st

S = st.Strategy
PS = ph.ProjectionStatus
HOJE = date(2026, 11, 2)


def test_cutting_por_peso_projeta_prazo_composto_e_progresso():
    p = ph.project_goal(S.CUTTING, 90.0, goal_weight_kg=80.0, start_weight_kg=100.0, as_of=HOJE)
    esperado = math.log(80 / 90) / math.log(1 - st.CUT_WEEKLY_RATE_SHARE_DEFAULT)
    assert p.status == PS.PROJECTED and p.goal_kind == ph.GoalKind.WEIGHT
    assert p.weeks == pytest.approx(esperado)
    assert p.eta_date == HOJE + timedelta(days=math.ceil(esperado * 7))
    assert p.progress_percent == pytest.approx(50.0)
    assert p.weekly_rate_share == st.CUT_WEEKLY_RATE_SHARE_DEFAULT and p.reasons == ()


def test_ritmo_informado_vale_sobre_o_default():
    lento = ph.project_goal(S.CUTTING, 90.0, goal_weight_kg=80.0, weekly_rate_share=0.005)
    rapido = ph.project_goal(S.CUTTING, 90.0, goal_weight_kg=80.0, weekly_rate_share=0.01)
    assert rapido.weeks < lento.weeks
    assert lento.eta_date is None  # sem relógio: só com `as_of`


def test_bulking_por_peso_usa_o_piso_do_ritmo_de_ganho():
    p = ph.project_goal(S.BULKING, 70.0, goal_weight_kg=76.0, start_weight_kg=70.0)
    assert p.status == PS.PROJECTED
    assert p.weeks == pytest.approx(math.log(76 / 70) / math.log(1 + st.BULK_WEEKLY_RATE_SHARE_RANGE[0]))
    assert p.progress_percent == pytest.approx(0.0)


def test_sem_ponto_de_partida_nao_inventa_progresso():
    p = ph.project_goal(S.CUTTING, 90.0, goal_weight_kg=80.0)
    assert p.progress_percent is None and p.reasons == (ph.REASON_MISSING_START,)
    assert p.status == PS.PROJECTED and p.weeks is not None


def test_cutting_por_gordura_supoe_mlg_constante():
    # 100 kg a 25% -> MLG 75 kg; 15% -> peso-alvo 75 / 0,85 = 88,235 kg.
    p = ph.project_goal(
        S.CUTTING, 100.0, goal_body_fat_percent=15.0, current_body_fat_percent=25.0, start_body_fat_percent=30.0
    )
    assert p.status == PS.PROJECTED and p.goal_kind == ph.GoalKind.BODY_FAT
    assert p.target_weight_kg == pytest.approx(75.0 / 0.85)
    assert p.weeks == pytest.approx(math.log((75.0 / 0.85) / 100.0) / math.log(1 - st.CUT_WEEKLY_RATE_SHARE_DEFAULT))
    assert p.progress_percent == pytest.approx((30 - 25) / (30 - 15) * 100)
    assert p.current == 25.0 and p.target == 15.0


def test_bulking_por_gordura_ate_o_teto():
    p = ph.project_goal(S.BULKING, 80.0, goal_body_fat_percent=15.0, current_body_fat_percent=10.0)
    assert p.status == PS.PROJECTED and p.target_weight_kg == pytest.approx(72.0 / 0.85)


def test_alvo_atingido_no_cutting_e_no_bulking():
    cut = ph.project_goal(S.CUTTING, 79.9, goal_weight_kg=80.0, start_weight_kg=90.0, as_of=HOJE)
    assert cut.status == PS.GOAL_REACHED and cut.weeks == 0.0 and cut.progress_percent == 100.0
    assert cut.eta_date == HOJE
    bulk = ph.project_goal(S.BULKING, 85.0, goal_weight_kg=84.0, start_weight_kg=75.0)
    assert bulk.status == PS.GOAL_REACHED
    gordura = ph.project_goal(
        S.CUTTING, 80.0, goal_body_fat_percent=15.0, current_body_fat_percent=14.0, start_body_fat_percent=20.0
    )
    assert gordura.status == PS.GOAL_REACHED


def test_alvo_dentro_da_tolerancia_conta_como_atingido():
    assert ph.project_goal(S.CUTTING, 80.05, goal_weight_kg=80.0).status == PS.GOAL_REACHED
    assert ph.project_goal(S.BULKING, 15.0 and 80.0, goal_weight_kg=80.05).status == PS.GOAL_REACHED


def test_alvo_do_lado_errado_e_incoerente_sem_partida_que_o_explique():
    cut = ph.project_goal(S.CUTTING, 70.0, goal_weight_kg=80.0)
    assert cut.status == PS.INCOHERENT and cut.reasons == (ph.REASON_TARGET_AGAINST_STRATEGY,)
    assert cut.weeks is None
    bulk = ph.project_goal(S.BULKING, 90.0, goal_weight_kg=80.0, start_weight_kg=85.0)
    assert bulk.status == PS.INCOHERENT
    gordura = ph.project_goal(S.CUTTING, 80.0, goal_body_fat_percent=20.0, current_body_fat_percent=15.0)
    assert gordura.status == PS.INCOHERENT


def test_manutencao_so_atinge_ou_e_incoerente():
    assert ph.project_goal(S.MAINTENANCE, 80.0, goal_weight_kg=80.0).status == PS.GOAL_REACHED
    longe = ph.project_goal(S.MAINTENANCE, 80.0, goal_weight_kg=75.0)
    assert longe.status == PS.INCOHERENT and longe.reasons == (ph.REASON_TARGET_MOVES_WITHOUT_STRATEGY,)


@pytest.mark.parametrize(
    ("kwargs", "motivo"),
    [
        ({"current_weight_kg": None, "goal_weight_kg": 80.0}, ph.REASON_MISSING_WEIGHT),
        ({"current_weight_kg": 0, "goal_weight_kg": 80.0}, ph.REASON_MISSING_WEIGHT),
        ({"current_weight_kg": 90.0}, ph.REASON_MISSING_TARGET),
        ({"current_weight_kg": 90.0, "goal_weight_kg": 80.0, "goal_body_fat_percent": 15.0}, ph.REASON_TWO_TARGETS),
        ({"current_weight_kg": 90.0, "goal_weight_kg": 5.0}, ph.REASON_INVALID_TARGET),
        ({"current_weight_kg": 90.0, "goal_weight_kg": "x"}, ph.REASON_INVALID_TARGET),
        ({"current_weight_kg": 90.0, "goal_body_fat_percent": 1.0, "current_body_fat_percent": 20.0}, ph.REASON_INVALID_TARGET),
        ({"current_weight_kg": 90.0, "goal_body_fat_percent": 15.0}, ph.REASON_MISSING_BODY_FAT),
        (
            {"current_weight_kg": 90.0, "goal_body_fat_percent": 15.0, "current_body_fat_percent": 99.0},
            ph.REASON_MISSING_BODY_FAT,
        ),
    ],
)
def test_dados_insuficientes_devolvem_o_motivo_sem_numero(kwargs, motivo):
    p = ph.project_goal(S.CUTTING, **kwargs)
    assert p.status == PS.INSUFFICIENT_DATA and p.reasons == (motivo,)
    assert p.weeks is None and p.eta_date is None and p.progress_percent is None


def test_progresso_fica_entre_0_e_100():
    # Voltou acima do ponto de partida: 0%, nunca negativo.
    p = ph.project_goal(S.CUTTING, 105.0, goal_weight_kg=80.0, start_weight_kg=100.0)
    assert p.progress_percent == 0.0


def test_rotulos_e_dicionario():
    for status in PS:
        assert lb.PROJECTION_STATUS_LABELS[status]
    for kind in ph.GoalKind:
        assert lb.GOAL_KIND_LABELS[kind]
    p = ph.project_goal(S.CUTTING, 90.0, goal_weight_kg=80.0, start_weight_kg=100.0, as_of=HOJE)
    dados = p.to_dict()
    assert dados["status"] == "projected" and dados["eta_date"] == p.eta_date.isoformat()
    for motivo in (
        ph.REASON_MISSING_WEIGHT,
        ph.REASON_MISSING_BODY_FAT,
        ph.REASON_MISSING_TARGET,
        ph.REASON_TWO_TARGETS,
        ph.REASON_INVALID_TARGET,
        ph.REASON_MISSING_RATE,
        ph.REASON_MISSING_START,
        ph.REASON_TARGET_AGAINST_STRATEGY,
        ph.REASON_TARGET_MOVES_WITHOUT_STRATEGY,
    ):
        assert lb.limitation_label(motivo) != motivo

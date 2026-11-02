"""Projeção até o alvo de peso ou %G (N1; dono `domain/nutrition/phases.py`)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.domain.nutrition import labels as lb
from app.domain.nutrition import phases as ph
from app.domain.nutrition import strategy as st

S = st.Strategy
PS = ph.ProjectionStatus
HOJE = date(2026, 11, 2)
CUT = st.StrategyParams.defaults_for(S.CUTTING)
BULK = st.StrategyParams.defaults_for(S.BULKING)
MANT = st.StrategyParams.defaults_for(S.MAINTENANCE)


def test_cutting_por_peso_projeta_prazo_composto_e_progresso():
    # 90 -> 80 kg a 0,7%/semana composto: ln(80/90) / ln(0,993) = 16,767 semanas (conta feita à parte).
    p = ph.project_goal(CUT, 90.0, goal_weight_kg=80.0, start_weight_kg=100.0, as_of=HOJE)
    assert p.status == PS.PROJECTED and p.goal_kind == ph.GoalKind.WEIGHT
    assert p.weeks == pytest.approx(16.767, abs=1e-3)
    assert p.eta_date == HOJE + timedelta(days=118)  # ceil(16,767 × 7) = 118
    assert p.progress_percent == pytest.approx(50.0)
    assert p.weekly_rate_share == 0.007 and p.reasons == () and not p.is_minimum_estimate


def test_ritmo_informado_vale_sobre_o_default():
    lento = ph.project_goal(CUT, 90.0, goal_weight_kg=80.0, weekly_rate_share=0.005)
    rapido = ph.project_goal(CUT, 90.0, goal_weight_kg=80.0, weekly_rate_share=0.01)
    assert rapido.weeks < lento.weeks
    assert lento.eta_date is None  # sem relógio: só com `as_of`


def test_bulking_por_peso_usa_o_piso_do_ritmo_de_ganho():
    # 70 -> 76 kg a 0,25%/semana: ln(76/70) / ln(1,0025) = 32,936 semanas.
    p = ph.project_goal(BULK, 70.0, goal_weight_kg=76.0, start_weight_kg=70.0)
    assert p.status == PS.PROJECTED and p.weeks == pytest.approx(32.936, abs=1e-3)
    assert p.progress_percent == pytest.approx(0.0)


def test_bulking_usa_o_superavit_do_plano_quando_ha_tdee_e_massa_gorda():
    # Superávit de 10% de 2800 kcal = 280 kcal/dia; MG 20 kg -> fração de gordura 20/30,4 = 0,658,
    # densidade 0,658 × 9400 + 0,342 × 1800 = 6800 kcal/kg; 280 × 7 / 6800 = 0,2882 kg/semana = 0,360% de 80 kg.
    p = ph.project_goal(BULK, 80.0, goal_weight_kg=84.0, tdee_kcal=2800.0, fat_mass_kg=20.0)
    assert p.weekly_rate_share == pytest.approx(0.00360, abs=2e-5)
    sem_dados = ph.project_goal(BULK, 80.0, goal_weight_kg=84.0)
    assert sem_dados.weekly_rate_share == st.BULK_WEEKLY_RATE_SHARE_RANGE[0]
    assert p.weeks < sem_dados.weeks


def test_sem_ponto_de_partida_nao_inventa_progresso():
    p = ph.project_goal(CUT, 90.0, goal_weight_kg=80.0)
    assert p.progress_percent is None and p.reasons == (ph.REASON_MISSING_START,)
    assert p.status == PS.PROJECTED and p.weeks is not None


@pytest.mark.parametrize(
    ("params", "atual", "alvo", "partida"),
    [
        (CUT, 90.0, 80.0, 75.0),  # partida abaixo do alvo: caminho de ganho, não de perda
        (CUT, 90.0, 80.0, 80.0),  # partida igual ao alvo
        (BULK, 70.0, 76.0, 80.0),  # espelho no bulking
        (BULK, 70.0, 76.0, 76.0),
    ],
)
def test_partida_do_lado_errado_ou_igual_ao_alvo_nao_vira_progresso(params, atual, alvo, partida):
    p = ph.project_goal(params, atual, goal_weight_kg=alvo, start_weight_kg=partida)
    assert p.status == PS.PROJECTED and p.progress_percent is None
    assert p.reasons == (ph.REASON_START_WRONG_SIDE,)


def test_cutting_por_gordura_supoe_mlg_constante_e_diz_que_e_estimativa_minima():
    # 100 kg a 25% -> MLG 75 kg; 15% -> peso 75 / 0,85 = 88,235 kg; ln(0,88235) / ln(0,993) = 17,818 semanas.
    p = ph.project_goal(
        CUT, 100.0, goal_body_fat_percent=15.0, current_body_fat_percent=25.0, start_body_fat_percent=30.0
    )
    assert p.status == PS.PROJECTED and p.goal_kind == ph.GoalKind.BODY_FAT
    assert p.target_weight_kg == pytest.approx(88.235, abs=1e-3)
    assert p.weeks == pytest.approx(17.818, abs=1e-3)
    assert p.progress_percent == pytest.approx(33.333, abs=1e-3)  # (30 - 25) / (30 - 15)
    assert p.current == 25.0 and p.target == 15.0
    assert p.is_minimum_estimate and ph.REASON_LEAN_MASS_CONSTANT in p.reasons
    assert p.to_dict()["is_minimum_estimate"] is True


def test_bulking_por_gordura_ate_o_teto():
    p = ph.project_goal(BULK, 80.0, goal_body_fat_percent=15.0, current_body_fat_percent=10.0)
    assert p.status == PS.PROJECTED and p.target_weight_kg == pytest.approx(84.706, abs=1e-3)  # 72 / 0,85
    assert p.is_minimum_estimate


def test_alvo_atingido_no_cutting_e_no_bulking():
    cut = ph.project_goal(CUT, 79.9, goal_weight_kg=80.0, start_weight_kg=90.0, as_of=HOJE)
    assert cut.status == PS.GOAL_REACHED and cut.weeks == 0.0 and cut.progress_percent == 100.0
    assert cut.eta_date == HOJE
    bulk = ph.project_goal(BULK, 85.0, goal_weight_kg=84.0, start_weight_kg=75.0)
    assert bulk.status == PS.GOAL_REACHED
    gordura = ph.project_goal(
        CUT, 80.0, goal_body_fat_percent=15.0, current_body_fat_percent=14.0, start_body_fat_percent=20.0
    )
    assert gordura.status == PS.GOAL_REACHED


def test_alvo_dentro_da_tolerancia_conta_como_atingido():
    assert ph.project_goal(CUT, 80.05, goal_weight_kg=80.0).status == PS.GOAL_REACHED
    assert ph.project_goal(BULK, 80.0, goal_weight_kg=80.05).status == PS.GOAL_REACHED


def test_alvo_do_lado_errado_e_incoerente_sem_partida_que_o_explique():
    cut = ph.project_goal(CUT, 70.0, goal_weight_kg=80.0)
    assert cut.status == PS.INCOHERENT and cut.reasons == (ph.REASON_TARGET_AGAINST_STRATEGY,)
    assert cut.weeks is None
    bulk = ph.project_goal(BULK, 90.0, goal_weight_kg=80.0, start_weight_kg=85.0)
    assert bulk.status == PS.INCOHERENT
    gordura = ph.project_goal(CUT, 80.0, goal_body_fat_percent=20.0, current_body_fat_percent=15.0)
    assert gordura.status == PS.INCOHERENT


def test_manutencao_so_atinge_ou_e_incoerente():
    assert ph.project_goal(MANT, 80.0, goal_weight_kg=80.0).status == PS.GOAL_REACHED
    longe = ph.project_goal(MANT, 80.0, goal_weight_kg=75.0)
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
        (
            {"current_weight_kg": 90.0, "goal_body_fat_percent": 1.0, "current_body_fat_percent": 20.0},
            ph.REASON_INVALID_TARGET,
        ),
        ({"current_weight_kg": 90.0, "goal_body_fat_percent": 15.0}, ph.REASON_MISSING_BODY_FAT),
        (
            {"current_weight_kg": 90.0, "goal_body_fat_percent": 15.0, "current_body_fat_percent": 99.0},
            ph.REASON_MISSING_BODY_FAT,
        ),
    ],
)
def test_dados_insuficientes_devolvem_o_motivo_sem_numero(kwargs, motivo):
    p = ph.project_goal(CUT, **kwargs)
    assert p.status == PS.INSUFFICIENT_DATA and p.reasons == (motivo,)
    assert p.weeks is None and p.eta_date is None and p.progress_percent is None


@pytest.mark.parametrize(
    "ritmo", [0, -0.007, "abc", float("nan"), float("inf"), 1.0, 2.0, 0.02, 0.001, 1e-17, 1e-12, 1e-300, True]
)
def test_ritmo_informado_invalido_ou_fora_da_faixa_nao_projeta_nem_estoura(ritmo):
    p = ph.project_goal(CUT, 90.0, goal_weight_kg=80.0, weekly_rate_share=ritmo)
    assert p.status == PS.INSUFFICIENT_DATA and p.reasons == (ph.REASON_MISSING_RATE,)
    assert p.weeks is None


def test_ritmo_derivado_minusculo_nao_estoura():
    # Superávit de 1e-15 do TDEE: o ritmo derivado é menor que a resolução do float (log(1 + r) = 0).
    p = ph.project_goal(replace(BULK, surplus_share=1e-15), 80.0, goal_weight_kg=84.0, tdee_kcal=2800.0, fat_mass_kg=20.0)
    assert p.status == PS.INSUFFICIENT_DATA and p.reasons == (ph.REASON_BEYOND_HORIZON,)


def test_horizonte_maximo_da_projecao():
    # 20 -> 350 kg no piso do ritmo de ganho passa de 1.100 semanas.
    p = ph.project_goal(BULK, 20.0, goal_weight_kg=350.0)
    assert p.status == PS.INSUFFICIENT_DATA and p.reasons == (ph.REASON_BEYOND_HORIZON,)
    assert ph.PROJECTION_MAX_WEEKS == 520.0


def test_progresso_fica_entre_0_e_100():
    # Voltou acima do ponto de partida: 0%, nunca negativo.
    p = ph.project_goal(CUT, 105.0, goal_weight_kg=80.0, start_weight_kg=100.0)
    assert p.progress_percent == 0.0


def test_rotulos_e_dicionario():
    for status in PS:
        assert lb.PROJECTION_STATUS_LABELS[status]
    for kind in ph.GoalKind:
        assert lb.GOAL_KIND_LABELS[kind]
    p = ph.project_goal(CUT, 90.0, goal_weight_kg=80.0, start_weight_kg=100.0, as_of=HOJE)
    dados = p.to_dict()
    assert dados["status"] == "projected" and dados["eta_date"] == "2027-02-28"
    for motivo in (
        ph.REASON_MISSING_WEIGHT,
        ph.REASON_MISSING_BODY_FAT,
        ph.REASON_MISSING_TARGET,
        ph.REASON_TWO_TARGETS,
        ph.REASON_INVALID_TARGET,
        ph.REASON_MISSING_RATE,
        ph.REASON_MISSING_START,
        ph.REASON_START_WRONG_SIDE,
        ph.REASON_MISSING_TARGET_WEIGHT,
        ph.REASON_BEYOND_HORIZON,
        ph.REASON_LEAN_MASS_CONSTANT,
        ph.REASON_TARGET_AGAINST_STRATEGY,
        ph.REASON_TARGET_MOVES_WITHOUT_STRATEGY,
    ):
        assert lb.limitation_label(motivo) != motivo


def test_project_goal_usa_o_ritmo_do_dono():
    assert st.weekly_rate_share_for(CUT) == st.CUT_WEEKLY_RATE_SHARE_DEFAULT
    assert st.weekly_rate_share_for(replace(CUT, weekly_rate_share=0.005)) == 0.005
    assert st.weekly_rate_share_for(MANT) is None
    p = ph.project_goal(replace(CUT, weekly_rate_share=0.005), 90.0, goal_weight_kg=80.0)
    assert p.weekly_rate_share == 0.005


def test_ritmo_derivado_fora_da_faixa_nao_e_limitado_mas_avisa():
    # Superávit de 20% de 2800 kcal = 560 kcal/dia; MG 8 kg -> fração de gordura 8/18,4 = 0,4348, densidade
    # 5.104 kcal/kg; 560 × 7 / 5104 = 0,768 kg/semana = 0,96% de 80 kg, acima do teto de 0,5%.
    params = replace(BULK, surplus_share=0.20)
    p = ph.project_goal(params, 80.0, goal_weight_kg=84.0, tdee_kcal=2800.0, fat_mass_kg=8.0)
    assert p.status == PS.PROJECTED
    assert p.weekly_rate_share == pytest.approx(0.0096, abs=1e-4)  # o ritmo do plano, sem corte
    assert ph.REASON_RATE_OUTSIDE_RANGE in p.reasons
    assert lb.limitation_label(ph.REASON_RATE_OUTSIDE_RANGE) != ph.REASON_RATE_OUTSIDE_RANGE
    dentro = ph.project_goal(BULK, 80.0, goal_weight_kg=84.0, tdee_kcal=2800.0, fat_mass_kg=20.0)
    assert ph.REASON_RATE_OUTSIDE_RANGE not in dentro.reasons
    # Cutting com ritmo do plano fora da faixa (1,5%/semana): projeta o do plano e avisa.
    cut = ph.project_goal(replace(CUT, weekly_rate_share=0.015), 90.0, goal_weight_kg=80.0)
    assert cut.weekly_rate_share == 0.015 and ph.REASON_RATE_OUTSIDE_RANGE in cut.reasons


def test_is_minimum_estimate_so_quando_projetado():
    atingido = ph.project_goal(CUT, 80.0, goal_body_fat_percent=15.0, current_body_fat_percent=14.0, start_body_fat_percent=20.0)
    incoerente = ph.project_goal(CUT, 80.0, goal_body_fat_percent=20.0, current_body_fat_percent=15.0)
    faltando = ph.project_goal(CUT, 80.0, goal_body_fat_percent=15.0)
    projetado = ph.project_goal(CUT, 100.0, goal_body_fat_percent=15.0, current_body_fat_percent=25.0)
    assert [x.is_minimum_estimate for x in (atingido, incoerente, faltando, projetado)] == [False, False, False, True]
    assert atingido.status == PS.GOAL_REACHED and incoerente.status == PS.INCOHERENT


def test_cutting_mlg_constante_e_o_maior_peso_e_o_prazo_real_e_maior():
    # 100 kg a 25%: MLG 75 kg, alvo 15% -> 88,2 kg com MLG constante. Se a MLG cair para 72 kg, o peso final
    # real é 72 / 0,85 = 84,7 kg: MENOR que o calculado, então falta mais peso a perder.
    p = ph.project_goal(CUT, 100.0, goal_body_fat_percent=15.0, current_body_fat_percent=25.0)
    assert p.target_weight_kg == pytest.approx(88.235, abs=1e-3)
    real = ph.project_goal(CUT, 100.0, goal_weight_kg=72.0 / 0.85)
    assert real.target_weight_kg == pytest.approx(84.706, abs=1e-3) and real.target_weight_kg < p.target_weight_kg
    assert real.weeks > p.weeks


def test_bulking_mlg_constante_e_o_menor_peso_e_o_prazo_real_e_maior():
    # 80 kg a 10%: MLG 72 kg, alvo 15% -> 84,7 kg. Se a MLG crescer para 74 kg, o peso final real é 87,1 kg.
    p = ph.project_goal(BULK, 80.0, goal_body_fat_percent=15.0, current_body_fat_percent=10.0)
    real = ph.project_goal(BULK, 80.0, goal_weight_kg=74.0 / 0.85)
    assert real.target_weight_kg == pytest.approx(87.059, abs=1e-3) and real.target_weight_kg > p.target_weight_kg
    assert real.weeks > p.weeks

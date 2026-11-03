"""Projeção do plano com gramas manuais (`plan.project_plan_goal`): o `project_goal` com o ritmo do plano roda
PRIMEIRO, então alvo já alcançado, dado faltando (peso, %G, alvo) e alvo incoerente valem como estão; quando as
gramas é que recusam a projeção (sem massa gorda, sem TDEE, contra a estratégia) o resultado mantém o tipo do alvo, o
atual, o alvo e o % do caminho, e tira só o que dependeria das gramas (prazo, ritmo, estimativa mínima)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from app.domain.nutrition import plan as pl
from app.domain.nutrition import phases
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.phases import GoalKind, ProjectionStatus

HOJE = date(2026, 10, 17)
SEM_GORDURA = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=79.0, activity_level=ActivityLevel.MODERATE)
COM_GORDURA = replace(SEM_GORDURA, body_fat_percent=20.0)
MANUAL_CUT = {"protein_g": 180, "carb_g": 200, "fat_g": 70}


def _plano(**alvo):
    return pl.build_plan_settings(strategy="cutting", manual_targets=MANUAL_CUT, **alvo).settings


def test_alvo_ja_alcancado_com_gramas_e_sem_percentual_de_gordura_e_alcancado():
    # Peso 79 já abaixo do alvo de 80, partida 85: alcançado; as gramas e a falta de massa gorda não mudam isso.
    p = pl.project_plan_goal(_plano(goal_weight_kg=80.0), SEM_GORDURA, 2800, HOJE, start_weight_kg=85.0)
    assert p.status == ProjectionStatus.GOAL_REACHED and p.reasons == ()
    assert p.goal_kind == GoalKind.WEIGHT and p.progress_percent == 100.0


def test_recusa_por_falta_de_massa_gorda_mantem_o_caminho_ja_feito():
    # Peso 79, partida 82, alvo 75: 3 de 7 kg = 42,9%. Sem massa gorda não há prazo, mas o caminho feito fica.
    p = pl.project_plan_goal(_plano(goal_weight_kg=75.0), SEM_GORDURA, 2800, HOJE, start_weight_kg=82.0)
    assert p.status == ProjectionStatus.INSUFFICIENT_DATA and p.reasons == (pl.REASON_MANUAL_MISSING_FAT_MASS,)
    assert p.goal_kind == GoalKind.WEIGHT and p.current == 79.0 and p.target == 75.0
    assert p.progress_percent == pytest.approx(100 * 3 / 7)
    assert p.weeks is None and p.eta_date is None and p.weekly_rate_share is None and p.is_minimum_estimate is False


def test_recusa_por_falta_de_tdee_e_por_gramas_contra_a_estrategia_mantem_o_caminho():
    sem_tdee = pl.project_plan_goal(_plano(goal_weight_kg=75.0), COM_GORDURA, None, HOJE, start_weight_kg=82.0)
    assert sem_tdee.status == ProjectionStatus.INSUFFICIENT_DATA and sem_tdee.reasons == (pl.REASON_MANUAL_MISSING_TDEE,)
    assert sem_tdee.progress_percent == pytest.approx(100 * 3 / 7) and sem_tdee.goal_kind == GoalKind.WEIGHT
    contra = pl.project_plan_goal(_plano(goal_weight_kg=75.0), COM_GORDURA, 1500, HOJE, start_weight_kg=82.0)  # gramas = 2.150 kcal > 1.500
    assert contra.status == ProjectionStatus.INCOHERENT and contra.reasons == (pl.REASON_MANUAL_AGAINST_STRATEGY,)
    assert contra.progress_percent == pytest.approx(100 * 3 / 7) and contra.weeks is None and contra.weekly_rate_share is None


def test_alvo_de_gordura_recusado_pelas_gramas_nao_carrega_a_estimativa_minima():
    p = pl.project_plan_goal(_plano(goal_body_fat_percent=12.0), COM_GORDURA, 1500, HOJE, start_body_fat_percent=24.0)
    assert p.status == ProjectionStatus.INCOHERENT and p.goal_kind == GoalKind.BODY_FAT
    assert p.is_minimum_estimate is False and p.progress_percent == pytest.approx(100 * 4 / 12)


def test_plano_manual_sem_pesagem_pede_o_peso():
    sem_peso = replace(SEM_GORDURA, weight_kg=None)
    p = pl.project_plan_goal(_plano(goal_weight_kg=75.0), sem_peso, 2800, HOJE)
    assert p.status == ProjectionStatus.INSUFFICIENT_DATA and p.reasons == (phases.REASON_MISSING_WEIGHT,)


def test_alvo_de_gordura_sem_percentual_atual_pede_o_percentual_antes_das_gramas():
    p = pl.project_plan_goal(_plano(goal_body_fat_percent=12.0), SEM_GORDURA, 2800, HOJE)
    assert p.status == ProjectionStatus.INSUFFICIENT_DATA and p.reasons == (phases.REASON_MISSING_BODY_FAT,)


def test_gramas_dentro_da_estrategia_ainda_projetam_pelo_saldo_das_gramas():
    p = pl.project_plan_goal(_plano(goal_weight_kg=75.0), COM_GORDURA, 2800, HOJE, start_weight_kg=82.0)
    assert p.status == ProjectionStatus.PROJECTED and p.weeks and p.weeks > 0
    plano_sem_gramas = pl.build_plan_settings(strategy="cutting", goal_weight_kg=75.0).settings
    padrao = pl.project_plan_goal(plano_sem_gramas, COM_GORDURA, 2800, HOJE, start_weight_kg=82.0)
    assert p.weekly_rate_share != padrao.weekly_rate_share  # o ritmo veio das gramas, não do padrão


def test_sem_gramas_o_resultado_e_o_do_project_goal_do_plano():
    cfg = pl.build_plan_settings(strategy="cutting", goal_weight_kg=75.0).settings
    direto = phases.project_goal(
        cfg.strategy_params(), 79.0, goal_weight_kg=75.0, current_body_fat_percent=20.0,
        start_weight_kg=82.0, as_of=HOJE, tdee_kcal=2800, fat_mass_kg=16.0,
    )
    assert pl.project_plan_goal(cfg, COM_GORDURA, 2800, HOJE, start_weight_kg=82.0) == direto

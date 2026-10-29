"""M76 (decisão do usuário 2026-10-07): "muito abaixo do planejado" volta,
calculado no backend.

Dono: `volume_service.planned_volume_severity` (`FAR_BELOW_PLANNED_RATIO`),
gravidade ADITIVA ao lado de `classify_planned_volume_status` — o status
continua `BELOW_TARGET`. A tabela de `/registro-exercicios` recebe
`assessment.severity` e só pinta.
"""

from __future__ import annotations

import pytest

from app.intelligence.volume_service import (
    FAR_BELOW_PLANNED_RATIO,
    PLANNED_VOLUME_SEVERITIES,
    PLANNED_VOLUME_STATUSES,
    assess_activation_against_target,
    assess_activation_rows,
    classify_planned_volume_status,
    planned_volume_severity,
)

PLANEJADO = 10.0


@pytest.mark.parametrize(
    ("pct", "status", "severity"),
    [
        (0.49, "BELOW_TARGET", "FAR_BELOW"),
        (0.50, "BELOW_TARGET", None),
        (0.94, "BELOW_TARGET", None),
        (0.95, "ON_TARGET", None),
        (1.05, "ON_TARGET", None),
        (1.06, "ABOVE_TARGET", None),
    ],
)
def test_faixas_do_planejado(pct, status, severity):
    realizado = PLANEJADO * pct
    assert classify_planned_volume_status(realizado, PLANEJADO) == status
    assert planned_volume_severity(realizado, PLANEJADO) == severity


def test_constante_e_vocabulario():
    assert FAR_BELOW_PLANNED_RATIO == 0.5
    assert PLANNED_VOLUME_SEVERITIES == ("FAR_BELOW",)
    # Gravidade não é um quarto estado: o contrato dos 3 estados não muda.
    assert PLANNED_VOLUME_STATUSES == ("BELOW_TARGET", "ON_TARGET", "ABOVE_TARGET")


@pytest.mark.parametrize("realizado", [0.0, 0.5, 3.0])
def test_planejado_zero_nunca_e_deficit(realizado):
    assert planned_volume_severity(realizado, 0.0) is None
    assert planned_volume_severity(realizado, None) is None


def test_realizado_zero_com_planejado_e_muito_abaixo():
    assert planned_volume_severity(0.0, PLANEJADO) == "FAR_BELOW"
    assert planned_volume_severity(None, PLANEJADO) == "FAR_BELOW"


@pytest.mark.parametrize(
    ("pct", "severity"),
    [(0.49, "FAR_BELOW"), (0.50, None), (0.94, None), (0.95, None), (1.05, None), (1.06, None)],
)
def test_assessment_do_alvo_do_slot_propaga_a_gravidade(pct, severity):
    alvo = {"sets_count": PLANEJADO, "max_weekly_sets": None, "target_source": "template_slot"}
    avaliacao = assess_activation_against_target(PLANEJADO * pct, alvo)
    assert avaliacao["status_domain"] == "plannedVolume"
    assert avaliacao["status"] == classify_planned_volume_status(PLANEJADO * pct, PLANEJADO)
    assert avaliacao["severity"] == severity


def test_alvo_do_slot_zero_cai_na_meta_generica_sem_gravidade():
    alvo = {"sets_count": 0.0, "max_weekly_sets": None, "target_source": "template_slot"}
    avaliacao = assess_activation_against_target(0.0, alvo)
    assert avaliacao["target_source"] == "generic_fallback"
    assert avaliacao["severity"] is None


def test_teto_do_slot_passado_nao_tem_gravidade():
    alvo = {"sets_count": 6.0, "max_weekly_sets": 7.0, "target_source": "template_slot"}
    avaliacao = assess_activation_against_target(8.0, alvo)
    assert avaliacao["status"] == "ABOVE_MAX"
    assert avaliacao["severity"] is None


def test_meta_generica_e_do_tipo_de_treino_ficam_sem_gravidade():
    """Escopo da decisão: só o alvo do slot ganha o nível extra."""
    assert assess_activation_against_target(0.5, None)["severity"] is None
    alvo_tipo = {"sets_count": 12.0, "min_weekly_sets": 8.0, "max_weekly_sets": 18.0, "target_source": "training_type"}
    avaliacao = assess_activation_against_target(1.0, alvo_tipo)
    assert avaliacao["status"] == "BELOW_MIN"
    assert avaliacao["severity"] is None


def test_linhas_levam_a_gravidade():
    linhas = assess_activation_rows(
        [{"muscle_group": "Peitoral", "total_equivalent": 2.0}, {"muscle_group": "Bíceps", "total_equivalent": 2.0}],
        [{"muscle_group_name": "Peitoral", "sets_count": 6.0, "target_source": "template_slot"}],
    )
    assert linhas[0]["assessment"]["severity"] == "FAR_BELOW"
    assert linhas[1]["assessment"]["severity"] is None

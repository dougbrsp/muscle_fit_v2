"""Motor dono da fadiga prévia (`domain/prior_fatigue.py`)."""

from __future__ import annotations

import pytest

from app.domain import prior_fatigue as pf
from app.domain.prior_fatigue import FatigueItem, MuscleContribution, PriorFatigueBand

CHEST, TRICEPS, SHOULDERS, BACK = 1, 2, 3, 4


def c(group_id, role="PRIMARY", weight=1.0, name=None):
    return MuscleContribution(group_id, name or f"G{group_id}", role, weight)


def item(sets, *contributions):
    return FatigueItem(sets=sets, contributions=tuple(contributions))


BENCH = (c(CHEST), c(TRICEPS, "SECONDARY", 0.5), c(SHOULDERS, "SECONDARY", 0.5))
TRICEPS_EXT = (c(TRICEPS),)
ROW = (c(BACK), c(SHOULDERS, "STABILIZER", 0.25))


def test_primeiro_item_e_descansado_com_nivel_zero():
    [first] = pf.prior_fatigue_for_sequence([item(3, *BENCH)])
    assert first.muscle_group_id == CHEST
    assert first.prior_sets == 0
    assert first.level == 0.0
    assert first.band is PriorFatigueBand.RESTED
    assert first.as_dict()["band_label"] == pf.PRIOR_FATIGUE_BAND_LABELS[PriorFatigueBand.RESTED]


def test_exercicio_de_outro_grupo_antes_nao_fatiga():
    _, bench = pf.prior_fatigue_for_sequence([item(4, *ROW), item(3, *BENCH)])
    assert bench.muscle_group_id == CHEST
    assert bench.prior_sets == 0
    assert bench.band is PriorFatigueBand.RESTED


def test_secundario_pondera_pelo_stimulus_weight():
    _, triceps = pf.prior_fatigue_for_sequence([item(4, *BENCH), item(3, *TRICEPS_EXT)])
    assert triceps.muscle_group_id == TRICEPS
    assert triceps.prior_sets == pytest.approx(4 * 0.5)


def test_series_do_proprio_item_nao_contam():
    [only] = pf.prior_fatigue_for_sequence([item(10, *BENCH)])
    assert only.prior_sets == 0


def test_ordem_invertida_muda_o_resultado():
    bench_first = pf.prior_fatigue_for_sequence([item(3, *BENCH), item(3, *BENCH), item(3, *TRICEPS_EXT)])
    triceps_first = pf.prior_fatigue_for_sequence([item(3, *TRICEPS_EXT), item(3, *BENCH), item(3, *BENCH)])
    assert [p.prior_sets for p in bench_first] == pytest.approx([0, 3, 3])
    assert [p.prior_sets for p in triceps_first] == pytest.approx([0, 0, 3])
    assert bench_first[2].band is PriorFatigueBand.PRE_FATIGUED
    assert triceps_first[0].band is PriorFatigueBand.RESTED


def test_saturacao_e_faixas():
    sat = pf.PRIOR_FATIGUE_SATURATION_SETS
    assert pf.level_for(0) == 0.0
    assert pf.level_for(sat / 2) == pytest.approx(0.5)
    assert pf.level_for(sat) == 1.0
    assert pf.level_for(sat * 3) == 1.0
    assert pf.band_for(pf.PRIOR_FATIGUE_PRE_FATIGUED_MIN_SETS - 0.01) is PriorFatigueBand.RESTED
    assert pf.band_for(pf.PRIOR_FATIGUE_PRE_FATIGUED_MIN_SETS) is PriorFatigueBand.PRE_FATIGUED
    assert pf.band_for(pf.PRIOR_FATIGUE_HEAVILY_FATIGUED_MIN_SETS) is PriorFatigueBand.HEAVILY_FATIGUED
    assert (
        0 < pf.PRIOR_FATIGUE_PRE_FATIGUED_MIN_SETS < pf.PRIOR_FATIGUE_HEAVILY_FATIGUED_MIN_SETS <= sat
    ), "cortes em ordem e dentro da saturação"

    many = pf.prior_fatigue_for_sequence([item(3, *BENCH)] * 6)
    assert many[-1].prior_sets == pytest.approx(15)
    assert many[-1].level == 1.0
    assert many[-1].band is PriorFatigueBand.HEAVILY_FATIGUED
    levels = [p.level for p in many]
    assert levels == sorted(levels)


def test_musculo_principal_primary_de_maior_peso_com_empate_deterministico():
    assert pf.principal_muscle([c(5, "PRIMARY", 0.8), c(9, "PRIMARY", 1.0)]).muscle_group_id == 9
    assert pf.principal_muscle([c(9), c(5)]).muscle_group_id == 5
    assert pf.principal_muscle([c(9), c(5)][::-1]).muscle_group_id == 5
    # sem PRIMARY: a de maior peso
    assert pf.principal_muscle([c(7, "SECONDARY", 0.5), c(8, "STABILIZER", 0.25)]).muscle_group_id == 7
    assert pf.principal_muscle([]) is None
    assert pf.principal_muscle([c(1, weight=0.0)]) is None


def test_item_sem_contribuicao_devolve_none_e_series_zero_nao_fatigam():
    result = pf.prior_fatigue_for_sequence([item(3), item(0, *BENCH), item(3, *BENCH)])
    assert result[0] is None
    assert result[2].prior_sets == 0


def test_outros_sem_id_casa_pelo_nome():
    outros = MuscleContribution(None, "Outros", "PRIMARY", 1.0)
    _, second = pf.prior_fatigue_for_sequence([item(3, outros), item(3, outros)])
    assert second.muscle_group_id is None and second.muscle_group_name == "Outros"
    assert second.prior_sets == 3


def test_contribution_from_mapping_aceita_a_linha_do_resolvedor():
    row = {"muscle_group_id": "4", "muscle_group_name": "Costas", "role": "SECONDARY", "stimulus_weight": "0.5"}
    assert pf.contribution_from_mapping(row) == MuscleContribution(4, "Costas", "SECONDARY", 0.5)

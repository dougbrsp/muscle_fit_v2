"""Medida da série do Modelo até a ficha do Modo B (Lote T2).

`mode_b.item_measure_and_reps` monta a parte da linha da ficha que depende da
medida: copia `measure`/`duration_seconds_target`/`time_window_seconds` do item
do Modelo e, fora de `reps` (`set_measures.is_rep_based`), deixa `reps_target`
e `suggested_reps` em None. Em `reps` (ou sem medida) o resultado é o de
sempre. `freeze_mode_b.reps_fields_for_measure` aplica a mesma regra ao
congelar."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.prescription_defaults import DEFAULT_REPS_TARGET
from app.domain.set_measures import SetMeasure
from app.generation import freeze_mode_b, mode_b


def _prescricao(reps_target="8-10", reps_max=10):
    return SimpleNamespace(reps_target=reps_target, decision=SimpleNamespace(suggested_reps_max=reps_max))


def test_item_sem_medida_e_o_comportamento_de_antes():
    linha = mode_b.item_measure_and_reps({"reps_target": "12"}, _prescricao())
    assert linha == {
        "measure": SetMeasure.REPS.value,
        "duration_seconds_target": None,
        "time_window_seconds": None,
        "reps_target": "8-10",
        "suggested_reps": 10,
    }
    assert mode_b.item_measure_and_reps({"reps_target": "12"}, None)["reps_target"] == "12"
    assert mode_b.item_measure_and_reps({}, None)["reps_target"] == DEFAULT_REPS_TARGET


def test_item_por_tempo_herda_a_medida_e_fica_sem_reps():
    item = {"measure": SetMeasure.DURATION.value, "duration_seconds_target": 60, "reps_target": None}
    linha = mode_b.item_measure_and_reps(item, _prescricao())
    assert linha["measure"] == SetMeasure.DURATION.value
    assert linha["duration_seconds_target"] == 60
    assert linha["reps_target"] is None and linha["suggested_reps"] is None


def test_item_de_maximo_de_reps_herda_a_janela_e_fica_sem_alvo():
    item = {"measure": SetMeasure.MAX_REPS.value, "time_window_seconds": 30}
    linha = mode_b.item_measure_and_reps(item, None)
    assert linha["measure"] == SetMeasure.MAX_REPS.value
    assert linha["time_window_seconds"] == 30
    assert linha["reps_target"] is None and linha["suggested_reps"] is None


def test_medida_desconhecida_na_linha_le_como_reps():
    linha = mode_b.item_measure_and_reps({"measure": "minutos"}, _prescricao())
    assert linha["measure"] == SetMeasure.REPS.value and linha["reps_target"] == "8-10"


def test_geracao_usa_o_helper_na_linha_da_ficha():
    import inspect

    fonte = inspect.getsource(mode_b.generate_plan_draft_from_equipment_template)
    # M97 F2: o objetivo do item entra para o default de reps (prescription_defaults)
    assert "**item_measure_and_reps(item, prescription, item_goal)" in fonte


@pytest.mark.parametrize(
    ("measure", "esperado"),
    [
        (None, {}),
        (SetMeasure.REPS.value, {}),
        (SetMeasure.DURATION.value, {"reps_target": None, "suggested_reps": None}),
        (SetMeasure.MAX_REPS.value, {"reps_target": None, "suggested_reps": None}),
    ],
)
def test_congelamento_tira_as_reps_fora_de_reps(measure, esperado):
    assert freeze_mode_b.reps_fields_for_measure({"measure": measure}) == esperado


def test_congelamento_le_a_medida_da_linha_da_ficha():
    from app import services

    for coluna in ("measure", "duration_seconds_target", "time_window_seconds"):
        assert coluna in services._FREEZE_COLUMNS

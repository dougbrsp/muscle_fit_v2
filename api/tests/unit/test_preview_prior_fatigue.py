"""Preview do Modelo: `item_prior_fatigue[]` (dono `domain/prior_fatigue.py`).

Mesma estratégia de `test_preview_activation_items.py`: só o resolvedor de
contribuições e os nomes de grupo saem do banco."""

from __future__ import annotations

import pytest

from app import workout_plan_templates as wpt
from app.domain import prior_fatigue as pf
from app.intelligence import muscle_contribution_resolver

NAMES = {1: "Peitoral", 2: "Tríceps", 3: "Costas"}
CONTRIBUTIONS = {
    # Supino
    10: [
        {"muscle_group_id": 1, "muscle_group_name": "Peitoral", "role": "PRIMARY", "stimulus_weight": 1.0},
        {"muscle_group_id": 2, "muscle_group_name": "Tríceps", "role": "SECONDARY", "stimulus_weight": 0.5},
    ],
    # Tríceps polia
    20: [{"muscle_group_id": 2, "muscle_group_name": "Tríceps", "role": "PRIMARY", "stimulus_weight": 1.0}],
    # Remada
    30: [{"muscle_group_id": 3, "muscle_group_name": "Costas", "role": "PRIMARY", "stimulus_weight": 1.0}],
}


@pytest.fixture(autouse=True)
def isolate_db(monkeypatch):
    monkeypatch.setattr(
        muscle_contribution_resolver,
        "_resolve_muscle_contributions_for_mappings",
        lambda ids: {i: [dict(c) for c in CONTRIBUTIONS.get(i, [])] for i in ids},
    )
    monkeypatch.setattr(wpt, "_load_muscle_group_names", lambda ids: {i: NAMES[i] for i in ids if i in NAMES})


def equipment(mapping_id, sets_count=3, order=1):
    return {"item_order": order, "item_kind": "equipment", "mapping_id": mapping_id, "sets_count": sets_count}


def slot(sequence, items=None, slot_type="normal"):
    return {"sequence": sequence, "name": f"Slot {sequence}", "slot_type": slot_type, "items": items or []}


def preview(slots, volume_mode="equipment_series"):
    return wpt.preview_activation(wpt.WorkoutPlanTemplatePreviewIn(slots=slots, volume_mode=volume_mode))


def by_key(result):
    return {(e["slot_sequence"], e["item_index"]): e for e in result["item_prior_fatigue"]}


def test_preview_traz_fadiga_previa_por_item_na_ordem_do_slot():
    result = preview([slot(1, [equipment(10, 4), equipment(30, 3, 2), equipment(20, 3, 3)])])
    entries = by_key(result)

    bench = entries[(1, 0)]
    assert bench["item_kind"] == "equipment" and bench["mapping_id"] == 10
    assert bench["prior_fatigue"]["muscle_group_id"] == 1
    assert bench["prior_fatigue"]["muscle_group_name"] == "Peitoral"
    assert bench["prior_fatigue"]["level"] == 0.0
    assert bench["prior_fatigue"]["band"] == pf.PriorFatigueBand.RESTED.value

    triceps = entries[(1, 2)]["prior_fatigue"]
    assert triceps["muscle_group_id"] == 2
    assert triceps["prior_sets"] == pytest.approx(2.0)  # 4 séries de supino x 0,5
    assert triceps["level"] == pytest.approx(pf.level_for(2.0), abs=1e-4)
    assert triceps["band"] == pf.band_for(2.0).value
    assert triceps["band_label"] == pf.PRIOR_FATIGUE_BAND_LABELS[pf.band_for(2.0)]


def test_reordenar_os_itens_recalcula():
    first = by_key(preview([slot(1, [equipment(20, 3), equipment(10, 6, 2)])]))
    second = by_key(preview([slot(1, [equipment(10, 6), equipment(20, 3, 2)])]))
    assert first[(1, 0)]["prior_fatigue"]["prior_sets"] == 0  # tríceps primeiro
    assert second[(1, 1)]["prior_fatigue"]["prior_sets"] == pytest.approx(3.0)  # depois de 6 de supino


def test_todos_os_slots_do_ciclo_e_descanso_fora():
    slots = [slot(i, [equipment(10)]) for i in range(1, 9)]
    slots[3] = slot(4, [], slot_type="descanso")
    result = preview(slots)
    sequences = {e["slot_sequence"] for e in result["item_prior_fatigue"]}
    # ciclo de 8 slots: a janela do preview tem 7, a fadiga cobre todos menos o descanso
    assert sequences == {1, 2, 3, 5, 6, 7, 8}


def test_item_de_grupo_no_modo_percent_budget_conta_como_primario():
    result = preview(
        [
            slot(
                1,
                [
                    {"item_order": 1, "item_kind": "muscle_group", "muscle_group_id": 1, "sets_count": 4},
                    {"item_order": 2, "item_kind": "muscle_group", "muscle_group_id": 1, "sets_count": 3},
                ],
            )
        ],
        volume_mode="percent_budget",
    )
    entries = by_key(result)
    assert entries[(1, 0)]["prior_fatigue"]["prior_sets"] == 0
    assert entries[(1, 1)]["prior_fatigue"]["prior_sets"] == pytest.approx(4.0)
    assert entries[(1, 1)]["muscle_group_id"] == 1


def test_falha_no_calculo_nao_quebra_o_preview(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(wpt, "_item_prior_fatigue", boom)
    result = preview([slot(1, [equipment(10)])])
    assert "item_prior_fatigue" not in result
    assert result["days"]

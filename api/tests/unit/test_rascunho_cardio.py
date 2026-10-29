"""Cardio do slot do Modelo no rascunho da ficha (M98 C0).

Antes: o rascunho de dia de treino saía sempre com `cardio_minutes: None`
(Modo A e Modo B), o de descanso só com a duração do 1º item, e o save
(`WorkoutPlanIn`) descartava o campo - o cardio do Modelo se perdia na ficha.
Agora todo gerador monta `cardio_minutes` e `cardio_prescriptions` pelo dono
(`cardio_prescription.draft_cardio`), e o frontend leva os dois no
`raw_payload` do save (`buildDraftRawPayload`).

Rodar: cd api && python -m pytest tests/unit/test_rascunho_cardio.py
"""

from __future__ import annotations

import inspect

import pytest

from app import workout_plan_templates_engine as engine
from app.generation import mode_a, mode_a_legacy, mode_b, mode_b_legacy

NORMAL = {"item_kind": "cardio", "item_order": 1, "cardio_duration_minutes": 20}
HIIT = {
    "item_kind": "cardio",
    "item_order": 2,
    "cardio_mode": "hiit",
    "cardio_warmup_minutes": 5,
    "hiit_low_seconds": 90,
    "hiit_high_seconds": 30,
    "hiit_low_zone": 2,
    "hiit_high_zone": 4,
    "hiit_rounds": 8,
    "cardio_cooldown_minutes": 5,
}


def test_dia_de_descanso_leva_todos_os_cardios_do_slot():
    slot = {"id": 3, "name": "Descanso", "sequence": 2, "slot_type": "descanso", "items": [NORMAL, HIIT]}
    draft = engine._empty_rest_day_draft({"id": 1, "name": "M"}, slot, None, None, None)
    assert draft["is_rest_day"] is True
    assert draft["cardio_minutes"] == 20 + 26
    assert [p["text"] for p in draft["cardio_prescriptions"]] == [
        "20 min",
        "5 min aquec. + 8× [30 s Z4 / 90 s Z2] + 5 min recuperação",
    ]
    assert draft["cardio_prescriptions"][0]["cardio_mode"] == "continuous"


def test_dia_de_descanso_sem_cardio():
    slot = {"id": 3, "name": "Descanso", "sequence": 2, "slot_type": "descanso", "items": []}
    draft = engine._empty_rest_day_draft({"id": 1, "name": "M"}, slot, None, None, None)
    assert draft["cardio_minutes"] is None and draft["cardio_prescriptions"] == []


@pytest.mark.parametrize(
    "funcao",
    [
        mode_a.generate_plan_draft_from_template_a,
        mode_b.generate_plan_draft_from_equipment_template,
        mode_a_legacy,
        mode_b_legacy,
        engine,
    ],
)
def test_todo_gerador_monta_o_cardio_pelo_dono(funcao):
    fonte = inspect.getsource(funcao)
    assert "cardio_prescription.draft_cardio(" in fonte
    assert '"cardio_minutes": None' not in fonte
    assert "cardio_duration_minutes" not in fonte

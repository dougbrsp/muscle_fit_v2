"""COD-10: os `except Exception: pass` silenciosos agora registram o erro em log,
sem mudar o fluxo (a resposta continua igual a de antes, so sem o campo opcional).

Rodar: cd api && python -m pytest tests/unit/test_cod10_excecoes_registradas.py
"""

from __future__ import annotations

import json
import logging

import pytest

from app import services
from app import workout_plan_templates as wpt
from app.intelligence import muscle_contribution_resolver
from app.intelligence.volume_service import VolumeService

NAMES = {1: "Peitoral"}
CONTRIBUTIONS = {10: [{"muscle_group_id": 1, "muscle_group_name": "Peitoral", "role": "PRIMARY", "stimulus_weight": 1.0}]}


@pytest.fixture
def preview_sem_banco(monkeypatch):
    monkeypatch.setattr(
        muscle_contribution_resolver,
        "_resolve_muscle_contributions_for_mappings",
        lambda ids: {i: [dict(c) for c in CONTRIBUTIONS.get(i, [])] for i in ids},
    )
    monkeypatch.setattr(wpt, "_load_muscle_group_names", lambda ids: {i: NAMES[i] for i in ids if i in NAMES})


def _preview():
    slots = [
        {
            "sequence": 1,
            "name": "Slot 1",
            "slot_type": "normal",
            "items": [{"item_order": 1, "item_kind": "equipment", "mapping_id": 10, "sets_count": 3}],
        }
    ]
    return wpt.preview_activation(wpt.WorkoutPlanTemplatePreviewIn(slots=slots, volume_mode="equipment_series"))


def _boom(*_args, **_kwargs):
    raise RuntimeError("falha simulada")


def test_preview_sem_fadiga_previa_registra_e_segue(preview_sem_banco, monkeypatch, caplog):
    normal = _preview()
    monkeypatch.setattr(wpt, "_item_prior_fatigue", _boom)
    with caplog.at_level(logging.WARNING, logger="fitness.workout_plan_templates"):
        degradado = _preview()
    esperado = {k: v for k, v in normal.items() if k != "item_prior_fatigue"}
    assert degradado == esperado
    assert any("item_prior_fatigue" in r.getMessage() and r.exc_info for r in caplog.records)


def test_preview_sem_detalhamento_registra_e_segue(preview_sem_banco, monkeypatch, caplog):
    normal = _preview()
    monkeypatch.setattr(wpt, "_compute_item_breakdown", _boom)
    with caplog.at_level(logging.WARNING, logger="fitness.workout_plan_templates"):
        degradado = _preview()
    # Sem o detalhamento, sobra exatamente o que existia antes do `update`.
    assert set(degradado) < set(normal)
    assert all(degradado[k] == normal[k] for k in degradado)
    assert any("detalhamento" in r.getMessage() and r.exc_info for r in caplog.records)


def test_volume_service_fallback_registra_e_segue(monkeypatch, caplog):
    payload = {"template_slot_targets": [{"muscle_group_id": 1, "sets_count": 4}]}
    svc = VolumeService.__new__(VolumeService)
    monkeypatch.setattr(svc, "fetch_one", lambda *_a, **_k: {"raw_payload": json.dumps(payload)}, raising=False)

    normal = svc._resolve_targets_from_plan_template_slots(7)
    monkeypatch.setattr(services, "_apply_effective_max_sets_fallback", _boom)
    with caplog.at_level(logging.WARNING, logger="app.intelligence.volume_service"):
        degradado = svc._resolve_targets_from_plan_template_slots(7)

    assert degradado is not None and len(degradado) == 1
    assert degradado[0]["muscle_group_id"] == normal[0]["muscle_group_id"] == 1
    assert any("plano 7" in r.getMessage() and r.exc_info for r in caplog.records)

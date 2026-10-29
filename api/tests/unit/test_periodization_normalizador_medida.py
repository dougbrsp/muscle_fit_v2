"""Periodização (geração de Modelo por IA) e a medida da série (Lote T2).

Prompt e schema da saída da LLM ficam INALTERADOS (nada de medida neles; os
snapshots dos prompts continuam os mesmos — `test_pr16_*`/snapshots). O
normalizador (`validation._to_template_in`) grava o default do dono (`reps`)
em todo item, sem campo de tempo."""

from __future__ import annotations

import json

from app.domain.set_measures import DEFAULT_MEASURE
from app.models import PeriodizationDraftIn
from app.periodization.output_schema import OUTPUT_SCHEMA, PeriodizationItemOut, PeriodizationOutput
from app.periodization.validation import _to_template_in


def _parsed(items):
    return PeriodizationOutput.model_validate(
        {
            "template": {
                "name": "X",
                "description": None,
                "volume_mode": "equipment_series",
                "slots": [{"sequence": 1, "name": "A", "slot_type": "normal", "weekday": None, "items": items}],
            },
            "rationale": "r",
            "source_citations": [],
        }
    )


def test_schema_da_saida_da_llm_nao_fala_de_medida():
    texto = json.dumps(OUTPUT_SCHEMA)
    for campo in ("measure", "duration_seconds_target", "time_window_seconds"):
        assert campo not in texto
        assert campo not in PeriodizationItemOut.model_fields


def test_normalizador_grava_reps_e_nenhum_campo_de_tempo():
    item = {"item_kind": "equipment", "muscle_group_id": 1, "mapping_id": 2, "equipment_id": 3, "sets_count": 3}
    template = _to_template_in(_parsed([item, {**item, "mapping_id": 4}]), PeriodizationDraftIn.model_construct())
    for gerado in template.slots[0].items:
        assert gerado.measure == DEFAULT_MEASURE.value
        assert gerado.duration_seconds_target is None and gerado.time_window_seconds is None

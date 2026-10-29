"""Medida da série no item do Modelo (Lote T2): `_validate_template_payload`
chama o dono (`domain/set_measures.validate_item_measure`) e devolve 422 com a
mensagem do dono. Medida só em item_kind='equipment'; fora de `reps`, sem
`reps_target` e sem técnica/aquecimento/ajuste/pico.

Função pura (pydantic + HTTPException), sem banco."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domain import set_measures as sm
from app.domain.set_measures import SetMeasure
from app.models import WorkoutPlanTemplateIn
from app.workout_plan_templates import _validate_template_payload

DURATION = SetMeasure.DURATION.value
MAX_REPS = SetMeasure.MAX_REPS.value
REPS = SetMeasure.REPS.value


def equip(**extra):
    return {
        "item_order": 1,
        "item_kind": "equipment",
        "muscle_group_id": 1,
        "equipment_id": 5,
        "mapping_id": 100,
        "sets_count": 3,
        **extra,
    }


def mg(**extra):
    return {"item_order": 1, "item_kind": "muscle_group", "muscle_group_id": 1, **extra}


def cardio(**extra):
    return {"item_order": 1, "item_kind": "cardio", "cardio_duration_minutes": 20, **extra}


def modelo(item, volume_mode="equipment_series"):
    return WorkoutPlanTemplateIn(
        name="Modelo", volume_mode=volume_mode, slots=[{"sequence": 1, "name": "A", "items": [item]}]
    )


def detalhe_422(model) -> str:
    with pytest.raises(HTTPException) as exc_info:
        _validate_template_payload(model)
    assert exc_info.value.status_code == 422
    return exc_info.value.detail


# ---------------------------------------------------------------------------
# Válidos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "item",
    [
        equip(),  # cliente antigo: sem medida = reps
        equip(measure=REPS, reps_target="8-12", advanced_technique="drop_set"),
        equip(measure=" REPS ", reps_target="10"),
        equip(measure="", reps_target="10"),
        equip(measure=DURATION, duration_seconds_target=60, rest_seconds=30),
        equip(measure=DURATION, duration_seconds_target=sm.DURATION_SECONDS_MIN),
        equip(measure=DURATION, duration_seconds_target=sm.DURATION_SECONDS_MAX),
        equip(measure=MAX_REPS),
        equip(measure=MAX_REPS, time_window_seconds=30, rpe_target="9"),
        equip(measure=MAX_REPS, time_window_seconds=sm.TIME_WINDOW_SECONDS_MAX),
    ],
    ids=lambda item: f"{item.get('measure')!r}",
)
def test_combinacoes_validas_passam(item):
    _validate_template_payload(modelo(item))


def test_itens_de_outros_tipos_com_medida_reps_ou_ausente_passam():
    _validate_template_payload(modelo(mg(measure=REPS), volume_mode="percent_budget"))
    _validate_template_payload(modelo(cardio(), volume_mode="percent_budget"))


# ---------------------------------------------------------------------------
# 422 por combinação (código do dono -> mensagem do dono)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("item", "code"),
    [
        (equip(measure="minutos"), sm.MEASURE_UNKNOWN),
        (equip(measure=DURATION), sm.DURATION_TARGET_REQUIRED),
        (equip(measure=DURATION, duration_seconds_target=0), sm.DURATION_TARGET_OUT_OF_RANGE),
        (equip(measure=DURATION, duration_seconds_target=sm.DURATION_SECONDS_MAX + 1), sm.DURATION_TARGET_OUT_OF_RANGE),
        (equip(measure=DURATION, duration_seconds_target=60, time_window_seconds=30), sm.TIME_WINDOW_NOT_ALLOWED),
        (equip(measure=DURATION, duration_seconds_target=60, reps_target="10"), sm.REPS_TARGET_NOT_ALLOWED),
        (equip(measure=DURATION, duration_seconds_target=60, advanced_technique="drop_set"), sm.ADVANCED_BLOCK_NOT_ALLOWED),
        (equip(measure=DURATION, duration_seconds_target=60, warmup_reps=10), sm.ADVANCED_BLOCK_NOT_ALLOWED),
        (equip(measure=DURATION, duration_seconds_target=60, feeler_set_reps=5), sm.ADVANCED_BLOCK_NOT_ALLOWED),
        (equip(measure=DURATION, duration_seconds_target=60, peak_contraction_seconds=2), sm.ADVANCED_BLOCK_NOT_ALLOWED),
        (equip(measure=MAX_REPS, duration_seconds_target=60), sm.DURATION_TARGET_NOT_ALLOWED),
        (equip(measure=MAX_REPS, time_window_seconds=sm.TIME_WINDOW_SECONDS_MIN - 1), sm.TIME_WINDOW_OUT_OF_RANGE),
        (equip(measure=MAX_REPS, reps_target="12"), sm.REPS_TARGET_NOT_ALLOWED),
        (equip(measure=MAX_REPS, advanced_technique="rest_pause"), sm.ADVANCED_BLOCK_NOT_ALLOWED),
        (equip(measure=REPS, duration_seconds_target=60), sm.DURATION_TARGET_NOT_ALLOWED),
        (equip(time_window_seconds=30), sm.TIME_WINDOW_NOT_ALLOWED),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_combinacao_invalida_vira_422_com_a_mensagem_do_dono(item, code):
    detail = detalhe_422(modelo(item))
    assert sm.item_validation_message(code) in detail
    assert "Slot 'A'" in detail


def test_mensagens_cobrem_todos_os_codigos_de_item_e_interpolam_os_limites():
    for code in sm.ITEM_VALIDATION_CODES:
        assert sm.item_validation_message(code) != code
    assert str(sm.DURATION_SECONDS_MAX) in sm.item_validation_message(sm.DURATION_TARGET_OUT_OF_RANGE)
    assert str(sm.TIME_WINDOW_SECONDS_MIN) in sm.item_validation_message(sm.TIME_WINDOW_OUT_OF_RANGE)


@pytest.mark.parametrize(
    "item",
    [
        mg(measure=DURATION),
        mg(duration_seconds_target=60),
        mg(measure="minutos"),
        cardio(time_window_seconds=30),
        cardio(measure=MAX_REPS),
    ],
)
def test_medida_fora_de_reps_so_em_item_de_equipamento(item):
    detail = detalhe_422(modelo(item, volume_mode="percent_budget"))
    assert "item_kind='equipment'" in detail


def test_criterios_do_bloco_avancado_e_da_medida_declarada_sao_do_dono():
    assert sm.has_advanced_block({"warmup_reps": 10}) and sm.has_advanced_block({"peak_contraction_seconds": 2})
    # Companions tolerantes sozinhos não marcam o bloco.
    assert not sm.has_advanced_block({"warmup_rest_seconds": 60, "advanced_technique_applications": 2})
    assert not sm.declares_non_rep_measure(None, None, None)
    assert not sm.declares_non_rep_measure(" reps ", None, None)
    assert sm.declares_non_rep_measure(DURATION, None, None)
    assert sm.declares_non_rep_measure("minutos", None, None)
    assert sm.declares_non_rep_measure(None, None, 30)
    texto = sm.item_validation_message(sm.MEASURE_ONLY_ON_EQUIPMENT)
    assert sm.MEASURE_LABELS[SetMeasure.DURATION].lower() in texto


def test_ficha_recusa_medida_desconhecida_e_canoniza_a_valida():
    from pydantic import ValidationError

    from app.models import WorkoutPlanExerciseIn

    assert WorkoutPlanExerciseIn(sequence=1, exercise_name="Prancha", measure=" Duration ").measure == DURATION
    with pytest.raises(ValidationError):
        WorkoutPlanExerciseIn(sequence=1, exercise_name="Prancha", measure="segundos")

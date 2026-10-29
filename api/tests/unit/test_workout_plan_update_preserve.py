import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.domain.set_measures import SetMeasure  # noqa: E402
from app.models import WorkoutPlanExerciseIn  # noqa: E402
from app.workout_plan_exercise_merge import (  # noqa: E402
    ADVANCED_EXERCISE_FIELDS,
    merge_unsent_advanced_fields,
)

EXISTING = {
    "id": 10,
    "sequence": 1,
    "exercise_name": "Supino",
    "advanced_technique": "drop_set",
    "advanced_technique_applications": 2,
    "advanced_technique_duration_seconds": None,
    "warmup_sets_count": 1,
    "warmup_reps": 12,
    "warmup_rest_seconds": 60,
    "feeler_set_sets_count": 1,
    "feeler_set_reps": 5,
    "feeler_set_rest_seconds": 90,
    "peak_contraction_seconds": 2,
    # Medida da série (Lote T2): copiada do Modelo; a tela da ficha não a edita.
    "measure": SetMeasure.DURATION.value,
    "duration_seconds_target": 60,
    "time_window_seconds": None,
    # Objetivo de treino (M97 F0): resolvido na geração; a tela da ficha não o edita.
    "training_goal": "strength",
}


def _dump(**kwargs):
    return WorkoutPlanExerciseIn(**kwargs).model_dump(exclude_unset=True)


def test_campos_omitidos_sao_preservados_da_linha_existente():
    incoming = _dump(sequence=1, exercise_name="Supino", sets_count=4)
    merged = merge_unsent_advanced_fields(incoming, EXISTING)
    for field in ADVANCED_EXERCISE_FIELDS:
        assert merged[field] == EXISTING[field]
    assert merged["sets_count"] == 4


def test_null_explicito_limpa_o_campo():
    incoming = _dump(sequence=1, exercise_name="Supino", advanced_technique=None, warmup_reps=None)
    merged = merge_unsent_advanced_fields(incoming, EXISTING)
    assert merged["advanced_technique"] is None
    assert merged["warmup_reps"] is None
    assert merged["warmup_sets_count"] == 1


def test_valor_enviado_sobrescreve():
    incoming = _dump(sequence=1, exercise_name="Supino", peak_contraction_seconds=5)
    merged = merge_unsent_advanced_fields(incoming, EXISTING)
    assert merged["peak_contraction_seconds"] == 5


def test_exercicio_novo_nao_herda_nada():
    incoming = _dump(sequence=2, exercise_name="Remada")
    merged = merge_unsent_advanced_fields(incoming, None)
    for field in ADVANCED_EXERCISE_FIELDS:
        assert field not in merged


def test_nao_muta_o_dict_de_entrada():
    incoming = _dump(sequence=1, exercise_name="Supino")
    merge_unsent_advanced_fields(incoming, EXISTING)
    assert "advanced_technique" not in incoming


def test_payload_completo_equivale_a_model_dump():
    full = WorkoutPlanExerciseIn(sequence=1, exercise_name="Supino").model_dump()
    merged = merge_unsent_advanced_fields(full, EXISTING)
    for field in ADVANCED_EXERCISE_FIELDS:
        assert merged[field] is None


def test_medida_da_serie_omitida_e_preservada_pelo_cliente_antigo():
    """Lote T2: o cliente que não conhece a medida (tela da ficha atual) não
    apaga a medida por tempo copiada do Modelo."""
    incoming = _dump(sequence=1, exercise_name="Supino", sets_count=3)
    merged = merge_unsent_advanced_fields(incoming, EXISTING)
    assert merged["measure"] == SetMeasure.DURATION.value
    assert merged["duration_seconds_target"] == 60
    assert merged["time_window_seconds"] is None


def test_medida_enviada_sobrescreve_e_e_canonica():
    incoming = _dump(sequence=1, exercise_name="Supino", measure=" MAX_REPS ", time_window_seconds=30)
    merged = merge_unsent_advanced_fields(incoming, EXISTING)
    assert merged["measure"] == SetMeasure.MAX_REPS.value
    assert merged["time_window_seconds"] == 30
    assert merged["duration_seconds_target"] == 60  # não enviado: preservado


def test_medida_vazia_vale_ausente_e_desconhecida_e_recusada():
    assert WorkoutPlanExerciseIn(sequence=1, exercise_name="Supino", measure="  ").measure is None
    with pytest.raises(ValidationError):
        WorkoutPlanExerciseIn(sequence=1, exercise_name="Supino", measure="minutos")

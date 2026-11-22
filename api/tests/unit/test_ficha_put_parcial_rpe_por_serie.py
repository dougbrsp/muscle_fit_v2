"""#184: PUT parcial da ficha preserva a prescricao gravada e valida o RPE por serie contra o `sets_count` efetivo."""

from __future__ import annotations

import pytest

from app import services
from app.models import WorkoutPlanExerciseIn
from app.workout_plan_edit_rules import PlanEditError
from tests.unit.test_ficha_edicao_nao_finalizada import _Cursor  # noqa: F401 (fixture autouse abaixo)
from tests.unit.test_ficha_edicao_nao_finalizada import _sem_resolucao  # noqa: F401

SALVA = {
    "id": 10,
    "sequence": 1,
    "exercise_name": "Supino",
    "mapping_id": None,
    "preferred_equipment_id": None,
    "muscle_primary": "Peito",
    "sets_count": 4,
    "reps_target": "8-10",
    "load_target": "40",
    "rpe_target": "7-8-9",
    "rest_seconds": 90,
}
# Ordem dos parametros do UPDATE: sets_count, reps_target, load_target, rpe_target, rest_seconds.
POS = {"sets_count": 7, "reps_target": 8, "load_target": 9, "rpe_target": 10, "rest_seconds": 11}


def _put(**campos):
    """Corpo do PUT como a rota o monta: `exclude_unset` sobre o modelo."""
    corpo = WorkoutPlanExerciseIn(
        sequence=1, exercise_name="Supino", muscle_primary="Peito", workout_plan_exercise_id=10, **campos
    ).model_dump(exclude_unset=True)
    cur = _Cursor(existing=[SALVA], primary_groups={})
    services._replace_plan_exercises(cur, 1, [corpo])
    update = next(c for c in cur.calls if c[0].startswith("UPDATE workout_plan_exercises"))
    return {nome: update[1][i] for nome, i in POS.items()}


def test_put_sem_prescricao_preserva_o_gravado():
    assert _put() == {
        "sets_count": 4,
        "reps_target": "8-10",
        "load_target": "40",
        "rpe_target": "7-8-9",
        "rest_seconds": 90,
    }


def test_put_so_com_sets_count_preserva_o_resto():
    gravado = _put(sets_count=5)
    assert gravado["sets_count"] == 5 and gravado["rpe_target"] == "7-8-9" and gravado["rest_seconds"] == 90


def test_null_explicito_limpa():
    gravado = _put(sets_count=None, rpe_target=None, reps_target=None, load_target=None, rest_seconds=None)
    assert set(gravado.values()) == {None}


def test_lista_por_serie_do_tamanho_do_sets_count_gravado_compoe_o_texto():
    assert _put(rpe_targets_by_set=[7, 8, 9, 9])["rpe_target"] == "7-8-9"  # cauda colapsa com 4 series
    assert _put(rpe_targets_by_set=[8, 8, 8, 8])["rpe_target"] == "8"


def test_lista_maior_que_o_sets_count_gravado_da_422():
    with pytest.raises(PlanEditError) as erro:
        _put(rpe_targets_by_set=[7, 8, 9, 9, 10])
    assert erro.value.status == 422


def test_lista_menor_que_o_sets_count_gravado_segue_a_regra_do_modelo():
    # o validador do corpo so recusa lista MAIOR; menor repete o ultimo degrau
    assert _put(rpe_targets_by_set=[7, 8])["rpe_target"] == "7-8"


def test_linha_nova_sem_sets_count_segue_como_hoje():
    corpo = WorkoutPlanExerciseIn(sequence=2, exercise_name="Remada", rpe_targets_by_set=[8, 8, 8]).model_dump(
        exclude_unset=True
    )
    cur = _Cursor()
    services._replace_plan_exercises(cur, 1, [corpo])
    insert = next(c for c in cur.calls if c[0].startswith("INSERT INTO workout_plan_exercises"))
    assert insert[1][12] == "8-8-8" and insert[1][9] is None  # rpe_target, sets_count: sem series nao colapsa

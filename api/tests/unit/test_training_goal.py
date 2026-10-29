"""Dono do objetivo de treino (M97 F0, `app/domain/training_goal.py`):
vocabulário, leituras estrita/tolerante, precedência item > Modelo > default,
Modo A por grupo e predicado SQL.

Rodar: cd api && python -m pytest tests/unit/test_training_goal.py
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain import training_goal as tg
from app.domain.training_goal import DEFAULT_GOAL, TrainingGoal

H, S = TrainingGoal.HYPERTROPHY, TrainingGoal.STRENGTH


def test_vocabulario_binario_e_default_hipertrofia():
    assert tg.GOAL_VALUES == (H.value, S.value)
    assert DEFAULT_GOAL is H


@pytest.mark.parametrize("valor", [S.value, " Strength ", S.value.upper().encode(), S])
def test_parse_estrito_aceita_o_vocabulario(valor):
    assert tg.parse_goal(valor) is S


@pytest.mark.parametrize("valor", [None, "", "forca", "power", 1])
def test_parse_estrito_recusa_fora_do_vocabulario(valor):
    with pytest.raises(tg.InvalidTrainingGoal, match="training_goal inválido"):
        tg.parse_goal(valor)


def test_campo_opcional_da_api_aceita_ausente_e_recusa_desconhecido():
    assert tg.optional_goal_value(None) is None
    assert tg.optional_goal_value("  ") is None
    assert tg.optional_goal_value(S.value) == S.value
    with pytest.raises(ValueError):
        tg.optional_goal_value("forca")


@pytest.mark.parametrize("valor", [None, "", "desconhecido", b"hypertrophy"])
def test_leitura_tolerante_cai_no_default(valor):
    assert tg.read_goal(valor) is H
    assert tg.goal_value(valor) == H.value
    assert tg.is_default(valor)


def test_override_nulo_ou_desconhecido_herda():
    assert tg.read_override(None) is None
    assert tg.read_override("xyz") is None
    assert tg.override_value(S.value) == S.value
    assert tg.override_value("") is None


@pytest.mark.parametrize(
    "override, modelo, esperado",
    [
        (None, None, H),  # nada declarado: default
        (None, S.value, S),  # herda do Modelo
        (H.value, S.value, H),  # override vence o Modelo
        (S.value, None, S),  # override num Modelo sem objetivo
        ("lixo", S.value, S),  # override inválido herda
    ],
)
def test_precedencia_item_modelo_default(override, modelo, esperado):
    assert tg.resolve_goal(override, modelo) is esperado
    assert tg.resolved_goal_value(override, modelo) == esperado.value


def test_modo_a_usa_o_primeiro_item_do_grupo_no_slot():
    itens = [
        {"item_order": 3, "muscle_group_id": 10, "training_goal": H.value},
        {"item_order": 1, "muscle_group_id": 10, "training_goal": S.value},
        {"item_order": 2, "muscle_group_id": 20, "training_goal": None},
        {"item_order": 4, "muscle_group_id": None, "training_goal": S.value},
    ]
    assert tg.goal_by_group(itens, None) == {10: S.value, 20: H.value}
    assert tg.goal_by_group(itens, S.value) == {10: S.value, 20: S.value}
    assert tg.goal_by_group([], S.value) == {}


def test_predicado_sql_parametrizado_e_validado():
    assert tg.sql_goal_is("e") == "e.training_goal = %s"
    assert tg.sql_goal_is("wpe", "%(goal)s") == "wpe.training_goal = %(goal)s"
    for alias, param in (("e; DROP", "%s"), ("", "%s"), ("e", "'strength'"), ("e", "1")):
        with pytest.raises(ValueError):
            tg.sql_goal_is(alias, param)


def test_carimbo_do_registro_vem_da_linha_da_ficha_e_cai_no_default():
    assert tg.SQL_PLAN_EXERCISE_GOAL.count("%s") == 2
    assert tg.plan_exercise_goal_params(55) == (55, H.value)
    assert tg.plan_exercise_goal_params(None) == (None, H.value)


def test_entradas_da_api_validam_pelo_dono():
    from app.models import WorkoutPlanExerciseIn, WorkoutPlanTemplateIn, WorkoutPlanTemplateSlotItemIn

    assert WorkoutPlanTemplateIn(name="m").training_goal is None
    assert WorkoutPlanTemplateIn(name="m", training_goal=" STRENGTH ").training_goal == S.value
    assert WorkoutPlanTemplateSlotItemIn(item_order=1, item_kind="equipment").training_goal is None
    assert WorkoutPlanExerciseIn(sequence=1, exercise_name="x", training_goal=S.value).training_goal == S.value
    for model, extra in (
        (WorkoutPlanTemplateIn, {"name": "m"}),
        (WorkoutPlanTemplateSlotItemIn, {"item_order": 1, "item_kind": "equipment"}),
        (WorkoutPlanExerciseIn, {"sequence": 1, "exercise_name": "x"}),
    ):
        with pytest.raises(ValidationError, match="training_goal inválido"):
            model(training_goal="forca", **extra)


def test_migracao_093_usa_o_vocabulario_do_dono():
    from app import migrate as m

    g = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "093_training_goal.py").__globals__
    assert g["GOAL_VALUES"] == tg.GOAL_VALUES
    assert g["DEFAULT_GOAL"] == DEFAULT_GOAL.value
    assert g["COLUMN"] == tg.TRAINING_GOAL_COLUMN

"""Propagação do objetivo de treino Modelo -> ficha -> prescrição -> registro
(M97 F0). Só gravação: nenhum motor lê o objetivo nesta fase.

- geração (Modos A/B): o objetivo da linha é o resolvido pelo dono (override
  do item > Modelo > default);
- save da ficha: INSERT grava o objetivo (ausente = default) e o UPDATE do
  cliente que não manda o campo preserva o gravado;
- `prescription_json`: leva o objetivo da linha (snapshot antigo = default);
- registro ao vivo: `exercises.training_goal` vem da linha da ficha de origem.

Rodar: cd api && python -m pytest tests/unit/test_training_goal_propagacao.py
"""

from __future__ import annotations

import inspect

import pytest

from app import services
from app.domain import prescription
from app.domain.training_goal import DEFAULT_GOAL, SQL_PLAN_EXERCISE_GOAL, TrainingGoal, resolved_goal_value
from app.generation import mode_a, mode_b

STRENGTH = TrainingGoal.STRENGTH.value
HYPERTROPHY = TrainingGoal.HYPERTROPHY.value


class _Cursor:
    def __init__(self, existing=()):
        self.existing = [dict(row) for row in existing]
        self.calls = []
        self.lastrowid = 900

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), tuple(params)))

    def fetchall(self):
        return list(self.existing)


@pytest.fixture(autouse=True)
def _sem_resolucao(monkeypatch):
    monkeypatch.setattr(services, "resolve_mapping_id", lambda name, amazfit, explicit, cur=None: explicit)
    monkeypatch.setattr(services, "is_pair_coherent", lambda cur, mapping_id, gym_equipment_id: True)


def _escrita(cur):
    (sql, params), = [
        c for c in cur.calls if c[0].startswith(("INSERT INTO workout_plan_exercises", "UPDATE workout_plan_exercises"))
    ]
    return sql, params


def test_geracao_grava_o_objetivo_resolvido_pelo_dono():
    fonte_b = inspect.getsource(mode_b.generate_plan_draft_from_equipment_template)
    # M97 F2: o objetivo resolvido vira uma variavel (tambem decide os defaults
    # de descanso/reps do item, dono `prescription_defaults`).
    assert 'item_goal = resolved_goal_value(item.get("training_goal"), template.get("training_goal"))' in fonte_b
    assert '"training_goal": item_goal' in fonte_b
    fonte_a = inspect.getsource(mode_a.generate_plan_draft_from_template_a)
    assert 'goal_by_group(slot.get("items") or [], template.get("training_goal"))' in fonte_a
    assert "group_goal = goals_by_group.get(budget.muscle_group_id, template_goal)" in fonte_a
    assert '"training_goal": group_goal' in fonte_a


def test_modelo_forca_com_override_hipertrofia_chega_a_linha_e_ao_snapshot():
    # Modelo de força; o item sobrescreve para hipertrofia; outro item herda.
    linha_override = resolved_goal_value(HYPERTROPHY, STRENGTH)
    linha_herda = resolved_goal_value(None, STRENGTH)
    assert (linha_override, linha_herda) == (HYPERTROPHY, STRENGTH)

    cur = _Cursor()
    services._replace_plan_exercises(
        cur, 1, [{"sequence": 1, "exercise_name": "Agachamento", "mapping_id": 5, "training_goal": linha_herda}]
    )
    sql, params = _escrita(cur)
    assert sql.startswith("INSERT INTO workout_plan_exercises") and "time_window_seconds, training_goal )" in sql
    assert params[-1] == STRENGTH and sql.count("%s") == len(params)

    snapshot = prescription.build_prescription({"sets_count": 3, "training_goal": linha_herda})
    assert snapshot["training_goal"] == STRENGTH
    assert prescription.build_prescription({"sets_count": 3})["training_goal"] == DEFAULT_GOAL.value
    assert "training_goal" in services._FREEZE_COLUMNS


def test_linha_nova_sem_objetivo_grava_o_default():
    cur = _Cursor()
    services._replace_plan_exercises(cur, 1, [{"sequence": 1, "exercise_name": "Supino", "mapping_id": 5}])
    _sql, params = _escrita(cur)
    assert params[-1] == DEFAULT_GOAL.value


def test_update_do_cliente_que_nao_manda_o_objetivo_preserva_o_gravado():
    existente = {
        "id": 77,
        "sequence": 1,
        "exercise_name": "Agachamento",
        **{campo: None for campo in services.ADVANCED_EXERCISE_FIELDS},
        "training_goal": STRENGTH,
    }
    cur = _Cursor(existing=[existente])
    services._replace_plan_exercises(cur, 1, [{"sequence": 1, "exercise_name": "Agachamento", "mapping_id": 5}])
    sql, params = _escrita(cur)
    assert sql.startswith("UPDATE workout_plan_exercises") and "training_goal = %s WHERE id = %s" in sql
    assert params[-2:] == (STRENGTH, 77)


def test_get_da_ficha_devolve_o_objetivo():
    assert "wpe.training_goal" in inspect.getsource(services.get_workout_plan)


def test_registro_ao_vivo_carimba_o_objetivo_da_linha_da_ficha():
    from app import workout_sessions as ws

    fonte = inspect.getsource(ws.add_session_exercise)
    assert "{SQL_PLAN_EXERCISE_GOAL}" in fonte
    assert "*plan_exercise_goal_params(payload.workout_plan_exercise_id)" in fonte
    assert "workout_plan_exercises wpe_goal WHERE wpe_goal.id = %s" in SQL_PLAN_EXERCISE_GOAL

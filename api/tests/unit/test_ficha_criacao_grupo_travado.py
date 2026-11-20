"""#178: a troca de grupo PRIMARY de uma linha GERADA e recusada na criacao da ficha."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from app import services, workout_plans
from app import workout_plan_edit_rules as rules
from app.models import WorkoutPlanExerciseIn, WorkoutPlanIn
from tests.unit.test_ficha_edicao_nao_finalizada import _Cursor, _linha, _payload, _sem_resolucao  # noqa: F401

GRUPOS = {5: 1, 6: 1, 7: 2}  # 5 e 6 = mesmo grupo; 7 = outro


def _nova(**extra):
    return {"sequence": 1, "exercise_name": "Remada", "mapping_id": 7, "muscle_primary": "Costas", **extra}


def _salvar(cur, exercises):
    return services._replace_plan_exercises(cur, 1, exercises)


def test_linha_gerada_trocada_para_outro_grupo_e_recusada():
    cur = _Cursor(primary_groups=GRUPOS)
    with pytest.raises(rules.PlanEditError) as info:
        _salvar(cur, [_nova(generated_mapping_id=5)])
    assert (info.value.status, info.value.reason) == (422, rules.REASON_MUSCLE_GROUP_LOCKED)
    assert not [c for c in cur.calls if c[0].startswith("INSERT INTO workout_plan_exercises")]


def test_linha_gerada_com_mesmo_grupo_ou_sem_troca_e_aceita():
    cur = _Cursor(primary_groups=GRUPOS)
    assert len(_salvar(cur, [_nova(mapping_id=6, generated_mapping_id=5)])) == 1
    assert len(_salvar(_Cursor(primary_groups=GRUPOS), [_nova(mapping_id=5, generated_mapping_id=5)])) == 1


def test_linha_manual_e_cliente_antigo_sao_livres():
    cur = _Cursor(primary_groups=GRUPOS)
    assert len(_salvar(cur, [_nova(), _nova(sequence=2, mapping_id=5)])) == 2


def test_put_linha_nova_carimbada_segue_a_mesma_regra():
    cur = _Cursor(existing=[_linha()], primary_groups=GRUPOS)
    with pytest.raises(rules.PlanEditError):
        _salvar(cur, [_payload(), _nova(sequence=2, generated_mapping_id=5)])
    ok = _Cursor(existing=[_linha()], primary_groups=GRUPOS)
    _salvar(ok, [_payload(), _nova(sequence=2, mapping_id=6, generated_mapping_id=5)])


def test_modelo_aceita_o_campo_e_exige_inteiro_positivo():
    assert WorkoutPlanExerciseIn(sequence=1, exercise_name="x", generated_mapping_id=5).generated_mapping_id == 5
    assert WorkoutPlanExerciseIn(sequence=1, exercise_name="x").generated_mapping_id is None
    with pytest.raises(ValueError):
        WorkoutPlanExerciseIn(sequence=1, exercise_name="x", generated_mapping_id=0)


def test_criacao_desfaz_a_transacao_quando_a_trava_dispara(monkeypatch):
    class _CtxCursor(_Cursor):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    cur = _CtxCursor(primary_groups=GRUPOS)
    estado = {"rollback": False}

    class _Conn:
        def cursor(self):
            return cur

    @contextmanager
    def _transaction():
        try:
            yield _Conn()
        except BaseException:
            estado["rollback"] = True  # mesmo contrato do app.db.transaction
            raise

    monkeypatch.setattr(services, "transaction", _transaction)
    monkeypatch.setattr(services, "resolve_gym_id", lambda *a, **k: None)
    monkeypatch.setattr(services, "_validated_template_version_id", lambda *a, **k: None)
    monkeypatch.setattr(services, "get_workout_plan", lambda *a, **k: {})
    with pytest.raises(rules.PlanEditError):
        services.create_workout_plan({"name": "F", "exercises": [_nova(generated_mapping_id=5)]}, user_id=1)
    assert estado["rollback"] is True


def test_rota_post_responde_422_no_formato_do_put(monkeypatch):
    def _recusa(*a, **k):
        raise rules.PlanEditError(422, rules.REASON_MUSCLE_GROUP_LOCKED, "grupo travado")

    monkeypatch.setattr(services, "create_workout_plan", _recusa)
    identity = type("I", (), {"user_id": 1})()
    with pytest.raises(HTTPException) as info:
        workout_plans.create_plan(WorkoutPlanIn(name="F", exercises=[]), identity=identity)
    assert info.value.status_code == 422
    assert info.value.detail == {"reason": rules.REASON_MUSCLE_GROUP_LOCKED, "message": "grupo travado"}

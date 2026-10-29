"""Edição de ficha não finalizada em /planos-treino (2026-10-24).

- `workout_plan_edit_rules`: ficha editável só `planned`; grupamento travado;
  quais mudanças re-congelam a prescrição.
- `services._replace_plan_exercises`: trava do grupamento pela linha salva
  (id ecoado) e `refreeze_ids` para troca de exercício/equipamento/técnica.
- `services.update_workout_plan`: ficha finalizada recusa exercícios (409) e
  aceita o save só do cabeçalho.
- `services.sync_load_target_from_frozen_suggestion`: a carga da linha vira a
  sugestão congelada do par novo (vazia sem histórico).
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from app import services
from app import workout_plan_edit_rules as rules


# ---------------------------------------------------------------------------
# Regras puras
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,editable",
    [("planned", True), (" Planned ", True), ("done", False), ("completed", False), ("skipped", False), (None, False)],
)
def test_ficha_editavel_so_planejada(status, editable):
    assert rules.is_plan_editable(status) is editable


def test_ficha_finalizada_recusa_com_409():
    with pytest.raises(rules.PlanEditError) as info:
        rules.assert_plan_editable("done")
    assert (info.value.status, info.value.reason) == (409, rules.REASON_PLAN_FINALIZED)


def test_texto_do_grupo_nao_muda_mas_normaliza_espacos_e_caixa():
    rules.assert_muscle_group_text_unchanged(persisted_group="Peitoral", incoming_group=" peitoral ", exercise_name="X")
    # linha antiga sem grupo aceita o primeiro preenchimento
    rules.assert_muscle_group_text_unchanged(persisted_group=None, incoming_group="Costas", exercise_name="X")
    with pytest.raises(rules.PlanEditError) as info:
        rules.assert_muscle_group_text_unchanged(persisted_group="Peitoral", incoming_group="Costas", exercise_name="X")
    assert (info.value.status, info.value.reason) == (422, rules.REASON_MUSCLE_GROUP_LOCKED)


def test_grupo_primary_diferente_recusa_e_desconhecido_passa():
    rules.assert_same_primary_group(persisted_group_id=3, new_group_id=3, exercise_name="X")
    rules.assert_same_primary_group(persisted_group_id=None, new_group_id=4, exercise_name="X")
    with pytest.raises(rules.PlanEditError):
        rules.assert_same_primary_group(persisted_group_id=3, new_group_id=4, exercise_name="X")


def test_refreeze_so_para_exercicio_equipamento_ou_tecnica():
    row = {"mapping_id": 5, "preferred_equipment_id": 9, "advanced_technique": None, "sets_count": 4}
    same = {"mapping_id": 5, "preferred_equipment_id": 9, "advanced_technique": ""}
    assert rules.refreeze_needed(row, same) is False
    assert rules.refreeze_needed(row, {**same, "preferred_equipment_id": 10}) is True
    assert rules.refreeze_needed(row, {**same, "mapping_id": 6}) is True
    assert rules.refreeze_needed(row, {**same, "advanced_technique": "drop_set"}) is True
    assert rules.refreeze_needed(None, same) is False


# ---------------------------------------------------------------------------
# _replace_plan_exercises
# ---------------------------------------------------------------------------


class _Cursor:
    """Cursor falso: SELECT das linhas da ficha, mapa de ids legados (no-op) e
    grupos PRIMARY por exercício."""

    def __init__(self, existing=(), primary_groups=None):
        self.existing = [dict(row) for row in existing]
        self.primary_groups = primary_groups or {}
        self.calls = []
        self.lastrowid = 900
        self._result = []

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        self.calls.append((flat, tuple(params)))
        if flat.startswith("SELECT id, sequence"):
            self._result = list(self.existing)
        elif "exercise_legacy_id_map" in flat:
            self._result = []
        elif "exercise_muscle_contributions" in flat:
            self._result = [
                {"exercise_id": i, "muscle_group_id": self.primary_groups[i], "stimulus_weight": 1}
                for i in params
                if i in self.primary_groups
            ]
        else:
            self._result = []

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None


@pytest.fixture(autouse=True)
def _sem_resolucao(monkeypatch):
    monkeypatch.setattr(services, "resolve_mapping_id", lambda name, amazfit, explicit, cur=None: explicit)
    monkeypatch.setattr(services, "is_pair_coherent", lambda cur, mapping_id, gym_equipment_id: True)


def _linha(**extra):
    return {
        "id": 77,
        "sequence": 1,
        "exercise_name": "Supino reto",
        "mapping_id": 5,
        "preferred_equipment_id": 9,
        "muscle_primary": "Peitoral",
        **{campo: None for campo in services.ADVANCED_EXERCISE_FIELDS},
        **extra,
    }


def _payload(**extra):
    return {
        "sequence": 1,
        "workout_plan_exercise_id": 77,
        "exercise_name": "Supino reto",
        "mapping_id": 5,
        "preferred_equipment_id": 9,
        "muscle_primary": "Peitoral",
        **extra,
    }


def test_editar_parametros_sem_trocar_par_nao_recongela():
    cur = _Cursor(existing=[_linha()])
    refreeze: list[int] = []
    services._replace_plan_exercises(
        cur, 1, [_payload(sets_count=5, rest_seconds=90, reps_target="8-10")], refreeze_ids=refreeze
    )
    assert refreeze == []


def test_trocar_tecnica_recongela_a_linha():
    cur = _Cursor(existing=[_linha()])
    refreeze: list[int] = []
    services._replace_plan_exercises(
        cur, 1, [_payload(advanced_technique="drop_set", advanced_technique_applications=2)], refreeze_ids=refreeze
    )
    assert refreeze == [77]


def test_mudar_o_grupamento_da_linha_salva_e_recusado():
    cur = _Cursor(existing=[_linha()])
    with pytest.raises(rules.PlanEditError) as info:
        services._replace_plan_exercises(cur, 1, [_payload(muscle_primary="Costas")])
    assert info.value.status == 422
    assert not [c for c in cur.calls if c[0].startswith("UPDATE workout_plan_exercises")]


def test_trocar_por_exercicio_do_mesmo_grupo_recongela():
    cur = _Cursor(existing=[_linha()], primary_groups={5: 1, 6: 1})
    refreeze: list[int] = []
    services._replace_plan_exercises(
        cur, 1, [_payload(exercise_name="Supino inclinado", mapping_id=6)], refreeze_ids=refreeze
    )
    assert refreeze == [77]


def test_trocar_por_exercicio_de_outro_grupo_e_recusado():
    cur = _Cursor(existing=[_linha()], primary_groups={5: 1, 6: 2})
    with pytest.raises(rules.PlanEditError) as info:
        services._replace_plan_exercises(cur, 1, [_payload(mapping_id=6)])
    assert info.value.reason == rules.REASON_MUSCLE_GROUP_LOCKED


def test_linha_nova_sem_id_escolhe_o_grupo_livremente():
    cur = _Cursor(existing=[_linha()], primary_groups={5: 1, 6: 2})
    services._replace_plan_exercises(
        cur,
        1,
        [
            _payload(),
            {"sequence": 2, "exercise_name": "Remada", "mapping_id": 6, "muscle_primary": "Costas"},
        ],
    )


def test_id_de_outra_ficha_nao_trava_nada():
    cur = _Cursor(existing=[_linha()])
    services._replace_plan_exercises(cur, 1, [_payload(workout_plan_exercise_id=12345, muscle_primary="Costas")])


# ---------------------------------------------------------------------------
# update_workout_plan
# ---------------------------------------------------------------------------


class _PlanCursor:
    def __init__(self, status):
        self.status = status
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.calls.append(" ".join(sql.split()))

    def fetchone(self):
        return {"id": 1, "user_id": 7, "status": self.status, "template_id": None, "template_version_id": None}


class _Conn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur


@pytest.fixture
def plano(monkeypatch):
    calls = {"replace": 0, "freeze": []}

    def _fake(status):
        cur = _PlanCursor(status)

        @contextmanager
        def _transaction():
            yield _Conn(cur)

        monkeypatch.setattr(services, "transaction", _transaction)
        monkeypatch.setattr(services, "resolve_gym_id", lambda cur, gym_id, gym_name: gym_id)
        monkeypatch.setattr(services, "get_workout_plan", lambda plan_id, user_id=None: {"id": plan_id})

        def _replace(*args, **kwargs):
            calls["replace"] += 1
            return []

        monkeypatch.setattr(services, "_replace_plan_exercises", _replace)
        monkeypatch.setattr(
            services, "_freeze_plan_prescriptions", lambda conn, plan_id, **kw: calls["freeze"].append(kw) or 0
        )
        return cur, calls

    return _fake


def test_ficha_finalizada_recusa_exercicios(plano):
    cur, calls = plano("done")
    with pytest.raises(rules.PlanEditError) as info:
        services.update_workout_plan(1, {"name": "x", "exercises": []}, user_id=7)
    assert info.value.status == 409
    assert calls["replace"] == 0
    assert not [sql for sql in cur.calls if sql.startswith("UPDATE workout_plans")]


def test_ficha_finalizada_salva_so_o_cabecalho(plano):
    cur, calls = plano("completed")
    assert services.update_workout_plan(1, {"name": "x", "status": "planned", "exercises": None}, user_id=7) == {"id": 1}
    assert calls["replace"] == 0
    assert [sql for sql in cur.calls if sql.startswith("UPDATE workout_plans")]


def test_ficha_planejada_edita_exercicios(plano):
    _cur, calls = plano("planned")
    services.update_workout_plan(1, {"name": "x", "exercises": [{"sequence": 1, "exercise_name": "A"}]}, user_id=7)
    assert calls["replace"] == 1


# ---------------------------------------------------------------------------
# Carga do par novo na troca de equipamento
# ---------------------------------------------------------------------------


class _SyncCursor:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.updates = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        if sql.startswith("UPDATE"):
            self.updates.append(params)

    def fetchone(self):
        return {"prescription_json": json.dumps(self.snapshot) if self.snapshot is not None else None}


@pytest.mark.parametrize(
    "snapshot,expected",
    [({"suggested_load_kg": 42.5}, "42,5kg"), ({"suggested_load_kg": 40.0}, "40kg"), ({"suggested_load_kg": None}, None), (None, None)],
)
def test_carga_da_linha_vira_a_sugestao_congelada_do_par_novo(snapshot, expected):
    cur = _SyncCursor(snapshot)
    services.sync_load_target_from_frozen_suggestion(_Conn(cur), 77)
    assert cur.updates == [(expected, 77)]


# ---------------------------------------------------------------------------
# Troca de equipamento em ficha planejada de data passada
# ---------------------------------------------------------------------------


def test_troca_de_equipamento_aceita_ficha_planejada_de_data_passada():
    from datetime import date

    passada = {"status": "planned", "plan_date": date(2020, 1, 1)}
    services.assert_plan_prescription_refreshable(passada, require_future_date=False)
    with pytest.raises(services.PlanPrescriptionRefreshError) as info:
        services.assert_plan_prescription_refreshable(passada)  # o refresh da ficha inteira continua exigindo
    assert info.value.reason == "plan_date_in_past"
    with pytest.raises(services.PlanPrescriptionRefreshError) as info:
        services.assert_plan_prescription_refreshable({"status": "done"}, require_future_date=False)
    assert info.value.reason == "plan_not_planned"

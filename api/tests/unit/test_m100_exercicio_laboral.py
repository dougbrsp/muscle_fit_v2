"""M100 - exercicio laboral (dono `app/domain/laboral.py`, set_type `laboral`
preparatorio de `app/set_types.py`).

Decisoes do usuario (27/10): o laboral conta SO no tempo de treino - fora de
progressao, recorde/e1RM, volume, ativacao, recuperacao e insights; series,
reps e carga vem de um padrao do catalogo.

Rodar: cd api && python -m pytest tests/unit/test_m100_exercicio_laboral.py
"""

from __future__ import annotations

import inspect
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app import set_types as st
from app.domain import laboral as lab


# ---------------------------------------------------------------------------
# Dono: padrao do catalogo > linha > fallback; validacao do admin
# ---------------------------------------------------------------------------


def test_padrao_do_catalogo_vence_a_linha():
    defaults = lab.LaboralDefaults(sets=3, reps=15, load_kg=Decimal("2"))
    got = lab.laboral_prescription(defaults, {"sets_count": 4, "reps_target": "8-10", "load_target": "40kg"})
    assert (got.sets, got.reps, got.load_kg) == (3, 15, Decimal("2"))


def test_sem_padrao_usa_a_linha_e_depois_o_fallback():
    got = lab.laboral_prescription(None, {"sets_count": 2, "reps_target": "12", "load_target": "1,5kg"})
    assert (got.sets, got.reps, got.load_kg) == (2, 12, Decimal("1.5"))
    vazio = lab.laboral_prescription(lab.LaboralDefaults(), {})
    assert (vazio.sets, vazio.reps, vazio.load_kg) == (lab.LABORAL_FALLBACK_SETS, lab.LABORAL_FALLBACK_REPS, None)


def test_defaults_da_linha_do_catalogo():
    row = {"laboral_default_sets": 2, "laboral_default_reps": None, "laboral_default_load_kg": "0.00"}
    assert lab.defaults_from_row(row) == lab.LaboralDefaults(sets=2, reps=None, load_kg=None)


@pytest.mark.parametrize("valor", [0, -1, lab.LABORAL_MAX_SETS + 1, "x", True])
def test_series_fora_dos_limites_sao_recusadas(valor):
    with pytest.raises(lab.LaboralDefaultError):
        lab.clean_default_sets(valor)


def test_campos_do_admin_validados_pelo_dono():
    from app.exercise_catalog_admin import clean_laboral_fields

    assert clean_laboral_fields({"name": "x"}) == {}
    assert clean_laboral_fields(
        {"is_laboral": True, "laboral_default_sets": "3", "laboral_default_reps": 15, "laboral_default_load_kg": "2,5"}
    ) == {
        "is_laboral": 1,
        "laboral_default_sets": 3,
        "laboral_default_reps": 15,
        "laboral_default_load_kg": Decimal("2.50"),
    }
    assert clean_laboral_fields({"laboral_default_load_kg": None}) == {"laboral_default_load_kg": None}
    with pytest.raises(HTTPException) as erro:
        clean_laboral_fields({"laboral_default_reps": lab.LABORAL_MAX_REPS + 1})
    assert erro.value.status_code == 400


# ---------------------------------------------------------------------------
# Fora de volume, recorde/e1RM, progressao e recuperacao
# ---------------------------------------------------------------------------

SERIES_LABORAIS = [
    {"set_number": 1, "set_type": "laboral", "reps": 15, "weight_kg": 2, "rpe": None},
    {"set_number": 2, "set_type": "laboral", "reps": 15, "weight_kg": 2, "rpe": None},
]


def test_serie_laboral_e_preparatoria_em_python_e_sql():
    assert st.LABORAL in st.PREPARATORY_SET_TYPES and st.LABORAL in st.KNOWN_SET_TYPES
    assert not any(st.is_working_set(s) for s in SERIES_LABORAIS)
    assert st.non_preparatory_sets(SERIES_LABORAIS) == []
    assert "'laboral'" in st.sql_not_preparatory()
    # valor conhecido: entra na lista do NOT IN, nao vira "fora do vocabulario"
    assert "'laboral'" in st.sql_is_unknown_set_type()


def test_volume_do_exercicio_nao_conta_o_laboral():
    from app.services import compute_exercise_stats

    stats = compute_exercise_stats(SERIES_LABORAIS)
    assert stats["sets_count"] == 0 and not stats["volume_kg"] and not stats["best_weight_kg"]


def test_recuperacao_e_volume_filtram_pelo_mesmo_predicado():
    from app.intelligence import recovery_read_model as rrm

    assert "'laboral'" in rrm._ES_NOT_PREPARATORY
    assert st.LABORAL in rrm.SET_TYPE_CODES


def test_ativacao_planejada_ignora_item_laboral():
    from app.intelligence.technique_volume import activation_payload, planned_item_sets

    item = {"mapping_id": 7, "sets_count": 3, "is_laboral": True}
    assert planned_item_sets(item, None) == 0.0
    assert activation_payload(item)["is_laboral"] is True


def test_tempo_estimado_conta_o_laboral_normalmente():
    from app.periodization.time_estimate import estimate_slot_seconds

    base = {"mapping_id": 7, "sets_count": 3, "reps_target": "15", "rest_seconds": 30}
    assert estimate_slot_seconds([{**base, "is_laboral": True}]) == estimate_slot_seconds([base]) > 0


def test_prescricao_do_motor_nao_prescreve_par_laboral(monkeypatch):
    from app import progression_state as ps
    from app.generation import deterministic_prescription as dp

    monkeypatch.setattr(ps, "load_laboral_ids", lambda conn, ids: frozenset({7}))

    def _nao_pode(*_args, **_kwargs):
        raise AssertionError("o motor nao pode ler nada para um par laboral")

    monkeypatch.setattr(dp, "resolve_training_safety_bulk", _nao_pode)
    monkeypatch.setattr(ps, "load_technique_catalog", _nao_pode)
    request = dp.PairRequest(exercise_id=7, equipment_id=3, muscle_group_id=1, model_sets_count=3, sequence=1)
    assert dp.prescribe_pairs(object(), 1, [request], now=datetime(2026, 10, 27, 10, 0)) == {}


def test_modo_a_e_substituicao_nao_escolhem_laboral():
    from app import equipment_recommender
    from app.generation import exercise_substitution

    assert 'sql_not_laboral("zm")' in inspect.getsource(equipment_recommender._load_candidates)
    assert "sql_not_laboral('ec')" in inspect.getsource(exercise_substitution.load_group_candidates)
    assert lab.sql_not_laboral("zm") == "zm.is_laboral = 0"


def test_serie_laboral_nao_exige_lados_m102():
    """M102 (lados da serie unilateral) e opcional: a serie laboral do card
    (sem lados) e valida e fica com reps/RPE do payload, sem lado nenhum."""
    from app.workout_sessions import SessionSetIn

    payload = SessionSetIn(set_number=1, set_type="laboral", reps=15, weight_kg=2, rpe=None)
    assert payload.reps == 15 and payload.rpe is None
    assert payload.reps_left is None and payload.reps_right is None


def test_serie_de_exercicio_laboral_e_forcada_a_laboral_no_servidor():
    """M100-R parcial: relogio/cliente antigo gravando `working` num laboral."""
    from app import workout_sessions

    fonte = inspect.getsource(workout_sessions.add_session_set)
    assert "load_laboral_ids" in fonte and "set_type = LABORAL" in fonte


# ---------------------------------------------------------------------------
# Congelamento: padrao do catalogo na linha e no snapshot
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append((sql, params))


class _Conn:
    def __init__(self):
        self.log: list = []

    def cursor(self):
        return _Cursor(self.log)


def test_congelamento_grava_o_padrao_do_catalogo(monkeypatch):
    from app import laboral_catalog, services

    monkeypatch.setattr(
        laboral_catalog,
        "load_laboral_defaults",
        lambda conn, ids: {7: lab.LaboralDefaults(sets=3, reps=15, load_kg=Decimal("2"))},
    )
    rows = [
        {"workout_plan_exercise_id": 11, "mapping_id": 7, "sets_count": 4, "reps_target": "8-10", "load_target": "40kg",
         "rpe_target": "8", "advanced_technique": "drop_set"},
        {"workout_plan_exercise_id": 12, "mapping_id": 8, "sets_count": 3, "reps_target": "10"},
    ]
    conn = _Conn()
    snapshots = services._laboral_rows_snapshots(
        conn, rows, safety_by_row={}, source="PLAN_CREATED", frozen_at=datetime(2026, 10, 27, 10, 0)
    )

    assert set(snapshots) == {11}
    snapshot = snapshots[11]
    assert snapshot["engine_version"] == lab.LABORAL_ENGINE_VERSION
    assert (snapshot["sets_count"], snapshot["reps_target"], snapshot["load_target"]) == (3, "15", "2kg")
    assert snapshot["suggested_load_kg"] == 2.0 and snapshot["advanced_technique"] is None
    assert snapshot["laboral"] == {"sets": 3, "reps": 15, "load_kg": 2.0}
    assert conn.log == [
        (
            "UPDATE workout_plan_exercises SET sets_count = %s, reps_target = %s, load_target = %s, "
            "advanced_technique = NULL WHERE id = %s",
            (3, "15", "2kg", 11),
        )
    ]


def test_congelamento_sem_laboral_nao_escreve_nada(monkeypatch):
    from app import laboral_catalog, services

    monkeypatch.setattr(laboral_catalog, "load_laboral_defaults", lambda conn, ids: {})
    conn = _Conn()
    assert services._laboral_rows_snapshots(
        conn, [{"workout_plan_exercise_id": 1, "mapping_id": 2}], safety_by_row={}, source="X", frozen_at=datetime.now()
    ) == {}
    assert conn.log == []

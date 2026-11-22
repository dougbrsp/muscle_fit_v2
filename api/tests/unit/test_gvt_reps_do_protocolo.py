"""#224: GVT = 10 repeticoes por serie, dono unico em `technique_accounting`."""

from __future__ import annotations

from types import SimpleNamespace

from app.domain import progression_engine as pe
from app.domain.prescription import build_prescription
from app.domain.technique_accounting import GVT_PROTOCOL_REPS, protocol_reps
from app.generation.deterministic_prescription import PairPrescription
from app.generation.mode_b import item_measure_and_reps
from tests.support import progression_fixtures as fx

READY = pe.READY_CONTRACT


def _prescribe(technique, **kw):
    return pe.prescribe(
        fx.track(sets_count=10),
        fx.group(),
        fx.safety(),
        READY,
        catalog=fx.CATALOG,
        model_technique=technique,
        model_fixed_sets=True,
        **kw,
    )


def test_dono_so_o_gvt_tem_reps_fixas():
    assert GVT_PROTOCOL_REPS == 10
    assert protocol_reps("gvt") == 10
    assert protocol_reps("fst7") is None
    assert protocol_reps("rest_pause") is None
    assert protocol_reps(None) is None


def test_technique_parameters_exporta_target_reps():
    assert pe.technique_parameters("gvt", source="model", catalog=fx.CATALOG)["target_reps"] == 10
    assert pe.technique_parameters("fst7", source="model", catalog=fx.CATALOG)["target_reps"] is None


def test_decisao_gvt_sai_com_10_mesmo_com_faixa_do_grupo():
    decision = _prescribe("gvt")
    assert (decision.suggested_reps_min, decision.suggested_reps_max) == (10, 10)
    assert PairPrescription(decision=decision, safety=None, technique="gvt").reps_target == "10"


def test_decisao_fst7_segue_a_faixa():
    decision = _prescribe("fst7")
    group_range = fx.group().reps_range
    assert (decision.suggested_reps_min, decision.suggested_reps_max) == tuple(group_range)
    assert PairPrescription(decision=decision, safety=None, technique="fst7").reps_target == f"{group_range[0]}-{group_range[1]}"


def test_linha_gerada_sem_decisao_usa_o_protocolo_mesmo_com_item_6_12():
    item = {"advanced_technique": "gvt", "reps_target": "6-12"}
    out = item_measure_and_reps(item, None)
    assert out["reps_target"] == "10" and out["suggested_reps"] == 10
    assert item_measure_and_reps({"advanced_technique": "fst7", "reps_target": "6-12"}, None)["reps_target"] == "6-12"


def test_linha_gerada_com_decisao_gvt_nao_usa_o_item():
    prescription = PairPrescription(decision=_prescribe("gvt"), safety=None, technique="gvt")
    out = item_measure_and_reps({"advanced_technique": "gvt", "reps_target": "6-12"}, prescription)
    assert out["reps_target"] == "10" and out["suggested_reps"] == 10


def test_congelada_e_ficha_manual_usam_o_protocolo():
    exercise = {"advanced_technique": "gvt", "reps_target": "6-12", "sets_count": 10, "suggested_reps": 6}
    snap = build_prescription(exercise, safety=fx.safety(), source="manual", engine_version="x")
    assert snap["reps_target"] == "10" and snap["suggested_reps"] == 10
    assert snap["advanced_technique_parameters"]["target_reps"] == 10
    exercise["advanced_technique"] = "fst7"
    snap = build_prescription(exercise, safety=fx.safety(), source="manual", engine_version="x")
    assert snap["reps_target"] == "6-12"


def test_snapshot_com_decisao_gvt_do_motor():
    decision = _prescribe("gvt").to_dict()
    exercise = {"advanced_technique": "gvt", "reps_target": "10", "sets_count": 10}
    snap = build_prescription(exercise, safety=fx.safety(), source="x", engine_version="x", decision=decision)
    assert snap["reps_target"] == "10" and snap["suggested_reps"] == 10


def test_gravacao_da_linha_manual_grava_as_reps_do_protocolo(monkeypatch):
    from app import services

    monkeypatch.setattr(services, "resolve_mapping_id", lambda name, amazfit, explicit, cur=None: explicit)
    from tests.unit.test_ficha_edicao_nao_finalizada import _Cursor

    def _reps_gravadas(**extra):
        cur = _Cursor()
        ex = {"sequence": 1, "exercise_name": "Supino", **extra}
        services._replace_plan_exercises(cur, 1, [ex])
        insert = next(c for c in cur.calls if c[0].startswith("INSERT INTO workout_plan_exercises"))
        return insert[1][10]  # plan_id, sequence, ... reps_target

    assert _reps_gravadas(advanced_technique="gvt", reps_target="6-12") == "10"
    assert _reps_gravadas(advanced_technique="fst7", reps_target="6-12") == "6-12"
    assert _reps_gravadas(reps_target="6-12") == "6-12"


def test_gvt_em_linha_por_tempo_nao_aplica_reps_do_protocolo(monkeypatch):
    from app import services

    exercise = {"advanced_technique": "gvt", "reps_target": "6-12", "sets_count": 10, "measure": "duration"}
    snap = build_prescription(exercise, safety=fx.safety(), source="manual", engine_version="x")
    assert snap["reps_target"] == "6-12"

    monkeypatch.setattr(services, "resolve_mapping_id", lambda name, amazfit, explicit, cur=None: explicit)
    from tests.unit.test_ficha_edicao_nao_finalizada import _Cursor

    cur = _Cursor()
    services._replace_plan_exercises(cur, 1, [{"sequence": 1, "exercise_name": "Prancha", **exercise}])
    insert = next(c for c in cur.calls if c[0].startswith("INSERT INTO workout_plan_exercises"))
    assert insert[1][10] == "6-12"
    exercise["reps_target"] = None
    cur = _Cursor()
    services._replace_plan_exercises(cur, 1, [{"sequence": 1, "exercise_name": "Prancha", **exercise}])
    insert = next(c for c in cur.calls if c[0].startswith("INSERT INTO workout_plan_exercises"))
    assert insert[1][10] is None


def test_pair_prescription_sem_tecnica_mantem_faixa():
    decision = SimpleNamespace(suggested_reps_min=8, suggested_reps_max=12, advanced_technique_parameters=None)
    assert PairPrescription(decision=decision, safety=None, technique=None).reps_target == "8-12"


def test_faixa_com_minimo_igual_ao_maximo_vira_um_numero():
    from app.domain.prescription_service import PairSuggestion

    decision = SimpleNamespace(suggested_reps_min=10, suggested_reps_max=10, advanced_technique_parameters=None)
    assert PairPrescription(decision=decision, safety=None, technique=None).reps_target == "10"
    sug = PairSuggestion(
        exercise_id=1, equipment_id=None, decision={"suggested_reps_min": 10, "suggested_reps_max": 10},
        suggested_load_kg=None, load_source=None, load_increment_kg=1.0,
    )
    assert sug.reps_target == "10"
    sug = PairSuggestion(
        exercise_id=1, equipment_id=None, decision={"suggested_reps_min": 8, "suggested_reps_max": 12},
        suggested_load_kg=None, load_source=None, load_increment_kg=1.0,
    )
    assert sug.reps_target == "8-12"

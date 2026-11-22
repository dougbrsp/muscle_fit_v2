"""#224/C: sem trabalho direto, GVT e FST-7 mantem series, parametros e RPE do protocolo."""

from __future__ import annotations

from dataclasses import replace

from app.domain import progression_engine as pe
from app.domain.training_safety import NO_DIRECT_WORK
from tests.support import progression_fixtures as fx

CONTRACT = pe.ReadinessContract(status=NO_DIRECT_WORK, max_rpe=8, allow_failure=False, volume_multiplier=0.0)


def _skip(technique, *, track=None, safety=None):
    return pe.prescribe(
        track or fx.track(sets_count=10),
        fx.group(),
        safety or fx.safety(),
        CONTRACT,
        catalog=fx.CATALOG,
        model_technique=technique,
        model_fixed_sets=True,
    )


def test_gvt_mantido_no_skip():
    d = _skip("gvt")
    assert d.decision == pe.SKIP and d.can_progress is False
    assert d.blocked_by == (pe.BLOCKED_NO_DIRECT_WORK,)
    assert d.advanced_technique == "gvt" and d.suggested_sets == 10
    assert d.advanced_technique_parameters["sets_only"] is True


def test_fst7_com_par_que_permite_falha_cai_no_skip():
    # Sem trabalho direto a recuperacao nega a falha; o FST-7 exige falha.
    d = _skip("fst7", safety=fx.safety(max_safe_rpe=10))
    assert d.decision == pe.SKIP and d.failure_allowed is False
    assert d.advanced_technique is None and d.advanced_technique_parameters is None
    assert d.suggested_sets is None


def test_gvt_mantido_e_valido_pela_falha_composta_da_ficha():
    from app.domain.training_safety import validate_technique

    s = fx.safety(max_safe_rpe=10)
    d = _skip("gvt", safety=s)
    assert d.advanced_technique == "gvt"
    # Coerencia com `build_prescription`: ele valida com esta mesma falha composta.
    assert validate_technique(d.advanced_technique, s, failure_allowed=d.failure_allowed).valid


def test_gvt_no_skip_mantem_10_reps():
    d = _skip("gvt")
    assert (d.suggested_reps_min, d.suggested_reps_max) == (10, 10)
    assert d.advanced_technique_parameters["target_reps"] == 10


def test_rpe_do_protocolo_limitado_pelo_teto_do_dia():
    d = _skip("gvt", safety=fx.safety(max_safe_rpe=10))
    assert d.advanced_technique_parameters["target_rpe"] == min(10, d.effective_rpe_ceiling)
    assert d.advanced_technique_parameters["target_rpe"] <= d.effective_rpe_ceiling


def test_outra_tecnica_continua_caindo_no_skip():
    d = _skip("rest_pause")
    assert d.decision == pe.SKIP and d.advanced_technique is None
    assert d.advanced_technique_parameters is None and d.suggested_sets is None


def test_sem_tecnica_continua_skip_puro():
    d = _skip(None)
    assert d.advanced_technique is None and d.suggested_sets is None


def test_proibicao_por_objetivo_forca_continua_valendo():
    d = _skip("gvt", track=replace(fx.track(sets_count=10), training_goal="strength"))
    assert d.decision == pe.SKIP and d.advanced_technique is None


def test_fst7_em_par_sem_falha_continua_removido():
    d = _skip("fst7", safety=fx.safety(max_safe_rpe=9))
    assert d.decision == pe.SKIP and d.advanced_technique is None


def _snapshot_do_motor(technique):
    from app.domain.prescription import build_prescription

    s = fx.safety(max_safe_rpe=10)
    d = _skip(technique, safety=s)
    exercise = {"advanced_technique": technique, "reps_target": "10", "sets_count": 10}
    snap = build_prescription(exercise, safety=s, source="x", engine_version="x", decision=d.to_dict())
    return d, snap


def test_snapshot_do_gvt_mantido_sem_trabalho_direto_nao_marca_tecnica_invalida():
    from app.domain.prescription import WARNING_MODEL_TECHNIQUE_INVALID

    d, snap = _snapshot_do_motor("gvt")
    assert d.advanced_technique == "gvt"
    assert WARNING_MODEL_TECHNIQUE_INVALID not in snap["warnings"]


def test_snapshot_do_fst7_sem_trabalho_direto_retira_a_tecnica_e_avisa():
    from app.domain.prescription import WARNING_MODEL_TECHNIQUE_INVALID, WARNING_TECHNIQUE_REMOVED_NO_DIRECT_WORK

    d, snap = _snapshot_do_motor("fst7")
    assert d.advanced_technique is None
    assert snap["advanced_technique"] is None and snap["advanced_technique_parameters"] is None
    # um aviso so: o novo, sem o de tecnica invalida e sem exigir escolha
    assert snap["warnings"] == [WARNING_TECHNIQUE_REMOVED_NO_DIRECT_WORK]
    assert WARNING_MODEL_TECHNIQUE_INVALID not in snap["warnings"]
    assert snap["requires_user_choice"] is False
    # series e faixa da linha intactas
    assert snap["sets_count"] == 10 and snap["reps_target"] == "10"


def test_snapshot_sem_decisao_com_prontidao_sem_trabalho_direto_retira_fst7():
    from app.domain.prescription import WARNING_TECHNIQUE_REMOVED_NO_DIRECT_WORK, build_prescription
    from app.domain.training_safety import readiness_for_status

    snap = build_prescription(
        {"advanced_technique": "fst7", "reps_target": "10", "sets_count": 4},
        safety=fx.safety(max_safe_rpe=10),
        readiness=readiness_for_status(NO_DIRECT_WORK),
    )
    assert snap["advanced_technique"] is None and snap["advanced_technique_parameters"] is None
    assert snap["warnings"] == [WARNING_TECHNIQUE_REMOVED_NO_DIRECT_WORK]
    assert snap["requires_user_choice"] is False and snap["sets_count"] == 4


def test_fst7_em_dia_normal_mantem_tecnica_sem_aviso():
    from app.domain.prescription import build_prescription

    snap = build_prescription(
        {"advanced_technique": "fst7", "reps_target": "10", "sets_count": 4}, safety=fx.safety(max_safe_rpe=10)
    )
    assert snap["advanced_technique"] == "fst7" and snap["warnings"] == []


def test_snapshot_do_gvt_sem_trabalho_direto_mantem_tecnica_sem_aviso():
    _, snap = _snapshot_do_motor("gvt")
    assert snap["advanced_technique"] == "gvt" and snap["warnings"] == []


def _prescrever_pares(monkeypatch, technique, contract, pair_safety=None):
    from datetime import datetime

    from app import progression_state as ps
    from app.generation import deterministic_prescription as dp

    monkeypatch.setattr(ps, "load_laboral_ids", lambda conn, ids: frozenset())
    monkeypatch.setattr(dp, "resolve_training_safety_bulk", lambda conn, pairs: {(7, 3): pair_safety or fx.safety(max_safe_rpe=10)})
    monkeypatch.setattr(ps, "load_technique_catalog", lambda conn: fx.CATALOG)
    monkeypatch.setattr(ps, "load_primary_groups", lambda conn, ids: ({7: 1}, {7: 1.0}))
    monkeypatch.setattr(ps, "load_exercise_flags", lambda conn, ids: {})
    monkeypatch.setattr(ps, "load_equipment_steps", lambda conn, ids: {})
    monkeypatch.setattr(ps, "load_user_flags", lambda conn, uid: False)
    monkeypatch.setattr(ps, "load_all_group_states", lambda conn, uid: {})
    monkeypatch.setattr(ps, "load_tracks", lambda conn, uid: {})
    monkeypatch.setattr(ps, "load_user_load_ceilings", lambda conn, uid: {})
    monkeypatch.setattr(dp, "current_e1rm_for_requests", lambda conn, uid, reqs, now: {})
    request = dp.PairRequest(
        exercise_id=7, equipment_id=3, muscle_group_id=1, model_sets_count=4, sequence=1, model_technique=technique
    )
    out = dp.prescribe_pairs(object(), 1, [request], readiness={1: contract}, now=datetime(2026, 11, 22, 10, 0))
    return out[request.key]


def test_modo_b_fst7_sem_trabalho_direto_emite_o_codigo_novo(monkeypatch):
    from app.domain.prescription import WARNING_TECHNIQUE_REMOVED_NO_DIRECT_WORK

    p = _prescrever_pares(monkeypatch, "fst7", CONTRACT)
    assert p.technique is None and p.warnings == (WARNING_TECHNIQUE_REMOVED_NO_DIRECT_WORK,)


def test_modo_b_tecnica_incompativel_com_o_par_sem_trabalho_direto_mantem_o_aviso_antigo(monkeypatch):
    from app.domain.prescription import WARNING_MODEL_TECHNIQUE_INVALID

    incompativel = fx.safety(max_safe_rpe=10, techniques={"fst7": 0})
    p = _prescrever_pares(monkeypatch, "fst7", CONTRACT, pair_safety=incompativel)
    assert p.technique is None and p.warnings == (WARNING_MODEL_TECHNIQUE_INVALID,)


def test_removed_technique_warning_os_dois_ramos():
    from app.domain.prescription import (
        WARNING_MODEL_TECHNIQUE_INVALID,
        WARNING_TECHNIQUE_REMOVED_NO_DIRECT_WORK,
        removed_technique_warning,
    )

    ok = fx.safety(max_safe_rpe=10)
    assert removed_technique_warning("fst7", ok) == WARNING_TECHNIQUE_REMOVED_NO_DIRECT_WORK
    assert removed_technique_warning("fst7", fx.safety(max_safe_rpe=10, techniques={"fst7": 0})) == (
        WARNING_MODEL_TECHNIQUE_INVALID
    )
    # par que proibe a falha: invalida mesmo com falha permitida
    assert removed_technique_warning("fst7", fx.safety(max_safe_rpe=9)) == WARNING_MODEL_TECHNIQUE_INVALID


def test_modo_b_tecnica_removida_fora_do_dia_sem_trabalho_direto_mantem_o_aviso_antigo(monkeypatch):
    from app.domain.prescription import WARNING_MODEL_TECHNIQUE_INVALID

    contract = pe.ReadinessContract(status="READY", max_rpe=10, allow_failure=True, volume_multiplier=1.0)
    p = _prescrever_pares(monkeypatch, "fst7", replace(contract, status="REDUCED_DOSE", allow_failure=False))
    assert p.technique is None and p.warnings == (WARNING_MODEL_TECHNIQUE_INVALID,)

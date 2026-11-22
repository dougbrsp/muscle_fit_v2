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


def test_snapshot_do_fst7_sem_trabalho_direto_registra_comportamento_atual():
    # Hoje o motor nao mantem o FST-7 nesses dias (exige falha); fixa o que o snapshot faz.
    d, snap = _snapshot_do_motor("fst7")
    assert d.advanced_technique is None
    assert snap["warnings"] == []
    assert snap.get("advanced_technique") == "fst7"

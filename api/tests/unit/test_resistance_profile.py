import pytest

from app.domain import resistance_profile as rp


def test_vocabularios_tem_unknown_e_versao():
    v = rp.export_vocabulary()
    assert v["version"] == rp.RESISTANCE_VOCABULARY_VERSION
    for k in ("source", "mechanism", "trajectory", "profile", "line", "direction_changes"):
        assert "unknown" in v[k]
    assert set(v["flags"]) == {
        "bench_adjustment", "foot_position", "trunk_hip_position",
        "anthropometry", "reaction_forces", "friction", "setup_dependent_range",
    }
    assert set(v["status"]) == {"suggested", "reviewed", "verified", "rejected"}


def test_ausencia_vira_unknown_nunca_constante():
    p = rp.validate_profile({})
    assert p.profile == "unknown" and p.line == "unknown"
    assert p.status == "suggested" and p.origin == "ai" and p.confidence == "low"
    assert rp.signature({}) == "unknown:unknown:unknown:unknown:unknown"


@pytest.mark.parametrize(
    "data,code,field",
    [
        ({"source": "foo"}, "invalid_code", "source"),
        ({"profile": 3}, "invalid_code", "profile"),
        ({"flags": ["nope"]}, "invalid_flag", "flags"),
        ({"flags": "friction"}, "invalid_flag", "flags"),
        ({"confidence": "max"}, "invalid_code", "confidence"),
        ({"status": "ok"}, "invalid_code", "status"),
        ({"origin": "ai", "status": "verified"}, "ai_cannot_review", "status"),
    ],
)
def test_recusa_invalidos(data, code, field):
    with pytest.raises(rp.ResistanceProfileError) as e:
        rp.validate_profile(data)
    assert (e.value.code, e.value.field) == (code, field)


def test_assinatura_e_rotulo():
    d = {"source": "cable", "mechanism": "simple_pulley", "trajectory": "cable_free_path",
         "profile": "approximately_constant", "line": "cable_angle_variable"}
    assert rp.signature(d) == (
        "cable:simple_pulley:cable_free_path:approximately_constant:cable_angle_variable"
    )
    assert rp.signature_label(d).startswith("Cabo · Polia simples")


def test_transicoes():
    t = rp.can_transition
    assert t(None, "suggested", "ai")
    assert t("suggested", "suggested", "ai")
    assert not t(None, "reviewed", "ai")
    assert not t("suggested", "verified", "ai")
    assert not t("rejected", "suggested", "ai")
    assert not t("verified", "suggested", "ai")
    assert t("suggested", "reviewed", "admin")
    assert t("reviewed", "verified", "admin")
    assert t(None, "verified", "admin")
    assert t("rejected", "suggested", "admin")
    assert not t("suggested", "bogus", "admin")
    assert not t("suggested", "reviewed", "robot")


def test_so_revisado_pesa():
    assert rp.is_reliable("reviewed") and rp.is_reliable("verified")
    assert not rp.is_reliable("suggested") and not rp.is_reliable("rejected")


def test_carimbo_de_revisao_humana():
    assert rp.HUMAN_REVIEW_STATUSES == {"reviewed", "verified", "rejected"}
    assert rp.stamps_review("rejected") and rp.stamps_review("verified")
    assert not rp.stamps_review("suggested")


def test_pendente():
    assert rp.PENDING_STATUSES == {"suggested"}
    assert rp.is_pending(None) and rp.is_pending("suggested")
    assert not rp.is_pending("reviewed") and not rp.is_pending("rejected")


def test_desconhecido():
    assert rp.is_unknown(None) and rp.is_unknown({})
    assert rp.is_unknown(rp.validate_profile(None))
    assert rp.is_unknown({"source": "unknown", "line": None})
    assert not rp.is_unknown({"mechanism": next(k for k in rp.MECHANISMS if k != "unknown")})
    assert not rp.is_unknown(rp.validate_profile({"line": next(k for k in rp.LINES if k != "unknown")}))


def test_origens():
    assert (rp.ORIGIN_AI, rp.ORIGIN_ADMIN) == ("ai", "admin")
    assert set(rp.ORIGINS) == {rp.ORIGIN_AI, rp.ORIGIN_ADMIN}

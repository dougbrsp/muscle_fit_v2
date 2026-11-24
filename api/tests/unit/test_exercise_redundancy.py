"""Regras do motor de redundância (issue #288, lote 45)."""

from app.domain import exercise_redundancy as er

REV = {"status": "reviewed", "origin": "admin", "confidence": "high", "source": "cable", "mechanism": "simple_pulley",
       "trajectory": "free", "profile": "ascending", "line": "horizontal"}
SUGGESTED = {**REV, "status": "suggested", "origin": "ai"}


def c(**kw):
    base = dict(exercise_id=1, equipment_id=1, contributions={10: "PRIMARY", 11: "SECONDARY"},
                biomechanical_pattern="push", primary_joint="elbow", movement="m", body_position="seated",
                trajectory="t", unilateral=False, resistance=dict(REV))
    base.update(kw)
    return base


def other(**kw):
    return c(exercise_id=2, equipment_id=2, **kw)


def lvl(a, b):
    return er.compare(a, b).level


def test_same_exercise_and_equipment_is_near_duplicate():
    assert lvl(c(), c(resistance=None)) == er.NEAR_DUPLICATE
    assert lvl(c(), c(equipment_id=2)) != er.NEAR_DUPLICATE


def test_same_exercise_without_equipment_is_near_duplicate():
    assert lvl(c(equipment_id=None), c(equipment_id=None, resistance=None)) == er.NEAR_DUPLICATE
    assert lvl(c(equipment_id=None), c(equipment_id=2)) != er.NEAR_DUPLICATE


def test_insufficient_data_is_unknown_never_low():
    assert lvl(c(), other(contributions={})) == er.UNKNOWN
    assert lvl(c(), other(contributions={10: "SECONDARY"})) == er.UNKNOWN
    assert lvl(c(), other(biomechanical_pattern=None)) == er.UNKNOWN


def test_shared_secondary_or_no_shared_muscle_is_low():
    r = er.compare(c(), other(contributions={11: "PRIMARY", 10: "SECONDARY"}))
    assert r.level == er.LOW and "shared_secondary_only" in r.reasons
    r = er.compare(c(), other(contributions={99: "PRIMARY"}))
    assert r.level == er.LOW and "no_shared_muscle" in r.reasons


def test_shared_primary_with_different_pattern_or_joint_is_low():
    assert lvl(c(), other(biomechanical_pattern="pull")) == er.LOW
    r = er.compare(c(), other(primary_joint="knee"))
    assert r.level == er.LOW and "Articulação principal" in r.diverging


def test_high_needs_equal_reliable_signature():
    r = er.compare(c(), other())
    assert r.level == er.HIGH and r.confidence == "high" and "same_resistance_signature" in r.reasons


def test_different_resistance_profile_is_moderate():
    r = er.compare(c(), other(resistance={**REV, "profile": "descending"}))
    assert r.level == er.MODERATE and "Resistência: perfil" in r.diverging


def test_exercise_traits_differ_caps_at_moderate():
    assert lvl(c(), other(trajectory="x")) == er.MODERATE
    assert lvl(c(), other(unilateral=True)) == er.MODERATE


def test_without_reliable_profile_on_both_sides_is_at_most_low():
    for res in (None, SUGGESTED):
        for a, b in ((c(), other(resistance=res)), (c(resistance=res), other()), (c(resistance=res), other(resistance=res))):
            r = er.compare(a, b)
            assert r.level == er.LOW and r.confidence == "low" and "no_reliable_profile" in r.reasons
    assert lvl(c(resistance=None), other(resistance=None, trajectory="x")) == er.LOW
    assert "profile_suggested_ignored" in er.compare(c(), other(resistance=SUGGESTED)).reasons


def test_unknown_component_is_not_equality():
    unk = {**REV, "line": "unknown"}
    r = er.compare(c(resistance=unk), other(resistance=unk))
    assert r.level == er.MODERATE and "profile_incomplete" in r.reasons


def test_confidence_is_lowest_of_profiles():
    r = er.compare(c(), other(resistance={**REV, "confidence": "medium"}))
    assert r.level == er.HIGH and r.confidence == "medium"


def test_level_order_helpers():
    assert er.LEVEL_ORDER == (er.UNKNOWN, er.LOW, er.MODERATE, er.HIGH, er.NEAR_DUPLICATE)
    assert [er.level_rank(x) for x in er.LEVEL_ORDER] == [0, 1, 2, 3, 4]
    assert er.DISPLAY_MIN_LEVEL == er.MODERATE
    assert er.at_least(er.HIGH, er.DISPLAY_MIN_LEVEL) and er.at_least(er.MODERATE, er.MODERATE)
    assert not er.at_least(er.LOW, er.DISPLAY_MIN_LEVEL) and not er.at_least(er.UNKNOWN, er.LOW)


def test_to_dict_has_labels_and_accepts_dataclass():
    d = er.to_dict(er.compare(er.Candidate(**c()), er.Candidate(**other())))
    assert d["level_label"] == "Alta" and d["reasons"][0]["label"] and d["version"] == er.REDUNDANCY_VERSION

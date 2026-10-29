from app.set_types import (
    BACKOFF,
    DROP_SET,
    FEELER,
    FORCED_REP,
    REST_PAUSE,
    WARMUP,
    WORKING,
    is_preparatory_set,
    is_technique_application,
    is_working_set,
    non_preparatory_sets,
    normalize_set_type,
    sql_is_working,
    sql_not_preparatory,
    sql_set_type_expr,
    working_sets,
)
import pytest


def test_constants():
    assert (WORKING, WARMUP, FEELER) == ("working", "warmup", "feeler")
    assert (REST_PAUSE, DROP_SET, FORCED_REP, BACKOFF) == ("rest_pause", "drop_set", "forced_rep", "backoff")


def test_normalize_empty_is_working():
    assert normalize_set_type(None) == WORKING
    assert normalize_set_type("") == WORKING
    assert normalize_set_type("   ") == WORKING


def test_normalize_legacy_dropset_and_case():
    assert normalize_set_type("dropset") == DROP_SET
    assert normalize_set_type(" DropSet ") == DROP_SET
    assert normalize_set_type("drop_set") == DROP_SET
    assert normalize_set_type("WARMUP") == WARMUP


def test_normalize_unknown_is_preserved_not_working():
    assert normalize_set_type("Mystery") == "mystery"
    assert not is_working_set("mystery")


def test_is_working_set():
    assert is_working_set("working")
    assert is_working_set(None)
    assert is_working_set({"set_type": None})
    assert is_working_set({})
    assert not is_working_set("warmup")
    assert not is_working_set({"set_type": "dropset"})


def test_is_technique_application():
    for value in ("rest_pause", "drop_set", "dropset", "forced_rep"):
        assert is_technique_application(value)
    for value in ("working", "warmup", "feeler", "backoff", None):
        assert not is_technique_application(value)


def test_is_preparatory_set():
    assert is_preparatory_set("warmup")
    assert is_preparatory_set({"set_type": "Feeler"})
    for value in ("working", None, "drop_set", "backoff"):
        assert not is_preparatory_set(value)


def test_filters():
    sets = [
        {"set_number": 1, "set_type": "warmup"},
        {"set_number": 2, "set_type": "feeler"},
        {"set_number": 3, "set_type": "working"},
        {"set_number": 4},
        {"set_number": 5, "set_type": "dropset"},
    ]
    assert [s["set_number"] for s in working_sets(sets)] == [3, 4]
    assert [s["set_number"] for s in non_preparatory_sets(sets)] == [3, 4, 5]
    assert working_sets(None) == []


def test_sql_fragments():
    assert sql_set_type_expr("es.set_type") == "COALESCE(LOWER(TRIM(es.set_type)), 'working')"
    # M76 Lote 3b: os apelidos legados entram no predicado (mesma resposta de
    # `is_preparatory_set`).
    assert sql_not_preparatory("s.set_type") == (
        "COALESCE(LOWER(TRIM(s.set_type)), 'working') NOT IN ('feeler', 'feeler_set', 'laboral', 'warm-up', 'warm_up', 'warmup')"
    )
    assert sql_is_working() == "COALESCE(LOWER(TRIM(es.set_type)), 'working') IN ('', 'working')"
    assert sql_not_preparatory("s.`set_type`").startswith("COALESCE(LOWER(TRIM(s.`set_type`))")


def test_laboral_e_preparatoria_m100():
    """M100: a serie laboral sai de progressao, recorde/e1RM, volume e
    recuperacao pelo MESMO filtro das preparatorias."""
    from app.set_types import LABORAL, is_laboral_set, is_preparatory_set, is_working_set

    assert is_preparatory_set(LABORAL) and is_preparatory_set(" Laboral ")
    assert not is_working_set(LABORAL)
    assert is_laboral_set({"set_type": "laboral"}) and not is_laboral_set({"set_type": "warmup"})
    assert "'laboral'" in sql_not_preparatory()
    sets = [{"set_type": "laboral", "set_number": 1}, {"set_type": "working", "set_number": 2}]
    assert [s["set_number"] for s in non_preparatory_sets(sets)] == [2]
    assert [s["set_number"] for s in working_sets(sets)] == [2]


def test_sql_fragment_rejects_injection():
    with pytest.raises(ValueError):
        sql_set_type_expr("es.set_type); DROP TABLE x; --")
    with pytest.raises(ValueError):
        sql_not_preparatory("")

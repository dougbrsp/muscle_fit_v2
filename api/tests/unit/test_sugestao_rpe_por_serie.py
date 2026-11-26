from types import SimpleNamespace

from app import services
from app.domain.prescription_defaults import DEFAULT_SETS_COUNT


def _payload(rpe, sets):
    decision = {} if sets is None else {"suggested_sets": sets}
    sug = SimpleNamespace(
        decision=decision,
        last_execution=None,
        track_load_status=None,
        rpe_target=rpe,
        reps_target="8-10",
        suggested_load_kg=None,
        trend="unknown",
        load_source=None,
        calibrating=False,
        calibration_code=None,
        load_increment_kg=2.5,
        max_load_kg=None,
        equipment_id=None,
        strength_load=None,
    )
    return services._suggested_progression_payload(sug)


def test_rampa_expandida_por_serie(monkeypatch):
    _monkeypatch_indicators(monkeypatch)
    assert _payload("7-8-9", 3)["rpe_targets_by_set"] == [7.0, 8.0, 9.0]


def test_alvo_unico_repetido(monkeypatch):
    _monkeypatch_indicators(monkeypatch)
    assert _payload("8", 4)["rpe_targets_by_set"] == [8.0] * 4


def test_sem_series_usa_default(monkeypatch):
    _monkeypatch_indicators(monkeypatch)
    assert _payload("8", None)["rpe_targets_by_set"] == [8.0] * DEFAULT_SETS_COUNT


def test_sem_alvo_lista_vazia(monkeypatch):
    _monkeypatch_indicators(monkeypatch)
    assert _payload(None, 3)["rpe_targets_by_set"] == []
    assert services._suggested_progression_payload(None)["rpe_targets_by_set"] == []


def _monkeypatch_indicators(monkeypatch):
    from app.domain import prescription_service as ps

    monkeypatch.setattr(
        ps, "legacy_indicators", lambda s: {"progression": None, "suggested_reps": None}
    )
    monkeypatch.setattr(ps, "progression_note", lambda s: "")

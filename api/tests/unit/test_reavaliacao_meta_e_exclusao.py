"""#219/#215: PATCH meta com data/hora reavalia a partir da posicao mais antiga;
recalculo do alvo gravado no deload pela regra unica; can_toggle por permissao."""

from __future__ import annotations

import datetime as dt

from app import workout_sessions
from app.domain import effective_intensity as ei
from app.domain.deload import DELOAD_TARGET_RPE
from app.security import Identity
from tests.support.fake_db import fake_transaction
from tests.unit.test_deload_correcao_treino_encerrado import _Cursor

IDENTITY = Identity(user_id=1, role="regular", source="session")
OLD_DATE = dt.date(2026, 10, 25)  # o que o cursor roteirizado devolve


def _patch(monkeypatch, cursor, **payload):
    calls = []
    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(cursor))
    monkeypatch.setattr(
        workout_sessions, "reevaluate_after_correction", lambda conn, uid, aid, **kw: calls.append(kw) or {"ok": 1}
    )
    out = workout_sessions.update_session_meta(77, workout_sessions.SessionMetaIn(**payload), identity=IDENTITY)
    return out, calls


def test_mudar_data_para_antes_ancora_na_nova(monkeypatch):
    out, calls = _patch(monkeypatch, _Cursor(), local_date=dt.date(2026, 10, 20))
    assert out["progression_reevaluated"] is True
    assert calls == [{"anchor_at": dt.datetime(2026, 10, 20)}]


def test_mudar_data_para_depois_ancora_na_antiga(monkeypatch):
    _, calls = _patch(monkeypatch, _Cursor(), local_date=dt.date(2026, 10, 30))
    assert calls == [{"anchor_at": dt.datetime(2026, 10, 25)}]


def test_mudar_hora_reavalia(monkeypatch):
    _, calls = _patch(monkeypatch, _Cursor(), started_at=dt.datetime(2026, 10, 25, 7, 0))
    assert calls[0]["anchor_at"] == dt.datetime(2026, 10, 25)


def test_so_nome_nao_reavalia(monkeypatch):
    out, calls = _patch(monkeypatch, _Cursor(), name="Treino B")
    assert calls == [] and "progression_reevaluated" not in out


def test_sessao_sem_status_nao_reavalia(monkeypatch):
    _, calls = _patch(monkeypatch, _Cursor(status=None), local_date=dt.date(2026, 10, 20))
    assert calls == []


def test_data_igual_nao_reavalia(monkeypatch):
    _, calls = _patch(monkeypatch, _Cursor(), local_date=OLD_DATE)
    assert calls == []


def test_deload_recalcula_alvo_gravado_das_series(monkeypatch):
    cursor = _Cursor()
    rows = [
        {"set_id": 1, "set_type": "working", "technique_code": None, "rpe_target_source": None,
         "rpe_target_effective": 8, "exercise_id": 5, "gym_equipment_id": None, "mapping_id": None,
         "plan_technique": None, "prescription_json": None},
        {"set_id": 2, "set_type": "warmup", "technique_code": None, "rpe_target_source": None,
         "rpe_target_effective": None, "exercise_id": 5, "gym_equipment_id": None, "mapping_id": None,
         "plan_technique": None, "prescription_json": None},
    ]
    cursor.fetchall = lambda: rows
    monkeypatch.setattr(
        ei, "resolve_for_logged_exercise", lambda conn, eid: ei.EffectiveIntensity(8, 9, True)
    )
    _patch(monkeypatch, cursor, deload=True)
    updates = [s for s in cursor.executed if s.startswith("UPDATE exercise_sets")]
    assert len(updates) == 1  # a preparatoria segue NULL


def test_deload_mantem_alvo_de_protocolo_gravado_como_o_put(monkeypatch):
    cursor = _Cursor()
    row = {"set_id": 1, "set_type": "working", "technique_code": None, "rpe_target_source": "protocol",
           "rpe_target_effective": 9, "exercise_id": 5, "gym_equipment_id": None, "mapping_id": None,
           "plan_technique": None, "prescription_json": None}
    cursor.fetchall = lambda: [row]
    base = ei.EffectiveIntensity(8, 9, True)
    monkeypatch.setattr(ei, "resolve_for_logged_exercise", lambda conn, eid: base)

    def _vivo(*a, **k):
        raise AssertionError("protocolo nao pode ser recalculado ao vivo")

    monkeypatch.setattr(ei, "protocol_target_for_logged_exercise", _vivo)
    _patch(monkeypatch, cursor, deload=True)
    updates = [s for s in cursor.executed if s.startswith("UPDATE exercise_sets")]
    assert len(updates) == 1  # 9 gravado -> alvo de deload
    # o PUT (services.py) chama a regra do dono igual, para a mesma linha
    assert ei.stored_rpe_target(
        "working", base, protocol_target=9, protocol_kept=True, deload_source="manual"
    ) == DELOAD_TARGET_RPE


def test_alvo_gravado_regra_unica():
    base = ei.EffectiveIntensity(8, 9, True)
    assert ei.stored_rpe_target("working", base) == 8
    assert ei.stored_rpe_target("working", base, deload_source="manual") == DELOAD_TARGET_RPE
    assert ei.stored_rpe_target("warmup", base) is None
    assert ei.stored_rpe_target("warmup", base, deload_source="manual") is None
    assert ei.stored_rpe_target("working", base, protocol_target=9) == 9
    assert ei.stored_rpe_target("working", base, protocol_target=9, deload_source="manual") == DELOAD_TARGET_RPE
    # PUT que mantem o protocolo com alvo gravado nulo: NULL, nao a prescricao
    assert ei.stored_rpe_target("working", base, protocol_kept=True) is None
    assert ei.stored_rpe_target("working", base, protocol_kept=True, deload_source="manual") is None
    assert ei.stored_rpe_target("working", base, protocol_target=9, protocol_kept=True) == 9


def test_get_deload_can_toggle_falso_sem_editar(monkeypatch):
    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(_Cursor(saved_sets=0)))
    monkeypatch.setattr(workout_sessions, "permissions_for_request", lambda r, i: object())
    monkeypatch.setattr(workout_sessions, "has_permission", lambda p, res, act: act == "ver")
    out = workout_sessions.get_session_deload(77, request=object(), identity=IDENTITY)
    assert out["deload"]["can_toggle"] is False
    monkeypatch.setattr(workout_sessions, "has_permission", lambda p, res, act: True)
    out = workout_sessions.get_session_deload(77, request=object(), identity=IDENTITY)
    assert out["deload"]["can_toggle"] is True


def test_primeiro_indice_a_partir_da_ancora():
    from app.domain.training_history import SessionRecord
    from app.progression_state import _first_index_from

    sessions = [
        SessionRecord(1, dt.date(2026, 5, 1), ended_at=dt.datetime(2026, 5, 1, 9)),
        SessionRecord(2, dt.date(2026, 5, 2), started_at=dt.datetime(2026, 5, 2, 9)),
        SessionRecord(3, dt.date(2026, 5, 3)),
    ]
    assert _first_index_from(sessions, dt.datetime(2026, 5, 2, 9)) == 1  # mesmo instante entra
    assert _first_index_from(sessions, dt.datetime(2026, 5, 1)) == 0
    assert _first_index_from(sessions, dt.datetime(2026, 5, 4)) == 3  # depois de todas: o fim


def test_stored_rpe_target_idempotente_sobre_alvo_ja_ajustado_pelo_deload():
    from app.domain.deload import deload_effort_target

    intensity = ei.EffectiveIntensity(8, 9, True)
    for technique in (None, "drop_set"):
        adjusted = deload_effort_target(9, technique)
        assert adjusted == DELOAD_TARGET_RPE
        stored = ei.stored_rpe_target(
            "working", intensity, protocol_target=adjusted, deload_source="manual", technique_code=technique
        )
        assert stored == adjusted

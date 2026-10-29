"""M97 F4 (pendências do §66.4): sugestão do dashboard e `/volume-grupamentos` por objetivo.

- `GET /analytics/dashboard-progression-suggestion?goal=`: a sugestão na trilha,
  na fase e na escada do objetivo (motor `prescription_service.suggest_for_pair`
  -> `deterministic_prescription.prescribe_pairs` -> `progression_engine`).
  Sem `goal` (HTTP ou chamada direta da função), a resposta e a chamada ao motor
  são as de sempre.
- `GET /analytics/muscle-groups-recent-exercise-volume?goal=`: filtro opcional
  pelo predicado do dono (`VolumeService.get_recent_exercise_volume(training_goal=)`);
  sem `goal`, a chamada ao serviço é a de sempre.

Nenhuma regra aqui: os números esperados vêm dos donos (`ladder_for`,
`STRENGTH_REPS`, `F1_REPS`/`PROGRESSED_REPS`).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.domain import prescription_service as svc
from app.domain import progression_engine as pe
from app.domain import training_goal as tg
from app.security import Identity
from tests.support import progression_fixtures as fx

H = tg.TrainingGoal.HYPERTROPHY.value
S = tg.TrainingGoal.STRENGTH.value
SUPINO, BANCO, PEITO = 10, 7, 3
NOW = datetime(2026, 10, 20, 18)
IDENTIDADE = Identity(user_id=1, role="regular", source="session")


# ---------------------------------------------------------------------------
# Rota da sugestão: contrato sem/com `goal`
# ---------------------------------------------------------------------------


def _resultado():
    return {"reps_target": "10-12", "load_target": "60kg", "rpe_target": "8", "progression_note": "x"}


@pytest.fixture()
def chamadas(monkeypatch):
    from app import services

    registro = []

    def fake(mapping_id, user_id=None, **kwargs):
        registro.append((mapping_id, user_id, kwargs))
        return _resultado()

    monkeypatch.setattr(services, "suggested_progression_for_mapping", fake)
    return registro


def test_sugestao_sem_goal_chamada_direta_e_a_resposta_de_sempre(chamadas):
    """Chamada direta sem `goal` (default do `Depends`) vale como ausente: o
    motor recebe o objetivo default (None) e a resposta não ganha campo."""
    from app import analytics

    corpo = analytics.dashboard_progression_suggestion(exercise_name=None, mapping_id=SUPINO, identity=IDENTIDADE)

    assert corpo == {"mapping_id": SUPINO, **_resultado()}
    assert chamadas == [(SUPINO, 1, {"training_goal": None})]


def test_sugestao_goal_ausente_pela_http_e_identica(chamadas):
    from app import analytics

    corpo = analytics.dashboard_progression_suggestion(
        exercise_name=None, mapping_id=SUPINO, goal=None, identity=IDENTIDADE
    )
    assert corpo == {"mapping_id": SUPINO, **_resultado()}
    assert "training_goal" not in corpo
    assert chamadas[-1][2] == {"training_goal": None}


def test_sugestao_com_goal_passa_o_objetivo_ao_motor_e_ecoa(chamadas):
    from app import analytics

    corpo = analytics.dashboard_progression_suggestion(
        exercise_name=None, mapping_id=SUPINO, goal=S, identity=IDENTIDADE
    )
    assert chamadas == [(SUPINO, 1, {"training_goal": S})]
    assert corpo == {"mapping_id": SUPINO, **_resultado(), "training_goal": S}


def test_sugestao_goal_invalido_e_422(chamadas):
    from app import analytics

    with pytest.raises(HTTPException) as erro:
        analytics.dashboard_progression_suggestion(
            exercise_name=None, mapping_id=SUPINO, goal="cardio", identity=IDENTIDADE
        )
    assert erro.value.status_code == 422
    assert chamadas == []


# ---------------------------------------------------------------------------
# services -> motor: o objetivo chega à PairQuery
# ---------------------------------------------------------------------------


def test_services_repassa_o_objetivo_ao_motor_e_default_e_none(monkeypatch):
    from contextlib import contextmanager

    from app import services
    from tests.support.fake_db import CursorFactoryConn

    class _Cursor:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            return None

        def fetchone(self):
            return {"id": SUPINO}

    @contextmanager
    def fake_transaction():
        yield CursorFactoryConn(_Cursor, None)

    pedidos = []

    def fake_suggest(conn, user_id, exercise_id, equipment_id=None, **kwargs):
        pedidos.append(kwargs.get("training_goal"))
        return None

    monkeypatch.setattr(services, "transaction", fake_transaction)
    monkeypatch.setattr(services, "_resolve_canonical_exercise_id", lambda mapping_id, cur: SUPINO)
    monkeypatch.setattr(services, "list_exercise_logs", lambda limit, user_id, only_mapping_id: [])
    monkeypatch.setattr(svc, "suggest_for_pair", fake_suggest)

    services.suggested_progression_for_mapping(SUPINO, user_id=1)
    services.suggested_progression_for_mapping(SUPINO, user_id=1, training_goal=S)
    assert pedidos == [None, S]


# ---------------------------------------------------------------------------
# Motor de verdade: com `goal=strength`, a escada de força; sem, a de sempre
# ---------------------------------------------------------------------------


def _motor_sem_banco(monkeypatch, tracks, groups):
    from app import progression_state as ps
    from app.generation import deterministic_prescription as dp

    monkeypatch.setattr(ps, "load_tracks", lambda conn, user_id: {track.key: track for track in tracks})
    monkeypatch.setattr(ps, "load_all_group_states", lambda conn, user_id: {group.key: group for group in groups})
    monkeypatch.setattr(ps, "load_technique_catalog", lambda conn: {})
    monkeypatch.setattr(ps, "load_primary_groups", lambda conn, ids: ({SUPINO: PEITO}, {SUPINO: 1.0}))
    monkeypatch.setattr(ps, "load_exercise_flags", lambda conn, ids: {SUPINO: True})
    monkeypatch.setattr(ps, "load_laboral_ids", lambda conn, ids: frozenset())
    monkeypatch.setattr(ps, "load_equipment_steps", lambda conn, ids: {BANCO: Decimal("2.5")})
    monkeypatch.setattr(ps, "load_user_flags", lambda conn, user_id: False)
    monkeypatch.setattr(
        dp,
        "resolve_training_safety_bulk",
        lambda conn, pairs: {pair: fx.safety(exercise_id=pair[0], equipment_id=pair[1]) for pair in pairs},
    )


def _trilhas_dos_dois_objetivos():
    base = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, load=60, is_compound=True)
    hipertrofia = replace(base, training_goal=H, id=1, last_executed_at=datetime(2026, 10, 10))
    forca = replace(base, training_goal=S, id=2, working_load_kg=Decimal("100"), last_executed_at=datetime(2026, 10, 15))
    grupo_h = replace(fx.group(muscle_group_id=PEITO, phase=pe.F2, reps=pe.PROGRESSED_REPS), training_goal=H)
    grupo_s = pe.new_group_state(PEITO, NOW, S)
    return [hipertrofia, forca], [grupo_h, grupo_s]


def test_sugestao_de_forca_vem_da_escada_de_forca_e_sem_goal_a_de_sempre(monkeypatch):
    tracks, groups = _trilhas_dos_dois_objetivos()
    _motor_sem_banco(monkeypatch, tracks, groups)

    sempre = svc.suggest_for_pair(object(), 1, SUPINO, BANCO, readiness={}, logs=[], now=NOW)
    hipertrofia = svc.suggest_for_pair(object(), 1, SUPINO, BANCO, readiness={}, logs=[], now=NOW, training_goal=H)
    forca = svc.suggest_for_pair(object(), 1, SUPINO, BANCO, readiness={}, logs=[], now=NOW, training_goal=S)

    # Sem objetivo = o default (hipertrofia), igual campo a campo.
    assert sempre == hipertrofia
    assert (sempre.decision["suggested_reps_min"], sempre.decision["suggested_reps_max"]) == pe.PROGRESSED_REPS
    assert sempre.decision["engine_version"] == pe.ladder_for(H).engine_version

    # Força: faixa, versão da escada e carga da trilha de força.
    escada = pe.ladder_for(S)
    assert (forca.decision["suggested_reps_min"], forca.decision["suggested_reps_max"]) == escada.initial_reps
    assert escada.initial_reps == pe.STRENGTH_REPS
    assert forca.decision["engine_version"] == escada.engine_version
    assert forca.suggested_load_kg is not None and forca.suggested_load_kg >= 90
    assert sempre.suggested_load_kg is not None and sempre.suggested_load_kg < 90


# ---------------------------------------------------------------------------
# /muscle-groups-recent-exercise-volume: filtro opcional
# ---------------------------------------------------------------------------


@pytest.fixture()
def volume_recente(monkeypatch):
    from app.intelligence import methodology_volume_service as mvs
    from app.intelligence import volume_service

    chamadas = []

    def fake(self, **kwargs):
        chamadas.append(kwargs)
        return []

    monkeypatch.setattr(volume_service.VolumeService, "get_recent_exercise_volume", fake)
    monkeypatch.setattr(mvs, "load_group_hierarchy", lambda: {})
    return chamadas


def test_volume_recente_sem_goal_chamada_direta_e_a_chamada_de_sempre(volume_recente):
    from app import analytics

    analytics.muscle_groups_recent_exercise_volume(limit=None, date_from=None, date_to=None, identity=IDENTIDADE)
    assert volume_recente == [{"user_id": 1, "limit": 400, "date_from": None, "date_to": None}]


def test_volume_recente_com_goal_filtra_pelo_dono(volume_recente):
    from app import analytics

    corpo = analytics.muscle_groups_recent_exercise_volume(
        limit=None, date_from=None, date_to=None, goal=S, identity=IDENTIDADE
    )
    assert volume_recente[-1]["training_goal"] == S
    assert set(corpo) == {"limit", "date_from", "date_to", "items"}  # contrato da resposta igual
    with pytest.raises(HTTPException) as erro:
        analytics.muscle_groups_recent_exercise_volume(
            limit=None, date_from=None, date_to=None, goal="cardio", identity=IDENTIDADE
        )
    assert erro.value.status_code == 422

"""#137 B: a carga de partida nunca vem de OUTRO equipamento.

Sem execução do exercício no equipamento pedido, o par fica sem carga
sugerida pela execução (a primeira sessão calibra); a nota diz que não há
histórico neste equipamento.
"""

from __future__ import annotations

from datetime import date

from app import progression_state as ps
from app.domain import prescription_service as svc
from app.domain import progression_engine as pe
from app.generation import deterministic_prescription as dp

EX, EQ_A, EQ_B = 10, 7, 8


def _log(equipment_id, kg, local_date=date(2026, 10, 1)):
    sets = [{"set_number": 1, "weight_kg": kg, "reps": 10, "rpe": 8, "set_type": "working"}]
    return {"mapping_id": EX, "equipment_catalog_id": equipment_id, "local_date": local_date, "sets": sets}


def _decisao(load=None):
    return pe.ProgressionDecision(
        group_state_id=1, muscle_group_id=3, track_id=5, suggested_reps_min=10, suggested_reps_max=12,
        decision=pe.HOLD, suggested_load_kg=load, effective_rpe_target=8,
    )


def _motor(monkeypatch, decisions):
    def fake_prescribe(conn, user_id, requests, *, readiness=None, now=None, model_fixed_sets=True):
        return {
            r.key: dp.PairPrescription(decision=decisions[r.key], safety=None, technique=None)
            for r in requests
            if r.key in decisions
        }

    monkeypatch.setattr(dp, "prescribe_pairs", fake_prescribe)
    monkeypatch.setattr(ps, "load_tracks", lambda conn, user_id: {})
    monkeypatch.setattr(ps, "load_equipment_steps", lambda conn, ids: {})


def _sugerir(equipment_id, logs):
    return svc.suggest_for_pair(object(), 1, EX, equipment_id, readiness={}, logs=logs)


def test_dois_equipamentos_cada_um_usa_so_o_proprio_historico(monkeypatch):
    _motor(monkeypatch, {(EX, EQ_A): _decisao(), (EX, EQ_B): _decisao()})
    logs = [_log(EQ_A, 50), _log(EQ_B, 30, date(2026, 9, 1))]
    a, b = _sugerir(EQ_A, logs), _sugerir(EQ_B, logs)
    assert (a.suggested_load_kg, a.last_execution.last_load_kg) == (50.0, 50.0)
    assert (b.suggested_load_kg, b.last_execution.last_load_kg) == (30.0, 30.0)


def test_equipamento_sem_execucao_nao_herda_carga_de_outro(monkeypatch):
    _motor(monkeypatch, {(EX, EQ_B): _decisao()})
    s = _sugerir(EQ_B, [_log(EQ_A, 50)])
    assert s.suggested_load_kg is None and s.load_source is None and s.last_execution is None
    nota = svc.progression_note(s)
    assert nota.startswith("Sem histórico neste equipamento")
    assert "calibração" not in nota and "Sem histórico registrado" not in nota


def test_sem_equipamento_pedido_mantem_o_comportamento_atual(monkeypatch):
    _motor(monkeypatch, {(EX, None): _decisao()})
    s = _sugerir(None, [_log(EQ_A, 50)])
    assert s.last_execution.last_load_kg == 50.0
    assert svc.last_execution_from_logs([_log(EQ_A, 50)], EX).equipment_id == EQ_A
    sem = _sugerir(None, [])
    assert svc.progression_note(sem).startswith("Sem histórico registrado")


def test_carga_do_motor_permanece_sem_execucao_no_equipamento(monkeypatch):
    _motor(monkeypatch, {(EX, EQ_B): _decisao(load=45.0)})
    s = _sugerir(EQ_B, [_log(EQ_A, 50)])
    assert s.suggested_load_kg == 45.0 and s.load_source == svc.LOAD_SOURCE_ENGINE
    assert s.last_execution is None


def test_log_sem_equipamento_nao_casa_com_o_pedido():
    assert svc.last_execution_from_logs([_log(None, 50)], EX, EQ_A) is None
    assert svc.last_execution_from_logs([_log(None, 50)], EX).last_load_kg == 50.0


def _trilha(equipment_id, executed_at, load_status="CALIBRATION"):
    from app.domain.progression_engine import TrackState

    return TrackState(exercise_id=EX, equipment_id=equipment_id, last_executed_at=executed_at, load_status=load_status)


def _motor_com_trilhas(monkeypatch, trilhas, decisions):
    _motor(monkeypatch, decisions)
    monkeypatch.setattr(ps, "load_tracks", lambda conn, user_id: {t.key: t for t in trilhas})


def test_sem_pedido_trilha_mais_recente_em_b_usa_execucao_de_b(monkeypatch):
    # ultima execucao em A (sem trilha em A); trilha so em B -> resolve para B
    _motor_com_trilhas(monkeypatch, [_trilha(EQ_B, date(2026, 9, 1))], {(EX, EQ_B): _decisao()})
    logs = [_log(EQ_A, 50, date(2026, 10, 1)), _log(EQ_B, 30, date(2026, 9, 1))]
    s = _sugerir(None, logs)
    assert s.equipment_id == EQ_B
    assert s.last_execution.equipment_id == EQ_B and s.last_execution.last_load_kg == 30.0


def test_sem_pedido_trilha_em_b_sem_execucao_em_b_fica_sem_carga(monkeypatch):
    _motor_com_trilhas(monkeypatch, [_trilha(EQ_B, date(2026, 9, 1))], {(EX, EQ_B): _decisao()})
    s = _sugerir(None, [_log(EQ_A, 50)])
    assert s.equipment_id == EQ_B
    assert s.last_execution is None and s.suggested_load_kg is None and s.load_source is None


def test_nota_nao_diz_sem_historico_no_equipamento_quando_ha_trilha(monkeypatch):
    trilha = _trilha(EQ_A, date(2026, 10, 1), svc.LOAD_NOT_APPLICABLE)
    _motor_com_trilhas(monkeypatch, [trilha], {(EX, EQ_A): _decisao()})
    s = _sugerir(EQ_A, [])
    assert s.track_load_status == svc.LOAD_NOT_APPLICABLE and s.last_execution is None
    assert "neste equipamento" not in svc.progression_note(s)

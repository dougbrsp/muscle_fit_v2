"""#188 (carga2): prescricao do par com a academia da maquina (contexto)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.domain import prescription_service as svc
from app.domain.equipment_context import MACHINE_TYPE_CODES
from app.domain.progression_engine import (
    CALIBRATION_CODE_CONTEXT,
    CALIBRATION_CODE_START_OTHER_GYM,
    TrackState,
)
from app.generation import deterministic_prescription as dp

MAQUINA = sorted(MACHINE_TYPE_CODES)[0]


def _log(gym, load, date, type_code=MAQUINA):
    return {
        "mapping_id": 10,
        "equipment_catalog_id": 7,
        "equipment_gym_id": gym,
        "equipment_type_code": type_code,
        "local_date": date,
        "sets": [{"set_number": 1, "set_type": "working", "reps": 10, "weight_kg": load}],
    }


def test_maquina_sem_historico_na_academia_b_parte_da_academia_a():
    logs = [_log(1, 50, "2026-11-10")]
    last = svc.last_execution_from_logs(logs, 10, 7, gym_context=2)
    assert last.from_other_gym is True
    assert last.working_load_kg == 50.0


def test_segunda_sessao_em_b_usa_a_ultima_de_b():
    logs = [_log(1, 60, "2026-11-12"), _log(2, 40, "2026-11-11"), _log(1, 50, "2026-11-10")]
    last = svc.last_execution_from_logs(logs, 10, 7, gym_context=2)
    assert last.from_other_gym is False
    assert last.working_load_kg == 40.0


def test_peso_livre_e_chamada_sem_academia_iguais_a_hoje():
    logs = [_log(1, 60, "2026-11-12", type_code=None), _log(2, 40, "2026-11-11", type_code=None)]
    # peso livre: o consumidor resolve pelo dono e recebe None (sem academia)
    assert svc.last_execution_from_logs(logs, 10, 7, gym_context=None).working_load_kg == 60.0
    assert svc.last_execution_from_logs(logs, 10, 7).working_load_kg == 60.0
    assert svc.last_execution_from_logs(logs, 10, 7).from_other_gym is False


def _suggestion(decision, source):
    return svc.PairSuggestion(
        exercise_id=10,
        equipment_id=7,
        decision=decision,
        suggested_load_kg=50.0,
        load_source=source,
        load_increment_kg=2.5,
        last_execution=svc.LastExecution("2026-11-10", 7, 50.0, 10, 50.0, from_other_gym=True),
    )


def test_partida_de_outra_academia_sinaliza_calibracao_e_nota():
    s = _suggestion({"decision": "HOLD"}, svc.LOAD_SOURCE_OTHER_GYM_START)
    assert s.calibrating is True
    assert s.calibration_code == CALIBRATION_CODE_START_OTHER_GYM
    assert svc.OTHER_GYM_START_NOTE in svc.progression_note(s)


def test_calibracao_do_contexto_vem_da_decisao_do_motor():
    s = _suggestion({"decision": "HOLD", "calibrating": True, "calibration_code": CALIBRATION_CODE_CONTEXT}, "engine")
    assert s.calibrating is True
    assert s.calibration_code == CALIBRATION_CODE_CONTEXT
    assert svc.CONTEXT_CALIBRATION_NOTE in svc.progression_note(s)


def test_apos_calibracao_decide_normalmente():
    s = _suggestion({"decision": "INCREASE_LOAD", "calibrating": False}, svc.LOAD_SOURCE_ENGINE)
    assert s.calibrating is False
    assert s.calibration_code is None
    assert svc.CONTEXT_CALIBRATION_NOTE not in svc.progression_note(s)


def test_geracao_passa_a_academia_ao_motor():
    tracks = {}
    source = TrackState(
        exercise_id=10,
        equipment_id=7,
        id=1,
        load_status="ACTIVE",
        working_load_kg=Decimal("50"),
        last_executed_at=datetime(2026, 11, 10),
        gym_context=1,
    )
    tracks[source.key] = source
    request = dp.PairRequest(exercise_id=10, equipment_id=7, muscle_group_id=3, gym_context=2)
    track = dp._track_for(request, tracks, muscle_group_id=3, stimulus_weight=1.0, is_compound=False, load_step=None)
    assert track.start_from_other_gym is True
    assert track.in_context_calibration is True
    assert track.working_load_kg == Decimal("50")
    # sem academia (peso livre / legado): a trilha de sempre
    legacy = dp._track_for(
        dp.PairRequest(exercise_id=10, equipment_id=7, muscle_group_id=3),
        tracks,
        muscle_group_id=3,
        stimulus_weight=1.0,
        is_compound=False,
        load_step=None,
    )
    assert legacy.start_from_other_gym is False


class _Cur:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.cur = _Cur(rows)

    def cursor(self):
        return self.cur


def test_academia_do_vinculo_resolvida_em_lote_pelo_dono():
    conn = _Conn([
        {"id": 5, "gym_id": 2, "equipment_type_code": MAQUINA},
        {"id": 6, "gym_id": 2, "equipment_type_code": None},
    ])
    assert dp.gym_contexts_by_gym_equipment(conn, [5, 6, None]) == {5: 2, 6: None}
    assert len(conn.cur.sql) == 1
    assert dp.gym_contexts_by_gym_equipment(_Conn([]), []) == {}


def test_gym_contexts_falha_de_leitura_propaga_em_vez_de_cair_na_trilha_sem_academia():
    import pytest

    class _Quebrada:
        def cursor(self):
            raise RuntimeError("data api fora")

    with pytest.raises(RuntimeError):
        dp.gym_contexts_by_gym_equipment(_Quebrada(), [5])

"""Dono de "sessao encerrada" (`domain/session_status.py`) e os reexports dos consumidores."""

from __future__ import annotations

from datetime import date, datetime

from app import nutrition_targets_service as nts
from app import progression_state as ps
from app.domain import deload as dl
from app.domain import session_status as ss


def test_predicado_e_literal_unico():
    assert ss.SESSION_STATUS_DONE == "done"
    assert ss.is_closed_session("done") and not ss.is_closed_session(None)
    assert not ss.is_closed_session("in_progress")  # NULL/em andamento fora do historico: nao muda


def test_decisao_de_reavaliar_fica_no_dono():
    assert ss.reevaluates_progression("done", changed=True)
    assert not ss.reevaluates_progression("done", changed=False)
    assert not ss.reevaluates_progression(None, changed=True)
    assert not ss.reevaluates_progression("in_progress", changed=True)


def test_reexports_continuam_validos():
    assert dl.SESSION_STATUS_DONE is ss.SESSION_STATUS_DONE
    assert nts.DONE_SESSION_STATUS is ss.SESSION_STATUS_DONE


def test_ancora_e_a_posicao_mais_antiga():
    assert ps.correction_anchor(date(2026, 5, 3), datetime(2026, 5, 1, 9), None) == datetime(2026, 5, 1, 9)
    assert ps.correction_anchor(date(2026, 5, 1), datetime(2026, 5, 1, 9)) == datetime(2026, 5, 1)
    assert ps.correction_anchor(None) is None

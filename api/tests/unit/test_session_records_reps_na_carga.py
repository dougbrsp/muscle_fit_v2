"""Reps exibidas da marca vigente = MAXIMO feito na carga (decisao `reps-max`).

A carga continua decidindo o recorde (`record_events` inalterado); so as reps
da marca vigente exibida no Modo Treino passam a ser o maximo de reps na
carga, em todas as exposicoes da chave.

Rodar: cd api && python -m pytest tests/unit/test_session_records_reps_na_carga.py
"""

from __future__ import annotations

from app.domain.equipment_record import current_mark_with_exposures, record_payload
from app.intelligence.dashboard import session_records as sr

EX = 38
GLOBAL = 38
OUTRO = 39


def _hist(activity_id, max_kg, *, equipment_id=GLOBAL):
    return {
        "activity_id": activity_id,
        "exercise_row_id": activity_id * 10,
        "exercise_id": EX,
        "local_date": f"2026-10-{activity_id:02d}",
        "started_at": None,
        "position_in_session": 1,
        "equipment_id": equipment_id,
        "load_convention": None,
        "prescribed_reps_target": None,
        "max_weight_kg": max_kg,
        "min_weight_kg": max_kg,
    }


def _sets(activity_id, reps_list, kg):
    return [
        {"exercise_row_id": activity_id * 10, "set_number": i + 1, "reps": r, "weight_kg": kg, "rpe": None}
        for i, r in enumerate(reps_list)
    ]


def _payload(history, sets, *, equipment_id=GLOBAL):
    mark, ids = current_mark_with_exposures(
        history, exercise_id=EX, equipment_id=equipment_id, load_convention=None
    )
    kind, event = mark
    return kind, record_payload(event, sets, kind, ids), ids


def test_historico_do_leg_press_mostra_14_reps_e_mantem_kind_e_eventos():
    history = [_hist(1, 120), _hist(2, 140), _hist(3, 140), _hist(4, 140), _hist(5, 140)]
    sets = (
        _sets(1, [12], 120)
        + _sets(2, [10, 9], 140)
        + _sets(3, [12, 10, 8], 140)
        + _sets(4, [14, 12, 11], 140)
        + _sets(5, [13, 12, 12], 140)
    )
    kind, payload, ids = _payload(history, sets)
    assert kind == "record"
    assert payload == {"reps": 14, "weight_kg": 140.0, "kind": "record"}
    assert ids == [20, 30, 40, 50]
    # a regra de evento nao muda: so a 2a exposicao (120 -> 140) e recorde
    eventos = sr.record_events(sr.parse_history(history))
    assert [e.exposure.activity_id for e in eventos] == [2]
    # reps por evento = as da propria exposicao
    assert sr.record_set_reps(eventos[0], sets) == 10


def test_empate_de_carga_com_mais_reps_em_sessao_posterior_so_muda_as_reps():
    history = [_hist(1, 100), _hist(2, 120), _hist(3, 120)]
    sets = _sets(2, [8], 120) + _sets(3, [11], 120)
    kind, payload, _ = _payload(history, sets)
    assert kind == "record"
    assert payload["reps"] == 11 and payload["weight_kg"] == 120.0
    assert [e.exposure.activity_id for e in sr.record_events(sr.parse_history(history))] == [2]


def test_best_load_tambem_usa_o_maximo_na_carga():
    history = [_hist(1, 140), _hist(2, 140), _hist(3, 100)]
    sets = _sets(1, [10], 140) + _sets(2, [14], 140) + _sets(3, [20], 100)
    kind, payload, _ = _payload(history, sets)
    assert kind == "best_load"
    assert payload == {"reps": 14, "weight_kg": 140.0, "kind": "best_load"}


def test_carga_maior_depois_zera_as_reps_da_nova_carga():
    history = [_hist(1, 100), _hist(2, 120), _hist(3, 120), _hist(4, 130)]
    sets = _sets(2, [15], 120) + _sets(3, [12], 120) + _sets(4, [6, 5], 130)
    kind, payload, ids = _payload(history, sets)
    assert kind == "record"
    assert payload == {"reps": 6, "weight_kg": 130.0, "kind": "record"}
    assert ids == [40]  # reps de 120 kg (15) nao contaminam a marca de 130 kg


def test_outra_chave_nao_contamina():
    history = [_hist(1, 120), _hist(2, 140), _hist(3, 140, equipment_id=OUTRO)]
    sets = _sets(2, [10], 140) + _sets(3, [25], 140)
    kind, payload, ids = _payload(history, sets)
    assert kind == "record"
    assert payload["reps"] == 10
    assert ids == [20]
    # a outra chave (so 1 exposicao) tem a sua propria marca
    kind2, payload2, _ = _payload(history, sets, equipment_id=OUTRO)
    assert kind2 == "best_load" and payload2["reps"] == 25


def test_series_de_outra_carga_na_mesma_exposicao_sao_ignoradas():
    history = [_hist(1, 100), _hist(2, 120)]
    sets = _sets(2, [8], 120) + _sets(2, [30], 80)
    _, payload, _ = _payload(history, sets)
    assert payload["reps"] == 8

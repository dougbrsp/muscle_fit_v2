"""`resolve_upcoming_slot`: o "proximo" da tela inicial e o treino MAIS PROXIMO
(hoje, se a ficha de hoje ainda nao foi executada), nao o de amanha.
`resolve_next_slot` (geracao) continua avancando a partir da ultima ficha."""

from __future__ import annotations

import contextlib

import pytest

from app import workout_plan_templates_engine as engine
from app.intelligence.dashboard import adherence_service as ads


def _template(n: int = 6) -> dict:
    return {"id": 28, "name": "M", "slots": [{"sequence": i, "name": f"S{i}"} for i in range(1, n + 1)]}


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.sql, self.params = sql, params

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, rows):
        self.cur = _Cursor(rows)

    def cursor(self):
        return self.cur


def _patch(monkeypatch, rows) -> _Conn:
    conn = _Conn(rows)
    monkeypatch.setattr("app.db.transaction", lambda: contextlib.nullcontext(conn))
    return conn


def _row(seq, activity_id=None, status=None):
    return {"template_slot_sequence": seq, "activity_id": activity_id, "session_status": status}


def _seq(monkeypatch, rows, n=6):
    _patch(monkeypatch, rows)
    return engine.resolve_upcoming_slot(_template(n), 1)["sequence"]


def test_ficha_nao_executada_mantem_o_proprio_slot(monkeypatch):
    # Caso real: ficha 167 (slot 5) gerada na vespera, sem atividade.
    assert _seq(monkeypatch, [_row(5)]) == 5


def test_ficha_executada_avanca_para_o_seguinte(monkeypatch):
    assert _seq(monkeypatch, [_row(5, 265, "done")]) == 6


def test_wraparound_no_ultimo_slot(monkeypatch):
    assert _seq(monkeypatch, [_row(6, 300, "done")]) == 1


def test_sem_ficha_devolve_primeiro_slot(monkeypatch):
    assert _seq(monkeypatch, []) == 1


def test_sequence_removido_do_modelo_devolve_primeiro_slot(monkeypatch):
    assert _seq(monkeypatch, [_row(99, 300, "done")]) == 1


def test_atividade_em_andamento_nao_conta_como_executada(monkeypatch):
    assert _seq(monkeypatch, [_row(5, 265, "in_progress")]) == 5


@pytest.mark.parametrize("status", [None, "", "imported"])
def test_atividade_importada_conta_como_executada(monkeypatch, status):
    assert _seq(monkeypatch, [_row(2, 7, status)]) == 3


def test_varias_atividades_basta_uma_executada(monkeypatch):
    assert _seq(monkeypatch, [_row(5, 1, "in_progress"), _row(5, 2, "done")]) == 6


def test_consulta_unica_filtra_por_user_id(monkeypatch):
    conn = _patch(monkeypatch, [_row(5)])
    engine.resolve_upcoming_slot(_template(), 42)
    assert conn.cur.params == (42, 42, 28)
    assert "a.user_id = wp.user_id" in conn.cur.sql
    assert "wp.user_id = %s" in conn.cur.sql


def test_modelo_sem_slots_devolve_none(monkeypatch):
    _patch(monkeypatch, [])
    assert engine.resolve_upcoming_slot({"id": 1, "slots": []}, 1) is None


def test_resolve_current_cycle_usa_o_slot_mais_proximo(monkeypatch):
    template = _template()
    monkeypatch.setattr("app.analytics._resolve_latest_assigned_template_id", lambda uid: 28)
    monkeypatch.setattr(engine, "get_template_if_assigned", lambda tid, uid, **_kw: template)
    monkeypatch.setattr(engine, "resolve_upcoming_slot", lambda tpl, uid: tpl["slots"][4])
    monkeypatch.setattr(
        engine,
        "resolve_next_slot",
        lambda *a, **k: pytest.fail("o ciclo nao deve usar resolve_next_slot"),
    )
    conn = _Conn([{"template_slot_sequence": 5}, {"template_slot_sequence": 4}])
    monkeypatch.setattr("app.db.transaction", lambda: contextlib.nullcontext(conn))

    cycle = ads.resolve_current_cycle(1)

    assert cycle["next_sequence"] == 5
    assert cycle["done_sequences"] == {5, 4}

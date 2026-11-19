"""#211: a execucao marcada como aprimoramento de tecnica nas medias do
contexto de IA, o resumo do exercicio, a leitura da marca no job da serie e o
rotulo do caminho legado do volume. Tudo sem banco."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

from app import services, workout_ai_generation_core as core
from app.domain.form_practice import sql_counts_for_progression
from app.intelligence.progression_service import ProgressionService
from app.intelligence.volume_service import VolumeService
from tests.support.fake_db import fake_transaction
from tests.unit.test_exercise_performance import _row


def test_sessoes_recentes_da_ia_excluem_a_execucao_marcada():
    service = object.__new__(ProgressionService)
    vistos: list[str] = []
    service._days_ago = lambda user_id, days: date(2026, 11, 1)
    service.fetch_all = lambda sql, params=(): vistos.append(sql) or []

    service.get_recent_sessions(days=30, user_id=1)

    assert sql_counts_for_progression("e") in vistos[0]


class _SummaryCur:
    def __init__(self, marked):
        self.marked, self.sql = marked, []
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        if "FROM exercises e" in sql:
            self._rows = [{"id": 5, "original_name": "Supino", "training_goal": "hypertrophy", "form_practice": self.marked}]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def test_resumo_do_exercicio_informa_a_marca(monkeypatch):
    for stored, expected in ((1, True), (0, False), (None, False)):
        cur = _SummaryCur(stored)
        monkeypatch.setattr(services, "transaction", fake_transaction(cur))
        ctx = services.exercise_summary_context(5, user_id=1)
        assert ctx["form_practice"] is expected
        assert "e.form_practice" in cur.sql[0]


def test_falha_so_da_marca_no_job_mantem_o_par_e_loga_o_evento(monkeypatch, caplog):
    aberturas = []

    @contextmanager
    def tx():
        aberturas.append(1)
        yield object()

    contexto = core.PairEngineContext(step_kg=core.equipment_step_kg(None))
    monkeypatch.setattr(core, "transaction", tx)
    monkeypatch.setattr(core, "load_pair_context", lambda *a: contexto)

    def quebra(*a):
        raise RuntimeError("boom")

    monkeypatch.setattr(core, "read_exercise_form_practice", quebra)
    with caplog.at_level(logging.ERROR):
        out = core._evaluate_set_engine({"exercise_id": 7}, 1)

    assert out is contexto and out.form_practice_marked is False
    assert "evaluate_set.form_practice_failed" in caplog.text
    assert aberturas == [1]  # uma transacao so (par + marca)


def test_marca_e_par_vem_na_mesma_transacao(monkeypatch):
    aberturas = []

    @contextmanager
    def tx():
        aberturas.append(1)
        yield object()

    contexto = core.PairEngineContext(step_kg=core.equipment_step_kg(None))
    monkeypatch.setattr(core, "transaction", tx)
    monkeypatch.setattr(core, "load_pair_context", lambda *a: contexto)
    monkeypatch.setattr(core, "read_exercise_form_practice", lambda *a: True)

    assert core._evaluate_set_engine({"exercise_id": 7}, 1).form_practice_marked is True
    assert aberturas == [1]


def test_volume_legado_rotula_cada_linha_de_exercicio_pela_propria_marca():
    # exercise_id da consulta e `exercises.id` (uma linha por execucao): o mesmo
    # exercicio de catalogo feito duas vezes na sessao vira dois itens.
    marcada = {**_row(1, 10, 1, 10, 20.0, exercise_id=101), "form_practice": 1}
    normal = {**_row(2, 10, 1, 10, 60.0, exercise_id=102), "form_practice": 0}
    service = SimpleNamespace(_complete_history_item=lambda item: item)
    items = VolumeService._history_items_from_sets(service, [marcada, normal])

    assert {(i["exercise_id"], i["form_practice"]) for i in items} == {(101, 1), (102, 0)}

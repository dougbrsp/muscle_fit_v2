"""Lote 1 (varredura 2026-10-08): COD-04 (predicado e1RM no dono) e IA-16
(FULLBODY.md fora da raiz do RAG)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from app import analytics, rag_chunking as rc
from app.domain import e1rm
from app.intelligence.dashboard import exposure_service
from app.intelligence.dashboard.metrics_registry import THRESHOLDS
from tests.support.fake_db import CursorFactoryConn

REPO = Path(__file__).resolve().parents[3]


def _antigo_analytics(reps: Any) -> bool:
    """Corpo anterior de `analytics._e1rm_reps_reliable` (referencia)."""
    if reps is None:
        return False
    return int(reps) <= int(THRESHOLDS["e1rm_max_reliable_reps"])


def _antigo_exposure(exposure: dict) -> bool:
    """Corpo anterior de `exposure_service.e1rm_reliable` (referencia)."""
    reps = exposure.get("_e1rm_reps", exposure.get("e1rm_reps"))
    if exposure.get("e1rm") is None or reps is None:
        return False
    return int(reps) <= int(THRESHOLDS["e1rm_max_reliable_reps"])


@pytest.mark.parametrize("reps", [None, 1, 5, 12, 13, 20])
def test_predicado_analytics_identico_ao_anterior(reps):
    assert analytics._e1rm_reps_reliable(reps) is _antigo_analytics(reps)


@pytest.mark.parametrize("reps", [None, 1, 5, 12, 13, 20])
@pytest.mark.parametrize("e1rm_value", [None, 100.0])
@pytest.mark.parametrize("chave", ["_e1rm_reps", "e1rm_reps"])
def test_predicado_exposure_identico_ao_anterior(reps, e1rm_value, chave):
    exposure = {"e1rm": e1rm_value, chave: reps}
    assert exposure_service.e1rm_reliable(exposure) is _antigo_exposure(exposure)


def test_reps_zero_e_dado_invalido_agora_nao_confiavel():
    """Unica divergencia: 0 reps (dado invalido; nao gera e1RM) deixa de ser
    'confiavel' - o dono exige reps > 0."""
    assert _antigo_analytics(0) is True
    assert analytics._e1rm_reps_reliable(0) is False
    assert exposure_service.e1rm_reliable({"e1rm": 50.0, "e1rm_reps": 0}) is False
    assert e1rm.estimated_1rm(50, 0, 8) is None


def test_teto_e_o_do_dono():
    assert THRESHOLDS["e1rm_max_reliable_reps"] == e1rm.E1RM_MAX_RELIABLE_REPS == 12


@pytest.fixture
def rag_real(monkeypatch):
    monkeypatch.setattr(rc, "RAG_MARKDOWN_DIR", REPO / "api" / "privado" / "rag" / "markdown")


@pytest.mark.parametrize(
    "grupos,kwargs",
    [(["Peito"], {}), (["Costas", "Biceps"], {}), ([], {"rag_folder": "1. Full Body"})],
)
def test_select_rag_text_sem_aviso_nem_placeholder_de_fullbody(rag_real, caplog, grupos, kwargs):
    with caplog.at_level(logging.WARNING, logger="app.rag_chunking"):
        texto = rc.select_rag_text(grupos, **kwargs)
    assert not [r for r in caplog.records if "FULLBODY.md" in r.getMessage()]
    assert "## Arquivo: FULLBODY.md\n\n(arquivo indisponivel)" not in texto
    assert "FULLBODY.md" not in rc.KNOWN_FILES


class _FakeCursor:
    def __init__(self, log):
        self.log = log
        self._rows: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        if "FROM activities a" in sql:
            self._rows = [{"id": 7, "name": "Treino"}]
        elif "FROM exercises e" in sql:
            self._rows = [{"id": 10, "sequence": 1}, {"id": 11, "sequence": 2}, {"id": 12, "sequence": 3}]
        elif "FROM exercise_sets" in sql:
            self._rows = [
                {"exercise_id": 10, "set_number": 1, "set_type": "working"},
                {"exercise_id": 10, "set_number": 2, "set_type": "working"},
                {"exercise_id": 12, "set_number": 1, "set_type": "working"},
            ]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def test_activity_detail_busca_series_em_uma_query(monkeypatch):
    from contextlib import contextmanager

    from app import services

    log: list = []

    @contextmanager
    def fake_transaction():
        yield CursorFactoryConn(_FakeCursor, log)

    monkeypatch.setattr(services, "transaction", fake_transaction)
    detail = services.activity_detail(7, 1)

    assert len(log) == 3  # atividade + exercicios + UMA query de series (antes 2+N)
    sql_series, params = log[2]
    assert "exercise_id IN (%s,%s,%s)" in sql_series and "ORDER BY exercise_id, set_number" in sql_series
    assert params == (10, 11, 12)
    by_id = {ex["id"]: ex["sets"] for ex in detail["exercises"]}
    assert [s["set_number"] for s in by_id[10]] == [1, 2]
    assert by_id[11] == []  # exercicio sem series continua com lista vazia
    assert len(by_id[12]) == 1

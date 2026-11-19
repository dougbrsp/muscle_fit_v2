"""Reimportacao (PUT /activities/{id}) apaga e recria a atividade: a marca de
aprimoramento de tecnica e o objetivo dos exercicios que casam (sequence +
nome) tem de sobreviver; exercicio novo fica com os defaults."""

from __future__ import annotations

from decimal import Decimal

from app import services
from tests.support.fake_db import fake_transaction


class _Cur:
    def __init__(self, previous):
        self.previous = previous
        self.calls: list[tuple[str, object]] = []
        self.lastrowid = None
        self._rows: list = []
        self._row = None
        self._ids = {"activities": 500, "exercises": 700}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, seq):
        list(seq)

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, params))
        self._row, self._rows = None, []
        for table in self._ids:
            if flat.startswith(f"INSERT INTO {table} "):
                self._ids[table] += 1
                self.lastrowid = self._ids[table]
        if flat.startswith("SELECT id FROM imports"):
            self._row = {"id": 9}
        elif flat.startswith("SELECT id FROM activities"):
            self._row = {"id": 42}
        elif flat.startswith("SELECT sequence, original_name"):
            self._rows = self.previous

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def _run(monkeypatch, previous, exercises):
    cur = _Cur(previous)
    monkeypatch.setattr(services, "transaction", fake_transaction(cur))
    monkeypatch.setattr(services, "source_id", lambda conn, name, source_type="api": 1)
    monkeypatch.setattr(services, "resolve_gym_id", lambda cur, *a: None)
    monkeypatch.setattr(services, "resolve_mapping_id", lambda *a, **k: None)
    monkeypatch.setattr(services, "resolve_equipment_id", lambda *a, **k: None)
    services.upsert_activity(
        {"external_id": "x", "local_date": "2026-11-01", "name": "T", "exercises": exercises}, user_id=7
    )
    return [(s, p) for s, p in cur.calls if s.startswith("UPDATE exercises SET")]


def _ex(seq, name):
    return {
        "sequence": seq,
        "original_name": name,
        "sets": [{"set_number": 1, "reps": 10, "weight_kg": Decimal("50"), "set_type": "working"}],
    }


def test_marca_e_objetivo_voltam_para_o_exercicio_que_casa(monkeypatch):
    previous = [
        {"sequence": 1, "original_name": "Supino", "form_practice": 1, "training_goal": "strength"},
        {"sequence": 2, "original_name": "Remada", "form_practice": 0, "training_goal": "hypertrophy"},
    ]
    updates = _run(monkeypatch, previous, [_ex(1, "Supino"), _ex(2, "Remada"), _ex(3, "Novo")])

    # id da atividade muda (42 -> 501) e nao importa: a chave nao usa ids.
    assert [p for _, p in updates] == [(True, "strength", 701), (False, "hypertrophy", 702)]
    assert all("form_practice" in s and "training_goal" in s for s, _ in updates)


def test_exercicio_sem_par_fica_com_os_defaults(monkeypatch):
    previous = [{"sequence": 1, "original_name": "Supino", "form_practice": 1, "training_goal": None}]
    assert _run(monkeypatch, previous, [_ex(1, "Outro"), _ex(2, "Supino")]) == []

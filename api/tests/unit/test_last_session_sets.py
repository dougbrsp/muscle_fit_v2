"""`GET /exercises/last-session-sets` com a medida da série (Lote T3 das
medidas de série): cada série traz `measure` (ausente = reps),
`duration_seconds` e `measure_text` pronto do dono
(`set_measures.format_set_measure`); o e1RM "nesse equipamento" só olha
séries de repetições.

Rodar: cd api && python -m pytest tests/unit/test_last_session_sets.py
"""

from __future__ import annotations

from datetime import date

from app import services
from app.domain.set_measures import sql_is_rep_based
from tests.support.fake_db import fake_transaction


class _Cursor:
    def __init__(self, sets):
        self.sets = sets
        self.sql: list[str] = []
        self._one = None
        self._all: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sql.append(flat)
        self._one, self._all = None, []
        if "FROM exercise_legacy_id_map" in flat and flat.startswith("SELECT canonical_exercise_id"):
            self._one = None
        elif "information_schema" in flat.lower():
            self._one = {"n": 1}
        elif "AS global_equipment_id" in flat:
            self._one = {"exercise_id": 5, "activity_id": 9, "local_date": date(2026, 10, 18),
                         "global_equipment_id": None, "load_convention": None}
        elif flat.startswith("SELECT id, set_number, set_type, reps"):
            self._all = [dict(s) for s in self.sets]

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


def test_series_trazem_medida_e_texto_do_dono(monkeypatch):
    cur = _Cursor(
        [
            {"id": 1, "set_number": 1, "set_type": "working", "reps": None, "weight_kg": None, "rpe": 8,
             "technique_code": None, "anchor_set_number": None, "measure": "duration", "duration_seconds": 58},
            {"id": 2, "set_number": 2, "set_type": "working", "reps": 15, "weight_kg": None, "rpe": 9,
             "technique_code": None, "anchor_set_number": None, "measure": "max_reps", "duration_seconds": 30},
            {"id": 3, "set_number": 3, "set_type": "working", "reps": 10, "weight_kg": 40, "rpe": 8,
             "technique_code": None, "anchor_set_number": None, "measure": None, "duration_seconds": None},
        ]
    )
    monkeypatch.setattr(services, "transaction", fake_transaction(cur))
    item = services.get_last_session_sets(11, exclude_activity_id=99)
    assert [(s["measure"], s["duration_seconds"], s["measure_text"]) for s in item["sets"]] == [
        ("duration", 58, "58 s"),
        ("max_reps", 30, "15 reps em 30 s"),
        ("reps", None, "10 reps"),
    ]
    leitura = next(sql for sql in cur.sql if sql.startswith("SELECT id, set_number, set_type, reps"))
    assert "measure, duration_seconds" in leitura
    e1rm = next(sql for sql in cur.sql if sql.startswith("SELECT es.weight_kg, es.reps, es.rpe"))
    assert sql_is_rep_based("es.measure") in e1rm


def test_detalhe_da_atividade_ganha_o_texto_da_medida():
    linha = services._attach_set_measure({"reps": 12, "duration_seconds": None, "measure": "reps"})
    assert (linha["measure"], linha["measure_text"]) == ("reps", "12 reps")
    assert services._attach_set_measure({"reps": None, "duration_seconds": 60, "measure": "duration"})[
        "measure_text"
    ] == "60 s"

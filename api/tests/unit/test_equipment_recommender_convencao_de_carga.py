"""#244: `/gym-equipment/recommend` e `/recommend-all` devolvem `load_convention` por item,
com o valor do dono (`load_conventions.normalize_load_convention`), o mesmo de GET /gym-equipment."""

from __future__ import annotations

from app import equipment_recommender as rec
from app import load_conventions as lc


class _Cur:
    def __init__(self):
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return []


def test_candidatos_selecionam_a_convencao_pelo_join_do_dono(monkeypatch):
    monkeypatch.setattr(rec, "load_convention_select_expr", lambda cursor: "eqex.load_convention")
    cur = _Cur()
    rec._load_candidates(cur, 3, "Peito")
    sql = " ".join(cur.sql.split())
    assert "eqex.load_convention AS load_convention" in sql
    assert lc.equipment_exercises_join_sql(canonical_expr="COALESCE(elm.canonical_exercise_id, ge.mapping_id)").split()[2] in sql
    assert "eqex.id" in sql.split("GROUP BY")[1]


def test_item_leva_o_valor_normalizado_e_null_vale_o_padrao(monkeypatch):
    monkeypatch.setattr(rec.ts, "resolve_training_safety_bulk", lambda conn, pairs: {p: rec.ts.TrainingSafety.__new__(rec.ts.TrainingSafety) for p in pairs})
    monkeypatch.setattr(rec.ranking, "safety_fields", lambda safety: {})
    monkeypatch.setattr(rec, "load_user_recency", lambda *a, **k: {})
    monkeypatch.setattr(rec, "load_user_stars", lambda *a, **k: {})
    rows = [
        {"id": 1, "mapping_id": 10, "equipment_id": 5, "equipment_name": "A", "load_convention": "per_side"},
        {"id": 2, "mapping_id": 11, "equipment_id": 6, "equipment_name": "B", "load_convention": None},
    ]
    ranked, _ = rec._enrich_and_rank(object(), object(), rows, 3, 1)
    by_id = {r["id"]: r["load_convention"] for r in ranked}
    assert by_id[1] == lc.normalize_load_convention("per_side").value
    assert by_id[2] == lc.DEFAULT_LOAD_CONVENTION.value

"""Carregador em lote de candidatos de redundância (issue #288, fatia 3)."""

from app.intelligence import redundancy_candidates as rc

CATALOG = {
    1: {"id": 1, "name": "Supino", "biomechanical_pattern": "push", "primary_joint": "elbow", "movement": "m", "body_position": "seated",
        "trajectory": "t", "is_unilateral": 0},
    2: {"id": 2, "name": "Remada", "biomechanical_pattern": "pull", "primary_joint": "shoulder", "movement": None, "body_position": None,
        "trajectory": None, "is_unilateral": None},
}
PROFILE_ROW = {"equipment_id": 5, "exercise_id": 1, "flags_json": '["a"]', "source": "cable", "mechanism": "m",
               "trajectory": "free", "profile": "p", "line": "l", "direction_changes": 0, "confidence": "high",
               "status": "reviewed", "origin": "admin"}


class FakeCursor:
    def __init__(self, table_exists=True):
        self.table_exists = table_exists
        self.sql: list[str] = []
        self._rows: list = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        if "information_schema" in sql:
            self._rows = [{"n": 1 if self.table_exists else 0}]
        elif "FROM exercise_catalog" in sql:
            self._rows = [CATALOG[i] for i in params if i in CATALOG]
        else:
            self._rows = [PROFILE_ROW]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def _patch(monkeypatch):
    calls = []

    def fake(cur, ids):
        calls.append(set(ids))
        return {1: [{"muscle_group_id": 10, "role": "PRIMARY"}, {"muscle_group_id": 11, "role": "SECONDARY"},
                    {"muscle_group_id": None, "role": "PRIMARY"}, {"muscle_group_id": 11, "role": "PRIMARY"}]}

    monkeypatch.setattr(rc, "resolve_muscle_contributions_with_cursor", fake)
    return calls


def test_batch_one_query_per_kind(monkeypatch):
    calls = _patch(monkeypatch)
    cur = FakeCursor()
    out = rc.load_candidates(cur, [(1, 5), (1, None), (2, None), (1, 5)])
    assert len(calls) == 1 and calls[0] == {1, 2}
    assert len([s for s in cur.sql if "FROM exercise_catalog" in s]) == 1
    assert len([s for s in cur.sql if "equipment_exercises" in s]) == 1
    cand = out[(1, 5)]
    assert cand.contributions == {10: "primary", 11: "primary"}  # minúsculas, grupo nulo fora, primário vence
    assert cand.unilateral is False and cand.resistance["flags"] == ["a"] and cand.resistance["status"] == "reviewed"
    assert out[(1, None)].resistance is None and out[(1, None)].equipment_id is None
    assert out[(2, None)].unilateral is None and out[(2, None)].contributions == {}


def test_missing_table_gives_none_profiles(monkeypatch):
    _patch(monkeypatch)
    cur = FakeCursor(table_exists=False)
    out = rc.load_candidates(cur, [(1, 5)])
    assert out[(1, 5)].resistance is None
    assert not any("equipment_exercises" in s for s in cur.sql)


def test_unknown_exercise_and_empty(monkeypatch):
    _patch(monkeypatch)
    assert rc.load_candidates(FakeCursor(), [(99, None)]) == {}
    cur = FakeCursor()
    assert rc.load_candidates(cur, []) == {} and cur.sql == []


def test_profile_batches_and_names(monkeypatch):
    _patch(monkeypatch)
    cur = FakeCursor()
    pairs = [(1, 100 + i) for i in range(250)]
    out, names = rc.load_candidates_with_names(cur, pairs)
    assert len([s for s in cur.sql if "equipment_exercises" in s]) == 3  # ceil(250/100)
    assert len(out) == 250 and names == {1: "Supino"}
    assert out[(1, 100)].resistance is None and rc.load_candidates(FakeCursor(), pairs[:1])[(1, 100)]

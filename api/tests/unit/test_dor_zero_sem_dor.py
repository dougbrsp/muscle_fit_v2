"""Dor zero conta como SEM dor (19/11/2026): so dor > 0 das series confirmadas
consolida em `exercises`; senao vale o campo do exercicio. Prova a funcao dona
(`workout_sessions.rollup_exercise_pain`) contra a fixture de paridade do front,
executando o SQL real em SQLite em memoria (NULLIF/COALESCE sao portaveis)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.workout_sessions import rollup_exercise_pain

FIXTURE = Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "pain-rollup.parity.json"
CASOS = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


class _SqliteCur:
    """Adapta o paramstyle %s do pymysql/Data API para o `?` do SQLite."""

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()

    def execute(self, sql, params=None):
        self.cur.execute(sql.replace("%s", "?"), params or ())

    def fetchone(self):
        row = self.cur.fetchone()
        return dict(row) if row is not None else None


def _roda(caso, **kwargs):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE exercises (id INTEGER PRIMARY KEY, muscle_pain INTEGER, joint_pain INTEGER)")
    conn.execute(
        "CREATE TABLE exercise_sets (exercise_id INTEGER, muscle_pain INTEGER, joint_pain INTEGER, reps_pending INTEGER DEFAULT 0)"
    )
    ex = caso["exercise"]
    conn.execute("INSERT INTO exercises VALUES (1, ?, ?)", (ex["muscle_pain"], ex["joint_pain"]))
    for s in caso["sets"]:
        joint = None if s["joint_pain"] is None else int(s["joint_pain"])
        conn.execute("INSERT INTO exercise_sets VALUES (1, ?, ?, ?)", (s["muscle_pain"], joint, int(s["reps_pending"])))
    rollup_exercise_pain(_SqliteCur(conn), 1, **kwargs)
    return conn.execute("SELECT muscle_pain, joint_pain FROM exercises WHERE id = 1").fetchone()


@pytest.mark.parametrize("caso", CASOS, ids=[c["name"] for c in CASOS])
def test_rollup_da_fixture_de_paridade(caso):
    row = _roda(caso)
    esperado = caso["expected"]
    assert (row["muscle_pain"], row["joint_pain"]) == (esperado["muscle_pain"], esperado["joint_pain"])
    # derived_*: o valor vem das series quando alguma serie confirmada tem dor > 0
    confirmadas = [s for s in caso["sets"] if not s["reps_pending"]]
    assert esperado["derived_muscle"] == any((s["muscle_pain"] or 0) > 0 for s in confirmadas)
    assert esperado["derived_joint"] == any(int(s["joint_pain"] or 0) > 0 for s in confirmadas)


def test_sem_preservar_so_zero_limpa_o_exercicio():
    caso = next(c for c in CASOS if c["name"] == "series_so_com_zero_vale_o_exercicio")
    row = _roda(caso, keep_stored_when_empty=False)
    assert (row["muscle_pain"], row["joint_pain"]) == (None, None)


def _modo_treino(series, dor_antiga):
    """`_refresh_exercise_aggregates` (Modo Treino) sobre SQLite."""
    from app.workout_sessions import _refresh_exercise_aggregates

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE exercises (id INTEGER PRIMARY KEY, sets_count, reps_total, volume_kg, min_weight_kg,"
        " best_weight_kg, avg_rpe, muscle_pain INTEGER, joint_pain INTEGER)"
    )
    conn.execute(
        "CREATE TABLE exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe, measure,"
        " muscle_pain INTEGER, joint_pain INTEGER, reps_pending INTEGER DEFAULT 0)"
    )
    conn.execute("INSERT INTO exercises (id, muscle_pain) VALUES (1, ?)", (dor_antiga,))
    for n, dor in enumerate(series, 1):
        conn.execute("INSERT INTO exercise_sets VALUES (1, ?, 'working', 10, 50, 8, NULL, ?, NULL, 0)", (n, dor))

    class _Cur(_SqliteCur):
        def execute(self, sql, params=None):
            super().execute(sql, tuple(float(p) if hasattr(p, "as_tuple") else p for p in params or ()))

        def fetchall(self):
            return [dict(r) for r in self.cur.fetchall()]

    _refresh_exercise_aggregates(_Cur(conn), 1)
    return conn.execute("SELECT muscle_pain FROM exercises WHERE id = 1").fetchone()["muscle_pain"]


def test_modo_treino_serie_corrigida_para_zero_limpa_a_dor_do_exercicio():
    assert _modo_treino([0], dor_antiga=3) is None


def test_modo_treino_dor_3_e_zero_consolida_3():
    assert _modo_treino([3, 0], dor_antiga=None) == 3

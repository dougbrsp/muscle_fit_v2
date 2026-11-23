"""Banco em memoria de `app.resistance_profiles`: despacho pela constante SQL do modulo (SQL novo sem o fake
acompanhar falha alto). Simula o UNIQUE do par, o LEFT JOIN dos perfis, `lastrowid` e o rollback da conexao quando
a rota levanta. `statements` conta cada `execute` (uma chamada a Data API cada)."""

from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import Any

from app import resistance_profiles as rp_mod

AGORA = datetime(2026, 11, 23, 12, 0, 0)
_PROFILE_KEYS = ("source", "mechanism", "trajectory", "profile", "line", "direction_changes", "flags_json",
                 "confidence", "status", "origin", "rationale", "evidence", "vocabulary_version")


class FakeProfilesDB:
    def __init__(self, tables: bool = True) -> None:
        self.tables = tables
        self.equipment = {1: "Leg press", 2: "Polia"}
        self.exercises = {10: "Agachamento", 11: "Crucifixo"}
        self.pairs: dict[int, dict[str, Any]] = {}
        self.profiles: dict[int, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self.next_id = 1
        self.statements = 0
        self.inject_duplicate_on_insert = False
        self.inject_deadlock_on_insert = False

    def add_pair(self, pid: int, equipment_id: int, exercise_id: int, active: bool = True) -> int:
        self.pairs[pid] = {"id": pid, "equipment_id": equipment_id, "exercise_id": exercise_id, "is_active": active}
        return pid

    def add_profile(self, pair_id: int, **campos: Any) -> int:
        row = {"source": "unknown", "mechanism": "unknown", "trajectory": "unknown", "profile": "unknown",
               "line": "unknown", "direction_changes": "unknown", "flags_json": "[]", "confidence": "low",
               "status": "suggested", "origin": "ai", "rationale": None, "evidence": None,
               "vocabulary_version": "resistance-v1", "model_version": None, "reviewed_by": None,
               "reviewed_at": None, "updated_at": AGORA, **campos}
        row.update(id=self.next_id, equipment_exercise_id=pair_id)
        self.next_id += 1
        self.profiles[row["id"]] = row
        return row["id"]

    def conn(self) -> "FakeConn":
        return FakeConn(self)


class FakeConn:
    def __init__(self, db: FakeProfilesDB) -> None:
        self.db = db

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self.db)

    def __enter__(self) -> "FakeConn":
        self._snap = copy.deepcopy((self.db.profiles, self.db.history, self.db.next_id))
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.db.profiles, self.db.history, self.db.next_id = self._snap
        return False


class FakeCursor:
    def __init__(self, db: FakeProfilesDB) -> None:
        self.db = db
        self.rows: list[dict[str, Any]] = []
        self.lastrowid: int | None = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.rows)

    def _profile_of(self, pair_id: int) -> dict[str, Any] | None:
        return next((p for p in self.db.profiles.values() if p["equipment_exercise_id"] == pair_id), None)

    def _joined(self, pair: dict[str, Any]) -> dict[str, Any]:
        base = {"equipment_exercise_id": pair["id"], "equipment_id": pair["equipment_id"],
                "equipment_name": self.db.equipment[pair["equipment_id"]], "exercise_id": pair["exercise_id"],
                "exercise_name": self.db.exercises[pair["exercise_id"]]}
        p = self._profile_of(pair["id"])
        cols = ("profile_id", "source", "mechanism", "trajectory", "profile", "line", "direction_changes",
                "flags_json", "confidence", "status", "origin", "rationale", "evidence", "vocabulary_version",
                "model_version", "reviewed_by", "reviewed_at", "updated_at")
        if p is None:
            return {**base, **{c: None for c in cols}}
        return {**base, **{c: p.get(c) for c in cols if c != "profile_id"}, "profile_id": p["id"]}

    def _filtered(self, filtros: str, params: tuple) -> list[dict[str, Any]]:
        clausulas = [c.strip() for c in re.split(r" AND (?=p\.|eqex\.|\()", filtros) if c.strip()]
        linhas = [self._joined(p) for p in self.db.pairs.values() if p["is_active"]]
        it = iter(params)
        for c in clausulas:
            if c.startswith("(p.id IS NULL"):
                vs = [next(it) for _ in range(c.count("%s"))]
                linhas = [r for r in linhas if r["profile_id"] is None or r["status"] in vs]
            else:
                col, v = c.split(" = ")[0], next(it)
                chave = {"p.status": "status", "p.confidence": "confidence", "eqex.equipment_id": "equipment_id",
                         "eqex.exercise_id": "exercise_id"}[col]
                linhas = [r for r in linhas if r[chave] == v]
        return sorted(linhas, key=lambda r: (r["equipment_name"], r["exercise_name"], r["equipment_exercise_id"]))

    def execute(self, sql: str, params: Any = None) -> None:  # noqa: C901 - despacho por statement
        db = self.db
        db.statements += 1
        p = tuple(params or ())
        self.rows = []
        if sql == rp_mod.TABLES_SQL:
            self.rows = [{"n": 2 if db.tables else 0}]
        elif sql == rp_mod.PAIR_SQL:
            pair = db.pairs.get(int(p[0]))
            self.rows = [self._joined(pair)] if pair and pair["is_active"] else []
        elif sql.startswith("SELECT COUNT(*) AS total FROM equipment_exercises"):
            filtros = sql.split("WHERE eqex.is_active = 1", 1)[1]
            self.rows = [{"total": len(self._filtered(filtros, p))}]
        elif sql.startswith(rp_mod._PAIR_SELECT) and "ORDER BY" in sql:
            filtros = sql.split("WHERE eqex.is_active = 1", 1)[1].split(" ORDER BY")[0]
            limite, deslocamento = p[-2], p[-1]
            self.rows = self._filtered(filtros, p[:-2])[deslocamento: deslocamento + limite]
        elif sql == rp_mod.COVERAGE_SQL:
            cont: dict[tuple, int] = {}
            for pair in db.pairs.values():
                if pair["is_active"]:
                    j = self._joined(pair)
                    chave = tuple(j[c] for c in ("status", "source", "mechanism", "trajectory", "profile", "line",
                                                 "direction_changes"))
                    cont[chave] = cont.get(chave, 0) + 1
            nomes = ("status", "source", "mechanism", "trajectory", "profile", "line", "direction_changes")
            self.rows = [{**dict(zip(nomes, k)), "n": n} for k, n in cont.items()]
        elif sql == rp_mod.PROFILE_FOR_UPDATE_SQL:
            atual = self._profile_of(int(p[0]))
            self.rows = [dict(atual)] if atual else []  # copia: o banco real nao devolve a linha viva
        elif sql == rp_mod.PROFILE_ID_SQL:
            atual = self._profile_of(int(p[0]))
            self.rows = [{"id": atual["id"]}] if atual else []
        elif sql.startswith(f"INSERT INTO {rp_mod.PROFILES}"):
            if db.inject_deadlock_on_insert:
                raise RuntimeError("(1213, 'Deadlock found when trying to get lock; try restarting transaction')")
            if db.inject_duplicate_on_insert or self._profile_of(int(p[0])):
                raise _Duplicate()
            row = {"equipment_exercise_id": p[0], **dict(zip(_PROFILE_KEYS, p[1:14])), "model_version": None,
                   "created_by": p[14], "reviewed_by": p[15], "updated_at": AGORA,
                   "reviewed_at": AGORA if sql.rstrip(")").endswith("CURRENT_TIMESTAMP") else None}
            row["id"] = self.lastrowid = db.next_id
            db.next_id += 1
            db.profiles[row["id"]] = row
        elif sql.startswith(f"UPDATE {rp_mod.PROFILES}"):
            row = db.profiles[int(p[-1])]
            row.update(dict(zip(_PROFILE_KEYS, p[:13])), reviewed_by=p[13], updated_at=AGORA,
                       model_version=None if "model_version = NULL" in sql else row.get("model_version"),
                       reviewed_at=AGORA if "reviewed_at = CURRENT_TIMESTAMP" in sql else None)
        elif sql == rp_mod.HISTORY_INSERT_SQL:
            db.history.append({"id": len(db.history) + 1, "profile_id": p[0], "before_json": p[1], "after_json": p[2],
                               "actor_user_id": p[3], "origin": p[4], "reason": p[5], "created_at": AGORA})
        elif sql == rp_mod.HISTORY_LIST_SQL:
            linhas = sorted((h for h in db.history if h["profile_id"] == p[0]), key=lambda h: -h["id"])
            self.rows = linhas[p[2]: p[2] + p[1]]
        else:
            raise AssertionError(f"SQL sem tratamento no fake: {sql[:120]}")


class _Duplicate(Exception):
    """Reconhecida por `is_duplicate_key_error` (ver o teste que ajusta o monkeypatch)."""

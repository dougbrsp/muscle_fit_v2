"""Tabela `next_set_load_rule_sets` (migração 088) em memória para os testes do
loader e das rotas das regras de carga (R2). Reconhece SÓ os statements de
`app/next_set_load_rules.py` (pela identidade da string) e reproduz o que o
MySQL garante: as colunas geradas `current_published`/`current_draft` com
UNIQUE (erro "Duplicate entry", o 1062 que `is_duplicate_key_error` reconhece),
o UNIQUE de `version_number`, `rowcount` = linhas encontradas e o JSON
devolvido como texto (como a Data API e o PyMySQL)."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Optional

from app import next_set_load_rules as rules
from app.domain import next_set_load as nsl


class DuplicateEntry(Exception):
    pass


class FakeRuleSetsDB:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self._clock = datetime(2026, 10, 20, 12, 0, 0)
        # Gancho de corrida: chamado antes do UPDATE de publicação.
        self.before_publish_update = None

    # -- helpers -------------------------------------------------------------
    def now(self) -> datetime:
        self._clock += timedelta(seconds=1)
        return self._clock

    def add(self, *, status: str, version_number: Optional[int], ruleset_dict: dict, superseded: bool = False,
            notes: Optional[str] = None, rules_json: Any = None) -> int:
        row_id = len(self.rows) + 1
        published = status == rules.STATUS_PUBLISHED
        self.rows.append({
            "id": row_id,
            "version_number": version_number,
            "status": status,
            "reps_floor": ruleset_dict["reps_floor"],
            "reps_ceiling": ruleset_dict["reps_ceiling"],
            "rules_json": rules_json if rules_json is not None else json.dumps(ruleset_dict["rules"]),
            "notes": notes,
            "engine_version": nsl.RULESET_ENGINE_VERSION,
            "created_by": 1,
            "published_by": 1 if published else None,
            "published_at": self.now() if published else None,
            "superseded_at": self.now() if superseded else None,
            "created_at": self.now(),
            "updated_at": self.now(),
        })
        self._check_unique()
        return row_id

    def by_id(self, row_id: int) -> Optional[dict[str, Any]]:
        return next((row for row in self.rows if row["id"] == row_id), None)

    @staticmethod
    def _current_published(row) -> Optional[int]:
        return 1 if row["status"] == rules.STATUS_PUBLISHED and row["superseded_at"] is None else None

    @staticmethod
    def _current_draft(row) -> Optional[int]:
        return 1 if row["status"] == rules.STATUS_DRAFT else None

    def _check_unique(self) -> None:
        for key in (self._current_published, self._current_draft, lambda row: row["version_number"]):
            values = [key(row) for row in self.rows if key(row) is not None]
            if len(values) != len(set(values)):
                raise DuplicateEntry("(1062, \"Duplicate entry '1' for key 'next_set_load_rule_sets.uq_nslrs'\")")

    def _out(self, row) -> dict[str, Any]:
        return copy.deepcopy(row)

    # -- conexão -------------------------------------------------------------
    def conn(self) -> "FakeConn":
        return FakeConn(self)

    def transaction(self):
        db = self

        @contextmanager
        def _tx():
            snapshot = copy.deepcopy(db.rows)
            conn = db.conn()
            try:
                yield conn
                db.commits += 1
            except Exception:
                db.rows = snapshot
                db.rollbacks += 1
                raise

        return _tx

    def fetch_rows(self, sql, params=None):
        cur = FakeCursor(self)
        cur.execute(sql, params)
        return cur.fetchall()


class FakeConn:
    def __init__(self, db: FakeRuleSetsDB) -> None:
        self.db = db

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self.db)


class FakeCursor:
    def __init__(self, db: FakeRuleSetsDB) -> None:
        self.db = db
        self._rows: list[dict[str, Any]] = []
        self.rowcount = -1
        self.lastrowid: Optional[int] = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def _select(self, rows) -> None:
        self._rows = [self.db._out(row) for row in rows]
        self.rowcount = len(self._rows)

    def execute(self, sql, params=None):
        db = self.db
        db.statements.append(sql)
        params = tuple(params or ())
        if sql == rules._PUBLISHED_SQL or sql == rules._CURRENT_PUBLISHED_FOR_UPDATE_SQL:
            return self._select([row for row in db.rows if db._current_published(row)])
        if sql == rules._DRAFT_SQL or sql == rules._CURRENT_DRAFT_FOR_UPDATE_SQL:
            return self._select([row for row in db.rows if db._current_draft(row)])
        if sql == rules._BY_ID_SQL:
            return self._select([row for row in db.rows if row["id"] == params[0]])
        if sql == rules._DRAFT_BY_ID_FOR_UPDATE_SQL:
            return self._select([row for row in db.rows if row["id"] == params[0] and row["status"] == params[1]])
        if sql == rules._VERSIONS_SQL:
            ordered = sorted(db.rows, key=lambda r: (r["version_number"] is not None, -(r["version_number"] or 0), -r["id"]))
            return self._select(ordered)
        if sql == rules._NEXT_VERSION_SQL:
            numbers = [row["version_number"] for row in db.rows if row["version_number"] is not None]
            self._rows = [{"next_version": (max(numbers) if numbers else 0) + 1}]
            return None
        if sql == rules._INSERT_DRAFT_SQL:
            status, floor, ceiling, rules_json, notes, engine, created_by = params
            row_id = max((row["id"] for row in db.rows), default=0) + 1
            db.rows.append({
                "id": row_id, "version_number": None, "status": status, "reps_floor": floor,
                "reps_ceiling": ceiling, "rules_json": json.dumps(rules_json), "notes": notes,
                "engine_version": engine, "created_by": created_by, "published_by": None, "published_at": None,
                "superseded_at": None, "created_at": db.now(), "updated_at": db.now(),
            })
            try:
                db._check_unique()
            except DuplicateEntry:
                db.rows.pop()
                raise
            self.lastrowid, self.rowcount = row_id, 1
            return None
        if sql == rules._UPDATE_DRAFT_SQL:
            floor, ceiling, rules_json, notes, engine, row_id, status = params
            found = [row for row in db.rows if row["id"] == row_id and row["status"] == status]
            for row in found:
                row.update(reps_floor=floor, reps_ceiling=ceiling, rules_json=json.dumps(rules_json), notes=notes,
                           engine_version=engine, updated_at=db.now())
            self.rowcount = len(found)
            return None
        if sql == rules._SUPERSEDE_SQL:
            found = [row for row in db.rows if db._current_published(row) and row["id"] != params[0]]
            for row in found:
                row["superseded_at"] = db.now()
            self.rowcount = len(found)
            return None
        if sql == rules._PUBLISH_SQL:
            if db.before_publish_update is not None:
                db.before_publish_update(db)
            status, version, user_id, engine, row_id, expected_status = params
            found = [row for row in db.rows if row["id"] == row_id and row["status"] == expected_status]
            before = copy.deepcopy(db.rows)
            for row in found:
                row.update(status=status, version_number=version, published_by=user_id, engine_version=engine,
                           published_at=db.now())
            try:
                db._check_unique()
            except DuplicateEntry:
                db.rows = before
                raise
            self.rowcount = len(found)
            return None
        raise AssertionError(f"SQL inesperado: {sql}")

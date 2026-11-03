"""Banco em memória do check-in semanal (N7): o fake do dashboard mais os statements próprios de
`app.nutrition_checkins` (despacho pela constante SQL do módulo; SQL novo sem o fake acompanhar falha alto).

Simula o que importa do MySQL para estas rotas: o UNIQUE `(user_id, week_start)` com `ON DUPLICATE KEY UPDATE id = id`
(a primeira inserção fica), `FOR UPDATE` (registrado em `locks`) e o rollback da conexão quando a rota levanta."""

from __future__ import annotations

import copy
import json
from typing import Any

from app import nutrition_checkins as ck
from tests.support.nutrition_dashboard_fake import FakeDashboardConn, FakeDashboardCursor, FakeDashboardDB


class FakeCheckinDB(FakeDashboardDB):
    def __init__(self) -> None:
        super().__init__()
        self.checkins: dict[int, dict[str, Any]] = {}
        self.checkin_settings: dict[int, int] = {}
        self.next_checkin_id = 1
        self.checkin_inserts = 0

    def conn(self) -> "FakeCheckinConn":
        return FakeCheckinConn(self)


class FakeCheckinConn(FakeDashboardConn):
    def cursor(self) -> "FakeCheckinCursor":
        return FakeCheckinCursor(self.db)

    def __enter__(self) -> "FakeCheckinConn":
        super().__enter__()
        self._checkins = copy.deepcopy(self.db.checkins)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.db.checkins = self._checkins
        return super().__exit__(exc_type, exc, tb)


class FakeCheckinCursor(FakeDashboardCursor):
    def execute(self, sql: str, params: Any = None) -> None:  # noqa: C901 - despacho por statement
        db: FakeCheckinDB = self.db  # type: ignore[assignment]
        p = tuple(params or ())
        if sql == ck._BY_WEEK_SQL:
            return self._result([r for r in db.checkins.values() if r["user_id"] == p[0] and r["week_start"] == p[1]])
        if sql in (ck._BY_ID_SQL, ck._BY_ID_FOR_UPDATE_SQL):
            if sql == ck._BY_ID_FOR_UPDATE_SQL:
                db.locks.append(("checkin", p[0]))
            linha = db.checkins.get(int(p[0]))
            return self._result([linha] if linha else [])
        if sql == ck._HISTORY_SQL:
            linhas = sorted((r for r in db.checkins.values() if r["user_id"] == p[0]), key=lambda r: (r["week_start"], r["id"]), reverse=True)
            return self._result(linhas[: int(p[1])])
        if sql == ck._INSERT_ONCE_SQL:
            ja_existe = any(r["user_id"] == p[0] and r["week_start"] == p[1] for r in db.checkins.values())
            if not ja_existe:
                colunas = ("user_id", "week_start", "due_date", "status", "verdict", "inputs_json", "result_json",
                           "adjustment_kcal", "proposed_changes_json", "proposed_targets_json", "expires_at",
                           "engine_version", "source_plan_id")
                linha = dict(zip(colunas, p))
                for chave in ("inputs_json", "result_json", "proposed_changes_json", "proposed_targets_json"):
                    if linha[chave] is not None:
                        json.loads(linha[chave])  # o banco recusaria JSON inválido
                linha.update(id=db.next_checkin_id, generated_plan_id=None, responded_at=None, created_at=db.now)
                db.checkins[db.next_checkin_id] = linha
                db.next_checkin_id += 1
                db.checkin_inserts += 1
                db.writes.append(f"insert checkin {linha['id']}")
            self.rowcount = 0 if ja_existe else 1
            return self._result([])
        if sql in (ck._EXPIRE_SQL, ck._DECLINE_SQL):
            novo, quando, checkin_id, esperado = p
            linha = db.checkins.get(int(checkin_id))
            self.rowcount = 0
            if linha and linha["status"] == esperado:
                linha.update(status=novo, responded_at=quando)
                self.rowcount = 1
            return self._result([])
        if sql == ck._ACCEPT_SQL:
            novo, plano_id, quando, checkin_id, esperado = p
            linha = db.checkins.get(int(checkin_id))
            self.rowcount = 0
            if linha and linha["status"] == esperado:
                linha.update(status=novo, generated_plan_id=plano_id, responded_at=quando)
                self.rowcount = 1
            return self._result([])
        if sql == ck._SETTINGS_SQL:
            dia = db.checkin_settings.get(int(p[0]))
            return self._result([] if dia is None else [{"weekday": dia}])
        if sql == ck._UPSERT_SETTINGS_SQL:
            db.checkin_settings[int(p[0])] = int(p[1])
            return self._result([])
        return super().execute(sql, params)

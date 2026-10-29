"""Banco em memória para os testes do plano nutricional (L2b).

Responde EXATAMENTE os statements de `app.nutrition_plans`,
`app.nutrition_targets_service` e `app.nutrition_access` (despacho pela
constante SQL do módulo, não por regex): um SQL novo ou alterado sem o fake
acompanhar falha alto. Simula o que importa do MySQL para estas rotas:
`lastrowid`, `rowcount`, o UNIQUE da vigente (`current_user_id`, 078) com a
mensagem "Duplicate entry" e o rollback da conexão quando a rota levanta.
"""

from __future__ import annotations

import copy
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from app import nutrition_access as access
from app import nutrition_plans as plans_mod
from app import nutrition_targets_service as svc

TARGET_COLUMNS = svc.UPSERT_COLUMNS


class FakeNutritionDB:
    def __init__(self) -> None:
        self.users: dict[int, dict[str, Any]] = {
            1: {"id": 1, "username": "admin", "full_name": "Admin"},
            2: {"id": 2, "username": "aluno", "full_name": "Aluno Teste"},
            3: {"id": 3, "username": "sem_dados", "full_name": None},
        }
        self.profiles: dict[int, dict[str, Any]] = {
            2: {"sex": "M", "birth_date": date(1990, 1, 1), "height_cm": Decimal("180.0"), "activity_level": "moderate"},
            3: {"sex": None, "birth_date": None, "height_cm": None, "activity_level": None},
        }
        self.plans: dict[int, dict[str, Any]] = {}
        self.targets: dict[tuple[int, date], dict[str, Any]] = {}
        self.composition: list[dict[str, Any]] = []
        self.measurements: list[dict[str, Any]] = []
        self.activities: list[dict[str, Any]] = []
        self.now = datetime(2026, 10, 17, 15, 0, 0)
        self.next_plan_id = 1
        self.writes: list[str] = []
        self.batches: list[int] = []
        self.locks: list[tuple[str, int]] = []

    # --- helpers de cenário -------------------------------------------------
    def add_weight(self, user_id: int, day: date, kg: float, **extra: Any) -> None:
        self.composition.append(
            {"id": len(self.composition) + 1, "user_id": user_id, "local_date": day, "weight_kg": Decimal(str(kg)),
             "body_fat_percent": extra.get("body_fat_percent"), "fat_free_mass_kg": extra.get("fat_free_mass_kg"),
             "bmr_kcal": extra.get("bmr_kcal")}
        )

    def add_session(self, user_id: int, day: date, kcal: Optional[float], seconds: Optional[int] = 3600) -> None:
        self.activities.append(
            {"user_id": user_id, "local_date": day, "session_status": "done",
             "calories_kcal": Decimal(str(kcal)) if kcal is not None else None, "duration_seconds": seconds}
        )

    def add_plan(self, user_id: int, *, status: str = "draft", strategy: str = "maintenance",
                 effective_from: Optional[date] = None, superseded_at: Optional[datetime] = None,
                 published_at: Optional[datetime] = None, **settings: Any) -> int:
        plano_id = self.next_plan_id
        self.next_plan_id += 1
        base = {
            "id": plano_id, "user_id": user_id, "created_by": 1, "published_by": None, "status": status,
            "strategy": strategy, "advanced": 0, "bmr_formula": "auto", "activity_level": None,
            "training_kcal_mode": "explicit_training", "goal_body_fat_percent": None, "goal_weight_kg": None,
            "weekly_rate_share": None, "surplus_share": None, "protein_g_per_kg": Decimal("2.00"),
            "fat_kcal_share": Decimal("0.2500"), "carb_min_g_per_kg": None, "meals_per_day": None, "notes": None,
            "engine_version": "nutrition-v1", "effective_from": effective_from, "superseded_at": superseded_at,
            "published_at": published_at, "created_at": self.now, "updated_at": self.now,
        }
        base.update(settings)
        self.plans[plano_id] = base
        return plano_id

    def conn(self) -> "FakeConn":
        return FakeConn(self)


class FakeConn:
    def __init__(self, db: FakeNutritionDB) -> None:
        self.db = db
        self._snapshot: Optional[tuple[Any, Any]] = None

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self.db)

    def __enter__(self) -> "FakeConn":
        self._snapshot = (copy.deepcopy(self.db.plans), copy.deepcopy(self.db.targets))
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None and self._snapshot is not None:
            self.db.plans, self.db.targets = self._snapshot
        return False


class FakeCursor:
    def __init__(self, db: FakeNutritionDB) -> None:
        self.db = db
        self._rows: list[dict[str, Any]] = []
        self.rowcount = 0
        self.lastrowid: Optional[int] = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def fetchone(self) -> Optional[dict[str, Any]]:
        return dict(self._rows[0]) if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]

    def _result(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def _upsert(self, p: tuple[Any, ...]) -> None:
        db = self.db
        linha = dict(zip(TARGET_COLUMNS, p))
        json.dumps(linha["inputs_json"])  # o banco recusaria JSON inválido
        linha["limitations_json"] = json.dumps(linha["limitations_json"])
        linha["computed_at"] = db.now
        linha["water_ml"] = None
        for chave in ("kcal_target", "protein_g", "carbs_g", "fat_g", "fiber_g", "bmr_kcal", "tdee_kcal", "training_kcal"):
            if linha[chave] is not None:
                linha[chave] = Decimal(str(round(float(linha[chave]), 2)))
        db.targets[(linha["user_id"], linha["local_date"])] = linha
        db.writes.append(f"upsert {linha['local_date'].isoformat()}")

    def executemany(self, sql: str, seq_of_params: Any) -> None:
        if sql != svc._UPSERT_SQL:
            raise AssertionError(f"executemany inesperado no fake de nutrição: {sql[:120]}")
        lote = [tuple(params) for params in seq_of_params]
        self.db.batches.append(len(lote))
        for params in lote:
            self._upsert(params)
        self.rowcount = -1

    def execute(self, sql: str, params: Any = None) -> None:  # noqa: C901 - despacho por statement
        db = self.db
        p = tuple(params or ())
        self.rowcount = 0
        if sql == access._USER_SQL:
            user = db.users.get(int(p[0]))
            return self._result([user] if user else [])
        if sql == svc._PROFILE_SQL:
            return self._result([db.profiles.get(int(p[0]), {})])
        if sql == svc._COMPOSITION_SQL:
            colunas = ("weight_kg", "body_fat_percent", "fat_free_mass_kg", "bmr_kcal")
            linhas = [r for r in db.composition if r["user_id"] == p[0] and r["local_date"] <= p[1]
                      and any(r[c] is not None for c in colunas)]
            return self._result(sorted(linhas, key=lambda r: (r["local_date"], r["id"])))
        if sql == svc._SITES_SQL:
            uid, sites, ate = p[0], p[1:-1], p[-1]
            linhas = [r for r in db.measurements if r["user_id"] == uid and r["site"] in sites and r["local_date"] <= ate]
            return self._result(sorted(linhas, key=lambda r: r["local_date"]))
        if sql == svc._SESSIONS_SQL:
            return self._result([r for r in db.activities if r["user_id"] == p[0] and r["session_status"] == "done" and p[1] <= r["local_date"] <= p[2]])
        if sql == svc._PUBLISHED_PLANS_SQL:
            return self._result([r for r in db.plans.values() if r["user_id"] == p[0] and r["status"] == p[1] and r["effective_from"] is not None])
        if sql == svc._TARGETS_SQL:
            linhas = [r for (uid, dia), r in db.targets.items() if uid == p[0] and p[1] <= dia <= p[2]]
            return self._result(sorted(linhas, key=lambda r: r["local_date"]))
        if sql == svc._LAST_TARGET_DATE_SQL:
            dias = [dia for (uid, dia) in db.targets if uid == p[0]]
            return self._result([{"ultimo": max(dias) if dias else None}])
        if sql == svc._UPSERT_SQL:
            self._upsert(p)
            return self._result([])
        if sql == "SELECT NOW() AS agora":
            return self._result([{"agora": db.now}])
        if sql == plans_mod._INSERT_SQL:
            cols = ("user_id", "created_by", "status", *plans_mod._SETTINGS_WRITE_COLUMNS)
            valores = dict(zip(cols, p))
            plano_id = db.add_plan(valores["user_id"])
            db.plans[plano_id].update(valores)
            self.lastrowid = plano_id
            self.rowcount = 1
            db.writes.append(f"insert plan {plano_id}")
            return self._result([])
        if sql == plans_mod._BY_ID_SQL:
            plano = db.plans.get(int(p[0]))
            return self._result([plano] if plano else [])
        if sql == plans_mod._BY_USER_SQL:
            return self._result(sorted((r for r in db.plans.values() if r["user_id"] == p[0]), key=lambda r: -r["id"]))
        if sql == plans_mod._CURRENT_FOR_UPDATE_SQL:
            db.locks.append(("current", p[0]))
            return self._result([{"id": r["id"]} for r in db.plans.values()
                                 if r["user_id"] == p[0] and r["status"] == p[1] and r["superseded_at"] is None])
        if sql == plans_mod._UPDATE_DRAFT_SQL:
            *valores, plano_id, status = p
            plano = db.plans.get(int(plano_id))
            if plano and plano["status"] == status:
                plano.update(dict(zip(plans_mod._SETTINGS_WRITE_COLUMNS, valores)))
                self.rowcount = 1
                db.writes.append(f"update plan {plano_id}")
            return self._result([])
        if sql == plans_mod._DELETE_DRAFT_SQL:
            plano = db.plans.get(int(p[0]))
            if plano and plano["status"] == p[1]:
                del db.plans[int(p[0])]
                self.rowcount = 1
                db.writes.append(f"delete plan {p[0]}")
            return self._result([])
        if sql == plans_mod._SUPERSEDE_SQL:
            agora, uid, status, exceto = p
            for plano in db.plans.values():
                if plano["user_id"] == uid and plano["status"] == status and plano["superseded_at"] is None and plano["id"] != exceto:
                    plano["superseded_at"] = agora
                    self.rowcount += 1
                    db.writes.append(f"supersede plan {plano['id']}")
            return self._result([])
        if sql == plans_mod._PUBLISH_SQL:
            status, inicio, publicado, por, versao, plano_id, rascunho = p
            plano = db.plans.get(int(plano_id))
            if plano and plano["status"] == rascunho:
                vigentes = [r for r in db.plans.values() if r["user_id"] == plano["user_id"] and r["status"] == status
                            and r["superseded_at"] is None and r["id"] != plano["id"]]
                if vigentes:
                    raise RuntimeError(f"Duplicate entry '{plano['user_id']}' for key 'nutrition_plans.uq_np_current_user'")
                plano.update({"status": status, "effective_from": inicio, "published_at": publicado,
                              "published_by": por, "engine_version": versao})
                self.rowcount = 1
                db.writes.append(f"publish plan {plano_id}")
            return self._result([])
        raise AssertionError(f"SQL inesperado no fake de nutrição: {sql[:120]}")

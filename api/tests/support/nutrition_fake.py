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
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from app import intervals_store
from app import nutrition_access as access
from app import nutrition_adjustments as adj
from app import nutrition_energy_service as energy_svc
from app import nutrition_schema as schema_mod
from app.load_conventions import _CURRENT_SCHEMA_SQL
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
        # `intervals_activities` do aluno (vinculadas ou não); a consulta do serviço simula o LEFT JOIN de
        # `activity_intervals_link` (`l.id IS NULL`): a vinculada sai da lista por AQUI, não por omissão à mão.
        self.intervals_extras: list[dict[str, Any]] = []
        # `intervals_activities` COMPLETAS (com id, id externo, hora e durações) para a sombra do gasto adaptativo; o fake
        # projeta só as colunas que a SQL pede, como o banco. `synced_at` de cada linha prova a sincronização.
        self.intervals_rows: list[dict[str, Any]] = []
        # Registro de execuções do sync (107): linhas gravadas e se a tabela existe (False = código novo sobre o schema
        # antigo). Lembrete: o cache POSITIVO de `nutrition_schema` é global; testes que tiram a tabela chamam
        # `reset_nutrition_tables_cache()`.
        self.sync_runs: list[dict[str, Any]] = []
        self.sync_runs_table = True
        # Fechamento do dia e correção de atividade (108): `closures` = {(user, dia): closed_at}; `corrections` =
        # {(user, activity_id): {action, energy_kind}}; `adjustment_tables` False = código novo sobre o schema antigo
        # (lembrete: o cache positivo de `nutrition_schema` é global, `reset_nutrition_tables_cache()` o limpa).
        self.closures: dict[tuple[int, date], datetime] = {}
        self.corrections: dict[tuple[int, str], dict[str, Any]] = {}
        self.adjustment_tables = True
        # As colunas da 106 existem neste banco? (False simula o código novo sobre o schema antigo.)
        self.plan_energy_columns = True
        self.timezone = "America/Sao_Paulo"  # `users.timezone` (a sombra e a meta adaptativa convertem a sincronização por ele)
        # Conexão e idade (min) da última sincronização do Intervals: padrão = desconectado.
        self.intervals_connected = False
        self.intervals_minutes_since_sync: Optional[int] = None
        self.now = datetime(2026, 10, 17, 15, 0, 0)
        self.next_plan_id = 1
        self.writes: list[str] = []
        self.batches: list[int] = []
        self.locks: list[tuple[str, int]] = []
        # Modelos de treino: id -> {"name", "slots": [(weekday | None, slot_type)]} (rota "puxar do Modelo").
        self.templates: dict[int, dict[str, Any]] = {}

    # --- helpers de cenário -------------------------------------------------
    def add_weight(self, user_id: int, day: date, kg: float, **extra: Any) -> None:
        self.composition.append(
            {"id": len(self.composition) + 1, "user_id": user_id, "local_date": day, "weight_kg": Decimal(str(kg)),
             "body_fat_percent": extra.get("body_fat_percent"), "fat_free_mass_kg": extra.get("fat_free_mass_kg"),
             "bmr_kcal": extra.get("bmr_kcal")}
        )

    def add_session(self, user_id: int, day: date, kcal: Optional[float], seconds: Optional[int] = 3600,
                    started_at: Optional[datetime] = None) -> None:
        self.activities.append(
            {"user_id": user_id, "local_date": day, "session_status": "done",
             "calories_kcal": Decimal(str(kcal)) if kcal is not None else None, "duration_seconds": seconds,
             "started_at": started_at}
        )

    def add_intervals_activity(self, user_id: int, day: date, sport_type: str, kcal: Optional[float],
                               name: Optional[str] = None, linked: bool = False, elapsed: Optional[int] = None,
                               moving: Optional[int] = None, external_id: Optional[str] = None,
                               start: Optional[datetime] = None) -> None:
        linha_id = len(self.intervals_extras) + 1
        self.intervals_extras.append(
            {"id": linha_id, "external_id": external_id or f"ext-{linha_id}", "start_time": start,
             "user_id": user_id, "activity_date": day, "sport_type": sport_type, "name": name,
             "calories_kcal": Decimal(str(kcal)) if kcal is not None else None,
             "elapsed_time_seconds": elapsed, "moving_time_seconds": moving,
             "linked": linked, "raw_json": "{\"nao\": \"deve sair\"}"}
        )

    def add_intervals_row(self, user_id: int, day: date, sport_type: str, kcal: Optional[float], *,
                          start: Optional[datetime] = None, name: Optional[str] = None, elapsed: Optional[int] = 3600,
                          moving: Optional[int] = None, external_id: Optional[str] = None,
                          synced_at: Optional[datetime] = None) -> int:
        """Linha de `intervals_activities` COMPLETA (sombra do gasto adaptativo); devolve o id."""
        linha_id = len(self.intervals_rows) + 1
        self.intervals_rows.append(
            {"id": linha_id, "user_id": user_id, "external_id": external_id or f"ext-{linha_id}", "activity_date": day,
             "start_time": start, "name": name, "sport_type": sport_type, "moving_time_seconds": moving,
             "elapsed_time_seconds": elapsed, "calories_kcal": Decimal(str(kcal)) if kcal is not None else None,
             "synced_at": synced_at or self.now, "raw_json": "{\"nao\": \"deve sair\"}"}
        )
        return linha_id

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
            "macro_priority": "balanced", "manual_targets_json": None, "carb_cycling_json": None, "maintenance_kcal_adjust": 0,
            "engine_version": "nutrition-v1", "effective_from": effective_from, "superseded_at": superseded_at,
            "energy_params_json": None, "energy_model_version": None, "published_preview_json": None,
            "archived_at": None, "published_at": published_at, "created_at": self.now, "updated_at": self.now,
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

    @staticmethod
    def _selected(sql: str) -> list[str]:
        """Colunas que o SELECT pede (`SELECT a, b FROM ...`): o fake projeta SÓ elas, como o banco."""
        return [c.strip() for c in sql.split(" FROM ")[0].removeprefix("SELECT ").split(",")]

    def _project(self, sql: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        pedidas = self._selected(sql)
        return [{c: r.get(c) for c in pedidas} for r in rows]

    @staticmethod
    def _json_column(valor: Any) -> Any:
        """Coluna JSON do MySQL: grava o objeto e devolve TEXTO JSON (o leitor tem de decodificar)."""
        return json.dumps(valor) if valor is not None and not isinstance(valor, str) else valor

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

    def _adjustments(self, sql: str, p: tuple[Any, ...]) -> None:
        db = self.db
        if sql == adj.CLOSURES_SQL:
            return self._result([{"day": d, "closed_at": t} for (uid, d), t in sorted(db.closures.items(), key=lambda i: i[0][1])
                                 if uid == p[0] and p[1] <= d <= p[2]])
        if sql == adj.CLOSURE_ONE_SQL:
            return self._result([{"day": p[1], "closed_at": db.closures[(p[0], p[1])]}] if (p[0], p[1]) in db.closures else [])
        if sql == adj.CLOSE_SQL:
            db.closures.setdefault((p[0], p[1]), db.now)  # ON DUPLICATE KEY ... closed_at = closed_at
            db.writes.append(f"close {p[1].isoformat()}")
        elif sql == adj.REOPEN_SQL:
            db.closures.pop((p[0], p[1]), None)
            db.writes.append(f"reopen {p[1].isoformat()}")
        elif sql == adj.CORRECTIONS_SQL:
            return self._result([{"activity_id": a, "action": c["action"], "energy_kind": c["energy_kind"]}
                                 for (uid, a), c in sorted(db.corrections.items(), key=lambda i: i[0][1]) if uid == p[0]])
        elif sql == adj.UPSERT_CORRECTION_SQL:
            db.corrections[(p[0], p[1])] = {"action": p[2], "energy_kind": p[3]}
            db.writes.append(f"correct {p[1]}")
        elif sql == adj.CLEAR_CORRECTION_SQL:
            db.corrections.pop((p[0], p[1]), None)
            db.writes.append(f"uncorrect {p[1]}")
        return self._result([])

    def execute(self, sql: str, params: Any = None) -> None:  # noqa: C901 - despacho por statement
        db = self.db
        p = tuple(params or ())
        self.rowcount = 0
        if sql == _CURRENT_SCHEMA_SQL:
            return self._result([{"db": "nutrition_test"}])
        if sql == schema_mod.column_sql():  # colunas do plano (106): presentes, salvo se o teste as tirar
            return self._result([{"n": 1 if db.plan_energy_columns else 0}])
        if sql == schema_mod.tables_sql(1) and p == (schema_mod.SYNC_RUNS_TABLE,):  # tabela da 107
            return self._result([{"n": 1 if db.sync_runs_table else 0}])
        if sql == schema_mod.tables_sql(2) and p == schema_mod.ADJUSTMENT_TABLES:  # tabelas da 108
            return self._result([{"n": 2 if db.adjustment_tables else 0}])
        if sql in (adj.CLOSURES_SQL, adj.CLOSURE_ONE_SQL, adj.CLOSE_SQL, adj.REOPEN_SQL, adj.CORRECTIONS_SQL,
                   adj.UPSERT_CORRECTION_SQL, adj.CLEAR_CORRECTION_SQL):
            if not db.adjustment_tables:
                raise AssertionError("SQL da 108 sem as tabelas (a guarda deveria ter impedido)")
            return self._adjustments(sql, p)
        if sql == adj.ACTIVITY_OWNER_SQL:
            linha = next((r for r in db.intervals_rows if r["user_id"] == p[0] and r["id"] == p[1]), None)
            return self._result([{"id": linha["id"], "activity_date": linha["activity_date"]}] if linha else [])
        if sql == energy_svc.SYNC_RUNS_SQL:
            if not db.sync_runs_table:
                raise AssertionError("SELECT em intervals_sync_runs sem a tabela (a guarda deveria ter impedido)")
            pedidas = self._selected(sql)
            linhas = [r for r in db.sync_runs if r["user_id"] == p[0] and r["status"] == p[1] and r["window_end"] >= p[2]]
            return self._result([{c: r[c] for c in pedidas}
                                 for r in sorted(linhas, key=lambda r: (r["finished_at"], r["id"]))])
        if sql == intervals_store._INSERT_SYNC_RUN_SQL:
            if not db.sync_runs_table:
                raise AssertionError("INSERT em intervals_sync_runs sem a tabela (a guarda deveria ter impedido)")
            colunas = ("user_id", "started_at", "finished_at", "window_start", "window_end", "status", "activities_count", "source")
            db.sync_runs.append({"id": len(db.sync_runs) + 1, **dict(zip(colunas, p))})
            db.writes.append("sync_run")
            return self._result([])
        if sql == intervals_store._PRUNE_SYNC_RUNS_SQL:
            antes = len(db.sync_runs)
            db.sync_runs = [r for r in db.sync_runs if not (r["user_id"] == p[0] and r["finished_at"] < p[1])]
            self.rowcount = antes - len(db.sync_runs)
            return self._result([])
        if sql == svc._STORED_TDEE_SQL:
            alvo = db.targets.get((p[0], p[1]))
            tdee = ((alvo or {}).get("inputs_json") or {}).get("tdee")
            return self._result([{"tdee_json": json.dumps(tdee) if tdee is not None else None}] if alvo else [])
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
            colunas = ("local_date", "calories_kcal", "duration_seconds", "started_at")
            return self._result([{c: r.get(c) for c in colunas} for r in db.activities
                                 if r["user_id"] == p[0] and r["session_status"] == "done" and p[1] <= r["local_date"] <= p[2]])
        if sql == svc.INTERVALS_CONNECTION_SQL:
            return self._result([{"intervals_athlete_id": "i1" if db.intervals_connected else None,
                                  "intervals_api_key_encrypted": "k" if db.intervals_connected else None}])
        if sql == svc.INTERVALS_SYNC_AGE_SQL:
            return self._result([{"minutes_since_sync": db.intervals_minutes_since_sync}])
        if sql == svc.EXTRA_ACTIVITIES_SQL:
            colunas = ("id", "external_id", "start_time", "activity_date", "sport_type", "name", "calories_kcal",
                       "elapsed_time_seconds", "moving_time_seconds")
            return self._result([{c: r.get(c) for c in colunas} for r in db.intervals_extras
                                 if r["user_id"] == p[0] and p[1] <= r["activity_date"] <= p[2] and not r["linked"]])
        if sql == energy_svc.USER_TIMEZONE_SQL:
            return self._result([{"timezone": db.timezone}])
        if sql == energy_svc.TARGET_BMR_SQL:
            linhas = [{"local_date": dia, "bmr_kcal": r.get("bmr_kcal")} for (uid, dia), r in sorted(db.targets.items(), key=lambda i: i[0][1])
                      if uid == p[0] and p[1] <= dia <= p[2]]
            return self._result(linhas)
        if sql == energy_svc.INTERVALS_SYNC_SQL:
            minhas = [r for r in db.intervals_rows if r["user_id"] == p[0]]
            return self._result([{"last_sync_at": max((r["synced_at"] for r in minhas), default=None),
                                  "first_activity_date": min((r["activity_date"] for r in minhas), default=None)}])
        if sql == energy_svc.SHADOW_ACTIVITIES_SQL:
            pedidas = [c.strip() for c in sql.split(" FROM ")[0].removeprefix("SELECT ").split(",")]
            linhas = [r for r in db.intervals_rows if r["user_id"] == p[0] and p[1] <= r["activity_date"] <= p[2]]
            ordenadas = sorted(linhas, key=lambda r: (r["activity_date"], r["start_time"] or datetime.min, r["id"]))
            return self._result([{c: r[c] for c in pedidas} for r in ordenadas])
        if sql == plans_mod._PUBLISHED_PREVIEW_SQL:
            plano = db.plans.get(int(p[1]))
            if plano:
                plano["published_preview_json"] = self._json_column(p[0])
                self.rowcount = 1
                db.writes.append(f"preview plan {p[1]}")
            return self._result([])
        if sql in (svc._PUBLISHED_PLANS_SQL, svc._PUBLISHED_PLANS_WITHOUT_ENERGY_SQL):
            return self._result(self._project(sql, [r for r in db.plans.values()
                                                    if r["user_id"] == p[0] and r["status"] == p[1] and r["effective_from"] is not None]))
        if sql == svc._TARGETS_SQL:
            linhas = [r for (uid, dia), r in db.targets.items() if uid == p[0] and p[1] <= dia <= p[2]]
            # Projeta SÓ as colunas que a SQL pede (como o banco): coluna não selecionada não existe na linha, e o JSON
            # extraído (`JSON_EXTRACT(inputs_json, '$.x') AS alias`) vira texto JSON.
            pedidas = [c.strip() for c in svc._TARGET_COLUMNS.split(",")]
            projetadas = []
            for r in sorted(linhas, key=lambda r: r["local_date"]):
                linha = {c: r.get(c) for c in pedidas}
                entradas = r.get("inputs_json") or {}
                for chave, alias in (("extra_activity", "extra_activity_json"), ("tdee_basis", "tdee_basis_json"), ("energy", "energy_json")):
                    if f"AS {alias}" in svc._TARGET_JSON_COLUMNS:
                        linha[alias] = json.dumps(entradas[chave]) if chave in entradas else None
                projetadas.append(linha)
            return self._result(projetadas)
        if sql == svc._LAST_TARGET_DATE_SQL:
            dias = [dia for (uid, dia) in db.targets if uid == p[0]]
            return self._result([{"ultimo": max(dias) if dias else None}])
        if sql == svc._UPSERT_SQL:
            self._upsert(p)
            return self._result([])
        if sql == plans_mod._TEMPLATE_NAME_SQL:
            modelo = db.templates.get(int(p[0]))
            return self._result([{"name": modelo["name"]}] if modelo else [])
        if sql == plans_mod._TEMPLATE_SLOTS_SQL:
            modelo = db.templates.get(int(p[0]))
            return self._result([{"weekday": dia, "slot_type": tipo} for dia, tipo in (modelo["slots"] if modelo else [])])
        if sql == "SELECT NOW() AS agora":
            return self._result([{"agora": db.now}])
        if sql == plans_mod._INSERT_SQL:
            cols = ("user_id", "created_by", "status", *plans_mod._SETTINGS_WRITE_COLUMNS)
            valores = dict(zip(cols, p))
            valores["energy_params_json"] = self._json_column(valores["energy_params_json"])
            plano_id = db.add_plan(valores["user_id"])
            db.plans[plano_id].update(valores)
            self.lastrowid = plano_id
            self.rowcount = 1
            db.writes.append(f"insert plan {plano_id}")
            return self._result([])
        if sql == plans_mod._BY_ID_SQL:
            plano = db.plans.get(int(p[0]))
            return self._result(self._project(sql, [plano]) if plano else [])
        if sql == plans_mod._BY_USER_SQL:
            linhas = sorted((r for r in db.plans.values() if r["user_id"] == p[0]), key=lambda r: -r["id"])
            return self._result(self._project(sql, linhas[: int(p[1])]))
        if sql == plans_mod._BY_USER_VISIBLE_SQL:
            linhas = sorted((r for r in db.plans.values() if r["user_id"] == p[0] and r["archived_at"] is None),
                            key=lambda r: -r["id"])
            return self._result(self._project(sql, linhas[: int(p[1])]))
        if sql == plans_mod._ARCHIVE_SQL:
            agora, plano_id, status = p
            plano = db.plans.get(int(plano_id))
            if plano and plano["archived_at"] is None and plano["status"] == status and plano["superseded_at"] is not None:
                plano["archived_at"] = agora
                self.rowcount = 1
                db.writes.append(f"archive plan {plano_id}")
            return self._result([])
        if sql == plans_mod._UNARCHIVE_SQL:
            plano = db.plans.get(int(p[0]))
            if plano and plano["archived_at"] is not None:
                plano["archived_at"] = None
                self.rowcount = 1
                db.writes.append(f"unarchive plan {p[0]}")
            return self._result([])
        if sql == plans_mod._DRAFT_COUNT_SQL:
            return self._result([{"n": sum(1 for r in db.plans.values() if r["user_id"] == p[0] and r["status"] == p[1])}])
        if sql == plans_mod._CURRENT_FOR_UPDATE_SQL:
            db.locks.append(("current", p[0]))
            return self._result([{"id": r["id"]} for r in db.plans.values()
                                 if r["user_id"] == p[0] and r["status"] == p[1] and r["superseded_at"] is None])
        if sql == plans_mod._UPDATE_DRAFT_SQL:
            *valores, plano_id, status = p
            plano = db.plans.get(int(plano_id))
            if plano and plano["status"] == status:
                escritos = dict(zip(plans_mod._SETTINGS_WRITE_COLUMNS, valores))
                escritos["energy_params_json"] = self._json_column(escritos["energy_params_json"])
                plano.update(escritos)
                plano["updated_at"] = db.now + timedelta(seconds=len(db.writes) + 1)  # o banco atualiza updated_at
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
        if sql in (plans_mod._PUBLISH_SQL, plans_mod._PUBLISH_IF_UNCHANGED_SQL):
            versao_lida = None
            if sql == plans_mod._PUBLISH_IF_UNCHANGED_SQL:
                *p, versao_lida = p
                p = tuple(p)
            status, inicio, publicado, por, versao, versao_energia, plano_id, rascunho = p
            if versao_lida is not None and db.plans.get(int(plano_id), {}).get("updated_at") != versao_lida:
                return self._result([])
            plano = db.plans.get(int(plano_id))
            if plano and plano["status"] == rascunho:
                vigentes = [r for r in db.plans.values() if r["user_id"] == plano["user_id"] and r["status"] == status
                            and r["superseded_at"] is None and r["id"] != plano["id"]]
                if vigentes:
                    raise RuntimeError(f"Duplicate entry '{plano['user_id']}' for key 'nutrition_plans.uq_np_current_user'")
                plano.update({"status": status, "effective_from": inicio, "published_at": publicado,
                              "published_by": por, "engine_version": versao, "energy_model_version": versao_energia,
                              })
                self.rowcount = 1
                db.writes.append(f"publish plan {plano_id}")
            return self._result([])
        raise AssertionError(f"SQL inesperado no fake de nutrição: {sql[:120]}")

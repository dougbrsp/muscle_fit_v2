"""Banco em memória para os testes do diário de nutrição (L4).

Estende o fake do plano (`nutrition_fake.FakeNutritionDB`: usuários, planos,
metas, composição e sessões) com o catálogo (`food_items`, `food_portions`), as
refeições (`meals`, `meal_items`), o dia (`nutrition_days`) e o link do
Intervals. Responde EXATAMENTE os statements de `app.nutrition_meals`,
`app.nutrition_days` e da meta retroativa de `app.nutrition_targets_service`
(despacho pela constante SQL do módulo ou pela função que monta o `IN (...)`):
SQL novo ou alterado sem o fake acompanhar falha alto. Simula `lastrowid`,
o UNIQUE de `nutrition_daily_targets` no INSERT único (a primeira gravação
fica), o arredondamento das colunas DECIMAL e o rollback da conexão.
"""

from __future__ import annotations

import copy
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from app import nutrition_days as days_mod
from app import nutrition_meals as meals_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition import catalog
from tests.support.nutrition_fake import FakeConn, FakeCursor, FakeNutritionDB

AGORA = datetime(2026, 10, 17, 12, 0, 0)


def _dec(valor: Any, casas: int = 2) -> Optional[Decimal]:
    if valor is None:
        return None
    return Decimal(str(round(float(valor), casas)))


class FakeDiaryDB(FakeNutritionDB):
    def __init__(self) -> None:
        super().__init__()
        self.foods: dict[int, dict[str, Any]] = {}
        self.portions: dict[int, dict[str, Any]] = {}
        self.meals: dict[int, dict[str, Any]] = {}
        self.items: dict[int, dict[str, Any]] = {}
        self.day_logs: dict[tuple[int, date], dict[str, Any]] = {}
        self.links: dict[int, Decimal] = {}
        self.next_id = 100
        self.users[4] = {"id": 4, "username": "outro", "full_name": "Outro Aluno"}
        self.profiles[4] = dict(self.profiles[2])

    def _novo_id(self) -> int:
        self.next_id += 1
        return self.next_id

    # --- cenário -------------------------------------------------------------
    def add_food(self, name: str, *, source: str = "taco", owner: Optional[int] = None, basis: str = "g",
                 active: bool = True, **valores: Any) -> int:
        food_id = self._novo_id()
        linha = {coluna: None for coluna in meals_mod.FOOD_COLUMNS}
        linha.update(id=food_id, source=source, source_id=str(food_id), name=name, basis=basis, owner_user_id=owner,
                     is_active=1 if active else 0, citation=catalog.citation_for(source))
        for campo, valor in valores.items():
            linha[campo] = Decimal(str(valor)) if isinstance(valor, (int, float)) else valor
        self.foods[food_id] = linha
        return food_id

    def add_portion(self, food_id: int, label: str, amount: float, owner: Optional[int] = None) -> int:
        portion_id = self._novo_id()
        self.portions[portion_id] = {"id": portion_id, "food_id": food_id, "label": label,
                                     "amount": Decimal(str(amount)), "owner_user_id": owner}
        return portion_id

    def add_activity(self, user_id: int, day: date, kcal: Optional[float], seconds: Optional[int] = 3600, *,
                     source: Optional[str] = None, intervals_kcal: Optional[float] = None, name: str = "Treino A") -> int:
        activity_id = self._novo_id()
        self.activities.append(
            {"id": activity_id, "user_id": user_id, "local_date": day, "session_status": "done", "name": name,
             "calories_kcal": _dec(kcal), "calories_source": source, "duration_seconds": seconds, "started_at": None}
        )
        if intervals_kcal is not None:
            self.links[activity_id] = _dec(intervals_kcal)
        return activity_id

    def conn(self) -> "FakeDiaryConn":
        return FakeDiaryConn(self)


class FakeDiaryConn(FakeConn):
    _ESTADO = ("plans", "targets", "meals", "items", "day_logs")

    def cursor(self) -> "FakeDiaryCursor":
        return FakeDiaryCursor(self.db)

    def __enter__(self) -> "FakeDiaryConn":
        self._snapshot = tuple(copy.deepcopy(getattr(self.db, nome)) for nome in self._ESTADO)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None and self._snapshot is not None:
            for nome, valor in zip(self._ESTADO, self._snapshot):
                setattr(self.db, nome, valor)
        return False


class FakeDiaryCursor(FakeCursor):
    db: FakeDiaryDB

    def executemany(self, sql: str, seq_of_params: Any) -> None:
        lote = [tuple(p) for p in seq_of_params]
        if sql == svc._INSERT_ONCE_SQL:
            self.db.batches.append(len(lote))
            for params in lote:
                chave = (params[0], params[1])
                if chave in self.db.targets:
                    continue  # ON DUPLICATE KEY UPDATE id = id: a primeira gravação fica
                self._upsert(params)
                self.db.writes[-1] = self.db.writes[-1].replace("upsert", "backfill")
            return
        if sql == meals_mod.INSERT_ITEM_SQL:
            for params in lote:
                self._insert_item(params)
            return
        super().executemany(sql, seq_of_params)

    def _insert_item(self, p: tuple) -> None:
        db = self.db
        linha = dict(zip(meals_mod._ITEM_WRITE_COLUMNS, p))
        json.dumps(linha["limitations_json"])
        json.dumps(linha["catalog_snapshot_json"])
        linha["limitations_json"] = json.dumps(linha["limitations_json"])
        linha["catalog_snapshot_json"] = json.dumps(linha["catalog_snapshot_json"])
        for campo, casas in (("quantity", 3), ("amount", 2), ("match_confidence", 3), *((c, 2) for c in meals_mod.NUTRIENTS)):
            linha[campo] = _dec(linha[campo], casas)
        linha["id"] = db._novo_id()
        db.items[linha["id"]] = linha
        db.writes.append(f"insert item {linha['id']}")

    def _meal_values(self, colunas: tuple[str, ...], valores: tuple) -> dict[str, Any]:
        linha = dict(zip(colunas, valores))
        for campo in meals_mod.NUTRIENTS:
            if campo in linha:
                linha[campo] = _dec(linha[campo])
        if "totals_incomplete_json" in linha:
            linha["totals_incomplete_json"] = json.dumps(linha["totals_incomplete_json"])
        return linha

    def execute(self, sql: str, params: Any = None) -> None:  # noqa: C901 - despacho por statement
        db = self.db
        p = tuple(params or ())
        self.rowcount = 0
        n = len(p)
        if sql == meals_mod.foods_by_ids_sql(n):
            return self._result([dict(db.foods[i]) for i in p if i in db.foods])
        if sql == meals_mod.portions_by_ids_sql(n):
            return self._result([dict(db.portions[i]) for i in p if i in db.portions])
        if sql == meals_mod.items_of_meals_sql(n):
            linhas = [r for r in db.items.values() if r["meal_id"] in p]
            return self._result(sorted(linhas, key=lambda r: (r["meal_id"], r["position"], r["id"])))
        if sql == meals_mod.MEAL_BY_ID_SQL:
            refeicao = db.meals.get(int(p[0]))
            return self._result([refeicao] if refeicao else [])
        if sql == meals_mod.MEALS_OF_RANGE_SQL:
            linhas = [r for r in db.meals.values() if r["user_id"] == p[0] and p[1] <= r["local_date"] <= p[2]]
            return self._result(sorted(linhas, key=lambda r: (r["local_date"], r["id"])))
        if sql == meals_mod.INSERT_MEAL_SQL:
            colunas = (*meals_mod._MEAL_HEADER_COLUMNS, *meals_mod._TOTAL_COLUMNS, "created_by", "updated_by")
            linha = self._meal_values(colunas, p)
            linha.update(id=db._novo_id(), created_at=AGORA, updated_at=AGORA)
            db.meals[linha["id"]] = linha
            self.lastrowid = linha["id"]
            self.rowcount = 1
            db.writes.append(f"insert meal {linha['id']}")
            return self._result([])
        if sql == meals_mod.UPDATE_MEAL_SQL:
            colunas = ("local_date", "meal_slot", "eaten_at", "notes", *meals_mod._TOTAL_COLUMNS, "updated_by")
            *valores, meal_id = p
            if int(meal_id) in db.meals:
                db.meals[int(meal_id)].update(self._meal_values(colunas, tuple(valores)))
                self.rowcount = 1
                db.writes.append(f"update meal {meal_id}")
            return self._result([])
        if sql == meals_mod.DELETE_ITEMS_SQL:
            for item_id in [i for i, r in db.items.items() if r["meal_id"] == p[0]]:
                del db.items[item_id]
                self.rowcount += 1
            db.writes.append(f"delete items {p[0]}")
            return self._result([])
        if sql == meals_mod.DELETE_MEAL_SQL:
            if db.meals.pop(int(p[0]), None) is not None:
                for item_id in [i for i, r in db.items.items() if r["meal_id"] == p[0]]:
                    del db.items[item_id]  # FK ON DELETE CASCADE
                self.rowcount = 1
                db.writes.append(f"delete meal {p[0]}")
            return self._result([])
        if sql == days_mod.DAY_LOG_SQL:
            linha = db.day_logs.get((p[0], p[1]))
            return self._result([linha] if linha else [])
        if sql == days_mod.UPSERT_DAY_LOG_SQL:
            uid, dia, agua, fome, notas, por = p
            db.day_logs[(uid, dia)] = {"local_date": dia, "water_ml": _dec(agua, 1), "hunger": fome, "notes": notas,
                                       "updated_by": por, "updated_at": AGORA}
            db.writes.append(f"day log {dia.isoformat()}")
            return self._result([])
        if sql == days_mod.SESSIONS_OF_DAY_SQL:
            linhas = [
                {**{k: r.get(k) for k in ("id", "name", "calories_kcal", "calories_source", "duration_seconds")},
                 "intervals_calories_kcal": db.links.get(r.get("id"))}
                for r in db.activities
                if r["user_id"] == p[0] and r["session_status"] == "done" and r["local_date"] == p[1]
            ]
            return self._result(linhas)
        if sql == svc._INSERT_ONCE_SQL:  # pragma: no cover - a rota grava em lote
            raise AssertionError("meta retroativa deve ir em lote (executemany)")
        return super().execute(sql, params)

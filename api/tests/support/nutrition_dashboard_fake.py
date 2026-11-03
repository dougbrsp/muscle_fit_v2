"""Banco em memória do dashboard de nutrição (N5): o fake do diário mais os três
statements próprios de `app.nutrition_dashboard` (janela do corpo e a primeira
medida a partir do início do plano). SQL novo sem o fake acompanhar falha alto."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from app import nutrition_dashboard as dash_mod
from tests.support.nutrition_diary_fake import FakeDiaryConn, FakeDiaryCursor, FakeDiaryDB


class FakeDashboardDB(FakeDiaryDB):
    def add_body(self, user_id: int, day: date, *, weight: Optional[float] = None, body_fat: Optional[float] = None,
                 muscle: Optional[float] = None) -> None:
        self.composition.append(
            {"id": len(self.composition) + 1, "user_id": user_id, "local_date": day,
             "weight_kg": Decimal(str(weight)) if weight is not None else None,
             "body_fat_percent": Decimal(str(body_fat)) if body_fat is not None else None,
             "muscle_mass_kg": Decimal(str(muscle)) if muscle is not None else None,
             "fat_free_mass_kg": None, "bmr_kcal": None}
        )

    def conn(self) -> "FakeDashboardConn":
        return FakeDashboardConn(self)


class FakeDashboardConn(FakeDiaryConn):
    def cursor(self) -> "FakeDashboardCursor":
        return FakeDashboardCursor(self.db)


class FakeDashboardCursor(FakeDiaryCursor):
    def execute(self, sql: str, params: Any = None) -> None:
        db = self.db
        p = tuple(params or ())
        if sql == dash_mod.BODY_WINDOW_SQL:
            linhas = [r for r in db.composition if r["user_id"] == p[0] and p[1] <= r["local_date"] <= p[2]
                      and any(r.get(c) is not None for c in ("weight_kg", "body_fat_percent", "muscle_mass_kg"))]
            return self._result(sorted(linhas, key=lambda r: (r["local_date"], r["id"])))
        for statement, coluna in ((dash_mod.FIRST_WEIGHT_SQL, "weight_kg"), (dash_mod.FIRST_BODY_FAT_SQL, "body_fat_percent")):
            if sql == statement:
                linhas = sorted((r for r in db.composition if r["user_id"] == p[0] and r["local_date"] >= p[1]
                                 and r.get(coluna) is not None), key=lambda r: (r["local_date"], r["id"]))
                return self._result([{coluna: linhas[0][coluna]}] if linhas else [])
        return super().execute(sql, params)

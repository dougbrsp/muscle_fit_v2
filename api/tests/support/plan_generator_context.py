"""Banco dublado para o contexto de geracao de ficha (SEG-04).

`workout_plan_ai.gather_ficha_context` lia `gyms` e o catalogo canonico por
self-call HTTP (`GET /gyms`, `GET /exercise-mapping-catalog`) com o token
mestre. A fixture `tests/unit/fixtures/plan_generator_context/contexto.json`
foi gravada com o codigo ANTERIOR a troca: as duas rotas, servidas por
TestClient sobre ESTE banco dublado, com a resposta JSON passando pelo mesmo
`_trim_exercise_catalog` do contexto. O teste novo monta o contexto sem rede
nem `API_TOKEN` sobre o mesmo banco e compara com ela: mesmas linhas de
entrada, mesmo SQL/parametros, mesmo payload.

As linhas cobrem os tipos que o MySQL/Data API devolvem e que a rota
convertia no caminho para JSON: `timedelta` (TIME), `datetime`, `Decimal`,
JSON em texto (`usual_weekdays`), `logo_path` com e sem valor.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "unit" / "fixtures" / "plan_generator_context" / "contexto.json"


def gym_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "owner_user_id": 1,
            "name": "Academia Centro",
            "usual_weekdays": "[1, 3, 5]",
            "is_recurring": 1,
            "notes": None,
            "logo_path": "gyms/1.png",
            "opens_at": timedelta(hours=6),
            "closes_at": timedelta(hours=22, minutes=30),
            "is_active": 1,
            "created_at": datetime(2026, 9, 1, 10, 0, 0),
            "updated_at": datetime(2026, 9, 2, 11, 30, 15),
        },
        {
            "id": 47,
            "owner_user_id": 2,
            "name": "Box Zona Sul",
            "usual_weekdays": None,
            "is_recurring": 0,
            "notes": "Sem barra olímpica",
            "logo_path": None,
            "opens_at": None,
            "closes_at": timedelta(hours=21, minutes=0, seconds=59),
            "is_active": 1,
            "created_at": datetime(2026, 9, 10, 8, 0, 0),
            "updated_at": datetime(2026, 9, 10, 8, 0, 0),
        },
    ]


def catalog_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1001,
            "exercise_name": "Supino reto com barra",
            "amazfit_name": "Supino reto",
            "equipment": None,
            "body_position": "deitado",
            "movement": "empurrar",
            "trajectory": "horizontal",
            "biomechanical_pattern": "empurrar horizontal",
            "primary_joint": "ombro",
            "resistance_vector": "vertical",
            "primary_group_id": 3,
            "primary_group": "Peitoral",
            "secondary_group_id": 7,
            "secondary_group": "Tríceps",
            "keywords": "supino, barra",
            "description": "Descida controlada até o peito.",
            "mapping_status": "confirmed",
            "source": "manual",
            "is_active": 1,
            "created_at": datetime(2026, 8, 1, 9, 0, 0),
            "updated_at": datetime(2026, 9, 20, 18, 45, 0),
        },
        {
            "id": 1004,
            "exercise_name": "Agachamento livre",
            "amazfit_name": None,
            "equipment": None,
            "body_position": "em pé",
            "movement": "agachar",
            "trajectory": None,
            "biomechanical_pattern": "dominante de joelho",
            "primary_joint": "joelho",
            "resistance_vector": Decimal("1.50"),
            "primary_group_id": 11,
            "primary_group": "Quadríceps",
            "secondary_group_id": None,
            "secondary_group": None,
            "keywords": None,
            "description": None,
            "mapping_status": None,
            "source": None,
            "is_active": 1,
            "created_at": datetime(2026, 8, 1, 9, 0, 0),
            "updated_at": datetime(2026, 8, 1, 9, 0, 0),
        },
    ]


class FakeCursor:
    def __init__(self, log: list[dict[str, Any]]):
        self._log = log
        self._rows: list[dict[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        normalized = " ".join(sql.split())
        self._log.append({"sql": normalized, "params": list(params) if params is not None else None})
        if "FROM gyms" in normalized:
            self._rows = gym_rows()
        elif "COUNT(*) AS total" in normalized:
            self._rows = [{"total": len(catalog_rows())}]
        elif "FROM exercise_catalog" in normalized:
            self._rows = catalog_rows()
        else:
            raise AssertionError(f"SQL inesperado no banco dublado: {normalized[:120]}")

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self):
        self.statements: list[dict[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.statements)


def fake_transaction_factory(conn: FakeConn):
    @contextlib.contextmanager
    def _transaction():
        yield conn

    return _transaction

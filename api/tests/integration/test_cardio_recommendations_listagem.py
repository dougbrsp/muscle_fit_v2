"""GET /cardio-recommendations (listagem) x GET /cardio-recommendations/{id}
(detalhe) contra MySQL real (backend pymysql).

A listagem NAO traz `context_snapshot` (chave mantida como `null` por
compatibilidade) e tem teto de linhas (`LIST_MAX_LIMIT`) - o JSON do snapshot
(~110-170 KB por linha) estourava o limite de 1 MiB da RDS Data API em
producao. O detalhe continua devolvendo o snapshot completo. O cenario com a
Data API em si esta em tests/integration/dataapi/test_cardio_recommendations_1mb.py.
"""

from __future__ import annotations

import inspect
import json
import os
import uuid

import pytest

from app import cardio_recommendations as cr
from app.security import Identity
from tests.support.cardio_schema import ensure_cardio_table
from tests.support.dataapi_backend import raw_mysql


def _raw(database: str):
    return raw_mysql(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=database,
    )


def _snapshot(size: int) -> str:
    return json.dumps({"blob": "x" * size})


class Seed:
    def __init__(self, conn):
        self.conn = conn

    def user(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'regular')",
                (f"cardio-{uuid.uuid4().hex[:12]}",),
            )
            return cur.lastrowid

    def plan(self, user_id: int) -> int:
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO workout_plans (name, user_id) VALUES ('Plano cardio', %s)", (user_id,))
            return cur.lastrowid

    def recs(self, user_id: int, n: int, *, snapshot_bytes: int = 1000, markdown_bytes: int = 200, workout_plan_id=None) -> list[int]:
        ids = []
        with self.conn.cursor() as cur:
            for i in range(n):
                cur.execute(
                    """
                    INSERT INTO cardio_recommendations
                      (reference_date, workout_plan_id, generated_at, model, context_snapshot,
                       recommendation_markdown, user_id)
                    VALUES ('2026-09-01', %s, TIMESTAMP('2026-09-01 00:00:00') + INTERVAL %s MINUTE,
                            'gpt-test', %s, %s, %s)
                    """,
                    (workout_plan_id, i, _snapshot(snapshot_bytes), f"# rec {i}\n" + "m" * markdown_bytes, user_id),
                )
                ids.append(cur.lastrowid)
        return ids


@pytest.fixture
def seed(mysql_schema):
    with _raw(mysql_schema) as conn:
        ensure_cardio_table(conn)  # migracao 025, mesmo caminho de producao
        with conn.cursor() as cur:
            cur.execute("DELETE FROM cardio_recommendations")
        yield Seed(conn)


def listar(user_id: int, *, limit: int = cr.LIST_DEFAULT_LIMIT, offset: int = 0, workout_plan_id=None, days=None):
    return cr.list_cardio_recommendations(
        days=days,
        limit=limit,
        offset=offset,
        workout_plan_id=workout_plan_id,
        identity=Identity(user_id=user_id, role="regular", source="session"),
    )["items"]


def _as_json(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def test_listagem_devolve_snapshot_null_e_detalhe_devolve_completo(seed):
    uid = seed.user()
    ids = seed.recs(uid, 3, snapshot_bytes=5000)

    items = listar(uid)

    assert [item["id"] for item in items] == list(reversed(ids))
    for item in items:
        assert "context_snapshot" in item and item["context_snapshot"] is None
        assert item["recommendation_markdown"].startswith("# rec ")
        assert set(item) == {
            "id", "reference_date", "workout_plan_id", "generated_at", "model", "prompt_version",
            "context_snapshot", "recommendation_markdown", "created_at",
        }

    detail = cr.get_cardio_recommendation(ids[0], identity=Identity(user_id=uid, role="regular", source="session"))
    assert _as_json(detail["context_snapshot"]) == {"blob": "x" * 5000}


def test_filtro_workout_plan_id(seed):
    uid = seed.user()
    plan_a, plan_b = seed.plan(uid), seed.plan(uid)
    ids_a = seed.recs(uid, 2, workout_plan_id=plan_a)
    seed.recs(uid, 3, workout_plan_id=plan_b)

    assert {item["id"] for item in listar(uid, workout_plan_id=plan_a)} == set(ids_a)
    # uso da tela de detalhe do plano (PlanDetailClient: ?workout_plan_id=&limit=1)
    assert [item["id"] for item in listar(uid, workout_plan_id=plan_a, limit=1)] == [ids_a[-1]]


def test_limite_padrao_maximo_truncado_e_offset(seed):
    uid = seed.user()
    ids = list(reversed(seed.recs(uid, cr.LIST_MAX_LIMIT + 5)))  # mais recente primeiro

    limit_param = inspect.signature(cr.list_cardio_recommendations).parameters["limit"].default
    assert limit_param.default == cr.LIST_DEFAULT_LIMIT == 50
    assert cr.LIST_MAX_LIMIT == 100

    assert [i["id"] for i in listar(uid)] == ids[:50]
    assert [i["id"] for i in listar(uid, limit=2)] == ids[:2]
    # cliente antigo (?limit=200 / 1000): sem 422, truncado no teto
    assert [i["id"] for i in listar(uid, limit=200)] == ids[:100]
    assert len(listar(uid, limit=1000)) == 100
    assert [i["id"] for i in listar(uid, limit=100, offset=100)] == ids[100:]


def test_listagem_isola_usuario(seed):
    uid, other = seed.user(), seed.user()
    mine = seed.recs(uid, 2)
    seed.recs(other, 2)

    assert {item["id"] for item in listar(uid)} == set(mine)

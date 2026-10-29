"""Limite de 1 MiB do resultado da RDS Data API x GET /cardio-recommendations.

Producao (2026-09, logs): `GET /cardio-recommendations?limit=200` falhava com
`UnsupportedResultException: The result exceeds the size limit 1 MB.` porque a
listagem trazia `context_snapshot` (~111 KB/linha, 25 linhas ~= 2,8 MB). Em
dev/CI (pymysql) nao ha limite, entao o erro so aparecia em producao. O dublê
agora reproduz o limite ([AWS-4] em tests/support/fake_rds_data.py):

- a query ANTIGA passa no pymysql e falha no shim Data API (divergencia real);
- a listagem NOVA passa nos dois backends com dados volumosos;
- o teto de 100 itens cabe com markdown de 8 KB (~2x o maximo medido, 4,3 KB).
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest
from botocore.exceptions import ClientError

from app import cardio_recommendations as cr
from app.security import Identity
from tests.support.cardio_schema import ensure_cardio_table
from tests.support.fake_rds_data import FAKE_CLUSTER_ARN, FAKE_SECRET_ARN, RESULT_SIZE_LIMIT_BYTES

SNAPSHOT_BYTES = 120 * 1024

# SELECT da listagem ANTES desta correcao (com context_snapshot).
OLD_LIST_SQL = """
    SELECT id, reference_date, workout_plan_id, generated_at, model,
           context_snapshot, recommendation_markdown, created_at
    FROM cardio_recommendations
    WHERE user_id = %s
    ORDER BY generated_at DESC, id DESC
    LIMIT %s
"""


def _identity(user_id: int) -> Identity:
    return Identity(user_id=user_id, role="regular", source="session")


@pytest.fixture
def seed_volumoso(raw):
    """Garante a tabela pela migracao 025 (conexao neutra) e devolve uma
    funcao que semeia N recomendacoes pela mesma conexao."""
    ensure_cardio_table(raw)

    def _seed(n: int, *, markdown_bytes: int = 3700) -> int:
        snapshot = json.dumps({"blob": "s" * SNAPSHOT_BYTES})
        with raw.cursor() as cur:
            cur.execute("DELETE FROM cardio_recommendations")
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'regular')",
                (f"cardio1mb-{uuid.uuid4().hex[:12]}",),
            )
            user_id = cur.lastrowid
            for i in range(n):
                cur.execute(
                    """
                    INSERT INTO cardio_recommendations
                      (reference_date, generated_at, model, context_snapshot, recommendation_markdown, user_id)
                    VALUES ('2026-09-01', TIMESTAMP('2026-09-01 00:00:00') + INTERVAL %s MINUTE,
                            'gpt-test', %s, %s, %s)
                    """,
                    (i, snapshot, "m" * markdown_bytes, user_id),
                )
        return user_id

    return _seed


# -- o dublê em si ----------------------------------------------------------


def _execute(client, mysql_schema, sql):
    return client.execute_statement(
        resourceArn=FAKE_CLUSTER_ARN, secretArn=FAKE_SECRET_ARN, database=mysql_schema, sql=sql
    )


def test_fake_rejeita_resultado_acima_de_1_mib(fake_rds, mysql_schema):
    client = fake_rds.client()

    with pytest.raises(ClientError) as exc_info:
        _execute(client, mysql_schema, f"SELECT REPEAT('a', {RESULT_SIZE_LIMIT_BYTES}) AS big")
    assert exc_info.value.response["Error"]["Code"] == "UnsupportedResultException"
    assert "The result exceeds the size limit 1 MB" in str(exc_info.value)

    ok = _execute(client, mysql_schema, "SELECT REPEAT('a', 1000000) AS big")
    assert len(ok["records"][0][0]["stringValue"]) == 1000000


def test_fake_limite_pode_ser_desligado_por_instancia(fake_rds, mysql_schema):
    fake_rds.max_result_bytes = None
    out = _execute(fake_rds.client(), mysql_schema, f"SELECT REPEAT('a', {RESULT_SIZE_LIMIT_BYTES + 10}) AS big")
    assert len(out["records"][0][0]["stringValue"]) == RESULT_SIZE_LIMIT_BYTES + 10


# -- listagem ---------------------------------------------------------------


def test_query_antiga_so_falha_no_data_api(seed_volumoso, db_backend):
    """Reproduz o bug de producao: 30 x 120 KB passa no pymysql (dev) e estoura
    1 MiB no caminho Data API."""
    user_id = seed_volumoso(30)
    conn = cr._connect()
    try:
        with conn.cursor() as cur:
            if db_backend == "pymysql":
                cur.execute(OLD_LIST_SQL, (user_id, 200))
                assert len(cur.fetchall()) == 30
            else:
                with pytest.raises(Exception, match="UnsupportedResultException"):
                    cur.execute(OLD_LIST_SQL, (user_id, 200))
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def test_listagem_nova_passa_com_snapshots_volumosos(seed_volumoso, db_backend, fake_rds):
    user_id = seed_volumoso(30)

    items = cr.list_cardio_recommendations(
        days=None, limit=200, offset=0, workout_plan_id=None, identity=_identity(user_id)
    )["items"]

    assert len(items) == 30
    assert all(item["context_snapshot"] is None for item in items)
    assert [c for c in fake_rds.calls if c.error_code] == []


def test_teto_de_100_itens_cabe_com_markdown_de_8kb(seed_volumoso, db_backend, fake_rds):
    user_id = seed_volumoso(cr.LIST_MAX_LIMIT + 20, markdown_bytes=8 * 1024)

    items = cr.list_cardio_recommendations(
        days=None, limit=1000, offset=0, workout_plan_id=None, identity=_identity(user_id)
    )["items"]

    assert len(items) == cr.LIST_MAX_LIMIT
    assert [c for c in fake_rds.calls if c.error_code] == []


def test_detalhe_com_snapshot_volumoso_segue_completo(seed_volumoso, db_backend):
    user_id = seed_volumoso(1)
    entry_id = cr.list_cardio_recommendations(
        days=None, limit=1, offset=0, workout_plan_id=None, identity=_identity(user_id)
    )["items"][0]["id"]

    detail = cr.get_cardio_recommendation(entry_id, identity=_identity(user_id))

    snapshot = detail["context_snapshot"]
    snapshot = json.loads(snapshot) if isinstance(snapshot, (str, bytes)) else snapshot
    assert len(snapshot["blob"]) == SNAPSHOT_BYTES

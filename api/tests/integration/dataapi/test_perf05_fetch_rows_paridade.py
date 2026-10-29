"""PERF-05: `db.fetch_rows` (leitura de um statement sem transacao explicita)
nos dois backends (PyMySQL e shim RDS Data API + duble), sobre MySQL 8.

- mesmas linhas e mesmos tipos que a leitura por `get_conn()` (o caminho de
  antes), nos dois backends;
- na Data API: 1 `ExecuteStatement` sem `transactionId`, nenhum
  Begin/Commit/RollbackTransaction;
- ve o que ja foi commitado por outra conexao (mesma visibilidade de uma
  transacao nova por consulta) e nao ve o que ainda nao foi;
- `IntelligenceServiceBase.fetch_all` devolve o mesmo que antes.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

import pytest

from app import db_dataapi, db_pymysql  # noqa: F401 - importados antes da troca de backend
from app.intelligence.base import IntelligenceServiceBase

SQL = "SELECT id, username, role, created_at FROM users WHERE username LIKE %s ORDER BY id"


@pytest.fixture
def users(raw):
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        cur.execute("TRUNCATE TABLE `users`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        for name in ("perf05-a", "perf05-b"):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'regular')", (name,))


def _via_transacao(sql, params):
    import app.db as db

    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall() or [])
    finally:
        conn.close()


def test_mesmas_linhas_e_tipos_que_a_leitura_com_transacao(db_backend, users):
    import app.db as db

    antes = _via_transacao(SQL, ("perf05-%",))
    depois = db.fetch_rows(SQL, ("perf05-%",))

    assert depois == antes
    assert [r["username"] for r in depois] == ["perf05-a", "perf05-b"]
    assert all(isinstance(r["created_at"], _dt.datetime) for r in depois)


def test_dataapi_uma_chamada_sem_transacao(dataapi_db, users):
    import app.db as db

    dataapi_db.calls.clear()
    db.fetch_rows(SQL, ("perf05-%",))

    assert [c.operation for c in dataapi_db.calls] == ["ExecuteStatement"]
    assert "transactionId" not in dataapi_db.calls[0].params
    assert dataapi_db.open_transactions == []


def test_visibilidade_igual_a_de_uma_transacao_nova(db_backend, users, raw):
    import app.db as db

    writer = db.get_conn()
    try:
        with writer.cursor() as cur:
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('perf05-c', 'x', 'regular')")
        # Nao commitado: invisivel nos dois caminhos.
        assert [r["username"] for r in db.fetch_rows(SQL, ("perf05-c",))] == []
        assert _via_transacao(SQL, ("perf05-c",)) == []
        writer.commit()
    finally:
        writer.close()
    assert db.fetch_rows(SQL, ("perf05-c",)) == _via_transacao(SQL, ("perf05-c",))
    assert len(db.fetch_rows(SQL, ("perf05-c",))) == 1


def test_fetch_all_dos_servicos(db_backend, users):
    rows = IntelligenceServiceBase().fetch_all(
        "SELECT username, CAST(1.50 AS DECIMAL(4,2)) AS v, created_at FROM users WHERE username = %s",
        ("perf05-a",),
    )

    assert rows[0]["username"] == "perf05-a"
    assert rows[0]["v"] == 1.5 and not isinstance(rows[0]["v"], Decimal)
    assert isinstance(rows[0]["created_at"], str)

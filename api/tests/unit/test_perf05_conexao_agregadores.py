"""PERF-05 (auditoria 2026-10-08): menos chamadas a Data API e blocos de leitura
em paralelo, mesma resposta.

- `db.fetch_rows` (dois backends): leitura de UM statement sem transacao
  explicita - na Data API, 1 ExecuteStatement sem `transactionId` em vez de
  BeginTransaction + ExecuteStatement + RollbackTransaction; recusa DML.
- `IntelligenceServiceBase.fetch_all` passa por `fetch_rows`.
- `parallel_reads`: ordem dos resultados, primeira excecao na ordem declarada,
  contexto da requisicao (`request_memo`) visivel nas threads, conexao propria
  por bloco.
- `build_overview` com `conn_factory` devolve o MESMO payload do caminho
  sequencial; o cooldown (escrita) continua na conexao da rota.

Unit (sem MySQL, sem AWS). A medicao no DEV esta no PR.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app import db_dataapi, db_pymysql, parallel_reads, request_memo
from app.intelligence import base as intelligence_base
from app.intelligence.dashboard import overview_service as ov
from tests.unit.test_perf02_perf04_leituras import _kwargs, overview_stubs  # noqa: F401 - fixture

# ---------------------------------------------------------------------------
# fetch_rows - Data API
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def begin_transaction(self, **kw):
        self.calls.append(("begin_transaction", kw))
        return {"transactionId": "tx-1"}

    def execute_statement(self, **kw):
        self.calls.append(("execute_statement", kw))
        return {
            "columnMetadata": [{"name": "id", "label": "id", "typeName": "BIGINT"}],
            "records": [[{"longValue": 7}], [{"longValue": 8}]],
        }

    def rollback_transaction(self, **kw):
        self.calls.append(("rollback_transaction", kw))
        return {}

    def commit_transaction(self, **kw):
        self.calls.append(("commit_transaction", kw))
        return {}


@pytest.fixture
def dataapi(monkeypatch):
    client = _Client()
    monkeypatch.setattr(db_dataapi, "boto3", SimpleNamespace(client=lambda *_a, **_kw: client))
    monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:sa-east-1:000000000000:cluster:unit")
    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:aws:secretsmanager:sa-east-1:000000000000:secret:unit")
    monkeypatch.setenv("AURORA_DATABASE", "unit")
    return client


def test_dataapi_fetch_rows_e_uma_chamada_sem_transacao(dataapi):
    rows = db_dataapi.fetch_rows("SELECT id FROM t WHERE user_id = %s", (1,))

    assert rows == [{"id": 7}, {"id": 8}]
    assert [name for name, _ in dataapi.calls] == ["execute_statement"]
    _, kw = dataapi.calls[0]
    assert "transactionId" not in kw
    assert kw["sql"] == "SELECT id FROM t WHERE user_id = :p0"


def test_dataapi_get_conn_continua_abrindo_transacao(dataapi):
    conn = db_dataapi.get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM t")
    conn.close()

    assert [name for name, _ in dataapi.calls] == ["begin_transaction", "execute_statement", "rollback_transaction"]
    assert dataapi.calls[1][1]["transactionId"] == "tx-1"


@pytest.mark.parametrize("module", [db_dataapi, db_pymysql], ids=["dataapi", "pymysql"])
@pytest.mark.parametrize(
    "sql",
    ["INSERT INTO t VALUES (1)", "  update t set a = 1", "DELETE FROM t", "REPLACE INTO t VALUES (1)", "SET @a = 1"],
)
def test_fetch_rows_recusa_escrita_antes_de_ir_ao_banco(module, sql, monkeypatch):
    def boom(*_a, **_kw):  # pragma: no cover - nao pode ser chamado
        raise AssertionError("nao deveria abrir conexao")

    monkeypatch.setattr(module, "Connection", boom)
    with pytest.raises(module.DataApiError, match="so leitura"):
        module.fetch_rows(sql)


@pytest.mark.parametrize("module", [db_dataapi, db_pymysql], ids=["dataapi", "pymysql"])
@pytest.mark.parametrize("sql", ["SELECT 1", "\n  select 1", "WITH x AS (SELECT 1) SELECT * FROM x", "SHOW TABLES", "(SELECT 1)"])
def test_fetch_rows_aceita_leitura(module, sql):
    module._require_read(sql)


def test_pymysql_fetch_rows_abre_em_autocommit_e_fecha(monkeypatch):
    seen = {}

    class _Raw:
        def cursor(self):
            return SimpleNamespace(execute=lambda sql, args: seen.update(sql=sql, args=args), fetchall=lambda: ({"id": 1},))

        def close(self):
            seen["closed"] = True

        def rollback(self):  # pragma: no cover - autocommit: nada a reverter
            raise AssertionError("fetch_rows nao reverte")

    def fake_connect(**kw):
        seen["autocommit"] = kw["autocommit"]
        return _Raw()

    monkeypatch.setattr(db_pymysql.pymysql, "connect", fake_connect)
    rows = db_pymysql.fetch_rows("SELECT id FROM t WHERE a = %s", (2,))

    assert rows == [{"id": 1}]
    assert seen == {"autocommit": True, "sql": "SELECT id FROM t WHERE a = %s", "args": [2], "closed": True}


def test_fetch_all_dos_servicos_usa_fetch_rows(monkeypatch):
    from decimal import Decimal

    import app.db as db

    seen = []
    monkeypatch.setattr(db, "fetch_rows", lambda sql, params: seen.append((sql, params)) or [{"v": Decimal("1.5")}])

    rows = intelligence_base.IntelligenceServiceBase().fetch_all("SELECT v FROM t WHERE id = %s", (3,))

    assert seen == [("SELECT v FROM t WHERE id = %s", (3,))]
    assert rows == [{"v": 1.5}]


# ---------------------------------------------------------------------------
# parallel_reads
# ---------------------------------------------------------------------------


def test_run_parallel_roda_junto_e_devolve_na_ordem():
    barrier = threading.Barrier(3, timeout=5)

    def task(value):
        def run():
            barrier.wait()  # so passa se as 3 estiverem rodando ao mesmo tempo
            return value

        return run

    assert parallel_reads.run_parallel([task("a"), task("b"), task("c")]) == ["a", "b", "c"]


def test_run_parallel_sobe_a_primeira_excecao_na_ordem_declarada():
    finished = []

    def ok():
        finished.append("ok")
        return 1

    def fails(name):
        def run():
            raise ValueError(name)

        return run

    with pytest.raises(ValueError, match="primeira"):
        parallel_reads.run_parallel([ok, fails("primeira"), fails("segunda")])
    assert finished == ["ok"]


def test_run_parallel_enxerga_o_memo_da_requisicao():
    loads = []

    def read():
        return request_memo.memoize(("perf05_teste", 1), lambda: loads.append(1) or {"v": 1})

    with request_memo.request_scope():
        request_memo.memoize(("perf05_teste", 1), lambda: loads.append(1) or {"v": 1})
        results = parallel_reads.run_parallel([read, read])

    assert results == [{"v": 1}, {"v": 1}]
    assert loads == [1]  # lido uma vez, antes; as threads reaproveitaram


def test_run_blocks_sem_fabrica_e_sequencial_com_a_conexao_recebida():
    order = []
    conn = object()

    def block(name):
        def run(c):
            order.append((name, c is conn, threading.current_thread() is threading.main_thread()))
            return name

        return run

    assert parallel_reads.run_blocks([block("a"), block("b")], conn=conn) == ["a", "b"]
    assert order == [("a", True, True), ("b", True, True)]


def test_run_blocks_com_fabrica_da_conexao_propria_a_cada_bloco():
    opened, closed = [], []

    class _Conn:
        def __enter__(self):
            opened.append(self)
            return self

        def __exit__(self, *exc):
            closed.append(self)

    results = parallel_reads.run_blocks([lambda c: c, lambda c: c, lambda c: c], conn=object(), conn_factory=_Conn)

    assert len(set(map(id, results))) == 3
    assert sorted(map(id, opened)) == sorted(map(id, results)) == sorted(map(id, closed))


# ---------------------------------------------------------------------------
# build_overview: paralelo == sequencial
# ---------------------------------------------------------------------------


def test_overview_paralelo_tem_o_mesmo_payload_e_cooldown_na_conexao_da_rota(overview_stubs, monkeypatch):  # noqa: F811
    route_conn = object()
    cooldown_conns = []
    block_conns = []
    original_cooldown = ov.apply_cooldown
    original_adherence = ov.build_adherence

    def spy_cooldown(conn, **k):
        cooldown_conns.append(conn)
        return original_cooldown(conn, **k)

    def spy_adherence(conn, **k):
        block_conns.append(conn)
        return original_adherence(conn, **k)

    monkeypatch.setattr(ov, "apply_cooldown", spy_cooldown)
    monkeypatch.setattr(ov, "build_adherence", spy_adherence)

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    sequencial = ov.build_overview(route_conn, **_kwargs())
    paralelo = ov.build_overview(route_conn, conn_factory=_Conn, **_kwargs())

    sequencial.pop("_timings")
    paralelo.pop("_timings")
    assert paralelo == sequencial
    assert overview_stubs["ctx"][0] == overview_stubs["ctx"][1]
    assert cooldown_conns == [route_conn, route_conn]
    assert block_conns[0] is route_conn and isinstance(block_conns[1], _Conn)


def test_insights_repassa_a_fabrica(overview_stubs, monkeypatch):  # noqa: F811
    seen = []
    original = ov.build_overview

    def spy(conn, **k):
        seen.append(k.get("conn_factory"))
        return original(conn, **k)

    monkeypatch.setattr(ov, "build_overview", spy)
    factory = object()
    monkeypatch.setattr(ov, "run_blocks", lambda blocks, conn, conn_factory: [b(conn) for b in blocks])
    ov.build_insights(object(), conn_factory=factory, **_kwargs())

    assert seen == [factory]

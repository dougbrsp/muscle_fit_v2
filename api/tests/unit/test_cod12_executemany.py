"""COD-12: `Cursor.executemany` nos dois shims e os caminhos de escrita em lote.

Unit (sem MySQL, sem AWS). O caminho real nos dois backends (PyMySQL e Data
API via dublê) fica nos testes de integracao que ja importam e editam treino
(`tests/integration/dataapi/test_sessoes_fichas_sem_ddl_paridade.py`) e
sincronizam o Intervals (`test_m52_m53_intervals_dado_velho_e_cache.py`).
"""

from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal
from types import SimpleNamespace

import pymysql
import pymysql.cursors
import pytest
from botocore.exceptions import ClientError

from app import db_dataapi, db_pymysql, intervals_sync, services
from tests.support.fake_db import fake_transaction

# ---------------------------------------------------------------------------
# Data API: BatchExecuteStatement
# ---------------------------------------------------------------------------


class _BatchClient:
    def __init__(self, errors: list[Exception] | None = None):
        self.batches: list[dict] = []
        self._errors = list(errors or [])

    def begin_transaction(self, **_kw):
        return {"transactionId": "tx-1"}

    def batch_execute_statement(self, **kw):
        if self._errors:
            raise self._errors.pop(0)
        self.batches.append(kw)
        return {"updateResults": [{"generatedFields": []} for _ in kw["parameterSets"]]}

    def execute_statement(self, **_kw):  # pragma: no cover - nao deve ser usado
        raise AssertionError("executemany nao pode cair em execute_statement")

    def rollback_transaction(self, **_kw):
        return {}

    def commit_transaction(self, **_kw):
        return {}


@pytest.fixture
def dataapi(monkeypatch):
    holder = SimpleNamespace(client=_BatchClient(), sleeps=[])
    monkeypatch.setattr(db_dataapi, "boto3", SimpleNamespace(client=lambda *_a, **_kw: holder.client))
    monkeypatch.setattr(db_dataapi, "_sleep", holder.sleeps.append)
    monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:sa-east-1:000000000000:cluster:unit")
    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:aws:secretsmanager:sa-east-1:000000000000:secret:unit")
    monkeypatch.setenv("AURORA_DATABASE", "unit")
    return holder


SQL = "INSERT INTO t (a, b, c) VALUES (%s, %s, %s)"


def _values(batch: dict) -> list[list]:
    return [[next(iter(p["value"].values())) for p in ps] for ps in batch["parameterSets"]]


def test_um_lote_na_transacao_na_ordem_e_com_os_tipos_do_execute(dataapi):
    cur = db_dataapi.get_conn().cursor()
    rows = [(1, "x", None), (2, Decimal("1.50"), _dt.date(2026, 10, 1)), (3, {"k": "á"}, True)]

    cur.executemany(SQL, rows)

    [batch] = dataapi.client.batches
    assert batch["transactionId"] == "tx-1"
    assert batch["sql"] == "INSERT INTO t (a, b, c) VALUES (:p0, :p1, :p2)"
    assert "includeResultMetadata" not in batch
    # Cada linha vira exatamente o que `execute()` mandaria (mesmo conversor).
    assert batch["parameterSets"] == [db_dataapi._translate_sql(SQL, row)[1] for row in rows]
    assert _values(batch) == [[1, "x", True], [2, "1.50", "2026-10-01"], [3, '{"k": "á"}', True]]
    assert (cur.lastrowid, cur.rowcount, cur.description) == (None, -1, None)


def test_sem_linhas_nao_chama_o_servico(dataapi):
    cur = db_dataapi.get_conn().cursor()
    cur.executemany(SQL, [])
    assert dataapi.client.batches == []
    assert (cur.lastrowid, cur.rowcount) == (None, -1)


def test_lotes_por_numero_de_linhas_preservam_a_ordem(dataapi, monkeypatch):
    monkeypatch.setattr(db_dataapi, "BATCH_MAX_PARAMETER_SETS", 2)
    db_dataapi.get_conn().cursor().executemany(SQL, [(i, "v", None) for i in range(5)])

    assert [len(b["parameterSets"]) for b in dataapi.client.batches] == [2, 2, 1]
    assert [v[0] for b in dataapi.client.batches for v in _values(b)] == [0, 1, 2, 3, 4]


def test_lotes_por_tamanho_de_payload(dataapi, monkeypatch):
    linha = db_dataapi._translate_sql(SQL, (0, "y" * 1000, None))[1]
    por_linha = db_dataapi._estimated_json_bytes(linha) + 1
    base = db_dataapi._estimated_json_bytes(db_dataapi._translate_sql(SQL, (0, "", None))[0])
    monkeypatch.setattr(db_dataapi, "BATCH_PAYLOAD_BUDGET_BYTES", base + 3 * por_linha)

    db_dataapi.get_conn().cursor().executemany(SQL, [(i, "y" * 1000, None) for i in range(7)])

    assert [len(b["parameterSets"]) for b in dataapi.client.batches] == [3, 3, 1]


def test_linha_sozinha_acima_do_orcamento_vai_num_lote_proprio(dataapi, monkeypatch):
    monkeypatch.setattr(db_dataapi, "BATCH_PAYLOAD_BUDGET_BYTES", 10)
    db_dataapi.get_conn().cursor().executemany(SQL, [(1, "a", None), (2, "b", None)])
    assert [len(b["parameterSets"]) for b in dataapi.client.batches] == [1, 1]


def test_lotes_padrao_cabem_no_limite_de_4_mib_da_requisicao(dataapi):
    """Pior caso realista: linhas com texto longo. Cada requisicao serializada
    (o JSON que vai no corpo HTTP) fica bem abaixo dos 4 MiB do servico."""
    assert db_dataapi.BATCH_PAYLOAD_BUDGET_BYTES <= db_dataapi.DATA_API_MAX_REQUEST_BYTES // 4
    rows = [(i, "nota " * 400, Decimal("82.50")) for i in range(3000)]

    db_dataapi.get_conn().cursor().executemany(SQL, rows)

    assert len(dataapi.client.batches) > 1
    for batch in dataapi.client.batches:
        corpo = len(json.dumps(batch).encode("utf-8"))
        assert corpo < db_dataapi.DATA_API_MAX_REQUEST_BYTES // 2
        assert len(batch["parameterSets"]) <= db_dataapi.BATCH_MAX_PARAMETER_SETS
    assert sum(len(b["parameterSets"]) for b in dataapi.client.batches) == 3000


def test_retry_de_banco_despertando_vale_para_o_lote(dataapi):
    erro = ClientError(
        {"Error": {"Code": "DatabaseResumingException", "Message": "paused"}}, "BatchExecuteStatement"
    )
    dataapi.client = _BatchClient(errors=[erro, erro])

    db_dataapi.get_conn().cursor().executemany(SQL, [(1, "a", None)])

    assert len(dataapi.client.batches) == 1
    assert dataapi.sleeps == list(db_dataapi._RESUME_RETRY_WAITS[:2])


def test_erro_do_servico_vira_dataapierror_com_o_sql(dataapi):
    erro = ClientError({"Error": {"Code": "DatabaseErrorException", "Message": "Duplicate entry"}}, "X")
    dataapi.client = _BatchClient(errors=[erro])

    with pytest.raises(db_dataapi.DataApiError) as exc:
        db_dataapi.get_conn().cursor().executemany(SQL, [(1, "a", None)])
    assert "batch_execute_statement falhou" in str(exc.value)
    assert db_dataapi.is_duplicate_key_error(exc.value)


def test_linha_com_parametros_errados_nao_envia_nada(dataapi):
    cur = db_dataapi.get_conn().cursor()
    with pytest.raises(db_dataapi.DataApiError):
        cur.executemany(SQL, [(1, "a", None), (2, "b")])
    with pytest.raises(db_dataapi.DataApiError):
        cur.executemany(SQL, [()])
    assert dataapi.client.batches == []


# ---------------------------------------------------------------------------
# PyMySQL (DEV): mesmo contrato
# ---------------------------------------------------------------------------


class _RawCursor:
    def __init__(self, error: Exception | None = None):
        self.many: list[tuple] = []
        self.error = error
        self.lastrowid = 41
        self.rowcount = 3
        self.description = None

    def executemany(self, sql, args):
        if self.error:
            raise self.error
        self.many.append((sql, args))

    def execute(self, sql, args):
        self.lastrowid, self.rowcount = 99, 1


def _pymysql_cursor(raw: _RawCursor) -> db_pymysql.Cursor:
    return db_pymysql.Cursor(SimpleNamespace(_raw=SimpleNamespace(cursor=lambda: raw)))


def test_pymysql_delega_com_o_mesmo_bind_e_mesmo_contrato():
    raw = _RawCursor()
    cur = _pymysql_cursor(raw)

    cur.executemany(SQL, [(1, {"k": "á"}, _dt.timedelta(hours=1, minutes=5)), (2, "b", None)])

    assert raw.many == [(SQL, [[1, '{"k": "á"}', "01:05:00"], [2, "b", None]])]
    assert (cur.lastrowid, cur.rowcount) == (None, -1)
    cur.execute("SELECT 1")
    assert (cur.lastrowid, cur.rowcount) == (99, 1)


def test_pymysql_erro_vira_dataapierror_e_linhas_vazias_sao_recusadas():
    cur = _pymysql_cursor(_RawCursor(error=pymysql.err.IntegrityError(1062, "Duplicate entry 'x'")))
    with pytest.raises(db_pymysql.DataApiError) as exc:
        cur.executemany(SQL, [(1, "a", None)])
    assert db_pymysql.is_duplicate_key_error(exc.value)

    raw = _RawCursor()
    with pytest.raises(db_pymysql.DataApiError):
        _pymysql_cursor(raw).executemany(SQL, [()])
    _pymysql_cursor(raw).executemany(SQL, [])
    assert raw.many == []


def test_pymysql_parametros_a_menos_ou_a_mais_viram_dataapierror():
    """O PyMySQL estoura TypeError no `sql % args`; o shim da Data API recusa
    com DataApiError. Mesmo tipo de falha nos dois (paridade na integracao)."""
    cur = _pymysql_cursor(_RawCursor(error=TypeError("not enough arguments for format string")))
    with pytest.raises(db_pymysql.DataApiError, match="placeholders"):
        cur.executemany(SQL, [(1, "a")])


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO exercise_sets (exercise_id, set_number) VALUES (%s,%s)",
        intervals_sync._UPSERT_LINK_SQL,
    ],
)
def test_no_dev_o_pymysql_reescreve_em_insert_multilinha(sql):
    """Os INSERTs so com `%s` no VALUES viram UM statement multi-linha no
    PyMySQL (um round-trip no DEV, como o lote na Data API)."""
    assert pymysql.cursors.RE_INSERT_VALUES.match(sql)


# ---------------------------------------------------------------------------
# services.upsert_activity / update_exercise_log
# ---------------------------------------------------------------------------


class _ImportCursor:
    def __init__(self):
        self.calls: list[tuple[str, str, object]] = []
        self._next_id = {"activities": 500, "exercises": 700}
        self.lastrowid = None
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, seq_of_params):
        rows = [tuple(p) for p in seq_of_params]
        self.calls.append(("many", " ".join(sql.split()), rows))

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append(("one", flat, params))
        self._row = None
        for table in self._next_id:
            if flat.startswith(f"INSERT INTO {table} "):
                self._next_id[table] += 1
                self.lastrowid = self._next_id[table]
        if flat.startswith("SELECT id FROM imports"):
            self._row = {"id": 9}

    def fetchone(self):
        return self._row


def _patch_import(monkeypatch, cur):
    monkeypatch.setattr(services, "transaction", fake_transaction(cur))
    monkeypatch.setattr(services, "source_id", lambda conn, name, source_type="api": 1)
    monkeypatch.setattr(services, "resolve_gym_id", lambda cur, *a: None)
    monkeypatch.setattr(services, "resolve_mapping_id", lambda *a, **k: None)
    monkeypatch.setattr(services, "resolve_equipment_id", lambda *a, **k: None)


def _serie(n, **extra):
    return {"set_number": n, "reps": 10, "weight_kg": Decimal("50"), "set_type": "working", **extra}


def test_upsert_activity_grava_zonas_e_series_em_um_lote_cada_na_ordem(monkeypatch):
    cur = _ImportCursor()
    _patch_import(monkeypatch, cur)
    activity = {
        "external_id": "cod12",
        "local_date": "2026-10-01",
        "name": "Treino",
        "hr_zones": [{"zone_number": 1, "duration_seconds": 60}, {"zone_number": 2, "duration_seconds": 30}],
        "exercises": [
            {"sequence": 1, "original_name": "Supino", "sets": [_serie(1), _serie(2)]},
            {"sequence": 2, "original_name": "Remada", "sets": [_serie(1, notes="ok")]},
        ],
    }

    out = services.upsert_activity(activity, user_id=7)

    many = [c for c in cur.calls if c[0] == "many"]
    assert [c[1].split(" (")[0] for c in many] == ["INSERT INTO activity_hr_zones", "INSERT INTO exercise_sets"]
    zonas, series = many[0][2], many[1][2]
    assert [(z[0], z[1], z[5]) for z in zonas] == [(501, 1, 60), (501, 2, 30)]
    # exercise_id vem do lastrowid de cada exercicio; ordem = exercicio, serie.
    assert [(s[0], s[1], s[9]) for s in series] == [(701, 1, None), (701, 2, None), (702, 1, "ok")]
    # Nenhum INSERT de serie/zona sobrou linha a linha.
    assert not [c for c in cur.calls if c[0] == "one" and ("exercise_sets" in c[1] or "activity_hr_zones" in c[1])
                and c[1].startswith("INSERT")]
    # Os exercicios continuam um a um (precisam do id), e antes do lote de series.
    ordem = [c[1].split(" (")[0] for c in cur.calls if c[1].startswith("INSERT INTO")]
    assert ordem.index("INSERT INTO exercise_sets") > max(i for i, t in enumerate(ordem) if t == "INSERT INTO exercises")
    assert out == {"activity_id": 501, "external_id": out["external_id"], "exercises": 2, "sets": 3}


def test_upsert_activity_sem_zonas_nem_series(monkeypatch):
    cur = _ImportCursor()
    _patch_import(monkeypatch, cur)

    out = services.upsert_activity({"external_id": "v", "local_date": "2026-10-01", "name": "Vazio"}, user_id=7)

    assert [c[2] for c in cur.calls if c[0] == "many"] == [[], []]
    assert (out["exercises"], out["sets"]) == (0, 0)

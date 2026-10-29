"""Contrato do shim de producao `app/db_dataapi.py` (via dublê da Data API)
comparado ao backend de dev/CI `app/db_pymysql.py`.

Convencao: testes `test_paridade_*` rodam a MESMA operacao nos dois backends
e exigem resultado identico (tipo + valor). Divergencia real conhecida fica
registrada como `xfail(strict=True)` com o motivo - se alguem corrigir a
divergencia, o xfail passa a "XPASS" e quebra o CI, forcando atualizar o teste.
Testes `test_dataapi_*` fixam o comportamento do caminho de producao em si.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from app import db_dataapi, db_pymysql, migrate

TABLE = "dataapi_contrato"
API_DIR = Path(__file__).resolve().parents[3]

BACKEND_MODULES = {"pymysql": db_pymysql, "data-api": db_dataapi}


@pytest.fixture
def contrato(raw, dataapi_db):
    with raw.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
        cur.execute(
            f"""
            CREATE TABLE {TABLE} (
              id BIGINT AUTO_INCREMENT PRIMARY KEY,
              flag TINYINT(1) NULL,
              small TINYINT NULL,
              big BIGINT NULL,
              dec_col DECIMAL(6,2) NULL,
              dbl DOUBLE NULL,
              dt DATETIME NULL,
              da DATE NULL,
              tm TIME NULL,
              ts TIMESTAMP NULL,
              js JSON NULL,
              bl BLOB NULL,
              txt VARCHAR(100) NULL,
              uq VARCHAR(20) NULL UNIQUE
            ) ENGINE=InnoDB
            """
        )
    yield dataapi_db
    with raw.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")


def _read_one(module, sql, params=None):
    conn = module.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()
    finally:
        conn.close()


def _xfail(reason):
    return pytest.mark.xfail(strict=True, reason=reason)


# ---------------------------------------------------------------------------
# 1. Tipos na LEITURA
# ---------------------------------------------------------------------------

SEED_ROW = (
    "INSERT INTO dataapi_contrato (flag, small, big, dec_col, dbl, dt, da, tm, ts, js, bl, txt) VALUES "
    "(1, 5, 9007199254740993, 7.5, 2.25, '2026-09-10 08:00:00', '2026-09-10', '09:30:00', "
    "'2026-09-10 08:00:00', '{\"a\": 1}', x'00ff', 'olá')"
)


def test_dataapi_leitura_converte_os_campos_da_data_api_para_os_tipos_do_pymysql(contrato, raw):
    """A Data API devolve DECIMAL/DATE/DATETIME/TIMESTAMP/TIME como
    stringValue e TINYINT(1) como booleanValue (medido na Aurora real); o shim
    converte pelo typeName para os tipos que o PyMySQL entrega."""
    with raw.cursor() as cur:
        cur.execute(SEED_ROW)
        cur.execute(f"INSERT INTO {TABLE} (txt) VALUES (NULL)")
    conn = db_dataapi.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT flag, small, big, dec_col, dbl, dt, da, tm, ts, js, bl, txt FROM {TABLE} ORDER BY id")
        first, second = cur.fetchall()
    finally:
        conn.close()
    raw_record = contrato.calls_of("ExecuteStatement")[-1]
    assert raw_record.params["includeResultMetadata"] is True
    assert first == {
        "flag": 1,
        "small": 5,
        "big": 9007199254740993,
        "dec_col": Decimal("7.50"),
        "dbl": 2.25,
        "dt": dt.datetime(2026, 9, 10, 8, 0),
        "da": dt.date(2026, 9, 10),
        "tm": dt.timedelta(hours=9, minutes=30),
        "ts": dt.datetime(2026, 9, 10, 8, 0),
        "js": '{"a": 1}',
        "bl": b"\x00\xff",
        "txt": "olá",
    }
    assert [type(v) for v in first.values()] == [
        int, int, int, Decimal, float, dt.datetime, dt.date, dt.timedelta, dt.datetime, str, bytes, str
    ]
    assert set(second.values()) == {None}


READ_CASES = [
    pytest.param("small", id="TINYINT"),
    pytest.param("big", id="BIGINT"),
    pytest.param("dbl", id="DOUBLE"),
    pytest.param("js", id="JSON"),
    pytest.param("bl", id="BLOB"),
    pytest.param("txt", id="VARCHAR-utf8"),
    pytest.param("flag", id="TINYINT(1)"),
    pytest.param("dec_col", id="DECIMAL"),
    pytest.param("dt", id="DATETIME"),
    pytest.param("da", id="DATE"),
    pytest.param("tm", id="TIME"),
    pytest.param("ts", id="TIMESTAMP"),
]


EXPRESSION_CASES = [
    # Mesmas expressoes medidas na Aurora real (ver fake_rds_data.py) + casos de borda do conversor.
    pytest.param("CAST(1.50 AS DECIMAL(5,2))", id="DECIMAL-cast"),
    pytest.param("CAST('2026-09-14 10:11:12.345678' AS DATETIME(6))", id="DATETIME(6)"),
    pytest.param("CAST('2026-09-14 10:11:12.3' AS DATETIME(1))", id="DATETIME(1)"),
    pytest.param("CAST('2026-09-14' AS DATE)", id="DATE-cast"),
    pytest.param("CAST('10:11:12' AS TIME)", id="TIME-cast"),
    pytest.param("CAST('-838:59:59' AS TIME)", id="TIME-negativo-limite"),
    pytest.param("CAST('26:00:01.5' AS TIME(1))", id="TIME-acima-de-24h-fracao"),
    pytest.param("TRUE", id="literal-TRUE"),
    pytest.param("CAST(42 AS UNSIGNED)", id="BIGINT-UNSIGNED"),
    pytest.param("3.5e0", id="DOUBLE-literal"),
    pytest.param("JSON_OBJECT('a', 1)", id="JSON_OBJECT"),
    pytest.param("SUM(CAST(1.25 AS DECIMAL(5,2)))", id="SUM-DECIMAL"),
    pytest.param("AVG(2)", id="AVG"),
    pytest.param("COUNT(*)", id="COUNT"),
    pytest.param("NULL", id="NULL"),
]


@pytest.mark.parametrize("expression", EXPRESSION_CASES)
def test_paridade_leitura_por_expressao(contrato, expression):
    values = {name: _read_one(module, f"SELECT {expression} AS v")["v"] for name, module in BACKEND_MODULES.items()}
    assert (type(values["data-api"]), values["data-api"]) == (type(values["pymysql"]), values["pymysql"])


@pytest.mark.parametrize("column", READ_CASES)
def test_paridade_leitura_por_tipo(contrato, raw, column):
    with raw.cursor() as cur:
        cur.execute(SEED_ROW)
    values = {
        name: _read_one(module, f"SELECT {column} AS v FROM {TABLE}")["v"] for name, module in BACKEND_MODULES.items()
    }
    assert (type(values["data-api"]), values["data-api"]) == (type(values["pymysql"]), values["pymysql"])


def test_paridade_null_em_todos_os_tipos(contrato, raw):
    with raw.cursor() as cur:
        cur.execute(f"INSERT INTO {TABLE} (txt) VALUES (NULL)")
    cols = "flag, small, big, dec_col, dbl, dt, da, tm, ts, js, bl, txt"
    rows = {name: _read_one(module, f"SELECT {cols} FROM {TABLE}") for name, module in BACKEND_MODULES.items()}
    assert rows["data-api"] == rows["pymysql"]
    assert set(rows["data-api"].values()) == {None}


# ---------------------------------------------------------------------------
# 1. Tipos no BIND (escrita via backend -> leitura neutra)
# ---------------------------------------------------------------------------

BIND_CASES = [
    pytest.param("dec_col", Decimal("7.25"), id="Decimal"),
    pytest.param("dt", dt.datetime(2026, 9, 10, 8, 15, 30), id="datetime"),
    pytest.param("ts", dt.datetime(2026, 9, 10, 8, 15, 30), id="datetime-timestamp"),
    pytest.param("da", dt.date(2026, 9, 10), id="date"),
    pytest.param("tm", dt.time(9, 30), id="time"),
    pytest.param("txt", None, id="None"),
    pytest.param("flag", True, id="bool"),
    pytest.param("small", 7, id="int"),
    pytest.param("big", 2**62, id="int-grande"),
    pytest.param("dbl", 0.125, id="float"),
    # dict/list/tuple: comportamento de producao (texto JSON) aplicado tambem no
    # db_pymysql (antes: TypeError / expansao em tupla SQL so em dev).
    pytest.param("js", {"plano": 3, "nome": "Força"}, id="dict-JSON"),
    pytest.param("js", [1, "dois"], id="list-JSON"),
    pytest.param("bl", b"\x00\x01\xff", id="bytes"),
    pytest.param("txt", "olá % :nome '%s'", id="str-com-%-e-:"),
    pytest.param("dec_col", "3.10", id="str-em-DECIMAL"),
    # Shim envia milissegundos truncados (formato documentado [.FFF]); o MySQL
    # arredonda DATETIME(0)/TIME(0) igual ao que faz com os microssegundos do PyMySQL.
    pytest.param("dt", dt.datetime(2026, 9, 10, 8, 15, 30, 600000), id="datetime-com-microssegundos"),
    pytest.param("dt", dt.datetime(2026, 9, 10, 8, 15, 30, 499999), id="datetime-499999us"),
    pytest.param("dt", dt.datetime(2026, 9, 10, 23, 59, 59, 999999), id="datetime-virada-de-dia"),
    pytest.param("tm", dt.time(9, 30, 0, 500000), id="time-com-microssegundos"),
    pytest.param("tm", dt.timedelta(hours=9, minutes=30), id="timedelta-em-TIME"),
    pytest.param("tm", dt.timedelta(hours=-1, minutes=-5), id="timedelta-negativo"),
    pytest.param("tm", dt.timedelta(days=1, hours=2, seconds=1), id="timedelta-acima-de-24h"),
]


def _neutral_value(raw, column):
    with raw.cursor() as cur:
        cur.execute(f"SELECT {column} AS v FROM {TABLE}")
        return cur.fetchone()["v"]


def _write(module, column, value):
    with module.transaction() as conn:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO {TABLE} ({column}) VALUES (%s)", (value,))


@pytest.mark.parametrize("column, value", BIND_CASES)
def test_paridade_bind_por_tipo(contrato, raw, column, value):
    stored = {}
    for name, module in BACKEND_MODULES.items():
        with raw.cursor() as cur:
            cur.execute(f"DELETE FROM {TABLE}")
        try:
            _write(module, column, value)
            stored[name] = ("ok", _neutral_value(raw, column))
        except Exception as exc:  # noqa: BLE001 - a divergencia pode ser um erro
            stored[name] = ("erro", type(exc).__name__)
    assert stored["data-api"] == stored["pymysql"]
    assert stored["data-api"][0] == "ok"


def test_dataapi_bind_usa_type_hint_da_documentacao(contrato):
    conn = db_dataapi.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO {TABLE} (dec_col, dt, da, tm, js, flag, big, dbl, bl, txt) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (Decimal("1.50"), dt.datetime(2026, 1, 2, 3, 4, 5), dt.date(2026, 1, 2), dt.time(3, 4, 5), {"a": 1}, False, 10, 1.5, b"x", None),
        )
        conn.commit()
    finally:
        conn.close()
    params = contrato.calls_of("ExecuteStatement")[-1].params["parameters"]
    assert [(p["name"], p.get("typeHint"), next(iter(p["value"]))) for p in params] == [
        ("p0", "DECIMAL", "stringValue"),
        ("p1", "TIMESTAMP", "stringValue"),
        ("p2", "DATE", "stringValue"),
        ("p3", "TIME", "stringValue"),
        ("p4", "JSON", "stringValue"),
        ("p5", None, "booleanValue"),
        ("p6", None, "longValue"),
        ("p7", None, "doubleValue"),
        ("p8", None, "blobValue"),
        ("p9", None, "isNull"),
    ]
    assert params[1]["value"]["stringValue"] == "2026-01-02 03:04:05"


@pytest.mark.parametrize("params", [None, ()], ids=["None", "tupla-vazia"])
def test_paridade_percent_duplo_sem_parametros(contrato, params):
    """`%%` sem parametros vira `%` nos dois backends (db_pymysql alinhado ao shim;
    antes o PyMySQL devolvia '100%%' com None e '100%' com ())."""
    values = {name: _read_one(module, "SELECT '100%%' AS v", params)["v"] for name, module in BACKEND_MODULES.items()}
    assert values["data-api"] == values["pymysql"] == "100%"


def test_paridade_percent_solto_sem_parametros(contrato):
    """`%` solto sem parametros: o shim sempre aceitou; o PyMySQL com `()`
    levantava TypeError (not enough arguments)."""
    values = {name: _read_one(module, "SELECT '50%' AS v", ())["v"] for name, module in BACKEND_MODULES.items()}
    assert values["data-api"] == values["pymysql"] == "50%"


def test_paridade_percent_duplo_com_parametros(contrato):
    values = {name: _read_one(module, "SELECT CONCAT('100%%', %s) AS v", ("!",))["v"] for name, module in BACKEND_MODULES.items()}
    assert values["data-api"] == values["pymysql"] == "100%!"


def test_dataapi_numero_de_parametros_errado_da_erro_claro(contrato):
    with pytest.raises(db_dataapi.DataApiError, match="placeholders"):
        _read_one(db_dataapi, "SELECT %s AS a, %s AS b", (1,))
    with pytest.raises(db_dataapi.DataApiError, match="placeholders"):
        _read_one(db_dataapi, "SELECT %s AS a", (1, 2))


# ---------------------------------------------------------------------------
# 2. Cursor: rowcount, lastrowid, description, fetch, DictCursor, executemany
# ---------------------------------------------------------------------------


def _cursor_trace(module):
    trace = {}
    with module.transaction() as conn:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO {TABLE} (txt) VALUES (%s)", ("a",))
        trace["insert"] = (cur.rowcount, cur.lastrowid, cur.description)
        cur.execute(f"INSERT INTO {TABLE} (txt) VALUES (%s), (%s)", ("b", "c"))
        trace["insert_multi_rowcount"] = cur.rowcount
        cur.execute(f"UPDATE {TABLE} SET txt = %s WHERE txt IN (%s, %s)", ("a", "a", "b"))
        trace["update_rowcount"] = cur.rowcount
        cur.execute(f"UPDATE {TABLE} SET txt = txt WHERE id = %s", (999,))
        trace["update_nada"] = cur.rowcount
        cur.execute(f"DELETE FROM {TABLE} WHERE txt = %s", ("c",))
        trace["delete_rowcount"] = cur.rowcount
        cur.execute(f"SELECT id, txt AS texto FROM {TABLE} WHERE id > %s ORDER BY id", (0,))
        trace["select_names"] = [d[0] for d in cur.description]
        trace["fetchone"] = cur.fetchone()
        trace["fetchall"] = cur.fetchall()
        trace["fetchone_fim"] = cur.fetchone()
        trace["fetchall_fim"] = cur.fetchall()
        cur.execute(f"SELECT id FROM {TABLE} WHERE id < 0")
        trace["vazio"] = ([d[0] for d in cur.description], cur.fetchall(), cur.fetchone(), cur.rowcount, cur.lastrowid)
        cur.execute(f"UPDATE {TABLE} SET txt = txt WHERE id = %s", (1,))
        # UPDATE sem mudanca real: linhas ENCONTRADAS (Data API medida; pymysql com FOUND_ROWS).
        trace["update_sem_mudanca"] = (cur.rowcount, cur.lastrowid, cur.fetchall())
    return trace


def test_paridade_fetchall_vazio_tipo(contrato):
    """PyMySQL DictCursor devolve `()` sem linhas; o shim agora tambem."""
    values = {}
    for name, module in BACKEND_MODULES.items():
        conn = module.get_conn()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT id FROM {TABLE} WHERE id < 0")
            values[name] = cur.fetchall()
        finally:
            conn.close()
    assert (type(values["data-api"]), values["data-api"]) == (type(values["pymysql"]), values["pymysql"])


def test_paridade_cursor_dml_description_e_fetch(contrato):
    traces = {}
    for name, module in BACKEND_MODULES.items():
        with module.transaction() as conn:
            conn.cursor().execute(f"TRUNCATE TABLE {TABLE}")
        traces[name] = _cursor_trace(module)
    assert traces["data-api"] == traces["pymysql"]
    assert traces["data-api"]["insert"] == (1, 1, None)
    # `SET txt = 'a' WHERE txt IN ('a', 'b')`: 2 linhas ENCONTRADAS (1 alterada).
    # Aurora conta encontradas (medido); db_pymysql usa CLIENT.FOUND_ROWS. Antes: 1.
    assert traces["data-api"]["update_rowcount"] == 2
    assert traces["data-api"]["fetchone"] == {"id": 1, "texto": "a"}
    assert traces["data-api"]["update_nada"] == 0
    assert traces["data-api"]["update_sem_mudanca"] == (1, 0, [])  # fetchall apos DML: [] no PyMySQL
    assert traces["data-api"]["vazio"] == (["id"], (), None, 0, None)


def test_paridade_iteracao_do_cursor(contrato, raw):
    with raw.cursor() as cur:
        cur.execute(f"INSERT INTO {TABLE} (txt) VALUES ('a'), ('b')")
    rows = {}
    for name, module in BACKEND_MODULES.items():
        conn = module.get_conn()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT txt FROM {TABLE} ORDER BY id")
            rows[name] = [r for r in cur]
        finally:
            conn.close()
    assert rows["data-api"] == rows["pymysql"] == [{"txt": "a"}, {"txt": "b"}]


def test_paridade_rowcount_apos_select(contrato, raw):
    """SELECT: rowcount = linhas devolvidas nos dois backends (a Data API manda
    numberOfRecordsUpdated = 0; o shim usa len(records))."""
    with raw.cursor() as cur:
        cur.execute(f"INSERT INTO {TABLE} (txt) VALUES ('a'), ('b')")
    counts = {}
    for name, module in BACKEND_MODULES.items():
        conn = module.get_conn()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT txt FROM {TABLE}")
            counts[name] = cur.rowcount
        finally:
            conn.close()
    assert counts["data-api"] == counts["pymysql"]


def test_paridade_lastrowid_nao_vaza_para_o_statement_seguinte(contrato):
    values = {}
    for name, module in BACKEND_MODULES.items():
        with module.transaction() as conn:
            cur = conn.cursor()
            cur.execute(f"INSERT INTO {TABLE} (txt) VALUES ('a')")
            cur.execute(f"UPDATE {TABLE} SET txt = 'b'")
            after_update = cur.lastrowid
            cur.execute(f"SELECT id FROM {TABLE}")
            values[name] = (after_update, cur.lastrowid)
    assert values["data-api"] == values["pymysql"] == (0, None)


def test_paridade_colunas_duplicadas_de_tabelas_distintas_sem_alias(contrato, raw):
    """Nome repetido recebe prefixo `<tabela>.` nos dois backends (antes o shim
    sobrescrevia e devolvia so o ULTIMO valor em 'id')."""
    with raw.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS dataapi_contrato_filho")
        cur.execute("CREATE TABLE dataapi_contrato_filho (id BIGINT PRIMARY KEY, pai_id BIGINT)")
        cur.execute(f"INSERT INTO {TABLE} (txt) VALUES ('a')")
        cur.execute("INSERT INTO dataapi_contrato_filho (id, pai_id) VALUES (77, 1)")
    try:
        rows = {
            name: _read_one(
                module,
                f"SELECT {TABLE}.id, dataapi_contrato_filho.id FROM {TABLE} "
                f"JOIN dataapi_contrato_filho ON dataapi_contrato_filho.pai_id = {TABLE}.id",
            )
            for name, module in BACKEND_MODULES.items()
        }
    finally:
        with raw.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS dataapi_contrato_filho")
    assert rows["data-api"] == rows["pymysql"] == {"id": 1, "dataapi_contrato_filho.id": 77}


@_xfail(
    "LIMITE DA DATA API: mesma tabela com ALIAS (`JOIN t AS x`) - o PyMySQL prefixa com o alias ('x.id') e a "
    "Data API so expoe a tabela ORIGINAL em columnMetadata.tableName (Connector/J getTableName), entao o shim "
    "gera 'dataapi_contrato.id'. O valor de 'id' (primeira coluna) ja e igual. Sempre usar alias de coluna em JOIN"
)
def test_paridade_colunas_duplicadas_sem_alias(contrato, raw):
    with raw.cursor() as cur:
        cur.execute(f"INSERT INTO {TABLE} (txt) VALUES ('a')")
    rows = {
        name: _read_one(module, f"SELECT {TABLE}.id, t.id FROM {TABLE} JOIN {TABLE} t ON t.id = {TABLE}.id")
        for name, module in BACKEND_MODULES.items()
    }
    assert rows["data-api"]["id"] == rows["pymysql"]["id"]
    assert rows["data-api"] == rows["pymysql"]


EXECUTEMANY_SQL = f"INSERT INTO {TABLE} (txt, dec_col, dt, js) VALUES (%s, %s, %s, %s)"
EXECUTEMANY_ROWS = [
    ("a", Decimal("1.50"), dt.datetime(2026, 10, 1, 7, 30), {"k": "á"}),
    ("b", None, None, None),
    ("olá", Decimal("-2.25"), dt.datetime(2026, 10, 2, 8, 0), [1, 2]),
]


def _executemany_trace(module, rows):
    """`executemany` + o estado do cursor logo depois + as linhas gravadas
    (lidas pelo proprio backend, na ordem do AUTO_INCREMENT)."""
    with module.transaction() as conn:
        conn.cursor().execute(f"TRUNCATE TABLE {TABLE}")
    with module.transaction() as conn:
        cur = conn.cursor()
        returned = cur.executemany(EXECUTEMANY_SQL, rows)
        after = (returned is cur, cur.rowcount, cur.lastrowid, cur.description)
    conn = module.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT id, txt, dec_col, dt, js FROM {TABLE} ORDER BY id")
        return after, list(cur.fetchall())
    finally:
        conn.close()


def test_paridade_executemany_grava_as_mesmas_linhas_na_mesma_ordem(contrato):
    """COD-12: `executemany` existe nos dois backends (Data API via
    BatchExecuteStatement na transacao; PyMySQL via INSERT multi-linha) e grava
    as MESMAS linhas, na ordem recebida, com os mesmos tipos. Contrato do PR:
    depois dele `rowcount` e -1 e `lastrowid` e None nos dois."""
    traces = {name: _executemany_trace(module, EXECUTEMANY_ROWS) for name, module in BACKEND_MODULES.items()}
    assert traces["data-api"] == traces["pymysql"]
    after, rows = traces["data-api"]
    assert after == (True, -1, None, None)
    assert [(r["id"], r["txt"]) for r in rows] == [(1, "a"), (2, "b"), (3, "olá")]
    assert rows[0]["dec_col"] == Decimal("1.50") and rows[0]["dt"] == dt.datetime(2026, 10, 1, 7, 30)
    # Um unico BatchExecuteStatement, DENTRO da transacao, com um parameterSet por linha.
    [batch] = contrato.calls_of("BatchExecuteStatement")
    assert batch.params["transactionId"]
    assert len(batch.params["parameterSets"]) == len(EXECUTEMANY_ROWS)
    assert contrato.calls_of("ExecuteStatement") and not any(
        "INSERT" in call.params["sql"] for call in contrato.calls_of("ExecuteStatement")
    )


def test_paridade_executemany_lista_vazia_nao_faz_nada(contrato):
    traces = {name: _executemany_trace(module, []) for name, module in BACKEND_MODULES.items()}
    assert traces["data-api"] == traces["pymysql"] == ((True, -1, None, None), [])
    assert contrato.calls_of("BatchExecuteStatement") == []


def _executemany_error(module, rows, sql=EXECUTEMANY_SQL):
    """Excecao do `executemany` dentro de `transaction()` + linhas que ficaram."""
    with module.transaction() as conn:
        conn.cursor().execute(f"TRUNCATE TABLE {TABLE}")
    error = None
    try:
        with module.transaction() as conn:
            conn.cursor().executemany(sql, rows)
    except Exception as exc:  # noqa: BLE001 - o tipo e o que se compara
        error = exc
    assert error is not None, f"{module.__name__}.executemany deveria falhar"
    conn = module.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS n FROM {TABLE}")
        left = cur.fetchone()["n"]
    finally:
        conn.close()
    return error, left


EXECUTEMANY_PARAM_ERRORS = [
    pytest.param([("a", None, None, None), ("b", None, None)], id="linha-com-parametros-a-menos"),
    pytest.param([("a", None, None, None), ("b", None, None, None, "extra")], id="linha-com-parametros-a-mais"),
    pytest.param([("a", None, None, None), ()], id="linha-sem-parametros"),
]


@pytest.mark.parametrize("rows", EXECUTEMANY_PARAM_ERRORS)
def test_paridade_executemany_erro_de_parametro_mesmo_tipo_e_nada_gravado(contrato, rows):
    results = {name: _executemany_error(module, rows) for name, module in BACKEND_MODULES.items()}
    for name, (error, left) in results.items():
        assert type(error) is BACKEND_MODULES[name].DataApiError, (name, repr(error))
        assert left == 0, name
    assert contrato.calls_of("BatchExecuteStatement") == []


def test_paridade_executemany_erro_do_banco_mesmo_tipo_e_rollback(contrato):
    """Chave duplicada na 2a linha: DataApiError reconhecida por
    `is_duplicate_key_error` nos dois, e a transacao desfaz a 1a linha."""
    sql = f"INSERT INTO {TABLE} (txt, uq) VALUES (%s, %s)"
    rows = [("a", "dup"), ("b", "dup")]
    results = {name: _executemany_error(module, rows, sql) for name, module in BACKEND_MODULES.items()}
    for name, (error, left) in results.items():
        module = BACKEND_MODULES[name]
        assert type(error) is module.DataApiError, (name, repr(error))
        assert module.is_duplicate_key_error(error), (name, str(error))
        assert left == 0, name


def _error_from_execute(module, *statements):
    """Excecao levantada pelo ULTIMO execute. O close() fica fora da asserção:
    o efeito do close() apos erro e coberto por testes proprios abaixo."""
    conn = module.get_conn()
    try:
        cur = conn.cursor()
        for sql in statements[:-1]:
            cur.execute(sql)
        try:
            cur.execute(statements[-1])
        except module.DataApiError as exc:
            return exc
        raise AssertionError(f"{statements[-1]!r} deveria falhar")
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def test_paridade_helpers_de_erro_reconhecem_a_mensagem_do_mysql(contrato):
    for name, module in BACKEND_MODULES.items():
        dup = _error_from_execute(
            module,
            f"INSERT INTO {TABLE} (uq) VALUES ('dup-{name}')",
            f"INSERT INTO {TABLE} (uq) VALUES ('dup-{name}')",
        )
        assert module.is_duplicate_key_error(dup), name
        col = _error_from_execute(module, f"ALTER TABLE {TABLE} ADD COLUMN txt INT")
        assert module.is_duplicate_column_error(col), name
        idx = _error_from_execute(module, f"ALTER TABLE {TABLE} DROP INDEX nao_existe")
        assert module.is_unknown_key_error(idx), name


def _error_from_transaction_block(module, value):
    try:
        with module.transaction() as conn:
            cur = conn.cursor()
            cur.execute(f"INSERT INTO {TABLE} (uq) VALUES (%s)", (value,))
            cur.execute(f"INSERT INTO {TABLE} (uq) VALUES (%s)", (value,))
    except Exception as exc:  # noqa: BLE001
        return exc
    raise AssertionError("deveria falhar")


@pytest.mark.parametrize("abort_on_error", [False, True], ids=["producao-nao-aborta", "hipotetico-transacao-inexistente"])
def test_paridade_erro_dentro_de_transaction_chega_como_data_api_error(contrato, abort_on_error):
    """Achado B (antes xfail). Cenario real (medido na Aurora de producao): o
    erro de SQL nao aborta a transacao e o rollback funciona. Cenario
    hipotetico (transacao ja inexistente no rollback, ex. expirada): o
    rollback nao mascara o DataApiError original (1062)."""
    contrato.abort_transaction_on_error = abort_on_error
    errors = {name: _error_from_transaction_block(module, f"t-{name}") for name, module in BACKEND_MODULES.items()}
    for name, module in BACKEND_MODULES.items():
        assert isinstance(errors[name], module.DataApiError), (name, type(errors[name]))
        assert module.is_duplicate_key_error(errors[name]), name
    assert contrato.open_transactions == []


# ---------------------------------------------------------------------------
# 3. Transacoes
# ---------------------------------------------------------------------------


def _count(raw):
    with raw.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM {TABLE}")
        return cur.fetchone()["n"]


def test_dataapi_conexao_abre_transacao_no_construtor(contrato):
    conn = db_dataapi.get_conn()
    try:
        assert conn.transaction_id
        assert [c.operation for c in contrato.calls] == ["BeginTransaction"]
        conn.cursor().execute("SELECT 1 AS n")
        assert contrato.calls[-1].params["transactionId"] == conn.transaction_id
    finally:
        conn.close()
    assert [c.operation for c in contrato.calls] == ["BeginTransaction", "ExecuteStatement", "RollbackTransaction"]
    assert contrato.open_transactions == []


@pytest.mark.parametrize("backend", list(BACKEND_MODULES))
def test_paridade_escrita_invisivel_ate_commit(contrato, raw, backend):
    module = BACKEND_MODULES[backend]
    conn = module.get_conn()
    try:
        conn.cursor().execute(f"INSERT INTO {TABLE} (txt) VALUES ('a')")
        assert _count(raw) == 0
        conn.commit()
        assert _count(raw) == 1
    finally:
        conn.close()


@pytest.mark.parametrize("backend", list(BACKEND_MODULES))
def test_paridade_close_sem_commit_descarta(contrato, raw, backend):
    conn = BACKEND_MODULES[backend].get_conn()
    conn.cursor().execute(f"INSERT INTO {TABLE} (txt) VALUES ('a')")
    conn.close()
    assert _count(raw) == 0


@pytest.mark.parametrize("backend", list(BACKEND_MODULES))
def test_paridade_rollback_explicito(contrato, raw, backend):
    conn = BACKEND_MODULES[backend].get_conn()
    try:
        conn.cursor().execute(f"INSERT INTO {TABLE} (txt) VALUES ('a')")
        conn.rollback()
        assert _count(raw) == 0
    finally:
        conn.close()


@pytest.mark.parametrize("backend", list(BACKEND_MODULES))
def test_paridade_with_conn_commit_e_rollback(contrato, raw, backend):
    module = BACKEND_MODULES[backend]
    with module.get_conn() as conn:
        conn.cursor().execute(f"INSERT INTO {TABLE} (txt) VALUES ('ok')")
    with pytest.raises(RuntimeError):
        with module.get_conn() as conn:
            conn.cursor().execute(f"INSERT INTO {TABLE} (txt) VALUES ('descartado')")
            raise RuntimeError("falha no meio")
    with pytest.raises(RuntimeError):
        with module.transaction() as conn:
            conn.cursor().execute(f"INSERT INTO {TABLE} (txt) VALUES ('descartado')")
            raise RuntimeError("falha no meio")
    with module.transaction() as conn:
        conn.cursor().execute(f"INSERT INTO {TABLE} (txt) VALUES ('ok2')")
    with raw.cursor() as cur:
        cur.execute(f"SELECT txt FROM {TABLE} ORDER BY id")
        assert [r["txt"] for r in cur.fetchall()] == ["ok", "ok2"]


def test_dataapi_apos_commit_statements_rodam_em_autocommit(contrato, raw):
    conn = db_dataapi.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO {TABLE} (txt) VALUES ('antes')")
        conn.commit()
        assert conn.transaction_id is None
        cur.execute(f"INSERT INTO {TABLE} (txt) VALUES ('depois')")
        assert "transactionId" not in contrato.calls[-1].params
        assert _count(raw) == 2  # ja visivel, sem commit
        conn.commit()  # no-op
        assert [c.operation for c in contrato.calls].count("CommitTransaction") == 1
    finally:
        conn.close()


@_xfail(
    "db_dataapi.Connection.commit() zera transaction_id e NAO abre nova transacao: writes seguintes sao "
    "autocommit e rollback()/close() nao os desfazem. PyMySQL abre transacao implicita e desfaz. "
    "Call-sites que dao commit no meio (migrate.py, sleep_metrics.py) nao dependem de "
    "rollback posterior; dependencia futura quebraria so em producao"
)
def test_paridade_rollback_depois_de_commit_intermediario(contrato, raw):
    counts = {}
    for name, module in BACKEND_MODULES.items():
        with raw.cursor() as cur:
            cur.execute(f"DELETE FROM {TABLE}")
        conn = module.get_conn()
        try:
            cur = conn.cursor()
            cur.execute(f"INSERT INTO {TABLE} (txt) VALUES ('commitado')")
            conn.commit()
            cur.execute(f"INSERT INTO {TABLE} (txt) VALUES ('deveria-sumir')")
            conn.rollback()
        finally:
            conn.close()
        counts[name] = _count(raw)
    assert counts["data-api"] == counts["pymysql"]


def test_paridade_transacao_sobrevive_a_erro_capturado(contrato, raw):
    """Antes xfail (premissa "Data API aborta a transacao no erro de SQL").
    Medido na Aurora de producao: NAO aborta (1146/1062 seguidos de SELECT 1 e
    rollback OK na mesma transacao) - mesmo comportamento do PyMySQL."""
    counts = {}
    for name, module in BACKEND_MODULES.items():
        with raw.cursor() as cur:
            cur.execute(f"DELETE FROM {TABLE}")
        conn = module.get_conn()
        try:
            cur = conn.cursor()
            cur.execute(f"INSERT INTO {TABLE} (uq) VALUES ('um')")
            try:
                cur.execute(f"INSERT INTO {TABLE} (uq) VALUES ('um')")
            except module.DataApiError:
                pass
            cur.execute(f"INSERT INTO {TABLE} (uq) VALUES ('dois')")
            conn.commit()
        except Exception:  # noqa: BLE001
            pass
        finally:
            conn.close()
        counts[name] = _count(raw)
    assert counts["data-api"] == counts["pymysql"]


def test_dataapi_transacao_expirada_vira_data_api_error_no_execute(contrato):
    conn = db_dataapi.get_conn()
    try:
        contrato.expire_transaction(conn.transaction_id)
        with pytest.raises(db_dataapi.DataApiError, match="TransactionNotFoundException"):
            conn.cursor().execute("SELECT 1 AS n")
    finally:
        conn.transaction_id = None
        conn.close()


def test_dataapi_commit_de_transacao_expirada_propaga_client_error_cru(contrato):
    """commit()/rollback() do shim nao encapsulam em DataApiError (so execute encapsula)."""
    conn = db_dataapi.get_conn()
    contrato.expire_transaction(conn.transaction_id)
    with pytest.raises(ClientError) as exc:
        conn.commit()
    assert exc.value.response["Error"]["Code"] == "TransactionNotFoundException"
    assert conn.transaction_id is not None  # commit() nao zera em falha (inalterado)
    # close() -> rollback() de id inexistente conta como ja revertido: nao levanta e zera o id.
    conn.close()
    assert conn.transaction_id is None


# ---------------------------------------------------------------------------
# 3. Aurora auto-pause: _is_resuming_error / _connect_with_retry
# ---------------------------------------------------------------------------

RESUMING_MSG = "The Aurora DB instance db-XYZ is resuming after being auto-paused. Please wait a few seconds and try again."


@pytest.fixture
def sleeps(monkeypatch):
    """Captura TODA espera do processo: `migrate.time` E o modulo `time`, entao
    este patch troca `time.sleep` GLOBALMENTE - inclusive o que o
    `db_dataapi._sleep` usa. Serve para medir as DUAS camadas de retry."""
    registro: list[float] = []
    monkeypatch.setattr(migrate.time, "sleep", registro.append)
    return registro


@pytest.fixture
def no_sleep(monkeypatch, sleeps):
    """`sleeps` com a camada de retry do SHIM desligada.

    Existem duas camadas para o auto-pause do Aurora: a de baixo, em
    `db_dataapi` (protege api-core e sync), e a do `migrate`
    (`_connect_with_retry`, anterior). Sem desligar a de baixo, ela absorveria
    a falha injetada antes de o `migrate` ve-la e estes testes mediriam o retry
    errado. Tupla vazia => `total_attempts = 1` => sem retry no shim, entao
    cada teste abaixo continua exercitando exatamente o retry do `migrate`.
    A coexistencia das duas camadas tem teste proprio (logo adiante)."""
    monkeypatch.setattr(db_dataapi, "_RESUME_RETRY_WAITS", ())
    return sleeps


def test_dataapi_retry_quando_begin_transaction_esta_retomando(contrato, no_sleep):
    contrato.inject_error("BeginTransaction", "DatabaseResumingException", RESUMING_MSG, times=2)
    conn = migrate._connect_with_retry(db_dataapi.get_conn, attempts=3, delay_seconds=0.01)
    try:
        assert conn.transaction_id
    finally:
        conn.close()
    assert no_sleep == [0.01, 0.01]


def test_dataapi_retry_quando_execute_esta_retomando(contrato, no_sleep):
    contrato.inject_error("ExecuteStatement", "DatabaseResumingException", RESUMING_MSG, times=1)
    conn = migrate._connect_with_retry(db_dataapi.get_conn, attempts=3, delay_seconds=0.01)
    conn.close()
    assert no_sleep == [0.01]


def test_dataapi_erro_que_nao_e_retomada_nao_faz_retry(contrato, no_sleep):
    contrato.inject_error("BeginTransaction", "ForbiddenException", "sem permissao", times=1, status=403)
    with pytest.raises(ClientError):
        migrate._connect_with_retry(db_dataapi.get_conn, attempts=3, delay_seconds=0.01)
    assert no_sleep == []


def test_dataapi_retomada_persistente_esgota_tentativas(contrato, no_sleep):
    contrato.inject_error("BeginTransaction", "DatabaseResumingException", RESUMING_MSG, times=5)
    with pytest.raises(ClientError) as exc:
        migrate._connect_with_retry(db_dataapi.get_conn, attempts=3, delay_seconds=0.01)
    assert migrate._is_resuming_error(exc.value)
    assert len(no_sleep) == 2


@_xfail(
    "migrate._connect_with_retry nao fecha a Connection quando o `SELECT 1` falha: a transacao aberta no "
    "construtor do shim fica pendurada no Aurora ate expirar (3 min). Impacto baixo"
)
def test_dataapi_retry_nao_deixa_transacao_orfa(contrato, no_sleep):
    contrato.inject_error("ExecuteStatement", "DatabaseResumingException", RESUMING_MSG, times=1)
    conn = migrate._connect_with_retry(db_dataapi.get_conn, attempts=3, delay_seconds=0.01)
    conn.close()
    assert contrato.open_transactions == []


def test_camadas_de_retry_coexistem_shim_absorve_antes_do_migrate(contrato, sleeps):
    """Com a camada do shim LIGADA (como em producao), uma retomada transitoria
    e absorvida em `db_dataapi` e o `migrate` nem chega a retentar: uma unica
    espera, e dela (5 s), nao a do `migrate` (0,01 s aqui)."""
    contrato.inject_error("BeginTransaction", "DatabaseResumingException", RESUMING_MSG, times=1)
    conn = migrate._connect_with_retry(db_dataapi.get_conn, attempts=3, delay_seconds=0.01)
    try:
        assert conn.transaction_id
    finally:
        conn.close()
    assert sleeps == [db_dataapi._RESUME_RETRY_WAITS[0]]


# ---------------------------------------------------------------------------
# Dispatcher app/db.py (subprocesso: o import escolhe o backend)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_value, expected",
    [(None, "app.db_dataapi"), ("data-api", "app.db_dataapi"), ("pymysql", "app.db_pymysql")],
)
def test_dispatcher_escolhe_o_backend_pelo_db_backend(env_value, expected):
    env = {k: v for k, v in os.environ.items() if k != "DB_BACKEND"}
    if env_value is not None:
        env["DB_BACKEND"] = env_value
    out = subprocess.run(
        [sys.executable, "-c", "import app.db as d; print(d.get_conn.__module__)"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == expected

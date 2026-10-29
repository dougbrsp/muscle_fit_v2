"""Testes do PROPRIO dublê da RDS Data API (tests/support/fake_rds_data.py),
usando um client boto3 `rds-data` real. Garantem que o dublê serializa e
valida como a documentacao AWS descreve, para que ele nao seja a fonte de um
falso verde nos testes do shim. Premissas NAO confirmadas pela documentacao
ficam explicitas nos testes marcados "[INFERIDO]"."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from tests.support.fake_rds_data import (
    FAKE_CLUSTER_ARN,
    FAKE_SECRET_ARN,
    FakeLimitationError,
    FakeRdsDataBackend,
    scan_sql,
)

TABLE = "dataapi_fake_probe"


@pytest.fixture
def probe(raw):
    with raw.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
        cur.execute(
            f"""
            CREATE TABLE {TABLE} (
              id BIGINT AUTO_INCREMENT PRIMARY KEY,
              flag TINYINT(1) NULL,
              small TINYINT NULL,
              dec_col DECIMAL(6,2) NULL,
              dbl DOUBLE NULL,
              dt DATETIME NULL,
              dt3 DATETIME(3) NULL,
              da DATE NULL,
              tm TIME NULL,
              ts TIMESTAMP NULL,
              js JSON NULL,
              bl BLOB NULL,
              bit1 BIT(1) NULL,
              bit8 BIT(8) NULL,
              ubig BIGINT UNSIGNED NULL,
              txt VARCHAR(100) NULL,
              uq VARCHAR(20) NULL UNIQUE
            ) ENGINE=InnoDB
            """
        )
    yield TABLE
    with raw.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")


@pytest.fixture
def client(fake_rds):
    return fake_rds.client()


def call(client, mysql_schema, sql, parameters=None, **extra):
    kwargs = dict(resourceArn=FAKE_CLUSTER_ARN, secretArn=FAKE_SECRET_ARN, database=mysql_schema, sql=sql, **extra)
    if parameters is not None:
        kwargs["parameters"] = parameters
    return client.execute_statement(**kwargs)


def error_code(exc_info) -> str:
    return exc_info.value.response["Error"]["Code"]


# ---------------------------------------------------------------------------
# Scanner de SQL
# ---------------------------------------------------------------------------


def test_scanner_ignora_parametros_em_strings_e_comentarios():
    scan = scan_sql("SELECT ':nao', `:nao2`, \"a:b\" -- :c\n, :sim /* :d */ FROM t # :e")
    assert scan.names == ["sim"]
    assert scan.statements == 1


def test_scanner_conta_statements_e_aceita_ponto_e_virgula_final():
    assert scan_sql("SELECT 1;").statements == 1
    assert scan_sql("SELECT ';' ; ").statements == 1
    assert scan_sql("SELECT 1; SELECT 2").statements == 2
    assert scan_sql("SET time_zone = '+00:00'").names == []


# ---------------------------------------------------------------------------
# Leitura: formato dos campos
# ---------------------------------------------------------------------------


def test_leitura_mapeia_cada_tipo_mysql_para_o_campo_da_data_api(client, mysql_schema, raw, probe):
    with raw.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {TABLE} (flag, small, dec_col, dbl, dt, dt3, da, tm, ts, js, bl, bit1, ubig, txt)
            VALUES (1, 5, 7.5, 2.25, '2026-09-10 08:00:00', '2026-09-10 08:00:00.123', '2026-09-10',
                    '09:30:00', '2026-09-10 08:00:00', '{{"a": 1}}', x'00ff', b'1', 42, 'olá')
            """
        )
        cur.execute(f"INSERT INTO {TABLE} (txt) VALUES (NULL)")
    resp = call(client, mysql_schema, f"SELECT flag, small, dec_col, dbl, dt, dt3, da, tm, ts, js, bl, bit1, ubig, txt, id FROM {TABLE} ORDER BY id")
    first, second = resp["records"]
    assert first[:14] == [
        {"booleanValue": True},  # [INFERIDO] TINYINT(1) -> BOOLEAN
        {"longValue": 5},
        {"stringValue": "7.50"},
        {"doubleValue": 2.25},
        {"stringValue": "2026-09-10 08:00:00"},
        {"stringValue": "2026-09-10 08:00:00.123"},
        {"stringValue": "2026-09-10"},
        {"stringValue": "09:30:00"},
        {"stringValue": "2026-09-10 08:00:00"},
        {"stringValue": '{"a": 1}'},
        {"blobValue": b"\x00\xff"},  # botocore ja decodificou o base64
        {"booleanValue": True},
        {"longValue": 42},
        {"stringValue": "olá"},
    ]
    assert first[14] == {"longValue": 1}
    assert second[:14] == [{"isNull": True}] * 14
    assert resp["numberOfRecordsUpdated"] == 0  # [INFERIDO] SELECT
    assert "columnMetadata" not in resp


def test_column_metadata_so_com_include_result_metadata(client, mysql_schema, probe):
    resp = call(client, mysql_schema, f"SELECT id, dec_col AS valor, flag FROM {TABLE}", includeResultMetadata=True)
    assert resp["records"] == []
    meta = resp["columnMetadata"]
    assert [(m["name"], m["label"], m["typeName"]) for m in meta] == [
        ("id", "id", "BIGINT"),
        ("dec_col", "valor", "DECIMAL"),
        ("flag", "flag", "BOOLEAN"),  # [MEDIDO] TINYINT(1) -> BOOLEAN
    ]
    assert meta[0]["nullable"] == 0 and meta[1]["nullable"] == 1
    assert (meta[1]["precision"], meta[1]["scale"]) == (6, 2)
    assert meta[0]["isAutoIncrement"] is True


@pytest.mark.parametrize(
    "ddl_value, column",
    [("b'10101010'", "bit8"), ("18446744073709551615", "ubig")],
)
def test_tipos_fora_do_escopo_falham_explicitamente(client, mysql_schema, raw, probe, ddl_value, column):
    with raw.cursor() as cur:
        cur.execute(f"INSERT INTO {TABLE} ({column}) VALUES ({ddl_value})")
    with pytest.raises(FakeLimitationError):
        call(client, mysql_schema, f"SELECT {column} FROM {TABLE}")


# ---------------------------------------------------------------------------
# Escrita: parametros e typeHint
# ---------------------------------------------------------------------------


def test_binds_com_type_hint_chegam_no_mysql_com_o_tipo_certo(client, mysql_schema, raw, probe):
    params = [
        {"name": "flag", "value": {"booleanValue": True}},
        {"name": "small", "value": {"longValue": 7}},
        {"name": "dec", "value": {"stringValue": "12.345"}, "typeHint": "DECIMAL"},
        {"name": "dbl", "value": {"doubleValue": 0.5}},
        {"name": "dt", "value": {"stringValue": "2026-09-10 08:15:30"}, "typeHint": "TIMESTAMP"},
        {"name": "dt3", "value": {"stringValue": "2026-09-10 08:15:30.250"}, "typeHint": "TIMESTAMP"},
        {"name": "da", "value": {"stringValue": "2026-09-11"}, "typeHint": "DATE"},
        {"name": "tm", "value": {"stringValue": "07:05:00"}, "typeHint": "TIME"},
        {"name": "js", "value": {"stringValue": '{"b": [1, 2]}'}, "typeHint": "JSON"},
        {"name": "bl", "value": {"blobValue": b"\x01\x02"}},
        {"name": "txt", "value": {"isNull": True}},
    ]
    resp = call(
        client,
        mysql_schema,
        f"INSERT INTO {TABLE} (flag, small, dec_col, dbl, dt, dt3, da, tm, js, bl, txt) "
        "VALUES (:flag, :small, :dec, :dbl, :dt, :dt3, :da, :tm, :js, :bl, :txt)",
        params,
    )
    assert resp["numberOfRecordsUpdated"] == 1
    assert resp["generatedFields"] == [{"longValue": 1}]
    with raw.cursor() as cur:
        cur.execute(f"SELECT flag, small, CAST(dec_col AS CHAR) AS dec_col, dbl, CAST(dt AS CHAR) AS dt, CAST(dt3 AS CHAR) AS dt3, CAST(da AS CHAR) AS da, CAST(tm AS CHAR) AS tm, js, bl, txt FROM {TABLE}")
        row = cur.fetchone()
    assert row == {
        "flag": 1,
        "small": 7,
        "dec_col": "12.35",  # DECIMAL(6,2) arredonda no servidor
        "dbl": 0.5,
        "dt": "2026-09-10 08:15:30",
        "dt3": "2026-09-10 08:15:30.250",
        "da": "2026-09-11",
        "tm": "07:05:00",
        "js": '{"b": [1, 2]}',
        "bl": b"\x01\x02",
        "txt": None,
    }


@pytest.mark.parametrize(
    "hint, value",
    [
        ("TIMESTAMP", "2026-09-10T08:00:00"),
        ("DATE", "10/09/2026"),
        ("TIME", "7:05"),
        ("DECIMAL", "abc"),
        ("JSON", "{nao-e-json"),
        ("UUID", "3f2504e0-4f89-11d3-9a0c-0305e82c3301"),
    ],
)
def test_type_hint_com_formato_invalido_e_rejeitado(client, mysql_schema, probe, hint, value):
    with pytest.raises(ClientError) as exc:
        call(client, mysql_schema, "SELECT :v AS v", [{"name": "v", "value": {"stringValue": value}, "typeHint": hint}])
    assert error_code(exc) == "BadRequestException"


def test_array_value_e_rejeitado(client, mysql_schema):
    with pytest.raises(ClientError) as exc:
        call(client, mysql_schema, "SELECT :v AS v", [{"name": "v", "value": {"arrayValue": {"longValues": [1, 2]}}}])
    assert error_code(exc) == "BadRequestException"


def test_parametro_ausente_e_rejeitado(client, mysql_schema):
    with pytest.raises(ClientError) as exc:
        call(client, mysql_schema, "SELECT :a AS a, :b AS b", [{"name": "a", "value": {"longValue": 1}}])
    assert error_code(exc) == "DatabaseErrorException"


def test_percent_e_dois_pontos_literais_preservados(client, mysql_schema):
    resp = call(
        client,
        mysql_schema,
        "SELECT CONCAT(:p, '%', ' 08:00') AS a, '50%' AS b",
        [{"name": "p", "value": {"stringValue": "x"}}],
    )
    assert resp["records"] == [[{"stringValue": "x% 08:00"}, {"stringValue": "50%"}]]


def test_multi_statement_e_rejeitado(client, mysql_schema):
    with pytest.raises(ClientError) as exc:
        call(client, mysql_schema, "SELECT 1; SELECT 2")
    assert error_code(exc) == "DatabaseErrorException"
    assert call(client, mysql_schema, "SELECT ';' AS a;")["records"] == [[{"stringValue": ";"}]]


def test_schema_parameter_e_rejeitado(client, mysql_schema):
    with pytest.raises(ClientError) as exc:
        call(client, mysql_schema, "SELECT 1", schema="x")
    assert error_code(exc) == "BadRequestException"


# ---------------------------------------------------------------------------
# DML: numberOfRecordsUpdated / generatedFields
# ---------------------------------------------------------------------------


def test_update_conta_linhas_encontradas_por_padrao_e_alteradas_so_no_modo_changed(mysql_kwargs, mysql_schema, raw, probe):
    with raw.cursor() as cur:
        cur.execute(f"INSERT INTO {TABLE} (txt) VALUES ('a'), ('b')")
    matched = FakeRdsDataBackend(**mysql_kwargs).client()
    changed = FakeRdsDataBackend(update_count_mode="changed", **mysql_kwargs).client()
    sql = f"UPDATE {TABLE} SET txt = 'a' WHERE txt IN ('a', 'b')"
    assert call(matched, mysql_schema, sql)["numberOfRecordsUpdated"] == 2  # [MEDIDO] matched
    assert call(changed, mysql_schema, sql)["numberOfRecordsUpdated"] == 0
    resp = call(matched, mysql_schema, f"DELETE FROM {TABLE}")
    assert resp["numberOfRecordsUpdated"] == 2
    assert resp["generatedFields"] == []


def test_fatos_medidos_na_aurora_real(client, mysql_schema, raw, probe):
    """Mesmas expressoes medidas pelo Tech Lead na Aurora MySQL 8.0 (3.08.2)
    via Data API em 2026-09-14 (ver docstring de fake_rds_data.py): typeName e
    campo devolvido precisam coincidir."""
    with raw.cursor() as cur:
        cur.execute(f"INSERT INTO {TABLE} (flag, txt) VALUES (1, 'a'), (0, 'b')")
    resp = call(
        client,
        mysql_schema,
        "SELECT CAST(1.50 AS DECIMAL(5,2)) AS d, CAST('2026-09-14 10:11:12.345678' AS DATETIME(6)) AS dt6, "
        "CAST('2026-09-14 10:11:12' AS DATETIME) AS dt0, CAST('2026-09-14' AS DATE) AS da, "
        "CAST('10:11:12' AS TIME) AS tm, flag, TRUE AS tr, CAST(42 AS UNSIGNED) AS u, 3.5e0 AS dbl, "
        f"JSON_OBJECT('a', 1) AS js, NULL AS n FROM {TABLE} WHERE txt = 'a'",
        includeResultMetadata=True,
    )
    assert [m["typeName"] for m in resp["columnMetadata"]] == [
        "DECIMAL", "DATETIME", "DATETIME", "DATE", "TIME", "BOOLEAN", "BIGINT", "BIGINT UNSIGNED", "DOUBLE", "JSON",
        resp["columnMetadata"][-1]["typeName"],  # NULL: typeName nao medido
    ]
    assert resp["records"] == [[
        {"stringValue": "1.50"},
        {"stringValue": "2026-09-14 10:11:12.345678"},
        {"stringValue": "2026-09-14 10:11:12"},
        {"stringValue": "2026-09-14"},
        {"stringValue": "10:11:12"},
        {"booleanValue": True},
        {"longValue": 1},
        {"longValue": 42},
        {"doubleValue": 3.5},
        {"stringValue": '{"a": 1}'},
        {"isNull": True},
    ]]
    assert resp["numberOfRecordsUpdated"] == 0

    same_value = call(client, mysql_schema, f"UPDATE {TABLE} SET txt = txt WHERE txt = 'a'")
    assert same_value["numberOfRecordsUpdated"] == 1
    assert call(client, mysql_schema, f"UPDATE {TABLE} SET txt = txt")["numberOfRecordsUpdated"] == 2


def test_generated_fields_de_insert_auto_increment(client, mysql_schema, probe):
    first = call(client, mysql_schema, f"INSERT INTO {TABLE} (txt) VALUES ('x')")
    second = call(client, mysql_schema, f"INSERT INTO {TABLE} (txt) VALUES ('y')")
    assert first["generatedFields"] == [{"longValue": 1}]
    assert second["generatedFields"] == [{"longValue": 2}]


# ---------------------------------------------------------------------------
# Transacoes
# ---------------------------------------------------------------------------


def _tx(client, mysql_schema):
    return client.begin_transaction(resourceArn=FAKE_CLUSTER_ARN, secretArn=FAKE_SECRET_ARN, database=mysql_schema)[
        "transactionId"
    ]


def _count(raw) -> int:
    with raw.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM {TABLE}")
        return cur.fetchone()["n"]


def test_commit_torna_visivel_e_invalida_o_transaction_id(client, mysql_schema, raw, probe):
    tx = _tx(client, mysql_schema)
    call(client, mysql_schema, f"INSERT INTO {TABLE} (txt) VALUES ('t')", transactionId=tx)
    assert _count(raw) == 0
    status = client.commit_transaction(resourceArn=FAKE_CLUSTER_ARN, secretArn=FAKE_SECRET_ARN, transactionId=tx)
    assert status["transactionStatus"] == "Transaction Committed"
    assert _count(raw) == 1
    with pytest.raises(ClientError) as exc:
        call(client, mysql_schema, "SELECT 1", transactionId=tx)
    assert error_code(exc) == "TransactionNotFoundException"
    with pytest.raises(ClientError) as exc:
        client.commit_transaction(resourceArn=FAKE_CLUSTER_ARN, secretArn=FAKE_SECRET_ARN, transactionId=tx)
    assert error_code(exc) == "TransactionNotFoundException"


def test_rollback_descarta(client, mysql_schema, raw, probe):
    tx = _tx(client, mysql_schema)
    call(client, mysql_schema, f"INSERT INTO {TABLE} (txt) VALUES ('t')", transactionId=tx)
    status = client.rollback_transaction(resourceArn=FAKE_CLUSTER_ARN, secretArn=FAKE_SECRET_ARN, transactionId=tx)
    assert status["transactionStatus"] == "Rollback Complete"
    assert _count(raw) == 0


def test_erro_de_sql_nao_aborta_a_transacao_por_padrao(client, mysql_schema, raw, probe):
    """Fiel a producao: medido na Aurora real (1146 e 1062 dentro de
    BeginTransaction -> SELECT 1 seguinte OK, RollbackTransaction OK)."""
    tx = _tx(client, mysql_schema)
    call(client, mysql_schema, f"INSERT INTO {TABLE} (uq) VALUES ('dup')", transactionId=tx)
    with pytest.raises(ClientError) as exc:
        call(client, mysql_schema, f"INSERT INTO {TABLE} (uq) VALUES ('dup')", transactionId=tx)
    assert error_code(exc) == "DatabaseErrorException"
    assert "Duplicate entry" in str(exc.value)
    with pytest.raises(ClientError) as exc:
        call(client, mysql_schema, "SELECT * FROM tabela_que_nao_existe", transactionId=tx)
    assert error_code(exc) == "DatabaseErrorException"
    assert call(client, mysql_schema, "SELECT 1 AS n", transactionId=tx)["records"] == [[{"longValue": 1}]]
    assert _count(raw) == 0  # ainda nao commitado
    status = client.rollback_transaction(resourceArn=FAKE_CLUSTER_ARN, secretArn=FAKE_SECRET_ARN, transactionId=tx)
    assert status["transactionStatus"] == "Rollback Complete"
    assert _count(raw) == 0


def test_modo_hipotetico_erro_de_sql_aborta_a_transacao(fake_rds, client, mysql_schema, raw, probe):
    fake_rds.abort_transaction_on_error = True
    tx = _tx(client, mysql_schema)
    call(client, mysql_schema, f"INSERT INTO {TABLE} (uq) VALUES ('dup')", transactionId=tx)
    with pytest.raises(ClientError) as exc:
        call(client, mysql_schema, f"INSERT INTO {TABLE} (uq) VALUES ('dup')", transactionId=tx)
    assert error_code(exc) == "DatabaseErrorException"
    assert "Duplicate entry" in str(exc.value)
    with pytest.raises(ClientError) as exc:
        call(client, mysql_schema, "SELECT 1", transactionId=tx)
    assert error_code(exc) == "TransactionNotFoundException"
    assert _count(raw) == 0


def test_transacao_expirada(fake_rds, client, mysql_schema, probe):
    tx = _tx(client, mysql_schema)
    fake_rds.expire_transaction(tx)
    with pytest.raises(ClientError) as exc:
        call(client, mysql_schema, "SELECT 1", transactionId=tx)
    assert error_code(exc) == "TransactionNotFoundException"


def test_sem_transacao_estado_de_sessao_nao_sobrevive(client, mysql_schema):
    call(client, mysql_schema, "SET @musclefit_probe = 1")
    assert call(client, mysql_schema, "SELECT @musclefit_probe AS v")["records"] == [[{"isNull": True}]]


def test_com_transacao_estado_de_sessao_sobrevive(client, mysql_schema):
    tx = _tx(client, mysql_schema)
    call(client, mysql_schema, "SET @musclefit_probe = 1", transactionId=tx)
    assert call(client, mysql_schema, "SELECT @musclefit_probe AS v", transactionId=tx)["records"] == [[{"longValue": 1}]]


# ---------------------------------------------------------------------------
# Erros injetados, batch, isolamento de rede
# ---------------------------------------------------------------------------


def test_erro_injetado_chega_como_client_error_com_codigo_aws(fake_rds, client, mysql_schema):
    fake_rds.inject_error("BeginTransaction", "DatabaseResumingException", "Aurora is resuming after being auto-paused", times=1)
    with pytest.raises(ClientError) as exc:
        _tx(client, mysql_schema)
    assert error_code(exc) == "DatabaseResumingException"
    assert "DatabaseResumingException" in str(exc.value)
    assert _tx(client, mysql_schema)


def test_batch_execute_statement(client, mysql_schema, raw, probe):
    resp = client.batch_execute_statement(
        resourceArn=FAKE_CLUSTER_ARN,
        secretArn=FAKE_SECRET_ARN,
        database=mysql_schema,
        sql=f"INSERT INTO {TABLE} (txt) VALUES (:t)",
        parameterSets=[[{"name": "t", "value": {"stringValue": "a"}}], [{"name": "t", "value": {"stringValue": "b"}}]],
    )
    assert resp["updateResults"] == [{"generatedFields": [{"longValue": 1}]}, {"generatedFields": [{"longValue": 2}]}]
    assert _count(raw) == 2


def test_todas_as_chamadas_sao_interceptadas(fake_rds, client, mysql_schema):
    call(client, mysql_schema, "SELECT 1")
    assert [c.operation for c in fake_rds.calls] == ["ExecuteStatement"]
    assert client.meta.endpoint_url.endswith(".invalid")

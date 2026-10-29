"""Dublê do servico AWS RDS Data API (`rds-data`) apoiado num MySQL real.

SO para testes. Nunca e importado pela aplicacao nem entra em imagem.

Desenho
=======

O dublê NAO substitui o client boto3: ele intercepta a requisicao HTTP de um
client `boto3.client("rds-data")` VERDADEIRO no evento `before-send` do
botocore e devolve uma resposta HTTP fabricada. Consequencias:

- validacao de parametros, serializacao do request (rest-json, `blobValue`
  em base64) e parsing da resposta sao os do proprio botocore, com o mesmo
  modelo de servico usado em producao (versao pinada em requirements.txt);
- erros chegam ao shim como `botocore.exceptions.ClientError` reais, com o
  codigo AWS (`TransactionNotFoundException`, `DatabaseErrorException`,
  `DatabaseResumingException`...) no texto da excecao;
- nenhuma chamada de rede sai do processo: o handler responde TODA
  requisicao, e o client e criado com credenciais e endpoint ficticios.

O SQL recebido e executado num MySQL 8 descartavel via PyMySQL e o resultado
e serializado no formato da Data API.

Regras de serializacao e onde cada uma vem da documentacao AWS
==============================================================

[AWS-1] https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api-operations.html
        (tipos MySQL suportados; tabela JDBC -> Data API; `typeHint`)
[AWS-2] https://docs.aws.amazon.com/rdsdataservice/latest/APIReference/API_ExecuteStatement.html
        (campos da resposta; erros; "sem transactionId = autocommit")
[AWS-3] https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.troubleshooting.html
        (multi-statement rejeitado; "Transaction <id> isn't found" apos
        commit/rollback, apos 3 min ocioso ou apos erro numa chamada anterior)

Leitura (valor de coluna -> `Field`), [AWS-1] tabela JDBC -> Data API:
- NULL                                   -> {"isNull": true}
- TINYINT/SMALLINT/MEDIUMINT/INT/BIGINT  -> longValue
- FLOAT/DOUBLE                           -> doubleValue
- DECIMAL                                -> stringValue (texto do servidor, ex. "7.50")
- DATE/TIME/DATETIME/TIMESTAMP           -> stringValue ("other types ... date and time")
- JSON                                   -> stringValue
- VARCHAR/CHAR/TEXT/ENUM                 -> stringValue
- BLOB/BINARY/VARBINARY                  -> blobValue
- BIT(1)                                 -> booleanValue ("BOOLEAN, BIT -> BOOLEAN")
- TINYINT(1)/BOOLEAN                     -> booleanValue  [INFERIDO, ver abaixo]

Escrita (`SqlParameter` -> valor enviado ao MySQL), [AWS-1]:
- isNull -> NULL; longValue -> int; doubleValue -> float; booleanValue -> bool;
  blobValue -> bytes; stringValue sem typeHint -> str;
- typeHint DECIMAL   -> Decimal (texto precisa ser decimal valido);
- typeHint DATE      -> formato aceito `YYYY-MM-DD`;
- typeHint TIME      -> formato aceito `HH:MM:SS[.FFF]`;
- typeHint TIMESTAMP -> formato aceito `YYYY-MM-DD HH:MM:SS[.FFF]`;
- typeHint JSON      -> texto precisa ser JSON valido;
- typeHint UUID      -> so Aurora PostgreSQL: rejeitado aqui;
- arrayValue         -> "Array parameters are not supported" [AWS-2].

Transacoes [AWS-2]/[AWS-3]: `BeginTransaction` reserva uma conexao; sem
`transactionId` cada chamada roda numa conexao NOVA em autocommit (estado de
sessao nao sobrevive entre chamadas). Depois de commit/rollback o id deixa de
existir (TransactionNotFoundException). Um erro de SQL dentro de uma
transacao NAO a aborta (medido na Aurora de producao, ver
`abort_transaction_on_error` em `FakeRdsDataBackend`).

MEDIDO na Aurora MySQL 8.0 real (Aurora 3.08.2) em 2026-09-14, via
`aws rds-data execute-statement --include-result-metadata` (marcado [MEDIDO]
no codigo; coberto por test_fake_rds_data.py::test_fatos_medidos_na_aurora_real):
- `CAST(1.50 AS DECIMAL(5,2))`, colunas DECIMAL -> typeName DECIMAL, stringValue "1.50";
- DATETIME(6) -> DATETIME, stringValue "2026-09-14 10:11:12.345678";
  DATETIME/coluna created_at -> DATETIME/TIMESTAMP, "2026-09-14 10:11:12";
- DATE -> DATE "2026-09-14"; TIME -> TIME "10:11:12";
- coluna TINYINT(1) (gyms.is_active) -> typeName BOOLEAN, booleanValue;
- literal TRUE -> BIGINT, longValue 1; CAST(42 AS UNSIGNED) -> BIGINT UNSIGNED, longValue;
- 3.5e0 -> DOUBLE, doubleValue; JSON_OBJECT/coluna JSON -> JSON, stringValue;
- NULL -> isNull; SELECT -> numberOfRecordsUpdated 0;
- `UPDATE ... SET col = col` (sem mudanca real) em 1 e 2 linhas ->
  numberOfRecordsUpdated 1 e 2: a Data API conta linhas ENCONTRADAS
  (matched), por isso `update_count_mode="matched"` e o padrao ("changed"
  fica disponivel so para testes de contraste);
- INSERT com AUTO_INCREMENT -> generatedFields [{"longValue": 1}].

NAO CONFIRMADO (marcado com [INFERIDO] no codigo):
1. Texto de erro: "Database error code: <n>. Message: <mensagem do MySQL>".
2. Ponto-e-virgula final unico ("SELECT 1;") e aceito.
3. BIT(1) -> booleanValue (doc: "BOOLEAN, BIT -> BOOLEAN"); nao ha BIT no schema.

Limite de tamanho do resultado [AWS-4]/[AWS-3]: "The response size limit is
1 MiB. If the call returns more than 1 MiB of response data, the call is
terminated." O fake mede o JSON serializado de `records` de um
ExecuteStatement e, acima de `max_result_bytes` (default 1 MiB), responde
`UnsupportedResultException` com a mensagem "The result exceeds the size limit
1 MB." - codigo e texto observados nos logs de producao (GET
/cardio-recommendations, 2026-09). [INFERIDO] a medida exata usada pela AWS
(bytes do JSON de `records`) e que a transacao segue aberta apos o erro.

[AWS-4] https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.limitations.html

Fora do escopo do fake (erro explicito `FakeLimitationError`): BIT(N>1),
BIGINT UNSIGNED acima de 2^63-1, tipos espaciais, `formatRecordsAs=JSON`,
`resultSetOptions`. Limite de 64 KB por linha ([AWS-3], "Packet for query is
too large"): nao simulado - producao devolve linhas de ~110 KB
(`cardio_recommendations.context_snapshot`) sem esse erro.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any, Callable, Optional

import boto3
import pymysql
from botocore.awsrequest import AWSResponse
from botocore.config import Config
from pymysql.constants import FIELD_TYPE, FLAG

__all__ = [
    "FakeLimitationError",
    "FakeRdsDataBackend",
    "RecordedCall",
    "scan_sql",
]

FAKE_REGION = "sa-east-1"
FAKE_ENDPOINT = "https://rds-data.fake-musclefit.invalid"
FAKE_CLUSTER_ARN = "arn:aws:rds:sa-east-1:000000000000:cluster:musclefit-fake"
FAKE_SECRET_ARN = "arn:aws:secretsmanager:sa-east-1:000000000000:secret:musclefit-fake"
# [AWS-4] "The response size limit is 1 MiB."
RESULT_SIZE_LIMIT_BYTES = 1024 * 1024

_OPERATIONS = {
    "/Execute": "ExecuteStatement",
    "/BatchExecute": "BatchExecuteStatement",
    "/BeginTransaction": "BeginTransaction",
    "/CommitTransaction": "CommitTransaction",
    "/RollbackTransaction": "RollbackTransaction",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}(\.\d{1,3})?$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{1,3})?$")
_MAX_LONG = 2**63 - 1

_INTEGER_TYPES = {FIELD_TYPE.TINY, FIELD_TYPE.SHORT, FIELD_TYPE.INT24, FIELD_TYPE.LONG, FIELD_TYPE.LONGLONG, FIELD_TYPE.YEAR}
_TEXTUAL_TEMPORAL = {FIELD_TYPE.DATE, FIELD_TYPE.NEWDATE, FIELD_TYPE.DATETIME, FIELD_TYPE.TIMESTAMP, FIELD_TYPE.TIME}
_DECIMAL_TYPES = {FIELD_TYPE.DECIMAL, FIELD_TYPE.NEWDECIMAL}

# Nome MySQL e codigo java.sql.Types aproximados (so para columnMetadata; o
# shim usa apenas label/name/typeName/precision/scale/nullable).
_TYPE_INFO = {
    FIELD_TYPE.TINY: ("TINYINT", -6),
    FIELD_TYPE.SHORT: ("SMALLINT", 5),
    FIELD_TYPE.INT24: ("MEDIUMINT", 4),
    FIELD_TYPE.LONG: ("INT", 4),
    FIELD_TYPE.LONGLONG: ("BIGINT", -5),
    FIELD_TYPE.YEAR: ("YEAR", 91),
    FIELD_TYPE.FLOAT: ("FLOAT", 7),
    FIELD_TYPE.DOUBLE: ("DOUBLE", 8),
    FIELD_TYPE.DECIMAL: ("DECIMAL", 3),
    FIELD_TYPE.NEWDECIMAL: ("DECIMAL", 3),
    FIELD_TYPE.DATE: ("DATE", 91),
    FIELD_TYPE.NEWDATE: ("DATE", 91),
    FIELD_TYPE.TIME: ("TIME", 92),
    FIELD_TYPE.DATETIME: ("DATETIME", 93),
    FIELD_TYPE.TIMESTAMP: ("TIMESTAMP", 93),
    FIELD_TYPE.JSON: ("JSON", -1),
    FIELD_TYPE.BIT: ("BIT", -7),
    FIELD_TYPE.VARCHAR: ("VARCHAR", 12),
    FIELD_TYPE.VAR_STRING: ("VARCHAR", 12),
    FIELD_TYPE.STRING: ("CHAR", 1),
    FIELD_TYPE.ENUM: ("ENUM", 1),
    FIELD_TYPE.SET: ("SET", 1),
    FIELD_TYPE.TINY_BLOB: ("TINYBLOB", -4),
    FIELD_TYPE.MEDIUM_BLOB: ("MEDIUMBLOB", -4),
    FIELD_TYPE.LONG_BLOB: ("LONGBLOB", -4),
    FIELD_TYPE.BLOB: ("BLOB", -4),
}


class FakeLimitationError(Exception):
    """O dublê nao sabe reproduzir fielmente este caso (ver docstring do modulo).
    Falha explicita em vez de inventar um mapeamento."""


class _AwsError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class RecordedCall:
    operation: str
    params: dict[str, Any]
    error_code: Optional[str] = None


@dataclass
class _Transaction:
    conn: pymysql.connections.Connection
    database: str


@dataclass
class _InjectedError:
    operation: str
    code: str
    message: str
    status: int
    remaining: int


# ---------------------------------------------------------------------------
# Scanner de SQL (strings/identificadores/comentarios do MySQL)
# ---------------------------------------------------------------------------


@dataclass
class ScanResult:
    """`segments`: pedacos de SQL; entre dois pedacos consecutivos ha um
    parametro nomeado (`names`). `statements`: quantidade de statements nao
    vazios separados por `;` fora de strings/comentarios."""

    segments: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    statements: int = 0


def scan_sql(sql: str) -> ScanResult:
    result = ScanResult()
    buf: list[str] = []
    stmt_has_content = False
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch in ("'", '"', "`"):
            quote = ch
            j = i + 1
            while j < n:
                if sql[j] == "\\" and quote != "`":
                    j += 2
                    continue
                if sql[j] == quote:
                    if j + 1 < n and sql[j + 1] == quote:
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i : j + 1])
            stmt_has_content = True
            i = j + 1
            continue
        if ch == "#" or (ch == "-" and nxt == "-" and (i + 2 >= n or sql[i + 2] in " \t\r\n")):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            buf.append(sql[i:j])
            i = j
            continue
        if ch == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            buf.append(sql[i:j])
            i = j
            continue
        if ch == ":" and nxt and (nxt.isalpha() or nxt == "_"):
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            result.segments.append("".join(buf))
            result.names.append(sql[i + 1 : j])
            buf = []
            stmt_has_content = True
            i = j
            continue
        if ch == ";":
            if stmt_has_content:
                result.statements += 1
            stmt_has_content = False
            buf.append(ch)
            i += 1
            continue
        if not ch.isspace():
            stmt_has_content = True
        buf.append(ch)
        i += 1
    if stmt_has_content:
        result.statements += 1
    result.segments.append("".join(buf))
    return result


# ---------------------------------------------------------------------------
# Conversao de parametros e de resultados
# ---------------------------------------------------------------------------


def _bad(message: str) -> _AwsError:
    return _AwsError("BadRequestException", message)


def _param_to_python(param: dict[str, Any]) -> Any:
    value = param.get("value") or {}
    hint = param.get("typeHint")
    if len(value) != 1:
        raise _bad(f"Parameter {param.get('name')!r} must have exactly one value member")
    (kind, raw), = value.items()
    if kind == "arrayValue":
        raise _bad("Array parameters are not supported")
    if kind == "isNull":
        return None
    if kind == "longValue":
        return int(raw)
    if kind == "doubleValue":
        return float(raw)
    if kind == "booleanValue":
        return bool(raw)
    if kind == "blobValue":
        return base64.b64decode(raw)
    if kind != "stringValue":
        raise _bad(f"Unsupported parameter value type {kind!r}")
    if hint is None:
        return raw
    if hint == "DECIMAL":
        try:
            return Decimal(raw)
        except InvalidOperation as exc:
            raise _bad(f"Invalid DECIMAL value {raw!r}") from exc
    if hint == "DATE":
        if not _DATE_RE.match(raw):
            raise _bad(f"Invalid DATE value {raw!r}: expected YYYY-MM-DD")
        return _dt.date.fromisoformat(raw)
    if hint == "TIME":
        if not _TIME_RE.match(raw):
            raise _bad(f"Invalid TIME value {raw!r}: expected HH:MM:SS[.FFF]")
        return raw
    if hint == "TIMESTAMP":
        if not _TIMESTAMP_RE.match(raw):
            raise _bad(f"Invalid TIMESTAMP value {raw!r}: expected YYYY-MM-DD HH:MM:SS[.FFF]")
        return raw
    if hint == "JSON":
        try:
            json.loads(raw)
        except ValueError as exc:
            raise _bad(f"Invalid JSON value {raw!r}") from exc
        return raw
    raise _bad(f"typeHint {hint!r} is not supported for Aurora MySQL")


def _escape_percent(segment: str) -> str:
    return segment.replace("%", "%%")


def _decoders() -> dict[Any, Any]:
    """Conversores PyMySQL SEM decodificacao de DECIMAL/data/hora: o fake
    precisa do texto cru do servidor para devolver `stringValue`."""
    conv = dict(pymysql.converters.conversions)
    for type_code in (*_DECIMAL_TYPES, *_TEXTUAL_TEMPORAL):
        conv.pop(type_code, None)
    return conv


def _field_to_data_api(value: Any, column: Any) -> dict[str, Any]:
    if value is None:
        return {"isNull": True}
    type_code = column.type_code
    if type_code == FIELD_TYPE.BIT:
        if column.length != 1:
            raise FakeLimitationError(f"BIT({column.length}) nao suportado pelo fake (coluna {column.name})")
        return {"booleanValue": value not in (b"\x00", 0)}
    if type_code == FIELD_TYPE.TINY and column.length == 1:
        return {"booleanValue": bool(int(value))}  # [INFERIDO] TINYINT(1) -> BOOLEAN
    if type_code in _INTEGER_TYPES:
        number = int(value)
        if number > _MAX_LONG:
            raise FakeLimitationError(f"inteiro acima de 2^63-1 nao suportado pelo fake (coluna {column.name})")
        return {"longValue": number}
    if type_code in (FIELD_TYPE.FLOAT, FIELD_TYPE.DOUBLE):
        return {"doubleValue": float(value)}
    if type_code in _DECIMAL_TYPES or type_code in _TEXTUAL_TEMPORAL:
        return {"stringValue": str(value)}
    if isinstance(value, (bytes, bytearray)):
        return {"blobValue": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, str):
        return {"stringValue": value}
    raise FakeLimitationError(f"tipo MySQL {type_code} nao suportado pelo fake (coluna {column.name})")


def _column_metadata(column: Any) -> dict[str, Any]:
    type_name, jdbc_type = _TYPE_INFO.get(column.type_code, ("UNKNOWN", 1111))
    unsigned = bool(column.flags & FLAG.UNSIGNED)
    if unsigned and column.type_code in _INTEGER_TYPES:
        type_name = f"{type_name} UNSIGNED"
    if column.type_code == FIELD_TYPE.TINY and column.length == 1:
        # [MEDIDO] coluna TINYINT(1) (gyms.is_active) -> typeName "BOOLEAN".
        type_name, jdbc_type = "BOOLEAN", -7
    precision = column.length
    if column.type_code in _DECIMAL_TYPES:
        # O protocolo MySQL manda o tamanho de exibicao (digitos + ponto + sinal);
        # a precisao JDBC de DECIMAL(M,D) e M.
        precision = column.length - (1 if column.scale else 0) - (0 if unsigned else 1)
    return {
        "arrayBaseColumnType": 0,
        "isAutoIncrement": bool(column.flags & FLAG.AUTO_INCREMENT),
        "isCaseSensitive": False,
        "isCurrency": False,
        "isSigned": not unsigned,
        "label": column.name,
        "name": column.org_name or column.name,
        "nullable": 0 if column.flags & FLAG.NOT_NULL else 1,
        "precision": precision,
        "scale": column.scale,
        "schemaName": "",
        "tableName": column.org_table or "",
        "type": jdbc_type,
        "typeName": type_name,
    }


_ROWS_MATCHED_RE = re.compile(rb"Rows matched:\s*(\d+)")


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class _FakeRaw:
    def __init__(self, body: bytes):
        self._body = body

    def stream(self, **_kwargs: Any):
        yield self._body


class FakeRdsDataBackend:
    """Estado do "servico": transacoes abertas, chamadas registradas e erros
    injetados. Use `client()` para obter um client boto3 real interceptado.

    `abort_transaction_on_error` (atributo mutavel, pode ser trocado no meio
    do teste):
    - `False` (PADRAO, fiel a producao): a transacao sobrevive ao erro de SQL.
      Evidencia medida na Aurora de producao (Aurora MySQL 8.0 / 3.08.2,
      cluster musclefit-db, via Data API, dentro de BeginTransaction, sem
      gravar dados): `SELECT` em tabela inexistente -> DatabaseErrorException
      "Error code: 1146"; `INSERT` duplicado -> DatabaseErrorException
      "Duplicate entry ... Error code: 1062"; nos dois casos um `SELECT 1`
      seguinte na MESMA transacao funcionou e `RollbackTransaction` devolveu
      "Rollback complete".
    - `True` (HIPOTETICO, so em teste explicito): erro de SQL descarta a
      transacao e chamadas seguintes com o id recebem
      `TransactionNotFoundException` (HTTP 404). Nao e o que acontece com erro
      de SQL em producao; serve para exercitar o caminho "transacao ja nao
      existe" (mesmo efeito de expiracao apos 3 min ocioso [AWS-3], ver tambem
      `expire_transaction`).

    `inject_error(operacao, codigo, ...)` simula falhas do proprio servico
    (ex.: `ThrottlingException` no `RollbackTransaction`) antes de a operacao
    ser executada."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        update_count_mode: str = "matched",
        abort_transaction_on_error: bool = False,
    ) -> None:
        if update_count_mode not in ("changed", "matched"):
            raise ValueError("update_count_mode deve ser 'changed' ou 'matched'")
        self._connect_kwargs = dict(host=host, port=port, user=user, password=password, charset="utf8mb4")
        self.update_count_mode = update_count_mode
        self.abort_transaction_on_error = abort_transaction_on_error
        self.calls: list[RecordedCall] = []
        self.client_configs: list[Any] = []
        self._transactions: dict[str, _Transaction] = {}
        self._injected: list[_InjectedError] = []
        self._lock = threading.Lock()

    # -- API de teste --------------------------------------------------------

    def client(self, region_name: str = FAKE_REGION) -> Any:
        session = boto3.session.Session(
            aws_access_key_id="fake-access-key",
            aws_secret_access_key="fake-secret-key",
            region_name=region_name,
        )
        client = session.client(
            "rds-data",
            endpoint_url=FAKE_ENDPOINT,
            config=Config(retries={"total_max_attempts": 1}),
        )
        client.meta.events.register("before-send.rds-data", self._before_send)
        return client

    def boto3_module(self) -> SimpleNamespace:
        """Substituto de `boto3` para `app.db_dataapi` (so `boto3.client`)."""

        def _client(service_name: str, region_name: Optional[str] = None, **kw: Any) -> Any:
            if service_name != "rds-data":
                raise AssertionError(f"db_dataapi pediu client inesperado: {service_name!r}")
            # `config` pedido pelo shim fica registrado (o client do dublê usa
            # sempre 1 tentativa, para nao dormir em backoff durante os testes).
            self.client_configs.append(kw.get("config"))
            return self.client(region_name or FAKE_REGION)

        return SimpleNamespace(client=_client)

    def inject_error(self, operation: str, code: str, message: str, *, times: int = 1, status: int = 400) -> None:
        self._injected.append(_InjectedError(operation, code, message, status, times))

    def expire_transaction(self, transaction_id: str) -> None:
        """Simula o timeout de 3 minutos ocioso [AWS-3]."""
        tx = self._transactions.pop(transaction_id)
        tx.conn.rollback()
        tx.conn.close()

    @property
    def open_transactions(self) -> list[str]:
        return list(self._transactions)

    def calls_of(self, operation: str) -> list[RecordedCall]:
        return [c for c in self.calls if c.operation == operation]

    def close(self) -> None:
        for tx_id in list(self._transactions):
            self.expire_transaction(tx_id)

    # -- transporte ----------------------------------------------------------

    def _before_send(self, request: Any, **_kwargs: Any) -> AWSResponse:
        path = request.url.split(FAKE_ENDPOINT, 1)[-1].split("?", 1)[0]
        operation = _OPERATIONS.get(path)
        params = json.loads(request.body or b"{}")
        call = RecordedCall(operation or path, params)
        self.calls.append(call)
        try:
            if operation is None:
                raise _bad(f"Operacao nao suportada pelo fake: {path}")
            self._raise_injected(operation)
            handler: Callable[[dict[str, Any]], dict[str, Any]] = getattr(self, f"_op_{operation}")
            with self._lock:
                body = handler(params)
            return self._response(request.url, 200, body)
        except _AwsError as err:
            call.error_code = err.code
            return self._response(
                request.url,
                err.status,
                {"message": err.message, "__type": err.code},
                {"x-amzn-ErrorType": f"{err.code}:"},
            )

    @staticmethod
    def _response(url: str, status: int, body: dict[str, Any], extra_headers: Optional[dict[str, str]] = None) -> AWSResponse:
        payload = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "Content-Length": str(len(payload))}
        headers.update(extra_headers or {})
        return AWSResponse(url, status, headers, _FakeRaw(payload))

    def _raise_injected(self, operation: str) -> None:
        for item in self._injected:
            if item.operation == operation and item.remaining > 0:
                item.remaining -= 1
                raise _AwsError(item.code, item.message, item.status)

    # -- conexoes ------------------------------------------------------------

    def _connect(self, database: str, *, autocommit: bool) -> pymysql.connections.Connection:
        # Sem CLIENT.MULTI_STATEMENTS: o proprio servidor tambem recusa multi-statement.
        return pymysql.connect(database=database, autocommit=autocommit, conv=_decoders(), **self._connect_kwargs)

    def _transaction(self, transaction_id: str) -> _Transaction:
        tx = self._transactions.get(transaction_id)
        if tx is None:
            raise _AwsError("TransactionNotFoundException", f"Transaction {transaction_id} is not found", 404)
        return tx

    @staticmethod
    def _require(params: dict[str, Any], *names: str) -> None:
        for name in names:
            if not params.get(name):
                raise _bad(f"Missing required parameter: {name}")
        if "schema" in params:
            raise _bad("The schema parameter isn't supported")

    # -- operacoes -----------------------------------------------------------

    def _op_BeginTransaction(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require(params, "resourceArn", "secretArn", "database")
        conn = self._connect(params["database"], autocommit=False)
        conn.begin()
        tx_id = secrets.token_urlsafe(96)
        self._transactions[tx_id] = _Transaction(conn, params["database"])
        return {"transactionId": tx_id}

    def _op_CommitTransaction(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require(params, "resourceArn", "secretArn", "transactionId")
        tx = self._transaction(params["transactionId"])
        del self._transactions[params["transactionId"]]
        try:
            tx.conn.commit()
        finally:
            tx.conn.close()
        return {"transactionStatus": "Transaction Committed"}

    def _op_RollbackTransaction(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require(params, "resourceArn", "secretArn", "transactionId")
        tx = self._transaction(params["transactionId"])
        del self._transactions[params["transactionId"]]
        try:
            tx.conn.rollback()
        finally:
            tx.conn.close()
        return {"transactionStatus": "Rollback Complete"}

    def _prepare(self, sql: str, parameters: Optional[list[dict[str, Any]]]) -> tuple[str, Optional[dict[str, Any]]]:
        scan = scan_sql(sql)
        if scan.statements > 1:
            raise _AwsError("DatabaseErrorException", "Multistatements aren't supported.")
        if not scan.names:
            # Sem placeholders o PyMySQL nao aplica %-formatting: SQL vai cru.
            return sql, None
        provided = {p["name"]: _param_to_python(p) for p in (parameters or [])}
        missing = [name for name in scan.names if name not in provided]
        if missing:
            raise _AwsError("DatabaseErrorException", f"No value specified for parameter: {missing[0]}")
        parts: list[str] = []
        for idx, segment in enumerate(scan.segments):
            parts.append(_escape_percent(segment))
            if idx < len(scan.names):
                parts.append(f"%({scan.names[idx]})s")
        return "".join(parts), provided

    def _run(self, params: dict[str, Any], sql: str, args: Optional[dict[str, Any]]) -> tuple[Any, pymysql.cursors.Cursor, bool]:
        tx_id = params.get("transactionId")
        if tx_id:
            conn = self._transaction(tx_id).conn
            disposable = False
        else:
            if not params.get("database"):
                raise _bad("Missing required parameter: database")
            conn = self._connect(params["database"], autocommit=True)
            disposable = True
        cur = conn.cursor()
        try:
            cur.execute(sql, args)
        except pymysql.err.Error as exc:
            cur.close()
            if disposable:
                conn.close()
            elif self.abort_transaction_on_error:
                self.expire_transaction(tx_id)
            code = exc.args[0] if exc.args else 0
            text = exc.args[1] if len(exc.args) > 1 else str(exc)
            raise _AwsError("DatabaseErrorException", f"Database error code: {code}. Message: {text}") from exc
        return conn, cur, disposable

    # Limite de 1 MiB do resultado [AWS-4] (ver docstring do modulo). Atributo
    # de classe (e nao parametro do __init__) para ser aditivo: um teste pode
    # sobrescrever por instancia (`fake.max_result_bytes = None` desliga).
    max_result_bytes: Optional[int] = RESULT_SIZE_LIMIT_BYTES

    def _check_result_size(self, records: list[Any]) -> None:
        if self.max_result_bytes is None:
            return
        size = len(json.dumps(records, separators=(",", ":")).encode("utf-8"))
        if size > self.max_result_bytes:
            raise _AwsError("UnsupportedResultException", "The result exceeds the size limit 1 MB.")

    def _op_ExecuteStatement(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require(params, "resourceArn", "secretArn", "sql")
        if params.get("formatRecordsAs", "NONE") != "NONE" or params.get("resultSetOptions"):
            raise FakeLimitationError("formatRecordsAs/resultSetOptions nao suportados pelo fake")
        sql, args = self._prepare(params["sql"], params.get("parameters"))
        conn, cur, disposable = self._run(params, sql, args)
        try:
            if cur.description:
                columns = cur._result.fields
                rows = cur.fetchall()
                records = [[_field_to_data_api(v, c) for v, c in zip(row, columns)] for row in rows]
                self._check_result_size(records)
                body: dict[str, Any] = {
                    "records": records,
                    "numberOfRecordsUpdated": 0,  # [INFERIDO]
                }
                if params.get("includeResultMetadata"):
                    body["columnMetadata"] = [_column_metadata(c) for c in columns]
                return body
            count = max(cur.rowcount, 0)
            if self.update_count_mode == "matched":
                matched = _ROWS_MATCHED_RE.search(cur._result.message or b"")
                if matched:
                    count = int(matched.group(1))
            generated = [{"longValue": cur.lastrowid}] if cur.lastrowid else []  # [INFERIDO]
            return {"numberOfRecordsUpdated": count, "generatedFields": generated}
        finally:
            cur.close()
            if disposable:
                conn.close()

    def _op_BatchExecuteStatement(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require(params, "resourceArn", "secretArn", "sql")
        results = []
        for parameter_set in params.get("parameterSets") or [[]]:
            sql, args = self._prepare(params["sql"], parameter_set)
            conn, cur, disposable = self._run(params, sql, args)
            try:
                if cur.description:
                    raise _AwsError("DatabaseErrorException", "BatchExecuteStatement doesn't return result sets")
                generated = [{"longValue": cur.lastrowid}] if cur.lastrowid else []
                results.append({"generatedFields": generated})
            finally:
                cur.close()
                if disposable:
                    conn.close()
        return {"updateResults": results}

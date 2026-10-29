"""Spike: shim de compatibilidade PyMySQL -> RDS Data API.

Objetivo: expor uma interface parecida o suficiente com o que os call-sites
do backend atual usam via `fitness-platform/api/app/db.py` (PyMySQL +
DictCursor) para que a maioria dos arquivos que fazem
`from .db import get_conn, transaction` continue funcionando quase sem
mudanca, trocando so o import.

Cobre deliberadamente APENAS o subconjunto de API realmente usado no
backend (validado por grep no repo de referencia, C:\\openclaw-dev-agent\\fitness-platform\\api\\app):

- `cursor.execute(sql, params)` com placeholders posicionais `%s` (estilo
  PyMySQL/paramstyle "format"), `params` como tupla/lista/None.
- `cursor.fetchone()` / `cursor.fetchall()` retornando `dict` (equivalente
  ao `DictCursor`).
- `cursor.lastrowid` apos INSERT com auto_increment.
- `cursor.rowcount` (usado em alguns callers para checar se um UPDATE/DELETE
  afetou linhas).
- `conn.commit()` / `conn.rollback()` / `conn.close()`.
- `with conn.cursor() as cur:` (context manager).
- A funcao `transaction()` como context manager (`with transaction() as conn:`),
  replicando o commit/rollback automatico do `db.py` original.

NAO cobre (fora de escopo do spike, ver docs/spike-data-api.md):
- `cursor.executemany()` (nao usado no backend de referencia).
- Streaming de result sets grandes (Data API sempre materializa a resposta
  inteira; para os volumes deste projeto, isso e aceitavel).
- Placeholders de LIKE por dentro de f-strings dinamicas complexas (o
  backend de referencia usa apenas `%s` posicional simples + `%%` literal
  para escapar `%` dentro de `DATE_FORMAT`/`LIKE` - ver `_translate_sql`).
"""

from __future__ import annotations

import datetime as _dt
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

import boto3

__all__ = ["Connection", "Cursor", "get_conn", "transaction", "DataApiError"]


class DataApiError(Exception):
    """Erro generico do shim (falha de execucao, parametro nao suportado etc)."""


# ---------------------------------------------------------------------------
# Traducao de SQL: placeholders %s (PyMySQL/paramstyle "format") -> :p0, :p1...
# ---------------------------------------------------------------------------
#
# ACHADO IMPORTANTE (ver docs/spike-data-api.md): o backend de referencia usa
# `%%` para escapar um `%` literal dentro do SQL (ex.: `DATE_FORMAT(d, '%%Y-%%m-%%d')`,
# `LIKE '%%muscul%%'`). Isso e necessario porque o PyMySQL, internamente, faz
# a substituicao de parametros via `%`-formatting estilo Python (paramstyle
# "format"), entao qualquer `%` literal precisa estar duplicado no texto para
# sobreviver a esse passo.
#
# O Data API NAO faz esse tipo de substituicao (usa parametros nomeados de
# verdade, `:p0`), entao um `%%` que chegasse intacto ate o Data API viraria
# SQL invalido (`DATE_FORMAT(d, '%%Y-%%m-%%d')` nao e sintaxe MySQL valida -
# o `%%` so faz sentido como uma camada de escape do PyMySQL, nao do MySQL
# em si). O shim precisa desfazer esse escape (`%%` -> `%`) ao mesmo tempo
# que troca `%s` -> `:pN`, senao toda query com DATE_FORMAT/LIKE quebra.
def _translate_sql(sql: str, params: Optional[Sequence[Any]]) -> tuple[str, list[dict[str, Any]]]:
    if not params:
        # Ainda assim e preciso desfazer o escape %% -> % para SQL estatico
        # (sem parametros) que contenha DATE_FORMAT/LIKE com % literal.
        return sql.replace("%%", "%"), []

    out_chars: list[str] = []
    named_params: list[dict[str, Any]] = []
    i = 0
    n = len(sql)
    param_idx = 0
    while i < n:
        ch = sql[i]
        if ch == "%" and i + 1 < n:
            nxt = sql[i + 1]
            if nxt == "%":
                out_chars.append("%")
                i += 2
                continue
            if nxt == "s":
                if param_idx >= len(params):
                    raise DataApiError(
                        f"SQL tem mais placeholders %s do que parametros fornecidos "
                        f"(placeholder #{param_idx + 1}, {len(params)} parametros)"
                    )
                name = f"p{param_idx}"
                out_chars.append(f":{name}")
                named_params.append(_to_data_api_param(name, params[param_idx]))
                param_idx += 1
                i += 2
                continue
        out_chars.append(ch)
        i += 1

    if param_idx != len(params):
        raise DataApiError(
            f"Numero de placeholders %s ({param_idx}) nao bate com o numero de "
            f"parametros fornecidos ({len(params)})"
        )

    return "".join(out_chars), named_params


def _to_data_api_param(name: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    if isinstance(value, bool):
        return {"name": name, "value": {"booleanValue": value}}
    if isinstance(value, int):
        return {"name": name, "value": {"longValue": value}}
    if isinstance(value, float):
        return {"name": name, "value": {"doubleValue": value}}
    if isinstance(value, Decimal):
        # Data API nao tem tipo decimal nativo em `value` - manda como string
        # tipada (DECIMAL), que o MySQL converte na coluna de destino.
        return {"name": name, "value": {"stringValue": str(value)}, "typeHint": "DECIMAL"}
    if isinstance(value, (_dt.datetime,)):
        return {
            "name": name,
            "value": {"stringValue": value.strftime("%Y-%m-%d %H:%M:%S")},
            "typeHint": "TIMESTAMP",
        }
    if isinstance(value, _dt.date):
        return {"name": name, "value": {"stringValue": value.isoformat()}, "typeHint": "DATE"}
    if isinstance(value, (bytes, bytearray)):
        return {"name": name, "value": {"blobValue": bytes(value)}}
    if isinstance(value, str):
        return {"name": name, "value": {"stringValue": value}}
    # dict/list (colunas JSON) - serializar como string com typeHint JSON.
    # Callers do backend de referencia (ver db.py/services.py `to_json`) ja
    # serializam JSON manualmente antes de passar como parametro (PyMySQL
    # tambem nao serializa dict/list automaticamente), entao normalmente
    # chega aqui como str. Mantido como fallback defensivo.
    import json as _json

    return {"name": name, "value": {"stringValue": _json.dumps(value, ensure_ascii=False, default=str)}, "typeHint": "JSON"}


def _decode_field(field: dict[str, Any]) -> Any:
    if field.get("isNull"):
        return None
    for key in ("stringValue", "longValue", "doubleValue", "booleanValue", "blobValue"):
        if key in field:
            return field[key]
    if "arrayValue" in field:
        return field["arrayValue"]
    return None


def _row_to_dict(columns: list[str], values: list[dict[str, Any]]) -> dict[str, Any]:
    return {col: _decode_field(val) for col, val in zip(columns, values)}


class Cursor:
    def __init__(self, conn: "Connection"):
        self._conn = conn
        self._rows: list[dict[str, Any]] = []
        self._pos = 0
        self.lastrowid: Optional[int] = None
        self.rowcount: int = -1

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> "Cursor":
        translated_sql, named_params = _translate_sql(sql, params)
        kwargs: dict[str, Any] = {
            "resourceArn": self._conn.resource_arn,
            "secretArn": self._conn.secret_arn,
            "database": self._conn.database,
            "sql": translated_sql,
            "includeResultMetadata": True,
        }
        if named_params:
            kwargs["parameters"] = named_params
        if self._conn.transaction_id:
            kwargs["transactionId"] = self._conn.transaction_id

        try:
            resp = self._conn.client.execute_statement(**kwargs)
        except Exception as exc:  # noqa: BLE001 - repassa com contexto do SQL
            raise DataApiError(f"execute_statement falhou para SQL: {translated_sql!r} -- {exc}") from exc

        column_meta = resp.get("columnMetadata") or []
        columns = [c.get("label") or c.get("name") for c in column_meta]
        records = resp.get("records") or []
        self._rows = [_row_to_dict(columns, rec) for rec in records]
        self._pos = 0

        generated = resp.get("generatedFields") or []
        if generated:
            gf = generated[0]
            self.lastrowid = gf.get("longValue")
        self.rowcount = resp.get("numberOfRecordsUpdated", len(self._rows))
        return self

    def fetchone(self) -> Optional[dict[str, Any]]:
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self) -> list[dict[str, Any]]:
        rows = self._rows[self._pos :]
        self._pos = len(self._rows)
        return rows

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def __iter__(self) -> Iterable[dict[str, Any]]:
        return iter(self._rows)


class Connection:
    """Uma "conexao logica": nao ha socket TCP de verdade, mas mantemos uma
    `transactionId` real do Data API do inicio (construtor) ate `commit`/
    `rollback`, exatamente como foi feito na migracao de dados da Fase 1
    (`BeginTransaction`/`CommitTransaction`) - sem isso, cada
    `execute_statement` roda isolado e SET/estado de sessao (e mesmo o
    isolamento de uma sequencia de INSERTs relacionados) nao persiste entre
    chamadas."""

    def __init__(self, resource_arn: str, secret_arn: str, database: str, region_name: str = "sa-east-1"):
        self.resource_arn = resource_arn
        self.secret_arn = secret_arn
        self.database = database
        self.client = boto3.client("rds-data", region_name=region_name)
        self.transaction_id: Optional[str] = None
        self._begin()

    def _begin(self) -> None:
        resp = self.client.begin_transaction(
            resourceArn=self.resource_arn,
            secretArn=self.secret_arn,
            database=self.database,
        )
        self.transaction_id = resp["transactionId"]

    def cursor(self) -> Cursor:
        return Cursor(self)

    def commit(self) -> None:
        if self.transaction_id:
            self.client.commit_transaction(
                resourceArn=self.resource_arn,
                secretArn=self.secret_arn,
                transactionId=self.transaction_id,
            )
            self.transaction_id = None

    def rollback(self) -> None:
        if self.transaction_id:
            self.client.rollback_transaction(
                resourceArn=self.resource_arn,
                secretArn=self.secret_arn,
                transactionId=self.transaction_id,
            )
            self.transaction_id = None

    def close(self) -> None:
        # Se ninguem deu commit/rollback explicito, aborta a transacao aberta
        # em vez de deixar pendurada no Aurora (paridade com o comportamento
        # de fechar uma conexao PyMySQL sem commit -> rollback implicito).
        if self.transaction_id:
            self.rollback()


def get_conn(resource_arn: str, secret_arn: str, database: str, region_name: str = "sa-east-1") -> Connection:
    return Connection(resource_arn, secret_arn, database, region_name)


@contextmanager
def transaction(resource_arn: str, secret_arn: str, database: str, region_name: str = "sa-east-1"):
    conn = get_conn(resource_arn, secret_arn, database, region_name)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

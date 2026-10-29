"""Dublê do client boto3 `rds-data`, apoiado num MySQL local (PyMySQL).

Existe para validar o caminho de ESCRITA da ferramenta sem depender de
credencial AWS e sem nunca tocar o Aurora de producao. Ele reproduz de
proposito duas caracteristicas do RDS Data API que sao a origem da maior
parte dos bugs sutis nesse tipo de integracao:

1. **Estado de sessao so sobrevive dentro de uma transacao.** Toda chamada
   FORA de transacao recebe uma conexao nova e descartavel, exatamente como
   o pool do Data API faz. Se o codigo de producao dependesse de um
   `SET time_zone` solto, o teste quebraria aqui - que e o ponto.
2. **Parametros nomeados `:nome`**, e nao `%s` posicional.

PyMySQL e dependencia SOMENTE DE TESTE. A ferramenta em si continua usando
apenas boto3 + stdlib.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pymysql


# `:nome` precisa comecar com letra/underscore: sem isso, o literal
# '+00:00' de `SET SESSION time_zone` seria lido como um parametro `:00`.
_PARAM_RE = re.compile(r":([A-Za-z_]\w*)")


def _to_python(param: dict[str, Any]) -> Any:
    value = param["value"]
    if value.get("isNull"):
        return None
    for key in ("stringValue", "longValue", "doubleValue", "booleanValue", "blobValue"):
        if key in value:
            return value[key]
    return None


def _translate(sql: str, parameters: list[dict[str, Any]] | None) -> tuple[str, dict[str, Any]]:
    if not parameters:
        return sql, {}
    mapping = {p["name"]: _to_python(p) for p in parameters}
    return _PARAM_RE.sub(lambda m: f"%({m.group(1)})s", sql), mapping


class FakeRdsDataClient:
    """Implementa apenas o subconjunto da API `rds-data` usado pela ferramenta."""

    def __init__(self, **connect_kwargs: Any) -> None:
        self._connect_kwargs = dict(connect_kwargs)
        self._connect_kwargs.setdefault("charset", "utf8mb4")
        self._connect_kwargs.setdefault("autocommit", False)
        self._transactions: dict[str, pymysql.connections.Connection] = {}
        self.call_count = 0

    # -- conexoes ----------------------------------------------------------

    def _connect(self, database: str) -> pymysql.connections.Connection:
        return pymysql.connect(database=database, **self._connect_kwargs)

    def _conn_for(self, kwargs: dict[str, Any]) -> tuple[pymysql.connections.Connection, bool]:
        tx = kwargs.get("transactionId")
        if tx:
            if tx not in self._transactions:
                raise RuntimeError(f"transactionId desconhecido: {tx}")
            return self._transactions[tx], False
        return self._connect(kwargs["database"]), True

    # -- transacao ---------------------------------------------------------

    def begin_transaction(self, **kwargs: Any) -> dict[str, Any]:
        tx = uuid.uuid4().hex
        conn = self._connect(kwargs["database"])
        conn.begin()
        self._transactions[tx] = conn
        return {"transactionId": tx}

    def commit_transaction(self, **kwargs: Any) -> dict[str, Any]:
        conn = self._transactions.pop(kwargs["transactionId"])
        conn.commit()
        conn.close()
        return {"transactionStatus": "Transaction Committed"}

    def rollback_transaction(self, **kwargs: Any) -> dict[str, Any]:
        conn = self._transactions.pop(kwargs["transactionId"])
        conn.rollback()
        conn.close()
        return {"transactionStatus": "Rollback Complete"}

    # -- execucao ----------------------------------------------------------

    @staticmethod
    def _field(value: Any) -> dict[str, Any]:
        if value is None:
            return {"isNull": True}
        if isinstance(value, bool):
            return {"booleanValue": value}
        if isinstance(value, int):
            return {"longValue": value}
        if isinstance(value, float):
            return {"doubleValue": value}
        if isinstance(value, (bytes, bytearray)):
            return {"blobValue": bytes(value)}
        return {"stringValue": str(value)}

    def execute_statement(self, **kwargs: Any) -> dict[str, Any]:
        self.call_count += 1
        sql, params = _translate(kwargs["sql"], kwargs.get("parameters"))
        conn, disposable = self._conn_for(kwargs)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or None)
                result: dict[str, Any] = {"numberOfRecordsUpdated": cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0}
                if cur.description:
                    labels = [d[0] for d in cur.description]
                    rows = cur.fetchall()
                    result["records"] = [[self._field(v) for v in row] for row in rows]
                    result["numberOfRecordsUpdated"] = 0
                    if kwargs.get("includeResultMetadata"):
                        result["columnMetadata"] = [{"label": lbl, "name": lbl} for lbl in labels]
                else:
                    result["records"] = []
                    if cur.lastrowid:
                        result["generatedFields"] = [{"longValue": cur.lastrowid}]
            if disposable:
                conn.commit()
            return result
        finally:
            if disposable:
                conn.close()

    def batch_execute_statement(self, **kwargs: Any) -> dict[str, Any]:
        self.call_count += 1
        parameter_sets = kwargs.get("parameterSets") or []
        if not parameter_sets:
            return {"updateResults": []}
        conn, disposable = self._conn_for(kwargs)
        try:
            sql, _ = _translate(kwargs["sql"], parameter_sets[0])
            payloads = [{p["name"]: _to_python(p) for p in ps} for ps in parameter_sets]
            with conn.cursor() as cur:
                cur.executemany(sql, payloads)
            if disposable:
                conn.commit()
            return {"updateResults": [{} for _ in parameter_sets]}
        finally:
            if disposable:
                conn.close()

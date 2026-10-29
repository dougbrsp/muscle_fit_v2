"""Captura do SQL que a aplicacao envia ao banco, igual nos dois backends.

- PyMySQL: todo `execute` do cursor real (`pymysql.cursors.Cursor.execute`,
  herdado pelo `DictCursor`).
- Data API: o `sql` de cada chamada ao dublê (ja traduzido pelo shim).

Usado para provar que caminhos de leitura nao escrevem (#89) e que as rotas/
handler de cardio nao emitem DDL (025).
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterator

import pymysql.cursors

WRITE_SQL = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|TRUNCATE|RENAME)\b", re.IGNORECASE)
DDL_SQL = re.compile(r"^\s*(CREATE|ALTER|DROP|TRUNCATE|RENAME)\b", re.IGNORECASE)


@contextmanager
def recorded_sql(backend: str, monkeypatch: Any, fake_rds: Any) -> Iterator[list[str]]:
    """SQL enviado ao banco dentro do bloco."""
    statements: list[str] = []
    if backend == "pymysql":
        original = pymysql.cursors.Cursor.execute

        def execute(self, query, args=None):
            statements.append(str(query))
            return original(self, query, args)

        monkeypatch.setattr(pymysql.cursors.Cursor, "execute", execute)
        try:
            yield statements
        finally:
            monkeypatch.setattr(pymysql.cursors.Cursor, "execute", original)
    else:
        start = len(fake_rds.calls)
        try:
            yield statements
        finally:
            statements.extend(str(call.params["sql"]) for call in fake_rds.calls[start:] if "sql" in call.params)


def _short(statement: str) -> str:
    return " ".join(statement.split())[:120]


def writes(statements: list[str]) -> list[str]:
    return [_short(s) for s in statements if WRITE_SQL.match(s)]


def ddl(statements: list[str]) -> list[str]:
    return [_short(s) for s in statements if DDL_SQL.match(s)]

"""Dispatcher fino da camada de acesso a dados - sem logica de negocio aqui.

A logica real vive em `db_dataapi.py` (RDS Data API do Aurora Serverless v2,
usado em producao AWS) e `db_pymysql.py` (PyMySQL puro sobre TCP, usado em
desenvolvimento local contra um MySQL real). Este arquivo so escolhe qual dos
dois modulos reexportar, conforme a variavel de ambiente `DB_BACKEND`:

- `DB_BACKEND` ausente ou `"data-api"` (default): usa `db_dataapi.py`. Isso e
  critico para que producao continue funcionando sem nenhuma mudanca de
  configuracao.
- `DB_BACKEND=pymysql`: usa `db_pymysql.py`.

Os ~30 call-sites do backend continuam importando so `from .db import ...`;
nenhum deles deve importar `db_dataapi`/`db_pymysql` diretamente.
"""

from __future__ import annotations

import os

from .runtime_guard import exigir_data_api_em_lambda

__all__ = [
    "Connection",
    "Cursor",
    "fetch_rows",
    "get_conn",
    "transaction",
    "DataApiError",
    "is_duplicate_column_error",
    "is_duplicate_key_error",
    "is_unknown_key_error",
]

# Recusa pymysql dentro de Lambda real ANTES de importar db_pymysql (anti-vazamento).
exigir_data_api_em_lambda()

if os.environ.get("DB_BACKEND", "data-api") == "pymysql":
    from .db_pymysql import (
        Connection,
        Cursor,
        DataApiError,
        fetch_rows,
        get_conn,
        is_duplicate_column_error,
        is_duplicate_key_error,
        is_unknown_key_error,
        transaction,
    )
else:
    from .db_dataapi import (
        Connection,
        Cursor,
        DataApiError,
        fetch_rows,
        get_conn,
        is_duplicate_column_error,
        is_duplicate_key_error,
        is_unknown_key_error,
        transaction,
    )

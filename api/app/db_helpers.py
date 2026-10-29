"""Helpers de acesso a dados repetidos em varios modulos (COD-02 da auditoria
auditoria de 2026-10-08, arquivo privado).

Extracao mecanica: cada funcao aqui e o corpo que ja existia, IDENTICO, em
varios modulos. Os modulos continuam expondo o nome antigo (`_connect`,
`_rows`, `_jsonable`, `_fetchall`), entao chamadas internas, imports cruzados
e `monkeypatch.setattr(modulo, "_connect", ...)` dos testes seguem iguais.
Variantes com qualquer diferenca de comportamento (outra mensagem, log, outro
formato de TIME, sem `list(...)`) ficaram nos modulos de origem.

Sem regra de negocio: nada de motor aqui (docs/motores/FONTES_DA_VERDADE.md).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Optional

from fastapi import HTTPException

from .db import DataApiError

logger = logging.getLogger("fitness.db_helpers")

# Mensagens do 503 de banco indisponivel, como estavam nos modulos.
# SEG-12: `DB_UNAVAILABLE_GENERIC` e a mesma de `body_composition`/
# `cardio_recommendations`, usada quando o modulo nao informa a sua.
DB_UNAVAILABLE_GENERIC = "Banco de dados indisponível."
DB_UNAVAILABLE_MYSQL = "Não foi possível conectar ao MySQL da Fitness API."
DB_UNAVAILABLE_RDS = "Não foi possível conectar ao banco (RDS Data API) da Fitness API."


def connect_or_503(get_conn: Callable[[], Any], message: Optional[str] = None) -> Any:
    """`get_conn()` ou HTTP 503 `database_unavailable` se o banco nao responde.

    `get_conn` vem do modulo chamador (o nome importado de `app.db` la), para
    manter o mesmo alvo de `monkeypatch` de antes. Corpo do 503: `error` e
    `message` (a do modulo ou `DB_UNAVAILABLE_GENERIC`).

    SEG-12: o texto da excecao NAO vai mais no corpo (antes ia em `exception`);
    vai para o log com stack. `DataApiError` traz o SQL com placeholders, sem
    os valores dos parametros."""
    try:
        return get_conn()
    except DataApiError:
        logger.exception("Banco indisponivel ao conectar; respondendo 503")
        raise HTTPException(
            status_code=503,
            detail={"error": "database_unavailable", "message": message or DB_UNAVAILABLE_GENERIC},
        )


def rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Executa `sql` e devolve todas as linhas como lista (vazia se nao houver)."""
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall() or [])


def jsonable(value: Any) -> Any:
    """Decimal -> float; date/datetime -> ISO 8601; o resto passa igual."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


__all__ = ["DB_UNAVAILABLE_GENERIC", "DB_UNAVAILABLE_MYSQL", "DB_UNAVAILABLE_RDS", "connect_or_503", "jsonable", "rows"]

"""Deteccao da tabela `body_measurements` (migracao 047) - compartilhada pelo
CRUD da api-core (`app/body_measurements.py`) e pelo `body_service` da
engines (`app/intelligence/dashboard/body_service.py`).

Janela de deploy: o codigo novo pode atender requisicoes ANTES de a 047
rodar. Nesse intervalo a leitura devolve vazio com a limitacao declarada e a
escrita responde 503 citando a migracao - nunca 500, nunca DDL por aqui (quem
cria a tabela e a 047, pelo pipeline).

Cache SO DO POSITIVO, chaveado por SCHEMA - a mesma regra (e a mesma licao,
PR #182) de `load_conventions.has_load_convention_column`: "a tabela existe"
e permanente; "nao existe" e apenas "a migracao ainda nao rodou" e memoriza-
la envenenaria o processo ate um restart. Enquanto a deteccao der negativa,
a consulta a `information_schema.TABLES` se repete a cada chamada; depois
que a tabela existe, no maximo uma vez por schema.
"""

from __future__ import annotations

from typing import Any

from .load_conventions import current_schema

TABLE = "body_measurements"
MIGRATION = "047"

TABLE_MISSING_LIMITATION = (
    "Medidas corporais indisponíveis: a tabela `body_measurements` (migração 047) ainda não "
    "existe neste banco."
)

_TABLE_EXISTS_SQL = (
    "SELECT COUNT(*) AS n FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
)

_schemas_with_table: set[str] = set()


def has_body_measurements_table(cursor: Any) -> bool:
    """`body_measurements` ja existe NESTE schema? Sem DDL."""
    schema = current_schema(cursor)
    if schema in _schemas_with_table:
        return True
    cursor.execute(_TABLE_EXISTS_SQL, (TABLE,))
    row = cursor.fetchone()
    exists = int((row or {}).get("n") or 0) > 0
    if exists:
        _schemas_with_table.add(schema)
    return exists


def reset_body_measurements_table_cache() -> None:
    """Esquece a deteccao de TODOS os schemas (so importa para um teste que
    DESFAZ a 047 e volta o schema a um estado anterior a ela)."""
    _schemas_with_table.clear()


__all__ = [
    "MIGRATION",
    "TABLE",
    "TABLE_MISSING_LIMITATION",
    "has_body_measurements_table",
    "reset_body_measurements_table_cache",
]

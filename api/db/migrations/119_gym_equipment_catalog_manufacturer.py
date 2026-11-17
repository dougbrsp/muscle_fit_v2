"""119 - Fabricante do aparelho por academia (issue #188).

A mesma maquina do catalogo pode ser de fabricantes diferentes em academias
diferentes. `gym_equipment_catalog` (uma linha por academia x equipamento)
ganha `manufacturer VARCHAR(100) NULL`, o mesmo tipo de
`equipment.manufacturer` (022). NULL = a academia nao informou: vale o do
catalogo (resolvido na leitura pelo dono `app/domain/equipment_context`).
Sem copia de dados, sem DML, sem FK.

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso (`DROP COLUMN`).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com dois-pontos; modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

TABLE = "gym_equipment_catalog"
COLUMN = "manufacturer"
COLUMN_DDL = "VARCHAR(100) NULL"
EXPECTED_TYPE = "varchar(100)"


class ManufacturerError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def add_column_sql() -> str:
    return f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {COLUMN_DDL}"


def drop_column_sql() -> str:
    return f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"


def _read(db):
    """(tabela existe?, linha da coluna ou None)."""
    exists = bool(
        db.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (TABLE,),
        )
    )
    rows = db.query(
        "SELECT TABLE_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (TABLE, COLUMN),
    )
    return exists, (rows[0] if rows else None)


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, column = _read(db)
    if not exists:
        if getattr(db, "dry_run", False):  # banco atrasado: so LISTA o DDL
            return [(add_column_sql(), None)]
        raise ManufacturerError(f"119: tabela {TABLE} nao existe. Nenhum statement executado.")
    if column is None:
        return [(add_column_sql(), None)]
    found = _text(column["COLUMN_TYPE"]).strip().lower()
    if found != EXPECTED_TYPE or _text(column["IS_NULLABLE"]).upper() != "YES":
        raise ManufacturerError(
            f"119: {TABLE}.{COLUMN} e {found} (nullable={_text(column['IS_NULLABLE'])}), "
            f"esperado {EXPECTED_TYPE} NULL. Nenhum statement executado."
        )
    return []


def plan_down(db) -> list[tuple[str, tuple | None]]:
    exists, column = _read(db)
    return [(drop_column_sql(), None)] if exists and column is not None else []


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

"""121 - RPE esperado da serie gravado (issue #182).

`exercise_sets` ganha `rpe_target_planned DECIMAL(3,1) NULL DEFAULT NULL`: o
RPE esperado DAQUELA serie (o que a ficha mostrou no Modo Treino: degrau da
rampa limitado pelo alvo efetivo), gravado no POST da serie para comparar com
o `rpe` realizado. `rpe_target_effective` (alvo do exercicio) nao muda. Serie
ja gravada fica NULL: sem backfill, sem indice, sem FK.

`ALGORITHM=INSTANT` (so metadado); falha em vez de copiar a tabela em silencio.
`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso (`DROP COLUMN`).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com dois-pontos; modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

TABLE = "exercise_sets"
COLUMN = "rpe_target_planned"
COLUMN_DDL = "DECIMAL(3,1) NULL DEFAULT NULL"
EXPECTED_TYPE = "decimal(3,1)"


class RpeTargetPlannedError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def add_column_sql() -> str:
    return f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {COLUMN_DDL}, ALGORITHM=INSTANT"


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
        "SELECT TABLE_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
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
        raise RpeTargetPlannedError(f"121: tabela {TABLE} nao existe. Nenhum statement executado.")
    if column is None:
        return [(add_column_sql(), None)]
    found = "".join(_text(column["COLUMN_TYPE"]).split()).lower()
    nullable = _text(column["IS_NULLABLE"]).upper()
    default = None if column.get("COLUMN_DEFAULT") is None else _text(column["COLUMN_DEFAULT"])
    if found != EXPECTED_TYPE or nullable != "YES" or default is not None:
        raise RpeTargetPlannedError(
            f"121: {TABLE}.{COLUMN} e {found} (nullable={nullable}, default={default!r}), "
            f"esperado {EXPECTED_TYPE} NULL DEFAULT NULL. Nenhum statement executado."
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

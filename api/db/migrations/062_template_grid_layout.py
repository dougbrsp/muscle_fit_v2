"""062 - grade configuravel do editor de Modelo (linhas x colunas de slots).

    workout_plan_templates
      + grid_rows  TINYINT NULL
      + grid_cols  TINYINT NULL

Por que: o editor de Modelo passa a ser uma grade de slots (1-2 linhas x 1-4
colunas, no maximo 8 posicoes). A grade e lida linha por linha e essa ordem E
a `sequence` do ciclo - nada muda no modelo de dados da ordem. As duas colunas
so guardam a FORMA da grade escolhida pelo profissional.

--- Decisoes do DDL -------------------------------------------------------

1. **NULL = automatico.** As colunas nascem NULL em todas as linhas e o
   cliente trata NULL como o comportamento de hoje (grade calculada a partir
   da quantidade de slots). Sem backfill, sem DEFAULT: nenhum Modelo
   existente muda.

2. **TINYINT.** Os limites (1-2 linhas, 1-4 colunas) sao da API (Pydantic).

3. **Sem `AFTER`.** Mesmo motivo da 058: as colunas vao para o fim; o codigo
   sempre nomeia as colunas.

Dependencias: so `workout_plan_templates` (007). Nenhuma migracao recente e
pre-requisito, entao o DRY-RUN planeja a 062 igual ao deploy real.

Fluxo (mesmo padrao da 058): `plan(db)` so LE `information_schema`;
`up(db)` executa o plano; `down(db)`/`plan_down(db)` sao o inverso.

- coluna ausente -> 1 `ALTER TABLE ... ADD COLUMN` por coluna ausente;
- coluna presente como `tinyint` NULL -> nada (reexecucao: 0 statements);
- coluna presente com outro tipo/nulabilidade, ou tabela ausente ->
  `TemplateGridLayoutError` ANTES do primeiro statement.

Aurora (sem as colunas) -> 2 statements. Reexecucao: 0.
`down` -> 1 `DROP COLUMN` por coluna presente (reversivel: sem as colunas,
todo Modelo volta a grade automatica).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "workout_plan_templates"
COLUMNS = ("grid_rows", "grid_cols")

ADD_GRID_ROWS_SQL = f"ALTER TABLE {TABLE} ADD COLUMN grid_rows TINYINT NULL"
ADD_GRID_COLS_SQL = f"ALTER TABLE {TABLE} ADD COLUMN grid_cols TINYINT NULL"
DROP_GRID_ROWS_SQL = f"ALTER TABLE {TABLE} DROP COLUMN grid_rows"
DROP_GRID_COLS_SQL = f"ALTER TABLE {TABLE} DROP COLUMN grid_cols"

_ADD = {"grid_rows": ADD_GRID_ROWS_SQL, "grid_cols": ADD_GRID_COLS_SQL}
_DROP = {"grid_rows": DROP_GRID_ROWS_SQL, "grid_cols": DROP_GRID_COLS_SQL}

EXPECTED_TYPE = "tinyint"
EXPECTED_NULLABLE = "YES"

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class TemplateGridLayoutError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise TemplateGridLayoutError(f"062: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `tinyint(4)` -> `tinyint`
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db) -> tuple[dict[str, str], dict[str, dict]]:
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
    }
    columns: dict[str, dict] = {}
    for row in db.query(
        "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME IN (%s) AND COLUMN_NAME IN (%s, %s)",
        (TABLE, *COLUMNS),
    ):
        name = _text(row["COLUMN_NAME"])
        if _text(row["TABLE_NAME"]) == TABLE and name in COLUMNS:
            columns[name] = row
    return engines, columns


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns = _read(db)
    if TABLE not in engines:
        _fail(f"tabela {TABLE} nao existe")
    statements: list[tuple[str, tuple | None]] = []
    for name in COLUMNS:
        column = columns.get(name)
        if column is None:
            statements.append((_ADD[name], None))
            continue
        if _normalize_type(column["COLUMN_TYPE"]) != EXPECTED_TYPE:
            _fail(f"{TABLE}.{name} e {_text(column['COLUMN_TYPE'])}, esperado {EXPECTED_TYPE}")
        if _text(column["IS_NULLABLE"]) != EXPECTED_NULLABLE:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(column['IS_NULLABLE'])}, esperado {EXPECTED_NULLABLE}")
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: DROP COLUMN so das colunas que existem."""
    engines, columns = _read(db)
    if TABLE not in engines:
        return []
    return [(_DROP[name], None) for name in reversed(COLUMNS) if name in columns]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

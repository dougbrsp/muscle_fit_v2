"""100 - reps e RPE por lado em exercício unilateral (M102, 2026-10-27;
dono da regra: `app/domain/set_sides.py`).

    exercise_sets
      + reps_left    INT NULL             (reps do lado esquerdo)
      + reps_right   INT NULL             (reps do lado direito)
      + rpe_left     DECIMAL(4,2) NULL    (RPE do lado esquerdo; mesmo tipo de `rpe`)
      + rpe_right    DECIMAL(4,2) NULL    (RPE do lado direito)

Por que: o usuário pediu reps e RPE por lado nas séries de exercício
unilateral, numa linha só por série. `reps`/`rpe` continuam sendo os valores
oficiais (pior lado, calculados pelo dono na gravação); a carga segue única e
TOTAL em `weight_kg`; a chave única `(exercise_id, set_number)` não muda.

--- Decisões do DDL -------------------------------------------------------

1. **Colunas NULL, sem default, sem backfill.** Toda linha existente fica com
   os lados NULL, e o canônico (`reps`/`rpe`) continua o mesmo de antes.
   INSERT que não cite as colunas (relógio, importadores) grava NULL.
2. **Mesmos tipos das colunas canônicas** (`reps INT`, `rpe DECIMAL(4,2)`).
3. **Um `ALTER TABLE ... ADD COLUMN ..., ALGORITHM=INSTANT` por coluna**
   (mesmo racional da 089: só metadado no MySQL 8.0 / Aurora 3; o ALGORITHM
   explícito falha em vez de copiar a tabela em silêncio).
4. **Nenhuma FK, nenhum índice.**

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` são o inverso.

- coluna ausente -> `ADD COLUMN`; presente com outro tipo, nulabilidade ou
  default -> `SeriesPorLadoError` antes do primeiro statement;
- banco atual -> 4 statements; convergido -> 0;
- tabela ausente: erro numa execução real; aceito em DRY-RUN (banco novo).

`down`: `DROP COLUMN` de cada coluna que existir (perde só os lados; `reps` e
`rpe` oficiais ficam).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com ':'; módulo autocontido (não importa código do app).
"""

from __future__ import annotations

REPS_DDL = "INT NULL DEFAULT NULL"
RPE_DDL = "DECIMAL(4,2) NULL DEFAULT NULL"
REPS_TYPE = "int"
RPE_TYPE = "decimal(4,2)"

# (tabela, coluna, DDL, tipo esperado, IS_NULLABLE esperado, default esperado) na ordem de execução.
TARGETS: tuple[tuple[str, str, str, str, str, str | None], ...] = (
    ("exercise_sets", "reps_left", REPS_DDL, REPS_TYPE, "YES", None),
    ("exercise_sets", "reps_right", REPS_DDL, REPS_TYPE, "YES", None),
    ("exercise_sets", "rpe_left", RPE_DDL, RPE_TYPE, "YES", None),
    ("exercise_sets", "rpe_right", RPE_DDL, RPE_TYPE, "YES", None),
)

TABLES: tuple[str, ...] = tuple(dict.fromkeys(table for table, *_rest in TARGETS))
COLUMNS: tuple[str, ...] = tuple(sorted({column for _table, column, *_rest in TARGETS}))


def add_column_sql(table: str, column: str, ddl: str) -> str:
    # INSTANT explícito: se não for elegível, falha na hora em vez de copiar a
    # tabela em silêncio. No deploy o migrate roda antes de as Lambdas
    # receberem o código novo: falhar aqui é seguro.
    return f"ALTER TABLE {table} ADD COLUMN {column} {ddl}, ALGORITHM=INSTANT"


def drop_column_sql(table: str, column: str) -> str:
    return f"ALTER TABLE {table} DROP COLUMN {column}"


class SeriesPorLadoError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise SeriesPorLadoError(f"100: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _read(db):
    """(tabelas existentes, {(tabela, coluna): linha de information_schema.COLUMNS})."""
    table_placeholders = ", ".join(["%s"] * len(TABLES))
    column_placeholders = ", ".join(["%s"] * len(COLUMNS))
    tables = {
        _text(row["TABLE_NAME"])
        for row in db.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({table_placeholders})",
            TABLES,
        )
    }
    columns = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({table_placeholders}) "
            f"AND COLUMN_NAME IN ({column_placeholders})",
            (*TABLES, *COLUMNS),
        )
    }
    return tables, columns


def _base_type(column_type: str) -> str:
    """`int(11)` (MySQL < 8.0.19) e `int` são o mesmo tipo; `decimal(4,2)` mantém a precisão."""
    text = column_type.strip().lower()
    return text.split("(", 1)[0] if text.startswith("int") else text


def _check_column(table: str, column: str, expected_type: str, expected_nullable: str, expected_default, info) -> None:
    found_type = _base_type(_text(info["COLUMN_TYPE"]))
    if found_type != expected_type:
        _fail(f"{table}.{column} e {found_type}, esperado {expected_type}")
    found_nullable = _text(info["IS_NULLABLE"]).upper()
    if found_nullable != expected_nullable:
        _fail(f"{table}.{column} e {'NULL' if found_nullable == 'YES' else 'NOT NULL'}, esperado {'NULL' if expected_nullable == 'YES' else 'NOT NULL'}")
    raw_default = info.get("COLUMN_DEFAULT")
    found_default = None if raw_default is None else _text(raw_default)
    if found_default != expected_default:
        _fail(f"{table}.{column} tem default {found_default!r}, esperado {expected_default!r}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    tables, columns = _read(db)
    statements: list[tuple[str, tuple | None]] = []
    for table, column, ddl, expected_type, expected_nullable, expected_default in TARGETS:
        if table not in tables:
            if bool(getattr(db, "dry_run", False)):
                statements.append((add_column_sql(table, column, ddl), None))
                continue
            _fail(f"tabela {table} nao existe")
        info = columns.get((table, column))
        if info is None:
            statements.append((add_column_sql(table, column, ddl), None))
        else:
            _check_column(table, column, expected_type, expected_nullable, expected_default, info)
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta cada coluna que existir (ordem inversa)."""
    tables, columns = _read(db)
    statements: list[tuple[str, tuple | None]] = []
    for table, column, *_rest in reversed(TARGETS):
        if table in tables and (table, column) in columns:
            statements.append((drop_column_sql(table, column), None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

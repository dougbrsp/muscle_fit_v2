"""089 - medidas de série: repetições, tempo e máximo de repetições
(Lote T1, 2026-10-20; dono do vocabulário: `app/domain/set_measures.py`).

    exercise_catalog
      + default_measure          VARCHAR(16) NULL            (sugestão por exercício; NULL = sem sugestão)
    workout_plan_template_slot_items
      + measure                  VARCHAR(16) NOT NULL DEFAULT 'reps'
      + duration_seconds_target  INT NULL                    (alvo da série por tempo)
      + time_window_seconds      INT NULL                    (janela do "máximo de reps em N s")
    workout_plan_exercises
      + measure                  VARCHAR(16) NOT NULL DEFAULT 'reps'
      + duration_seconds_target  INT NULL
      + time_window_seconds      INT NULL
    exercise_sets
      + measure                  VARCHAR(16) NOT NULL DEFAULT 'reps'
      + duration_seconds         INT NULL                    (tempo da série por tempo; janela usada em `max_reps`)

Por que: o usuário pediu exercício por tempo (prancha, 3 × 1 min, cronometrado
no Modo Treino) e série de máximo de repetições (com janela de tempo opcional).
Hoje não existe medida nem duração por série: `exercise_sets.reps` é a única
quantidade. Este lote só abre o schema; nenhum consumidor grava ou lê as colunas
(PRs T2-T5).

--- Decisões do DDL -------------------------------------------------------

1. **`measure NOT NULL DEFAULT 'reps'`, sem backfill.** Toda linha existente
   passa a dizer `reps` pelo DEFAULT (é o que ela sempre foi), e todo INSERT
   que não cite a coluna (celular, relógio antigo, importadores, `POST
   /activities`) grava `reps`. O valor default é o `DEFAULT_MEASURE` do dono;
   um teste confere a igualdade (a migração é autocontida e não importa o app).
2. **`VARCHAR(16)` livre, não ENUM.** Mesma decisão de `set_type`: vocabulário
   normalizado na leitura/gravação pelo dono, sem ALTER de ENUM a cada valor
   novo. 16 cabe o maior valor (`max_reps`, 8) com folga.
3. **Colunas de tempo NULL, sem default.** Só a medida correspondente as
   preenche; o dono valida a forma (`validate_item_measure`,
   `validate_set_measure`).
4. **Um `ALTER TABLE ... ADD COLUMN ..., ALGORITHM=INSTANT` por coluna.** No
   MySQL 8.0 / Aurora MySQL 3, `ADD COLUMN` no fim da tabela é INSTANT (só
   metadado), inclusive `NOT NULL DEFAULT '...'` (o default fica no metadado,
   desde 8.0.12). O ALGORITHM explícito faz o statement falhar em vez de
   copiar a tabela em silêncio se o servidor não puder cumprir (ex.: limite de
   64 versões de linha de `INNODB_TABLES.TOTAL_ROW_VERSIONS`, 8.0.29+; cada
   statement INSTANT consome 1). Cada ALTER pede um MDL exclusivo curto.
5. **Nenhuma FK, nenhum índice.** As colunas não referenciam nada; nenhuma FK
   para `exercise_catalog` (a 028 recusa).

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` são o inverso.

- coluna ausente -> `ADD COLUMN`; presente com outro tipo, nulabilidade ou
  default -> `MedidasDeSerieError` antes do primeiro statement;
- banco atual -> 9 statements (1 + 3 + 3 + 2); convergido -> 0;
- tabela ausente: erro numa execução real; aceito em DRY-RUN (banco novo).

`down`: `DROP COLUMN` de cada coluna que existir (perde só a medida/tempo das
séries gravadas depois da 089; `reps`, carga e RPE ficam).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com ':' (viraria parâmetro no shim); módulo autocontido (não
importa código do app).
"""

from __future__ import annotations

MEASURE_DEFAULT = "reps"
MEASURE_MAX_LENGTH = 16
MEASURE_DDL = f"VARCHAR({MEASURE_MAX_LENGTH}) NOT NULL DEFAULT '{MEASURE_DEFAULT}'"
MEASURE_NULLABLE_DDL = f"VARCHAR({MEASURE_MAX_LENGTH}) NULL DEFAULT NULL"
SECONDS_DDL = "INT NULL DEFAULT NULL"
MEASURE_TYPE = f"varchar({MEASURE_MAX_LENGTH})"
SECONDS_TYPE = "int"

# (tabela, coluna, DDL, tipo esperado, IS_NULLABLE esperado, default esperado) na ordem de execução.
TARGETS: tuple[tuple[str, str, str, str, str, str | None], ...] = (
    ("exercise_catalog", "default_measure", MEASURE_NULLABLE_DDL, MEASURE_TYPE, "YES", None),
    ("workout_plan_template_slot_items", "measure", MEASURE_DDL, MEASURE_TYPE, "NO", MEASURE_DEFAULT),
    ("workout_plan_template_slot_items", "duration_seconds_target", SECONDS_DDL, SECONDS_TYPE, "YES", None),
    ("workout_plan_template_slot_items", "time_window_seconds", SECONDS_DDL, SECONDS_TYPE, "YES", None),
    ("workout_plan_exercises", "measure", MEASURE_DDL, MEASURE_TYPE, "NO", MEASURE_DEFAULT),
    ("workout_plan_exercises", "duration_seconds_target", SECONDS_DDL, SECONDS_TYPE, "YES", None),
    ("workout_plan_exercises", "time_window_seconds", SECONDS_DDL, SECONDS_TYPE, "YES", None),
    ("exercise_sets", "measure", MEASURE_DDL, MEASURE_TYPE, "NO", MEASURE_DEFAULT),
    ("exercise_sets", "duration_seconds", SECONDS_DDL, SECONDS_TYPE, "YES", None),
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


class MedidasDeSerieError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise MedidasDeSerieError(f"089: {message}. Nenhum statement executado.")


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
    """`int(11)` (MySQL < 8.0.19) e `int` são o mesmo tipo; `varchar(16)` mantém o tamanho."""
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

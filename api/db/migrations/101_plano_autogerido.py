"""101 - prioridade de macros e gramas manuais no plano nutricional (nutricao N2,
2026-11-02; donos: `app/domain/nutrition/strategy.MacroPriority` e
`manual_targets`).

    nutrition_plans
      + macro_priority       VARCHAR(16) NOT NULL DEFAULT 'balanced'
      + manual_targets_json  JSON NULL    gramas manuais de proteina/carbo/gordura

Os dias de mais/menos gasto (D5) usam a coluna `carb_cycling_json` que a 078 ja
criou (mapa dia da semana -> alto/normal/baixo); nada novo para ela.

Por que: o usuario cria e publica o proprio plano (D2) escolhendo a prioridade
de macros e, se quiser, as gramas do dia. A tabela so guarda o que ele escolheu;
faixas, avisos e bloqueios sao do motor.

--- Decisoes do DDL -------------------------------------------------------

1. **Plano antigo continua igual.** `macro_priority` nasce `balanced` (o
   `macro_split` de sempre, bit a bit) e `manual_targets_json` nasce NULL (sem
   gramas manuais): nenhum backfill e o calculo das metas dos planos existentes
   nao muda.
2. **Um `ALTER TABLE ... ADD COLUMN ..., ALGORITHM=INSTANT` por coluna** (so
   metadado no MySQL 8.0 / Aurora 3; o ALGORITHM explicito falha em vez de
   copiar a tabela em silencio).
3. **Nenhuma FK, nenhum indice.**

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso.

- coluna ausente -> `ADD COLUMN`; presente com outro tipo, nulabilidade ou
  default -> `PlanoAutogeridoError` antes do primeiro statement;
- banco atual -> 2 statements; convergido -> 0 (idempotente);
- tabela ausente: erro numa execucao real; aceito em DRY-RUN (banco novo).

`down`: `DROP COLUMN` de cada coluna que existir (perde so a prioridade e as
gramas manuais; os demais campos do plano ficam e o plano volta ao equilibrado).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com dois pontos; modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

PRIORITY_DDL = "VARCHAR(16) NOT NULL DEFAULT 'balanced'"
MANUAL_DDL = "JSON NULL"

# (tabela, coluna, DDL, tipo esperado, IS_NULLABLE esperado, default esperado) na ordem de execução.
TARGETS: tuple[tuple[str, str, str, str, str, str | None], ...] = (
    ("nutrition_plans", "macro_priority", PRIORITY_DDL, "varchar(16)", "NO", "balanced"),
    ("nutrition_plans", "manual_targets_json", MANUAL_DDL, "json", "YES", None),
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


class PlanoAutogeridoError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise PlanoAutogeridoError(f"101: {message}. Nenhum statement executado.")


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
    """Tipo normalizado (minúsculas); `varchar(16)` e `json` mantêm o formato do information_schema."""
    return column_type.strip().lower()


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

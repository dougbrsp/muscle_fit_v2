"""092 - Modo Intensivo: série com reps pendentes, hora da conclusão, carga
planejada e origem do ajuste/alvo (2026-10-24; donos:
`app/set_types.py` (pendência), `app/domain/training_safety.py` (RPE-alvo do
protocolo) e `app/domain/session_loads.py` (ajuste rápido de carga)).

    exercise_sets
      + reps_pending        TINYINT(1) NOT NULL DEFAULT 0   (1 = reps a confirmar; motores ignoram)
      + completed_at        DATETIME(3) NULL                (hora real da conclusão; retomada/descanso)
      + planned_weight_kg   DECIMAL(8,2) NULL               (carga planejada após ajustes; `weight_kg` = executada)
      + load_adjust_source  VARCHAR(16) NULL                (protocol | fatigue | manual)
      + rpe_target_source   VARCHAR(16) NULL                (protocol = `rpe_target_effective` veio do protocolo da técnica)
      ~ reps                INT NULL                        (já é NULL desde a 001; só é alterada se algum banco divergir)

Por que: no FST-7 (descanso de 30 s) o usuário conclui a série com um toque e
informa as reps depois. A linha é gravada com `reps` NULL e `reps_pending = 1`;
até a confirmação (PATCH da série) nenhum motor nem leitura analítica a vê
(predicado único `set_types.sql_set_confirmed`). O RPE PRESCRITO pelo
protocolo nunca vai para `rpe` (o relatado): vai para `rpe_target_effective`
com `rpe_target_source = 'protocol'`.

--- Decisões do DDL -------------------------------------------------------

1. **`reps_pending NOT NULL DEFAULT 0`, sem backfill.** Toda linha existente
   passa a dizer "confirmada" pelo DEFAULT (é o que ela sempre foi) e todo
   INSERT que não cite a coluna (relógio, importadores, `PUT
   /exercises/logs`) grava 0. Nenhuma linha antiga muda de comportamento.
2. **Demais colunas NULL, sem default.** Só o Modo Intensivo as preenche.
3. **`VARCHAR(16)` livre, não ENUM** (mesma decisão de `set_type`/`measure`):
   o vocabulário é validado na API.
4. **Um `ALTER TABLE ... ADD COLUMN ..., ALGORITHM=INSTANT` por coluna**
   (ver 089, decisão 4): só metadado; falha em vez de copiar a tabela.
5. **`reps` já aceita NULL** (001: `reps INT NULL`). O plano confere; se
   algum banco divergir (NOT NULL), emite `MODIFY reps INT NULL` antes das
   colunas novas. O `down` NÃO volta `reps` a NOT NULL (o histórico tem
   séries por tempo com `reps` NULL desde a 089).
6. **Nenhuma FK, nenhum índice.** Nenhuma FK para `exercise_catalog` (a 028
   recusa).

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` são o inverso.

- coluna ausente -> `ADD COLUMN`; presente com outro tipo, nulabilidade ou
  default -> `ModoIntensivoError` antes do primeiro statement;
- banco atual -> 5 statements; convergido -> 0;
- tabela ausente: erro numa execução real; aceito em DRY-RUN (banco novo).

`down`: `DROP COLUMN` de cada coluna nova que existir (perde só a pendência,
a hora da conclusão, a carga planejada e as origens das séries gravadas
depois da 092; `reps`, carga e RPE ficam).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com ':' (viraria parâmetro no shim); módulo autocontido (não
importa código do app).
"""

from __future__ import annotations

TABLE = "exercise_sets"
SOURCE_MAX_LENGTH = 16
SOURCE_TYPE = f"varchar({SOURCE_MAX_LENGTH})"

# (coluna, DDL, tipo esperado, IS_NULLABLE esperado, default esperado) na ordem de execução.
TARGETS: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("reps_pending", "TINYINT(1) NOT NULL DEFAULT 0", "tinyint(1)", "NO", "0"),
    ("completed_at", "DATETIME(3) NULL DEFAULT NULL", "datetime(3)", "YES", None),
    ("planned_weight_kg", "DECIMAL(8,2) NULL DEFAULT NULL", "decimal(8,2)", "YES", None),
    ("load_adjust_source", f"VARCHAR({SOURCE_MAX_LENGTH}) NULL DEFAULT NULL", SOURCE_TYPE, "YES", None),
    ("rpe_target_source", f"VARCHAR({SOURCE_MAX_LENGTH}) NULL DEFAULT NULL", SOURCE_TYPE, "YES", None),
)

REPS_COLUMN = "reps"
REPS_NULLABLE_SQL = f"ALTER TABLE {TABLE} MODIFY COLUMN {REPS_COLUMN} INT NULL"
COLUMNS: tuple[str, ...] = tuple(sorted({column for column, *_rest in TARGETS} | {REPS_COLUMN}))


def add_column_sql(column: str, ddl: str) -> str:
    # INSTANT explícito: se não for elegível, falha na hora em vez de copiar a
    # tabela em silêncio. No deploy o migrate roda antes de as Lambdas
    # receberem o código novo: falhar aqui é seguro.
    return f"ALTER TABLE {TABLE} ADD COLUMN {column} {ddl}, ALGORITHM=INSTANT"


def drop_column_sql(column: str) -> str:
    return f"ALTER TABLE {TABLE} DROP COLUMN {column}"


class ModoIntensivoError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise ModoIntensivoError(f"092: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _read(db):
    """(tabela existe?, {coluna: linha de information_schema.COLUMNS})."""
    tables = db.query(
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (TABLE,),
    )
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    columns = {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME IN ({placeholders})",
            (TABLE, *COLUMNS),
        )
    }
    return bool(tables), columns


def _base_type(column_type: str) -> str:
    """`int(11)` (MySQL < 8.0.19) e `int` são o mesmo tipo; os demais mantêm o tamanho."""
    text = column_type.strip().lower()
    return text.split("(", 1)[0] if text.startswith("int") else text


def _check_column(column: str, expected_type: str, expected_nullable: str, expected_default, info) -> None:
    found_type = _base_type(_text(info["COLUMN_TYPE"]))
    if found_type != expected_type:
        _fail(f"{TABLE}.{column} e {found_type}, esperado {expected_type}")
    found_nullable = _text(info["IS_NULLABLE"]).upper()
    if found_nullable != expected_nullable:
        _fail(
            f"{TABLE}.{column} e {'NULL' if found_nullable == 'YES' else 'NOT NULL'}, "
            f"esperado {'NULL' if expected_nullable == 'YES' else 'NOT NULL'}"
        )
    raw_default = info.get("COLUMN_DEFAULT")
    found_default = None if raw_default is None else _text(raw_default)
    if found_default != expected_default:
        _fail(f"{TABLE}.{column} tem default {found_default!r}, esperado {expected_default!r}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, columns = _read(db)
    if not exists:
        if bool(getattr(db, "dry_run", False)):
            return [(add_column_sql(column, ddl), None) for column, ddl, *_rest in TARGETS]
        _fail(f"tabela {TABLE} nao existe")
    statements: list[tuple[str, tuple | None]] = []
    reps = columns.get(REPS_COLUMN)
    if reps is None:
        _fail(f"{TABLE}.{REPS_COLUMN} nao existe")
    if _base_type(_text(reps["COLUMN_TYPE"])) != "int":
        _fail(f"{TABLE}.{REPS_COLUMN} e {_text(reps['COLUMN_TYPE'])}, esperado int")
    if _text(reps["IS_NULLABLE"]).upper() != "YES":
        statements.append((REPS_NULLABLE_SQL, None))
    for column, ddl, expected_type, expected_nullable, expected_default in TARGETS:
        info = columns.get(column)
        if info is None:
            statements.append((add_column_sql(column, ddl), None))
        else:
            _check_column(column, expected_type, expected_nullable, expected_default, info)
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta cada coluna nova que existir (ordem inversa).
    `reps` continua NULL (ver decisão 5)."""
    exists, columns = _read(db)
    if not exists:
        return []
    return [(drop_column_sql(column), None) for column, *_rest in reversed(TARGETS) if column in columns]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

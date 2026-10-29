"""087 - chave do cliente para gravação idempotente na sessão ao vivo
(Evolução ZEPP, PR C, 2026-10-18).

    exercises
      + client_key  VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL
      + UNIQUE KEY uq_exercises_activity_client_key (activity_id, client_key)
    exercise_sets
      + client_key  VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL
      + UNIQUE KEY uq_exercise_sets_exercise_client_key (exercise_id, client_key)

Por que: o relógio grava offline-first. Cada `POST /workout-sessions/{id}/
exercises` e `.../sets` sai de uma fila com uma chave gerada no cliente; se a
resposta se perde (BLE, celular dormindo, teto de 29 s do API Gateway), a fila
reenvia. Com a chave, a repetição devolve o registro já gravado
(`duplicate: true`) em vez de duplicar o exercício ou a série
(`app/workout_sessions.py`). O pai da unicidade é o pai real de cada tabela: a
sessão é `activities` (`exercises.activity_id`), e a série pertence a um
exercício da sessão (`exercise_sets.exercise_id`).

--- Decisões do DDL -------------------------------------------------------

1. **NULL, sem DEFAULT nem backfill.** Linhas existentes e clientes que não
   mandam o campo (celular, importadores, `POST /activities`) gravam NULL; a
   UNIQUE do InnoDB aceita quantos NULL houver, então nada muda para eles.
2. **`ascii_bin`.** A chave é um identificador opaco do cliente (o serviço
   aceita só `[A-Za-z0-9._:-]`); comparação binária evita que duas chaves que
   diferem só na caixa colidam (no `utf8mb4_0900_ai_ci` da tabela colidiriam) e
   o índice usa 1 byte por caractere.
3. **ADD COLUMN e ADD UNIQUE em statements separados.** No MySQL 8.0/Aurora
   MySQL 3, `ADD COLUMN` NULL no fim da tabela é INSTANT (só metadado); o
   `ADD UNIQUE KEY` é INPLACE com DML concorrente (constrói o índice sem
   reconstruir a tabela). Juntos no mesmo ALTER, o servidor cairia no
   algoritmo mais caro para os dois. Os dois declaram `ALGORITHM` (e o índice
   `LOCK=NONE`): se o servidor não puder cumprir, o statement falha em vez de
   cair em silêncio para COPY. Cada ALTER ainda pede um MDL exclusivo curto e
   espera transações abertas nessas tabelas.
4. **Nenhuma FK.** As colunas não referenciam nada; nenhuma FK para
   `exercise_catalog` (a 028 recusa). As FKs existentes (`fk_exercises_activity`,
   `fk_sets_exercise`) seguem nos índices de hoje.

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` são o inverso.

- coluna ausente -> `ADD COLUMN`; presente com outro tipo, nulabilidade ou
  collation -> `ClientKeysError` antes do primeiro statement;
- UNIQUE ausente -> `ADD UNIQUE KEY`; presente com outras colunas ou não única
  -> `ClientKeysError`;
- banco atual -> 4 statements; convergido -> 0;
- tabela ausente: erro numa execução real; aceito em DRY-RUN (banco novo).

`down`: `DROP INDEX` e `DROP COLUMN` do que existir (a chave é só
deduplicação; nenhum dado de treino se perde).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com ':' (viraria parâmetro no shim); módulo autocontido (não
importa código do app).
"""

from __future__ import annotations

COLUMN = "client_key"
CLIENT_KEY_MAX_LENGTH = 64
COLUMN_DDL = f"VARCHAR({CLIENT_KEY_MAX_LENGTH}) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL"
EXPECTED_TYPE = f"varchar({CLIENT_KEY_MAX_LENGTH})"
EXPECTED_COLLATION = "ascii_bin"

# tabela -> (nome da UNIQUE, colunas na ordem)
TARGETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "exercises": ("uq_exercises_activity_client_key", ("activity_id", COLUMN)),
    "exercise_sets": ("uq_exercise_sets_exercise_client_key", ("exercise_id", COLUMN)),
}


def add_column_sql(table: str) -> str:
    # INSTANT explícito: se não for elegível (ex.: limite de versões de linha
    # do 8.0.29+), falha na hora em vez de copiar a tabela em silêncio. No
    # deploy o migrate roda antes de as Lambdas receberem o código novo: falhar aqui é seguro.
    return f"ALTER TABLE {table} ADD COLUMN {COLUMN} {COLUMN_DDL}, ALGORITHM=INSTANT"


def add_unique_sql(table: str) -> str:
    name, columns = TARGETS[table]
    return f"ALTER TABLE {table} ADD UNIQUE KEY {name} ({', '.join(columns)}), ALGORITHM=INPLACE, LOCK=NONE"


def drop_unique_sql(table: str) -> str:
    return f"ALTER TABLE {table} DROP INDEX {TARGETS[table][0]}"


def drop_column_sql(table: str) -> str:
    return f"ALTER TABLE {table} DROP COLUMN {COLUMN}"


class ClientKeysError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise ClientKeysError(f"087: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _read(db):
    """(tabelas existentes, {tabela: linha da coluna}, {(tabela, índice): [linhas]})."""
    names = tuple(TARGETS)
    placeholders = ", ".join(["%s"] * len(names))
    tables = {
        _text(row["TABLE_NAME"])
        for row in db.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    columns = {
        _text(row["TABLE_NAME"]): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLLATION_NAME FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders}) AND COLUMN_NAME = %s",
            (*names, COLUMN),
        )
    }
    index_names = tuple(name for name, _cols in TARGETS.values())
    indexes: dict[tuple[str, str], list[dict]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
        f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders}) AND INDEX_NAME IN ({placeholders})",
        (*names, *index_names),
    ):
        indexes.setdefault((_text(row["TABLE_NAME"]), _text(row["INDEX_NAME"])), []).append(row)
    return tables, columns, indexes


def _check_column(table: str, info) -> None:
    found_type = _text(info["COLUMN_TYPE"]).strip().lower()
    if found_type != EXPECTED_TYPE:
        _fail(f"{table}.{COLUMN} e {found_type}, esperado {EXPECTED_TYPE}")
    if _text(info["IS_NULLABLE"]) != "YES":
        _fail(f"{table}.{COLUMN} e NOT NULL, esperado NULL")
    collation = _text(info.get("COLLATION_NAME")).lower()
    if collation != EXPECTED_COLLATION:
        _fail(f"{table}.{COLUMN} usa collation {collation or '?'}, esperado {EXPECTED_COLLATION}")


def _check_unique(table: str, rows: list[dict]) -> None:
    name, expected = TARGETS[table]
    ordered = sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"]))
    found = tuple(_text(r["COLUMN_NAME"]) for r in ordered)
    if found != expected:
        _fail(f"{table}.{name} cobre {found}, esperado {expected}")
    if int(ordered[0]["NON_UNIQUE"]) != 0:
        _fail(f"{table}.{name} existe mas NAO e UNIQUE")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    tables, columns, indexes = _read(db)
    statements: list[tuple[str, tuple | None]] = []
    for table in TARGETS:
        if table not in tables:
            if bool(getattr(db, "dry_run", False)):
                statements += [(add_column_sql(table), None), (add_unique_sql(table), None)]
                continue
            _fail(f"tabela {table} nao existe")
        info = columns.get(table)
        if info is None:
            statements.append((add_column_sql(table), None))
        else:
            _check_column(table, info)
        rows = indexes.get((table, TARGETS[table][0]))
        if rows:
            _check_unique(table, rows)
        else:
            statements.append((add_unique_sql(table), None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta a UNIQUE e a coluna do que existir."""
    tables, columns, indexes = _read(db)
    statements: list[tuple[str, tuple | None]] = []
    for table in TARGETS:
        if table not in tables:
            continue
        if indexes.get((table, TARGETS[table][0])):
            statements.append((drop_unique_sql(table), None))
        if table in columns:
            statements.append((drop_column_sql(table), None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

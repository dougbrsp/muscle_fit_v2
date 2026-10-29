"""094 - deload (M97 D; dono `app/domain/deload.py`).

    deload_periods
      id            BIGINT AUTO_INCREMENT PK
      user_id       BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      starts_on     DATE NOT NULL      primeiro dia do deload (fuso do aluno)
      ends_on       DATE NOT NULL      último dia (inclusive)
      created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      cancelled_at  DATETIME NULL      NULL = ativo; cancelar não apaga
      KEY idx_dp_user_period (user_id, starts_on, ends_on)

    activities
      + deload_source ENUM('period','manual') NULL   (NULL = sessão normal)

Por que: deload = dias em que o usuário reduz carga, RPE, reps e séries SEM
que a progressão trate a sessão como regressão. Ativação por PERÍODO
("semana de deload") ou AVULSA (marca no início do Modo Treino). A origem é
carimbada em `activities.deload_source` no `start` da sessão (congelada: o
período cancelado depois não desmarca a sessão).

--- Decisões do DDL -------------------------------------------------------

1. **ENUM, não VARCHAR.** Vocabulário fechado do dono (`DeloadSource`); o
   teste `test_migration_094_plano.py` prova que o ENUM daqui é o
   `DELOAD_SOURCE_VALUES` do dono (a migração é autocontida).
2. **NULL sem default, sem backfill.** Toda sessão existente é normal; todo
   INSERT que não cita a coluna (relógio, importadores) grava NULL. Nenhum
   comportamento muda sem deload.
3. **ADD COLUMN com ALGORITHM=INSTANT** (ver 089/092/093): só metadado.
4. **Sem UNIQUE de período.** "No máximo um período ativo sobreposto" é regra
   de intervalo (não de igualdade): a API checa com a linha do usuário travada
   e responde 409. O índice serve à busca "período que cobre o dia".
5. **FK só para `users`.** Nenhuma FK para `exercise_catalog` (a 028 recusa).

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` são o inverso (o runner de produção não tem ação
`down`: em produção, reverter é uma migração nova; `down` serve ao DEV e aos
testes).

- tabela ausente -> `CREATE TABLE`; presente com forma divergente, ENGINE !=
  InnoDB ou `users` incompatível -> `DeloadError` antes do primeiro statement;
- coluna ausente -> `ADD COLUMN`; presente com outro tipo/nulabilidade -> erro;
- banco atual -> 2 statements; convergido -> 0;
- `users`/`activities` ausentes: erro numa execução real; aceito em DRY-RUN.

`down`: `DROP COLUMN` (perde só a marca de deload das sessões) e `DROP TABLE`.

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com ':'; módulo autocontido (não importa código do app).
"""

from __future__ import annotations

import re

TABLE = "deload_periods"
USERS_TABLE = "users"
ACTIVITIES_TABLE = "activities"
COLUMN = "deload_source"

SOURCE_VALUES: tuple[str, ...] = ("period", "manual")
_ENUM_SQL = "ENUM(" + ",".join(f"'{value}'" for value in SOURCE_VALUES) + ")"
EXPECTED_SOURCE_TYPE = "enum(" + ",".join(f"'{value}'" for value in SOURCE_VALUES) + ")"

IDX_USER_PERIOD = "idx_dp_user_period"
FK_USER = "fk_dp_user"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  starts_on DATE NOT NULL,
  ends_on DATE NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  cancelled_at DATETIME NULL,
  KEY {IDX_USER_PERIOD} (user_id, starts_on, ends_on),
  CONSTRAINT {FK_USER} FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_TABLE_SQL = f"DROP TABLE IF EXISTS {TABLE}"
ADD_COLUMN_SQL = f"ALTER TABLE {ACTIVITIES_TABLE} ADD COLUMN {COLUMN} {_ENUM_SQL} NULL DEFAULT NULL, ALGORITHM=INSTANT"
DROP_COLUMN_SQL = f"ALTER TABLE {ACTIVITIES_TABLE} DROP COLUMN {COLUMN}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "starts_on": ("date", "NO"),
    "ends_on": ("date", "NO"),
    "created_at": ("datetime", "NO"),
    "cancelled_at": ("datetime", "YES"),
}
EXPECTED_INDEX_COLUMNS = ("user_id", "starts_on", "ends_on")

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class DeloadError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise DeloadError(f"094: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    text = "".join(_text(column_type).split()).lower()
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", text)


def _read(db):
    """({tabela: ENGINE}, {(tabela, coluna): linha}, [linhas do índice])."""
    names = (TABLE, USERS_TABLE, ACTIVITIES_TABLE)
    placeholders = ", ".join(["%s"] * len(names))
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    columns = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders}) "
            "AND (TABLE_NAME <> %s OR COLUMN_NAME = %s)",
            (*names, ACTIVITIES_TABLE, COLUMN),
        )
    }
    index_rows = db.query(
        "SELECT INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
        (TABLE, IDX_USER_PERIOD),
    )
    return engines, columns, list(index_rows)


def _check_users(engines, columns) -> None:
    if engines[USERS_TABLE].lower() != "innodb":
        _fail(f"{USERS_TABLE} usa ENGINE={engines[USERS_TABLE]}, esperado InnoDB")
    info = columns.get((USERS_TABLE, "id"))
    if info is None:
        _fail(f"{USERS_TABLE}.id nao existe")
    if _normalize_type(info["COLUMN_TYPE"]) != "bigint":
        _fail(f"{USERS_TABLE}.id e {_text(info['COLUMN_TYPE'])}, esperado bigint")
    if _text(info["COLUMN_KEY"]) != "PRI":
        _fail(f"{USERS_TABLE}.id nao e PRIMARY KEY")


def _check_table(engines, columns, index_rows) -> None:
    if engines[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engines[TABLE]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS.items():
        info = columns.get((TABLE, name))
        if info is None:
            _fail(f"{TABLE}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    found = tuple(_text(r["COLUMN_NAME"]) for r in sorted(index_rows, key=lambda r: int(r["SEQ_IN_INDEX"])))
    if found != EXPECTED_INDEX_COLUMNS:
        _fail(f"{TABLE}.{IDX_USER_PERIOD} cobre {found}, esperado {EXPECTED_INDEX_COLUMNS}")


def _check_column(info) -> None:
    found_type = _normalize_type(info["COLUMN_TYPE"])
    if found_type != EXPECTED_SOURCE_TYPE:
        _fail(f"{ACTIVITIES_TABLE}.{COLUMN} e {found_type}, esperado {EXPECTED_SOURCE_TYPE}")
    if _text(info["IS_NULLABLE"]) != "YES":
        _fail(f"{ACTIVITIES_TABLE}.{COLUMN} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado YES")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, index_rows = _read(db)
    dry_run = bool(getattr(db, "dry_run", False))
    statements: list[tuple[str, tuple | None]] = []

    if USERS_TABLE not in engines:
        if not dry_run:
            _fail(f"tabela {USERS_TABLE} nao existe")
        statements.append((CREATE_SQL, None))
    else:
        _check_users(engines, columns)
        if TABLE in engines:
            _check_table(engines, columns, index_rows)
        else:
            statements.append((CREATE_SQL, None))

    if ACTIVITIES_TABLE not in engines:
        if not dry_run:
            _fail(f"tabela {ACTIVITIES_TABLE} nao existe")
        statements.append((ADD_COLUMN_SQL, None))
    else:
        info = columns.get((ACTIVITIES_TABLE, COLUMN))
        if info is None:
            statements.append((ADD_COLUMN_SQL, None))
        else:
            _check_column(info)
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta a coluna (se existe) e a tabela (se existe)."""
    engines, columns, _index_rows = _read(db)
    statements: list[tuple[str, tuple | None]] = []
    if ACTIVITIES_TABLE in engines and (ACTIVITIES_TABLE, COLUMN) in columns:
        statements.append((DROP_COLUMN_SQL, None))
    if TABLE in engines:
        statements.append((DROP_TABLE_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

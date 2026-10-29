"""083 - diário do dia: água, fome e anotação (plano de nutrição, Lote L4, 2026-10-17).

    nutrition_days
      id                    BIGINT AUTO_INCREMENT PK
      user_id               BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      local_date            DATE NOT NULL     dia civil no fuso do aluno
      water_ml              DECIMAL(7,1) NULL água do dia (faixa do motor: `diary.WATER_ML_RANGE`)
      hunger                TINYINT NULL      fome na escala do motor (`diary.HUNGER_SCALE`, 1 a 5)
      notes                 VARCHAR(1000) NULL
      updated_by            BIGINT NULL       quem gravou (aluno, admin ou PT); referência LÓGICA
      created_at / updated_at
      UNIQUE uq_nd_user_date (user_id, local_date)

--- Decisões do DDL -------------------------------------------------------

1. **Uma linha por (aluno, dia)**, upsert pelo `PUT /nutrition/days/{date}`;
   NULL = não informado (nunca 0). O peso do dia continua em
   `body_composition` (`source='manual'`), sem cópia aqui.
2. **Faixas no motor** (`domain/nutrition/diary.validate_day_log`): a coluna
   só guarda; o 400 sai com o código do motor.
3. **FK só para `users`**; `updated_by` é lógico. Nenhuma FK para
   `exercise_catalog`.

--- Fluxo -----------------------------------------------------------------

Mesmo de 079: tabela ausente -> 1 `CREATE TABLE`; convergido -> 0; forma
divergente, ENGINE != InnoDB ou `users` incompatível ->
`NutritionDaysError` ANTES do primeiro statement; `users` ausente só passa em
DRY-RUN. `down` = DROP TABLE (nada referencia a tabela).

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; módulo
autocontido (não importa código do app).
"""

from __future__ import annotations

import re

TABLE = "nutrition_days"
USERS_TABLE = "users"

IDX_USER_DATE = "uq_nd_user_date"
FK_USER = "fk_nd_user"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  local_date DATE NOT NULL,
  water_ml DECIMAL(7,1) NULL,
  hunger TINYINT NULL,
  notes VARCHAR(1000) NULL,
  updated_by BIGINT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY {IDX_USER_DATE} (user_id, local_date),
  CONSTRAINT {FK_USER} FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "local_date": ("date", "NO"),
    "water_ml": ("decimal(7,1)", "YES"),
    "hunger": ("tinyint", "YES"),
    "notes": ("varchar(1000)", "YES"),
    "updated_by": ("bigint", "YES"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
}

# `tinyint(4)` -> `tinyint`; `tinyint(1)` (booleano) fica como está.
_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class NutritionDaysError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise NutritionDaysError(f"083: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db):
    names = (TABLE, USERS_TABLE)
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
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    indexes: dict[str, list[dict]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
        (TABLE,),
    ):
        indexes.setdefault(_text(row["INDEX_NAME"]), []).append(row)
    return engines, columns, indexes


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


def _check_existing(engines, columns, indexes) -> None:
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
    rows = indexes.get(IDX_USER_DATE)
    if not rows:
        _fail(f"{TABLE}.{IDX_USER_DATE} nao existe")
    encontradas = tuple(_text(r["COLUMN_NAME"]) for r in sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"])))
    if encontradas != ("user_id", "local_date"):
        _fail(f"{TABLE}.{IDX_USER_DATE} cobre {encontradas}, esperado ('user_id', 'local_date')")
    if int(rows[0]["NON_UNIQUE"]) != 0:
        _fail(f"{TABLE}.{IDX_USER_DATE} NAO e UNIQUE")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, indexes = _read(db)
    if USERS_TABLE not in engines:
        if bool(getattr(db, "dry_run", False)):
            return [(CREATE_SQL, None)]
        _fail(f"tabela {USERS_TABLE} nao existe")
    _check_users(engines, columns)
    if TABLE in engines:
        _check_existing(engines, columns, indexes)
        return []
    return [(CREATE_SQL, None)]


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: 1 `DROP TABLE` se a tabela existe, senão nada."""
    engines, _columns, _indexes = _read(db)
    if TABLE not in engines:
        return []
    return [(DROP_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

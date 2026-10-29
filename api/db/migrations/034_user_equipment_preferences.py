"""034 - preferencia do usuario por equipamento, em estrelas (PR-08 da
refatoracao dos motores; PLANO_20260912.md secao 8.2, la numerada "026" - o
numero real e o proximo livre).

    user_equipment_preferences
      user_id      BIGINT NOT NULL   -> users(id)      ON DELETE CASCADE
      equipment_id BIGINT NOT NULL   -> equipment(id)  ON DELETE CASCADE
      stars        TINYINT NOT NULL  (1-5; CHECK chk_uep_stars)
      updated_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      PRIMARY KEY (user_id, equipment_id)
      KEY idx_uep_user_stars (user_id, stars)
      KEY idx_uep_equipment (equipment_id)       (indice da FK de equipment)

Ausencia de linha = 3 estrelas (neutro): o recomendador nunca grava o default.
A preferencia e do EQUIPAMENTO do catalogo (`equipment.id`), nao da associacao
por academia (`gym_equipment.id`): vale em qualquer academia que o tenha.

Tipos das colunas referenciadas conferidos na Aurora em 2026-09-17 (somente
leitura): `users.id` e `equipment.id` sao BIGINT signed, PRIMARY KEY, InnoDB.

Fluxo: `plan(db)` so LE `information_schema` e devolve os statements;
`up(db)` executa o plano.

- tabela ausente -> exatamente 1 `CREATE TABLE`;
- tabela presente no formato esperado -> nada;
- tabela presente com coluna/tipo/nulabilidade/PK/indice/ENGINE/FK diferente,
  ou tabela/coluna REFERENCIADA ausente ou de outro tipo ->
  `UserEquipmentPreferencesError` antes do primeiro statement.

Reexecucao: 0 statements. Data API: sem PREPARE/SET/multi-statement; um
statement por chamada; modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "user_equipment_preferences"
STARS_INDEX = "idx_uep_user_stars"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  user_id BIGINT NOT NULL,
  equipment_id BIGINT NOT NULL,
  stars TINYINT NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, equipment_id),
  KEY {STARS_INDEX} (user_id, stars),
  KEY idx_uep_equipment (equipment_id),
  CONSTRAINT fk_uep_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_uep_equipment FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
  CONSTRAINT chk_uep_stars CHECK (stars BETWEEN 1 AND 5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

# (tabela, coluna) referenciada -> COLUMN_TYPE esperado
REFERENCED = {
    ("users", "id"): "bigint",
    ("equipment", "id"): "bigint",
}

# coluna -> (COLUMN_TYPE normalizado, IS_NULLABLE)
EXPECTED_COLUMNS = {
    "user_id": ("bigint", "NO"),
    "equipment_id": ("bigint", "NO"),
    "stars": ("tinyint", "NO"),
    "updated_at": ("timestamp", "NO"),
}
EXPECTED_PRIMARY = ["user_id", "equipment_id"]
EXPECTED_STARS_INDEX = ["user_id", "stars"]
EXPECTED_FKS = {"user_id": "users", "equipment_id": "equipment"}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class UserEquipmentPreferencesError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise UserEquipmentPreferencesError(f"034: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _check_referenced(engines: dict[str, str], columns: dict[tuple[str, str], dict]) -> None:
    for (table, column), expected_type in REFERENCED.items():
        if table not in engines:
            _fail(f"tabela referenciada {table} nao existe")
        if engines[table].lower() != "innodb":
            _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
        info = columns.get((table, column))
        if info is None:
            _fail(f"{table}.{column} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{table}.{column} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["COLUMN_KEY"]) != "PRI":
            _fail(f"{table}.{column} nao e PRIMARY KEY")


def _check_existing(db, engines: dict[str, str], columns: dict[tuple[str, str], dict]) -> None:
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

    indexes: dict[str, list[str]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s) ORDER BY INDEX_NAME, SEQ_IN_INDEX",
        (TABLE,),
    ):
        if _text(row["TABLE_NAME"]) == TABLE:
            indexes.setdefault(_text(row["INDEX_NAME"]), []).append(_text(row["COLUMN_NAME"]))
    if indexes.get("PRIMARY") != EXPECTED_PRIMARY:
        _fail(f"PRIMARY KEY de {TABLE} e {indexes.get('PRIMARY')}, esperado {EXPECTED_PRIMARY}")
    if indexes.get(STARS_INDEX) != EXPECTED_STARS_INDEX:
        _fail(f"indice {STARS_INDEX} de {TABLE} e {indexes.get(STARS_INDEX)}, esperado {EXPECTED_STARS_INDEX}")

    fks = {
        _text(row["COLUMN_NAME"]): _text(row["REFERENCED_TABLE_NAME"])
        for row in db.query(
            "SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE k "
            "WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME IN (%s) AND k.REFERENCED_TABLE_NAME IS NOT NULL",
            (TABLE,),
        )
        if _text(row["TABLE_NAME"]) == TABLE
    }
    if fks != EXPECTED_FKS:
        _fail(f"FKs de {TABLE} sao {fks}, esperado {EXPECTED_FKS}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    names = tuple(sorted({TABLE, *(table for table, _ in REFERENCED)}))
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
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    _check_referenced(engines, columns)

    if TABLE in engines:
        _check_existing(db, engines, columns)
        return []
    return [(CREATE_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)

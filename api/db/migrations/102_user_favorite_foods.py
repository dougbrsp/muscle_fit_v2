"""102 - alimentos preferidos do usuario (nutricao N3, 2026-11-02).

    user_favorite_foods             um alimento preferido de um usuario
      id                  BIGINT AUTO_INCREMENT PK
      user_id             BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      food_id             BIGINT NOT NULL   referencia LOGICA a food_items(id) (sem FK)
      default_quantity    DECIMAL(10,3) NULL  porcao padrao: quantidade na `default_unit`
                                              (mesmo tipo de `meal_items.quantity`)
      default_unit        VARCHAR(24) NULL    codigo de `meal_math.Unit`
      default_portion_id  BIGINT NULL         referencia LOGICA a food_portions(id)
      position            INT NOT NULL DEFAULT 0   ordem na lista (menor primeiro)
      created_at / updated_at
      UNIQUE uq_ufav_user_food (user_id, food_id)
      KEY    idx_ufav_user_pos (user_id, position, id)

Por que: o usuario favorita alimentos para te-los primeiro na busca do diario e
num atalho no editor de refeicao, com a porcao que ele costuma comer. A
quantidade da porcao padrao e convertida pelo motor (`meal_math.resolve_amount`),
como qualquer item do diario; a tabela so guarda o que o usuario escolheu.

--- Decisoes do DDL -------------------------------------------------------

1. **FK so para `users`** (ON DELETE CASCADE): favoritos sao do usuario e somem
   com ele. `food_id` e `default_portion_id` sao referencias logicas (como em
   `meal_items`): o alimento pode ser desativado/promovido sem apagar o
   favorito, e a rota filtra o que o usuario ainda pode ver. Nenhuma FK para
   `exercise_catalog` (a 028 recusa).
2. **UNIQUE (user_id, food_id)**: um alimento e favorito uma vez por usuario;
   a rota usa `ON DUPLICATE KEY UPDATE` para ser idempotente.
3. **Porcao padrao opcional**: sem quantidade/unidade o favorito entra no
   diario sem quantidade pre-preenchida. Faixas e conversao: `meal_math`/`diary`.
4. **`position` INT**: reordenar regrava as posicoes da lista inteira numa
   transacao (a lista e curta; `nutrition_favorites.MAX_FAVORITES`).
5. **Criacao idempotente**: `CREATE TABLE IF NOT EXISTS`; reexecucao em banco
   convergido = 0 statements. Reversivel: `down` e um `DROP TABLE IF EXISTS`
   (perde so a lista de favoritos, nenhum dado do diario ou do catalogo).

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso.

- tabela ausente -> 1 `CREATE TABLE`; presente na forma esperada -> 0;
- coluna/tipo/nulabilidade diferente, indice ausente/diferente/nao UNIQUE,
  ENGINE != InnoDB ou `users` incompativel -> `UserFavoriteFoodsError` ANTES do
  primeiro statement;
- `users` ausente: erro numa execucao real; aceito em DRY-RUN (banco novo).

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; nenhuma
literal com ':' (viraria parametro no shim); modulo autocontido (nao importa
codigo do app).
"""

from __future__ import annotations

import re

TABLE = "user_favorite_foods"
USERS_TABLE = "users"

IDX_USER_FOOD = "uq_ufav_user_food"
IDX_USER_POSITION = "idx_ufav_user_pos"
FK_USER = "fk_ufav_user"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  food_id BIGINT NOT NULL,
  default_quantity DECIMAL(10,3) NULL,
  default_unit VARCHAR(24) NULL,
  default_portion_id BIGINT NULL,
  position INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY {IDX_USER_FOOD} (user_id, food_id),
  KEY {IDX_USER_POSITION} (user_id, position, id),
  CONSTRAINT {FK_USER} FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "food_id": ("bigint", "NO"),
    "default_quantity": ("decimal(10,3)", "YES"),
    "default_unit": ("varchar(24)", "YES"),
    "default_portion_id": ("bigint", "YES"),
    "position": ("int", "NO"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
}

# indice -> (UNIQUE?, colunas)
EXPECTED_INDEXES: dict[str, tuple[bool, tuple[str, ...]]] = {
    IDX_USER_FOOD: (True, ("user_id", "food_id")),
    IDX_USER_POSITION: (False, ("user_id", "position", "id")),
}

# `int(11)` -> `int`; `tinyint(1)` (booleano) fica como esta.
_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class UserFavoriteFoodsError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise UserFavoriteFoodsError(f"102: {message}. Nenhum statement executado.")


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
    for name, (unique, cols) in EXPECTED_INDEXES.items():
        rows = indexes.get(name)
        if not rows:
            _fail(f"{TABLE}.{name} nao existe")
        ordered = sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"]))
        found = tuple(_text(r["COLUMN_NAME"]) for r in ordered)
        if found != cols:
            _fail(f"{TABLE}.{name} cobre {found}, esperado {cols}")
        if (int(ordered[0]["NON_UNIQUE"]) == 0) != unique:
            _fail(f"{TABLE}.{name} {'NAO e' if unique else 'e'} UNIQUE")


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
    """Inverso de `plan`: 1 `DROP TABLE` se a tabela existe, senao nada."""
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

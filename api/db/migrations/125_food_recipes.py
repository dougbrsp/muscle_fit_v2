"""125 - receitas do usuario (nutricao, issue #286, primeira fatia).

    food_recipe_versions            uma versao de uma receita (a receita e uma linha de `food_items`, source recipe)
      id               BIGINT AUTO_INCREMENT PK
      food_id          BIGINT NOT NULL   referencia LOGICA a food_items(id) (sem FK)
      version          INT NOT NULL      1, 2, 3... por receita
      final_weight_g   DECIMAL(10,2) NOT NULL   peso final pronto
      raw_weight_g     DECIMAL(10,2) NULL       soma dos ingredientes em gramas
      yield_percent    DECIMAL(7,2) NULL        peso final / peso cru
      instructions     MEDIUMTEXT NULL        descricao/modo de preparo (privado do dono)
      calculated_json  MEDIUMTEXT NOT NULL      `RecipeResult.to_dict()` (motor)
      effective_json   MEDIUMTEXT NOT NULL      `RecipeEffective.to_dict()` (efetivos, calculados, substituidos)
      engine_version   VARCHAR(32) NOT NULL
      created_by       BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      created_at
      UNIQUE uq_frv_food_version (food_id, version)

    food_recipe_ingredients         ingredientes de uma versao
      id                    BIGINT AUTO_INCREMENT PK
      version_id            BIGINT NOT NULL -> food_recipe_versions(id) ON DELETE CASCADE
      position              INT NOT NULL
      food_id               BIGINT NOT NULL   referencia LOGICA ao alimento de origem (sem FK)
      item_label            VARCHAR(255) NOT NULL
      quantity              DECIMAL(10,3) NOT NULL   como o usuario digitou, na `unit`
      unit                  VARCHAR(24) NOT NULL
      portion_id            BIGINT NULL              referencia LOGICA a food_portions(id)
      amount                DECIMAL(12,3) NOT NULL   quantidade na base do alimento (g ou ml)
      basis                 VARCHAR(8) NOT NULL
      catalog_snapshot_json MEDIUMTEXT NOT NULL      o alimento como estava (mesmo snapshot do diario)
      KEY idx_fri_version_pos (version_id, position)

Decisoes: sem FK para `food_items` (o alimento pode ser desativado/promovido sem apagar o historico; o
snapshot congela os valores); FK so para `users` e entre as duas tabelas; `CREATE TABLE IF NOT EXISTS`;
`down` e DESTRUTIVO e guardado: so solta as duas tabelas (nenhum dado do catalogo ou do diario) quando
`food_recipe_versions` esta vazia; com linhas, `plan_down` falha antes de qualquer statement. Valores em JSON sao os do motor
`domain/nutrition/recipe.py`; este modulo so guarda.

Fluxo: `plan(db)` so LE `information_schema`; tabela ausente -> 1 `CREATE TABLE`; presente na forma esperada
-> 0; coluna/tipo/nulabilidade/indice diferente, ENGINE != InnoDB ou `users` incompativel ->
`FoodRecipesError` ANTES do primeiro statement. `users` ausente: erro numa execucao real; aceito em DRY-RUN.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; nenhuma literal com ':'; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

VERSIONS = "food_recipe_versions"
INGREDIENTS = "food_recipe_ingredients"
USERS_TABLE = "users"

IDX_VERSION = "uq_frv_food_version"
IDX_INGREDIENT = "idx_fri_version_pos"
FK_USER = "fk_frv_user"
FK_VERSION = "fk_fri_version"

CREATE_VERSIONS_SQL = f"""
CREATE TABLE IF NOT EXISTS {VERSIONS} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  food_id BIGINT NOT NULL,
  version INT NOT NULL,
  final_weight_g DECIMAL(10,2) NOT NULL,
  raw_weight_g DECIMAL(10,2) NULL,
  yield_percent DECIMAL(7,2) NULL,
  instructions MEDIUMTEXT NULL,
  calculated_json MEDIUMTEXT NOT NULL,
  effective_json MEDIUMTEXT NOT NULL,
  engine_version VARCHAR(32) NOT NULL,
  created_by BIGINT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY {IDX_VERSION} (food_id, version),
  CONSTRAINT {FK_USER} FOREIGN KEY (created_by) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

CREATE_INGREDIENTS_SQL = f"""
CREATE TABLE IF NOT EXISTS {INGREDIENTS} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  version_id BIGINT NOT NULL,
  position INT NOT NULL,
  food_id BIGINT NOT NULL,
  item_label VARCHAR(255) NOT NULL,
  quantity DECIMAL(10,3) NOT NULL,
  unit VARCHAR(24) NOT NULL,
  portion_id BIGINT NULL,
  amount DECIMAL(12,3) NOT NULL,
  basis VARCHAR(8) NOT NULL,
  catalog_snapshot_json MEDIUMTEXT NOT NULL,
  KEY {IDX_INGREDIENT} (version_id, position),
  CONSTRAINT {FK_VERSION} FOREIGN KEY (version_id) REFERENCES {VERSIONS}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_INGREDIENTS_SQL = f"DROP TABLE IF EXISTS {INGREDIENTS}"
DROP_VERSIONS_SQL = f"DROP TABLE IF EXISTS {VERSIONS}"
COUNT_VERSIONS_SQL = f"SELECT COUNT(*) AS total FROM {VERSIONS}"

# tabela -> (CREATE, colunas {nome: (tipo normalizado, IS_NULLABLE)}, indices {nome: (UNIQUE?, colunas)})
SPECS: dict[str, tuple[str, dict[str, tuple[str, str]], dict[str, tuple[bool, tuple[str, ...]]]]] = {
    VERSIONS: (
        CREATE_VERSIONS_SQL,
        {
            "id": ("bigint", "NO"),
            "food_id": ("bigint", "NO"),
            "version": ("int", "NO"),
            "final_weight_g": ("decimal(10,2)", "NO"),
            "raw_weight_g": ("decimal(10,2)", "YES"),
            "yield_percent": ("decimal(7,2)", "YES"),
            "instructions": ("mediumtext", "YES"),
            "calculated_json": ("mediumtext", "NO"),
            "effective_json": ("mediumtext", "NO"),
            "engine_version": ("varchar(32)", "NO"),
            "created_by": ("bigint", "NO"),
            "created_at": ("datetime", "NO"),
        },
        {IDX_VERSION: (True, ("food_id", "version"))},
    ),
    INGREDIENTS: (
        CREATE_INGREDIENTS_SQL,
        {
            "id": ("bigint", "NO"),
            "version_id": ("bigint", "NO"),
            "position": ("int", "NO"),
            "food_id": ("bigint", "NO"),
            "item_label": ("varchar(255)", "NO"),
            "quantity": ("decimal(10,3)", "NO"),
            "unit": ("varchar(24)", "NO"),
            "portion_id": ("bigint", "YES"),
            "amount": ("decimal(12,3)", "NO"),
            "basis": ("varchar(8)", "NO"),
            "catalog_snapshot_json": ("mediumtext", "NO"),
        },
        {IDX_INGREDIENT: (False, ("version_id", "position"))},
    ),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class FoodRecipesError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise FoodRecipesError(f"125: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db):
    names = (VERSIONS, INGREDIENTS, USERS_TABLE)
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
    indexes: dict[tuple[str, str], list[dict]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
        (VERSIONS, INGREDIENTS),
    ):
        indexes.setdefault((_text(row["TABLE_NAME"]), _text(row["INDEX_NAME"])), []).append(row)
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


def _check_existing(table, engines, columns, indexes) -> None:
    """Valida tabela ja existente: confere engine, colunas e indices; NAO confere as chaves estrangeiras
    nem a regra de exclusao (mesma lacuna do molde 102)."""
    _create, expected_columns, expected_indexes = SPECS[table]
    if engines[table].lower() != "innodb":
        _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in expected_columns.items():
        info = columns.get((table, name))
        if info is None:
            _fail(f"{table}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{table}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{table}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    for name, (unique, cols) in expected_indexes.items():
        rows = indexes.get((table, name))
        if not rows:
            _fail(f"{table}.{name} nao existe")
        ordered = sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"]))
        found = tuple(_text(r["COLUMN_NAME"]) for r in ordered)
        if found != cols:
            _fail(f"{table}.{name} cobre {found}, esperado {cols}")
        if (int(ordered[0]["NON_UNIQUE"]) == 0) != unique:
            _fail(f"{table}.{name} {'NAO e' if unique else 'e'} UNIQUE")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem (versoes antes dos ingredientes)."""
    engines, columns, indexes = _read(db)
    if USERS_TABLE not in engines:
        if bool(getattr(db, "dry_run", False)):
            return [(SPECS[t][0], None) for t in (VERSIONS, INGREDIENTS)]
        _fail(f"tabela {USERS_TABLE} nao existe")
    _check_users(engines, columns)
    steps: list[tuple[str, tuple | None]] = []
    for table in (VERSIONS, INGREDIENTS):
        if table in engines:
            _check_existing(table, engines, columns, indexes)
        else:
            steps.append((SPECS[table][0], None))
    return steps


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: ingredientes antes das versoes (a FK), so o que existe."""
    engines, _columns, _indexes = _read(db)
    if VERSIONS in engines:
        rows = db.query(COUNT_VERSIONS_SQL)
        if rows and int(next(iter(rows[0].values()))) > 0:
            _fail(f"o down e destrutivo e so roda com {VERSIONS} vazia (ha receitas salvas)")
    steps: list[tuple[str, tuple | None]] = []
    if INGREDIENTS in engines:
        steps.append((DROP_INGREDIENTS_SQL, None))
    if VERSIONS in engines:
        steps.append((DROP_VERSIONS_SQL, None))
    return steps


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

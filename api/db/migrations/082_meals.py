"""082 - diário de refeições (plano de nutrição, Lote L4, 2026-10-17).

    meals                           uma refeição do aluno num dia
      id                    BIGINT AUTO_INCREMENT PK
      user_id               BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      local_date            DATE NOT NULL     dia civil no fuso do aluno
      meal_slot             VARCHAR(24)       `domain/nutrition/diary.MealSlot`
      eaten_at              TIME NULL         hora local (opcional; ordena dentro do slot)
      source                VARCHAR(16)       `diary.MealSource` (manual no L4; ai_text no L5)
      description_raw       TEXT NULL         texto livre do parse (L5)
      notes                 VARCHAR(1000) NULL
      energy_kcal ... sodium_mg  DECIMAL NULL totais CONGELADOS pelo motor (`meal_math.sum_items`),
                                              códigos de `meal_math.NUTRIENT_FIELDS` (RDC 429); por
                                              campo, a soma das contribuições conhecidas; NULL =
                                              nenhuma conhecida
      totals_incomplete_json JSON NULL        campos com alguma contribuição desconhecida (o valor
                                              da coluna é então um mínimo)
      totals_nature         VARCHAR(16) NULL  `meal_math.MealNature` (DERIVED/ESTIMATED/MIXED)
      item_count            SMALLINT
      engine_version        VARCHAR(32)
      ai_job_id             VARCHAR(64) NULL  job do parse (L5)
      created_by/updated_by BIGINT NULL       quem gravou (aluno, admin ou PT); referência LÓGICA
      created_at / updated_at
      KEY idx_meals_user_date (user_id, local_date)

    meal_items                      um alimento da refeição, com os nutrientes CONGELADOS (E4)
      id                    BIGINT AUTO_INCREMENT PK
      meal_id               BIGINT NOT NULL -> meals(id) ON DELETE CASCADE
      position              SMALLINT          ordem na refeição
      food_id               BIGINT NULL       referência LÓGICA a food_items(id) (+ índice); NULL só
                                              para item estimado pela IA sem catálogo (L5)
      portion_id            BIGINT NULL       referência LÓGICA a food_portions(id)
      item_label            VARCHAR(255)      nome do alimento na gravação
      quantity              DECIMAL(10,3)     como o aluno informou, na `unit`
      unit                  VARCHAR(24)       `meal_math.Unit` (g, ml, porção, medida caseira)
      amount                DECIMAL(9,2)      quantidade NA BASE do alimento (`meal_math.resolve_amount`)
      basis                 VARCHAR(2)        base da tabela do alimento ('g' | 'ml')
      energy_kcal ... sodium_mg  DECIMAL NULL nutrientes do item pelo motor; NULL = a tabela não
                                              informa (nunca 0 inventado; zero inferido pela regra do
                                              macro ausente fica 0 com a limitação)
      nature                VARCHAR(16)       `MealNature` do item (DERIVED no L4)
      match_confidence      DECIMAL(4,3) NULL nota do casamento da IA (L5)
      limitations_json      JSON NULL         códigos do motor (macro inferido, desconhecido, densidade)
      catalog_snapshot_json JSON NULL         o alimento e a porção como estavam na gravação (E4)
      engine_version        VARCHAR(32)
      created_at
      KEY idx_mi_meal (meal_id, position), KEY idx_mi_food (food_id)

--- Decisões do DDL -------------------------------------------------------

1. **Nutrientes congelados por item** (decisão E4 do plano): editar o alimento
   no catálogo depois não muda refeição antiga; o `catalog_snapshot_json`
   guarda os valores por 100 e a porção usados, e reeditar a refeição reusa o
   snapshot do item mantido.
2. **Nomes do motor**: os nutrientes usam os códigos da RDC 429 do catálogo
   (`energy_kcal`, `carbohydrates_g`, `total_fat_g`... como a 080), não os do
   rascunho do §6.2 (`kcal`, `carbs_g`, `fat_g`): sem conversão de nome fora do
   dono. A quantidade resolvida é `amount` NA BASE (g ou ml, como
   `food_portions.amount`), não `grams_resolved`: alimento por 100 ml soma em ml.
3. **NULL ≠ 0** (decisão 1 do L4): macro que a tabela não informa fica NULL no
   item; o total da refeição é a soma do que se conhece e o campo vai para
   `totals_incomplete_json`.
4. **FK só para `users` e `meal_items -> meals`** (ambas ON DELETE CASCADE);
   `food_id`, `portion_id`, `created_by`, `updated_by` são referências lógicas.
   Nenhuma FK para `exercise_catalog` (a 028 recusa).
5. **Códigos em VARCHAR**, vocabulário do motor; a migração não enumera.
6. **DECIMAL**: item até `diary.ITEM_AMOUNT_MAX` (2.000 na base) × a maior
   densidade da base do catálogo cabe em `DECIMAL(9,2)` (energia) e
   `DECIMAL(10,2)` (sódio, mg); a refeição (até `diary.MEAL_MAX_ITEMS` = 30
   itens) em `DECIMAL(10,2)`/`DECIMAL(11,2)`.

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa; `down`/`plan_down`
fazem DROP TABLE (itens primeiro).

- tabela ausente -> 1 `CREATE TABLE` cada (2 num banco novo; `meals` antes);
- presentes na forma esperada -> 0 statements (reexecução);
- coluna/tipo/nulabilidade diferente, índice ausente ou de outro tipo,
  ENGINE != InnoDB ou `users` incompatível -> `MealsError` ANTES do primeiro
  statement;
- `users` ausente: erro numa execução real; aceito em DRY-RUN.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; nenhuma
literal com ':' (viraria parâmetro no shim); módulo autocontido (não importa
código do app).
"""

from __future__ import annotations

import re

MEALS = "meals"
ITEMS = "meal_items"
USERS_TABLE = "users"

IDX_MEALS_USER_DATE = "idx_meals_user_date"
FK_MEALS_USER = "fk_meals_user"
IDX_ITEMS_MEAL = "idx_mi_meal"
IDX_ITEMS_FOOD = "idx_mi_food"
FK_ITEMS_MEAL = "fk_mi_meal"

# Nutrientes (códigos de `meal_math.NUTRIENT_FIELDS`): tipo no item e na refeição.
ITEM_NUTRIENT_TYPES: dict[str, str] = {
    "energy_kcal": "decimal(9,2)",
    "protein_g": "decimal(8,2)",
    "carbohydrates_g": "decimal(8,2)",
    "total_fat_g": "decimal(8,2)",
    "fiber_g": "decimal(8,2)",
    "sodium_mg": "decimal(10,2)",
}
MEAL_NUTRIENT_TYPES: dict[str, str] = {
    "energy_kcal": "decimal(10,2)",
    "protein_g": "decimal(9,2)",
    "carbohydrates_g": "decimal(9,2)",
    "total_fat_g": "decimal(9,2)",
    "fiber_g": "decimal(9,2)",
    "sodium_mg": "decimal(11,2)",
}


def _nutrient_ddl(tipos: dict[str, str]) -> str:
    return "".join(f"  {coluna} {tipo.upper()} NULL,\n" for coluna, tipo in tipos.items())


CREATE_MEALS_SQL = f"""
CREATE TABLE IF NOT EXISTS {MEALS} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  local_date DATE NOT NULL,
  meal_slot VARCHAR(24) NOT NULL,
  eaten_at TIME NULL,
  source VARCHAR(16) NOT NULL,
  description_raw TEXT NULL,
  notes VARCHAR(1000) NULL,
{_nutrient_ddl(MEAL_NUTRIENT_TYPES)}  totals_incomplete_json JSON NULL,
  totals_nature VARCHAR(16) NULL,
  item_count SMALLINT NOT NULL DEFAULT 0,
  engine_version VARCHAR(32) NOT NULL,
  ai_job_id VARCHAR(64) NULL,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY {IDX_MEALS_USER_DATE} (user_id, local_date),
  CONSTRAINT {FK_MEALS_USER} FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

CREATE_ITEMS_SQL = f"""
CREATE TABLE IF NOT EXISTS {ITEMS} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  meal_id BIGINT NOT NULL,
  position SMALLINT NOT NULL,
  food_id BIGINT NULL,
  portion_id BIGINT NULL,
  item_label VARCHAR(255) NOT NULL,
  quantity DECIMAL(10,3) NOT NULL,
  unit VARCHAR(24) NOT NULL,
  amount DECIMAL(9,2) NOT NULL,
  basis VARCHAR(2) NOT NULL,
{_nutrient_ddl(ITEM_NUTRIENT_TYPES)}  nature VARCHAR(16) NOT NULL,
  match_confidence DECIMAL(4,3) NULL,
  limitations_json JSON NULL,
  catalog_snapshot_json JSON NULL,
  engine_version VARCHAR(32) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY {IDX_ITEMS_MEAL} (meal_id, position),
  KEY {IDX_ITEMS_FOOD} (food_id),
  CONSTRAINT {FK_ITEMS_MEAL} FOREIGN KEY (meal_id) REFERENCES {MEALS}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_ITEMS_SQL = f"DROP TABLE IF EXISTS {ITEMS}"
DROP_MEALS_SQL = f"DROP TABLE IF EXISTS {MEALS}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_MEAL_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "local_date": ("date", "NO"),
    "meal_slot": ("varchar(24)", "NO"),
    "eaten_at": ("time", "YES"),
    "source": ("varchar(16)", "NO"),
    "description_raw": ("text", "YES"),
    "notes": ("varchar(1000)", "YES"),
    **{coluna: (tipo, "YES") for coluna, tipo in MEAL_NUTRIENT_TYPES.items()},
    "totals_incomplete_json": ("json", "YES"),
    "totals_nature": ("varchar(16)", "YES"),
    "item_count": ("smallint", "NO"),
    "engine_version": ("varchar(32)", "NO"),
    "ai_job_id": ("varchar(64)", "YES"),
    "created_by": ("bigint", "YES"),
    "updated_by": ("bigint", "YES"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
}

EXPECTED_ITEM_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "meal_id": ("bigint", "NO"),
    "position": ("smallint", "NO"),
    "food_id": ("bigint", "YES"),
    "portion_id": ("bigint", "YES"),
    "item_label": ("varchar(255)", "NO"),
    "quantity": ("decimal(10,3)", "NO"),
    "unit": ("varchar(24)", "NO"),
    "amount": ("decimal(9,2)", "NO"),
    "basis": ("varchar(2)", "NO"),
    **{coluna: (tipo, "YES") for coluna, tipo in ITEM_NUTRIENT_TYPES.items()},
    "nature": ("varchar(16)", "NO"),
    "match_confidence": ("decimal(4,3)", "YES"),
    "limitations_json": ("json", "YES"),
    "catalog_snapshot_json": ("json", "YES"),
    "engine_version": ("varchar(32)", "NO"),
    "created_at": ("datetime", "NO"),
}

# índice -> (UNIQUE?, colunas)
EXPECTED_MEAL_INDEXES: dict[str, tuple[bool, tuple[str, ...]]] = {
    IDX_MEALS_USER_DATE: (False, ("user_id", "local_date")),
}
EXPECTED_ITEM_INDEXES: dict[str, tuple[bool, tuple[str, ...]]] = {
    IDX_ITEMS_MEAL: (False, ("meal_id", "position")),
    IDX_ITEMS_FOOD: (False, ("food_id",)),
}

# `tinyint(4)`/`smallint(6)` -> sem largura; `tinyint(1)` (booleano) fica como está.
_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class MealsError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise MealsError(f"082: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db):
    names = (MEALS, ITEMS, USERS_TABLE)
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
        (MEALS, ITEMS),
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


def _check_table(table, engines, columns, indexes, expected_columns, expected_indexes) -> None:
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
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, indexes = _read(db)
    if USERS_TABLE not in engines:
        if bool(getattr(db, "dry_run", False)):
            return [(CREATE_MEALS_SQL, None), (CREATE_ITEMS_SQL, None)]
        _fail(f"tabela {USERS_TABLE} nao existe")
    _check_users(engines, columns)
    statements: list[tuple[str, tuple | None]] = []
    if MEALS in engines:
        _check_table(MEALS, engines, columns, indexes, EXPECTED_MEAL_COLUMNS, EXPECTED_MEAL_INDEXES)
    else:
        statements.append((CREATE_MEALS_SQL, None))
    if ITEMS in engines:
        _check_table(ITEMS, engines, columns, indexes, EXPECTED_ITEM_COLUMNS, EXPECTED_ITEM_INDEXES)
    else:
        statements.append((CREATE_ITEMS_SQL, None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: DROP das tabelas que existem (itens primeiro, pela FK)."""
    engines, _columns, _indexes = _read(db)
    statements: list[tuple[str, tuple | None]] = []
    if ITEMS in engines:
        statements.append((DROP_ITEMS_SQL, None))
    if MEALS in engines:
        statements.append((DROP_MEALS_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

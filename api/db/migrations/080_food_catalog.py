"""080 - catálogo de alimentos (plano de nutrição, Lote L3a, 2026-10-17).

    food_items                      um alimento, valores POR 100 g ou 100 ml (`basis`)
      id                    BIGINT AUTO_INCREMENT PK
      source                VARCHAR(16)       `domain/nutrition/catalog.FoodSource` (taco, usda, off,
                                              tbca_manual, user, label_photo, ai_estimate)
      source_id             VARCHAR(64) NULL  id na fonte (número da TACO, fdcId, EAN); NULL no
                                              cadastro do usuário
      source_version        VARCHAR(32) NULL  edição/versão da fonte (`4ed-2011` na TACO)
      barcode               VARCHAR(32) NULL  EAN/GTIN (L3b, Open Food Facts)
      name                  VARCHAR(255)      nome como na fonte
      name_normalized       VARCHAR(255)      `food_text.normalize_name(name)`: FULLTEXT + prefixo
      brand                 VARCHAR(120) NULL
      category              VARCHAR(120) NULL grupo da fonte (os 15 grupos da TACO)
      basis                 VARCHAR(2)        `catalog.FOOD_BASES` ('g' | 'ml')
      energy_kcal ... sodium_mg  DECIMAL NULL códigos da RDC 429 (`catalog.PER_100_FIELDS`); NULL =
                                              a fonte não informa (nunca 0 inventado)
      serving_size          DECIMAL(7,2) NULL porção do rótulo, na base
      household_measure     VARCHAR(120) NULL medida caseira do rótulo
      servings_per_package  DECIMAL(7,2) NULL
      license / citation    VARCHAR NULL      por linha (R4): citação obrigatória da fonte
      owner_user_id         BIGINT NULL -> users(id) ON DELETE CASCADE; NULL = público (D-G)
      label_s3_key          VARCHAR(255) NULL foto do rótulo (L6)
      is_active             TINYINT(1)
      created_by            BIGINT NULL       quem cadastrou; referência LÓGICA
      created_at / updated_at
      UNIQUE uq_fi_source (source, source_id)  upsert idempotente do seed (081) e dos caches
      KEY idx_fi_barcode (barcode)
      KEY idx_fi_name_prefix (name_normalized(64))  busca por prefixo (termo curto demais p/ FULLTEXT)
      KEY idx_fi_owner (owner_user_id)
      FULLTEXT ft_fi_name (name_normalized)

    food_portions                   porção de um alimento ("1 unidade média = 86 g")
      id                    BIGINT AUTO_INCREMENT PK
      food_id               BIGINT            referência LÓGICA a food_items(id) (+ índice)
      label                 VARCHAR(120)
      amount                DECIMAL(8,2)      quantidade NA BASE do alimento (g, ou ml se basis = ml)
      source                VARCHAR(16)       `catalog.FoodSource` de quem criou (user nas manuais)
      owner_user_id         BIGINT NULL -> users(id) ON DELETE CASCADE; NULL = pública
      created_by            BIGINT NULL       referência LÓGICA
      created_at / updated_at
      KEY idx_fp_food (food_id), KEY idx_fp_owner (owner_user_id)

--- Decisões do DDL -------------------------------------------------------

1. **FULLTEXT nasce aqui, antes do seed** (risco R1 do plano): o índice existe
   desde o CREATE (sem `ALTER ... ADD FULLTEXT` com reconstrução depois da
   carga) e a 081 só insere. O texto indexado é o NORMALIZADO pelo motor
   (sem acento, sinônimos, stopwords), o mesmo que a busca envia.
2. **Nomes de coluna = códigos da RDC 429 do motor** (`labels.RDC429_FIELDS`,
   `catalog.PER_100_FIELDS`): `carbohydrates_g`, `total_fat_g`,
   `total_sugars_g`... O rascunho do §6.2 do plano (`carbs_g`, `fat_g`,
   `sugars_total_g`, `serving_size_g`, `serving_label`) foi trocado para não
   haver conversão de nome fora do dono (mesma regra da 078). Porção da tabela
   em `food_portions.amount` (na base), não `grams`: um alimento por 100 ml
   tem porção em ml.
3. **FK só para `users`** (`owner_user_id`, nas duas tabelas): alimento e
   porção privados somem com o dono; os públicos (NULL) ficam. `food_id`,
   `created_by` são referências lógicas (o diário do L4 congela os macros no
   item, E4). Nenhuma FK para `exercise_catalog` (a 028 recusa).
4. **Códigos em VARCHAR**, vocabulário do motor; a migração não enumera.
5. **DECIMAL**: gramas até 99.999,99 (`DECIMAL(7,2)`); sódio em mg até
   9.999.999,99 (`DECIMAL(9,2)`; o sal da TACO tem ~39 mil mg/100 g). As
   faixas da entrada são do motor (`catalog.validate_food_values`).

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa; `down`/`plan_down`
fazem DROP TABLE (porções primeiro).

- tabela ausente -> 1 `CREATE TABLE` cada (2 num banco novo);
- presentes na forma esperada -> 0 statements (reexecução);
- coluna/tipo/nulabilidade diferente, índice ausente ou de outro tipo
  (FULLTEXT, UNIQUE, prefixo), ENGINE != InnoDB ou `users` incompatível ->
  `FoodCatalogError` ANTES do primeiro statement;
- `users` ausente: erro numa execução real; aceito em DRY-RUN.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; nenhuma
literal com ':' (viraria parâmetro no shim); módulo autocontido (não importa
código do app).
"""

from __future__ import annotations

import re

ITEMS = "food_items"
PORTIONS = "food_portions"
USERS_TABLE = "users"

IDX_SOURCE = "uq_fi_source"
IDX_BARCODE = "idx_fi_barcode"
IDX_NAME_PREFIX = "idx_fi_name_prefix"
IDX_ITEM_OWNER = "idx_fi_owner"
IDX_FULLTEXT = "ft_fi_name"
FK_ITEM_OWNER = "fk_fi_owner"
IDX_PORTION_FOOD = "idx_fp_food"
IDX_PORTION_OWNER = "idx_fp_owner"
FK_PORTION_OWNER = "fk_fp_owner"
NAME_PREFIX_CHARS = 64

PER_100_GRAM_COLUMNS = (
    "energy_kcal",
    "carbohydrates_g",
    "total_sugars_g",
    "added_sugars_g",
    "protein_g",
    "total_fat_g",
    "saturated_fat_g",
    "trans_fat_g",
    "fiber_g",
)

_GRAM_COLUMNS_DDL = "".join(f"  {coluna} DECIMAL(7,2) NULL,\n" for coluna in PER_100_GRAM_COLUMNS)

CREATE_ITEMS_SQL = f"""
CREATE TABLE IF NOT EXISTS {ITEMS} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  source VARCHAR(16) NOT NULL,
  source_id VARCHAR(64) NULL,
  source_version VARCHAR(32) NULL,
  barcode VARCHAR(32) NULL,
  name VARCHAR(255) NOT NULL,
  name_normalized VARCHAR(255) NOT NULL,
  brand VARCHAR(120) NULL,
  category VARCHAR(120) NULL,
  basis VARCHAR(2) NOT NULL DEFAULT 'g',
{_GRAM_COLUMNS_DDL}  sodium_mg DECIMAL(9,2) NULL,
  serving_size DECIMAL(7,2) NULL,
  household_measure VARCHAR(120) NULL,
  servings_per_package DECIMAL(7,2) NULL,
  license VARCHAR(255) NULL,
  citation VARCHAR(500) NULL,
  owner_user_id BIGINT NULL,
  label_s3_key VARCHAR(255) NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_by BIGINT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY {IDX_SOURCE} (source, source_id),
  KEY {IDX_BARCODE} (barcode),
  KEY {IDX_NAME_PREFIX} (name_normalized({NAME_PREFIX_CHARS})),
  KEY {IDX_ITEM_OWNER} (owner_user_id),
  FULLTEXT KEY {IDX_FULLTEXT} (name_normalized),
  CONSTRAINT {FK_ITEM_OWNER} FOREIGN KEY (owner_user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

CREATE_PORTIONS_SQL = f"""
CREATE TABLE IF NOT EXISTS {PORTIONS} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  food_id BIGINT NOT NULL,
  label VARCHAR(120) NOT NULL,
  amount DECIMAL(8,2) NOT NULL,
  source VARCHAR(16) NOT NULL,
  owner_user_id BIGINT NULL,
  created_by BIGINT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY {IDX_PORTION_FOOD} (food_id),
  KEY {IDX_PORTION_OWNER} (owner_user_id),
  CONSTRAINT {FK_PORTION_OWNER} FOREIGN KEY (owner_user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_PORTIONS_SQL = f"DROP TABLE IF EXISTS {PORTIONS}"
DROP_ITEMS_SQL = f"DROP TABLE IF EXISTS {ITEMS}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_ITEM_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "source": ("varchar(16)", "NO"),
    "source_id": ("varchar(64)", "YES"),
    "source_version": ("varchar(32)", "YES"),
    "barcode": ("varchar(32)", "YES"),
    "name": ("varchar(255)", "NO"),
    "name_normalized": ("varchar(255)", "NO"),
    "brand": ("varchar(120)", "YES"),
    "category": ("varchar(120)", "YES"),
    "basis": ("varchar(2)", "NO"),
    **{coluna: ("decimal(7,2)", "YES") for coluna in PER_100_GRAM_COLUMNS},
    "sodium_mg": ("decimal(9,2)", "YES"),
    "serving_size": ("decimal(7,2)", "YES"),
    "household_measure": ("varchar(120)", "YES"),
    "servings_per_package": ("decimal(7,2)", "YES"),
    "license": ("varchar(255)", "YES"),
    "citation": ("varchar(500)", "YES"),
    "owner_user_id": ("bigint", "YES"),
    "label_s3_key": ("varchar(255)", "YES"),
    "is_active": ("tinyint(1)", "NO"),
    "created_by": ("bigint", "YES"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
}

EXPECTED_PORTION_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "food_id": ("bigint", "NO"),
    "label": ("varchar(120)", "NO"),
    "amount": ("decimal(8,2)", "NO"),
    "source": ("varchar(16)", "NO"),
    "owner_user_id": ("bigint", "YES"),
    "created_by": ("bigint", "YES"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
}

# índice -> (UNIQUE?, colunas, tipo, prefixo por coluna)
EXPECTED_ITEM_INDEXES: dict[str, tuple[bool, tuple[str, ...], str, tuple[int | None, ...]]] = {
    IDX_SOURCE: (True, ("source", "source_id"), "BTREE", (None, None)),
    IDX_BARCODE: (False, ("barcode",), "BTREE", (None,)),
    IDX_NAME_PREFIX: (False, ("name_normalized",), "BTREE", (NAME_PREFIX_CHARS,)),
    IDX_ITEM_OWNER: (False, ("owner_user_id",), "BTREE", (None,)),
    IDX_FULLTEXT: (False, ("name_normalized",), "FULLTEXT", (None,)),
}
EXPECTED_PORTION_INDEXES: dict[str, tuple[bool, tuple[str, ...], str, tuple[int | None, ...]]] = {
    IDX_PORTION_FOOD: (False, ("food_id",), "BTREE", (None,)),
    IDX_PORTION_OWNER: (False, ("owner_user_id",), "BTREE", (None,)),
}

# `tinyint(4)` -> `tinyint`; `tinyint(1)` (booleano) fica como está.
_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class FoodCatalogError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise FoodCatalogError(f"080: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _sub_part(value) -> int | None:
    texto = _text(value).strip()
    return int(texto) if texto else None


def _read(db):
    names = (ITEMS, PORTIONS, USERS_TABLE)
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
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE, INDEX_TYPE, SUB_PART "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
        (ITEMS, PORTIONS),
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
    for name, (unique, cols, index_type, prefixes) in expected_indexes.items():
        rows = indexes.get((table, name))
        if not rows:
            _fail(f"{table}.{name} nao existe")
        ordered = sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"]))
        found = tuple(_text(r["COLUMN_NAME"]) for r in ordered)
        if found != cols:
            _fail(f"{table}.{name} cobre {found}, esperado {cols}")
        if (int(ordered[0]["NON_UNIQUE"]) == 0) != unique:
            _fail(f"{table}.{name} {'NAO e' if unique else 'e'} UNIQUE")
        if _text(ordered[0].get("INDEX_TYPE")).upper() != index_type:
            _fail(f"{table}.{name} e {_text(ordered[0].get('INDEX_TYPE'))}, esperado {index_type}")
        found_prefixes = tuple(_sub_part(r.get("SUB_PART")) for r in ordered)
        if found_prefixes != prefixes:
            _fail(f"{table}.{name} tem prefixo {found_prefixes}, esperado {prefixes}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, indexes = _read(db)
    if USERS_TABLE not in engines:
        if bool(getattr(db, "dry_run", False)):
            return [(CREATE_ITEMS_SQL, None), (CREATE_PORTIONS_SQL, None)]
        _fail(f"tabela {USERS_TABLE} nao existe")
    _check_users(engines, columns)
    statements: list[tuple[str, tuple | None]] = []
    if ITEMS in engines:
        _check_table(ITEMS, engines, columns, indexes, EXPECTED_ITEM_COLUMNS, EXPECTED_ITEM_INDEXES)
    else:
        statements.append((CREATE_ITEMS_SQL, None))
    if PORTIONS in engines:
        _check_table(PORTIONS, engines, columns, indexes, EXPECTED_PORTION_COLUMNS, EXPECTED_PORTION_INDEXES)
    else:
        statements.append((CREATE_PORTIONS_SQL, None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: DROP das tabelas que existem (porções primeiro)."""
    engines, _columns, _indexes = _read(db)
    statements: list[tuple[str, tuple | None]] = []
    if PORTIONS in engines:
        statements.append((DROP_PORTIONS_SQL, None))
    if ITEMS in engines:
        statements.append((DROP_ITEMS_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

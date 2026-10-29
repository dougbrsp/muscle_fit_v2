"""022 - paridade de schema: `equipment_types` + colunas de `equipment`.

A Aurora de producao tem a tabela `equipment_types` e as colunas
`equipment.equipment_type_id/manufacturer/model/location` (criadas no legado
on-premise por fora das migracoes numeradas e trazidas pelo mysqldump), mas
nenhum arquivo de `api/db/migrations/` as criava. O codigo depende delas:
`api/app/gyms.py` (POST/PUT /equipment) e `api/app/exercise_catalog_admin.py`
(CRUD /equipment-types). Localmente isso quebrava esses endpoints e o seed com
dump de producao (`api/scripts/bootstrap_local_db.py`).

Definicao copiada do schema real da Aurora (MUSCLE_FIT.md, secao do schema):
- equipment_types: id BIGINT UNSIGNED AI PK, code VARCHAR(50) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL UNIQUE, description VARCHAR(255) NULL.
- equipment: equipment_type_id BIGINT UNSIGNED NULL, manufacturer VARCHAR(100)
  NULL, model VARCHAR(150) NULL, location VARCHAR(150) NULL; indice
  idx_equipment_type; FK fk_equipment_type -> equipment_types(id) (sem ON
  DELETE: o DELETE de tipo ja checa uso antes, em exercise_catalog_admin.py).

Na Aurora tudo ja existe -> no-op (so leituras em information_schema).
Nao ha seed de tipos: na Aurora eles ja existem e sao mantidos pela tela de
admin; reinserir codigos aqui poderia ressuscitar tipos removidos.
"""

EQUIPMENT_COLUMNS = (
    ("equipment_type_id", "BIGINT UNSIGNED NULL"),
    ("manufacturer", "VARCHAR(100) NULL"),
    ("model", "VARCHAR(150) NULL"),
    ("location", "VARCHAR(150) NULL"),
)


def _column_has_index(db, table, column):
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s AND SEQ_IN_INDEX = 1",
        (table, column),
    )
    return int(row["n"] or 0) > 0 if row else False


def _column_has_fk_to(db, table, column, referenced_table):
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s "
        "AND REFERENCED_TABLE_NAME = %s",
        (table, column, referenced_table),
    )
    return int(row["n"] or 0) > 0 if row else False


def up(db):
    if not db.table_exists("equipment_types"):
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS equipment_types (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              code VARCHAR(50) NOT NULL,
              name VARCHAR(100) NOT NULL,
              description VARCHAR(255) NULL,
              PRIMARY KEY (id),
              UNIQUE KEY code (code),
              UNIQUE KEY name (name)
            ) ENGINE=InnoDB
            """
        )

    for column, definition in EQUIPMENT_COLUMNS:
        if not db.column_exists("equipment", column):
            db.execute(f"ALTER TABLE equipment ADD COLUMN {column} {definition}")

    # Checagem por coluna (e nao so pelo nome) para nao duplicar indice/FK caso
    # algum ambiente os tenha com outro nome.
    if not db.index_exists("equipment", "idx_equipment_type") and not _column_has_index(
        db, "equipment", "equipment_type_id"
    ):
        db.execute("ALTER TABLE equipment ADD INDEX idx_equipment_type (equipment_type_id)")

    if not db.fk_exists("equipment", "fk_equipment_type") and not _column_has_fk_to(
        db, "equipment", "equipment_type_id", "equipment_types"
    ):
        db.execute(
            "ALTER TABLE equipment ADD CONSTRAINT fk_equipment_type "
            "FOREIGN KEY (equipment_type_id) REFERENCES equipment_types (id)"
        )

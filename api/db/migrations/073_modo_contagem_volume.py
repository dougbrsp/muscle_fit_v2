"""073 - modo de contagem do volume por Modelo (Lote 2 do plano "modo de
contagem", 2026-10-16).

    workout_plan_template_versions
      + volume_counting_mode       ENUM('primary_only','primary_secondary')
                                   NOT NULL DEFAULT 'primary_only'
    workout_plan_templates
      + volume_counting_mode       idem (espelho da vigente, como `volume_mode`)

    muscle_volume_class_mode_ranges              (NOVA) faixas por frequencia
      PK (class_id, counting_mode, frequency_bucket)   dos modos != primary_only
      FK class_id -> muscle_volume_classes(id) ON DELETE CASCADE
      sets_min/max, exercises_min/max, *_source/*_evidence (forma da 057),
      copied_from_counting_mode, updated_at

    muscle_volume_class_weekly_targets           (NOVA) meta semanal dos
      PK (class_id, counting_mode)                     modos != primary_only
      FK class_id -> muscle_volume_classes(id) ON DELETE CASCADE
      weekly_sets_min/max, weekly_source, weekly_evidence,
      copied_from_counting_mode, updated_at

    muscle_volume_class_ranges / muscle_volume_classes: NAO MUDAM.

Por que: o usuario escolhe, AO MONTAR O MODELO, se o volume da metodologia
conta so o grupo PRIMARIO (`primary_only`, o de hoje) ou PRIMARIO + SECUNDARIO
ponderado pelo `stimulus_weight` (`primary_secondary`). O motor ja sabe contar
nos dois modos (Lote 1, `intelligence/methodology_volume.VolumeCountingMode`);
esta migracao da ao Modelo onde gravar a escolha e ao cadastro de macro-grupos
faixas e metas PROPRIAS do modo ponderado - para o indireto nao ser comparado
com as faixas do primario (o risco que fez a EXG-014 descartar o M45).

--- Decisoes do DDL -------------------------------------------------------

1. **ENUM, e nao VARCHAR.** O dominio e FECHADO pelo motor (os dois valores
   de `VolumeCountingMode`); valor novo de modo exige codigo novo no motor de
   qualquer forma. Mesmo criterio da intencao da metodologia (056).
   Espelho LITERAL do enum do motor: a migracao e autocontida e nao importa
   codigo do app (`test_migration_073_plano.py` confere a paridade).

2. **Colunas aditivas com DEFAULT `primary_only`** no Modelo e na versao. O
   deploy aplica o schema ANTES do codigo: o INSERT/UPDATE do codigo antigo
   nao cita as colunas novas e grava o default, que e o comportamento dele.

3. **O modo vai na VERSAO** (065), espelhado no Modelo como `volume_mode`:
   trocar o modo e mudanca estrutural e abre uma versao com
   `effective_from`. Sem backfill: o DEFAULT preenche as linhas existentes.

4. **Faixas e metas dos modos != `primary_only` em TABELAS FILHAS; as de
   `primary_only` continuam onde estao** (`muscle_volume_class_ranges` e
   `muscle_volume_classes.weekly_*`, intocadas: sem coluna nova, PK igual).
   As tabelas da 057 SAO o cadastro de `primary_only` por definicao. Por que
   nao o modo na PK de `muscle_volume_class_ranges`: o codigo antigo le essa
   tabela sem filtro e o CRUD antigo de `/ranges` faz `DELETE ... WHERE
   class_id` - na janela de deploy ou num rollback de codigo ele misturaria
   as linhas dos dois modos e apagaria as do ponderado. Com as filhas, o
   codigo antigo nunca ve o modo ponderado, e um rollback do codigo nao muda
   nenhuma faixa nem meta de `primary_only`. Filhas com CASCADE seguem a
   convencao da 057 (o que varia por dimensao vai numa filha), e um modo
   novo nao exige DDL na tabela mae. O unico lugar que escolhe a fonte pelo
   modo e o loader do motor (`methodology_volume_service.load_volume_classes`).

5. **"Nao calibrada" e uma coluna propria (`copied_from_counting_mode`), e
   nao um valor novo em `*_source`.** `*_source` diz a PROCEDENCIA do numero
   (EXG-0xx, derivado, admin) e a copia mantem a do original. NULL = valor
   proprio do modo; `primary_only` = copia ainda nao calibrada. O CRUD admin
   limpa a marca quando grava series (ou meta semanal) diferentes da copia.

6. **Sem FK nova para `exercise_catalog`** (a 028 recusa). As FKs novas
   apontam para `muscle_volume_classes`.

--- Seed ------------------------------------------------------------------

O modo ponderado nasce como COPIA EXATA do de hoje (decisao 1 do usuario,
2026-10-16): cada linha de `muscle_volume_class_ranges` e cada meta semanal de
`muscle_volume_classes`, com a mesma procedencia e evidencia, e
`copied_from_counting_mode = 'primary_only'` para a tela mostrar "copia, nao
calibrada". Dois `INSERT ... SELECT` com `NOT EXISTS`: reexecutar nao duplica
nem sobrescreve uma linha que o admin ja calibrou.

Nenhum Modelo usa o modo ponderado ao fim desta migracao. A medicao do Lote 0
esta no §38 de `docs/motores/FONTES_DA_VERDADE.md`.

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`plan_down(db)`/`down(db)` sao o inverso. Ordem (e por que):

1. CREATE das faixas por modo e 2. das metas por modo;
3. seed das faixas e 4. seed das metas (reexecutaveis);
5. versoes e 6. Modelos ganham `volume_counting_mode` - POR ULTIMO.

O seed entra no plano enquanto a forma final (passos 5-6) nao estiver
completa - mesma regra do backfill da 065: uma execucao interrompida depois
do DDL e antes do seed e completada pela reexecucao, e o banco convergido
planeja 0 statements (o seed nunca e reaplicado sobre um cadastro em que o
admin ja apagou ou recalibrou linhas).

- coluna/tabela com outra forma, tabela do baseline ausente ->
  `CountingModeError` ANTES do primeiro statement;
- `migrate --dry-run` de banco atrasado: tabela do baseline ausente e aceita
  (o plano sai completo) - mesmo padrao da 057/072.

`plan_down` PARA antes do primeiro statement se algum Modelo ou versao usa
`primary_secondary`: soltar a coluna mudaria em silencio a regua de um Modelo
do usuario. Sem isso, solta as colunas e as duas tabelas novas (perde
calibracao do modo ponderado, nunca comportamento).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
literais de modo como parametro; modulo autocontido (nao importa codigo do
app).
"""

from __future__ import annotations

import re

VERSIONS = "workout_plan_template_versions"
TEMPLATES = "workout_plan_templates"
CLASSES = "muscle_volume_classes"
RANGES = "muscle_volume_class_ranges"
MODE_RANGES = "muscle_volume_class_mode_ranges"
WEEKLY = "muscle_volume_class_weekly_targets"

COL_TEMPLATE_MODE = "volume_counting_mode"
COL_COPIED = "copied_from_counting_mode"

FK_MODE_RANGES_CLASS = "fk_mvcmr_class"
FK_WEEKLY_CLASS = "fk_mvcwt_class"

# Espelho LITERAL de `intelligence.methodology_volume.VolumeCountingMode` (o
# primeiro e o default, `DEFAULT_VOLUME_COUNTING_MODE`).
COUNTING_MODES = ("primary_only", "primary_secondary")
DEFAULT_MODE = COUNTING_MODES[0]
SEEDED_MODE = COUNTING_MODES[1]

# Espelhos da 057.
SOURCES = ("methodology", "derived", "admin")
BUCKETS = ("1x", "2x", "3x_plus")

MODE_SQL_TYPE = "ENUM(" + ", ".join(f"'{v}'" for v in COUNTING_MODES) + ")"
MODE_COLUMN_TYPE = "enum(" + ",".join(f"'{v}'" for v in COUNTING_MODES) + ")"
SOURCE_SQL_TYPE = "ENUM(" + ", ".join(f"'{v}'" for v in SOURCES) + ")"
SOURCE_COLUMN_TYPE = "enum(" + ",".join(f"'{v}'" for v in SOURCES) + ")"
BUCKET_SQL_TYPE = "ENUM(" + ", ".join(f"'{v}'" for v in BUCKETS) + ")"
BUCKET_COLUMN_TYPE = "enum(" + ",".join(f"'{v}'" for v in BUCKETS) + ")"

# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------

CREATE_MODE_RANGES_SQL = f"""
CREATE TABLE IF NOT EXISTS {MODE_RANGES} (
  class_id BIGINT NOT NULL,
  counting_mode {MODE_SQL_TYPE} NOT NULL,
  frequency_bucket {BUCKET_SQL_TYPE} NOT NULL,
  sets_min DECIMAL(5,1) NOT NULL,
  sets_max DECIMAL(5,1) NOT NULL,
  exercises_min SMALLINT NULL,
  exercises_max SMALLINT NULL,
  sets_source {SOURCE_SQL_TYPE} NOT NULL DEFAULT 'admin',
  sets_evidence TEXT NULL,
  exercises_source {SOURCE_SQL_TYPE} NULL,
  exercises_evidence TEXT NULL,
  {COL_COPIED} {MODE_SQL_TYPE} NULL DEFAULT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (class_id, counting_mode, frequency_bucket),
  CONSTRAINT {FK_MODE_RANGES_CLASS} FOREIGN KEY (class_id) REFERENCES {CLASSES}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

CREATE_WEEKLY_SQL = f"""
CREATE TABLE IF NOT EXISTS {WEEKLY} (
  class_id BIGINT NOT NULL,
  counting_mode {MODE_SQL_TYPE} NOT NULL,
  weekly_sets_min DECIMAL(5,1) NULL,
  weekly_sets_max DECIMAL(5,1) NULL,
  weekly_source {SOURCE_SQL_TYPE} NOT NULL DEFAULT 'admin',
  weekly_evidence TEXT NULL,
  {COL_COPIED} {MODE_SQL_TYPE} NULL DEFAULT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (class_id, counting_mode),
  CONSTRAINT {FK_WEEKLY_CLASS} FOREIGN KEY (class_id) REFERENCES {CLASSES}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

# Copia exata das faixas de primary_only (procedencia e evidencia inclusive).
SEED_MODE_RANGES_SQL = (
    f"INSERT INTO {MODE_RANGES} "
    "(class_id, counting_mode, frequency_bucket, sets_min, sets_max, exercises_min, exercises_max, "
    f"sets_source, sets_evidence, exercises_source, exercises_evidence, {COL_COPIED}) "
    "SELECT r.class_id, %s, r.frequency_bucket, r.sets_min, r.sets_max, r.exercises_min, r.exercises_max, "
    "r.sets_source, r.sets_evidence, r.exercises_source, r.exercises_evidence, %s "
    f"FROM {RANGES} r WHERE NOT EXISTS ("
    f"SELECT 1 FROM {MODE_RANGES} x WHERE x.class_id = r.class_id AND x.counting_mode = %s "
    "AND x.frequency_bucket = r.frequency_bucket)"
)
SEED_MODE_RANGES_PARAMS = (SEEDED_MODE, DEFAULT_MODE, SEEDED_MODE)

SEED_WEEKLY_SQL = (
    f"INSERT INTO {WEEKLY} "
    f"(class_id, counting_mode, weekly_sets_min, weekly_sets_max, weekly_source, weekly_evidence, {COL_COPIED}) "
    "SELECT c.id, %s, c.weekly_sets_min, c.weekly_sets_max, c.weekly_source, c.weekly_evidence, %s "
    f"FROM {CLASSES} c WHERE NOT EXISTS ("
    f"SELECT 1 FROM {WEEKLY} w WHERE w.class_id = c.id AND w.counting_mode = %s)"
)
SEED_WEEKLY_PARAMS = (SEEDED_MODE, DEFAULT_MODE, SEEDED_MODE)

ADD_VERSION_MODE_SQL = (
    f"ALTER TABLE {VERSIONS} ADD COLUMN {COL_TEMPLATE_MODE} {MODE_SQL_TYPE} NOT NULL "
    f"DEFAULT '{DEFAULT_MODE}' AFTER methodology_target_group_ids"
)
ADD_TEMPLATE_MODE_SQL = (
    f"ALTER TABLE {TEMPLATES} ADD COLUMN {COL_TEMPLATE_MODE} {MODE_SQL_TYPE} NOT NULL "
    f"DEFAULT '{DEFAULT_MODE}' AFTER volume_mode"
)

# --- down -------------------------------------------------------------------

DROP_TEMPLATE_MODE_SQL = f"ALTER TABLE {TEMPLATES} DROP COLUMN {COL_TEMPLATE_MODE}"
DROP_VERSION_MODE_SQL = f"ALTER TABLE {VERSIONS} DROP COLUMN {COL_TEMPLATE_MODE}"
DROP_WEEKLY_SQL = f"DROP TABLE IF EXISTS {WEEKLY}"
DROP_MODE_RANGES_SQL = f"DROP TABLE IF EXISTS {MODE_RANGES}"

COUNT_NON_DEFAULT_SQL = "SELECT COUNT(*) AS n FROM {table} WHERE {column} <> %s"

EXPECTED_MODE_RANGES_COLUMNS: dict[str, tuple[str, str]] = {
    "class_id": ("bigint", "NO"),
    "counting_mode": (MODE_COLUMN_TYPE, "NO"),
    "frequency_bucket": (BUCKET_COLUMN_TYPE, "NO"),
    "sets_min": ("decimal(5,1)", "NO"),
    "sets_max": ("decimal(5,1)", "NO"),
    "exercises_min": ("smallint", "YES"),
    "exercises_max": ("smallint", "YES"),
    "sets_source": (SOURCE_COLUMN_TYPE, "NO"),
    "sets_evidence": ("text", "YES"),
    "exercises_source": (SOURCE_COLUMN_TYPE, "YES"),
    "exercises_evidence": ("text", "YES"),
    COL_COPIED: (MODE_COLUMN_TYPE, "YES"),
    "updated_at": ("datetime", "NO"),
}
EXPECTED_WEEKLY_COLUMNS: dict[str, tuple[str, str]] = {
    "class_id": ("bigint", "NO"),
    "counting_mode": (MODE_COLUMN_TYPE, "NO"),
    "weekly_sets_min": ("decimal(5,1)", "YES"),
    "weekly_sets_max": ("decimal(5,1)", "YES"),
    "weekly_source": (SOURCE_COLUMN_TYPE, "NO"),
    "weekly_evidence": ("text", "YES"),
    COL_COPIED: (MODE_COLUMN_TYPE, "YES"),
    "updated_at": ("datetime", "NO"),
}

# tabela nova -> (colunas esperadas, FK esperada)
NEW_TABLES = {
    MODE_RANGES: (EXPECTED_MODE_RANGES_COLUMNS, FK_MODE_RANGES_CLASS),
    WEEKLY: (EXPECTED_WEEKLY_COLUMNS, FK_WEEKLY_CLASS),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class CountingModeError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise CountingModeError(f"073: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    text = _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())
    return "".join(text.split())


def _read(db):
    """(tabelas, colunas, FKs das tabelas novas) - so `information_schema`."""
    names = (VERSIONS, TEMPLATES, CLASSES, RANGES, MODE_RANGES, WEEKLY)
    placeholders = ", ".join(["%s"] * len(names))
    tables = {
        _text(row["TABLE_NAME"]): _text(row.get("ENGINE"))
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    columns = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    fks = {
        (_text(row["TABLE_NAME"]), _text(row["CONSTRAINT_NAME"]))
        for row in db.query(
            "SELECT TABLE_NAME, CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s) AND REFERENCED_TABLE_NAME IS NOT NULL",
            (MODE_RANGES, WEEKLY),
        )
    }
    return tables, columns, fks


def _check_column(columns, table: str, name: str, expected_type: str, expected_nullable: str) -> bool:
    """Devolve se a coluna existe; forma divergente e erro."""
    info = columns.get((table, name))
    if info is None:
        return False
    tipo = _normalize_type(info["COLUMN_TYPE"])
    if tipo != expected_type:
        _fail(f"{table}.{name} e {tipo}, esperado {expected_type}")
    if _text(info["IS_NULLABLE"]) != expected_nullable:
        _fail(f"{table}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    return True


def _check_new_table(table: str, tables, columns, fks) -> bool:
    if table not in tables:
        return False
    expected, fk = NEW_TABLES[table]
    if tables[table] and tables[table].lower() != "innodb":
        _fail(f"{table} usa ENGINE={tables[table]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in expected.items():
        if not _check_column(columns, table, name, expected_type, expected_nullable):
            _fail(f"{table}.{name} nao existe")
    if (table, fk) not in fks:
        _fail(f"{table} existe sem a FK {fk}")
    return True


def _full_plan() -> list[tuple[str, tuple | None]]:
    return [
        (CREATE_MODE_RANGES_SQL, None),
        (CREATE_WEEKLY_SQL, None),
        (SEED_MODE_RANGES_SQL, SEED_MODE_RANGES_PARAMS),
        (SEED_WEEKLY_SQL, SEED_WEEKLY_PARAMS),
        (ADD_VERSION_MODE_SQL, None),
        (ADD_TEMPLATE_MODE_SQL, None),
    ]


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura (`information_schema`). Devolve [(sql, params)] na ordem."""
    tables, columns, fks = _read(db)
    missing = [name for name in (VERSIONS, TEMPLATES, CLASSES, RANGES) if name not in tables]
    if missing:
        if bool(getattr(db, "dry_run", False)):
            return _full_plan()
        _fail(f"tabela(s) {', '.join(missing)} nao existe(m) (a 057 e a 065 precisam estar aplicadas)")

    tem_mode_ranges = _check_new_table(MODE_RANGES, tables, columns, fks)
    tem_weekly = _check_new_table(WEEKLY, tables, columns, fks)
    tem_version_mode = _check_column(columns, VERSIONS, COL_TEMPLATE_MODE, MODE_COLUMN_TYPE, "NO")
    tem_template_mode = _check_column(columns, TEMPLATES, COL_TEMPLATE_MODE, MODE_COLUMN_TYPE, "NO")

    statements: list[tuple[str, tuple | None]] = []
    if not tem_mode_ranges:
        statements.append((CREATE_MODE_RANGES_SQL, None))
    if not tem_weekly:
        statements.append((CREATE_WEEKLY_SQL, None))
    # Seed enquanto a forma final nao estiver completa (ver docstring).
    if not (tem_version_mode and tem_template_mode):
        statements.append((SEED_MODE_RANGES_SQL, SEED_MODE_RANGES_PARAMS))
        statements.append((SEED_WEEKLY_SQL, SEED_WEEKLY_PARAMS))
    if not tem_version_mode:
        statements.append((ADD_VERSION_MODE_SQL, None))
    if not tem_template_mode:
        statements.append((ADD_TEMPLATE_MODE_SQL, None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`. Recusa (antes do primeiro statement) se algum Modelo
    ou versao usa um modo != primary_only."""
    tables, columns, _fks = _read(db)
    for table in (VERSIONS, TEMPLATES):
        if (table, COL_TEMPLATE_MODE) in columns:
            rows = db.query(
                COUNT_NON_DEFAULT_SQL.format(table=table, column=COL_TEMPLATE_MODE), (DEFAULT_MODE,)
            )
            n = int((rows[0] if rows else {}).get("n") or 0)
            if n:
                _fail(
                    f"{n} linha(s) de {table} usam modo de contagem != {DEFAULT_MODE}; "
                    "volte esses Modelos para primary_only antes de reverter"
                )

    statements: list[tuple[str, tuple | None]] = []
    if (TEMPLATES, COL_TEMPLATE_MODE) in columns:
        statements.append((DROP_TEMPLATE_MODE_SQL, None))
    if (VERSIONS, COL_TEMPLATE_MODE) in columns:
        statements.append((DROP_VERSION_MODE_SQL, None))
    if WEEKLY in tables:
        statements.append((DROP_WEEKLY_SQL, None))
    if MODE_RANGES in tables:
        statements.append((DROP_MODE_RANGES_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

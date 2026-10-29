"""097 - zonas de FC do Intervals.icu e tempo em zona por atividade (M98 C2;
dono `app/domain/hr_zones.py`).

    intervals_athlete_hr_zones            (nova) zonas de FC do atleta por esporte
      id                 BIGINT AUTO_INCREMENT PK
      user_id            BIGINT NOT NULL
      sport_settings_id  BIGINT NOT NULL    `sportSettings[].id` do `GET /athlete/{id}`
      sport_types        JSON NULL          `sportSettings[].types` (["Run", "VirtualRun", ...])
      hr_zones_bpm       JSON NULL          `sportSettings[].hr_zones`: LIMITE SUPERIOR de cada zona (bpm)
      hr_zone_names      JSON NULL          `sportSettings[].hr_zone_names`
      max_hr_bpm         SMALLINT NULL      `sportSettings[].max_hr`
      lthr_bpm           SMALLINT NULL      `sportSettings[].lthr`
      fetched_at         DATETIME           última leitura pelo sync
      UNIQUE uq_iahz_user_settings (user_id, sport_settings_id)

    intervals_activities
      + hr_zone_seconds  JSON NULL          segundos em cada zona de FC do Intervals
                                            (`icu_hr_zone_times`, na ordem das zonas)

Por que: a precedência das zonas do app (`hr_zones.resolve_athlete_zones`)
começa no Intervals.icu; e a aderência do cardio (`cardio_policy`) precisa do
tempo em zona de cada atividade realizada. Plano:
`docs/cardio/PLANO_MOTOR_CARDIO_2026-10-26.md` §C2.

--- Decisões do DDL -------------------------------------------------------

1. **Tudo anulável onde o Intervals pode não mandar.** As zonas guardadas são
   as do Intervals, cruas (limites superiores em bpm, como a API devolve); o
   mapeamento 7 -> 5 e a faixa de cada zona do app são do dono, na leitura.
   Nada de número de regra no banco.
2. **Sem FK** (como `intervals_activities`, que também não referencia
   `users`): é cache do Intervals, reescrito pelo sync. Nenhuma FK para
   `exercise_catalog`.
3. **`hr_zone_seconds` com `ALGORITHM=INSTANT`**: só metadado, coluna no fim,
   uma versão de linha (`INNODB_TABLES.TOTAL_ROW_VERSIONS`, limite 64).
4. **Backfill a partir do `raw_json` já gravado**: `icu_hr_zone_times` quando
   for um array. Só linhas com a coluna NULL: reexecutar não toca nada. O
   sync passa a gravar a coluna em todo upsert.

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ (`information_schema` e a contagem do backfill); `up(db)`
executa o plano; `down(db)`/`plan_down(db)` são o inverso (o runner de
produção não tem ação `down`: em produção, reverter é uma migração nova).

- tabela ausente -> 1 CREATE; presente com a forma esperada -> 0; forma
  divergente -> `IntervalsZonasError` antes de qualquer statement;
- coluna ausente -> 1 ALTER INSTANT; presente com a forma esperada -> 0;
- backfill: 1 UPDATE se houver linha a preencher; senão 0;
- convergido: 0 statements.
- `intervals_activities` ausente: erro numa execução real; aceito em DRY-RUN.

`down`: DROP COLUMN `hr_zone_seconds` se existir e DROP TABLE da tabela nova
(perde as zonas e o tempo em zona; o próximo sync e a reaplicação da 097
refazem tudo a partir do Intervals e do `raw_json`).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com ':' (viraria parâmetro no shim); módulo autocontido (não
importa código do app).
"""

from __future__ import annotations

import re

ZONES_TABLE = "intervals_athlete_hr_zones"
ACTIVITIES_TABLE = "intervals_activities"
COLUMN = "hr_zone_seconds"
UQ_USER_SETTINGS = "uq_iahz_user_settings"

# Caminho do tempo em zona no payload da atividade do Intervals.
ZONE_TIMES_PATH = "$.icu_hr_zone_times"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {ZONES_TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  sport_settings_id BIGINT NOT NULL,
  sport_types JSON NULL,
  hr_zones_bpm JSON NULL,
  hr_zone_names JSON NULL,
  max_hr_bpm SMALLINT NULL,
  lthr_bpm SMALLINT NULL,
  fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY {UQ_USER_SETTINGS} (user_id, sport_settings_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""
DROP_TABLE_SQL = f"DROP TABLE IF EXISTS {ZONES_TABLE}"

ADD_COLUMN_SQL = f"ALTER TABLE {ACTIVITIES_TABLE} ADD COLUMN {COLUMN} JSON NULL DEFAULT NULL, ALGORITHM=INSTANT"
DROP_COLUMN_SQL = f"ALTER TABLE {ACTIVITIES_TABLE} DROP COLUMN {COLUMN}"

_HAS_ZONE_TIMES = f"JSON_TYPE(JSON_EXTRACT(raw_json, '{ZONE_TIMES_PATH}')) = 'ARRAY'"
BACKFILL_SQL = (
    f"UPDATE {ACTIVITIES_TABLE} SET {COLUMN} = JSON_EXTRACT(raw_json, '{ZONE_TIMES_PATH}') "
    f"WHERE {COLUMN} IS NULL AND {_HAS_ZONE_TIMES}"
)
# Antes da coluna existir, toda linha com o campo será preenchida.
_COUNT_NEW_COLUMN_SQL = f"SELECT COUNT(*) AS n FROM {ACTIVITIES_TABLE} WHERE {_HAS_ZONE_TIMES}"
_COUNT_PENDING_SQL = f"SELECT COUNT(*) AS n FROM {ACTIVITIES_TABLE} WHERE {COLUMN} IS NULL AND {_HAS_ZONE_TIMES}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_ZONES_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "sport_settings_id": ("bigint", "NO"),
    "sport_types": ("json", "YES"),
    "hr_zones_bpm": ("json", "YES"),
    "hr_zone_names": ("json", "YES"),
    "max_hr_bpm": ("smallint", "YES"),
    "lthr_bpm": ("smallint", "YES"),
    "fetched_at": ("datetime", "NO"),
}
EXPECTED_UNIQUE_COLUMNS = ("user_id", "sport_settings_id")

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class IntervalsZonasError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise IntervalsZonasError(f"097: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    text = "".join(_text(column_type).split()).lower()
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", text)


def _read(db):
    """(tabelas existentes, colunas por (tabela, coluna), índice único da tabela nova)."""
    names = (ZONES_TABLE, ACTIVITIES_TABLE)
    tables = {
        _text(row["TABLE_NAME"])
        for row in db.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
            names,
        )
    }
    columns = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND ((TABLE_NAME = %s) OR (TABLE_NAME = %s AND COLUMN_NAME = %s))",
            (ZONES_TABLE, ACTIVITIES_TABLE, COLUMN),
        )
    }
    unique_rows = list(
        db.query(
            "SELECT INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
            (ZONES_TABLE, UQ_USER_SETTINGS),
        )
    )
    return tables, columns, unique_rows


def _check_zones_table(columns, unique_rows) -> None:
    for name, (expected_type, expected_nullable) in EXPECTED_ZONES_COLUMNS.items():
        info = columns.get((ZONES_TABLE, name))
        if info is None:
            _fail(f"{ZONES_TABLE}.{name} nao existe")
        found = _normalize_type(info["COLUMN_TYPE"])
        if found != expected_type:
            _fail(f"{ZONES_TABLE}.{name} e {found}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{ZONES_TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    ordered = sorted(unique_rows, key=lambda r: int(r["SEQ_IN_INDEX"]))
    found_cols = tuple(_text(r["COLUMN_NAME"]) for r in ordered)
    if found_cols != EXPECTED_UNIQUE_COLUMNS or int(ordered[0]["NON_UNIQUE"]) != 0:
        _fail(f"{ZONES_TABLE}.{UQ_USER_SETTINGS} cobre {found_cols}, esperado UNIQUE {EXPECTED_UNIQUE_COLUMNS}")


def _check_column(info) -> None:
    found = _normalize_type(info["COLUMN_TYPE"])
    if found != "json":
        _fail(f"{ACTIVITIES_TABLE}.{COLUMN} e {found}, esperado json")
    if _text(info["IS_NULLABLE"]) != "YES":
        _fail(f"{ACTIVITIES_TABLE}.{COLUMN} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado YES")


def _count(db, sql: str) -> int:
    rows = list(db.query(sql, None))
    return int(rows[0]["n"] or 0) if rows else 0


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    tables, columns, unique_rows = _read(db)
    dry_run = bool(getattr(db, "dry_run", False))
    statements: list[tuple[str, tuple | None]] = []

    if ZONES_TABLE in tables:
        _check_zones_table(columns, unique_rows)
    else:
        statements.append((CREATE_SQL, None))

    if ACTIVITIES_TABLE not in tables:
        if not dry_run:
            _fail(f"tabela {ACTIVITIES_TABLE} nao existe")
        return statements + [(ADD_COLUMN_SQL, None), (BACKFILL_SQL, None)]

    info = columns.get((ACTIVITIES_TABLE, COLUMN))
    if info is None:
        statements.append((ADD_COLUMN_SQL, None))
        pending = _count(db, _COUNT_NEW_COLUMN_SQL)
    else:
        _check_column(info)
        pending = _count(db, _COUNT_PENDING_SQL)
    if pending:
        statements.append((BACKFILL_SQL, None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta a coluna e a tabela nova, se existirem."""
    tables, columns, _unique = _read(db)
    statements: list[tuple[str, tuple | None]] = []
    if (ACTIVITIES_TABLE, COLUMN) in columns:
        statements.append((DROP_COLUMN_SQL, None))
    if ZONES_TABLE in tables:
        statements.append((DROP_TABLE_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

"""026 - colunas de sessoes de treino e fichas saem do runtime.

Ate aqui, cinco guards rodavam DDL no caminho de requisicao (uma vez por
processo, flag de modulo; na Lambda, uma vez por container):

- `workout_sessions._ensure_schema()` (POST /workout-sessions/start,
  /{id}/exercises, /{id}/exercises/{ex}/sets, /{id}/finish e
  PUT /exercises/logs/{id}), numa transacao propria: 5 SELECTs em
  `information_schema.COLUMNS` + ALTER se faltasse;
- `services._ensure_set_hr_columns` (POST /activities com series,
  PUT /exercises/logs/{id});
- `services._ensure_workout_plans_time_column` e
  `_ensure_workout_plans_split_day_column` (GET/POST /workout-plans,
  GET/PUT /workout-plans/{id});
- `services._ensure_plan_link_columns` (GET /workout-plans/{id}, POST/PUT
  /workout-plans via `_replace_plan_exercises`).

Nenhum deles fazia backfill de dados. Esta migracao garante o estado final que
eles produziam, conferido na Aurora (schema extraido em 2026-09-14) e no MySQL
de dev - identicos para estes objetos:

    activities.session_status            VARCHAR(40) NULL   KEY idx_activities_session_status
    activities.workout_plan_id           BIGINT NULL        KEY idx_activities_workout_plan_id
        FK fk_activities_workout_plan -> workout_plans(id) ON DELETE SET NULL
    exercise_sets.hr_avg_bpm/hr_max_bpm  DECIMAL(6,2) NULL
    exercise_sets.muscle_pain            TINYINT NULL
    exercise_sets.joint_pain             TINYINT(1) NULL
    exercises.workout_plan_exercise_id   BIGINT NULL        KEY idx_exercises_workout_plan_exercise_id
        FK fk_exercises_workout_plan_exercise -> workout_plan_exercises(id) ON DELETE SET NULL
    workout_plans.plan_time              TIME NULL
    workout_plans.split_day              VARCHAR(1) NULL
    workout_plan_exercises.mapping_id    BIGINT UNSIGNED NULL KEY idx_wpe_mapping
        FK (qualquer nome) -> exercise_catalog(id) ON DELETE SET NULL
    workout_plan_exercises.preferred_equipment_id BIGINT NULL KEY idx_wpe_pref_equipment
        FK fk_wpe_pref_equipment -> gym_equipment(id) ON DELETE SET NULL

Divergencia conhecida do guard: `_ensure_plan_link_columns` criava
`workout_plan_exercises.mapping_id BIGINT` (com sinal) com FK para
`exercise_catalog(id)`, que e `BIGINT UNSIGNED` - no MySQL 8 isso falha com
ER 3780. O formato real (Aurora, dev e o baseline apos a 006) e
`BIGINT UNSIGNED` com FK `fk_wpe_mapping_catalog`; e ele que a 026 cria e
exige.

No baseline 001..021 todas as colunas acima ja existem, exceto
`activities.workout_plan_id` e `exercises.workout_plan_exercise_id` (so os
guards as criavam).

Fluxo: `plan(db)` so LE (`information_schema` + contagens) e devolve os
statements; `up(db)` executa o plano. Toda incompatibilidade e detectada no
plano, antes do primeiro statement - nada e aplicado:

- coluna ausente -> um ALTER com a coluna, o indice e a FK (como o guard);
- coluna presente sem indice/FK -> so o ADD INDEX / ADD CONSTRAINT que falta;
- tudo presente (esperado na Aurora e em dev) -> 0 statements;
- tabela ausente, ENGINE diferente de InnoDB, tipo/nulabilidade/default
  divergente, indice ou FK com o nome esperado em outra coluna, FK da coluna
  para outro alvo/regra, tipo incompativel com a coluna referenciada, ou
  orfaos que impediriam a FK -> `SessoesFichasSchemaError`.

Indices e FKs sao reconhecidos por coluna (qualquer nome), nao so pelo nome.
Data API: sem PREPARE/multi-statement; cada statement vai numa chamada. Em
dry-run o runner so lista o plano. Reexecutavel: numa base completa, 0
statements.
"""

from __future__ import annotations

import re
from typing import NamedTuple

REQUIRED_TABLES = (
    "activities",
    "exercise_sets",
    "exercises",
    "workout_plans",
    "workout_plan_exercises",
    "exercise_catalog",
    "gym_equipment",
)


# NamedTuple (e nao dataclass): o runner carrega a migracao sem registra-la em
# sys.modules, e dataclasses depende disso.
class Fk(NamedTuple):
    name: str
    ref_table: str
    ref_column: str = "id"
    on_delete: str = "SET NULL"


class Column(NamedTuple):
    table: str
    name: str
    ddl_type: str  # como aparece no ADD COLUMN
    after: str
    index: str | None = None
    fk: Fk | None = None


# Ordem = ordem de execucao (a mesma dos guards; `after` pode citar coluna
# criada antes nesta lista).
COLUMNS = (
    Column("activities", "session_status", "VARCHAR(40)", "conducted_by", index="idx_activities_session_status"),
    Column(
        "activities",
        "workout_plan_id",
        "BIGINT",
        "conducted_by",
        index="idx_activities_workout_plan_id",
        fk=Fk("fk_activities_workout_plan", "workout_plans"),
    ),
    Column("exercise_sets", "hr_avg_bpm", "DECIMAL(6,2)", "rest_seconds"),
    Column("exercise_sets", "hr_max_bpm", "DECIMAL(6,2)", "hr_avg_bpm"),
    Column("exercise_sets", "muscle_pain", "TINYINT", "hr_max_bpm"),
    Column("exercise_sets", "joint_pain", "TINYINT(1)", "muscle_pain"),
    Column(
        "exercises",
        "workout_plan_exercise_id",
        "BIGINT",
        "mapping_id",
        index="idx_exercises_workout_plan_exercise_id",
        fk=Fk("fk_exercises_workout_plan_exercise", "workout_plan_exercises"),
    ),
    Column("workout_plans", "plan_time", "TIME", "plan_date"),
    Column("workout_plans", "split_day", "VARCHAR(1)", "plan_type"),
    Column(
        "workout_plan_exercises",
        "mapping_id",
        "BIGINT UNSIGNED",
        "amazfit_name",
        index="idx_wpe_mapping",
        fk=Fk("fk_wpe_mapping_catalog", "exercise_catalog"),
    ),
    Column(
        "workout_plan_exercises",
        "preferred_equipment_id",
        "BIGINT",
        "equipment_name",
        index="idx_wpe_pref_equipment",
        fk=Fk("fk_wpe_pref_equipment", "gym_equipment"),
    ),
)

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class SessoesFichasSchemaError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise SessoesFichasSchemaError(f"026: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `bigint(20) unsigned` (MySQL 5.7) == `bigint unsigned` (MySQL 8);
    # `tinyint(1)` e `tinyint` sao o mesmo tipo de armazenamento.
    text = " ".join(_text(column_type).strip().lower().split())
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", text)


def _in_clause(values) -> str:
    return ", ".join(["%s"] * len(values))


def _read_schema(db) -> tuple[dict, dict, dict, list]:
    tables = tuple({c.table for c in COLUMNS} | {c.fk.ref_table for c in COLUMNS if c.fk})
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({_in_clause(REQUIRED_TABLES)})",
            REQUIRED_TABLES,
        )
    }
    columns: dict[tuple[str, str], dict] = {}
    for row in db.query(
        "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
        f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({_in_clause(tables)})",
        tables,
    ):
        columns[(_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"]))] = row
    indexes: dict[tuple[str, str], list[str]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
        f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({_in_clause(tables)}) "
        "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
        tables,
    ):
        indexes.setdefault((_text(row["TABLE_NAME"]), _text(row["INDEX_NAME"])), []).append(_text(row["COLUMN_NAME"]))
    fks = db.query(
        "SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, "
        "r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k "
        "JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
        "ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME "
        "AND r.TABLE_NAME = k.TABLE_NAME "
        f"WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME IN ({_in_clause(tables)}) "
        "AND k.REFERENCED_TABLE_NAME IS NOT NULL",
        tables,
    )
    return engines, columns, indexes, [{k: _text(v) for k, v in fk.items()} for fk in fks]


def _check_existing_column(col: Column, info: dict) -> None:
    expected = _normalize_type(col.ddl_type)
    actual = _normalize_type(info["COLUMN_TYPE"])
    label = f"{col.table}.{col.name}"
    if actual != expected:
        _fail(f"{label} e {_text(info['COLUMN_TYPE'])}, esperado {expected}")
    if _text(info["IS_NULLABLE"]) != "YES":
        _fail(f"{label} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado YES")
    if info["COLUMN_DEFAULT"] is not None:
        _fail(f"{label} tem DEFAULT {_text(info['COLUMN_DEFAULT'])!r}, esperado NULL")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, indexes, fks = _read_schema(db)

    for table in REQUIRED_TABLES:
        if table not in engines:
            _fail(f"tabela {table} nao existe (esperada pelo baseline 001_schema.sql)")
        if engines[table].lower() != "innodb":
            _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")

    present = {key for key in columns}
    statements: list[tuple[str, tuple | None]] = []

    for col in COLUMNS:
        key = (col.table, col.name)
        col_fks = [fk for fk in fks if fk["TABLE_NAME"] == col.table and fk["COLUMN_NAME"] == col.name]
        index_named = indexes.get((col.table, col.index)) if col.index else None
        fk_named = [fk for fk in fks if col.fk and fk["TABLE_NAME"] == col.table and fk["CONSTRAINT_NAME"] == col.fk.name]

        if col.fk:
            ref = columns.get((col.fk.ref_table, col.fk.ref_column))
            if ref is None:
                _fail(f"{col.fk.ref_table}.{col.fk.ref_column} nao existe")
            if _normalize_type(ref["COLUMN_TYPE"]) != _normalize_type(col.ddl_type):
                _fail(
                    f"{col.fk.ref_table}.{col.fk.ref_column} e {_text(ref['COLUMN_TYPE'])}, "
                    f"incompativel com {col.table}.{col.name} {col.ddl_type.lower()}"
                )

        if key not in columns:
            if index_named is not None:
                _fail(f"indice {col.index} existe em {index_named}, esperado ({col.name})")
            if fk_named:
                _fail(f"constraint {col.fk.name} existe em outra coluna")
            after = f" AFTER {col.after}" if (col.table, col.after) in present else ""
            parts = [f"ADD COLUMN {col.name} {col.ddl_type} NULL{after}"]
            if col.index:
                parts.append(f"ADD KEY {col.index} ({col.name})")
            if col.fk:
                parts.append(
                    f"ADD CONSTRAINT {col.fk.name} FOREIGN KEY ({col.name}) "
                    f"REFERENCES {col.fk.ref_table}({col.fk.ref_column}) ON DELETE {col.fk.on_delete}"
                )
            statements.append((f"ALTER TABLE {col.table} " + ", ".join(parts), None))
            present.add(key)
            continue

        _check_existing_column(col, columns[key])

        if col.index and not any(
            cols and cols[0] == col.name for (table, _), cols in indexes.items() if table == col.table
        ):
            if index_named is not None:
                _fail(f"indice {col.index} existe em {index_named}, esperado ({col.name})")
            statements.append((f"ALTER TABLE {col.table} ADD INDEX {col.index} ({col.name})", None))

        if not col.fk:
            continue
        expected_target = (col.fk.ref_table, col.fk.ref_column, col.fk.on_delete)
        for fk in col_fks:
            target = (fk["REFERENCED_TABLE_NAME"], fk["REFERENCED_COLUMN_NAME"], fk["DELETE_RULE"])
            if target != expected_target:
                _fail(f"FK {fk['CONSTRAINT_NAME']} de {col.table}.{col.name} aponta para {target}, esperado {expected_target}")
        if col_fks:
            continue
        if fk_named:
            _fail(f"constraint {col.fk.name} existe em outra coluna")
        row = db.query_one(
            f"SELECT COUNT(*) AS n FROM {col.table} c LEFT JOIN {col.fk.ref_table} p "
            f"ON p.{col.fk.ref_column} = c.{col.name} WHERE c.{col.name} IS NOT NULL AND p.{col.fk.ref_column} IS NULL"
        )
        orphans = int(next(iter(row.values())) or 0) if row else 0
        if orphans:
            _fail(
                f"{orphans} linha(s) em {col.table}.{col.name} sem {col.fk.ref_table} correspondente; "
                f"FK {col.fk.name} nao pode ser criada"
            )
        statements.append(
            (
                f"ALTER TABLE {col.table} ADD CONSTRAINT {col.fk.name} FOREIGN KEY ({col.name}) "
                f"REFERENCES {col.fk.ref_table}({col.fk.ref_column}) ON DELETE {col.fk.on_delete}",
                None,
            )
        )

    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)

"""025 - schema de `cardio_recommendations` sai do runtime.

Ate aqui, a primeira chamada de cada processo a POST/GET/DELETE
/cardio-recommendations, ao handler da Lambda `musclefit-cardio-agent`
(`cardio_generation_core.dispatch_cardio_generation`) e ao contexto do agente
(`CardioAgentContextService._get_past_recommendations`) rodava
`cardio_recommendations._ensure_schema()`: `CREATE TABLE IF NOT EXISTS`, dois
`ALTER TABLE ... ADD` com erro "ja existe" engolido, backfill de `user_id` e
`MODIFY COLUMN user_id NOT NULL`. DDL faz commit implicito no MySQL (quebra a
atomicidade da transacao Data API aberta pelo shim) e cada statement custava
uma chamada `ExecuteStatement`. A tabela NUNCA esteve no baseline 001..021:
so existe onde algum endpoint de cardio ja rodou.

Esta migracao garante exatamente o estado final que o `_ensure_schema()`
produzia (conferido no MySQL de dev, criado por ele):

    id                      BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY
    user_id                 BIGINT NOT NULL            (sem indice/FK, como antes)
    reference_date          DATE NOT NULL              KEY idx_cardio_recommendations_date
    workout_plan_id         BIGINT NULL                KEY idx_cardio_recommendations_plan
    generated_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    model                   VARCHAR(120) NULL
    context_snapshot        JSON NULL
    recommendation_markdown MEDIUMTEXT NOT NULL
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    FK fk_cardio_recommendations_workout_plan (workout_plan_id)
       -> workout_plans(id) ON DELETE SET NULL

Fluxo: `plan(db)` so LE (`information_schema` + contagens) e devolve a lista
de statements; `up(db)` executa o plano. Toda incompatibilidade e detectada no
plano, antes do primeiro statement - nada e aplicado:

- tabela ausente -> um unico `CREATE TABLE` ja no formato final;
- tabela completa (esperado na Aurora e em dev) -> 0 statements;
- tabela de uma versao anterior do guard (sem `workout_plan_id`, sem
  `user_id`, sem FK/indice, `user_id` ainda NULL) -> so os ALTERs que faltam,
  mais o mesmo backfill de `user_id` para o admin ativo que o guard fazia;
- coluna com tipo/nulabilidade/default diferente, indice com o nome esperado
  em outra coluna, FK de `workout_plan_id` para outro alvo/regra, orfaos que
  impediriam a FK, ou `user_id` NULL sem admin ativo para o backfill ->
  `CardioRecommendationsSchemaError`.

Indices e FK sao reconhecidos por coluna (qualquer nome), nao so pelo nome.
Data API: sem PREPARE/multi-statement; cada statement vai numa chamada. Em
dry-run o runner so lista o plano. Reexecutavel: numa base completa, 0
statements.
"""

from __future__ import annotations

import re

TABLE = "cardio_recommendations"
FK_NAME = "fk_cardio_recommendations_workout_plan"
DATE_INDEX = "idx_cardio_recommendations_date"
PLAN_INDEX = "idx_cardio_recommendations_plan"

# coluna -> (COLUMN_TYPE normalizado, IS_NULLABLE, COLUMN_DEFAULT esperado ou None p/ nao conferir)
EXPECTED_COLUMNS = {
    "id": ("bigint", "NO", None),
    "user_id": ("bigint", "NO", None),
    "reference_date": ("date", "NO", None),
    "workout_plan_id": ("bigint", "YES", None),
    "generated_at": ("timestamp", "NO", "CURRENT_TIMESTAMP"),
    "model": ("varchar(120)", "YES", None),
    "context_snapshot": ("json", "YES", None),
    "recommendation_markdown": ("mediumtext", "NO", None),
    "created_at": ("timestamp", "NO", "CURRENT_TIMESTAMP"),
}

# Colunas que versoes anteriores do guard adicionavam depois do CREATE.
LATE_COLUMNS = ("user_id", "workout_plan_id")

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  reference_date DATE NOT NULL,
  workout_plan_id BIGINT NULL,
  generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  model VARCHAR(120) NULL,
  context_snapshot JSON NULL,
  recommendation_markdown MEDIUMTEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY {DATE_INDEX} (reference_date),
  KEY {PLAN_INDEX} (workout_plan_id),
  CONSTRAINT {FK_NAME} FOREIGN KEY (workout_plan_id) REFERENCES workout_plans(id) ON DELETE SET NULL
) ENGINE=InnoDB
"""

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class CardioRecommendationsSchemaError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise CardioRecommendationsSchemaError(f"025: {message}. Nenhum statement executado.")


def _normalize_type(column_type: str) -> str:
    # Aurora MySQL 2 (5.7) reporta `bigint(20)`; MySQL/Aurora 3 (8.0), `bigint`.
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", str(column_type).strip().lower())


def _normalize_default(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    return "CURRENT_TIMESTAMP" if text in ("CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP()", "NOW()") else text


def _scalar(db, sql, params=None) -> int:
    row = db.query_one(sql, params)
    return int(next(iter(row.values())) or 0) if row else 0


def _columns(db) -> dict[str, dict]:
    rows = db.query(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (TABLE,),
    )
    return {row["COLUMN_NAME"]: row for row in rows}


def _index_columns(db) -> dict[str, list[str]]:
    """INDEX_NAME -> colunas na ordem do indice."""
    rows = db.query(
        "SELECT INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s ORDER BY INDEX_NAME, SEQ_IN_INDEX",
        (TABLE,),
    )
    indexes: dict[str, list[str]] = {}
    for row in rows:
        indexes.setdefault(row["INDEX_NAME"], []).append(row["COLUMN_NAME"])
    return indexes


def _foreign_keys(db) -> list[dict]:
    return db.query(
        "SELECT k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, "
        "r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k "
        "JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
        "ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME "
        "AND r.TABLE_NAME = k.TABLE_NAME "
        "WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME = %s AND k.REFERENCED_TABLE_NAME IS NOT NULL",
        (TABLE,),
    )


def _engine(db) -> str:
    row = db.query_one(
        "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (TABLE,),
    )
    return str((row or {}).get("ENGINE") or "")


def _check_column(name: str, info: dict, *, allow_nullable: bool = False) -> None:
    expected_type, expected_nullable, expected_default = EXPECTED_COLUMNS[name]
    actual_type = _normalize_type(info["COLUMN_TYPE"])
    if actual_type != expected_type:
        _fail(f"{TABLE}.{name} e {info['COLUMN_TYPE']}, esperado {expected_type}")
    nullable = info["IS_NULLABLE"]
    if nullable != expected_nullable and not (allow_nullable and nullable == "YES"):
        _fail(f"{TABLE}.{name} tem IS_NULLABLE={nullable}, esperado {expected_nullable}")
    if expected_default is not None and _normalize_default(info["COLUMN_DEFAULT"]) != expected_default:
        _fail(f"{TABLE}.{name} tem DEFAULT {info['COLUMN_DEFAULT']!r}, esperado {expected_default}")


def _plan_index(indexes: dict[str, list[str]], column: str, name: str) -> list:
    if any(cols and cols[0] == column for cols in indexes.values()):
        return []
    if name in indexes:
        _fail(f"indice {name} existe em {indexes[name]}, esperado ({column})")
    return [(f"ALTER TABLE {TABLE} ADD INDEX {name} ({column})", None)]


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    for required in ("users", "workout_plans"):
        if not db.table_exists(required):
            _fail(f"tabela {required} nao existe (esperada pelo baseline 001_schema.sql)")

    if not db.table_exists(TABLE):
        return [(CREATE_TABLE_SQL, None)]

    engine = _engine(db)
    if engine.lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engine}, esperado InnoDB")

    columns = _columns(db)
    for name in EXPECTED_COLUMNS:
        if name in LATE_COLUMNS and name not in columns:
            continue
        if name not in columns:
            _fail(f"{TABLE}.{name} nao existe")
        _check_column(name, columns[name], allow_nullable=name == "user_id")
    if columns["id"]["COLUMN_KEY"] != "PRI" or "auto_increment" not in str(columns["id"]["EXTRA"]).lower():
        _fail(f"{TABLE}.id precisa ser PRIMARY KEY AUTO_INCREMENT")

    statements: list[tuple[str, tuple | None]] = []
    has_user_id = "user_id" in columns
    has_plan_id = "workout_plan_id" in columns

    if not has_user_id:
        statements.append((f"ALTER TABLE {TABLE} ADD COLUMN user_id BIGINT NULL AFTER id", None))
    if not has_plan_id:
        statements.append((f"ALTER TABLE {TABLE} ADD COLUMN workout_plan_id BIGINT NULL AFTER reference_date", None))

    indexes = _index_columns(db)
    statements += _plan_index(indexes, "reference_date", DATE_INDEX)
    statements += _plan_index(indexes, "workout_plan_id", PLAN_INDEX)

    fks = _foreign_keys(db)
    plan_fks = [fk for fk in fks if fk["COLUMN_NAME"] == "workout_plan_id"]
    for fk in plan_fks:
        target = (fk["REFERENCED_TABLE_NAME"], fk["REFERENCED_COLUMN_NAME"], fk["DELETE_RULE"])
        if target != ("workout_plans", "id", "SET NULL"):
            _fail(f"FK {fk['CONSTRAINT_NAME']} de workout_plan_id aponta para {target}, esperado (workout_plans, id, SET NULL)")
    if not plan_fks:
        if any(fk["CONSTRAINT_NAME"] == FK_NAME for fk in fks):
            _fail(f"constraint {FK_NAME} existe em outra coluna")
        if has_plan_id:
            orphans = _scalar(
                db,
                f"SELECT COUNT(*) AS n FROM {TABLE} c LEFT JOIN workout_plans p ON p.id = c.workout_plan_id "
                "WHERE c.workout_plan_id IS NOT NULL AND p.id IS NULL",
            )
            if orphans:
                _fail(f"{orphans} linha(s) com workout_plan_id sem plano correspondente; FK {FK_NAME} nao pode ser criada")
        statements.append(
            (
                f"ALTER TABLE {TABLE} ADD CONSTRAINT {FK_NAME} FOREIGN KEY (workout_plan_id) "
                "REFERENCES workout_plans(id) ON DELETE SET NULL",
                None,
            )
        )

    user_nullable = not has_user_id or columns["user_id"]["IS_NULLABLE"] == "YES"
    if user_nullable:
        if has_user_id:
            orphans = _scalar(db, f"SELECT COUNT(*) AS n FROM {TABLE} WHERE user_id IS NULL")
        else:
            orphans = _scalar(db, f"SELECT COUNT(*) AS n FROM {TABLE}")
        if orphans:
            admin = db.query_one("SELECT id FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id ASC LIMIT 1")
            if not admin:
                _fail(f"{orphans} linha(s) com user_id NULL e nenhum admin ativo para o backfill")
            statements.append((f"UPDATE {TABLE} SET user_id = %s WHERE user_id IS NULL", (int(admin["id"]),)))
        statements.append((f"ALTER TABLE {TABLE} MODIFY COLUMN user_id BIGINT NOT NULL", None))

    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)

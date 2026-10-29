"""027 - schema de integracoes, metricas e relatorios sai do runtime (grupo B).

Ate aqui, rotas de `devices`, `sleep_metrics`, `manual_effort_metrics`,
`intervals_integration`, `reports`, `gyms` e `exercise_mapping_catalog`
chamavam `_ensure_schema()` no caminho de requisicao: `CREATE TABLE IF NOT
EXISTS`, `ALTER TABLE ... ADD COLUMN` com erro "ja existe" engolido, backfill
de `user_id` para o admin ativo, `MODIFY ... NOT NULL` e troca de chave unica
/ PK para composta por usuario. DDL faz commit implicito no MySQL (quebra a
atomicidade da transacao Data API aberta pelo shim) e cada statement custava
uma chamada `ExecuteStatement` - em `devices`, em TODA requisicao (sem flag).
`sleep_metrics`, `manual_effort_metrics`, `intervals_activities`,
`intervals_wellness` e `reports` NUNCA estiveram no baseline 001..021; so
existem onde algum endpoint ja rodou. `devices` esta no 001, mas sem
`user_id`.

Esta migracao garante exatamente o estado final que os guards produziam,
conferido contra o `information_schema` da Aurora de producao (2026-09-14) e
do MySQL de dev - identicos para estas 6 tabelas (ver `SPECS`).

Fora do escopo por decisao (sem statement):
- `gyms`: o guard removia `gyms.color_palette` (coluna de uma feature
  descartada). A coluna ja nao existe na Aurora nem em dev. Uma migracao nao
  remove dado sem decisao explicita: se ela reaparecer (ex.: dump antigo), fica
  onde esta - nenhum codigo le/escreve a coluna.
- `exercise_mapping_catalog`: o guard ja era no-op (sem SQL).

Fluxo: `plan(db)` so LE (`information_schema` + contagens) e devolve a lista
de statements; `up(db)` executa o plano. Toda incompatibilidade, de QUALQUER
tabela, e detectada no plano, antes do primeiro statement - nada e aplicado:

- tabela ausente -> um unico `CREATE TABLE` ja no formato final;
- tabela completa (esperado na Aurora e em dev) -> 0 statements;
- tabela de uma versao anterior do guard (sem `user_id`, sem
  `readiness_score`/`daily_effort_load`, `user_id` NULL, chave unica/PK antiga
  so por data/external_id, indice faltando) -> so os ALTERs que faltam, com o
  mesmo backfill de `user_id` para o primeiro admin ativo que o guard fazia;
- coluna com tipo/nulabilidade/default diferente, coluna esperada ausente,
  `id` sem PK auto_increment, ENGINE != InnoDB, nome de indice esperado em
  outras colunas, PK diferente da esperada/legada, `user_id` NULL sem admin
  ativo, ou linhas que colidiriam na nova chave unica ->
  `IntegracoesMetricasSchemaError`.

Indices sao reconhecidos pelas colunas (qualquer nome). Colunas extras nao
bloqueiam. Data API: sem PREPARE/SET @/multi-statement; um statement por
chamada. Em dry-run o runner so lista o plano. Reexecutavel.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

CT = "CURRENT_TIMESTAMP"
ON_UPDATE = "on update current_timestamp"

# NamedTuple (e nao dataclass): o runner carrega a migracao por caminho de
# arquivo, sem registrar o modulo em sys.modules - dataclass com anotacoes
# adiadas falha nesse cenario.


class Column(NamedTuple):
    type: str
    nullable: str
    default: Optional[str] = None  # None = nao confere
    extra: Optional[str] = None  # trecho obrigatorio em EXTRA (minusculo)
    add_sql: Optional[str] = None  # coluna que versoes antigas do guard adicionavam depois


class Key(NamedTuple):
    name: str
    columns: tuple
    unique: bool = False
    primary: bool = False


class TableSpec(NamedTuple):
    name: str
    create_sql: str
    columns: dict
    keys: tuple
    # chaves unicas/PK de versoes antigas do guard, trocadas pela composta
    legacy_keys: tuple = ()
    has_user_backfill: bool = True


def _user_id(after: str) -> Column:
    return Column("bigint", "NO", add_sql=f"ADD COLUMN user_id BIGINT NULL AFTER {after}")


DEVICES = TableSpec(
    name="devices",
    create_sql="""
CREATE TABLE IF NOT EXISTS devices (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  device_name VARCHAR(160) NOT NULL,
  platform VARCHAR(80) NOT NULL DEFAULT 'zepp',
  token_hash CHAR(64) NOT NULL,
  paired_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP NULL,
  revoked_at TIMESTAMP NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  UNIQUE KEY uq_devices_token_hash (token_hash),
  KEY idx_devices_active (is_active)
) ENGINE=InnoDB
""",
    columns={
        "id": Column("bigint", "NO"),
        "user_id": _user_id("id"),
        "device_name": Column("varchar(160)", "NO"),
        "platform": Column("varchar(80)", "NO", "zepp"),
        "token_hash": Column("char(64)", "NO"),
        "paired_at": Column("timestamp", "NO", CT),
        "last_seen_at": Column("timestamp", "YES"),
        "revoked_at": Column("timestamp", "YES"),
        "is_active": Column("tinyint(1)", "NO", "1"),
    },
    keys=(
        Key("uq_devices_token_hash", ("token_hash",), unique=True),
        Key("idx_devices_active", ("is_active",)),
    ),
)

SLEEP_METRICS = TableSpec(
    name="sleep_metrics",
    create_sql="""
CREATE TABLE IF NOT EXISTS sleep_metrics (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  local_date DATE NOT NULL,
  duration_hours DECIMAL(4,2) NULL,
  regularity_percent DECIMAL(5,2) NULL,
  deep_sleep_hours DECIMAL(4,2) NULL,
  rem_sleep_hours DECIMAL(4,2) NULL,
  awake_count INT NULL,
  awake_hours DECIMAL(4,2) NULL,
  notes TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_sleep_metrics_user_date (user_id, local_date)
) ENGINE=InnoDB
""",
    columns={
        "id": Column("bigint", "NO"),
        "user_id": _user_id("id"),
        "local_date": Column("date", "NO"),
        "duration_hours": Column("decimal(4,2)", "YES"),
        "regularity_percent": Column("decimal(5,2)", "YES"),
        "deep_sleep_hours": Column("decimal(4,2)", "YES"),
        "rem_sleep_hours": Column("decimal(4,2)", "YES"),
        "awake_count": Column("int", "YES"),
        "awake_hours": Column("decimal(4,2)", "YES"),
        "notes": Column("text", "YES"),
        "created_at": Column("timestamp", "NO", CT),
        "updated_at": Column("timestamp", "NO", CT, ON_UPDATE),
    },
    keys=(Key("uq_sleep_metrics_user_date", ("user_id", "local_date"), unique=True),),
    legacy_keys=(Key("local_date", ("local_date",), unique=True),),
)

MANUAL_EFFORT_METRICS = TableSpec(
    name="manual_effort_metrics",
    create_sql="""
CREATE TABLE IF NOT EXISTS manual_effort_metrics (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  local_date DATE NOT NULL,
  epoc_training_load INT NULL,
  pai_score INT NULL,
  notes TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_manual_effort_metrics_user_date (user_id, local_date)
) ENGINE=InnoDB
""",
    columns={
        "id": Column("bigint", "NO"),
        "user_id": _user_id("id"),
        "local_date": Column("date", "NO"),
        "epoc_training_load": Column("int", "YES"),
        "pai_score": Column("int", "YES"),
        "notes": Column("text", "YES"),
        "created_at": Column("timestamp", "NO", CT),
        "updated_at": Column("timestamp", "NO", CT, ON_UPDATE),
    },
    keys=(Key("uq_manual_effort_metrics_user_date", ("user_id", "local_date"), unique=True),),
    legacy_keys=(Key("local_date", ("local_date",), unique=True),),
)

INTERVALS_ACTIVITIES = TableSpec(
    name="intervals_activities",
    create_sql="""
CREATE TABLE IF NOT EXISTS intervals_activities (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  external_id VARCHAR(191) NOT NULL,
  activity_date DATE NOT NULL,
  start_time DATETIME NULL,
  name VARCHAR(255) NULL,
  sport_type VARCHAR(80) NULL,
  moving_time_seconds INT NULL,
  elapsed_time_seconds INT NULL,
  distance_meters DECIMAL(12,2) NULL,
  calories_kcal DECIMAL(10,2) NULL,
  avg_hr_bpm DECIMAL(6,2) NULL,
  max_hr_bpm DECIMAL(6,2) NULL,
  training_load DECIMAL(10,2) NULL,
  intensity DECIMAL(10,2) NULL,
  raw_json JSON NULL,
  synced_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_intervals_activities_user_external (user_id, external_id),
  KEY idx_intervals_activities_date (activity_date),
  KEY idx_intervals_activities_sport_type (sport_type)
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
""",
    columns={
        "id": Column("bigint", "NO"),
        "user_id": _user_id("id"),
        "external_id": Column("varchar(191)", "NO"),
        "activity_date": Column("date", "NO"),
        "start_time": Column("datetime", "YES"),
        "name": Column("varchar(255)", "YES"),
        "sport_type": Column("varchar(80)", "YES"),
        "moving_time_seconds": Column("int", "YES"),
        "elapsed_time_seconds": Column("int", "YES"),
        "distance_meters": Column("decimal(12,2)", "YES"),
        "calories_kcal": Column("decimal(10,2)", "YES"),
        "avg_hr_bpm": Column("decimal(6,2)", "YES"),
        "max_hr_bpm": Column("decimal(6,2)", "YES"),
        "training_load": Column("decimal(10,2)", "YES"),
        "intensity": Column("decimal(10,2)", "YES"),
        "raw_json": Column("json", "YES"),
        "synced_at": Column("timestamp", "NO", CT, ON_UPDATE),
    },
    keys=(
        Key("uq_intervals_activities_user_external", ("user_id", "external_id"), unique=True),
        Key("idx_intervals_activities_date", ("activity_date",)),
        Key("idx_intervals_activities_sport_type", ("sport_type",)),
    ),
    legacy_keys=(Key("uq_intervals_activities_external_id", ("external_id",), unique=True),),
)

INTERVALS_WELLNESS = TableSpec(
    name="intervals_wellness",
    create_sql="""
CREATE TABLE IF NOT EXISTS intervals_wellness (
  wellness_date DATE NOT NULL,
  user_id BIGINT NOT NULL,
  fitness_ctl DECIMAL(10,2) NULL,
  fatigue_atl DECIMAL(10,2) NULL,
  form_tsb DECIMAL(10,2) NULL,
  ramp_rate DECIMAL(10,2) NULL,
  weight_kg DECIMAL(10,2) NULL,
  resting_hr_bpm DECIMAL(6,2) NULL,
  hrv_ms DECIMAL(10,2) NULL,
  sleep_seconds INT NULL,
  raw_json JSON NULL,
  synced_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  readiness_score DECIMAL(6,2) NULL,
  daily_effort_load INT NULL,
  PRIMARY KEY (user_id, wellness_date)
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
""",
    columns={
        "wellness_date": Column("date", "NO"),
        "user_id": _user_id("wellness_date"),
        "fitness_ctl": Column("decimal(10,2)", "YES"),
        "fatigue_atl": Column("decimal(10,2)", "YES"),
        "form_tsb": Column("decimal(10,2)", "YES"),
        "ramp_rate": Column("decimal(10,2)", "YES"),
        "weight_kg": Column("decimal(10,2)", "YES"),
        "resting_hr_bpm": Column("decimal(6,2)", "YES"),
        "hrv_ms": Column("decimal(10,2)", "YES"),
        "sleep_seconds": Column("int", "YES"),
        "raw_json": Column("json", "YES"),
        "synced_at": Column("timestamp", "NO", CT, ON_UPDATE),
        "readiness_score": Column("decimal(6,2)", "YES", add_sql="ADD COLUMN readiness_score DECIMAL(6,2) NULL"),
        "daily_effort_load": Column("int", "YES", add_sql="ADD COLUMN daily_effort_load INT NULL"),
    },
    keys=(Key("PRIMARY", ("user_id", "wellness_date"), unique=True, primary=True),),
    legacy_keys=(Key("PRIMARY", ("wellness_date",), unique=True, primary=True),),
)

REPORTS = TableSpec(
    name="reports",
    create_sql="""
CREATE TABLE IF NOT EXISTS reports (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  status ENUM('generating','done','failed') NOT NULL DEFAULT 'generating',
  period_days INT NOT NULL DEFAULT 90,
  requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP NULL,
  model VARCHAR(120) NULL,
  pdf_path VARCHAR(255) NULL,
  error_message TEXT NULL,
  context_snapshot JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_reports_user (user_id),
  KEY idx_reports_status (status)
) ENGINE=InnoDB
""",
    columns={
        "id": Column("bigint", "NO"),
        "user_id": Column("bigint", "NO"),
        "status": Column("enum('generating','done','failed')", "NO", "generating"),
        "period_days": Column("int", "NO", "90"),
        "requested_at": Column("timestamp", "NO", CT),
        "completed_at": Column("timestamp", "YES"),
        "model": Column("varchar(120)", "YES"),
        "pdf_path": Column("varchar(255)", "YES"),
        "error_message": Column("text", "YES"),
        "context_snapshot": Column("json", "YES"),
        "created_at": Column("timestamp", "NO", CT),
    },
    keys=(Key("idx_reports_user", ("user_id",)), Key("idx_reports_status", ("status",))),
    has_user_backfill=False,
)

SPECS: tuple[TableSpec, ...] = (
    DEVICES,
    SLEEP_METRICS,
    MANUAL_EFFORT_METRICS,
    INTERVALS_ACTIVITIES,
    INTERVALS_WELLNESS,
    REPORTS,
)

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(smallint|mediumint|int|bigint)\(\d+\)")


class IntegracoesMetricasSchemaError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise IntegracoesMetricasSchemaError(f"027: {message}. Nenhum statement executado.")


def _normalize_type(column_type: str) -> str:
    # Aurora MySQL 2 (5.7) reporta `bigint(20)`/`int(11)`; 8.0, `bigint`/`int`.
    # `tinyint(1)` e reportado igual nas duas versoes.
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", str(column_type).strip().lower())


def _normalize_default(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip("'").upper()
    return CT if text in (CT, "CURRENT_TIMESTAMP()", "NOW()") else text


def _scalar(db, sql, params=None) -> int:
    row = db.query_one(sql, params)
    return int(next(iter(row.values())) or 0) if row else 0


def _columns(db, table: str) -> dict[str, dict]:
    rows = db.query(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return {row["COLUMN_NAME"]: row for row in rows}


def _indexes(db, table: str) -> dict[str, tuple[tuple[str, ...], bool]]:
    """INDEX_NAME -> (colunas na ordem do indice, unico)."""
    rows = db.query(
        "SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s ORDER BY INDEX_NAME, SEQ_IN_INDEX",
        (table,),
    )
    columns: dict[str, list[str]] = {}
    unique: dict[str, bool] = {}
    for row in rows:
        columns.setdefault(row["INDEX_NAME"], []).append(row["COLUMN_NAME"])
        unique[row["INDEX_NAME"]] = int(row["NON_UNIQUE"]) == 0
    return {name: (tuple(cols), unique[name]) for name, cols in columns.items()}


def _engine(db, table: str) -> str:
    row = db.query_one(
        "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return str((row or {}).get("ENGINE") or "")


def _check_column(table: str, name: str, spec: Column, info: dict, *, allow_nullable: bool = False) -> None:
    actual_type = _normalize_type(info["COLUMN_TYPE"])
    if actual_type != _normalize_type(spec.type):
        _fail(f"{table}.{name} e {info['COLUMN_TYPE']}, esperado {spec.type}")
    nullable = info["IS_NULLABLE"]
    # user_id NULL e o estado de uma versao antiga do guard (backfill + NOT NULL no plano).
    if nullable != spec.nullable and not (allow_nullable and nullable == "YES"):
        _fail(f"{table}.{name} tem IS_NULLABLE={nullable}, esperado {spec.nullable}")
    if spec.default is not None and _normalize_default(info["COLUMN_DEFAULT"]) != _normalize_default(spec.default):
        _fail(f"{table}.{name} tem DEFAULT {info['COLUMN_DEFAULT']!r}, esperado {spec.default!r}")
    if spec.extra is not None and spec.extra not in str(info["EXTRA"] or "").lower():
        _fail(f"{table}.{name} tem EXTRA {info['EXTRA']!r}, esperado {spec.extra!r}")


class _Admin:
    """Primeiro admin ativo, consultado so se algum backfill for necessario."""

    def __init__(self, db):
        self._db = db
        self._loaded = False
        self._id: int | None = None

    def id(self) -> int | None:
        if not self._loaded:
            row = self._db.query_one("SELECT id FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id ASC LIMIT 1")
            self._id = int(row["id"]) if row else None
            self._loaded = True
        return self._id


def _find_key(indexes, key: Key) -> str | None:
    """Nome do indice existente que ja satisfaz `key` (pelas colunas)."""
    if key.primary:
        found = indexes.get("PRIMARY")
        return "PRIMARY" if found and found[0] == key.columns else None
    for name, (cols, unique) in indexes.items():
        if name == "PRIMARY":
            continue
        if key.unique and unique and cols == key.columns:
            return name
        if not key.unique and cols[: len(key.columns)] == key.columns:
            return name
    return None


def _plan_table(db, spec: TableSpec, admin: _Admin) -> list[tuple[str, tuple | None]]:
    table = spec.name
    if not db.table_exists(table):
        return [(spec.create_sql, None)]

    engine = _engine(db, table)
    if engine.lower() != "innodb":
        _fail(f"{table} usa ENGINE={engine}, esperado InnoDB")

    columns = _columns(db, table)
    for name, col in spec.columns.items():
        if name not in columns:
            if col.add_sql is None:
                _fail(f"{table}.{name} nao existe")
            continue
        _check_column(
            table, name, col, columns[name], allow_nullable=name == "user_id" and spec.has_user_backfill
        )
    if "id" in spec.columns and (
        columns["id"]["COLUMN_KEY"] != "PRI" or "auto_increment" not in str(columns["id"]["EXTRA"]).lower()
    ):
        _fail(f"{table}.id precisa ser PRIMARY KEY AUTO_INCREMENT")

    indexes = _indexes(db, table)
    missing_keys = [key for key in spec.keys if _find_key(indexes, key) is None]
    for key in missing_keys:
        if key.primary:
            pk = indexes.get("PRIMARY")
            if pk and not any(legacy.primary and pk[0] == legacy.columns for legacy in spec.legacy_keys):
                _fail(f"{table} tem PRIMARY KEY {list(pk[0])}, esperado {list(key.columns)}")
        elif key.name in indexes:
            _fail(f"indice {key.name} de {table} existe em {list(indexes[key.name][0])}, esperado {list(key.columns)}")

    statements: list[tuple[str, tuple | None]] = []
    for name, col in spec.columns.items():
        if name not in columns:
            statements.append((f"ALTER TABLE {table} {col.add_sql}", None))

    has_user_id = "user_id" in columns
    user_nullable = spec.has_user_backfill and (not has_user_id or columns["user_id"]["IS_NULLABLE"] == "YES")
    admin_id = None
    if user_nullable:
        where = "WHERE user_id IS NULL" if has_user_id else ""
        orphans = _scalar(db, f"SELECT COUNT(*) AS n FROM {table} {where}")
        if orphans:
            admin_id = admin.id()
            if admin_id is None:
                _fail(f"{orphans} linha(s) de {table} com user_id NULL e nenhum admin ativo para o backfill")

    # Linhas que colidiriam na chave composta nova (com o backfill aplicado).
    for key in missing_keys:
        if not key.unique or "user_id" not in key.columns:
            continue
        other = [c for c in key.columns if c != "user_id"]
        user_expr = "user_id" if has_user_id else "%s"
        params: tuple = ()
        if has_user_id and admin_id is not None:
            user_expr = "COALESCE(user_id, %s)"
            params = (admin_id,)
        elif not has_user_id:
            params = (admin_id or 0,)
        group_by = ", ".join(["u", *other])
        collisions = _scalar(
            db,
            f"SELECT COUNT(*) AS n FROM (SELECT {user_expr} AS u, {', '.join(other)} FROM {table} "
            f"GROUP BY {group_by} HAVING COUNT(*) > 1) d",
            params,
        )
        if collisions:
            _fail(f"{collisions} grupo(s) duplicado(s) em {table} {list(key.columns)}; {key.name} nao pode ser criada")

    if user_nullable:
        if admin_id is not None:
            statements.append((f"UPDATE {table} SET user_id = %s WHERE user_id IS NULL", (admin_id,)))
        statements.append((f"ALTER TABLE {table} MODIFY COLUMN user_id BIGINT NOT NULL", None))

    for key in missing_keys:
        column_list = ", ".join(key.columns)
        if key.primary:
            if "PRIMARY" in indexes:
                statements.append((f"ALTER TABLE {table} DROP PRIMARY KEY, ADD PRIMARY KEY ({column_list})", None))
            else:
                statements.append((f"ALTER TABLE {table} ADD PRIMARY KEY ({column_list})", None))
            continue
        if key.unique:
            for legacy in spec.legacy_keys:
                legacy_name = _find_key(indexes, legacy)
                if legacy_name and not legacy.primary:
                    statements.append((f"ALTER TABLE {table} DROP INDEX `{legacy_name}`", None))
            statements.append((f"ALTER TABLE {table} ADD UNIQUE KEY {key.name} ({column_list})", None))
        else:
            statements.append((f"ALTER TABLE {table} ADD INDEX {key.name} ({column_list})", None))
    return statements


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    if not db.table_exists("users"):
        _fail("tabela users nao existe (esperada pelo baseline 001_schema.sql)")
    admin = _Admin(db)
    statements: list[tuple[str, tuple | None]] = []
    for spec in SPECS:
        statements += _plan_table(db, spec, admin)
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)

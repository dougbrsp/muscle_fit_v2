"""085 - uso e cota de IA das features de nutrição (plano de nutrição, Lote L5a, 2026-10-18).

    ai_usage_log                     uma tentativa de uso de IA (texto da refeição, foto do rótulo)
      id                    BIGINT AUTO_INCREMENT PK
      user_id               BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      feature               VARCHAR(32)       `domain/nutrition/ai_usage.AiFeature` (meal_parse, label_photo)
      job_id                VARCHAR(64) NULL  job do DynamoDB (`async_jobs`), gravado depois do 202
      prompt_version        VARCHAR(40) NULL  `PROMPT_VERSION` da feature
      model                 VARCHAR(80) NULL  modelo pedido na reserva; o efetivo no fechamento
      status                VARCHAR(16)       `ai_usage.AiUsageStatus` (pending, succeeded, failed)
      error_code            VARCHAR(64) NULL  por que falhou (`worker_lost` do sweep, `OPENAI_*` do worker)
      tokens_in/tokens_out  INT NULL          da resposta da OpenAI (NULL = sem resposta)
      image_count           SMALLINT          imagens enviadas (0 no texto)
      cost_usd              DECIMAL(10,6) NULL `ai_usage.estimate_cost_usd`; NULL = modelo sem preço
      started_at            DATETIME          a reserva (relógio do banco); é o relógio da cota
      finished_at           DATETIME NULL     fechamento pelo worker ou pelo sweep
      updated_at
      KEY idx_aul_user_feature_started (user_id, feature, started_at)   cota do dia
      KEY idx_aul_status_started (status, started_at)                   sweep dos pendentes

--- Decisões do DDL -------------------------------------------------------

1. **Tabela genérica** (decisão E7 do plano): a cota é `COUNT(*)` por
   (usuário, feature, dia do usuário), qualquer status; as cotas antigas
   (periodização 10/dia, cards 100/dia) não migram agora.
2. **Códigos em VARCHAR**, vocabulário do motor (`ai_usage`); a migração não
   enumera nem dá default de status (a reserva sempre grava `pending`).
3. **Desvios do rascunho do §6.2** (o plano registra): `error_code` (o motivo
   da falha, que o rascunho não tinha onde guardar) e `updated_at`;
   `started_at` é o `created_at` da regra do §6.1 (o instante da reserva), sem
   coluna repetida. `cost_usd` com 6 casas: uma leitura custa frações de
   centavo (`ai_usage.COST_USD_DECIMAL_PLACES`).
4. **FK só para `users`** (ON DELETE CASCADE); `job_id` é referência lógica
   ao DynamoDB. Nenhuma FK para `exercise_catalog` (a 028 recusa). A FK usa o
   prefixo `user_id` do índice da cota (sem índice a mais).

--- Fluxo -----------------------------------------------------------------

Mesmo de 083: tabela ausente -> 1 `CREATE TABLE`; convergido -> 0; forma
divergente, ENGINE != InnoDB ou `users` incompatível -> `AiUsageLogError`
ANTES do primeiro statement; `users` ausente só passa em DRY-RUN. `down` =
DROP TABLE (nada referencia a tabela).

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; nenhuma
literal com ':' (viraria parâmetro no shim); módulo autocontido (não importa
código do app).
"""

from __future__ import annotations

import re

TABLE = "ai_usage_log"
USERS_TABLE = "users"

IDX_USER_FEATURE_STARTED = "idx_aul_user_feature_started"
IDX_STATUS_STARTED = "idx_aul_status_started"
FK_USER = "fk_aul_user"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  feature VARCHAR(32) NOT NULL,
  job_id VARCHAR(64) NULL,
  prompt_version VARCHAR(40) NULL,
  model VARCHAR(80) NULL,
  status VARCHAR(16) NOT NULL,
  error_code VARCHAR(64) NULL,
  tokens_in INT NULL,
  tokens_out INT NULL,
  image_count SMALLINT NOT NULL DEFAULT 0,
  cost_usd DECIMAL(10,6) NULL,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY {IDX_USER_FEATURE_STARTED} (user_id, feature, started_at),
  KEY {IDX_STATUS_STARTED} (status, started_at),
  CONSTRAINT {FK_USER} FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "feature": ("varchar(32)", "NO"),
    "job_id": ("varchar(64)", "YES"),
    "prompt_version": ("varchar(40)", "YES"),
    "model": ("varchar(80)", "YES"),
    "status": ("varchar(16)", "NO"),
    "error_code": ("varchar(64)", "YES"),
    "tokens_in": ("int", "YES"),
    "tokens_out": ("int", "YES"),
    "image_count": ("smallint", "NO"),
    "cost_usd": ("decimal(10,6)", "YES"),
    "started_at": ("datetime", "NO"),
    "finished_at": ("datetime", "YES"),
    "updated_at": ("datetime", "NO"),
}

# índice -> (UNIQUE?, colunas)
EXPECTED_INDEXES: dict[str, tuple[bool, tuple[str, ...]]] = {
    IDX_USER_FEATURE_STARTED: (False, ("user_id", "feature", "started_at")),
    IDX_STATUS_STARTED: (False, ("status", "started_at")),
}

# `int(11)`/`smallint(6)` -> sem largura; `tinyint(1)` (booleano) fica como está.
_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class AiUsageLogError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise AiUsageLogError(f"085: {message}. Nenhum statement executado.")


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
    """Inverso de `plan`: 1 `DROP TABLE` se a tabela existe, senão nada."""
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

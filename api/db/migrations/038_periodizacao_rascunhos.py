"""038 - auditoria do Motor de Periodizacao (PR-16; PLANO_20260912.md secoes
7.3 e 8.2, la numerada "029" - o numero real e o proximo livre, ja que
027..029 foram consumidas por integracoes/metricas, paridade do catalogo e
aliases).

    template_generation_logs                      auditoria de cada geracao
      id BIGINT PK AUTO_INCREMENT
      user_id BIGINT NOT NULL            -> users(id) ON DELETE CASCADE
      request_json JSON NULL             payload do POST (inclui as instrucoes
                                         livres do usuario - ficam SO aqui, nunca
                                         no log estruturado, secao 14)
      rag_sources JSON NULL              pastas de TIPOS_TREINO escolhidas
      model VARCHAR(80) NULL             modelo OpenAI efetivamente usado
      prompt_version VARCHAR(40) NULL    versao do prompt/schema
      status VARCHAR(20) NOT NULL DEFAULT 'pending'   pending|succeeded|failed
      validation_report JSON NULL        errors/warnings/auto_fixes do validador
      tokens_in INT NULL, tokens_out INT NULL, cost_usd DECIMAL(8,4) NULL
      saved_template_id BIGINT NULL      -> workout_plan_templates(id) ON DELETE SET NULL
                                         preenchido SO se o usuario salvar o
                                         rascunho depois (PR-17); NULL = rascunho
                                         gerado e nao salvo, que e o normal
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      KEY (user_id, created_at)          usado pelo limite de 10 geracoes/dia

    workout_plan_templates + colunas
      origin ENUM('manual','periodization') NOT NULL DEFAULT 'manual'
          UNICA coluna NOT NULL do lote: o default 'manual' vale para todas as
          linhas existentes e mantem exatamente o significado de hoje (todo
          modelo foi feito a mao), entao nenhuma linha muda de comportamento.
      generation_log_id BIGINT NULL
          log que originou o modelo, com indice proprio `idx_wpt_generation_log`.

`status` e VARCHAR(20), nao ENUM, pela mesma razao de `volume_mode`/
`template_type` (varchar na Aurora): valor novo de status nao exige DDL.
`origin` e ENUM porque a secao 8.2 pede ENUM explicitamente e o dominio e
fechado (os dois unicos jeitos de um modelo nascer).

Tipos conferidos no recorte real da Aurora (somente leitura, 2026-09-19,
tests/unit/data/aurora_schema_2026_09_19_038.json): `users.id` e
`workout_plan_templates.id` sao BIGINT **signed** - a armadilha do catalogo
(`exercise_catalog.id` BIGINT UNSIGNED) nao se aplica a nenhuma das duas.

`workout_plan_templates.generation_log_id` tem FK **LOGICA** (indice proprio,
sem constraint fisica), pela mesma razao da 037: `template_generation_logs`
.saved_template_id ja aponta para `workout_plan_templates`, e uma FK fisica no
sentido inverso fecharia um CICLO entre as duas tabelas - que o MySQL aceita
mas que complica DELETE, dump/restore e a limpeza de logs antigos. A secao 8.2
tambem so pede as colunas, sem FK.

Fluxo: `plan(db)` so LE `information_schema` e devolve os statements;
`up(db)` executa o plano.

- tabela ausente -> 1 `CREATE TABLE`;
- coluna/indice ausente -> um unico `ALTER TABLE ... ADD ...` com o que falta;
- tudo presente no formato esperado -> nada (estado convergido = 0 statements);
- tabela referenciada ausente/de outro tipo, coluna presente com tipo/
  nulabilidade/default diferente, indice presente em outras colunas, ENGINE !=
  InnoDB -> `PeriodizacaoRascunhosError` ANTES do primeiro statement.

Aurora hoje (versao 37): `template_generation_logs` nao existe e nenhuma das 2
colunas novas existe -> 2 statements. Reexecucao: 0.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app nem usa classes de dados).
"""

from __future__ import annotations

import re

LOGS_TABLE = "template_generation_logs"
TEMPLATES_TABLE = "workout_plan_templates"

GENERATION_LOG_INDEX = "idx_wpt_generation_log"
LOGS_USER_INDEX = "idx_tgl_user_created"
LOGS_TEMPLATE_INDEX = "idx_tgl_saved_template"
COLLATION = "utf8mb4_0900_ai_ci"

CREATE_LOGS_SQL = f"""
CREATE TABLE IF NOT EXISTS {LOGS_TABLE} (
  id BIGINT NOT NULL AUTO_INCREMENT,
  user_id BIGINT NOT NULL,
  request_json JSON NULL,
  rag_sources JSON NULL,
  model VARCHAR(80) NULL,
  prompt_version VARCHAR(40) NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  validation_report JSON NULL,
  tokens_in INT NULL,
  tokens_out INT NULL,
  cost_usd DECIMAL(8,4) NULL,
  saved_template_id BIGINT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY {LOGS_USER_INDEX} (user_id, created_at),
  KEY {LOGS_TEMPLATE_INDEX} (saved_template_id),
  CONSTRAINT fk_tgl_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_tgl_saved_template FOREIGN KEY (saved_template_id) REFERENCES {TEMPLATES_TABLE}(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE={COLLATION}
"""

# coluna nova de workout_plan_templates -> (DDL do ADD COLUMN, COLUMN_TYPE
# normalizado, IS_NULLABLE, COLUMN_DEFAULT esperado)
TEMPLATE_COLUMNS = (
    (
        "origin",
        "ENUM('manual','periodization') NOT NULL DEFAULT 'manual'",
        "enum('manual','periodization')",
        "NO",
        "manual",
    ),
    ("generation_log_id", "BIGINT NULL", "bigint", "YES", None),
)

# colunas esperadas de template_generation_logs quando a tabela ja existe:
# coluna -> (COLUMN_TYPE normalizado, IS_NULLABLE)
LOGS_COLUMNS = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "request_json": ("json", "YES"),
    "rag_sources": ("json", "YES"),
    "model": ("varchar(80)", "YES"),
    "prompt_version": ("varchar(40)", "YES"),
    "status": ("varchar(20)", "NO"),
    "validation_report": ("json", "YES"),
    "tokens_in": ("int", "YES"),
    "tokens_out": ("int", "YES"),
    "cost_usd": ("decimal(8,4)", "YES"),
    "saved_template_id": ("bigint", "YES"),
    "created_at": ("timestamp", "NO"),
}

# FKs esperadas de template_generation_logs: coluna -> tabela referenciada
LOGS_FKS = {"user_id": "users", "saved_template_id": TEMPLATES_TABLE}

# (tabela, coluna) referenciada -> COLUMN_TYPE esperado. Ambas BIGINT SIGNED
# na Aurora (conferido no recorte do teste).
REFERENCED = {("users", "id"): "bigint", (TEMPLATES_TABLE, "id"): "bigint"}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class PeriodizacaoRascunhosError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise PeriodizacaoRascunhosError(f"038: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    """`int(11)` -> `int` (largura de exibicao legada) e ENUM sem espacos."""
    text = _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())
    return text.replace(", ", ",") if text.startswith("enum(") else text


def _normalize_default(value) -> str | None:
    if value is None:
        return None
    return _text(value).strip().strip("'").lower()


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    names = tuple(sorted({LOGS_TABLE, TEMPLATES_TABLE, *(table for table, _ in REFERENCED)}))
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
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    indexes: dict[tuple[str, str], list[str]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
        f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders}) "
        "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
        names,
    ):
        indexes.setdefault((_text(row["TABLE_NAME"]), _text(row["INDEX_NAME"])), []).append(_text(row["COLUMN_NAME"]))

    for (table, column), expected_type in REFERENCED.items():
        if table not in engines:
            _fail(f"tabela referenciada {table} nao existe")
        if engines[table].lower() != "innodb":
            _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
        info = columns.get((table, column))
        if info is None:
            _fail(f"{table}.{column} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{table}.{column} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["COLUMN_KEY"]) != "PRI":
            _fail(f"{table}.{column} nao e PRIMARY KEY")

    statements: list[tuple[str, tuple | None]] = []
    if LOGS_TABLE in engines:
        _check_existing_logs(db, engines, columns, indexes)
    else:
        statements.append((CREATE_LOGS_SQL, None))

    missing: list[str] = []
    for column, ddl, expected_type, expected_nullable, expected_default in TEMPLATE_COLUMNS:
        info = columns.get((TEMPLATES_TABLE, column))
        if info is None:
            missing.append(f"ADD COLUMN {column} {ddl}")
            continue
        label = f"{TEMPLATES_TABLE}.{column}"
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{label} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{label} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
        if _normalize_default(info["COLUMN_DEFAULT"]) != _normalize_default(expected_default):
            _fail(f"{label} tem DEFAULT {_text(info['COLUMN_DEFAULT'])!r}, esperado {expected_default!r}")

    found = indexes.get((TEMPLATES_TABLE, GENERATION_LOG_INDEX))
    if found is None:
        missing.append(f"ADD KEY {GENERATION_LOG_INDEX} (generation_log_id)")
    elif found != ["generation_log_id"]:
        _fail(f"indice {GENERATION_LOG_INDEX} de {TEMPLATES_TABLE} e {found}, esperado ['generation_log_id']")

    if missing:
        statements.append((f"ALTER TABLE {TEMPLATES_TABLE} " + ", ".join(missing), None))
    return statements


def _check_existing_logs(db, engines, columns, indexes) -> None:
    """`template_generation_logs` ja existe: precisa estar no formato desta
    migracao, senao a execucao para antes de qualquer statement."""
    if engines[LOGS_TABLE].lower() != "innodb":
        _fail(f"{LOGS_TABLE} usa ENGINE={engines[LOGS_TABLE]}, esperado InnoDB")
    for column, (expected_type, expected_nullable) in LOGS_COLUMNS.items():
        info = columns.get((LOGS_TABLE, column))
        if info is None:
            _fail(f"{LOGS_TABLE}.{column} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{LOGS_TABLE}.{column} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{LOGS_TABLE}.{column} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    for index, expected_columns in (
        ("PRIMARY", ["id"]),
        (LOGS_USER_INDEX, ["user_id", "created_at"]),
        (LOGS_TEMPLATE_INDEX, ["saved_template_id"]),
    ):
        found = indexes.get((LOGS_TABLE, index))
        if found != expected_columns:
            _fail(f"indice {index} de {LOGS_TABLE} e {found}, esperado {expected_columns}")
    fks = {
        _text(row["COLUMN_NAME"]): _text(row["REFERENCED_TABLE_NAME"])
        for row in db.query(
            "SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE k "
            "WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME IN (%s) AND k.REFERENCED_TABLE_NAME IS NOT NULL",
            (LOGS_TABLE,),
        )
        if _text(row["TABLE_NAME"]) == LOGS_TABLE
    }
    if fks != LOGS_FKS:
        _fail(f"FKs de {LOGS_TABLE} sao {fks}, esperado {LOGS_FKS}")


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)

"""064 - Cards ilustrados de exercicios: historico de geracoes + ponteiro da
versao publicada no catalogo.

    exercise_card_generations                      (tabela nova)
      id                    BIGINT AUTO_INCREMENT PK
      exercise_id           BIGINT UNSIGNED NOT NULL  ref. LOGICA a
                                                      exercise_catalog(id), SEM FK
      card_type             VARCHAR(40)  'exercise_execution' (secao 46 da spec)
      status                VARCHAR(16)  PENDING|GENERATING|GENERATED|APPROVING|
                                         PUBLISHED|SUPERSEDED|REJECTED|FAILED
      version               INT NULL     so na publicacao (v1, v2, ...)
      parent_generation_id  BIGINT NULL  geracao que esta regenerou
      idempotency_key       CHAR(36)     UUID do cliente (replay do POST)
      job_id                VARCHAR(36)  job do DynamoDB (diagnostico)
      provider/model/quality/size        o que foi pedido a OpenAI
      prompt_version/text_level/prompt_text/request_instruction/reject_reason
      preview_s3_key / card_s3_key / card_png_s3_key / thumb_s3_key
      width/height/bytes/tokens_in/tokens_out/cost_usd
      error_code/error_message
      created_by/approved_by             ids de `users`, SEM FK
      created_at/updated_at/started_at/generated_at/approved_at/
      published_at/rejected_at/failed_at
      KEY        idx_ecg_exercise_status   (exercise_id, status)
      UNIQUE KEY uq_ecg_exercise_version   (exercise_id, version)
      UNIQUE KEY uq_ecg_exercise_idem      (exercise_id, idempotency_key)
      KEY        idx_ecg_created_by_created (created_by, created_at)
      KEY        idx_ecg_status_started    (status, started_at)

    exercise_catalog
      + card_generation_id  BIGINT NULL    geracao PUBLICADA vigente
      + card_version        INT NULL       versao vigente (monta a URL)
      + card_published_at   DATETIME NULL

--- Decisoes do DDL -------------------------------------------------------

1. **Uma tabela de historico + 3 colunas de ponteiro no catalogo** (e nao a
   2a tabela "imagens publicadas" da secao 25 da spec). A 2a tabela
   duplicaria `s3_key/version/width/height`, e um `is_active` sem indice
   parcial (MySQL nao tem) dependeria de disciplina transacional para nao
   ter dois ativos. O ponteiro no catalogo e UNICO por construcao, e o Modo
   Treino ja le `exercise_catalog` - ganha o card com 2 colunas a mais no
   SELECT, sem JOIN novo.

2. **Sem FOREIGN KEY.** `exercise_id` aponta para `exercise_catalog(id)`, e a
   028 ENUMERA as FKs validas para essa tabela e recusa qualquer FK nova
   (registrar a FK la tambem nao resolve: a 028 passaria a exigir uma
   tabela que nasce 36 migracoes adiante). O tipo e o certo
   (`BIGINT UNSIGNED`, conferido abaixo contra `exercise_catalog.id`), sem a
   constraint. `created_by`/`approved_by` tambem sem FK, por decisao: o card
   pertence ao CATALOGO, nao ao admin - apagar o usuario nao pode apagar
   nem bloquear o historico de um card publicado. `exercise_catalog.
   card_generation_id` idem (referencia logica a esta tabela).

3. **`status` VARCHAR, nao ENUM** (mesmo padrao da 038/054): estado novo da
   maquina nao pode exigir DDL. **Sem coluna gerada** para "no maximo 1
   geracao viva por exercicio": a checagem e do servico, na transacao; a
   corrida entre dois admins simultaneos e aceita e documentada no plano.

4. **`UNIQUE (exercise_id, version)`**: MySQL aceita varios NULL num indice
   UNIQUE, entao as geracoes nao publicadas (version NULL) convivem; duas
   publicacoes com o mesmo numero de versao sao recusadas pelo banco.
   **`UNIQUE (exercise_id, idempotency_key)`** e o que torna o replay do
   POST seguro (a 2a insercao falha e o servico devolve a existente).

5. **Colunas novas do catalogo: NULL, sem DEFAULT, sem `AFTER`.** NULL =
   sem card publicado. Coluna anulavel no fim da tabela e `ALGORITHM=INSTANT`
   por padrao no MySQL 8.0.29+ (Aurora 3.08 = MySQL 8.0.39): nada de
   rebuild nem de lock longo em `exercise_catalog`.

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso.

- tabela ausente -> 1 `CREATE TABLE`; coluna ausente -> 1 `ALTER TABLE ...
  ADD COLUMN` por coluna (um statement por chamada; reexecucao depois de
  falha no meio retoma de onde parou);
- tudo presente no formato esperado -> nada (reexecucao: 0 statements);
- `exercise_catalog.id` que nao e BIGINT UNSIGNED PK, `users.id` que nao e
  BIGINT PK, ENGINE != InnoDB, tabela presente com coluna/tipo/
  nulabilidade/indice diferente, ou coluna do catalogo em outro formato ->
  `ExerciseCardGenerationsError` ANTES do primeiro statement.

Aurora (sem a tabela e sem as colunas) -> 4 statements. Reexecucao: 0.
`down` -> 3 `DROP COLUMN` (das que existem) + `DROP TABLE` (reversivel: sem
as colunas e sem a tabela o codigo anterior nao tem o que ler).

Sem DML: a tabela nasce vazia e as colunas nascem NULL (nenhum card existe).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "exercise_card_generations"
CATALOG_TABLE = "exercise_catalog"
USERS_TABLE = "users"

STATUS_INDEX = "idx_ecg_exercise_status"
VERSION_INDEX = "uq_ecg_exercise_version"
IDEMPOTENCY_INDEX = "uq_ecg_exercise_idem"
CREATED_BY_INDEX = "idx_ecg_created_by_created"
STARTED_INDEX = "idx_ecg_status_started"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  exercise_id BIGINT UNSIGNED NOT NULL,
  card_type VARCHAR(40) NOT NULL DEFAULT 'exercise_execution',
  status VARCHAR(16) NOT NULL,
  version INT NULL,
  parent_generation_id BIGINT NULL,
  idempotency_key CHAR(36) NOT NULL,
  job_id VARCHAR(36) NULL,
  provider VARCHAR(20) NOT NULL DEFAULT 'openai',
  model VARCHAR(60) NULL,
  quality VARCHAR(10) NULL,
  size VARCHAR(12) NULL,
  prompt_version VARCHAR(40) NOT NULL,
  text_level VARCHAR(10) NOT NULL DEFAULT 'minimal',
  prompt_text TEXT NULL,
  request_instruction TEXT NULL,
  reject_reason TEXT NULL,
  preview_s3_key VARCHAR(255) NULL,
  card_s3_key VARCHAR(255) NULL,
  card_png_s3_key VARCHAR(255) NULL,
  thumb_s3_key VARCHAR(255) NULL,
  width INT NULL,
  height INT NULL,
  bytes INT NULL,
  tokens_in INT NULL,
  tokens_out INT NULL,
  cost_usd DECIMAL(8,4) NULL,
  error_code VARCHAR(40) NULL,
  error_message TEXT NULL,
  created_by BIGINT NOT NULL,
  approved_by BIGINT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at DATETIME NULL,
  generated_at DATETIME NULL,
  approved_at DATETIME NULL,
  published_at DATETIME NULL,
  rejected_at DATETIME NULL,
  failed_at DATETIME NULL,
  KEY {STATUS_INDEX} (exercise_id, status),
  UNIQUE KEY {VERSION_INDEX} (exercise_id, version),
  UNIQUE KEY {IDEMPOTENCY_INDEX} (exercise_id, idempotency_key),
  KEY {CREATED_BY_INDEX} (created_by, created_at),
  KEY {STARTED_INDEX} (status, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_TABLE_SQL = f"DROP TABLE IF EXISTS {TABLE}"

ADD_GENERATION_ID_SQL = f"ALTER TABLE {CATALOG_TABLE} ADD COLUMN card_generation_id BIGINT NULL"
ADD_VERSION_SQL = f"ALTER TABLE {CATALOG_TABLE} ADD COLUMN card_version INT NULL"
ADD_PUBLISHED_AT_SQL = f"ALTER TABLE {CATALOG_TABLE} ADD COLUMN card_published_at DATETIME NULL"
DROP_GENERATION_ID_SQL = f"ALTER TABLE {CATALOG_TABLE} DROP COLUMN card_generation_id"
DROP_VERSION_SQL = f"ALTER TABLE {CATALOG_TABLE} DROP COLUMN card_version"
DROP_PUBLISHED_AT_SQL = f"ALTER TABLE {CATALOG_TABLE} DROP COLUMN card_published_at"

CATALOG_COLUMNS = ("card_generation_id", "card_version", "card_published_at")
_ADD = {
    "card_generation_id": ADD_GENERATION_ID_SQL,
    "card_version": ADD_VERSION_SQL,
    "card_published_at": ADD_PUBLISHED_AT_SQL,
}
_DROP = {
    "card_generation_id": DROP_GENERATION_ID_SQL,
    "card_version": DROP_VERSION_SQL,
    "card_published_at": DROP_PUBLISHED_AT_SQL,
}
# coluna do catalogo -> tipo normalizado (todas IS_NULLABLE = YES)
CATALOG_EXPECTED_TYPES = {
    "card_generation_id": "bigint",
    "card_version": "int",
    "card_published_at": "datetime",
}

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "exercise_id": ("bigint unsigned", "NO"),
    "card_type": ("varchar(40)", "NO"),
    "status": ("varchar(16)", "NO"),
    "version": ("int", "YES"),
    "parent_generation_id": ("bigint", "YES"),
    "idempotency_key": ("char(36)", "NO"),
    "job_id": ("varchar(36)", "YES"),
    "provider": ("varchar(20)", "NO"),
    "model": ("varchar(60)", "YES"),
    "quality": ("varchar(10)", "YES"),
    "size": ("varchar(12)", "YES"),
    "prompt_version": ("varchar(40)", "NO"),
    "text_level": ("varchar(10)", "NO"),
    "prompt_text": ("text", "YES"),
    "request_instruction": ("text", "YES"),
    "reject_reason": ("text", "YES"),
    "preview_s3_key": ("varchar(255)", "YES"),
    "card_s3_key": ("varchar(255)", "YES"),
    "card_png_s3_key": ("varchar(255)", "YES"),
    "thumb_s3_key": ("varchar(255)", "YES"),
    "width": ("int", "YES"),
    "height": ("int", "YES"),
    "bytes": ("int", "YES"),
    "tokens_in": ("int", "YES"),
    "tokens_out": ("int", "YES"),
    "cost_usd": ("decimal(8,4)", "YES"),
    "error_code": ("varchar(40)", "YES"),
    "error_message": ("text", "YES"),
    "created_by": ("bigint", "NO"),
    "approved_by": ("bigint", "YES"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
    "started_at": ("datetime", "YES"),
    "generated_at": ("datetime", "YES"),
    "approved_at": ("datetime", "YES"),
    "published_at": ("datetime", "YES"),
    "rejected_at": ("datetime", "YES"),
    "failed_at": ("datetime", "YES"),
}

# indice -> (colunas na ordem, unico?)
EXPECTED_INDEXES: dict[str, tuple[tuple[str, ...], bool]] = {
    STATUS_INDEX: (("exercise_id", "status"), False),
    VERSION_INDEX: (("exercise_id", "version"), True),
    IDEMPOTENCY_INDEX: (("exercise_id", "idempotency_key"), True),
    CREATED_BY_INDEX: (("created_by", "created_at"), False),
    STARTED_INDEX: (("status", "started_at"), False),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class ExerciseCardGenerationsError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise ExerciseCardGenerationsError(f"064: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `bigint(20) unsigned` -> `bigint unsigned`; `int(11)` -> `int`
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db) -> tuple[dict[str, str], dict[tuple[str, str], dict], dict[str, list[dict]]]:
    names = (TABLE, CATALOG_TABLE, USERS_TABLE)
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
    if TABLE in engines:
        # A ordem das colunas de cada indice vem do ORDER BY (SEQ_IN_INDEX).
        for row in db.query(
            "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s) ORDER BY INDEX_NAME, SEQ_IN_INDEX",
            (TABLE,),
        ):
            if _text(row["TABLE_NAME"]) == TABLE:
                indexes.setdefault(_text(row["INDEX_NAME"]), []).append(row)
    return engines, columns, indexes


def _check_reference(
    engines: dict[str, str],
    columns: dict[tuple[str, str], dict],
    table: str,
    expected_type: str,
) -> None:
    """`table.id` tem de ser `expected_type` e PRIMARY KEY, numa tabela InnoDB:
    e o tipo que as colunas de referencia LOGICA desta migracao copiam."""
    if table not in engines:
        _fail(f"tabela {table} nao existe")
    if engines[table].lower() != "innodb":
        _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
    info = columns.get((table, "id"))
    if info is None:
        _fail(f"{table}.id nao existe")
    if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
        _fail(f"{table}.id e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
    if _text(info["COLUMN_KEY"]) != "PRI":
        _fail(f"{table}.id nao e PRIMARY KEY")


def _check_existing_table(
    engines: dict[str, str],
    columns: dict[tuple[str, str], dict],
    indexes: dict[str, list[dict]],
) -> None:
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
    for name, (expected_columns, unique) in EXPECTED_INDEXES.items():
        rows = indexes.get(name)
        if not rows:
            _fail(f"indice {TABLE}.{name} nao existe")
        found = tuple(_text(row["COLUMN_NAME"]) for row in rows)
        if found != expected_columns:
            _fail(f"indice {TABLE}.{name} cobre {found}, esperado {expected_columns}")
        if (int(_text(rows[0]["NON_UNIQUE"]) or 1) == 0) != unique:
            _fail(f"indice {TABLE}.{name} deveria ser {'UNIQUE' if unique else 'nao unico'}")


def _check_catalog_column(columns: dict[tuple[str, str], dict], name: str) -> bool:
    """True se a coluna ja existe no formato esperado; False se falta."""
    info = columns.get((CATALOG_TABLE, name))
    if info is None:
        return False
    if _normalize_type(info["COLUMN_TYPE"]) != CATALOG_EXPECTED_TYPES[name]:
        _fail(f"{CATALOG_TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {CATALOG_EXPECTED_TYPES[name]}")
    if _text(info["IS_NULLABLE"]) != "YES":
        _fail(f"{CATALOG_TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado YES")
    return True


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem.
    Toda validacao acontece ANTES de montar a lista: se algo esta
    incompativel, nenhum statement e devolvido."""
    engines, columns, indexes = _read(db)
    _check_reference(engines, columns, CATALOG_TABLE, "bigint unsigned")
    _check_reference(engines, columns, USERS_TABLE, "bigint")
    statements: list[tuple[str, tuple | None]] = []
    if TABLE in engines:
        _check_existing_table(engines, columns, indexes)
    else:
        statements.append((CREATE_SQL, None))
    for name in CATALOG_COLUMNS:
        if not _check_catalog_column(columns, name):
            statements.append((_ADD[name], None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: DROP COLUMN das colunas do catalogo que existem
    (ordem inversa) e DROP TABLE se a tabela existe."""
    engines, columns, _indexes = _read(db)
    statements: list[tuple[str, tuple | None]] = [
        (_DROP[name], None) for name in reversed(CATALOG_COLUMNS) if (CATALOG_TABLE, name) in columns
    ]
    if TABLE in engines:
        statements.append((DROP_TABLE_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

"""041 - associacao entre treino realizado e atividade WeightTraining do
Intervals.icu (melhoria M2: sync automatico + FC no treino).

UMA tabela nova, puramente aditiva (nenhuma tabela existente e tocada):

    activity_intervals_link   um treino de `activities` <-> uma atividade de
                              `intervals_activities`, casados pelo HORARIO
      user_id                 BIGINT NOT NULL -> users(id)                ON DELETE CASCADE
      activity_id             BIGINT NOT NULL -> activities(id)           ON DELETE CASCADE
      intervals_activity_id   BIGINT NOT NULL -> intervals_activities(id) ON DELETE CASCADE
      intervals_external_id   VARCHAR(191) NOT NULL  (id do Intervals, para auditoria/joins legados)
      match_status            ENUM('MATCHED','MATCHED_AMBIGUOUS')
      match_delta_seconds     INT NOT NULL   |activities.started_at - intervals.start_time|
      candidates_count        SMALLINT NOT NULL DEFAULT 1
      avg_hr_bpm / max_hr_bpm DECIMAL(6,2) NULL   FC media/maxima da atividade associada
      calories_kcal           DECIMAL(10,2) NULL
      training_load           DECIMAL(10,2) NULL
      moving_time_seconds     INT NULL
      matched_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      PRIMARY KEY (id)
      UNIQUE KEY uk_ail_activity (activity_id)          -- UM link por treino
      KEY idx_ail_user_matched (user_id, matched_at)
      KEY idx_ail_intervals (intervals_activity_id)

Por que `uk_ail_activity` e por que as metricas de FC sao DESNORMALIZADAS aqui
(e nao so o par de ids): o job de sync (`app/intervals_sync.py`) roda de novo o
tempo todo e precisa de um alvo unico por treino para o `ON DUPLICATE KEY
UPDATE` - e isso, sozinho, ja garante a idempotencia (rodar duas vezes escreve
os MESMOS valores, nunca uma segunda linha). As colunas de FC copiadas da
atividade sao o que as telas/relatorios leem sem refazer o casamento por
horario a cada consulta.

Sem linha para "nao casou": quando nenhuma atividade `WeightTraining` cai na
janela de tolerancia, NADA e gravado (e nada e apagado) - ver as regras de
produto em `app/intervals_hr_match.py`. Ausencia de linha = ainda sem FC
associada, e a proxima execucao tenta de novo.

Tipos referenciados conferidos: `users.id`, `activities.id` e
`intervals_activities.id` sao BIGINT signed, PRIMARY KEY, InnoDB
(`activities` vem do baseline 001; `intervals_activities`, da 027).

`intervals_activities` ausente (banco novo em que a 027 ainda esta pendente no
MESMO lote; o runner a aplica ANTES desta): numa execucao real a ausencia e
erro (falha segura). So em DRY-RUN (`db.dry_run`), em que a 027 pendente nao
chega a criar a tabela, a ausencia e aceita para o plano do lote inteiro poder
ser exibido - o formato e conferido de novo na execucao real. Mesma regra ja
usada pela 033 com `advanced_techniques`. Na Aurora a 027 ja foi aplicada.

Fluxo: `plan(db)` so LE `information_schema` e devolve os statements; `up(db)`
executa o plano. Tabela ausente -> `CREATE TABLE`; presente no formato
esperado -> 0 statements; presente em outro formato -> erro ANTES do primeiro
statement. Reexecutavel.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

LINK = "activity_intervals_link"

MATCH_STATUS_SQL = "'MATCHED','MATCHED_AMBIGUOUS'"

_TABLE_SUFFIX = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"

CREATE_LINK_SQL = f"""
CREATE TABLE IF NOT EXISTS {LINK} (
  id BIGINT NOT NULL AUTO_INCREMENT,
  user_id BIGINT NOT NULL,
  activity_id BIGINT NOT NULL,
  intervals_activity_id BIGINT NOT NULL,
  intervals_external_id VARCHAR(191) NOT NULL,
  match_status ENUM({MATCH_STATUS_SQL}) NOT NULL,
  match_delta_seconds INT NOT NULL,
  candidates_count SMALLINT NOT NULL DEFAULT 1,
  moving_time_seconds INT NULL,
  avg_hr_bpm DECIMAL(6,2) NULL,
  max_hr_bpm DECIMAL(6,2) NULL,
  calories_kcal DECIMAL(10,2) NULL,
  training_load DECIMAL(10,2) NULL,
  matched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ail_activity (activity_id),
  KEY idx_ail_user_matched (user_id, matched_at),
  KEY idx_ail_intervals (intervals_activity_id),
  CONSTRAINT fk_ail_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_ail_activity FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE CASCADE,
  CONSTRAINT fk_ail_intervals FOREIGN KEY (intervals_activity_id) REFERENCES intervals_activities(id) ON DELETE CASCADE
) {_TABLE_SUFFIX}
"""

# (tabela, coluna) referenciada -> COLUMN_TYPE esperado. Uma FK exige tipo
# IDENTICO (signed/unsigned inclusive) no MySQL.
REFERENCED = {
    ("users", "id"): "bigint",
    ("activities", "id"): "bigint",
    ("intervals_activities", "id"): "bigint",
}

EXPECTED_COLUMNS = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "activity_id": ("bigint", "NO"),
    "intervals_activity_id": ("bigint", "NO"),
    "intervals_external_id": ("varchar(191)", "NO"),
    "match_status": (f"enum({MATCH_STATUS_SQL})", "NO"),
    "match_delta_seconds": ("int", "NO"),
    "candidates_count": ("smallint", "NO"),
    "moving_time_seconds": ("int", "YES"),
    "avg_hr_bpm": ("decimal(6,2)", "YES"),
    "max_hr_bpm": ("decimal(6,2)", "YES"),
    "calories_kcal": ("decimal(10,2)", "YES"),
    "training_load": ("decimal(10,2)", "YES"),
    "matched_at": ("timestamp", "NO"),
}

# Indices adicionais no banco nao sao erro (o MySQL cria indices auxiliares de
# FK); os listados aqui precisam existir com exatamente estas colunas.
EXPECTED_INDEXES = {
    "PRIMARY": ["id"],
    "uk_ail_activity": ["activity_id"],
    "idx_ail_user_matched": ["user_id", "matched_at"],
    "idx_ail_intervals": ["intervals_activity_id"],
}

UNIQUE_INDEXES = {"PRIMARY", "uk_ail_activity"}

EXPECTED_FKS = {
    "user_id": "users",
    "activity_id": "activities",
    "intervals_activity_id": "intervals_activities",
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class IntervalsHrLinkError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise IntervalsHrLinkError(f"041: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    normalized = _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())
    if normalized.startswith("enum("):
        normalized = normalized.replace(", ", ",")
    return normalized


# Tabela referenciada que NAO vem do baseline 001..021: e criada pela 027, que
# pode estar pendente no mesmo lote. Ver docstring.
_CREATED_BY_PENDING_MIGRATION = "intervals_activities"


def _check_referenced(engines: dict, columns: dict, dry_run: bool = False) -> None:
    for (table, column), expected_type in REFERENCED.items():
        if table not in engines:
            if table == _CREATED_BY_PENDING_MIGRATION and dry_run:
                continue  # 027 pendente no mesmo lote: ver docstring
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


def _check_existing(engines: dict, columns: dict, indexes: dict, fks: dict) -> None:
    if engines[LINK].lower() != "innodb":
        _fail(f"{LINK} usa ENGINE={engines[LINK]}, esperado InnoDB")

    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS.items():
        info = columns.get((LINK, name))
        if info is None:
            _fail(f"{LINK}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != _normalize_type(expected_type):
            _fail(f"{LINK}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{LINK}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")

    for name, expected_columns in EXPECTED_INDEXES.items():
        found = indexes.get(name)
        if found is None or found["columns"] != expected_columns:
            _fail(f"indice {name} de {LINK} e {found['columns'] if found else None}, esperado {expected_columns}")
        if name in UNIQUE_INDEXES and found["non_unique"]:
            _fail(f"indice {name} de {LINK} nao e UNIQUE")

    if fks != EXPECTED_FKS:
        _fail(f"FKs de {LINK} sao {fks}, esperado {EXPECTED_FKS}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    names = tuple(sorted({LINK, *(table for table, _ in REFERENCED)}))
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
    _check_referenced(engines, columns, bool(getattr(db, "dry_run", False)))

    if LINK not in engines:
        return [(CREATE_LINK_SQL, None)]

    indexes: dict[str, dict] = {}
    for row in db.query(
        "SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s ORDER BY INDEX_NAME, SEQ_IN_INDEX",
        (LINK,),
    ):
        entry = indexes.setdefault(
            _text(row["INDEX_NAME"]), {"columns": [], "non_unique": int(row["NON_UNIQUE"] or 0)}
        )
        entry["columns"].append(_text(row["COLUMN_NAME"]))

    fks: dict[str, str] = {}
    for row in db.query(
        "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL",
        (LINK,),
    ):
        fks[_text(row["COLUMN_NAME"])] = _text(row["REFERENCED_TABLE_NAME"])

    _check_existing(engines, columns, indexes, fks)
    return []


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)

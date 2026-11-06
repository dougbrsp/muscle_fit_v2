"""107 - registro de execucoes do sync do Intervals.icu (2026-11-06, Lote F da nutricao adaptativa, E1).

Tabela NOVA `intervals_sync_runs`: uma linha por usuario por execucao do sync (job agendado e refresh manual), com a
JANELA realmente buscada e o status. E a prova que faltava para o "dia confirmado" do gasto adaptativo: com ela o motor
(`domain/nutrition/activity_energy`) sabe que uma execucao `ok` cobriu o dia e terminou depois do fim dele, em vez de
inferir pelo `MAX(synced_at)` das atividades (relogio parado deixava de ser distinguivel de descanso).

    id                BIGINT AUTO_INCREMENT PK
    user_id           BIGINT NOT NULL, FK users(id) ON DELETE CASCADE
    started_at        DATETIME NOT NULL   (UTC)
    finished_at       DATETIME NOT NULL   (UTC)
    window_start      DATE NOT NULL       dia do primeiro dia buscado
    window_end        DATE NOT NULL       dia do ultimo dia buscado
    status            VARCHAR(16) NOT NULL  `ok` | `error`
    activities_count  INT NOT NULL DEFAULT 0
    source            VARCHAR(16) NOT NULL  `scheduled` | `manual`
    KEY idx_isr_user_finished (user_id, finished_at)

--- Decisoes do DDL -------------------------------------------------------

1. **Aditiva (tabela nova)**: o codigo antigo ignora a tabela; o codigo novo tolera a ausencia (o sync nao falha, so nao
   registra; a leitura cai na regra antiga). Ordem do deploy: migracao 107, depois o codigo. A promocao exige
   `RUN_DB_MIGRATIONS=true`.
2. **Sem FK para `exercise_catalog`**; FK so para `users(id)` com `ON DELETE CASCADE` (apagar usuario leva o registro).
3. **Sem CHECK/ENUM** em `status`/`source`: os valores sao do codigo (constantes em `intervals_store`); VARCHAR evita
   migracao a cada valor novo.
4. **Idempotente**: `CREATE TABLE IF NOT EXISTS`; reexecucao em banco convergido = 0 statements. Forma divergente
   (tipo, nulabilidade, indice) -> `IntervalsSyncRunsError` ANTES do primeiro statement. Reversivel: `down` faz
   `DROP TABLE IF EXISTS` (perde so o registro; o motor volta a regra antiga).

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano; `down(db)`/`plan_down(db)` sao o inverso.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; nenhuma literal com dois-pontos (viraria
parametro no shim); modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "intervals_sync_runs"
USERS_TABLE = "users"
IDX_USER_FINISHED = "idx_isr_user_finished"
FK_USER = "fk_isr_user"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  started_at DATETIME NOT NULL,
  finished_at DATETIME NOT NULL,
  window_start DATE NOT NULL,
  window_end DATE NOT NULL,
  status VARCHAR(16) NOT NULL,
  activities_count INT NOT NULL DEFAULT 0,
  source VARCHAR(16) NOT NULL,
  KEY {IDX_USER_FINISHED} (user_id, finished_at),
  CONSTRAINT {FK_USER} FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""
DROP_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "started_at": ("datetime", "NO"),
    "finished_at": ("datetime", "NO"),
    "window_start": ("date", "NO"),
    "window_end": ("date", "NO"),
    "status": ("varchar(16)", "NO"),
    "activities_count": ("int", "NO"),
    "source": ("varchar(16)", "NO"),
}
EXPECTED_INDEX = (IDX_USER_FINISHED, ("user_id", "finished_at"))

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class IntervalsSyncRunsError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise IntervalsSyncRunsError(f"107: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db):
    names = (TABLE, USERS_TABLE)
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
            names,
        )
    }
    columns = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
            names,
        )
    }
    indexes: dict[str, list[dict]] = {}
    for row in db.query(
        "SELECT INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
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
    index_name, expected_columns = EXPECTED_INDEX
    rows = indexes.get(index_name)
    if not rows:
        _fail(f"{TABLE}.{index_name} nao existe")
    found = tuple(_text(r["COLUMN_NAME"]) for r in sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"])))
    if found != expected_columns:
        _fail(f"{TABLE}.{index_name} cobre {found}, esperado {expected_columns}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, indexes = _read(db)
    if USERS_TABLE not in engines:
        if bool(getattr(db, "dry_run", False)):
            return [] if TABLE in engines else [(CREATE_SQL, None)]
        _fail(f"tabela {USERS_TABLE} nao existe")
    _check_users(engines, columns)
    if TABLE in engines:
        _check_existing(engines, columns, indexes)
        return []
    return [(CREATE_SQL, None)]


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: 1 `DROP TABLE` se a tabela existe, senao nada."""
    engines, _columns, _indexes = _read(db)
    return [(DROP_SQL, None)] if TABLE in engines else []


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

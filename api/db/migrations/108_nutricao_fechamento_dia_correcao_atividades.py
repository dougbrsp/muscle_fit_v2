"""108 - fechamento manual do dia e correcao manual de atividades (2026-11-06, Lote G da nutricao adaptativa).

Duas tabelas NOVAS, ambas por usuario e reversiveis (reabrir o dia / desfazer a correcao = DELETE da linha):

`nutrition_day_closures` ("Fechei o dia"): o aluno declara que registrou tudo naquele dia. O motor
(`domain/nutrition/adaptive_energy`) trata o dia fechado como COMPLETO (origem `manual_close`); sem a linha vale a regra
dos 70% da TMB. Este modulo so guarda a marca; a regra nao mora aqui.

    user_id    BIGINT NOT NULL, FK users(id) ON DELETE CASCADE
    day        DATE NOT NULL        dia local do aluno
    closed_at  DATETIME NOT NULL    (UTC)
    PRIMARY KEY (user_id, day)

`activity_energy_corrections` (correcao de atividade do Intervals): `exclude` (duplicata/erro; a atividade continua
listada, marcada, mas nao soma) ou `reclassify` (muda o tipo de kcal: `active` ou `gross`). O motor aplica as correcoes
ANTES de normalizar/deduplicar (`activity_energy.apply_activity_corrections`).

    user_id      BIGINT NOT NULL, FK users(id) ON DELETE CASCADE
    activity_id  VARCHAR(64) NOT NULL   id da atividade como o servico a identifica (`str(intervals_activities.id)`)
    action       VARCHAR(16) NOT NULL   `exclude` | `reclassify`
    energy_kind  VARCHAR(16) NULL       `active` | `gross` quando `reclassify`
    created_at   DATETIME NOT NULL, updated_at DATETIME NOT NULL   (UTC)
    PRIMARY KEY (user_id, activity_id)

--- Decisoes do DDL -------------------------------------------------------

1. **Aditiva (tabelas novas)**: o codigo antigo ignora; o codigo novo tolera a ausencia (sem a tabela = sem
   fechamentos/correcoes, nunca 500). Ordem do deploy: migracao 108, depois o codigo. A promocao exige
   `RUN_DB_MIGRATIONS=true`.
2. **Sem FK para `exercise_catalog` nem para `intervals_activities`** (a correcao sobrevive a recarga do cache e o
   escopo e garantido pela rota); FK so para `users(id)` com `ON DELETE CASCADE`.
3. **Sem CHECK/ENUM** em `action`/`energy_kind`: os valores sao do codigo (motor); VARCHAR evita migracao a cada valor.
4. **Idempotente**: `CREATE TABLE IF NOT EXISTS`; convergido = 0 statements. Forma divergente (tipo, nulabilidade,
   chave primaria) -> `ClosuresCorrectionsError` ANTES do primeiro statement. Reversivel: `down` faz `DROP TABLE IF
   EXISTS` das duas (perde so as marcas; o motor volta a regra dos 70% e as atividades sem correcao).

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano; `down(db)`/`plan_down(db)` sao o inverso.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; nenhuma literal com dois-pontos; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

CLOSURES_TABLE = "nutrition_day_closures"
CORRECTIONS_TABLE = "activity_energy_corrections"
USERS_TABLE = "users"
FK_CLOSURES_USER = "fk_ndc_user"
FK_CORRECTIONS_USER = "fk_aec_user"

CREATE_CLOSURES_SQL = f"""
CREATE TABLE IF NOT EXISTS {CLOSURES_TABLE} (
  user_id BIGINT NOT NULL,
  day DATE NOT NULL,
  closed_at DATETIME NOT NULL,
  PRIMARY KEY (user_id, day),
  CONSTRAINT {FK_CLOSURES_USER} FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""
CREATE_CORRECTIONS_SQL = f"""
CREATE TABLE IF NOT EXISTS {CORRECTIONS_TABLE} (
  user_id BIGINT NOT NULL,
  activity_id VARCHAR(64) NOT NULL,
  action VARCHAR(16) NOT NULL,
  energy_kind VARCHAR(16) NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (user_id, activity_id),
  CONSTRAINT {FK_CORRECTIONS_USER} FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""
DROP_SQL = (f"DROP TABLE IF EXISTS {CORRECTIONS_TABLE}", f"DROP TABLE IF EXISTS {CLOSURES_TABLE}")

# tabela -> (coluna -> (tipo normalizado, IS_NULLABLE)), colunas da PK em ordem
EXPECTED_COLUMNS: dict[str, dict[str, tuple[str, str]]] = {
    CLOSURES_TABLE: {"user_id": ("bigint", "NO"), "day": ("date", "NO"), "closed_at": ("datetime", "NO")},
    CORRECTIONS_TABLE: {
        "user_id": ("bigint", "NO"),
        "activity_id": ("varchar(64)", "NO"),
        "action": ("varchar(16)", "NO"),
        "energy_kind": ("varchar(16)", "YES"),
        "created_at": ("datetime", "NO"),
        "updated_at": ("datetime", "NO"),
    },
}
EXPECTED_PK: dict[str, tuple[str, ...]] = {
    CLOSURES_TABLE: ("user_id", "day"),
    CORRECTIONS_TABLE: ("user_id", "activity_id"),
}
CREATE_ORDER = ((CLOSURES_TABLE, CREATE_CLOSURES_SQL), (CORRECTIONS_TABLE, CREATE_CORRECTIONS_SQL))

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class ClosuresCorrectionsError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise ClosuresCorrectionsError(f"108: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db):
    names = (CLOSURES_TABLE, CORRECTIONS_TABLE, USERS_TABLE)
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s, %s)",
            names,
        )
    }
    columns = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s, %s)",
            names,
        )
    }
    pks: dict[str, list[dict]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, SEQ_IN_INDEX, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s) AND INDEX_NAME = 'PRIMARY'",
        (CLOSURES_TABLE, CORRECTIONS_TABLE),
    ):
        pks.setdefault(_text(row["TABLE_NAME"]), []).append(row)
    return engines, columns, pks


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


def _check_existing(table, engines, columns, pks) -> None:
    if engines[table].lower() != "innodb":
        _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS[table].items():
        info = columns.get((table, name))
        if info is None:
            _fail(f"{table}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{table}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{table}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    rows = pks.get(table)
    found = tuple(_text(r["COLUMN_NAME"]) for r in sorted(rows or [], key=lambda r: int(r["SEQ_IN_INDEX"])))
    if found != EXPECTED_PK[table]:
        _fail(f"{table} tem PRIMARY KEY {found}, esperado {EXPECTED_PK[table]}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, pks = _read(db)
    if USERS_TABLE not in engines:
        if bool(getattr(db, "dry_run", False)):
            return [(sql, None) for table, sql in CREATE_ORDER if table not in engines]
        _fail(f"tabela {USERS_TABLE} nao existe")
    _check_users(engines, columns)
    statements: list[tuple[str, tuple | None]] = []
    for table, sql in CREATE_ORDER:
        if table in engines:
            _check_existing(table, engines, columns, pks)
        else:
            statements.append((sql, None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: 1 `DROP TABLE` por tabela existente, senao nada."""
    engines, _columns, _pks = _read(db)
    return [(sql, None) for sql, table in zip(DROP_SQL, (CORRECTIONS_TABLE, CLOSURES_TABLE)) if table in engines]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

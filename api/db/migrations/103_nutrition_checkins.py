"""103 - check-in semanal da nutricao (reformulacao N7, 2026-11-03).

    nutrition_checkins              um check-in por usuario e semana fechada
      id                    BIGINT AUTO_INCREMENT PK
      user_id               BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      week_start            DATE NOT NULL        segunda-feira da semana AVALIADA (fechada)
      due_date              DATE NOT NULL        dia civil do aluno em que o check-in foi gerado
      status                VARCHAR(16) NOT NULL proposed | accepted | declined | expired | informative
      verdict               VARCHAR(24) NOT NULL `weekly_checkin.CheckinVerdict`
      inputs_json           JSON NOT NULL        entradas CONGELADAS (resumo da semana: consumo, gasto, peso, %G...)
      result_json           JSON NOT NULL        veredito, motivos e o que faltou, como o motor devolveu
      adjustment_kcal       DECIMAL(8,2) NULL    diferenca de kcal/dia da proposta (NULL = sem proposta)
      proposed_changes_json JSON NULL            parametros do plano que mudam se aceito
      proposed_targets_json JSON NULL            metas de um dia sem treino, antes e depois
      expires_at            DATETIME NULL        so a proposta expira (relogio do banco)
      engine_version        VARCHAR(32) NOT NULL
      source_plan_id        BIGINT NOT NULL      referencia LOGICA ao plano vigente na hora (sem FK)
      generated_plan_id     BIGINT NULL          referencia LOGICA ao plano criado ao aceitar (sem FK)
      responded_at          DATETIME NULL
      created_at / updated_at
      UNIQUE uq_nci_user_week (user_id, week_start)
      KEY    idx_nci_user_created (user_id, created_at)

    nutrition_checkin_settings      o dia da semana do check-in escolhido pelo usuario
      user_id     BIGINT PK -> users(id) ON DELETE CASCADE
      weekday     TINYINT NOT NULL   0 = segunda ... 6 = domingo (`date.weekday()`)
      updated_at

Por que: o check-in avalia a semana fechada e PROPOE metas novas; o usuario confirma
com um toque, recusa, ou deixa expirar em 3 dias (D7). E gerado ao abrir o app (D8),
sem Lambda nem EventBridge novos, e precisa ser idempotente mesmo com aberturas
concorrentes: o UNIQUE `(user_id, week_start)` e a barreira (a segunda insercao falha
com chave duplicada e a rota le a linha da primeira).

--- Decisoes do DDL -------------------------------------------------------

1. **FK so para `users`** (ON DELETE CASCADE). `source_plan_id` e `generated_plan_id` sao
   referencias logicas: uma FK para `nutrition_plans` faria a exclusao de um usuario
   depender da ordem de apagar planos e check-ins. Nenhuma FK para `exercise_catalog`
   (a 028 recusa).
2. **Entradas congeladas em JSON.** A proposta precisa mostrar, dias depois, a semana
   que a gerou; as leituras do corpo e do diario podem mudar depois. O que o motor
   concluiu fica em `result_json`; as colunas escalares existem para listar e filtrar.
3. **Estado `informative`**: check-in sem mudanca de meta a confirmar (no ritmo,
   inconclusivo, alvo atingido, baixa aderencia). Nao expira nem pede resposta.
4. **Dia do check-in em tabela propria** (e nao em `nutrition_plans`): e preferencia do
   usuario, nao parametro de calculo; trocar o dia nao publica versao de plano.
5. **Criacao idempotente**: `CREATE TABLE IF NOT EXISTS`; reexecucao em banco
   convergido = 0 statements. Reversivel: `down` e um `DROP TABLE IF EXISTS` de cada
   tabela (perde so o historico de check-ins e o dia escolhido; planos e metas ficam).

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso.

- tabela ausente -> 1 `CREATE TABLE`; presente na forma esperada -> 0;
- coluna/tipo/nulabilidade diferente, indice ausente/diferente/nao UNIQUE,
  ENGINE != InnoDB ou `users` incompativel -> `NutritionCheckinsError` ANTES do
  primeiro statement;
- `users` ausente: erro numa execucao real; aceito em DRY-RUN (banco novo).

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; nenhuma
literal com ':' (viraria parametro no shim); modulo autocontido (nao importa
codigo do app).
"""

from __future__ import annotations

import re

CHECKINS_TABLE = "nutrition_checkins"
SETTINGS_TABLE = "nutrition_checkin_settings"
USERS_TABLE = "users"
TABLES = (CHECKINS_TABLE, SETTINGS_TABLE)

IDX_USER_WEEK = "uq_nci_user_week"
IDX_USER_CREATED = "idx_nci_user_created"
FK_CHECKIN_USER = "fk_nci_user"
FK_SETTINGS_USER = "fk_ncs_user"

CREATE_CHECKINS_SQL = f"""
CREATE TABLE IF NOT EXISTS {CHECKINS_TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  week_start DATE NOT NULL,
  due_date DATE NOT NULL,
  status VARCHAR(16) NOT NULL,
  verdict VARCHAR(24) NOT NULL,
  inputs_json JSON NOT NULL,
  result_json JSON NOT NULL,
  adjustment_kcal DECIMAL(8,2) NULL,
  proposed_changes_json JSON NULL,
  proposed_targets_json JSON NULL,
  expires_at DATETIME NULL,
  engine_version VARCHAR(32) NOT NULL,
  source_plan_id BIGINT NOT NULL,
  generated_plan_id BIGINT NULL,
  responded_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY {IDX_USER_WEEK} (user_id, week_start),
  KEY {IDX_USER_CREATED} (user_id, created_at),
  CONSTRAINT {FK_CHECKIN_USER} FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

CREATE_SETTINGS_SQL = f"""
CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE} (
  user_id BIGINT NOT NULL PRIMARY KEY,
  weekday TINYINT NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT {FK_SETTINGS_USER} FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

CREATE_BY_TABLE = {CHECKINS_TABLE: CREATE_CHECKINS_SQL, SETTINGS_TABLE: CREATE_SETTINGS_SQL}
DROP_SQL = {name: f"DROP TABLE IF EXISTS {name}" for name in TABLES}

# tabela -> coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, dict[str, tuple[str, str]]] = {
    CHECKINS_TABLE: {
        "id": ("bigint", "NO"),
        "user_id": ("bigint", "NO"),
        "week_start": ("date", "NO"),
        "due_date": ("date", "NO"),
        "status": ("varchar(16)", "NO"),
        "verdict": ("varchar(24)", "NO"),
        "inputs_json": ("json", "NO"),
        "result_json": ("json", "NO"),
        "adjustment_kcal": ("decimal(8,2)", "YES"),
        "proposed_changes_json": ("json", "YES"),
        "proposed_targets_json": ("json", "YES"),
        "expires_at": ("datetime", "YES"),
        "engine_version": ("varchar(32)", "NO"),
        "source_plan_id": ("bigint", "NO"),
        "generated_plan_id": ("bigint", "YES"),
        "responded_at": ("datetime", "YES"),
        "created_at": ("datetime", "NO"),
        "updated_at": ("datetime", "NO"),
    },
    SETTINGS_TABLE: {
        "user_id": ("bigint", "NO"),
        "weekday": ("tinyint", "NO"),
        "updated_at": ("datetime", "NO"),
    },
}

# tabela -> indice -> (UNIQUE?, colunas)
EXPECTED_INDEXES: dict[str, dict[str, tuple[bool, tuple[str, ...]]]] = {
    CHECKINS_TABLE: {
        IDX_USER_WEEK: (True, ("user_id", "week_start")),
        IDX_USER_CREATED: (False, ("user_id", "created_at")),
    },
    SETTINGS_TABLE: {"PRIMARY": (True, ("user_id",))},
}

# `int(11)` -> `int`; `tinyint(1)` (booleano) fica como esta.
_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class NutritionCheckinsError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise NutritionCheckinsError(f"103: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db):
    names = (*TABLES, USERS_TABLE)
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
    indexes: dict[tuple[str, str], list[dict]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE "
        f"FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({', '.join(['%s'] * len(TABLES))})",
        TABLES,
    ):
        indexes.setdefault((_text(row["TABLE_NAME"]), _text(row["INDEX_NAME"])), []).append(row)
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


def _check_existing(table, engines, columns, indexes) -> None:
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
    for name, (unique, cols) in EXPECTED_INDEXES[table].items():
        rows = indexes.get((table, name))
        if not rows:
            _fail(f"{table}.{name} nao existe")
        ordered = sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"]))
        found = tuple(_text(r["COLUMN_NAME"]) for r in ordered)
        if found != cols:
            _fail(f"{table}.{name} cobre {found}, esperado {cols}")
        if (int(ordered[0]["NON_UNIQUE"]) == 0) != unique:
            _fail(f"{table}.{name} {'NAO e' if unique else 'e'} UNIQUE")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, indexes = _read(db)
    if USERS_TABLE not in engines:
        if bool(getattr(db, "dry_run", False)):
            return [(CREATE_BY_TABLE[t], None) for t in TABLES if t not in engines]
        _fail(f"tabela {USERS_TABLE} nao existe")
    _check_users(engines, columns)
    statements: list[tuple[str, tuple | None]] = []
    for table in TABLES:
        if table in engines:
            _check_existing(table, engines, columns, indexes)
        else:
            statements.append((CREATE_BY_TABLE[table], None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: 1 `DROP TABLE` por tabela que existe, senao nada."""
    engines, _columns, _indexes = _read(db)
    return [(DROP_SQL[t], None) for t in TABLES if t in engines]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

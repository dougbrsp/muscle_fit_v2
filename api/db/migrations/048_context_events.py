"""048 - eventos de contexto (Novo Dashboard, F2.1 - plano §14, §12, §17).

    context_events
      id             BIGINT AUTO_INCREMENT PK
      user_id        BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      event_type     ENUM('alcohol','travel','illness','atypical_sleep',
                          'gym_change','plan_change','diet_phase',
                          'supplement','injury_pain','other') NOT NULL
      local_date     DATE NOT NULL         dia civil no fuso do usuario em que
                                           o evento COMECOU
      end_local_date DATE NULL             fim (inclusive) de evento com
                                           duracao (viagem, fase de dieta);
                                           NULL = evento de um dia so
      intensity      TINYINT NULL          1..5 quando faz sentido graduar
                                           (quanto de alcool, quanto de dor);
                                           NULL e o estado honesto de "nao
                                           graduado" - nunca 0
      notes          VARCHAR(255) NULL
      source         ENUM('manual','derived') NOT NULL DEFAULT 'manual'
      created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                                        ON UPDATE CURRENT_TIMESTAMP
      KEY (user_id, local_date)           timeline e janela do dashboard

Desvios do DDL do plano (§14), ambos aditivos e deliberados:

1. `updated_at` existe aqui e nao no texto do plano. O CRUD tem PUT (o
   usuario corrige a nota ou a data de um evento que ele mesmo registrou) e
   toda tabela de entrada manual do hub ja carrega `updated_at` (047). Sem
   ela, "quando isso foi corrigido?" nao teria resposta.
2. `source` nasce com DEFAULT 'manual'. O valor `derived` esta no ENUM
   porque o plano o preve, mas a F2.1 NAO materializa evento derivado: troca
   de ficha e troca de academia sao DERIVADAS NA LEITURA
   (`intelligence/dashboard/context_service.py`) das tabelas que ja sao a
   verdade (`workout_plan_template_assignments` e `activities.gym_id`).
   Materializar exigiria um job idempotente e criaria dupla verdade: apagar
   uma atribuicao de Modelo deixaria para tras um evento "troca de ficha"
   que nunca aconteceu. O ENUM fica pronto para o dia em que um derivado
   precisar ser editavel pelo usuario.

Sem UNIQUE: dois eventos do MESMO tipo no MESMO dia sao legitimos (duas
dores diferentes, dois voos). O POST por isso e INSERT, nao upsert - ao
contrario da 047, onde "cintura do dia" e unica por definicao.

ENUM para `event_type` e `source`: dominio FECHADO e pequeno, definido por
nos (mesma decisao da 045/047); o espelho do enum vive em
`app/context_events_schema.py::EVENT_TYPES` e o teste de plano compara os
dois lados.

Numeracao: o plano (§14) previa esta tabela como 047, mas a 046 foi ocupada
pela reversao da convencao de carga e a 047 por `body_measurements` - por
isso 048. `daily_checkins`/`dashboard_alert_states` da F2.2 deslocam junto.

Fluxo (mesmo padrao da 043/047): `plan(db)` so LE `information_schema` e
devolve os statements; `up(db)` executa o plano; `down(db)`/`plan_down(db)`
sao o inverso (DROP TABLE se a tabela existir - o `down` e real, a tabela
nasce vazia e nada a referencia).

- tabela ausente -> exatamente 1 `CREATE TABLE`;
- tabela presente no formato esperado -> nada (reexecucao: 0 statements);
- tabela presente com coluna/tipo/nulabilidade diferente, ENGINE != InnoDB
  ou `users` incompativel -> `ContextEventsError` ANTES do primeiro
  statement.

Aurora (recorte real somente leitura, tests/unit/data/aurora_schema_2026_09_17_035.json
- nao tem `context_events`) -> 1 statement. Reexecucao: 0.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "context_events"
USERS_TABLE = "users"

# Ordem CANONICA dos tipos (espelhada em `app/context_events_schema.py`).
EVENT_TYPES = (
    "alcohol",
    "travel",
    "illness",
    "atypical_sleep",
    "gym_change",
    "plan_change",
    "diet_phase",
    "supplement",
    "injury_pain",
    "other",
)

EVENT_SOURCES = ("manual", "derived")

_TYPE_VALUES_SQL = ",".join(f"'{value}'" for value in EVENT_TYPES)
_SOURCE_VALUES_SQL = ",".join(f"'{value}'" for value in EVENT_SOURCES)

EVENT_TYPE_DDL = f"ENUM({_TYPE_VALUES_SQL}) NOT NULL"
SOURCE_DDL = f"ENUM({_SOURCE_VALUES_SQL}) NOT NULL DEFAULT 'manual'"

EXPECTED_EVENT_TYPE = f"enum({_TYPE_VALUES_SQL})"
EXPECTED_SOURCE_TYPE = f"enum({_SOURCE_VALUES_SQL})"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  event_type {EVENT_TYPE_DDL},
  local_date DATE NOT NULL,
  end_local_date DATE NULL,
  intensity TINYINT NULL,
  notes VARCHAR(255) NULL,
  source {SOURCE_DDL},
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_ce_user_date (user_id, local_date),
  CONSTRAINT fk_ce_user FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "event_type": (EXPECTED_EVENT_TYPE, "NO"),
    "local_date": ("date", "NO"),
    "end_local_date": ("date", "YES"),
    "intensity": ("tinyint", "YES"),
    "notes": ("varchar(255)", "YES"),
    "source": (EXPECTED_SOURCE_TYPE, "NO"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class ContextEventsError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise ContextEventsError(f"048: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `tinyint(4)` -> `tinyint` (MySQL 8 ainda mostra a largura do tinyint).
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db) -> tuple[dict[str, str], dict[tuple[str, str], dict]]:
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
    return engines, columns


def _check_users(engines: dict[str, str], columns: dict[tuple[str, str], dict]) -> None:
    if USERS_TABLE not in engines:
        _fail(f"tabela {USERS_TABLE} nao existe")
    if engines[USERS_TABLE].lower() != "innodb":
        _fail(f"{USERS_TABLE} usa ENGINE={engines[USERS_TABLE]}, esperado InnoDB")
    info = columns.get((USERS_TABLE, "id"))
    if info is None:
        _fail(f"{USERS_TABLE}.id nao existe")
    if _normalize_type(info["COLUMN_TYPE"]) != "bigint":
        _fail(f"{USERS_TABLE}.id e {_text(info['COLUMN_TYPE'])}, esperado bigint")
    if _text(info["COLUMN_KEY"]) != "PRI":
        _fail(f"{USERS_TABLE}.id nao e PRIMARY KEY")


def _check_existing(engines: dict[str, str], columns: dict[tuple[str, str], dict]) -> None:
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


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns = _read(db)
    _check_users(engines, columns)
    if TABLE in engines:
        _check_existing(engines, columns)
        return []
    return [(CREATE_SQL, None)]


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: 1 `DROP TABLE` se a tabela existe, senao nada."""
    engines, _columns = _read(db)
    if TABLE not in engines:
        return []
    return [(DROP_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

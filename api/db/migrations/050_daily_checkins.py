"""050 - check-in diario subjetivo (Novo Dashboard, F2.2 - plano §5, §6
`recovery_signals`, §10 "api-core: entrada", §14).

    daily_checkins
      id          BIGINT AUTO_INCREMENT PK
      user_id     BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      local_date  DATE NOT NULL        dia civil no fuso do usuario a que o
                                       check-in se refere
      energy      TINYINT NULL         1..5  disposicao/energia (5 = otima)
      soreness    TINYINT NULL         1..5  dor muscular GLOBAL (5 = muita)
      stress      TINYINT NULL         1..5  estresse (5 = muito)
      mood        TINYINT NULL         1..5  humor (5 = otimo)
      notes       VARCHAR(255) NULL
      created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                                   ON UPDATE CURRENT_TIMESTAMP
      UNIQUE (user_id, local_date)   "o check-in de hoje" e unico por
                                     definicao -> POST e UPSERT

Decisoes do DDL, todas documentadas porque nenhuma e obvia:

1. **UNIQUE `(user_id, local_date)`** - ao contrario da 048 (`context_events`,
   onde dois eventos do mesmo tipo no mesmo dia sao legitimos), "como voce
   esta HOJE" tem uma resposta so. Responder de novo CORRIGE, nao acrescenta:
   o POST e upsert, no mesmo padrao da 047 (`body_measurements`).

2. **Os quatro escores sao NULL** (e nao `NOT NULL DEFAULT 3`). Check-in
   parcial e legitimo - quem so quer registrar que esta dolorido nao deve
   ser obrigado a inventar um humor. `NULL` e "nao respondido"; `0` nao
   existe na escala. E a mesma regra de `null != 0` do resto do hub: um
   escore ausente NAO entra na baseline nem nos sinais de recuperacao.

3. **Escala 1..5, `TINYINT`** - a mesma de `context_events.intensity` (048).
   A validacao de faixa fica no CRUD (`app/daily_checkins.py`), nao em CHECK
   constraint: o projeto nao usa CHECK em nenhuma tabela e a Data API nao
   daria mensagem util ao usuario.

4. **Nao existe `sleep_quality`.** O plano (§6) lista sono entre os sinais de
   recuperacao, mas duracao e regularidade do sono JA vem de `sleep_metrics` e
   de `intervals_wellness` (objetivo, medido pelo wearable). Um escore
   subjetivo de sono aqui seria uma SEGUNDA verdade sobre a mesma coisa, e o
   hub teria de escolher uma - exatamente o problema que a F2.1 evitou ao nao
   materializar evento derivado. Se um dia a percepcao de sono for pedida, ela
   entra como coluna nova (aditiva) com um nome que diga que e percepcao.

5. **`soreness` nao e `exercises.muscle_pain`.** A dor de `muscle_pain` e por
   SESSAO e por exercicio (rollup 0-10 que o Modo Treino mantem); esta aqui e
   a dor GLOBAL do dia, inclusive em dia sem treino - e isso que ela acrescenta.
   Os dois sinais convivem no `recovery_signals_service` sem se somarem.

Numeracao: o plano (§14) reservava a 049 para esta tabela, mas o M23 chegou
antes (`049_template_assignment_history.py`) e a F2.2 desloca para 050/051.

Fluxo (mesmo padrao da 047/048): `plan(db)` so LE `information_schema` e
devolve os statements; `up(db)` executa o plano; `down(db)`/`plan_down(db)`
sao o inverso (DROP TABLE se existir - o `down` e real, a tabela nasce vazia
e nada a referencia).

- tabela ausente -> exatamente 1 `CREATE TABLE`;
- tabela presente no formato esperado -> nada (reexecucao: 0 statements);
- tabela presente com coluna/tipo/nulabilidade diferente, ENGINE != InnoDB
  ou `users` incompativel -> `DailyCheckinsError` ANTES do primeiro statement.

Aurora (recorte real somente leitura, tests/unit/data/aurora_schema_2026_09_17_035.json
- nao tem `daily_checkins`) -> 1 statement. Reexecucao: 0.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "daily_checkins"
USERS_TABLE = "users"

# Ordem CANONICA dos escores (espelhada em `app/daily_checkins_schema.py`).
SCORE_FIELDS = ("energy", "soreness", "stress", "mood")

SCORE_MIN = 1
SCORE_MAX = 5

_SCORE_DDL = "\n".join(f"  {name} TINYINT NULL," for name in SCORE_FIELDS)

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  local_date DATE NOT NULL,
{_SCORE_DDL}
  notes VARCHAR(255) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_dc_user_date (user_id, local_date),
  CONSTRAINT fk_dc_user FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "local_date": ("date", "NO"),
    **{name: ("tinyint", "YES") for name in SCORE_FIELDS},
    "notes": ("varchar(255)", "YES"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class DailyCheckinsError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise DailyCheckinsError(f"050: {message}. Nenhum statement executado.")


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

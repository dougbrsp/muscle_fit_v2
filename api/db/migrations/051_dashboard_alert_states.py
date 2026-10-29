"""051 - memoria dos alertas do hub, para o cooldown (Novo Dashboard, F2.2 -
plano §12 "Cooldown", §14, §17).

    dashboard_alert_states
      id               BIGINT AUTO_INCREMENT PK
      user_id          BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      alert_key        VARCHAR(500) NOT NULL   IDENTIDADE do alerta (codigo +
                                               chaves do ALVO); e o que o
                                               cooldown silencia
      alert_key_hash   CHAR(64) NOT NULL       sha256 hex de `alert_key`, so
                                               para caber num UNIQUE
      rule_code        VARCHAR(64) NOT NULL    codigo da regra (telemetria e
                                               filtro por regra)
      fingerprint      VARCHAR(500) NOT NULL   ULTIMA impressao observada
                                               (evidencia; NAO e a identidade)
      severity         ENUM('INFO','ATTENTION','ACTION') NOT NULL
                                               severidade da ULTIMA emissao
      last_reason      VARCHAR(32) NOT NULL    por que foi mostrado na ultima
                                               emissao (`first_seen`,
                                               `new_episode`, `escalated`,
                                               `cooldown_expired`)
      first_seen_at    DATETIME NOT NULL       inicio do EPISODIO atual
      last_seen_at     DATETIME NOT NULL       ultima vez que a condicao foi
                                               observada (emitida ou silenciada)
      last_emitted_at  DATETIME NOT NULL       ultima vez que foi MOSTRADA
      emitted_count    INT NOT NULL DEFAULT 1
      suppressed_count INT NOT NULL DEFAULT 0
      resolved_at      DATETIME NULL           episodio encerrado (a condicao
                                               deixou de aparecer); NULL = aberto
      snoozed_until    DATETIME NULL           silenciado ATE este instante
      created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                                           ON UPDATE CURRENT_TIMESTAMP
      UNIQUE (user_id, alert_key_hash)        um estado por alerta por usuario
      KEY (user_id, rule_code)                telemetria por regra

Desvios do DDL do plano (§12), todos deliberados e medidos:

1. **A identidade e `alert_key`, nao `fingerprint`.** O plano assumia que a
   impressao (`insight_rules.build_fingerprint`) identificava o alerta. Ela
   nao identifica quando uma chave da comparacao e um CONJUNTO. Medido no
   dataset real de DEV (usuario 1, 8 leituras de `/dashboard/insights` entre
   2026-08-14 e 2026-09-25): a regra `EXERCISES_REGRESSING` produziu QUATRO
   impressoes diferentes nas 8 leituras
   (`EXERCISES_REGRESSING:exercise_ids=9,14,16,...` muda a cada exercicio que
   entra ou sai da lista), sempre para a MESMA condicao. Cooldown por
   impressao nunca silenciaria essa regra: cada dia seria um "alerta novo".
   Por isso cada regra declara `cooldown_keys` (o subconjunto de chaves que
   identifica o ALVO) e a `alert_key` e construida so com elas - para
   `EXERCISES_REGRESSING` o alvo e o treino como um todo, e a chave e o
   proprio codigo. A `fingerprint` continua gravada, como EVIDENCIA do que
   foi visto por ultimo.

2. **`alert_key_hash`.** A mesma medicao mostrou impressoes de 95 caracteres
   HOJE, e o comprimento cresce com o numero de exercicios na lista - nao ha
   teto natural. Indexar texto sem teto e como se pede uma truncagem (e, num
   UNIQUE, uma truncagem e uma COLISAO: dois alertas diferentes virariam um,
   silenciando o segundo para sempre). O UNIQUE vai no sha256 hex (64 bytes,
   comprimento fixo) e o texto fica legivel na coluna ao lado.

3. **`last_emitted_at`, `emitted_count`, `suppressed_count`.** O plano previa
   so `first_seen_at`/`last_seen_at`. Sem separar "vista" de "MOSTRADA" nao
   da para responder "quantas vezes esse alerta foi silenciado?" - que e
   justamente a pergunta que diz se o cooldown esta calibrado. Sao tres
   colunas de auditoria, aditivas, escritas na mesma linha do upsert.

4. **`severity` e ENUM, espelhando `contracts.Severity`** (dominio FECHADO,
   mesma decisao da 045/047/048). Ela existe porque o cooldown NAO silencia
   um alerta que PIOROU: comparar a severidade de agora com a da ultima
   emissao exige guardar a ultima.

5. **`last_reason`.** O cooldown e medido em DIAS, entao um alerta mostrado
   hoje continua visivel nas outras leituras de hoje (abrir a home e depois a
   tela de insights sao duas leituras da MESMA ocasiao - sem isso a segunda
   tela apareceria vazia). Para essas leituras devolverem exatamente a mesma
   resposta, o motivo da exibicao tem de ser LEMBRADO, nao recalculado: a
   segunda leitura nao e "primeira vez", mas e a mesma noticia, com o mesmo
   selo. `VARCHAR(32)` e nao ENUM porque este vocabulario e de CODIGO (cresce
   com o desenho do cooldown), nao de dominio do usuario - ao contrario de
   `severity`, que aparece na tela.

Sem DML: a tabela nasce vazia e o estado e construido pelas leituras
seguintes. Nao ha backfill possivel (nao existe historico de alerta mostrado)
e nem desejavel: um estado inventado silenciaria alerta que o usuario nunca viu.

Numeracao: o plano (§14) previa 050 para esta tabela; a 049 foi ocupada pelo
M23 (`template_assignment_history`) e a F2.2 desloca `daily_checkins` para a
050 e esta para a 051.

Fluxo (mesmo padrao da 047/048/050): `plan(db)` so LE `information_schema`;
`up(db)` executa o plano; `down(db)`/`plan_down(db)` sao o inverso (DROP TABLE
se existir - reversivel de verdade: a tabela e so memoria de exibicao, nada
a referencia e perde-la volta o hub ao comportamento pre-F2.2).

- tabela ausente -> exatamente 1 `CREATE TABLE`;
- tabela presente no formato esperado -> nada (reexecucao: 0 statements);
- tabela presente com coluna/tipo/nulabilidade diferente, ENGINE != InnoDB
  ou `users` incompativel -> `DashboardAlertStatesError` ANTES do primeiro
  statement.

Aurora (recorte real somente leitura, tests/unit/data/aurora_schema_2026_09_17_035.json
- nao tem `dashboard_alert_states`) -> 1 statement. Reexecucao: 0.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "dashboard_alert_states"
USERS_TABLE = "users"

# Espelho de `contracts.Severity`, na ordem canonica (menos grave -> mais grave).
SEVERITIES = ("INFO", "ATTENTION", "ACTION")

_SEVERITY_VALUES_SQL = ",".join(f"'{value}'" for value in SEVERITIES)

SEVERITY_DDL = f"ENUM({_SEVERITY_VALUES_SQL}) NOT NULL"
# `_normalize_type` compara em minusculas (os valores deste ENUM sao os unicos
# MAIUSCULOS do projeto - eles espelham `contracts.Severity`, que e maiusculo
# no JSON da resposta).
EXPECTED_SEVERITY_TYPE = f"enum({_SEVERITY_VALUES_SQL})".lower()

# sha256 em hexadecimal.
ALERT_KEY_HASH_LENGTH = 64

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  alert_key VARCHAR(500) NOT NULL,
  alert_key_hash CHAR({ALERT_KEY_HASH_LENGTH}) NOT NULL,
  rule_code VARCHAR(64) NOT NULL,
  fingerprint VARCHAR(500) NOT NULL,
  severity {SEVERITY_DDL},
  last_reason VARCHAR(32) NOT NULL,
  first_seen_at DATETIME NOT NULL,
  last_seen_at DATETIME NOT NULL,
  last_emitted_at DATETIME NOT NULL,
  emitted_count INT NOT NULL DEFAULT 1,
  suppressed_count INT NOT NULL DEFAULT 0,
  resolved_at DATETIME NULL,
  snoozed_until DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_das_user_key (user_id, alert_key_hash),
  KEY idx_das_user_rule (user_id, rule_code),
  CONSTRAINT fk_das_user FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "alert_key": ("varchar(500)", "NO"),
    "alert_key_hash": (f"char({ALERT_KEY_HASH_LENGTH})", "NO"),
    "rule_code": ("varchar(64)", "NO"),
    "fingerprint": ("varchar(500)", "NO"),
    "severity": (EXPECTED_SEVERITY_TYPE, "NO"),
    "last_reason": ("varchar(32)", "NO"),
    "first_seen_at": ("datetime", "NO"),
    "last_seen_at": ("datetime", "NO"),
    "last_emitted_at": ("datetime", "NO"),
    "emitted_count": ("int", "NO"),
    "suppressed_count": ("int", "NO"),
    "resolved_at": ("datetime", "YES"),
    "snoozed_until": ("datetime", "YES"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class DashboardAlertStatesError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise DashboardAlertStatesError(f"051: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `int(11)` -> `int` (MySQL 8 ainda mostra a largura em alguns recortes).
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

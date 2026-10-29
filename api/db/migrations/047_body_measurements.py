"""047 - medidas corporais por fita metrica (Novo Dashboard, F1.4 - plano §6
`waist_cm`/`recomposition_signal`, §14, §15).

    body_measurements
      id            BIGINT AUTO_INCREMENT PK
      user_id       BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      local_date    DATE NOT NULL            data civil no fuso do usuario
      site          ENUM('waist','hip','chest','arm_l','arm_r','thigh_l',
                         'thigh_r','calf_l','calf_r') NOT NULL
      value_cm      DECIMAL(5,1) NOT NULL    10.0 .. 300.0 (validado na API)
      protocol_ok   TINYINT(1) NOT NULL DEFAULT 1
                                             1 = medida no protocolo (mesmo
                                             horario, em jejum, mesmo ponto);
                                             0 = fora dele -> qualidade LOW,
                                             fora do sinal de recomposicao
      notes         VARCHAR(255) NULL
      source        VARCHAR(40) NOT NULL DEFAULT 'manual'
      created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                                       ON UPDATE CURRENT_TIMESTAMP
      UNIQUE (user_id, local_date, site)     upsert do POST
      KEY (user_id, site, local_date)        serie por local (§15)

Por que uma tabela nova e nao colunas em `body_composition`: aquela tabela e
UMA linha por dia com o que a balanca devolve (bioimpedancia, ESTIMATED). A
fita metrica e OBSERVADA, por local do corpo, em dias independentes da
pesagem, e a cintura e a unica medida que separa "recomposicao" de "simples
manutencao" no sinal `recomp-v1`. Misturar as duas obrigaria a criar linhas
de bioimpedancia vazias so para guardar uma cintura.

ENUM para `site`: dominio FECHADO e pequeno, definido por nos (mesma decisao
da 045 para `load_convention`); o espelho do enum vive em
`app/body_measurements.py::MEASUREMENT_SITES` e o teste de plano compara os
dois lados.

Numeracao: o plano (§14) previa esta tabela como 046, mas a 046 foi ocupada
pela reversao da convencao de carga - por isso 047.

Fluxo (mesmo padrao da 043): `plan(db)` so LE `information_schema` e devolve
os statements; `up(db)` executa o plano; `down(db)`/`plan_down(db)` sao o
inverso (DROP TABLE se a tabela existir - aqui o `down` e real, a tabela
nasce vazia e nada a referencia).

- tabela ausente -> exatamente 1 `CREATE TABLE`;
- tabela presente no formato esperado -> nada (reexecucao: 0 statements);
- tabela presente com coluna/tipo/nulabilidade diferente, ENGINE != InnoDB
  ou `users` incompativel -> `BodyMeasurementsError` ANTES do primeiro
  statement.

Aurora (recorte real somente leitura, tests/unit/data/aurora_schema_2026_09_17_035.json
- nao tem `body_measurements`) -> 1 statement. Reexecucao: 0.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "body_measurements"
USERS_TABLE = "users"

# Ordem CANONICA dos locais (espelhada em `app/body_measurements.py`).
MEASUREMENT_SITES = (
    "waist",
    "hip",
    "chest",
    "arm_l",
    "arm_r",
    "thigh_l",
    "thigh_r",
    "calf_l",
    "calf_r",
)

_ENUM_VALUES_SQL = ",".join(f"'{value}'" for value in MEASUREMENT_SITES)
SITE_DDL = f"ENUM({_ENUM_VALUES_SQL}) NOT NULL"
EXPECTED_SITE_TYPE = f"enum({_ENUM_VALUES_SQL})"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  local_date DATE NOT NULL,
  site {SITE_DDL},
  value_cm DECIMAL(5,1) NOT NULL,
  protocol_ok TINYINT(1) NOT NULL DEFAULT 1,
  notes VARCHAR(255) NULL,
  source VARCHAR(40) NOT NULL DEFAULT 'manual',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_bm_user_date_site (user_id, local_date, site),
  KEY idx_bm_user_site_date (user_id, site, local_date),
  CONSTRAINT fk_bm_user FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "local_date": ("date", "NO"),
    "site": (EXPECTED_SITE_TYPE, "NO"),
    "value_cm": ("decimal(5,1)", "NO"),
    "protocol_ok": ("tinyint", "NO"),
    "notes": ("varchar(255)", "YES"),
    "source": ("varchar(40)", "NO"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class BodyMeasurementsError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise BodyMeasurementsError(f"047: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `tinyint(1)` -> `tinyint` (MySQL 8 ainda mostra a largura do tinyint(1)).
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

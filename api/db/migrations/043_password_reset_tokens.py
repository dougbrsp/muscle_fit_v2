"""043 - tokens de redefinicao de senha e contadores de limitacao de taxa
(M5 "Esqueci minha senha").

Duas tabelas NOVAS, nenhuma coluna existente tocada:

    password_reset_tokens        um token por solicitacao aceita
      id            BIGINT AUTO_INCREMENT PK
      user_id       BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      token_hash    CHAR(64) NOT NULL UNIQUE   SHA-256 do token; o token em
                                               claro so existe no e-mail
      expires_at    DATETIME NOT NULL          validade curta (30 min)
      used_at       DATETIME NULL              uso unico: preenchido ao trocar
      created_ip    VARCHAR(45) NULL           IPv4/IPv6 de quem solicitou
      created_at    DATETIME NOT NULL

    password_reset_attempts      contador com janela deslizante
      id            BIGINT AUTO_INCREMENT PK
      scope         VARCHAR(16) NOT NULL       'request_ip' | 'request_account'
                                               | 'confirm_ip'
      subject_hash  CHAR(64) NOT NULL          SHA-256 do IP ou do identificador
                                               digitado (nunca o texto em claro:
                                               quem digita um e-mail que NAO
                                               existe nao vira PII no banco)
      created_at    DATETIME NOT NULL

Por que o contador vive no banco e nao em memoria: a api-core e uma Lambda
com N instancias sem memoria compartilhada; o unico ponto comum e o banco.
A janela e uma contagem `WHERE scope = ? AND subject_hash = ? AND created_at
>= ?`, coberta pelo indice composto. Sem dependencia nova de infra
(Redis/DynamoDB) - decisao do backlog M5.

DATETIME e nao TIMESTAMP, como nas 039/042: marcos gravados pela aplicacao em
UTC (`datetime.utcnow()`, o mesmo relogio de `user_sessions.expires_at` em
`app/auth.py`), sem default/ON UPDATE implicitos.

`users` vem do baseline (001): se a tabela nao existe ou `users.id` nao e
BIGINT PK InnoDB, o banco nao e o que esta migracao espera e ela falha
inclusive em dry-run (mesma regra da 033 para tabelas do baseline).

Fluxo: `plan(db)` so LE `information_schema` e devolve os statements; `up(db)`
executa o plano.

- tabela ausente -> 1 `CREATE TABLE` por tabela (no maximo 2 statements);
- tabela presente no formato esperado -> nada (reexecucao: 0 statements);
- tabela presente com coluna/tipo/nulabilidade diferente, ENGINE != InnoDB ou
  `users` incompativel -> `PasswordResetTokensError` ANTES do primeiro
  statement.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TOKENS_TABLE = "password_reset_tokens"
ATTEMPTS_TABLE = "password_reset_attempts"
USERS_TABLE = "users"

_TABLE_OPTIONS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"

CREATE_TOKENS_SQL = f"""
CREATE TABLE IF NOT EXISTS {TOKENS_TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  token_hash CHAR(64) NOT NULL,
  expires_at DATETIME NOT NULL,
  used_at DATETIME NULL,
  created_ip VARCHAR(45) NULL,
  created_at DATETIME NOT NULL,
  UNIQUE KEY uq_prt_token_hash (token_hash),
  KEY idx_prt_user (user_id),
  CONSTRAINT fk_prt_user FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) {_TABLE_OPTIONS}
"""

CREATE_ATTEMPTS_SQL = f"""
CREATE TABLE IF NOT EXISTS {ATTEMPTS_TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  scope VARCHAR(16) NOT NULL,
  subject_hash CHAR(64) NOT NULL,
  created_at DATETIME NOT NULL,
  KEY idx_pra_scope_subject_created (scope, subject_hash, created_at)
) {_TABLE_OPTIONS}
"""

# tabela -> (SQL de criacao, {coluna: (tipo normalizado, IS_NULLABLE)})
TARGETS: dict[str, tuple[str, dict[str, tuple[str, str]]]] = {
    TOKENS_TABLE: (
        CREATE_TOKENS_SQL,
        {
            "id": ("bigint", "NO"),
            "user_id": ("bigint", "NO"),
            "token_hash": ("char(64)", "NO"),
            "expires_at": ("datetime", "NO"),
            "used_at": ("datetime", "YES"),
            "created_ip": ("varchar(45)", "YES"),
            "created_at": ("datetime", "NO"),
        },
    ),
    ATTEMPTS_TABLE: (
        CREATE_ATTEMPTS_SQL,
        {
            "id": ("bigint", "NO"),
            "scope": ("varchar(16)", "NO"),
            "subject_hash": ("char(64)", "NO"),
            "created_at": ("datetime", "NO"),
        },
    ),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class PasswordResetTokensError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise PasswordResetTokensError(f"043: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


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


def _check_existing(table: str, expected: dict[str, tuple[str, str]], engines, columns) -> None:
    if engines[table].lower() != "innodb":
        _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in expected.items():
        info = columns.get((table, name))
        if info is None:
            _fail(f"{table}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{table}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{table}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    names = (ATTEMPTS_TABLE, TOKENS_TABLE, USERS_TABLE)
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
    _check_users(engines, columns)

    statements: list[tuple[str, tuple | None]] = []
    for table, (create_sql, expected) in TARGETS.items():
        if table in engines:
            _check_existing(table, expected, engines, columns)
        else:
            statements.append((create_sql, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)

"""044 - fuso horario do usuario (Novo Dashboard, F0.1 - plano §14).

    users.timezone  VARCHAR(64) NOT NULL DEFAULT 'America/Sao_Paulo'

Por que: toda data do dashboard passa a ser calculada no fuso do USUARIO
(`intelligence/dashboard/time_windows.py`), nao no do servidor (Lambda em
UTC). `activities.local_date` ja e gravado na data local do treino; a coluna
so diz em que fuso "hoje" deve ser resolvido para as janelas 7d/28d/ciclo.

DEFAULT 'America/Sao_Paulo': toda linha existente passa a significar
exatamente o que o sistema ja assume hoje (todos os usuarios estao no
Brasil), entao nenhum comportamento muda com o deploy. Sem backfill de
historico (as datas ja sao locais).

VARCHAR(64) e nao ENUM: o dominio e o banco IANA (`zoneinfo`), aberto e
atualizado fora do nosso codigo; a validacao do NOME fica na aplicacao
(`app/timezones.py`, usado por `PUT /users/me`). 64 chars cobre o maior nome
IANA existente (~32) com folga.

Posicao: SEM `AFTER` de proposito - independente da 040
(`preferred_weight_unit`) e de qualquer outra coluna: a coluna entra no fim
de `users`. Nada no app depende da ordem fisica das colunas (`SELECT *` +
acesso por nome).

Fluxo: `plan(db)` so LE `information_schema` e devolve os statements;
`up(db)` executa o plano; `down(db)` devolve/executa o inverso (DROP COLUMN
se a coluna existir) - o runner (`app.migrate`) so chama `up`; `down` existe
para reversao manual controlada e para o teste de plano provar que a
migracao e reversivel.

- coluna ausente -> exatamente 1 `ALTER TABLE users ADD COLUMN ...`;
- coluna presente no formato esperado -> nada (estado convergido);
- tabela `users` ausente, ENGINE != InnoDB, ou coluna presente com tipo/
  nulabilidade/default diferente -> `UsersTimezoneError` ANTES do primeiro
  statement.

Aurora (recorte real somente leitura, tests/unit/data/aurora_schema_2026_09_19_038.json
- `users` NAO tem `timezone`) -> 1 statement. Reexecucao: 0 statements.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

TABLE = "users"
COLUMN = "timezone"

DEFAULT_TIMEZONE = "America/Sao_Paulo"
COLUMN_DDL = f"VARCHAR(64) NOT NULL DEFAULT '{DEFAULT_TIMEZONE}'"
ALTER_SQL = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {COLUMN_DDL}"
DROP_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"

EXPECTED_TYPE = "varchar(64)"
EXPECTED_NULLABLE = "NO"
EXPECTED_DEFAULT = DEFAULT_TIMEZONE


class UsersTimezoneError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise UsersTimezoneError(f"044: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _read_users_columns(db) -> dict:
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
    }
    if TABLE not in engines:
        _fail(f"tabela {TABLE} nao existe")
    if engines[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engines[TABLE]}, esperado InnoDB")

    return {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
        if _text(row["TABLE_NAME"]) == TABLE
    }


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    columns = _read_users_columns(db)
    info = columns.get(COLUMN)
    if info is None:
        return [(ALTER_SQL, None)]

    actual_type = _text(info["COLUMN_TYPE"]).strip().lower()
    if actual_type != EXPECTED_TYPE:
        _fail(f"{TABLE}.{COLUMN} e {_text(info['COLUMN_TYPE'])}, esperado {EXPECTED_TYPE}")
    if _text(info["IS_NULLABLE"]) != EXPECTED_NULLABLE:
        _fail(f"{TABLE}.{COLUMN} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {EXPECTED_NULLABLE}")
    if _text(info["COLUMN_DEFAULT"]) != EXPECTED_DEFAULT:
        _fail(f"{TABLE}.{COLUMN} tem DEFAULT={info['COLUMN_DEFAULT']!r}, esperado {EXPECTED_DEFAULT!r}")
    return []


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: 1 `DROP COLUMN` se a coluna existe, senao nada."""
    columns = _read_users_columns(db)
    if COLUMN not in columns:
        return []
    return [(DROP_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

"""074 - antropometria no perfil (plano de nutrição, Lote L0, 2026-10-15).

    users
      + height_cm       DECIMAL(5,1) NULL   altura em cm (faixa validada pelo motor do L1;
                                            PATCH e tela chegam no L2)
      + activity_level  VARCHAR(16) NULL    código do nível de atividade (vocabulário do
                                            motor `domain/nutrition`, L1)

Por que: o motor de nutrição (TMB por Mifflin-St Jeor/Katch-McArdle, TDEE por
fator de atividade) precisa de altura e nível de atividade, que `users` não
tem (leitura em produção autorizada, 2026-10-15).

`users.sex` NÃO é tocado aqui: as telas atuais de perfil e de administração
gravam 'M'/'F' e não reconheceriam outro vocabulário. A conversão para o
vocabulário do motor fica para o L2, junto com as telas de perfil.

--- Decisoes do DDL -------------------------------------------------------

1. **DECIMAL(5,1) NULL** para altura (cm com uma decimal, mesmo tipo de
   `body_measurements.value_cm`). **VARCHAR(16) NULL** para o nível de
   atividade: os códigos são constantes do motor (padrão da 072).
2. **Sem backfill e sem índice.** Leitura sempre pela linha do usuário.
3. Ordem do deploy: com `RUN_DB_MIGRATIONS=true` o deploy de produção aplica o
   schema antes de atualizar as Lambdas, e o código ANTERIOR continua funcionando
   com as colunas novas (as escritas em `users` não as tocam). Com o gate
   desligado esta migração não quebra nada sozinha (`/auth/me` lê com
   `row.get` e devolve `null`), mas o PATCH de kcal (076) e o POST de medida
   com local novo (075) dão 500: a promoção do L0 exige o gate ligado e o
   dry-run antes.

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` soltam as colunas que existem. Mas
o runner de produção não tem ação `down`: em produção, reverter é sempre
uma migração nova; `down`/`plan_down` servem ao DEV e aos testes.

- coluna ausente -> `ADD COLUMN`; presente com outro tipo/nulabilidade ->
  `UsersAnthropometricsError` antes do primeiro statement;
- estado convergido (as duas colunas presentes) -> 0 statements;
- `users` ausente: erro numa execução real; aceito em DRY-RUN (só os ADDs).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
módulo autocontido (não importa código do app).
"""

from __future__ import annotations

TABLE = "users"

ADD_HEIGHT_SQL = f"ALTER TABLE {TABLE} ADD COLUMN height_cm DECIMAL(5,1) NULL DEFAULT NULL"
ADD_ACTIVITY_SQL = f"ALTER TABLE {TABLE} ADD COLUMN activity_level VARCHAR(16) NULL DEFAULT NULL"
DROP_HEIGHT_SQL = f"ALTER TABLE {TABLE} DROP COLUMN height_cm"
DROP_ACTIVITY_SQL = f"ALTER TABLE {TABLE} DROP COLUMN activity_level"

# coluna -> (tipo normalizado, IS_NULLABLE, ADD, DROP)
COLUMNS = {
    "height_cm": ("decimal(5,1)", "YES", ADD_HEIGHT_SQL, DROP_HEIGHT_SQL),
    "activity_level": ("varchar(16)", "YES", ADD_ACTIVITY_SQL, DROP_ACTIVITY_SQL),
}


class UsersAnthropometricsError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise UsersAnthropometricsError(f"074: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return "".join(_text(column_type).split()).lower()


def _read(db) -> tuple[bool, dict[str, dict]]:
    tables = db.query(
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (TABLE,),
    )
    exists = any(_text(row["TABLE_NAME"]) == TABLE for row in tables)
    columns: dict[str, dict] = {}
    if not exists:
        return False, columns
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    for row in db.query(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME IN ({placeholders})",
        (TABLE, *COLUMNS),
    ):
        columns[_text(row["COLUMN_NAME"])] = row
    return True, columns


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, columns = _read(db)
    if not exists:
        if bool(getattr(db, "dry_run", False)):
            return [(add_sql, None) for _type, _nullable, add_sql, _drop in COLUMNS.values()]
        _fail(f"tabela {TABLE} nao existe (a 001 precisa estar aplicada)")

    statements: list[tuple[str, tuple | None]] = []
    for name, (expected_type, expected_nullable, add_sql, _drop) in COLUMNS.items():
        column = columns.get(name)
        if column is None:
            statements.append((add_sql, None))
            continue
        if _normalize_type(column["COLUMN_TYPE"]) != expected_type:
            _fail(f"{TABLE}.{name} e {_text(column['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(column["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(column['IS_NULLABLE'])}, esperado {expected_nullable}")
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta as colunas que existem."""
    exists, columns = _read(db)
    if not exists:
        return []
    return [(drop_sql, None) for name, (_t, _n, _a, drop_sql) in reversed(COLUMNS.items()) if name in columns]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

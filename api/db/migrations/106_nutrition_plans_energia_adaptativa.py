"""106 - colunas do modo de gasto adaptativo em `nutrition_plans` (2026-11-06, Lote C da nutrição adaptativa).

    nutrition_plans.energy_params_json      JSON NULL         parâmetros do modelo de gasto (hoje só `watch_calorie_mode`)
    nutrition_plans.energy_model_version    VARCHAR(32) NULL  versão do algoritmo gravada na publicação do plano adaptativo
    nutrition_plans.published_preview_json  JSON NULL         prévia calculada no servidor no momento da publicação

Por que: o 4º modo de gasto (`adaptive_balance`) tem parâmetros próprios (como interpretar a kcal do relógio), precisa
registrar COM QUE VERSÃO do algoritmo o plano foi publicado (reprodutibilidade, spec §18) e guarda a prévia aprovada na
publicação (todos os modos, só em publicações NOVAS). Planos existentes ficam com NULL nas três colunas e se comportam
exatamente como antes; nenhuma meta já gravada muda.

--- Decisões do DDL -------------------------------------------------------

1. **Aditiva, nullable, sem default**: o código antigo sobre o schema novo segue funcionando (seus SELECT/INSERT listam
   colunas, e as novas ficam NULL); o código novo lê as colunas, então a ORDEM do deploy é: migração 106, depois o
   código (mesmo cuidado da 103/104/105). A promoção exige `RUN_DB_MIGRATIONS=true`.
2. **Sem `AFTER` e sem `ALGORITHM=INSTANT` explícito**: a coluna entra no fim da tabela e o servidor escolhe o
   algoritmo (um erro de algoritmo derrubaria a migração por uma otimização). `nutrition_plans` não tem FULLTEXT.
3. **Sem FK e sem índice**: são atributos lidos junto da linha do plano (poucas linhas por usuário). `JSON` sem default.
4. **Idempotente**: cada `ADD COLUMN` só roda se a coluna não existe; reexecução em banco convergido = 0 statements. Um
   `ALTER` por coluna (um statement por chamada). Reversível: `down` solta as colunas que existem (perde só estes dados).

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LE `information_schema`; `up(db)` executa o plano; `down(db)`/`plan_down(db)` são o inverso.

- coluna ausente -> 1 `ALTER TABLE` por coluna; presente na forma esperada -> 0;
- tipo/nulabilidade diferente -> `EnergiaAdaptativaError` ANTES do primeiro statement;
- tabela ausente: erro numa execução real; aceito em DRY-RUN (banco novo).

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; nenhuma literal com dois-pontos (viraria
parâmetro no shim); módulo autocontido (não importa código do app).
"""

from __future__ import annotations

TABLE = "nutrition_plans"
# (coluna, DDL, COLUMN_TYPE esperado em information_schema), na ordem de criação.
COLUMNS = (
    ("energy_params_json", "JSON NULL", "json"),
    ("energy_model_version", "VARCHAR(32) NULL", "varchar(32)"),
    ("published_preview_json", "JSON NULL", "json"),
)
EXPECTED_NULLABLE = "YES"


def _add_sql(column: str, ddl: str) -> str:
    return f"ALTER TABLE {TABLE} ADD COLUMN {column} {ddl}"


def _drop_sql(column: str) -> str:
    return f"ALTER TABLE {TABLE} DROP COLUMN {column}"


class EnergiaAdaptativaError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise EnergiaAdaptativaError(f"106: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _read(db):
    tables = {
        _text(row["TABLE_NAME"])
        for row in db.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (TABLE,),
        )
    }
    names = tuple(name for name, _ddl, _type in COLUMNS)
    columns = {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME IN (%s, %s, %s)",
            (TABLE, *names),
        )
    }
    return tables, columns


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    tables, columns = _read(db)
    dry_run = bool(getattr(db, "dry_run", False))
    if TABLE not in tables:
        if dry_run:
            return [(_add_sql(name, ddl), None) for name, ddl, _type in COLUMNS]
        _fail(f"tabela {TABLE} nao existe")
    statements: list[tuple[str, tuple | None]] = []
    for name, ddl, expected_type in COLUMNS:
        column = columns.get(name)
        if column is None:
            statements.append((_add_sql(name, ddl), None))
            continue
        if _text(column["COLUMN_TYPE"]).strip().lower() != expected_type:
            _fail(f"{TABLE}.{name} e {_text(column['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(column["IS_NULLABLE"]) != EXPECTED_NULLABLE:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(column['IS_NULLABLE'])}, esperado {EXPECTED_NULLABLE}")
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta cada coluna que existe (na ordem inversa), senao nada."""
    tables, columns = _read(db)
    if TABLE not in tables:
        return []
    return [(_drop_sql(name), None) for name, _ddl, _type in reversed(COLUMNS) if name in columns]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

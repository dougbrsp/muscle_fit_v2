"""105 - arquivar versões de Modelo e planos de nutrição fora de uso (2026-11-05).

    workout_plan_template_versions.archived_at   DATETIME NULL   quando a versão foi ocultada do histórico
    nutrition_plans.archived_at                  DATETIME NULL   quando o plano foi ocultado do histórico

Por que: com o tempo o histórico de versões do Modelo e a lista de planos de nutrição enchem de itens que ninguém
mais usa. Arquivar é SÓ ocultar da exibição, reversível (`archived_at` volta a NULL); nada é apagado e nenhum
cálculo muda: `workout_plan_template_versions` continua governando `resolve_version_at/now`, o `revert` e
`workout_plans.template_version_id`; `nutrition_plans` continua alimentando `load_published_plans` e o histórico de
metas por dia. Quem decide o que é arquivável (versão não vigente; plano substituído) é o backend
(`template_versions.is_archivable`, `domain/nutrition/plan.is_archivable_plan`); esta coluna só guarda o fato.

--- Decisões do DDL -------------------------------------------------------

1. **Aditiva, nullable, sem default**: o código antigo sobre o schema novo segue funcionando (não lê a coluna e os
   INSERTs antigos deixam NULL); o código novo lê a coluna, então a ORDEM do deploy é: migração 105, depois o código
   (o mesmo cuidado da 103/104). A promoção exige `RUN_DB_MIGRATIONS=true`.
2. **Sem `AFTER` e sem `ALGORITHM=INSTANT` explícito**: a coluna entra no fim da tabela e o servidor escolhe o
   algoritmo (no Aurora MySQL 3 é INSTANT para ADD COLUMN no fim; se não puder, cai em INPLACE). Nenhuma das duas
   tabelas tem FULLTEXT (o que barrou o INSTANT na `food_items`, 1846), mas não forçamos a cláusula: um erro de
   algoritmo derrubaria a migração por uma otimização.
3. **Sem FK e sem índice**: é atributo de exibição. As listagens filtram `archived_at IS NULL` sobre conjuntos
   pequenos (versões de UM Modelo; planos de UM usuário, no máximo `PLAN_LIST_MAX`). Nenhuma FK para
   `exercise_catalog` (a 028 recusa).
4. **Idempotente**: cada `ADD COLUMN` só roda se a coluna não existe; reexecução em banco convergido = 0 statements.
   Reversível: `down` solta as colunas (perde só a marca de arquivado; os dados ficam).

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LE `information_schema`; `up(db)` executa o plano; `down(db)`/`plan_down(db)` são o inverso.

- coluna ausente -> 1 `ALTER TABLE` por tabela; presente na forma esperada -> 0;
- tipo/nulabilidade diferente -> `ArchivedAtError` ANTES do primeiro statement;
- tabela ausente: erro numa execução real; aceito em DRY-RUN (banco novo).

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; nenhuma literal com ':' (viraria parâmetro no
shim); módulo autocontido (não importa código do app).
"""

from __future__ import annotations

TABLES = ("workout_plan_template_versions", "nutrition_plans")
COLUMN = "archived_at"
COLUMN_DDL = "DATETIME NULL"
EXPECTED_TYPE = "datetime"
EXPECTED_NULLABLE = "YES"


def _add_sql(table: str) -> str:
    return f"ALTER TABLE {table} ADD COLUMN {COLUMN} {COLUMN_DDL}"


def _drop_sql(table: str) -> str:
    return f"ALTER TABLE {table} DROP COLUMN {COLUMN}"


class ArchivedAtError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise ArchivedAtError(f"105: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _read(db):
    tables = {
        _text(row["TABLE_NAME"])
        for row in db.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
            TABLES,
        )
    }
    columns = {
        _text(row["TABLE_NAME"]): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s) AND COLUMN_NAME = %s",
            (*TABLES, COLUMN),
        )
    }
    return tables, columns


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    tables, columns = _read(db)
    dry_run = bool(getattr(db, "dry_run", False))
    statements: list[tuple[str, tuple | None]] = []
    for table in TABLES:
        if table not in tables:
            if dry_run:
                statements.append((_add_sql(table), None))
                continue
            _fail(f"tabela {table} nao existe")
        column = columns.get(table)
        if column is None:
            statements.append((_add_sql(table), None))
            continue
        if _text(column["COLUMN_TYPE"]).strip().lower() != EXPECTED_TYPE:
            _fail(f"{table}.{COLUMN} e {_text(column['COLUMN_TYPE'])}, esperado {EXPECTED_TYPE}")
        if _text(column["IS_NULLABLE"]) != EXPECTED_NULLABLE:
            _fail(f"{table}.{COLUMN} tem IS_NULLABLE={_text(column['IS_NULLABLE'])}, esperado {EXPECTED_NULLABLE}")
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta cada coluna que existe, senao nada."""
    tables, columns = _read(db)
    return [(_drop_sql(table), None) for table in TABLES if table in tables and table in columns]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

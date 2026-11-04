"""104 - base da tabela do alimento (nutrição, registro rápido D1, 2026-11-04).

    food_items.table_base_amount   DECIMAL(7,2) NULL   quantidade (g ou ml, conforme `basis`) POR QUE o usuário
                                                       digitou os valores do alimento; NULL = por 100 (o
                                                       comportamento de sempre, alimento antigo igual bit a bit)

Por que: o rótulo traz os valores por porção ("3,3 g de proteína em 30 g"). O usuário passa a digitar assim; o
MOTOR (`domain/nutrition/catalog.per_100_from_table_base`, sobre `meal_math.per_reference_from_serving`) converte
para o canônico por 100 g/ml, que continua sendo o que `food_items` guarda em todas as colunas de nutrientes e o que o
diário usa. Esta coluna só guarda a base digitada, para a edição reabrir como foi digitada
(`catalog.values_at_base`). Nenhum cálculo do diário lê esta coluna; itens já gravados em refeições usam o snapshot
congelado (E4) e não mudam.

--- Decisões do DDL -------------------------------------------------------

1. **Aditiva, nullable, sem default**: o código antigo sobre o schema novo segue funcionando (não lê a coluna e o
   INSERT antigo deixa NULL); o código novo lê a coluna em todo SELECT de alimento, então a ORDEM do deploy é:
   migração 104, depois o código (o mesmo cuidado da 103).
2. **DECIMAL(7,2)** como `serving_size` (até 99.999,99); o teto de aceitação é do motor
   (`catalog.TABLE_BASE_AMOUNT_MAX`).
3. **Sem FK e sem índice**: é atributo de exibição do próprio alimento. Nenhuma FK para `exercise_catalog` (a 028
   recusa).
4. **ALTER como a 084, sem `ALGORITHM=INSTANT`**: `food_items` tem o FULLTEXT `ft_fi_name` (080) e, com índice FULLTEXT, o
   InnoDB não faz ADD COLUMN INSTANT (erro 1846 no CI: "ALGORITHM=INSTANT is not supported ... one FULLTEXT index
   creation at a time"). Segue o precedente da 084 na mesma tabela: `ADD COLUMN ... AFTER <coluna>` sem cláusula de
   algoritmo, o servidor escolhe INPLACE e reconstrói a tabela (~600 linhas), com lock curto de metadados.
5. **Idempotente**: o `ADD COLUMN` só roda se a coluna não existe; reexecução em banco convergido = 0
   statements. Reversível: `down` solta a coluna (perde só a base digitada; os valores por 100 ficam).

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LE `information_schema`; `up(db)` executa o plano; `down(db)`/`plan_down(db)` são o inverso.

- coluna ausente -> 1 `ALTER TABLE`; presente na forma esperada -> 0;
- tipo/nulabilidade diferente -> `FoodItemsTableBaseError` ANTES do primeiro statement;
- `food_items` ausente: erro numa execução real; aceito em DRY-RUN (banco novo).

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; nenhuma literal com ':' (viraria parâmetro no
shim); módulo autocontido (não importa código do app).
"""

from __future__ import annotations

import re

TABLE = "food_items"
COLUMN = "table_base_amount"
COLUMN_DDL = "DECIMAL(7,2) NULL"
EXPECTED_TYPE = "decimal(7,2)"
EXPECTED_NULLABLE = "YES"

ADD_SQL = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {COLUMN_DDL} AFTER basis"
DROP_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"

# `int(11)` -> `int`; `tinyint(1)` (booleano) fica como esta.
_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class FoodItemsTableBaseError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise FoodItemsTableBaseError(f"104: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db):
    tables = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (TABLE,),
        )
    }
    column = db.query(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (TABLE, COLUMN),
    )
    return tables, (column[0] if column else None)


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    tables, column = _read(db)
    if TABLE not in tables:
        if bool(getattr(db, "dry_run", False)):
            return [(ADD_SQL, None)]
        _fail(f"tabela {TABLE} nao existe")
    if column is None:
        return [(ADD_SQL, None)]
    if _normalize_type(column["COLUMN_TYPE"]) != EXPECTED_TYPE:
        _fail(f"{TABLE}.{COLUMN} e {_text(column['COLUMN_TYPE'])}, esperado {EXPECTED_TYPE}")
    if _text(column["IS_NULLABLE"]) != EXPECTED_NULLABLE:
        _fail(f"{TABLE}.{COLUMN} tem IS_NULLABLE={_text(column['IS_NULLABLE'])}, esperado {EXPECTED_NULLABLE}")
    return []


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta a coluna se existe, senao nada."""
    tables, column = _read(db)
    if TABLE not in tables or column is None:
        return []
    return [(DROP_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

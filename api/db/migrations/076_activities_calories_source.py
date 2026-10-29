"""076 - origem da kcal gasta do treino (plano de nutrição, Lote L0, 2026-10-15).

    activities
      + calories_source  VARCHAR(16) NULL   `manual` | `intervals` (vocabulário em
                                            `app/domain/calories_source.py`)

Por que: `activities.calories_kcal` pode vir do dono do treino (edição na tela
do registro, `PATCH /workout-sessions/{id}/meta`) ou do sync do Intervals
(`intervals_sync._FILL_ACTIVITY_HR_SQL`, por `COALESCE`). A tela mostra a
origem e o módulo de nutrição a usa para decidir entre kcal informada e
estimada.

--- Decisoes do DDL -------------------------------------------------------

1. **VARCHAR(16) NULL**: códigos do motor (até 9 caracteres), padrão da 072.
2. **Sem backfill.** Medido em produção (2026-10-15): 34/34 sessões `done`
   desde 09/2026 têm kcal igual à do `activity_intervals_link`. O leitor
   (`domain/calories_source.resolve_calories_source`) INFERE `intervals` quando
   `activities.calories_kcal` = `activity_intervals_link.calories_kcal`; a
   coluna só é escrita pela edição manual, com o valor que o dono decide
   (`source_on_manual_write`: `manual`, ou `NULL` ao limpar ou quando a kcal
   digitada é a do link).
3. **Sem índice.** Leitura pela linha do treino.
4. Ordem do deploy: com `RUN_DB_MIGRATIONS=true` o deploy de produção aplica o
   schema antes de atualizar as Lambdas, e o código ANTERIOR continua
   funcionando (nenhuma escrita dele toca a coluna nova). Com o gate
   desligado, o PATCH de kcal (`PATCH /workout-sessions/{id}/meta` com
   `calories_kcal`) e o POST de medida com local novo (075) dão 500; a
   leitura (`GET /activities/{id}`) segue funcionando (`calories_source`
   ausente vira `None`).

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` são o inverso, mas
o runner de produção não tem ação `down`: em produção, reverter é sempre
uma migração nova; `down`/`plan_down` servem ao DEV e aos testes.

- coluna ausente -> `ADD COLUMN`; presente com outro tipo/nulabilidade ->
  `ActivitiesCaloriesSourceError` antes do primeiro statement;
- estado convergido -> 0 statements;
- `activities` ausente: erro numa execução real; aceito em DRY-RUN.

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
módulo autocontido (não importa código do app).
"""

from __future__ import annotations

TABLE = "activities"
COLUMN = "calories_source"

ADD_SQL = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} VARCHAR(16) NULL DEFAULT NULL"
DROP_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"

EXPECTED_TYPE = "varchar(16)"
EXPECTED_NULLABLE = "YES"


class ActivitiesCaloriesSourceError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise ActivitiesCaloriesSourceError(f"076: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return "".join(_text(column_type).split()).lower()


def _read(db) -> tuple[bool, dict | None]:
    tables = db.query(
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (TABLE,),
    )
    exists = any(_text(row["TABLE_NAME"]) == TABLE for row in tables)
    if not exists:
        return False, None
    rows = db.query(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (TABLE, COLUMN),
    )
    return True, (rows[0] if rows else None)


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, column = _read(db)
    if not exists:
        if bool(getattr(db, "dry_run", False)):
            return [(ADD_SQL, None)]
        _fail(f"tabela {TABLE} nao existe (a 001 precisa estar aplicada)")
    if column is None:
        return [(ADD_SQL, None)]
    if _normalize_type(column["COLUMN_TYPE"]) != EXPECTED_TYPE:
        _fail(f"{TABLE}.{COLUMN} e {_text(column['COLUMN_TYPE'])}, esperado {EXPECTED_TYPE}")
    if _text(column["IS_NULLABLE"]) != EXPECTED_NULLABLE:
        _fail(f"{TABLE}.{COLUMN} tem IS_NULLABLE={_text(column['IS_NULLABLE'])}, esperado {EXPECTED_NULLABLE}")
    return []


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta a coluna se ela existe (a kcal fica; só a origem se perde)."""
    exists, column = _read(db)
    if not exists or column is None:
        return []
    return [(DROP_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

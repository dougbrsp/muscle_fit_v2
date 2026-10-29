"""090 - linhagem do Modelo de Treino: de qual Modelo esta copia nasceu
(pedido do usuario 2026-10-24; dono da regra: `app/intelligence/slot_session_comparison.py`).

    workout_plan_templates
      + source_template_id   BIGINT NULL DEFAULT NULL   (Modelo de origem da copia; NULL = original)
      + KEY idx_wpt_source_template (source_template_id)

Por que: `POST /workout-plan-templates/{id}/duplicate` cria OUTRO `template_id`
com a mesma grade e nao guardava a origem. A identidade de slot do comparativo
T-0..T-3 e dos grupos de `/dashboard/treinos` (`SessionSlot.key`) e Modelo +
posicao, entao as sessoes do original e as da copia ficavam separadas (o 36,
copia do 34 de 17/10, separado das 2 sessoes do 34). Com a origem gravada, o
dono sobe a linhagem ate a raiz e as duas contam como o mesmo Modelo.

--- Decisoes do DDL -------------------------------------------------------

1. **`BIGINT NULL`, o tipo de `workout_plan_templates.id` (007).** NULL = Modelo
   original (todos os de hoje). Sem backfill aqui: as copias antigas nao
   deixaram rastro; a 091 junta so o par 36 -> 34, com aval do usuario.
2. **Sem FK, nem para a propria tabela.** Mesma decisao das colunas de
   historico da 065 (`reverted_from_version_id`, autores): a origem e
   historico. Modelos sao excluidos logicamente (065, `deleted_at`), entao a
   origem quase nunca some; se sumir (DELETE fisico), o id orfao continua
   sendo uma raiz comum para as copias, e o dono trata "origem fora do mapa"
   como raiz. Uma FK auto-referente tambem obrigaria um ALTER com copia da
   tabela (ADD FOREIGN KEY nao e INSTANT) e nenhuma FK para `exercise_catalog`
   entra (a 028 recusa).
3. **Indice proprio.** A leitura da linhagem (`template_lineage_reader`) filtra
   `source_template_id IS NOT NULL`; o indice tambem serve a "copias deste
   Modelo" no futuro.
4. **`ADD COLUMN ... ALGORITHM=INSTANT`** (MySQL 8.0 / Aurora MySQL 3: coluna
   nullable no fim e so metadado; o ALGORITHM explicito falha em vez de copiar
   em silencio). O indice e `ALGORITHM=INPLACE, LOCK=NONE` (online).

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso.

- coluna ausente -> ADD COLUMN; presente com outro tipo ou NOT NULL ->
  `TemplateLineageError` antes do primeiro statement;
- indice ausente -> ADD INDEX; presente em outra coluna -> erro;
- banco atual -> 2 statements; convergido -> 0;
- tabela ausente: erro numa execucao real; aceito em DRY-RUN (banco novo).

`down`: DROP INDEX e DROP COLUMN do que existir (perde so a linhagem; o dono
volta a separar copia e original, o comportamento de antes).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com ':'; modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

TABLE = "workout_plan_templates"
COLUMN = "source_template_id"
INDEX = "idx_wpt_source_template"
COLUMN_DDL = "BIGINT NULL DEFAULT NULL"
COLUMN_TYPE = "bigint"

ADD_COLUMN_SQL = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {COLUMN_DDL}, ALGORITHM=INSTANT"
ADD_INDEX_SQL = f"ALTER TABLE {TABLE} ADD INDEX {INDEX} ({COLUMN}), ALGORITHM=INPLACE, LOCK=NONE"
DROP_INDEX_SQL = f"ALTER TABLE {TABLE} DROP INDEX {INDEX}"
DROP_COLUMN_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"


class TemplateLineageError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise TemplateLineageError(f"090: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _read(db):
    """(tabela existe, linha da coluna ou None, colunas do indice)."""
    table_exists = bool(
        db.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (TABLE,),
        )
    )
    columns = db.query(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (TABLE, COLUMN),
    )
    index_columns = [
        _text(row["COLUMN_NAME"])
        for row in db.query(
            "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s ORDER BY SEQ_IN_INDEX",
            (TABLE, INDEX),
        )
    ]
    return table_exists, (columns[0] if columns else None), index_columns


def _base_type(column_type: str) -> str:
    """`bigint(20)` (MySQL < 8.0.19) e `bigint` sao o mesmo tipo."""
    return column_type.strip().lower().split("(", 1)[0]


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    table_exists, column, index_columns = _read(db)
    if not table_exists:
        if bool(getattr(db, "dry_run", False)):
            return [(ADD_COLUMN_SQL, None), (ADD_INDEX_SQL, None)]
        _fail(f"tabela {TABLE} nao existe")
    statements: list[tuple[str, tuple | None]] = []
    if column is None:
        statements.append((ADD_COLUMN_SQL, None))
    else:
        found_type = _base_type(_text(column["COLUMN_TYPE"]))
        if found_type != COLUMN_TYPE:
            _fail(f"{TABLE}.{COLUMN} e {found_type}, esperado {COLUMN_TYPE}")
        if _text(column["IS_NULLABLE"]).upper() != "YES":
            _fail(f"{TABLE}.{COLUMN} e NOT NULL, esperado NULL")
    if not index_columns:
        statements.append((ADD_INDEX_SQL, None))
    elif index_columns != [COLUMN]:
        _fail(f"indice {INDEX} cobre {index_columns}, esperado [{COLUMN}]")
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta o indice e a coluna que existirem."""
    table_exists, column, index_columns = _read(db)
    if not table_exists:
        return []
    statements: list[tuple[str, tuple | None]] = []
    if index_columns:
        statements.append((DROP_INDEX_SQL, None))
    if column is not None:
        statements.append((DROP_COLUMN_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

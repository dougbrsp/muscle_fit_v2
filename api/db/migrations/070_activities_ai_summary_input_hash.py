"""070 - impressao digital da entrada do resumo de treino por IA (IA-14,
2026-10-10).

    activities
      + ai_summary_input_sha256  CHAR(64) NULL   SHA-256 do modelo + versao do
                                                 prompt + prompt renderizado
                                                 que geraram `ai_summary`

Por que: `POST /activities/{id}/ai-summary/generate` chamava a OpenAI e
sobrescrevia o resumo a cada pedido, mesmo quando nada tinha mudado (reentrega
assincrona da Lambda, pedido repetido). Com a impressao digital guardada, o
dispatch (`workout_ai_generation_core.dispatch_activity_summary`) devolve o
resumo salvo quando a entrada e identica e so paga a OpenAI quando o treino,
as decisoes do motor, o prompt ou o modelo mudaram (ou com `force`).

--- Decisoes do DDL -------------------------------------------------------

1. **CHAR(64) NULL, sem default.** Linhas existentes ficam NULL: a primeira
   geracao depois do deploy se comporta como hoje (gera e grava a impressao).
2. **Sem indice.** A leitura e sempre pela PK de `activities`.
3. **Sem DML/backfill.** O codigo ANTERIOR a esta migracao (o deploy aplica o
   schema antes do codigo) continua funcionando: o UPDATE dele nao toca a
   coluna nova.

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso.

- coluna ausente -> `ADD COLUMN`; presente com outro tipo/nulabilidade ->
  `AiSummaryInputHashError` antes do primeiro statement;
- estado convergido -> 0 statements;
- `activities` ausente: erro numa execucao real; aceito em DRY-RUN.

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

TABLE = "activities"
COLUMN = "ai_summary_input_sha256"
EXPECTED_TYPE = "char(64)"
EXPECTED_NULLABLE = "YES"

ADD_SQL = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} CHAR(64) NULL DEFAULT NULL"
DROP_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"


class AiSummaryInputHashError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise AiSummaryInputHashError(f"070: {message}. Nenhum statement executado.")


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
        _fail(f"tabela {TABLE} nao existe")
    if column is None:
        return [(ADD_SQL, None)]
    if _normalize_type(column["COLUMN_TYPE"]) != EXPECTED_TYPE:
        _fail(f"{TABLE}.{COLUMN} e {_text(column['COLUMN_TYPE'])}, esperado {EXPECTED_TYPE}")
    if _text(column["IS_NULLABLE"]) != EXPECTED_NULLABLE:
        _fail(f"{TABLE}.{COLUMN} tem IS_NULLABLE={_text(column['IS_NULLABLE'])}, esperado {EXPECTED_NULLABLE}")
    return []


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta a coluna (o resumo salvo fica; so a proxima
    geracao volta a chamar a OpenAI sempre)."""
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

"""123 - politica do ciclo de carbo em coluna propria (issue #248).

`nutrition_plans` ganha `carb_cycle_policy VARCHAR(24) NULL DEFAULT NULL`: o codigo da politica do ciclo
(`vary_kcal` ou `fat_compensation`, dono `app/domain/nutrition/carb_cycling.py`). Ate aqui a politica morava
como chave irma `policy` dentro de `carb_cycling_json` (ADR do #242) e sumia ao desligar o ciclo; na coluna ela
e lembrada com o ciclo desligado. NULL = "sem valor na coluna": a leitura cai na chave do JSON (reserva) e, sem
ela, na politica padrao.

Preenchimento idempotente: `UPDATE ... SET carb_cycle_policy = <chave policy do JSON> WHERE carb_cycle_policy IS
NULL` so quando a chave e um dos codigos validos (passados como parametros). A chave do JSON NAO e apagada.

Medicao (somente leitura no DEV = copia da producao do usuario 1, 22/11/2026): 5 planos publicados, 4 com ciclo,
NENHUM com a chave `policy` gravada (todos na politica padrao). Hoje o preenchimento nao altera nenhuma linha.

`ALGORITHM=INSTANT` (so metadado); falha em vez de copiar a tabela em silencio. `plan(db)` so LE
`information_schema`; `up(db)` executa o plano; `down(db)`/`plan_down(db)` removem a coluna (a chave do JSON
continua la, entao a leitura antiga segue valendo).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada; nenhuma literal com dois-pontos; modulo
autocontido (nao importa codigo do app; os codigos abaixo espelham o enum do dono e um teste confere a paridade).
"""

from __future__ import annotations

TABLE = "nutrition_plans"
COLUMN = "carb_cycle_policy"
COLUMN_DDL = "VARCHAR(24) NULL DEFAULT NULL"
EXPECTED_TYPE = "varchar(24)"
VALID_POLICIES = ("vary_kcal", "fat_compensation")  # espelho de CarbCyclePolicy


class CarbCyclePolicyColumnError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def add_column_sql() -> str:
    return f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {COLUMN_DDL}, ALGORITHM=INSTANT"


def drop_column_sql() -> str:
    return f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"


def backfill_sql() -> str:
    chave = "JSON_UNQUOTE(JSON_EXTRACT(carb_cycling_json, '$.policy'))"
    marcas = ", ".join(["%s"] * len(VALID_POLICIES))
    return f"UPDATE {TABLE} SET {COLUMN} = {chave} WHERE {COLUMN} IS NULL AND {chave} IN ({marcas})"


def _read(db):
    """(tabela existe?, linha da coluna ou None)."""
    exists = bool(
        db.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (TABLE,),
        )
    )
    rows = db.query(
        "SELECT TABLE_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (TABLE, COLUMN),
    )
    return exists, (rows[0] if rows else None)


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, column = _read(db)
    preencher = (backfill_sql(), VALID_POLICIES)
    if not exists:
        if getattr(db, "dry_run", False):  # banco atrasado: so LISTA o DDL
            return [(add_column_sql(), None), preencher]
        raise CarbCyclePolicyColumnError(f"123: tabela {TABLE} nao existe. Nenhum statement executado.")
    if column is None:
        return [(add_column_sql(), None), preencher]
    found = "".join(_text(column["COLUMN_TYPE"]).split()).lower()
    nullable = _text(column["IS_NULLABLE"]).upper()
    default = None if column.get("COLUMN_DEFAULT") is None else _text(column["COLUMN_DEFAULT"])
    if found != EXPECTED_TYPE or nullable != "YES" or default is not None:
        raise CarbCyclePolicyColumnError(
            f"123: {TABLE}.{COLUMN} e {found} (nullable={nullable}, default={default!r}), "
            f"esperado {EXPECTED_TYPE} NULL DEFAULT NULL. Nenhum statement executado."
        )
    return [preencher]  # coluna ja existe: so repete o preenchimento (idempotente, WHERE coluna IS NULL)


def plan_down(db) -> list[tuple[str, tuple | None]]:
    exists, column = _read(db)
    return [(drop_column_sql(), None)] if exists and column is not None else []


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

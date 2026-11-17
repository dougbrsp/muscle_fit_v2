"""118 - Contexto de academia na trilha de progressao (issue #188).

A mesma MAQUINA em academias diferentes e outra trilha (regulagem, polia,
placa); peso livre nao muda. A academia vem do dono
`app/domain/equipment_context.context_gym_id`; aqui so a forma:

- progression_tracks + gym_context_id INT NOT NULL DEFAULT 0
  (0 = sem contexto de academia: peso livre ou academia desconhecida);
- UNIQUE KEY uk_ptr_user_pair_context
      (user_id, exercise_id, equipment_id, fatigue_context, training_goal)
    ~ (user_id, exercise_id, equipment_id, fatigue_context, training_goal, gym_context_id)

A chave e refeita num ALTER so (`DROP INDEX k, ADD UNIQUE KEY k (...)`),
como a 093: atomico, nunca ha um instante sem unicidade. Nenhuma linha
existente muda de identidade (todas nascem com 0); sem backfill, o estado de
maquina se separa por academia no replay da progressao. Sem FK.

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso (o MySQL recusa voltar a chave se ja
houver duas trilhas da mesma maquina em academias diferentes - de proposito).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com dois-pontos; modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

TABLE = "progression_tracks"
COLUMN = "gym_context_id"
COLUMN_DDL = "INT NOT NULL DEFAULT 0"
EXPECTED_TYPE_PREFIX = "int"
EXPECTED_DEFAULT = "0"
KEY = "uk_ptr_user_pair_context"
KEY_BEFORE: tuple[str, ...] = ("user_id", "exercise_id", "equipment_id", "fatigue_context", "training_goal")
KEY_AFTER: tuple[str, ...] = KEY_BEFORE + (COLUMN,)


def add_column_sql() -> str:
    return f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {COLUMN_DDL}"


def drop_column_sql() -> str:
    return f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"


def rebuild_key_sql(columns: tuple[str, ...]) -> str:
    return f"ALTER TABLE {TABLE} DROP INDEX {KEY}, ADD UNIQUE KEY {KEY} ({', '.join(columns)})"


def add_key_sql(columns: tuple[str, ...]) -> str:
    return f"ALTER TABLE {TABLE} ADD UNIQUE KEY {KEY} ({', '.join(columns)})"


class GymContextError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise GymContextError(f"118: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _read(db):
    """(tabela existe?, linha da coluna ou None, colunas da chave ou None)."""
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
    column = rows[0] if rows else None
    key = [
        _text(row["COLUMN_NAME"])
        for row in db.query(
            "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s "
            "ORDER BY SEQ_IN_INDEX",
            (TABLE, KEY),
        )
    ]
    return exists, column, (tuple(key) or None)


def _check_column(info) -> None:
    found_type = _text(info["COLUMN_TYPE"]).strip().lower()
    if not found_type.startswith(EXPECTED_TYPE_PREFIX) or "unsigned" in found_type:
        _fail(f"{TABLE}.{COLUMN} e {found_type}, esperado int")
    if _text(info["IS_NULLABLE"]).upper() != "NO":
        _fail(f"{TABLE}.{COLUMN} aceita NULL, esperado NOT NULL")
    raw_default = info.get("COLUMN_DEFAULT")
    found_default = None if raw_default is None else _text(raw_default).strip("'")
    if found_default != EXPECTED_DEFAULT:
        _fail(f"{TABLE}.{COLUMN} tem default {found_default!r}, esperado {EXPECTED_DEFAULT!r}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, column, key = _read(db)
    dry_run = bool(getattr(db, "dry_run", False))
    if not exists:
        if dry_run:  # banco atrasado: as anteriores do lote so LISTAM o DDL
            return [(add_column_sql(), None), (add_key_sql(KEY_AFTER), None)]
        _fail(f"tabela {TABLE} nao existe")
    statements: list[tuple[str, tuple | None]] = []
    if column is None:
        statements.append((add_column_sql(), None))
    else:
        _check_column(column)
    if key is None:
        if dry_run:
            statements.append((add_key_sql(KEY_AFTER), None))
            return statements
        _fail(f"chave {KEY} de {TABLE} nao existe")
    if key == KEY_AFTER:
        return statements
    if key != KEY_BEFORE:
        _fail(f"chave {KEY} de {TABLE} cobre {list(key)}, esperado {list(KEY_BEFORE)} ou {list(KEY_AFTER)}")
    statements.append((rebuild_key_sql(KEY_AFTER), None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: chave de volta a forma da 093 (antes de soltar a
    coluna que ela cobre), depois `DROP COLUMN`."""
    exists, column, key = _read(db)
    statements: list[tuple[str, tuple | None]] = []
    if key == KEY_AFTER:
        statements.append((rebuild_key_sql(KEY_BEFORE), None))
    if exists and column is not None:
        statements.append((drop_column_sql(), None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

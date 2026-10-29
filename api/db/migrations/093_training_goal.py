"""093 - objetivo de treino (M97, F0; dono `app/domain/training_goal.py`).

    workout_plan_templates            + training_goal ENUM(...) NOT NULL DEFAULT 'hypertrophy'
    workout_plan_template_versions    + training_goal ENUM(...) NOT NULL DEFAULT 'hypertrophy'
    workout_plan_template_slot_items  + training_goal ENUM(...) NULL           (NULL = herda do Modelo)
    workout_plan_exercises            + training_goal ENUM(...) NOT NULL DEFAULT 'hypertrophy'
    exercises                         + training_goal ENUM(...) NOT NULL DEFAULT 'hypertrophy'
    progression_decisions             + training_goal ENUM(...) NOT NULL DEFAULT 'hypertrophy'
    progression_tracks                + training_goal ENUM(...) NOT NULL DEFAULT 'hypertrophy'
      ~ UNIQUE KEY uk_ptr_user_pair_context (user_id, exercise_id, equipment_id, fatigue_context)
                                         -> (user_id, exercise_id, equipment_id, fatigue_context, training_goal)
    progression_group_states          + training_goal ENUM(...) NOT NULL DEFAULT 'hypertrophy'
      ~ UNIQUE KEY uk_pgs_user_group (user_id, muscle_group_id)
                                  -> (user_id, muscle_group_id, training_goal)

Por que: o app passa a suportar o objetivo FORÇA além de hipertrofia. A F0 só
cria o lugar do dado: nenhum motor lê a coluna ainda (F1/F2). Progressão de
força e de hipertrofia do mesmo par/grupo serão trilhas separadas (F1); por
isso o objetivo já entra nas chaves únicas.

--- Decisões do DDL -------------------------------------------------------

1. **ENUM, não VARCHAR.** O eixo é binário e fechado (decisão do usuário); o
   ENUM recusa no banco qualquer valor fora do vocabulário do dono. O teste
   `test_migration_093_plano.py` prova que o ENUM daqui é o `GOAL_VALUES` do
   dono (a migração é autocontida e não importa o app).
2. **NOT NULL DEFAULT 'hypertrophy', sem backfill.** Toda linha existente
   passa a ser hipertrofia pelo DEFAULT (é o que o app sempre prescreveu) e
   todo INSERT que não cita a coluna (relógio, importadores, UPSERTs da
   progressão anteriores à F1) grava o default. Nenhum comportamento muda.
3. **Item do slot é NULL sem default.** NULL = herda o objetivo do Modelo
   (`training_goal.resolve_goal`). Nenhum item existente tem override.
4. **ADD COLUMN com ALGORITHM=INSTANT** (ver 089/092): só metadado; falha em
   vez de copiar a tabela.
5. **Chaves únicas recriadas com o MESMO nome, num ALTER só** (`DROP INDEX x,
   ADD UNIQUE KEY x (...)`): atômico, nunca há um instante sem unicidade, e as
   FKs por `user_id` continuam servidas (a chave nova começa por `user_id`).
   Como só existe 'hypertrophy', a chave nova não separa linha nenhuma: o
   `UPDATE ... WHERE (chave antiga)` + INSERT de `progression_state.save_track`
   / `save_group_state` continua achando exatamente a mesma linha.
6. **Nenhuma FK** (nenhuma para `exercise_catalog`; a 028 recusa).

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` são o inverso.

- coluna ausente -> `ADD COLUMN`; presente com outro tipo, nulabilidade ou
  default -> `TrainingGoalError` antes do primeiro statement;
- chave única na forma anterior -> recriada com o objetivo; já na forma nova
  -> nada; em qualquer outra forma -> erro antes do primeiro statement;
- banco atual -> 8 ADD COLUMN + 2 chaves; convergido -> 0;
- tabela ou chave ausente: erro numa execução real; aceito em DRY-RUN (banco
  atrasado: as anteriores do lote só listam DDL) -> lista o ADD na forma nova.

`down`: chaves de volta à forma anterior (o MySQL recusa se houver duas
trilhas/estados do mesmo par/grupo com objetivos diferentes - nada é apagado
em silêncio) e `DROP COLUMN` de cada coluna nova que existir. Está em
`tests/support/migrations.RESHAPING_MIGRATIONS`: a 036 e a 068 conferem as
chaves por igualdade quando reexecutam num schema de teste.

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com ':'; módulo autocontido (não importa código do app).
"""

from __future__ import annotations

COLUMN = "training_goal"
GOAL_VALUES: tuple[str, ...] = ("hypertrophy", "strength")
DEFAULT_GOAL = "hypertrophy"

_ENUM_SQL = "ENUM(" + ",".join(f"'{value}'" for value in GOAL_VALUES) + ")"
EXPECTED_TYPE = "enum(" + ",".join(f"'{value}'" for value in GOAL_VALUES) + ")"

NOT_NULL_DDL = f"{_ENUM_SQL} NOT NULL DEFAULT '{DEFAULT_GOAL}'"
NULL_DDL = f"{_ENUM_SQL} NULL DEFAULT NULL"

# (tabela, DDL, IS_NULLABLE esperado, default esperado) na ordem de execução.
TARGETS: tuple[tuple[str, str, str, str | None], ...] = (
    ("workout_plan_templates", NOT_NULL_DDL, "NO", DEFAULT_GOAL),
    ("workout_plan_template_versions", NOT_NULL_DDL, "NO", DEFAULT_GOAL),
    ("workout_plan_template_slot_items", NULL_DDL, "YES", None),
    ("workout_plan_exercises", NOT_NULL_DDL, "NO", DEFAULT_GOAL),
    ("exercises", NOT_NULL_DDL, "NO", DEFAULT_GOAL),
    ("progression_decisions", NOT_NULL_DDL, "NO", DEFAULT_GOAL),
    ("progression_tracks", NOT_NULL_DDL, "NO", DEFAULT_GOAL),
    ("progression_group_states", NOT_NULL_DDL, "NO", DEFAULT_GOAL),
)
TABLES: tuple[str, ...] = tuple(table for table, *_rest in TARGETS)

# (tabela, chave, colunas antes, colunas depois)
UNIQUE_KEYS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "progression_tracks",
        "uk_ptr_user_pair_context",
        ("user_id", "exercise_id", "equipment_id", "fatigue_context"),
        ("user_id", "exercise_id", "equipment_id", "fatigue_context", COLUMN),
    ),
    (
        "progression_group_states",
        "uk_pgs_user_group",
        ("user_id", "muscle_group_id"),
        ("user_id", "muscle_group_id", COLUMN),
    ),
)


def add_column_sql(table: str, ddl: str) -> str:
    return f"ALTER TABLE {table} ADD COLUMN {COLUMN} {ddl}, ALGORITHM=INSTANT"


def drop_column_sql(table: str) -> str:
    return f"ALTER TABLE {table} DROP COLUMN {COLUMN}"


def rebuild_key_sql(table: str, key: str, columns: tuple[str, ...]) -> str:
    return f"ALTER TABLE {table} DROP INDEX {key}, ADD UNIQUE KEY {key} ({', '.join(columns)})"


def add_key_sql(table: str, key: str, columns: tuple[str, ...]) -> str:
    return f"ALTER TABLE {table} ADD UNIQUE KEY {key} ({', '.join(columns)})"


class TrainingGoalError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise TrainingGoalError(f"093: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _read(db):
    """({tabelas existentes}, {tabela: linha da coluna}, {(tabela, chave): [colunas]})."""
    placeholders = ", ".join(["%s"] * len(TABLES))
    existing = {
        _text(row["TABLE_NAME"])
        for row in db.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            TABLES,
        )
    }
    columns = {
        _text(row["TABLE_NAME"]): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME = %s AND TABLE_NAME IN ({placeholders})",
            (COLUMN, *TABLES),
        )
    }
    keys: dict[tuple[str, str], list[str]] = {}
    key_tables = tuple(table for table, *_rest in UNIQUE_KEYS)
    key_names = tuple(key for _table, key, *_rest in UNIQUE_KEYS)
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() "
        f"AND TABLE_NAME IN ({', '.join(['%s'] * len(key_tables))}) "
        f"AND INDEX_NAME IN ({', '.join(['%s'] * len(key_names))}) "
        "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
        (*key_tables, *key_names),
    ):
        keys.setdefault((_text(row["TABLE_NAME"]), _text(row["INDEX_NAME"])), []).append(_text(row["COLUMN_NAME"]))
    return existing, columns, keys


def _check_column(table: str, expected_nullable: str, expected_default, info) -> None:
    found_type = _text(info["COLUMN_TYPE"]).strip().lower().replace(" ", "")
    if found_type != EXPECTED_TYPE:
        _fail(f"{table}.{COLUMN} e {found_type}, esperado {EXPECTED_TYPE}")
    found_nullable = _text(info["IS_NULLABLE"]).upper()
    if found_nullable != expected_nullable:
        _fail(f"{table}.{COLUMN} tem IS_NULLABLE={found_nullable}, esperado {expected_nullable}")
    raw_default = info.get("COLUMN_DEFAULT")
    found_default = None if raw_default is None else _text(raw_default).strip("'")
    if found_default != expected_default:
        _fail(f"{table}.{COLUMN} tem default {found_default!r}, esperado {expected_default!r}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    existing, columns, keys = _read(db)
    dry_run = bool(getattr(db, "dry_run", False))
    statements: list[tuple[str, tuple | None]] = []
    for table, ddl, expected_nullable, expected_default in TARGETS:
        if table not in existing:
            if dry_run:
                statements.append((add_column_sql(table, ddl), None))
                continue
            _fail(f"tabela {table} nao existe")
        info = columns.get(table)
        if info is None:
            statements.append((add_column_sql(table, ddl), None))
        else:
            _check_column(table, expected_nullable, expected_default, info)
    for table, key, before, after in UNIQUE_KEYS:
        found = keys.get((table, key))
        if table not in existing:
            statements.append((add_key_sql(table, key, after), None))  # só DRY-RUN chega aqui
            continue
        if found is None:
            # DRY-RUN num banco atrasado: as migrações anteriores do mesmo lote
            # (a 068 cria `uk_ptr_user_pair_context`) só LISTAM o DDL, então a
            # chave ainda não existe. Lista a criação na forma nova. Numa
            # execução real a 036/068 já rodaram: chave ausente é estado
            # incompatível.
            if dry_run:
                statements.append((add_key_sql(table, key, after), None))
                continue
            _fail(f"chave {key} de {table} nao existe")
        if tuple(found) == after:
            continue
        if tuple(found) != before:
            _fail(f"chave {key} de {table} cobre {found}, esperado {list(before)} ou {list(after)}")
        statements.append((rebuild_key_sql(table, key, after), None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: chaves de volta à forma anterior (antes de soltar a
    coluna que elas cobrem), depois `DROP COLUMN` (ordem inversa)."""
    existing, columns, keys = _read(db)
    statements: list[tuple[str, tuple | None]] = []
    for table, key, before, after in UNIQUE_KEYS:
        if tuple(keys.get((table, key)) or ()) == after:
            statements.append((rebuild_key_sql(table, key, before), None))
    for table, *_rest in reversed(TARGETS):
        if table in existing and table in columns:
            statements.append((drop_column_sql(table), None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

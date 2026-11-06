"""Detecção das tabelas de nutrição: plano (migrações 078 e 079, Lote L2b),
catálogo de alimentos (080, com o seed da TACO na 081, Lote L3a, e a coluna de
proveniência por campo da 084, M85), diário (082 e 083, Lote L4) e uso de IA
(085, Lote L5a).

Janela de deploy: o código novo pode atender antes de as migrações rodarem.
Nesse intervalo as rotas de nutrição respondem 503 citando a migração — nunca
500, nunca DDL por aqui (quem cria as tabelas é o pipeline). Mesmo padrão e
mesma lição (PR #182) de `body_measurements_schema.py`: cache SÓ DO POSITIVO,
chaveado por schema ("não existe" é só "a migração ainda não rodou").
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .load_conventions import current_schema

PLANS_TABLE = "nutrition_plans"
TARGETS_TABLE = "nutrition_daily_targets"
TABLES: tuple[str, ...] = (PLANS_TABLE, TARGETS_TABLE)
MIGRATIONS = "078/079"

TABLES_MISSING_MESSAGE = (
    "Plano nutricional indisponível: as tabelas `nutrition_plans` (migração 078) e "
    "`nutrition_daily_targets` (migração 079) ainda não existem neste banco."
)

FOOD_ITEMS_TABLE = "food_items"
FOOD_PORTIONS_TABLE = "food_portions"
FOOD_TABLES: tuple[str, ...] = (FOOD_ITEMS_TABLE, FOOD_PORTIONS_TABLE)
FOOD_MIGRATIONS = "080/081"

FOOD_TABLES_MISSING_MESSAGE = (
    "Catálogo de alimentos indisponível: as tabelas `food_items` e `food_portions` (migração 080, "
    "com a TACO carregada pela 081) ainda não existem neste banco."
)

# Coluna da proveniência por campo (M85): toda leitura de `food_items` a seleciona.
FOOD_FIELD_SOURCES_COLUMN = "field_sources_json"
FOOD_FIELD_SOURCES_MIGRATION = "084"

FOOD_FIELD_SOURCES_MISSING_MESSAGE = (
    "Catálogo de alimentos indisponível: a coluna `food_items.field_sources_json` (migração 084, proveniência "
    "dos valores da TACO em reavaliação) ainda não existe neste banco."
)

# Coluna da base da tabela digitada (104, nutrição D1): toda leitura e escrita de `food_items` a usa.
FOOD_TABLE_BASE_COLUMN = "table_base_amount"
FOOD_TABLE_BASE_MIGRATION = "104"

FOOD_TABLE_BASE_MISSING_MESSAGE = (
    "Catálogo de alimentos indisponível: a coluna `food_items.table_base_amount` (migração 104, base da tabela "
    "digitada pelo usuário) ainda não existe neste banco."
)

# Colunas do modo de gasto adaptativo em `nutrition_plans` (106, Lote C): toda leitura e escrita do plano nas rotas de
# plano as usa. Aditivas: o código ANTIGO sobre o schema novo segue funcionando; o código novo exige a 106 (ordem do
# deploy: migração, depois o código).
PLAN_ENERGY_COLUMNS: tuple[str, ...] = ("energy_params_json", "energy_model_version", "published_preview_json")
PLAN_ENERGY_MIGRATION = "106"

PLAN_ENERGY_MISSING_MESSAGE = (
    "Plano nutricional indisponível: as colunas `energy_params_json`, `energy_model_version` e "
    "`published_preview_json` de `nutrition_plans` (migração 106, modo de gasto adaptativo) ainda não existem neste banco."
)

MEALS_TABLE = "meals"
MEAL_ITEMS_TABLE = "meal_items"
DAYS_TABLE = "nutrition_days"
DIARY_TABLES: tuple[str, ...] = (MEALS_TABLE, MEAL_ITEMS_TABLE, DAYS_TABLE)
DIARY_MIGRATIONS = "082/083"

DIARY_TABLES_MISSING_MESSAGE = (
    "Diário de refeições indisponível: as tabelas `meals` e `meal_items` (migração 082) e "
    "`nutrition_days` (migração 083) ainda não existem neste banco."
)

AI_USAGE_TABLE = "ai_usage_log"
AI_USAGE_TABLES: tuple[str, ...] = (AI_USAGE_TABLE,)
AI_USAGE_MIGRATION = "085"

AI_USAGE_TABLE_MISSING_MESSAGE = (
    "Leitura por IA indisponível: a tabela `ai_usage_log` (migração 085, uso e cota de IA) ainda não "
    "existe neste banco."
)


FAVORITES_TABLE = "user_favorite_foods"
FAVORITES_TABLES: tuple[str, ...] = (FAVORITES_TABLE,)
FAVORITES_MIGRATION = "102"

FAVORITES_TABLE_MISSING_MESSAGE = (
    "Alimentos preferidos indisponíveis: a tabela `user_favorite_foods` (migração 102) ainda não existe neste banco."
)

CHECKINS_TABLE = "nutrition_checkins"
CHECKIN_SETTINGS_TABLE = "nutrition_checkin_settings"
CHECKIN_TABLES: tuple[str, ...] = (CHECKINS_TABLE, CHECKIN_SETTINGS_TABLE)
CHECKIN_MIGRATION = "103"

CHECKIN_TABLES_MISSING_MESSAGE = (
    "Check-in semanal indisponível: as tabelas `nutrition_checkins` e `nutrition_checkin_settings` "
    "(migração 103) ainda não existem neste banco."
)

# Registro de execuções do sync do Intervals (107, Lote F): ESCRITO pelo sync e LIDO pela confirmação do dia. Aditiva: o
# código tolera a ausência (o sync não falha, só não registra; a leitura cai na regra antiga).
SYNC_RUNS_TABLE = "intervals_sync_runs"
SYNC_RUNS_TABLES: tuple[str, ...] = (SYNC_RUNS_TABLE,)
SYNC_RUNS_MIGRATION = "107"

# Fechamento manual do dia e correção manual de atividades (108, Lote G): aditivas; sem as tabelas não há fechamentos
# nem correções (leitura) e as rotas de escrita respondem 503 citando a migração; nenhuma leitura cai por isso.
DAY_CLOSURES_TABLE = "nutrition_day_closures"
ACTIVITY_CORRECTIONS_TABLE = "activity_energy_corrections"
ADJUSTMENT_TABLES: tuple[str, ...] = (DAY_CLOSURES_TABLE, ACTIVITY_CORRECTIONS_TABLE)
ADJUSTMENT_MIGRATION = "108"
ADJUSTMENT_TABLES_MISSING_MESSAGE = (
    "Fechamento do dia e correção de atividades indisponíveis: as tabelas `nutrition_day_closures` e "
    "`activity_energy_corrections` (migração 108) ainda não existem neste banco."
)


def tables_sql(count: int) -> str:
    """COUNT das tabelas pedidas no schema atual (um `%s` por tabela)."""
    return (
        "SELECT COUNT(*) AS n FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({', '.join(['%s'] * count)})"
    )


def column_sql() -> str:
    """COUNT da coluna `(tabela, coluna)` no schema atual."""
    return (
        "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s"
    )


# (schema, tabelas) que já existem; só o positivo vai para o cache.
_present: set[tuple[str, tuple[str, ...]]] = set()
# (schema, tabela, coluna) que já existem; idem.
_present_columns: set[tuple[str, str, str]] = set()


def _has_tables(cursor: Any, tables: tuple[str, ...]) -> bool:
    schema = current_schema(cursor)
    if (schema, tables) in _present:
        return True
    cursor.execute(tables_sql(len(tables)), tables)
    row = cursor.fetchone()
    exists = int((row or {}).get("n") or 0) >= len(tables)
    if exists:
        _present.add((schema, tables))
    return exists


def _has_column(cursor: Any, table: str, column: str) -> bool:
    chave = (current_schema(cursor), table, column)
    if chave in _present_columns:
        return True
    cursor.execute(column_sql(), (table, column))
    row = cursor.fetchone()
    exists = int((row or {}).get("n") or 0) >= 1
    if exists:
        _present_columns.add(chave)
    return exists


def has_nutrition_tables(cursor: Any) -> bool:
    """As duas tabelas do plano existem NESTE schema? Sem DDL."""
    return _has_tables(cursor, TABLES)


def tables_missing_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": "nutrition_tables_missing", "migration": MIGRATIONS, "message": TABLES_MISSING_MESSAGE},
    )


def require_nutrition_tables(cursor: Any) -> None:
    """503 citando as migrações quando as tabelas ainda não existem."""
    if not has_nutrition_tables(cursor):
        raise tables_missing_error()


def has_plan_energy_columns(cursor: Any) -> bool:
    """As colunas do modo de gasto adaptativo (106) existem NESTE schema? Sem DDL."""
    return all(_has_column(cursor, PLANS_TABLE, coluna) for coluna in PLAN_ENERGY_COLUMNS)


def plan_energy_columns_missing_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "nutrition_plan_migration_pending",
            "migration": PLAN_ENERGY_MIGRATION,
            "message": PLAN_ENERGY_MISSING_MESSAGE,
        },
    )


def require_plan_energy_columns(cursor: Any) -> None:
    """503 citando a 106 quando as colunas novas do plano ainda faltam (janela de deploy)."""
    if not has_plan_energy_columns(cursor):
        raise plan_energy_columns_missing_error()


def has_food_tables(cursor: Any) -> bool:
    """As duas tabelas do catálogo existem NESTE schema? Sem DDL."""
    return _has_tables(cursor, FOOD_TABLES)


def food_tables_missing_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": "food_catalog_tables_missing", "migration": FOOD_MIGRATIONS, "message": FOOD_TABLES_MISSING_MESSAGE},
    )


def has_food_field_sources(cursor: Any) -> bool:
    """A coluna da proveniência por campo (084) existe NESTE schema? Sem DDL."""
    return _has_column(cursor, FOOD_ITEMS_TABLE, FOOD_FIELD_SOURCES_COLUMN)


def food_field_sources_missing_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "food_catalog_migration_pending",
            "migration": FOOD_FIELD_SOURCES_MIGRATION,
            "message": FOOD_FIELD_SOURCES_MISSING_MESSAGE,
        },
    )


def has_food_table_base(cursor: Any) -> bool:
    """A coluna da base da tabela (104) existe NESTE schema? Sem DDL."""
    return _has_column(cursor, FOOD_ITEMS_TABLE, FOOD_TABLE_BASE_COLUMN)


def food_table_base_missing_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "food_catalog_migration_pending",
            "migration": FOOD_TABLE_BASE_MIGRATION,
            "message": FOOD_TABLE_BASE_MISSING_MESSAGE,
        },
    )


def require_food_tables(cursor: Any) -> None:
    """503 citando a 080/081 quando o catálogo ainda não existe, ou a 084
    quando a coluna da proveniência (que toda leitura seleciona) ainda falta, ou a 104 quando falta a
    base da tabela."""
    if not has_food_tables(cursor):
        raise food_tables_missing_error()
    if not has_food_field_sources(cursor):
        raise food_field_sources_missing_error()
    if not has_food_table_base(cursor):
        raise food_table_base_missing_error()


def has_diary_tables(cursor: Any) -> bool:
    """As três tabelas do diário existem NESTE schema? Sem DDL."""
    return _has_tables(cursor, DIARY_TABLES)


def diary_tables_missing_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": "nutrition_diary_tables_missing", "migration": DIARY_MIGRATIONS,
                "message": DIARY_TABLES_MISSING_MESSAGE},
    )


def require_diary_tables(cursor: Any) -> None:
    """503 citando a 082/083 quando o diário ainda não existe."""
    if not has_diary_tables(cursor):
        raise diary_tables_missing_error()


def has_ai_usage_table(cursor: Any) -> bool:
    """A tabela de uso de IA (085) existe NESTE schema? Sem DDL."""
    return _has_tables(cursor, AI_USAGE_TABLES)


def ai_usage_table_missing_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": "ai_usage_table_missing", "migration": AI_USAGE_MIGRATION,
                "message": AI_USAGE_TABLE_MISSING_MESSAGE},
    )


def require_ai_usage_table(cursor: Any) -> None:
    """503 citando a 085 quando o `ai_usage_log` ainda não existe."""
    if not has_ai_usage_table(cursor):
        raise ai_usage_table_missing_error()


def has_favorites_table(cursor: Any) -> bool:
    """A tabela dos alimentos preferidos (102) existe NESTE schema? Sem DDL."""
    return _has_tables(cursor, FAVORITES_TABLES)


def favorites_table_missing_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": "favorite_foods_table_missing", "migration": FAVORITES_MIGRATION,
                "message": FAVORITES_TABLE_MISSING_MESSAGE},
    )


def require_favorites_table(cursor: Any) -> None:
    """503 citando a 102 quando `user_favorite_foods` ainda não existe."""
    if not has_favorites_table(cursor):
        raise favorites_table_missing_error()


def has_checkin_tables(cursor: Any) -> bool:
    """As tabelas do check-in semanal (103) existem NESTE schema? Sem DDL."""
    return _has_tables(cursor, CHECKIN_TABLES)


def checkin_tables_missing_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": "nutrition_checkin_tables_missing", "migration": CHECKIN_MIGRATION,
                "message": CHECKIN_TABLES_MISSING_MESSAGE},
    )


def require_checkin_tables(cursor: Any) -> None:
    """503 citando a 103 quando as tabelas do check-in semanal ainda não existem."""
    if not has_checkin_tables(cursor):
        raise checkin_tables_missing_error()


def has_sync_runs_table(cursor: Any) -> bool:
    """O registro de execuções do sync (107) existe NESTE schema? Sem DDL; cache só do positivo."""
    return _has_tables(cursor, SYNC_RUNS_TABLES)


def has_adjustment_tables(cursor: Any) -> bool:
    """Fechamento do dia e correção de atividades (108) existem NESTE schema? Sem DDL; cache só do positivo."""
    return _has_tables(cursor, ADJUSTMENT_TABLES)


def adjustment_tables_missing_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "nutrition_adjustment_tables_missing",
            "migration": ADJUSTMENT_MIGRATION,
            "message": ADJUSTMENT_TABLES_MISSING_MESSAGE,
        },
    )


def require_adjustment_tables(cursor: Any) -> None:
    if not has_adjustment_tables(cursor):
        raise adjustment_tables_missing_error()


def reset_nutrition_tables_cache() -> None:
    """Esquece a detecção (só para testes que desfazem as migrações)."""
    _present.clear()
    _present_columns.clear()


__all__ = [
    "AI_USAGE_MIGRATION",
    "CHECKINS_TABLE",
    "CHECKIN_MIGRATION",
    "CHECKIN_SETTINGS_TABLE",
    "CHECKIN_TABLES",
    "checkin_tables_missing_error",
    "has_checkin_tables",
    "require_checkin_tables",
    "AI_USAGE_TABLE",
    "DIARY_MIGRATIONS",
    "DIARY_TABLES",
    "FAVORITES_MIGRATION",
    "FAVORITES_TABLE",
    "FOOD_FIELD_SOURCES_COLUMN",
    "FOOD_FIELD_SOURCES_MIGRATION",
    "FOOD_MIGRATIONS",
    "FOOD_TABLES",
    "MIGRATIONS",
    "PLANS_TABLE",
    "TARGETS_TABLE",
    "ai_usage_table_missing_error",
    "diary_tables_missing_error",
    "favorites_table_missing_error",
    "food_field_sources_missing_error",
    "food_tables_missing_error",
    "has_ai_usage_table",
    "has_diary_tables",
    "has_favorites_table",
    "has_food_field_sources",
    "has_food_tables",
    "has_nutrition_tables",
    "has_sync_runs_table",
    "ACTIVITY_CORRECTIONS_TABLE",
    "ADJUSTMENT_MIGRATION",
    "ADJUSTMENT_TABLES",
    "DAY_CLOSURES_TABLE",
    "adjustment_tables_missing_error",
    "has_adjustment_tables",
    "require_adjustment_tables",
    "SYNC_RUNS_MIGRATION",
    "SYNC_RUNS_TABLE",
    "require_ai_usage_table",
    "require_diary_tables",
    "require_favorites_table",
    "require_food_tables",
    "require_nutrition_tables",
    "reset_nutrition_tables_cache",
    "tables_missing_error",
]

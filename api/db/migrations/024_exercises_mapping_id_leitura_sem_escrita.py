"""024 - `exercises.mapping_id` sem escrita no caminho de leitura.

Ate aqui, cada GET de /analytics/muscle-groups-mapped-{trend,table,health,
debug} (Grupos musculares, Dashboard, Insights) rodava:

- `ensure_canonical_columns()`: `CREATE INDEX idx_exercises_mapping_id` (erro
  "ja existe" engolido) - DDL, commit implicito no MySQL;
- `reclassify_exercises(only_missing=True)`: `UPDATE exercises SET
  mapping_id=...` para cada exercicio sem vinculo, inclusive os que
  continuavam sem correspondencia (`SET mapping_id=NULL`, no-op).

As duas coisas sairam do runtime. Esta migracao cobre o que elas garantiam:

1. Indice: `001_schema.sql` (baseline) ja cria `idx_exercises_mapping_id`.
   Checa via `information_schema.STATISTICS` se existe QUALQUER indice
   comecando por `mapping_id` e so cria se faltar - em bases que seguem o
   baseline (Aurora e dev) sao 0 statements. A coluna nao e criada as cegas:
   sem ela a aplicacao inteira ja estaria quebrada, entao a migracao FALHA.
2. Backfill UNICO: a mesma `reclassify_exercises(only_missing=True)` que a
   leitura rodava, na conexao do runner (`db.query`/`db.execute`). Emite
   UPDATE so para exercicio sem vinculo que casa com o catalogo atual (ex.:
   catalogo alterado depois da ultima leitura). Exercicio que continua sem
   correspondencia nao gera escrita. Daqui para frente o vinculo e mantido na
   escrita (`resolve_mapping_id` nas sessoes/importacao e
   `reclassify_after_catalog_change` nas rotas do catalogo).

Data API: sem PREPARE/multi-statement; cada UPDATE vai numa chamada. Em
dry-run o runner so lista os statements planejados. Reexecutavel: uma segunda
execucao nao emite nenhum statement.
"""

INDEX_NAME = "idx_exercises_mapping_id"

# Sem teto pratico: a leitura e paginada por id (RECLASSIFY_PAGE_SIZE).
BACKFILL_LIMIT = 10**9


class ExercisesMappingIdMigrationError(Exception):
    """Pre-condicao nao atendida; nenhum statement desta execucao foi aplicado."""


def _has_leading_mapping_index(db):
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercises' "
        "AND COLUMN_NAME = 'mapping_id' AND SEQ_IN_INDEX = 1"
    )
    return int((row or {}).get("n") or 0) > 0


def plan_index(db):
    if not db.table_exists("exercises") or not db.column_exists("exercises", "mapping_id"):
        raise ExercisesMappingIdMigrationError(
            "024: exercises.mapping_id nao existe (esperado pelo baseline 001_schema.sql). Nenhum statement executado."
        )
    if _has_leading_mapping_index(db):
        return []
    if db.index_exists("exercises", INDEX_NAME):
        raise ExercisesMappingIdMigrationError(
            f"024: exercises ja tem um indice {INDEX_NAME} que nao comeca por mapping_id. Nenhum statement executado."
        )
    return [f"ALTER TABLE exercises ADD INDEX {INDEX_NAME} (mapping_id)"]


def up(db):
    # Import tardio: so quando a migracao de fato roda (a imagem da Lambda
    # musclefit-migrations e a mesma de api/Dockerfile.ai, com `app/`).
    from app.exercise_mapping_canonical import reclassify_exercises

    for statement in plan_index(db):
        db.execute(statement)

    reclassify_exercises(
        limit=BACKFILL_LIMIT,
        only_missing=True,
        query_fn=db.query,
        execute_fn=db.execute,
    )

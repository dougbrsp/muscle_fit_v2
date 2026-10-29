"""Sync helpers that recompute `gym_equipment` (a materialized cache) from
the two source-of-truth tables:

- `equipment_exercises` (fonte canonica desde o PR-05; sucessora de
  `equipment_exercise_mappings`, mesmos valores numericos via
  exercise_id/mapping_id): equipment -> exercise, GLOBAL (independente de
  academia).
- `gym_equipment_catalog`: gym -> equipment (which equipment a gym has).

`gym_equipment` itself keeps its exact schema/FKs/unique key so that
`/gym-equipment/recommend`, `/gym-equipment/recommend-all`,
`workout_plan_ai.py` and the FKs `exercises.equipment_id` /
`workout_plan_exercises.preferred_equipment_id` (which reference
`gym_equipment.id`, not `equipment.id`) keep working unchanged.

These functions NEVER delete rows from `gym_equipment` — only flip
`is_active` — because historical rows are referenced by id from
`exercises.equipment_id` / `workout_plan_exercises.preferred_equipment_id`.

FK de `gym_equipment.mapping_id`:
O caminho esperado e a FK referenciar `exercise_catalog(id)`: a migracao
`023_mapping_fks_exercise_catalog.py` reaponta a FK legada do 001_schema.sql
(`fk_gym_equipment_mapping` -> `exercise_zepp_mappings`) e o schema documentado
da Aurora ja aponta para o catalogo. Nesse caso o sync nao aplica filtro.

Protecao mantida (ambiente onde a 023 ainda nao rodou, ex.: schema de testes
so com 001..021): com a FK apontando para outra tabela, um exercicio que so
existe no catalogo canonico faria o INSERT...SELECT inteiro falhar (e, como
roda na mesma transacao da escrita que disparou o sync, derrubaria a request
com 500 + rollback). Por isso o sync descobre em runtime (via
`information_schema`; so o estado CANONICO e cacheado, e por schema - ver
`_canonical_fk_schemas`) para qual tabela a FK aponta:
- `exercise_catalog` (ou sem FK): nenhum filtro.
- qualquer outra tabela (ex.: `exercise_zepp_mappings`): o INSERT so
  materializa exercicios que existem nela; os excluidos geram warning
  estruturado `gym_equipment_sync.fk_legacy_excluded` (sinal de que a 023 nao
  foi aplicada nesse banco) e sao devolvidos ao chamador.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .load_conventions import current_schema

logger = logging.getLogger(__name__)

CANONICAL_EXERCISE_TABLE = "exercise_catalog"
FK_LEGACY_EXCLUDED_CODE = "gym_equipment_sync.fk_legacy_excluded"
REMEDIATION_MIGRATION = "023_mapping_fks_exercise_catalog.py"
MAX_LOGGED_IDS = 100

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")

# Cache SO DO ESTADO CANONICO (migracao 023 aplicada), chaveado por SCHEMA.
#
# Mesma regra que o PR #182 fixou em `load_conventions.py`, e pelas duas
# mesmas razoes - este cache tinha os dois defeitos de uma vez (M18):
#
# 1. Memorizava a AUSENCIA. O valor guardado era o alvo da FK "como estava
#    quando o processo perguntou", inclusive `None` ("nao ha FK") e o alvo
#    LEGADO (`exercise_zepp_mappings`). Esses dois sao estados TRANSITORIOS:
#    significam apenas "a 023 ainda nao rodou neste banco". Um container que
#    atendesse UMA requisicao na janela entre o deploy e a migracao ficava
#    preso nesse "nao" ate reciclar - e no caminho de LEITURA o sintoma e
#    SILENCIOSO: o filtro legado continua excluindo do `gym_equipment`
#    exercicios que ja estao perfeitamente validos no banco, com um warning
#    que parece ruido, sem nenhum erro que denuncie. So o estado canonico
#    (`exercise_catalog`) e permanente: as migracoes deste projeto sao
#    aditivas e nada reaponta a FK de volta.
# 2. Era por PROCESSO, sem chave por schema. Em producao um processo so fala
#    com um schema, mas a suite de integracao cria um
#    `musclefit_run<token>_<tag>_test` por ARQUIVO e roda tudo no MESMO
#    processo - uns com a 023 aplicada, `test_migration_023_mapping_fks.py`
#    deliberadamente num estado anterior a ela. O cache vazava de um schema
#    para o outro e o teste so falhava DENTRO da suite, nunca isolado.
#
# Custo deliberado: enquanto a deteccao nao der canonica, a consulta a
# `information_schema` se repete a cada chamada. E temporario (dura ate a 023
# rodar), acontece so no caminho de ESCRITA (sync de academia) e e o preco de
# nunca ficar preso num "nao existe" falso.
_canonical_fk_schemas: Dict[str, set[Tuple[str, str]]] = {}


def _row_value(row: Any, key: str) -> Any:
    return row[key] if isinstance(row, dict) else row[0]


def resolve_fk_referenced_table(cursor: Any, table: str, column: str) -> Optional[str]:
    """Tabela referenciada pela FK de `table.column` no schema atual, ou None
    se nao houver FK.

    Cacheia SO o estado canonico (`exercise_catalog`, migracao 023) e SO para
    o schema em que ele foi observado - ver o comentario de
    `_canonical_fk_schemas`. Qualquer outra resposta e reconsultada na chamada
    seguinte, porque ela significa "a 023 ainda nao rodou", nunca "nunca vai
    rodar"."""
    schema = current_schema(cursor)
    key = (table, column)
    if key in _canonical_fk_schemas.get(schema, ()):
        return CANONICAL_EXERCISE_TABLE

    cursor.execute(
        """
        SELECT REFERENCED_TABLE_NAME AS ref FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
          AND COLUMN_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL
        LIMIT 1
        """,
        (table, column),
    )
    row = cursor.fetchone()
    referenced = _row_value(row, "ref") if row else None
    if referenced == CANONICAL_EXERCISE_TABLE:
        _canonical_fk_schemas.setdefault(schema, set()).add(key)
    return referenced


def reset_fk_ref_cache() -> None:
    """Esquece a deteccao de TODOS os schemas. Como so o estado canonico e
    cacheado, isto so importa no caminho inverso - um teste que DESFAZ a 023 e
    devolve a FK ao alvo legado (o que `test_migration_023_mapping_fks.py`
    faz entre as duas metades do teste)."""
    _canonical_fk_schemas.clear()


def fk_filter_table(referenced_table: Optional[str]) -> Optional[str]:
    """Logica pura: dada a tabela referenciada pela FK de
    `gym_equipment.mapping_id`, devolve a tabela contra a qual o INSERT deve
    ser restrito (ou None = sem filtro). Valida o identificador, ja que ele e
    interpolado no SQL."""
    if referenced_table is None or referenced_table == CANONICAL_EXERCISE_TABLE:
        return None
    if not _SAFE_IDENTIFIER.match(referenced_table):
        raise ValueError(f"nome de tabela referenciada inesperado: {referenced_table!r}")
    return referenced_table


def build_fk_excluded_warning(gym_id: int, referenced_table: str, excluded_ids: Sequence[int]) -> Dict[str, Any]:
    """Logica pura: payload do aviso de exercicios nao materializados."""
    ids = sorted({int(value) for value in excluded_ids})
    return {
        "code": FK_LEGACY_EXCLUDED_CODE,
        "gym_id": gym_id,
        "fk_referenced_table": referenced_table,
        "excluded_count": len(ids),
        "excluded_exercise_ids": ids[:MAX_LOGGED_IDS],
        "excluded_ids_truncated": len(ids) > MAX_LOGGED_IDS,
    }


def sync_gym_equipment_for_gym(conn: Any, gym_id: int) -> List[Dict[str, Any]]:
    """Recompute all `gym_equipment` rows for a single gym, from the cross of
    `gym_equipment_catalog` (equipment the gym has) x
    `equipment_exercises` (what each equipment serves; fonte canonica desde o
    PR-05 - `equipment_exercise_mappings` e so espelho legado). Idempotent —
    safe to call repeatedly for the same gym_id.

    Retorna a lista de avisos (vazia no caso normal); ver "FK legada" no
    docstring do modulo.
    """
    warnings: List[Dict[str, Any]] = []

    with conn.cursor() as cursor:
        filter_table = fk_filter_table(resolve_fk_referenced_table(cursor, "gym_equipment", "mapping_id"))

        fk_filter_sql = ""
        if filter_table:
            cursor.execute(
                f"""
                SELECT DISTINCT eem.exercise_id AS exercise_id
                FROM gym_equipment_catalog gec
                JOIN equipment_exercises eem
                  ON eem.equipment_id = gec.equipment_id AND eem.is_active = 1
                WHERE gec.gym_id = %s AND gec.is_active = 1
                  AND NOT EXISTS (SELECT 1 FROM `{filter_table}` fkt WHERE fkt.id = eem.exercise_id)
                ORDER BY eem.exercise_id
                """,
                (gym_id,),
            )
            excluded = [int(_row_value(row, "exercise_id")) for row in cursor.fetchall()]
            if excluded:
                warning = build_fk_excluded_warning(gym_id, filter_table, excluded)
                logger.warning(
                    FK_LEGACY_EXCLUDED_CODE,
                    extra={
                        "event": FK_LEGACY_EXCLUDED_CODE,
                        **warning,
                        # caminho esperado: FK canonica (migracao 023); este aviso indica banco sem a 023
                        "expected_fk_referenced_table": CANONICAL_EXERCISE_TABLE,
                        "remediation_migration": REMEDIATION_MIGRATION,
                    },
                )
                warnings.append(warning)
            fk_filter_sql = f"AND EXISTS (SELECT 1 FROM `{filter_table}` fkt WHERE fkt.id = eem.exercise_id)"

        cursor.execute(
            f"""
            INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id, is_active)
            SELECT gec.gym_id, eem.exercise_id, gec.equipment_id, 1
            FROM gym_equipment_catalog gec
            JOIN equipment_exercises eem
              ON eem.equipment_id = gec.equipment_id AND eem.is_active = 1
            WHERE gec.gym_id = %s AND gec.is_active = 1
              {fk_filter_sql}
            ON DUPLICATE KEY UPDATE is_active = 1
            """,
            (gym_id,),
        )

        # Desativacao sem filtro: exercicios excluidos pela FK nunca chegaram a
        # existir em gym_equipment, entao nao ha o que desativar para eles.
        cursor.execute(
            """
            UPDATE gym_equipment ge
            LEFT JOIN (
                SELECT gec.gym_id, eem.exercise_id AS mapping_id, gec.equipment_id
                FROM gym_equipment_catalog gec
                JOIN equipment_exercises eem
                  ON eem.equipment_id = gec.equipment_id AND eem.is_active = 1
                WHERE gec.gym_id = %s AND gec.is_active = 1
            ) valid
              ON valid.gym_id = ge.gym_id
             AND valid.mapping_id = ge.mapping_id
             AND valid.equipment_id = ge.equipment_id
            SET ge.is_active = 0
            WHERE ge.gym_id = %s AND valid.gym_id IS NULL AND ge.is_active = 1
            """,
            (gym_id, gym_id),
        )

    return warnings


def sync_gym_equipment_for_equipment(conn: Any, equipment_id: int) -> List[Dict[str, Any]]:
    """Resolve every gym that currently has this equipment active in
    `gym_equipment_catalog` and re-sync `gym_equipment` for each of them.
    Call this after changing `equipment_exercises` for a given equipment,
    since that change can affect every gym that owns it. Retorna os avisos
    agregados de todas as academias.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT gym_id FROM gym_equipment_catalog WHERE equipment_id = %s AND is_active = 1",
            (equipment_id,),
        )
        rows = cursor.fetchall()
        gym_ids = [_row_value(row, "gym_id") for row in rows]

    warnings: List[Dict[str, Any]] = []
    for gym_id in gym_ids:
        warnings.extend(sync_gym_equipment_for_gym(conn, gym_id))
    return warnings

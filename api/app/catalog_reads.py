"""Leituras de catalogo compartilhadas na mesma requisicao (PERF-04).

`/exercises/suggested-progression` (e toda prescricao por par) lia as MESMAS
linhas duas vezes, so com colunas diferentes:

- `advanced_techniques` ativas: `domain/training_safety.load_safety_inputs_bulk`
  (`code, failure_requirement`) e `progression_state.load_technique_catalog`
  (catalogo completo do motor);
- `equipment WHERE id IN (...)`: `training_safety` (`max_safe_rpe`) e
  `progression_state.load_equipment_steps` (`load_increment_kg`).

Aqui fica UMA leitura de cada, com a uniao das colunas, memoizada por
requisicao GET (`request_memo`). Cada consumidor continua montando o proprio
valor a partir das linhas cruas - nenhuma regra mora aqui, so o acesso a dados
(as regras seguem nos donos: `docs/motores/FONTES_DA_VERDADE.md`). Fora de uma
requisicao GET nada e memoizado: cada chamada le o banco, como antes.
"""

from __future__ import annotations

from typing import Any, Iterable

from . import request_memo

ADVANCED_TECHNIQUES_MEMO = "advanced_techniques_active"
EQUIPMENT_ROWS_MEMO = "equipment_rows"

ACTIVE_TECHNIQUES_SQL = (
    "SELECT code, failure_requirement, auto_applicable, min_experience, set_type, is_active, "
    "activation_set_equivalent, max_equivalent_per_anchor, default_sets_count "
    "FROM advanced_techniques WHERE is_active = 1"
)


def active_technique_rows(conn: Any) -> list[dict[str, Any]]:
    """Linhas ativas de `advanced_techniques`. Erro do banco (tabela ausente)
    propaga - cada chamador mantem o proprio tratamento - e nao e memoizado."""

    def read() -> list[dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(ACTIVE_TECHNIQUES_SQL)
            return [dict(row) for row in cur.fetchall() or []]

    return request_memo.memoize((ADVANCED_TECHNIQUES_MEMO,), read)


def equipment_rows(conn: Any, equipment_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """`equipment.max_safe_rpe` e `equipment.load_increment_kg` por id (so os
    ids que existem). Ids vazios nao tocam o banco."""
    ids = tuple(sorted({int(value) for value in equipment_ids}))
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))

    def read() -> dict[int, dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, max_safe_rpe, load_increment_kg FROM equipment WHERE id IN ({placeholders})",
                ids,
            )
            return {int(row["id"]): dict(row) for row in cur.fetchall() or []}

    return request_memo.memoize((EQUIPMENT_ROWS_MEMO, ids), read)


__all__ = [
    "ACTIVE_TECHNIQUES_SQL",
    "ADVANCED_TECHNIQUES_MEMO",
    "EQUIPMENT_ROWS_MEMO",
    "active_technique_rows",
    "equipment_rows",
]

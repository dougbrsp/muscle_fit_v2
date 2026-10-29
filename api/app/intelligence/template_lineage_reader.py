"""Leitura da linhagem dos Modelos de Treino (`copia -> origem`, migracao 090).

So traz os ids: a regra (subir ate a raiz, ciclo, teto de profundidade) mora no
dono `intelligence/slot_session_comparison.py` (`resolve_lineage_roots`), mapa
`docs/motores/FONTES_DA_VERDADE.md` §3.5. Nenhum consumidor percorre a cadeia
em SQL nem em outra camada (guarda `linhagem_modelo_fora_do_dono`).

Um statement, sem parametro: so os Modelos que TEM origem (a tabela de Modelos
e pequena, dezenas de linhas; as copias, menos ainda). Inclui os excluidos
logicamente (065): a sessao antiga de um Modelo apagado continua na linhagem.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Optional

from .slot_session_comparison import resolve_lineage_roots

TEMPLATE_LINEAGE_SQL = """
SELECT id AS template_id, source_template_id
FROM workout_plan_templates
WHERE source_template_id IS NOT NULL
"""


def parents_from_rows(rows: Iterable[Mapping[str, Any]]) -> dict[int, int]:
    """`template_id -> source_template_id` das linhas de `TEMPLATE_LINEAGE_SQL`."""
    return {
        int(row["template_id"]): int(row["source_template_id"])
        for row in rows
        if row.get("template_id") is not None and row.get("source_template_id") is not None
    }


def load_lineage_roots(
    fetch_rows: Callable[[str, tuple], Iterable[Mapping[str, Any]]],
    template_ids: Iterable[Optional[int]],
) -> dict[int, int]:
    """`template_id -> raiz da linhagem` dos Modelos pedidos. `fetch_rows(sql,
    params)` e o executor de quem chama (cursor da rota ou `fetch_all` do
    servico). Sem Modelo pedido, nao consulta."""
    ids = {int(template_id) for template_id in template_ids if template_id is not None}
    if not ids:
        return {}
    parents = parents_from_rows(fetch_rows(TEMPLATE_LINEAGE_SQL, ()))
    return resolve_lineage_roots(ids, parents)

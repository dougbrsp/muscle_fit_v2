"""Item de "exercicio livre" do Modelo Modo B no volume PLANEJADO (M101b/M101c).

Dono unico (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.5) de duas coisas:

1. O que E um item livre: `item_kind='equipment'` com o grupo preenchido e
   SEM par (`mapping_id` e `equipment_id` vazios) - "o motor escolhe o
   exercicio" (`generation/mode_b._resolve_free_exercise`). O exercicio so
   existe na ficha gerada.
2. Como ele conta no planejado do Modelo: as series planejadas do item
   (`technique_volume.planned_item_sets`, com a tecnica) no grupo PRIMARIO do
   item, com o peso `FREE_EXERCISE_PRIMARY_WEIGHT` - a mesma regra do item
   `muscle_group` do Modo A. Sem SECONDARY/estabilizador: nao ha exercicio
   para ler as contribuicoes.

Consumidores (nenhum refaz a regra): a previa do editor
(`workout_plan_templates._compute_slot_activation_equipment_series`,
detalhamento por item e fadiga previa), a metodologia de volume
(`methodology_volume_service._item_input` -> `MethodologyItemInput.free_exercise`)
e o planejado do dashboard (`muscle_summary_service.resolve_template_plan`).

Python puro: aceita o Pydantic do builder ou o dict de `_get_template`/da
leitura do dashboard.
"""

from __future__ import annotations

from typing import Any, Mapping

# Peso do item livre no grupo primario do item: o de um item de grupo do
# Modo A (primario puro), em qualquer modo de contagem.
FREE_EXERCISE_PRIMARY_WEIGHT = 1.0


def _field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def is_free_exercise_item(item: Any, *, require_kind: bool = True) -> bool:
    """Item do Modo B com grupo e SEM par (exercicio livre).

    `require_kind=False`: o chamador ja so tem itens `equipment` do Modo B
    (a geracao e o congelamento, `generation/freeze_mode_b`, que recebem os
    itens sem o `item_kind`)."""
    return (
        (not require_kind or _field(item, "item_kind") == "equipment")
        and not _field(item, "mapping_id")
        and not _field(item, "equipment_id")
        and bool(_field(item, "muscle_group_id"))
    )


__all__ = ["FREE_EXERCISE_PRIMARY_WEIGHT", "is_free_exercise_item"]

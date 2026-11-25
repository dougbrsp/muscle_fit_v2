"""Prévia de redundância do editor manual de Modelo (issue #288, fatia 2).

    POST /workout-plan-templates/redundancy-preview   modelos.treino/editar (a mesma do editor)

    GET  /workout-plan-templates/{id}/redundancy-diagnostic   modelos.treino/ver (a mesma da leitura do Modelo)

Só orquestra: o nível sai de `domain/exercise_redundancy` (dono), a contribuição muscular de
`intelligence/muscle_contribution_resolver` (reduzida a grupo -> papel, sem recalcular pesos) e o perfil de resistência
da tabela `equipment_exercise_resistance_profiles` (validado pelo dono `resistance_profile` dentro de `compare`).
`equipment_id` do item é `equipment.id` (par `equipment_exercises` = equipment_id + exercise_id). Em lote: 1 leitura do
catálogo, 2 do resolvedor, 1 de perfis, qualquer que seja o número de itens. Sem a migração 126, perfis = None (sem 503).
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .access import require_permission
from .db import get_conn
from .db_helpers import connect_or_503
from .domain import exercise_redundancy as red
from . import workout_plan_templates as wpt
from .intelligence.redundancy_candidates import load_candidates, load_candidates_with_names
from .request_models import RedundancyPreviewIn

router = APIRouter(prefix="/workout-plan-templates", tags=["workout-plan-templates"])


@router.post(
    "/redundancy-preview",
    dependencies=[Depends(require_permission("modelos.treino", "editar", min_scope="alunos"))],
)
def redundancy_preview(payload: RedundancyPreviewIn) -> dict[str, Any]:
    if payload.min_level is not None and payload.min_level not in red.LEVEL_ORDER:
        raise HTTPException(status_code=422, detail={"error": "invalid_min_level", "field": "min_level"})
    items = payload.items
    pairs = [(int(i.mapping_id), int(i.equipment_id) if i.equipment_id else None) for i in items]
    with connect_or_503(get_conn) as conn:
        with conn.cursor() as cur:
            loaded = load_candidates(cur, pairs)
    candidates = [loaded.get(p) for p in pairs]  # fora do catálogo: None, sem par

    target = payload.target_index
    indexes = (
        [(min(target, j), max(target, j)) for j in range(len(items)) if j != target]
        if target is not None
        else list(combinations(range(len(items)), 2))
    )
    floor = payload.min_level or red.DISPLAY_MIN_LEVEL
    out: list[dict[str, Any]] = []
    for a, b in indexes:
        if candidates[a] is None or candidates[b] is None:
            continue
        result = red.compare(candidates[a], candidates[b])
        if not red.at_least(result.level, floor):
            continue
        out.append({
            "a_index": a, "b_index": b,
            "a_mapping_id": int(items[a].mapping_id), "b_mapping_id": int(items[b].mapping_id),
            **red.to_dict(result),
        })
    out.sort(key=lambda r: (-red.level_rank(r["level"]), r["a_index"], r["b_index"]))
    return {"items": out}


@router.get("/{template_id}/redundancy-diagnostic")
def redundancy_diagnostic(
    template_id: int,
    min_level: str | None = None,
    viewer: wpt.TemplateViewer = Depends(wpt.template_guard("ver", for_write=False)),
) -> dict[str, Any]:
    """Somente leitura: pares redundantes dentro de cada dia/slot da versão vigente (posse = a da leitura do Modelo)."""
    if min_level is not None and min_level not in red.LEVEL_ORDER:
        raise HTTPException(status_code=422, detail={"error": "invalid_min_level", "field": "min_level"})
    # Acoplamento: sem equivalente público em workout_plan_templates (get_template é a rota); mesma leitura do GET do Modelo.
    template = wpt._get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")
    slots = [
        (slot, [i for i in slot.get("items") or [] if i.get("mapping_id")]) for slot in template.get("slots") or []
    ]
    pairs = [
        (int(i["mapping_id"]), int(i["equipment_id"]) if i.get("equipment_id") else None)
        for _, its in slots
        for i in its
    ]
    loaded: dict[Any, red.Candidate] = {}
    names: dict[int, str] = {}
    if pairs:
        with connect_or_503(get_conn) as conn:
            with conn.cursor() as cur:
                loaded, names = load_candidates_with_names(cur, pairs)
    floor = min_level or red.DISPLAY_MIN_LEVEL
    out_slots: list[dict[str, Any]] = []
    for slot, its in slots:
        found: list[dict[str, Any]] = []
        for a, b in combinations(its, 2):
            ca = loaded.get((int(a["mapping_id"]), int(a["equipment_id"]) if a.get("equipment_id") else None))
            cb = loaded.get((int(b["mapping_id"]), int(b["equipment_id"]) if b.get("equipment_id") else None))
            if ca is None or cb is None:
                continue
            result = red.compare(ca, cb)
            if red.at_least(result.level, floor):
                found.append({
                    "a_item_id": a.get("id"), "b_item_id": b.get("id"),
                    "a_mapping_id": int(a["mapping_id"]), "b_mapping_id": int(b["mapping_id"]),
                    "a_exercise_name": names.get(int(a["mapping_id"])),
                    "b_exercise_name": names.get(int(b["mapping_id"])),
                    **red.to_dict(result),
                })
        found.sort(key=lambda r: -red.level_rank(r["level"]))
        out_slots.append({
            "slot_id": slot.get("id"), "sequence": slot.get("sequence"), "name": slot.get("name"),
            "weekday": slot.get("weekday"), "pairs": found,
        })
    return {"template_id": int(template_id), "slots": out_slots}

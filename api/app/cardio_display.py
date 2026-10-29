"""Cardio prescrito no slot do dia, pronto para EXIBIR (M98 C3b).

O Modo Treino, ao encerrar a força, mostra o cardio que o slot da ficha tem no
Modelo: aquecimento, parte principal (Normal: tempo e zona-alvo; HIIT: séries
× alta/baixa), recuperação, as faixas de bpm de cada zona e o tempo total.
Não há registro nem timer: o realizado vem do Intervals; a execução guiada é
o backlog M98-R.

Nenhuma regra aqui:

- etapas, textos e tempo total são do dono `domain/cardio_prescription`
  (`describe_steps`, `describe`, `describe_short`, `total_seconds`,
  `total_minutes`);
- as faixas de bpm são as zonas resolvidas do atleta
  (`athlete_hr_zones.load_athlete_zones` -> dono `domain/hr_zones`); aqui só
  se procura a zona pelo número. No Modo Treino o atleta é sempre quem está
  logado (a sessão é dele), a mesma regra de `resolve_template_zones_subject`
  para admin e aluno.

O slot é o da ficha (`workout_plans.template_slot_sequence`) na versão
VIGENTE do Modelo (vínculo vivo, como a geração da ficha), lido por
`get_template_if_assigned` (posse do Modelo). Nunca o rascunho.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .domain import cardio_prescription
from .domain.hr_zones import zone_label

logger = logging.getLogger("fitness.cardio_display")


def _zone_index(zones_payload: Optional[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(zone["zone"]): zone for zone in (zones_payload or {}).get("zones") or []}


def prescription_view(item: Any, zones_payload: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Um item de cardio pronto para exibir, com as faixas de bpm do atleta
    em cada etapa (`null` quando o atleta não tem zonas)."""
    index = _zone_index(zones_payload)
    steps = []
    for step in cardio_prescription.describe_steps(item):
        zone_ranges = []
        for zone in step["zones"]:
            resolved = index.get(int(zone))
            zone_ranges.append(
                {
                    "zone": zone,
                    "label": resolved["label"] if resolved else zone_label(zone),
                    "lower_bpm": resolved["lower_bpm"] if resolved else None,
                    "upper_bpm": resolved["upper_bpm"] if resolved else None,
                }
            )
        steps.append({**step, "zones": zone_ranges})
    mode = cardio_prescription.mode_of(item)
    return {
        "mode": mode.value,
        "mode_label": cardio_prescription.MODE_LABELS[mode],
        "text": cardio_prescription.describe(item),
        "short": cardio_prescription.describe_short(item),
        "total_seconds": cardio_prescription.summarize(item)["total_seconds"],
        "total_minutes_label": cardio_prescription.minutes_label(cardio_prescription.total_seconds(item)),
        "steps": steps,
    }


def slot_cardio_for_plan(plan: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Cardio do slot da ficha `plan` (já lida e já do usuário). Ficha sem
    Modelo/slot, Modelo que não é do usuário ou slot sem cardio -> `items`
    vazio (o Modo Treino só não mostra o bloco)."""
    from . import athlete_hr_zones
    from .db import transaction
    from .workout_plan_templates_engine import get_template_if_assigned

    empty = {"workout_plan_id": plan.get("id"), "slot_name": None, "items": [], "zones_source": None}
    template_id = plan.get("template_id")
    sequence = plan.get("template_slot_sequence")
    if template_id is None or sequence is None:
        return empty
    try:
        template = get_template_if_assigned(int(template_id), int(user_id))
    except Exception:  # noqa: BLE001 - só exibição: o fim do treino nunca quebra por isto
        logger.warning("cardio_display: Modelo da ficha indisponível", exc_info=True)
        return empty
    if not template:
        return empty
    slot = next((s for s in template.get("slots") or [] if s.get("sequence") == int(sequence)), None)
    items = [item for item in (slot or {}).get("items") or [] if cardio_prescription.is_cardio_item(item)]
    if not items:
        return {**empty, "slot_name": (slot or {}).get("name")}
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                zones = athlete_hr_zones.zones_payload(athlete_hr_zones.load_athlete_zones(cur, int(user_id)))
    except Exception:  # noqa: BLE001 - sem zonas o cardio ainda aparece (sem bpm)
        logger.warning("cardio_display: zonas do atleta indisponíveis", exc_info=True)
        zones = None
    return {
        "workout_plan_id": plan.get("id"),
        "slot_name": slot.get("name"),
        "items": [prescription_view(item, zones) for item in items],
        "zones_source": (zones or {}).get("source"),
    }

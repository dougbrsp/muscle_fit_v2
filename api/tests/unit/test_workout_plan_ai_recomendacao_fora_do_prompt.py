"""Campos do fabricante (#188) nao entram no contexto do prompt da IA."""

from __future__ import annotations

from app.workout_plan_ai import _trim_gym_equipment_recommendation

FABRICANTE = ("gym_equipment_catalog_id", "manufacturer", "manufacturer_source", "can_edit_manufacturer")


def _item():
    return {
        "id": 1,
        "equipment_name": "Polia",
        "gym_equipment_catalog_id": 9,
        "manufacturer": "Life Fitness",
        "manufacturer_source": "gym",
        "can_edit_manufacturer": True,
        "gym_equipment_id": 1,
    }


def test_trim_tira_campos_do_fabricante_e_mantem_os_demais():
    rec = {
        "gym_id": 3,
        "groups": {"Peitoral": {"has_equipment_for_group": True, "recommended": _item(), "alternatives": [_item()]}},
        "groups_by_id": {"52": {"has_equipment_for_group": True, "recommended": _item(), "alternatives": []}},
    }
    out = _trim_gym_equipment_recommendation(rec)
    itens = [
        out["groups"]["Peitoral"]["recommended"],
        out["groups"]["Peitoral"]["alternatives"][0],
        out["groups_by_id"]["52"]["recommended"],
    ]
    for item in itens:
        assert set(item) == {"id", "equipment_name"}
        assert not set(FABRICANTE) & set(item)

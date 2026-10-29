"""`GET /advanced-techniques` expõe os coeficientes da 060 (Fase 5, PR 3).

O Modo Treino espelha `domain/technique_accounting.py` no cliente e precisa do
teto por âncora e do número de séries padrão vindos do catálogo."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.advanced_techniques import _row_to_item

BASE = {
    "code": "rest_pause",
    "name": "Rest-pause",
    "failure_requirement": "to_failure",
    "requires_partner": 0,
    "auto_applicable": 1,
    "min_experience": "intermediate",
    "set_type": "rest_pause",
    "activation_set_equivalent": Decimal("0.40"),
    "recovery_cost_factor": Decimal("1.10"),
    "is_active": 1,
}


@pytest.mark.parametrize(
    "extra, teto, series",
    [
        ({"max_equivalent_per_anchor": Decimal("1.00"), "default_sets_count": None}, 1.0, None),
        ({"max_equivalent_per_anchor": Decimal("0.00"), "default_sets_count": 7}, 0.0, 7),
        ({"max_equivalent_per_anchor": None, "default_sets_count": None}, None, None),
    ],
)
def test_item_expoe_teto_e_series_padrao_tipados(extra, teto, series):
    item = _row_to_item({**BASE, **extra})
    assert item["max_equivalent_per_anchor"] == teto
    assert item["default_sets_count"] == series
    assert item["activation_set_equivalent"] == 0.4
    assert isinstance(item["activation_set_equivalent"], float)

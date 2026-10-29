"""Rascunho da IA e a grade do editor (migracao 062).

- `days_per_cycle` vai de 1 a 8 (o teto de slots de um Modelo);
- o rascunho sai com a grade sugerida `grid_cols = min(n, 4)`,
  `grid_rows = ceil(n / 4)`, calculada sobre a quantidade FINAL de slots.

Puro: sem banco, sem rede.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import PeriodizationDraftIn, WorkoutPlanTemplateIn
from app.periodization import validation


def _template(n: int) -> WorkoutPlanTemplateIn:
    return WorkoutPlanTemplateIn(
        name="Rascunho",
        slots=[{"sequence": seq, "name": f"D{seq}", "slot_type": "descanso", "items": []} for seq in range(1, n + 1)],
    )


@pytest.mark.parametrize("dias", [1, 8])
def test_days_per_cycle_aceita_de_1_a_8(dias):
    assert PeriodizationDraftIn(days_per_cycle=dias).days_per_cycle == dias


@pytest.mark.parametrize("dias", [0, 9, 14])
def test_days_per_cycle_fora_de_1_a_8_e_recusado(dias):
    with pytest.raises(ValidationError):
        PeriodizationDraftIn(days_per_cycle=dias)


@pytest.mark.parametrize(
    "n, esperado",
    [(1, (1, 1)), (3, (1, 3)), (4, (1, 4)), (5, (2, 4)), (7, (2, 4)), (8, (2, 4))],
)
def test_grade_sugerida_cabe_o_ciclo(n, esperado):
    template = _template(n)
    validation._suggest_grid(template)
    assert (template.grid_rows, template.grid_cols) == esperado
    assert template.grid_rows * template.grid_cols >= n


def test_grade_sugerida_usa_a_quantidade_depois_do_ajuste_de_slots():
    """A LLM devolveu 3 dias, o pedido era 6: o auto-fix completa com descanso
    e a grade acompanha o ciclo final (2 x 4), nao o da LLM (1 x 3)."""
    template = _template(3)
    pedido = PeriodizationDraftIn(volume_mode="percent_budget", days_per_cycle=6)
    validation._apply_structural_auto_fixes(template, pedido, validation.ValidationReport())
    validation._suggest_grid(template)
    assert len(template.slots) == 6
    assert (template.grid_rows, template.grid_cols) == (2, 4)

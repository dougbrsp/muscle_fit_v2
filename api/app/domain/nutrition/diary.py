"""Diário de refeições: vocabulário da refeição e regras de entrada do diário
(Lote L4).

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7 e §52; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão `diario_nutricao`).
Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §6.2, §8.1 e §10.
Nasceu no L4: até ali o diário não tinha dono (o `MealSlot` morava em `labels`,
que continua a reexportá-lo).

- Refeição (`MealSlot`): café da manhã … ceia e "outra"; a ordem do enum é a
  ordem do dia (`meal_sort_key`). Origem (`MealSource`): no L4 só `manual`; o
  parse por IA (L5) e a foto (L10) usam os outros códigos.
- Entrada da refeição (`validate_meal_header`, `item_amount_errors`): slot do
  vocabulário; de 1 a `MEAL_MAX_ITEMS` itens; a quantidade do item precisa
  converter para a base do alimento (`meal_math.resolve_amount`; senão
  `unit_without_conversion` ou `missing_quantity`) e ficar até
  `ITEM_AMOUNT_MAX` na base (a mesma faixa da porção do catálogo,
  `catalog.PORTION_AMOUNT_MAX`: 2 kg ou 2 L num item é erro de digitação).
- Diário do dia (`validate_day_log`): fome na escala `HUNGER_SCALE` (1 a 5) e
  água em `WATER_ML_RANGE` (ml).
- Origem do item (L5b, `item_source_errors`): um alimento do catálogo OU a
  estimativa da IA (`meal_parse`, natureza ESTIMATED), nunca os dois nem nenhum.

Defaults de desenho, SEM fonte bibliográfica: `MEAL_MAX_ITEMS` (uma refeição
real não passa de algumas dezenas de itens; o teto protege o tamanho do lote) e
`WATER_ML_RANGE` (10 L/dia é muito acima de qualquer ingestão plausível).
A escala de fome de 1 a 5 é a escala ordinal de 5 pontos do diário (o rótulo
de cada ponto está em `labels.HUNGER_LABELS`).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Final, Mapping, Optional

from .catalog import PORTION_AMOUNT_MAX
from .meal_math import (
    LIMIT_MISSING_QUANTITY,
    LIMIT_MISSING_PORTION_G,
    LIMIT_UNKNOWN_BASIS,
    LIMIT_UNKNOWN_UNIT,
    AmountResolution,
)


class MealSlot(str, Enum):
    BREAKFAST = "breakfast"
    MORNING_SNACK = "morning_snack"
    LUNCH = "lunch"
    AFTERNOON_SNACK = "afternoon_snack"
    PRE_WORKOUT = "pre_workout"
    POST_WORKOUT = "post_workout"
    DINNER = "dinner"
    SUPPER = "supper"
    OTHER = "other"


class MealSource(str, Enum):
    """Como a refeição entrou no diário (`meals.source`)."""

    MANUAL = "manual"
    AI_TEXT = "ai_text"  # L5
    AI_PHOTO = "ai_photo"  # L10


MEAL_MAX_ITEMS: Final[int] = 30
ITEM_AMOUNT_MAX: Final[float] = PORTION_AMOUNT_MAX
HUNGER_SCALE: Final[tuple[int, int]] = (1, 5)
WATER_ML_RANGE: Final[tuple[float, float]] = (0.0, 10000.0)

# Ordem das refeições no dia = ordem do enum.
MEAL_SLOT_ORDER: Final[Mapping[MealSlot, int]] = {slot: indice for indice, slot in enumerate(MealSlot)}

ERR_INVALID_MEAL_SLOT = "invalid_meal_slot"
ERR_MEAL_WITHOUT_ITEMS = "meal_without_items"
ERR_TOO_MANY_ITEMS = "too_many_meal_items"
ERR_UNIT_WITHOUT_CONVERSION = "unit_without_conversion"
ERR_ITEM_AMOUNT_OUT_OF_RANGE = "item_amount_out_of_range"
ERR_INVALID_HUNGER = "invalid_hunger"
ERR_ITEM_FOOD_OR_ESTIMATE = "item_food_or_estimate_required"
ERR_INVALID_WATER = "invalid_water_ml"

# Limitação da conversão -> erro da entrada (a quantidade ausente fica como está).
_AMOUNT_ERRORS: Final[Mapping[str, str]] = {
    LIMIT_MISSING_QUANTITY: LIMIT_MISSING_QUANTITY,
    LIMIT_UNKNOWN_UNIT: ERR_UNIT_WITHOUT_CONVERSION,
    LIMIT_MISSING_PORTION_G: ERR_UNIT_WITHOUT_CONVERSION,
    LIMIT_UNKNOWN_BASIS: ERR_UNIT_WITHOUT_CONVERSION,
}


def normalize_meal_slot(raw: Any) -> Optional[MealSlot]:
    if isinstance(raw, MealSlot):
        return raw
    if raw is None:
        return None
    try:
        return MealSlot(str(raw).strip().lower())
    except ValueError:
        return None


def meal_sort_key(slot: Any, eaten_at: Any, meal_id: Any) -> tuple[int, str, int]:
    """Ordem das refeições no dia: slot (ordem do enum), hora (sem hora vem
    depois das com hora no mesmo slot) e id."""
    refeicao = normalize_meal_slot(slot)
    posicao = MEAL_SLOT_ORDER[refeicao] if refeicao is not None else len(MEAL_SLOT_ORDER)
    hora = str(eaten_at) if eaten_at is not None else "~"
    return (posicao, hora, int(meal_id or 0))


def validate_meal_header(slot: Any, item_count: int) -> tuple[str, ...]:
    """Erros do cabeçalho da refeição (slot e quantidade de itens)."""
    erros: list[str] = []
    if normalize_meal_slot(slot) is None:
        erros.append(ERR_INVALID_MEAL_SLOT)
    if item_count < 1:
        erros.append(ERR_MEAL_WITHOUT_ITEMS)
    elif item_count > MEAL_MAX_ITEMS:
        erros.append(ERR_TOO_MANY_ITEMS)
    return tuple(erros)


def item_amount_errors(resolution: AmountResolution) -> tuple[str, ...]:
    """Erros da quantidade de UM item: não converte para a base do alimento
    (`unit_without_conversion`, `missing_quantity`) ou passa de `ITEM_AMOUNT_MAX`."""
    if resolution.amount is None:
        erros = [_AMOUNT_ERRORS[c] for c in resolution.limitations if c in _AMOUNT_ERRORS]
        return tuple(dict.fromkeys(erros)) or (ERR_UNIT_WITHOUT_CONVERSION,)
    if resolution.amount > ITEM_AMOUNT_MAX:
        return (ERR_ITEM_AMOUNT_OUT_OF_RANGE,)
    return ()


def item_source_errors(has_food: bool, has_estimate: bool) -> tuple[str, ...]:
    """Origem de UM item (L5b): alimento do catálogo ou estimativa da IA, exatamente um."""
    return () if bool(has_food) != bool(has_estimate) else (ERR_ITEM_FOOD_OR_ESTIMATE,)


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        numero = float(value)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def validate_day_log(water_ml: Any, hunger: Any) -> tuple[str, ...]:
    """Água e fome do dia (`None` = não informado, sempre aceito)."""
    erros: list[str] = []
    if water_ml is not None:
        agua = _finite(water_ml)
        if agua is None or not WATER_ML_RANGE[0] <= agua <= WATER_ML_RANGE[1]:
            erros.append(ERR_INVALID_WATER)
    if hunger is not None:
        fome = _finite(hunger)
        if fome is None or fome != int(fome) or not HUNGER_SCALE[0] <= fome <= HUNGER_SCALE[1]:
            erros.append(ERR_INVALID_HUNGER)
    return tuple(erros)


def hunger_levels() -> tuple[int, ...]:
    return tuple(range(HUNGER_SCALE[0], HUNGER_SCALE[1] + 1))

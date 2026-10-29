"""Diário de refeições (dono `app/domain/nutrition/diary.py`, Lote L4): refeição,
ordem do dia, entrada da refeição e do item, água e fome."""

from __future__ import annotations

import pytest

from app.domain.nutrition import catalog, diary
from app.domain.nutrition import labels as lb
from app.domain.nutrition import meal_math as mm


def test_constantes_e_vocabulario():
    assert diary.MEAL_MAX_ITEMS == 30
    assert diary.ITEM_AMOUNT_MAX == catalog.PORTION_AMOUNT_MAX
    assert diary.HUNGER_SCALE == (1, 5) and diary.hunger_levels() == (1, 2, 3, 4, 5)
    assert diary.WATER_ML_RANGE == (0.0, 10000.0)
    assert [s.value for s in diary.MealSource] == ["manual", "ai_text", "ai_photo"]
    assert lb.MealSlot is diary.MealSlot  # `labels` reexporta o enum que mora no dono do diário
    assert set(lb.HUNGER_LABELS) == set(diary.hunger_levels())


@pytest.mark.parametrize(("bruto", "esperado"), [("lunch", diary.MealSlot.LUNCH), (" DINNER ", diary.MealSlot.DINNER),
                                                  (diary.MealSlot.OTHER, diary.MealSlot.OTHER), ("brunch", None), (None, None)])
def test_normalize_meal_slot(bruto, esperado):
    assert diary.normalize_meal_slot(bruto) == esperado


def test_ordem_do_dia_slot_hora_e_id():
    refeicoes = [("dinner", None, 5), ("breakfast", "08:00", 9), ("lunch", "13:00", 2), ("lunch", "12:00", 7),
                 ("lunch", None, 1), ("estranho", None, 3)]
    ordenadas = sorted(refeicoes, key=lambda r: diary.meal_sort_key(*r))
    assert [r[2] for r in ordenadas] == [9, 7, 2, 1, 5, 3]


@pytest.mark.parametrize(
    ("slot", "itens", "erros"),
    [
        ("lunch", 1, ()),
        ("lunch", diary.MEAL_MAX_ITEMS, ()),
        ("lunch", 0, (diary.ERR_MEAL_WITHOUT_ITEMS,)),
        ("lunch", diary.MEAL_MAX_ITEMS + 1, (diary.ERR_TOO_MANY_ITEMS,)),
        ("brunch", 0, (diary.ERR_INVALID_MEAL_SLOT, diary.ERR_MEAL_WITHOUT_ITEMS)),
    ],
)
def test_validate_meal_header(slot, itens, erros):
    assert diary.validate_meal_header(slot, itens) == erros


@pytest.mark.parametrize(
    ("resolucao", "erros"),
    [
        (mm.resolve_amount(100, "g", "g"), ()),
        (mm.resolve_amount(diary.ITEM_AMOUNT_MAX, "g", "g"), ()),
        (mm.resolve_amount(diary.ITEM_AMOUNT_MAX + 1, "g", "g"), (diary.ERR_ITEM_AMOUNT_OUT_OF_RANGE,)),
        (mm.resolve_amount(10, "xicara", "g"), (diary.ERR_ITEM_AMOUNT_OUT_OF_RANGE,)),  # 2.400 ml
        (mm.resolve_amount(1, "kg", "g"), (diary.ERR_UNIT_WITHOUT_CONVERSION,)),
        (mm.resolve_amount(1, "unit", "g"), (diary.ERR_UNIT_WITHOUT_CONVERSION,)),
        (mm.resolve_amount(1, "g", "kg"), (diary.ERR_UNIT_WITHOUT_CONVERSION,)),
        (mm.resolve_amount(0, "g", "g"), (mm.LIMIT_MISSING_QUANTITY,)),
    ],
)
def test_item_amount_errors(resolucao, erros):
    assert diary.item_amount_errors(resolucao) == erros


@pytest.mark.parametrize(
    ("agua", "fome", "erros"),
    [
        (None, None, ()),
        (0, 1, ()),
        (2500, 5, ()),
        (diary.WATER_ML_RANGE[1], 3.0, ()),
        (-1, None, (diary.ERR_INVALID_WATER,)),
        (diary.WATER_ML_RANGE[1] + 1, None, (diary.ERR_INVALID_WATER,)),
        (float("nan"), None, (diary.ERR_INVALID_WATER,)),
        (None, 0, (diary.ERR_INVALID_HUNGER,)),
        (None, 6, (diary.ERR_INVALID_HUNGER,)),
        (None, 2.5, (diary.ERR_INVALID_HUNGER,)),
        (None, True, (diary.ERR_INVALID_HUNGER,)),
        ("abc", "x", (diary.ERR_INVALID_WATER, diary.ERR_INVALID_HUNGER)),
    ],
)
def test_validate_day_log(agua, fome, erros):
    assert diary.validate_day_log(agua, fome) == erros

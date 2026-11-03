"""Motor dos preferidos (`domain/nutrition/favorites.py`, N3): porção padrão pela
matemática do diário, teto, ordem e posições."""

from __future__ import annotations

import pytest

from app.domain.nutrition import diary, favorites, labels


def test_sem_porcao_padrao_e_valido():
    assert favorites.default_errors(None, None, None, "g") == ()


@pytest.mark.parametrize(
    ("quantidade", "unidade", "porcao"),
    [(1, None, None), (None, "g", None), (None, None, 5)],
)
def test_quantidade_e_unidade_vao_juntas(quantidade, unidade, porcao):
    assert favorites.default_errors(quantidade, unidade, porcao, "g") == (favorites.ERR_DEFAULT_INCOMPLETE,)


def test_porcao_cadastrada_so_com_unidade_de_porcao():
    assert favorites.default_errors(1, "g", 5, "g", portion_amount=45) == (favorites.ERR_DEFAULT_PORTION_UNIT,)
    assert favorites.default_errors(2, "portion", 5, "g", portion_amount=45) == ()
    assert favorites.default_errors(2, "unit", 5, "g", portion_amount=45) == ()


def test_conversao_e_teto_sao_os_do_diario():
    assert favorites.default_errors(150, "g", None, "g") == ()
    assert favorites.default_errors(1, "xicara", None, "g") == ()  # medida caseira, densidade assumida
    # porção do rótulo vale para `portion`, não para `unit`
    assert favorites.default_errors(1, "portion", None, "g", serving_size=30) == ()
    assert favorites.default_errors(1, "unit", None, "g", serving_size=30) == (diary.ERR_UNIT_WITHOUT_CONVERSION,)
    assert favorites.default_errors(diary.ITEM_AMOUNT_MAX + 1, "g", None, "g") == (diary.ERR_ITEM_AMOUNT_OUT_OF_RANGE,)
    assert favorites.default_errors(0, "g", None, "g") != ()
    assert favorites.default_errors(-1, "g", None, "g") != ()
    assert favorites.default_errors(1, "balde", None, "g") != ()


def test_unidade_canonica():
    assert favorites.canonical_unit(" G ") == "g"
    assert favorites.canonical_unit("portion") == "portion"
    assert favorites.canonical_unit("balde") is None


def test_teto_da_lista():
    assert favorites.can_add(favorites.MAX_FAVORITES - 1) is True
    assert favorites.can_add(favorites.MAX_FAVORITES) is False


def test_ordem_precisa_ser_permutacao_exata():
    assert favorites.order_errors([1, 2, 3], [3, 1, 2]) == ()
    for pedido in ([1, 2], [1, 1, 2], [1, 2, 3, 4], []):
        assert favorites.order_errors([1, 2, 3], pedido) == (favorites.ERR_INVALID_ORDER,)
    assert favorites.order_errors([], []) == ()


def test_posicoes_consecutivas_a_partir_de_1():
    assert favorites.positions_for([7, 3, 9]) == [(7, 1), (3, 2), (9, 3)]
    assert favorites.next_position(None) == 1 and favorites.next_position(4) == 5


def test_todo_codigo_de_erro_tem_texto_pt_br():
    for codigo in (favorites.ERR_TOO_MANY_FAVORITES, favorites.ERR_DEFAULT_INCOMPLETE,
                   favorites.ERR_DEFAULT_PORTION_UNIT, favorites.ERR_INVALID_ORDER):
        assert labels.limitation_label(codigo) != codigo

"""Alimentos preferidos do usuario (nutricao N3, mapa `docs/motores/FONTES_DA_VERDADE.md`).

Dono das regras da lista de preferidos: tamanho maximo, a porcao padrao de cada
favorito e a ordem. Python puro, sem I/O. A rota (`app/nutrition_favorites.py`)
so monta o SQL e devolve o que o motor decidiu; a tela so exibe.

Porcao padrao: e um item de refeicao em potencial (quantidade + unidade +
porcao opcional do catalogo). Nao ha conta nova aqui: a conversao para a base do
alimento e a faixa sao as do diario (`meal_math.resolve_amount` e
`diary.item_amount_errors`), a mesma matematica que o diario aplica ao gravar o
item. Quantidade e unidade vao juntas (ou nenhuma: favorito sem porcao padrao).

Visibilidade (quem pode favoritar o que) e de `nutrition_access`; aqui so o
vocabulario de erros e as regras de lista.
"""

from __future__ import annotations

from typing import Any, Final, Optional, Sequence

from . import diary, meal_math

# Teto de favoritos por usuario (default de desenho, sem fonte externa): a lista
# e um atalho curto; o catalogo inteiro continua na busca.
MAX_FAVORITES: Final[int] = 200

ERR_TOO_MANY_FAVORITES = "too_many_favorites"
ERR_DEFAULT_INCOMPLETE = "favorite_default_incomplete"
ERR_DEFAULT_PORTION_UNIT = "favorite_default_portion_unit"
ERR_INVALID_ORDER = "invalid_favorite_order"


def default_errors(
    quantity: Any,
    unit: Any,
    portion_id: Any,
    basis: Any,
    portion_amount: Any = None,
    serving_size: Any = None,
) -> tuple[str, ...]:
    """Erros da porcao padrao. Sem quantidade, unidade e porcao = sem padrao (valido).

    `portion_amount` e a quantidade (na base) da porcao escolhida, ja lida do
    catalogo pela rota; `serving_size` e a porcao do rotulo do alimento."""
    sem_quantidade = quantity is None
    sem_unidade = unit is None
    if sem_quantidade and sem_unidade and portion_id is None:
        return ()
    if sem_quantidade or sem_unidade:
        return (ERR_DEFAULT_INCOMPLETE,)
    unidade = meal_math.normalize_unit(unit)
    if portion_id is not None and unidade not in meal_math.PORTION_UNITS:
        return (ERR_DEFAULT_PORTION_UNIT,)
    quantidade_porcao = meal_math.portion_amount_for(unit, portion_amount, serving_size)
    resolucao = meal_math.resolve_amount(quantity, unit, basis, quantidade_porcao)
    return diary.item_amount_errors(resolucao)


def canonical_unit(unit: Any) -> Optional[str]:
    """Codigo canonico de `meal_math.Unit` ou None (desconhecida)."""
    normalizada = meal_math.normalize_unit(unit)
    return normalizada.value if normalizada else None


def can_add(current_count: int) -> bool:
    """Cabe mais um favorito na lista?"""
    return int(current_count) < MAX_FAVORITES


def order_errors(current_ids: Sequence[int], requested_ids: Sequence[int]) -> tuple[str, ...]:
    """A ordem pedida precisa ser uma permutacao exata dos favoritos atuais."""
    pedido = [int(i) for i in requested_ids]
    if len(pedido) != len(set(pedido)) or set(pedido) != {int(i) for i in current_ids}:
        return (ERR_INVALID_ORDER,)
    return ()


def positions_for(ordered_ids: Sequence[int]) -> list[tuple[int, int]]:
    """`[(food_id, posicao)]` com posicoes consecutivas a partir de 1."""
    return [(int(food_id), indice) for indice, food_id in enumerate(ordered_ids, start=1)]


def next_position(max_position: Optional[int]) -> int:
    """Posicao do favorito novo: depois do ultimo."""
    return int(max_position or 0) + 1


__all__ = [
    "ERR_DEFAULT_INCOMPLETE",
    "ERR_DEFAULT_PORTION_UNIT",
    "ERR_INVALID_ORDER",
    "ERR_TOO_MANY_FAVORITES",
    "MAX_FAVORITES",
    "can_add",
    "canonical_unit",
    "default_errors",
    "next_position",
    "order_errors",
    "positions_for",
]

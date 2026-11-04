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
- Origem do item (L5b, `item_source_errors`): um alimento do catálogo, a
  estimativa da IA (`meal_parse`, natureza ESTIMATED) OU o lançamento rápido
  (2026-11-04, abaixo), exatamente uma.
- Lançamento rápido (D2, `validate_quick_entry`, `quick_entry_item_nutrition`): item sem
  `food_id` com nome, peso em g e kcal/proteína/carboidrato/gordura ABSOLUTOS desse peso,
  todos obrigatórios. Natureza própria (`MealNature.USER_ENTERED`), sem aviso de IA e sem
  alimento no catálogo; fibra e sódio ficam desconhecidos (`None`). Nada a ver com o
  `estimate` da IA (`meal_parse`).
- Hora -> refeição (D3, `meal_slot_for_time`): a refeição sugerida pela hora local, por
  janelas nomeadas; pré e pós-treino não se derivam da hora (dependem do treino).
- Ordem do dia (D5, `meal_sort_key`): cronológica pela hora; sem hora vem por último.

Defaults de desenho, SEM fonte bibliográfica: `MEAL_MAX_ITEMS` (uma refeição
real não passa de algumas dezenas de itens; o teto protege o tamanho do lote) e
`MEAL_SLOT_WINDOWS` (hábito alimentar comum no Brasil; é só uma sugestão editável).
Água e fome do dia saíram do produto (2026-11-03); as colunas de `nutrition_days`
ficam, sem uso, até uma limpeza futura.

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import time
from enum import Enum
from typing import Any, Final, Mapping, Optional

from .catalog import (
    ERR_INVALID_NAME,
    ERR_INVALID_VALUE,
    ERR_MISSING_FIELD,
    PORTION_AMOUNT_MAX,
)
from .macros import KCAL_PER_G_FAT
from .meal_math import (
    CARB_FIELD,
    ENERGY_FIELD,
    FAT_FIELD,
    FIBER_FIELD,
    LIMIT_MISSING_QUANTITY,
    LIMIT_MISSING_PORTION_G,
    LIMIT_UNKNOWN_BASIS,
    LIMIT_UNKNOWN_UNIT,
    PROTEIN_FIELD,
    SODIUM_FIELD,
    AmountResolution,
    ItemNutrition,
    MealNature,
    Nutrients,
    Unit,
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
# Slot com que o preview valida a refeição sem refeição escolhida: os macros do item
# não dependem dele e o preview não grava nada (L10). Gravar continua exigindo o slot.
PREVIEW_MEAL_SLOT: Final[MealSlot] = MealSlot.OTHER
ITEM_AMOUNT_MAX: Final[float] = PORTION_AMOUNT_MAX

# Ordem das refeições no dia = ordem do enum.
MEAL_SLOT_ORDER: Final[Mapping[MealSlot, int]] = {slot: indice for indice, slot in enumerate(MealSlot)}

ERR_INVALID_MEAL_SLOT = "invalid_meal_slot"
ERR_MEAL_WITHOUT_ITEMS = "meal_without_items"
ERR_TOO_MANY_ITEMS = "too_many_meal_items"
ERR_UNIT_WITHOUT_CONVERSION = "unit_without_conversion"
ERR_ITEM_AMOUNT_OUT_OF_RANGE = "item_amount_out_of_range"
ERR_ITEM_FOOD_OR_ESTIMATE = "item_food_or_estimate_required"
ERR_QUICK_ENTRY_WEIGHT = "quick_entry_weight_invalid"
ERR_QUICK_ABOVE_WEIGHT = "quick_entry_above_weight"  # `quick_entry_above_weight:<campo>`
ERR_INVALID_CLOCK_TIME = "invalid_clock_time"

# Lançamento rápido (D2). Campos com os códigos do catálogo (RDC 429) + o peso, em g.
QUICK_ENTRY_WEIGHT_FIELD: Final[str] = "weight_g"
QUICK_ENTRY_NUTRIENT_FIELDS: Final[tuple[str, ...]] = (ENERGY_FIELD, PROTEIN_FIELD, CARB_FIELD, FAT_FIELD)
QUICK_ENTRY_REQUIRED_FIELDS: Final[tuple[str, ...]] = (QUICK_ENTRY_WEIGHT_FIELD, *QUICK_ENTRY_NUTRIENT_FIELDS)
# Fibra e sódio não são pedidos no lançamento rápido: não tornam a refeição nem o dia incompletos (decisão do líder,
# 2026-11-04). A linha `fiber_g` do balanço continua sem número e com `intake_complete: false`.
QUICK_ENTRY_UNREQUESTED_FIELDS: Final[tuple[str, ...]] = (FIBER_FIELD, SODIUM_FIELD)
QUICK_ENTRY_NAME_MAX_CHARS: Final[int] = 255
QUICK_ENTRY_SOURCE: Final[str] = "quick_entry"  # `source` do alimento no snapshot do item (não é `catalog.FoodSource`)
QUICK_ENTRY_NATURE: Final[MealNature] = MealNature.USER_ENTERED
QUICK_ENTRY_UNIT: Final[Unit] = Unit.GRAM  # peso em g; o item guarda quantity = peso, unit = g, basis = g

# Hora local -> refeição (D3): início da janela (minutos desde 00:00) -> refeição, em ordem crescente.
# Cada janela vai até o início da próxima; a última vai até 23:59 e a primeira cobre a madrugada (ceia).
MINUTES_PER_HOUR: Final[int] = 60


def _clock(hour: int, minute: int = 0) -> int:
    return hour * MINUTES_PER_HOUR + minute


MEAL_SLOT_WINDOWS: Final[tuple[tuple[int, MealSlot], ...]] = (
    (_clock(0), MealSlot.SUPPER),  # 00:00-03:59
    (_clock(4), MealSlot.BREAKFAST),  # 04:00-09:29
    (_clock(9, 30), MealSlot.MORNING_SNACK),  # 09:30-11:29
    (_clock(11, 30), MealSlot.LUNCH),  # 11:30-14:59
    (_clock(15), MealSlot.AFTERNOON_SNACK),  # 15:00-17:59
    (_clock(18), MealSlot.DINNER),  # 18:00-21:29
    (_clock(21, 30), MealSlot.SUPPER),  # 21:30-23:59
)

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


def meal_sort_key(slot: Any, eaten_at: Any, meal_id: Any) -> tuple[int, str, int, int]:
    """Ordem cronológica das refeições no dia (D5, 2026-11-04): pela hora (`HH:MM`);
    sem hora vem depois das com hora; empate pelo slot (ordem do enum) e pelo id."""
    refeicao = normalize_meal_slot(slot)
    posicao = MEAL_SLOT_ORDER[refeicao] if refeicao is not None else len(MEAL_SLOT_ORDER)
    tem_hora = eaten_at is not None and str(eaten_at) != ""
    return (0 if tem_hora else 1, str(eaten_at) if tem_hora else "", posicao, int(meal_id or 0))


def clock_minutes(raw: Any) -> Optional[int]:
    """Hora local (`datetime.time` ou `HH:MM[:SS]`) -> minutos desde 00:00; inválida -> None."""
    if isinstance(raw, time):
        return raw.hour * MINUTES_PER_HOUR + raw.minute
    partes = str(raw or "").strip().split(":")
    if len(partes) not in (2, 3) or not all(p.isdigit() for p in partes):
        return None
    hora, minuto = int(partes[0]), int(partes[1])
    return hora * MINUTES_PER_HOUR + minuto if 0 <= hora <= 23 and 0 <= minuto <= 59 else None


def meal_slot_for_time(raw: Any) -> Optional[MealSlot]:
    """Refeição sugerida pela hora local (`MEAL_SLOT_WINDOWS`); hora inválida -> None. A tela mostra
    esta sugestão e deixa trocar; nunca calcula a janela."""
    minutos = clock_minutes(raw)
    if minutos is None:
        return None
    escolhida = MEAL_SLOT_WINDOWS[0][1]
    for inicio, refeicao in MEAL_SLOT_WINDOWS:
        if minutos >= inicio:
            escolhida = refeicao
    return escolhida


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


def item_source_errors(has_food: bool, has_estimate: bool, has_quick_entry: bool = False) -> tuple[str, ...]:
    """Origem de UM item: alimento do catálogo, estimativa da IA (L5b) ou lançamento rápido (D2),
    exatamente uma."""
    return () if (bool(has_food) + bool(has_estimate) + bool(has_quick_entry)) == 1 else (ERR_ITEM_FOOD_OR_ESTIMATE,)


# ---------------------------------------------------------------------------
# Lançamento rápido (D2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuickEntry:
    """Item lançado à mão: nome, peso (g) e nutrientes ABSOLUTOS desse peso (fibra e sódio `None`)."""

    name: str
    weight_g: float
    nutrients: Nutrients

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, QUICK_ENTRY_WEIGHT_FIELD: self.weight_g, "nutrients": self.nutrients.to_dict()}


@dataclass(frozen=True)
class QuickEntryValidation:
    entry: Optional[QuickEntry]
    errors: tuple[str, ...]


def _quick_number(raw: Any) -> tuple[Optional[float], bool]:
    """(valor, válido): ausente -> (None, True); texto/negativo/não finito/booleano -> (None, False)."""
    if raw is None:
        return None, True
    if isinstance(raw, bool):
        return None, False
    try:
        numero = float(raw)
    except (TypeError, ValueError):
        return None, False
    return (numero, True) if math.isfinite(numero) and numero >= 0 else (None, False)


def validate_quick_entry(raw: Any) -> QuickEntryValidation:
    """Regras do lançamento rápido: nome; peso em g > 0 e até `ITEM_AMOUNT_MAX`; kcal, proteína,
    carboidrato e gordura >= 0 e todos obrigatórios; cada macro até o peso e as kcal até o que o
    peso comportaria em gordura (código próprio `quick_entry_above_weight`)."""
    dados: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    nome = " ".join(str(dados.get("name") or "").split())
    erros: list[str] = []
    if not nome or len(nome) > QUICK_ENTRY_NAME_MAX_CHARS:
        erros.append(ERR_INVALID_NAME)
    valores: dict[str, float] = {}
    for campo in QUICK_ENTRY_REQUIRED_FIELDS:
        numero, valido = _quick_number(dados.get(campo))
        if not valido:
            erros.append(f"{ERR_INVALID_VALUE}:{campo}")
        elif numero is None:
            erros.append(f"{ERR_MISSING_FIELD}:{campo}")
        else:
            valores[campo] = numero
    peso = valores.get(QUICK_ENTRY_WEIGHT_FIELD)
    if peso is not None:
        if peso <= 0:
            erros.append(ERR_QUICK_ENTRY_WEIGHT)
        elif peso > ITEM_AMOUNT_MAX:
            erros.append(ERR_ITEM_AMOUNT_OUT_OF_RANGE)
        else:
            for campo in (PROTEIN_FIELD, CARB_FIELD, FAT_FIELD):
                if campo in valores and valores[campo] > peso:
                    erros.append(f"{ERR_QUICK_ABOVE_WEIGHT}:{campo}")
            if ENERGY_FIELD in valores and valores[ENERGY_FIELD] > peso * KCAL_PER_G_FAT:
                erros.append(f"{ERR_QUICK_ABOVE_WEIGHT}:{ENERGY_FIELD}")
    if erros:
        return QuickEntryValidation(None, tuple(dict.fromkeys(erros)))
    return QuickEntryValidation(
        QuickEntry(
            name=nome,
            weight_g=valores[QUICK_ENTRY_WEIGHT_FIELD],
            nutrients=Nutrients(**{campo: valores[campo] for campo in QUICK_ENTRY_NUTRIENT_FIELDS}),
        ),
        (),
    )


def quick_entry_item_nutrition(entry: QuickEntry) -> ItemNutrition:
    """Nutrientes do item lançado: os valores digitados, sem conversão. Fibra e sódio ficam
    `None` e não pedidos (`QUICK_ENTRY_UNREQUESTED_FIELDS`): não marcam o total como incompleto."""
    return ItemNutrition(
        amount=entry.weight_g,
        basis=QUICK_ENTRY_UNIT,
        nutrients=entry.nutrients,
        nature=QUICK_ENTRY_NATURE,
        not_requested=QUICK_ENTRY_UNREQUESTED_FIELDS,
    )


def quick_entry_snapshot(entry: QuickEntry) -> dict[str, Any]:
    """`catalog_snapshot_json` do item lançado (E4): a mesma forma do snapshot do catálogo, sem id,
    com a fonte `QUICK_ENTRY_SOURCE` e os valores absolutos em `quick_entry`."""
    return {
        "food": {
            "id": None,
            "source": QUICK_ENTRY_SOURCE,
            "name": entry.name,
            "basis": QUICK_ENTRY_UNIT.value,
            "per_100": {},
            "quick_entry": entry.to_dict(),
        },
        "portion": None,
    }


def snapshot_is_quick_entry(food_snapshot: Any) -> bool:
    """O snapshot gravado é de um item lançado à mão?"""
    return isinstance(food_snapshot, Mapping) and food_snapshot.get("source") == QUICK_ENTRY_SOURCE


def quick_entry_from_snapshot(food_snapshot: Any) -> Optional[QuickEntry]:
    """Item lançado de volta do snapshot congelado (edição que mantém o item); malformado -> None."""
    if not snapshot_is_quick_entry(food_snapshot):
        return None
    bruto = food_snapshot.get("quick_entry")
    if not isinstance(bruto, Mapping):
        return None
    nutrientes = bruto.get("nutrients")
    nutrientes = nutrientes if isinstance(nutrientes, Mapping) else {}
    entrada = {"name": bruto.get("name"), QUICK_ENTRY_WEIGHT_FIELD: bruto.get(QUICK_ENTRY_WEIGHT_FIELD)}
    entrada.update({campo: nutrientes.get(campo) for campo in QUICK_ENTRY_NUTRIENT_FIELDS})
    return validate_quick_entry(entrada).entry

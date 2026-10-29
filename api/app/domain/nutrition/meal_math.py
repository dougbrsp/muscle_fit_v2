"""Matemática de refeição: gramas a partir de quantidade + unidade, macros do
item e da refeição, natureza (derivado/estimado/misto) e consistência.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão `kcal_por_grama`).
Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §4, §5 (D2, E4, E9) e §7.

- Catálogo por 100 g (TACO/USDA/OFF): macros do item = por 100 g × gramas / 100.
- Porções genéricas (medidas caseiras, em ml): colher de sopa 15, colher de
  sobremesa 10, colher de chá 5, xícara 240. Porções ESPECÍFICAS de um alimento
  vêm do catálogo (`portion_g`), não daqui.
- ml -> g pela densidade do alimento; sem densidade, assume 1,0 g/ml (água) e
  marca `assumed_density`.
- Consistência de Atwater: kcal declarada × kcal dos macros dentro de
  `ATWATER_TOLERANCE_SHARE` (15%; desvio relativo ao maior dos dois).
- Rótulo: valores por porção × (por 100 g × porção/100) dentro de
  `LABEL_SERVING_TOLERANCE_SHARE` (10%). A RDC 429/2020 + IN 75/2020 definem o
  formato da tabela (por 100 g/ml e por porção) e os campos, NÃO essa
  tolerância: os 10% são da checagem de leitura do rótulo, não da norma.
- 1 kcal = 4,184 kJ.
- Natureza dos totais (`MealNature`): DERIVED/ESTIMATED de `DataNature` + MIXED.

Defaults de desenho, SEM fonte bibliográfica (a confirmar pelo usuário):
`ATWATER_TOLERANCE_SHARE` e `LABEL_SERVING_TOLERANCE_SHARE`.

Diário de refeições (Lote L4, mapa §52):
- Nutrientes do item, da refeição e do dia (`Nutrients`) usam os códigos da
  RDC 429 do catálogo (`NUTRIENT_FIELDS` ⊂ `catalog.PER_100_FIELDS`); `None` =
  desconhecido, nunca 0.
- Quantidade na BASE do alimento (`resolve_amount`): g ou ml direto; medida
  caseira em ml; g ↔ ml pela densidade (sem densidade, 1,0 g/ml e
  `assumed_density`); porção/unidade pela quantidade da porção NA BASE.
- Macro ausente (decisão 1 do L4, `complete_missing_nutrients`): proteína,
  carboidrato ou gordura ausentes valem 0 SÓ quando a energia é conhecida e os
  macros conhecidos a explicam dentro de `ATWATER_TOLERANCE_SHARE` (os óleos
  da TACO: 884 kcal, 100 g de gordura, proteína e carboidrato "NA"), com o
  código `macro_inferred_zero:<campo>`. Fibra ausente vale 0 só com
  carboidrato 0 (conhecido ou inferido): na TACO e no USDA o carboidrato é
  calculado por diferença e contém a fibra (`fiber_inferred_zero`). O resto
  (energia ausente, sódio, macro que não explica a energia) fica desconhecido
  (`nutrient_unknown:<campo>`).
- Totais (`sum_items`, `combine_totals`): por campo, a soma das contribuições
  CONHECIDAS e o campo marcado incompleto (`incomplete_fields`,
  `total_incomplete:<campo>`) quando alguma contribuição é desconhecida; sem
  nenhuma conhecida, `None`. Sem refeição no dia, o total do dia é `None`.

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, Final, Iterable, Mapping, Optional, Sequence

from ..data_nature import DataNature
from .anthropometrics import non_negative_number, positive_number
from .macros import ZERO_MACROS, Macros, kcal_from_macros

PER_100G_REFERENCE: Final[float] = 100.0
DEFAULT_DENSITY_G_PER_ML: Final[float] = 1.0
KJ_PER_KCAL: Final[float] = 4.184
# Defaults de desenho, sem fonte bibliográfica (a RDC 429 não fixa tolerância).
ATWATER_TOLERANCE_SHARE: Final[float] = 0.15
LABEL_SERVING_TOLERANCE_SHARE: Final[float] = 0.10


class Unit(str, Enum):
    GRAM = "g"
    MILLILITER = "ml"
    UNIT = "unit"  # unidade do alimento (1 ovo, 1 banana): exige `portion_g`
    PORTION = "portion"  # porção do rótulo/catálogo: exige `portion_g`
    TABLESPOON = "colher_sopa"
    DESSERT_SPOON = "colher_sobremesa"
    TEASPOON = "colher_cha"
    CUP = "xicara"


# Unidades cuja quantidade vem de uma porção (do catálogo ou do rótulo), não de g/ml.
PORTION_UNITS: Final[tuple[Unit, ...]] = (Unit.UNIT, Unit.PORTION)


@dataclass(frozen=True)
class Portion:
    code: str
    label: str
    ml: float

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "label": self.label, "ml": self.ml}


GENERIC_PORTIONS: Final[Mapping[Unit, Portion]] = {
    Unit.TABLESPOON: Portion(Unit.TABLESPOON.value, "Colher de sopa", 15.0),
    Unit.DESSERT_SPOON: Portion(Unit.DESSERT_SPOON.value, "Colher de sobremesa", 10.0),
    Unit.TEASPOON: Portion(Unit.TEASPOON.value, "Colher de chá", 5.0),
    Unit.CUP: Portion(Unit.CUP.value, "Xícara", 240.0),
}

LIMIT_MISSING_QUANTITY = "missing_quantity"
LIMIT_UNKNOWN_UNIT = "unknown_unit"
LIMIT_MISSING_PORTION_G = "missing_portion_g"
LIMIT_ASSUMED_DENSITY = "assumed_density"


class MealNature(str, Enum):
    """Natureza dos totais de um item ou refeição.

    DERIVED e ESTIMATED são os MESMOS valores de `DataNature` (dono:
    `domain/data_nature.py`). MIXED é próprio da refeição (itens derivados e
    estimados somados) e não existe no vocabulário comum, por isso o enum é
    deste módulo e não um `DataNature`."""

    DERIVED = DataNature.DERIVED.value  # calculado do catálogo
    ESTIMATED = DataNature.ESTIMATED.value  # estimado (IA, sem casamento)
    MIXED = "MIXED"


def normalize_unit(raw: Any) -> Optional[Unit]:
    if raw is None:
        return None
    if isinstance(raw, Unit):
        return raw
    try:
        return Unit(str(raw).strip().lower())
    except ValueError:
        return None


@dataclass(frozen=True)
class GramsResolution:
    grams: Optional[float]
    unit: Optional[Unit]
    assumed_density: bool = False
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "grams": self.grams,
            "unit": self.unit.value if self.unit else None,
            "assumed_density": self.assumed_density,
            "limitations": list(self.limitations),
        }


def resolve_grams(
    quantity: Any, unit: Any, portion_g: Any = None, density_g_per_ml: Any = None
) -> GramsResolution:
    """Gramas de um item: g direto; ml e medidas caseiras pela densidade;
    unidade/porção pelo `portion_g` do catálogo."""
    qtd = positive_number(quantity)
    unidade = normalize_unit(unit)
    if qtd is None:
        return GramsResolution(None, unidade, limitations=(LIMIT_MISSING_QUANTITY,))
    if unidade is None:
        return GramsResolution(None, None, limitations=(LIMIT_UNKNOWN_UNIT,))
    if unidade == Unit.GRAM:
        return GramsResolution(qtd, unidade)
    if unidade in PORTION_UNITS:
        porcao = positive_number(portion_g)
        if porcao is None:
            return GramsResolution(None, unidade, limitations=(LIMIT_MISSING_PORTION_G,))
        return GramsResolution(qtd * porcao, unidade)
    ml = qtd if unidade == Unit.MILLILITER else qtd * GENERIC_PORTIONS[unidade].ml
    densidade = positive_number(density_g_per_ml)
    if densidade is None:
        return GramsResolution(ml * DEFAULT_DENSITY_G_PER_ML, unidade, assumed_density=True, limitations=(LIMIT_ASSUMED_DENSITY,))
    return GramsResolution(ml * densidade, unidade)


def item_macros(per_100g: Macros, grams: Any) -> Optional[Macros]:
    """Macros do item a partir do catálogo por 100 g."""
    g = non_negative_number(grams)
    if g is None:
        return None
    return per_100g.scaled(g / PER_100G_REFERENCE)


def sum_macros(items: Iterable[Optional[Macros]]) -> Macros:
    total = ZERO_MACROS
    for item in items:
        if item is not None:
            total = total.plus(item)
    return total


def meal_nature(natures: Sequence[MealNature]) -> Optional[MealNature]:
    """Refeição toda derivada -> DERIVED; toda estimada -> ESTIMATED; mistura -> MIXED."""
    distintas = set(natures)
    if not distintas:
        return None
    if len(distintas) == 1:
        return next(iter(distintas))
    return MealNature.MIXED


@dataclass(frozen=True)
class AtwaterCheck:
    declared_kcal: float
    computed_kcal: float
    deviation_share: Optional[float]
    consistent: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared_kcal": self.declared_kcal,
            "computed_kcal": self.computed_kcal,
            "deviation_share": self.deviation_share,
            "consistent": self.consistent,
        }


def atwater_check(macros: Macros) -> Optional[AtwaterCheck]:
    """kcal declarada × kcal calculada dos macros (tolerância `ATWATER_TOLERANCE_SHARE`)."""
    calculada = kcal_from_macros(macros.protein_g, macros.carb_g, macros.fat_g)
    declarada = non_negative_number(macros.kcal)
    if calculada is None or declarada is None:
        return None
    if calculada == 0 and declarada == 0:
        return AtwaterCheck(declarada, calculada, 0.0, True)
    base = max(calculada, declarada)
    desvio = abs(declarada - calculada) / base
    return AtwaterCheck(declarada, calculada, desvio, desvio <= ATWATER_TOLERANCE_SHARE)


@dataclass(frozen=True)
class LabelCheck:
    serving_g: float
    fields_off: tuple[str, ...]
    consistent: bool

    def to_dict(self) -> dict[str, Any]:
        return {"serving_g": self.serving_g, "fields_off": list(self.fields_off), "consistent": self.consistent}


def _relative_gap(a: float, b: float) -> float:
    base = max(abs(a), abs(b))
    return 0.0 if base == 0 else abs(a - b) / base


def label_consistency(per_100g: Macros, per_serving: Macros, serving_g: Any) -> Optional[LabelCheck]:
    """Rótulo: cada campo por porção deve bater com por 100 g × porção / 100
    dentro de `LABEL_SERVING_TOLERANCE_SHARE`."""
    porcao = positive_number(serving_g)
    if porcao is None:
        return None
    esperado = per_100g.scaled(porcao / PER_100G_REFERENCE)
    fora: list[str] = []
    for campo in ("kcal", "protein_g", "carb_g", "fat_g"):
        if _relative_gap(getattr(esperado, campo), getattr(per_serving, campo)) > LABEL_SERVING_TOLERANCE_SHARE:
            fora.append(campo)
    if esperado.fiber_g is not None and per_serving.fiber_g is not None:
        if _relative_gap(esperado.fiber_g, per_serving.fiber_g) > LABEL_SERVING_TOLERANCE_SHARE:
            fora.append("fiber_g")
    return LabelCheck(serving_g=porcao, fields_off=tuple(fora), consistent=not fora)


def per_reference_from_serving(value: Any, serving_size: Any) -> Optional[float]:
    """Valor por 100 g/ml a partir do valor de UMA porção de `serving_size` (na
    mesma base): a inversa de `label_consistency`. Rótulo antigo, só por porção
    (foto do rótulo, L6). Valor ou porção inválidos -> None."""
    valor, porcao = non_negative_number(value), positive_number(serving_size)
    if valor is None or porcao is None:
        return None
    return valor * PER_100G_REFERENCE / porcao


def kj_to_kcal(kj: Any) -> Optional[float]:
    valor = non_negative_number(kj)
    return None if valor is None else valor / KJ_PER_KCAL


def kcal_to_kj(kcal: Any) -> Optional[float]:
    valor = non_negative_number(kcal)
    return None if valor is None else valor * KJ_PER_KCAL


# ---------------------------------------------------------------------------
# Diário de refeições (Lote L4, mapa §52)
# ---------------------------------------------------------------------------

# Campos de nutriente do diário: os códigos da RDC 429 do catálogo
# (`catalog.PER_100_FIELDS`, teste de paridade), só os que o diário soma.
ENERGY_FIELD: Final[str] = "energy_kcal"
PROTEIN_FIELD: Final[str] = "protein_g"
CARB_FIELD: Final[str] = "carbohydrates_g"
FAT_FIELD: Final[str] = "total_fat_g"
FIBER_FIELD: Final[str] = "fiber_g"
SODIUM_FIELD: Final[str] = "sodium_mg"
NUTRIENT_FIELDS: Final[tuple[str, ...]] = (ENERGY_FIELD, PROTEIN_FIELD, CARB_FIELD, FAT_FIELD, FIBER_FIELD, SODIUM_FIELD)
# Macros que entram na energia por Atwater (`macros.kcal_from_macros`).
ATWATER_MACRO_FIELDS: Final[tuple[str, ...]] = (PROTEIN_FIELD, CARB_FIELD, FAT_FIELD)

LIMIT_UNKNOWN_BASIS = "unknown_basis"
# `<código>:<campo>` (o rótulo do campo vem de `labels.RDC429_FIELD_LABELS`).
LIMIT_MACRO_INFERRED_ZERO = "macro_inferred_zero"
LIMIT_NUTRIENT_UNKNOWN = "nutrient_unknown"
LIMIT_TOTAL_INCOMPLETE = "total_incomplete"
LIMIT_FIBER_INFERRED_ZERO = "fiber_inferred_zero"

# Sem estas, a quantidade do item não converte para a base do alimento.
AMOUNT_BLOCKING_LIMITATIONS: Final[frozenset[str]] = frozenset(
    {LIMIT_MISSING_QUANTITY, LIMIT_UNKNOWN_UNIT, LIMIT_MISSING_PORTION_G, LIMIT_UNKNOWN_BASIS}
)


def _unique_codes(*groups: Iterable[str]) -> tuple[str, ...]:
    vistos: list[str] = []
    for grupo in groups:
        for codigo in grupo:
            if codigo not in vistos:
                vistos.append(codigo)
    return tuple(vistos)


@dataclass(frozen=True)
class Nutrients:
    """Nutrientes de um item, refeição ou dia (códigos da RDC 429; `None` =
    desconhecido, nunca 0). Por 100 g/ml no catálogo; absolutos no diário."""

    energy_kcal: Optional[float] = None
    protein_g: Optional[float] = None
    carbohydrates_g: Optional[float] = None
    total_fat_g: Optional[float] = None
    fiber_g: Optional[float] = None
    sodium_mg: Optional[float] = None

    def get(self, field: str) -> Optional[float]:
        return getattr(self, field)

    def unknown_fields(self) -> tuple[str, ...]:
        return tuple(campo for campo in NUTRIENT_FIELDS if self.get(campo) is None)

    def scaled(self, factor: float) -> "Nutrients":
        return Nutrients(**{campo: None if (v := self.get(campo)) is None else v * factor for campo in NUTRIENT_FIELDS})

    def as_macros(self) -> Optional[Macros]:
        """`Macros` (vocabulário da meta) só com energia e os três macros conhecidos."""
        energia, proteina, carbo, gordura = (self.get(c) for c in (ENERGY_FIELD, *ATWATER_MACRO_FIELDS))
        if energia is None or proteina is None or carbo is None or gordura is None:
            return None
        return Macros(kcal=energia, protein_g=proteina, carb_g=carbo, fat_g=gordura, fiber_g=self.fiber_g)

    def to_dict(self) -> dict[str, Any]:
        return {campo: self.get(campo) for campo in NUTRIENT_FIELDS}


# Os campos da dataclass SÃO os códigos de `NUTRIENT_FIELDS`, na mesma ordem.
assert tuple(f.name for f in fields(Nutrients)) == NUTRIENT_FIELDS


def nutrients_from_values(values: Mapping[str, Any]) -> Nutrients:
    """Valores por campo (Decimal, texto, número ou `None`) -> `Nutrients`;
    ausente, negativo ou inválido vira `None`."""
    return Nutrients(**{campo: non_negative_number(values.get(campo)) for campo in NUTRIENT_FIELDS})


@dataclass(frozen=True)
class NutrientCompletion:
    """Nutrientes do alimento depois da regra do macro ausente."""

    nutrients: Nutrients
    inferred_zero: tuple[str, ...]
    unknown: tuple[str, ...]

    @property
    def limitations(self) -> tuple[str, ...]:
        inferidos = tuple(
            LIMIT_FIBER_INFERRED_ZERO if campo == FIBER_FIELD else f"{LIMIT_MACRO_INFERRED_ZERO}:{campo}"
            for campo in self.inferred_zero
        )
        return (*inferidos, *(f"{LIMIT_NUTRIENT_UNKNOWN}:{campo}" for campo in self.unknown))

    def to_dict(self) -> dict[str, Any]:
        return {
            "nutrients": self.nutrients.to_dict(),
            "inferred_zero": list(self.inferred_zero),
            "unknown": list(self.unknown),
            "limitations": list(self.limitations),
        }


def complete_missing_nutrients(per_100: Nutrients) -> NutrientCompletion:
    """Regra do macro ausente (decisão 1 do L4). Um macro ausente só vira 0
    quando a energia é CONHECIDA e os macros conhecidos (o ausente como 0) a
    explicam dentro de `ATWATER_TOLERANCE_SHARE`; fibra ausente só vira 0 com
    carboidrato 0. Nada mais é inferido: o que falta fica `None`."""
    valores = per_100.to_dict()
    inferidos: list[str] = []
    ausentes = [campo for campo in ATWATER_MACRO_FIELDS if valores[campo] is None]
    energia = valores[ENERGY_FIELD]
    if ausentes and energia is not None:
        hipotese = Macros(
            kcal=energia,
            protein_g=valores[PROTEIN_FIELD] or 0.0,
            carb_g=valores[CARB_FIELD] or 0.0,
            fat_g=valores[FAT_FIELD] or 0.0,
        )
        checagem = atwater_check(hipotese)
        if checagem is not None and checagem.consistent:
            for campo in ausentes:
                valores[campo] = 0.0
                inferidos.append(campo)
    if valores[FIBER_FIELD] is None and valores[CARB_FIELD] == 0:
        valores[FIBER_FIELD] = 0.0
        inferidos.append(FIBER_FIELD)
    completos = Nutrients(**valores)
    return NutrientCompletion(nutrients=completos, inferred_zero=tuple(inferidos), unknown=completos.unknown_fields())


def normalize_basis(raw: Any) -> Optional[Unit]:
    """Base da tabela do alimento: g ou ml (os códigos de `Unit`)."""
    unidade = normalize_unit(raw)
    return unidade if unidade in (Unit.GRAM, Unit.MILLILITER) else None


@dataclass(frozen=True)
class AmountResolution:
    """Quantidade do item NA BASE do alimento (g ou ml) ou o motivo de não converter."""

    amount: Optional[float]
    basis: Optional[Unit]
    unit: Optional[Unit]
    assumed_density: bool = False
    limitations: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.amount is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": self.amount,
            "basis": self.basis.value if self.basis else None,
            "unit": self.unit.value if self.unit else None,
            "assumed_density": self.assumed_density,
            "limitations": list(self.limitations),
        }


def portion_amount_for(unit: Any, chosen_portion_amount: Any = None, serving_size: Any = None) -> Optional[float]:
    """Quantidade da porção (na base) de um item em `unit`/`portion`: a porção
    cadastrada escolhida; sem ela, `portion` usa a porção do rótulo
    (`serving_size`) e `unit` fica sem conversão (não há peso da unidade)."""
    unidade = normalize_unit(unit)
    if unidade not in PORTION_UNITS:
        return None
    escolhida = positive_number(chosen_portion_amount)
    if escolhida is not None:
        return escolhida
    return positive_number(serving_size) if unidade == Unit.PORTION else None


def resolve_amount(
    quantity: Any, unit: Any, basis: Any, portion_amount: Any = None, density_g_per_ml: Any = None
) -> AmountResolution:
    """Quantidade NA BASE do alimento: g ou ml direto; medida caseira em ml;
    g ↔ ml pela densidade (sem densidade, `DEFAULT_DENSITY_G_PER_ML` +
    `assumed_density`); porção/unidade × `portion_amount` (já na base)."""
    base = normalize_basis(basis)
    qtd = positive_number(quantity)
    unidade = normalize_unit(unit)
    if base is None:
        return AmountResolution(None, None, unidade, limitations=(LIMIT_UNKNOWN_BASIS,))
    if qtd is None:
        return AmountResolution(None, base, unidade, limitations=(LIMIT_MISSING_QUANTITY,))
    if unidade is None:
        return AmountResolution(None, base, None, limitations=(LIMIT_UNKNOWN_UNIT,))
    if unidade in PORTION_UNITS:
        porcao = positive_number(portion_amount)
        if porcao is None:
            return AmountResolution(None, base, unidade, limitations=(LIMIT_MISSING_PORTION_G,))
        return AmountResolution(qtd * porcao, base, unidade)
    if unidade == base:
        return AmountResolution(qtd, base, unidade)
    densidade = positive_number(density_g_per_ml)
    assumida = densidade is None
    densidade_usada = DEFAULT_DENSITY_G_PER_ML if densidade is None else densidade
    limitacoes = (LIMIT_ASSUMED_DENSITY,) if assumida else ()
    if unidade == Unit.GRAM:  # alimento por 100 ml, item em gramas
        return AmountResolution(qtd / densidade_usada, base, unidade, assumida, limitacoes)
    ml = qtd if unidade == Unit.MILLILITER else qtd * GENERIC_PORTIONS[unidade].ml
    if base == Unit.MILLILITER:
        return AmountResolution(ml, base, unidade)
    return AmountResolution(ml * densidade_usada, base, unidade, assumida, limitacoes)


@dataclass(frozen=True)
class ItemNutrition:
    """Um item do diário: quantidade na base, nutrientes (já na quantidade) e
    natureza. `unknown` = campos que o catálogo não informa."""

    amount: float
    basis: Unit
    nutrients: Nutrients
    nature: MealNature
    inferred_zero: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": self.amount,
            "basis": self.basis.value,
            "nutrients": self.nutrients.to_dict(),
            "nature": self.nature.value,
            "inferred_zero": list(self.inferred_zero),
            "unknown": list(self.unknown),
            "limitations": list(self.limitations),
        }


def item_nutrition(
    per_100: Nutrients, resolution: AmountResolution, nature: MealNature = MealNature.DERIVED
) -> Optional[ItemNutrition]:
    """Nutrientes do item a partir do alimento por 100 g/ml (com a regra do
    macro ausente) e da quantidade já na base. Sem quantidade -> `None`."""
    if resolution.amount is None or resolution.basis is None:
        return None
    completo = complete_missing_nutrients(per_100)
    return ItemNutrition(
        amount=resolution.amount,
        basis=resolution.basis,
        nutrients=completo.nutrients.scaled(resolution.amount / PER_100G_REFERENCE),
        nature=nature,
        inferred_zero=completo.inferred_zero,
        unknown=completo.unknown,
        limitations=_unique_codes(resolution.limitations, completo.limitations),
    )


@dataclass(frozen=True)
class NutritionTotals:
    """Total de uma refeição ou de um dia. Por campo: soma das contribuições
    CONHECIDAS (`None` se nenhuma) e o campo em `incomplete_fields` quando
    alguma contribuição é desconhecida — o total é então um mínimo, nunca um
    valor completo."""

    nutrients: Nutrients
    incomplete_fields: tuple[str, ...]
    nature: Optional[MealNature]
    item_count: int

    def is_complete(self, field: str) -> bool:
        return field not in self.incomplete_fields and self.nutrients.get(field) is not None

    @property
    def limitations(self) -> tuple[str, ...]:
        return tuple(f"{LIMIT_TOTAL_INCOMPLETE}:{campo}" for campo in self.incomplete_fields)

    def complete_nutrients(self) -> Nutrients:
        """Só os campos completos (o incompleto vira `None`)."""
        return Nutrients(
            **{campo: self.nutrients.get(campo) if self.is_complete(campo) else None for campo in NUTRIENT_FIELDS}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nutrients": self.nutrients.to_dict(),
            "incomplete_fields": list(self.incomplete_fields),
            "complete": not self.incomplete_fields,
            "nature": self.nature.value if self.nature else None,
            "item_count": self.item_count,
            "limitations": list(self.limitations),
        }


def _sum_parts(parts: Sequence[tuple[Nutrients, tuple[str, ...]]]) -> tuple[Nutrients, tuple[str, ...]]:
    somas: dict[str, Optional[float]] = {}
    incompletos: list[str] = []
    for campo in NUTRIENT_FIELDS:
        conhecidos = [valor for nutrientes, _ in parts if (valor := nutrientes.get(campo)) is not None]
        somas[campo] = float(sum(conhecidos)) if conhecidos else None
        if not parts or any(nutrientes.get(campo) is None or campo in faltam for nutrientes, faltam in parts):
            incompletos.append(campo)
    return Nutrients(**somas), tuple(incompletos)


def sum_items(items: Sequence[ItemNutrition]) -> NutritionTotals:
    """Total da refeição. Sem item, tudo `None` e incompleto (refeição vazia
    não é zero)."""
    nutrientes, incompletos = _sum_parts([(item.nutrients, ()) for item in items])
    return NutritionTotals(
        nutrients=nutrientes,
        incomplete_fields=incompletos,
        nature=meal_nature([item.nature for item in items]),
        item_count=len(items),
    )


def combine_totals(parts: Sequence[NutritionTotals]) -> Optional[NutritionTotals]:
    """Total do dia: soma dos totais das refeições; sem refeição -> `None`
    (dia sem registro, nunca 0)."""
    if not parts:
        return None
    nutrientes, incompletos = _sum_parts([(parte.nutrients, parte.incomplete_fields) for parte in parts])
    return NutritionTotals(
        nutrients=nutrientes,
        incomplete_fields=incompletos,
        nature=meal_nature([parte.nature for parte in parts if parte.nature is not None]),
        item_count=sum(parte.item_count for parte in parts),
    )


def normalize_meal_nature(raw: Any) -> Optional[MealNature]:
    if raw is None:
        return None
    try:
        return MealNature(str(raw).strip().upper())
    except ValueError:
        return None


def totals_from_stored(
    values: Mapping[str, Any], incomplete_fields: Any, nature: Any = None, item_count: Any = 0
) -> NutritionTotals:
    """Total gravado de uma refeição (colunas com os códigos de
    `NUTRIENT_FIELDS` + a lista de campos incompletos) de volta ao motor."""
    marcados = {str(campo) for campo in (incomplete_fields or ())}
    return NutritionTotals(
        nutrients=nutrients_from_values(values),
        incomplete_fields=tuple(campo for campo in NUTRIENT_FIELDS if campo in marcados),
        nature=normalize_meal_nature(nature),
        item_count=int(item_count or 0),
    )

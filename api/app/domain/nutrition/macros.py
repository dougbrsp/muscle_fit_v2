"""Macronutrientes: gramas/dia a partir do alvo calórico, proteína por refeição.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrões `kcal_por_grama` e
`faixa_proteina`). Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §3.3 e §7.

- Fatores de Atwater: 4 kcal/g proteína e carboidrato, 9 kcal/g gordura,
  7 kcal/g álcool.
- Fibra: 14 g por 1.000 kcal (referência dietética).
- Proteína por refeição: 0,40–0,55 g/kg; 3–6 refeições/dia.
- Divisão: proteína pela base da estratégia (g/kg de MLG no cutting, de peso no
  resto), gordura como fração das kcal, carboidrato com o resto.

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Optional

from .anthropometrics import non_negative_number, positive_number
from .strategy import ProteinBasis, Strategy, StrategyParams

KCAL_PER_G_PROTEIN: Final[float] = 4.0
KCAL_PER_G_CARB: Final[float] = 4.0
KCAL_PER_G_FAT: Final[float] = 9.0
KCAL_PER_G_ALCOHOL: Final[float] = 7.0

FIBER_G_PER_1000_KCAL: Final[float] = 14.0
FIBER_KCAL_REFERENCE: Final[float] = 1000.0

PROTEIN_PER_MEAL_G_PER_KG_RANGE: Final[tuple[float, float]] = (0.40, 0.55)
MEALS_PER_DAY_RANGE: Final[tuple[int, int]] = (3, 6)

LIMIT_MISSING_KCAL = "missing_kcal"
LIMIT_MISSING_PROTEIN_BASIS = "missing_protein_basis"
LIMIT_KCAL_BELOW_PROTEIN_AND_FAT = "kcal_below_protein_and_fat"
LIMIT_CARB_BELOW_MIN = "carb_below_min_g_per_kg"


@dataclass(frozen=True)
class Macros:
    """Energia e macros de um item, refeição ou dia (gramas; kcal pode ser
    declarada ou calculada). `fiber_g` opcional."""

    kcal: float
    protein_g: float
    carb_g: float
    fat_g: float
    fiber_g: Optional[float] = None

    def plus(self, other: "Macros") -> "Macros":
        fibra = None
        if self.fiber_g is not None or other.fiber_g is not None:
            fibra = (self.fiber_g or 0.0) + (other.fiber_g or 0.0)
        return Macros(
            kcal=self.kcal + other.kcal,
            protein_g=self.protein_g + other.protein_g,
            carb_g=self.carb_g + other.carb_g,
            fat_g=self.fat_g + other.fat_g,
            fiber_g=fibra,
        )

    def scaled(self, factor: float) -> "Macros":
        return Macros(
            kcal=self.kcal * factor,
            protein_g=self.protein_g * factor,
            carb_g=self.carb_g * factor,
            fat_g=self.fat_g * factor,
            fiber_g=None if self.fiber_g is None else self.fiber_g * factor,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kcal": self.kcal,
            "protein_g": self.protein_g,
            "carb_g": self.carb_g,
            "fat_g": self.fat_g,
            "fiber_g": self.fiber_g,
        }


ZERO_MACROS: Final[Macros] = Macros(kcal=0.0, protein_g=0.0, carb_g=0.0, fat_g=0.0, fiber_g=0.0)


def kcal_from_macros(protein_g: Any, carb_g: Any, fat_g: Any, alcohol_g: Any = 0.0) -> Optional[float]:
    """kcal pelos fatores de Atwater. Qualquer grama negativa/inválida -> None."""
    valores = [non_negative_number(v if v is not None else 0.0) for v in (protein_g, carb_g, fat_g, alcohol_g)]
    if any(v is None for v in valores):
        return None
    proteina, carbo, gordura, alcool = valores  # type: ignore[misc]
    return (
        proteina * KCAL_PER_G_PROTEIN + carbo * KCAL_PER_G_CARB + gordura * KCAL_PER_G_FAT + alcool * KCAL_PER_G_ALCOHOL
    )


def fiber_target_g(kcal: Any) -> Optional[float]:
    energia = positive_number(kcal)
    if energia is None:
        return None
    return FIBER_G_PER_1000_KCAL * energia / FIBER_KCAL_REFERENCE


@dataclass(frozen=True)
class MacroTargets:
    """Metas do dia em gramas (None = não calculável)."""

    kcal: Optional[float]
    protein_g: Optional[float]
    carb_g: Optional[float]
    fat_g: Optional[float]
    fiber_g: Optional[float]
    protein_basis: Optional[ProteinBasis]
    protein_basis_kg: Optional[float]
    protein_g_per_kg: Optional[float]
    limitations: tuple[str, ...] = ()

    @property
    def computable(self) -> bool:
        return None not in (self.kcal, self.protein_g, self.carb_g, self.fat_g)

    def as_macros(self) -> Optional[Macros]:
        if not self.computable:
            return None
        return Macros(
            kcal=float(self.kcal),  # type: ignore[arg-type]
            protein_g=float(self.protein_g),  # type: ignore[arg-type]
            carb_g=float(self.carb_g),  # type: ignore[arg-type]
            fat_g=float(self.fat_g),  # type: ignore[arg-type]
            fiber_g=self.fiber_g,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kcal": self.kcal,
            "protein_g": self.protein_g,
            "carb_g": self.carb_g,
            "fat_g": self.fat_g,
            "fiber_g": self.fiber_g,
            "protein_basis": self.protein_basis.value if self.protein_basis else None,
            "protein_basis_kg": self.protein_basis_kg,
            "protein_g_per_kg": self.protein_g_per_kg,
            "limitations": list(self.limitations),
        }


def _not_computable(params: StrategyParams, basis_kg: Optional[float], limitations: tuple[str, ...]) -> MacroTargets:
    return MacroTargets(
        kcal=None,
        protein_g=None,
        carb_g=None,
        fat_g=None,
        fiber_g=None,
        protein_basis=params.protein_basis,
        protein_basis_kg=basis_kg,
        protein_g_per_kg=params.protein_g_per_kg,
        limitations=limitations,
    )


def macro_split(kcal: Any, params: StrategyParams, weight_kg: Any = None, ffm_kg: Any = None) -> MacroTargets:
    """Gramas do dia: proteína = g/kg × base; gordura = fração das kcal;
    carboidrato = resto (nunca negativo: se faltar energia, 0 + limitação)."""
    energia = positive_number(kcal)
    base_kg = positive_number(ffm_kg) if params.protein_basis == ProteinBasis.FAT_FREE_MASS else positive_number(weight_kg)
    limitacoes: list[str] = []
    if energia is None:
        limitacoes.append(LIMIT_MISSING_KCAL)
    if base_kg is None:
        limitacoes.append(LIMIT_MISSING_PROTEIN_BASIS)
    if energia is None or base_kg is None:
        return _not_computable(params, base_kg, tuple(limitacoes))

    proteina = params.protein_g_per_kg * base_kg
    gordura = params.fat_kcal_share * energia / KCAL_PER_G_FAT
    restante = energia - proteina * KCAL_PER_G_PROTEIN - gordura * KCAL_PER_G_FAT
    if restante < 0:
        restante = 0.0
        limitacoes.append(LIMIT_KCAL_BELOW_PROTEIN_AND_FAT)
    carbo = restante / KCAL_PER_G_CARB
    peso = positive_number(weight_kg)
    if (
        params.strategy == Strategy.BULKING
        and params.carb_min_g_per_kg is not None
        and peso is not None
        and carbo < params.carb_min_g_per_kg * peso
    ):
        limitacoes.append(LIMIT_CARB_BELOW_MIN)
    return MacroTargets(
        kcal=energia,
        protein_g=proteina,
        carb_g=carbo,
        fat_g=gordura,
        fiber_g=fiber_target_g(energia),
        protein_basis=params.protein_basis,
        protein_basis_kg=base_kg,
        protein_g_per_kg=params.protein_g_per_kg,
        limitations=tuple(limitacoes),
    )


def protein_per_meal(protein_g: Any, meals_per_day: Any) -> Optional[float]:
    """Proteína por refeição dividindo igualmente. Refeições < 1 -> None."""
    proteina = non_negative_number(protein_g)
    refeicoes = positive_number(meals_per_day)
    if proteina is None or refeicoes is None or refeicoes < 1:
        return None
    return proteina / refeicoes


def protein_per_meal_range_g(weight_kg: Any) -> Optional[tuple[float, float]]:
    """Faixa recomendada de proteína por refeição (0,40–0,55 g/kg de peso)."""
    peso = positive_number(weight_kg)
    if peso is None:
        return None
    return (PROTEIN_PER_MEAL_G_PER_KG_RANGE[0] * peso, PROTEIN_PER_MEAL_G_PER_KG_RANGE[1] * peso)


def meals_per_day_is_valid(meals_per_day: Any) -> bool:
    refeicoes = positive_number(meals_per_day)
    return refeicoes is not None and MEALS_PER_DAY_RANGE[0] <= refeicoes <= MEALS_PER_DAY_RANGE[1]

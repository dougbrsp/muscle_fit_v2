"""Estratégias (cutting / bulking / manutenção): faixas, defaults e alvo calórico.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão `faixa_proteina`).
Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §3.3 e §7.

| Estratégia | Balanço | Ritmo | Proteína | Gordura | Fontes |
|---|---|---|---|---|---|
| Cutting | déficit p/ 0,5–1 % PC/sem (padrão 0,7), ≤ 25% do TDEE, ingestão ≥ 1,0 × TMB | ↓ | 2,3–3,1 g/kg MLG (padrão 2,6) | 15–30% kcal (padrão 25%) | Helms 2014; Garthe 2011 |
| Bulking | +10–20% (avançado +5–10%) | ↑ 0,25–0,5 % PC/sem | 1,6–2,2 g/kg PC (padrão 2,0) | 0,5–1,5 g/kg (20–35%) | Iraki 2019; Morton 2018 |
| Maintenance | 0 (±5%) | 0 | 1,6–2,2 g/kg PC | 20–35% | ISSN 2017 |

Defaults sem valor explícito no plano (documentados aqui, editáveis pelo
admin/PT no plano do aluno): superávit do bulking no PISO da faixa (10%;
avançado 5%) — Iraki 2019 e Slater 2019 defendem superávit pequeno; gordura do
bulking e da manutenção em 25% das kcal (dentro de 20–35%); carbo mínimo do
bulking no piso da faixa 3–5 g/kg (3,0).

Clamps do cutting (decisão D-B): déficit ≤ `CUT_MAX_DEFICIT_SHARE` do TDEE e
ingestão ≥ `CUT_MIN_INTAKE_BMR_MULTIPLE` × TMB; cada um registrado em
`KcalTarget.clamped_by`. O ritmo pedido vira kcal/dia pela densidade energética
de Hall/Forbes (`energy_density`), que exige a massa gorda: sem ela o alvo do
cutting é não calculável (None + limitação), nunca um "7.700 fixo".

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Final, Optional

from .anthropometrics import positive_number
from .energy_density import daily_kcal_for_weekly_rate, weekly_kg_for_daily_kcal


class Strategy(str, Enum):
    CUTTING = "cutting"
    BULKING = "bulking"
    MAINTENANCE = "maintenance"


class ProteinBasis(str, Enum):
    """Base da proteína em g/kg: massa livre de gordura ou peso corporal."""

    FAT_FREE_MASS = "fat_free_mass"
    BODY_WEIGHT = "body_weight"


# --- Cutting (Helms 2014; Garthe 2011) ---------------------------------------
CUT_WEEKLY_RATE_SHARE_RANGE: Final[tuple[float, float]] = (0.005, 0.010)  # % do PC por semana
CUT_WEEKLY_RATE_SHARE_DEFAULT: Final[float] = 0.007
CUT_MAX_DEFICIT_SHARE: Final[float] = 0.25  # do TDEE
CUT_MIN_INTAKE_BMR_MULTIPLE: Final[float] = 1.0
CUT_PROTEIN_G_PER_KG_FFM_RANGE: Final[tuple[float, float]] = (2.3, 3.1)
CUT_PROTEIN_G_PER_KG_FFM_DEFAULT: Final[float] = 2.6
CUT_FAT_KCAL_SHARE_RANGE: Final[tuple[float, float]] = (0.15, 0.30)
CUT_FAT_KCAL_SHARE_DEFAULT: Final[float] = 0.25

# --- Bulking (Iraki 2019; Morton 2018; Slater 2019) --------------------------
BULK_SURPLUS_SHARE_RANGE: Final[tuple[float, float]] = (0.10, 0.20)
BULK_SURPLUS_SHARE_DEFAULT: Final[float] = 0.10
BULK_SURPLUS_SHARE_RANGE_ADVANCED: Final[tuple[float, float]] = (0.05, 0.10)
BULK_SURPLUS_SHARE_DEFAULT_ADVANCED: Final[float] = 0.05
BULK_WEEKLY_RATE_SHARE_RANGE: Final[tuple[float, float]] = (0.0025, 0.005)  # ganho esperado, monitoramento
BULK_PROTEIN_G_PER_KG_RANGE: Final[tuple[float, float]] = (1.6, 2.2)
BULK_PROTEIN_G_PER_KG_DEFAULT: Final[float] = 2.0
BULK_FAT_G_PER_KG_RANGE: Final[tuple[float, float]] = (0.5, 1.5)
BULK_FAT_KCAL_SHARE_RANGE: Final[tuple[float, float]] = (0.20, 0.35)
BULK_FAT_KCAL_SHARE_DEFAULT: Final[float] = 0.25
BULK_CARB_MIN_G_PER_KG_RANGE: Final[tuple[float, float]] = (3.0, 5.0)
BULK_CARB_MIN_G_PER_KG: Final[float] = 3.0

# --- Manutenção (ISSN 2017) --------------------------------------------------
MAINT_TOLERANCE_SHARE: Final[float] = 0.05
MAINT_PROTEIN_G_PER_KG_RANGE: Final[tuple[float, float]] = (1.6, 2.2)
MAINT_PROTEIN_G_PER_KG_DEFAULT: Final[float] = 2.0
MAINT_FAT_KCAL_SHARE_RANGE: Final[tuple[float, float]] = (0.20, 0.35)
MAINT_FAT_KCAL_SHARE_DEFAULT: Final[float] = 0.25

# Códigos de clamp e de limitação (estáveis).
CLAMP_MAX_DEFICIT_SHARE = "max_deficit_share"
CLAMP_MIN_INTAKE_BMR_MULTIPLE = "min_intake_bmr_multiple"
LIMIT_MISSING_TDEE = "missing_tdee"
LIMIT_MISSING_BMR_FOR_FLOOR = "missing_bmr_for_floor"
LIMIT_MISSING_WEIGHT = "missing_weight"
LIMIT_MISSING_FAT_MASS = "missing_fat_mass"
LIMIT_PARAM_OUT_OF_RANGE = "param_out_of_range"


def protein_range_for(strategy: Strategy) -> tuple[float, float]:
    if strategy == Strategy.CUTTING:
        return CUT_PROTEIN_G_PER_KG_FFM_RANGE
    if strategy == Strategy.BULKING:
        return BULK_PROTEIN_G_PER_KG_RANGE
    return MAINT_PROTEIN_G_PER_KG_RANGE


def fat_share_range_for(strategy: Strategy) -> tuple[float, float]:
    if strategy == Strategy.CUTTING:
        return CUT_FAT_KCAL_SHARE_RANGE
    if strategy == Strategy.BULKING:
        return BULK_FAT_KCAL_SHARE_RANGE
    return MAINT_FAT_KCAL_SHARE_RANGE


def surplus_range_for(advanced: bool) -> tuple[float, float]:
    return BULK_SURPLUS_SHARE_RANGE_ADVANCED if advanced else BULK_SURPLUS_SHARE_RANGE


def _within(value: Optional[float], bounds: tuple[float, float]) -> bool:
    return value is not None and bounds[0] <= value <= bounds[1]


@dataclass(frozen=True)
class StrategyParams:
    """Parâmetros do plano de um aluno (defaults por estratégia, editáveis)."""

    strategy: Strategy
    protein_g_per_kg: float
    protein_basis: ProteinBasis
    fat_kcal_share: float
    advanced: bool = False
    weekly_rate_share: Optional[float] = None  # cutting: % do PC a perder por semana
    surplus_share: Optional[float] = None  # bulking: % do TDEE acima da manutenção
    carb_min_g_per_kg: Optional[float] = None  # bulking: piso de carbo

    @classmethod
    def defaults_for(cls, strategy: Strategy, advanced: bool = False) -> "StrategyParams":
        if strategy == Strategy.CUTTING:
            return cls(
                strategy=strategy,
                protein_g_per_kg=CUT_PROTEIN_G_PER_KG_FFM_DEFAULT,
                protein_basis=ProteinBasis.FAT_FREE_MASS,
                fat_kcal_share=CUT_FAT_KCAL_SHARE_DEFAULT,
                advanced=advanced,
                weekly_rate_share=CUT_WEEKLY_RATE_SHARE_DEFAULT,
            )
        if strategy == Strategy.BULKING:
            return cls(
                strategy=strategy,
                protein_g_per_kg=BULK_PROTEIN_G_PER_KG_DEFAULT,
                protein_basis=ProteinBasis.BODY_WEIGHT,
                fat_kcal_share=BULK_FAT_KCAL_SHARE_DEFAULT,
                advanced=advanced,
                surplus_share=BULK_SURPLUS_SHARE_DEFAULT_ADVANCED if advanced else BULK_SURPLUS_SHARE_DEFAULT,
                carb_min_g_per_kg=BULK_CARB_MIN_G_PER_KG,
            )
        return cls(
            strategy=strategy,
            protein_g_per_kg=MAINT_PROTEIN_G_PER_KG_DEFAULT,
            protein_basis=ProteinBasis.BODY_WEIGHT,
            fat_kcal_share=MAINT_FAT_KCAL_SHARE_DEFAULT,
            advanced=advanced,
        )

    def as_maintenance(self) -> "StrategyParams":
        """Mesma proteína/gordura, balanço zero (pausa de dieta, refeed)."""
        return replace(self, strategy=Strategy.MAINTENANCE, weekly_rate_share=None, surplus_share=None)

    def out_of_range(self) -> tuple[str, ...]:
        """Códigos dos parâmetros fora da faixa da fonte (aviso, não bloqueio)."""
        avisos: list[str] = []
        if not _within(self.protein_g_per_kg, protein_range_for(self.strategy)):
            avisos.append(f"{LIMIT_PARAM_OUT_OF_RANGE}:protein_g_per_kg")
        if not _within(self.fat_kcal_share, fat_share_range_for(self.strategy)):
            avisos.append(f"{LIMIT_PARAM_OUT_OF_RANGE}:fat_kcal_share")
        if self.strategy == Strategy.CUTTING and not _within(self.weekly_rate_share, CUT_WEEKLY_RATE_SHARE_RANGE):
            avisos.append(f"{LIMIT_PARAM_OUT_OF_RANGE}:weekly_rate_share")
        if self.strategy == Strategy.BULKING:
            if not _within(self.surplus_share, surplus_range_for(self.advanced)):
                avisos.append(f"{LIMIT_PARAM_OUT_OF_RANGE}:surplus_share")
            if self.carb_min_g_per_kg is not None and not _within(self.carb_min_g_per_kg, BULK_CARB_MIN_G_PER_KG_RANGE):
                avisos.append(f"{LIMIT_PARAM_OUT_OF_RANGE}:carb_min_g_per_kg")
        return tuple(avisos)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "protein_g_per_kg": self.protein_g_per_kg,
            "protein_basis": self.protein_basis.value,
            "fat_kcal_share": self.fat_kcal_share,
            "advanced": self.advanced,
            "weekly_rate_share": self.weekly_rate_share,
            "surplus_share": self.surplus_share,
            "carb_min_g_per_kg": self.carb_min_g_per_kg,
        }


@dataclass(frozen=True)
class KcalTarget:
    """Alvo calórico do dia: kcal (None = não calculável), diferença para o
    TDEE (negativa = déficit), ritmo semanal implicado (% do PC, positivo =
    perda; só no cutting) e os clamps aplicados."""

    kcal: Optional[float]
    delta_kcal: Optional[float]
    rate_applied: Optional[float]
    clamped_by: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kcal": self.kcal,
            "delta_kcal": self.delta_kcal,
            "rate_applied": self.rate_applied,
            "clamped_by": list(self.clamped_by),
            "limitations": list(self.limitations),
        }


def _cutting_target(
    params: StrategyParams, tdee_kcal: float, bmr_kcal: Optional[float], weight_kg: Any, fat_mass_kg: Any
) -> KcalTarget:
    peso = positive_number(weight_kg)
    limitacoes: list[str] = []
    if peso is None:
        limitacoes.append(LIMIT_MISSING_WEIGHT)
    ritmo = params.weekly_rate_share if params.weekly_rate_share is not None else CUT_WEEKLY_RATE_SHARE_DEFAULT
    deficit = daily_kcal_for_weekly_rate(peso * ritmo, fat_mass_kg) if peso is not None else None
    if deficit is None and peso is not None:
        limitacoes.append(LIMIT_MISSING_FAT_MASS)
    if deficit is None:
        return KcalTarget(kcal=None, delta_kcal=None, rate_applied=None, limitations=tuple(limitacoes))

    clamps: list[str] = []
    teto_deficit = CUT_MAX_DEFICIT_SHARE * tdee_kcal
    if deficit > teto_deficit:
        deficit = teto_deficit
        clamps.append(CLAMP_MAX_DEFICIT_SHARE)
    ingestao = tdee_kcal - deficit
    if bmr_kcal is None:
        limitacoes.append(LIMIT_MISSING_BMR_FOR_FLOOR)
    else:
        piso = CUT_MIN_INTAKE_BMR_MULTIPLE * bmr_kcal
        if ingestao < piso:
            ingestao = piso
            clamps.append(CLAMP_MIN_INTAKE_BMR_MULTIPLE)
    delta = ingestao - tdee_kcal
    kg_semana = weekly_kg_for_daily_kcal(-delta, fat_mass_kg)
    ritmo_aplicado = kg_semana / peso if kg_semana is not None and peso else None
    return KcalTarget(
        kcal=ingestao,
        delta_kcal=delta,
        rate_applied=ritmo_aplicado,
        clamped_by=tuple(clamps),
        limitations=tuple(limitacoes),
    )


def kcal_target(
    params: StrategyParams,
    tdee_kcal: Any,
    bmr_kcal: Any = None,
    weight_kg: Any = None,
    fat_mass_kg: Any = None,
) -> KcalTarget:
    """Alvo calórico do dia pela estratégia. Sem TDEE -> None."""
    tdee = positive_number(tdee_kcal)
    if tdee is None:
        return KcalTarget(kcal=None, delta_kcal=None, rate_applied=None, limitations=(LIMIT_MISSING_TDEE,))
    if params.strategy == Strategy.CUTTING:
        return _cutting_target(params, tdee, positive_number(bmr_kcal), weight_kg, fat_mass_kg)
    if params.strategy == Strategy.BULKING:
        superavit = params.surplus_share
        if superavit is None:
            superavit = BULK_SURPLUS_SHARE_DEFAULT_ADVANCED if params.advanced else BULK_SURPLUS_SHARE_DEFAULT
        delta = superavit * tdee
        return KcalTarget(kcal=tdee + delta, delta_kcal=delta, rate_applied=None)
    return KcalTarget(kcal=tdee, delta_kcal=0.0, rate_applied=None)


def maintenance_band(tdee_kcal: Any) -> Optional[tuple[float, float]]:
    """Faixa de manutenção (±`MAINT_TOLERANCE_SHARE`) em torno do TDEE."""
    tdee = positive_number(tdee_kcal)
    if tdee is None:
        return None
    return (tdee * (1.0 - MAINT_TOLERANCE_SHARE), tdee * (1.0 + MAINT_TOLERANCE_SHARE))

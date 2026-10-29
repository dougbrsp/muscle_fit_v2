"""Ciclo de carboidrato: distribuição planejada do orçamento semanal com a
média preservada e a proteína constante.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`). Plano:
`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §3.3, §3.5 e §7 (decisão D-I).

Evidência fraca para composição corporal; aqui é só DISTRIBUIÇÃO do carbo da
semana: dia de treino (pernas incluídas) ×1,25, dia de descanso ×0,75, piso de
1 g/kg no dia baixo; proteína e gordura constantes; kcal do dia acompanha o
carbo. Invariante: soma semanal de carbo (e de kcal) = 7 × alvo.

Os multiplicadores são expressos como oscilação simétrica em torno da média
(`CARB_CYCLE_SWING_SHARE` = 0,25 -> 1,25 e 0,75). Quando a semana não tem o
mesmo número de dias altos e baixos, os multiplicadores nominais são
reescalados por um fator comum para que a soma continue 7 × alvo (a razão
alto/baixo se mantém). Se o piso de 1 g/kg prende o dia baixo, o dia alto
absorve o resto; se mesmo assim não dá para ciclar, a semana fica NORMAL com
limitação.

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Final, Mapping, Optional, Sequence

from .anthropometrics import positive_number
from .energy_density import DAYS_PER_WEEK
from .macros import MacroTargets, fiber_target_g, kcal_from_macros

CARB_CYCLE_SWING_SHARE: Final[float] = 0.25
HIGH_CARB_MULTIPLIER: Final[float] = 1.0 + CARB_CYCLE_SWING_SHARE  # 1,25
LOW_CARB_MULTIPLIER: Final[float] = 1.0 - CARB_CYCLE_SWING_SHARE  # 0,75
NORMAL_CARB_MULTIPLIER: Final[float] = 1.0
MIN_LOW_CARB_G_PER_KG: Final[float] = 1.0

LIMIT_WEEK_NOT_SEVEN_DAYS = "week_must_have_seven_days"
LIMIT_CARB_FLOOR_PREVENTS_CYCLE = "carb_floor_prevents_cycle"
LIMIT_TARGET_NOT_COMPUTABLE = "target_not_computable"


class DayKind(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


NOMINAL_MULTIPLIERS: Final[Mapping[DayKind, float]] = {
    DayKind.HIGH: HIGH_CARB_MULTIPLIER,
    DayKind.NORMAL: NORMAL_CARB_MULTIPLIER,
    DayKind.LOW: LOW_CARB_MULTIPLIER,
}
FLAT_MULTIPLIERS: Final[Mapping[DayKind, float]] = {kind: NORMAL_CARB_MULTIPLIER for kind in DayKind}


def day_kinds_for_week(training_days: Sequence[bool]) -> tuple[DayKind, ...]:
    """Tipo de cada dia da semana (7 flags, segunda primeiro): treino -> alto,
    descanso -> baixo. Semana toda igual (só treino ou só descanso) -> normal."""
    if len(training_days) != DAYS_PER_WEEK:
        raise ValueError(LIMIT_WEEK_NOT_SEVEN_DAYS)
    flags = [bool(d) for d in training_days]
    if all(flags) or not any(flags):
        return tuple(DayKind.NORMAL for _ in flags)
    return tuple(DayKind.HIGH if treino else DayKind.LOW for treino in flags)


@dataclass(frozen=True)
class WeekMultipliers:
    by_kind: Mapping[DayKind, float]
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"by_kind": {k.value: v for k, v in self.by_kind.items()}, "limitations": list(self.limitations)}


def week_multipliers(kinds: Sequence[DayKind], carb_g: Any, weight_kg: Any = None) -> WeekMultipliers:
    """Multiplicador de carbo por tipo de dia que preserva a soma semanal."""
    if len(kinds) != DAYS_PER_WEEK:
        raise ValueError(LIMIT_WEEK_NOT_SEVEN_DAYS)
    contagem = {kind: sum(1 for k in kinds if k == kind) for kind in DayKind}
    if contagem[DayKind.HIGH] == 0 or contagem[DayKind.LOW] == 0:
        return WeekMultipliers(by_kind=dict(FLAT_MULTIPLIERS))
    soma_nominal = sum(contagem[kind] * NOMINAL_MULTIPLIERS[kind] for kind in DayKind)
    escala = DAYS_PER_WEEK / soma_nominal
    por_tipo = {kind: NOMINAL_MULTIPLIERS[kind] * escala for kind in DayKind}

    carbo = positive_number(carb_g)
    peso = positive_number(weight_kg)
    if carbo is None or peso is None:
        return WeekMultipliers(by_kind=por_tipo)
    piso = MIN_LOW_CARB_G_PER_KG * peso / carbo
    if por_tipo[DayKind.LOW] >= piso:
        return WeekMultipliers(by_kind=por_tipo)
    # O piso prende o dia baixo: dias normais ficam em 1,0 e o alto absorve o resto.
    baixo = piso
    normal = NORMAL_CARB_MULTIPLIER
    alto = (DAYS_PER_WEEK - contagem[DayKind.LOW] * baixo - contagem[DayKind.NORMAL] * normal) / contagem[DayKind.HIGH]
    if alto <= normal:
        return WeekMultipliers(by_kind=dict(FLAT_MULTIPLIERS), limitations=(LIMIT_CARB_FLOOR_PREVENTS_CYCLE,))
    return WeekMultipliers(by_kind={DayKind.HIGH: alto, DayKind.NORMAL: normal, DayKind.LOW: baixo})


def _day_target(target: MacroTargets, multiplier: float) -> MacroTargets:
    carbo = float(target.carb_g) * multiplier  # type: ignore[arg-type]
    kcal = kcal_from_macros(target.protein_g, carbo, target.fat_g)
    return replace(target, carb_g=carbo, kcal=kcal, fiber_g=fiber_target_g(kcal))


@dataclass(frozen=True)
class WeekDistribution:
    """Sete metas (segunda primeiro) com o tipo de cada dia e os multiplicadores."""

    days: tuple[MacroTargets, ...]
    kinds: tuple[DayKind, ...]
    multipliers: WeekMultipliers
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": [d.to_dict() for d in self.days],
            "kinds": [k.value for k in self.kinds],
            "multipliers": self.multipliers.to_dict(),
            "limitations": list(self.limitations),
        }


def distribute_week(target: MacroTargets, kinds: Sequence[DayKind], weight_kg: Any = None) -> WeekDistribution:
    """Metas dos 7 dias a partir da meta média: proteína e gordura iguais todos
    os dias; carbo pelo multiplicador do tipo; kcal recalculada."""
    tipos = tuple(kinds)
    if not target.computable:
        return WeekDistribution(
            days=tuple(target for _ in tipos),
            kinds=tipos,
            multipliers=WeekMultipliers(by_kind=dict(FLAT_MULTIPLIERS)),
            limitations=(LIMIT_TARGET_NOT_COMPUTABLE,),
        )
    mult = week_multipliers(tipos, target.carb_g, weight_kg)
    dias = tuple(_day_target(target, mult.by_kind[kind]) for kind in tipos)
    return WeekDistribution(days=dias, kinds=tipos, multipliers=mult, limitations=mult.limitations)


def apply_cycle(
    target: MacroTargets, kind: DayKind, kinds: Sequence[DayKind], weight_kg: Any = None
) -> tuple[MacroTargets, tuple[str, ...]]:
    """Meta de UM dia do tipo `kind` dentro da semana `kinds`."""
    if not target.computable:
        return target, (LIMIT_TARGET_NOT_COMPUTABLE,)
    mult = week_multipliers(tuple(kinds), target.carb_g, weight_kg)
    return _day_target(target, mult.by_kind[kind]), mult.limitations


def weekly_carb_total(distribution: WeekDistribution) -> Optional[float]:
    if any(d.carb_g is None for d in distribution.days):
        return None
    return sum(float(d.carb_g) for d in distribution.days)  # type: ignore[arg-type]

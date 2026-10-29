"""Calendário de fases: bulking / cutting / manutenção, pausas de dieta e refeeds.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão `pausa_dieta`).
Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §3.3, §3.4 e §7.

- MATADOR (Byrne 2018): 2 semanas de déficit / 2 semanas de manutenção.
- ICECAP (Peos 2021): 3 semanas de déficit / 1 semana de manutenção.
- Refeed de carboidrato (Campbell 2020): 2 dias por semana, na manutenção.
- Cutting contínuo acima de `MAX_CUT_WEEKS_WITHOUT_BREAK` semanas vira AVISO
  no calendário proposto (o admin/PT decide a pausa).
- Parada sugerida por % de gordura (decisão D-C, default editável no plano):
  bulking até H 15% / M 25%; cutting até H 10% / M 18%. `Sex.OTHER` sem default.

O motor PROPÕE o calendário; admin/PT publica (decisão D4). Tudo em datas
civis; a data de início é argumento (sem relógio).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Any, Final, Mapping, Optional, Sequence

from .anthropometrics import Sex
from .energy_density import DAYS_PER_WEEK
from .strategy import Strategy

MATADOR_ON_WEEKS: Final[int] = 2
MATADOR_OFF_WEEKS: Final[int] = 2
ICECAP_ON_WEEKS: Final[int] = 3
ICECAP_OFF_WEEKS: Final[int] = 1
REFEED_DAYS_PER_WEEK_DEFAULT: Final[int] = 2
MAX_CUT_WEEKS_WITHOUT_BREAK: Final[int] = 8

# Decisão D-C: % de gordura em que o motor sugere parar (default editável).
BULK_STOP_BODY_FAT_PERCENT: Final[Mapping[Sex, float]] = {Sex.MALE: 15.0, Sex.FEMALE: 25.0}
CUT_STOP_BODY_FAT_PERCENT: Final[Mapping[Sex, float]] = {Sex.MALE: 10.0, Sex.FEMALE: 18.0}

WARN_EXCEEDS_MAX_CUT_WEEKS = "exceeds_max_cut_weeks_without_break"
LIMIT_WEEKS_NOT_POSITIVE = "weeks_must_be_positive"


class PhaseKind(str, Enum):
    CUTTING = "cutting"
    BULKING = "bulking"
    MAINTENANCE = "maintenance"
    DIET_BREAK = "diet_break"
    REFEED = "refeed"


class BreakProtocol(str, Enum):
    NONE = "none"
    MATADOR = "matador"
    ICECAP = "icecap"


BREAK_PROTOCOL_WEEKS: Final[Mapping[BreakProtocol, tuple[int, int]]] = {
    BreakProtocol.MATADOR: (MATADOR_ON_WEEKS, MATADOR_OFF_WEEKS),
    BreakProtocol.ICECAP: (ICECAP_ON_WEEKS, ICECAP_OFF_WEEKS),
}

PHASE_FOR_STRATEGY: Final[Mapping[Strategy, PhaseKind]] = {
    Strategy.CUTTING: PhaseKind.CUTTING,
    Strategy.BULKING: PhaseKind.BULKING,
    Strategy.MAINTENANCE: PhaseKind.MAINTENANCE,
}

# Fases em que o alvo calórico do dia é o de manutenção.
MAINTENANCE_KCAL_PHASES: Final[frozenset[PhaseKind]] = frozenset(
    {PhaseKind.MAINTENANCE, PhaseKind.DIET_BREAK, PhaseKind.REFEED}
)


@dataclass(frozen=True)
class PhaseBlock:
    """Bloco contínuo de dias de uma fase (`end` inclusivo)."""

    kind: PhaseKind
    start: date
    end: date
    index: int

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "index": self.index,
            "days": self.days,
        }


@dataclass(frozen=True)
class PhaseCalendar:
    blocks: tuple[PhaseBlock, ...]
    protocol: BreakProtocol
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocks": [b.to_dict() for b in self.blocks],
            "protocol": self.protocol.value,
            "warnings": list(self.warnings),
        }


def _block(kind: PhaseKind, start: date, weeks: int, index: int) -> PhaseBlock:
    return PhaseBlock(kind=kind, start=start, end=start + timedelta(days=weeks * DAYS_PER_WEEK - 1), index=index)


def propose_calendar(
    strategy: Strategy, start_date: date, weeks: int, protocol: BreakProtocol = BreakProtocol.NONE
) -> PhaseCalendar:
    """Blocos semanais a partir de `start_date`. Só o cutting alterna com
    pausas (MATADOR/ICECAP); bulking e manutenção são um bloco só."""
    if weeks <= 0:
        raise ValueError(LIMIT_WEEKS_NOT_POSITIVE)
    fase = PHASE_FOR_STRATEGY[strategy]
    if strategy != Strategy.CUTTING or protocol == BreakProtocol.NONE:
        bloco = _block(fase, start_date, weeks, 0)
        avisos: tuple[str, ...] = ()
        if strategy == Strategy.CUTTING and weeks > MAX_CUT_WEEKS_WITHOUT_BREAK:
            avisos = (WARN_EXCEEDS_MAX_CUT_WEEKS,)
        return PhaseCalendar(blocks=(bloco,), protocol=BreakProtocol.NONE, warnings=avisos)

    on_weeks, off_weeks = BREAK_PROTOCOL_WEEKS[protocol]
    blocos: list[PhaseBlock] = []
    restante = weeks
    inicio = start_date
    indice = 0
    em_deficit = True
    while restante > 0:
        duracao = min(on_weeks if em_deficit else off_weeks, restante)
        blocos.append(_block(PhaseKind.CUTTING if em_deficit else PhaseKind.DIET_BREAK, inicio, duracao, indice))
        inicio = inicio + timedelta(days=duracao * DAYS_PER_WEEK)
        restante -= duracao
        indice += 1
        em_deficit = not em_deficit
    return PhaseCalendar(blocks=tuple(blocos), protocol=protocol)


def phase_for_date(blocks: Sequence[PhaseBlock], day: date) -> Optional[PhaseBlock]:
    for bloco in blocks:
        if bloco.contains(day):
            return bloco
    return None


def refeed_dates(blocks: Sequence[PhaseBlock], weekdays: Sequence[int]) -> tuple[date, ...]:
    """Dias de refeed: dentro dos blocos de CUTTING, os dias da semana pedidos
    (0 = segunda … 6 = domingo; tipicamente `REFEED_DAYS_PER_WEEK_DEFAULT` dias)."""
    escolhidos = {int(d) for d in weekdays}
    datas: list[date] = []
    for bloco in blocks:
        if bloco.kind != PhaseKind.CUTTING:
            continue
        dia = bloco.start
        while dia <= bloco.end:
            if dia.weekday() in escolhidos:
                datas.append(dia)
            dia += timedelta(days=1)
    return tuple(datas)


def stop_body_fat_default(strategy: Strategy, sex: Optional[Sex]) -> Optional[float]:
    """% de gordura sugerido para encerrar a fase (D-C). Manutenção/OTHER -> None."""
    if sex is None:
        return None
    if strategy == Strategy.BULKING:
        return BULK_STOP_BODY_FAT_PERCENT.get(sex)
    if strategy == Strategy.CUTTING:
        return CUT_STOP_BODY_FAT_PERCENT.get(sex)
    return None


def resolve_stop_body_fat(
    strategy: Strategy, sex: Optional[Sex], plan_goal: Any = None
) -> tuple[Optional[float], bool]:
    """%G de parada efetivo (D-C): o do plano quando definido; senão o default
    do motor para a estratégia e o sexo. Devolve `(valor, veio_do_default)`;
    sem plano nem default (manutenção, sexo OTHER/None) -> `(None, False)`."""
    if plan_goal is not None:
        return float(plan_goal), False
    padrao = stop_body_fat_default(strategy, sex)
    return padrao, padrao is not None


def should_stop(strategy: Strategy, body_fat_percent: Any, stop_at_percent: Any) -> Optional[bool]:
    """Bulking para ao ATINGIR o teto de %G; cutting, ao atingir o piso."""
    if body_fat_percent is None or stop_at_percent is None:
        return None
    atual = float(body_fat_percent)
    limite = float(stop_at_percent)
    if strategy == Strategy.BULKING:
        return atual >= limite
    if strategy == Strategy.CUTTING:
        return atual <= limite
    return None

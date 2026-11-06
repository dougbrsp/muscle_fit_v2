"""Peso de tendência (EMA com meia-vida) e inclinação para o gasto adaptativo.

Dono único do peso de tendência do modo adaptativo (mapa
`docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`). Spec §8 em
`.omc/research/NUTRICAO-spec-2026-11-05.md`.

Regras:

- EMA por dia local com meia-vida `WEIGHT_HALF_LIFE_DAYS`; `WEIGHT_ALPHA` é
  DERIVADO dela (1 − 2^(−1/meia-vida)), nunca escrito à mão. Dia sem pesagem
  carrega a tendência anterior (não cria pesagem fictícia).
- Mais de uma pesagem no mesmo dia: vale a MÉDIA das pesagens válidas do dia
  (uma observação por dia; pesagens repetidas não pesam mais na EMA).
- Aquecimento: a EMA é semeada pela primeira pesagem do histórico e percorre
  TODO o histórico anterior à janela; a inclinação dos últimos
  `TREND_WINDOW_DAYS` dias já nasce de uma tendência aquecida. Sem isso a EMA
  parte do primeiro ponto da janela e subestima a inclinação nas primeiras
  semanas (o risco registrado em `adaptive_tdee.py`).
- Valores impossíveis (fora de `WEIGHT_PLAUSIBLE_RANGE_KG`, não numéricos) são
  ignorados e contados. Outlier: pesagem do dia a mais de
  `OUTLIER_DEVIATION_KG` da tendência vigente. É SINALIZADO e NÃO move a EMA;
  se o desvio persiste em `OUTLIER_ACCEPT_STREAK` pesagens seguidas, a última é
  aceita como mudança de patamar (a tendência é re-semeada nela e o ponto leva
  `level_shift`).
- Inclinação (kg/dia): regressão linear dos últimos `TREND_WINDOW_DAYS` pontos
  da TENDÊNCIA, reaproveitando `adaptive_tdee.weight_trend` (sem alterá-lo). Depois
  de uma mudança de patamar a regressão parte do degrau (não o mede como
  inclinação); com poucos dias depois dele, a inclinação fica indisponível.
- Suficiência: histórico mínimo `MIN_HISTORY_DAYS`, ideal
  `IDEAL_HISTORY_DAYS`; pesagens por semana: mínimo duro
  `MIN_WEIGHINS_PER_WEEK`, recomendado `RECOMMENDED_WEIGHINS_PER_WEEK`.

Defaults de desenho, SEM fonte bibliográfica (a confirmar pelo usuário):
`WEIGHT_PLAUSIBLE_RANGE_KG`, `OUTLIER_DEVIATION_KG`, `OUTLIER_ACCEPT_STREAK`,
`MIN_WEIGHINS_PER_WEEK`. Vêm da spec §8: meia-vida 7 dias, janela 21 dias,
histórico 14/21 dias, ≥5 pesagens por semana.

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final, Iterable, Optional, Sequence

from .adaptive_tdee import WeightTrend, weight_trend
from .anthropometrics import positive_number
from .energy_density import DAYS_PER_WEEK

# Spec §8.
WEIGHT_HALF_LIFE_DAYS: Final[float] = 7.0
WEIGHT_ALPHA: Final[float] = 1.0 - 2.0 ** (-1.0 / WEIGHT_HALF_LIFE_DAYS)
TREND_WINDOW_DAYS: Final[int] = 21
MIN_HISTORY_DAYS: Final[int] = 14
IDEAL_HISTORY_DAYS: Final[int] = TREND_WINDOW_DAYS
RECOMMENDED_WEIGHINS_PER_WEEK: Final[float] = 5.0

# Defaults de desenho (a confirmar).
WEIGHT_PLAUSIBLE_RANGE_KG: Final[tuple[float, float]] = (20.0, 400.0)
OUTLIER_DEVIATION_KG: Final[float] = 3.0
OUTLIER_ACCEPT_STREAK: Final[int] = 3
MIN_WEIGHINS_PER_WEEK: Final[float] = 2.0

FLAG_WEIGHT_MISSING = "weight_missing"
FLAG_WEIGHT_INVALID_READINGS = "weight_invalid_readings_ignored"
FLAG_WEIGHT_OUTLIER = "weight_outlier"
FLAG_WEIGHT_LEVEL_SHIFT = "weight_level_shift"
FLAG_WEIGHT_HISTORY_SHORT = "weight_history_short"
FLAG_WEIGHT_HISTORY_BELOW_IDEAL = "weight_history_below_ideal"
FLAG_WEIGHINS_BELOW_MIN = "weighins_below_min"
FLAG_WEIGHINS_BELOW_RECOMMENDED = "weighins_below_recommended"


@dataclass(frozen=True)
class TrendState:
    """Estado da EMA (o que persiste entre dias)."""

    trend_kg: Optional[float] = None
    first_day: Optional[date] = None
    last_day: Optional[date] = None
    outlier_streak: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trend_kg": self.trend_kg,
            "first_day": self.first_day.isoformat() if self.first_day else None,
            "last_day": self.last_day.isoformat() if self.last_day else None,
            "outlier_streak": self.outlier_streak,
        }


@dataclass(frozen=True)
class TrendPoint:
    """Um dia da série: pesagem do dia (média das válidas), tendência e marcas."""

    day: date
    measured_kg: Optional[float]
    trend_kg: Optional[float]
    outlier: bool = False
    level_shift: bool = False
    invalid_readings: int = 0

    @property
    def used(self) -> bool:
        """A pesagem do dia entrou na EMA (existe e não foi descartada como outlier)."""
        return self.measured_kg is not None and not self.outlier

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "measured_kg": self.measured_kg,
            "trend_kg": self.trend_kg,
            "outlier": self.outlier,
            "level_shift": self.level_shift,
            "invalid_readings": self.invalid_readings,
        }


def _valid_weight(value: Any) -> Optional[float]:
    peso = positive_number(value)
    if peso is None:
        return None
    minimo, maximo = WEIGHT_PLAUSIBLE_RANGE_KG
    if peso < minimo or peso > maximo:
        return None
    return peso


def trend_step(state: TrendState, day: date, readings_kg: Iterable[Any] = ()) -> tuple[TrendState, TrendPoint]:
    """Avança a EMA um dia. `readings_kg` são as pesagens do dia local (0..n)."""
    brutas = list(readings_kg)
    validas = [p for p in (_valid_weight(v) for v in brutas) if p is not None]
    invalidas = len(brutas) - len(validas)
    if not validas:
        novo = TrendState(state.trend_kg, state.first_day, day, state.outlier_streak)
        return novo, TrendPoint(day, None, state.trend_kg, invalid_readings=invalidas)
    medida = sum(validas) / len(validas)
    primeiro = state.first_day or day
    if state.trend_kg is None:
        return TrendState(medida, primeiro, day, 0), TrendPoint(day, medida, medida, invalid_readings=invalidas)
    if abs(medida - state.trend_kg) > OUTLIER_DEVIATION_KG:
        sequencia = state.outlier_streak + 1
        if sequencia < OUTLIER_ACCEPT_STREAK:
            novo = TrendState(state.trend_kg, primeiro, day, sequencia)
            return novo, TrendPoint(day, medida, state.trend_kg, outlier=True, invalid_readings=invalidas)
        novo = TrendState(medida, primeiro, day, 0)
        return novo, TrendPoint(day, medida, medida, level_shift=True, invalid_readings=invalidas)
    tendencia = WEIGHT_ALPHA * medida + (1.0 - WEIGHT_ALPHA) * state.trend_kg
    return TrendState(tendencia, primeiro, day, 0), TrendPoint(day, medida, tendencia, invalid_readings=invalidas)


@dataclass(frozen=True)
class WeightWindow:
    """Inclinação da janela e indicadores de suficiência."""

    slope: WeightTrend
    weighin_days: int
    weighins_per_week: float
    history_days: int
    flags: tuple[str, ...] = ()
    sufficient: bool = False

    @property
    def slope_kg_per_day(self) -> Optional[float]:
        return self.slope.slope_kg_per_day

    def to_dict(self) -> dict[str, Any]:
        return {
            "slope": self.slope.to_dict(),
            "weighin_days": self.weighin_days,
            "weighins_per_week": self.weighins_per_week,
            "history_days": self.history_days,
            "flags": list(self.flags),
            "sufficient": self.sufficient,
        }


def assess_window(points: Sequence[TrendPoint], history_days: int) -> WeightWindow:
    """Regressão da tendência + suficiência. `points` = um ponto por dia da janela,
    em ordem; `history_days` = dias corridos desde a primeira pesagem."""
    # Mudança de patamar: a EMA re-semeada vira um degrau; a regressão parte do degrau (último `level_shift`) para
    # não medi-lo como inclinação. Com menos de `MIN_WEIGHT_SPAN_DAYS` depois dele, a inclinação fica indisponível.
    inicio = max((i for i, p in enumerate(points) if p.level_shift), default=0)
    inclinacao = weight_trend([p.trend_kg for p in points[inicio:]])
    dias_pesados = sum(1 for p in points if p.used)
    por_semana = dias_pesados / (len(points) / DAYS_PER_WEEK) if points else 0.0
    flags: list[str] = []
    if history_days <= 0:
        flags.append(FLAG_WEIGHT_MISSING)
    elif history_days < MIN_HISTORY_DAYS:
        flags.append(FLAG_WEIGHT_HISTORY_SHORT)
    elif history_days < IDEAL_HISTORY_DAYS:
        flags.append(FLAG_WEIGHT_HISTORY_BELOW_IDEAL)
    if history_days > 0:
        if por_semana < MIN_WEIGHINS_PER_WEEK:
            flags.append(FLAG_WEIGHINS_BELOW_MIN)
        elif por_semana < RECOMMENDED_WEIGHINS_PER_WEEK:
            flags.append(FLAG_WEIGHINS_BELOW_RECOMMENDED)
    if any(p.outlier for p in points):
        flags.append(FLAG_WEIGHT_OUTLIER)
    if any(p.level_shift for p in points):
        flags.append(FLAG_WEIGHT_LEVEL_SHIFT)
    if any(p.invalid_readings for p in points):
        flags.append(FLAG_WEIGHT_INVALID_READINGS)
    suficiente = (
        inclinacao.slope_kg_per_day is not None and history_days >= MIN_HISTORY_DAYS and por_semana >= MIN_WEIGHINS_PER_WEEK
    )
    return WeightWindow(inclinacao, dias_pesados, por_semana, max(history_days, 0), tuple(flags), suficiente)


@dataclass(frozen=True)
class WeightTrendSeries:
    """Série de tendência dos últimos dias até `end` + avaliação da janela."""

    points: tuple[TrendPoint, ...]
    window: WeightWindow
    state: TrendState

    def to_dict(self) -> dict[str, Any]:
        return {
            "points": [p.to_dict() for p in self.points],
            "window": self.window.to_dict(),
            "state": self.state.to_dict(),
        }


def history_days_of(state: TrendState, day: date) -> int:
    """Dias corridos (inclusive) da primeira pesagem até `day`; 0 sem pesagem."""
    if state.first_day is None:
        return 0
    return (day - state.first_day).days + 1


def weight_trend_series(
    readings: Iterable[tuple[date, Any]],
    end: date,
    window_days: int = TREND_WINDOW_DAYS,
) -> WeightTrendSeries:
    """Percorre TODO o histórico de pesagens até `end` (aquecimento) e devolve os
    últimos `window_days` dias. Dias antes da primeira pesagem saem sem tendência."""
    por_dia: dict[date, list[Any]] = defaultdict(list)
    for dia, kg in readings:
        if dia <= end:
            por_dia[dia].append(kg)
    estado = TrendState()
    por_data: dict[date, TrendPoint] = {}
    if por_dia:
        dia = min(por_dia)
        while dia <= end:
            estado, ponto = trend_step(estado, dia, por_dia.get(dia, ()))
            por_data[dia] = ponto
            dia += timedelta(days=1)
    janela = tuple(
        por_data.get(d) or TrendPoint(d, None, None)
        for d in (end - timedelta(days=window_days - 1 - i) for i in range(window_days))
    )
    return WeightTrendSeries(janela, assess_window(janela, history_days_of(estado, end)), estado)

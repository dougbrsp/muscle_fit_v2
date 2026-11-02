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
- Projeção até o alvo (N1): `project_goal`. O ritmo é % do peso por semana,
  composto (o peso muda, o ritmo em kg também): semanas = ln(alvo/atual) /
  ln(1 ∓ ritmo). Para alvo de %G, o peso final supõe a MLG atual constante
  (`target_weight_for_body_fat`): é o peso MÍNIMO (cutting) ou o mais baixo (bulking) que
  cumpre o alvo. No cutting a MLG cai um pouco e o peso final real fica acima, e no
  bulking a MLG cresce e o peso final real é maior: o prazo é OTIMISTA (limite inferior).
  O resultado carrega `lean_mass_constant` e o motivo `projection_lean_mass_constant`.
  Sem dado não há número: devolve o motivo.

O motor PROPÕE o calendário; admin/PT publica (decisão D4). Tudo em datas
civis; a data de início é argumento (sem relógio).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Any, Final, Mapping, Optional, Sequence

from .anthropometrics import WEIGHT_KG_RANGE, Sex, positive_number
from .body_composition import PERCENT, body_fat_is_valid, ffm_kg, target_weight_for_body_fat
from .energy_density import DAYS_PER_WEEK
from .strategy import Strategy, StrategyParams, weekly_rate_share_for, weekly_rate_share_range_for

MATADOR_ON_WEEKS: Final[int] = 2
MATADOR_OFF_WEEKS: Final[int] = 2
ICECAP_ON_WEEKS: Final[int] = 3
ICECAP_OFF_WEEKS: Final[int] = 1
REFEED_DAYS_PER_WEEK_DEFAULT: Final[int] = 2
MAX_CUT_WEEKS_WITHOUT_BREAK: Final[int] = 8

# Decisão D-C: % de gordura em que o motor sugere parar (default editável).
BULK_STOP_BODY_FAT_PERCENT: Final[Mapping[Sex, float]] = {Sex.MALE: 15.0, Sex.FEMALE: 25.0}
CUT_STOP_BODY_FAT_PERCENT: Final[Mapping[Sex, float]] = {Sex.MALE: 10.0, Sex.FEMALE: 18.0}

# Projeção: tolerâncias para dizer "no alvo" (default de desenho): 0,1 kg é a resolução
# de uma balança doméstica; 0,5 ponto percentual é a ordem do erro de bioimpedância.
PROJECTION_WEIGHT_TOLERANCE_KG: Final[float] = 0.1
PROJECTION_BODY_FAT_TOLERANCE_PERCENT: Final[float] = 0.5
# Horizonte máximo da projeção: 10 anos (default de desenho); além disso o prazo não é informação útil.
PROJECTION_MAX_WEEKS: Final[float] = 520.0

REASON_MISSING_WEIGHT = "projection_missing_weight"
REASON_MISSING_BODY_FAT = "projection_missing_body_fat"
REASON_MISSING_TARGET = "projection_missing_target"
REASON_TWO_TARGETS = "projection_two_targets"
REASON_INVALID_TARGET = "projection_invalid_target"
REASON_MISSING_RATE = "projection_missing_rate"
REASON_MISSING_START = "projection_missing_start"
REASON_START_WRONG_SIDE = "projection_start_on_wrong_side"
REASON_MISSING_TARGET_WEIGHT = "projection_missing_target_weight"
REASON_BEYOND_HORIZON = "projection_beyond_horizon"
REASON_LEAN_MASS_CONSTANT = "projection_lean_mass_constant"
REASON_TARGET_AGAINST_STRATEGY = "projection_target_against_strategy"
REASON_TARGET_MOVES_WITHOUT_STRATEGY = "projection_maintenance_with_distant_target"

WARN_EXCEEDS_MAX_CUT_WEEKS = "exceeds_max_cut_weeks_without_break"
LIMIT_WEEKS_NOT_POSITIVE = "weeks_must_be_positive"


class PhaseKind(str, Enum):
    CUTTING = "cutting"
    BULKING = "bulking"
    MAINTENANCE = "maintenance"
    DIET_BREAK = "diet_break"
    REFEED = "refeed"


class GoalKind(str, Enum):
    WEIGHT = "weight"
    BODY_FAT = "body_fat"


class ProjectionStatus(str, Enum):
    PROJECTED = "projected"
    GOAL_REACHED = "goal_reached"
    INCOHERENT = "incoherent_with_strategy"
    INSUFFICIENT_DATA = "insufficient_data"


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


# ---------------------------------------------------------------------------
# Projeção até o alvo de peso ou de % de gordura (N1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoalProjection:
    """Projeção até o alvo. `current`/`target` na unidade do alvo (kg ou %G);
    `target_weight_kg` é o peso em que o alvo se cumpre; `weeks`/`eta_date` só
    com PROJECTED (0 e a data de hoje quando já atingido); `progress_percent`
    precisa do ponto de partida (0..100). `is_minimum_estimate`: alvo de %G com a MLG
    atual suposta constante, então `weeks` é um limite inferior (prazo otimista)."""

    status: ProjectionStatus
    goal_kind: Optional[GoalKind]
    current: Optional[float] = None
    target: Optional[float] = None
    target_weight_kg: Optional[float] = None
    weeks: Optional[float] = None
    eta_date: Optional[date] = None
    progress_percent: Optional[float] = None
    weekly_rate_share: Optional[float] = None
    reasons: tuple[str, ...] = ()
    is_minimum_estimate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "goal_kind": self.goal_kind.value if self.goal_kind else None,
            "current": self.current,
            "target": self.target,
            "target_weight_kg": self.target_weight_kg,
            "weeks": self.weeks,
            "eta_date": self.eta_date.isoformat() if self.eta_date else None,
            "progress_percent": self.progress_percent,
            "weekly_rate_share": self.weekly_rate_share,
            "reasons": list(self.reasons),
            "is_minimum_estimate": self.is_minimum_estimate,
        }


def _insufficient(kind: Optional[GoalKind], *reasons: str) -> GoalProjection:
    return GoalProjection(status=ProjectionStatus.INSUFFICIENT_DATA, goal_kind=kind, reasons=tuple(reasons))


def _progress_percent(start: Optional[float], current: float, target: float, wants_loss: bool) -> Optional[float]:
    """Parte do caminho já feita (0..100). Partida do lado errado do alvo (ou igual a ele)
    não define caminho: None."""
    if start is None:
        return None
    lado_certo = start > target if wants_loss else start < target
    if not lado_certo:
        return None
    avancado = (start - current) / (start - target)
    return min(max(avancado, 0.0), 1.0) * PERCENT


def _explicit_rate(value: Any, bounds: Optional[tuple[float, float]]) -> Optional[float]:
    """Ritmo informado: número finito dentro da faixa da estratégia; senão None."""
    ritmo = positive_number(value)
    if ritmo is None or bounds is None or not bounds[0] <= ritmo <= bounds[1]:
        return None
    return ritmo


def project_goal(
    params: StrategyParams,
    current_weight_kg: Any,
    goal_weight_kg: Any = None,
    goal_body_fat_percent: Any = None,
    current_body_fat_percent: Any = None,
    start_weight_kg: Any = None,
    start_body_fat_percent: Any = None,
    weekly_rate_share: Any = None,
    as_of: Optional[date] = None,
    tdee_kcal: Any = None,
    fat_mass_kg: Any = None,
) -> GoalProjection:
    """Quanto falta, em quanto tempo e quanto do caminho já foi feito.

    Exatamente um alvo (peso ou %G). O ritmo (fração do peso por semana) é o informado em
    `weekly_rate_share`, que precisa estar na faixa da estratégia (senão
    `REASON_MISSING_RATE`), ou o de `strategy.weekly_rate_share_for` (cutting: o do plano;
    bulking: o do superávit do plano quando há `tdee_kcal`, peso e `fat_mass_kg`, senão o piso
    da faixa de ganho). Manutenção não move o peso: só "atingido" (dentro da tolerância) ou
    incoerente. Alvo do lado errado da estratégia é incoerente, salvo se o ponto de partida mostra
    que o alvo já foi ultrapassado no sentido certo. Passa de `PROJECTION_MAX_WEEKS`: sem prazo."""
    strategy = params.strategy
    tem_peso, tem_gordura = goal_weight_kg is not None, goal_body_fat_percent is not None
    if tem_peso and tem_gordura:
        return _insufficient(None, REASON_TWO_TARGETS)
    if not tem_peso and not tem_gordura:
        return _insufficient(None, REASON_MISSING_TARGET)
    kind = GoalKind.WEIGHT if tem_peso else GoalKind.BODY_FAT
    peso = positive_number(current_weight_kg)
    if peso is None:
        return _insufficient(kind, REASON_MISSING_WEIGHT)

    if kind == GoalKind.WEIGHT:
        alvo = positive_number(goal_weight_kg)
        if alvo is None or not WEIGHT_KG_RANGE[0] <= alvo <= WEIGHT_KG_RANGE[1]:
            return _insufficient(kind, REASON_INVALID_TARGET)
        atual = peso
        partida = positive_number(start_weight_kg)
        tolerancia = PROJECTION_WEIGHT_TOLERANCE_KG
        peso_alvo: Optional[float] = alvo
    else:
        if not body_fat_is_valid(goal_body_fat_percent):
            return _insufficient(kind, REASON_INVALID_TARGET)
        if current_body_fat_percent is None or not body_fat_is_valid(current_body_fat_percent):
            return _insufficient(kind, REASON_MISSING_BODY_FAT)
        alvo = float(goal_body_fat_percent)
        atual = float(current_body_fat_percent)
        partida = float(start_body_fat_percent) if body_fat_is_valid(start_body_fat_percent) else None
        tolerancia = PROJECTION_BODY_FAT_TOLERANCE_PERCENT
        peso_alvo = target_weight_for_body_fat(ffm_kg(peso, atual), alvo)

    def _result(status: ProjectionStatus, **extra: Any) -> GoalProjection:
        return GoalProjection(
            status=status,
            goal_kind=kind,
            current=atual,
            target=alvo,
            target_weight_kg=peso_alvo,
            is_minimum_estimate=kind == GoalKind.BODY_FAT,
            **extra,
        )

    def _reached() -> GoalProjection:
        return _result(ProjectionStatus.GOAL_REACHED, weeks=0.0, eta_date=as_of, progress_percent=PERCENT)

    folga = atual - alvo  # > 0: acima do alvo
    if abs(folga) <= tolerancia:
        return _reached()
    if strategy == Strategy.MAINTENANCE:
        return _result(ProjectionStatus.INCOHERENT, reasons=(REASON_TARGET_MOVES_WITHOUT_STRATEGY,))

    quer_perder = strategy == Strategy.CUTTING
    passou = folga < 0 if quer_perder else folga > 0
    if passou:
        partida_no_sentido = partida is not None and (partida > alvo if quer_perder else partida < alvo)
        if partida_no_sentido:
            return _reached()
        return _result(ProjectionStatus.INCOHERENT, reasons=(REASON_TARGET_AGAINST_STRATEGY,))

    if weekly_rate_share is not None:
        ritmo = _explicit_rate(weekly_rate_share, weekly_rate_share_range_for(strategy))
    else:
        ritmo = positive_number(weekly_rate_share_for(params, tdee_kcal, peso, fat_mass_kg))
    if ritmo is None or ritmo >= 1.0:
        return _result(ProjectionStatus.INSUFFICIENT_DATA, reasons=(REASON_MISSING_RATE,))
    if peso_alvo is None:
        return _result(ProjectionStatus.INSUFFICIENT_DATA, reasons=(REASON_MISSING_TARGET_WEIGHT,))
    fator = (1.0 - ritmo) if quer_perder else (1.0 + ritmo)
    passo = math.log(fator)  # 0 quando o ritmo derivado é menor que a resolução do float
    semanas = math.log(peso_alvo / peso) / passo if passo != 0.0 else math.inf
    if not math.isfinite(semanas) or semanas > PROJECTION_MAX_WEEKS:
        return _result(ProjectionStatus.INSUFFICIENT_DATA, reasons=(REASON_BEYOND_HORIZON,))
    eta = as_of + timedelta(days=math.ceil(semanas * DAYS_PER_WEEK)) if as_of is not None else None
    progresso = _progress_percent(partida, atual, alvo, quer_perder)
    motivos: list[str] = []
    if partida is None:
        motivos.append(REASON_MISSING_START)
    elif progresso is None:
        motivos.append(REASON_START_WRONG_SIDE)
    if kind == GoalKind.BODY_FAT:
        motivos.append(REASON_LEAN_MASS_CONSTANT)
    return _result(
        ProjectionStatus.PROJECTED,
        weeks=semanas,
        eta_date=eta,
        progress_percent=progresso,
        weekly_rate_share=ritmo,
        reasons=tuple(motivos),
    )

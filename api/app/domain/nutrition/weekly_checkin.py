"""Check-in semanal da nutrição (reformulação N7, D7 a D10): avalia a semana FECHADA e PROPÕE metas novas.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §83; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão `checkin_semanal`). Plano:
`.omc/plans/nutricao-reformulacao-2026-11-02.md` §3 (lote N7).

O que o motor faz (tudo Python puro, sem banco, sem relógio, sem I/O):

1. **Semana.** 7 dias da semana de segunda a domingo JÁ FECHADA, dentro de uma janela de `BODY_WINDOW_DAYS` dias
   que termina no domingo (peso, %G e massa muscular olham para trás). A aderência e a ingestão média usam SÓ
   os dias completos (`weekly_budget.weekly_adherence`: meta calculável e refeições de total completo).
2. **Dados suficientes (D9).** Menos de `MIN_COMPLETE_DAYS` dias completos, pesagens insuficientes para a
   tendência (`MIN_WEIGHT_READINGS_FOR_TREND` na janela de `WEIGHT_WINDOW_DAYS`, ao menos
   `MIN_WEIGHT_READINGS_IN_WEEK` dentro da semana, cobrindo `adaptive_tdee.MIN_WEIGHT_SPAN_DAYS`) ou sem massa
   gorda para converter peso em energia = veredito INCONCLUSIVO: as metas ficam como estão e `missing` diz o que
   faltou. Nunca se corta calorias sem dado.
3. **Peso guia (D10).** O ritmo vem da tendência por mínimos quadrados do `adaptive_tdee.weight_trend` (nunca uma
   pesagem isolada) e é comparado com o ritmo ESPERADO pelo saldo planejado da semana (média das metas menos
   a média do gasto dos mesmos dias, na densidade energética de `energy_density`). Banda de "no ritmo":
   `ON_TRACK_TOLERANCE_SHARE` do ritmo esperado, nunca menos que `RATE_NOISE_FLOOR_SHARE` do peso (ruído de
   balança). Manutenção: o peso deve ficar na banda de `strategy.MAINT_TOLERANCE_SHARE` do gasto convertida em
   kg/semana; fora dela o veredito é ACIMA/ABAIXO DA BANDA e o motor SÓ SUGERE revisar o plano (a manutenção não
   tem parâmetro de balanço para ajustar).
4. **Bioimpedância (D10), ESTIMADA e ruidosa.** Só corrige com tendência de 2 ou mais semanas
   (`MIN_BIA_READINGS` leituras cobrindo `MIN_BIA_SPAN_DAYS` dias); uma semana isolada é ignorada (código
   `checkin_bia_short_trend`). No cutting, perda de massa magra acima de `LEAN_LOSS_LIMIT_SHARE_PER_WEEK` da massa
   magra por semana reduz o déficit (e impede cortar mais); no bulking, ganho de gordura acima de
   `BULK_FAT_SHARE_OF_GAIN_LIMIT` do ganho de peso (e acima do piso de ruído) reduz o superávit.
5. **Ajuste.** Corrige `CORRECTION_FRACTION` da diferença, limitado a `MAX_KCAL_STEP_SHARE` do gasto base por semana
   e descartado abaixo de `MIN_KCAL_STEP` (ruído). Respeita as faixas da estratégia (ritmo do cutting, superávit do
   bulking por nível), o teto de déficit e o piso de ingestão (`strategy.CUT_*`), `manual_targets.KCAL_BLOCK_MIN`, a
   TMB e `plan.check_cycle_floors`. Baixa aderência não vira corte: o ajuste só ocorre com a aderência calórica
   da semana dentro da tolerância do `weekly_budget` (D9). Metas novas = o MESMO plano com o parâmetro ajustado
   (`weekly_rate_share` no cutting, `surplus_share` no bulking), então o `macro_split`/`MacroPriority`, o ciclo de
   dias e o modo do treino seguem do plano. Com gramas manuais, a diferença de kcal vai em carboidrato e gordura
   na proporção atual (proteína fixa) e as gramas passam por `check_manual_targets` (bloqueio = não propõe).
6. **Alvo atingido.** Peso-alvo ou %G de parada (`phases.resolve_stop_body_fat`/`should_stop`, mesma regra do
   `plan.goal_summary`) atingidos geram o veredito ALVO ATINGIDO e a sugestão de revisar o plano; sem ajuste.

Constantes sem fonte bibliográfica são DEFAULTS DE DESENHO (a confirmar pelo usuário): nenhuma citação inventada.
As faixas, o piso e os tetos vêm dos donos (`strategy`, `manual_targets`, `weekly_budget`, `adaptive_tdee`,
`phases`, `energy_density`).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Any, Final, Optional, Sequence

from . import bmr as bmr_mod
from . import energy_expenditure as ee
from .adaptive_tdee import MIN_WEIGHT_SPAN_DAYS, PREFERRED_WINDOW_DAYS, AdaptiveTdee, observed_tdee, weight_trend
from .anthropometrics import Subject, positive_number
from .body_composition import fat_mass_kg, resolved_fat_mass_kg
from .daily_target import DayContext, compute_daily_target
from .energy_density import DAYS_PER_WEEK, daily_kcal_for_weekly_rate, weekly_kg_for_daily_kcal
from .macros import KCAL_PER_G_CARB, KCAL_PER_G_FAT, Macros, MacroTargets, kcal_from_macros
from .manual_targets import KCAL_BLOCK_MAX, KCAL_BLOCK_MIN, RangeStatus
from .phases import PROJECTION_WEIGHT_TOLERANCE_KG, resolve_stop_body_fat, should_stop
from .plan import (
    ManualMacros,
    PlanSettings,
    PlanValidation,
    build_plan_settings,
    check_cycle_floors,
    check_plan_manual_targets,
    manual_save_errors,
)
from .strategy import (
    CUT_MAX_DEFICIT_SHARE,
    CUT_MIN_INTAKE_BMR_MULTIPLE,
    CUT_WEEKLY_RATE_SHARE_RANGE,
    MAINT_TOLERANCE_SHARE,
    Strategy,
    kcal_target,
    surplus_range_for,
)
from .weekly_budget import MIN_LOGGED_DAYS_FOR_WEEK, weekly_adherence

# --- Janelas e mínimos de dados (D9, D10) -------------------------------------------------------------------------
WEIGHT_WINDOW_DAYS: Final[int] = 14  # a semana fechada e a anterior: a inclinação precisa cobrir `MIN_WEIGHT_SPAN_DAYS`
BODY_WINDOW_DAYS: Final[int] = PREFERRED_WINDOW_DAYS  # peso, %G, massa muscular e TDEE observado: 28 dias até o domingo
MIN_COMPLETE_DAYS: Final[int] = MIN_LOGGED_DAYS_FOR_WEEK  # a "semana válida" do `weekly_budget`
MIN_WEIGHT_READINGS_FOR_TREND: Final[int] = 3  # default de desenho: nunca uma pesagem isolada
MIN_WEIGHT_READINGS_IN_WEEK: Final[int] = 1
MIN_BIA_READINGS: Final[int] = 3  # default de desenho: tendência, não um ponto
MIN_BIA_SPAN_DAYS: Final[int] = 2 * DAYS_PER_WEEK  # D10: tendência de 2 ou mais semanas
MIN_READINGS_FOR_GOAL: Final[int] = 2  # alvo de peso ou de %G só vale com 2 leituras recentes (bioimpedância é ruidosa)

# --- Veredito e ajuste (todos DEFAULTS DE DESENHO, sem fonte bibliográfica) -----------------------------------------
ON_TRACK_TOLERANCE_SHARE: Final[float] = 0.25  # do ritmo esperado
RATE_NOISE_FLOOR_SHARE: Final[float] = 0.002  # do peso por semana (~0,15 kg a 75 kg): menos que isso é ruído de balança
CORRECTION_FRACTION: Final[float] = 0.5  # corrige metade da diferença por semana
MAX_KCAL_STEP_SHARE: Final[float] = 0.05  # passo máximo semanal: 5% do gasto base (mesma ordem de `MAINT_TOLERANCE_SHARE`)
MIN_KCAL_STEP: Final[float] = 50.0  # abaixo disso o ajuste é ruído e não se propõe
BIA_CORRECTION_FRACTION: Final[float] = 0.5  # da fração do passo máximo usada na correção pela bioimpedância
LEAN_LOSS_LIMIT_SHARE_PER_WEEK: Final[float] = 0.0025  # da massa magra por semana (cutting)
BULK_FAT_SHARE_OF_GAIN_LIMIT: Final[float] = 0.5  # parte do ganho de peso que é gordura (bulking)
BULK_FAT_GAIN_NOISE_FLOOR_KG_PER_WEEK: Final[float] = 0.1  # abaixo disso o ganho de gordura é ruído de bioimpedância
RATE_DECIMALS: Final[int] = 4  # `nutrition_plans.weekly_rate_share`/`surplus_share` são DECIMAL(6,4)

# --- Ciclo de vida da proposta (D7, D8) -----------------------------------------------------------------------------
PROPOSAL_VALIDITY_DAYS: Final[int] = 3  # D7: sem resposta em 3 dias a proposta expira e as metas atuais seguem
DEFAULT_CHECKIN_WEEKDAY: Final[int] = 0  # D8: segunda-feira (0 = segunda, como `date.weekday()`)
CHECKIN_WEEKDAY_RANGE: Final[tuple[int, int]] = (0, 6)


class CheckinVerdict(str, Enum):
    ON_TRACK = "on_track"
    SLOW = "slow"
    FAST = "fast"
    ABOVE_BAND = "above_band"  # manutenção: o peso subiu além da banda
    BELOW_BAND = "below_band"  # manutenção: o peso caiu além da banda
    INCONCLUSIVE = "inconclusive"
    GOAL_REACHED = "goal_reached"


class CheckinStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"
    INFORMATIVE = "informative"  # sem mudança de meta a confirmar (no ritmo, inconclusivo, alvo atingido...)


# Códigos estáveis (`labels.limitation_label` rotula). `missing`: o que faltou (D9); `reasons`: por que o motor decidiu.
MISSING_FEW_COMPLETE_DAYS = "checkin_few_complete_days"
MISSING_NO_TARGETS = "checkin_no_targets"
MISSING_FEW_WEIGHINGS = "checkin_few_weighings"
MISSING_NO_WEIGHING_IN_WEEK = "checkin_no_weighing_in_week"
MISSING_WEIGHT_SPAN_SHORT = "checkin_weight_span_short"
MISSING_FAT_MASS = "checkin_missing_fat_mass"
MISSING_SUBJECT = "checkin_missing_subject"
REASON_ADHERENCE_OFF = "checkin_adherence_off"
REASON_ADJUSTMENT_BELOW_MIN = "checkin_adjustment_below_minimum"
REASON_AT_STRATEGY_LIMIT = "checkin_at_strategy_limit"
REASON_BLOCKED_BY_CYCLE = "checkin_blocked_by_cycle_floor"
REASON_BLOCKED_BY_MANUAL = "checkin_blocked_by_manual_check"
REASON_PROPOSAL_INVALID = "checkin_proposal_invalid"
REASON_BIA_SHORT_TREND = "checkin_bia_short_trend"
REASON_LEAN_LOSS = "checkin_lean_loss"
REASON_FAT_GAIN = "checkin_fat_gain"
REASON_BIA_HOLD = "checkin_bia_hold"
REASON_GOAL_WEIGHT_REACHED = "checkin_goal_weight_reached"
REASON_GOAL_BODY_FAT_REACHED = "checkin_goal_body_fat_reached"
REASON_MAINTENANCE_REVIEW = "checkin_maintenance_review_plan"

# Por que não há check-in a mostrar (a tela explica em vez de esconder).
NONE_NO_PLAN = "checkin_no_plan"
NONE_PLAN_AFTER_WEEK = "checkin_plan_after_week"

# Histórico: itens por página (default de desenho).
HISTORY_LIMIT_DEFAULT: Final[int] = 12
HISTORY_LIMIT_MAX: Final[int] = 52

# Chaves das mudanças propostas (JSON gravado em `nutrition_checkins.proposed_changes_json`).
CHANGE_WEEKLY_RATE_SHARE = "weekly_rate_share"
CHANGE_SURPLUS_SHARE = "surplus_share"
CHANGE_MANUAL_TARGETS = "manual_targets"


# ---------------------------------------------------------------------------
# Entradas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckinDay:
    """Um dia da janela: meta materializada, ingestão COMPLETA (None = sem refeição ou incompleta), gasto da
    meta (TDEE) e as leituras do corpo nesse dia (None = sem leitura)."""

    local_date: date
    target: Optional[MacroTargets] = None
    intake: Optional[Macros] = None
    tdee_kcal: Optional[float] = None
    weight_kg: Optional[float] = None
    body_fat_percent: Optional[float] = None
    muscle_mass_kg: Optional[float] = None


@dataclass(frozen=True)
class CheckinInputs:
    """`days` = exatamente `BODY_WINDOW_DAYS` dias seguidos terminando no domingo da semana avaliada (os 7 últimos
    são a semana). `reference_day` = o dia em que as metas novas passam a valer (o dia do check-in)."""

    settings: PlanSettings
    subject: Subject
    days: tuple[CheckinDay, ...]
    reference_day: date
    scale_bmr_kcal: Optional[float] = None
    ffm_age_days: Optional[int] = None

    @property
    def week_days(self) -> tuple[CheckinDay, ...]:
        return self.days[-DAYS_PER_WEEK:]

    @property
    def week_start(self) -> date:
        return self.week_days[0].local_date


# ---------------------------------------------------------------------------
# Saídas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BodyTrend:
    """Tendência de uma grandeza do corpo (kg/semana; None = não calculável) e o que a sustenta."""

    weekly_change: Optional[float]
    readings: int
    span_days: Optional[int]
    mean: Optional[float]

    @property
    def valid_for_bia(self) -> bool:
        return (
            self.weekly_change is not None
            and self.readings >= MIN_BIA_READINGS
            and self.span_days is not None
            and self.span_days >= MIN_BIA_SPAN_DAYS
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "weekly_change": self.weekly_change,
            "readings": self.readings,
            "span_days": self.span_days,
            "mean": self.mean,
            "valid_for_bia": self.valid_for_bia,
        }


@dataclass(frozen=True)
class WeekSummary:
    """O que a tela mostra: a semana contra o alvo. Tudo em valores prontos; a tela não calcula."""

    complete_days: int
    days_with_target: int
    mean_intake_kcal: Optional[float]
    mean_target_kcal: Optional[float]
    mean_tdee_kcal: Optional[float]
    planned_balance_kcal: Optional[float]
    kcal_share: Optional[float]
    protein_share: Optional[float]
    adherence_valid: bool
    kcal_within_tolerance: Optional[bool]
    weight_week_mean_kg: Optional[float]
    weight_previous_week_mean_kg: Optional[float]
    weight_weekly_change_kg: Optional[float]
    weight_weekly_change_share: Optional[float]
    expected_weekly_change_kg: Optional[float]
    maintenance_band_kg: Optional[float]
    body_fat_percent: Optional[float]
    muscle_mass_kg: Optional[float]
    weight_trend: BodyTrend
    lean_trend: BodyTrend
    fat_trend: BodyTrend
    observed_tdee: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete_days": self.complete_days,
            "days_with_target": self.days_with_target,
            "mean_intake_kcal": self.mean_intake_kcal,
            "mean_target_kcal": self.mean_target_kcal,
            "mean_tdee_kcal": self.mean_tdee_kcal,
            "planned_balance_kcal": self.planned_balance_kcal,
            "kcal_share": self.kcal_share,
            "protein_share": self.protein_share,
            "adherence_valid": self.adherence_valid,
            "kcal_within_tolerance": self.kcal_within_tolerance,
            "weight_week_mean_kg": self.weight_week_mean_kg,
            "weight_previous_week_mean_kg": self.weight_previous_week_mean_kg,
            "weight_weekly_change_kg": self.weight_weekly_change_kg,
            "weight_weekly_change_share": self.weight_weekly_change_share,
            "expected_weekly_change_kg": self.expected_weekly_change_kg,
            "maintenance_band_kg": self.maintenance_band_kg,
            "body_fat_percent": self.body_fat_percent,
            "muscle_mass_kg": self.muscle_mass_kg,
            "weight_trend": self.weight_trend.to_dict(),
            "lean_trend": self.lean_trend.to_dict(),
            "fat_trend": self.fat_trend.to_dict(),
            "observed_tdee": self.observed_tdee,
        }


@dataclass(frozen=True)
class DayMacros:
    kcal: Optional[float]
    protein_g: Optional[float]
    carb_g: Optional[float]
    fat_g: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return {"kcal": self.kcal, "protein_g": self.protein_g, "carb_g": self.carb_g, "fat_g": self.fat_g}


@dataclass(frozen=True)
class CheckinProposal:
    """Metas novas propostas: as de um dia SEM treino antes e depois (a base do plano), a diferença de energia e
    as mudanças de parâmetro (o que a publicação grava)."""

    before: DayMacros
    after: DayMacros
    delta_kcal: float
    changes: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "delta_kcal": self.delta_kcal,
            "changes": self.changes,
        }


@dataclass(frozen=True)
class CheckinResult:
    verdict: CheckinVerdict
    summary: WeekSummary
    missing: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    proposal: Optional[CheckinProposal] = None

    @property
    def status(self) -> CheckinStatus:
        """Estado inicial da linha: só há o que confirmar quando existe proposta."""
        return CheckinStatus.PROPOSED if self.proposal is not None else CheckinStatus.INFORMATIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "summary": self.summary.to_dict(),
            "missing": list(self.missing),
            "reasons": list(self.reasons),
            "proposal": self.proposal.to_dict() if self.proposal else None,
        }


# ---------------------------------------------------------------------------
# Séries do corpo
# ---------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _series(days: Sequence[CheckinDay], pick: Any) -> list[Optional[float]]:
    return [positive_number(pick(dia)) for dia in days]


def body_trend(series: Sequence[Optional[float]]) -> BodyTrend:
    """Inclinação (kg/semana) pelos mínimos quadrados do `adaptive_tdee.weight_trend`, com a média das leituras."""
    tendencia = weight_trend(series)
    leituras = [v for v in series if v is not None]
    por_semana = None if tendencia.slope_kg_per_day is None else tendencia.slope_kg_per_day * DAYS_PER_WEEK
    return BodyTrend(
        weekly_change=por_semana, readings=tendencia.readings, span_days=tendencia.span_days, mean=_mean(leituras)
    )


def _lean_series(days: Sequence[CheckinDay]) -> list[Optional[float]]:
    """Massa magra por dia: a massa muscular da bioimpedância; sem ela, peso × (1 − %G) do mesmo dia."""
    saida: list[Optional[float]] = []
    for dia in days:
        musculo = positive_number(dia.muscle_mass_kg)
        if musculo is None and dia.weight_kg is not None and dia.body_fat_percent is not None:
            massa_gorda = fat_mass_kg(dia.weight_kg, dia.body_fat_percent)
            musculo = positive_number(dia.weight_kg - massa_gorda) if massa_gorda is not None else None
        saida.append(musculo)
    return saida


def _fat_series(days: Sequence[CheckinDay]) -> list[Optional[float]]:
    return [
        positive_number(fat_mass_kg(dia.weight_kg, dia.body_fat_percent))
        if dia.weight_kg is not None and dia.body_fat_percent is not None
        else None
        for dia in days
    ]


# ---------------------------------------------------------------------------
# Plano: gasto base, kcal, mudanças
# ---------------------------------------------------------------------------


def _builder_args(settings: PlanSettings) -> dict[str, Any]:
    """Argumentos de `build_plan_settings` que reproduzem `settings` (para validar o plano ajustado pelo MESMO
    caminho da entrada do plano)."""
    manual = settings.manual_macros
    return {
        "strategy": settings.strategy.value,
        "advanced": settings.advanced,
        "protein_g_per_kg": settings.protein_g_per_kg,
        "fat_kcal_share": settings.fat_kcal_share,
        "weekly_rate_share": settings.weekly_rate_share,
        "surplus_share": settings.surplus_share,
        "carb_min_g_per_kg": settings.carb_min_g_per_kg,
        "bmr_formula": settings.bmr_formula.value,
        "tdee_mode": settings.tdee_mode.value,
        "activity_level": settings.activity_level.value if settings.activity_level else None,
        "meals_per_day": settings.meals_per_day,
        "goal_body_fat_percent": settings.goal_body_fat_percent,
        "goal_weight_kg": settings.goal_weight_kg,
        "macro_priority": settings.macro_priority.value,
        "manual_targets": (
            {"protein_g": manual.protein_g, "carb_g": manual.carb_g, "fat_g": manual.fat_g} if manual else None
        ),
        "manual_training_mode": manual.training_mode.value if manual else None,
        "cycle_days": settings.cycle_days,
    }


def apply_changes(settings: PlanSettings, changes: dict[str, Any]) -> PlanValidation:
    """O plano com as `changes` aplicadas, validado por `plan.build_plan_settings` (mesmas faixas e bloqueios). Um plano
    JÁ publicado pode estar fora de uma faixa que mudou depois (o orquestrador só avisa): esses códigos pré-existentes
    não bloqueiam a proposta; só os que a mudança introduz."""
    base = _builder_args(settings)
    existentes = set(build_plan_settings(**base).errors)
    validacao = build_plan_settings(**{**base, **changes})
    novos = tuple(codigo for codigo in validacao.errors if codigo not in existentes)
    return PlanValidation(settings=validacao.settings if not novos else None, errors=novos)


def _base_tdee_kcal(settings: PlanSettings, subject: Subject, bmr_kcal: Optional[float]) -> Optional[float]:
    """Gasto de um dia SEM treino (a base do plano): é o que o ritmo e o superávit multiplicam."""
    return ee.tdee(bmr_kcal, subject.activity_level, settings.tdee_mode, 0.0).kcal


def _day_macros(settings: PlanSettings, inputs: CheckinInputs) -> DayMacros:
    meta = compute_daily_target(
        inputs.subject,
        settings.plan_params(inputs.ffm_age_days),
        DayContext(day=inputs.reference_day),
        scale_bmr=inputs.scale_bmr_kcal,
    ).macros
    return DayMacros(kcal=meta.kcal, protein_g=meta.protein_g, carb_g=meta.carb_g, fat_g=meta.fat_g)


def _round_rate(value: float) -> float:
    return round(value, RATE_DECIMALS)


def _kcal_floor(strategy: Strategy, bmr_kcal: Optional[float], tdee_base: float) -> float:
    """Piso da ingestão base: o bloqueio de energia do motor, a TMB (`CUT_MIN_INTAKE_BMR_MULTIPLE`) e, no cutting, o
    teto de déficit."""
    piso = KCAL_BLOCK_MIN
    if bmr_kcal is not None:
        piso = max(piso, CUT_MIN_INTAKE_BMR_MULTIPLE * bmr_kcal)
    if strategy == Strategy.CUTTING:
        piso = max(piso, tdee_base * (1.0 - CUT_MAX_DEFICIT_SHARE))
    return piso


# ---------------------------------------------------------------------------
# Ajuste de kcal -> parâmetros do plano
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Applied:
    changes: dict[str, Any]
    settings: PlanSettings
    delta_kcal: float


def _round_grams(value: float) -> float:
    return float(round(value))


def _manual_changes(
    inputs: CheckinInputs, delta: float, tdee_base: float, bmr_kcal: Optional[float], fat_mass: Optional[float]
) -> tuple[Optional[_Applied], tuple[str, ...]]:
    """Gramas manuais: a proteína fica; a diferença de kcal divide entre carboidrato e gordura na proporção atual
    (a mesma prioridade escolhida pelo usuário). Respeita os mesmos limites de energia da estratégia."""
    settings = inputs.settings
    manual: ManualMacros = settings.manual_macros  # type: ignore[assignment]
    atual = kcal_from_macros(manual.protein_g, manual.carb_g, manual.fat_g)
    if atual is None:
        return None, (REASON_PROPOSAL_INVALID,)
    if settings.strategy == Strategy.CUTTING:
        peso = positive_number(inputs.subject.weight_kg)
        teto = tdee_base - (daily_kcal_for_weekly_rate(peso * CUT_WEEKLY_RATE_SHARE_RANGE[0], fat_mass) or 0.0) if peso else tdee_base
        piso = _kcal_floor(settings.strategy, bmr_kcal, tdee_base)
    else:  # bulking
        faixa = surplus_range_for(settings.advanced)
        piso, teto = tdee_base * (1.0 + faixa[0]), tdee_base * (1.0 + faixa[1])
        piso = max(piso, KCAL_BLOCK_MIN)
    alvo = atual + delta
    # Só limita no sentido do movimento: quem já está fora da faixa por escolha própria não é puxado de volta.
    if delta > 0:
        alvo = min(alvo, max(teto, atual))
    else:
        alvo = max(alvo, min(piso, atual))
    alvo = min(max(alvo, KCAL_BLOCK_MIN), KCAL_BLOCK_MAX)
    efetivo = alvo - atual
    if abs(efetivo) < MIN_KCAL_STEP:
        return None, (REASON_AT_STRATEGY_LIMIT,)
    kcal_carbo = manual.carb_g * KCAL_PER_G_CARB
    kcal_gordura = manual.fat_g * KCAL_PER_G_FAT
    base = kcal_carbo + kcal_gordura
    parte_carbo = kcal_carbo / base if base > 0 else 1.0
    carbo = max(manual.carb_g + efetivo * parte_carbo / KCAL_PER_G_CARB, 0.0)
    gordura = max(manual.fat_g + efetivo * (1.0 - parte_carbo) / KCAL_PER_G_FAT, 0.0)
    novas = {
        "protein_g": _round_grams(manual.protein_g),
        "carb_g": _round_grams(carbo),
        "fat_g": _round_grams(gordura),
    }
    nova_kcal = kcal_from_macros(novas["protein_g"], novas["carb_g"], novas["fat_g"])
    if nova_kcal is None:
        return None, (REASON_PROPOSAL_INVALID,)
    return _Applied({CHANGE_MANUAL_TARGETS: novas}, settings, nova_kcal - atual), ()


def _parametric_changes(
    inputs: CheckinInputs, delta: float, tdee_base: float, bmr_kcal: Optional[float], fat_mass: Optional[float]
) -> tuple[Optional[_Applied], tuple[str, ...]]:
    """Sem gramas manuais: o ajuste de kcal vira o parâmetro do plano que gera a meta (ritmo do cutting ou superávit
    do bulking), dentro da faixa da estratégia."""
    settings = inputs.settings
    peso = positive_number(inputs.subject.weight_kg)
    antes = kcal_target(settings.strategy_params(), tdee_base, bmr_kcal, peso, fat_mass)
    if antes.kcal is None:
        return None, (REASON_PROPOSAL_INVALID,)
    alvo = antes.kcal + delta
    if settings.strategy == Strategy.CUTTING:
        if peso is None:
            return None, (REASON_PROPOSAL_INVALID,)
        alvo = max(alvo, _kcal_floor(settings.strategy, bmr_kcal, tdee_base))
        kg_semana = weekly_kg_for_daily_kcal(tdee_base - alvo, fat_mass)
        if kg_semana is None:
            return None, (REASON_PROPOSAL_INVALID,)
        faixa = CUT_WEEKLY_RATE_SHARE_RANGE
        ritmo = _round_rate(min(max(kg_semana / peso, faixa[0]), faixa[1]))
        mudanca = {CHANGE_WEEKLY_RATE_SHARE: ritmo}
    else:
        faixa = surplus_range_for(settings.advanced)
        superavit = _round_rate(min(max((alvo - tdee_base) / tdee_base, faixa[0]), faixa[1]))
        mudanca = {CHANGE_SURPLUS_SHARE: superavit}
    validacao = apply_changes(settings, mudanca)
    if not validacao.ok:
        return None, (REASON_PROPOSAL_INVALID,)
    depois = kcal_target(validacao.settings.strategy_params(), tdee_base, bmr_kcal, peso, fat_mass)  # type: ignore[union-attr]
    if depois.kcal is None:
        return None, (REASON_PROPOSAL_INVALID,)
    efetivo = depois.kcal - antes.kcal
    if abs(efetivo) < MIN_KCAL_STEP:
        return None, (REASON_AT_STRATEGY_LIMIT,)
    return _Applied(mudanca, validacao.settings, efetivo), ()  # type: ignore[arg-type]


def _propose(inputs: CheckinInputs, delta: float) -> tuple[Optional[CheckinProposal], tuple[str, ...]]:
    """Transforma o ajuste de kcal/dia em metas novas e confere os pisos. `(None, motivos)` = não propõe."""
    settings, subject = inputs.settings, inputs.subject
    bmr_kcal = positive_number(bmr_mod.bmr(subject, settings.bmr_formula, inputs.ffm_age_days).kcal)
    tdee_base = _base_tdee_kcal(settings, subject, bmr_kcal)
    fat_mass = resolved_fat_mass_kg(subject)
    if tdee_base is None:
        return None, (REASON_PROPOSAL_INVALID,)
    if settings.manual_macros is not None:
        aplicado, motivos = _manual_changes(inputs, delta, tdee_base, bmr_kcal, fat_mass)
    else:
        aplicado, motivos = _parametric_changes(inputs, delta, tdee_base, bmr_kcal, fat_mass)
    if aplicado is None:
        return None, motivos
    novo = aplicado.settings
    if settings.manual_macros is not None:
        validacao = apply_changes(settings, aplicado.changes)
        if not validacao.ok or validacao.settings is None:
            return None, (REASON_PROPOSAL_INVALID,)
        novo = validacao.settings
        referencia = kcal_target(settings.strategy_params(), tdee_base, bmr_kcal, subject.weight_kg, fat_mass).kcal
        checagem = check_plan_manual_targets(novo, subject, referencia, bmr_kcal)
        if manual_save_errors(checagem, subject) or (checagem is not None and checagem.status == RangeStatus.BLOCKED):
            return None, (REASON_BLOCKED_BY_MANUAL,)
    erros_ciclo, _ = check_cycle_floors(novo, subject, inputs.scale_bmr_kcal, inputs.ffm_age_days)
    if erros_ciclo:
        return None, (REASON_BLOCKED_BY_CYCLE,)
    antes, depois = _day_macros(settings, inputs), _day_macros(novo, inputs)
    if depois.kcal is None or antes.kcal is None:
        return None, (REASON_PROPOSAL_INVALID,)
    return (
        CheckinProposal(before=antes, after=depois, delta_kcal=depois.kcal - antes.kcal, changes=aplicado.changes),
        (),
    )


# ---------------------------------------------------------------------------
# Veredito
# ---------------------------------------------------------------------------


def _adaptive(days: Sequence[CheckinDay], fat_mass: Optional[float]) -> AdaptiveTdee:
    return observed_tdee(
        [None if dia.intake is None else dia.intake.kcal for dia in days],
        _series(days, lambda dia: dia.weight_kg),
        fat_mass,
    )


def _goal_reasons(inputs: CheckinInputs, weight_week: BodyTrend, bf_recent: list[float]) -> tuple[str, ...]:
    """Alvo atingido (mesma regra do `plan.goal_summary`): peso-alvo pela média das pesagens da semana e %G de parada
    (do plano ou o default do motor) pela média das leituras recentes. Exige `MIN_READINGS_FOR_GOAL` leituras."""
    settings = inputs.settings
    if settings.strategy == Strategy.MAINTENANCE:
        return ()
    motivos: list[str] = []
    semana = [dia.weight_kg for dia in inputs.week_days if dia.weight_kg is not None]
    if settings.goal_weight_kg is not None and len(semana) >= MIN_READINGS_FOR_GOAL:
        media = float(_mean([float(v) for v in semana]))  # type: ignore[arg-type]
        alvo = float(settings.goal_weight_kg)
        passou = media <= alvo + PROJECTION_WEIGHT_TOLERANCE_KG if settings.strategy == Strategy.CUTTING else media >= alvo - PROJECTION_WEIGHT_TOLERANCE_KG
        if passou:
            motivos.append(REASON_GOAL_WEIGHT_REACHED)
    parada, _ = resolve_stop_body_fat(settings.strategy, inputs.subject.sex, settings.goal_body_fat_percent)
    if parada is not None and len(bf_recent) >= MIN_READINGS_FOR_GOAL:
        if should_stop(settings.strategy, _mean(bf_recent), parada):
            motivos.append(REASON_GOAL_BODY_FAT_REACHED)
    return tuple(motivos)


def _summary(
    inputs: CheckinInputs,
    weight: BodyTrend,
    lean: BodyTrend,
    fat: BodyTrend,
    fat_mass: Optional[float],
) -> tuple[WeekSummary, int, list[str]]:
    semana = inputs.week_days
    metas = [dia.target for dia in semana]
    ingestoes = [dia.intake for dia in semana]
    aderencia = weekly_adherence(metas, ingestoes)
    completos = [dia for dia in semana if dia.target is not None and dia.target.computable and dia.intake is not None]
    com_meta = [dia for dia in semana if dia.target is not None and dia.target.computable]
    pares = [(float(dia.target.kcal), float(dia.tdee_kcal)) for dia in com_meta if dia.target.kcal is not None and positive_number(dia.tdee_kcal) is not None]  # type: ignore[union-attr, arg-type]
    media_meta = _mean([a for a, _ in pares])
    media_gasto = _mean([b for _, b in pares])
    saldo = None if media_meta is None or media_gasto is None else media_meta - media_gasto
    peso_semana = _mean([float(d.weight_kg) for d in semana if d.weight_kg is not None])  # type: ignore[arg-type]
    peso_anterior = _mean([float(d.weight_kg) for d in inputs.days[-2 * DAYS_PER_WEEK : -DAYS_PER_WEEK] if d.weight_kg is not None])  # type: ignore[arg-type]
    esperado = weekly_kg_for_daily_kcal(saldo, fat_mass) if saldo is not None else None
    banda = None
    if media_gasto is not None:
        banda = weekly_kg_for_daily_kcal(MAINT_TOLERANCE_SHARE * media_gasto, fat_mass)
    mudanca_peso = weight.weekly_change
    referencia = weight.mean
    parte = None if mudanca_peso is None or not referencia else mudanca_peso / referencia
    gordura = [float(d.body_fat_percent) for d in inputs.days[-WEIGHT_WINDOW_DAYS:] if d.body_fat_percent is not None]  # type: ignore[arg-type]
    musculos = [float(d.muscle_mass_kg) for d in inputs.days[-WEIGHT_WINDOW_DAYS:] if d.muscle_mass_kg is not None]  # type: ignore[arg-type]
    observado = _adaptive(inputs.days, fat_mass)
    resumo = WeekSummary(
        complete_days=len(completos),
        days_with_target=len(com_meta),
        mean_intake_kcal=_mean([float(d.intake.kcal) for d in completos]),  # type: ignore[union-attr]
        mean_target_kcal=media_meta,
        mean_tdee_kcal=media_gasto,
        planned_balance_kcal=saldo,
        kcal_share=aderencia.kcal_share,
        protein_share=aderencia.protein_share,
        adherence_valid=aderencia.valid_week,
        kcal_within_tolerance=aderencia.kcal_within_tolerance,
        weight_week_mean_kg=peso_semana,
        weight_previous_week_mean_kg=peso_anterior,
        weight_weekly_change_kg=mudanca_peso,
        weight_weekly_change_share=parte,
        expected_weekly_change_kg=esperado,
        maintenance_band_kg=banda,
        body_fat_percent=_mean(gordura),
        muscle_mass_kg=_mean(musculos),
        weight_trend=weight,
        lean_trend=lean,
        fat_trend=fat,
        observed_tdee=observado.to_dict(),
    )
    return resumo, len(completos), gordura


def _missing(inputs: CheckinInputs, resumo: WeekSummary, weight: BodyTrend, fat_mass: Optional[float]) -> tuple[str, ...]:
    faltas: list[str] = []
    if resumo.days_with_target == 0:
        faltas.append(MISSING_NO_TARGETS)
    if resumo.complete_days < MIN_COMPLETE_DAYS:
        faltas.append(MISSING_FEW_COMPLETE_DAYS)
    na_semana = sum(1 for dia in inputs.week_days if dia.weight_kg is not None)
    if weight.readings < MIN_WEIGHT_READINGS_FOR_TREND:
        faltas.append(MISSING_FEW_WEIGHINGS)
    elif na_semana < MIN_WEIGHT_READINGS_IN_WEEK:
        faltas.append(MISSING_NO_WEIGHING_IN_WEEK)
    elif weight.weekly_change is None or (weight.span_days is not None and weight.span_days < MIN_WEIGHT_SPAN_DAYS):
        faltas.append(MISSING_WEIGHT_SPAN_SHORT)
    if fat_mass is None:
        faltas.append(MISSING_FAT_MASS)
    return tuple(faltas)


def evaluate_week(inputs: CheckinInputs) -> CheckinResult:
    """Avalia a semana fechada e devolve o veredito, o que faltou, os motivos e, quando há o que mudar, a proposta."""
    janela_peso = inputs.days[-WEIGHT_WINDOW_DAYS:]
    peso = body_trend(_series(janela_peso, lambda d: d.weight_kg))
    magra = body_trend(_lean_series(inputs.days))
    gorda = body_trend(_fat_series(inputs.days))
    peso_longo = body_trend(_series(inputs.days, lambda d: d.weight_kg))
    massa_gorda = resolved_fat_mass_kg(inputs.subject)
    resumo, _, gordura_recente = _summary(inputs, peso, magra, gorda, massa_gorda)

    # Alvo atingido não depende do diário: vale mesmo com a semana inconclusiva.
    atingido = _goal_reasons(inputs, peso, gordura_recente)
    if atingido:
        return CheckinResult(CheckinVerdict.GOAL_REACHED, resumo, reasons=atingido)

    faltas = _missing(inputs, resumo, peso, massa_gorda)
    if faltas or resumo.expected_weekly_change_kg is None or peso.weekly_change is None:
        return CheckinResult(CheckinVerdict.INCONCLUSIVE, resumo, missing=faltas or (MISSING_SUBJECT,))

    estrategia = inputs.settings.strategy
    real = float(peso.weekly_change)
    esperado = float(resumo.expected_weekly_change_kg)
    ruido = RATE_NOISE_FLOOR_SHARE * float(peso.mean or 0.0)
    folga = max(ON_TRACK_TOLERANCE_SHARE * abs(esperado), ruido)

    if estrategia == Strategy.MAINTENANCE:
        banda = max(float(resumo.maintenance_band_kg or 0.0), ruido)
        if real > banda:
            return CheckinResult(CheckinVerdict.ABOVE_BAND, resumo, reasons=(REASON_MAINTENANCE_REVIEW,))
        if real < -banda:
            return CheckinResult(CheckinVerdict.BELOW_BAND, resumo, reasons=(REASON_MAINTENANCE_REVIEW,))
        return CheckinResult(CheckinVerdict.ON_TRACK, resumo)

    direcao = -1.0 if estrategia == Strategy.CUTTING else 1.0
    diferenca = direcao * (real - esperado)  # > 0: andou além do esperado; < 0: ficou aquém
    if diferenca < -folga:
        veredito = CheckinVerdict.SLOW
    elif diferenca > folga:
        veredito = CheckinVerdict.FAST
    else:
        veredito = CheckinVerdict.ON_TRACK

    motivos: list[str] = []
    # Quanto comer a mais (+) ou a menos (-) por dia para corrigir a diferença, na densidade da massa gorda atual.
    correcao = daily_kcal_for_weekly_rate(real - esperado, massa_gorda)
    ajuste = 0.0 if veredito == CheckinVerdict.ON_TRACK or correcao is None else -CORRECTION_FRACTION * correcao

    # D10: a bioimpedância só corrige com tendência de 2+ semanas; uma semana isolada é ignorada.
    corrige = False
    if estrategia == Strategy.CUTTING:
        massa_magra = float(magra.mean or 0.0)
        if magra.valid_for_bia and (magra.weekly_change or 0.0) <= -LEAN_LOSS_LIMIT_SHARE_PER_WEEK * massa_magra:
            motivos.append(REASON_LEAN_LOSS)
            corrige = True
    elif gorda.valid_for_bia and peso_longo.valid_for_bia:
        if fat_gain_exceeds_limit(float(gorda.weekly_change or 0.0), float(peso_longo.weekly_change or 0.0)):
            motivos.append(REASON_FAT_GAIN)
            corrige = True
    if not corrige and (magra.readings or gorda.readings) and not (magra.valid_for_bia or gorda.valid_for_bia):
        motivos.append(REASON_BIA_SHORT_TREND)

    teto_passo = MAX_KCAL_STEP_SHARE * float(resumo.mean_tdee_kcal or 0.0)
    if corrige:
        passo_bia = BIA_CORRECTION_FRACTION * teto_passo
        if veredito == CheckinVerdict.SLOW:
            ajuste = 0.0  # lento + perda de massa magra (ou ganho de gordura): não corta nem sobe, só segura
            motivos.append(REASON_BIA_HOLD)
        elif estrategia == Strategy.CUTTING:
            ajuste = max(ajuste, passo_bia)  # menos déficit
        else:
            ajuste = min(ajuste, -passo_bia)  # menos superávit

    # D9: baixa aderência não vira corte. Só ajusta com a aderência calórica válida e dentro da tolerância; a única
    # exceção é a correção que REDUZ o déficit/superávit pela bioimpedância (comer mais no cutting, menos no bulking
    # só depende do corpo, não do que foi comido).
    ajusta_sem_aderencia = corrige and veredito != CheckinVerdict.SLOW
    aderencia_ok = resumo.adherence_valid and resumo.kcal_within_tolerance is True
    if ajuste != 0.0 and not aderencia_ok and not ajusta_sem_aderencia:
        motivos.append(REASON_ADHERENCE_OFF)
        ajuste = 0.0

    ajuste = max(-teto_passo, min(teto_passo, ajuste))
    if abs(ajuste) < MIN_KCAL_STEP:
        if veredito != CheckinVerdict.ON_TRACK and not {REASON_ADHERENCE_OFF, REASON_BIA_HOLD} & set(motivos):
            motivos.append(REASON_ADJUSTMENT_BELOW_MIN)
        return CheckinResult(veredito, resumo, reasons=tuple(dict.fromkeys(motivos)))

    proposta, bloqueios = _propose(inputs, ajuste)
    motivos.extend(bloqueios)
    return CheckinResult(veredito, resumo, reasons=tuple(dict.fromkeys(motivos)), proposal=proposta)


def fat_gain_exceeds_limit(fat_gain_kg_week: float, weight_gain_kg_week: float) -> bool:
    """Ganho de gordura acima do limite: mais que o piso de ruído E mais que `BULK_FAT_SHARE_OF_GAIN_LIMIT` do ganho de peso."""
    return (
        weight_gain_kg_week > 0
        and fat_gain_kg_week >= BULK_FAT_GAIN_NOISE_FLOOR_KG_PER_WEEK
        and fat_gain_kg_week > BULK_FAT_SHARE_OF_GAIN_LIMIT * weight_gain_kg_week
    )


# ---------------------------------------------------------------------------
# Calendário do check-in (D8) e validade (D7)
# ---------------------------------------------------------------------------


def checkin_weekday_is_valid(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and CHECKIN_WEEKDAY_RANGE[0] <= value <= CHECKIN_WEEKDAY_RANGE[1]
    )


def due_day(today: date, weekday: int = DEFAULT_CHECKIN_WEEKDAY) -> date:
    """O dia do check-in mais recente que não passa de `today` (hoje, se hoje é o dia escolhido)."""
    return today - timedelta(days=(today.weekday() - weekday) % DAYS_PER_WEEK)


def closed_week_start(due: date) -> date:
    """Segunda-feira da semana FECHADA que o check-in de `due` avalia: a semana anterior à semana de `due`."""
    return due - timedelta(days=due.weekday() + DAYS_PER_WEEK)


def window_dates(week_start: date) -> tuple[date, ...]:
    """Os `BODY_WINDOW_DAYS` dias que terminam no domingo da semana avaliada."""
    fim = week_start + timedelta(days=DAYS_PER_WEEK - 1)
    return tuple(fim - timedelta(days=BODY_WINDOW_DAYS - 1 - i) for i in range(BODY_WINDOW_DAYS))


def proposal_expires_on(created_on: date) -> date:
    """Último dia (inclusive) em que a proposta criada em `created_on` ainda pode ser aceita."""
    return created_on + timedelta(days=PROPOSAL_VALIDITY_DAYS)


def is_proposal_expired(created_on: date, today: date) -> bool:
    return today > proposal_expires_on(created_on)


def is_card_visible(status: CheckinStatus, generated_on: date, today: date) -> bool:
    """O card do check-in aparece enquanto há o que responder (proposta não expirada) ou, para o check-in sem mudança
    de metas, durante a mesma validade de `PROPOSAL_VALIDITY_DAYS` dias. Aceito, recusado e expirado não aparecem."""
    if status == CheckinStatus.PROPOSED:
        return not is_proposal_expired(generated_on, today)
    if status == CheckinStatus.INFORMATIVE:
        return not is_proposal_expired(generated_on, today)
    return False


__all__ = [
    "HISTORY_LIMIT_DEFAULT",
    "HISTORY_LIMIT_MAX",
    "NONE_NO_PLAN",
    "NONE_PLAN_AFTER_WEEK",
    "is_card_visible",
    "BODY_WINDOW_DAYS",
    "CHANGE_MANUAL_TARGETS",
    "CHANGE_SURPLUS_SHARE",
    "CHANGE_WEEKLY_RATE_SHARE",
    "CheckinDay",
    "CheckinInputs",
    "CheckinProposal",
    "CheckinResult",
    "CheckinStatus",
    "CheckinVerdict",
    "DEFAULT_CHECKIN_WEEKDAY",
    "MIN_COMPLETE_DAYS",
    "PROPOSAL_VALIDITY_DAYS",
    "apply_changes",
    "checkin_weekday_is_valid",
    "closed_week_start",
    "due_day",
    "evaluate_week",
    "is_proposal_expired",
    "proposal_expires_on",
    "window_dates",
]

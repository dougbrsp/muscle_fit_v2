"""Plano nutricional do aluno: parâmetros do admin/PT, validação, ciclo de vida
e qual plano vale em cada dia.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7 e §47; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`). Plano:
`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §5 (D4), §6.2 e §8.1 (Lote L2b).

- `build_plan_settings`: o que o admin/PT envia vira `PlanSettings` com os
  defaults da estratégia (`strategy.StrategyParams.defaults_for`) nos campos
  ausentes e é validado nas faixas da fonte (`StrategyParams.out_of_range`,
  `macros.MEALS_PER_DAY_RANGE`, `body_composition.BODY_FAT_RANGE_VALID`,
  `anthropometrics.WEIGHT_KG_RANGE`). Na entrada do plano a faixa BLOQUEIA
  (400 com o código); dentro do orquestrador ela só avisa (`out_of_range`).
- Campos de outra estratégia (ritmo do cutting num bulking, superávit num
  cutting) são recusados com `param_not_applicable:<campo>`: o plano guarda só
  o que vale para a estratégia escolhida.
- Ciclo de carbo: recusado com `carb_cycling_unavailable` até o L8 (os dias de
  treino planejados pela atribuição de Modelo ainda não entram na meta).
- `plan_state`: rascunho, vigente ou substituído (a coluna `status` guarda
  rascunho/publicado; "substituído" é publicado com `superseded_at`).
- `plan_in_force`: o plano de um dia é o publicado com o maior
  `effective_from <= dia` (desempate pela publicação mais recente). Um plano
  substituído continua valendo para os dias do seu período.

Módulo PURO: sem banco, sem FastAPI, sem I/O, sem relógio.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
from typing import Any, Final, Optional, Sequence

from . import energy_expenditure as ee
from .anthropometrics import ActivityLevel, Subject, normalize_activity_level, positive_number, weight_is_valid
from .body_composition import body_fat_is_valid, resolved_ffm_kg, target_weight_for_body_fat
from .bmr import DEFAULT_BMR_FORMULA, BmrFormula
from .daily_target import PlanParams
from .macros import meals_per_day_is_valid
from .phases import resolve_stop_body_fat, should_stop
from .strategy import LIMIT_PARAM_OUT_OF_RANGE, Strategy, StrategyParams


class PlanStatus(str, Enum):
    """O que a coluna `nutrition_plans.status` guarda."""

    DRAFT = "draft"
    PUBLISHED = "published"


class PlanState(str, Enum):
    """Estado exibido: rascunho, vigente ou substituído."""

    DRAFT = "draft"
    CURRENT = "current"
    SUPERSEDED = "superseded"


# Códigos de erro da entrada do plano (estáveis; `labels.limitation_label` rotula).
ERR_INVALID_CODE = "invalid_code"  # f"invalid_code:<campo>"
ERR_PARAM_NOT_APPLICABLE = "param_not_applicable"  # f"param_not_applicable:<campo>"
ERR_CARB_CYCLING_UNAVAILABLE = "carb_cycling_unavailable"

# Campos que só valem para uma estratégia.
STRATEGY_ONLY_FIELDS: Final[dict[str, Strategy]] = {
    "weekly_rate_share": Strategy.CUTTING,
    "surplus_share": Strategy.BULKING,
    "carb_min_g_per_kg": Strategy.BULKING,
}


def _code(prefix: str, field: str) -> str:
    return f"{prefix}:{field}"


def plan_state(status: Any, superseded_at: Any) -> PlanState:
    """Rascunho, vigente (publicado sem `superseded_at`) ou substituído."""
    if str(status) != PlanStatus.PUBLISHED.value:
        return PlanState.DRAFT
    return PlanState.CURRENT if superseded_at is None else PlanState.SUPERSEDED


@dataclass(frozen=True)
class PlanSettings:
    """Parâmetros do plano já normalizados, com os defaults da estratégia."""

    strategy: Strategy
    advanced: bool
    protein_g_per_kg: float
    fat_kcal_share: float
    weekly_rate_share: Optional[float]
    surplus_share: Optional[float]
    carb_min_g_per_kg: Optional[float]
    bmr_formula: BmrFormula
    tdee_mode: ee.TdeeMode
    activity_level: Optional[ActivityLevel]
    meals_per_day: Optional[int]
    goal_body_fat_percent: Optional[float]
    goal_weight_kg: Optional[float]
    carb_cycling_enabled: bool = False

    def strategy_params(self) -> StrategyParams:
        base = StrategyParams.defaults_for(self.strategy, self.advanced)
        return replace(
            base,
            protein_g_per_kg=self.protein_g_per_kg,
            fat_kcal_share=self.fat_kcal_share,
            weekly_rate_share=self.weekly_rate_share,
            surplus_share=self.surplus_share,
            carb_min_g_per_kg=self.carb_min_g_per_kg,
        )

    def plan_params(self, ffm_age_days: Optional[int] = None) -> PlanParams:
        return PlanParams(
            strategy_params=self.strategy_params(),
            bmr_formula=self.bmr_formula,
            tdee_mode=self.tdee_mode,
            carb_cycling_enabled=self.carb_cycling_enabled,
            meals_per_day=self.meals_per_day,
            ffm_age_days=ffm_age_days,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "advanced": self.advanced,
            "protein_g_per_kg": self.protein_g_per_kg,
            "protein_basis": self.strategy_params().protein_basis.value,
            "fat_kcal_share": self.fat_kcal_share,
            "weekly_rate_share": self.weekly_rate_share,
            "surplus_share": self.surplus_share,
            "carb_min_g_per_kg": self.carb_min_g_per_kg,
            "bmr_formula": self.bmr_formula.value,
            "tdee_mode": self.tdee_mode.value,
            "activity_level": self.activity_level.value if self.activity_level else None,
            "meals_per_day": self.meals_per_day,
            "goal_body_fat_percent": self.goal_body_fat_percent,
            "goal_weight_kg": self.goal_weight_kg,
            "carb_cycling_enabled": self.carb_cycling_enabled,
        }


@dataclass(frozen=True)
class PlanValidation:
    """Resultado de `build_plan_settings`: as configurações ou os códigos de erro."""

    settings: Optional[PlanSettings]
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.settings is not None and not self.errors


def _enum_or_error(enum_cls: Any, raw: Any, default: Any, field: str, errors: list[str]) -> Any:
    if raw is None:
        return default
    try:
        return enum_cls(str(raw))
    except ValueError:
        errors.append(_code(ERR_INVALID_CODE, field))
        return default


def _number(raw: Any) -> Optional[float]:
    """Número finito (> 0) ou None; `None`/vazio -> None."""
    return positive_number(raw)


def build_plan_settings(
    *,
    strategy: Any,
    advanced: Any = False,
    protein_g_per_kg: Any = None,
    fat_kcal_share: Any = None,
    weekly_rate_share: Any = None,
    surplus_share: Any = None,
    carb_min_g_per_kg: Any = None,
    bmr_formula: Any = None,
    tdee_mode: Any = None,
    activity_level: Any = None,
    meals_per_day: Any = None,
    goal_body_fat_percent: Any = None,
    goal_weight_kg: Any = None,
    carb_cycling_enabled: Any = False,
) -> PlanValidation:
    """Normaliza e valida o plano. Campo ausente (None) recebe o default da
    estratégia; valor fora da faixa da fonte vira `param_out_of_range:<campo>`."""
    erros: list[str] = []
    try:
        estrategia = Strategy(str(strategy))
    except ValueError:
        return PlanValidation(settings=None, errors=(_code(ERR_INVALID_CODE, "strategy"),))
    avancado = bool(advanced)
    base = StrategyParams.defaults_for(estrategia, avancado)

    informados = {
        "weekly_rate_share": weekly_rate_share,
        "surplus_share": surplus_share,
        "carb_min_g_per_kg": carb_min_g_per_kg,
    }
    for campo, dono in STRATEGY_ONLY_FIELDS.items():
        if informados[campo] is not None and estrategia != dono:
            erros.append(_code(ERR_PARAM_NOT_APPLICABLE, campo))

    def _ou_default(raw: Any, default: Optional[float], field: str) -> Optional[float]:
        if raw is None:
            return default
        valor = _number(raw)
        if valor is None:
            erros.append(_code(LIMIT_PARAM_OUT_OF_RANGE, field))
            return default
        return valor

    proteina = _ou_default(protein_g_per_kg, base.protein_g_per_kg, "protein_g_per_kg")
    gordura = _ou_default(fat_kcal_share, base.fat_kcal_share, "fat_kcal_share")
    ritmo = base.weekly_rate_share
    superavit = base.surplus_share
    carbo_minimo = base.carb_min_g_per_kg
    if estrategia == Strategy.CUTTING:
        ritmo = _ou_default(weekly_rate_share, base.weekly_rate_share, "weekly_rate_share")
    if estrategia == Strategy.BULKING:
        superavit = _ou_default(surplus_share, base.surplus_share, "surplus_share")
        carbo_minimo = _ou_default(carb_min_g_per_kg, base.carb_min_g_per_kg, "carb_min_g_per_kg")

    formula = _enum_or_error(BmrFormula, bmr_formula, DEFAULT_BMR_FORMULA, "bmr_formula", erros)
    modo = _enum_or_error(ee.TdeeMode, tdee_mode, ee.DEFAULT_TDEE_MODE, "tdee_mode", erros)
    nivel: Optional[ActivityLevel] = None
    if activity_level is not None:
        nivel = normalize_activity_level(activity_level)
        if nivel is None:
            erros.append(_code(ERR_INVALID_CODE, "activity_level"))

    refeicoes: Optional[int] = None
    if meals_per_day is not None:
        if isinstance(meals_per_day, bool) or not meals_per_day_is_valid(meals_per_day) or int(meals_per_day) != meals_per_day:
            erros.append(_code(LIMIT_PARAM_OUT_OF_RANGE, "meals_per_day"))
        else:
            refeicoes = int(meals_per_day)

    gordura_alvo: Optional[float] = None
    if goal_body_fat_percent is not None:
        if body_fat_is_valid(goal_body_fat_percent):
            gordura_alvo = float(goal_body_fat_percent)
        else:
            erros.append(_code(LIMIT_PARAM_OUT_OF_RANGE, "goal_body_fat_percent"))

    peso_alvo: Optional[float] = None
    if goal_weight_kg is not None:
        if weight_is_valid(goal_weight_kg):
            peso_alvo = float(goal_weight_kg)
        else:
            erros.append(_code(LIMIT_PARAM_OUT_OF_RANGE, "goal_weight_kg"))

    if bool(carb_cycling_enabled):
        erros.append(ERR_CARB_CYCLING_UNAVAILABLE)

    settings = PlanSettings(
        strategy=estrategia,
        advanced=avancado,
        protein_g_per_kg=float(proteina) if proteina is not None else base.protein_g_per_kg,
        fat_kcal_share=float(gordura) if gordura is not None else base.fat_kcal_share,
        weekly_rate_share=ritmo,
        surplus_share=superavit,
        carb_min_g_per_kg=carbo_minimo,
        bmr_formula=formula,
        tdee_mode=modo,
        activity_level=nivel,
        meals_per_day=refeicoes,
        goal_body_fat_percent=gordura_alvo,
        goal_weight_kg=peso_alvo,
        carb_cycling_enabled=False,
    )
    for codigo in settings.strategy_params().out_of_range():
        if codigo not in erros:
            erros.append(codigo)
    return PlanValidation(settings=settings, errors=tuple(erros))


# ---------------------------------------------------------------------------
# Meta de composição (D-C)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoalSummary:
    """%G alvo (o do plano ou o default do motor), o peso em que a MLG atual
    chega a ele, o peso-alvo informado e se a fase já deveria parar."""

    goal_body_fat_percent: Optional[float]
    goal_body_fat_is_default: bool
    goal_weight_kg: Optional[float]
    target_weight_for_goal_kg: Optional[float]
    should_stop: Optional[bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_body_fat_percent": self.goal_body_fat_percent,
            "goal_body_fat_is_default": self.goal_body_fat_is_default,
            "goal_weight_kg": self.goal_weight_kg,
            "target_weight_for_goal_kg": self.target_weight_for_goal_kg,
            "should_stop": self.should_stop,
        }


def goal_summary(settings: PlanSettings, subject: Subject) -> GoalSummary:
    """Resumo da meta de composição do plano para o sujeito do dia."""
    alvo, padrao = resolve_stop_body_fat(settings.strategy, subject.sex, settings.goal_body_fat_percent)
    return GoalSummary(
        goal_body_fat_percent=alvo,
        goal_body_fat_is_default=padrao,
        goal_weight_kg=settings.goal_weight_kg,
        target_weight_for_goal_kg=target_weight_for_body_fat(resolved_ffm_kg(subject), alvo),
        should_stop=should_stop(settings.strategy, subject.body_fat_percent, alvo),
    )


# ---------------------------------------------------------------------------
# Vigência por dia
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishedPlanRef:
    """Um plano publicado (vigente ou substituído), só o que a vigência usa."""

    plan_id: int
    effective_from: date
    published_at: Optional[datetime] = None


def plan_in_force(plans: Sequence[PublishedPlanRef], day: date) -> Optional[PublishedPlanRef]:
    """Plano que vale em `day`: o de maior `effective_from <= day`; no mesmo
    dia de início, o publicado por último (e o maior id como desempate final)."""
    candidatos = [plano for plano in plans if plano.effective_from <= day]
    if not candidatos:
        return None
    return max(candidatos, key=lambda p: (p.effective_from, p.published_at or datetime.min, p.plan_id))

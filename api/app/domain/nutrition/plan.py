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
- N2 (2026-11-02): o plano guarda também a prioridade de macros (`MacroPriority`),
  as gramas manuais (D6) e os dias alto/normal/baixo (D5). Prioridade com gramas
  manuais é `param_not_applicable:macro_priority` (as gramas mandam). Gramas manuais
  validam no motor (`manual_targets`): valor inválido ou bloqueado não salva; fora da
  faixa recomendada salva com AVISO. O peso do aluno é exigido para salvar gramas
  (`check_plan_manual_targets`/`manual_save_errors`). Ritmo derivado do superávit fora
  de `strategy.weekly_rate_share_range_for` é só aviso (`plan_warnings`), nunca recusa.
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
from typing import Any, Final, Mapping, Optional, Sequence

from . import energy_expenditure as ee
from .anthropometrics import ActivityLevel, Subject, normalize_activity_level, positive_number, weight_is_valid
from .body_composition import body_fat_is_valid, resolved_fat_mass_kg, resolved_ffm_kg, target_weight_for_body_fat
from .bmr import DEFAULT_BMR_FORMULA, BmrFormula
from .carb_cycling import CycleDays, DayKind, propose_day_kinds, validate_cycle_days
from .daily_target import PlanParams
from .macros import meals_per_day_is_valid
from .manual_targets import ManualTargetsCheck, RangeStatus, check_manual_targets
from .phases import GoalProjection, REASON_RATE_OUTSIDE_RANGE, project_goal, resolve_stop_body_fat, should_stop
from .strategy import (
    DEFAULT_MACRO_PRIORITY,
    LIMIT_PARAM_OUT_OF_RANGE,
    MacroPriority,
    Strategy,
    StrategyParams,
    weekly_rate_share_for,
    weekly_rate_share_range_for,
)


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
ERR_MANUAL_REQUIRES_WEIGHT = "manual_targets_require_weight"
# Notas do botão "puxar do Modelo" (D5): por que não houve proposta de dias.
NOTE_TEMPLATE_NOT_ASSIGNED = "template_not_assigned"
NOTE_TEMPLATE_WITHOUT_WEEKDAYS = "template_without_weekdays"
NOTE_TEMPLATE_NO_CYCLE = "template_no_cycle"
FIELD_MANUAL_TARGETS = "manual_targets"
FIELD_MACRO_PRIORITY = "macro_priority"
# Chaves das gramas manuais no payload e no JSON gravado.
MANUAL_KEYS: Final[tuple[str, str, str]] = ("protein_g", "carb_g", "fat_g")

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
class ManualMacros:
    """Gramas manuais do dia (D6): proteína, carboidrato e gordura."""

    protein_g: float
    carb_g: float
    fat_g: float

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.protein_g, self.carb_g, self.fat_g)

    def to_dict(self) -> dict[str, float]:
        return {"protein_g": self.protein_g, "carb_g": self.carb_g, "fat_g": self.fat_g}


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
    macro_priority: MacroPriority = DEFAULT_MACRO_PRIORITY
    manual_macros: Optional[ManualMacros] = None
    cycle_kinds: Optional[tuple[DayKind, ...]] = None

    @property
    def carb_cycling_enabled(self) -> bool:
        return self.cycle_kinds is not None

    @property
    def cycle_days(self) -> Optional[dict[str, str]]:
        """Mapa gravado em `carb_cycling_json` (chave = dia 0..6, segunda = 0)."""
        if self.cycle_kinds is None:
            return None
        return {str(i): kind.value for i, kind in enumerate(self.cycle_kinds)}

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
            meals_per_day=self.meals_per_day,
            ffm_age_days=ffm_age_days,
            macro_priority=self.macro_priority,
            manual_macros=self.manual_macros.as_tuple() if self.manual_macros else None,
            cycle_kinds=self.cycle_kinds,
        )

    def fingerprint_dict(self) -> dict[str, Any]:
        """`to_dict` para o hash das entradas: as chaves do N2 só entram quando saem do padrão,
        então o plano antigo mantém o hash (e não é regravado à toa)."""
        dados = self.to_dict()
        if self.macro_priority == DEFAULT_MACRO_PRIORITY:
            dados.pop("macro_priority")
        if self.manual_macros is None:
            dados.pop("manual_targets")
        if self.cycle_kinds is None:
            dados.pop("cycle_days")
        return dados

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
            "macro_priority": self.macro_priority.value,
            "manual_targets": self.manual_macros.to_dict() if self.manual_macros else None,
            "cycle_days": self.cycle_days,
        }


@dataclass(frozen=True)
class PlanValidation:
    """Resultado de `build_plan_settings`: as configurações ou os códigos de erro."""

    settings: Optional[PlanSettings]
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.settings is not None and not self.errors


def normalize_priority(value: Any) -> Optional[MacroPriority]:
    """Prioridade de macros do payload (vazio = padrão); código desconhecido = None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_MACRO_PRIORITY
    try:
        return MacroPriority(str(value).strip().lower())
    except ValueError:
        return None


def _manual_macros(raw: Any, strategy: Strategy, advanced: bool, errors: list[str]) -> Optional[ManualMacros]:
    """Gramas manuais do payload, validadas pelo motor sem a base de g/kg (peso e MLG são do
    aluno e entram em `check_plan_manual_targets`): formato, valor inválido e bloqueio de energia
    e de gordura. Erro vira código `<código>:<campo>`; dá `None` com erro."""
    if not isinstance(raw, Mapping) or any(chave not in MANUAL_KEYS for chave in raw):
        errors.append(_code(ERR_INVALID_CODE, FIELD_MANUAL_TARGETS))
        return None
    base = StrategyParams.defaults_for(strategy, advanced)
    checagem = check_manual_targets(raw.get("protein_g"), raw.get("carb_g"), raw.get("fat_g"), base)
    bloqueados = [codigo for campo in checagem.fields if campo.status == RangeStatus.BLOCKED for codigo in campo.codes]
    if bloqueados:
        errors.extend(codigo for codigo in bloqueados if codigo not in errors)
        return None
    macros = checagem.macros
    assert macros is not None
    return ManualMacros(protein_g=macros.protein_g, carb_g=macros.carb_g, fat_g=macros.fat_g)


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
    macro_priority: Any = None,
    manual_targets: Any = None,
    cycle_days: Any = None,
) -> PlanValidation:
    """Normaliza e valida o plano. Campo ausente (None) recebe o default da
    estratégia; valor fora da faixa da fonte vira `param_out_of_range:<campo>`.
    `manual_targets` = `{protein_g, carb_g, fat_g}`; `cycle_days` = mapa dia da semana
    (0 = segunda … 6 = domingo) -> `high`/`normal`/`low` (D5)."""
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

    prioridade = normalize_priority(macro_priority)
    if prioridade is None:
        erros.append(_code(ERR_INVALID_CODE, FIELD_MACRO_PRIORITY))
        prioridade = DEFAULT_MACRO_PRIORITY

    manual: Optional[ManualMacros] = None
    if manual_targets is not None:
        manual = _manual_macros(manual_targets, estrategia, avancado, erros)
        if manual is not None and prioridade != DEFAULT_MACRO_PRIORITY:
            erros.append(_code(ERR_PARAM_NOT_APPLICABLE, FIELD_MACRO_PRIORITY))

    ciclo: Optional[tuple[DayKind, ...]] = None
    if cycle_days is not None:
        dias = validate_cycle_days(cycle_days)
        erros.extend(codigo for codigo in dias.errors if codigo not in erros)
        ciclo = dias.kinds if dias.cycles else None

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
        macro_priority=prioridade,
        manual_macros=manual,
        cycle_kinds=ciclo,
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


# ---------------------------------------------------------------------------
# Gramas manuais com o sujeito (D6), avisos e projeção do plano (N2)
# ---------------------------------------------------------------------------


def check_plan_manual_targets(
    settings: PlanSettings, subject: Subject, target_kcal: Any = None, bmr_kcal: Any = None
) -> Optional[ManualTargetsCheck]:
    """Checagem completa das gramas manuais do plano contra o sujeito (peso e MLG dão a base
    dos g/kg). `None` quando o plano não tem gramas manuais. `target_kcal` é a meta da
    ESTRATÉGIA (sem as gramas) e `bmr_kcal` a TMB, só para avisos."""
    if settings.manual_macros is None:
        return None
    return check_manual_targets(
        *settings.manual_macros.as_tuple(),
        settings.strategy_params(),
        subject.weight_kg,
        resolved_ffm_kg(subject),
        target_kcal,
        bmr_kcal,
    )


def manual_save_errors(check: Optional[ManualTargetsCheck], subject: Subject) -> tuple[str, ...]:
    """O que impede SALVAR as gramas manuais: sem peso do aluno (`manual_targets_require_weight`)
    ou algum campo BLOQUEADO pelo motor. Aviso nunca impede (D6)."""
    if check is None:
        return ()
    erros: list[str] = []
    if not weight_is_valid(subject.weight_kg):
        erros.append(ERR_MANUAL_REQUIRES_WEIGHT)
    for campo in check.fields:
        if campo.status == RangeStatus.BLOCKED:
            erros.extend(codigo for codigo in campo.codes if codigo not in erros)
    return tuple(erros)


def manual_save_warnings(check: Optional[ManualTargetsCheck]) -> tuple[str, ...]:
    """Avisos (salva, mas mostra): códigos dos campos fora da faixa recomendada e as limitações da checagem."""
    if check is None:
        return ()
    avisos: list[str] = []
    for campo in check.fields:
        if campo.status == RangeStatus.WARNING:
            avisos.extend(codigo for codigo in campo.codes if codigo not in avisos)
    avisos.extend(codigo for codigo in check.limitations if codigo not in avisos)
    return tuple(avisos)


def plan_warnings(settings: PlanSettings, subject: Subject, tdee_kcal: Any = None) -> tuple[str, ...]:
    """Avisos do plano que NÃO impedem salvar nem publicar. O ritmo implicado pelo superávit do
    bulking fora de `strategy.weekly_rate_share_range_for` é só aviso: o default avançado do
    motor (superávit 5%, TDEE 2800, 12 kg de gordura, 80 kg) dá cerca de 0,21%/semana, abaixo do
    piso de 0,25% (a faixa de superávit e a de ritmo não são coerentes entre si; mapa §81)."""
    faixa = weekly_rate_share_range_for(settings.strategy)
    if settings.strategy != Strategy.BULKING or faixa is None:
        return ()
    ritmo = weekly_rate_share_for(
        settings.strategy_params(), tdee_kcal, subject.weight_kg, resolved_fat_mass_kg(subject)
    )
    if ritmo is not None and not faixa[0] <= ritmo <= faixa[1]:
        return (REASON_RATE_OUTSIDE_RANGE,)
    return ()


def project_plan_goal(
    settings: PlanSettings, subject: Subject, tdee_kcal: Any = None, as_of: Optional[date] = None
) -> GoalProjection:
    """Projeção do alvo do plano (`phases.project_goal`) para o sujeito de hoje: peso-alvo OU %G
    alvo do plano (os dois ou nenhum voltam como dados insuficientes, com o motivo)."""
    return project_goal(
        settings.strategy_params(),
        subject.weight_kg,
        goal_weight_kg=settings.goal_weight_kg,
        goal_body_fat_percent=settings.goal_body_fat_percent,
        current_body_fat_percent=subject.body_fat_percent,
        as_of=as_of,
        tdee_kcal=tdee_kcal,
        fat_mass_kg=resolved_fat_mass_kg(subject),
    )


def training_weekdays_from_template(slots: Sequence[tuple[Optional[int], bool]]) -> Optional[tuple[int, ...]]:
    """Dias da semana (0 = segunda … 6 = domingo) com treino no Modelo, a partir dos slots
    `(weekday 1 = segunda … 7 = domingo | None, é_treino)`. O dia da semana do slot é
    informativo no Modelo; sem NENHUM slot com dia da semana não há como mapear: `None`."""
    com_dia = [(dia, treino) for dia, treino in slots if dia is not None and 1 <= int(dia) <= 7]
    if not com_dia:
        return None
    return tuple(sorted({int(dia) - 1 for dia, treino in com_dia if treino}))


def propose_cycle_from_template(slots: Sequence[tuple[Optional[int], bool]]) -> Optional[CycleDays]:
    """Proposta do botão "puxar do Modelo" (D5): `None` sem como mapear os dias; senão
    `carb_cycling.propose_day_kinds` sobre os dias de treino do Modelo."""
    dias = training_weekdays_from_template(slots)
    return None if dias is None else propose_day_kinds(dias)

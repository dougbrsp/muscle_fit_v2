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
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Final, Mapping, Optional, Sequence

from . import energy_expenditure as ee
from .anthropometrics import ActivityLevel, Subject, normalize_activity_level, positive_number, weight_is_valid
from .body_composition import body_fat_is_valid, resolved_fat_mass_kg, resolved_ffm_kg, target_weight_for_body_fat
from .bmr import DEFAULT_BMR_FORMULA, BmrFormula
from ..cardio_envelope import slots_have_weekdays
from .carb_cycling import CycleDays, DayKind, propose_day_kinds, validate_cycle_days
from .daily_target import PlanParams, compute_daily_target, manual_adds_training, rest_day_context
from .macros import meals_per_day_is_valid
from .energy_density import weekly_kg_for_daily_kcal
from .macros import kcal_from_macros
from .manual_targets import (
    DEFAULT_MANUAL_TRAINING_MODE,
    KCAL_BLOCK_MAX,
    KCAL_BLOCK_MIN,
    ManualTargetsCheck,
    ManualTrainingMode,
    RangeStatus,
    check_manual_targets,
)
from .phases import (
    GoalProjection,
    ProjectionStatus,
    REASON_INVALID_TARGET,
    REASON_MISSING_BODY_FAT,
    REASON_MISSING_TARGET,
    REASON_MISSING_WEIGHT,
    REASON_RATE_OUTSIDE_RANGE,
    REASON_TWO_TARGETS,
    project_goal,
    resolve_stop_body_fat,
    should_stop,
)
from .strategy import (
    CUT_MIN_INTAKE_BMR_MULTIPLE,
    DEFAULT_MACRO_PRIORITY,
    LIMIT_PARAM_OUT_OF_RANGE,
    MAINT_KCAL_ADJUST_DEFAULT,
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
NOTE_TEMPLATE_PARTIAL_WEEKDAYS = "template_partial_weekdays"
# Pisos do ciclo (decisão do líder, N2 rodada 1): o menor e o maior dia do ciclo são conferidos ao salvar e publicar.
ERR_CYCLE_KCAL_BELOW_MIN = "cycle_kcal_below_minimum"  # bloqueia: abaixo de `manual_targets.KCAL_BLOCK_MIN`
ERR_CYCLE_KCAL_ABOVE_MAX = "cycle_kcal_above_maximum"  # bloqueia: acima de `manual_targets.KCAL_BLOCK_MAX`
WARN_CYCLE_KCAL_BELOW_BMR = "cycle_kcal_below_bmr"  # avisa, como na D6
WARN_CYCLE_PARTIAL_WEEK = "cycle_partial_week"  # publicação fora da segunda: sem compensação
# Projeção com gramas manuais.
REASON_MANUAL_AGAINST_STRATEGY = "projection_manual_against_strategy"
REASON_MANUAL_MISSING_TDEE = "projection_manual_missing_tdee"
REASON_MANUAL_MISSING_FAT_MASS = "projection_manual_missing_fat_mass"
# Premissa da projeção: sem histórico de treino o TDEE é a base sem treino.
REASON_TDEE_WITHOUT_TRAINING = "projection_tdee_without_training"
# Janela (dias ANTES de hoje) do gasto médio de treino que estabiliza o TDEE da projeção. Default de desenho.
PROJECTION_TRAINING_WINDOW_DAYS: Final[int] = 14
# Rascunhos por usuário e itens por listagem (default de desenho, N2 rodada 1): autogerido sem teto encheria a tabela.
MAX_DRAFTS_PER_USER: Final[int] = 20
PLAN_LIST_MAX: Final[int] = 100
ERR_DRAFT_LIMIT = "draft_limit"
# Dia de referência só para escolher o dia da semana ao conferir o ciclo (uma segunda-feira).
_CYCLE_CHECK_MONDAY: Final[date] = date(2026, 1, 5)
FIELD_MANUAL_TARGETS = "manual_targets"
FIELD_MANUAL_TRAINING_MODE = "manual_training_mode"
FIELD_MACRO_PRIORITY = "macro_priority"
# Chaves das gramas manuais no payload e no JSON gravado.
MANUAL_KEYS: Final[tuple[str, str, str]] = ("protein_g", "carb_g", "fat_g")

# Campos que só valem para uma estratégia.
STRATEGY_ONLY_FIELDS: Final[dict[str, Strategy]] = {
    "weekly_rate_share": Strategy.CUTTING,
    "surplus_share": Strategy.BULKING,
    "carb_min_g_per_kg": Strategy.BULKING,
    "maintenance_kcal_adjust": Strategy.MAINTENANCE,
}


def _code(prefix: str, field: str) -> str:
    return f"{prefix}:{field}"


def plan_state(status: Any, superseded_at: Any) -> PlanState:
    """Rascunho, vigente (publicado sem `superseded_at`) ou substituído."""
    if str(status) != PlanStatus.PUBLISHED.value:
        return PlanState.DRAFT
    return PlanState.CURRENT if superseded_at is None else PlanState.SUPERSEDED


def is_archivable_plan(status: Any, superseded_at: Any) -> bool:
    """Arquivar (só ocultar da lista, reversível) vale SÓ para plano substituído: o rascunho tem DELETE e o vigente
    governa a meta de hoje. Arquivado continua alimentando o histórico de metas (`load_published_plans`)."""
    return plan_state(status, superseded_at) is PlanState.SUPERSEDED


@dataclass(frozen=True)
class ManualMacros:
    """Gramas manuais do dia (D6): proteína, carboidrato e gordura."""

    protein_g: float
    carb_g: float
    fat_g: float
    training_mode: ManualTrainingMode = DEFAULT_MANUAL_TRAINING_MODE

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.protein_g, self.carb_g, self.fat_g)

    def to_dict(self) -> dict[str, Any]:
        return {"protein_g": self.protein_g, "carb_g": self.carb_g, "fat_g": self.fat_g, "training_mode": self.training_mode.value}


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
    maintenance_kcal_adjust: int = MAINT_KCAL_ADJUST_DEFAULT  # N7: kcal/dia da manutenção (0 = como sempre)

    @property
    def is_auto_intervals(self) -> bool:
        return self.tdee_mode == ee.TdeeMode.AUTO_INTERVALS

    @property
    def subject_activity_level(self) -> Optional[ActivityLevel]:
        """Nível que sobrescreve o do perfil no sujeito. No modo automático `activity_level` é a ROTINA fora do
        exercício (outro eixo): o fator com exercício do fallback continua vindo do nível do perfil."""
        return None if self.is_auto_intervals else self.activity_level

    @property
    def manual_adds_training(self) -> bool:
        """Gramas manuais no modo que soma o treino do dia (e o gasto do treino à parte)."""
        return manual_adds_training(self.plan_params())

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
            maintenance_kcal_adjust=self.maintenance_kcal_adjust,
        )

    def plan_params(self, ffm_age_days: Optional[int] = None) -> PlanParams:
        return PlanParams(
            strategy_params=self.strategy_params(),
            bmr_formula=self.bmr_formula,
            tdee_mode=self.tdee_mode,
            routine_level=self.activity_level if self.is_auto_intervals else None,
            meals_per_day=self.meals_per_day,
            ffm_age_days=ffm_age_days,
            macro_priority=self.macro_priority,
            manual_macros=self.manual_macros.as_tuple() if self.manual_macros else None,
            manual_training_mode=self.manual_macros.training_mode if self.manual_macros else DEFAULT_MANUAL_TRAINING_MODE,
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
        elif self.manual_macros.training_mode == DEFAULT_MANUAL_TRAINING_MODE:
            dados["manual_targets"] = {k: v for k, v in dados["manual_targets"].items() if k != "training_mode"}
        if self.cycle_kinds is None:
            dados.pop("cycle_days")
        if self.maintenance_kcal_adjust == MAINT_KCAL_ADJUST_DEFAULT:
            dados.pop("maintenance_kcal_adjust")  # plano antigo mantém o hash das entradas
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
            "maintenance_kcal_adjust": self.maintenance_kcal_adjust,
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


def manual_training_modes_for(tdee_mode: ee.TdeeMode) -> tuple[ManualTrainingMode, ...]:
    """Modos das gramas manuais que valem para o modo de gasto do treino: `add_training` só com o treino à parte
    (explícito); com o treino já no fator do gasto só o `fixed`. A tela só oferece estes."""
    if tdee_mode in (ee.TdeeMode.EXPLICIT_TRAINING, ee.TdeeMode.AUTO_INTERVALS):
        # No automático, `add_training` soma o treino e os extras no dia sincronizado; no dia de fallback vale como `fixed`.
        return (ManualTrainingMode.FIXED, ManualTrainingMode.ADD_TRAINING)
    return (ManualTrainingMode.FIXED,)


def _manual_training_mode(value: Any) -> Optional[ManualTrainingMode]:
    """Modo das gramas manuais com o treino (vazio = padrão); código desconhecido = None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_MANUAL_TRAINING_MODE
    try:
        return ManualTrainingMode(str(value).strip().lower())
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
    manual_training_mode: Any = None,
    maintenance_kcal_adjust: Any = None,
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
        # 0 (o padrão gravado em todo plano) não conta como "informado" fora da manutenção.
        "maintenance_kcal_adjust": maintenance_kcal_adjust if maintenance_kcal_adjust not in (None, 0) else None,
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

    ajuste_manutencao = MAINT_KCAL_ADJUST_DEFAULT
    if informados["maintenance_kcal_adjust"] is not None and estrategia == Strategy.MAINTENANCE:
        bruto = informados["maintenance_kcal_adjust"]
        if isinstance(bruto, bool) or not isinstance(bruto, (int, float)) or int(bruto) != bruto:
            erros.append(_code(LIMIT_PARAM_OUT_OF_RANGE, "maintenance_kcal_adjust"))  # só inteiro (kcal/dia)
        else:
            ajuste_manutencao = int(bruto)

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
    modo_manual = _manual_training_mode(manual_training_mode)
    if modo_manual is None:
        erros.append(_code(ERR_INVALID_CODE, FIELD_MANUAL_TRAINING_MODE))
        modo_manual = DEFAULT_MANUAL_TRAINING_MODE
    elif manual_targets is None and modo_manual != DEFAULT_MANUAL_TRAINING_MODE:
        erros.append(_code(ERR_PARAM_NOT_APPLICABLE, FIELD_MANUAL_TRAINING_MODE))
    if modo_manual not in manual_training_modes_for(modo):
        # `add_training` soma o treino do dia, que só existe à parte quando o gasto do treino é explícito.
        erros.append(_code(ERR_PARAM_NOT_APPLICABLE, FIELD_MANUAL_TRAINING_MODE))
    if manual_targets is not None:
        manual = _manual_macros(manual_targets, estrategia, avancado, erros)
        if manual is not None:
            manual = replace(manual, training_mode=modo_manual)
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
        maintenance_kcal_adjust=ajuste_manutencao,
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
    """Avisos do plano que NÃO impedem salvar nem publicar. O ritmo implicado pelo superávit do bulking
    fora de `strategy.weekly_rate_share_range_for` (a faixa do nível, padrão ou avançado) é só aviso. Com
    gramas manuais o superávit é o SALDO manual contra o `tdee_kcal`, o mesmo da projeção; gramas que não
    dão superávit não geram este aviso (a projeção já diz que são incoerentes). Faixas e a grade: mapa §81."""
    faixa = weekly_rate_share_range_for(settings.strategy, settings.advanced)
    if settings.strategy != Strategy.BULKING or faixa is None:
        return ()
    params = settings.strategy_params()
    if settings.manual_macros is not None:
        tdee = positive_number(tdee_kcal)
        saldo = (kcal_from_macros(*settings.manual_macros.as_tuple()) or 0.0) - (tdee or 0.0)
        if tdee is None or saldo <= 0:
            return ()
        params = replace(params, surplus_share=saldo / tdee)
    ritmo = weekly_rate_share_for(params, tdee_kcal, subject.weight_kg, resolved_fat_mass_kg(subject))
    if ritmo is not None and not faixa[0] <= ritmo <= faixa[1]:
        return (REASON_RATE_OUTSIDE_RANGE,)
    return ()


def training_kcal_of_day(is_training_day: bool, resolved_kcal: Any) -> Optional[float]:
    """Gasto de treino de um dia PASSADO para a média da projeção. `resolved_kcal` é a kcal JÁ resolvida pelo
    dono (`energy_expenditure.resolve_training_kcal`: informada > MET). 0 em dia sem treino; a kcal do treino; ou
    `None` (desconhecida) só em dia de treino sem kcal e sem duração ou peso para estimar."""
    kcal = positive_number(resolved_kcal)
    if kcal is not None:
        return kcal
    return None if is_training_day else 0.0


def auto_intervals_blockers(settings: PlanSettings, profile_level: Optional[ActivityLevel]) -> tuple[str, ...]:
    """Bloqueios do plano `auto_intervals`: sem nível de atividade no PERFIL o fallback (fator com exercício) não tem
    fator, e não se inventa um. Os outros modos não dependem disto."""
    if settings.is_auto_intervals and profile_level is None:
        return (ee.LIMIT_AUTO_REQUIRES_PROFILE_LEVEL,)
    return ()


def rest_day_strategy_kcal(
    settings: PlanSettings, subject: Subject, day: date, scale_bmr: Any = None, ffm_age_days: Optional[int] = None
) -> Optional[float]:
    """Meta calórica da ESTRATÉGIA num dia sem treino (sem gramas manuais nem ciclo): a referência dos avisos de
    energia das gramas manuais no modo que soma o treino."""
    base = replace(settings.plan_params(ffm_age_days), manual_macros=None, cycle_kinds=None)
    return compute_daily_target(subject, base, rest_day_context(base.tdee_mode, day), scale_bmr=scale_bmr).kcal_target.kcal


def projection_tdee(
    tdee: ee.TdeeResult, past_training_kcal: Sequence[Optional[float]], training_in_targets: bool = False
) -> tuple[Optional[float], tuple[str, ...]]:
    """TDEE ESTÁVEL da projeção (decisão do líder, N2 rodada 2): não pode depender do treino do dia em que a
    prévia é aberta. Modo explícito: a base sem treino do dia (TMB × fator) mais a média do gasto de treino dos
    dias passados conhecidos (`PROJECTION_TRAINING_WINDOW_DAYS`); sem histórico de treino, só a base, com a
    premissa `projection_tdee_without_training`. Modo com o treino no fator: o TDEE do fator."""
    if tdee.kcal is None or tdee.mode != ee.TdeeMode.EXPLICIT_TRAINING:
        return tdee.kcal, ()
    bmr, fator = positive_number(tdee.bmr_kcal), positive_number(tdee.activity_factor)
    if bmr is None or fator is None:
        return tdee.kcal, ()
    base = bmr * fator
    if training_in_targets:
        # Gramas manuais que SOMAM o treino em carbo: o saldo de todo dia é a meta de repouso contra a base sem treino,
        # então o treino não entra no TDEE da projeção (contaria duas vezes).
        return base, ()
    conhecidos = [kcal for kcal in past_training_kcal if kcal is not None]
    if not conhecidos or not any(kcal > 0 for kcal in conhecidos):
        return base, (REASON_TDEE_WITHOUT_TRAINING,)
    return base + sum(conhecidos) / len(conhecidos), ()


def project_plan_goal(
    settings: PlanSettings,
    subject: Subject,
    tdee_kcal: Any = None,
    as_of: Optional[date] = None,
    premises: Sequence[str] = (),
    start_weight_kg: Any = None,
    start_body_fat_percent: Any = None,
) -> GoalProjection:
    """Projeção do alvo do plano (`phases.project_goal`) para o sujeito de hoje: peso-alvo OU %G
    alvo do plano (os dois ou nenhum voltam como dados insuficientes, com o motivo). Com gramas
    manuais o ritmo vem das kcal manuais contra o TDEE (cutting: o déficit; bulking: o superávit,
    que `strategy.weekly_rate_share_for` converte em ritmo); gramas que vão contra a estratégia
    devolvem `incoherent_with_strategy` e sem TDEE, dados insuficientes, sempre com o motivo."""
    projecao = _project_plan_goal(settings, subject, tdee_kcal, as_of, start_weight_kg, start_body_fat_percent)
    # A premissa do TDEE só aparece quando ele entrou no ritmo projetado (bulking ou gramas manuais fora da manutenção).
    bulking_deriva = (
        settings.strategy == Strategy.BULKING
        and positive_number(tdee_kcal) is not None
        and positive_number(subject.weight_kg) is not None
        and resolved_fat_mass_kg(subject) is not None
        and settings.surplus_share is not None
    )  # sem isso o ritmo cai no valor padrão e o TDEE não entrou
    tdee_no_ritmo = projecao.status == ProjectionStatus.PROJECTED and (
        bulking_deriva or (settings.manual_macros is not None and settings.strategy != Strategy.MAINTENANCE)
    )
    if not premises or not tdee_no_ritmo:
        return projecao
    return replace(projecao, reasons=(*projecao.reasons, *(p for p in premises if p not in projecao.reasons)))


# Motivos do `project_goal` que não dependem do ritmo (nem das gramas): faltam peso, % de gordura ou alvo, ou o alvo é inválido.
_TARGET_OR_SUBJECT_REASONS: Final[frozenset[str]] = frozenset(
    {REASON_MISSING_WEIGHT, REASON_MISSING_BODY_FAT, REASON_MISSING_TARGET, REASON_TWO_TARGETS, REASON_INVALID_TARGET}
)


def _project_plan_goal(
    settings: PlanSettings,
    subject: Subject,
    tdee_kcal: Any,
    as_of: Optional[date],
    start_weight_kg: Any = None,
    start_body_fat_percent: Any = None,
) -> GoalProjection:
    def _projetar(params: StrategyParams) -> GoalProjection:
        return project_goal(
            params,
            subject.weight_kg,
            goal_weight_kg=settings.goal_weight_kg,
            goal_body_fat_percent=settings.goal_body_fat_percent,
            current_body_fat_percent=subject.body_fat_percent,
            start_weight_kg=start_weight_kg,
            start_body_fat_percent=start_body_fat_percent,
            as_of=as_of,
            tdee_kcal=tdee_kcal,
            fat_mass_kg=resolved_fat_mass_kg(subject),
        )

    params = settings.strategy_params()
    # Sempre pelo ritmo do plano primeiro: alvo já alcançado, alvo incoerente ou dado do sujeito/alvo faltando não
    # dependem das gramas e valem como estão (com o `goal_kind`, o atual e o alvo).
    base = _projetar(params)
    if settings.manual_macros is None or settings.strategy == Strategy.MAINTENANCE:
        return base
    if base.status in (ProjectionStatus.GOAL_REACHED, ProjectionStatus.INCOHERENT) or (
        base.status == ProjectionStatus.INSUFFICIENT_DATA and _TARGET_OR_SUBJECT_REASONS.intersection(base.reasons)
    ):
        return base

    def _recusada(status: ProjectionStatus, motivo: str) -> GoalProjection:
        # Mantém o que não depende das gramas (tipo do alvo, atual, alvo, % do caminho) e tira o que dependeria delas.
        return replace(
            base,
            status=status,
            reasons=(motivo,),
            weeks=None,
            eta_date=None,
            weekly_rate_share=None,
            is_minimum_estimate=False,
        )

    tdee = positive_number(tdee_kcal)
    massa_gorda = resolved_fat_mass_kg(subject)
    if massa_gorda is None:
        # Sem massa gorda não há densidade do tecido nem ritmo derivado: nunca cair no ritmo padrão.
        return _recusada(ProjectionStatus.INSUFFICIENT_DATA, REASON_MANUAL_MISSING_FAT_MASS)
    if tdee is None:
        return _recusada(ProjectionStatus.INSUFFICIENT_DATA, REASON_MANUAL_MISSING_TDEE)
    kcal = kcal_from_macros(*settings.manual_macros.as_tuple())
    saldo = (kcal or 0.0) - tdee  # negativo = déficit
    contra = saldo >= 0 if settings.strategy == Strategy.CUTTING else saldo <= 0
    if contra:
        return _recusada(ProjectionStatus.INCOHERENT, REASON_MANUAL_AGAINST_STRATEGY)
    if settings.strategy == Strategy.CUTTING:
        kg_semana = weekly_kg_for_daily_kcal(-saldo, massa_gorda)
        peso = positive_number(subject.weight_kg)
        ritmo = kg_semana / peso if kg_semana is not None and peso is not None else None
        params = replace(params, weekly_rate_share=ritmo)
    else:
        params = replace(params, surplus_share=saldo / tdee)
    return _projetar(params)


def check_cycle_floors(
    settings: PlanSettings, subject: Subject, scale_bmr: Any = None, ffm_age_days: Optional[int] = None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Pisos do ciclo (erros, avisos): o menor e o maior dia do ciclo, num dia sem treino, com ou sem gramas
    manuais. Energia abaixo de `KCAL_BLOCK_MIN` ou acima de `KCAL_BLOCK_MAX` BLOQUEIA; abaixo da TMB
    (`CUT_MIN_INTAKE_BMR_MULTIPLE` × TMB) só avisa, como na D6. Sem ciclo: nada."""
    if settings.cycle_kinds is None:
        return (), ()
    params = settings.plan_params(ffm_age_days)
    erros: list[str] = []
    avisos: list[str] = []
    for tipo in dict.fromkeys(settings.cycle_kinds):
        indice = settings.cycle_kinds.index(tipo)
        meta = compute_daily_target(
            subject, params, rest_day_context(params.tdee_mode, _CYCLE_CHECK_MONDAY + timedelta(days=indice)), scale_bmr=scale_bmr
        )
        kcal = meta.macros.kcal
        if kcal is None:
            continue
        if kcal < KCAL_BLOCK_MIN and ERR_CYCLE_KCAL_BELOW_MIN not in erros:
            erros.append(ERR_CYCLE_KCAL_BELOW_MIN)
        if kcal > KCAL_BLOCK_MAX and ERR_CYCLE_KCAL_ABOVE_MAX not in erros:
            erros.append(ERR_CYCLE_KCAL_ABOVE_MAX)
        tmb = positive_number(meta.bmr.kcal)
        if tmb is not None and kcal < CUT_MIN_INTAKE_BMR_MULTIPLE * tmb and WARN_CYCLE_KCAL_BELOW_BMR not in avisos:
            avisos.append(WARN_CYCLE_KCAL_BELOW_BMR)
    return tuple(erros), tuple(avisos)


def partial_week_warnings(settings: PlanSettings, today: date) -> tuple[str, ...]:
    """Publicar com ciclo fora da segunda-feira: os dias que já passaram não entram e não há compensação
    (decisão do líder); a média da semana só fecha em semana completa do mesmo plano."""
    return (WARN_CYCLE_PARTIAL_WEEK,) if settings.cycle_kinds is not None and today.weekday() != 0 else ()


def _weekday_slots(slots: Sequence[tuple[Optional[int], bool]]) -> list[dict[str, Any]]:
    return [{"weekday": dia} for dia, _treino in slots]


def template_weekdays_note(slots: Sequence[tuple[Optional[int], bool]]) -> Optional[str]:
    """Por que o Modelo não dá para mapear nos dias da semana: nenhum slot com dia (`template_without_weekdays`)
    ou só alguns (`template_partial_weekdays`). `None` = todos os slots têm dia."""
    if slots_have_weekdays(_weekday_slots(slots)):
        return None
    if any(slots_have_weekdays([slot]) for slot in _weekday_slots(slots)):
        return NOTE_TEMPLATE_PARTIAL_WEEKDAYS
    return NOTE_TEMPLATE_WITHOUT_WEEKDAYS


def training_weekdays_from_template(slots: Sequence[tuple[Optional[int], bool]]) -> Optional[tuple[int, ...]]:
    """Dias da semana (0 = segunda … 6 = domingo) com treino no Modelo, a partir dos slots
    `(weekday 1 = segunda … 7 = domingo | None, é_treino)`. O dia da semana do slot é informativo no
    Modelo: só mapeia quando TODOS os slots o têm (o mesmo predicado de `cardio_envelope`); senão `None`."""
    if not slots_have_weekdays(_weekday_slots(slots)):
        return None
    return tuple(sorted({int(dia) - 1 for dia, treino in slots if treino and dia is not None}))


def propose_cycle_from_template(slots: Sequence[tuple[Optional[int], bool]]) -> Optional[CycleDays]:
    """Proposta do botão "puxar do Modelo" (D5): `None` sem como mapear os dias; senão
    `carb_cycling.propose_day_kinds` sobre os dias de treino do Modelo."""
    dias = training_weekdays_from_template(slots)
    return None if dias is None else propose_day_kinds(dias)

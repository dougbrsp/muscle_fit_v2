"""Rótulos pt-BR dos códigos do motor de nutrição e vocabulário do rótulo
nutricional (RDC 429/2020 + IN 75/2020).

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão `rotulo_estrategia`).
Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §4 e §7.

Telas, prompts e relatórios NÃO escrevem estes textos à mão: o FE lê o bloco
`nutrition` de `motor-constants.generated.json` (gerado por
`export_for_frontend()`), prompts interpolam daqui.

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Mapping

from . import adaptive_tdee as atdee_mod
from . import ai_usage as ai_usage_mod
from . import bmr as bmr_mod
from . import body_composition as bc_mod
from . import carb_cycling as cc_mod
from . import catalog as catalog_mod
from . import daily_target as dt_mod
from . import dashboard as dash_mod
from . import diary as diary_mod
from . import energy_expenditure as ee_mod
from . import external_sources as ext_mod
from . import favorites as fav_mod
from . import label_photo as lp_mod
from . import macros as macros_mod
from . import manual_targets as manual_mod
from . import meal_math as meal_math_mod
from . import meal_parse as meal_parse_mod
from . import meal_photo as mp_mod
from . import phases as phases_mod
from . import plan as plan_mod
from . import plan_suggestion as ps_mod
from . import strategy as strategy_mod
from . import weekly_budget as wb_mod
from . import weekly_checkin as wc_mod
from .ai_usage import AiFeature
from .anthropometrics import ActivityLevel, Sex
from .bmr import BmrFormula
from .body_composition import BodyFatMethod
from .carb_cycling import DayKind
from .energy_density import DAYS_PER_WEEK
from .catalog import FoodSource
from .diary import MealSlot  # noqa: F401 - reexport (o enum mora no dono do diário desde o L4)
from .energy_expenditure import SessionKind, TdeeMode, TrainingKcalNature
from .external_sources import UsdaDataType
from .label_photo import LabelLegibility
from .meal_math import GENERIC_PORTIONS, MealNature, Unit
from .meal_parse import MatchStatus
from .manual_targets import RangeStatus
from .phases import BreakProtocol, GoalKind, PhaseKind, ProjectionStatus
from .plan import PlanState
from .strategy import MacroPriority, ProteinBasis, Strategy
from .weekly_checkin import CheckinStatus, CheckinVerdict


SEX_LABELS: Final[Mapping[Sex, str]] = {
    Sex.MALE: "Masculino",
    Sex.FEMALE: "Feminino",
    Sex.OTHER: "Outro",
}

ACTIVITY_LEVEL_LABELS: Final[Mapping[ActivityLevel, str]] = {
    ActivityLevel.SEDENTARY: "Sedentário",
    ActivityLevel.LIGHT: "Leve",
    ActivityLevel.MODERATE: "Moderado",
    ActivityLevel.ACTIVE: "Ativo",
    ActivityLevel.VERY_ACTIVE: "Muito ativo",
}

STRATEGY_LABELS: Final[Mapping[Strategy, str]] = {
    Strategy.CUTTING: "Cutting",
    Strategy.BULKING: "Bulking",
    Strategy.MAINTENANCE: "Manutenção",
}

PROTEIN_BASIS_LABELS: Final[Mapping[ProteinBasis, str]] = {
    ProteinBasis.FAT_FREE_MASS: "g por kg de massa livre de gordura",
    ProteinBasis.BODY_WEIGHT: "g por kg de peso corporal",
}

DAY_KIND_LABELS: Final[Mapping[DayKind, str]] = {
    DayKind.HIGH: "Dia alto em carbo",
    DayKind.NORMAL: "Dia normal",
    DayKind.LOW: "Dia baixo em carbo",
}

MACRO_PRIORITY_LABELS: Final[Mapping[MacroPriority, str]] = {
    MacroPriority.BALANCED: "Equilibrado",
    MacroPriority.FIXED_PROTEIN: "Proteína fixa (carboidrato e gordura equilibrados)",
    MacroPriority.HIGH_CARB: "Carboidrato alto (gordura baixa)",
    MacroPriority.HIGH_FAT: "Gordura alta (carboidrato baixo)",
}

MANUAL_TRAINING_MODE_LABELS: Final[Mapping[manual_mod.ManualTrainingMode, str]] = {
    manual_mod.ManualTrainingMode.FIXED: "Metas fixas todo dia",
    manual_mod.ManualTrainingMode.ADD_TRAINING: "Somar o treino do dia em carboidrato",
}

# Explicação de cada modo (a tela mostra a do modo escolhido).
MANUAL_TRAINING_MODE_HINTS: Final[Mapping[manual_mod.ManualTrainingMode, str]] = {
    manual_mod.ManualTrainingMode.FIXED: (
        "Os números que você digitou valem todos os dias; treino e estratégia não alteram essas metas."
    ),
    manual_mod.ManualTrainingMode.ADD_TRAINING: (
        "Os números valem para o dia sem treino e o gasto do treino do dia soma em carboidrato, "
        "pela conversão de energia do motor. Só vale com o gasto do treino à parte."
    ),
}

RANGE_STATUS_LABELS: Final[Mapping[RangeStatus, str]] = {
    RangeStatus.WITHIN_RANGE: "Dentro da faixa recomendada",
    RangeStatus.WARNING: "Fora da faixa recomendada (salva com aviso)",
    RangeStatus.BLOCKED: "Valor não permitido",
}

GOAL_KIND_LABELS: Final[Mapping[GoalKind, str]] = {
    GoalKind.WEIGHT: "Peso-alvo",
    GoalKind.BODY_FAT: "% de gordura alvo",
}

PROJECTION_STATUS_LABELS: Final[Mapping[ProjectionStatus, str]] = {
    ProjectionStatus.PROJECTED: "Projeção até o alvo",
    ProjectionStatus.GOAL_REACHED: "Alvo alcançado",
    ProjectionStatus.INCOHERENT: "Alvo incoerente com a estratégia",
    ProjectionStatus.INSUFFICIENT_DATA: "Dados insuficientes para projetar",
}

# Dia da semana do ciclo de carbo (0 = segunda … 6 = domingo, como em `phases.refeed_dates`).
WEEKDAY_LABELS: Final[Mapping[int, str]] = {
    0: "Segunda-feira",
    1: "Terça-feira",
    2: "Quarta-feira",
    3: "Quinta-feira",
    4: "Sexta-feira",
    5: "Sábado",
    6: "Domingo",
}

MEAL_SLOT_LABELS: Final[Mapping[MealSlot, str]] = {
    MealSlot.BREAKFAST: "Café da manhã",
    MealSlot.MORNING_SNACK: "Lanche da manhã",
    MealSlot.LUNCH: "Almoço",
    MealSlot.AFTERNOON_SNACK: "Lanche da tarde",
    MealSlot.PRE_WORKOUT: "Pré-treino",
    MealSlot.POST_WORKOUT: "Pós-treino",
    MealSlot.DINNER: "Jantar",
    MealSlot.SUPPER: "Ceia",
    MealSlot.OTHER: "Outra refeição",
}

BMR_FORMULA_LABELS: Final[Mapping[BmrFormula, str]] = {
    BmrFormula.AUTO: "Automática",
    BmrFormula.MIFFLIN_ST_JEOR: "Mifflin-St Jeor",
    BmrFormula.KATCH_MCARDLE: "Katch-McArdle",
    BmrFormula.CUNNINGHAM: "Cunningham",
    BmrFormula.HARRIS_BENEDICT: "Harris-Benedict (revisada)",
}

TDEE_MODE_LABELS: Final[Mapping[TdeeMode, str]] = {
    TdeeMode.EXPLICIT_TRAINING: "Treino somado à parte",
    TdeeMode.INCLUDED_IN_FACTOR: "Treino embutido no fator",
}

SESSION_KIND_LABELS: Final[Mapping[SessionKind, str]] = {
    SessionKind.VIGOROUS: "Musculação vigorosa",
    SessionKind.SQUAT_DEADLIFT: "Agachamento / levantamento terra",
    SessionKind.MULTIPLE_EXERCISES: "Múltiplos exercícios",
    SessionKind.CIRCUIT: "Circuito",
}

# Natureza, não proveniência: "manual" × "Intervals" é rótulo de
# `domain/calories_source.py` (L0), nunca deste pacote.
TRAINING_KCAL_NATURE_LABELS: Final[Mapping[TrainingKcalNature, str]] = {
    TrainingKcalNature.REPORTED: "Informada",
    TrainingKcalNature.MET: "Estimada por MET",
}

MEAL_NATURE_LABELS: Final[Mapping[MealNature, str]] = {
    MealNature.DERIVED: "Calculado do catálogo",
    MealNature.ESTIMATED: "Estimado",
    MealNature.USER_ENTERED: "Informado por você",
    MealNature.MIXED: "Misto",
}

# Unidades do item da refeição (L4). As medidas caseiras usam o rótulo da
# própria porção genérica (`meal_math.GENERIC_PORTIONS`).
UNIT_LABELS: Final[Mapping[Unit, str]] = {
    Unit.GRAM: "Gramas (g)",
    Unit.MILLILITER: "Mililitros (ml)",
    Unit.UNIT: "Unidade",
    Unit.PORTION: "Porção",
    **{unidade: porcao.label for unidade, porcao in GENERIC_PORTIONS.items()},
}

BODY_FAT_METHOD_LABELS: Final[Mapping[BodyFatMethod, str]] = {
    BodyFatMethod.BIOIMPEDANCE: "Bioimpedância",
    BodyFatMethod.NAVY: "Circunferências (Marinha dos EUA)",
}

PHASE_KIND_LABELS: Final[Mapping[PhaseKind, str]] = {
    PhaseKind.CUTTING: "Cutting",
    PhaseKind.BULKING: "Bulking",
    PhaseKind.MAINTENANCE: "Manutenção",
    PhaseKind.DIET_BREAK: "Pausa de dieta",
    PhaseKind.REFEED: "Refeed",
}

BREAK_PROTOCOL_LABELS: Final[Mapping[BreakProtocol, str]] = {
    BreakProtocol.NONE: "Sem pausas programadas",
    BreakProtocol.MATADOR: "MATADOR",
    BreakProtocol.ICECAP: "ICECAP",
}

# Catálogo de alimentos (Lote L3a; regras e citações em `catalog`).
FOOD_SOURCE_LABELS: Final[Mapping[FoodSource, str]] = {
    FoodSource.TACO: "TACO 4ª ed.",
    FoodSource.USDA: "USDA FoodData Central",
    FoodSource.OFF: "Open Food Facts",
    FoodSource.TBCA_MANUAL: "TBCA (digitado)",
    FoodSource.USER: "Cadastro próprio",
    FoodSource.LABEL_PHOTO: "Foto do rótulo",
    FoodSource.AI_ESTIMATE: "Estimativa da IA",
}

# Tipos de dado do USDA FoodData Central importados (L3b; códigos do FDC em `external_sources`).
USDA_DATA_TYPE_LABELS: Final[Mapping[UsdaDataType, str]] = {
    UsdaDataType.FOUNDATION: "Foundation Foods",
    UsdaDataType.SR_LEGACY: "SR Legacy (Standard Reference)",
}

FOOD_BASIS_LABELS: Final[Mapping[Unit, str]] = {
    Unit.GRAM: f"por {meal_math_mod.PER_100G_REFERENCE:g} g",
    Unit.MILLILITER: f"por {meal_math_mod.PER_100G_REFERENCE:g} ml",
}

# Uso de IA (Lote L5a; features, cota e custo em `ai_usage`).
AI_FEATURE_LABELS: Final[Mapping[AiFeature, str]] = {
    AiFeature.MEAL_PARSE: "Leitura da refeição por IA",
    AiFeature.LABEL_PHOTO: "Leitura do rótulo por foto",
    AiFeature.MEAL_PHOTO: "Leitura da foto do prato",
    AiFeature.PLAN_SUGGESTION: "Sugestão de metas por IA",
}

# O que a cota conta, no plural, para o texto do 429.
_AI_QUOTA_NOUNS: Final[Mapping[AiFeature, str]] = {
    AiFeature.MEAL_PARSE: "leituras de refeição por IA",
    AiFeature.LABEL_PHOTO: "leituras de rótulo por foto",
    AiFeature.MEAL_PHOTO: "análises de foto do prato",
    AiFeature.PLAN_SUGGESTION: "sugestões de metas por IA",
}


# Foto do rótulo (Lote L6; upload, revisão e retenção em `label_photo`).
LABEL_LEGIBILITY_LABELS: Final[Mapping[LabelLegibility, str]] = {
    LabelLegibility.OK: "Tabela legível",
    LabelLegibility.PARTIAL: "Tabela parcialmente legível",
    LabelLegibility.UNREADABLE: "Tabela ilegível",
}

# Norma do rótulo nutricional, citada no prompt da leitura da foto (interpolada, nunca escrita à mão).
LABEL_REGULATION_REFERENCE: Final[str] = "RDC nº 429/2020 e IN nº 75/2020 da Anvisa"

# Leitura da refeição por IA (Lote L5b, `meal_parse`): a decisão do casamento de cada item.
MEAL_PARSE_MATCH_STATUS_LABELS: Final[Mapping[MatchStatus, str]] = {
    MatchStatus.SELECTED: "Encontrado no catálogo",
    MatchStatus.CHOICE: "Escolha o alimento",
    MatchStatus.ESTIMATED: "Estimado pela IA",
    MatchStatus.UNMATCHED: "Não encontrado no catálogo",
}


def days_label(days: int) -> str:
    """`1 dia`, `30 dias`: o prazo do dono, no texto para a pessoa."""
    return f"{days} dia" if days == 1 else f"{days} dias"


def ai_quota_exceeded_label(feature: Any) -> str:
    """Texto do 429 da feature, com a cota do dono (`ai_usage.DAILY_QUOTA`)."""
    codigo = ai_usage_mod.normalize_feature(feature)
    return (
        f"Limite de {ai_usage_mod.DAILY_QUOTA[codigo]} {_AI_QUOTA_NOUNS[codigo]} por dia atingido. "
        "Tente de novo amanhã."
    )


@dataclass(frozen=True)
class Rdc429Field:
    """Campo da tabela nutricional (RDC 429/2020): código estável, rótulo e unidade."""

    code: str
    label: str
    unit: str
    required: bool

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "label": self.label, "unit": self.unit, "required": self.required}


# Ordem e nomes da tabela obrigatória (por 100 g/ml e por porção) + campos da porção.
RDC429_FIELDS: Final[tuple[Rdc429Field, ...]] = (
    Rdc429Field("serving_size", "Porção", "g ou ml", True),
    Rdc429Field("household_measure", "Medida caseira", "texto", True),
    Rdc429Field("servings_per_package", "Porções por embalagem", "n", False),
    Rdc429Field("energy_kcal", "Valor energético", "kcal", True),
    Rdc429Field("energy_kj", "Valor energético", "kJ", False),
    Rdc429Field("carbohydrates_g", "Carboidratos", "g", True),
    Rdc429Field("total_sugars_g", "Açúcares totais", "g", True),
    Rdc429Field("added_sugars_g", "Açúcares adicionados", "g", True),
    Rdc429Field("protein_g", "Proteínas", "g", True),
    Rdc429Field("total_fat_g", "Gorduras totais", "g", True),
    Rdc429Field("saturated_fat_g", "Gorduras saturadas", "g", True),
    Rdc429Field("trans_fat_g", "Gorduras trans", "g", True),
    Rdc429Field("fiber_g", "Fibras alimentares", "g", True),
    Rdc429Field("sodium_mg", "Sódio", "mg", True),
)


def labels_by_code(mapping: Mapping[Any, str]) -> dict[str, str]:
    """`{Enum: rótulo}` -> `{código: rótulo}` para exportar (chave sem `.value`,
    como o nível da escala de fome, vira texto)."""
    return {str(getattr(chave, "value", chave)): rotulo for chave, rotulo in mapping.items()}


# ---------------------------------------------------------------------------
# Plano do aluno e limitações (Lote L2b)
# ---------------------------------------------------------------------------

PLAN_STATE_LABELS: Final[Mapping[PlanState, str]] = {
    PlanState.DRAFT: "Rascunho",
    PlanState.CURRENT: "Vigente",
    PlanState.SUPERSEDED: "Substituído",
}

# Check-in semanal (N7): veredito e estado da proposta, com o texto que a tela mostra.
CHECKIN_VERDICT_LABELS: Final[Mapping[CheckinVerdict, str]] = {
    CheckinVerdict.ON_TRACK: "No ritmo",
    CheckinVerdict.SLOW: "Abaixo do ritmo esperado",
    CheckinVerdict.FAST: "Acima do ritmo esperado",
    CheckinVerdict.ABOVE_BAND: "Peso acima da banda de manutenção",
    CheckinVerdict.BELOW_BAND: "Peso abaixo da banda de manutenção",
    CheckinVerdict.INCONCLUSIVE: "Inconclusivo",
    CheckinVerdict.GOAL_REACHED: "Objetivo do plano atingido",
}

CHECKIN_STATUS_LABELS: Final[Mapping[CheckinStatus, str]] = {
    CheckinStatus.PROPOSED: "Aguardando sua resposta",
    CheckinStatus.ACCEPTED: "Aceito",
    CheckinStatus.DECLINED: "Recusado",
    CheckinStatus.EXPIRED: "Expirado",
    CheckinStatus.INFORMATIVE: "Sem mudança de metas",
}

# O que as metas "antes e depois" da proposta representam (`weekly_checkin.SCOPE_*`).
CHECKIN_SCOPE_LABELS: Final[Mapping[str, str]] = {
    wc_mod.SCOPE_REST_DAY: "Dia sem treino",
    wc_mod.SCOPE_CYCLE_AVERAGE: f"Média dos {DAYS_PER_WEEK} dias do ciclo",
}

# Campos do plano, para os códigos `<prefixo>:<campo>` (faixa, código inválido,
# campo de outra estratégia).
PLAN_FIELD_LABELS: Final[Mapping[str, str]] = {
    "strategy": "Estratégia",
    ps_mod.FIELD_ADVANCED: "Nível avançado",
    "protein_g_per_kg": "Proteína (g/kg)",
    "fat_kcal_share": "Gordura (% das kcal)",
    "weekly_rate_share": "Ritmo de perda semanal",
    "surplus_share": "Superávit",
    "carb_min_g_per_kg": "Carboidrato mínimo (g/kg)",
    "meals_per_day": "Refeições por dia",
    "goal_body_fat_percent": "% de gordura alvo",
    "goal_weight_kg": "Peso-alvo",
    "bmr_formula": "Fórmula da TMB",
    "tdee_mode": "Gasto do treino",
    "activity_level": "Nível de atividade",
    manual_mod.FIELD_PROTEIN: "Proteína (g)",
    manual_mod.FIELD_CARB: "Carboidrato (g)",
    manual_mod.FIELD_FAT: "Gordura (g)",
    manual_mod.FIELD_KCAL: "Energia do dia (kcal)",
    plan_mod.FIELD_MACRO_PRIORITY: "Prioridade de macros",
    plan_mod.FIELD_MANUAL_TARGETS: "Gramas manuais",
    plan_mod.FIELD_MANUAL_TRAINING_MODE: "Gramas manuais e treino",
    "maintenance_kcal_adjust": "Ajuste da manutenção (kcal por dia)",
    "manual_macros": "Gramas manuais",
    "cycle_kinds": "Dias de mais e menos gasto",
}


def _percent(share: float) -> str:
    return f"{share:.0%}"


def _decimal(value: float) -> str:
    """Número com vírgula decimal (pt-BR), sem zeros à direita."""
    return f"{value:g}".replace(".", ",")


# Código de limitação/clamp/erro -> texto exibido. Número no texto vem do dono.
LIMITATION_LABELS: Final[Mapping[str, str]] = {
    # Dado do sujeito (o 422 do preview lista estes).
    bmr_mod.LIMIT_MISSING_SEX: "Sexo não informado no perfil.",
    bmr_mod.LIMIT_SEX_OTHER_REQUIRES_FFM: "Sexo \"Outro\" exige a massa livre de gordura (bioimpedância) para a TMB.",
    bmr_mod.LIMIT_MISSING_HEIGHT: "Altura ausente ou fora da faixa no perfil.",
    bmr_mod.LIMIT_MISSING_WEIGHT: "Nenhum peso registrado até o dia.",
    bmr_mod.LIMIT_MISSING_AGE: "Data de nascimento ausente no perfil.",
    bmr_mod.LIMIT_MISSING_FFM: "Massa livre de gordura indisponível (bioimpedância ou %G).",
    bmr_mod.LIMIT_FFM_STALE: f"Massa livre de gordura com mais de {bmr_mod.FFM_FRESHNESS_MAX_DAYS} dias.",
    ee_mod.LIMIT_MISSING_ACTIVITY_LEVEL: "Nível de atividade não informado (perfil ou plano).",
    strategy_mod.LIMIT_MISSING_FAT_MASS: (
        "Massa gorda indisponível: o cutting precisa de %G (bioimpedância ou pescoço e cintura na fita)."
    ),
    macros_mod.LIMIT_MISSING_PROTEIN_BASIS: "Sem a base da proteína (peso ou massa livre de gordura).",
    # Derivadas.
    ee_mod.LIMIT_MISSING_BMR: "TMB não calculável.",
    strategy_mod.LIMIT_MISSING_TDEE: "Gasto total (TDEE) não calculável.",
    strategy_mod.LIMIT_MISSING_BMR_FOR_FLOOR: "Sem TMB para aplicar o piso de ingestão do cutting.",
    macros_mod.LIMIT_MISSING_KCAL: "Alvo calórico não calculável.",
    macros_mod.LIMIT_KCAL_BELOW_PROTEIN_AND_FAT: "As kcal do dia não cobrem a proteína e a gordura: carboidrato zerado.",
    macros_mod.LIMIT_CARB_BELOW_MIN: "Carboidrato abaixo do mínimo do bulking.",
    # Treino do dia.
    ee_mod.LIMIT_NO_TRAINING_KCAL: "Dia de treino sem kcal nem duração: o treino não entrou no gasto.",
    dt_mod.LIMIT_TRAINING_UNKNOWN_FUTURE_DAY: "Dia futuro: o treino ainda não aconteceu e não entrou no gasto.",
    # Ciclo de carbo (L8).
    dt_mod.LIMIT_WEEK_FLAGS_REQUIRED_FOR_CYCLE: "O ciclo de carbo precisa dos dias de treino da semana.",
    cc_mod.LIMIT_WEEK_NOT_SEVEN_DAYS: "A semana do ciclo de carbo precisa ter sete dias.",
    cc_mod.LIMIT_CARB_FLOOR_PREVENTS_CYCLE: "O piso de carboidrato impede o ciclo nesta semana.",
    cc_mod.LIMIT_TARGET_NOT_COMPUTABLE: "Meta não calculável.",
    # %G.
    bc_mod.LIMIT_BODY_FAT_MISSING: "Sem % de gordura (bioimpedância ou fita).",
    cc_mod.ERR_INVALID_DAY_MAP: "Escolha de dias do ciclo de carbo inválida: esperado um mapa dia da semana → tipo.",
    cc_mod.ERR_CYCLE_ALL_DAYS_SAME_EXTREME: (
        "Com os sete dias altos (ou os sete baixos) não há ciclo: marque ao menos um dia de outro tipo."
    ),
    # Prioridade de macros (N1).
    macros_mod.ERR_INVALID_MACRO_PRIORITY: "Prioridade de macros inválida.",
    # Projeção até o alvo (N1).
    phases_mod.REASON_MISSING_WEIGHT: "Sem peso atual para projetar.",
    phases_mod.REASON_MISSING_BODY_FAT: "Sem % de gordura atual válido para projetar o alvo de gordura.",
    phases_mod.REASON_MISSING_TARGET: "Defina o peso-alvo ou o % de gordura alvo.",
    phases_mod.REASON_TWO_TARGETS: "Defina só um alvo: peso ou % de gordura.",
    phases_mod.REASON_INVALID_TARGET: "Alvo fora da faixa plausível.",
    phases_mod.REASON_MISSING_RATE: "Sem ritmo semanal válido (dentro da faixa da estratégia) para projetar.",
    phases_mod.REASON_MISSING_TARGET_WEIGHT: "Não foi possível calcular o peso em que o alvo de gordura se cumpre.",
    phases_mod.REASON_BEYOND_HORIZON: "Nesse ritmo o alvo fica além do horizonte da projeção.",
    phases_mod.REASON_START_WRONG_SIDE: "O ponto de partida está do lado errado do alvo: o percentual do caminho não se aplica.",
    phases_mod.REASON_RATE_OUTSIDE_RANGE: (
        "O ritmo que o plano implica está fora da faixa recomendada da estratégia; a projeção usa o ritmo do plano."
    ),
    phases_mod.REASON_LEAN_MASS_CONSTANT: (
        "Estimativa mínima: supõe a massa livre de gordura atual constante; o prazo real tende a ser maior."
    ),
    phases_mod.REASON_MISSING_START: "Sem o ponto de partida: o percentual do caminho não pode ser calculado.",
    phases_mod.REASON_TARGET_AGAINST_STRATEGY: "O alvo está no sentido contrário ao da estratégia.",
    phases_mod.REASON_TARGET_MOVES_WITHOUT_STRATEGY: "A manutenção não move o peso: o alvo está distante do atual.",
    # Clamps do cutting (decisão D-B).
    strategy_mod.CLAMP_MAX_DEFICIT_SHARE: (
        f"Déficit limitado a {_percent(strategy_mod.CUT_MAX_DEFICIT_SHARE)} do gasto total."
    ),
    strategy_mod.CLAMP_MIN_INTAKE_BMR_MULTIPLE: "Ingestão elevada ao piso do cutting (TMB).",
    strategy_mod.CLAMP_MIN_KCAL_BLOCK: "Ingestão elevada ao piso de energia (TMB).",
    # Entrada do plano.
    plan_mod.NOTE_TEMPLATE_NOT_ASSIGNED: "Você não tem Modelo de treino atribuído: marque os dias à mão.",
    plan_mod.NOTE_TEMPLATE_WITHOUT_WEEKDAYS: (
        "O Modelo não define os dias da semana dos treinos: marque os dias à mão."
    ),
    plan_mod.NOTE_TEMPLATE_NO_CYCLE: (
        "O Modelo treina todos os dias (ou nenhum): não há dia de mais ou menos gasto para marcar."
    ),
    dt_mod.LIMIT_MANUAL_TARGETS_IGNORE_PHASE: (
        "Pausa ou refeed: as gramas manuais continuam as mesmas (a fase não as altera)."
    ),
    plan_mod.ERR_CYCLE_KCAL_BELOW_MIN: (
        f"Num dia do ciclo a energia fica abaixo do mínimo seguro ({manual_mod.KCAL_BLOCK_MIN:.0f} kcal). Não pode ser salvo."
    ),
    plan_mod.ERR_CYCLE_KCAL_ABOVE_MAX: (
        f"Num dia do ciclo a energia passa do máximo aceito ({manual_mod.KCAL_BLOCK_MAX:.0f} kcal). Não pode ser salvo."
    ),
    plan_mod.WARN_CYCLE_KCAL_BELOW_BMR: "Num dia do ciclo a energia fica abaixo da taxa metabólica basal. Salva, mas confira.",
    plan_mod.WARN_CYCLE_PARTIAL_WEEK: (
        "Publicando fora da segunda-feira, os dias que já passaram não entram e não há compensação: a média da "
        "semana só fecha em semana completa do mesmo plano."
    ),
    plan_mod.REASON_MANUAL_AGAINST_STRATEGY: (
        "As gramas manuais vão contra a estratégia (superávit no cutting ou déficit no bulking): sem projeção."
    ),
    plan_mod.REASON_MANUAL_MISSING_FAT_MASS: (
        "Sem massa gorda (% de gordura) não dá para derivar o ritmo das gramas manuais: registre um % de gordura."
    ),
    plan_mod.REASON_TDEE_WITHOUT_TRAINING: (
        "Sem treino registrado nos últimos dias, o gasto usado na projeção é o da base sem treino."
    ),
    plan_mod.REASON_MANUAL_MISSING_TDEE: "Sem o gasto total (TDEE) não dá para derivar o ritmo das gramas manuais.",
    plan_mod.NOTE_TEMPLATE_PARTIAL_WEEKDAYS: (
        "Só alguns slots do Modelo têm dia da semana: marque os dias à mão."
    ),
    plan_mod.ERR_DRAFT_LIMIT: "Você atingiu o limite de rascunhos: apague um para criar outro.",
    plan_mod.ERR_MANUAL_REQUIRES_WEIGHT: "Para salvar gramas manuais, registre seu peso: sem ele o motor não confere as faixas em g/kg.",
    # Janelas de recálculo.
    dt_mod.ERR_DATE_FROM_FROZEN: (
        f"Dias com mais de {dt_mod.TARGET_FREEZE_LAG_DAYS - 1} dia(s) no passado já estão congelados e não são recalculados."
    ),
    dt_mod.ERR_DATE_TO_BEFORE_DATE_FROM: "A data final vem antes da inicial.",
    dt_mod.ERR_RANGE_TOO_LONG: f"Intervalo maior que {dt_mod.TARGET_RECOMPUTE_MAX_DAYS} dias.",
    # Catálogo de alimentos (L3a).
    catalog_mod.ERR_INVALID_BASIS: (
        f"Base da tabela inválida: use {' ou '.join(base.value for base in catalog_mod.FOOD_BASES)}."
    ),
    catalog_mod.ERR_INVALID_NAME: "Informe o nome do alimento.",
    catalog_mod.ERR_INVALID_SOURCE: "Fonte de alimento desconhecida.",
    catalog_mod.ERR_INVALID_PORTION_AMOUNT: (
        f"A porção precisa ser maior que zero e até {catalog_mod.PORTION_AMOUNT_MAX:g} na base do alimento (g ou ml)."
    ),
    catalog_mod.ERR_SOURCE_READ_ONLY: (
        "Alimento de tabela publicada ou de base externa: não é editável. Cadastre um alimento novo com os "
        "valores que quiser."
    ),
    # Fontes externas (L3b).
    ext_mod.ERR_INVALID_BARCODE: (
        f"Código de barras inválido: informe os {', '.join(str(n) for n in ext_mod.GTIN_LENGTHS[:-1])} ou "
        f"{ext_mod.GTIN_LENGTHS[-1]} dígitos do EAN/GTIN, com o dígito verificador."
    ),
    ext_mod.ERR_OFF_DAILY_LIMIT: (
        f"Limite de {ext_mod.OFF_LOOKUPS_PER_USER_DAY} consultas novas de código de barras por dia atingido. "
        "Produtos já consultados continuam disponíveis; tente de novo amanhã."
    ),
    # Uso de IA (L5a): o 429 de cada feature leva o número (`ai_quota_exceeded_label`).
    ai_usage_mod.ERR_AI_QUOTA_EXCEEDED: "Limite diário de leituras por IA atingido. Tente de novo amanhã.",
    # Foto do rótulo (L6, `label_photo`): upload, worker e revisão da leitura.
    lp_mod.ERR_LABEL_PHOTO_EMPTY: "A foto chegou vazia. Escolha a foto de novo.",
    lp_mod.ERR_LABEL_PHOTO_TOO_LARGE: (
        f"Foto maior que {_decimal(lp_mod.LABEL_PHOTO_MAX_MIB)} MB mesmo depois de reduzida. Tire outra foto, "
        "mais perto da tabela."
    ),
    lp_mod.ERR_LABEL_PHOTO_UNSUPPORTED_TYPE: "Formato de imagem não aceito: a tela envia a foto reduzida em JPEG.",
    lp_mod.ERR_LABEL_PHOTO_INVALID_ENCODING: "A foto chegou corrompida. Escolha a foto de novo.",
    lp_mod.ERR_LABEL_PHOTO_STORAGE_FAILED: "Não foi possível guardar a foto agora. Tente de novo em instantes.",
    lp_mod.ERR_LABEL_PHOTO_DISPATCH_FAILED: (
        "Não foi possível iniciar a leitura do rótulo agora. Tente de novo em instantes."
    ),
    lp_mod.ERR_LABEL_PHOTO_KEY_INVALID: "Foto do rótulo inválida.",
    lp_mod.ERR_LABEL_PHOTO_NOT_FOUND: (
        f"A foto não está mais disponível (as fotos de rótulo ficam guardadas por {lp_mod.LABEL_PHOTO_RETENTION_DAYS} "
        "dias). Envie de novo."
    ),
    lp_mod.ERR_LABEL_READING_INVALID: "A IA devolveu a leitura do rótulo fora do formato. Tente de novo.",
    lp_mod.ERR_LABEL_UNREADABLE: (
        "Não deu para ler a tabela nutricional nesta foto. Tire outra de frente, com a tabela inteira, nítida e "
        "bem iluminada."
    ),
    lp_mod.WARN_LABEL_PARTIAL: "Parte da tabela não ficou legível: confira os campos vazios antes de salvar.",
    lp_mod.WARN_LABEL_BASIS_ASSUMED: (
        f"A foto não mostra se a tabela é {FOOD_BASIS_LABELS[catalog_mod.FOOD_BASES[0]]} ou "
        f"{FOOD_BASIS_LABELS[catalog_mod.FOOD_BASES[1]]}: considerada "
        f"{FOOD_BASIS_LABELS[catalog_mod.DEFAULT_FOOD_BASIS]}. Confira."
    ),
    lp_mod.WARN_LABEL_ENERGY_FROM_KJ: "Valor energético lido só em kJ e convertido para kcal.",
    lp_mod.WARN_LABEL_NAME_MISSING: "O nome do produto não foi lido: digite o nome antes de salvar.",
    # Sugestão de metas por IA (N6, `plan_suggestion`).
    ps_mod.ERR_SUGGESTION_SHAPE: "A resposta da IA não veio na forma esperada.",
    ps_mod.ERR_SUGGESTION_REASON_INVALID: "A resposta da IA trouxe um motivo fora da lista.",
    ps_mod.ERR_SUGGESTION_STOP_REACHED: "A estratégia sugerida já atingiu o % de gordura de parada.",
    ps_mod.ERR_SUGGESTION_NOT_COMPUTABLE: "O motor não calcula a meta desta estratégia com os dados atuais do perfil.",
    ps_mod.ERR_SUGGESTION_TIMEOUT: "A IA demorou demais para responder. Nada mudou no formulário. Tente de novo em instantes.",
    ps_mod.ERR_SUGGESTION_REFUSED: (
        "A sugestão da IA ficou fora das regras do motor e foi descartada. Nada mudou no formulário. Tente de novo."
    ),
    ps_mod.ERR_SUGGESTION_FAILED: "Não foi possível gerar a sugestão agora. Tente de novo em instantes.",
    ps_mod.ERR_SUGGESTION_DISPATCH_FAILED: "Não foi possível iniciar a sugestão agora. Tente de novo em instantes.",
    # Foto do prato (L10, `meal_photo`): upload, worker e revisão da leitura.
    mp_mod.ERR_MEAL_PHOTO_EMPTY: "A foto chegou vazia. Escolha a foto de novo.",
    mp_mod.ERR_MEAL_PHOTO_TOO_LARGE: (
        f"Foto maior que {_decimal(lp_mod.LABEL_PHOTO_MAX_MIB)} MB mesmo depois de reduzida. Tire outra foto."
    ),
    mp_mod.ERR_MEAL_PHOTO_UNSUPPORTED_TYPE: "Formato de imagem não aceito: a tela envia a foto reduzida em JPEG.",
    mp_mod.ERR_MEAL_PHOTO_INVALID_ENCODING: "A foto chegou corrompida. Escolha a foto de novo.",
    mp_mod.ERR_MEAL_PHOTO_STORAGE_FAILED: "Não foi possível guardar a foto agora. Tente de novo em instantes.",
    mp_mod.ERR_MEAL_PHOTO_DISPATCH_FAILED: "Não foi possível iniciar a análise da foto agora. Tente de novo em instantes.",
    mp_mod.ERR_MEAL_PHOTO_KEY_INVALID: "Foto do prato inválida.",
    mp_mod.ERR_MEAL_PHOTO_NOT_FOUND: (
        f"A foto não está mais disponível (as fotos do prato expiram depois de {days_label(mp_mod.MEAL_PHOTO_RETENTION_DAYS)}). "
        "Envie de novo."
    ),
    mp_mod.ERR_MEAL_READING_INVALID: "A IA devolveu a análise da foto fora do formato. Tente de novo.",
    mp_mod.ERR_MEAL_READING_UNEXPECTED: "Não foi possível analisar a foto agora. Tente novamente.",
    mp_mod.LIMIT_PHOTO_NO_FOOD: "Não encontramos comida nesta foto. Tire outra foto do prato ou registre a refeição à mão.",
    mp_mod.LIMIT_PHOTO_UNCLEAR: (
        "Não deu para identificar os alimentos nesta foto. Tire outra, de cima, com o prato inteiro e bem iluminado, "
        "ou registre a refeição à mão."
    ),
    mp_mod.LIMIT_GRAMS_OUT_OF_RANGE: "O peso estimado pela IA ficou fora do limite do diário: informe a quantidade.",
    # Leitura da refeição por IA (L5b, `meal_parse`).
    meal_parse_mod.ERR_TEXT_EMPTY: "Descreva a refeição.",
    meal_parse_mod.ERR_TEXT_TOO_LONG: f"Descreva a refeição em até {meal_parse_mod.TEXT_MAX_CHARS} caracteres.",
    meal_parse_mod.LIMIT_AI_ESTIMATE: (
        "Alimento fora do catálogo: valores estimados pela IA. Cadastre o alimento para usar valores de tabela."
    ),
    meal_parse_mod.LIMIT_ESTIMATE_INVALID: (
        "A estimativa da IA para este alimento não passou nas regras do catálogo e foi descartada."
    ),
    meal_parse_mod.LIMIT_QUANTITY_FROM_GRAMS: (
        "Quantidade em gramas estimada pela IA a partir da medida descrita (unidade, colher, xícara ou ml). Confira."
    ),
    meal_parse_mod.LIMIT_QUANTITY_MISSING: "A IA não identificou a quantidade deste alimento: informe.",
    meal_parse_mod.LIMIT_ITEMS_TRUNCATED: (
        f"A descrição tinha mais de {diary_mod.MEAL_MAX_ITEMS} alimentos: só os primeiros foram lidos."
    ),
    meal_parse_mod.LIMIT_NO_ITEMS: "A IA não encontrou alimentos na descrição.",
    meal_parse_mod.ERR_PARSE_FAILED: "Não foi possível ler a refeição agora. Tente novamente.",
    meal_parse_mod.ERR_MATCH_FAILED: "A IA leu a refeição, mas a busca no catálogo falhou. Tente novamente.",
    meal_parse_mod.ERR_DISPATCH_FAILED: "Não foi possível iniciar a leitura da refeição. Tente novamente em instantes.",
    diary_mod.ERR_ITEM_FOOD_OR_ESTIMATE: (
        "Cada item precisa de uma origem só: um alimento do catálogo, a estimativa da IA ou o lançamento rápido."
    ),
    diary_mod.ERR_QUICK_ENTRY_WEIGHT: (
        f"O peso precisa ser maior que zero e até {diary_mod.ITEM_AMOUNT_MAX:g} g."
    ),
    diary_mod.ERR_INVALID_CLOCK_TIME: "Hora inválida: use o formato HH:MM (00:00 a 23:59).",
    catalog_mod.ERR_INVALID_TABLE_BASE: (
        f"A base da tabela precisa ser maior que zero e até {catalog_mod.TABLE_BASE_AMOUNT_MAX:g} (g ou ml)."
    ),
    ext_mod.ERR_USDA_DATA_TYPE: (
        "Só alimentos "
        + " e ".join(USDA_DATA_TYPE_LABELS[tipo] for tipo in ext_mod.USDA_DATA_TYPES)
        + " do USDA são importados."
    ),
    catalog_mod.WARN_ATWATER: (
        f"As kcal informadas diferem mais de {_percent(meal_math_mod.ATWATER_TOLERANCE_SHARE)} "
        "das kcal calculadas pelos macros (Atwater). Confira os valores."
    ),
    catalog_mod.WARN_MACROS_ABOVE_BASIS: "Proteína, carboidrato e gordura somados passam da base do alimento.",
    # Diário (L4): quantidade do item, macro ausente e totais.
    meal_math_mod.LIMIT_MISSING_QUANTITY: "Quantidade ausente ou inválida (precisa ser maior que zero).",
    meal_math_mod.LIMIT_UNKNOWN_UNIT: "Unidade desconhecida.",
    meal_math_mod.LIMIT_MISSING_PORTION_G: "Porção sem quantidade: escolha uma porção cadastrada do alimento ou use g/ml.",
    meal_math_mod.LIMIT_UNKNOWN_BASIS: "Base da tabela do alimento desconhecida.",
    meal_math_mod.LIMIT_ASSUMED_DENSITY: (
        f"Sem densidade do alimento: g e ml convertidos a {meal_math_mod.DEFAULT_DENSITY_G_PER_ML:g} g por ml (água)."
    ),
    meal_math_mod.LIMIT_FIBER_INFERRED_ZERO: (
        "Fibra ausente na tabela, considerada zero porque o carboidrato do alimento é zero."
    ),
    diary_mod.ERR_INVALID_MEAL_SLOT: "Refeição inválida.",
    diary_mod.ERR_MEAL_WITHOUT_ITEMS: "A refeição precisa de pelo menos um alimento.",
    diary_mod.ERR_TOO_MANY_ITEMS: f"A refeição aceita até {diary_mod.MEAL_MAX_ITEMS} alimentos.",
    diary_mod.ERR_UNIT_WITHOUT_CONVERSION: (
        "Unidade sem conversão para este alimento: use g, ml, uma medida caseira ou uma porção cadastrada."
    ),
    diary_mod.ERR_ITEM_AMOUNT_OUT_OF_RANGE: (
        f"Quantidade acima de {diary_mod.ITEM_AMOUNT_MAX:g} g ou ml num alimento só."
    ),
    # Alimentos preferidos (N3, `favorites`).
    fav_mod.ERR_TOO_MANY_FAVORITES: f"A lista de preferidos aceita até {fav_mod.MAX_FAVORITES} alimentos.",
    fav_mod.ERR_DEFAULT_INCOMPLETE: "Porção padrão: informe a quantidade e a unidade juntas, ou deixe as duas vazias.",
    fav_mod.ERR_DEFAULT_PORTION_UNIT: "Porção cadastrada só vale com a unidade porção ou unidade.",
    fav_mod.ERR_INVALID_ORDER: "A nova ordem precisa conter exatamente os alimentos da lista, cada um uma vez.",
    # Semana e balanço do dia (L4, `weekly_budget`).
    wb_mod.LIMIT_NO_TARGETS: "Nenhum dia da semana tem meta calculada.",
    wb_mod.LIMIT_NO_LOGGED_DAYS: "Nenhum dia da semana com refeições de total completo.",
    wb_mod.LIMIT_INCOMPLETE_INTAKE_DAYS: (
        "Dia com refeição de total incompleto (alimento sem energia ou macro na tabela) fica fora do banco e da aderência."
    ),
    wb_mod.LIMIT_NO_DAY_TARGET: "Sem meta para o dia (sem plano publicado ou com dados do aluno faltando).",
    wb_mod.LIMIT_NO_INTAKE: "Nenhuma refeição registrada no dia.",
    wb_mod.CLAMP_DAILY_CARRYOVER: (
        f"Transferência para o dia seguinte limitada a {_percent(wb_mod.MAX_DAILY_CARRYOVER_SHARE)} do alvo diário."
    ),
    wb_mod.CLAMP_WEEKLY_BANK: f"Banco da semana limitado a {_percent(wb_mod.MAX_WEEKLY_BANK_SHARE)} do alvo semanal.",
    # Dashboard de nutrição (N5, `dashboard`) e gasto observado (`adaptive_tdee`).
    dash_mod.EMPTY_NO_PLAN: "Sem plano de nutrição publicado: não há meta para comparar com o que você comeu.",
    dash_mod.EMPTY_NO_GOAL: "O plano não tem peso-alvo nem % de gordura alvo: sem projeção de chegada.",
    dash_mod.EMPTY_NO_WEIGHT: (
        "Nenhuma pesagem de balança ou bioimpedância (Indicadores físicos) no período. Este painel só lê essas "
        "pesagens: peso que está só no relógio ou no Intervals.icu não aparece aqui."
    ),
    dash_mod.EMPTY_NO_BIOIMPEDANCE: "Nenhuma bioimpedância no período: sem % de gordura nem massa muscular.",
    dash_mod.EMPTY_FEW_LOGGED_DAYS: (
        f"Menos de {dash_mod.FEW_LOGGED_DAYS} dias fechados com refeições de total completo desde o início do plano "
        "(dentro do período): a aderência ainda diz pouco."
    ),
    dash_mod.EMPTY_DAYS_WITHOUT_TARGET: (
        "Há dia(s) com refeição registrada e sem meta calculada (a meta nasce ao abrir o diário do dia): "
        "ficam fora da aderência."
    ),
    atdee_mod.LIMIT_WINDOW_TOO_SHORT: (
        f"Período curto para o gasto observado (mínimo de {atdee_mod.MIN_WINDOW_DAYS} dias)."
    ),
    atdee_mod.LIMIT_TOO_FEW_LOGGED_DAYS: (
        f"Menos de {_percent(atdee_mod.MIN_LOGGED_DAYS_SHARE)} dos dias com refeições de total completo."
    ),
    atdee_mod.LIMIT_TOO_FEW_WEIGHTS: "Pesagens insuficientes para a tendência do peso.",
    atdee_mod.LIMIT_WEIGHT_SPAN_TOO_SHORT: (
        f"As pesagens cobrem menos de {atdee_mod.MIN_WEIGHT_SPAN_DAYS} dias entre a primeira e a última."
    ),
    atdee_mod.LIMIT_MISSING_FAT_MASS: "Sem massa gorda estimada para converter a variação do peso em energia.",
    atdee_mod.LIMIT_FORMULA_TDEE_MISSING: "Sem gasto calculado pela fórmula para comparar com o observado.",
    # Check-in semanal (N7, `weekly_checkin`): o que faltou e por que o motor decidiu assim.
    wc_mod.MISSING_FEW_COMPLETE_DAYS: (
        f"Menos de {wc_mod.MIN_COMPLETE_DAYS} dias da semana com refeições de total completo."
    ),
    wc_mod.NONE_NO_PLAN: "Sem plano publicado: o check-in semanal começa depois que você publicar um plano.",
    wc_mod.NONE_MIGRATION_PENDING: "O check-in semanal ainda não está disponível neste ambiente (atualização em andamento).",
    wc_mod.MISSING_PLAN_CHANGED_IN_WEEK: (
        f"O plano mudou durante a semana avaliada: só há menos de {wc_mod.MIN_DAYS_UNDER_CURRENT_PLAN} dias sob o plano "
        "atual para julgá-lo. O próximo check-in avalia a semana inteira com ele."
    ),
    wc_mod.NONE_PLAN_AFTER_WEEK: "Seu plano começou depois da última semana fechada: o primeiro check-in vem na próxima semana.",
    wc_mod.MISSING_NO_TARGETS: "Nenhum dia da semana com meta calculada.",
    wc_mod.MISSING_FEW_WEIGHINGS: (
        f"Pesagens insuficientes: a tendência pede ao menos {wc_mod.MIN_WEIGHT_READINGS_FOR_TREND} pesagens "
        f"nos últimos {wc_mod.WEIGHT_WINDOW_DAYS} dias."
    ),
    wc_mod.MISSING_NO_WEIGHING_IN_WEEK: "Nenhuma pesagem dentro da semana avaliada.",
    wc_mod.MISSING_WEIGHT_SPAN_SHORT: (
        f"As pesagens cobrem menos de {atdee_mod.MIN_WEIGHT_SPAN_DAYS} dias: falta tempo entre a primeira e a última."
    ),
    wc_mod.MISSING_FAT_MASS: "Sem massa gorda estimada para converter a variação do peso em energia.",
    wc_mod.MISSING_SUBJECT: "Faltam dados do perfil para avaliar a semana.",
    wc_mod.REASON_ADHERENCE_OFF: (
        f"A ingestão da semana ficou fora de {_percent(wb_mod.ADHERENCE_KCAL_TOLERANCE_SHARE)} da meta: baixa aderência "
        "não vira corte de calorias, então as metas ficam como estão."
    ),
    wc_mod.REASON_ADJUSTMENT_BELOW_MIN: (
        f"A correção calculada é menor que {wc_mod.MIN_KCAL_STEP:.0f} kcal por dia, dentro do ruído: nada muda."
    ),
    wc_mod.REASON_AT_STRATEGY_LIMIT: "O plano já está no limite da faixa da estratégia: não há como ajustar mais.",
    wc_mod.REASON_BLOCKED_BY_CYCLE: "O ajuste faria algum dia do ciclo cair fora dos pisos de energia.",
    wc_mod.REASON_BLOCKED_BY_MANUAL: "O ajuste violaria a checagem das gramas manuais.",
    wc_mod.REASON_PROPOSAL_INVALID: "Não foi possível montar metas novas válidas para o plano.",
    wc_mod.REASON_BIA_SHORT_TREND: (
        f"A bioimpedância tem menos de {wc_mod.MIN_BIA_SPAN_DAYS // DAYS_PER_WEEK} semanas de tendência: ruidosa, foi "
        "ignorada neste ajuste."
    ),
    wc_mod.REASON_LEAN_LOSS: (
        "A massa magra estimada pela bioimpedância caiu acima do limite nas últimas semanas: o déficit é reduzido."
    ),
    wc_mod.REASON_FAT_GAIN: (
        "A gordura estimada pela bioimpedância subiu acima do limite nas últimas semanas: o superávit é reduzido."
    ),
    wc_mod.REASON_BIA_HOLD: "O ritmo está lento, mas o corpo sinaliza perda de massa magra ou ganho de gordura: sem ajuste.",
    wc_mod.REASON_GOAL_WEIGHT_REACHED: "O peso-alvo do plano foi atingido: revise o plano.",
    wc_mod.REASON_GOAL_BODY_FAT_REACHED: "O % de gordura de parada foi atingido: revise o plano.",
    # Meta lida pelo diário (L4, `daily_target.diary_target_action`).
    dt_mod.LIMIT_TARGET_BACKFILLED: (
        "Meta calculada depois que o dia congelou (ninguém abriu o diário a tempo), com os dados disponíveis no dia "
        "em que o diário foi aberto."
    ),
    dt_mod.LIMIT_TARGET_FUTURE_BEYOND_WEEK: "Dia além da semana atual: a meta é calculada quando a semana chegar.",
}

# Avisos exibidos pelas telas (o FE lê do bloco `nutrition.notices`).
NOTICE_SCALE_BMR_DEVIATION = "scale_bmr_deviation"
NOTICE_PHASE_SHOULD_STOP = "phase_should_stop"
# Diário (L4): selos e dicas das telas com o MESMO texto das limitações do motor.
NOTICE_TARGET_FROZEN = "target_frozen"
NOTICE_TARGET_BACKFILLED = dt_mod.LIMIT_TARGET_BACKFILLED
NOTICE_INCOMPLETE_INTAKE_DAYS = wb_mod.LIMIT_INCOMPLETE_INTAKE_DAYS
NOTICE_TRAINING_UNKNOWN_FUTURE_DAY = dt_mod.LIMIT_TRAINING_UNKNOWN_FUTURE_DAY

NOTICE_LABELS: Final[Mapping[str, str]] = {
    NOTICE_SCALE_BMR_DEVIATION: (
        f"A TMB da balança difere mais de {_percent(bmr_mod.SCALE_BMR_DEVIATION_WARN_SHARE)} da calculada pela fórmula."
    ),
    NOTICE_PHASE_SHOULD_STOP: "O % de gordura atual já chegou à meta desta fase.",
    NOTICE_TARGET_FROZEN: (
        f"Meta congelada: {dt_mod.TARGET_FREEZE_LAG_DAYS} dia(s) depois do dia ela não muda mais."
    ),
    NOTICE_TARGET_BACKFILLED: LIMITATION_LABELS[NOTICE_TARGET_BACKFILLED],
    NOTICE_INCOMPLETE_INTAKE_DAYS: LIMITATION_LABELS[NOTICE_INCOMPLETE_INTAKE_DAYS],
    NOTICE_TRAINING_UNKNOWN_FUTURE_DAY: LIMITATION_LABELS[NOTICE_TRAINING_UNKNOWN_FUTURE_DAY],
}

# Códigos com campo (`<prefixo>:<campo>`): o texto recebe o rótulo do campo.
PREFIXED_LIMITATION_LABELS: Final[Mapping[str, str]] = {
    strategy_mod.LIMIT_PARAM_OUT_OF_RANGE: "{field}: fora da faixa da fonte.",
    plan_mod.ERR_INVALID_CODE: "{field}: código inválido.",
    # Gramas manuais (N1, D6): `<código>:<campo>`.
    manual_mod.MANUAL_INVALID_VALUE: "{field}: valor inválido (número maior ou igual a zero).",
    manual_mod.MANUAL_BELOW_RANGE: "{field}: abaixo da faixa recomendada. Salva, mas confira.",
    manual_mod.MANUAL_ABOVE_RANGE: "{field}: acima da faixa recomendada. Salva, mas confira.",
    manual_mod.MANUAL_BLOCKED_BELOW: "{field}: abaixo do mínimo seguro. Não pode ser salvo.",
    manual_mod.MANUAL_BLOCKED_ABOVE: "{field}: acima do máximo aceito. Não pode ser salvo.",
    manual_mod.MANUAL_BELOW_BMR: "{field}: abaixo da taxa metabólica basal. Salva, mas confira.",
    manual_mod.MANUAL_DIFFERS_FROM_TARGET: "{field}: distante da meta calórica da estratégia. Salva, mas confira.",
    manual_mod.LIMIT_MISSING_BASE: "{field}: sem peso ou massa livre de gordura, a faixa em g/kg não foi conferida.",
    # Dias do ciclo de carbo (N1, D5).
    cc_mod.ERR_INVALID_WEEKDAY: "Dia da semana inválido: {field}.",
    cc_mod.ERR_DUPLICATE_WEEKDAY: "Dia da semana repetido: {field}.",
    cc_mod.ERR_INVALID_DAY_KIND: "Tipo de dia inválido ({field}).",
    plan_mod.ERR_PARAM_NOT_APPLICABLE: "{field}: não se aplica a esta estratégia.",
    # Sugestão de metas por IA (N6): o que a revisão do motor recusou.
    ps_mod.ERR_SUGGESTION_FIELD_FORBIDDEN: "{field}: a IA não pode sugerir este campo.",
    ps_mod.ERR_SUGGESTION_FIELD_MISSING: "{field}: faltou na resposta da IA.",
    ps_mod.ERR_SUGGESTION_FIELD_INVALID: "{field}: valor inválido na resposta da IA.",
    bc_mod.LIMIT_BODY_FAT_OUT_OF_RANGE: "% de gordura fora da faixa plausível ({field}).",
    # Catálogo de alimentos (L3a): `<código>:<campo da RDC 429>`.
    catalog_mod.ERR_MISSING_FIELD: "{field}: obrigatório.",
    catalog_mod.ERR_INVALID_VALUE: "{field}: valor inválido (número maior ou igual a zero).",
    catalog_mod.ERR_ABOVE_BASIS: "{field}: acima do máximo possível para a base da tabela.",
    catalog_mod.WARN_SUBTOTAL_ABOVE_TOTAL: "{field}: maior que o total que o contém.",
    # Diário (L4): `<código>:<campo da RDC 429>` (regra do macro ausente, `meal_math`).
    meal_math_mod.LIMIT_MACRO_INFERRED_ZERO: (
        "{field}: ausente na tabela, considerado zero porque a energia do alimento já é explicada pelos outros "
        "macros (Atwater)."
    ),
    meal_math_mod.LIMIT_NUTRIENT_UNKNOWN: "{field}: a tabela não informa; o total fica incompleto.",
    meal_math_mod.LIMIT_TOTAL_INCOMPLETE: "{field}: total incompleto (há alimento sem esse valor na tabela).",
    # Foto do rótulo (L6): `<código>:<campo da RDC 429>` (revisão de `label_photo`).
    lp_mod.WARN_LABEL_DERIVED_FROM_SERVING: (
        "{field}: a coluna por 100 não foi lida; o valor saiu da coluna da porção. Confira."
    ),
    lp_mod.WARN_LABEL_NOT_SIGNIFICANT: (
        "{field}: a tabela declara que não há quantidade significativa; considerado zero. Confira."
    ),
    lp_mod.WARN_LABEL_SERVING_INCONSISTENT: (
        f"{{field}}: o valor por porção difere mais de {_percent(meal_math_mod.LABEL_SERVING_TOLERANCE_SHARE)} "
        "do valor por 100 aplicado à porção. Confira a leitura."
    ),
}

# Campo da RDC 429 com a unidade ("Valor energético (kcal)" × "(kJ)").
RDC429_FIELD_LABELS: Final[Mapping[str, str]] = {campo.code: f"{campo.label} ({campo.unit})" for campo in RDC429_FIELDS}

_FIELD_LABELS: Final[Mapping[str, str]] = {
    **PLAN_FIELD_LABELS,
    **{str(indice): nome for indice, nome in WEEKDAY_LABELS.items()},
    **labels_by_code(BODY_FAT_METHOD_LABELS),
    **RDC429_FIELD_LABELS,
    diary_mod.QUICK_ENTRY_WEIGHT_FIELD: "Peso (g)",
}


# Sugestão de metas por IA (N6): o único "texto" da IA que chega à tela é um destes rótulos.
PLAN_SUGGESTION_REASON_LABELS: Final[Mapping[ps_mod.SuggestionReason, str]] = {
    ps_mod.SuggestionReason.GOAL_WEIGHT_BELOW_CURRENT: "O peso-alvo está abaixo do peso atual.",
    ps_mod.SuggestionReason.GOAL_WEIGHT_ABOVE_CURRENT: "O peso-alvo está acima do peso atual.",
    ps_mod.SuggestionReason.BODY_FAT_ABOVE_GOAL: "O % de gordura está acima do alvo.",
    ps_mod.SuggestionReason.BODY_FAT_BELOW_GOAL: "O % de gordura está abaixo do alvo.",
    ps_mod.SuggestionReason.WEIGHT_TREND_DOWN: "O peso vem caindo nas últimas semanas.",
    ps_mod.SuggestionReason.WEIGHT_TREND_UP: "O peso vem subindo nas últimas semanas.",
    ps_mod.SuggestionReason.WEIGHT_TREND_STABLE: "O peso está estável nas últimas semanas.",
    ps_mod.SuggestionReason.HIGH_TRAINING_LOAD: "O gasto de treino recente é alto.",
    ps_mod.SuggestionReason.LOW_TRAINING_LOAD: "O gasto de treino recente é baixo.",
    ps_mod.SuggestionReason.PRESERVE_LEAN_MASS: "Prioriza preservar a massa magra.",
    ps_mod.SuggestionReason.CONSERVATIVE_PACE: "Ritmo conservador, mais fácil de sustentar.",
    ps_mod.SuggestionReason.LIMITED_DATA: "Há poucos dados recentes: confira com atenção.",
}
PLAN_SUGGESTION_NO_VALUE: Final[str] = "—"


def _decimal_text(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def plan_suggestion_value_label(field: str, value: Any) -> str:
    """Valor de um campo da sugestão como texto para a tela: a tela não converte nem formata regra."""
    if value is None:
        return PLAN_SUGGESTION_NO_VALUE
    if field == "strategy":
        return STRATEGY_LABELS[strategy_mod.Strategy(value)]
    if field == plan_mod.FIELD_MACRO_PRIORITY:
        return MACRO_PRIORITY_LABELS[strategy_mod.MacroPriority(value)]
    if field in (ps_mod.FIELD_ADVANCED, ps_mod.FIELD_MANUAL_ACTIVE):
        return "Sim" if value else "Não"
    if field == ps_mod.FIELD_MAINTENANCE_ADJUST:
        return f"{int(value):+d} kcal por dia" if value else "0 kcal por dia"
    if field.endswith("_share"):
        return f"{_decimal_text(round(float(value) * ps_mod.SHARE_TO_PERCENT, ps_mod.PERCENT_LABEL_DECIMALS))}%"
    if field.endswith("_g_per_kg"):
        return f"{_decimal_text(float(value))} g/kg"
    return _decimal_text(float(value))


def plan_suggestion_change_items(changes: Any) -> list[dict[str, Any]]:
    """"O que mudou" para a tela: rótulo do campo e os dois valores já em texto."""
    return [
        {
            "field": mudanca.field,
            "label": field_label(mudanca.field),
            "before_label": plan_suggestion_value_label(mudanca.field, mudanca.before),
            "after_label": plan_suggestion_value_label(mudanca.field, mudanca.after),
        }
        for mudanca in changes
    ]


def plan_suggestion_reason_items(reasons: Any) -> list[dict[str, str]]:
    return [{"code": motivo.value, "label": PLAN_SUGGESTION_REASON_LABELS[motivo]} for motivo in reasons]


def field_label(code: str) -> str:
    """Rótulo pt-BR de um campo (`protein_g`, `kcal`...); desconhecido volta como o próprio código."""
    return _FIELD_LABELS.get(code, code)


def limitation_label(code: str) -> str:
    """Texto pt-BR do código; desconhecido volta como o próprio código."""
    if code in LIMITATION_LABELS:
        return LIMITATION_LABELS[code]
    prefixo, _, campo = code.partition(":")
    modelo = PREFIXED_LIMITATION_LABELS.get(prefixo)
    if modelo is None or not campo:
        return code
    return modelo.format(field=_FIELD_LABELS.get(campo, campo))


def limitation_items(codes: Any) -> list[dict[str, str]]:
    """`[{code, label}]` na ordem recebida, sem repetir código."""
    vistos: list[str] = []
    for codigo in codes or ():
        if codigo not in vistos:
            vistos.append(str(codigo))
    return [{"code": codigo, "label": limitation_label(codigo)} for codigo in vistos]


# ---------------------------------------------------------------------------
# Proveniência por campo (M85, `catalog.FieldSource`): a nota que a tela mostra
# ---------------------------------------------------------------------------

FIELD_SOURCE_REASON_LABELS: Final[Mapping[catalog_mod.FieldSourceReason, str]] = {
    catalog_mod.FieldSourceReason.TACO_UNDER_REVIEW: f"em reavaliação na {FOOD_SOURCE_LABELS[FoodSource.TACO]}",
}

# Nome do campo da RDC 429 sem a unidade, para a frase da nota ("Valor energético, carboidratos...").
_RDC429_FIELD_NAMES: Final[Mapping[str, str]] = {campo.code: campo.label for campo in RDC429_FIELDS}


def _enumeration(items: list[str]) -> str:
    """Enumeração em português: "a"; "a e b"; "a, b e c"."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} e {items[-1]}"


def field_source_note(origin: catalog_mod.FieldSource) -> str:
    """Frase da tela: "Valor energético, carboidratos, proteínas e gorduras
    totais: USDA FoodData Central (em reavaliação na TACO 4ª ed.)."."""
    nomes = [_RDC429_FIELD_NAMES.get(campo, campo) for campo in origin.fields]
    nomes = [nome if i == 0 else nome[:1].lower() + nome[1:] for i, nome in enumerate(nomes)]
    texto = f"{_enumeration(nomes)}: {FOOD_SOURCE_LABELS.get(origin.source, origin.source.value)}"
    motivo = FIELD_SOURCE_REASON_LABELS.get(origin.reason) if origin.reason else None
    return f"{texto} ({motivo})." if motivo else f"{texto}."


def _date_pt(iso: str) -> str:
    """`2026-10-17` -> `17/10/2026`; outro formato volta como veio."""
    partes = iso.split("-")
    if len(partes) == 3 and all(p.isdigit() for p in partes):
        return f"{partes[2]}/{partes[1]}/{partes[0]}"
    return iso


def field_source_detail(origin: catalog_mod.FieldSource) -> str:
    """Detalhe da origem para o tooltip: nome na fonte, citação, licença e data
    da consulta, nesta ordem, só o que existir."""
    partes = [
        origin.name,
        origin.citation,
        f"Licença: {origin.license}" if origin.license else None,
        f"Consulta em {_date_pt(origin.retrieved_on)}" if origin.retrieved_on else None,
    ]
    return " · ".join(parte for parte in partes if parte)


def field_sources_public(raw: Any) -> list[dict[str, Any]]:
    """Origens por campo para a API (`catalog.parse_field_sources`), cada uma com
    o rótulo da fonte, a nota e o detalhe prontos. Sem origem -> `[]`."""
    return [
        {
            **origem.to_dict(),
            "source_label": FOOD_SOURCE_LABELS.get(origem.source, origem.source.value),
            "note": field_source_note(origem),
            "detail": field_source_detail(origem),
        }
        for origem in catalog_mod.parse_field_sources(raw)
    ]

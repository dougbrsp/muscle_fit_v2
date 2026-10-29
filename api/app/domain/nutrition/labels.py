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

from . import ai_usage as ai_usage_mod
from . import bmr as bmr_mod
from . import body_composition as bc_mod
from . import carb_cycling as cc_mod
from . import catalog as catalog_mod
from . import daily_target as dt_mod
from . import diary as diary_mod
from . import energy_expenditure as ee_mod
from . import external_sources as ext_mod
from . import label_photo as lp_mod
from . import macros as macros_mod
from . import meal_math as meal_math_mod
from . import meal_parse as meal_parse_mod
from . import plan as plan_mod
from . import strategy as strategy_mod
from . import weekly_budget as wb_mod
from .ai_usage import AiFeature
from .anthropometrics import ActivityLevel, Sex
from .bmr import BmrFormula
from .body_composition import BodyFatMethod
from .carb_cycling import DayKind
from .catalog import FoodSource
from .diary import MealSlot  # noqa: F401 - reexport (o enum mora no dono do diário desde o L4)
from .energy_expenditure import SessionKind, TdeeMode, TrainingKcalNature
from .external_sources import UsdaDataType
from .label_photo import LabelLegibility
from .meal_math import GENERIC_PORTIONS, MealNature, Unit
from .meal_parse import MatchStatus
from .phases import BreakProtocol, PhaseKind
from .plan import PlanState
from .strategy import ProteinBasis, Strategy


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

# Escala de fome do diário (`diary.HUNGER_SCALE`).
HUNGER_LABELS: Final[Mapping[int, str]] = {
    1: "Sem fome",
    2: "Pouca fome",
    3: "Fome moderada",
    4: "Muita fome",
    5: "Fome intensa",
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
}

# O que a cota conta, no plural, para o texto do 429.
_AI_QUOTA_NOUNS: Final[Mapping[AiFeature, str]] = {
    AiFeature.MEAL_PARSE: "leituras de refeição por IA",
    AiFeature.LABEL_PHOTO: "leituras de rótulo por foto",
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

# Campos do plano, para os códigos `<prefixo>:<campo>` (faixa, código inválido,
# campo de outra estratégia).
PLAN_FIELD_LABELS: Final[Mapping[str, str]] = {
    "strategy": "Estratégia",
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
    # Clamps do cutting (decisão D-B).
    strategy_mod.CLAMP_MAX_DEFICIT_SHARE: (
        f"Déficit limitado a {_percent(strategy_mod.CUT_MAX_DEFICIT_SHARE)} do gasto total."
    ),
    strategy_mod.CLAMP_MIN_INTAKE_BMR_MULTIPLE: "Ingestão elevada ao piso do cutting (TMB).",
    # Entrada do plano.
    plan_mod.ERR_CARB_CYCLING_UNAVAILABLE: "Ciclo de carboidrato ainda indisponível (chega com o calendário de fases).",
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
    diary_mod.ERR_ITEM_FOOD_OR_ESTIMATE: "Cada item precisa de um alimento do catálogo ou da estimativa da IA.",
    ext_mod.ERR_USDA_DATA_TYPE: (
        "Só alimentos "
        + " e ".join(USDA_DATA_TYPE_LABELS[tipo] for tipo in ext_mod.USDA_DATA_TYPES)
        + " do USDA são importados."
    ),
    catalog_mod.WARN_ATWATER: (
        f"As kcal informadas diferem mais de {_percent(meal_math_mod.ATWATER_TOLERANCE_SHARE)} "
        "das kcal calculadas pelos macros (Atwater). Confira os valores."
    ),
    catalog_mod.WARN_MACROS_ABOVE_BASIS: "Proteína, carboidrato e gordura somados passam da base da tabela.",
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
    diary_mod.ERR_INVALID_HUNGER: f"Fome: de {diary_mod.HUNGER_SCALE[0]} a {diary_mod.HUNGER_SCALE[1]}.",
    diary_mod.ERR_INVALID_WATER: (
        f"Água: de {diary_mod.WATER_ML_RANGE[0]:g} a {diary_mod.WATER_ML_RANGE[1]:g} ml no dia."
    ),
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
    # Meta lida pelo diário (L4, `daily_target.diary_target_action`).
    dt_mod.LIMIT_TARGET_BACKFILLED: (
        "Meta calculada depois que o dia congelou (ninguém abriu o diário a tempo), com os dados disponíveis no dia "
        "em que o diário foi aberto."
    ),
    dt_mod.LIMIT_TARGET_FUTURE_BEYOND_WEEK: "Dia além da semana atual: a meta é calculada quando a semana chegar.",
}

# Avisos exibidos pelas telas (o FE lê do bloco `nutrition.notices`).
NOTICE_CARB_CYCLING_UNAVAILABLE = plan_mod.ERR_CARB_CYCLING_UNAVAILABLE
NOTICE_SCALE_BMR_DEVIATION = "scale_bmr_deviation"
NOTICE_PHASE_SHOULD_STOP = "phase_should_stop"
# Diário (L4): selos e dicas das telas com o MESMO texto das limitações do motor.
NOTICE_TARGET_FROZEN = "target_frozen"
NOTICE_TARGET_BACKFILLED = dt_mod.LIMIT_TARGET_BACKFILLED
NOTICE_INCOMPLETE_INTAKE_DAYS = wb_mod.LIMIT_INCOMPLETE_INTAKE_DAYS
NOTICE_TRAINING_UNKNOWN_FUTURE_DAY = dt_mod.LIMIT_TRAINING_UNKNOWN_FUTURE_DAY

NOTICE_LABELS: Final[Mapping[str, str]] = {
    NOTICE_CARB_CYCLING_UNAVAILABLE: (
        "Ciclo de carboidrato: chega com o calendário de fases; por enquanto a meta é a mesma todos os dias da semana."
    ),
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
    plan_mod.ERR_PARAM_NOT_APPLICABLE: "{field}: não se aplica a esta estratégia.",
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
    **labels_by_code(BODY_FAT_METHOD_LABELS),
    **RDC429_FIELD_LABELS,
}


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

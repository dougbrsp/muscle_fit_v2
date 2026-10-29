// Constantes de nutrição (Lote L1 do plano `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`).
//
// Códigos, rótulos pt-BR, faixas de formulário, campos da RDC 429 e porções
// caseiras NÃO são escritos aqui. O dono é `api/app/domain/nutrition/`
// (`export_for_frontend()`), exportado para o bloco `nutrition` de
// `motor-constants.generated.json` por `api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`.
// A guarda (`rotulo_estrategia`, `fator_atividade`, `kcal_por_grama`...) recusa
// cópia em `frontend/src/lib/nutrition-*`. O FE não calcula nutrição: metas,
// macros e o preview da refeição vêm prontos da API (decisão E9 do plano).

import motorConstants from './motor-constants.generated.json';

const NUTRITION = motorConstants.nutrition;

/** Código de um enum do motor (a API valida; o FE não enumera à mão). */
export type NutritionCode = string;

/** Opção de Select: código do motor + rótulo do dono. */
export type NutritionOption = { value: NutritionCode; label: string };

/** Campo da tabela nutricional (RDC 429/2020) na ordem do dono. */
export type Rdc429Field = { code: string; label: string; unit: string; required: boolean };

/** Medida caseira genérica (colher, xícara) em ml; a conversão é da API. */
export type GenericPortion = { code: NutritionCode; label: string; ml: number };

/** Faixas e defaults dos formulários de perfil e de plano, exatamente como o dono exporta. */
export type NutritionFormRanges = typeof NUTRITION.form_ranges;

function optionsOf(
  codes: ReadonlyArray<NutritionCode>,
  labels: Readonly<Record<NutritionCode, string>>,
): ReadonlyArray<NutritionOption> {
  return codes.map((value) => ({ value, label: labels[value] ?? value }));
}

export const NUTRITION_ENGINE_VERSION: string = NUTRITION.engine_version;

export const SEX_OPTIONS = optionsOf(NUTRITION.sexes, NUTRITION.sex_labels);
export const ACTIVITY_LEVEL_OPTIONS = optionsOf(NUTRITION.activity_levels, NUTRITION.activity_level_labels);
export const STRATEGY_OPTIONS = optionsOf(NUTRITION.strategies, NUTRITION.strategy_labels);
export const PROTEIN_BASIS_OPTIONS = optionsOf(NUTRITION.protein_bases, NUTRITION.protein_basis_labels);
export const DAY_KIND_OPTIONS = optionsOf(NUTRITION.day_kinds, NUTRITION.day_kind_labels);
export const MEAL_SLOT_OPTIONS = optionsOf(NUTRITION.meal_slots, NUTRITION.meal_slot_labels);
export const BMR_FORMULA_OPTIONS = optionsOf(NUTRITION.bmr_formulas, NUTRITION.bmr_formula_labels);
export const TDEE_MODE_OPTIONS = optionsOf(NUTRITION.tdee_modes, NUTRITION.tdee_mode_labels);
export const SESSION_KIND_OPTIONS = optionsOf(NUTRITION.session_kinds, NUTRITION.session_kind_labels);
/** Natureza da kcal do treino (informada × estimada por MET). A proveniência
 * (manual × Intervals) não é deste bloco: é de `domain/calories_source.py` (L0). */
export const TRAINING_KCAL_NATURE_OPTIONS = optionsOf(
  NUTRITION.training_kcal_natures,
  NUTRITION.training_kcal_nature_labels,
);
/** Natureza dos totais da refeição (DERIVED/ESTIMATED de `DataNature` + MIXED).
 * A natureza e a qualidade comuns (OBSERVED, HIGH...) não são deste bloco: são
 * de `api/app/domain/data_nature.py`, com rótulos em `dashboard-status.ts`. */
export const MEAL_NATURE_OPTIONS = optionsOf(NUTRITION.meal_natures, NUTRITION.meal_nature_labels);
/** Método da leitura de %G (bioimpedância ou circunferências). */
export const BODY_FAT_METHOD_OPTIONS = optionsOf(NUTRITION.body_fat_methods, NUTRITION.body_fat_method_labels);
export const PHASE_KIND_OPTIONS = optionsOf(NUTRITION.phase_kinds, NUTRITION.phase_kind_labels);
export const BREAK_PROTOCOL_OPTIONS = optionsOf(NUTRITION.break_protocols, NUTRITION.break_protocol_labels);
/** Estado do plano do aluno (rascunho, vigente, substituído) — L2b. */
export const PLAN_STATE_OPTIONS = optionsOf(NUTRITION.plan_states, NUTRITION.plan_state_labels);

export const DEFAULT_TDEE_MODE: NutritionCode = NUTRITION.default_tdee_mode;
/** Fórmula da TMB que o motor usa quando o plano não fixa uma (seleção automática). */
export const DEFAULT_BMR_FORMULA: NutritionCode = NUTRITION.default_bmr_formula;
/** Unidades aceitas no item da refeição (g, ml, unidade, porção e medidas caseiras). */
export const MEAL_UNITS: ReadonlyArray<NutritionCode> = NUTRITION.units;
export const GENERIC_PORTIONS: ReadonlyArray<GenericPortion> = NUTRITION.generic_portions;
export const RDC429_FIELDS: ReadonlyArray<Rdc429Field> = NUTRITION.rdc429_fields;
export const NUTRITION_FORM_RANGES: NutritionFormRanges = NUTRITION.form_ranges;

/**
 * Faixas e defaults do formulário de UMA estratégia (bloco `form_ranges.<código>`
 * do dono). Os campos presentes dizem o que a estratégia usa: só o cutting tem
 * ritmo semanal; só o bulking tem superávit e carbo mínimo. Valores na unidade
 * do motor (fração, g/kg) — a tela só converte fração em % para exibir.
 */
export type StrategyFormRanges = {
  protein_basis: NutritionCode;
  protein_g_per_kg: ReadonlyArray<number>;
  protein_g_per_kg_default: number;
  fat_kcal_share: ReadonlyArray<number>;
  fat_kcal_share_default: number;
  weekly_rate_share?: ReadonlyArray<number>;
  weekly_rate_share_default?: number;
  surplus_share?: ReadonlyArray<number>;
  surplus_share_default?: number;
  surplus_share_advanced?: ReadonlyArray<number>;
  surplus_share_default_advanced?: number;
  carb_min_g_per_kg?: ReadonlyArray<number>;
  carb_min_g_per_kg_default?: number;
};

export function strategyFormRanges(strategy: NutritionCode | null | undefined): StrategyFormRanges | null {
  if (!strategy || !NUTRITION.strategies.includes(strategy)) return null;
  const blocos = NUTRITION.form_ranges as unknown as Record<string, StrategyFormRanges | undefined>;
  return blocos[strategy] ?? null;
}

/** Campo do plano -> a ÚNICA estratégia em que ele vale (dono: `plan.STRATEGY_ONLY_FIELDS`). */
export const STRATEGY_ONLY_FIELDS: Readonly<Record<string, NutritionCode>> = NUTRITION.strategy_only_fields;

/** O campo do formulário vale para a estratégia? Campo fora do mapa vale para todas. */
export function fieldAppliesToStrategy(field: string, strategy: NutritionCode | null | undefined): boolean {
  const dona = STRATEGY_ONLY_FIELDS[field];
  return dona === undefined || dona === strategy;
}

// ---------------------------------------------------------------------------
// Catálogo de alimentos (Lote L3a): dono `domain/nutrition/catalog.py` e
// `labels.py`. Regras de entrada (obrigatórios, faixas, Atwater) são da API;
// aqui só vocabulário, rótulos, campos e citações para exibir.
// ---------------------------------------------------------------------------

/** Fonte do alimento (TACO, USDA, cadastro próprio...). */
export const FOOD_SOURCE_OPTIONS = optionsOf(NUTRITION.food_sources, NUTRITION.food_source_labels);
/** Base da tabela do alimento: por 100 g ou por 100 ml. */
export const FOOD_BASIS_OPTIONS = optionsOf(NUTRITION.food_bases, NUTRITION.food_basis_labels);
export const DEFAULT_FOOD_BASIS: NutritionCode = NUTRITION.default_food_basis;

function rdc429Fields(codes: ReadonlyArray<string>): ReadonlyArray<Rdc429Field> {
  return codes
    .map((code) => RDC429_FIELDS.find((campo) => campo.code === code))
    .filter((campo): campo is Rdc429Field => campo !== undefined);
}

/** Campos por 100 g/ml do catálogo, na ordem da RDC 429, com rótulo e unidade do dono. */
export const FOOD_PER_100_FIELDS: ReadonlyArray<Rdc429Field> = rdc429Fields(NUTRITION.food_per_100_fields);
/** Campos da porção do rótulo (porção, medida caseira, porções por embalagem). */
export const FOOD_SERVING_FIELDS: ReadonlyArray<Rdc429Field> = rdc429Fields(NUTRITION.food_serving_fields);
/** Campos que o cadastro exige (a API confere de novo e responde 400 com o código do motor). */
export const FOOD_REQUIRED_FIELDS: ReadonlyArray<string> = NUTRITION.food_required_fields;
/** Citação obrigatória por fonte (rodapé da tela; cada item da API também traz a sua). */
export const FOOD_CITATIONS: Readonly<Record<string, string>> = NUTRITION.food_citations;
/** Licença de cada fonte (rodapé; a ODbL do Open Food Facts exige a atribuição, risco R4). */
export const FOOD_LICENSES: Readonly<Record<string, string>> = NUTRITION.food_licenses;
/** Tipos de dado do USDA FoodData Central que a importação aceita (L3b). */
export const USDA_DATA_TYPE_OPTIONS = optionsOf(NUTRITION.usda_data_types, NUTRITION.usda_data_type_labels);

// ---------------------------------------------------------------------------
// Diário de refeições (Lote L4): dono `domain/nutrition/meal_math.py`,
// `diary.py` e `labels.py`. Conversão de unidade, macros, totais, semana e
// balanço são da API; aqui só vocabulário, rótulos e faixas do formulário.
// ---------------------------------------------------------------------------

/** Unidade do item da refeição (g, ml, porção, medidas caseiras) com o rótulo do dono. */
export const UNIT_OPTIONS = optionsOf(NUTRITION.units, NUTRITION.unit_labels);
/** Unidades cuja quantidade vem de uma porção (cadastrada ou do rótulo): a tela mostra o seletor de porção. */
export const PORTION_UNITS: ReadonlyArray<NutritionCode> = NUTRITION.portion_units;
/** Nutrientes exibidos no diário, na ordem do dono, com rótulo e unidade da RDC 429. */
export const NUTRIENT_FIELDS: ReadonlyArray<Rdc429Field> = rdc429Fields(NUTRITION.nutrient_fields);
/** Colunas por item da refeição (energia + os três macros de Atwater), na ordem do dono. */
export const ITEM_NUTRIENT_FIELDS: ReadonlyArray<Rdc429Field> = rdc429Fields(NUTRITION.item_nutrient_fields);
/** Escala de fome do diário (códigos `1`..`5` como texto, para o SegmentedControl). */
export const HUNGER_OPTIONS: ReadonlyArray<NutritionOption> = NUTRITION.hunger_levels.map((nivel) => ({
  value: String(nivel),
  label: (NUTRITION.hunger_labels as Record<string, string>)[String(nivel)] ?? String(nivel),
}));
/** Faixa de água do dia em ml (a API confere de novo). */
export const WATER_ML_LIMITS: ReadonlyArray<number> = NUTRITION.water_ml_range;
/** Teto de itens de uma refeição (a API confere de novo). */
export const MEAL_ITEMS_LIMIT: number = NUTRITION.meal_max_items;

/** Código do nutriente de energia (RDC 429, `meal_math.ENERGY_FIELD`), chave dos totais e da semana. */
export const ENERGY_NUTRIENT: NutritionCode = NUTRITION.energy_field;

/** Rótulo e unidade de um nutriente do diário (`energy_kcal` -> Valor energético, kcal). */
export function nutrientField(code: string): Rdc429Field | null {
  return NUTRIENT_FIELDS.find((campo) => campo.code === code) ?? null;
}

// ---------------------------------------------------------------------------
// Leitura da refeição por IA (Lote L5b): dono `domain/nutrition/meal_parse.py`.
// A cota do dia vem da API (`GET /nutrition/meals/parse/quota`); o casamento,
// a quantidade proposta e os nutrientes do item estimado também.
// ---------------------------------------------------------------------------

const MEAL_PARSE = NUTRITION.meal_parse;
/** Teto de caracteres da descrição (a API confere de novo e responde 400 com o código do motor). */
export const MEAL_PARSE_TEXT_MAX_CHARS: number = MEAL_PARSE.text_max_chars;
/** Decisão do casamento de cada item lido (encontrado, escolher, estimado, não encontrado). */
export const MEAL_PARSE_STATUS_OPTIONS = optionsOf(MEAL_PARSE.match_statuses, MEAL_PARSE.match_status_labels);
/** Fonte do item estimado pela IA (`food_source` do item gravado). */
export const AI_ESTIMATE_SOURCE: NutritionCode = MEAL_PARSE.estimate_source;
/** Base da estimativa da IA (por 100 g): a base do item estimado no editor. */
export const AI_ESTIMATE_BASIS: NutritionCode = MEAL_PARSE.estimate_basis;
/** Texto do dono para a cota do dia esgotada (com o número da cota). */
export const MEAL_PARSE_QUOTA_EXCEEDED_LABEL: string = MEAL_PARSE.quota_exceeded_label;

/** Avisos das telas, com o texto do dono (`labels.NOTICE_LABELS`). */
export const NUTRITION_NOTICES: Readonly<Record<string, string>> = NUTRITION.notices;

export function nutritionNotice(code: string): string {
  return NUTRITION_NOTICES[code] ?? code;
}

/** Rótulo do código na lista de opções; código desconhecido (payload mais novo que o
 * bundle) volta como o próprio código, nunca como texto inventado. */
export function nutritionLabel(
  options: ReadonlyArray<NutritionOption>,
  code: NutritionCode | null | undefined,
): string | null {
  if (code === null || code === undefined) return null;
  return options.find((option) => option.value === code)?.label ?? code;
}

// ---------------------------------------------------------------------------
// Foto do rótulo (Lote L6): dono `domain/nutrition/label_photo.py`. A tela
// reduz a foto por canvas com estes parâmetros (borda, qualidade, tipo); limite e
// tipo são conferidos pela API (413/415). Cota, leitura, revisão e avisos vêm
// prontos da API.
// ---------------------------------------------------------------------------

/** Parâmetros da redução e do upload da foto do rótulo, exatamente como o dono exporta. */
export type LabelPhotoParams = typeof NUTRITION.label_photo;

export const LABEL_PHOTO: LabelPhotoParams = NUTRITION.label_photo;
/** Legibilidade que a leitura devolve (tabela legível, parcial, ilegível). */
export const LABEL_LEGIBILITY_OPTIONS = optionsOf(NUTRITION.label_legibilities, NUTRITION.label_legibility_labels);

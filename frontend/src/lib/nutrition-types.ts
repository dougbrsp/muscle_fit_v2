// Contratos do plano nutricional (Lote L2b do plano
// `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`), espelhando as respostas de
// `api/app/nutrition_plans.py` e `api/app/nutrition_targets_service.py`.
//
// Só tipos: códigos vêm do motor (`nutrition-constants.ts`, JSON gerado) e
// todo número (TMB, TDEE, alvo, macros) vem pronto da API. `null` = não
// calculável ou não informado — nunca 0.

import type { NutritionCode } from './nutrition-constants';

/** Código + texto pt-BR de uma limitação/erro do motor (a API já rotula). */
export type NutritionLimitation = { code: string; label: string };

/** Configuração do plano no vocabulário do motor (`PlanSettings.to_dict`). */
export type NutritionPlanSettings = {
  strategy: NutritionCode;
  advanced: boolean;
  protein_g_per_kg: number;
  protein_basis: NutritionCode;
  fat_kcal_share: number;
  weekly_rate_share: number | null;
  surplus_share: number | null;
  carb_min_g_per_kg: number | null;
  bmr_formula: NutritionCode;
  tdee_mode: NutritionCode;
  activity_level: NutritionCode | null;
  meals_per_day: number | null;
  goal_body_fat_percent: number | null;
  goal_weight_kg: number | null;
  carb_cycling_enabled: boolean;
};

/** `GET /nutrition/plans` (itens), `POST/PUT` e `plan` das demais respostas. */
export type NutritionPlan = NutritionPlanSettings & {
  id: number;
  user_id: number;
  status: 'draft' | 'published';
  /** `draft` | `current` | `superseded` (rótulo em `PLAN_STATE_OPTIONS`). */
  state: NutritionCode;
  notes: string | null;
  engine_version: string | null;
  effective_from: string | null;
  superseded_at: string | null;
  published_at: string | null;
  created_by: number | null;
  published_by: number | null;
  created_at: string | null;
  updated_at: string | null;
};

export type NutritionPlanUser = { id: number; username: string; full_name: string | null };

export type NutritionPlanListResponse = { user: NutritionPlanUser; items: NutritionPlan[] };

/**
 * Corpo de `PUT /nutrition/plans/{id}` (`NutritionPlanSettingsIn`, `extra="forbid"`).
 * Campo `null` = default da estratégia no motor. Faixas e códigos são
 * validados pela API (400 com o código do motor).
 */
export type NutritionPlanSettingsPayload = {
  strategy: NutritionCode;
  advanced: boolean;
  protein_g_per_kg: number | null;
  fat_kcal_share: number | null;
  weekly_rate_share: number | null;
  surplus_share: number | null;
  carb_min_g_per_kg: number | null;
  bmr_formula: NutritionCode;
  tdee_mode: NutritionCode;
  activity_level: NutritionCode | null;
  meals_per_day: number | null;
  goal_body_fat_percent: number | null;
  goal_weight_kg: number | null;
  carb_cycling_enabled: boolean;
  notes: string | null;
};

/** Corpo de `POST /nutrition/plans` (`NutritionPlanIn`). */
export type NutritionPlanPayload = NutritionPlanSettingsPayload & { user_id: number };

/** Corpo de `POST /nutrition/plans/{id}/publish` (`NutritionPlanPublishIn`): obrigatório, pode ser null. */
export type NutritionPlanPublishPayload = { expected_current_id: number | null };

/** Janela de materialização do motor (`daily_target.TargetWindow`). */
export type NutritionTargetWindow = { date_from: string; date_to: string; create_until: string };

/** Corpo de `POST /nutrition/targets/recompute` (`NutritionTargetsRecomputeIn`). */
export type NutritionRecomputePayload = { user_id: number; date_from: string; date_to: string | null };

/** Sujeito do dia (`subject.ResolvedSubject.to_dict`). */
export type NutritionSubject = {
  day: string;
  sex: NutritionCode | null;
  age_years: number | null;
  height_cm: number | null;
  weight_kg: number | null;
  body_fat_percent: number | null;
  fat_free_mass_kg: number | null;
  activity_level: NutritionCode | null;
  bmi: number | null;
  body_fat_method: NutritionCode | null;
  body_fat_date: string | null;
  weight_date: string | null;
  ffm_age_days: number | null;
  ffm_kg: number | null;
  fat_mass_kg: number | null;
  scale_bmr_kcal: number | null;
  limitations: string[];
};

/** Meta de um dia — calculada (preview) ou materializada (`nutrition_daily_targets`). */
export type NutritionDayTarget = {
  local_date: string;
  plan_id: number | null;
  day_kind: NutritionCode;
  is_training_day: boolean;
  /** `false` = dia futuro: o treino ainda não entrou no gasto. */
  training_known: boolean;
  phase_kind: NutritionCode | null;
  kcal: number | null;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
  fiber_g: number | null;
  water_ml: number | null;
  bmr_kcal: number | null;
  bmr_formula: NutritionCode;
  tdee_kcal: number | null;
  training_kcal: number | null;
  training_kcal_nature: NutritionCode | null;
  limitations: NutritionLimitation[];
  engine_version: string | null;
  computed_at: string | null;
  /** Congelada: já não muda (lag do motor após o dia). */
  frozen: boolean;
  /** L4: meta calculada depois do congelamento (o dia passou sem meta), marcada pelo motor. */
  backfilled?: boolean;
};

/** Meta de hoje com o detalhe de cada etapa do motor (preview). */
export type NutritionTargetDetail = NutritionDayTarget & {
  bmr: { kcal: number | null; formula: NutritionCode; requested_formula: NutritionCode; ffm_kg: number | null };
  scale_comparison: { formula_kcal: number; scale_kcal: number; deviation_share: number; warn: boolean } | null;
  tdee: { kcal: number | null; mode: NutritionCode; activity_factor: number | null; bmr_kcal: number | null; training_kcal: number | null };
  training: { kcal: number | null; kcal_nature: NutritionCode | null; nature: string | null; quality: string | null };
  kcal_target: {
    kcal: number | null;
    delta_kcal: number | null;
    rate_applied: number | null;
    clamped_by: NutritionLimitation[];
    limitations: string[];
  };
  macros: Record<string, unknown>;
  protein_per_meal_g: number | null;
};

/** Meta de %G e peso correspondente (motor: `phases`, `body_composition`). */
export type NutritionGoal = {
  goal_body_fat_percent: number | null;
  goal_body_fat_is_default: boolean;
  goal_weight_kg: number | null;
  target_weight_for_goal_kg: number | null;
  should_stop: boolean | null;
};

/** `POST /nutrition/plans/{id}/preview` (200). */
export type NutritionPlanPreview = {
  plan: NutritionPlan;
  user: NutritionPlanUser;
  today: string;
  engine_version: string;
  subject: NutritionSubject;
  target: NutritionTargetDetail;
  goal: NutritionGoal;
  week: NutritionDayTarget[];
  limitations: NutritionLimitation[];
};

/** `detail` do 422 do preview/publish: dados do aluno que faltam. */
export type NutritionSubjectIncompleteDetail = {
  error: 'nutrition_subject_incomplete';
  message: string;
  missing: NutritionLimitation[];
  subject: NutritionSubject;
};

/** `detail` do 400 de validação do plano. */
export type NutritionInvalidPlanDetail = { error: 'invalid_plan'; message: string; errors: NutritionLimitation[] };

/** `POST /nutrition/plans/{id}/publish` (200). */
export type NutritionPublishResponse = {
  plan: NutritionPlan;
  window: NutritionTargetWindow;
  targets: NutritionDayTarget[];
};

/** `POST /nutrition/targets/recompute` (200). */
export type NutritionRecomputeResponse = NutritionTargetWindow & {
  user_id: number;
  targets: NutritionDayTarget[];
};

/** `GET /nutrition/me/plan` (200). */
export type NutritionMyPlan = {
  plan: NutritionPlan;
  today: string;
  subject: NutritionSubject;
  goal: NutritionGoal;
  today_target: NutritionDayTarget | null;
  week: { local_date: string; target: NutritionDayTarget | null }[];
  missing: NutritionLimitation[];
};

// ---------------------------------------------------------------------------
// Catálogo de alimentos (Lote L3a): respostas de `api/app/nutrition_foods.py`.
// Valores por 100 g/ml com os códigos da RDC 429 do motor
// (`FOOD_PER_100_FIELDS`); `null` = a fonte não informa (nunca 0 inventado).
// Permissões vêm prontas da API (`nutrition_access`, decisão D-G).
// ---------------------------------------------------------------------------

export type FoodPer100 = Record<string, number | null>;

export type FoodServing = {
  serving_size: number | null;
  household_measure: string | null;
  servings_per_package: number | null;
};

export type FoodPermissions = { can_edit: boolean; can_add_portion: boolean; can_promote: boolean };

/**
 * Origem de valores do alimento que NÃO vieram da fonte da linha (M85,
 * `catalog.FieldSource`): hoje, os campos "em reavaliação" da TACO preenchidos
 * com o USDA FoodData Central. `note` e `detail` são frases prontas do motor
 * (`labels`); a tela só exibe.
 */
export type FoodFieldSource = {
  /** Códigos da RDC 429 (`FOOD_PER_100_FIELDS`) cujos valores vieram desta origem. */
  fields: string[];
  /** Código de `FOOD_SOURCE_OPTIONS` (usda...). */
  source: NutritionCode;
  source_label: string;
  source_id: string | null;
  source_version: string | null;
  name: string | null;
  /** Data da consulta à fonte (ISO). */
  retrieved_on: string | null;
  reason: string | null;
  citation: string | null;
  license: string | null;
  note: string;
  /** Nome na fonte, citação, licença e data da consulta, prontos para o tooltip. */
  detail: string;
};

export type FoodItem = {
  id: number;
  /** Código de `FOOD_SOURCE_OPTIONS` (taco, user...). */
  source: NutritionCode;
  source_id: string | null;
  source_version: string | null;
  barcode: string | null;
  name: string;
  brand: string | null;
  category: string | null;
  /** Base da tabela: código de `FOOD_BASIS_OPTIONS` (g | ml). */
  basis: NutritionCode;
  per_100: FoodPer100;
  serving: FoodServing;
  license: string | null;
  citation: string | null;
  /** Valores que vieram de outra fonte (ex.: USDA nos campos "em reavaliação" da TACO); `[]` = todos da linha. */
  field_sources: FoodFieldSource[];
  is_public: boolean;
  is_own: boolean;
  is_active: boolean;
  permissions: FoodPermissions;
  created_at: string | null;
  updated_at: string | null;
};

/** Item de `GET /nutrition/foods/search`: nota e pré-seleção do motor (`food_text`). */
export type FoodSearchItem = FoodItem & { score: number; auto_accept: boolean };

export type FoodSearchResponse = { query: string; normalized: string; items: FoodSearchItem[] };

/** `visible` (públicos + os meus) ou `private_all` (admin: privados de todos, para promover). */
export type FoodSearchScope = 'visible' | 'private_all';

export type FoodPortion = {
  id: number;
  food_id: number;
  label: string;
  /** Quantidade na base do alimento (g, ou ml quando `basis` = ml). */
  amount: number;
  source: NutritionCode;
  is_public: boolean;
  is_own: boolean;
};

/** `GET /nutrition/foods/{id}`. */
export type FoodDetail = FoodItem & { portions: FoodPortion[] };

/** `POST /nutrition/foods` (201) e `PUT /nutrition/foods/{id}`: avisos do motor (não bloqueiam). */
export type FoodSaveResponse = FoodItem & {
  warnings: NutritionLimitation[];
  atwater: { declared_kcal: number; computed_kcal: number; deviation_share: number | null; consistent: boolean } | null;
};

/**
 * Corpo de `POST /nutrition/foods` e `PUT /nutrition/foods/{id}` (`FoodItemIn`,
 * `extra="forbid"`): nome, marca, categoria, base, os campos por 100 da RDC 429
 * e os da porção do rótulo. Obrigatórios e faixas são da API (400 com o código).
 */
export type FoodItemPayload = Record<string, string | number | null>;

/** Corpo de `POST /nutrition/foods/{id}/portions` (`FoodPortionIn`). */
export type FoodPortionPayload = { label: string; amount: number };

// ---------------------------------------------------------------------------
// Fontes externas do catálogo (Lote L3b): respostas de
// `api/app/nutrition_external_foods.py`. Mapeamento, conversões e validação
// são do motor (`domain/nutrition/external_sources`); aqui só o formato.
// ---------------------------------------------------------------------------

/** Alimento gravado/lido de uma fonte externa (OFF ou USDA), com os avisos do motor. */
export type ExternalFoodResponse = FoodSaveResponse & {
  /** Código de barras: `true` quando o produto já estava no catálogo (nenhuma consulta nova ao OFF). */
  from_cache?: boolean;
  /** Importação do USDA: `false` quando o item já tinha sido importado (idempotente). */
  created?: boolean;
};

/** Item de `GET /nutrition/foods/usda/search`: nome em inglês e campos por 100 g já mapeados pelo motor. */
export type UsdaSearchItem = {
  fdc_id: number;
  name: string | null;
  /** Código de `USDA_DATA_TYPE_OPTIONS` (Foundation, SR Legacy). */
  data_type: NutritionCode;
  category: string | null;
  basis: NutritionCode;
  per_100: FoodPer100;
  energy_origin: string | null;
};

export type UsdaSearchResponse = { query: string; total_hits: number | null; items: UsdaSearchItem[] };

// ---------------------------------------------------------------------------
// Diário de refeições (Lote L4): respostas de `api/app/nutrition_meals.py` e
// `api/app/nutrition_days.py`. Nutrientes com os códigos da RDC 429 do motor
// (`NUTRIENT_FIELDS`); `null` = desconhecido (nunca 0). Totais, balanço,
// semana e kcal do treino vêm prontos do motor; a tela só exibe.
// ---------------------------------------------------------------------------

export type NutrientValues = Record<string, number | null>;

/** Total de uma refeição ou do dia (`meal_math.NutritionTotals`): campo em
 * `incomplete_fields` = há alimento sem esse valor; o número é um mínimo. */
export type NutritionTotals = {
  nutrients: NutrientValues;
  incomplete_fields: string[];
  complete: boolean;
  /** Código de `MEAL_NATURE_OPTIONS` (DERIVED, ESTIMATED, MIXED). */
  nature: NutritionCode | null;
  item_count: number;
  limitations: NutritionLimitation[];
};

export type MealItem = {
  /** `null` no preview de um item novo. */
  id: number | null;
  position: number;
  food_id: number | null;
  portion_id: number | null;
  portion_label: string | null;
  label: string;
  quantity: number;
  /** Código de `UNIT_OPTIONS`. */
  unit: NutritionCode;
  /** Quantidade na base do alimento (g ou ml), resolvida pelo motor. */
  amount: number;
  basis: NutritionCode;
  nutrients: NutrientValues;
  nature: NutritionCode;
  limitations: NutritionLimitation[];
  food_source: NutritionCode | null;
  citation: string | null;
  /** Origem dos valores do alimento congelada no item (M85); `[]` em itens gravados antes. */
  field_sources: FoodFieldSource[];
  /** Nota do casamento da leitura por IA (L5b); `null` no item manual ou estimado. */
  match_confidence?: number | null;
};

export type Meal = {
  id: number;
  user_id: number;
  local_date: string;
  meal_slot: NutritionCode;
  /** `HH:MM` ou `null`. */
  eaten_at: string | null;
  source: NutritionCode;
  /** Texto descrito na leitura por IA (L5b); `null` na refeição manual. */
  description_raw?: string | null;
  ai_job_id?: string | null;
  notes: string | null;
  totals: NutritionTotals;
  items: MealItem[];
  engine_version: string | null;
  created_at: string | null;
  updated_at: string | null;
};

/** Estimativa da IA por 100 g de um alimento fora do catálogo (`MealItemEstimateIn`, L5b):
 * a tela devolve o que a leitura entregou, sem calcular nada. */
export type MealItemEstimatePayload = {
  name: string;
  energy_kcal: number | null;
  protein_g: number | null;
  carbohydrates_g: number | null;
  total_fat_g: number | null;
  fiber_g: number | null;
};

/** Item do corpo de `POST/PUT /nutrition/meals` (`MealItemIn`, `extra="forbid"`): o FE nunca calcula macro.
 * Origem: um alimento do catálogo (`food_id`) OU a estimativa da IA (`estimate`; na edição, só o `id`). */
export type MealItemPayload = {
  /** Item já gravado da refeição (edição): reusa os valores congelados. */
  id?: number | null;
  food_id: number | null;
  estimate?: MealItemEstimatePayload | null;
  quantity: number;
  unit: NutritionCode;
  portion_id?: number | null;
  match_confidence?: number | null;
};

/** `PUT /nutrition/meals/{id}` (`MealBodyIn`). */
export type MealPayload = {
  local_date: string;
  meal_slot: NutritionCode;
  eaten_at: string | null;
  notes: string | null;
  items: MealItemPayload[];
};

/** `POST /nutrition/meals` (`MealIn`): `user_id` só quando admin/PT registra para um aluno;
 * `ai_job_id` e `description_raw` quando a refeição veio da leitura por IA (L5b). */
export type MealCreatePayload = MealPayload & {
  user_id?: number | null;
  ai_job_id?: string | null;
  description_raw?: string | null;
};

/** `POST /nutrition/meals/preview` (`MealPreviewIn`): `meal_id` = refeição em edição. */
export type MealPreviewPayload = MealCreatePayload & { meal_id?: number | null };

// ---------------------------------------------------------------------------
// Leitura da refeição por IA (Lote L5b): `POST /nutrition/meals/parse` (202) +
// polling em `GET /jobs/{id}`. A decisão de cada item é do motor (`meal_parse`).
// ---------------------------------------------------------------------------

/** `POST /nutrition/meals/parse` (`MealParseIn`). */
export type MealParsePayload = {
  text: string;
  local_date: string;
  meal_slot: NutritionCode | null;
  user_id?: number | null;
};

/** Cota de hoje de quem pede (`ai_usage.QuotaStatus`). */
export type AiQuota = { feature: string; limit: number; used: number; remaining: number; allowed: boolean };

/** Candidato do catálogo para um item lido, na ordem da nota do motor. */
export type MealParseCandidate = {
  candidate_id: number;
  food_id: number;
  name: string;
  score: number;
  auto_accept: boolean;
  basis: NutritionCode | null;
  source: NutritionCode | null;
  brand: string | null;
};

/** Um item lido: o que a IA entendeu, a decisão do motor e a proposta (alimento ou estimativa + quantidade). */
export type MealParseItem = {
  index: number;
  ai: {
    name: string;
    quantity: number | null;
    unit: NutritionCode | null;
    grams_estimate: number | null;
    preparation: string | null;
    confidence: number | null;
  };
  /** Código de `MEAL_PARSE_STATUS_OPTIONS`. */
  status: NutritionCode;
  candidates: MealParseCandidate[];
  /** Alimento pré-selecionado pelo motor (aceite automático) ou `null`. */
  food_id: number | null;
  match_confidence: number | null;
  quantity: number | null;
  unit: NutritionCode;
  estimate: MealItemEstimatePayload | null;
  candidate_for_catalog: boolean;
  /** A proposta do item é a estimativa da IA (decisão do motor). */
  uses_estimate: boolean;
  limitations: NutritionLimitation[];
};

/** `result` do job da leitura. */
export type MealParseResult = {
  items: MealParseItem[];
  limitations: NutritionLimitation[];
  notes: string | null;
  job_id: string | null;
  user_id: number;
  local_date: string | null;
  meal_slot: NutritionCode | null;
  model: string | null;
  prompt_version: string;
  engine_version: string;
};

export type MealPreview = {
  user_id: number;
  local_date: string;
  meal_slot: NutritionCode;
  items: MealItem[];
  totals: NutritionTotals;
  engine_version: string;
};

/** Um nutriente do dia: meta, ingestão (mínimo se incompleta), falta e fração da meta (`weekly_budget.BalanceRow`). */
export type BalanceRow = {
  field: string;
  target: number | null;
  intake: number | null;
  intake_complete: boolean;
  remaining: number | null;
  share: number | null;
};

export type DayBalance = {
  rows: BalanceRow[];
  tdee_kcal: number | null;
  /** Ingestão − gasto do dia (negativo = déficit); `null` sem gasto ou com ingestão incompleta. */
  energy_balance_kcal: number | null;
  limitations: NutritionLimitation[];
};

/** kcal do treino numa natureza (`energy_expenditure.TrainingKcal`). */
export type TrainingKcalValue = {
  kcal: number | null;
  /** Código de `TRAINING_KCAL_NATURE_OPTIONS` (reported, met). */
  kcal_nature: NutritionCode | null;
  nature: string | null;
  quality: string | null;
  limitations: string[];
};

export type TrainingSession = {
  id: number;
  name: string | null;
  calories_kcal: number | null;
  /** Proveniência (dono `domain/calories_source`): manual | intervals | null. */
  calories_source: string | null;
  calories_source_label: string | null;
  duration_seconds: number | null;
};

/** Uma natureza da kcal do treino na ordem da prioridade do motor, com o valor e se é a usada. */
export type TrainingKcalSide = { kcal_nature: NutritionCode; value: TrainingKcalValue; is_used: boolean };

export type DayTraining = {
  is_training_day: boolean;
  training_known: boolean;
  sessions: TrainingSession[];
  /** Informada × estimada por MET e a que o motor usa (`sides` já na ordem da prioridade). */
  comparison: {
    reported: TrainingKcalValue;
    estimated: TrainingKcalValue;
    used: TrainingKcalValue;
    sides: TrainingKcalSide[];
  } | null;
};

export type DayLog = { water_ml: number | null; hunger: number | null; notes: string | null; updated_at: string | null };

/** Decisão do motor sobre a meta do dia (`daily_target.TargetAction`). */
export type DayTargetAction = 'read' | 'materialize' | 'backfill' | 'skip';

/** `GET /nutrition/days/{date}`. */
export type NutritionDay = {
  user: NutritionPlanUser;
  local_date: string;
  today: string;
  target: NutritionDayTarget | null;
  target_action: DayTargetAction;
  target_notices: NutritionLimitation[];
  meals: Meal[];
  totals: NutritionTotals | null;
  balance: DayBalance;
  day_log: DayLog | null;
  training: DayTraining;
};

/** Corpo de `PUT /nutrition/days/{date}` (`NutritionDayIn`): substitui a linha do dia. */
export type DayLogPayload = { water_ml: number | null; hunger: number | null; notes: string | null };

export type DayLogResponse = { user_id: number; local_date: string; day_log: DayLog | null };

export type WeekDay = {
  local_date: string;
  target: NutritionDayTarget | null;
  target_action: DayTargetAction;
  totals: NutritionTotals | null;
  /** Dia com refeições de total completo (entra no banco e na aderência). */
  logged: boolean;
  /** Dia com refeição de total incompleto (fica fora do banco e da aderência). */
  intake_incomplete: boolean;
};

export type WeekReport = {
  days: string[];
  budget: { kcal: number | null; protein_g: number | null; carb_g: number | null; fat_g: number | null; days_with_target: number };
  bank: {
    balance_kcal: number | null;
    carryover_kcal: number | null;
    max_daily_carryover_kcal: number | null;
    max_weekly_bank_kcal: number | null;
    logged_days: number;
  };
  adherence: {
    kcal_share: number | null;
    protein_share: number | null;
    logged_days: number;
    valid_week: boolean;
    kcal_within_tolerance: boolean | null;
    protein_ok: boolean | null;
  };
  logged_days: string[];
  incomplete_days: string[];
  limitations: NutritionLimitation[];
};

/** `GET /nutrition/weeks`. */
export type NutritionWeek = {
  user: NutritionPlanUser;
  today: string;
  start: string;
  end: string;
  days: WeekDay[];
  report: WeekReport;
};

// ---------------------------------------------------------------------------
// Foto do rótulo (Lote L6): `api/app/nutrition_label_photo.py` (upload, 202) e o
// resultado do job (`nutrition_label_ai.job_result`). Leitura, revisão (Atwater,
// porção × por 100, kJ -> kcal) e avisos são da API e do motor
// (`domain/nutrition/label_photo`); aqui só o formato.
// ---------------------------------------------------------------------------

/** Corpo de `POST /nutrition/foods` (`FoodItemCreateIn`): o de `FoodItemPayload` + a fonte do cadastro. */
export type FoodItemCreatePayload = FoodItemPayload & { source?: NutritionCode | null };

/** Corpo de `POST /nutrition/foods/label-photo` (`LabelPhotoUploadIn`): a foto reduzida em base64, sem padding. */
export type LabelPhotoUploadPayload = { image_base64: string };

/** `POST /nutrition/foods/label-photo` (202): o job e o prazo do polling (o do worker perdido, em segundos). */
export type LabelPhotoDispatch = { job_id: string; status: string; poll_timeout_seconds: number | null };

/** Resultado do job da leitura do rótulo: formulário pré-preenchido + o que o usuário precisa conferir. */
export type LabelPhotoReading = {
  prompt_version: string;
  model: string;
  /** Código de `LABEL_LEGIBILITY_OPTIONS`. */
  legibility: NutritionCode;
  readable: boolean;
  barcode: string | null;
  /** Campos do cadastro (`FoodItemIn`), por 100 g/ml com os códigos da RDC 429; `null` = não lido. */
  food: FoodItemPayload;
  /** Valores da coluna da porção, como lidos. */
  per_serving: FoodPer100;
  /** Bloqueiam o cadastro até o usuário corrigir (regras do catálogo). */
  errors: NutritionLimitation[];
  /** Conferir antes de salvar (Atwater, porção × por 100, kJ, valor derivado da porção...). */
  warnings: NutritionLimitation[];
  /** Observações livres da IA sobre o que não deu para ler. */
  ai_notes: string[];
};

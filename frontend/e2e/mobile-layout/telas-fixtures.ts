import type { Page, Route } from '@playwright/test';
import { MOCK_API_ORIGIN } from '../desktop-baseline/mock-api';

/**
 * Dados realistas para as telas da frente C (sem alterar `mock-api.ts`): as
 * rotas daqui são registradas DEPOIS do mock base, então têm prioridade; o que
 * não está aqui cai (`route.fallback()`) no mock base.
 */
const sets = (rows: Array<Record<string, unknown>>) =>
  rows.map((r, i) => ({ id: i + 1, set_number: i + 1, set_type: 'working', ...r }));

const ACTIVITY_LIST = {
  items: [
    { id: 1, local_date: '2026-10-13', activity_type: 'strength', name: 'Treino A - Peito e tríceps', gym_name: 'Academia Central', conducted_by: 'dougfitness', session_status: 'done', workout_plan_name: 'Ficha A', total_volume_kg: 8420.5, avg_rpe: 8.2 },
    { id: 2, local_date: '2026-10-11', activity_type: 'strength', name: 'Treino B - Costas e bíceps', gym_name: 'Academia Central', conducted_by: 'personal', session_status: 'done', workout_plan_name: 'Ficha B', total_volume_kg: 7310, avg_rpe: 8 },
    { id: 3, local_date: '2026-10-09', activity_type: 'strength', name: 'Treino C - Pernas', gym_name: 'Academia Central', conducted_by: 'dougfitness', session_status: 'in_progress', workout_plan_name: null, total_volume_kg: 5120, avg_rpe: 7.5 },
  ],
};

const ACTIVITY_DETAIL = {
  ...ACTIVITY_LIST.items[0],
  id: 1,
  started_at: '2026-10-13T18:30:00',
  gym_id: 1,
  conducted_by: 'dougfitness',
  workout_plan_id: 1,
  calories_kcal: 412.37,
  calories_source_label: 'Intervals.icu',
  ai_summary: 'Boa sessão de empurrar, com progressão de carga no supino.',
  ai_summary_generated_at: '2026-10-13T20:00:00',
  exercises: [
    {
      id: 11, activity_id: 1, sequence: 1, original_name: 'Supino reto com barra', mapped_name: 'Supino reto', mapping_id: 1, muscle_primary: 'Peitoral',
      equipment_name: 'Banco reto', workout_plan_exercise_id: 101, planned_mapping_id: 1,
      sets: sets([
        { reps: 10, weight_kg: 80, rpe: 8 },
        { reps: 9, weight_kg: 80, rpe: 8.5 },
        { reps: 8, weight_kg: 80, rpe: 9 },
      ]),
    },
    {
      id: 12, activity_id: 1, sequence: 2, original_name: 'Rosca unilateral com halter', mapped_name: 'Rosca direta', mapping_id: 2, muscle_primary: 'Bíceps',
      equipment_name: 'Halteres', workout_plan_exercise_id: 102, planned_mapping_id: 3, planned_exercise_name: 'Rosca martelo',
      sets: sets([
        { reps: 10, weight_kg: 16, rpe: 8, reps_left: 10, reps_right: 9, rpe_left: 8, rpe_right: 8.5, sides_text: 'reps E 10 · D 9' },
        { reps: 9, weight_kg: 16, rpe: 9, reps_left: 10, reps_right: 9, rpe_left: 8.5, rpe_right: 9, sides_text: 'reps E 10 · D 9' },
      ]),
    },
  ],
};

const PLAN_EXERCISES = [
  { workout_plan_exercise_id: 101, sequence: 1, exercise_name: 'Supino reto com barra', mapping_id: 1, muscle_primary: 'Peitoral', equipment_name: 'Banco reto', sets_count: 4, reps_target: '8-10', load_target: '80 kg', rpe_target: '8', rest_seconds: 120, warmup: false, progression: 'increased', progression_note: 'Carga subiu em relação à sessão anterior.' },
  { workout_plan_exercise_id: 102, sequence: 2, exercise_name: 'Crucifixo inclinado', mapping_id: 4, muscle_primary: 'Peitoral', equipment_name: 'Halteres', sets_count: 3, reps_target: '10-12', load_target: '18 kg', rpe_target: '8', rest_seconds: 90, warmup: false, advanced_technique: null },
  { workout_plan_exercise_id: 103, sequence: 3, exercise_name: 'Tríceps na polia', mapping_id: 5, muscle_primary: 'Tríceps', equipment_name: 'Polia alta', sets_count: 3, reps_target: '10-15', load_target: '30 kg', rpe_target: '8', rest_seconds: 75, warmup: false },
];

const PLAN_LIST = {
  items: [
    { id: 1, name: 'Ficha A - Peito e tríceps', plan_date: '2026-10-14', plan_time: '18:30', gym_id: 1, gym_name: 'Academia Central', plan_type: 'abc', split_day: 'A', template_id: 7, template_name: 'ABC Hipertrofia', goal: 'Hipertrofia de peito', status: 'planned', exercises_count: 3, created_at: '2026-10-10T10:00:00', updated_at: '2026-10-10T10:00:00' },
    { id: 2, name: 'Ficha B - Costas e bíceps', plan_date: '2026-10-11', plan_time: null, gym_id: 1, gym_name: 'Academia Central', plan_type: 'abc', split_day: 'B', template_id: 7, template_name: 'ABC Hipertrofia', goal: null, status: 'completed', exercises_count: 4, created_at: '2026-10-08T10:00:00', updated_at: '2026-10-11T10:00:00' },
  ],
};

const PLAN_DETAIL = { ...PLAN_LIST.items[0], template_slot_sequence: 1, editable: true, raw_payload: null, exercises: PLAN_EXERCISES };

const FOOD = (id: number, name: string) => ({
  id, source: 'taco', source_id: String(id), source_version: '4', barcode: null, name, brand: null, category: 'Cereais', basis: 'g',
  per_100: { energy_kcal: 128, protein_g: 2.5, carbohydrate_g: 28.1, lipid_g: 0.2 }, serving: { serving_size: null, household_measure: null, servings_per_package: null },
  license: null, citation: 'TACO 4ª ed.', field_sources: [], is_public: true, is_own: false, is_active: true,
  permissions: { can_edit: false, can_add_portion: false, can_promote: false }, created_at: null, updated_at: null,
  score: 0.9, auto_accept: true, is_favorite: id === 1,
});

const USER = { id: 1, username: 'ana', full_name: 'Ana Teste' };
const NUTRITION_DAY = {
  user: USER, local_date: '2026-10-14', today: '2026-10-14', target: null, target_action: 'skip', target_notices: [], meals: [], totals: null,
  balance: { rows: [], tdee_kcal: null, energy_balance_kcal: null, limitations: [] }, day_log: null,
  training: { is_training_day: false, training_known: true, sessions: [], comparison: null },
};
const NUTRITION_WEEK = {
  user: USER, today: '2026-10-14', start: '2026-10-12', end: '2026-10-18',
  days: ['12', '13', '14', '15', '16', '17', '18'].map((d) => ({ local_date: `2026-10-${d}`, target: null, target_action: 'skip', totals: null, intake_kcal: null, logged: false, intake_incomplete: false })),
  report: { days: [], budget: { kcal: null, protein_g: null, carb_g: null, fat_g: null, days_with_target: 0 }, bank: { balance_kcal: null, carryover_kcal: null, max_daily_carryover_kcal: null, max_weekly_bank_kcal: null, logged_days: 0 },
    adherence: { kcal_share: null, protein_share: null, logged_days: 0, valid_week: false, kcal_within_tolerance: null, protein_ok: null }, logged_days: [], incomplete_days: [], limitations: [] },
};

const NUTRIENTS = { energy_kcal: 128, protein_g: 2.5, carbohydrate_g: 28.1, lipid_g: 0.2 };
const MEAL_ITEM = {
  id: 1, position: 1, food_id: 1, portion_id: null, portion_label: null, label: 'Arroz, integral, cozido', quantity: 150, unit: 'g', amount: 150, basis: 'g',
  nutrients: NUTRIENTS, nature: 'DERIVED', limitations: [], food_source: 'taco', citation: 'TACO 4ª ed.', field_sources: [],
};
const MEAL_TOTALS = { nutrients: NUTRIENTS, incomplete_fields: [], complete: true, nature: 'DERIVED', item_count: 1, limitations: [] };
const MEAL = {
  id: 5, user_id: 1, local_date: '2026-10-14', meal_slot: 'lunch', eaten_at: '12:30', source: 'manual', notes: null,
  totals: MEAL_TOTALS, items: [MEAL_ITEM], engine_version: 'x', created_at: null, updated_at: null,
};
const FOOD_DETAIL = {
  ...FOOD(1, 'Arroz, integral, cozido'),
  portions: [{ id: 1, food_id: 1, label: '1 colher de servir', amount: 60, source: 'taco', is_public: true, is_own: false }],
  permissions: { can_edit: false, can_add_portion: true, can_promote: false },
};
const CHECKIN = {
  id: 9, user_id: 1, week_start: '2026-10-05', week_end: '2026-10-11', due_date: '2026-10-12', status: 'proposed', status_label: 'Aguardando sua resposta',
  verdict: 'slow', verdict_label: 'Abaixo do ritmo', can_respond: true, show_card: true, expires_at: null, expires_on: '2026-10-19', adjustment_kcal: 100,
  missing_items: [], reason_items: [{ code: 'slow', label: 'O peso variou menos do que o esperado para a semana.' }],
  proposal: {
    before: { kcal: 2200, protein_g: 160, carb_g: 240, fat_g: 70 }, after: { kcal: 2300, protein_g: 160, carb_g: 265, fat_g: 70 }, delta_kcal: 100, changes: {},
    change_items: [{ field: 'maintenance_kcal_adjust', label: 'Ajuste da manutenção', unit: 'kcal', before: 0, after: 100 }], scope: 'rest_day', scope_label: 'Dia sem treino',
  },
  engine_version: 'x', source_plan_id: 1, generated_plan_id: null, responded_at: null, created_at: null,
  summary: {
    complete_days: 6, days_with_target: 7, mean_intake_kcal: 2150, mean_target_kcal: 2200, mean_tdee_kcal: 2500, planned_balance_kcal: -300, kcal_share: 0.98, protein_share: 1,
    adherence_valid: true, kcal_within_tolerance: true, weight_week_mean_kg: 82.4, weight_previous_week_mean_kg: 82.5, weight_weekly_change_kg: -0.1, weight_weekly_change_share: -0.001,
    expected_weekly_change_kg: -0.4, maintenance_band_kg: 0.2, body_fat_percent: 17.8, muscle_mass_kg: 36.2,
    weight_trend: { weekly_change: -0.1, valid_for_bia: false }, lean_trend: { weekly_change: null, valid_for_bia: false }, fat_trend: { weekly_change: null, valid_for_bia: false },
    observed_tdee: { kcal: 2480, quality: 'ok' },
  },
  goal: { goal_weight_kg: 78, goal_body_fat_percent: 15 },
};
const GROUP = (key: string, label: string) => ({ key, label, order: 0 });
const SLOT = { template_id: 7, slot_sequence: 1, slot_id: 1, slot_name: 'A', template_name: 'ABC Hipertrofia', label: 'A - ABC Hipertrofia' };
const VOL_ROW = (key: string, label: string) => ({
  group: GROUP(key, label), comparison_status: 'same_slot', slot: SLOT,
  by_period: {
    t3: { activity_id: 1, local_date: '2026-09-22', volume_kg: 3000, avg_rpe: 7.5, sets_count: 9 },
    t2: { activity_id: 2, local_date: '2026-09-29', volume_kg: 3200, avg_rpe: 8, sets_count: 9 },
    t1: { activity_id: 3, local_date: '2026-10-06', volume_kg: 3100, avg_rpe: 8, sets_count: 9 },
    t0: { activity_id: 4, local_date: '2026-10-13', volume_kg: 3400, avg_rpe: 8.5, sets_count: 9 },
  },
});
const SETS_ROW = (key: string, label: string) => ({
  group: GROUP(key, label), comparison_status: 'same_slot', slot: SLOT,
  by_period: {
    t3: { activity_id: 1, local_date: '2026-09-22', equivalent_sets: 9 },
    t2: { activity_id: 2, local_date: '2026-09-29', equivalent_sets: 9 },
    t1: { activity_id: 3, local_date: '2026-10-06', equivalent_sets: 10 },
    t0: { activity_id: 4, local_date: '2026-10-13', equivalent_sets: 11 },
  },
});
const LEVEL = { volume: [VOL_ROW('peito', 'Peito'), VOL_ROW('costas', 'Costas')], equivalent_sets: [SETS_ROW('peito', 'Peito'), SETS_ROW('costas', 'Costas')] };
const NUTRITION_PLAN = {
  id: 1, user_id: 1, status: 'published', state: 'current', notes: null, engine_version: 'x', effective_from: '2026-10-01', superseded_at: null, published_at: '2026-10-01T10:00:00',
  created_by: 1, published_by: 1, created_at: null, updated_at: '2026-10-01T10:00:00', strategy: 'maintenance', advanced: false, protein_g_per_kg: null, fat_kcal_share: 0.25,
  weekly_rate_share: null, surplus_share: null, carb_min_g_per_kg: null, bmr_formula: 'mifflin', tdee_mode: 'activity_factor', activity_level: null, meals_per_day: 4,
  goal_body_fat_percent: null, goal_weight_kg: null, carb_cycling_enabled: false, macro_priority: 'protein', manual_targets: null, cycle_days: null, maintenance_kcal_adjust: 0,
};

const FIXTURES: Record<string, unknown> = {
  'GET /training-goals': { default: 'hypertrophy', items: [] },
  'GET /analytics/muscle-groups-recent-exercise-volume': { items: [] },
  'GET /analytics/muscle-groups-slot-comparison': { limit: 400, sessions_per_slot: 4, period_keys: ['t3', 't2', 't1', 't0'], levels: { volume_class: LEVEL, parent_group: LEVEL, muscle_group: LEVEL } },
  'GET /analytics/training-volume': { overview: { days: 14, items: [] }, trend: { days: 14, items: [] } },
  'GET /nutrition/plans': { user: USER, items: [NUTRITION_PLAN] },
  'GET /nutrition/me/plan': { __status: 404, body: { detail: { error: 'no_published_plan', message: 'Você ainda não tem plano publicado.' } } },
  'GET /nutrition/foods/1': FOOD_DETAIL,
  'GET /nutrition/foods/usda/search': {
    query: 'rice', total_hits: 1,
    items: [{ fdc_id: 1, name: 'Rice, white, cooked', data_type: 'foundation', category: 'Cereal Grains', basis: 'g', per_100: { energy_kcal: 130, protein_g: 2.7, carbohydrate_g: 28, lipid_g: 0.3 }, energy_origin: null }],
  },
  'POST /nutrition/meals/preview': { items: [MEAL_ITEM], totals: MEAL_TOTALS, warnings: [] },
  'POST /nutrition/meals': MEAL,
  'POST /workout-sessions/1/exercises': { exercise_id: 21 },
  'POST /workout-sessions/1/exercises/21/sets': { ok: true },
  'DELETE /users/2': { status: 'ok', id: 2, is_active: false },

  'GET /activities': ACTIVITY_LIST,
  'GET /activities/1': ACTIVITY_DETAIL,
  'GET /analytics/muscle-activation-by-activity': {
    items: [
      { muscle_group: 'Peitoral', primary_series: 3, primary_equivalent: 3, secondary_series: 0, secondary_equivalent: 0, stabilizer_series: 0, stabilizer_equivalent: 0, total_equivalent: 3 },
      { muscle_group: 'Bíceps', primary_series: 2, primary_equivalent: 2, secondary_series: 0, secondary_equivalent: 0, stabilizer_series: 0, stabilizer_equivalent: 0, total_equivalent: 2 },
    ],
  },
  'GET /workout-plans': PLAN_LIST,
  'GET /workout-plans/1': PLAN_DETAIL,
  'GET /workout-plans/1/exercise-swap-options': { plan_id: 1, editable: true, rows: [] },
  'GET /workout-plan-templates/assigned-to-me': { items: [{ id: 7, name: 'ABC Hipertrofia', slots: [{ sequence: 1, name: 'A' }] }] },
  'GET /mapping-canonical/options': { items: [{ id: 1, exercise_name: 'Supino reto com barra', amazfit_name: 'Bench press', primary_group: 'Peitoral' }] },
  'GET /exercise-mapping-catalog': { items: [{ id: 1, exercise_name: 'Supino reto com barra', amazfit_name: 'Bench press', primary_group: 'Peitoral', secondary_group: 'Tríceps' }] },
  'GET /exercise-mapping-catalog/groups': { items: [{ primary_group: 'Peitoral' }, { primary_group: 'Bíceps' }] },
  'GET /gym-equipment': { items: [{ id: 1, equipment_id: 1, equipment_name: 'Banco reto', mapping_exercise_name: 'Supino reto com barra', photo_url: null }] },
  'GET /cardio-recommendations': { items: [] },
  'POST /analytics/muscle-activation-breakdown': { items: [] },
  'POST /analytics/muscle-activation-preview': { items: [] },
  'GET /nutrition/weeks': NUTRITION_WEEK,
  'GET /nutrition/days/2026-10-14': NUTRITION_DAY,
  'GET /nutrition/foods/search': { query: 'arroz', normalized: 'arroz', items: [FOOD(1, 'Arroz, integral, cozido'), FOOD(2, 'Arroz, tipo 1, cozido')] },
  'GET /nutrition/favorites': { items: [] },
  'GET /advanced-techniques': { items: [] },
  'GET /nutrition/checkin': {
    user: USER, today: '2026-10-14', settings: { weekday: 0, weekday_label: 'Segunda-feira', is_default: true }, weekday_options: [],
    due_date: '2026-10-19', week_start: '2026-10-12', week_end: '2026-10-18', checkin: null, none_items: [],
  },
  'GET /nutrition/foods/label-photo/usage': { feature: 'label_photo', limit: 5, used: 0, remaining: 5, allowed: true },
  'PUT /exercises/logs/11': { ok: true },
  'PUT /workout-plans/1': PLAN_DETAIL,
  'GET /users': {
    items: [
      { id: 1, username: 'ana', full_name: 'Ana Teste', email: 'ana@example.com', role: 'admin', is_active: true, has_photo: false, birth_date: null, sex: null },
      { id: 2, username: 'bia', full_name: 'Bia Souza', email: 'bia@example.com', role: 'regular', is_active: true, has_photo: false, birth_date: null, sex: null },
    ],
  },
  'POST /users/2/reset-password': { temporary_password: 'Tmp-Abc12345', revoked_sessions: 1 },
  'GET /body-composition': {
    items: [
      { id: 1, local_date: '2026-10-12', measured_at: null, weight_kg: 82.4, bmi: 25.1, body_fat_percent: 17.8, muscle_mass_kg: 36.2, body_water_percent: 56.1 },
      { id: 2, local_date: '2026-09-28', measured_at: null, weight_kg: 83.1, bmi: 25.3, body_fat_percent: 18.4, muscle_mass_kg: 35.9, body_water_percent: 55.7 },
    ],
  },
};

/** Registra as fixtures da frente C por cima do mock base. */
export async function installTelasFixtures(page: Page) {
  await page.route(`${MOCK_API_ORIGIN}/**`, async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.pathname.endsWith('/muscle-map.png')) {
      await route.fulfill({
        status: 200,
        headers: { 'access-control-allow-origin': req.headers()['origin'] ?? '*', 'access-control-allow-credentials': 'true' },
        contentType: 'image/png',
        body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64'),
      });
      return;
    }
    const fixture = FIXTURES[`${req.method()} ${url.pathname}`];
    if (fixture === undefined || req.method() === 'OPTIONS') {
      await route.fallback();
      return;
    }
    const origin = req.headers()['origin'] ?? '*';
    const withStatus = fixture as { __status?: number; body?: unknown };
    await route.fulfill({
      status: withStatus.__status ?? 200,
      headers: {
        'access-control-allow-origin': origin,
        'access-control-allow-credentials': 'true',
        'access-control-allow-headers': '*',
        'access-control-allow-methods': '*',
      },
      contentType: 'application/json',
      body: JSON.stringify(withStatus.__status ? withStatus.body : fixture),
    });
  });
}

/** `/auth/me` como admin (a sessão base do mock é de usuário comum). */
export async function installAdminSession(page: Page) {
  await page.route(`${MOCK_API_ORIGIN}/auth/me`, async (route: Route) => {
    const origin = route.request().headers()['origin'] ?? '*';
    if (route.request().method() === 'OPTIONS') {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      headers: { 'access-control-allow-origin': origin, 'access-control-allow-credentials': 'true' },
      contentType: 'application/json',
      body: JSON.stringify({ id: 1, username: 'ana', full_name: 'Ana Teste', role: 'admin', must_change_password: false, intervals_configured: false, timezone: 'America/Sao_Paulo', features: { exercise_cards: false } }),
    });
  });
}

export async function installCheckinProposal(page: Page) {
  await page.route(`${MOCK_API_ORIGIN}/nutrition/checkin`, async (route: Route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      headers: { 'access-control-allow-origin': route.request().headers()['origin'] ?? '*', 'access-control-allow-credentials': 'true' },
      contentType: 'application/json',
      body: JSON.stringify({
        user: USER, today: '2026-10-14', settings: { weekday: 0, weekday_label: 'Segunda-feira', is_default: true }, weekday_options: [],
        due_date: '2026-10-19', week_start: '2026-10-12', week_end: '2026-10-18', checkin: CHECKIN, none_items: [],
      }),
    });
  });
}

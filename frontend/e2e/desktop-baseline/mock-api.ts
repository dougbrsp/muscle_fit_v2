import type { Page, Route } from '@playwright/test';

/**
 * API 100% mockada do baseline desktop. As fixtures sao DETERMINISTICAS e so
 * existem para o app renderizar estados estaveis: nao replicam regra de
 * negocio (os valores sao arbitrarios; o teste compara pixels, nao numeros).
 *
 * Qualquer chamada a um caminho nao mapeado FALHA o teste (ver
 * `installMockApi().unhandled`), para uma rota nova nao passar a depender de
 * rede real sem ninguem perceber.
 */
export const MOCK_API_ORIGIN = 'http://mock-api.test';

/** Relogio congelado de todos os testes (meio-dia no fuso de Sao Paulo). */
export const FROZEN_NOW = new Date('2026-10-14T15:00:00.000Z');

type Json = unknown;

const QUALITY = 'HIGH';
const metric = (value: number | null, unit: string | null = null) => ({
  value,
  unit,
  nature: 'MEASURED',
  metric_version: 'e2e-fixture',
  data_quality: QUALITY,
  limitations: [] as string[],
});

const WINDOW = {
  kind: '28d',
  date_from: '2026-09-17',
  date_to: '2026-10-14',
  timezone: 'America/Sao_Paulo',
  days: 28,
};

const blockBase = (question: string, answer: string, href: string) => ({
  question,
  answer,
  drilldown_href: href,
  data_quality: QUALITY,
  limitations: [] as string[],
});

export const SESSION_USER = {
  id: 1,
  username: 'ana',
  full_name: 'Ana Teste',
  role: 'user',
  must_change_password: false,
  intervals_configured: false,
  timezone: 'America/Sao_Paulo',
  features: { exercise_cards: false },
};

const overview = {
  window: WINDOW,
  timezone: 'America/Sao_Paulo',
  blocks: {
    consistency: {
      ...blockBase('Estou treinando como planejei?', 'Voce concluiu 10 de 12 treinos esperados.', '/dashboard/treinos'),
      has_template: true,
      template_name: 'Modelo ABC',
      cycle_length_days: 7,
      next_slot: null,
      cycle_slots_done: 2,
      cycle_slots_total: 3,
      expected_sessions: metric(12),
      completed_sessions: metric(10),
      planned_sessions: metric(9),
      unplanned_sessions: metric(1),
      missed_sessions: metric(2),
      rate: metric(0.83, 'ratio'),
      cycle_rate: metric(0.67, 'ratio'),
    },
    progression: {
      ...blockBase('Estou progredindo?', 'Cinco exercicios com comparacao valida.', '/dashboard/progressao'),
      exercises_total: 8,
      comparable_total: 5,
      counts: [],
      exposures_per_exercise: 3,
      truncated: false,
    },
    priority_muscles: {
      ...blockBase('Quais musculos priorizar?', 'Nenhum grupo abaixo da faixa.', '/dashboard/musculos'),
      groups_total: 12,
      groups_with_target: 12,
      target_source: 'DEFAULT',
      below_min: [],
      above_max: [],
      frequency_lagging: [],
    },
    body: {
      ...blockBase('Como esta meu corpo?', 'Peso estavel.', '/dashboard/corpo'),
      weight_latest: metric(78.4, 'kg'),
      weight_ma7_latest: metric(78.6, 'kg'),
      weight_ma7_change: metric(-0.2, 'kg'),
      weight_ma7_change_pct: metric(-0.3, '%'),
      weighings_count: 12,
      weighings_per_week: metric(3),
      weight_points: [],
      body_fat_percent: metric(null, '%'),
      muscle_mass_kg: metric(null, 'kg'),
      bioimpedance_local_date: null,
      waist_cm: metric(null, 'cm'),
    },
    recovery: {
      ...blockBase('Estou recuperado?', 'Recuperacao adequada.', '/dashboard/recovery'),
      window: WINDOW,
      level: 'INFO',
      readiness_reading: 'READY',
      headline: 'Pronto para treinar',
      uncertainty: QUALITY,
      allow_intensification: true,
      intensification_blocked_reason: null,
      groups_total: 12,
      groups_ready: 10,
      groups_reduced_dose: 2,
      groups_no_direct_work: 0,
      signals_agreeing: 3,
      signals_diverging: 0,
      signals_unavailable: 1,
      reassess_after_hours: 24,
    },
    next_actions: {
      ...blockBase('O que fazer a seguir?', 'Nenhuma acao pendente.', '/dashboard/insights'),
      items: [],
      total_triggered: 0,
      max_items: 3,
    },
  },
  data_quality: QUALITY,
  data_quality_overall: QUALITY,
  data_quality_issues: 0,
  target_source: 'DEFAULT',
  training_type_id: null,
  limitations: [] as string[],
  metric_versions: {} as Record<string, string>,
};

const counts = { completed: 5, interrupted: 0, skipped: 0, substituted: 0, extra: 0 };
const workoutSession = (id: number, date: string, name: string, slotSeq: number) => ({
  activity_id: id,
  local_date: date,
  started_at: `${date}T18:00:00`,
  name,
  session_status: 'DONE',
  duration_seconds: 3600,
  exercises_count: 5,
  physical_sets: metric(15, 'sets'),
  effective_sets_stimulus: metric(14, 'sets'),
  effective_sets_fatigue: metric(15, 'sets'),
  avg_rpe: metric(8, 'rpe'),
  records: metric(1),
  progressions: metric(2),
  item_status_counts: counts,
  plan_link: null,
  group: {
    key: `slot-${slotSeq}`,
    kind: 'slot',
    label: `Treino ${'ABC'[slotSeq - 1]}`,
    template_id: 1,
    template_name: 'Modelo ABC',
    lineage_template_id: 1,
    slot_sequence: slotSeq,
  },
  data_quality: QUALITY,
  limitations: [] as string[],
});

const workouts = {
  window: WINDOW,
  items: [
    workoutSession(103, '2026-10-13', 'Costas e biceps', 2),
    workoutSession(102, '2026-10-11', 'Peito e triceps', 1),
    workoutSession(101, '2026-10-09', 'Pernas', 3),
  ],
  total: 3,
  limit: 20,
  offset: 0,
  has_more: false,
  data_quality: QUALITY,
  limitations: [] as string[],
  metric_versions: {} as Record<string, string>,
};

const slot = (sequence: number, name: string, status: string) => ({
  sequence,
  name,
  slot_type: 'strength',
  weekday: null,
  status,
  last_activity_id: null,
  last_local_date: null,
});

const adherence = {
  window: WINDOW,
  has_template: true,
  template_id: 1,
  template_name: 'Modelo ABC',
  cycle_length_days: 7,
  slots: [slot(1, 'Treino A', 'done'), slot(2, 'Treino B', 'done'), slot(3, 'Treino C', 'next')],
  next_slot: slot(3, 'Treino C', 'next'),
  expected_sessions: metric(12),
  completed_sessions: metric(10),
  planned_sessions: metric(9),
  unplanned_sessions: metric(1),
  missed_sessions: metric(2),
  rate: metric(0.83, 'ratio'),
  cycle_rate: metric(0.67, 'ratio'),
  data_quality: QUALITY,
  limitations: [] as string[],
  metric_versions: {} as Record<string, string>,
};

const trainingEvolution = {
  days: 7,
  weekly_summary: {
    completed_workouts: 3,
    planned_workouts: 3,
    total_sets: 45,
    total_volume_kg: 12000,
    avg_rpe: 8,
    high_rpe_sets: 6,
    latest_workouts: [],
    next_workout: null,
  },
  volume_overview: [],
  recent_records: [],
  exercise_progression: [],
  recovery: [],
  intervals_correlation: { available: false, items: [] },
  weekly_trend: [],
  weekly_trend_by_muscle_group: [],
  sessions: [],
};

const trend = {
  period: 'week',
  days: 28,
  items: [
    ['2026-09-21', '2026-09-27'],
    ['2026-09-28', '2026-10-04'],
    ['2026-10-05', '2026-10-11'],
    ['2026-10-12', '2026-10-18'],
  ].map(([start, end], i) => ({
    period: `2026-W${39 + i}`,
    period_start: start,
    period_end: end,
    workouts: 3,
    sets_count: 40 + i,
    reps_total: 400 + i * 10,
    volume_kg: 11000 + i * 500,
    avg_rpe: 8,
    high_rpe_sets: 5,
  })),
};

const profile = {
  item: {
    id: 1,
    username: 'ana',
    full_name: 'Ana Teste',
    email: 'ana@example.test',
    birth_date: '1990-05-20',
    sex: 'female',
    height_cm: 168,
    activity_level: 'moderate',
    max_hr_bpm_manual: null,
    role: 'user',
    is_active: true,
    intervals_athlete_id: null,
    preferred_weight_unit: 'kg',
    timezone: 'America/Sao_Paulo',
    has_photo: false,
    intervals_configured: false,
  },
};

const plans = {
  items: [
    { id: 11, name: 'Treino A - Peito e triceps', plan_date: '2026-10-14', status: 'planned' },
    { id: 12, name: 'Treino B - Costas e biceps', plan_date: '2026-10-11', status: 'completed' },
  ],
};

const exerciseProgress = {
  name: 'Puxada',
  days: 1095,
  items: [
    ['2026-09-20', 60],
    ['2026-09-30', 62.5],
    ['2026-10-09', 65],
  ].map(([date, kg]) => ({
    local_date: date,
    activity_name: 'Costas e biceps',
    original_name: 'Puxada',
    mapped_name: 'Puxada',
    sets_count: 4,
    reps_total: 40,
    best_weight_kg: kg,
    volume_kg: Number(kg) * 40,
    avg_rpe: 8,
    high_rpe_sets: 1,
  })),
};

const deloadCurrent = {
  active: false,
  source: null,
  period: null,
  today: '2026-10-14',
  constants: {
    load_factor: 0.6,
    target_rpe: 6,
    sets_reduction_fraction: 0.5,
    min_sets: 2,
    period_default_days: 7,
    period_max_days: 14,
  },
};

/** Mapa `METODO caminho` -> fixture. A query string e ignorada na chave. */
const ROUTES: Record<string, Json> = {
  'GET /auth/me': SESSION_USER,
  'GET /analytics/dashboard/overview': overview,
  'GET /analytics/dashboard/workouts': workouts,
  'GET /analytics/dashboard/adherence': adherence,
  'GET /analytics/training-evolution': trainingEvolution,
  'GET /analytics/training-trend': trend,
  'GET /analytics/rpe-distribution': {
    days: 28,
    items: [7, 8, 9, 10].map((rpe, i) => ({ rpe, sets_count: 20 - i * 4 })),
  },
  'GET /analytics/exercise-progress': exerciseProgress,
  'GET /analytics/dashboard/strength-panel': {
    window: WINDOW,
    training_goal: 'hypertrophy',
    training_goal_label: 'Hipertrofia',
    max_exercises: 6,
    lifts: [],
    data_quality: QUALITY,
    limitations: [],
    metric_versions: {},
  },
  'GET /devices': { items: [] },
  'GET /workout-plans': plans,
  'GET /users/me': profile,
  'GET /gyms': { items: [{ id: 1, name: 'Academia Central', is_active: true }] },
  'GET /deload-periods/current': deloadCurrent,
  'GET /activities': { items: [] },
};

export type MockApiOptions = {
  /** `/auth/me` responde 401: tela de login. */
  anonymous?: boolean;
};

export async function installMockApi(page: Page, options: MockApiOptions = {}) {
  const unhandled: string[] = [];

  await page.route(`${MOCK_API_ORIGIN}/**`, async (route: Route) => {
    const req = route.request();
    const origin = req.headers()['origin'] ?? '*';
    const cors = {
      'access-control-allow-origin': origin,
      'access-control-allow-credentials': 'true',
      'access-control-allow-headers': '*',
      'access-control-allow-methods': '*',
    };
    if (req.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    const url = new URL(req.url());
    const key = `${req.method()} ${url.pathname}`;

    if (key === 'GET /auth/me' && options.anonymous) {
      await route.fulfill({
        status: 401,
        headers: cors,
        contentType: 'application/json',
        body: '{"detail":"Nao autenticado"}',
      });
      return;
    }
    const fixture = ROUTES[key];
    if (fixture === undefined) {
      unhandled.push(key);
      await route.fulfill({
        status: 599,
        headers: cors,
        contentType: 'application/json',
        body: '{"detail":"e2e: rota nao mockada"}',
      });
      return;
    }
    await route.fulfill({
      status: 200,
      headers: cors,
      contentType: 'application/json',
      body: JSON.stringify(fixture),
    });
  });

  return { unhandled };
}

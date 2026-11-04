/**
 * Fixtures SO PARA EVIDENCIA VISUAL da frente B (Evolucao): dados realistas no
 * formato dos tipos da API, servidos por `page.route` em `routes-evolucao.spec.ts`
 * por cima do `mock-api.ts` (que nao e alterado). Os numeros sao arbitrarios e
 * nao replicam regra de motor: so existem para a tela ser vista com dados.
 */

const Q = 'HIGH';
const metric = (value: number | null, unit: string | null = null, nature = 'OBSERVED') => ({
  value,
  unit,
  nature,
  metric_version: 'e2e-fixture',
  data_quality: Q,
  limitations: [] as string[],
});
const WINDOW = { kind: '28d', date_from: '2026-09-17', date_to: '2026-10-14', timezone: 'America/Sao_Paulo', days: 28 };
const days = (n: number, end = '2026-10-14') => {
  const out: string[] = [];
  const e = new Date(`${end}T12:00:00Z`).getTime();
  for (let i = n - 1; i >= 0; i--) out.push(new Date(e - i * 86_400_000).toISOString().slice(0, 10));
  return out;
};
const GROUPS = [
  [1, 'Peito'], [2, 'Costas'], [3, 'Quadríceps'], [4, 'Ombros'], [5, 'Bíceps'], [6, 'Tríceps'], [7, 'Posterior de coxa'], [8, 'Glúteos'],
] as const;
const base = { data_quality: Q, limitations: [] as string[], metric_versions: {} as Record<string, string> };

// ---------------------------------------------------------------- recuperacao
const recGroup = (i: number, id: number, name: string) => {
  const st = ['READY', 'READY', 'REDUCED_DOSE', 'NO_DIRECT_WORK', 'READY', 'READY', 'REDUCED_DOSE', 'READY'][i];
  return {
    muscle_group_id: id, muscle_group: name, readiness_status: st, guidance: null,
    recovery_pct: metric(st === 'READY' ? 100 : st === 'REDUCED_DOSE' ? 72 : 38, '%'),
    estimated_recovery_hours: metric(48, 'h'), remaining_hours: metric(st === 'READY' ? 0 : 20 + i, 'h'),
    hours_since_last_stimulus: metric(30 + i * 8, 'h'), session_dose: metric(6.5),
    last_session_at: '2026-10-12T19:30:00-03:00', last_trained_local_date: '2026-10-12',
    ready_at: st === 'READY' ? null : '2026-10-15T14:00:00-03:00', ready_label: st === 'READY' ? 'Pronto agora' : null,
    max_rpe: 8, allow_failure: false, volume_multiplier: 0.75, blocking_signal_codes: [],
    explanation: ['Dose das últimas 72 h dentro do esperado para o grupo.'],
    recommendation: { direct_sets_min: 3, direct_sets_max: 6, maximum_rpe: 8, allow_failure_on_last_set: false, allow_progression: true, allow_indirect_work: true, volume_multiplier: 0.75 },
    factors: {
      session_dose: 6.5, dose_last_72h: 9.2, avg_rpe_used: 8, avg_rpe_window: 7.8, density_penalty: 0.1, signal_penalty: 0, performance_ratio: 1.02,
      recent_sets: [], notes: ['Séries de trabalho com RPE médio 8 nas últimas 72 h.'],
    },
  };
};
const sig = (code: string, label: string, cur: number | null, base_: number | null, unit: string, reading: string, agreement: string) => ({
  code, label, description: `Compara ${label.toLowerCase()} do período com a sua mediana pessoal.`,
  current: metric(cur, unit), baseline: metric(base_, unit), delta: metric(cur != null && base_ != null ? cur - base_ : null, unit),
  direction: cur == null || base_ == null ? 'UNKNOWN' : cur >= base_ ? 'ABOVE' : 'BELOW', reading, agreement,
  observations: 12, baseline_observations: base_ == null ? 3 : 26, min_observations: 7, source: 'intervals', limitations: base_ == null ? ['Sem baseline suficiente.'] : [],
});
const recovery = {
  window: WINDOW, metric_versions: {}, data_quality: 'MEDIUM', limitations: ['HRV disponível em 12 dos 28 dias.'],
  groups: GROUPS.map(([id, n], i) => recGroup(i, id, n)),
  signals: [
    sig('sleep_duration', 'Duração do sono', 7.2, 7.4, 'h', 'NEUTRAL', 'AGREES'),
    sig('hrv', 'HRV (rMSSD)', 58, 64, 'ms', 'UNFAVORABLE', 'DIVERGES'),
    sig('resting_hr', 'FC de repouso', 54, 53, 'bpm', 'NEUTRAL', 'AGREES'),
    sig('tsb', 'Forma (TSB)', -8, -3, '', 'UNFAVORABLE', 'AGREES'),
    sig('soreness', 'Dor muscular (check-in)', 3, null, '', 'UNKNOWN', 'NO_BASELINE'),
  ],
  assessment: {
    level: 'ATTENTION', readiness_reading: 'RECOVERED', headline: 'Prontidão boa, mas o HRV está abaixo da sua mediana.', uncertainty: 'MEDIUM',
    allow_intensification: false, intensification_blocked_reason: 'HRV abaixo da baseline por 3 dias seguidos.', agreeing_codes: ['sleep_duration'], diverging_codes: ['hrv'], unavailable_codes: ['soreness'],
    explanation: ['3 sinais concordam com a prontidão; 1 diverge.', 'Reavalie amanhã antes de intensificar.'], reassess_after_hours: 24, context_events: [],
  },
  training_vs_rest: {
    training_days: 12, rest_days: 16, min_days: 5, limitations: [],
    metrics: ['Sono', 'HRV', 'FC de repouso'].map((label, i) => ({
      code: ['sleep', 'hrv', 'rhr'][i], label, unit: ['h', 'ms', 'bpm'][i], source: 'intervals', higher_is_better: i < 2,
      training_days: metric([7.0, 56, 55][i]), rest_days: metric([7.5, 62, 53][i]), delta: metric([-0.5, -6, 2][i]), direction: 'BELOW', reading: 'NEUTRAL',
      training_observations: 12, rest_observations: 16, min_days: 5, limitations: [],
    })),
  },
  global_readiness_adjustment: { factor: 0.95, form_tsb: -8, wellness_date: '2026-10-14', description: 'TSB de -8 reduziu o tempo de recuperação em 5%.' },
  baseline_window_days: 28, readiness_model: 'fullbody-no-reps-v1',
};

// --------------------------------------------------------------------- musculos
const mItem = (id: number, name: string, i: number) => {
  const status = ['ON_TARGET', 'BETWEEN_MIN_TARGET', 'BELOW_MIN', 'ABOVE_MAX', 'ON_TARGET', 'BETWEEN_TARGET_MAX', 'BELOW_MIN', 'ON_TARGET'][i];
  return {
    muscle_group_id: id, muscle_group: name,
    targets: { min: metric(10, 'séries/sem'), target: metric(14, 'séries/sem'), max: metric(20, 'séries/sem'), source: 'explicit', training_type_id: 1 },
    realized: {
      physical_sets: metric(30 + i), effective_sets_stimulus: metric(28 + i), effective_sets_fatigue: metric(30), direct_sets: metric(16 - i, 'séries'), indirect_sets: metric(6, 'séries'),
      stabilizer_sets: metric(2), weekly_effective_sets: metric(9 + i * 1.5, 'séries/sem'), avg_rpe: metric(8),
    },
    planned: { effective_sets: metric(14, 'séries'), weekly_effective_sets: metric(14, 'séries/sem'), plans_count: 3 },
    projected: { remaining_sets: metric(4), pending_slots: 1, cycle_length_days: 7 },
    frequency: { direct_sessions: metric(2 + (i % 2), 'sessões'), participation_sessions: metric(3), median_interval_hours: metric(72, 'h') },
    readiness: { status: 'READY', guidance: null, estimated_recovery_hours: metric(48), hours_since_last_stimulus: metric(36), explanation: ['Pronto para treinar.'] },
    last_stimulus_local_date: '2026-10-12', last_direct_local_date: '2026-10-12', hours_since_last_stimulus: metric(52 + i * 4, 'h'),
    status, status_basis: 'realized_only',
    top_exercises: [{ exercise_id: 100 + id, name: ['Supino reto', 'Puxada alta', 'Agachamento livre', 'Desenvolvimento', 'Rosca direta', 'Tríceps corda', 'Stiff', 'Hip thrust'][i], sets_count: 8, effective_sets_stimulus: 7, contribution_share: 0.42, roles: ['PRIMARY'], last_local_date: '2026-10-12' }],
    data_quality: Q, limitations: [] as string[],
  };
};
const pt = (off: number, date: string, sets: number) => ({ offset: off, activity_id: 100 + off, activity_name: 'Treino', local_date: date, physical_sets: sets + 1, effective_sets_stimulus: sets, tonnage_kg: 4200 - off * 300, avg_rpe: 8 - off * 0.2, has_direct_work: true });
const epts = (name: string) => ({ equipment_name: name, effective_sets_stimulus: 9, share_of_group: 0.6, points: [0, 1, 3].map((o) => ({ offset: o, activity_id: 100 + o, local_date: '2026-10-1' + (2 - o > 0 ? 2 - o : 0), physical_sets: 4, effective_sets_stimulus: 3.5, tonnage_kg: 2100, avg_rpe: 8 })) });
const muscles = {
  window: WINDOW, target_source: 'explicit', training_type_id: 1, unattributed_sets: 0, ...base,
  items: GROUPS.map(([id, n], i) => mItem(id, n, i)),
  comparatives: {
    unit: 'session', sessions_compared: 4, data_quality: Q, limitations: [],
    by_session: GROUPS.slice(0, 5).map(([id, n]) => ({ muscle_group_id: id, muscle_group: n, sessions_in_window: 8, points: [pt(0, '2026-10-12', 9), pt(1, '2026-10-09', 8), pt(2, '2026-10-06', 10), pt(3, '2026-10-02', 7)] })),
    by_equipment: GROUPS.slice(0, 3).map(([id, n]) => ({ muscle_group_id: id, muscle_group: n, equipment_limit: 5, equipment_truncated: false, equipments: [epts('Barra'), epts('Halter')] })),
    heatmap: [{ activity_id: 103, activity_name: 'Costas e bíceps', local_date: '2026-10-13', max_effective_sets_stimulus: 9, cells: GROUPS.slice(0, 5).map(([id, n], i) => ({ muscle_group_id: id, muscle_group: n, physical_sets: 9 - i, effective_sets_stimulus: 9 - i, tonnage_kg: 3000, avg_rpe: 8, share_of_max: (9 - i) / 9, band: ['VERY_HIGH', 'HIGH', 'MODERATE', 'LOW', 'NONE'][i] })) }],
  },
};
const muscleDetail = (id: number) => ({
  window: WINDOW, item: mItem(id, GROUPS.find((g) => g[0] === id)?.[1] ?? 'Peito', 0), sets_limit: 400, sets_truncated: false, target_source: 'explicit', training_type_id: 1, ...base,
  sessions: [{
    activity_id: 102, activity_name: 'Peito e tríceps', local_date: '2026-10-11', started_at: '2026-10-11T18:00:00', physical_sets: 9, effective_sets_stimulus: 8, has_direct_work: true,
    exposures: [{ exercise_row_id: 1, exercise_id: 101, name: 'Supino reto', position_in_session: 1, role: 'PRIMARY', effective_sets_stimulus: 4,
      sets: [1, 2, 3, 4].map((n) => ({ set_id: n, set_number: n, set_type: 'working', reps: 10 - n, weight_kg: 60 + n * 2.5, rpe: 8, effective_sets_stimulus: 1, effective_sets_fatigue: 1, role: 'PRIMARY' })) }],
  }],
});

// ----------------------------------------------------------------- progressao
const progByGroup = {
  days: 28, stable_band_pct: 3, min_sessions_per_window: 3, comparison_basis: 'tonnage_per_session', performance_basis: 'best_e1rm_epley_same_exercise',
  current_from: '2026-09-17', current_to: '2026-10-14', previous_from: '2026-08-20', previous_to: '2026-09-16', degraded: false,
  exercise_progression: {
    window_days: 28, period_start: '2026-09-17', period_end: '2026-10-14', answer: '5 de 8 exercícios comparáveis estão progredindo.',
    counts: { progressing: 5, stable: 2, plateau_suspected: 1, regressing: 0, comparable_total: 8, not_comparable: 2, insufficient_data: 1 },
  },
  items: GROUPS.map(([id, n], i) => ({
    muscle_group_id: id, muscle_group_name: n, current_tonnage_kg: 18000 + i * 900, previous_tonnage_kg: 16500, current_sets: 40, previous_sets: 36, current_sessions: 8, previous_sessions: 7,
    current_tonnage_per_session_kg: 2250, previous_tonnage_per_session_kg: 2357, classification: 'ESTAVEL', volume_status: ['VOLUME_UP', 'VOLUME_STABLE', 'VOLUME_DOWN'][i % 3],
    performance_status: ['PROGREDINDO', 'ESTAVEL', 'REGREDINDO', 'PROGREDINDO'][i % 4], performance_change_pct: [4.2, 0.8, -3.1, 6.5][i % 4], performance_exercises_compared: 2, performance_exercises_excluded: i === 2 ? 1 : 0,
    performance_activation_sum: 1.4, performance_insufficient_reason: null,
    performance_exercises: [{ exercise_id: 101, exercise_name: 'Supino reto', equipment_id: 1, load_convention: 'total', previous_e1rm_kg: 92, current_e1rm_kg: 96, change_pct: 4.3, stimulus_weight: 1, outlier: false, excluded_reason: null }],
    change_pct: [8.4, -2.0, -6.1][i % 3], total_change_pct: [9.0, 3.0, -8.0][i % 3], phase: ['Fase 2', 'Fase 1', null][i % 3], phase_status: null,
  })),
};
const exOptions = { ok: true, source: 'e2e', items: [['Supino reto', 'Peito'], ['Puxada alta', 'Costas'], ['Agachamento livre', 'Quadríceps'], ['Desenvolvimento', 'Ombros'], ['Rosca direta', 'Bíceps']].map(([n, g], i) => ({ id: i + 1, exercise_name: n, amazfit_name: null, primary_group: g, secondary_group: null, primary_muscles: null, secondary_muscles: null })) };
const progression = {
  exercise_name: 'Supino reto', mapping_id: 1, days: 28, items: days(28).filter((_, i) => i % 3 === 0).map((d, i) => ({
    activity_id: 200 + i, local_date: d, activity_name: 'Peito e tríceps', sets_count: 4, best_load_kg: 60 + i * 2.5, best_reps: 8, avg_rpe: 8, e1rm: 88 + i * 1.6, intensity_status: 'REACHED', rpe_target_effective: 8,
    failure_allowed: false, evolution: { status: i === 0 ? 'NOT_COMPARABLE' : 'PROGRESSING', reasons: [] }, e1rm_reps: 8, e1rm_reliable: true, top_load_kg: 60 + i * 2.5, top_set_reps: [8, 8], in_verdict_key: true, training_goal: 'hypertrophy', deload_source: null, deload_label: null,
  })),
};
const progressionTrend = {
  exercise_name: 'Supino reto', mapping_id: 1, days: 28, status: 'PROGRESSING', status_reasons: [], comparable_exposures: 8, exposures_in_window: 9, comparability_key: null,
  equipment_name: 'Barra olímpica', load_convention: 'total', load_convention_source: 'equipment_exercises', last_local_date: '2026-10-12',
};
const suggestion = { mapping_id: 1, reps_target: '8-10', load_target: '82,5 kg', rpe_target: '8', progression_note: 'Subir 2,5 kg se completar 10 reps.', has_history: true, progression: 'increased', last_load_kg: 80, last_reps: 9, trend: 'up', last_date: '2026-10-12' };
const strengthPanel = {
  window: WINDOW, training_goal: 'hypertrophy', training_goal_label: 'Hipertrofia', max_exercises: 6, ...base,
  lifts: [{
    exercise_id: 101, name: 'Supino reto', strength_sessions: 3,
    e1rm_series: days(28).filter((_, i) => i % 4 === 0).map((d, i) => ({ activity_id: 300 + i, local_date: d, e1rm: 90 + i * 1.5, e1rm_reps: 5, e1rm_reliable: true, training_goal: i % 2 ? 'strength' : 'hypertrophy', same_physical_key: true })),
    top_sets: [{ activity_id: 301, local_date: '2026-10-12', load_kg: 100, reps: 5, top_sets_count: 2, avg_rpe: 9, e1rm: 112, e1rm_reliable: true }],
    best_e1rm: { value: 112, activity_id: 301, local_date: '2026-10-12', reliable: true, max_reliable_reps: 12 },
  }],
};

// ----------------------------------------------------------------------- corpo
const bodyPts = days(28).filter((_, i) => i % 2 === 0);
const mpts = (v: number) => bodyPts.slice(0, 6).map((d, i) => ({ id: i, local_date: d, value_cm: v - i * 0.2, protocol_ok: i !== 3, data_quality: i === 3 ? 'LOW' : Q, notes: null }));
const body = {
  window: WINDOW, timezone: 'America/Sao_Paulo', metric_versions: {}, data_quality: Q, limitations: [],
  weight: {
    answer: 'Peso estável: média de 7 dias em 78,4 kg, -0,3 kg no período.', latest: metric(78.4, 'kg'), ma7_latest: metric(78.6, 'kg'), change: metric(-0.3, 'kg'), change_pct: metric(-0.4, '%'), weighings_count: 14, weighings_per_week: metric(3.5),
    points: bodyPts.map((d, i) => ({ local_date: d, weight_kg: 79 - i * 0.05 + (i % 3) * 0.2, source: 'body_composition', ma7_kg: 78.9 - i * 0.04, ma7_points: 4 })), data_quality: Q, limitations: [],
  },
  waist: { available: true, table_available: true, latest: metric(82.4, 'cm'), latest_local_date: '2026-10-08', change: metric(-1.2, 'cm'), change_span_days: 21, points: mpts(83.4), protocol_points: 5, out_of_protocol_points: 1, data_quality: Q, limitations: [] },
  measurements: [{ site: 'chest', label: 'Peitoral', latest: metric(101.2, 'cm'), latest_local_date: '2026-10-08', change: metric(0.6, 'cm'), points: mpts(100.6), data_quality: Q }, { site: 'arm_right', label: 'Braço direito', latest: metric(36.4, 'cm'), latest_local_date: '2026-10-08', change: metric(0.4, 'cm'), points: mpts(36), data_quality: Q }],
  bioimpedance: {
    readings_count: 4, latest_local_date: '2026-10-10', body_fat_percent: metric(17.8, '%', 'ESTIMATED'), muscle_mass_kg: metric(35.2, 'kg', 'ESTIMATED'), body_water_percent: metric(56.1, '%', 'ESTIMATED'),
    body_fat_change: metric(-0.6, '%', 'ESTIMATED'), muscle_mass_change: metric(0.3, 'kg', 'ESTIMATED'), points: [], data_quality: 'MEDIUM', limitations: ['Estimativa da balança; varia com a hidratação.'],
  },
  recomposition: {
    status: 'compatible', nature: 'INFERRED', metric_version: 'e2e', window: WINDOW, answer: 'Os dados são compatíveis com recomposição: cintura menor e força estável ou maior.', data_quality: 'MEDIUM', comparable_exercises: 8, progressing_or_stable: 7,
    checks: [{ code: 'waist', label: 'Cintura', result: true, detail: 'Caiu 1,2 cm no protocolo.' }, { code: 'strength', label: 'Força', result: true, detail: '7 de 8 exercícios progredindo ou estáveis.' }, { code: 'weight', label: 'Peso', result: null, detail: 'Poucas pesagens.' }],
    evidence: [{ metric: 'Cintura', from_value: 83.6, to_value: 82.4, unit: 'cm', nature: 'OBSERVED', observed_at: null }],
    limitations: ['Inferência, nunca prova.'], alternative_explanations: ['Retenção de líquido pode explicar parte da variação.'],
  },
  performance: { points: bodyPts.map((d, i) => ({ local_date: d, e1rm_avg_kg: 80 + i * 0.6, exercises: 6 })), exercises_total: 10, comparable_exercises: 8, progressing_or_stable: 7, data_quality: Q, limitations: [] },
  milestones: [{ local_date: '2026-09-28', kind: 'plan_change', label: 'Troca de ficha', detail: 'Modelo ABC v3' }], milestones_limitations: [],
};

// ---------------------------------------------------------------------- cardio
const zmin = (a: number, b: number, c: number, d: number, e: number) => ({ Z1: a, Z2: b, Z3: c, Z4: d, Z5: e });
const weeksStart = ['2026-09-21', '2026-09-28', '2026-10-05', '2026-10-12'];
const cardio = {
  window: WINDOW, metric_versions: {}, data_quality: 'MEDIUM', limitations: ['2 atividades sem FC.'], nature: 'OBSERVED', intervals_configured: true, last_synced_at: '2026-10-14T11:00:00-03:00',
  totals: { sessions: 9, sessions_with_hr: 7, minutes: 380, distance_km: 52.4, calories_kcal: 3100, training_load: 410, avg_hr_bpm: 138, max_hr_bpm: 176 },
  by_sport: [{ sport_type: 'Run', sessions: 4, minutes: 170, distance_km: 28.1, calories_kcal: 1700, avg_hr_bpm: 148, max_hr_bpm: 176 }, { sport_type: 'Ride', sessions: 5, minutes: 210, distance_km: 24.3, calories_kcal: 1400, avg_hr_bpm: 128, max_hr_bpm: 160 }],
  sessions: days(14).filter((_, i) => i % 3 === 0).map((d, i) => ({ intervals_activity_id: i, external_id: `i${i}`, activity_date: d, start_time: `${d}T07:00:00`, name: i % 2 ? 'Pedal leve' : 'Corrida contínua', sport_type: i % 2 ? 'Ride' : 'Run', kind: 'cardio', minutes: 40 + i * 5, distance_km: 8.2, calories_kcal: 420, avg_hr_bpm: 140, max_hr_bpm: 165, training_load: 48, zone_seconds: null, zone_minutes: zmin(5, 25, 8, 2, 0), high_intensity: false })),
  strength_activities_total: 12, unmatched_strength_activities_total: 1, other_activities_total: 2,
  strength_heart_rate: {
    sessions_total: 12, sessions_with_hr: 10, coverage_pct: 83.3, ambiguous_total: 1, ambiguous_in_use_total: 1, from_activity_total: 7, from_link_total: 3, avg_hr_bpm: 112, max_hr_bpm: 158,
    items: [103, 102, 101].map((id, i) => ({ activity_id: id, local_date: `2026-10-${13 - i * 2}`, name: ['Costas e bíceps', 'Peito e tríceps', 'Pernas'][i], started_at: null, minutes: 62, avg_hr_bpm: 112 + i * 3, max_hr_bpm: 150, hr_source: i ? 'intervals_link' : 'activity', calories_kcal: null, training_load: null, match_status: i === 1 ? 'MATCHED_AMBIGUOUS' : 'MATCHED', match_delta_seconds: 240, candidates_count: i === 1 ? 2 : 1, has_heart_rate: true })),
  },
  weeks: weeksStart.map((w, i) => ({ week_start: w, sessions: 2 + (i % 2), minutes: 85 + i * 10, calories_kcal: 700, sessions_with_zones: 2, zone_minutes: zmin(8 + i, 40, 18, 9, 2), continuous_sessions: 2, continuous_minutes: 70, high_intensity_sessions: 1, high_intensity_minutes: 22, unclassified_sessions: 0, unclassified_minutes: 0 })),
  adherence: {
    anchor: 'strength_end', thresholds: {}, status_labels: {},
    sessions: [{ activity_id: 103, local_date: '2026-10-13', mode: 'continuous', prescription_text: '30 min em Z2', planned_minutes: 30, realized_minutes: 34, time_completion_pct: 100, zone_completion_pct: 90, has_zone_data: true, match_status: 'MATCHED', status: 'adherent', status_label: 'Cumprido' },
      { activity_id: 102, local_date: '2026-10-11', mode: 'hiit', prescription_text: '6 × 1 min em Z5', planned_minutes: 20, realized_minutes: 12, time_completion_pct: 60, zone_completion_pct: null, has_zone_data: false, match_status: 'MATCHED', status: 'partial', status_label: 'Parcial' }],
    weeks: weeksStart.slice(2).map((w) => ({ week_start: w, planned_sessions: 3, done_sessions: 2, planned_minutes: 90, realized_minutes: 70, time_completion_pct: 78, by_status: {} })),
  },
  wellness: {
    points: days(21).map((d, i) => ({ date: d, ctl: 38 + i * 0.2, atl: 40 + (i % 5), tsb: -2 - (i % 6), hrv_ms: 58 + (i % 7), resting_hr_bpm: 53 + (i % 3) })),
    latest: { ctl: 42.2, ctl_date: '2026-10-14', atl: 44, atl_date: '2026-10-14', tsb: -8, tsb_date: '2026-10-14', hrv_ms: 58, hrv_ms_date: '2026-10-14', resting_hr_bpm: 54, resting_hr_bpm_date: '2026-10-14' },
  },
  pai: { points: days(14).map((d, i) => ({ date: d, pai: 60 + i * 3 })), target_min: 100, latest: 99, latest_date: '2026-10-14', latest_below_target: true, estimated: true, model_version: 'e2e' },
  next_session: { recommendation_id: 7, generated_at: '2026-10-13T20:00:00-03:00', prompt_version: 'cardio-agent-v3', date: '2026-10-15', date_label: 'qui, 15/10', prescription_text: '30 min em Z2, ritmo conversável', prescription_short: '30 min Z2', total_minutes_label: '30 min', rationale: 'TSB negativo pede cardio leve.', signals: [], adjustment_labels: ['Reduzido por TSB'], slot_name: 'Treino C', plan_action: 'add' },
  zones: { source: 'intervals', max_hr_bpm: 186, zones: [1, 2, 3, 4, 5].map((z) => ({ zone: z, label: `Z${z}`, lower_bpm: 90 + z * 15, upper_bpm: 105 + z * 15 })) },
};

// -------------------------------------------------------------------- nutricao
const nBal = (share: number) => ({ field: 'x', target: 100, intake: share * 100, intake_complete: true, remaining: 0, share, within_target: Math.min(share, 1) * 100, to_consume: Math.max(0, 1 - share) * 100, excess: Math.max(0, share - 1) * 100 });
const nDays = days(14);
const nutrition = {
  user: { id: 1, username: 'ana', full_name: 'Ana Teste' }, today: '2026-10-14', start: '2026-10-01', end: '2026-10-14', window_days: 14, window_choices: [7, 14, 28],
  plan: { id: 1, strategy: 'cut', effective_from: '2026-09-01', goal_weight_kg: 74, goal_body_fat_percent: 14 }, empty_states: [],
  days: nDays.map((d, i) => ({
    local_date: d, state: i === 13 ? 'open_today' : i === 5 ? 'no_record' : 'logged', has_target: true, logged: i !== 5, intake_incomplete: false,
    macros: { energy_kcal: { ...nBal(0.9 + (i % 4) * 0.05), field: 'energy_kcal' }, protein_g: { ...nBal(0.95 + (i % 3) * 0.06), field: 'protein_g' }, carbohydrates_g: { ...nBal(0.8 + (i % 5) * 0.06), field: 'carbohydrates_g' }, total_fat_g: { ...nBal(0.85 + (i % 4) * 0.07), field: 'total_fat_g' } },
    kcal_share: 0.95, protein_share: 1.0, kcal_within_tolerance: true, protein_ok: true,
  })),
  adherence: { window_days: 14, counted_days: 13, logged_days: 12, no_record_days: 1, meal_days_without_target: 0, kcal_within_tolerance_days: 9, protein_ok_days: 11, open_today: true, incomplete_days: [] },
  weeks: [['2026-09-28', '2026-10-04', true], ['2026-10-05', '2026-10-11', false], ['2026-10-12', '2026-10-18', true]].map(([s, e, p]) => ({
    start: s, end: e, partial: p, report: { days: [], budget: { kcal: 14000, protein_g: 900, carb_g: 1200, fat_g: 350, days_with_target: 7 }, bank: { balance_kcal: 0, carryover_kcal: 0, max_daily_carryover_kcal: null, max_weekly_bank_kcal: null, logged_days: 6 }, adherence: { kcal_share: 0.93, protein_share: 1.02, logged_days: p ? 3 : 6, valid_week: !p, kcal_within_tolerance: true, protein_ok: true }, logged_days: [], incomplete_days: [], limitations: [] },
  })),
  body: { points: days(14).map((d, i) => ({ local_date: d, weight_kg: 79 - i * 0.08, weight_trend_kg: 79 - i * 0.07, body_fat_percent: i % 4 === 0 ? 18.4 - i * 0.05 : null, muscle_mass_kg: i % 4 === 0 ? 35 + i * 0.02 : null })), weight_trend: { slope_kg_per_day: -0.07, readings: 14, span_days: 13, weekly_change_kg: -0.49, limitations: [], limitation_items: [] } },
  projection: { status: 'projected', goal_kind: 'weight', current: 77.9, target: 74, target_weight_kg: 74, weeks: 8, eta_date: '2026-12-09', progress_percent: 42, weekly_rate_share: 0.6, reasons: [], reason_items: [], is_minimum_estimate: false },
  adaptive_tdee: { applied: false, observed: { kcal: 2480, quality: 'MEDIUM', window_days: 14, logged_days: 12, mean_intake_kcal: 2150, limitation_items: [] }, suggestion: { formula_kcal: 2400, observed_kcal: 2480, suggested_kcal: null, delta_kcal: null, should_suggest: false, limitation_items: [{ code: 'few_days', label: 'Ainda poucos dias com diário completo.' }] } },
};
const nutriCheckins = {
  items: [['2026-10-05', '2026-10-11', 'on_track', 'No ritmo', -100, 'Aplicado'], ['2026-09-28', '2026-10-04', 'too_slow', 'Abaixo do esperado', null, 'Recusado']].map(([s, e, v, vl, a, sl], i) => ({
    id: i + 1, user_id: 1, week_start: s, week_end: e, due_date: null, status: 'accepted', status_label: sl, verdict: v, verdict_label: vl, can_respond: false, show_card: false, expires_at: null, expires_on: null, adjustment_kcal: a, missing_items: [], reason_items: [], proposal: null, engine_version: 'e2e', source_plan_id: 1, generated_plan_id: null, responded_at: null, created_at: null, summary: null,
  })),
};

// ----------------------------------------------------------------------- ciclo
const cycle = {
  has_template: true, template_id: 1, template_name: 'Modelo ABC', cycle_length_days: 7, training_type_source: 'template',
  slots: [{ sequence: 1, name: 'Treino A', slot_type: 'strength', weekday: 1, status: 'done' }, { sequence: 2, name: 'Treino B', slot_type: 'strength', weekday: 3, status: 'done' }, { sequence: 3, name: 'Treino C', slot_type: 'strength', weekday: 5, status: 'next' }, { sequence: 4, name: 'Descanso', slot_type: 'rest', weekday: null, status: 'pending' }],
  next_slot: { sequence: 3, name: 'Treino C', slot_type: 'strength', weekday: 5 },
  coverage: GROUPS.map(([id, n], i) => ({ muscle_group_id: id, muscle_group: n, min_weekly_sets: 10, target_weekly_sets: 14, max_weekly_sets: 20, actual_weekly_sets: 9 + i, projected_weekly_sets: 12 + i, status: ['ON_TARGET', 'BELOW_MIN', 'BETWEEN_MIN_TARGET', 'ABOVE_MAX'][i % 4] })),
};

// -------------------------------------------------------------------- contexto
const context = {
  window: WINDOW, metric_versions: {}, data_quality: Q, limitations: [], nature: 'OBSERVED', manual_total: 3, derived_total: 2, table_available: true, last_session_local_date: '2026-10-13', days_since_last_session: 1,
  counts: [{ event_type: 'alcohol', event_type_label: 'Álcool', count: 1 }, { event_type: 'travel', event_type_label: 'Viagem', count: 1 }, { event_type: 'plan_change', event_type_label: 'Troca de ficha', count: 2 }],
  items: [
    { id: 11, event_type: 'alcohol', event_type_label: 'Álcool', local_date: '2026-10-10', end_local_date: null, intensity: 2, notes: 'Jantar com amigos', source: 'manual', derived: false, editable: true },
    { id: 12, event_type: 'travel', event_type_label: 'Viagem', local_date: '2026-10-02', end_local_date: '2026-10-05', intensity: null, notes: 'Congresso em Curitiba', source: 'manual', derived: false, editable: true },
    { id: null, event_type: 'plan_change', event_type_label: 'Troca de ficha', local_date: '2026-09-28', end_local_date: null, intensity: null, notes: 'Modelo ABC v3', source: 'derived', derived: true, editable: false },
  ],
};

// --------------------------------------------------------------------- checkin
const ck = (id: number, d: string, e: number | null, s: number | null, st: number | null, m: number | null, notes: string | null) => ({ id, local_date: d, energy: e, soreness: s, stress: st, mood: m, notes, created_at: null, updated_at: null, answered: [], score_labels: {} });
const checkins = { items: [ck(1, '2026-10-14', 4, 3, 2, 4, null), ck(2, '2026-10-13', 3, 4, null, 3, 'Dormi mal'), ck(3, '2026-10-12', 5, 2, 2, 5, null)], limit: 14, offset: 0, has_more: false, table_available: true, limitations: [] };

// ---------------------------------------------------------------------- engine
const prescribed = { source: 'frozen_snapshot', sets_count: 4, reps_target: '8-10', load_target: '80 kg', rpe_target: '8', rest_seconds: 90, engine_version: 'gen-v15' };
const eItem = (n: number, name: string, status: string) => ({
  plan_exercise_id: n, plan_sequence: n, status, planned: { source: n === 2 ? 'muscle_group_budget' : 'template_item', item_kind: 'exercise', item_order: n, muscle_group_id: 1, muscle_group_name: 'Peito', exercise_id: 100 + n, exercise_name: name, equipment_id: null, sets_count: 4, reps_target: '8-10', rpe_target: '8', advanced_technique: null, prioritize: false, budget_min_sets: null, budget_max_sets: null, budget_weekly_sets: null },
  generated_name: name, generated_exercise_id: 100 + n, generated: prescribed,
  executed: status === 'SKIPPED' ? null : { exercise_row_id: n, exercise_id: 100 + n, name, position_in_session: n, working_sets: 4, total_sets: 5, total_reps: 36, best_load_kg: 80, avg_rpe: 8, sets: [] },
  missing_sets: status === 'INTERRUPTED' ? 1 : null, substitution: null,
  reasons: [{ kind: 'selection', code: 'best_fit', detail: null, data: {} }, { kind: 'safety_clamp', code: 'rpe_ceiling', detail: null, data: {} }], progression: null, limitations: [],
});
const engineRuns = {
  window: WINDOW, total: 3, limit: 20, runs_table_total: 3, raw_payload_total: 0, ...base,
  items: [12, 11, 10].map((id, i) => ({ run_id: id, source: 'generation_runs', created_at: `2026-10-${13 - i * 3}T09:00:00-03:00`, plan_date: `2026-10-${13 - i * 3}`, workout_plan_id: i === 2 ? null : 40 + i, plan_name: i === 2 ? null : `Treino ${'ABC'[i]}`, template_id: 1, template_slot_sequence: i + 1, volume_mode: 'percent_budget', engine_version: 'gen-v15', llm_used: i === 0, job_id: null, selection_count: 6, safety_clamp_count: 1, substitution_count: 0, fallback_count: 0, limitations: [] })),
};
const enginePlan = {
  window: WINDOW, run: engineRuns.items[0], plan_id: 40, plan_name: 'Treino A', plan_date: '2026-10-13', template_id: 1, template_name: 'Modelo ABC', template_slot_sequence: 1, template_slot_name: 'Treino A', template_version_number: 3, volume_mode: 'percent_budget',
  items: [eItem(1, 'Supino reto', 'COMPLETED'), eItem(2, 'Crucifixo', 'INTERRUPTED'), eItem(3, 'Tríceps corda', 'SKIPPED')],
  item_status_counts: { completed: 1, interrupted: 1, skipped: 1, substituted: 0, extra: 0 },
  group_budgets: [{ muscle_group_id: 1, muscle_group_name: 'Peito', target_weekly_sets: 14, target_min_sets: 10, target_max_sets: 20, generated_sets: 12, deviation_status: 'within', recovery_status: 'READY' }],
  rejections: [{ muscle_group_id: 1, exercise_id: 150, exercise_name: 'Supino declinado', rank: 4, reason: 'lower_rank', data: {} }],
  plan_level_reasons: [], sessions_count: 1, ...base,
};

// --------------------------------------------------------- qualidade e insights
const dataQuality = {
  window: WINDOW, overall: 'MEDIUM', data_quality: Q, checked_rules: 14, limitations: ['2 regras dependem de Intervals.icu.'], metric_versions: {},
  items: [
    { code: 'sets_without_rpe', label: 'Séries sem RPE', description: 'Sem RPE a leitura de esforço e a progressão ficam menos confiáveis.', count: 18, severity: 'ATTENTION', scope: 'window', fix_link: '/registro-exercicios', examples: [{ id: 1, label: 'Supino reto', detail: '2026-10-12' }, { id: 2, label: 'Puxada alta', detail: '2026-10-11' }], limitations: [] },
    { code: 'exercise_without_muscles', label: 'Exercícios sem contribuição muscular', description: 'O volume por grupo ignora esses exercícios.', count: 3, severity: 'ACTION', scope: 'catalog', fix_link: '/dominio-exercicios', examples: [{ id: 5, label: 'Remada curvada', detail: null }], limitations: [] },
    { code: 'weighings_sparse', label: 'Poucas pesagens', description: 'Menos de 2 por semana.', count: 1, severity: 'INFO', scope: 'window', fix_link: '/indicadores-fisicos', examples: [], limitations: [] },
  ],
};
const insight = (n: number, sev: string, q: string, answer: string, cooldown: boolean) => ({
  code: `RULE_${n}`, question: q, status: 'TRIGGERED', severity: sev, answer, evidence: [{ metric: 'HRV', from_value: 64, to_value: 58, unit: 'ms', nature: 'OBSERVED', observed_at: null }], comparison: { basis: 'baseline', window: '28d', keys: {} }, data_quality: 'MEDIUM',
  limitations: ['Poucas medições de HRV.'], alternative_explanations: ['Sono curto na semana.'], suggested_action: 'Mantenha a carga e reavalie amanhã.', reassess_after: '24 h', source_screen: 'Recuperação', fingerprint: `fp-${n}`,
  cooldown: cooldown ? { alert_key: `k${n}`, reason: 'first_seen', suppressed: false, first_seen_at: null, last_emitted_at: null, emitted_count: 1, suppressed_count: 0, snoozed_until: null, metric_version: 'e2e' } : null,
});
const insights = {
  window: WINDOW, total: 3, limit: 500, offset: 0, has_more: false, rules_evaluated: 22, rules_disabled: [], ...base, suppressed_total: 2, include_suppressed: false, cooldown_available: true,
  items: [insight(1, 'ATTENTION', 'O HRV caiu?', 'HRV 9% abaixo da sua mediana nos últimos 3 dias.', true), insight(2, 'INFO', 'Há exercício estagnado?', 'Um exercício sem progresso há 4 exposições.', true), insight(3, 'ACTION', 'Falta dado?', 'Séries sem RPE reduzem a confiança.', false)],
};

// ------------------------------------------------------ exercicio (drill-down)
const exerciseKey = { canonical_exercise_id: 101, equipment_id: 1, load_convention: 'total', equipment_agnostic: false, fatigue_context: null };
const expo = (i: number) => ({
  activity_id: 300 + i, local_date: `2026-09-${20 + i * 3}`, started_at: null, equipment_id: 1, equipment_name: 'Barra olímpica', load_convention: 'total', load_convention_source: 'equipment_exercises', position_in_session: 1, fatigue_context: null,
  sets: [1, 2, 3].map((n) => ({ set_number: n, reps: 10 - n, weight_kg: 60 + i * 2.5, rpe: 8, rest_seconds: 90, notes: null })), total_reps: 27, best_load_kg: 60 + i * 2.5, avg_rpe: 8, e1rm: 85 + i * 1.5, tonnage: 4800, comparability_key: exerciseKey, e1rm_reps: 8, e1rm_reliable: true, comparable_with_current: i !== 1, mismatch_with_current: i === 1 ? 'equipment' : null,
});
const exerciseProgress = {
  window: WINDOW, exercise: { exercise_id: 101, name: 'Supino reto', amazfit_name: null }, comparability_key: exerciseKey, status: 'PROGRESSING', status_reasons: [], fatigue_context: null,
  exposures: [0, 1, 2, 3, 4].map(expo),
  latest_comparison: { comparable: true, paired_reps: { pairs: 2, previous: [8, 8], current: [9, 8], previous_sum: 16, current_sum: 17, delta: 1 }, e1rm_decisive: true, e1rm_max_reliable_reps: 12 },
  records: { best_e1rm: { value: 91, activity_id: 304, local_date: '2026-10-02', reliable: true, max_reliable_reps: 12 }, best_load_kg: { value: 70, activity_id: 304, local_date: '2026-10-02' } },
  training_goal: 'hypertrophy', e1rm_series: [0, 1, 2, 3, 4].map((i) => ({ activity_id: 300 + i, local_date: `2026-09-${20 + i * 3}`, e1rm: 85 + i * 1.5, e1rm_reps: 8, e1rm_reliable: true, training_goal: 'hypertrophy', same_physical_key: true })),
  deload_sessions: [], progression_decisions: [{ id: 1, kind: 'load', decision: 'increase', phase_before: 'base', phase_after: 'base', engine_version: 'prog-v2', created_at: '2026-10-02T10:00:00', equipment_id: 1 }],
  data_quality: Q, limitations: [], metric_versions: { exercise_progression_status: 'v4' },
};
const workoutDetail = {
  window: WINDOW, notes: 'Treino bom, cansaço no fim.', sets_limit: 400, sets_truncated: false, ...base,
  session: { activity_id: 103, local_date: '2026-10-13', name: 'Costas e bíceps' },
  items: [1, 2, 3].map((n) => ({
    status: n === 3 ? 'SUBSTITUTED' : 'COMPLETED', plan_exercise_id: n, plan_sequence: n, planned_name: ['Puxada alta', 'Remada baixa', 'Rosca direta'][n - 1], prescribed,
    performed: { exercise_row_id: n, exercise_id: 100 + n, name: ['Puxada alta', 'Remada baixa', 'Rosca martelo'][n - 1], position_in_session: n, working_sets: 4, total_sets: 5, total_reps: 38, best_load_kg: 70, avg_rpe: 8,
      sets: [1, 2, 3, 4].map((s) => ({ set_id: n * 10 + s, set_number: s, set_type: 'working', is_working: true, reps: 10, weight_kg: 70, rpe: 8, rest_seconds: 90 })) },
    missing_sets: null, substitution: n === 3 ? { planned_exercise_id: 105, planned_name: 'Rosca direta', performed_exercise_id: 106, performed_name: 'Rosca martelo' } : null, is_record: n === 1, notes: null, limitations: [],
  })),
};

// -------------------------------------------------- agente de cardio / intervals
const agentCtx = {
  days: 14, body_composition: { items: [] }, recent_workouts: [], weekly_training_load: {}, past_recommendations: [],
  muscle_recovery: GROUPS.slice(0, 5).map(([, n], i) => ({ muscle_group: n, recovery_pct: [100, 92, 64, 40, 100][i], status: ['green', 'green', 'yellow', 'red', 'green'][i], readiness_status: ['READY', 'READY', 'REDUCED_DOSE', 'NO_DIRECT_WORK', 'READY'][i], last_trained_at: '2026-10-12T19:00:00-03:00' })),
  intervals_wellness: days(14).reverse().map((d, i) => ({ wellness_date: d, fitness_ctl: 42 - i * 0.2, fatigue_atl: 44 - (i % 4), form_tsb: -2 - (i % 5), ramp_rate: 1.2, resting_hr_bpm: 54, hrv_ms: 58 })),
  effort_metrics_estimated: days(14).reverse().map((d, i) => ({ local_date: d, epoc_7d_estimated: 300 - i * 5, pai_7d_estimated: 99 - i * 2, model_version: 'e2e' })),
  sleep_metrics: days(14).reverse().map((d, i) => ({ id: i, local_date: d, duration_hours: 6.5 + (i % 3) * 0.5, deep_sleep_hours: 1.4 })),
};
const cardioRecs = { items: [9, 8].map((id, i) => ({ id, reference_date: `2026-10-${13 - i * 4}`, workout_plan_id: null, generated_at: `2026-10-${13 - i * 4}T20:00:00-03:00`, model: 'claude-haiku', recommendation_markdown: '## Cardio da semana\n\n- 30 min em Z2 na quinta.\n- Evite Z5 até o HRV voltar.', created_at: '2026-10-13T20:00:00', prompt_version: null })) };
const ivDaily = days(30).map((d, i) => ({ date: d, activities: i % 3 === 0 ? 1 : 0, minutes: i % 3 === 0 ? 45 : 0, calories: i % 3 === 0 ? 380 : 0, distanceKm: i % 3 === 0 ? 8.2 : 0, load: i % 3 === 0 ? 52 : 0, avgHr: i % 3 === 0 ? 141 : null }));
const intervalsDash = {
  range: { days: 90, oldest: '2026-07-16', newest: '2026-10-14' }, activity_count: 24, wellness_count: 30, wellness_source: 'cache', wellness_refresh: 'refreshed',
  daily_activity: ivDaily,
  wellness_trend: days(30).map((d, i) => ({ date: d, fitness: 38 + i * 0.15, fatigue: 40 + (i % 6), form: -2 - (i % 5), restingHr: 54 + (i % 2), restingHrSource: i === 3 ? 'manual' : 'intervals', hrv: 58 + (i % 6), hrvSource: 'intervals', rampRate: 1.1 })),
  recent_activities: days(10).map((d, i) => ({ date: d, name: i % 2 ? 'Pedal leve' : 'Corrida contínua', sport: i % 2 ? 'Ride' : 'Run', minutes: 45, calories: 400, avgHr: 142, load: 50 })),
};

// ----------------------------------------------------------------- muscle extra
const methodology = {
  metric_version: 'e2e', days: days(7),
  items: GROUPS.slice(0, 6).map(([id, n], i) => ({
    muscle_group_id: id, muscle_group: n, member_groups: [{ muscle_group_id: id, muscle_group: n }], volume_class_name: ['Grandes', 'Grandes', 'Médios', 'Pequenos'][i % 4], days_trained: 2,
    days: days(7).map((d, k) => ({ date: d, direct_sets: k % 3 === 0 ? 6 : 0, effective_sets: k % 3 === 0 ? 6.5 : 0, status: k % 3 === 0 ? ['WITHIN_RANGE', 'BELOW_RANGE', 'ABOVE_RANGE'][(i + k) % 3] : null })),
    frequency_bucket: '2x', session_range: { min: 4, max: 8 }, direct_sets_per_session: 6, session_status: 'WITHIN_RANGE', weekly_total: 12, weekly_range: { min: 10, max: 20 }, weekly_status: ['WITHIN_RANGE', 'BELOW_RANGE'][i % 2],
  })),
};
const consecutive = { days: 7, items: [{ muscle_group_id: 1, muscle_group: 'Peito', date_from: days(7)[0], date_to: days(7)[1], elapsed_hours: 20, required_hours: 48, insufficient_recovery: true, timestamp_precision: 'exact' }] };
const trendRows = days(84).filter((_, i) => i % 7 === 0).flatMap((d, w) => GROUPS.slice(0, 6).map(([, n], g) => ({ period: `2026-W${String(30 + w).padStart(2, '0')}`, group: n, muscle_group: n, value: 8 + ((w + g * 2) % 9), sets: 10, volume_kg: 9000, avg_rpe: 8, rpe_sets: 8, exercise_count: 3, exercises: [], source: 'e2e' })));
const mappedTrend = { ok: true, source: 'e2e', metric: 'volume', period: 'weekly', days: 180, total_sets: 400, mapped_sets: 392, unmapped_sets: 8, mapped_ratio: 0.98, unmapped: [], trend_rows: trendRows, table_rows: [] };
const goals = { default: 'hypertrophy', items: [{ value: 'hypertrophy', label: 'Hipertrofia', is_default: true, defaults: {}, rest_follows_generation_interval: true, target_rpe: 8, rpe_ceiling: 9, disallowed_techniques: [] }, { value: 'strength', label: 'Força', is_default: false, defaults: {}, rest_follows_generation_interval: false, target_rpe: 9, rpe_ceiling: 10, disallowed_techniques: [] }] };

const bodyMap = { items: [['Peito','chest'],['Costas','trapezius'],['Quadríceps','quadriceps'],['Ombros','trapezius'],['Bíceps','biceps'],['Tríceps','triceps'],['Posterior de coxa','hamstring'],['Glúteos','gluteal']].map(([g,s]) => ({ muscle_group: g, slugs: [s] })) };

/** Mapa `METODO caminho` -> payload; caminho com `*` = um segmento dinamico. */
export const EVOLUCAO_FIXTURES: Array<[RegExp, unknown]> = [
  [/^GET \/analytics\/dashboard\/recovery-signals$/, recovery],
  [/^GET \/analytics\/dashboard\/muscles$/, muscles],
  [/^GET \/analytics\/dashboard\/muscles\/(\d+)$/, 'muscleDetail'],
  [/^GET \/analytics\/dashboard-progression-by-muscle-group$/, progByGroup],
  [/^GET \/analytics\/dashboard-progression$/, progression],
  [/^GET \/analytics\/dashboard-progression-trend$/, progressionTrend],
  [/^GET \/analytics\/dashboard-progression-suggestion$/, suggestion],
  [/^GET \/analytics\/dashboard\/strength-panel$/, strengthPanel],
  [/^GET \/mapping-canonical\/options$/, exOptions],
  [/^GET \/analytics\/dashboard\/body$/, body],
  [/^GET \/analytics\/dashboard\/cardio$/, cardio],
  [/^GET \/nutrition\/dashboard$/, nutrition],
  [/^GET \/nutrition\/checkins$/, nutriCheckins],
  [/^GET \/analytics\/dashboard-cycle$/, cycle],
  [/^GET \/analytics\/dashboard\/context$/, context],
  [/^GET \/daily-checkins$/, checkins],
  [/^GET \/analytics\/dashboard\/engine$/, engineRuns],
  [/^GET \/analytics\/dashboard\/engine\/plans\/\d+$/, enginePlan],
  [/^GET \/analytics\/dashboard\/data-quality$/, dataQuality],
  [/^GET \/analytics\/dashboard\/insights$/, insights],
  [/^GET \/analytics\/dashboard\/exercises\/\d+\/progress$/, exerciseProgress],
  [/^GET \/analytics\/dashboard\/workouts\/\d+$/, workoutDetail],
  [/^GET \/analytics\/cardio-agent-context$/, agentCtx],
  [/^GET \/cardio-recommendations$/, cardioRecs],
  [/^GET \/integrations\/intervals\/status$/, { configured: true, message: 'ok' }],
  [/^GET \/integrations\/intervals\/dashboard$/, intervalsDash],
  [/^GET \/analytics\/methodology-realized$/, methodology],
  [/^GET \/analytics\/muscle-recovery-consecutive-check$/, consecutive],
  [/^GET \/analytics\/muscle-groups-mapped-trend$/, mappedTrend],
  [/^GET \/training-goals$/, goals],
  [/^GET \/analytics\/muscle-group-body-map$/, bodyMap],
];

/** Resolve o payload (alguns dependem do id da rota). */
export function resolveFixture(key: string): unknown | undefined {
  for (const [re, payload] of EVOLUCAO_FIXTURES) {
    const m = re.exec(key);
    if (!m) continue;
    if (payload === 'muscleDetail') return muscleDetail(Number(m[1]));
    return payload;
  }
  return undefined;
}

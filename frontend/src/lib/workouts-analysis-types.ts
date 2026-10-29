// Tipos para a tela "Treinos" — análise de evolução de treinos via IA.
//
// Contrato confirmado em `C:\muscle_fit\api\app\workouts_analysis.py`
// (`POST /workouts-analysis/generate`), reaproveitando o payload `training`
// produzido por `DashboardAggregatorService.build_training_evolution_payload`
// (`C:\muscle_fit\api\app\intelligence\dashboard_aggregator_service.py`):
//
//   - Requisição: { days?: number } (7 a 365, default 90 no backend quando
//     omitido) — ver `WorkoutsAnalysisGenerateIn` em `models.py`.
//   - Resposta (contrato assíncrono, Fase 3 da migração AWS): 202
//     `{ job_id, status }` — o resultado final, com o MESMO shape de antes
//     (`{ model, insight, training }`), vem de `GET /jobs/{job_id}`, campo
//     `result`, mesmo padrão 202 + job_id + polling já usado por
//     `/cardio-recommendations/generate`/`/workout-plans/generate` (ver
//     `AsyncJob<T>` em `analytics-types.ts` e `pollAsyncJob` em
//     `async-job-poll.ts`).
//   - Sem OPENAI_API_KEY configurada / falha na OpenAI: job termina com
//     `status: 'failed'` e `error` de texto (`ReportGenerationError`,
//     subclasse de `OpenAIGenerationError`), em vez do antigo 503 síncrono.
//
// Os subtipos abaixo foram modelados por leitura direta das queries em
// `intelligence/training_load_service.py`, `intelligence/volume_service.py`,
// `intelligence/progression_service.py`, `intelligence/intervals_correlation_service.py`
// e `analytics.py::compute_rolling_weekly_trend`. Campos cujo shape exato não
// foi possível confirmar (estruturas auxiliares internas, não usadas pela UI)
// ficam como `unknown`, nunca `any`.

export type WorkoutsAnalysisGenerateRequest = {
  days?: number;
};

export type LatestWorkoutSummary = {
  id: number;
  local_date: string;
  name: string;
  activity_type: string;
  avg_rpe: number | null;
  duration_seconds: number | null;
};

export type NextWorkoutSummary = {
  id: number;
  plan_date: string;
  name: string;
  plan_type: string;
  status: string;
};

export type WeeklySummary = {
  completed_workouts: number;
  planned_workouts: number;
  total_sets: number;
  total_volume_kg: number;
  avg_rpe: number | null;
  high_rpe_sets: number;
  latest_workouts: LatestWorkoutSummary[];
  next_workout: NextWorkoutSummary | null;
};

// Item de `VolumeService.get_muscle_group_overview` — usado tanto em
// `volume_overview` quanto (com campos extras de recuperação) em `recovery`.
export type VolumeOverviewItem = {
  muscle_group: string;
  muscle_group_id: number | null;
  sessions: number;
  sets_count: number;
  reps_total: number;
  volume_kg: number;
  avg_weight_kg: number | null;
  avg_rpe: number | null;
  last_trained_at: string | null;
  last_trained_at_ts: string | null;
  hours_since_trained: number | null;
  days_since_trained: number | null;
  sets_last_day: number | null;
  last_set_rpe: number | null;
  session_dose: number;
  session_participation: number;
  dose_last_72h: number;
  performance_ratio: number | null;
  /** @deprecated Sempre `false` desde 2026-09-14 (RPE alto não é mais sinal de recuperação). Mantido só por compatibilidade. */
  rpe_anticipated: boolean;
  excessive_rep_drop: boolean;
  muscle_pain: number | null;
  joint_pain: boolean | number | null;
  direct_sets: number;
  indirect_sets: number;
  stabilizer_sets: number;
  stimulus_equivalent_sets: number;
  fatigue_equivalent_sets: number;
  direct_sessions: number;
  participation_sessions: number;
  last_direct_training_at: string | null;
  last_participation_at: string | null;
  direct_rpe_avg: number | null;
  indirect_rpe_avg: number | null;
  factors: unknown;
  activity_dose_history: unknown;
};

// `MuscleRecoveryService.get_recovery_overview` faz spread de um
// `VolumeOverviewItem` (`row`) e soma os campos de recuperação abaixo.
export type MuscleRecoveryItem = VolumeOverviewItem & {
  recovery_pct: number;
  estimated_recovery_hours: number;
  remaining_hours: number;
  status: string;
  guidance: string;
  readiness_status: string;
};

// Recorde de carga dos últimos 60 dias (M76 Lote 3b), já decidido pela
// regra única do backend (`session_records.record_events`): carga
// estritamente maior que todas as execuções anteriores na mesma chave
// exercício + equipamento + convenção. A 1ª execução não é recorde.
// `record_weight_kg` em `assisted` = a MENOR assistência. `reps` só informa.
export type RecentRecord = {
  exercise_id: number;
  exercise_name: string | null;
  equipment_id: number | null;
  equipment_name: string | null;
  load_convention: string;
  local_date: string;
  record_weight_kg: number | null;
  previous_best_weight_kg: number | null;
  reps: number | null;
};

export type IntervalsMatchedWorkout = {
  activity_id: number;
  local_date: string;
  name: string | null;
  intervals_external_id: string | null;
  moving_time_seconds: number | null;
  avg_hr_bpm: number | null;
  max_hr_bpm: number | null;
  calories_kcal: number | null;
  training_load: number | null;
  sleep_seconds: number | null;
  hrv_ms: number | null;
  resting_hr_bpm: number | null;
};

export type IntervalsWellnessRow = {
  wellness_date: string;
  sleep_seconds: number | null;
  hrv_ms: number | null;
  resting_hr_bpm: number | null;
};

export type IntervalsWellnessAverage = {
  sleep_seconds: number | null;
  hrv_ms: number | null;
  resting_hr_bpm: number | null;
  days: number;
};

// `IntervalsCorrelationService.get_training_correlation` — quando não há
// nenhuma sessão WeightTraining sincronizada do Intervals.icu na janela,
// `available` vem `false` e os demais campos vêm vazios/zerados (mesmo
// shape, sem chaves ausentes).
export type IntervalsCorrelation = {
  available: boolean;
  matched_workouts: IntervalsMatchedWorkout[];
  unmatched_workouts_count: number;
  wellness_by_training_day: IntervalsWellnessRow[];
  wellness_avg_training_days: IntervalsWellnessAverage | Record<string, never>;
  wellness_avg_rest_days: IntervalsWellnessAverage | Record<string, never>;
};

// `analytics.py::compute_rolling_weekly_trend` — janela móvel de 7 dias
// ancorada em hoje (bucket mais recente primeiro).
export type WeeklyTrendItem = {
  period: string;
  period_start: string;
  period_end: string;
  workouts: number;
  sets_count: number;
  reps_total: number;
  volume_kg: number;
  avg_rpe: number | null;
  high_rpe_sets: number;
};

// `VolumeService.get_rolling_weekly_trend_by_muscle_group` — mesma janela
// móvel, quebrada por grupo muscular.
export type WeeklyTrendByMuscleGroupItem = {
  period: string;
  period_start: string | null;
  period_end: string | null;
  muscle_group: string;
  sets_count: number;
  reps_total: number;
  volume_kg: number;
  avg_rpe: number | null;
  direct_sets: number;
  indirect_sets: number;
  stimulus_equivalent_sets: number;
};

// `ProgressionService.get_recent_sessions` — sessões recentes (até 30 dias)
// com a composição completa de exercícios, agrupadas em Python a partir de
// linhas flat (`_group_rows_into_sessions`).
export type RecentSessionExercise = {
  exercise_name: string;
  muscle_primary: string | null;
  muscle_secondary: string | null;
  sets_count: number;
  avg_weight_kg: number | null;
  avg_reps: number | null;
  avg_rpe: number | null;
};

export type RecentSession = {
  date: string;
  activity_type: string;
  exercises: RecentSessionExercise[];
};

// Snapshot de UMA exposição no contexto de progressão (exposure_service.exposure_snapshot).
export type ExerciseProgressionExposure = {
  local_date: string | null;
  /** Carga de TOPO da exposição. */
  best_load_kg: number | null;
  /** Reps das séries feitas na carga de topo (maior para menor). */
  top_set_reps: number[];
  working_sets: number;
  avg_rpe: number | null;
  e1rm: number | null;
  e1rm_reps: number | null;
  /** e1RM de série com até 12 reps — só então ele é evidência. */
  e1rm_reliable: boolean;
};

// Status de progressão por exercício da REGRA ÚNICA (`comparability`), o
// mesmo da home/drill-down/`/dashboard/progressao`. É o que a IA usa.
export type ExerciseProgressionContextItem = {
  exercise_id: number;
  exercise_name: string | null;
  status: string;
  status_label: string | null;
  status_reasons: string[];
  comparable_exposures: number;
  exposures_in_window: number;
  equipment_id: number | null;
  equipment_name: string | null;
  load_convention: string | null;
  latest: ExerciseProgressionExposure | null;
  previous_comparable: ExerciseProgressionExposure | null;
  /** Mesmo formato de `PairedReps` (dashboard-v3-types): `reps + RIR` quando `effort_adjusted`. */
  paired_top_reps: {
    pairs: number;
    previous: number[];
    current: number[];
    previous_sum: number;
    current_sum: number;
    delta: number;
    effort_adjusted?: boolean;
    previous_reps?: number[];
    current_reps?: number[];
  } | null;
};

// Payload completo de `DashboardAggregatorService.build_training_evolution_payload`,
// devolvido tal e qual (sob a chave `training`) por `POST /workouts-analysis/generate`.
export type WorkoutsAnalysisTraining = {
  days: number;
  weekly_summary: WeeklySummary;
  volume_overview: VolumeOverviewItem[];
  /** Recordes de carga (60 dias) pela regra única. Opcional: snapshots anteriores ao M76 Lote 3b não têm. */
  recent_records?: RecentRecord[];
  /** Opcional: snapshots gerados antes de 2026-10-06 não têm. */
  exercise_progression?: ExerciseProgressionContextItem[];
  recovery: MuscleRecoveryItem[];
  intervals_correlation: IntervalsCorrelation;
  weekly_trend: WeeklyTrendItem[];
  weekly_trend_by_muscle_group: WeeklyTrendByMuscleGroupItem[];
  sessions: RecentSession[];
};

export type WorkoutsAnalysisGenerateResponse = {
  model: string;
  insight: string;
  training: WorkoutsAnalysisTraining;
};

// Contrato de GET /analytics/training-evolution — mesmo payload `training`
// de `WorkoutsAnalysisTraining` acima (produzido pelo mesmo
// `DashboardAggregatorService.build_training_evolution_payload`), devolvido
// diretamente (sem o wrapper `model`/`insight` da IA) para alimentar a tela
// `/treinos` sem depender de geração por IA. Ver
// `C:\openclaw-dev-agent\fitness-frontend\src\features\workouts\WorkoutsClient.tsx`,
// que consome este endpoint via `/api/analytics/training-evolution?days=`.
export type TrainingEvolutionResponse = WorkoutsAnalysisTraining;

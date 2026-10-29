// Tipos mínimos para os endpoints de analytics consumidos pelo Dashboard
// nesta etapa. Ver `C:\muscle_fit\api\app\analytics.py` para o contrato
// completo — ampliar aqui conforme novas telas forem portadas.

import type { PlannedVolumeSeverity } from './dashboard-status';
import type { WeeklySummary } from './workouts-analysis-types';

export type TrainingTrendItem = {
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

export type TrainingTrendResponse = {
  period: 'day' | 'week' | 'month' | 'year';
  days: number;
  items: TrainingTrendItem[];
};

export type RpeDistributionItem = {
  rpe: number;
  sets_count: number;
};

export type RpeDistributionResponse = {
  days: number;
  items: RpeDistributionItem[];
};

// Linha da tabela "Ativação muscular" de /registro-exercicios/[activityId],
// devolvida por GET /analytics/muscle-activation-by-activity — ver
// VolumeService.get_muscle_activation_by_activity no backend.
// Contrato de GET /analytics/muscle-recovery — ver
// `MuscleRecoveryService.get_recovery_overview` em
// `C:\muscle_fit\api\app\intelligence\muscle_recovery_service.py` e a rota
// em `C:\muscle_fit\api\app\dashboard_modules.py` (mesma fonte usada por
// /analytics/dashboard-recovery).
export type MuscleRecoveryRecentSet = {
  exercise_id: number;
  trained_at?: string | null;
  reps?: number | null;
  rpe?: number | null;
  set_type: string;
  role: 'primary' | 'secondary';
  participation: number;
  effort_points: number;
  repetition_factor: number;
  set_type_factor: number;
};

export type MuscleRecoveryFactors = {
  session_dose: number;
  dose_last_72h: number;
  avg_rpe_used?: number | null;
  recent_sets: MuscleRecoveryRecentSet[];
  rpe10_isolated_note?: string;
};

export type MuscleRecoveryReadinessStatus = 'READY' | 'REDUCED_DOSE' | 'NO_DIRECT_WORK';

export type MuscleRecoveryRecommendation = {
  volume_multiplier: number;
  /** `null` na dose reduzida (M76 B2): a regra é `sets_rule`, não uma faixa fixa. */
  direct_sets_min: number | null;
  direct_sets_max: number | null;
  maximum_rpe: number;
  allow_failure_on_last_set: boolean;
  allow_progression: boolean;
  allow_indirect_work: boolean;
  /** Dose reduzida (M76 B2): piso de séries e texto da regra, gerados pelo modelo. */
  minimum_sets?: number;
  sets_rule?: string;
};

export type MuscleRecoveryBlockingSignalCode =
  | 'JOINT_PAIN'
  | 'MUSCLE_PAIN'
  | 'PERFORMANCE_DROP'
  /** @deprecated Não é mais emitido pela API desde 2026-09-14 (RPE alto não bloqueia). */
  | 'RPE_ANTICIPATED'
  | 'EXCESSIVE_REP_DROP'
  | 'LOW_READINESS';

export type MuscleRecoveryBlockingSignal = {
  code: MuscleRecoveryBlockingSignalCode;
  value: number | boolean;
  source_session_at: string | null;
};

export type MuscleRecoveryItem = {
  muscle_group: string;
  muscle_group_id?: number | null;
  // Contrato de prontidão (PR-09) — campos aditivos, opcionais para
  // compatibilidade com respostas antigas.
  max_rpe?: number;
  allow_failure?: boolean;
  volume_multiplier?: number;
  blocking_signals?: MuscleRecoveryBlockingSignal[];
  signals_policy?: string;
  last_session_at?: string | null;
  projected_for?: string | null;
  sessions?: number;
  sets_count?: number;
  volume_kg?: number;
  avg_weight_kg?: number | null;
  avg_rpe?: number | null;
  last_trained_at?: string | null;
  last_trained_at_ts?: string | null;
  days_since_trained?: number | null;
  recovery_pct: number;
  estimated_recovery_hours: number;
  remaining_hours: number;
  status: 'green' | 'yellow' | 'red';
  guidance: 'ideal' | 'recovering' | 'avoid';
  readiness_status: MuscleRecoveryReadinessStatus;
  session_dose: number;
  dose_last_72h: number;
  density_penalty: number;
  performance_ratio?: number | null;
  recommendation: MuscleRecoveryRecommendation;
  explanation: string[];
  factors?: MuscleRecoveryFactors;
};

export type GlobalReadinessAdjustment = {
  form_tsb: number | null;
  factor: number;
  wellness_date?: string | null;
  /** M52: wellness mais velho que `max_age_days` — fator neutro aplicado. */
  stale?: boolean;
  max_age_days?: number | null;
};

export type MuscleRecoveryResponse = {
  days: number;
  items: MuscleRecoveryItem[];
  global_readiness_adjustment?: GlobalReadinessAdjustment | null;
};

// Contrato de GET /analytics/muscle-group-body-map — mapeamento de cada
// grupo muscular para os `slugs` das regiões do mapa corporal (usado pelo
// MuscleBodyMap; regiões em `components/bodymap/regions.v1.ts`). Os nomes dos
// slugs vêm do antigo react-body-highlighter e foram mantidos no v1.
export type MuscleGroupBodyMapItem = {
  muscle_group: string;
  slugs: string[];
};

export type MuscleGroupBodyMapResponse = {
  items: MuscleGroupBodyMapItem[];
};

export type MuscleActivationRow = {
  muscle_group: string;
  primary_series: number;
  primary_avg_weight_kg: number | null;
  primary_equivalent: number;
  secondary_series: number;
  secondary_avg_weight_kg: number | null;
  secondary_equivalent: number;
  stabilizer_series: number;
  stabilizer_avg_weight_kg: number | null;
  stabilizer_equivalent: number;
  total_equivalent: number;
  /**
   * Só em `GET /analytics/muscle-activation-by-activity` (M76 E4): status do
   * grupo contra o alvo, calculado no backend
   * (`volume_service.assess_activation_against_target`). A tela só desenha.
   */
  assessment?: MuscleActivationAssessment;
};

export type MuscleActivationTargetSource = 'template_slot' | 'training_type' | 'generic_fallback';

export type MuscleActivationAssessment = {
  /** Código no domínio `status_domain` de `dashboard-status.ts`. */
  status: string;
  status_domain: 'weeklyVolume' | 'plannedVolume';
  target_source: MuscleActivationTargetSource;
  /** Alvo e teto em séries equivalentes; `null` na meta genérica. */
  target_sets: number | null;
  max_sets: number | null;
  /**
   * Gravidade aditiva do `BELOW_TARGET` planejado
   * (`volume_service.planned_volume_severity`): `'FAR_BELOW'` = "muito abaixo
   * do planejado". Só vem com alvo do slot; `null` nos demais casos.
   */
  severity?: PlannedVolumeSeverity | null;
};

// Contrato de POST /analytics/muscle-activation-breakdown — mesma fonte de
// dados de /analytics/muscle-activation-preview
// (VolumeService.get_muscle_activation_breakdown_by_exercise), mas SEM
// agregar por grupo do plano inteiro: devolve a ativação POR EXERCÍCIO, um
// item de saída por item de entrada `{mapping_id, sets_count}[]`, na MESMA
// ordem/tamanho — usada pela sub-linha de detalhe de ativação muscular por
// linha da tabela de exercícios em /planos-treino (ExercisesTable.tsx).
// Contrato confirmado lendo `C:\muscle_fit\api\app\analytics.py` (rota
// muscle_activation_breakdown) e
// `intelligence/volume_service.py::get_muscle_activation_breakdown_by_exercise`.
export type MuscleActivationBreakdownItem = {
  mapping_id: number | null;
  primary_activation: number;
  secondary: { muscle_group_name: string; activation: number }[];
  stabilizer: { muscle_group_name: string; activation: number }[];
};

// Contrato de GET /cardio-recommendations — item de
// `cardio_recommendations` (log de auditoria imutável, sem PUT). Confirmado
// lendo `app/cardio_recommendations.py` (SELECT em list_cardio_recommendations).
export type CardioRecommendation = {
  id: number;
  reference_date: string;
  workout_plan_id: number | null;
  generated_at: string;
  model: string | null;
  /**
   * @deprecated Na listagem (GET /cardio-recommendations) vem sempre `null`:
   * o JSON (~110 KB/linha) estourava o limite de 1 MiB da RDS Data API.
   * Só GET /cardio-recommendations/{id} devolve o snapshot completo.
   */
  context_snapshot?: unknown;
  recommendation_markdown: string;
  created_at: string;
  /** `cardio-agent-v3` (098, M98 C3a) nas estruturadas; null nas antigas. */
  prompt_version?: string | null;
};

export type CardioRecommendationsResponse = {
  items: CardioRecommendation[];
};

// M98 C3b: `recommendation_view` de GET /cardio-recommendations/{id}
// (`cardio_recommendation_v3.build_view`). Tudo pronto para exibir: rótulos,
// tempo total e se a sessão pode ir para o Modelo. A tela não calcula nada.
export type CardioRecommendationSessionView = {
  date: string;
  date_label: string;
  prescription_text: string;
  prescription_short: string;
  total_minutes_label: string;
  rationale: string | null;
  signals: string[];
  adjustment_labels: string[];
  slot_name: string | null;
  plan_action: 'replace' | 'add' | null;
  applicable: boolean;
};

export type CardioRecommendationNoteView = {
  date: string | null;
  date_label: string;
  action: 'adjusted' | 'rejected' | string | null;
  code: string;
  label: string;
  detail_label: string | null;
};

export type CardioRecommendationView = {
  summary: string | null;
  objective: string | null;
  confidence_label: string | null;
  confidence_reason: string | null;
  sessions: CardioRecommendationSessionView[];
  notes: CardioRecommendationNoteView[];
  plan: { template_id: number | null; template_name: string | null; template_version_id: number | null } | null;
  can_apply: boolean;
};

export type CardioRecommendationDetail = CardioRecommendation & {
  recommendation_view: CardioRecommendationView | null;
};

// POST /cardio-recommendations/{id}/apply (grava no RASCUNHO do Modelo).
export type CardioApplyResultItem = {
  date: string;
  status: 'applied' | 'already_applied' | 'rejected';
  code: string;
  message: string;
  slot_sequence: number | null;
  slot_name: string | null;
};

export type CardioApplyResponse = {
  recommendation_id: number;
  template_id: number;
  template_name: string | null;
  draft_saved: boolean;
  has_draft: boolean;
  draft_updated_at: string | null;
  results: CardioApplyResultItem[];
};

// GET /workout-plans/{id}/cardio (Modo Treino ao encerrar a força).
export type PlanCardioZoneRange = {
  zone: number;
  label: string;
  lower_bpm: number | null;
  upper_bpm: number | null;
};

export type PlanCardioStep = {
  kind: 'warmup' | 'main' | 'intervals' | 'cooldown';
  label: string;
  text: string;
  zones: PlanCardioZoneRange[];
};

export type PlanCardioItem = {
  mode: string;
  mode_label: string;
  text: string;
  short: string;
  total_minutes_label: string;
  steps: PlanCardioStep[];
};

export type PlanCardioResponse = {
  workout_plan_id: number;
  slot_name: string | null;
  items: PlanCardioItem[];
  zones_source: string | null;
};

// Contrato do padrão assíncrono 202 + job_id + polling (Fase 3 da migração
// AWS — ver `app/async_jobs.py` e `app/jobs.py`). `POST
// /cardio-recommendations/generate` retorna só `{job_id, status}`; o
// resultado final vem de `GET /jobs/{job_id}`, campo `result`, no mesmo
// formato de `cardio_generation_core.dispatch_cardio_generation`.
export type AsyncJobStatus = 'pending' | 'done' | 'failed';

export type CardioGenerationResult = {
  id: number;
  model: string | null;
  reference_date: string;
  workout_plan_id: number | null;
  recommendation_markdown: string;
  prompt_version?: string | null;
};

export type AsyncJob<TResult> = {
  job_id: string;
  job_type: string;
  status: AsyncJobStatus;
  created_at: number | null;
  finished_at: number | null;
  result?: TResult;
  // Mensagem já amigável (pt-BR) — inclusive no `worker_lost` (M73: a Lambda
  // morreu e o backend fechou o job por prazo).
  error?: string;
  error_code?: string;
};

// Contrato de GET /analytics/body-composition-trend — ver
// `BodyCompositionTrendService.get_body_trend` em
// `C:\muscle_fit\api\app\intelligence\body_composition_trend_service.py`
// (rota registrada em `dashboard_modules.py`).
export type BodyCompositionTrendItem = {
  local_date: string;
  weight_kg: number | null;
  bmi: number | null;
  body_fat_percent: number | null;
  muscle_mass_kg: number | null;
  body_water_percent: number | null;
  visceral_fat_rating: number | null;
  fat_free_mass_kg: number | null;
  subcutaneous_fat_percent: number | null;
  protein_percent: number | null;
  metabolic_age: number | null;
};

export type BodyCompositionTrend = {
  status?: 'insufficient_data';
  weight_velocity_per_day?: number | null;
  muscle_velocity_per_day?: number | null;
  fat_velocity_per_day?: number | null;
  water_velocity_per_day?: number | null;
};

export type BodyCompositionTrendResponse = {
  days: number;
  items: BodyCompositionTrendItem[];
  trend: BodyCompositionTrend;
};

// Contrato de GET /analytics/exercise-progress — ver rota `exercise_progress`
// em `C:\muscle_fit\api\app\analytics.py`.
export type ExerciseProgressItem = {
  local_date: string;
  activity_name: string;
  original_name: string;
  mapped_name?: string | null;
  sets_count: number;
  reps_total: number;
  best_weight_kg: number | null;
  volume_kg: number;
  avg_rpe: number | null;
  high_rpe_sets: number;
};

export type ExerciseProgressResponse = {
  name: string;
  days: number;
  items: ExerciseProgressItem[];
};

// Contrato de GET /analytics/muscle-groups-daily-activation — usado pela
// grade de ativação muscular (últimos 7 dias) de `/treinos`. Ver
// `C:\openclaw-dev-agent\fitness-frontend\src\features\workouts\WorkoutsClient.tsx`
// (fonte de verdade da regra: MIN_ACTIVATION_SETS = 9 séries + ao menos uma
// série em RPE 9-10 no mesmo dia marca a célula como "ativado").
export type MuscleGroupsDailyActivationItem = {
  local_date: string;
  muscle_group: string;
  /** Aditivo (tabela de 7 dias de /dashboard/musculos); ausente em respostas antigas. */
  muscle_group_id?: number | null;
  /** Séries EQUIVALENTES (`sets × stimulus_weight`, todos os papéis). */
  sets_count: number;
  /**
   * Séries DIRETAS: só contribuições PRIMARY, sem peso — a unidade das faixas
   * dos macro-grupos de volume (motor `methodology_volume`). Aditivo.
   */
  direct_sets?: number | null;
  had_rpe_9_10: boolean;
};

export type MuscleGroupsDailyActivationResponse = {
  days: number;
  items: MuscleGroupsDailyActivationItem[];
};

// Contrato de GET /analytics/methodology-realized (M76 E6) — metodologia do
// REALIZADO nos últimos 7 dias, por BLOCO (fatia -> bloco pela cadeia
// inteira), com os status prontos (`intelligence/methodology_realized.py`).
// Status no vocabulário do motor: WITHIN_RANGE / BELOW_RANGE / ABOVE_RANGE /
// NOT_EVALUABLE (sem faixa) / NOT_DIRECTLY_TRAINED (média sem treino).
export type MethodologyRealizedStatus =
  | 'WITHIN_RANGE'
  | 'BELOW_RANGE'
  | 'ABOVE_RANGE'
  | 'NOT_EVALUABLE'
  | 'NOT_DIRECTLY_TRAINED';

export type MethodologyRealizedRange = { min: number; max: number };

export type MethodologyRealizedDay = {
  date: string;
  direct_sets: number;
  effective_sets: number;
  /** `null` = dia sem séries diretas. */
  status: MethodologyRealizedStatus | null;
};

export type MethodologyRealizedItem = {
  muscle_group_id: number | null;
  muscle_group: string;
  /** Grupos (bloco e fatias) que somaram nesta linha. */
  member_groups: Array<{ muscle_group_id: number | null; muscle_group: string }>;
  volume_class_name: string | null;
  days: MethodologyRealizedDay[];
  days_trained: number;
  frequency_bucket: '1x' | '2x' | '3x_plus' | null;
  session_range: MethodologyRealizedRange | null;
  direct_sets_per_session: number | null;
  session_status: MethodologyRealizedStatus;
  weekly_total: number;
  weekly_range: MethodologyRealizedRange | null;
  weekly_status: MethodologyRealizedStatus;
};

export type MethodologyRealizedResponse = {
  metric_version: string;
  /** Datas ISO da janela, da mais antiga para a mais recente (hoje do usuário). */
  days: string[];
  items: MethodologyRealizedItem[];
};

// Contrato de GET /analytics/muscle-recovery-consecutive-check — pares de
// dias consecutivos em que um grupo muscular foi treinado, com a checagem
// REAL de recuperação (elapsed_hours vs required_hours, calculada pelo
// backend), não uma detecção puramente booleana de "dias seguidos". Cruzado
// com a grade de ativação de 7 dias para marcar descanso insuficiente.
export type MuscleRecoveryConsecutiveCheckItem = {
  muscle_group_id?: number | null;
  muscle_group: string;
  date_from: string;
  date_to: string;
  elapsed_hours: number;
  required_hours: number;
  insufficient_recovery: boolean;
  timestamp_precision: 'exact' | 'day_fallback';
};

export type MuscleRecoveryConsecutiveCheckResponse = {
  days: number;
  items: MuscleRecoveryConsecutiveCheckItem[];
};

// ─── Agente de cardio (/agente-cardio) — contrato de GET
// /analytics/cardio-agent-context, portado de
// `C:\openclaw-dev-agent\fitness-frontend\src\lib\analytics-types.ts`. Ver
// `C:\muscle_fit\api\app\intelligence\cardio_context_service.py`
// (CardioAgentContextService.build_context) para o contrato completo. A
// tela `/agente-cardio` do AWS só renderiza os blocos Intervals.icu, sono,
// esforço EPOC/PAI e a tabela de recuperação muscular — os demais campos
// ficam disponíveis no tipo por completude/paridade com o contrato do
// backend, mesmo sem uso na tela hoje.
export type CardioAgentRecentWorkout = {
  activity_id: number;
  local_date: string;
  activity_name?: string | null;
  activity_type?: string | null;
  duration_seconds?: number | null;
  activity_avg_rpe?: number | null;
  muscle_groups: string[];
  exercises_count: number;
  sets_count: number;
  reps_total: number;
  volume_kg: number;
  avg_rpe?: number | null;
};

export type CardioAgentIntervalsWellnessDay = {
  wellness_date: string;
  fitness_ctl?: number | null;
  fatigue_atl?: number | null;
  form_tsb?: number | null;
  ramp_rate?: number | null;
  weight_kg?: number | null;
  resting_hr_bpm?: number | null;
  hrv_ms?: number | null;
  sleep_seconds?: number | null;
  readiness_score?: number | null;
  daily_effort_load?: number | null;
  synced_at?: string | null;
};

// M99: EPOC e PAI ACUMULADOS DE 7 DIAS, ESTIMADOS pelo backend (motor
// `cardio_load`) do tempo em zona de FC do Intervals; só os dias com valor,
// do mais recente para o mais antigo.
export type CardioAgentEffortEstimate = {
  local_date: string;
  epoc_7d_estimated: number | null;
  pai_7d_estimated: number | null;
  model_version: string;
};

export type CardioAgentSleepMetric = {
  id: number;
  local_date: string;
  duration_hours?: number | null;
  regularity_percent?: number | null;
  deep_sleep_hours?: number | null;
  rem_sleep_hours?: number | null;
  awake_count?: number | null;
  awake_hours?: number | null;
  notes?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type CardioAgentPastRecommendation = {
  id: number;
  reference_date: string;
  generated_at: string;
  model?: string | null;
  recommendation_markdown: string;
};

export type CardioAgentContextResponse = {
  days: number;
  body_composition: BodyCompositionTrendResponse;
  recent_workouts: CardioAgentRecentWorkout[];
  muscle_recovery: MuscleRecoveryItem[];
  intervals_wellness: CardioAgentIntervalsWellnessDay[];
  effort_metrics_estimated: CardioAgentEffortEstimate[];
  sleep_metrics: CardioAgentSleepMetric[];
  weekly_training_load: WeeklySummary;
  past_recommendations: CardioAgentPastRecommendation[];
};

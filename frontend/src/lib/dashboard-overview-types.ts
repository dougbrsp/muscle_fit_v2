/**
 * Tipos da visão geral do Novo Dashboard (`GET /analytics/dashboard/overview`
 * e `/insights`, F1.6) — espelho dos contratos Pydantic de
 * `api/app/intelligence/dashboard/contracts.py` (`OverviewResponse`,
 * `InsightsResponse`, `Insight`).
 *
 * Nada aqui recalcula regra: contagens, frases de resposta, severidade,
 * evidências, comparação, confiança, alternativas, ação e data de reavaliação
 * chegam prontas do backend. A tela só apresenta.
 */

import type {
  DashboardWindow,
  DataNature,
  DataQuality,
  FatigueContext,
  Metric,
  Severity,
  TargetSource,
} from './dashboard-v3-types';
import type { ReadinessReading } from './dashboard-recovery-types';
import type { CycleSlot } from './dashboard-workouts-types';

// ---------------------------------------------------------------------------
// Insight (§12)
// ---------------------------------------------------------------------------

export type InsightEvidence = {
  metric: string;
  from_value: number | null;
  to_value: number | null;
  unit: string | null;
  nature: DataNature;
  observed_at: string | null;
};

export type InsightComparison = {
  basis: string;
  window: string;
  keys: Record<string, unknown>;
};

/**
 * Memória de exibição do alerta (`dashboard_alert_states`, migração 051; F2.2).
 *
 * É contrato, não detalhe interno: um cartão que desaparece sem explicação é
 * indistinguível de defeito. Com este bloco a tela pode dizer "você já viu isso
 * em 04/09, silenciado até 09/10".
 *
 * `reason`: `first_seen | new_episode | escalated | cooldown_expired` aparecem;
 * `cooldown_active` é o único que silencia. `escalated` é o alerta que PIOROU de
 * severidade e por isso furou o cooldown.
 */
export type InsightCooldown = {
  alert_key: string;
  reason:
    | 'first_seen'
    | 'new_episode'
    | 'escalated'
    | 'cooldown_expired'
    | 'cooldown_active';
  suppressed: boolean;
  first_seen_at: string | null;
  last_emitted_at: string | null;
  emitted_count: number;
  suppressed_count: number;
  snoozed_until: string | null;
  metric_version: string;
};

export type Insight = {
  code: string;
  question: string;
  status: string;
  severity: Severity;
  answer: string;
  evidence: InsightEvidence[];
  comparison: InsightComparison | null;
  data_quality: DataQuality;
  limitations: string[];
  alternative_explanations: string[];
  suggested_action: string | null;
  reassess_after: string | null;
  source_screen: string;
  fingerprint: string;
  /**
   * `null` = não houve decisão de cooldown (antes da migração 051 ou com
   * `DASHBOARD_ALERT_COOLDOWN=0`) — nunca "não silenciado".
   */
  cooldown: InsightCooldown | null;
};

// ---------------------------------------------------------------------------
// Blocos
// ---------------------------------------------------------------------------

export type OverviewBlockBase = {
  question: string;
  answer: string;
  drilldown_href: string;
  data_quality: DataQuality;
  limitations: string[];
};

export type ConsistencyBlock = OverviewBlockBase & {
  has_template: boolean;
  template_name: string | null;
  cycle_length_days: number | null;
  next_slot: CycleSlot | null;
  cycle_slots_done: number;
  cycle_slots_total: number;
  expected_sessions: Metric;
  completed_sessions: Metric;
  planned_sessions: Metric;
  unplanned_sessions: Metric;
  missed_sessions: Metric;
  rate: Metric;
  cycle_rate: Metric;
};

export type ProgressionExercise = {
  exercise_id: number;
  name: string | null;
  status: string;
  status_reasons: string[];
  /** Condição de fadiga em que o status vale (opcional: backend anterior não envia). */
  fatigue_context?: FatigueContext | null;
  comparable_exposures: number;
  exposures_in_window: number;
  last_local_date: string | null;
};

export type ProgressionStatusCount = {
  status: string;
  label: string;
  count: number;
  exercises: ProgressionExercise[];
};

export type ProgressionBlock = OverviewBlockBase & {
  exercises_total: number;
  comparable_total: number;
  counts: ProgressionStatusCount[];
  exposures_per_exercise: number;
  truncated: boolean;
};

export type PriorityMuscle = {
  muscle_group_id: number;
  muscle_group: string;
  status: string;
  weekly_effective_sets: Metric;
  target_min: Metric;
  target_max: Metric;
  target_source: TargetSource;
  direct_sets: Metric;
  indirect_sets: Metric;
  direct_sessions: Metric;
  hours_since_last_stimulus: Metric;
  last_direct_local_date: string | null;
};

export type PriorityMusclesBlock = OverviewBlockBase & {
  groups_total: number;
  groups_with_target: number;
  target_source: TargetSource;
  below_min: PriorityMuscle[];
  above_max: PriorityMuscle[];
  frequency_lagging: PriorityMuscle[];
};

export type WeightPoint = {
  local_date: string;
  weight_kg: number;
  source: string;
  ma7_kg: number | null;
  ma7_points: number;
};

export type BodyBlock = OverviewBlockBase & {
  weight_latest: Metric;
  weight_ma7_latest: Metric;
  weight_ma7_change: Metric;
  weight_ma7_change_pct: Metric;
  weighings_count: number;
  weighings_per_week: Metric;
  weight_points: WeightPoint[];
  body_fat_percent: Metric;
  muscle_mass_kg: Metric;
  bioimpedance_local_date: string | null;
  waist_cm: Metric;
};

export type RecoveryBlock = OverviewBlockBase & {
  window: DashboardWindow;
  level: Severity;
  readiness_reading: ReadinessReading;
  headline: string;
  uncertainty: DataQuality;
  allow_intensification: boolean;
  intensification_blocked_reason: string | null;
  groups_total: number;
  groups_ready: number;
  groups_reduced_dose: number;
  groups_no_direct_work: number;
  signals_agreeing: number;
  signals_diverging: number;
  signals_unavailable: number;
  reassess_after_hours: number | null;
};

export type NextActionsBlock = OverviewBlockBase & {
  items: Insight[];
  total_triggered: number;
  max_items: number;
};

export type OverviewBlocks = {
  consistency: ConsistencyBlock;
  progression: ProgressionBlock;
  priority_muscles: PriorityMusclesBlock;
  body: BodyBlock;
  recovery: RecoveryBlock;
  next_actions: NextActionsBlock;
};

export type OverviewResponse = {
  window: DashboardWindow;
  timezone: string;
  blocks: OverviewBlocks;
  /** A PIOR qualidade entre os blocos. */
  data_quality: DataQuality;
  /** Veredito do `data_quality_service` sobre os DADOS (mesmo `overall` de `/dashboard/data-quality`). */
  data_quality_overall: DataQuality;
  data_quality_issues: number;
  target_source: TargetSource;
  training_type_id: number | null;
  limitations: string[];
  metric_versions: Record<string, string>;
};

export type InsightsResponse = {
  window: DashboardWindow;
  items: Insight[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  rules_evaluated: number;
  rules_disabled: string[];
  data_quality: DataQuality;
  limitations: string[];
  metric_versions: Record<string, string>;
  /** Quantos ficaram de fora por já terem sido vistos (F2.2). */
  suppressed_total: number;
  include_suppressed: boolean;
  /** `false` antes da migração 051 ou com a flag desligada: nada foi silenciado. */
  cooldown_available: boolean;
};

/**
 * Tipos de `GET /analytics/dashboard/recovery-signals` (F1.5) — espelho dos
 * contratos Pydantic de `api/app/intelligence/dashboard/contracts.py`.
 *
 * Arquivo separado de `dashboard-v3-types.ts` só por tamanho; as regras são as
 * mesmas: o tipo É o contrato e nada aqui recalcula. Direção, leitura,
 * concordância, nível e incerteza chegam prontos do `response_model` — a tela
 * só apresenta.
 *
 * Não confundir com `dashboard-v2-types.ts`, que tipa a rota antiga
 * `/analytics/dashboard-recovery` (ainda existente, consumida por
 * `/recuperacao-muscular`, que a F2.4 manteve viva por falta de paridade).
 */

import type { DashboardWindow, DataQuality, Metric, Severity } from './dashboard-v3-types';

/** Onde o valor da janela caiu em relação à baseline pessoal. */
export type SignalDirection = 'ABOVE' | 'IN_LINE' | 'BELOW' | 'UNKNOWN';

/** O que a direção significa para recuperação (depende do sinal). */
export type SignalReading = 'FAVORABLE' | 'NEUTRAL' | 'UNFAVORABLE' | 'UNKNOWN';

/** Relação do sinal com a leitura de prontidão do modelo. */
export type SignalAgreement = 'AGREES' | 'DIVERGES' | 'NEUTRAL' | 'NO_BASELINE';

/** Resumo da prontidão do modelo — só para dizer com o que os sinais concordam. */
export type ReadinessReading = 'RECOVERED' | 'STRAINED' | 'UNKNOWN';

/** Status de prontidão do modelo `fullbody-no-reps-v1`. */
export type ReadinessStatus = 'READY' | 'REDUCED_DOSE' | 'NO_DIRECT_WORK';

export type RecoverySignal = {
  code: string;
  label: string;
  description: string;
  current: Metric;
  /** `value: null` = sem baseline suficiente. NUNCA um número inventado. */
  baseline: Metric;
  delta: Metric;
  direction: SignalDirection;
  reading: SignalReading;
  agreement: SignalAgreement;
  observations: number;
  baseline_observations: number;
  min_observations: number;
  source: string;
  limitations: string[];
};

/** Evento de contexto — sempre `[]` até a F2.1 criar `context_events`. */
export type ContextEvent = {
  event_type: string;
  local_date: string;
  end_local_date: string | null;
  intensity: number | null;
  notes: string | null;
};

export type RecoveryAssessment = {
  level: Severity;
  readiness_reading: ReadinessReading;
  headline: string;
  uncertainty: DataQuality;
  allow_intensification: boolean;
  intensification_blocked_reason: string | null;
  agreeing_codes: string[];
  diverging_codes: string[];
  unavailable_codes: string[];
  explanation: string[];
  reassess_after_hours: number | null;
  context_events: ContextEvent[];
};

/**
 * Recomendação de séries do modelo `fullbody-no-reps-v1` (M33) — a MESMA
 * tabela que governa a geração de ficha e o Modo Treino. A tela não escolhe
 * número de série nenhum; até a F2.5 este bloco só existia em
 * `/recuperacao-muscular`.
 */
export type RecoveryRecommendation = {
  direct_sets_min: number | null;
  direct_sets_max: number | null;
  maximum_rpe: number | null;
  allow_failure_on_last_set: boolean | null;
  allow_progression: boolean | null;
  allow_indirect_work: boolean | null;
  volume_multiplier: number | null;
  /**
   * Dose reduzida (M76 B2): piso de séries e o texto da regra
   * (`ceil(S × multiplicador)` por exercício), escritos pelo modelo. Com
   * `sets_rule`, `direct_sets_min/max` vêm `null`: não há faixa fixa.
   */
  minimum_sets?: number | null;
  sets_rule?: string | null;
};

/** Uma série recente do grupo, com os fatores que a viraram dose. */
export type RecoveryFactorSet = {
  trained_at: string | null;
  reps: number | null;
  rpe: number | null;
  set_type: string;
  role: string;
  participation: number | null;
  effort_points: number | null;
  repetition_factor: number | null;
  set_type_factor: number | null;
};

/**
 * De ONDE saiu a prontidão do grupo (M33). Tudo repassado do backend —
 * `null` é "não dá para calcular", nunca zero.
 */
export type RecoveryFactors = {
  session_dose: number | null;
  dose_last_72h: number | null;
  avg_rpe_used: number | null;
  avg_rpe_window: number | null;
  density_penalty: number | null;
  signal_penalty: number | null;
  performance_ratio: number | null;
  recent_sets: RecoveryFactorSet[];
  notes: string[];
};

/**
 * Ajuste global de fadiga (TSB do Intervals.icu) que o modelo APLICOU no
 * tempo estimado de recuperação (M33). O campo inteiro é `null` sem
 * Intervals.icu sincronizado — nunca "fator 1,0".
 */
export type GlobalReadinessAdjustment = {
  factor: number;
  form_tsb: number | null;
  wellness_date: string | null;
  /**
   * M52: o último wellness do Intervals.icu passou da idade máxima
   * (`max_age_days`). O fator aplicado foi neutro (1,0) e `form_tsb` é o
   * valor IGNORADO — a tela mostra um aviso em vez do ajuste.
   */
  stale?: boolean;
  max_age_days?: number | null;
  description: string;
};

/** Uma métrica recortada por dia COM treino × dia SEM treino (M36). */
export type TrainingVsRestMetric = {
  code: string;
  label: string;
  unit: string;
  source: string;
  higher_is_better: boolean;
  training_days: Metric;
  rest_days: Metric;
  delta: Metric;
  direction: SignalDirection;
  reading: SignalReading;
  training_observations: number;
  rest_observations: number;
  min_days: number;
  limitations: string[];
};

/**
 * Sono, HRV e FC de repouso em dia com treino × dia sem treino (M36).
 * Descritivo: não entra em `assessment.level` nem em contagem de sinais.
 */
export type TrainingVsRest = {
  training_days: number;
  rest_days: number;
  min_days: number;
  metrics: TrainingVsRestMetric[];
  limitations: string[];
};

export type RecoveryGroup = {
  muscle_group_id: number | null;
  muscle_group: string;
  readiness_status: ReadinessStatus | string;
  guidance: string | null;
  recovery_pct: Metric;
  estimated_recovery_hours: Metric;
  remaining_hours: Metric;
  hours_since_last_stimulus: Metric;
  session_dose: Metric;
  last_session_at: string | null;
  last_trained_local_date: string | null;
  /**
   * "Bom para": primeiro instante em que o modelo projeta o grupo READY, em ISO
   * COM o fuso do usuário (`recovery_projection.ready_forecast`). `null` com
   * `ready_label` = já está pronto; `null` sem rótulo = sem previsão.
   */
  ready_at: string | null;
  ready_label: string | null;
  max_rpe: number | null;
  allow_failure: boolean | null;
  volume_multiplier: number | null;
  blocking_signal_codes: string[];
  explanation: string[];
  /** M33: o detalhamento que só existia em `/recuperacao-muscular`. */
  recommendation: RecoveryRecommendation | null;
  factors: RecoveryFactors | null;
};

export type RecoverySignalsResponse = {
  window: DashboardWindow;
  metric_versions: Record<string, string>;
  data_quality: DataQuality;
  limitations: string[];
  groups: RecoveryGroup[];
  signals: RecoverySignal[];
  assessment: RecoveryAssessment;
  /** M36 / M33 — o que fechou a paridade com `/recuperacao-muscular` e `/treinos`. */
  training_vs_rest: TrainingVsRest;
  global_readiness_adjustment: GlobalReadinessAdjustment | null;
  baseline_window_days: number;
  readiness_model: string;
};

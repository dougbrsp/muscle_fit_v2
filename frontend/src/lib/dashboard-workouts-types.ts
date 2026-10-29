/**
 * Tipos de `GET /analytics/dashboard/workouts`, `/workouts/{activity_id}` e
 * `/adherence` (F1.1) — espelho dos contratos Pydantic de
 * `api/app/intelligence/dashboard/contracts.py`.
 *
 * Arquivo separado de `dashboard-v3-types.ts` só por tamanho: as regras são as
 * mesmas (o tipo É o contrato, nada aqui recalcula). Status de item, contagens
 * e limitações chegam prontos do `response_model`.
 */

import type { DashboardWindow, DataQuality, Metric } from './dashboard-v3-types';

/** Status DERIVADO de um item da ficha dentro de uma sessão (plano §5). */
export type PlanItemStatus =
  | 'COMPLETED'
  | 'INTERRUPTED'
  | 'SKIPPED'
  | 'SUBSTITUTED'
  | 'EXTRA';

/** `activities.session_status` normalizado (`IMPORTED` = o `NULL` histórico). */
export type SessionStatus = 'DONE' | 'IN_PROGRESS' | 'IMPORTED';

/** De onde saiu o prescrito do item — snapshot congelado ou ficha legada. */
export type PrescriptionSource = 'frozen_snapshot' | 'plan_columns' | 'unavailable';

export type ItemStatusCounts = {
  completed: number;
  interrupted: number;
  skipped: number;
  substituted: number;
  extra: number;
};

/**
 * Ligação REAL entre sessão e ficha/slot. É o que substitui o agrupamento por
 * string de nome da tela v2: "Treino A" e "treino a " são o mesmo slot.
 */
export type PlanLink = {
  workout_plan_id: number | null;
  plan_name: string | null;
  template_id: number | null;
  template_slot_sequence: number | null;
  template_slot_name: string | null;
};

/**
 * Grupo da sessão na lista, pronto do dono
 * (`slot_session_comparison.session_group_of`): chave = Modelo + posição do
 * slot; `label`/`template_name` são só rótulo.
 */
export type WorkoutSessionGroup = {
  key: string;
  kind: 'slot' | 'no_template' | 'unlinked';
  label: string;
  template_id: number | null;
  template_name: string | null;
  /** Raiz da linhagem do Modelo (cópia → origem); a chave do grupo usa ela. */
  lineage_template_id: number | null;
  slot_sequence: number | null;
};

export type WorkoutSession = {
  activity_id: number;
  local_date: string | null;
  started_at: string | null;
  name: string | null;
  session_status: SessionStatus;
  /** M97 F4: origem do deload (`period`/`manual`; `null` = sessão normal) e o rótulo pronto do backend. */
  deload_source?: string | null;
  deload_label?: string | null;
  duration_seconds: number | null;
  exercises_count: number;
  physical_sets: Metric;
  effective_sets_stimulus: Metric;
  effective_sets_fatigue: Metric;
  avg_rpe: Metric;
  records: Metric;
  progressions: Metric;
  item_status_counts: ItemStatusCounts;
  plan_link: PlanLink | null;
  group: WorkoutSessionGroup;
  data_quality: DataQuality;
  limitations: string[];
};

export type WorkoutsResponse = {
  window: DashboardWindow;
  items: WorkoutSession[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  data_quality: DataQuality;
  limitations: string[];
  metric_versions: Record<string, string>;
};

export type Prescribed = {
  source: PrescriptionSource;
  sets_count: number | null;
  reps_target: string | null;
  load_target: string | null;
  rpe_target: string | null;
  rest_seconds: number | null;
  engine_version: string | null;
};

export type PerformedSet = {
  set_id: number;
  set_number: number | null;
  set_type: string;
  is_working: boolean;
  reps: number | null;
  weight_kg: number | null;
  rpe: number | null;
  rest_seconds: number | null;
};

export type Performed = {
  exercise_row_id: number | null;
  /** Id CANÔNICO (`exercise_catalog`) — o que `/dashboard/exercicios/[id]` espera. */
  exercise_id: number | null;
  name: string | null;
  position_in_session: number | null;
  /** Séries de trabalho (sem aquecimento/ajuste nem aplicação de técnica). */
  working_sets: number;
  /** Linhas de aplicação de técnica avançada do item (Fase 5). */
  technique_sets?: number;
  total_sets: number;
  total_reps: number | null;
  best_load_kg: number | null;
  avg_rpe: number | null;
  sets: PerformedSet[];
};

export type PlanItemSubstitution = {
  planned_exercise_id: number | null;
  planned_name: string | null;
  performed_exercise_id: number | null;
  performed_name: string | null;
};

/** "Técnica aplicada X/N" do item — indicador próprio, fora da completude. */
export type PlanItemTechnique = {
  code: string | null;
  /** N: aplicações prescritas (só técnica de aplicação; FST-7/GVT = null). */
  planned_applications: number | null;
  /** X: aplicações registradas. */
  performed_applications: number;
  /** Séries equivalentes que a técnica somou ao volume do grupo. */
  equivalent_sets: number | null;
};

export type PlanItem = {
  status: PlanItemStatus;
  plan_exercise_id: number | null;
  plan_sequence: number | null;
  planned_name: string | null;
  prescribed: Prescribed;
  performed: Performed | null;
  /** Só em `INTERRUPTED`: quanto faltou para o prescrito. `null` = sem prescrito. */
  missing_sets: number | null;
  technique?: PlanItemTechnique | null;
  substitution: PlanItemSubstitution | null;
  is_record: boolean;
  notes: string | null;
  limitations: string[];
};

export type WorkoutDetailResponse = {
  window: DashboardWindow;
  session: WorkoutSession;
  items: PlanItem[];
  sets_limit: number;
  sets_truncated: boolean;
  notes: string | null;
  data_quality: DataQuality;
  limitations: string[];
  metric_versions: Record<string, string>;
};

/** Slot do Modelo na posição do ciclo — mesmo vocabulário de `/dashboard/ciclo`. */
export type CycleSlot = {
  sequence: number;
  name: string | null;
  slot_type: string | null;
  weekday: number | null;
  status: 'done' | 'next' | 'pending';
  last_activity_id: number | null;
  last_local_date: string | null;
};

export type AdherenceResponse = {
  window: DashboardWindow;
  has_template: boolean;
  template_id: number | null;
  template_name: string | null;
  cycle_length_days: number | null;
  slots: CycleSlot[];
  next_slot: CycleSlot | null;
  expected_sessions: Metric;
  completed_sessions: Metric;
  planned_sessions: Metric;
  unplanned_sessions: Metric;
  missed_sessions: Metric;
  /** Sessões com ficha / esperadas. `null` sem Modelo — nunca 0. */
  rate: Metric;
  cycle_rate: Metric;
  data_quality: DataQuality;
  limitations: string[];
  metric_versions: Record<string, string>;
};

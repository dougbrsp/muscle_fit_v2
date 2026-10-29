/**
 * Tipos de `GET /analytics/dashboard/engine` e
 * `/engine/plans/{plan_id}` (F3.1) — espelho dos contratos Pydantic de
 * `api/app/intelligence/dashboard/contracts.py`.
 *
 * O tipo É o contrato: nada aqui recalcula status, limitação ou contagem. As
 * três colunas da tela (planejado × gerado × executado) chegam prontas, e as
 * duas primeiras reusam `Prescribed`/`Performed` de `dashboard-workouts-types`
 * de propósito — "o que o motor prescreveu" e "o que a sessão registrou" são
 * exatamente os mesmos objetos de `/dashboard/treinos`, e duplicá-los aqui
 * criaria dois formatos para o mesmo dado.
 */

import type { DashboardWindow, DataQuality } from './dashboard-v3-types';
import type {
  ItemStatusCounts,
  Performed,
  PlanItemStatus,
  PlanItemSubstitution,
  Prescribed,
} from './dashboard-workouts-types';

/**
 * De onde veio o bloco de auditoria da geração.
 * - `generation_runs`: a tabela da migração 054 (fonte preferida);
 * - `plan_raw_payload`: o bloco ecoado na ficha — único caminho das fichas
 *   anteriores ao M16, e sem data de geração, job ou pedido;
 * - `unavailable`: não há auditoria (ficha criada à mão / salva sem payload).
 */
export type EngineAuditSource = 'generation_runs' | 'plan_raw_payload' | 'unavailable';

/**
 * De onde saiu a coluna PLANEJADO do item. `muscle_group_budget` é o modo
 * `percent_budget`: o Modelo pediu VOLUME para o grupo, não um exercício.
 */
export type EnginePlannedSource = 'template_item' | 'muscle_group_budget' | 'unavailable';

export type EngineReasonKind =
  | 'selection'
  | 'safety_clamp'
  | 'substitution'
  | 'fallback'
  | 'progression';

export type EngineReason = {
  kind: EngineReasonKind;
  /** Código ESTÁVEL do motor (`reason`, `decision`) — nunca traduzido no backend. */
  code: string | null;
  detail: string | null;
  data: Record<string, unknown>;
};

export type EngineProgression = {
  id: number;
  decision: string | null;
  kind: string | null;
  phase_before: string | null;
  phase_after: string | null;
  engine_version: string | null;
  created_at: string | null;
};

export type EnginePlanned = {
  source: EnginePlannedSource;
  item_kind: string | null;
  item_order: number | null;
  muscle_group_id: number | null;
  muscle_group_name: string | null;
  exercise_id: number | null;
  exercise_name: string | null;
  equipment_id: number | null;
  sets_count: number | null;
  reps_target: string | null;
  rpe_target: string | null;
  advanced_technique: string | null;
  prioritize: boolean | null;
  /** M101d: item de exercício livre do Modelo ("o motor escolhe o exercício"). */
  free_exercise?: boolean | null;
  budget_min_sets: number | null;
  budget_max_sets: number | null;
  budget_weekly_sets: number | null;
};

export type EngineItem = {
  plan_exercise_id: number | null;
  plan_sequence: number | null;
  status: PlanItemStatus;
  planned: EnginePlanned;
  generated_name: string | null;
  generated_exercise_id: number | null;
  generated: Prescribed;
  /** `null` = nada registrado para o item — quem diz o que houve é `status`. */
  executed: Performed | null;
  missing_sets: number | null;
  substitution: PlanItemSubstitution | null;
  reasons: EngineReason[];
  progression: EngineProgression | null;
  limitations: string[];
};

export type EngineGroupBudget = {
  muscle_group_id: number | null;
  muscle_group_name: string | null;
  target_weekly_sets: number | null;
  target_min_sets: number | null;
  target_max_sets: number | null;
  generated_sets: number | null;
  deviation_status: string | null;
  recovery_status: string | null;
};

export type EngineRejection = {
  muscle_group_id: number | null;
  exercise_id: number | null;
  exercise_name: string | null;
  rank: number | null;
  reason: string | null;
  data: Record<string, unknown>;
};

export type EngineRun = {
  run_id: number | null;
  source: EngineAuditSource;
  created_at: string | null;
  plan_date: string | null;
  /** `null` = o rascunho gerado NÃO virou ficha (a 054 registra isso também). */
  workout_plan_id: number | null;
  plan_name: string | null;
  template_id: number | null;
  template_slot_sequence: number | null;
  volume_mode: string | null;
  engine_version: string | null;
  llm_used: boolean | null;
  job_id: string | null;
  selection_count: number | null;
  safety_clamp_count: number | null;
  substitution_count: number | null;
  fallback_count: number | null;
  limitations: string[];
};

export type EngineRunsResponse = {
  window: DashboardWindow;
  items: EngineRun[];
  total: number;
  limit: number;
  runs_table_total: number;
  raw_payload_total: number;
  data_quality: DataQuality;
  limitations: string[];
  metric_versions: Record<string, string>;
};

export type EnginePlanResponse = {
  window: DashboardWindow;
  run: EngineRun;
  plan_id: number;
  plan_name: string | null;
  plan_date: string | null;
  template_id: number | null;
  template_name: string | null;
  template_slot_sequence: number | null;
  template_slot_name: string | null;
  // PR4: número da versão do Modelo de que o PLANEJADO saiu ("vN").
  template_version_number?: number | null;
  volume_mode: string | null;
  items: EngineItem[];
  item_status_counts: ItemStatusCounts;
  group_budgets: EngineGroupBudget[];
  rejections: EngineRejection[];
  plan_level_reasons: EngineReason[];
  sessions_count: number;
  data_quality: DataQuality;
  limitations: string[];
  metric_versions: Record<string, string>;
};

/**
 * Tipos de `GET /analytics/dashboard/context` (F2.1) e do CRUD
 * `/context-events` — espelho dos contratos Pydantic de
 * `api/app/intelligence/dashboard/contracts.py` (`ContextResponse`) e do router
 * `api/app/context_events.py`.
 *
 * As regras são as mesmas dos outros arquivos de tipo do hub: o tipo É o
 * contrato e nada aqui recalcula. Rótulo do tipo, origem (manual x derivado),
 * contagens e o intervalo desde a última sessão chegam prontos do
 * `response_model`.
 */

import type { DashboardWindow, DataNature, DataQuality } from './dashboard-v3-types';

/** Vocabulário de eventos (ENUM da migração 048). */
export const CONTEXT_EVENT_TYPES = [
  'alcohol',
  'travel',
  'illness',
  'atypical_sleep',
  'gym_change',
  'plan_change',
  'diet_phase',
  'supplement',
  'injury_pain',
  'other',
] as const;

export type ContextEventType = (typeof CONTEXT_EVENT_TYPES)[number];

/** `manual` = linha de `context_events`; `derived` = calculado na leitura. */
export type ContextEventSource = 'manual' | 'derived';

export type ContextEvent = {
  /** `null` em evento derivado: ele não é uma linha, é uma função do histórico. */
  id: number | null;
  event_type: string;
  event_type_label: string | null;
  local_date: string;
  /** Fim (inclusive) de evento com duração; `null` = evento de um dia só. */
  end_local_date: string | null;
  /** 1..5 ou `null` — nunca 0: "sem gradação" é ausência. */
  intensity: number | null;
  notes: string | null;
  source: ContextEventSource;
  derived: boolean;
  /** Derivado não é editável nem apagável (não é opinião, é registro). */
  editable: boolean;
};

export type ContextEventCount = {
  event_type: string;
  event_type_label: string;
  count: number;
};

export type ContextResponse = {
  window: DashboardWindow;
  metric_versions: Record<string, string>;
  data_quality: DataQuality;
  limitations: string[];
  items: ContextEvent[];
  counts: ContextEventCount[];
  manual_total: number;
  derived_total: number;
  /** `false` antes da migração 048: só os derivados aparecem. */
  table_available: boolean;
  nature: DataNature;
  last_session_local_date: string | null;
  /** Insumo do insight `TRAINING_PAUSE_DETECTED`; `null` sem sessão nenhuma. */
  days_since_last_session: number | null;
};

// ---------------------------------------------------------------------------
// CRUD `/context-events` (api-core)
// ---------------------------------------------------------------------------

export type ContextEventItem = {
  id: number;
  event_type: string;
  event_type_label: string;
  local_date: string;
  end_local_date: string | null;
  intensity: number | null;
  notes: string | null;
  source: ContextEventSource;
  derived: boolean;
  created_at: string | null;
  updated_at: string | null;
};

export type ContextEventInput = {
  event_type: ContextEventType;
  local_date: string;
  end_local_date?: string | null;
  intensity?: number | null;
  notes?: string | null;
};

export type ContextEventWriteResponse = {
  status: 'created';
  item: ContextEventItem;
};

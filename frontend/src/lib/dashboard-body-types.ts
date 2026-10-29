/**
 * Tipos de `GET /analytics/dashboard/body` (F1.4) e do CRUD
 * `/body-measurements` — espelho dos contratos Pydantic de
 * `api/app/intelligence/dashboard/contracts.py` (`BodyResponse`) e do router
 * `api/app/body_measurements.py`.
 *
 * Arquivo separado de `dashboard-v3-types.ts` só por tamanho; as regras são as
 * mesmas: o tipo É o contrato e nada aqui recalcula. Média móvel, variação de
 * cintura, tendência da bioimpedância e o sinal de recomposição chegam prontos
 * do `response_model` — a tela só apresenta.
 */

import type { DashboardWindow, DataNature, DataQuality, Metric } from './dashboard-v3-types';

/**
 * Locais medidos por fita (ENUM das migrações 047 + 075): lista e tipo vêm do
 * espelho gerado do dono (`body-measurement-sites.ts`), reexportados aqui para
 * os tipos do dashboard.
 */
import type { MeasurementSite } from './body-measurement-sites';

export { MEASUREMENT_SITES } from './body-measurement-sites';
export type { MeasurementSite };

export type WeightPoint = {
  local_date: string;
  weight_kg: number;
  /** Fonte vencedora do dia: `body_composition` | `daily_biometrics` | `intervals_wellness`. */
  source: string;
  ma7_kg: number | null;
  ma7_points: number;
};

export type BodyWeight = {
  answer: string;
  latest: Metric;
  ma7_latest: Metric;
  change: Metric;
  change_pct: Metric;
  weighings_count: number;
  weighings_per_week: Metric;
  points: WeightPoint[];
  data_quality: DataQuality;
  limitations: string[];
};

export type MeasurementPoint = {
  id: number | null;
  local_date: string;
  value_cm: number;
  protocol_ok: boolean;
  /** `LOW` quando fora do protocolo — entra na série, não no sinal. */
  data_quality: DataQuality;
  notes: string | null;
};

export type BodyWaist = {
  /** Há alguma medida de cintura no período (mesmo fora do protocolo). */
  available: boolean;
  /** `false` antes da migração 047 (janela de deploy). */
  table_available: boolean;
  latest: Metric;
  latest_local_date: string | null;
  change: Metric;
  change_span_days: number | null;
  points: MeasurementPoint[];
  protocol_points: number;
  out_of_protocol_points: number;
  data_quality: DataQuality;
  limitations: string[];
};

export type BodyMeasurementSeries = {
  site: string;
  label: string;
  latest: Metric;
  latest_local_date: string;
  change: Metric;
  points: MeasurementPoint[];
  data_quality: DataQuality;
};

export type BioimpedancePoint = {
  local_date: string;
  weight_kg: number | null;
  body_fat_percent: number | null;
  muscle_mass_kg: number | null;
  body_water_percent: number | null;
};

export type BodyBioimpedance = {
  readings_count: number;
  latest_local_date: string | null;
  body_fat_percent: Metric;
  muscle_mass_kg: Metric;
  body_water_percent: Metric;
  body_fat_change: Metric;
  muscle_mass_change: Metric;
  points: BioimpedancePoint[];
  data_quality: DataQuality;
  limitations: string[];
};

export type RecompositionStatus = 'compatible' | 'not_compatible' | 'insufficient_data';

export type RecompositionCheck = {
  code: string;
  label: string;
  /** `null` = não avaliável com os dados do período. */
  result: boolean | null;
  detail: string;
};

export type RecompositionEvidence = {
  metric: string;
  from_value: number | null;
  to_value: number | null;
  unit: string | null;
  nature: DataNature;
  observed_at: string | null;
};

export type RecompositionSignal = {
  status: RecompositionStatus;
  nature: DataNature;
  metric_version: string;
  /** Janela PRÓPRIA do sinal (28 dias), independente da janela da tela. */
  window: DashboardWindow;
  answer: string;
  checks: RecompositionCheck[];
  evidence: RecompositionEvidence[];
  limitations: string[];
  alternative_explanations: string[];
  data_quality: DataQuality;
  comparable_exercises: number;
  progressing_or_stable: number;
};

export type PerformancePoint = {
  local_date: string;
  e1rm_avg_kg: number;
  exercises: number;
};

export type BodyPerformance = {
  points: PerformancePoint[];
  exercises_total: number;
  comparable_exercises: number;
  progressing_or_stable: number;
  data_quality: DataQuality;
  limitations: string[];
};

export type BodyMilestone = {
  local_date: string;
  kind: string;
  label: string;
  detail: string | null;
};

export type BodyResponse = {
  window: DashboardWindow;
  timezone: string;
  metric_versions: Record<string, string>;
  data_quality: DataQuality;
  limitations: string[];
  weight: BodyWeight;
  waist: BodyWaist;
  measurements: BodyMeasurementSeries[];
  bioimpedance: BodyBioimpedance;
  recomposition: RecompositionSignal;
  performance: BodyPerformance;
  milestones: BodyMilestone[];
  milestones_limitations: string[];
};

// ---------------------------------------------------------------------------
// CRUD `/body-measurements` (api-core)
// ---------------------------------------------------------------------------

export type BodyMeasurementItem = {
  id: number;
  local_date: string;
  site: string;
  site_label: string;
  value_cm: number;
  protocol_ok: boolean;
  notes: string | null;
  source: string;
  created_at: string | null;
  updated_at: string | null;
};

export type BodyMeasurementsListResponse = {
  items: BodyMeasurementItem[];
  limit: number;
  offset: number;
  has_more: boolean;
  /** Preenchido antes da migração 047 (tabela ausente). */
  limitations: string[];
};

export type BodyMeasurementInput = {
  local_date: string;
  site: MeasurementSite;
  value_cm: number;
  protocol_ok: boolean;
  notes?: string | null;
};

export type BodyMeasurementWriteResponse = {
  status: 'created' | 'updated';
  item: BodyMeasurementItem;
};

// Tipos e helpers compartilhados pelas 4 seções de
// `src/app/(app)/indicadores-fisicos/page.tsx`. Contratos confirmados lendo
// o backend real em `C:\muscle_fit\api\app\`:
//
// - Bioimpedância: `body_composition.py` (GET/PUT/DELETE) + `models.py`
//   (`BodyCompositionIn`, usado pelo `POST /body-composition` legado
//   declarado em `main.py`, mantido para o MCP/OpenClaw).
// - Recuperação: `intervals_integration.py` (`GET /integrations/intervals/wellness`,
//   prefixo do router `/integrations/intervals`), só leitura.
// - Sono: `sleep_metrics.py` — `POST` é create-or-update por `local_date`
//   (UNIQUE composta `user_id, local_date`).
// - Esforço: `effort_metrics.py` (`GET /effort-metrics`), só leitura — EPOC/PAI
//   de 7 dias estimados pelo motor `domain/cardio_load.py` (M99).

import type { IntervalsWellnessSource } from './intervals-types';

export type ApiListResponse<T> = { items: T[] };

export function formatDateBR(value?: string | null): string {
  if (!value) return '-';
  const isoDate = value.slice(0, 10);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!match) return value;
  const [, yyyy, mm, dd] = match;
  return `${dd}/${mm}/${yyyy}`;
}

export function todayLocalDate(): string {
  return new Date().toISOString().slice(0, 10);
}

// COD-09: a definição mora em `lib/format.ts` (era idêntica a outras duas cópias).
export { formatNumberOrDash } from './format';

// ─── Conversores hh:mm <-> horas decimais (campos de duração de sono) ──────

export function decimalHoursToHHMM(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return '';
  const num = Number(value);
  if (!Number.isFinite(num)) return '';
  let totalMinutes = Math.round(num * 60);
  if (totalMinutes < 0) totalMinutes = 0;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
}

export function hhmmToDecimalHours(value: string): number | null {
  if (!value) return null;
  const trimmed = value.trim();
  const match = /^(\d{1,2}):(\d{2})$/.exec(trimmed);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (minutes >= 60 || hours < 0 || minutes < 0) return null;
  return Math.round((hours + minutes / 60) * 100) / 100;
}

// ─── 1. Bioimpedância — GET/POST /body-composition, GET/PUT/DELETE /body-composition/{id} ──

export type BodyCompositionEntry = {
  id: number;
  local_date: string;
  measured_at?: string | null;
  weight_kg?: number | string | null;
  bmi?: number | string | null;
  body_fat_percent?: number | string | null;
  fat_mass_kg?: number | string | null;
  fat_free_mass_kg?: number | string | null;
  muscle_mass_kg?: number | string | null;
  skeletal_muscle_percent?: number | string | null;
  body_water_percent?: number | string | null;
  visceral_fat_rating?: number | string | null;
  bone_mass_kg?: number | string | null;
  bmr_kcal?: number | string | null;
  subcutaneous_fat_percent?: number | string | null;
  protein_percent?: number | string | null;
  metabolic_age?: number | null;
  source?: string | null;
  notes?: string | null;
};

export type BodyCompositionFormState = Omit<BodyCompositionEntry, 'id' | 'source'>;

export function emptyBodyCompositionForm(): BodyCompositionFormState {
  return {
    local_date: todayLocalDate(),
    measured_at: null,
    weight_kg: null,
    bmi: null,
    body_fat_percent: null,
    fat_mass_kg: null,
    fat_free_mass_kg: null,
    muscle_mass_kg: null,
    skeletal_muscle_percent: null,
    body_water_percent: null,
    visceral_fat_rating: null,
    bone_mass_kg: null,
    bmr_kcal: null,
    subcutaneous_fat_percent: null,
    protein_percent: null,
    metabolic_age: null,
    notes: null,
  };
}

// ─── 2. Recuperação — GET /integrations/intervals/wellness (só leitura) ────

export type IntervalsWellnessEntry = {
  wellness_date: string;
  fitness_ctl: number | string | null;
  fatigue_atl: number | string | null;
  form_tsb: number | string | null;
  ramp_rate: number | string | null;
  weight_kg: number | string | null;
  resting_hr_bpm: number | string | null;
  /** Origem da FC de repouso: digitada em /intervals-icu ou do Intervals.icu. */
  resting_hr_source?: IntervalsWellnessSource;
  hrv_ms: number | string | null;
  /** Origem do HRV: digitado em /intervals-icu ou do Intervals.icu. */
  hrv_source?: IntervalsWellnessSource;
  manual_updated_at?: string | null;
  sleep_seconds: number | string | null;
  readiness_score: number | string | null;
  daily_effort_load: number | string | null;
  synced_at?: string | null;
};

export type IntervalsWellnessResponse = {
  days: number;
  limit: number;
  items: IntervalsWellnessEntry[];
};

// ─── 3. Sono — GET/POST /sleep-metrics, GET/PUT/DELETE /sleep-metrics/{id} ─

export type SleepMetricsEntry = {
  id: number;
  local_date: string;
  duration_hours?: number | string | null;
  regularity_percent?: number | string | null;
  deep_sleep_hours?: number | string | null;
  rem_sleep_hours?: number | string | null;
  awake_count?: number | null;
  awake_hours?: number | string | null;
  notes?: string | null;
};

export type SleepFormState = {
  local_date: string;
  duration_hours: string;
  regularity_percent: number | null;
  deep_sleep_hours: string;
  rem_sleep_hours: string;
  awake_count: number | null;
  awake_hours: string;
  notes: string | null;
};

export function emptySleepForm(): SleepFormState {
  return {
    local_date: todayLocalDate(),
    duration_hours: '',
    regularity_percent: null,
    deep_sleep_hours: '',
    rem_sleep_hours: '',
    awake_count: null,
    awake_hours: '',
    notes: null,
  };
}

export function sleepEntryToFormState(entry: SleepMetricsEntry): SleepFormState {
  return {
    local_date: entry.local_date,
    duration_hours: decimalHoursToHHMM(entry.duration_hours),
    regularity_percent: entry.regularity_percent != null ? Number(entry.regularity_percent) : null,
    deep_sleep_hours: decimalHoursToHHMM(entry.deep_sleep_hours),
    rem_sleep_hours: decimalHoursToHHMM(entry.rem_sleep_hours),
    awake_count: entry.awake_count ?? null,
    awake_hours: decimalHoursToHHMM(entry.awake_hours),
    notes: entry.notes ?? null,
  };
}

// Payload de envio: converte hh:mm de volta pra horas decimais (formato
// esperado pelo backend, DECIMAL(4,2)).
export function sleepFormToApiPayload(form: SleepFormState) {
  return {
    local_date: form.local_date,
    duration_hours: hhmmToDecimalHours(form.duration_hours),
    regularity_percent: form.regularity_percent,
    deep_sleep_hours: hhmmToDecimalHours(form.deep_sleep_hours),
    rem_sleep_hours: hhmmToDecimalHours(form.rem_sleep_hours),
    awake_count: form.awake_count,
    awake_hours: hhmmToDecimalHours(form.awake_hours),
    notes: form.notes,
  };
}

// ─── 4. Esforço estimado — GET /effort-metrics (M99, só leitura) ──
// EPOC e PAI ACUMULADOS DE 7 DIAS, ESTIMADOS pelo backend (motor
// `api/app/domain/cardio_load.py`) a partir do tempo em zona de FC das
// atividades do Intervals.icu. A tela só exibe: nenhum peso ou janela aqui.

export type EffortEstimateEntry = {
  local_date: string;
  epoc_7d_estimated: number | null;
  pai_7d_estimated: number | null;
  model_version: string;
};

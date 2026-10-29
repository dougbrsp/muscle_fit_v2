/**
 * Tipos de `GET /analytics/dashboard/cardio` (F2.3) — espelho do contrato
 * Pydantic `CardioResponse` em
 * `api/app/intelligence/dashboard/contracts.py`.
 *
 * Mesma regra dos demais arquivos de tipo do hub: o tipo É o contrato e nada
 * aqui recalcula. Totais, agregado por modalidade, cobertura de FC e o status
 * do casamento chegam prontos do `response_model`.
 *
 * É a ÚNICA leitura do Intervals para cardio (M98 C4): a tela antiga `/cardio`
 * e o endpoint AO VIVO `/integrations/intervals/cardio-dashboard` saíram. A
 * resposta lê o CACHE (`intervals_activities`) e traz também a FC por sessão
 * (`activity_intervals_link`) e o acompanhamento da C4 (semanas por zona,
 * planejado × realizado, tendência, PAI, próxima sessão e zonas em uso).
 */

import type { DashboardWindow, DataNature, DataQuality } from './dashboard-v3-types';
import type { AthleteHrZones } from './hr-zones';

/** Classificação da atividade (módulo puro `app/intervals_activity_kind.py`). */
export type IntervalsActivityKind = 'cardio' | 'strength' | 'other';

/**
 * Status do casamento treino ↔ atividade do Intervals, como
 * `activity_intervals_link.match_status` o gravou. `null` = sem link (a
 * sessão simplesmente não tem FC associada).
 */
export type HrMatchStatus = 'MATCHED' | 'MATCHED_AMBIGUOUS';

/**
 * De onde veio a FC daquela sessão. `activity` é a MEDIDA do próprio treino
 * (`activities.avg_hr_bpm`, do import do relógio); `intervals_link` é a
 * atividade WeightTraining casada por horário. A primeira é medida, a segunda
 * é associação por proximidade — e a tela diz qual é qual.
 */
export type HrSource = 'activity' | 'intervals_link';

export type CardioSession = {
  intervals_activity_id: number | null;
  external_id: string;
  activity_date: string | null;
  start_time: string | null;
  name: string | null;
  sport_type: string | null;
  kind: IntervalsActivityKind | string;
  minutes: number | null;
  /** `null` quando não houve GPS — nunca `0` km. */
  distance_km: number | null;
  calories_kcal: number | null;
  /** `null` quando o relógio não mediu FC: a atividade ainda conta nos minutos. */
  avg_hr_bpm: number | null;
  max_hr_bpm: number | null;
  training_load: number | null;
  /** M98 C4: segundos em Z1..Z5 do app; `null` sem tempo em zona do Intervals. */
  zone_seconds: number[] | null;
  /** `{"Z1": min, ..., "Z5": min}`, pronto do backend. */
  zone_minutes: Record<string, number> | null;
  /** Sessão intensa pela regra do envelope; `null` = sem tempo em zona (sem classe). */
  high_intensity: boolean | null;
};

/** Uma semana (segunda a domingo) de cardio — M98 C4. */
export type CardioWeek = {
  week_start: string;
  sessions: number;
  minutes: number;
  calories_kcal: number | null;
  sessions_with_zones: number;
  /** `null` quando nenhuma sessão da semana trouxe tempo em zona. */
  zone_minutes: Record<string, number> | null;
  continuous_sessions: number;
  continuous_minutes: number;
  high_intensity_sessions: number;
  high_intensity_minutes: number;
  unclassified_sessions: number;
  unclassified_minutes: number;
};

export type CardioAdherenceStatus = 'adherent' | 'partial' | 'low' | 'not_done';

export type CardioAdherenceSession = {
  /** null no dia de descanso com cardio (casado pelo dia local inteiro). */
  activity_id: number | null;
  workout_plan_id?: number | null;
  /** `strength_end` (sessão de força) ou `local_day` (ficha de descanso). */
  anchor?: string | null;
  local_date: string | null;
  mode: string | null;
  prescription_text: string | null;
  planned_minutes: number | null;
  realized_minutes: number | null;
  time_completion_pct: number | null;
  zone_completion_pct: number | null;
  has_zone_data: boolean;
  match_status: string | null;
  status: CardioAdherenceStatus | string;
  status_label: string;
};

export type CardioAdherenceWeek = {
  week_start: string;
  planned_sessions: number;
  done_sessions: number;
  planned_minutes: number | null;
  realized_minutes: number | null;
  time_completion_pct: number | null;
  by_status: Record<string, number>;
};

export type CardioAdherence = {
  anchor: string;
  sessions: CardioAdherenceSession[];
  weeks: CardioAdherenceWeek[];
  thresholds: Record<string, number>;
  status_labels: Record<string, string>;
};

export type CardioWellnessPoint = {
  date: string;
  ctl: number | null;
  atl: number | null;
  tsb: number | null;
  hrv_ms: number | null;
  resting_hr_bpm: number | null;
};

export type CardioWellness = {
  points: CardioWellnessPoint[];
  latest: {
    ctl: number | null;
    ctl_date: string | null;
    atl: number | null;
    atl_date: string | null;
    tsb: number | null;
    tsb_date: string | null;
    hrv_ms: number | null;
    hrv_ms_date: string | null;
    resting_hr_bpm: number | null;
    resting_hr_bpm_date: string | null;
  };
};

/** PAI de 7 dias ESTIMADO pelo backend (M99, motor `cardio_load`). */
export type CardioPai = {
  points: { date: string; pai: number }[];
  /** Meta do dono (`cardio_policy.PAI_TARGET_MIN`), também de 7 dias. */
  target_min: number;
  latest: number | null;
  latest_date: string | null;
  latest_below_target: boolean | null;
  estimated?: boolean;
  model_version?: string | null;
};

/** Próxima sessão da última recomendação `cardio-agent-v3` ainda válida. */
export type CardioNextSession = {
  recommendation_id: number | null;
  generated_at: string | null;
  prompt_version: string | null;
  date: string | null;
  date_label: string | null;
  prescription_text: string | null;
  prescription_short: string | null;
  total_minutes_label: string | null;
  rationale: string | null;
  signals: string[];
  adjustment_labels: string[];
  slot_name: string | null;
  plan_action: string | null;
};

export type CardioTotals = {
  sessions: number;
  sessions_with_hr: number;
  minutes: number;
  distance_km: number | null;
  calories_kcal: number | null;
  training_load: number | null;
  avg_hr_bpm: number | null;
  max_hr_bpm: number | null;
};

export type CardioSport = {
  sport_type: string;
  sessions: number;
  minutes: number;
  distance_km: number | null;
  calories_kcal: number | null;
  avg_hr_bpm: number | null;
  max_hr_bpm: number | null;
};

export type SessionHeartRate = {
  activity_id: number | null;
  local_date: string | null;
  name: string | null;
  started_at: string | null;
  minutes: number | null;
  avg_hr_bpm: number | null;
  max_hr_bpm: number | null;
  /** `null` quando a sessão não tem FC de origem nenhuma. */
  hr_source: HrSource | string | null;
  calories_kcal: number | null;
  training_load: number | null;
  match_status: HrMatchStatus | string | null;
  /** Diferença em segundos entre o início do treino e o da atividade casada. */
  match_delta_seconds: number | null;
  candidates_count: number | null;
  /** `avg_hr_bpm != null` — um link pode existir sem FC medida. */
  has_heart_rate: boolean;
};

export type StrengthHeartRate = {
  sessions_total: number;
  sessions_with_hr: number;
  /** `null` sem sessão nenhuma: sem denominador não há percentual. */
  coverage_pct: number | null;
  /** Casamentos ambíguos na janela. */
  ambiguous_total: number;
  /** Os que de fato são a fonte exibida (sessão com FC própria não depende deles). */
  ambiguous_in_use_total: number;
  from_activity_total: number;
  from_link_total: number;
  avg_hr_bpm: number | null;
  max_hr_bpm: number | null;
  items: SessionHeartRate[];
};

export type CardioResponse = {
  window: DashboardWindow;
  metric_versions: Record<string, string>;
  data_quality: DataQuality;
  limitations: string[];
  nature: DataNature;
  /** `false` = Intervals.icu não configurado no perfil (tudo vem vazio). */
  intervals_configured: boolean;
  /** Última sincronização do cache; `null` = nunca sincronizou. */
  last_synced_at: string | null;
  totals: CardioTotals;
  by_sport: CardioSport[];
  sessions: CardioSession[];
  /** Atividades de FORÇA sincronizadas: elas explicam a ausência de cardio. */
  strength_activities_total: number;
  /**
   * M37 — o LADO DO INTERVALS do casamento: atividades WeightTraining que não
   * viraram FC de sessão nenhuma (treino não registrado aqui, ou horário fora
   * da tolerância de 60 min). Subconjunto de `strength_activities_total`.
   */
  unmatched_strength_activities_total: number;
  /** Nem cardio nem força (yoga, alongamento): ficam FORA dos totais. */
  other_activities_total: number;
  strength_heart_rate: StrengthHeartRate;
  /** M98 C4: todas as semanas da janela (vazio sem Intervals configurado). */
  weeks: CardioWeek[];
  /** `null` sem Intervals configurado (sem realizado) ou bloco indisponível. */
  adherence: CardioAdherence | null;
  wellness: CardioWellness;
  pai: CardioPai;
  next_session: CardioNextSession | null;
  /** Zonas em uso e a origem; `null` = bloco indisponível. */
  zones: AthleteHrZones | null;
};

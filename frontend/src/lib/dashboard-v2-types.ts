// Tipos do "Dashboard v2" — as 6 sub-rotas novas sob `/dashboard/*`
// (treinos, progressao, recovery, engine, musculos, ciclo), Fase 4 / etapa 28.
//
// Arquivo separado de `analytics-types.ts` (que já concentra os tipos das
// telas analíticas clássicas) para não inflar mais o arquivo existente e para
// deixar explícito que estes tipos espelham 1:1 os endpoints
// `GET /analytics/dashboard-*` de `api/app/analytics.py`.
//
// A rota RAIZ `/dashboard` NÃO usa nada daqui — ela consome
// `GET /analytics/dashboard/overview` (ver `DashboardOverviewClient.tsx`).

import type { ComparabilityKey, FatigueContext } from './dashboard-v3-types';
import type { LoadConvention } from './equipment-exercises-types';

// ---------------------------------------------------------------------------
// Comuns
// ---------------------------------------------------------------------------

/** 5 estados de `classify_weekly_volume_status` (intelligence/volume_service.py). */
export type MuscleVolumeStatus =
  | 'BELOW_MIN'
  | 'BETWEEN_MIN_TARGET'
  | 'ON_TARGET'
  | 'BETWEEN_TARGET_MAX'
  | 'ABOVE_MAX';

export type MuscleTargetSource = 'training_type' | 'generic_fallback';

// ---------------------------------------------------------------------------
// GET /analytics/dashboard-progression{,-trend,-suggestion}  →  /dashboard/progressao
// ---------------------------------------------------------------------------

export type ProgressionSessionItem = {
  activity_id: number;
  local_date: string;
  activity_name: string | null;
  sets_count: number;
  best_load_kg: number | null;
  best_reps: number | null;
  avg_rpe: number | null;
  e1rm: number | null;
  // PR-14 (§5.7): status de intensidade da sessão, derivado do que ficou
  // gravado nas séries `working` (`rpe_target_effective`/`safety_event`).
  // `null` = sessão anterior ao PR-14 (sem alvo efetivo gravado) — exibida
  // como está, sem alvo retroativo inventado (§12).
  // Lote 1/P1 (2026-10-11): `ABOVE_SAFE_CEILING` só para o par que proíbe a
  // falha; RPE 10 com a falha negada só pela recuperação segue o status normal.
  intensity_status: 'REACHED' | 'BELOW_TARGET' | 'FAILURE_NOT_ALLOWED_REACHED_CEILING' | 'ABOVE_SAFE_CEILING' | null;
  rpe_target_effective: number | null;
  failure_allowed: boolean | null;
  /**
   * Seta "Evolução" PRONTA do backend: `compare_exposures` da sessão contra a
   * exposição imediatamente anterior, se for da mesma chave (exercício,
   * equipamento, convenção de carga) — a mesma regra da home e do drill-down.
   * `null` = sem exposição anterior lida na janela. Opcional só para tolerar
   * a API anterior durante o deploy.
   */
  evolution?: ProgressionSessionEvolution | null;
  /** Reps da série do melhor e1RM e se ele é confiável (≤ 12 reps). */
  e1rm_reps?: number | null;
  e1rm_reliable?: boolean;
  /** Carga de TOPO da exposição (a que a regra compara) e as reps nela. */
  top_load_kg?: number | null;
  top_set_reps?: number[] | null;
  /**
   * A sessão é da chave do veredito (mesmo equipamento/convenção do
   * "Comparado em")? `null` = não foi possível saber (sem exposição lida).
   */
  in_verdict_key?: boolean | null;
  /** Contexto de fadiga prévia da sessão (pronto do backend; opcional). */
  fatigue_context?: FatigueContext | null;
  /** M97 F4: objetivo da execução (`hypertrophy`/`strength`). */
  training_goal?: string | null;
  /**
   * M97 F4: sessão de deload (`period`/`manual`; `null` = normal). Vem com
   * `e1rm = null` (fora do e1RM) e sem seta; `deload_label` é o rótulo pronto.
   */
  deload_source?: string | null;
  deload_label?: string | null;
};

/** Status de UMA transição entre exposições consecutivas (`compare_exposures`). */
export type ProgressionSessionEvolution = {
  status: 'PROGRESSING' | 'REGRESSING' | 'STABLE' | 'NOT_COMPARABLE';
  /** Códigos `comparability.REASON_*` (texto em `lib/exercise-progress.ts`). */
  reasons: string[];
};

export type DashboardProgressionResponse = {
  exercise_name: string | null;
  mapping_id: number | null;
  days: number;
  /** Ecoados só na janela `custom` (M61); `null` nas pré-definidas. */
  date_from?: string | null;
  date_to?: string | null;
  items: ProgressionSessionItem[];
};

export type ProgressionTrendClassification =
  | 'PROGREDINDO'
  | 'ESTAVEL'
  | 'ESTAGNADO'
  | 'REGREDINDO'
  | 'DADOS_INSUFICIENTES';

/**
 * Veredito de `/dashboard/progressao` — desde 2026-10-06 é a MESMA regra da
 * home e do drill-down (`classify_exercise_progression`).
 */
export type DashboardProgressionTrendResponse = {
  exercise_name: string | null;
  mapping_id: number | null;
  days: number;
  /** Ecoados só na janela `custom` (M61); `null` nas pré-definidas. */
  date_from?: string | null;
  date_to?: string | null;
  /** Enum v3 (`ExerciseProgressionStatus`); visual no domínio `exerciseProgression`. */
  status: string;
  /** Códigos `comparability.REASON_*`. */
  status_reasons: string[];
  /** Exposições do run comparável final (mesma chave da última). */
  comparable_exposures: number;
  /** Contexto de fadiga em que o status vale (o da última exposição). */
  fatigue_context?: FatigueContext | null;
  exposures_in_window: number;
  /** Chave em que a comparação foi feita (a da última exposição). */
  comparability_key: ComparabilityKey | null;
  equipment_name: string | null;
  load_convention: LoadConvention | null;
  load_convention_source: 'equipment_exercises' | 'default_total' | null;
  last_local_date: string | null;
  /** @deprecated rótulo da regra antiga, mantido só para compatibilidade de deploy. Use `status`. */
  classification?: ProgressionTrendClassification;
  /** @deprecated idem. Use `status_reasons`. */
  reason?: string;
};

export type DashboardProgressionSuggestionResponse = {
  mapping_id: number;
  reps_target: string;
  load_target: string;
  rpe_target: string;
  progression_note: string;
  has_history: boolean;
  progression: 'increased' | 'decreased' | null;
  last_load_kg: number | null;
  last_reps: number | null;
  trend: 'up' | 'down' | 'same' | 'unknown';
  last_date: string | null;
  // Aditivos do M76 (Lote 1a): mesma resposta de `/exercises/suggested-progression`
  // (motor v2 por par). O card lê a carga pronta; nada é recalculado na tela.
  suggested_load_kg?: number | null;
  load_source?: 'engine' | 'last_execution' | null;
  load_increment_kg?: number | null;
  decision?: string | null;
  effective_rpe_ceiling?: number | null;
  failure_allowed?: boolean;
  /** M97 F4: eco do `?goal=` pedido (ausente sem `goal`). */
  training_goal?: string;
};

// GET /analytics/dashboard-progression-by-muscle-group (M7)
export type MuscleGroupProgressionClassification =
  | 'PROGREDINDO'
  | 'ESTAVEL'
  | 'REGREDINDO'
  | 'DADOS_INSUFICIENTES'
  | 'NOVO'
  | 'SEM_DADOS';

/** Status de VOLUME (tonelagem) — rótulo neutro: menos volume não é regressão. */
export type MuscleGroupVolumeStatus =
  | 'VOLUME_UP'
  | 'VOLUME_STABLE'
  | 'VOLUME_DOWN'
  | 'DADOS_INSUFICIENTES'
  | 'NOVO'
  | 'SEM_DADOS';

/** Status de DESEMPENHO: agregação ponderada dos status dos exercícios do grupo (mesma regra da home), na janela atual. */
export type MuscleGroupPerformanceStatus = 'PROGREDINDO' | 'ESTAVEL' | 'REGREDINDO' | 'DADOS_INSUFICIENTES';

export type MuscleGroupPerformanceExercise = {
  exercise_id: number;
  exercise_name: string | null;
  equipment_id: number | null;
  load_convention: string;
  previous_e1rm_kg: number;
  current_e1rm_kg: number;
  change_pct: number;
  stimulus_weight: number;
  /** Variação de e1RM acima de ±50%: listado, mas fora do cálculo do grupo ("conferir registro"). */
  outlier: boolean;
  excluded_reason: 'OUTLIER_CHANGE' | null;
};

/** Por que o grupo ficou sem leitura de desempenho (`null` quando classificado). */
export type MuscleGroupPerformanceInsufficientReason =
  | 'NO_COMPARABLE_EXERCISE'
  | 'LOW_ACTIVATION'
  | 'PROGRESSION_UNAVAILABLE';

export type MuscleGroupProgressionItem = {
  muscle_group_id: number;
  muscle_group_name: string | null;
  current_tonnage_kg: number;
  previous_tonnage_kg: number;
  current_sets: number;
  previous_sets: number;
  // M50: sessões em que o grupo recebeu tonelagem, e a média por sessão.
  current_sessions: number;
  previous_sessions: number;
  current_tonnage_per_session_kg: number | null;
  previous_tonnage_per_session_kg: number | null;
  /** @deprecated volume com rótulo de desempenho; use `volume_status`/`performance_status`. */
  classification: MuscleGroupProgressionClassification;
  volume_status: MuscleGroupVolumeStatus;
  performance_status: MuscleGroupPerformanceStatus;
  /** Média das variações de e1RM dos exercícios comparáveis, ponderada pela ativação. */
  performance_change_pct: number | null;
  /** Só os exercícios USADOS no cálculo (sem os atípicos). */
  performance_exercises_compared: number;
  /** Exercícios descartados por variação atípica (continuam em `performance_exercises`). */
  performance_exercises_excluded: number;
  /** Soma da ativação (`stimulus_weight`) dos exercícios usados; mínimo 0,5 para classificar. */
  performance_activation_sum: number;
  performance_insufficient_reason: MuscleGroupPerformanceInsufficientReason | null;
  performance_exercises: MuscleGroupPerformanceExercise[];
  /** Contagem de exercícios do grupo por status (mesmas chaves do resumo). Só exibir; ausente em respostas antigas. */
  progression_counts?: ExerciseProgressionCounts;
  /** Variação da tonelagem POR SESSÃO (M50) — a janela atual termina hoje. */
  change_pct: number | null;
  /** Variação da tonelagem total (contexto; a classificação exige que concorde). */
  total_change_pct: number | null;
  // Informação secundária (trilha de progressão); não define a classificação.
  phase: string | null;
  phase_status: string | null;
};

/** Contagem de EXERCÍCIOS comparáveis por status, vinda do motor (mesma da home). Só exibir. */
export type ExerciseProgressionCounts = {
  progressing: number;
  stable: number;
  plateau_suspected: number;
  regressing: number;
  comparable_total: number;
  not_comparable: number;
  insufficient_data: number;
};

export type ExerciseProgressionSummary = {
  window_days: number;
  period_start: string;
  period_end: string;
  counts: ExerciseProgressionCounts;
  /** Frase pronta do backend (a mesma da home). */
  answer: string;
  /** Leitura parcial: janela longa demais para cobrir todos os exercícios. */
  truncated?: boolean;
};

export type MuscleGroupProgressionResponse = {
  /** Ausente em respostas antigas: nesse caso nada é exibido. */
  exercise_progression?: ExerciseProgressionSummary | null;
  /** Base do `performance_status` por grupo (descrição vinda do backend). */
  performance_status_basis?: string;
  /** Classificação indisponível no momento: o resumo não é exibido. */
  degraded?: boolean;
  days: number;
  stable_band_pct: number;
  min_sessions_per_window: number;
  comparison_basis: 'tonnage_per_session';
  performance_basis: 'best_e1rm_epley_same_exercise';
  current_from: string;
  current_to: string;
  previous_from: string;
  previous_to: string;
  items: MuscleGroupProgressionItem[];
};

// ---------------------------------------------------------------------------
// GET /analytics/dashboard-recovery  →  /dashboard/recovery
// ---------------------------------------------------------------------------

export type RecoveryStatusColor = 'green' | 'yellow' | 'red';
export type RecoveryReadinessStatus = 'READY' | 'REDUCED_DOSE' | 'NO_DIRECT_WORK';
export type RecoveryGuidance = 'ideal' | 'recovering' | 'avoid';

// ---------------------------------------------------------------------------
// GET /analytics/dashboard-cycle  →  /dashboard/ciclo
// ---------------------------------------------------------------------------

export type CycleSlotStatus = 'done' | 'next' | 'pending';

/**
 * 100% genérico: `name`/`sequence`/`weekday` vêm do Modelo de Ficha cadastrado,
 * nunca de uma letra A/B/C hardcoded. `weekday`: 1 (segunda) a 6 (sábado), ou
 * `null` quando o slot não tem dia fixo.
 */
export type CycleSlot = {
  sequence: number;
  name: string;
  slot_type: string;
  weekday: number | null;
  status: CycleSlotStatus;
};

export type CycleNextSlot = {
  sequence: number;
  name: string;
  slot_type: string;
  weekday: number | null;
};

export type CycleTrainingTypeSource = 'template' | 'assigned_template' | 'unavailable';

export type CycleCoverageItem = {
  muscle_group_id: number;
  muscle_group: string;
  min_weekly_sets: number | null;
  target_weekly_sets: number | null;
  max_weekly_sets: number | null;
  /** Séries equivalentes nos últimos 7 dias (a semana do backend). */
  actual_weekly_sets: number;
  /** Planejado DA SEMANA do Modelo (M76 Lote 4a); mesmo valor de `planned_weekly_sets`. */
  projected_weekly_sets: number;
  planned_weekly_sets?: number;
  /** O que ainda falta no ciclo corrente (informativo, sem status). */
  pending_cycle_sets?: number;
  /** Status do planejado da semana contra a meta. */
  status: MuscleVolumeStatus | null;
  status_basis?: 'planned_weekly';
};

export type DashboardCycleResponse =
  | { has_template: false; message: string }
  | {
      has_template: true;
      template_id: number;
      template_name: string;
      cycle_length_days: number;
      slots: CycleSlot[];
      next_slot: CycleNextSlot | null;
      coverage: CycleCoverageItem[];
      training_type_source: CycleTrainingTypeSource;
      window_days?: number;
    };

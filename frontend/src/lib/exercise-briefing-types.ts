import type { BriefingDeload } from './deload';

// Contrato de `GET /workout-plans/{plan_id}/exercises/{wpe_id}/briefing`
// (M4), lido diretamente de `api/app/exercise_briefing.py`.
//
// UMA chamada monta a tela inteira (catálogo + equipamento + prescrição
// congelada + última execução + trilha de progressão) porque a tela abre na
// academia, no celular, entre a lista e a primeira série: cinco requisições
// seriam cinco invocações da Lambda `api-core` e cinco chances de spinner.
//
// Todo campo "faltando" é nulo, nunca ausente-e-quebrando:
// - `exercise.execution_description` null = descrição ainda não gerada
//   (migração 039 + backfill) -> a tela omite a seção;
// - `equipment` null, ou `equipment.photo_url` null = sem equipamento
//   resolvido / sem foto -> placeholder;
// - `last_execution` null = primeira vez neste exercício;
// - `track` null = ambiente sem o motor de progressão v2;
// - `exercise.exercise_card` null = exercício sem card ilustrado publicado
//   -> a tela não mostra a miniatura (nem espaço vazio).

import type { RecoverySignal } from './intensity-status';
import type { ExerciseCardSummary, StrengthLoadRange } from './workout-plans-types';
import type { SessionLoadPlan } from './workout-mode-load-suggestions';

export type BriefingTrendStatus = 'progressing' | 'maintaining' | 'regressing' | 'unknown';

/**
 * LEGADO — mantido para clientes em cache. Desde o alinhamento com o
 * dashboard ele é DERIVADO de `performance`; a tela usa `performance` +
 * `adjustment`.
 */
export type BriefingTrend = {
  status: BriefingTrendStatus;
  label: string;
  reason: string;
  source: 'comparability' | 'engine_decision' | 'legacy_indicator' | 'last_execution_comparison' | 'none';
};

/**
 * Status de DESEMPENHO do exercício pela regra única do dashboard
 * (`comparability`): última execução contra a anterior comparável (mesmo
 * exercício + equipamento + convenção de carga). Mesmos códigos de
 * `/dashboard/exercicios` (`ExerciseProgressionCode`).
 */
export type BriefingPerformance = {
  status: string;
  /** Códigos `REASON_*` de `comparability.py` (o front traduz). */
  reasons: string[];
  /** `false` = sem duas execuções comparáveis: a tela mostra "Sem comparação". */
  comparable: boolean;
  /** `equipment` = só o aparelho de hoje; `exercise` = histórico do exercício. */
  scope: 'equipment' | 'exercise' | null;
  comparable_exposures: number;
  equipment_name: string | null;
  load_convention: string | null;
  last_local_date: string | null;
};

/** AJUSTE DE HOJE decidido pelo motor (ação, nunca rótulo de desempenho). */
export type BriefingAdjustment = {
  decision: string;
  label: string;
  reason: string | null;
  source: 'engine_decision' | 'legacy_indicator';
  // RPE 10 Lote 6/P9 (ausentes em respostas antigas): sinais de recuperação
  // congelados na ficha e o teto do dia, para "Teto 9 hoje pela recuperação: …".
  recovery_signals?: RecoverySignal[] | null;
  effective_rpe_ceiling?: number | null;
};

export type BriefingMuscle = {
  muscle_group_id: number;
  name: string;
  role: 'PRIMARY' | 'SECONDARY' | 'STABILIZER' | string;
  stimulus_weight: number | null;
};

export type BriefingSet = {
  set_number: number;
  set_type: string;
  reps: number | null;
  weight_kg: number | null;
  rpe: number | null;
  is_working: boolean;
};

export type BriefingLastExecution = {
  activity_id: number | null;
  local_date: string | null;
  equipment_name: string | null;
  sets: BriefingSet[];
  working_sets: number;
  top_weight_kg: number | null;
  reps_min: number | null;
  reps_max: number | null;
  top_rpe: number | null;
};

// `SessionLoadPlan` (M76 Lote 1a/1b): cargas de aquecimento/ajuste/técnica
// já calculadas pelo backend (`domain/session_loads.py`) + passo do equipamento.
export type BriefingPrescription = SessionLoadPlan & {
  sets_count: number | null;
  reps_target: string | null;
  load_target: string | null;
  suggested_load_kg: number | null;
  // Fix M97: faixa de carga de força por %1RM (pronta do backend); null fora dela.
  strength_load?: StrengthLoadRange | null;
  rpe_target: string | number | null;
  effective_rpe_target: number | null;
  effective_rpe_ceiling: number | null;
  failure_allowed: boolean | null;
  advanced_technique: string | null;
  advanced_technique_applications: number | null;
  rest_seconds: number | null;
  status: string | null;
  warnings: string[];
};

export type ExerciseBriefingResponse = {
  plan_id: number;
  workout_plan_exercise_id: number;
  sequence: number | null;
  exercise: {
    id: number | null;
    name: string | null;
    catalog_name: string | null;
    description: string | null;
    execution_description: string | null;
    execution_description_reviewed: boolean;
    is_unilateral: boolean;
    // Card ilustrado publicado do exercício canônico (PR5 dos cards) — o
    // mesmo objeto de `WorkoutPlanExercise.exercise_card`.
    exercise_card?: ExerciseCardSummary | null;
  };
  equipment: {
    gym_equipment_id: number | null;
    equipment_id: number | null;
    name: string | null;
    photo_url: string | null;
  } | null;
  muscles: BriefingMuscle[];
  prescription: BriefingPrescription;
  last_execution: BriefingLastExecution | null;
  // Opcionais só para tolerar uma resposta de versão anterior do backend.
  performance?: BriefingPerformance | null;
  adjustment?: BriefingAdjustment | null;
  trend: BriefingTrend;
  track: {
    load_status: string | null;
    working_load_kg: number | null;
    sets_count: number | null;
    technique_code: string | null;
    phase: string | null;
    phase_status: string | null;
    reps_min: number | null;
    reps_max: number | null;
    last_executed_at: string | null;
  } | null;
  requires_user_choice: boolean;
  // M97 D/F3: deload da sessão (com `?session_id=`) ou o período ativo hoje; a
  // prescrição de deload do item vem pronta do dono `domain/deload.py`.
  deload?: BriefingDeload | null;
};

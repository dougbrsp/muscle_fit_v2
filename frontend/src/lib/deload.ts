// Deload (M97 F3): tipos e textos das telas do Modo Treino.
//
// A REGRA (fator de carga, RPE-alvo, reps, séries, duração do período, quando a
// sessão é de deload e se ainda pode mudar) mora no dono do backend
// `api/app/domain/deload.py` (mapa `docs/motores/FONTES_DA_VERDADE.md` §67). A
// tela só recebe os números prontos — `GET /deload-periods/current`
// (`constants`), o bloco `deload` do `start`/`GET /workout-sessions/{id}/deload`
// e o `deload.prescription` do briefing — e os interpola nos textos.
// Nenhum número de deload é escrito aqui.

import { formatLoadIn } from './exercise-briefing';
import type { WeightUnit } from './weight-units';

export type DeloadConstants = {
  load_factor: number;
  target_rpe: number;
  sets_reduction_fraction: number;
  min_sets: number;
  period_default_days: number;
  period_max_days: number;
};

export type DeloadPeriod = {
  id: number;
  starts_on: string;
  ends_on: string;
  created_at?: string | null;
  cancelled_at?: string | null;
  cancelled?: boolean;
};

/** Bloco `deload` do `start`, do PATCH meta e de `GET /workout-sessions/{id}/deload`. */
export type SessionDeloadStatus = {
  active: boolean;
  source: string | null;
  period: DeloadPeriod | null;
  /** Só no PATCH/GET da sessão: ainda sem série gravada (dono `can_toggle_deload`). */
  can_toggle?: boolean;
};

export type DeloadCurrentResponse = SessionDeloadStatus & {
  today: string;
  constants: DeloadConstants;
};

/** `deload.prescription` do briefing (dono `DeloadPrescription.to_dict`). */
export type DeloadPrescription = {
  training_goal: string;
  working_load_kg: number | null;
  load_kg: number | null;
  load_factor: number;
  target_rpe: number;
  reps_target: number | null;
  sets_count: number | null;
};

export type BriefingDeload = SessionDeloadStatus & { prescription?: DeloadPrescription | null };

export const DELOAD_CURRENT_QUERY_KEY = ['deload-periods', 'current'] as const;

export function sessionDeloadQueryKey(sessionId: number) {
  return ['workout-session-deload', sessionId] as const;
}

// Ficha do Modo Treino COMO A SESSÃO A EXECUTA (fix M97, 2026-10-26): com
// `session_id`, numa sessão de deload o servidor já devolve cada item com a
// prescrição de deload desde a 1ª série (carga, reps, séries, RPE-alvo) e sem
// técnica. Ligar/desligar o deload invalida esta consulta.
export const WORKOUT_MODE_PLAN_QUERY_ROOT = 'workout-plan-detail-workout-mode';

export function workoutModePlanQueryKey(planId: number | null | undefined, sessionId: number) {
  return [WORKOUT_MODE_PLAN_QUERY_ROOT, planId ?? null, sessionId] as const;
}

export function workoutModePlanPath(planId: number, sessionId: number | null | undefined): string {
  return sessionId != null ? `/workout-plans/${planId}?session_id=${sessionId}` : `/workout-plans/${planId}`;
}

// Sugestão do PAR TROCADO no Modo Treino (M97-D2, 2026-10-26): com a sessão e
// o item trocado, o servidor devolve a sugestão do par novo no objetivo do item
// (resolvido do item da ficha da sessão; `training_goal` é só o valor do item,
// repassado) e, numa sessão de deload, já com a prescrição de deload desde a 1ª
// série (carga, reps no piso, RPE-alvo). Ligar/desligar o deload invalida esta
// consulta.
export const PAIR_SUGGESTION_QUERY_ROOT = 'suggested-progression';

export function pairSuggestionPath(params: {
  mappingId: number;
  equipmentLinkId?: number | null;
  sessionId?: number | null;
  workoutPlanExerciseId?: number | null;
  trainingGoal?: string | null;
}): string {
  const query = new URLSearchParams({ mapping_id: String(params.mappingId) });
  if (params.equipmentLinkId != null) query.set('equipment_id', String(params.equipmentLinkId));
  if (params.sessionId != null) query.set('session_id', String(params.sessionId));
  if (params.workoutPlanExerciseId != null) query.set('workout_plan_exercise_id', String(params.workoutPlanExerciseId));
  if (params.trainingGoal) query.set('training_goal', params.trainingGoal);
  return `/exercises/suggested-progression?${query.toString()}`;
}

function percent(fraction: number | null | undefined): number | null {
  return typeof fraction === 'number' && Number.isFinite(fraction) ? Math.round(fraction * 100) : null;
}

/** "25/10" a partir de "2026-10-25" (sem fuso: a data já é a do usuário). */
export function shortDate(isoDate: string | null | undefined): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate ?? '');
  return match ? `${match[3]}/${match[2]}` : (isoDate ?? '');
}

/** "20/10 a 26/10". */
export function periodRangeLabel(period: Pick<DeloadPeriod, 'starts_on' | 'ends_on'> | null | undefined): string {
  if (!period) return '';
  return `${shortDate(period.starts_on)} a ${shortDate(period.ends_on)}`;
}

/** Explicação do deload com os números do dono. */
export function deloadRuleText(constants: DeloadConstants | null | undefined): string {
  if (!constants) return 'Carga, RPE, repetições e séries reduzidos pelo motor; a progressão não conta esta sessão.';
  const load = percent(constants.load_factor);
  const fewerSets = percent(constants.sets_reduction_fraction);
  return (
    `Carga em ${load}% da carga de trabalho, RPE-alvo ${constants.target_rpe}, repetições no piso da faixa ` +
    `e cerca de ${fewerSets}% menos séries. A progressão não conta como regressão.`
  );
}

/** Texto do botão de criar período, com a duração default do dono. */
export function startPeriodLabel(constants: DeloadConstants | null | undefined): string {
  return constants ? `Iniciar semana de deload (${constants.period_default_days} dias)` : 'Iniciar semana de deload';
}

/** Título do banner da sessão. */
export function deloadBannerTitle(status: SessionDeloadStatus | null | undefined): string | null {
  if (!status?.active) return null;
  return status.period ? `Semana de deload (${periodRangeLabel(status.period)})` : 'Sessão de deload';
}

/** "2 séries × 8 reps · 48 kg · RPE 6" (campos ausentes ficam de fora). */
export function deloadPrescriptionText(
  prescription: DeloadPrescription | null | undefined,
  unit: WeightUnit = 'kg',
): string | null {
  if (!prescription) return null;
  const parts: string[] = [];
  if (prescription.sets_count != null && prescription.reps_target != null) {
    parts.push(`${prescription.sets_count} ${prescription.sets_count === 1 ? 'série' : 'séries'} × ${prescription.reps_target} reps`);
  } else if (prescription.sets_count != null) {
    parts.push(`${prescription.sets_count} ${prescription.sets_count === 1 ? 'série' : 'séries'}`);
  } else if (prescription.reps_target != null) {
    parts.push(`${prescription.reps_target} reps`);
  }
  if (prescription.load_kg != null) parts.push(formatLoadIn(prescription.load_kg, unit));
  parts.push(`RPE ${prescription.target_rpe}`);
  return parts.join(' · ');
}

/** Detalhe da carga: "60% de 80 kg (carga de trabalho)". */
export function deloadLoadOrigin(
  prescription: DeloadPrescription | null | undefined,
  unit: WeightUnit = 'kg',
): string | null {
  if (!prescription || prescription.working_load_kg == null) return null;
  return `${percent(prescription.load_factor)}% de ${formatLoadIn(prescription.working_load_kg, unit)} (carga de trabalho)`;
}

/** Mensagem do 409 do PATCH meta quando já há série gravada. */
export const DELOAD_LOCKED_MESSAGE =
  'Esta sessão já tem série gravada: o deload só pode ser ligado ou desligado antes da primeira série.';

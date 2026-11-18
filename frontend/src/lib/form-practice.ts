/**
 * Aprimoramento de técnica (issue #196, ADR 0017). PURO: o frontend só guarda a
 * marcação pendente (antes da 1ª série) e lê o que a API devolve. Toda regra de
 * progressão mora no backend (`api/app/domain/form_practice.py`).
 */

import type { MidExerciseExtra, StoredWorkoutModeSession } from './workout-mode-types';

export const FORM_PRACTICE_LABEL = 'Aprimoramento de técnica';

export const FORM_PRACTICE_HELP = 'A execução fica no histórico e não conta para a progressão.';

/** Ajuda do campo no Registro de exercícios (treino já encerrado). */
export const FORM_PRACTICE_REGISTRO_HELP =
  'Fica no histórico e não conta para a progressão. Ao mudar, a progressão é reavaliada.';

type PendingSession = Pick<StoredWorkoutModeSession, 'form_practice_plan_exercise_ids'> | null | undefined;

/** Id do item da ficha: continuação (id sintético) resolve para o id ORIGINAL. */
export function resolveFormPracticePlanExerciseId(
  planExerciseId: number,
  midExerciseExtras: readonly MidExerciseExtra[] | null | undefined,
): number | null {
  const extra = midExerciseExtras?.find((item) => item.localId === planExerciseId);
  return extra ? extra.originalWorkoutPlanExerciseId : planExerciseId;
}

export function isFormPracticePending(session: PendingSession, planExerciseId: number | null | undefined): boolean {
  return planExerciseId != null && (session?.form_practice_plan_exercise_ids ?? []).includes(planExerciseId);
}

/** Devolve a sessão nova (imutável) com a marcação alternada. */
export function toggleFormPracticePending<T extends NonNullable<PendingSession>>(session: T, planExerciseId: number): T {
  const current = session.form_practice_plan_exercise_ids ?? [];
  const next = current.includes(planExerciseId)
    ? current.filter((id) => id !== planExerciseId)
    : [...current, planExerciseId];
  return { ...session, form_practice_plan_exercise_ids: next };
}

/** Linha da API (atividade/exercício): ausente ou null = false. */
export function isFormPracticeRow(row: { form_practice?: boolean | null } | null | undefined): boolean {
  return row?.form_practice === true;
}

// Séries com repetições PENDENTES na sessão (Modo Intensivo, PR 3).
//
// Módulo puro: lê o detalhe da sessão (`GET /activities/{id}`, que devolve as
// pendentes com `reps_pending`) e monta a lista compacta mostrada ao tocar em
// "Finalizar treino". Depois de finalizada a sessão, o PATCH da série devolve
// 409 e não há tela para corrigir: a confirmação acontece ANTES de finalizar.
// Nenhuma regra numérica aqui (a pendência é a marca gravada pelo servidor).

import { getAdvancedTechniqueApplicationNoun } from './advanced-techniques';
import { isTechniqueApplicationSetType, isWorkingSetType, normalizeSetType } from './set-types';
import type { ExerciseLogRow, ExerciseSetRow } from './workout-log-types';

export type PendingRepsSet = {
  /** `exercises.id` da sessão (rota do PATCH). */
  exerciseId: number;
  exerciseName: string;
  /** `set_number` FÍSICO gravado (rota do PATCH). */
  setNumber: number;
  setType: string | null;
  weightKg: number | null;
  /** Rótulo da série pela âncora e etapa ("série 2 · mini-bloco 2"), não pelo `set_number` físico. */
  label: string;
};

// `set_type` da aplicação -> código da técnica, só para achar o nome da etapa
// quando a linha não traz `technique_code` (gravação antiga).
const TECHNIQUE_BY_SET_TYPE: Record<string, string> = {
  rest_pause: 'rest_pause',
  drop_set: 'drop_set',
  forced_rep: 'forced_reps',
};

type LabelSet = Pick<ExerciseSetRow, 'set_number' | 'set_type'> &
  Partial<Pick<ExerciseSetRow, 'anchor_set_number' | 'technique_code'>>;

/**
 * Rótulos das séries do exercício: série de trabalho = "série N" (N = ordem
 * entre as de trabalho, sem aquecimento/ajuste); aplicação de técnica =
 * "série N · <etapa> K" (N = a da âncora, K = posição entre as aplicações
 * daquela âncora). Sem âncora gravada, a última série de trabalho anterior.
 */
export function pendingSetLabels(sets: ReadonlyArray<LabelSet>): Map<number, string> {
  const sorted = [...sets].sort((a, b) => a.set_number - b.set_number);
  const ordinal = new Map<number, number>();
  const stageCount = new Map<string, number>();
  const labels = new Map<number, string>();
  let lastWorking: number | null = null;
  for (const set of sorted) {
    if (isWorkingSetType(set.set_type)) {
      ordinal.set(set.set_number, ordinal.size + 1);
      lastWorking = set.set_number;
      labels.set(set.set_number, `série ${ordinal.get(set.set_number)}`);
      continue;
    }
    if (!isTechniqueApplicationSetType(set.set_type)) {
      labels.set(set.set_number, `série ${set.set_number}`);
      continue;
    }
    const anchor = set.anchor_set_number ?? lastWorking;
    const anchorOrdinal = anchor != null ? ordinal.get(anchor) : undefined;
    const technique = set.technique_code || TECHNIQUE_BY_SET_TYPE[normalizeSetType(set.set_type)] || '';
    const key = `${anchor ?? ''}#${technique}`;
    const stage = (stageCount.get(key) ?? 0) + 1;
    stageCount.set(key, stage);
    const noun = getAdvancedTechniqueApplicationNoun(technique).toLowerCase();
    labels.set(
      set.set_number,
      anchorOrdinal != null ? `série ${anchorOrdinal} · ${noun} ${stage}` : `${noun} ${stage}`,
    );
  }
  return labels;
}

type PendingSource = Pick<ExerciseLogRow, 'id' | 'sets'> &
  Partial<Pick<ExerciseLogRow, 'exercise_name' | 'planned_exercise_name' | 'mapped_name' | 'original_name'>>;

function isPending(set: Pick<ExerciseSetRow, 'reps_pending'>): boolean {
  return Boolean(set.reps_pending);
}

function weightOf(value: unknown): number | null {
  if (value == null || value === '') return null;
  const n = typeof value === 'string' ? Number(value) : value;
  return typeof n === 'number' && Number.isFinite(n) ? n : null;
}

/** Pendentes da sessão, na ordem dos exercícios e das séries. */
export function collectPendingRepsSets(exercises: ReadonlyArray<PendingSource> | null | undefined): PendingRepsSet[] {
  const result: PendingRepsSet[] = [];
  for (const exercise of exercises ?? []) {
    const name =
      exercise.exercise_name || exercise.planned_exercise_name || exercise.mapped_name || exercise.original_name || 'Exercício';
    const sets = [...(exercise.sets ?? [])].sort((a, b) => a.set_number - b.set_number);
    const labels = pendingSetLabels(sets);
    for (const set of sets) {
      if (!isPending(set)) continue;
      result.push({
        exerciseId: exercise.id,
        exerciseName: name,
        setNumber: set.set_number,
        setType: set.set_type ?? null,
        weightKg: weightOf(set.weight_kg),
        label: labels.get(set.set_number) ?? `série ${set.set_number}`,
      });
    }
  }
  return result;
}

export function pendingRepsKey(item: Pick<PendingRepsSet, 'exerciseId' | 'setNumber'>): string {
  return `${item.exerciseId}#${item.setNumber}`;
}

/** "Finalizar treino": com pendentes, abre a confirmação; sem elas, finaliza direto. */
export function finishNeedsPendingConfirmation(pending: ReadonlyArray<PendingRepsSet>): boolean {
  return pending.length > 0;
}

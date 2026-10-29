// Aquecimento GENÉRICO do Modo Treino: 2 séries opcionais antes do 1º
// exercício de um grupo muscular ainda não ativado nesta sessão (modo normal;
// no "Fazer treino avançado" quem manda é o aquecimento configurado na ficha).
//
// Antes esta regra morava dentro de `modo-treino/PlanExecutionScreen.tsx`
// (com `GENERIC_WARMUP_REPS = 15` escrito à mão). Saiu do componente no PR B
// da Evolução ZEPP para o relógio ter o MESMO comportamento: o núcleo
// `workout-mode-core/` reexporta daqui. Sem mudança de comportamento no
// celular.
//
// Números: nenhum escrito aqui. As reps vêm do dono
// `api/app/domain/session_loads.py` (`GENERIC_WARMUP_REPS`) e as cargas
// (20%/40% da referência de trabalho) de `warmupLoads`
// (`workout-mode-load-suggestions.ts`, espelho C7), ambos via
// `motor-constants.generated.json`.
//
// Contrato JSON-friendly (o golden do núcleo roda no relógio): entradas e
// saídas são objetos/arrays simples, nada de `Set`/`Map`.

import motorConstants from './motor-constants.generated.json';
import { isRepBased } from './set-measures';
import { countSetsOfType, reconcileWarmupState, type WarmupState } from './workout-mode-types';
import { warmupLoads, type SessionLoadContext } from './workout-mode-load-suggestions';

/** Repetições de cada série do aquecimento genérico (dono: `session_loads.GENERIC_WARMUP_REPS`). */
export const GENERIC_WARMUP_REPS: number = motorConstants.session_loads.generic_warmup_reps;

export type GenericWarmupSlot = 'first' | 'second';

type MuscleGroupFields = { muscle_primary?: string | null; muscle_secondary?: string | null };

function normalizeMuscleGroup(value: string | null | undefined): string {
  return (value || '').trim().toLowerCase();
}

/**
 * Grupos musculares (normalizados) já ativados na sessão: primário e
 * secundário de todo item da fila já CONCLUÍDO. Ordem de primeira aparição,
 * sem repetição.
 */
export function collectActivatedMuscleGroups(
  items: ReadonlyArray<{ done: boolean; planExercise: MuscleGroupFields }>,
): string[] {
  const activated: string[] = [];
  for (const item of items) {
    if (!item.done) continue;
    for (const group of [item.planExercise.muscle_primary, item.planExercise.muscle_secondary]) {
      const normalized = normalizeMuscleGroup(group);
      if (normalized && !activated.includes(normalized)) activated.push(normalized);
    }
  }
  return activated;
}

/** O grupo PRIMÁRIO do exercício ainda não foi ativado nesta sessão. */
export function needsGenericWarmup(
  musclePrimary: string | null | undefined,
  activatedGroups: ReadonlyArray<string>,
): boolean {
  const group = normalizeMuscleGroup(musclePrimary);
  return Boolean(group) && !activatedGroups.includes(group);
}

/**
 * Estado do aquecimento do item atual, reconciliado com os aquecimentos já
 * gravados no backend (`set_type: 'warmup'`): ao retomar a sessão sem o
 * estado local (ou se o app caiu entre o POST e o save local), a série já
 * gravada não é oferecida de novo. Sem item da ficha: tudo resolvido.
 */
export function resolveGenericWarmupState(params: {
  workoutPlanExerciseId: number | null;
  local: WarmupState | undefined;
  loggedSets: ReadonlyArray<{ set_type?: string | null }>;
}): WarmupState {
  if (params.workoutPlanExerciseId == null) return { first: 'skipped', second: 'skipped' };
  return reconcileWarmupState(params.local, countSetsOfType(params.loggedSets, 'warmup'));
}

/**
 * Mostrar o card do aquecimento genérico agora? Só no modo normal (no
 * avançado ele é substituído pelo aquecimento REAL da ficha, nunca os dois),
 * antes da 1ª série do exercício, com item da ficha, grupo ainda não ativado
 * e alguma das 2 séries ainda pendente. Medidas de série (Lote T5): só em
 * item de repetições (`isRepBased`; `measure` ausente = reps) — prancha ou
 * máximo de reps não têm carga de trabalho de onde tirar o aquecimento.
 */
export function shouldShowGenericWarmup(params: {
  advancedMode: boolean;
  measure?: string | null;
  hasCurrent: boolean;
  loggedCount: number;
  workoutPlanExerciseId: number | null;
  needsWarmup: boolean;
  warmupState: WarmupState;
}): boolean {
  const resolved = params.warmupState.first !== 'pending' && params.warmupState.second !== 'pending';
  return (
    !params.advancedMode &&
    isRepBased(params.measure) &&
    params.hasCurrent &&
    params.loggedCount === 0 &&
    params.workoutPlanExerciseId != null &&
    params.needsWarmup &&
    !resolved
  );
}

/** Carga de cada série do aquecimento genérico (mesma ordem de `GenericWarmupSlot`). */
export function genericWarmupLoads(referenceWorkingLoadKg: number, context?: SessionLoadContext): number[] {
  return warmupLoads(referenceWorkingLoadKg, context);
}

/** Série de aquecimento genérico a gravar ao tocar "Concluí". */
export function genericWarmupSet(
  which: GenericWarmupSlot,
  referenceWorkingLoadKg: number,
  context?: SessionLoadContext,
): { set_type: 'warmup'; reps: number; weight_kg: number } {
  return {
    set_type: 'warmup',
    reps: GENERIC_WARMUP_REPS,
    weight_kg: genericWarmupLoads(referenceWorkingLoadKg, context)[which === 'first' ? 0 : 1],
  };
}

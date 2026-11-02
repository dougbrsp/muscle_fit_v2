// Sobreposição das operações ainda não confirmadas pelo servidor sobre o detalhe
// da sessão (`GET /activities/{id}`), para a tela contar e numerar o que o
// usuário acabou de registrar mesmo sem rede: exercícios criados offline
// (linha sintética com id LOCAL negativo) e séries pendentes. É só uma cópia do
// que a tela já enviou (corpo da op); nada é calculado, derivado ou arredondado.

import type { ActivityDetail, ExerciseLogRow, ExerciseSetRow } from '../workout-log-types';
import type { OfflineOp } from './op-types';

export type OverlaidSetRow = ExerciseSetRow & {
  /** Série ainda local (não confirmada pelo servidor). */
  local_sync_status?: OfflineOp['status'];
};

/** Linha de série a partir do corpo da op (campos copiados como estão). */
function rowFromOp(op: OfflineOp): OverlaidSetRow {
  return { ...(op.payload as ExerciseSetRow), client_key: op.clientKey, local_sync_status: op.status };
}

function serverIdOf(op: OfflineOp): number | null {
  const id = (op.serverResponse as { exercise_id?: unknown } | undefined)?.exercise_id;
  return op.status === 'synced' && typeof id === 'number' ? id : null;
}

/**
 * Anexa as séries das ops `addSet` que o servidor ainda não devolveu e cria a
 * linha sintética dos exercícios criados offline. Uma op sincronizada continua
 * aparecendo até o refetch trazê-la (casada por `client_key`, `set_number` ou
 * id do exercício), sem sumir nem duplicar. Sem ops aplicáveis devolve o MESMO
 * objeto (identidade estável).
 */
export function overlayQueuedSets(activity: ActivityDetail | undefined, ops: readonly OfflineOp[]): ActivityDetail | undefined {
  if (!activity) return activity;
  const sessionOps = ops.filter((op) => op.sessionId === activity.id);
  const exerciseOps = sessionOps.filter((op) => op.kind === 'addExercise' && typeof op.localId === 'number');
  const setOps = sessionOps.filter((op) => op.kind === 'addSet');
  if (exerciseOps.length === 0 && setOps.length === 0) return activity;

  const realIds = new Set(activity.exercises.map((exercise) => exercise.id));

  // Exercício da série → id da linha que a hospeda (real quando já existe, senão o local).
  const hostIdOf = (op: OfflineOp): number | null => {
    if (typeof op.exerciseId === 'number' && op.exerciseId > 0) return op.exerciseId;
    const parent = exerciseOps.find((candidate) =>
      typeof op.exerciseId === 'number' ? candidate.localId === op.exerciseId : candidate.clientKey === op.exerciseId,
    );
    if (!parent) return null;
    const serverId = serverIdOf(parent);
    return serverId != null && realIds.has(serverId) ? serverId : (parent.localId as number);
  };

  let changed = false;
  let exercises: ExerciseLogRow[] = activity.exercises;

  // Linhas sintéticas: só enquanto o servidor ainda não devolveu o exercício.
  const synthetic: ExerciseLogRow[] = [];
  let nextSequence = exercises.reduce((max, exercise) => Math.max(max, exercise.sequence), 0);
  for (const op of exerciseOps) {
    const serverId = serverIdOf(op);
    if (serverId != null && realIds.has(serverId)) continue;
    nextSequence += 1;
    synthetic.push({
      id: op.localId as number,
      activity_id: activity.id,
      sequence: typeof op.payload.sequence === 'number' ? (op.payload.sequence as number) : nextSequence,
      original_name: String(op.payload.original_name ?? ''),
      mapping_id: (op.payload.mapping_id as number | null | undefined) ?? null,
      equipment_id: (op.payload.equipment_id as number | null | undefined) ?? null,
      workout_plan_exercise_id: (op.payload.workout_plan_exercise_id as number | null | undefined) ?? null,
      sets: [],
    });
  }
  if (synthetic.length > 0) {
    exercises = [...exercises, ...synthetic];
    changed = true;
  }

  exercises = exercises.map((exercise) => {
    const additions: OverlaidSetRow[] = [];
    for (const op of setOps) {
      if (hostIdOf(op) !== exercise.id) continue;
      const row = rowFromOp(op);
      const onServer = exercise.sets.some(
        (set) => (set.client_key != null && set.client_key === op.clientKey) || set.set_number === row.set_number,
      );
      if (!onServer) additions.push(row);
    }
    if (additions.length === 0) return exercise;
    changed = true;
    additions.sort((a, b) => a.set_number - b.set_number);
    return { ...exercise, sets: [...exercise.sets, ...additions] };
  });

  return changed ? { ...activity, exercises } : activity;
}

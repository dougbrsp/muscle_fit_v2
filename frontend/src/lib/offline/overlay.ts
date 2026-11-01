// Sobreposição das séries ainda não confirmadas pelo servidor sobre o detalhe
// da sessão (`GET /activities/{id}`), para a tela contar e numerar a série que
// o usuário acabou de registrar mesmo sem rede. É só uma cópia do que a tela
// já enviou (corpo da op); nada é calculado, derivado ou arredondado aqui.

import type { ActivityDetail, ExerciseSetRow } from '../workout-log-types';
import type { OfflineOp } from './op-types';

export type OverlaidSetRow = ExerciseSetRow & {
  /** Série ainda local (não confirmada pelo servidor). */
  local_sync_status?: OfflineOp['status'];
};

/** Linha de série a partir do corpo da op (campos copiados como estão). */
function rowFromOp(op: OfflineOp): OverlaidSetRow {
  return { ...(op.payload as ExerciseSetRow), client_key: op.clientKey, local_sync_status: op.status };
}

/**
 * Anexa ao exercício correspondente as séries das ops `addSet` da sessão que o
 * servidor ainda não devolveu. Uma série sincronizada continua aparecendo até
 * o refetch trazê-la (casada por `client_key` ou `set_number`), sem sumir nem
 * duplicar. Sem ops aplicáveis devolve o MESMO objeto (identidade estável).
 */
export function overlayQueuedSets(activity: ActivityDetail | undefined, ops: readonly OfflineOp[]): ActivityDetail | undefined {
  if (!activity) return activity;
  const relevant = ops.filter(
    (op) => op.kind === 'addSet' && op.sessionId === activity.id && typeof op.exerciseId === 'number',
  );
  if (relevant.length === 0) return activity;

  let changed = false;
  const exercises = activity.exercises.map((exercise) => {
    const additions: OverlaidSetRow[] = [];
    for (const op of relevant) {
      if (op.exerciseId !== exercise.id) continue;
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

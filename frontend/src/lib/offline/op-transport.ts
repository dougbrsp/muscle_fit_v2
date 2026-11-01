// Transporte HTTP das ops offline: reenvia EXATAMENTE o corpo que a tela
// montou, com a mesma `client_key` (o servidor deduplica e responde
// `duplicate: true` no replay). Nenhum cálculo ou default aqui.

import { apiFetch } from '../api';
import type { OpTransport } from './op-queue';

export const apiOpTransport: OpTransport = async (op, resolvedExerciseId) => {
  const body = JSON.stringify({ ...op.payload, client_key: op.clientKey });
  if (op.kind === 'addExercise') {
    const response = await apiFetch(`/workout-sessions/${op.sessionId}/exercises`, { method: 'POST', body });
    return { response };
  }
  const exerciseId = resolvedExerciseId ?? (typeof op.exerciseId === 'number' ? op.exerciseId : null);
  if (exerciseId == null) throw new Error('Exercício ainda não confirmado no servidor.');
  const response = await apiFetch(`/workout-sessions/${op.sessionId}/exercises/${exerciseId}/sets`, {
    method: 'POST',
    body,
  });
  return { response };
};

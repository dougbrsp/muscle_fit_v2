import { apiFetch, isTransientApiError } from '@/lib/api';
import { opQueue } from '@/lib/offline/queue';

export type SetPostResult<T> =
  /** O servidor respondeu 2xx: `saved` é a resposta (confirmado). */
  | { queued: false; saved: T }
  /** Sem rede/serviço indisponível: gravada no aparelho e na fila; ainda NÃO confirmada. */
  | { queued: true; saved: null; clientKey: string };

/**
 * POST de uma série com a `client_key` já presente no corpo. Se a rede falhar
 * (status 0) ou o serviço estiver indisponível (503), a série é gravada na fila
 * offline e o reenvio usa a MESMA chave (o servidor deduplica). Qualquer outro
 * erro HTTP sobe como antes: a tela continua mostrando o erro recuperável.
 * Só o corpo montado pela tela é reenviado; nada é calculado aqui.
 */
export async function postSetOrQueue<T>(args: {
  sessionId: number;
  exerciseId: number;
  body: Record<string, unknown> & { client_key: string };
}): Promise<SetPostResult<T>> {
  const { sessionId, exerciseId, body } = args;
  let saved: T;
  try {
    saved = await apiFetch<T>(`/workout-sessions/${sessionId}/exercises/${exerciseId}/sets`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  } catch (error) {
    if (!isTransientApiError(error)) throw error;
    await opQueue.enqueue({
      kind: 'addSet',
      sessionId,
      exerciseId,
      payload: body,
      clientKey: body.client_key,
    });
    return { queued: true, saved: null, clientKey: body.client_key };
  }
  // Confirmado pelo servidor por fora da fila (ex.: "Tentar de novo" depois de
  // ficar na fila): a pendência local com a mesma chave deixa de existir.
  if (opQueue.stateOf(body.client_key) != null) await opQueue.discardConfirmed(body.client_key).catch(() => undefined);
  return { queued: false, saved };
}

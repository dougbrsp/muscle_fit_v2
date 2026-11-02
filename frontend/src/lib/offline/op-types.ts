// Tipos da fila de operações offline (plano mobile 3.5). Só `addExercise` e
// `addSet` entram na fila: são as duas escritas que o servidor já deduplica
// por `client_key` (api/app/workout_sessions.py). start/finish/PATCH ficam fora.

/** saved_local -> syncing -> synced | error | conflict. */
export type OpStatus = 'saved_local' | 'syncing' | 'synced' | 'error' | 'conflict';

export type OpKind = 'addExercise' | 'addSet';

export type OfflineOp = {
  opId: string;
  userId: number;
  kind: OpKind;
  sessionId: number;
  /** Exercício da série: id do servidor (>0), id LOCAL negativo (`localId` de um addExercise) ou a `client_key` dele. */
  exerciseId: number | string | null;
  /** Corpo exato que seria enviado ao servidor (sem derivar nada no cliente). */
  payload: Record<string, unknown>;
  clientKey: string;
  createdAt: number;
  attempts: number;
  status: OpStatus;
  /** Epoch ms da próxima tentativa (backoff); ausente = elegível agora. */
  nextAttemptAt?: number;
  /** Por que está em conflito (decide as ações oferecidas ao usuário). */
  conflictKind?: 'echo_mismatch' | 'set_number' | 'orphan' | 'http';
  /** addExercise: id LOCAL negativo usado pela tela até o servidor devolver o id real. */
  localId?: number;
  /** Mensagem recuperável da última falha (nunca mostrada como sucesso). */
  lastError?: string | null;
  /** Resposta do servidor quando `synced` (para conciliar a tela). */
  serverResponse?: unknown;
};

export const OP_KEY_PREFIX = 'op:';

/** Op que ainda não está confirmada pelo servidor (bloqueia logout silencioso e atualização do SW). */
export function isUnsyncedOp(op: Pick<OfflineOp, 'status'>): boolean {
  return op.status !== 'synced';
}

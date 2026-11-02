// Fila de operações offline (plano mobile 3.5). Só `addExercise` e `addSet`:
// são as duas escritas que o servidor já deduplica por `client_key`
// (`api/app/workout_sessions.py`, UNIQUE `uq_exercises_activity_client_key` e
// `uq_exercise_sets_exercise_client_key`; o replay devolve `duplicate: true`
// sem gravar de novo). start/finish/PATCH NÃO entram aqui.
//
// Garantias:
// - `enqueue` só resolve DEPOIS de a op estar gravada no aparelho (saved_local);
// - "synced" só depois de uma resposta 2xx do servidor;
// - FIFO por exercício (uma op que falha trava as seguintes do mesmo exercício);
// - falha de rede/5xx -> `error` com backoff; 409/422/404/403 -> `conflict`
//   (ação do usuário), nunca sobrescreve em silêncio;
// - nenhum cálculo de domínio: o corpo (`payload`) é o que a tela já montou.

import { getOfflineBackend, type OfflineBackend } from './backend';
import { makeKey } from './keys';
import { describeEchoMismatch } from './echo';
import { OP_KEY_PREFIX, isUnsyncedOp, type OfflineOp, type OpKind, type OpStatus } from './op-types';
import { enqueueBackendTask, onOfflinePurge } from './scope';

// Constantes TÉCNICAS de reenvio (não são regra de treino).
export const BACKOFF_MS: readonly number[] = [2_000, 5_000, 15_000, 30_000, 60_000];
export const SYNC_TICK_MS = 20_000;
export const SYNCED_RETENTION_MS = 24 * 60 * 60 * 1000;

export type TransportResult = { response: unknown };
/** Envia a op ao servidor. Lança `{ status, message }` (ex.: `ApiError`) em falha HTTP/rede. */
export type OpTransport = (op: OfflineOp, resolvedExerciseId: number | null) => Promise<TransportResult>;

export type OpInput = {
  kind: OpKind;
  sessionId: number;
  /** addSet: id do servidor (número) ou `client_key` do exercício ainda não sincronizado (string). */
  exerciseId: number | string | null;
  payload: Record<string, unknown>;
  clientKey: string;
};

export type QueueSnapshot = {
  ops: readonly OfflineOp[];
  /** saved_local + syncing + error: aguardando o servidor. */
  pending: number;
  error: number;
  conflict: number;
  syncing: number;
  unsynced: number;
};

export type OpQueueDeps = {
  backend?: () => Promise<OfflineBackend>;
  transport: OpTransport;
  now?: () => number;
  isOnline?: () => boolean;
  newId?: () => string;
};

type FailureKind = 'retry' | 'conflict' | 'auth';

export function classifyFailure(error: unknown): FailureKind {
  const status = (error as { status?: unknown } | null)?.status;
  if (typeof status !== 'number' || status === 0) return 'retry';
  if (status === 401) return 'auth';
  if (status === 408 || status === 429 || status >= 500) return 'retry';
  return 'conflict';
}

function defaultId(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (c?.randomUUID) return c.randomUUID();
  return `op-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function errorMessage(error: unknown): string {
  const detail = (error as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string') {
    return (detail as { message: string }).message;
  }
  return error instanceof Error && error.message ? error.message : 'Falha ao sincronizar.';
}

/** 409 `set_number_conflict`: outro registro ocupa esse número de série no servidor. */
function isSetNumberConflict(error: unknown): boolean {
  const detail = (error as { detail?: unknown } | null)?.detail;
  return Boolean(detail) && typeof detail === 'object' && (detail as { code?: unknown }).code === 'set_number_conflict';
}

function groupOf(op: OfflineOp): string {
  if (op.kind === 'addExercise') return `ex:${op.clientKey}`;
  return typeof op.exerciseId === 'string' ? `ex:${op.exerciseId}` : `id:${String(op.exerciseId)}`;
}

export function createOpQueue(deps: OpQueueDeps) {
  const now = deps.now ?? (() => Date.now());
  const newId = deps.newId ?? defaultId;
  const isOnline = deps.isOnline ?? (() => (typeof navigator === 'undefined' ? true : navigator.onLine !== false));
  const getBackend = deps.backend ?? getOfflineBackend;

  let userId: number | null = null;
  let ops: OfflineOp[] = [];
  let snapshot: QueueSnapshot = buildSnapshot();
  const listeners = new Set<() => void>();
  let flushing: Promise<void> | null = null;
  let flushAgain = false;
  let generation = 0;
  let hydratedUser: number | null = null;

  function buildSnapshot(): QueueSnapshot {
    let error = 0;
    let conflict = 0;
    let syncing = 0;
    let pending = 0;
    let unsynced = 0;
    for (const op of ops) {
      if (isUnsyncedOp(op)) unsynced += 1;
      if (op.status === 'error') error += 1;
      if (op.status === 'conflict') conflict += 1;
      if (op.status === 'syncing') syncing += 1;
      if (op.status === 'saved_local' || op.status === 'syncing' || op.status === 'error') pending += 1;
    }
    return { ops: [...ops].sort((a, b) => a.createdAt - b.createdAt), pending, error, conflict, syncing, unsynced };
  }

  function emit(): void {
    snapshot = buildSnapshot();
    for (const listener of listeners) listener();
  }

  async function persistOp(op: OfflineOp): Promise<void> {
    const owner = userId;
    if (owner == null) throw new Error('Nenhum usuário ativo para gravar a operação.');
    await enqueueBackendTask(async () => {
      const backend = await getBackend();
      await backend.put('op_queue', makeKey(owner, `${OP_KEY_PREFIX}${op.opId}`), op);
    });
  }

  async function removeOp(op: OfflineOp): Promise<void> {
    const owner = userId;
    if (owner == null) return;
    await enqueueBackendTask(async () => {
      const backend = await getBackend();
      await backend.delete('op_queue', makeKey(owner, `${OP_KEY_PREFIX}${op.opId}`));
    });
  }

  function replace(op: OfflineOp): void {
    ops = ops.map((current) => (current.opId === op.opId ? op : current));
    emit();
  }

  onOfflinePurge(() => {
    generation += 1;
    userId = null;
    hydratedUser = null;
    ops = [];
    emit();
  });

  /** Carrega as ops do usuário; `syncing` que sobrou de um fechamento abrupto volta a `saved_local`. */
  async function hydrate(forUser: number): Promise<void> {
    // Mesmo usuário já carregado (remontagem, volta da rede): não recarrega do disco
    // nem derruba um envio em andamento — evita reenvio desnecessário.
    if (hydratedUser === forUser && userId === forUser) return;
    generation += 1;
    const myGeneration = generation;
    userId = forUser;
    const backend = await getBackend();
    const rows = await enqueueBackendTask(() => backend.list('op_queue', makeKey(forUser, OP_KEY_PREFIX)));
    if (myGeneration !== generation || userId !== forUser) return;
    const loaded: OfflineOp[] = [];
    for (const row of rows) {
      const op = row.value as OfflineOp;
      if (!op || typeof op.opId !== 'string') continue;
      if (op.status === 'synced' && now() - op.createdAt > SYNCED_RETENTION_MS) {
        void removeOp(op);
        continue;
      }
      if (op.status === 'syncing') {
        const reverted: OfflineOp = { ...op, status: 'saved_local' };
        loaded.push(reverted);
        void persistOp(reverted).catch(() => undefined);
      } else {
        loaded.push(op);
      }
    }
    ops = loaded;
    hydratedUser = forUser;
    emit();
  }

  function isEditable(op: OfflineOp): boolean {
    return op.status === 'saved_local' || op.status === 'error' || op.status === 'conflict';
  }

  function samePayload(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  /** Troca o corpo de uma op ainda não enviada (mesma client_key) e a recoloca na fila. */
  async function replacePayload(op: OfflineOp, payload: Record<string, unknown>): Promise<void> {
    const next: OfflineOp = {
      ...op,
      payload,
      status: 'saved_local',
      conflictKind: undefined,
      lastError: null,
      nextAttemptAt: undefined,
    };
    await persistOp(next);
    replace(next);
  }

  /**
   * Edita uma série AINDA PENDENTE (mesma client_key, sem nova op).
   * 'edited' = corpo atualizado na fila; 'in_flight' = o envio está em curso (tente
   * de novo em instantes); 'none' = não está pendente (use o PATCH ao servidor).
   */
  async function editPendingSet(
    sessionId: number,
    exerciseId: number,
    setNumber: number,
    patch: Record<string, unknown>,
  ): Promise<'edited' | 'in_flight' | 'none'> {
    const op = ops.find(
      (candidate) =>
        candidate.kind === 'addSet' &&
        candidate.sessionId === sessionId &&
        candidate.exerciseId === exerciseId &&
        candidate.payload.set_number === setNumber &&
        candidate.status !== 'synced',
    );
    if (!op) return 'none';
    if (op.status === 'syncing') return 'in_flight';
    await replacePayload(op, { ...op.payload, ...patch });
    void flush();
    return 'edited';
  }

  /** Grava a op no aparelho. Só resolve depois de gravada; rejeita se não conseguiu (a tela NÃO mostra "salvo"). */
  async function enqueue(input: OpInput): Promise<OfflineOp> {
    if (userId == null) throw new Error('Nenhum usuário ativo para gravar a operação.');
    const existing = ops.find((op) => op.kind === input.kind && op.sessionId === input.sessionId && op.clientKey === input.clientKey);
    if (existing) {
      // Mesma chave = mesma operação (duplo toque, remontagem, edição): atualiza o
      // corpo se ainda não saiu; em voo/sincronizada devolve a op como está.
      if (isEditable(existing) && !samePayload(existing.payload, input.payload)) {
        await replacePayload(existing, input.payload);
        void flush();
      }
      return ops.find((op) => op.opId === existing.opId) ?? existing;
    }
    const op: OfflineOp = {
      opId: newId(),
      userId,
      kind: input.kind,
      sessionId: input.sessionId,
      exerciseId: input.exerciseId,
      payload: input.payload,
      clientKey: input.clientKey,
      createdAt: now(),
      attempts: 0,
      status: 'saved_local',
    };
    await persistOp(op);
    ops = [...ops, op];
    emit();
    void flush();
    return op;
  }

  function resolveExerciseId(op: OfflineOp): number | null | 'blocked' {
    if (op.kind === 'addExercise') return null;
    if (typeof op.exerciseId === 'number') return op.exerciseId;
    const parent = ops.find((candidate) => candidate.kind === 'addExercise' && candidate.clientKey === op.exerciseId);
    if (!parent || parent.status !== 'synced') return 'blocked';
    const id = (parent.serverResponse as { exercise_id?: unknown } | undefined)?.exercise_id;
    return typeof id === 'number' ? id : 'blocked';
  }

  async function runFlush(): Promise<void> {
    const myGeneration = generation;
    const blocked = new Set<string>();
    const order = [...ops].sort((a, b) => a.createdAt - b.createdAt);
    for (const original of order) {
      if (myGeneration !== generation) return;
      if (!isOnline()) return;
      const op = ops.find((candidate) => candidate.opId === original.opId);
      if (!op || op.status === 'synced') continue;
      const group = groupOf(op);
      if (op.status === 'conflict') {
        blocked.add(group); // ação do usuário pendente: não passa na frente
        continue;
      }
      if (blocked.has(group)) continue;
      if (op.status === 'error' && (op.nextAttemptAt ?? 0) > now()) {
        blocked.add(group);
        continue;
      }
      const exerciseId = resolveExerciseId(op);
      if (exerciseId === 'blocked') {
        blocked.add(group);
        continue;
      }
      const syncing: OfflineOp = { ...op, status: 'syncing', attempts: op.attempts + 1 };
      replace(syncing);
      await persistOp(syncing).catch(() => undefined);
      try {
        const result = await deps.transport(syncing, exerciseId);
        if (myGeneration !== generation) return;
        // Replay com a mesma chave mas conteúdo diferente: nunca vira "sincronizado".
        const mismatch = syncing.kind === 'addSet' ? describeEchoMismatch(syncing.payload, result.response) : null;
        if (mismatch) {
          const clash: OfflineOp = {
            ...syncing,
            status: 'conflict',
            conflictKind: 'echo_mismatch',
            lastError: mismatch,
            serverResponse: result.response,
          };
          replace(clash);
          await persistOp(clash).catch(() => undefined);
          blocked.add(group);
          continue;
        }
        const done: OfflineOp = {
          ...syncing,
          status: 'synced',
          lastError: null,
          nextAttemptAt: undefined,
          serverResponse: result.response,
        };
        replace(done);
        await persistOp(done).catch(() => undefined);
      } catch (error) {
        if (myGeneration !== generation) return;
        const kind = classifyFailure(error);
        if (kind === 'auth') {
          // Sessão expirada: nada foi confirmado; volta a saved_local e para.
          const back: OfflineOp = { ...syncing, status: 'saved_local', attempts: op.attempts };
          replace(back);
          await persistOp(back).catch(() => undefined);
          return;
        }
        const failed: OfflineOp =
          kind === 'conflict'
            ? {
                ...syncing,
                status: 'conflict',
                conflictKind: isSetNumberConflict(error) ? 'set_number' : 'http',
                lastError: errorMessage(error),
              }
            : {
                ...syncing,
                status: 'error',
                lastError: errorMessage(error),
                nextAttemptAt: now() + BACKOFF_MS[Math.min(syncing.attempts - 1, BACKOFF_MS.length - 1)],
              };
        replace(failed);
        await persistOp(failed).catch(() => undefined);
        blocked.add(group);
      }
    }
  }

  /** Sincroniza o que estiver elegível; chamadas simultâneas se juntam (uma execução + uma repetição). */
  function flush(): Promise<void> {
    if (userId == null) return Promise.resolve();
    if (flushing) {
      flushAgain = true;
      return flushing;
    }
    flushing = (async () => {
      try {
        do {
          flushAgain = false;
          await runFlush();
        } while (flushAgain);
      } finally {
        flushing = null;
      }
    })();
    return flushing;
  }

  /** Tentar agora: ignora o backoff de uma op em `error`. */
  async function retry(opId: string): Promise<void> {
    const op = ops.find((candidate) => candidate.opId === opId || candidate.clientKey === opId);
    if (!op || (op.status !== 'error' && op.status !== 'conflict')) return;
    const next: OfflineOp = { ...op, status: 'saved_local', nextAttemptAt: undefined };
    replace(next);
    await persistOp(next);
    await flush();
  }

  /**
   * Ação do usuário sobre um conflito: 'resend' tenta de novo com a mesma
   * `client_key` (sem sobrescrever o que o servidor já tem); 'keep_server'
   * descarta a versão local.
   */
  async function resolveConflict(opId: string, choice: 'resend' | 'keep_server'): Promise<void> {
    const op = ops.find((candidate) => candidate.opId === opId);
    if (!op || op.status !== 'conflict') return;
    if (choice === 'keep_server') {
      await removeOp(op);
      ops = ops.filter((candidate) => candidate.opId !== opId);
      emit();
      return;
    }
    await retry(opId);
  }

  function start(): () => void {
    if (typeof window === 'undefined') return () => undefined;
    const trigger = () => void flush();
    const onVisibility = () => {
      if (typeof document === 'undefined' || document.visibilityState === 'visible') trigger();
    };
    window.addEventListener('online', trigger);
    window.addEventListener('pageshow', trigger);
    window.addEventListener('focus', trigger);
    document.addEventListener('visibilitychange', onVisibility);
    const tick = window.setInterval(() => {
      if (document.visibilityState === 'visible') trigger();
    }, SYNC_TICK_MS);
    trigger();
    return () => {
      window.removeEventListener('online', trigger);
      window.removeEventListener('pageshow', trigger);
      window.removeEventListener('focus', trigger);
      document.removeEventListener('visibilitychange', onVisibility);
      window.clearInterval(tick);
    };
  }

  /**
   * O servidor confirmou esta `client_key` por fora da fila (POST direto com
   * 2xx): a op local deixa de ser pendência. Nada é enviado.
   */
  async function discardConfirmed(clientKey: string): Promise<void> {
    const stale = ops.filter((op) => op.clientKey === clientKey && op.status !== 'synced');
    if (stale.length === 0) return;
    for (const op of stale) await removeOp(op);
    ops = ops.filter((op) => !stale.includes(op));
    emit();
  }

  /**
   * Espera a op chegar a um estado final: 'synced' (2xx do servidor),
   * 'conflict' (ação do usuário) ou 'removed' (descartada/purge). Erros de
   * rede NÃO resolvem: a op segue tentando.
   */
  function whenSettled(clientKey: string): Promise<'synced' | 'conflict' | 'removed'> {
    return new Promise((resolve) => {
      const check = (): boolean => {
        const op = ops.find((candidate) => candidate.clientKey === clientKey);
        const outcome = !op ? 'removed' : op.status === 'synced' ? 'synced' : op.status === 'conflict' ? 'conflict' : null;
        if (outcome) resolve(outcome);
        return outcome != null;
      };
      if (check()) return;
      const unsubscribe = (() => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      })();
      function listener() {
        if (check()) unsubscribe();
      }
    });
  }

  function stateOf(clientKey: string): OpStatus | null {
    return ops.find((op) => op.clientKey === clientKey)?.status ?? null;
  }

  return {
    hydrate,
    enqueue,
    flush,
    retry,
    resolveConflict,
    editPendingSet,
    start,
    stateOf,
    whenSettled,
    discardConfirmed,
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export type OpQueue = ReturnType<typeof createOpQueue>;

import { beforeEach, describe, expect, it } from 'vitest';
import { createMemoryBackend, type OfflineBackend } from './backend';
import { makeKey } from './keys';
import { BACKOFF_MS, classifyFailure, createOpQueue, type OpTransport } from './op-queue';
import type { OfflineOp } from './op-types';
import { countUnsyncedOps } from './purge';
import { setOfflineBackendForTests } from './backend';
import { flushBackendTasks } from './scope';

class HttpError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

// Servidor falso com a MESMA semântica de idempotência do backend: a segunda
// chamada com a mesma client_key devolve o registro original com duplicate:true
// e NÃO grava de novo (api/app/workout_sessions.py, `_find_*_by_client_key`).
function fakeServer() {
  const exercises = new Map<string, { exercise_id: number }>();
  const sets = new Map<string, { set_number: number }>();
  const calls: Array<{ kind: string; clientKey: string; exerciseId: number | null; payload: Record<string, unknown> }> = [];
  let nextExerciseId = 100;
  let failWith: (() => Error | null) | null = null;
  const transport: OpTransport = async (op, exerciseId) => {
    calls.push({ kind: op.kind, clientKey: op.clientKey, exerciseId, payload: op.payload });
    const failure = failWith?.();
    if (failure) throw failure;
    if (op.kind === 'addExercise') {
      const existing = exercises.get(op.clientKey);
      if (existing) return { response: { status: 'ok', ...existing, duplicate: true } };
      const created = { exercise_id: nextExerciseId++ };
      exercises.set(op.clientKey, created);
      return { response: { status: 'ok', ...created, duplicate: false } };
    }
    const key = `${exerciseId}:${op.clientKey}`;
    const existing = sets.get(key);
    if (existing) return { response: { status: 'ok', ...existing, duplicate: true } };
    const created = { set_number: sets.size + 1 };
    sets.set(key, created);
    return { response: { status: 'ok', ...created, duplicate: false } };
  };
  return {
    transport,
    calls,
    sets,
    exercises,
    failWith(fn: (() => Error | null) | null) {
      failWith = fn;
    },
  };
}

let backend: OfflineBackend;
let clock: number;
let online: boolean;

beforeEach(() => {
  backend = createMemoryBackend();
  setOfflineBackendForTests(backend);
  clock = 1_000_000;
  online = true;
});

function build(server: ReturnType<typeof fakeServer>) {
  return createOpQueue({
    backend: async () => backend,
    transport: server.transport,
    now: () => clock,
    isOnline: () => online,
    newId: (() => {
      let n = 0;
      return () => `op-${++n}`;
    })(),
  });
}

const SET_INPUT = {
  kind: 'addSet' as const,
  sessionId: 10,
  exerciseId: 5,
  payload: { reps: 10, weight_kg: 40 },
  clientKey: 'set:5:1',
};

describe('fila offline: gravação e estados', () => {
  it('enqueue só resolve com a op gravada no aparelho; offline fica saved_local e NÃO chama o servidor', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    online = false;
    const op = await queue.enqueue(SET_INPUT);
    await queue.flush();
    expect(op.status).toBe('saved_local');
    expect(server.calls).toHaveLength(0);
    const stored = await backend.get('op_queue', makeKey(1, 'op:op-1'));
    expect((stored as OfflineOp).status).toBe('saved_local');
    expect(queue.getSnapshot().unsynced).toBe(1);
    expect(await countUnsyncedOps(1)).toBe(1);
  });

  it('reconectar sincroniza exatamente 1 vez, com a mesma client_key, e só então vira synced', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    online = false;
    await queue.enqueue(SET_INPUT);
    online = true;
    await queue.flush();
    await queue.flush(); // gatilhos repetidos (online + visibilitychange + tick) não reenviam
    expect(server.calls).toHaveLength(1);
    expect(server.calls[0]).toMatchObject({ kind: 'addSet', clientKey: 'set:5:1', exerciseId: 5, payload: { reps: 10, weight_kg: 40 } });
    expect(queue.stateOf('set:5:1')).toBe('synced');
    expect(queue.getSnapshot().unsynced).toBe(0);
  });

  it('enqueue repetido com a mesma client_key não duplica a operação (duplo toque / remontagem)', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    online = false;
    await queue.enqueue(SET_INPUT);
    await queue.enqueue(SET_INPUT);
    expect(queue.getSnapshot().ops).toHaveLength(1);
  });

  it('replay após resposta perdida (servidor gravou, cliente não viu): duplicate:true vira synced, sem 2ª gravação', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    // 1ª tentativa grava no servidor mas a resposta se perde (timeout de rede).
    let first = true;
    const original = server.transport;
    const lossy: OpTransport = async (op, id) => {
      const result = await original(op, id);
      if (first) {
        first = false;
        throw new HttpError('timeout', 0);
      }
      return result;
    };
    const q = createOpQueue({ backend: async () => backend, transport: lossy, now: () => clock, isOnline: () => online });
    await q.hydrate(1);
    await q.enqueue(SET_INPUT);
    await q.flush();
    expect(q.stateOf('set:5:1')).toBe('error');
    clock += BACKOFF_MS[0] + 1;
    await q.flush();
    expect(q.stateOf('set:5:1')).toBe('synced');
    expect(server.sets.size).toBe(1);
    const synced = q.getSnapshot().ops[0];
    expect((synced.serverResponse as { duplicate: boolean }).duplicate).toBe(true);
  });

  it('API 500: fica em error (nunca synced), com backoff; antes do prazo não reenvia', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    server.failWith(() => new HttpError('Erro interno', 500));
    await queue.enqueue(SET_INPUT);
    await queue.flush();
    expect(queue.stateOf('set:5:1')).toBe('error');
    expect(queue.getSnapshot().error).toBe(1);
    const calls = server.calls.length;
    await queue.flush();
    expect(server.calls.length).toBe(calls); // ainda dentro do backoff
    server.failWith(null);
    clock += BACKOFF_MS[0] + 1;
    await queue.flush();
    expect(queue.stateOf('set:5:1')).toBe('synced');
  });

  it('retry manual ignora o backoff', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    server.failWith(() => new HttpError('Erro interno', 503));
    const op = await queue.enqueue(SET_INPUT);
    await queue.flush();
    server.failWith(null);
    await queue.retry(op.opId);
    expect(queue.stateOf('set:5:1')).toBe('synced');
  });
});

describe('fila offline: conflito e ordem', () => {
  it('409 (treino finalizado) e 422 viram conflict, sem reenvio automático e sem sobrescrever', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    server.failWith(() => new HttpError('Este treino já foi finalizado.', 409));
    await queue.enqueue(SET_INPUT);
    await queue.flush();
    expect(queue.stateOf('set:5:1')).toBe('conflict');
    const calls = server.calls.length;
    server.failWith(null);
    clock += 10 * BACKOFF_MS[BACKOFF_MS.length - 1];
    await queue.flush();
    expect(server.calls.length).toBe(calls);
    expect(queue.getSnapshot().ops[0].lastError).toContain('finalizado');
    expect(queue.getSnapshot().conflict).toBe(1);
  });

  it('resolver conflito: manter o servidor descarta a op; reenviar tenta com a mesma client_key', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    server.failWith(() => new HttpError('Dados inválidos', 422));
    const a = await queue.enqueue(SET_INPUT);
    await queue.flush();
    await queue.resolveConflict(a.opId, 'keep_server');
    expect(queue.getSnapshot().ops).toHaveLength(0);
    expect(await backend.get('op_queue', makeKey(1, `op:${a.opId}`))).toBeUndefined();

    const b = await queue.enqueue({ ...SET_INPUT, clientKey: 'set:5:2' });
    await queue.flush();
    expect(queue.stateOf('set:5:2')).toBe('conflict');
    server.failWith(null);
    await queue.resolveConflict(b.opId, 'resend');
    expect(queue.stateOf('set:5:2')).toBe('synced');
    expect(server.calls.at(-1)?.clientKey).toBe('set:5:2');
  });

  it('FIFO por exercício: uma série com erro trava as seguintes do mesmo exercício, não as de outro', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    let failFirst = true;
    server.failWith(() => {
      if (failFirst) {
        failFirst = false;
        return new HttpError('Erro interno', 500);
      }
      return null;
    });
    clock = 1;
    await queue.enqueue(SET_INPUT);
    clock = 2;
    await queue.enqueue({ ...SET_INPUT, clientKey: 'set:5:2' });
    clock = 3;
    await queue.enqueue({ ...SET_INPUT, exerciseId: 6, clientKey: 'set:6:1' });
    await queue.flush();
    expect(queue.stateOf('set:5:1')).toBe('error');
    expect(queue.stateOf('set:5:2')).toBe('saved_local'); // travada atrás da 1ª
    expect(queue.stateOf('set:6:1')).toBe('synced'); // outro exercício segue
    expect(server.calls.map((c) => c.clientKey)).toEqual(['set:5:1', 'set:6:1']);
  });

  it('série de exercício criado offline espera o addExercise e usa o exercise_id devolvido', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    online = false;
    clock = 1;
    await queue.enqueue({ kind: 'addExercise', sessionId: 10, exerciseId: null, payload: { original_name: 'Supino' }, clientKey: 'ex:A' });
    clock = 2;
    await queue.enqueue({ ...SET_INPUT, exerciseId: 'ex:A', clientKey: 'set:A:1' });
    online = true;
    await queue.flush();
    expect(server.calls.map((c) => [c.kind, c.exerciseId])).toEqual([
      ['addExercise', null],
      ['addSet', 100],
    ]);
    expect(queue.getSnapshot().unsynced).toBe(0);
  });

  it('401 não confirma nada: volta a saved_local sem contar tentativa e para o ciclo', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    server.failWith(() => new HttpError('Sessão expirada.', 401));
    await queue.enqueue(SET_INPUT);
    await queue.flush();
    const op = queue.getSnapshot().ops[0];
    expect(op.status).toBe('saved_local');
    expect(op.attempts).toBe(0);
  });
});

describe('fila offline: persistência e isolamento', () => {
  it('reabrir a página: ops pendentes são recarregadas; syncing órfão volta a saved_local e é enviado uma vez', async () => {
    const server = fakeServer();
    const first = build(server);
    await first.hydrate(1);
    online = false;
    await first.enqueue(SET_INPUT);
    // simula fechamento no meio de um envio
    const stored = (await backend.get('op_queue', makeKey(1, 'op:op-1'))) as OfflineOp;
    await backend.put('op_queue', makeKey(1, 'op:op-1'), { ...stored, status: 'syncing' });

    const reopened = build(server);
    await reopened.hydrate(1);
    expect(reopened.getSnapshot().ops[0].status).toBe('saved_local');
    online = true;
    await reopened.flush();
    expect(server.calls).toHaveLength(1);
    expect(reopened.stateOf('set:5:1')).toBe('synced');
  });

  it('ops de um usuário não aparecem para outro', async () => {
    const server = fakeServer();
    const queueA = build(server);
    await queueA.hydrate(1);
    online = false;
    await queueA.enqueue(SET_INPUT);
    const queueB = build(server);
    await queueB.hydrate(2);
    expect(queueB.getSnapshot().ops).toHaveLength(0);
    expect(await countUnsyncedOps(2)).toBe(0);
    await flushBackendTasks();
  });

  it('classifyFailure: rede/5xx/429 = retry; 401 = auth; demais 4xx = conflict', () => {
    expect(classifyFailure(new HttpError('x', 0))).toBe('retry');
    expect(classifyFailure(new Error('rede'))).toBe('retry');
    expect(classifyFailure(new HttpError('x', 503))).toBe('retry');
    expect(classifyFailure(new HttpError('x', 429))).toBe('retry');
    expect(classifyFailure(new HttpError('x', 401))).toBe('auth');
    expect(classifyFailure(new HttpError('x', 409))).toBe('conflict');
    expect(classifyFailure(new HttpError('x', 422))).toBe('conflict');
    expect(classifyFailure(new HttpError('x', 404))).toBe('conflict');
  });
});

describe('fila offline: apoio à tela', () => {
  it('whenSettled resolve synced só depois do 2xx e conflict em recusa; erro de rede não resolve', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    server.failWith(() => new HttpError('Erro interno', 500));
    await queue.enqueue(SET_INPUT);
    await queue.flush();
    let settled: string | null = null;
    void queue.whenSettled('set:5:1').then((value) => (settled = value));
    await Promise.resolve();
    expect(settled).toBeNull(); // error não é final
    server.failWith(null);
    clock += BACKOFF_MS[0] + 1;
    await queue.flush();
    await Promise.resolve();
    await Promise.resolve();
    expect(settled).toBe('synced');
  });

  it('discardConfirmed remove a pendência quando o servidor confirmou por fora da fila', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    server.failWith(() => new HttpError('Dados inválidos', 422));
    await queue.enqueue(SET_INPUT);
    await queue.flush();
    expect(queue.getSnapshot().conflict).toBe(1);
    await queue.discardConfirmed('set:5:1');
    expect(queue.getSnapshot().ops).toHaveLength(0);
    expect(await countUnsyncedOps(1)).toBe(0);
  });

  it('retry aceita a client_key (usado pelo "Tentar de novo" do Modo Intensivo)', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    server.failWith(() => new HttpError('Dados inválidos', 422));
    await queue.enqueue(SET_INPUT);
    await queue.flush();
    server.failWith(null);
    await queue.retry('set:5:1');
    expect(queue.stateOf('set:5:1')).toBe('synced');
  });
});

describe('fila offline: eco do servidor e edição de pendente', () => {
  function serverWithEcho(echo: Record<string, unknown>): OpTransport {
    return async () => ({ response: { status: 'ok', duplicate: true, set_number: 2, ...echo } });
  }
  async function run(transport: OpTransport) {
    const queue = createOpQueue({ backend: async () => backend, transport, now: () => clock, isOnline: () => online });
    await queue.hydrate(1);
    await queue.enqueue(SET_INPUT);
    await queue.flush();
    return queue;
  }

  it('replay com valores gravados diferentes vira conflict (echo_mismatch), nunca synced', async () => {
    const queue = await run(serverWithEcho({ reps: 8, weight_kg: 40, set_type: 'working', rpe: 8 }));
    const op = queue.getSnapshot().ops[0];
    expect(op.status).toBe('conflict');
    expect(op.conflictKind).toBe('echo_mismatch');
    expect(op.lastError).toContain('repetições');
  });

  it('eco igual (inclusive tipos string/number) ou ausente não gera conflito', async () => {
    expect((await run(serverWithEcho({ reps: 10, weight_kg: '40.00' }))).stateOf('set:5:1')).toBe('synced');
    expect((await run(serverWithEcho({}))).stateOf('set:5:1')).toBe('synced');
  });

  it('eco em criação nova (duplicate:false) não é comparado', async () => {
    const queue = await run(async () => ({ response: { status: 'ok', duplicate: false, reps: 99 } }));
    expect(queue.stateOf('set:5:1')).toBe('synced');
  });

  it('409 set_number_conflict vira conflict recuperável com a mensagem do servidor', async () => {
    const error = Object.assign(new HttpError('x', 409), {
      detail: { code: 'set_number_conflict', message: 'Já existe a série 2.', set_number: 2 },
    });
    const queue = await run(async () => {
      throw error;
    });
    const op = queue.getSnapshot().ops[0];
    expect(op.status).toBe('conflict');
    expect(op.conflictKind).toBe('set_number');
    expect(op.lastError).toBe('Já existe a série 2.');
  });

  it('editar série pendente atualiza a MESMA op (mesma client_key), sem criar outra', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    online = false;
    const first = await queue.enqueue(SET_INPUT);
    const edited = await queue.enqueue({ ...SET_INPUT, payload: { ...SET_INPUT.payload, reps: 9 } });
    expect(edited.opId).toBe(first.opId);
    expect(queue.getSnapshot().ops).toHaveLength(1);
    expect(queue.getSnapshot().ops[0].payload.reps).toBe(9);
    expect(await queue.editPendingSet(10, 5, 2, { reps: 7 })).toBe('none');
  });

  it('editPendingSet altera o corpo pendente por set_number e envia só a versão final', async () => {
    const server = fakeServer();
    const queue = build(server);
    await queue.hydrate(1);
    online = false;
    await queue.enqueue({ ...SET_INPUT, payload: { set_number: 2, reps: 10, weight_kg: 40 } });
    expect(await queue.editPendingSet(10, 5, 2, { reps: 8 })).toBe('edited');
    online = true;
    await queue.flush();
    expect(server.calls).toHaveLength(1);
    expect(server.calls[0].payload).toMatchObject({ set_number: 2, reps: 8, weight_kg: 40 });
    expect(await queue.editPendingSet(10, 5, 2, { reps: 6 })).toBe('none'); // já sincronizada: PATCH
  });
});

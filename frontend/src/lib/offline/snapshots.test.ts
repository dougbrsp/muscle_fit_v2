import { beforeEach, describe, expect, it } from 'vitest';
import { createMemoryBackend, setOfflineBackendForTests } from './backend';
import { makeKey } from './keys';
import { flushBackendTasks } from './scope';
import { purgeOfflineUser } from './purge';
import { fetchWithSnapshot, loadSnapshots, readSnapshot } from './snapshots';

class Net extends Error {
  status = 0;
}

beforeEach(async () => {
  setOfflineBackendForTests(createMemoryBackend());
  await purgeOfflineUser(1);
  await purgeOfflineUser(2);
});

describe('retrato offline das leituras', () => {
  it('rede ok grava o retrato; falha de conexão devolve o retrato; erro de servidor não', async () => {
    await loadSnapshots(1);
    expect(await fetchWithSnapshot('activity:9', async () => ({ id: 9 }))).toEqual({ id: 9 });
    await flushBackendTasks();
    expect(await fetchWithSnapshot('activity:9', async () => Promise.reject(new Net('sem rede')))).toEqual({ id: 9 });
    await expect(
      fetchWithSnapshot('activity:9', async () => Promise.reject(Object.assign(new Error('x'), { status: 500 }))),
    ).rejects.toThrow('x');
    await expect(fetchWithSnapshot('plan:1', async () => Promise.reject(new Net('sem rede')))).rejects.toThrow('sem rede');
  });

  it('persiste por usuário e recarrega na reabertura; outro usuário não lê', async () => {
    await loadSnapshots(1);
    await fetchWithSnapshot('activity:9', async () => ({ id: 9 }));
    await flushBackendTasks();
    const backend = createMemoryBackend();
    setOfflineBackendForTests(backend);
    await backend.put('meta', makeKey(1, 'snap:activity:9'), { v: 1, data: { id: 9 } });
    await backend.put('meta', makeKey(1, 'snap:old'), { v: 0, data: { id: 1 } });
    await loadSnapshots(1);
    expect(readSnapshot('activity:9')).toEqual({ id: 9 });
    expect(readSnapshot('old')).toBeUndefined(); // outra versao do formato
    await purgeOfflineUser(1);
    await purgeOfflineUser(2);
    await loadSnapshots(2);
    expect(readSnapshot('activity:9')).toBeUndefined();
  });
});

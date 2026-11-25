import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());
const pollAsyncJob = vi.hoisted(() => vi.fn());
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), apiFetch }));
vi.mock('./async-job-poll', () => ({ pollAsyncJob }));

import {
  buildListQuery,
  buildSuggestBody,
  fetchHistory,
  fetchProfiles,
  payloadWithStatus,
  putProfile,
  suggestProfiles,
  type ResistanceProfile,
} from './resistance-profiles-api';

describe('cliente de perfis de resistência', () => {
  beforeEach(() => {
    apiFetch.mockReset();
    apiFetch.mockResolvedValue({});
    pollAsyncJob.mockReset();
  });

  it('monta a query só com filtros preenchidos', () => {
    expect(buildListQuery({})).toBe('');
    expect(buildListQuery({ status: 'x1', equipment_id: '3', pending: true, limit: 20, offset: 40 })).toBe(
      '?status=x1&equipment_id=3&pending=true&limit=20&offset=40',
    );
  });

  it('lista, histórico e PUT usam rota e método certos', async () => {
    await fetchProfiles({ pending: true });
    await fetchHistory(9);
    await putProfile(9, { flags: [], confidence: null, status: 's', rationale: null, evidence: null, reason: 'r' });
    expect(apiFetch).toHaveBeenNthCalledWith(1, '/resistance-profiles?pending=true');
    expect(apiFetch).toHaveBeenNthCalledWith(2, '/resistance-profiles/9/history');
    expect(apiFetch.mock.calls[2][0]).toBe('/resistance-profiles/9');
    expect(apiFetch.mock.calls[2][1].method).toBe('PUT');
  });

  it('corpo da sugestão: sem ids deixa a API escolher; com ids envia só os ids', () => {
    expect(buildSuggestBody(null)).toEqual({});
    expect(buildSuggestBody([])).toEqual({});
    expect(buildSuggestBody([4, 5])).toEqual({ equipment_exercise_ids: [4, 5] });
  });

  it('sugerir: sem job devolve null; com job acompanha o poll', async () => {
    apiFetch.mockResolvedValueOnce({ job_id: null, pairs: 0 });
    expect(await suggestProfiles(null)).toBeNull();
    expect(pollAsyncJob).not.toHaveBeenCalled();

    apiFetch.mockResolvedValueOnce({ job_ids: ['j1'], job_id: 'j1', pairs: 2, failed_batches: 0 });
    pollAsyncJob.mockResolvedValueOnce({ processed: 2, suggested: 1, skipped: 1, failed: [] });
    expect(await suggestProfiles([1, 2])).toEqual({
      processed: 2, suggested: 1, skipped: 1, failed: [], not_processed: 0, batches: 1, failed_batches: 0,
    });
    expect(pollAsyncJob.mock.calls[0][0]).toBe('j1');
  });

  it('sugerir: soma vários jobs, conta job que falha e lote não despachado, e reporta progresso', async () => {
    apiFetch.mockResolvedValueOnce({ job_ids: ['a', 'b', 'c'], job_id: 'a', pairs: 15, failed_batches: 1 });
    pollAsyncJob.mockImplementation(async (id: string) => {
      if (id === 'b') throw new Error('expirou');
      return id === 'a'
        ? { processed: 6, suggested: 4, skipped: 1, failed: [{ id: 9, code: 'x' }] }
        : { processed: 3, suggested: 1, skipped: 0, failed: [{ id: 10, code: 'not_processed' }] };
    });
    const progress: [number, number][] = [];
    const r = await suggestProfiles([1], (d, t) => progress.push([d, t]));
    expect(r).toEqual({
      processed: 9, suggested: 5, skipped: 1, failed: [{ id: 9, code: 'x' }, { id: 10, code: 'not_processed' }],
      not_processed: 1, batches: 4, failed_batches: 2,
    });
    expect(progress[0]).toEqual([1, 4]);
    expect(progress[progress.length - 1]).toEqual([4, 4]);
  });

  it('payload rápido preserva o perfil e troca só o estado', () => {
    const p = {
      source: 's1', mechanism: 'm1', trajectory: 't1', profile: 'p1', line: 'l1', direction_changes: 'd1',
      flags: ['f1'], confidence: 'c1', rationale: 'why', evidence: null,
    } as unknown as ResistanceProfile;
    expect(payloadWithStatus(p, 'novo', 'motivo')).toEqual({
      source: 's1', mechanism: 'm1', trajectory: 't1', profile: 'p1', line: 'l1', direction_changes: 'd1',
      flags: ['f1'], confidence: 'c1', status: 'novo', rationale: 'why', evidence: null, reason: 'motivo',
    });
  });
});

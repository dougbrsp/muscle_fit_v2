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

    apiFetch.mockResolvedValueOnce({ job_id: 'j1', pairs: 2 });
    pollAsyncJob.mockResolvedValueOnce({ processed: 2, suggested: 1, skipped: 1, failed: [] });
    expect(await suggestProfiles([1, 2])).toEqual({ processed: 2, suggested: 1, skipped: 1, failed: [] });
    expect(pollAsyncJob.mock.calls[0][0]).toBe('j1');
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

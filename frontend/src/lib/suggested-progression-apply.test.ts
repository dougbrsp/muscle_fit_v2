import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '@/lib/api';
import { fetchSuggestedProgressionPatch } from './suggested-progression-apply';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

const mocked = vi.mocked(apiFetch);

// Resposta de exemplo do servidor (a tela só repassa os valores prontos).
const base = {
  reps_target: '8-10',
  load_target: '60 kg',
  rpe_target: '8-9',
  progression_note: 'Mantenha',
  has_history: true,
  progression: 'same' as const,
};

describe('fetchSuggestedProgressionPatch', () => {
  beforeEach(() => mocked.mockReset());

  it('repassa o RPE por série da resposta', async () => {
    mocked.mockResolvedValueOnce({ ...base, rpe_targets_by_set: [8, 8.5, 9] });
    const patch = await fetchSuggestedProgressionPatch(42);
    expect(mocked).toHaveBeenCalledWith('/exercises/suggested-progression?mapping_id=42');
    expect(patch).toEqual({
      reps_target: '8-10',
      load_target: '60 kg',
      rpe_target: '8-9',
      rpe_targets_by_set: [8, 8.5, 9],
      progression_note: 'Mantenha',
      progression: 'same',
    });
  });

  it('API antiga sem a lista: devolve lista vazia (a do exercício anterior sai)', async () => {
    mocked.mockResolvedValueOnce(base);
    const patch = await fetchSuggestedProgressionPatch(7);
    expect(patch.rpe_targets_by_set).toEqual([]);
  });
});

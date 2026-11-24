import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), apiFetch }));

import { fetchRedundancyDiagnostic, previewRedundancy } from './exercise-redundancy-api';

describe('cliente da prévia de redundância', () => {
  beforeEach(() => {
    apiFetch.mockReset();
    apiFetch.mockResolvedValue({ items: [] });
  });

  it('POST só com itens e alvo (sem min_level); equipamento ausente é omitido', async () => {
    const ctrl = new AbortController();
    await previewRedundancy([{ mapping_id: 1, equipment_id: 5 }, { mapping_id: 2, equipment_id: null }], 1, ctrl.signal);
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe('/workout-plan-templates/redundancy-preview');
    expect(init.method).toBe('POST');
    expect(init.signal).toBe(ctrl.signal);
    expect(JSON.parse(init.body)).toEqual({
      items: [{ mapping_id: 1, equipment_id: 5 }, { mapping_id: 2 }],
      target_index: 1,
    });
  });

  it('diagnóstico: GET do Modelo, sem corpo nem filtro de nível', async () => {
    apiFetch.mockResolvedValue({ template_id: 7, slots: [] });
    const ctrl = new AbortController();
    const r = await fetchRedundancyDiagnostic(7, ctrl.signal);
    expect(apiFetch).toHaveBeenCalledWith('/workout-plan-templates/7/redundancy-diagnostic', { signal: ctrl.signal });
    expect(r.slots).toEqual([]);
  });
});

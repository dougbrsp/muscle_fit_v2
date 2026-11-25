import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { RedundancyDiagnostic } from '@/lib/exercise-redundancy-api';
import { theme } from '@/theme';
import { loadDiagnostic, RedundancyDiagnosticView, type DiagnosticState } from './RedundancyDiagnosticPanel';

const DATA: RedundancyDiagnostic = {
  template_id: 7,
  slots: [
    { slot_id: 1, sequence: 1, name: 'Peito', weekday: 1, pairs: [] },
    {
      slot_id: 2,
      sequence: 2,
      name: 'Costas',
      weekday: 3,
      pairs: [
        {
          a_item_id: 11,
          b_item_id: 12,
          a_mapping_id: 5,
          b_mapping_id: 6,
          a_exercise_name: 'Remada curvada',
          b_exercise_name: null,
          level: 'moderate',
          level_label: 'Moderada',
          confidence: 'medium',
          reasons: [{ code: 'r1', label: 'Mesmo padrão' }],
          matching: ['Padrão de movimento'],
          diverging: ['Equipamento'],
          limitations: [],
        },
      ],
    },
  ],
};

const render = (state: DiagnosticState) =>
  renderToStaticMarkup(createElement(MantineProvider, { theme }, createElement(RedundancyDiagnosticView, { state })));

describe('RedundancyDiagnosticView (SSR)', () => {
  it('mostra dia, nível, motivos, itens, Igual e Muda; omite dia sem pares', () => {
    const html = render({ status: 'ready', data: DATA });
    for (const t of ['Costas', 'Moderada', 'Mesmo padrão', 'Remada curvada', 'mapeamento 6', 'Igual', 'Muda', 'Equipamento']) {
      expect(html).toContain(t);
    }
    expect(html).not.toContain('Peito');
  });

  it('vazio explica a condição; erro e loading têm papel próprio', () => {
    expect(render({ status: 'ready', data: { template_id: 7, slots: [] } })).toContain('exercícios repetidos no mesmo dia aparecem sempre');
    expect(render({ status: 'error' })).toContain('Não foi possível carregar');
    expect(render({ status: 'loading' })).toContain('role="status"');
  });
});

describe('loadDiagnostic', () => {
  it('sucesso, erro e abort', async () => {
    const ok = vi.fn().mockResolvedValue(DATA);
    expect(await loadDiagnostic(ok, 7)).toEqual({ status: 'ready', data: DATA });
    expect(ok).toHaveBeenCalledWith(7, undefined);
    expect(await loadDiagnostic(vi.fn().mockRejectedValue(new Error('x')), 7)).toEqual({ status: 'error' });
    expect(await loadDiagnostic(vi.fn().mockRejectedValue(new DOMException('a', 'AbortError')), 7)).toBeNull();
  });
});

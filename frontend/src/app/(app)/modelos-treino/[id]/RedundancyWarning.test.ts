import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { RedundancyPair } from '@/lib/exercise-redundancy-api';
import { theme } from '@/theme';
import { findChangedTarget, loadRedundancy, RedundancyAlert } from './RedundancyWarning';

const PAIR: RedundancyPair = {
  a_index: 0,
  b_index: 1,
  a_mapping_id: 1,
  b_mapping_id: 2,
  version: 'v1',
  level: 'moderate',
  level_label: 'Moderada',
  confidence: 'medium',
  reasons: [{ code: 'r1', label: 'Mesmo padrão' }, { code: 'r2', label: 'Mesma articulação' }],
  matching: ['Padrão de movimento'],
  diverging: ['Equipamento'],
  limitations: [],
};

const render = () =>
  renderToStaticMarkup(
    createElement(
      MantineProvider,
      { theme },
      createElement(RedundancyAlert, { pair: PAIR, otherPosition: 2, onDismiss: () => {} }),
    ),
  );

describe('RedundancyAlert (SSR)', () => {
  it('mostra nível, motivos, item relacionado, o que muda e Manter assim', () => {
    const html = render();
    for (const t of ['Moderada', 'Mesmo padrão · Mesma articulação', 'exercício 2', 'Padrão de movimento', 'Equipamento', 'Manter assim']) {
      expect(html).toContain(t);
    }
  });
});

const it_ = (localId: string, mapping_id: number | null, equipment_id: number | null = null) => ({ localId, mapping_id, equipment_id });

describe('findChangedTarget', () => {
  it('detecta item novo/trocado e ignora o que não mudou ou sem exercício', () => {
    const prev = new Map([['a', '1:5']]);
    expect(findChangedTarget(prev, [it_('a', 1, 5), it_('b', null)]).targetIndex).toBeNull();
    expect(findChangedTarget(prev, [it_('a', 1, 5), it_('b', 2)]).targetIndex).toBe(1);
    expect(findChangedTarget(prev, [it_('a', 1, 6), it_('b', 2)]).targetIndex).toBe(0);
  });
});

describe('loadRedundancy', () => {
  const entries = [{ localId: 'a', mapping_id: 1 }, { localId: 'b', mapping_id: 2 }];

  it('devolve o primeiro par com a identidade do outro item', async () => {
    const fetchPreview = vi.fn().mockResolvedValue({ items: [PAIR] });
    const r = await loadRedundancy(fetchPreview, entries, 1);
    expect(fetchPreview).toHaveBeenCalledWith(entries, 1, undefined);
    expect(r).toEqual({ pair: PAIR, otherLocalId: 'a' });
  });

  it('sem pares (baixa/desconhecida), erro ou item único: sem aviso', async () => {
    expect(await loadRedundancy(vi.fn().mockResolvedValue({ items: [] }), entries, 1)).toBeNull();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(await loadRedundancy(vi.fn().mockRejectedValue(new Error('x')), entries, 1)).toBeNull();
    expect(warn).toHaveBeenCalledTimes(1);
    expect(await loadRedundancy(vi.fn().mockRejectedValue(new DOMException('a', 'AbortError')), entries, 1)).toBeNull();
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
    const never = vi.fn();
    expect(await loadRedundancy(never, entries.slice(0, 1), 0)).toBeNull();
    expect(never).not.toHaveBeenCalled();
  });
});

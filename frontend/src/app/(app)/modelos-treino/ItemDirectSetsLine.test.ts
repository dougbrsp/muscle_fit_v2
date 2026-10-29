import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { buildItemDirectSetsShareLines } from '@/lib/methodology-card-share';
import { cardShare, FIXTURE_CARD_SHARE_EVALUATION } from '@/lib/methodology-volume-fixtures';
import { theme } from '@/theme';
import { ItemDirectSetsLine } from './ItemDirectSetsLine';

/**
 * M43 — render mínimo (SSR, sem jsdom) da linha de participação por séries
 * diretas do card do Modo B: texto exato, ícone com `aria-label`, cinza para
 * abaixo da faixa e ausência de ícone em `NOT_EVALUABLE`.
 */

function render(props: Parameters<typeof ItemDirectSetsLine>[0]): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, createElement(ItemDirectSetsLine, props)));
}

// Lote 3: a fatia do card vem do payload (`item_shares`); aqui 3 séries por bloco.
function linesFor(slotSequence: number, primaryGroups: Array<{ muscleGroupId: number | null; muscleGroup: string }>) {
  return buildItemDirectSetsShareLines({
    volumeMode: 'equipment_series',
    slotSequence,
    share: cardShare(
      slotSequence,
      primaryGroups.map((group) => ({ id: group.muscleGroupId ?? 0, name: group.muscleGroup, sets: 3 })),
    ),
    evaluation: FIXTURE_CARD_SHARE_EVALUATION,
  });
}

describe('ItemDirectSetsLine (SSR)', () => {
  it('escreve "Quadríceps: 3/9/12" com ícone verde e aria-label', () => {
    const html = render({ lines: linesFor(1, [{ muscleGroupId: 4, muscleGroup: 'Quadríceps' }]) });
    expect(html).toContain('Quadríceps: 3/9/12');
    expect(html).toContain('aria-label="Quadríceps: Na faixa"');
    expect(html).toContain('tabler-icon-circle-check');
    expect(html).toContain('data-status="WITHIN_RANGE"');
    expect(html).toContain('--mantine-color-green-6');
    expect(html).toContain('3 neste exercício / 9 neste dia / 12 no Modelo (ciclo)');
  });

  it('abaixo e acima da faixa são laranja e se distinguem pelo ícone (2026-10-02)', () => {
    const html = render({
      lines: linesFor(2, [
        { muscleGroupId: 5, muscleGroup: 'Bíceps' },
        { muscleGroupId: 7, muscleGroup: 'Ombros' },
      ]),
    });
    expect(html).toContain('Bíceps: 3/3/3');
    expect(html).toContain('aria-label="Bíceps: Abaixo da faixa, eixos em conflito"');
    expect(html).toContain('tabler-icon-trending-down');
    expect(html).not.toContain('--mantine-color-gray-6');
    expect(html).not.toContain('--mantine-color-red-');
    expect(html).toContain('Ombros: 3/10/10');
    expect(html).toContain('aria-label="Ombros: Acima da faixa (esperado)"');
    expect(html).toContain('--mantine-color-orange-6');
    expect(html).toContain('tabler-icon-trending-up');
  });

  it('NOT_EVALUABLE sai sem ícone e com o motivo no title', () => {
    const html = render({ lines: linesFor(3, [{ muscleGroupId: 9, muscleGroup: 'Trapézio' }]) });
    expect(html).toContain('Trapézio: 3/4/4');
    expect(html).toContain('(sem status)');
    expect(html).not.toContain('tabler-icon');
    expect(html).toContain('grupo sem macro-grupo');
  });

  it('sem linhas não renderiza nada; carregando mostra placeholder discreto', () => {
    // O MantineProvider emite um <style> mesmo com filho nulo: afirma pela
    // ausência do conteúdo da linha, não pela string vazia.
    const empty = render({ lines: [] });
    expect(empty).not.toContain('Séries diretas');
    expect(empty).not.toContain('data-testid="item-direct-sets');
    expect(render({ lines: [], pending: true })).toContain('Séries diretas: calculando…');
  });

  it('Fase 5: técnica que soma equivalente aparece ao lado do rótulo; sem técnica, nada muda', () => {
    const lines = linesFor(1, [{ muscleGroupId: 4, muscleGroup: 'Quadríceps' }]);
    const html = render({ lines, techniqueNote: '4 séries + 0,8 equiv. (rest-pause ×2)' });
    expect(html).toContain('data-testid="item-direct-sets-technique"');
    expect(html).toContain('4 séries + 0,8 equiv. (rest-pause ×2)');
    expect(render({ lines })).not.toContain('item-direct-sets-technique');
  });
});

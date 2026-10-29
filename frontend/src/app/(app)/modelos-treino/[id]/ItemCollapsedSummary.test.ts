import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ItemActivationSummary } from '@/lib/item-activation-summary';
import { BRAND, theme } from '@/theme';
import { priorFatigueColor } from '@/lib/prior-fatigue';
import type { PriorFatigue } from '@/lib/workout-plan-templates-types';
import { ItemCollapsedSummary, itemCardBackground, itemCardStyle, PriorFatigueStripe } from './ItemCollapsedSummary';

/**
 * Card de item recolhido do editor de Modelo — render mínimo (SSR, sem jsdom),
 * no padrão de `ItemDirectSetsLine.test.ts`.
 */

function render(props: Parameters<typeof ItemCollapsedSummary>[0]): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, createElement(ItemCollapsedSummary, props)));
}

const ACTIVATION: ItemActivationSummary = {
  primary: [{ muscleGroupId: 1, muscleGroup: 'Peitoral', value: 3, total: 9, text: 'Peitoral: 3,0/9,0' }],
  secondary: [
    { muscleGroupId: 2, muscleGroup: 'Tríceps', value: 1.5, total: 6, text: 'Tríceps: 1,5/6,0' },
    { muscleGroupId: 3, muscleGroup: 'Ombros', value: 1, total: 8, text: 'Ombros: 1,0/8,0' },
  ],
  stabilizer: [],
};

const BASE = {
  itemIndex: 2,
  title: 'Peitoral',
  detail: 'Supino reto articulado',
  activation: ACTIVATION,
  onExpand: () => {},
};

describe('ItemCollapsedSummary (SSR)', () => {
  it('mostra alça, número 1-based, grupo, equipamento e o chevron de expandir', () => {
    const html = render({ ...BASE, photo: { url: 'https://cdn.example/foto.png', alt: 'Supino', onOpen: () => {} } });
    expect(html).toContain('aria-label="Arrastar para reordenar"');
    expect(html).toContain('aria-label="Exercício número 3 do slot"');
    expect(html).toMatch(/data-testid="item-collapsed-title"[^>]*>Peitoral</);
    expect(html).toMatch(/data-testid="item-collapsed-detail"[^>]*>Supino reto articulado</);
    expect(html).toContain('aria-label="Expandir card"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('aria-label="Ampliar foto do equipamento"');
    expect(html).toContain('src="https://cdn.example/foto.png"');
    expect(html).toContain('width="36"');
  });

  it('séries equivalentes: só o grupo principal no recolhido', () => {
    const html = render(BASE);
    const roles = [...html.matchAll(/data-role="(\w+)"/g)].map((m) => m[1]);
    expect(roles).toEqual(['primary']);
    expect(html).toContain('Principal: Peitoral: 3,0/9,0');
    expect(html).not.toContain('Secundários');
    expect(html).not.toContain('Estabilizadores');
    expect(html).toContain('aria-label="Ativação deste item: Principal: Peitoral: 3,0/9,0"');
  });

  it('mostra o nome do exercício além do equipamento', () => {
    const html = render({ ...BASE, detail: 'Máquina articulada', exercise: 'Supino reto' });
    expect(html).toMatch(/data-testid="item-collapsed-exercise"[^>]*>Supino reto</);
    expect(html).toMatch(/data-testid="item-collapsed-detail"[^>]*>Máquina articulada</);
  });

  it('técnica avançada, aquecimento e ajuste só quando houver', () => {
    const none = render({ ...BASE, techniqueLabel: null, warmupSets: null, feelerSets: 0 });
    expect(none).not.toContain('item-collapsed-extras');

    const html = render({ ...BASE, techniqueLabel: 'Drop-set', warmupSets: 1, feelerSets: 2 });
    expect(html).toMatch(/data-testid="item-collapsed-technique"[^>]*>.*Drop-set/);
    expect(html).toContain('>Aquecimento: 1<');
    expect(html).toContain('>Ajuste: 2<');

    const onlyWarmup = render({ ...BASE, warmupSets: 2 });
    expect(onlyWarmup).toContain('item-collapsed-warmup');
    expect(onlyWarmup).not.toContain('item-collapsed-technique');
    expect(onlyWarmup).not.toContain('item-collapsed-feeler');
  });

  it('medida fora de reps aparece como badge; sem ela (reps) nada muda', () => {
    expect(render({ ...BASE, measureText: null })).not.toContain('item-collapsed-measure');
    const html = render({ ...BASE, measureText: '3 × 60 s' });
    expect(html).toMatch(/data-testid="item-collapsed-measure"[^>]*>.*3 × 60 s/);
  });

  it('sem ativação e sem equipamento: nada de linhas por papel nem foto', () => {
    const html = render({ ...BASE, activation: null, photo: null, detail: 'Equipamento não escolhido', detailMuted: true });
    expect(html).not.toContain('data-role=');
    expect(html).not.toContain('Ampliar foto do equipamento');
    expect(html).toContain('Equipamento não escolhido');
  });
});

describe('fundo alternado dos cards', () => {
  it('alterna pelo índice entre bgElevated e bgSurface, com a borda da marca', () => {
    expect([0, 1, 2, 3].map(itemCardBackground)).toEqual([
      BRAND.bgElevated,
      BRAND.bgSurface,
      BRAND.bgElevated,
      BRAND.bgSurface,
    ]);
    expect(itemCardStyle(1)).toEqual({ borderColor: BRAND.border, background: BRAND.bgSurface, position: 'relative' });
  });
});

describe('PriorFatigueStripe (SSR)', () => {
  const PRIOR: PriorFatigue = {
    muscle_group_id: 2,
    muscle_group_name: 'Tríceps',
    prior_sets: 4,
    level: 0.44,
    band: 'pre_fatigued',
    band_label: 'pré-fatigado',
  };

  function renderStripe(priorFatigue: PriorFatigue | null | undefined): string {
    return renderToStaticMarkup(
      createElement(MantineProvider, { theme }, createElement(PriorFatigueStripe, { priorFatigue })),
    );
  }

  it('desenha a faixa com a cor do nível e diz a faixa em texto (não só cor)', () => {
    const html = renderStripe(PRIOR);
    expect(html).toContain('data-testid="prior-fatigue-stripe"');
    expect(html).toContain('data-band="pre_fatigued"');
    expect(html).toContain('role="img"');
    expect(html).toContain('aria-label="Fadiga prévia no Tríceps: pré-fatigado"');
    expect(html).toContain(`background:${priorFatigueColor(PRIOR.level)}`);
  });

  it('sem fadiga prévia não desenha nada', () => {
    expect(renderStripe(null)).not.toContain('prior-fatigue-stripe');
    expect(renderStripe(undefined)).not.toContain('prior-fatigue-stripe');
  });
});

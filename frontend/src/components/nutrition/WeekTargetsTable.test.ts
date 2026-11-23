import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { NutritionDayTarget } from '@/lib/nutrition-types';
import { theme } from '@/theme';
import { WeekTargetsTable } from './WeekTargetsTable';

// Valores de exemplo da resposta da API: a tabela só exibe, não calcula.
function meta(sobre: Record<string, unknown>): NutritionDayTarget {
  return {
    kcal: 2400, protein_g: 160, carbs_g: 250, fat_g: 70, fiber_g: 30,
    training_known: true, is_training_day: true, training_kcal: 400, training_kcal_nature: 'reported',
    frozen: false, limitations: [], day_kind: 'normal', carb_cycle_policy: 'fat_compensation',
    ...sobre,
  } as unknown as NutritionDayTarget;
}

const rows = [
  { local_date: '2026-11-16', target: meta({ day_kind: 'high', carbs_g: 330, fat_g: 55 }) },
  { local_date: '2026-11-17', target: meta({}) },
  { local_date: '2026-11-18', target: meta({ day_kind: 'low', carbs_g: 120, fat_g: 95, is_training_day: false, training_kcal: null }) },
  { local_date: '2026-11-19', target: null },
];

const html = () =>
  renderToStaticMarkup(createElement(MantineProvider, { theme }, createElement(WeekTargetsTable, { rows, today: '2026-11-17' })))
    .replace(/ /g, ' ');

describe('WeekTargetsTable', () => {
  it('mostra dia alto, normal e baixo com o rótulo do tipo e da política vindos da API', () => {
    const markup = html();
    expect(markup).toContain('Dia alto em carbo · Compensar com gordura');
    expect(markup).toContain('Dia normal · Compensar com gordura');
    expect(markup).toContain('Dia baixo em carbo · Compensar com gordura');
  });

  it('exibe os gramas que a API mandou, inclusive a gordura maior do dia baixo', () => {
    const markup = html();
    expect(markup).toMatch(/330[^<]*g/);
    expect(markup).toMatch(/120[^<]*g/);
    expect(markup).toMatch(/95[^<]*g/);
  });

  it('marca hoje, mostra treino/sem treino e dia sem meta', () => {
    const markup = html();
    expect(markup.match(/hoje/g)?.length).toBe(1);
    expect(markup).toContain('Sem treino');
    expect(markup).toContain('Sem meta para este dia.');
  });
});

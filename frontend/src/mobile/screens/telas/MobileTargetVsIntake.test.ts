import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { BalanceRow, NutritionDay } from '@/lib/nutrition-types';
import { theme } from '@/theme';
import { MobileTargetVsIntake } from './MobileTargetVsIntake';

function html(el: ReturnType<typeof createElement>): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, el));
}

function linha(field: string, extra: Partial<BalanceRow> = {}): BalanceRow {
  return {
    field, target: 2000, intake: 1500, intake_complete: true, remaining: 500, share: 0.75,
    within_target: 1500, to_consume: 500, excess: 0, ...extra,
  };
}

function dia(rows: BalanceRow[]): NutritionDay {
  return {
    target: { kcal: 2000, frozen: false, backfilled: false, limitations: [] },
    target_notices: [],
    balance: { rows, tdee_kcal: null, energy_balance_kcal: null, limitations: [] },
  } as unknown as NutritionDay;
}

const QUATRO = ['energy_kcal', 'protein_g', 'carbohydrates_g', 'total_fat_g'];

describe('MobileTargetVsIntake: consumido e restante das 4 linhas', () => {
  it('mostra consumido, meta e restante de energia e dos 3 macros, com barra', () => {
    const markup = html(createElement(MobileTargetVsIntake, { day: dia(QUATRO.map((f) => linha(f))) }));
    expect(markup.match(/Consumido:/g)?.length).toBe(4);
    expect(markup.match(/Restante:/g)?.length).toBe(4);
    expect(markup.match(/role="progressbar"/g)?.length).toBe(4);
  });

  it('excesso aparece em texto, não só pela cor', () => {
    const markup = html(
      createElement(MobileTargetVsIntake, {
        day: dia([linha('energy_kcal', { intake: 2300, within_target: 2000, to_consume: 0, excess: 300, share: 1.15 })]),
      }),
    );
    expect(markup).toMatch(/Acima da meta: [^<]*300/);
    expect(markup).not.toContain('Restante:');
  });

  it('sem meta só o consumido', () => {
    const markup = html(
      createElement(MobileTargetVsIntake, {
        day: dia([linha('energy_kcal', { target: null, share: null, within_target: null, to_consume: null, excess: null })]),
      }),
    );
    expect(markup).toMatch(/Consumido:[^]*1[. ]?500 kcal/);
    expect(markup).toContain('Sem meta para este dia');
    expect(markup).not.toContain('Restante:');
    expect(markup).not.toContain('progressbar');
  });

  it('sem refeição: consumido zero e restante igual à meta', () => {
    const markup = html(
      createElement(MobileTargetVsIntake, {
        day: dia([linha('energy_kcal', { intake: null, share: null, within_target: 0, to_consume: 2000, excess: 0 })]),
      }),
    );
    expect(markup).toMatch(/Consumido:[^]*0 kcal/);
    expect(markup).toMatch(/Restante: 2[. ]?000 kcal/);
  });

  it('fibra fica fora do gráfico (linha de lista, sem barra)', () => {
    const markup = html(createElement(MobileTargetVsIntake, { day: dia([linha('energy_kcal'), linha('fiber_g', { target: 25, intake: 10, to_consume: 15 })]) }));
    expect(markup.match(/role="progressbar"/g)?.length).toBe(1);
  });

  it('excesso mantém a cor da marca (a tela não julga passar da meta, como no desktop) e o texto do excesso', () => {
    const markup = html(
      createElement(MobileTargetVsIntake, {
        day: dia([linha('energy_kcal', { intake: 2300, within_target: 2000, to_consume: 0, excess: 300, share: 1.15 })]),
      }),
    );
    expect(markup).not.toContain('--mantine-color-red');
    expect(markup).toMatch(/Acima da meta: [^<]*300/);
    expect(markup).toContain('role="progressbar"');
  });

  it('total incompleto (partes do motor nulas): texto do desktop em vez de barra em 0%', () => {
    const markup = html(
      createElement(MobileTargetVsIntake, {
        day: dia([linha('energy_kcal', { intake: null, intake_complete: false, share: null, within_target: null, to_consume: null, excess: null })]),
      }),
    );
    expect(markup).toContain('Sem gráfico: falta meta ou o total do dia está incompleto.');
    expect(markup).not.toContain('progressbar');
  });

  it('sem meta: o mesmo texto de sem gráfico, sem barra', () => {
    const markup = html(
      createElement(MobileTargetVsIntake, {
        day: dia([linha('energy_kcal', { target: null, share: null, within_target: null, to_consume: null, excess: null })]),
      }),
    );
    expect(markup).toContain('Sem gráfico: falta meta ou o total do dia está incompleto.');
    expect(markup).not.toContain('progressbar');
  });
});

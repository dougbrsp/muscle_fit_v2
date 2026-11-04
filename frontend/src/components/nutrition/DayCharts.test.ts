import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { BalanceRow, NutritionDay, NutritionWeek } from '@/lib/nutrition-types';
import { theme } from '@/theme';
import { TargetVsIntakeCard } from './TargetVsIntakeCard';
import { WeekStrip } from './WeekStrip';

// Rosca do dia e barras da semana por SSR (sem jsdom): os gráficos carregam sob demanda, então o
// teste confere os números em texto e os estados, que são o que a tela promete.
function html(el: ReturnType<typeof createElement>): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, el));
}

function linha(field: string, extra: Partial<BalanceRow> = {}): BalanceRow {
  return {
    field,
    target: 2000,
    intake: 1500,
    intake_complete: true,
    remaining: 500,
    share: 0.75,
    within_target: 1500,
    to_consume: 500,
    excess: 0,
    ...extra,
  };
}

function dia(rows: BalanceRow[]): NutritionDay {
  return {
    target: { kcal: 2000, frozen: false, backfilled: false, limitations: [] },
    target_notices: [],
    balance: { rows, tdee_kcal: 2500, energy_balance_kcal: null, limitations: [] },
  } as unknown as NutritionDay;
}

describe('TargetVsIntakeCard', () => {
  it('mostra os quatro macros com consumido, meta e restante, e a legenda do gráfico', () => {
    const markup = html(
      createElement(TargetVsIntakeCard, {
        day: dia([linha('energy_kcal'), linha('protein_g', { target: 150, intake: 100, within_target: 100, to_consume: 50 }), linha('carbohydrates_g'), linha('total_fat_g')]),
      }),
    );
    expect(markup.match(/Restante:/g)?.length).toBe(4);
    for (const trecho of ['Consumido:', 'Meta:', 'Consumido', 'Restante', 'Acima da meta']) expect(markup).toContain(trecho);
  });

  it('passou da meta mostra "Acima da meta" em vez de restante', () => {
    const markup = html(
      createElement(TargetVsIntakeCard, {
        day: dia([linha('energy_kcal', { intake: 2300, within_target: 2000, to_consume: 0, excess: 300, remaining: -300, share: 1.15 })]),
      }),
    );
    expect(markup).not.toContain('Restante:');
    expect(markup).toMatch(/Acima da meta: <b>[^<]*300/);
  });

  it('com meta e sem refeição mostra 0 consumido e a meta toda como restante (e o gráfico existe)', () => {
    const markup = html(
      createElement(TargetVsIntakeCard, {
        day: dia([linha('energy_kcal', { intake: null, share: null, remaining: null, within_target: 0, to_consume: 2000, excess: 0 })]),
      }),
    );
    expect(markup).toMatch(/Consumido: <b>0 kcal/);
    expect(markup).toMatch(/Restante: <b>2[. ]?000 kcal/);
    expect(markup).not.toContain('Sem gráfico');
  });

  it('sem meta mostra só o consumido, sem meta nem restante', () => {
    const markup = html(
      createElement(TargetVsIntakeCard, {
        day: dia([linha('energy_kcal', { target: null, share: null, remaining: null, within_target: null, to_consume: null, excess: null })]),
      }),
    );
    expect(markup).toMatch(/Consumido: <b>1[. ]?500 kcal/);
    expect(markup).toContain('Sem meta para este dia');
    expect(markup).not.toContain('Meta:');
    expect(markup).not.toContain('Restante:');
  });

  it('ingestão incompleta continua sem gráfico e diz o motivo', () => {
    const markup = html(
      createElement(TargetVsIntakeCard, {
        day: dia([linha('energy_kcal', { intake_complete: false, within_target: null, to_consume: null, excess: null, remaining: null })]),
      }),
    );
    expect(markup).toContain('Sem gráfico');
  });
});

function semana(dias: Array<Partial<NutritionWeek['days'][number]>>): NutritionWeek {
  return {
    today: '2026-10-14',
    start: '2026-10-12',
    end: '2026-10-18',
    days: dias.map((d, i) => ({
      local_date: `2026-10-${12 + i}`,
      target: null,
      totals: null,
      intake_kcal: null,
      logged: false,
      intake_incomplete: false,
      ...d,
    })),
    report: {
      budget: { kcal: 14000 },
      adherence: { kcal_share: 0.9, kcal_within_tolerance: false, protein_share: 1, protein_ok: true, logged_days: 3, valid_week: false },
      bank: { balance_kcal: 0, carryover_kcal: 0 },
      limitations: [],
    },
  } as unknown as NutritionWeek;
}

describe('WeekStrip', () => {
  it('com refeições ou metas mostra o título do gráfico da semana', () => {
    const markup = html(
      createElement(WeekStrip, {
        week: semana([{ target: { kcal: 2000 } as never, intake_kcal: 1800, logged: true }]),
        selected: null,
        onSelect: vi.fn(),
        onWeekChange: vi.fn(),
      }),
    );
    expect(markup).toContain('Energia da semana');
    expect(markup).not.toContain('Sem refeições nem metas nesta semana');
    expect(markup).toContain('registrado');
  });

  it('semana sem nenhum dado não desenha gráfico vazio e explica', () => {
    const markup = html(
      createElement(WeekStrip, { week: semana([{}, {}]), selected: null, onSelect: vi.fn(), onWeekChange: vi.fn() }),
    );
    expect(markup).toContain('Sem refeições nem metas nesta semana');
    expect(markup).not.toContain('role="img"');
  });

  it('dia incompleto aparece marcado e a semana aparece sem a marca de válida', () => {
    const markup = html(
      createElement(WeekStrip, {
        week: semana([{ intake_incomplete: true, target: { kcal: 2000 } as never }]),
        selected: null,
        onSelect: vi.fn(),
        onWeekChange: vi.fn(),
      }),
    );
    expect(markup).toContain('incompleto');
    expect(markup).not.toContain('semana válida');
  });
});

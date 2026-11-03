import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { NutritionToday } from '@/lib/nutrition-dashboard';
import type { BalanceRow } from '@/lib/nutrition-types';
import { theme } from '@/theme';
import { NutritionTodayCard, NutritionTodayView, todayCardState, type TodayCardState } from './NutritionTodayCard';

// Card da home mobile por SSR (sem jsdom), um teste por estado.
function envolver(el: ReturnType<typeof createElement>): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, el));
}

function linha(field: string, extra: Partial<BalanceRow> = {}): BalanceRow {
  return {
    field,
    target: 2000,
    intake: 500,
    intake_complete: true,
    remaining: 1500,
    share: 0.25,
    within_target: 500,
    to_consume: 1500,
    excess: 0,
    ...extra,
  };
}

const HOJE = {
  target: { kcal: 2000 },
  balance: {
    rows: [
      linha('fiber_g'),
      linha('energy_kcal'),
      linha('protein_g', { target: 150, intake: 200, within_target: 150, to_consume: 0, excess: 50, remaining: -50 }),
      linha('carbohydrates_g'),
      linha('total_fat_g'),
    ],
  },
} as unknown as NutritionToday;

function view(state: TodayCardState, today?: NutritionToday, checkinSlot?: ReturnType<typeof createElement>): string {
  return envolver(createElement(NutritionTodayView, { state, today, checkinSlot }));
}

describe('todayCardState', () => {
  it('pausada sem dado é offline (não erro); erro, sem meta e pronto saem da consulta', () => {
    expect(todayCardState({ data: undefined, isError: false, fetchStatus: 'paused' })).toBe('offline');
    expect(todayCardState({ data: undefined, isError: true, fetchStatus: 'idle' })).toBe('error');
    expect(todayCardState({ data: undefined, isError: false, fetchStatus: 'fetching' })).toBe('loading');
    expect(todayCardState({ data: { ...HOJE, target: null } as NutritionToday, isError: false, fetchStatus: 'idle' })).toBe('no_target');
    expect(todayCardState({ data: HOJE, isError: false, fetchStatus: 'idle' })).toBe('ready');
    // dado em cache continua valendo mesmo se a rede cair
    expect(todayCardState({ data: HOJE, isError: false, fetchStatus: 'paused' })).toBe('ready');
  });
});

describe('NutritionTodayView', () => {
  it('com macros mostra energia, proteína, carboidrato e gordura (consumido, meta e falta/acima), sem a fibra', () => {
    const markup = view('ready', HOJE);
    expect(markup).toContain('Nutrição de hoje');
    expect(markup).toContain('falta');
    expect(markup).toContain('acima');
    expect(markup).not.toContain('aria-busy');
    expect(markup.match(/falta|acima/g)?.length).toBe(4);
    expect(markup).toContain('href="/nutricao"');
  });

  it.each([
    ['loading', 'aria-busy="true"'],
    ['offline', 'Sem conexão'],
    ['error', 'Não foi possível carregar'],
    ['no_target', 'Sem meta para hoje'],
  ] as const)('estado %s', (estado, trecho) => {
    const markup = view(estado);
    expect(markup).toContain(trecho);
    expect(markup).toContain('Registrar refeição'); // o atalho existe em todo estado
  });

  it('renderiza o ponto de extensão do check-in acima dos macros e do atalho', () => {
    const markup = view('ready', HOJE, createElement('div', { 'data-testid': 'checkin-n7' }, 'Check-in'));
    expect(markup.indexOf('checkin-n7')).toBeGreaterThan(-1);
    expect(markup.indexOf('checkin-n7')).toBeLessThan(markup.indexOf('falta'));
    expect(markup.indexOf('checkin-n7')).toBeLessThan(markup.indexOf('Registrar refeição'));
  });
});

describe('NutritionTodayCard', () => {
  it('no primeiro render, antes da resposta, mostra a carga e o atalho', () => {
    const client = new QueryClient();
    const markup = envolver(createElement(QueryClientProvider, { client }, createElement(NutritionTodayCard)));
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain('Registrar refeição');
  });
});

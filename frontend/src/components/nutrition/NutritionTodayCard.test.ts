import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { theme } from '@/theme';
import { NutritionTodayCard } from './NutritionTodayCard';

// Card da home mobile por SSR (sem jsdom): no primeiro render a consulta ainda não voltou.
function html(props: Parameters<typeof NutritionTodayCard>[0] = {}): string {
  const client = new QueryClient();
  return renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client },
      createElement(MantineProvider, { theme }, createElement(NutritionTodayCard, props)),
    ),
  );
}

describe('NutritionTodayCard', () => {
  it('mostra o título, o estado de carga e o atalho para registrar a refeição', () => {
    const markup = html();
    expect(markup).toContain('Nutrição de hoje');
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain('href="/nutricao"');
    expect(markup).toContain('Registrar refeição');
  });

  it('renderiza o ponto de extensão do check-in acima dos macros', () => {
    const markup = html({ checkinSlot: createElement('div', { 'data-testid': 'checkin-n7' }, 'Check-in') });
    expect(markup).toContain('data-testid="checkin-n7"');
    expect(markup.indexOf('checkin-n7')).toBeLessThan(markup.indexOf('Registrar refeição'));
  });
});

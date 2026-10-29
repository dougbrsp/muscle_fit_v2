import { MantineProvider } from '@mantine/core';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { theme } from '@/theme';
import { DashboardRelatoriosView, type DashboardRelatoriosViewProps } from './DashboardRelatoriosClient';

/** `/dashboard/relatorios` renderizada via `react-dom/server` (sem jsdom). */

function render(element: ReactElement): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, element));
}

const noop = () => {};

function props(overrides: Partial<DashboardRelatoriosViewProps> = {}): DashboardRelatoriosViewProps {
  return {
    period: '90d',
    dateFrom: '',
    dateTo: '',
    downloading: false,
    error: null,
    rangeError: null,
    onPeriodChange: noop,
    onDateFromChange: noop,
    onDateToChange: noop,
    onDownload: noop,
    ...overrides,
  };
}

describe('<DashboardRelatoriosView />', () => {
  it('explica o conteúdo e oferece o botão de download', () => {
    const html = render(createElement(DashboardRelatoriosView, props()));
    expect(html).toContain('Relatórios');
    expect(html).toContain('O que vem no relatório');
    expect(html).toContain('Treino por grupo muscular');
    expect(html).toContain('Baixar relatório (.md)');
    expect(html).not.toContain('Data inicial');
  });

  it('personalizado mostra as datas e bloqueia o botão com período inválido', () => {
    const html = render(
      createElement(DashboardRelatoriosView, props({ period: 'custom', rangeError: 'O período deve ter entre 7 e 180 dias (selecionado: 3).' })),
    );
    expect(html).toContain('Data inicial');
    expect(html).toContain('Data final');
    expect(html).toContain('selecionado: 3');
    expect(html).toMatch(/<button[^>]*disabled[^>]*>/);
  });

  it('estados de carregando e de erro', () => {
    const carregando = render(createElement(DashboardRelatoriosView, props({ downloading: true })));
    expect(carregando).toContain('Gerando o relatório');

    const erro = render(createElement(DashboardRelatoriosView, props({ error: 'Erro ao consultar (HTTP 503).' })));
    expect(erro).toContain('Não foi possível gerar o relatório.');
    expect(erro).toContain('HTTP 503');
  });
});

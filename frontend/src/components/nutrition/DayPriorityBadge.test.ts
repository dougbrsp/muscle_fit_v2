import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { theme } from '@/theme';
import { DayHeader } from './DayHeader';
import { DayPriorityBadge } from './DayPriorityBadge';

function html(el: ReturnType<typeof createElement>): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, el));
}

const ROTULO = 'Priorizar carboidrato';
const EXPLICACAO = 'Hoje o plano concentra mais carboidratos.';
const alvo = { nutrition_priority: 'carb', nutrition_priority_label: ROTULO, nutrition_priority_explanation: EXPLICACAO };

describe('DayPriorityBadge', () => {
  it('com rótulo e explicação: texto visível, focável e rótulo acessível com a explicação', () => {
    const markup = html(createElement(DayPriorityBadge, { target: alvo }));
    expect(markup).toContain(ROTULO);
    expect(markup).toContain('tabindex="0"');
    expect(markup).toContain(`aria-label="${ROTULO}. ${EXPLICACAO}"`);
  });

  it('sem rótulo (null, ausente ou indisponível): nada', () => {
    expect(html(createElement(DayPriorityBadge, { target: null }))).not.toContain('day-priority-badge');
    expect(html(createElement(DayPriorityBadge, { target: {} }))).not.toContain('day-priority-badge');
    const indisponivel = { nutrition_priority: 'unavailable', nutrition_priority_label: null, nutrition_priority_explanation: null };
    expect(html(createElement(DayPriorityBadge, { target: indisponivel }))).not.toContain('day-priority-badge');
  });

  it('sem explicação: só o selo, sem foco nem rótulo de tooltip', () => {
    const markup = html(createElement(DayPriorityBadge, { target: { ...alvo, nutrition_priority_explanation: null } }));
    expect(markup).toContain(ROTULO);
    expect(markup).not.toContain('tabindex');
    expect(markup).not.toContain('aria-label');
  });
});

describe('DayHeader com a prioridade', () => {
  const base = { date: '2026-11-22', today: '2026-11-22', onDateChange: () => {} };
  it('com a prop mostra o selo; sem ela fica como antes', () => {
    expect(html(createElement(DayHeader, { ...base, priority: alvo as never }))).toContain(ROTULO);
    expect(html(createElement(DayHeader, base))).not.toContain('day-priority-badge');
  });
});

import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { CardioRecommendationView } from '@/lib/analytics-types';
import { theme } from '@/theme';
import { StructuredRecommendation } from './CardioRecommendationPanel';

function render(element: ReactElement): string {
  const client = new QueryClient();
  return renderToStaticMarkup(
    createElement(QueryClientProvider, { client }, createElement(MantineProvider, { theme }, element)),
  ).replace(/<style[^>]*>[\s\S]*?<\/style>/g, '');
}

// Forma de `recommendation_view` (backend `cardio_recommendation_v3.build_view`).
const VIEW: CardioRecommendationView = {
  summary: 'TSB baixo: base aeróbica leve.',
  objective: 'Manter o volume aeróbico sem atrapalhar a perna.',
  confidence_label: 'Médio',
  confidence_reason: 'poucos dados de HRV',
  plan: { template_id: 36, template_name: 'ABC', template_version_id: 31 },
  can_apply: true,
  sessions: [
    {
      date: '2026-10-28',
      date_label: '28/10 (qua)',
      prescription_text: '5 min aquec. + 30 min Z2 + 5 min recuperação',
      prescription_short: 'Normal 30 min Z2 · 5+5 min',
      total_minutes_label: '40 min',
      rationale: 'base aeróbica',
      signals: ['TSB'],
      adjustment_labels: ['HIIT vetado, trocado por Normal'],
      slot_name: 'Lower - TER',
      plan_action: 'replace',
      applicable: true,
    },
    {
      date: '2026-10-30',
      date_label: '30/10 (sex)',
      prescription_text: '20 min Z2',
      prescription_short: 'Normal 20 min Z2',
      total_minutes_label: '20 min',
      rationale: null,
      signals: [],
      adjustment_labels: [],
      slot_name: null,
      plan_action: null,
      applicable: false,
    },
  ],
  notes: [
    {
      date: '2026-10-28',
      date_label: '28/10 (qua)',
      action: 'adjusted',
      code: 'hiit_vetoed',
      label: 'HIIT vetado, trocado por Normal',
      detail_label: 'véspera de perna',
    },
    {
      date: '2026-11-01',
      date_label: '01/11 (dom)',
      action: 'rejected',
      code: 'weekly_minutes_cap',
      label: 'teto semanal de minutos',
      detail_label: null,
    },
  ],
};

describe('StructuredRecommendation', () => {
  it('mostra sessões, ajustes e o botão só nas sessões aplicáveis', () => {
    const html = render(createElement(StructuredRecommendation, { recommendationId: 5, view: VIEW }));
    expect(html).toContain('28/10 (qua)');
    expect(html).toContain('5 min aquec. + 30 min Z2 + 5 min recuperação');
    expect(html).toContain('40 min no total');
    expect(html).toContain('base aeróbica');
    expect(html).toContain('Substitui o cardio de Lower - TER');
    expect(html).toContain('28/10 (qua): HIIT vetado, trocado por Normal (véspera de perna)');
    expect(html).toContain('01/11 (dom): teto semanal de minutos');
    expect(html).toContain('Cortada');
    expect(html.match(/Aplicar no Modelo/g)?.length).toBe(1);
    // Uma só sessão aplicável: sem "Aplicar todas".
    expect(html).not.toContain('Aplicar todas no Modelo');
  });

  it('quem não edita o Modelo não vê botão', () => {
    const view = {
      ...VIEW,
      can_apply: false,
      sessions: VIEW.sessions.map((s) => ({ ...s, applicable: false })),
    };
    const html = render(createElement(StructuredRecommendation, { recommendationId: 5, view }));
    expect(html).not.toContain('Aplicar');
  });

  it('com mais de uma aplicável oferece aplicar todas', () => {
    const view = { ...VIEW, sessions: VIEW.sessions.map((s) => ({ ...s, applicable: true })) };
    expect(render(createElement(StructuredRecommendation, { recommendationId: 5, view }))).toContain(
      'Aplicar todas no Modelo',
    );
  });
});

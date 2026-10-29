import { MantineProvider } from '@mantine/core';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { PlanCardioResponse } from '@/lib/analytics-types';
import { theme } from '@/theme';
import { CardioAfterStrength, zoneRangeText } from './CardioAfterStrength';

function render(element: ReactElement): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, element)).replace(/<style[^>]*>[\s\S]*?<\/style>/g, '');
}

// Forma de `GET /workout-plans/{id}/cardio` (backend `cardio_display`): textos,
// total e faixas já prontos. A tela só exibe.
const HIIT: PlanCardioResponse = {
  workout_plan_id: 9,
  slot_name: 'Lower - TER',
  zones_source: 'intervals',
  items: [
    {
      mode: 'hiit',
      mode_label: 'HIIT',
      text: '5 min aquec. + 8× [30 s Z4 / 90 s Z2] + 5 min recuperação',
      short: 'HIIT 8× 30 s Z4 / 90 s Z2 · 5+5 min',
      total_minutes_label: '26 min',
      steps: [
        { kind: 'warmup', label: 'Aquecimento', text: '5 min', zones: [] },
        {
          kind: 'intervals',
          label: 'Séries',
          text: '8× [30 s Z4 / 90 s Z2]',
          zones: [
            { zone: 4, label: 'Z4', lower_bpm: 145, upper_bpm: 159 },
            { zone: 2, label: 'Z2', lower_bpm: 115, upper_bpm: 129 },
          ],
        },
        { kind: 'cooldown', label: 'Recuperação', text: '5 min', zones: [] },
      ],
    },
  ],
};

describe('CardioAfterStrength', () => {
  it('mostra etapas, faixas de bpm e o tempo total vindos do backend', () => {
    const html = render(createElement(CardioAfterStrength, { cardio: HIIT }));
    expect(html).toContain('Cardio de hoje · Lower - TER');
    expect(html).toContain('26 min no total');
    expect(html).toContain('Aquecimento');
    expect(html).toContain('8× [30 s Z4 / 90 s Z2]');
    expect(html).toContain('Z4 · 145–159 bpm');
    expect(html).toContain('Z2 · 115–129 bpm');
    expect(html).toContain('Recuperação');
  });

  it('sem cardio no slot não desenha nada', () => {
    expect(render(createElement(CardioAfterStrength, { cardio: { ...HIIT, items: [] } }))).toBe('');
    expect(render(createElement(CardioAfterStrength, { cardio: undefined }))).toBe('');
  });

  it('sem zonas do atleta mostra só a zona', () => {
    expect(zoneRangeText({ zone: 2, label: 'Z2', lower_bpm: null, upper_bpm: null })).toBe('Z2');
  });
});

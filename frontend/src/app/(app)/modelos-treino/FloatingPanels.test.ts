import { MantineProvider } from '@mantine/core';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { DEFAULT_PANEL_STATE } from '@/lib/floating-panel-geometry';
import { FIXTURE_EVALUATION } from '@/lib/methodology-volume-fixtures';
import type { PreviewActivationResponse } from '@/lib/workout-plan-templates-types';
import { theme } from '@/theme';
import type { IntensityDensityGroupRow } from '@/lib/intensity-density';
import { IntensityDensityFloatingPanel } from './IntensityDensityFloatingPanel';
import { IntensityDensityTable } from './IntensityDensityTable';
import { RoleActivationFloatingPanel } from './RoleActivationFloatingPanel';
import type { FloatingPanelControls } from './useFloatingPanels';
import { WeeklySetsFloatingPanel } from './WeeklySetsFloatingPanel';

/**
 * Render SSR das janelas flutuantes (Fase 7, Frente A). Sem jsdom: afirma
 * sobre o HTML de `renderToStaticMarkup`, com `withinPortal: false` (o
 * `Portal` do Mantine não monta no servidor). Arrasto fica na verificação manual.
 */
const noop = () => {};
function controls(minimized = false): FloatingPanelControls {
  return { state: { ...DEFAULT_PANEL_STATE, minimized }, onMove: noop, onToggleMinimized: noop, onClose: noop };
}

function render(element: ReactElement): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, element));
}

function weeklyPanel(minimized: boolean): string {
  return render(
    createElement(WeeklySetsFloatingPanel, {
      evaluation: FIXTURE_EVALUATION,
      isPending: false,
      isError: false,
      controls: controls(minimized),
      index: 0,
      withinPortal: false,
    }),
  );
}

describe('WeeklySetsFloatingPanel', () => {
  it('mostra macro-grupos, barra com valor/meta e controles da janela', () => {
    const html = weeklyPanel(false);
    expect(html).toContain('Séries por grupo');
    expect(html).toContain('Grandes');
    expect(html).toContain('28 / 10–20');
    expect(html).toContain('data-marker');
    expect(html).toContain('aria-label="Minimizar Séries por grupo"');
    expect(html).toContain('aria-label="Fechar Séries por grupo"');
  });

  it('minimizada: só a barra de título', () => {
    const html = weeklyPanel(true);
    expect(html).toContain('aria-label="Restaurar Séries por grupo"');
    expect(html).not.toContain('28 / 10–20');
  });
});

describe('RoleActivationFloatingPanel', () => {
  it('mostra números por papel, ordenados pelo total', () => {
    const response: PreviewActivationResponse = {
      cycle_length: 7,
      window_days: 7,
      days: [
        {
          day: 1,
          slot_sequence: 1,
          slot_name: 'A',
          slot_type: 'normal',
          groups: [
            { muscle_group: 'Peitoral', primary_equivalent: 6, secondary_equivalent: 0, stabilizer_equivalent: 0, total_equivalent: 6 },
            { muscle_group: 'Tríceps', primary_equivalent: 0, secondary_equivalent: 2.5, stabilizer_equivalent: 0, total_equivalent: 2.5 },
          ],
        },
      ],
    };
    const html = render(
      createElement(RoleActivationFloatingPanel, {
        response,
        isPending: false,
        isError: false,
        controls: controls(),
        index: 0,
        withinPortal: false,
      }),
    );
    expect(html).toContain('Ativação por participação');
    expect(html).toContain('aria-label="Principal"');
    expect(html).toContain('2,5');
    expect(html.indexOf('Peitoral')).toBeLessThan(html.indexOf('Tríceps'));
  });
});

const ID_ROWS: IntensityDensityGroupRow[] = [
  {
    muscleGroupId: 1,
    muscleGroup: 'Peitoral',
    weight: 14,
    position: 0.8,
    intensity: 0.9,
    density: 0.3,
    subindices: { reps: 0.2, peak: 0, rest: 0.4, sets: 0.6 },
    direction: 'intensity',
    label: '→ Intensidade',
  },
  {
    muscleGroupId: 2,
    muscleGroup: 'Costas',
    weight: 3,
    position: null,
    intensity: null,
    density: null,
    subindices: { reps: null, peak: null, rest: null, sets: null },
    direction: null,
    label: 'sem dados',
  },
];

describe('IntensityDensityFloatingPanel', () => {
  it('mostra legenda, direção e marcador na posição do grupo', () => {
    const html = render(
      createElement(IntensityDensityFloatingPanel, {
        rows: ID_ROWS,
        isPending: false,
        isError: false,
        controls: controls(),
        index: 2,
        withinPortal: false,
      }),
    );
    expect(html).toContain('Intensidade × Densidade');
    expect(html).toContain('← Densidade/Volume');
    expect(html).toContain('→ Intensidade');
    expect(html).toContain('data-position="80"');
    expect(html).toContain('sem dados');
    expect(html).toContain('aria-label="Fechar Intensidade × Densidade"');
  });

  it('vazio sem linhas', () => {
    const html = render(
      createElement(IntensityDensityFloatingPanel, {
        rows: [],
        isPending: false,
        isError: false,
        controls: controls(),
        index: 0,
        withinPortal: false,
      }),
    );
    expect(html).toContain('Adicione exercícios aos slots');
  });
});

describe('IntensityDensityTable', () => {
  it('colunas e percentuais das mesmas linhas da janela', () => {
    const html = render(
      createElement(IntensityDensityTable, { rows: ID_ROWS, hasAnyFilledSlot: true, isPending: false, isError: false }),
    );
    for (const header of ['Grupo', 'Barra', 'Intensidade', 'Densidade/Volume', 'Direção']) {
      expect(html).toContain(header);
    }
    expect(html).toContain('90%');
    expect(html).toContain('30%');
    expect(html).toContain('data-position="80"');
  });

  it('sem slot preenchido mostra o aviso', () => {
    const html = render(
      createElement(IntensityDensityTable, { rows: [], hasAnyFilledSlot: false, isPending: false, isError: false }),
    );
    expect(html).toContain('Preencha ao menos um slot');
  });
});

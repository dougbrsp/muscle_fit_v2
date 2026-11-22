import { MantineProvider } from '@mantine/core';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { AnaliseIaSnapshotMobile } from '@/mobile/screens/evolucao/AnaliseIaSnapshotMobile';
import type { ExerciseProgressionContextItem, WorkoutsAnalysisTraining } from '@/lib/workouts-analysis-types';
import { theme } from '@/theme';
import { AnaliseIaSnapshot } from './AnaliseIaSnapshot';

function render(element: ReactElement): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, element));
}

const SCOPE = {
  code: 'exercise_last_vs_previous_exposure',
  entity: 'exercise',
  metric: 'e1rm',
  comparator: 'last_vs_previous_exposure',
  label: 'Última exposição vs. anterior',
  description: 'Compara a última exposição comparável com a anterior.',
};

const ITEM: ExerciseProgressionContextItem = {
  exercise_id: 1001,
  exercise_name: 'Agachamento livre',
  status: 'PROGRESSING',
  status_label: 'Progredindo',
  status_reasons: [],
  comparable_exposures: 2,
  exposures_in_window: 2,
  equipment_id: 11,
  equipment_name: 'Gaiola',
  load_convention: 'total',
  latest: null,
  previous_comparable: null,
  paired_top_reps: null,
};

function training(item: ExerciseProgressionContextItem): WorkoutsAnalysisTraining {
  return { weekly_trend: [], volume_overview: [], recovery: [], exercise_progression: [item] } as unknown as WorkoutsAnalysisTraining;
}

describe.each([
  ['web', AnaliseIaSnapshot],
  ['mobile', AnaliseIaSnapshotMobile],
])('escopo do selo no snapshot (%s)', (_nome, Componente) => {
  it('com status_scope mostra o rótulo do escopo junto do selo', () => {
    const html = render(createElement(Componente, { training: training({ ...ITEM, status_scope: SCOPE }) }));
    expect(html).toContain('Última exposição vs. anterior');
    expect(html).toContain('Agachamento livre');
  });

  it('snapshot antigo, sem o campo, abre sem texto de escopo', () => {
    const html = render(createElement(Componente, { training: training(ITEM) }));
    expect(html).toContain('Agachamento livre');
    expect(html).not.toContain('Última exposição vs. anterior');
  });
});

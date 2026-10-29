import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CountingModeComparisonSection } from '@/app/(app)/diagnostico-volume/[templateId]/CountingModeComparisonSection';
import { VolumeClassesPanel, MUSCLE_GROUPS_CRUD_QUERY_KEY } from '@/app/(app)/dominio-exercicios/components/VolumeClassesPanel';
import { theme } from '@/theme';
import { buildCountingModeRows, uncalibratedWarning } from './counting-mode-comparison';
import motorConstants from './motor-constants.generated.json';
import type { CountingModeComparison, CountingModeGroupSnapshot } from './methodology-volume-types';
import { buildMethodologyVolumePayload } from './methodology-volume';
import { volumeClassesQueryKey } from './volume-classes';
import type { VolumeClassItem } from './volume-classes-types';
import {
  COUNTING_MODES,
  DEFAULT_COUNTING_MODE,
  countingModeOption,
  isDefaultCountingMode,
  normalizeCountingMode,
  setsUnitOf,
} from './volume-counting-mode';

/**
 * Lote 3 do modo de contagem: o FE lê os modos do JSON gerado pelo motor,
 * formata a comparação lado a lado que o backend calcula e a aba admin mostra
 * o selo de cópia não calibrada e o botão de copiar.
 */

const DEFAULT = DEFAULT_COUNTING_MODE;
const WEIGHTED = COUNTING_MODES.find((mode) => mode.value !== DEFAULT)!.value;

describe('volume-counting-mode (espelho do motor, sem literal)', () => {
  it('os modos e o default são os do JSON gerado pelo backend', () => {
    expect(COUNTING_MODES).toBe(motorConstants.methodology_volume.counting_modes);
    expect(DEFAULT).toBe(motorConstants.methodology_volume.default_counting_mode);
    expect(isDefaultCountingMode(undefined)).toBe(true);
    expect(normalizeCountingMode('desconhecido')).toBe(DEFAULT);
    expect(normalizeCountingMode(WEIGHTED)).toBe(WEIGHTED);
    expect(setsUnitOf(DEFAULT).plural).toBe('séries diretas');
    expect(countingModeOption(WEIGHTED).description.length).toBeGreaterThan(0);
  });

  it('o corpo do preview leva o modo do formulário (e não muda sem ele)', () => {
    expect(buildMethodologyVolumePayload([], 'equipment_series', 'standard', [], WEIGHTED).volume_counting_mode).toBe(
      WEIGHTED,
    );
    expect('volume_counting_mode' in buildMethodologyVolumePayload([], 'equipment_series', 'standard', [])).toBe(false);
  });
});

const snap = (mode: string, over: Partial<CountingModeGroupSnapshot>): CountingModeGroupSnapshot => ({
  counting_mode: mode,
  sets_unit_label: setsUnitOf(mode).plural,
  weekly_frequency: 1,
  frequency_bucket: '1x',
  direct_sets_weekly: 12,
  direct_sets_per_session: 12,
  status: 'WITHIN_RANGE',
  severity: null,
  status_reason: null,
  session_range_min: 9,
  session_range_max: 16,
  weekly_range_min: 12,
  weekly_range_max: 20,
  uncalibrated_ranges: false,
  ...over,
});

const COMPARISON: CountingModeComparison = {
  metric: 'methodology_volume',
  metric_version: 'methodology-volume-v5',
  current_counting_mode: DEFAULT,
  counting_modes: [DEFAULT, WEIGHTED],
  uncalibrated_modes: [WEIGHTED],
  template_id: 28,
  template_name: 'ABC-DEF',
  groups: [
    {
      muscle_group_id: 5,
      muscle_group_name: 'Bíceps',
      volume_class_name: 'Pequenos',
      modes: [
        snap(DEFAULT, {}),
        snap(WEIGHTED, { direct_sets_weekly: 21.5, status: 'ABOVE_RANGE', severity: 'warning', uncalibrated_ranges: true }),
      ],
      status_changed: true,
      bucket_changed: false,
    },
    {
      muscle_group_id: 6,
      muscle_group_name: 'Costas',
      volume_class_name: 'Grandes',
      modes: [snap(DEFAULT, {}), snap(WEIGHTED, {})],
      status_changed: false,
      bucket_changed: false,
    },
  ],
};

describe('comparação lado a lado (só formatação)', () => {
  it('linhas com a unidade de cada modo e a transição vinda do backend', () => {
    const [biceps, costas] = buildCountingModeRows(COMPARISON);
    expect(biceps.cells.map((cell) => cell.weeklyText)).toEqual([
      `12 ${setsUnitOf(DEFAULT).plural}/sem`,
      `21,5 ${setsUnitOf(WEIGHTED).plural}/sem`,
    ]);
    expect(biceps.cells[0].isCurrent).toBe(true);
    expect(biceps.cells[1].uncalibrated).toBe(true);
    expect(biceps.transitionText).toBe('Na faixa → Acima da faixa');
    expect(costas.transitionText).toBeNull();
    expect(uncalibratedWarning(COMPARISON)).toContain(countingModeOption(WEIGHTED).label);
    expect(uncalibratedWarning({ ...COMPARISON, uncalibrated_modes: [] })).toBeNull();
  });

  it('render: aviso de não calibrada, selo e destaque da transição', () => {
    const html = renderToStaticMarkup(
      createElement(
        MantineProvider,
        { theme },
        createElement(CountingModeComparisonSection, { comparison: COMPARISON, isPending: false, error: null }),
      ),
    );
    expect(html).toContain('data-uncalibrated-warning');
    expect(html).toContain('cópia — não calibrada');
    expect(html).toContain('data-comparison-row="5" data-transition="true"');
    expect(html).toContain('muda o status');
    expect(html).toContain('1 grupo muda de status ou de frequência entre os modos.');
  });
});

describe('aba admin no modo ponderado', () => {
  const ITEM: VolumeClassItem = {
    id: 30,
    name: 'Novo',
    description: null,
    sort_order: 30,
    weekly_sets_min: null,
    weekly_sets_max: null,
    weekly_source: null,
    weekly_evidence: null,
    counting_mode: WEIGHTED,
    weekly_uncalibrated: false,
    ranges: [
      {
        frequency_bucket: '1x',
        sets_min: 9,
        sets_max: 16,
        exercises_min: null,
        exercises_max: null,
        sets_source: 'methodology',
        sets_evidence: 'EXG-017',
        exercises_source: null,
        exercises_evidence: null,
        copied_from_counting_mode: DEFAULT,
        uncalibrated: true,
      },
    ],
    muscle_group_ids: [],
  };

  function render(mode: string): string {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    client.setQueryData(volumeClassesQueryKey(mode), { items: [ITEM] });
    client.setQueryData(MUSCLE_GROUPS_CRUD_QUERY_KEY, { items: [] });
    return renderToStaticMarkup(
      createElement(
        QueryClientProvider,
        { client },
        createElement(
          MantineProvider,
          { theme },
          createElement(VolumeClassesPanel, { isAdmin: true, initialCountingMode: mode }),
        ),
      ),
    );
  }

  it('selo de cópia, unidade do modo e botão de copiar o que falta', () => {
    const html = render(WEIGHTED);
    expect(html).toContain('data-counting-mode-switch');
    expect(html).toContain('data-uncalibrated');
    expect(html).toContain(`Meta semanal (${setsUnitOf(WEIGHTED).plural} por semana)`);
    expect(html).toContain('data-copy-from-default');
    expect(html).toContain(`Copiar de ${countingModeOption(DEFAULT).label}`);
    expect(html).toContain('faltam 2 faixas e a meta semanal');
  });

  it('no modo default não há botão de copiar nem selo', () => {
    const html = render(DEFAULT);
    expect(html).not.toContain('data-copy-from-default');
    expect(html).not.toContain('data-uncalibrated');
  });
});

import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { buildVersionSummary } from '@/lib/template-version-summary';
import type {
  WorkoutPlanTemplateVersionDetail,
  WorkoutPlanTemplateVersionSlotItem,
} from '@/lib/workout-plan-templates-types';
import { theme } from '@/theme';
import { TemplateVersionSummary } from './TemplateVersionSummary';

/**
 * Resumo somente leitura de uma versão (feedback do usuário no PR #386: o
 * modal replicava o editor). Render por `react-dom/server`: nenhum controle
 * de edição, Modo A e B, descanso, cardio e técnica.
 */

function item(overrides: Partial<WorkoutPlanTemplateVersionSlotItem>): WorkoutPlanTemplateVersionSlotItem {
  return {
    item_order: 1,
    item_kind: 'equipment',
    muscle_group_id: 52,
    mapping_id: 7100,
    equipment_id: 9001,
    sets_count: 3,
    reps_target: '8-10',
    rpe_target: '8',
    prioritize: false,
    cardio_duration_minutes: null,
    priority_min_pct: null,
    priority_max_pct: null,
    advanced_technique: null,
    advanced_technique_applications: null,
    advanced_technique_duration_seconds: null,
    warmup_sets_count: null,
    warmup_reps: null,
    warmup_rest_seconds: null,
    feeler_set_sets_count: null,
    feeler_set_reps: null,
    feeler_set_rest_seconds: null,
    peak_contraction_seconds: null,
    rest_seconds: 90,
    exercise_name: 'Supino reto',
    equipment_name: 'Banco reto',
    muscle_group_name: 'Peitoral',
    ...overrides,
  };
}

function detail(overrides: Partial<WorkoutPlanTemplateVersionDetail> = {}): WorkoutPlanTemplateVersionDetail {
  return {
    id: 7,
    name: 'Modelo ABC',
    description: null,
    training_type_id: null,
    volume_mode: 'equipment_series',
    grid_rows: null,
    grid_cols: null,
    version: {
      id: 1,
      number: 1,
      effective_from: null,
      superseded_at: '2026-10-04T10:00:00',
      is_current: false,
      change_summary: 'v1 (backfill da migracao 065)',
      author_name: null,
      reverted_from_number: null,
    },
    slots: [
      {
        sequence: 2,
        name: 'Descanso ativo',
        weekday: null,
        slot_type: 'descanso',
        priority_min_pct: null,
        priority_max_pct: null,
        items: [],
      },
      {
        sequence: 1,
        name: 'Push',
        weekday: 1,
        slot_type: 'normal',
        priority_min_pct: null,
        priority_max_pct: null,
        items: [
          item({}),
          item({
            item_order: 2,
            exercise_name: 'Crucifixo',
            equipment_name: 'Polia',
            advanced_technique: 'drop_set',
            advanced_technique_applications: 2,
            rest_seconds: 60,
          }),
          item({
            item_order: 3,
            item_kind: 'cardio',
            mapping_id: null,
            equipment_id: null,
            muscle_group_id: null,
            cardio_duration_minutes: 20,
            exercise_name: null,
            equipment_name: null,
            muscle_group_name: null,
          }),
        ],
      },
    ],
    ...overrides,
  };
}

function render(d: WorkoutPlanTemplateVersionDetail): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, createElement(TemplateVersionSummary, { detail: d })));
}

describe('buildVersionSummary', () => {
  it('ordena os slots e monta as linhas do Modo B', () => {
    const summary = buildVersionSummary(detail());
    expect(summary.slots.map((s) => s.title)).toEqual(['Push', 'Descanso ativo']);
    const [supino, crucifixo, cardio] = summary.slots[0].items;
    expect(supino).toMatchObject({ title: 'Supino reto', subtitle: 'Banco reto · Peitoral' });
    expect(supino.details).toEqual(['3 × 8-10', 'RPE 8', 'Descanso 1min 30s']);
    expect(crucifixo.details.some((d) => d.startsWith('Técnica: ') && d.includes('(2×)'))).toBe(true);
    expect(cardio).toMatchObject({ title: 'Cardio', details: ['20 min'] });
    expect(summary.slots[0]).toMatchObject({ kindLabel: 'Treino', weekdayLabel: 'Segunda' });
    expect(summary.slots[1]).toMatchObject({ kindLabel: 'Descanso', isRest: true, items: [] });
  });

  it('cabeçalho: modo, início, autor e o texto da v1 sem jargão', () => {
    const summary = buildVersionSummary(detail());
    expect(summary.modeLabel).toBe('Equipamento + séries fixas');
    expect(summary.startLabel).toBe('Desde o início');
    expect(summary.authorLabel).toBe('Autor não registrado');
    expect(summary.changeLabel).toBe('Versão inicial (anterior ao versionamento)');
  });

  it('Modo A mostra o grupo muscular e a faixa da meta, sem exercício', () => {
    const summary = buildVersionSummary(
      detail({
        volume_mode: 'percent_budget',
        slots: [
          {
            sequence: 1,
            name: 'Full body',
            weekday: null,
            slot_type: 'normal',
            priority_min_pct: null,
            priority_max_pct: null,
            items: [
              item({
                item_kind: 'muscle_group',
                mapping_id: null,
                equipment_id: null,
                exercise_name: null,
                equipment_name: null,
                priority_min_pct: 20,
                priority_max_pct: 30,
                prioritize: true,
              }),
            ],
          },
        ],
      }),
    );
    expect(summary.modeLabel).toBe('% de orçamento (motor v2)');
    expect(summary.slots[0].items[0]).toMatchObject({
      title: 'Peitoral',
      subtitle: 'Exercício escolhido pelo motor',
      details: ['20–30% da meta semanal', 'Priorizado'],
    });
  });
});

describe('TemplateVersionSummary (SSR)', () => {
  const html = render(detail());

  it('não tem nenhum controle de edição', () => {
    expect(html).not.toMatch(/<input|<select|<textarea|<button/i);
    expect(html).not.toContain('Adicionar');
    expect(html).not.toContain('Slot sem exercício');
  });

  it('mostra cada slot com tipo, dia e itens; descanso só "Descanso"', () => {
    expect(html).toContain('Push');
    expect(html).toContain('Segunda');
    expect(html).toContain('Supino reto');
    expect(html).toContain('3 × 8-10 · RPE 8 · Descanso 1min 30s');
    expect(html).toContain('Cardio');
    expect(html).toContain('20 min');
    expect(html).toContain('data-slot-kind="Descanso"');
    expect(html).toContain('>Descanso<');
  });

  it('cabeçalho em português, sem jargão de migração', () => {
    expect(html).toContain('Versão inicial (anterior ao versionamento)');
    expect(html).toContain('Desde o início');
    expect(html).not.toMatch(/backfill|migracao/i);
  });
});

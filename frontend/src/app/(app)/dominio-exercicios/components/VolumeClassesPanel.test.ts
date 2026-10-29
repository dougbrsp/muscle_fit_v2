import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { MuscleGroupItem } from '@/lib/exercise-catalog-admin-types';
import {
  VOLUME_CLASSES_QUERY_KEY,
  buildRangesPayload,
  formatDecimalRange,
  groupOptionsFor,
  rangeDrafts,
  unassignedGroups,
  volumeClassLabelForGroup,
} from '@/lib/volume-classes';
import type { VolumeClassItem } from '@/lib/volume-classes-types';
import { theme } from '@/theme';
import { MUSCLE_GROUPS_CRUD_QUERY_KEY, VolumeClassesPanel } from './VolumeClassesPanel';

/**
 * Render da aba "Macro-grupos de volume" (Fase 6, PR 3) pelo `react-dom/server`
 * (sem jsdom): o cache do React Query é pré-carregado, então o SSR já mostra
 * os cards. Sem interação — salvar/excluir é coberto pelos testes do CRUD no
 * backend; aqui só o que a tela mostra e os helpers puros.
 */

const GROUPS: MuscleGroupItem[] = [
  { id: 1, name: 'Peitoral', primary_muscles: null, secondary_muscles: null, methodology_parent_group_id: null },
  { id: 2, name: 'Peitoral superior', primary_muscles: null, secondary_muscles: null, methodology_parent_group_id: 1 },
  { id: 3, name: 'Bíceps', primary_muscles: null, secondary_muscles: null, methodology_parent_group_id: null },
  { id: 4, name: 'Antebraço', primary_muscles: null, secondary_muscles: null, methodology_parent_group_id: null },
];

const CLASSES: VolumeClassItem[] = [
  {
    id: 20,
    name: 'Pequenos',
    description: 'Braços e panturrilhas',
    sort_order: 20,
    weekly_sets_min: '6.0',
    weekly_sets_max: 12,
    weekly_source: 'derived',
    weekly_evidence: 'regra escrita',
    ranges: [
      {
        frequency_bucket: '1x',
        sets_min: 9,
        sets_max: 16,
        exercises_min: 3,
        exercises_max: 4,
        sets_source: 'methodology',
        sets_evidence: 'EXG-017',
        exercises_source: 'methodology',
        exercises_evidence: 'EXG-017',
      },
    ],
    muscle_group_ids: [3],
  },
  {
    id: 10,
    name: 'Grandes',
    description: null,
    sort_order: 10,
    weekly_sets_min: 12.5,
    weekly_sets_max: 20,
    weekly_source: 'admin',
    weekly_evidence: null,
    ranges: [
      {
        frequency_bucket: '2x',
        sets_min: 9,
        sets_max: 16,
        exercises_min: null,
        exercises_max: null,
        sets_source: 'admin',
        sets_evidence: null,
        exercises_source: null,
        exercises_evidence: null,
      },
    ],
    muscle_group_ids: [1],
  },
];

function render(isAdmin: boolean): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(VOLUME_CLASSES_QUERY_KEY, { items: CLASSES });
  client.setQueryData(MUSCLE_GROUPS_CRUD_QUERY_KEY, { items: GROUPS });
  return renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client },
      createElement(MantineProvider, { theme }, createElement(VolumeClassesPanel, { isAdmin })),
    ),
  );
}

describe('VolumeClassesPanel (SSR)', () => {
  const html = render(true);

  it('um card por macro-grupo, na ordem de sort_order', () => {
    const grandes = html.indexOf('data-volume-class="10"');
    const pequenos = html.indexOf('data-volume-class="20"');
    expect(grandes).toBeGreaterThan(-1);
    expect(pequenos).toBeGreaterThan(grandes);
    expect(html).toContain('Braços e panturrilhas');
    expect(html).toContain('Meta semanal (séries diretas por semana)');
    expect(html).toContain('Atual: 12,5–20');
  });

  it('tabela 1x/2x/3x+ sempre completa, com a origem de cada número em badge', () => {
    expect(html.match(/data-frequency-bucket="1x"/g)).toHaveLength(2);
    expect(html.match(/data-frequency-bucket="3x_plus"/g)).toHaveLength(2);
    expect(html).toContain('sem faixa (não avalia por treino)');
    expect(html).toContain('data-range-source="methodology"');
    expect(html).toContain('data-range-source="derived"');
    expect(html).toContain('data-range-source="admin"');
    expect(html).toContain('>Metodologia<');
    expect(html).toContain('>Admin<');
  });

  it('bloco "Sem macro-grupo" lista só grupos não-fatia sem associação', () => {
    const start = html.indexOf('data-unassigned-groups');
    const block = html.slice(start);
    expect(block).toContain('Sem macro-grupo (1)');
    expect(block).toContain('Antebraço');
    expect(block).not.toContain('Peitoral superior');
  });

  it('não-admin: inputs e botões desabilitados', () => {
    const readOnly = render(false);
    expect(readOnly).toMatch(/<button[^>]*disabled[^>]*>(?:(?!<\/button>).)*Novo macro-grupo/);
    expect(readOnly).toMatch(/<input[^>]*disabled[^>]*value="Grandes"|<input[^>]*value="Grandes"[^>]*disabled/);
  });
});

describe('helpers da aba de macro-grupos', () => {
  it('formata fração com vírgula e inteiro sem casa', () => {
    expect(formatDecimalRange(12.5, 20)).toBe('12,5–20');
    expect(formatDecimalRange('6.0', 12)).toBe('6–12');
    expect(formatDecimalRange(null, 12)).toBe('—');
  });

  it('opções do MultiSelect: sem fatias, com "(em <outro>)" para grupo de outro macro-grupo', () => {
    const options = groupOptionsFor(10, GROUPS, CLASSES);
    expect(options.map((option) => option.label)).toEqual(['Antebraço', 'Bíceps (em Pequenos)', 'Peitoral']);
  });

  it('rascunho sempre com 1x/2x/3x+; linha toda vazia sai do payload (apaga a linha)', () => {
    const drafts = rangeDrafts(CLASSES[1].ranges);
    expect(drafts.map((draft) => draft.frequency_bucket)).toEqual(['1x', '2x', '3x_plus']);
    expect(buildRangesPayload(drafts)).toEqual([
      { frequency_bucket: '2x', sets_min: 9, sets_max: 16, exercises_min: null, exercises_max: null },
    ]);
    drafts[2] = { ...drafts[2], sets_min: 4, sets_max: '8,5' };
    expect(buildRangesPayload(drafts)[1]).toEqual({
      frequency_bucket: '3x_plus',
      sets_min: 4,
      sets_max: 8.5,
      exercises_min: null,
      exercises_max: null,
    });
  });

  it('grupos sem macro-grupo e rótulo só-leitura da aba de grupos (fatia usa o do pai)', () => {
    expect(unassignedGroups(GROUPS, CLASSES).map((group) => group.name)).toEqual(['Antebraço']);
    const byId = new Map(GROUPS.map((group) => [group.id, group]));
    expect(volumeClassLabelForGroup(GROUPS[0], byId, CLASSES)).toBe('Grandes');
    expect(volumeClassLabelForGroup(GROUPS[1], byId, CLASSES)).toBe('Grandes (do grupo pai)');
    expect(volumeClassLabelForGroup(GROUPS[3], byId, CLASSES)).toBe('Sem macro-grupo');
  });
});

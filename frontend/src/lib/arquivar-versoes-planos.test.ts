import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { TemplateVersionHistoryList } from '@/app/(app)/modelos-treino/[id]/TemplateVersionHistory';
import { PlanHistory } from '@/components/nutrition/PlanHistory';
import { ApiError } from './api';
import { nutritionKeys } from './nutrition-api';
import type { NutritionPlan } from './nutrition-types';
import {
  readArchiveError,
  versionArchivePath,
  versionHistoryRows,
  versionsListPath,
  type TemplateVersionListItem,
} from './template-versions';
import { theme } from '@/theme';

/**
 * Arquivar (migração 105) é só ocultar: o backend decide quem pode (`can_archive`) e o frontend só consome.
 * Funções puras + render por `react-dom/server` (o repo não tem jsdom).
 */

const count = (html: string, text: string) => html.split(text).length - 1;
const noop = () => undefined;
const wrap = (node: ReturnType<typeof createElement>) =>
  renderToStaticMarkup(createElement(MantineProvider, { theme }, node));

const VERSOES: TemplateVersionListItem[] = [
  {
    id: 3, number: 3, effective_from: '2026-10-05T21:30:00', superseded_at: null, author_user_id: 1, author_name: 'Doug',
    reverted_from_number: null, change_summary: null, is_current: true, archived: false, can_archive: false,
  },
  {
    id: 2, number: 2, effective_from: '2026-10-04T10:00:00', superseded_at: '2026-10-05T21:30:00', author_user_id: 1,
    author_name: 'Doug', reverted_from_number: null, change_summary: null, is_current: false, archived: false,
    can_archive: true,
  },
  {
    id: 1, number: 1, effective_from: null, superseded_at: '2026-10-04T10:00:00', author_user_id: null, author_name: null,
    reverted_from_number: null, change_summary: null, is_current: false, archived: true,
    archived_at: '2026-10-06T10:00:00', can_archive: false,
  },
];

describe('versões de Modelo: caminhos e linhas', () => {
  it('include_archived só entra com a chave ligada', () => {
    expect(versionsListPath(7, false)).toBe('/workout-plan-templates/7/versions');
    expect(versionsListPath(7, true)).toBe('/workout-plan-templates/7/versions?include_archived=true');
    expect(versionArchivePath(7, 2, true)).toBe('/workout-plan-templates/7/versions/2/archive');
    expect(versionArchivePath(7, 2, false)).toBe('/workout-plan-templates/7/versions/2/unarchive');
  });

  it('as linhas copiam archived/can_archive do backend; backend antigo (sem campos) = nada arquivável', () => {
    const rows = versionHistoryRows(VERSOES);
    expect(rows.map((r) => [r.archived, r.canArchive])).toEqual([[false, false], [false, true], [true, false]]);
    const antigo = versionHistoryRows([{ ...VERSOES[1], archived: undefined, can_archive: undefined }]);
    expect(antigo[0].archived).toBe(false);
    expect(antigo[0].canArchive).toBe(false);
  });

  it('erro do 409 chega com o texto da API', () => {
    expect(readArchiveError(new ApiError('A v3 é a versão vigente e não pode ser arquivada.', 409))).toContain('vigente');
    expect(readArchiveError(new Error('rede'))).toBe('rede');
    expect(readArchiveError('x')).toBe('Não foi possível arquivar a versão.');
  });
});

describe('TemplateVersionHistoryList: Arquivar / Desarquivar (SSR)', () => {
  const render = (canManage: boolean, handlers = true) =>
    wrap(
      createElement(TemplateVersionHistoryList, {
        rows: versionHistoryRows(VERSOES),
        canManage,
        onView: noop,
        onRevert: noop,
        ...(handlers ? { onArchive: noop, onUnarchive: noop } : {}),
      }),
    );

  it('Arquivar só na não vigente que o backend liberou; Desarquivar + badge só na arquivada', () => {
    const html = render(true);
    expect(count(html, 'Arquivar</span>')).toBe(1);
    expect(count(html, 'Desarquivar')).toBe(1);
    expect(count(html, 'data-testid="version-archived-badge"')).toBe(1);
    expect(count(html, 'data-archived="true"')).toBe(1);
  });

  it('sem admin/PT ou sem os handlers, nenhuma ação de arquivar (o badge continua)', () => {
    for (const html of [render(false), render(true, false)]) {
      expect(count(html, 'Arquivar</span>')).toBe(0);
      expect(count(html, 'Desarquivar')).toBe(0);
      expect(count(html, 'version-archived-badge')).toBe(1);
    }
  });
});

function plano(id: number, state: string, extra: Partial<NutritionPlan> = {}): NutritionPlan {
  return {
    id, user_id: 2, status: state === 'draft' ? 'draft' : 'published', state, strategy: 'maintenance', notes: null,
    engine_version: null, effective_from: '2026-09-01', superseded_at: state === 'superseded' ? '2026-10-01' : null,
    published_at: null, created_by: 1, published_by: null, created_at: '2026-09-01T00:00:00', updated_at: null,
    ...extra,
  } as NutritionPlan;
}

describe('planos de nutrição: PlanHistory (SSR)', () => {
  const planos = [
    plano(4, 'draft'),
    plano(3, 'current'),
    plano(2, 'superseded', { can_archive: true, archived: false }),
    plano(1, 'superseded', { can_archive: false, archived: true, archived_at: '2026-10-05T10:00:00' }),
  ];
  const render = (extra: Record<string, unknown> = { onArchive: noop, onUnarchive: noop }) =>
    wrap(
      createElement(PlanHistory, {
        plans: planos, selectedId: null, onSelect: noop, onEdit: noop, onDelete: noop, deletingId: null, ...extra,
      }),
    );

  it('Arquivar só no substituído liberado pelo backend; Desarquivar e badge só no arquivado', () => {
    const html = render();
    expect(count(html, 'Arquivar</span>')).toBe(1);
    expect(count(html, 'Desarquivar')).toBe(1);
    expect(count(html, 'data-testid="plan-archived-badge"')).toBe(1);
    // rascunho mantém Editar/Excluir; vigente e rascunho nunca recebem Arquivar
    expect(count(html, 'Excluir')).toBe(1);
  });

  it('sem os handlers, nenhuma ação', () => {
    const html = render({});
    expect(count(html, 'Arquivar</span>')).toBe(0);
    expect(count(html, 'Desarquivar')).toBe(0);
  });
});

describe('nutritionKeys.plansList', () => {
  it('começa pelo prefixo plans(), então invalidar plans(userId) atualiza as duas listas', () => {
    const prefixo = nutritionKeys.plans(2);
    for (const incluir of [false, true]) {
      expect(nutritionKeys.plansList(2, incluir).slice(0, prefixo.length)).toEqual([...prefixo]);
    }
    expect(nutritionKeys.plansList(2, true)).not.toEqual(nutritionKeys.plansList(2, false));
  });
});

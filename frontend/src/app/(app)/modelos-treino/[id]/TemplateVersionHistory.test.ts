import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { versionHistoryRows, type TemplateVersionListItem } from '@/lib/template-versions';
import parity from '@/lib/training-goal.parity.json';
import type { TrainingGoalsResponse } from '@/lib/training-goals';
import { theme } from '@/theme';
import { RevertErrorAlert, TemplateVersionHistoryList } from './TemplateVersionHistory';

/**
 * Render do painel "Histórico de versões" (PR4) pelo `react-dom/server` (o
 * repo não tem jsdom nem testing-library): lista, vigente destacada, "revertida
 * da vN", ações só para admin/PT e nunca "Reverter" na vigente; e o 422 da
 * reversão em lista.
 */

const ITEMS: TemplateVersionListItem[] = [
  {
    id: 3, number: 3, effective_from: '2026-10-05T21:30:00', superseded_at: null, author_user_id: 1,
    author_name: 'Doug', reverted_from_number: 1, change_summary: 'Revertido para a v1', is_current: true,
  },
  {
    id: 2, number: 2, effective_from: '2026-10-04T10:00:00', superseded_at: '2026-10-05T21:30:00',
    author_user_id: 1, author_name: 'Doug', reverted_from_number: null, change_summary: null, is_current: false,
  },
  {
    id: 1, number: 1, effective_from: null, superseded_at: '2026-10-04T10:00:00', author_user_id: null,
    author_name: null, reverted_from_number: null, change_summary: null, is_current: false,
  },
];

function render(canManage: boolean): string {
  return renderToStaticMarkup(
    createElement(
      MantineProvider,
      { theme },
      createElement(TemplateVersionHistoryList, {
        rows: versionHistoryRows(ITEMS),
        canManage,
        onView: () => undefined,
        onRevert: () => undefined,
      }),
    ),
  );
}

function count(html: string, text: string): number {
  return html.split(text).length - 1;
}

describe('TemplateVersionHistoryList (SSR)', () => {
  const html = render(true);

  it('lista as versões com a vigente destacada', () => {
    expect(html).toContain('v3');
    expect(html).toContain('v2');
    expect(html).toContain('v1');
    expect(count(html, 'Vigente')).toBe(1);
    expect(count(html, 'data-current="true"')).toBe(1);
    expect(html).toContain('revertida da v1');
    expect(html).toContain('Desde o início');
    expect(html).toContain('Versão inicial (anterior ao versionamento)');
  });

  it('Ver em todas; Reverter só nas não vigentes', () => {
    expect(count(html, '>Ver<')).toBe(3);
    expect(count(html, 'Reverter para esta versão')).toBe(2);
  });

  it('sem admin/PT, nenhuma ação de reverter', () => {
    const semPermissao = render(false);
    expect(count(semPermissao, 'Reverter para esta versão')).toBe(0);
    expect(count(semPermissao, '>Ver<')).toBe(3);
  });

  it('M97 F3: badge do objetivo de cada versão, com o rótulo da API', () => {
    const goals = parity.payload as TrainingGoalsResponse;
    const other = goals.items.find((item) => !item.is_default)!;
    const items = ITEMS.map((item, i) => ({ ...item, training_goal: i === 0 ? other.value : goals.default }));
    const withGoals = renderToStaticMarkup(
      createElement(
        MantineProvider,
        { theme },
        createElement(TemplateVersionHistoryList, {
          rows: versionHistoryRows(items),
          canManage: false,
          onView: () => undefined,
          onRevert: () => undefined,
          trainingGoals: goals,
        }),
      ),
    );
    // só o objetivo diferente do default ganha badge (decisão do Tech Lead no PR #630)
    expect(count(withGoals, 'data-testid="version-goal-badge"')).toBe(1);
    expect(count(withGoals, `>${other.label}<`)).toBe(1);
    // sem a lista de objetivos (ou backend sem o campo), nenhum badge
    expect(count(html, 'version-goal-badge')).toBe(0);
  });

  it('lista vazia avisa em vez de sumir', () => {
    const vazio = renderToStaticMarkup(
      createElement(
        MantineProvider,
        { theme },
        createElement(TemplateVersionHistoryList, { rows: [], canManage: true, onView: () => {}, onRevert: () => {} }),
      ),
    );
    expect(vazio).toContain('Nenhuma versão registrada ainda.');
  });
});

describe('RevertErrorAlert (SSR)', () => {
  it('mostra a mensagem e CADA item inválido', () => {
    const html = renderToStaticMarkup(
      createElement(
        MantineProvider,
        { theme },
        createElement(RevertErrorAlert, {
          error: {
            message: 'A v1 não pode ser reativada hoje:',
            errors: ['Slot A: técnica drop_set incompatível', 'Slot B: item_kind exercise'],
          },
        }),
      ),
    );
    expect(html).toContain('Reversão bloqueada');
    expect(html).toContain('A v1 não pode ser reativada hoje:');
    expect(count(html, '<li')).toBe(2);
    expect(html).toContain('Slot A: técnica drop_set incompatível');
  });
});

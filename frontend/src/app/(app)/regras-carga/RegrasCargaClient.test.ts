import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  DRAFT_RULES_QUERY_KEY,
  MOTOR_DEFAULT_RULESET,
  PUBLISHED_RULES_QUERY_KEY,
  VERSIONS_RULES_QUERY_KEY,
  draftFromRuleSet,
} from '@/lib/next-set-load-rules';
import type { PublishedRuleSetResponse, StoredRuleSet } from '@/lib/next-set-load-rules-types';
import { theme } from '@/theme';
import { RegrasCargaClient } from './RegrasCargaClient';
import { RuleSetTable } from './components/RuleSetTable';
import { VersionsList } from './components/VersionsList';

/**
 * Render pelo `react-dom/server` (sem jsdom), como `VolumeClassesPanel.test.ts`:
 * cache do React Query pré-carregado, sem interação. O que importa aqui é que
 * a tela mostra o que o SERVIDOR mandou (versão, textos), sem calcular nada.
 */

function render(element: ReactElement, qc = new QueryClient()): string {
  return renderToStaticMarkup(
    createElement(MantineProvider, { theme }, createElement(QueryClientProvider, { client: qc }, element)),
  );
}

const STORED: StoredRuleSet = {
  id: 7,
  status: 'published',
  version: 3,
  current: true,
  notes: 'ajuste do pedido',
  engine_version: 'x',
  created_by: 1,
  published_by: 1,
  published_at: '2026-10-20T10:00:00',
  superseded_at: null,
  created_at: '2026-10-20T09:00:00',
  updated_at: '2026-10-20T10:00:00',
  ruleset: { ...MOTOR_DEFAULT_RULESET, version: 3, source: 'published' },
  summary: 'piso · teto\n1. regra',
};

describe('RegrasCargaClient', () => {
  it('cabeçalho com a versão vigente publicada', () => {
    const qc = new QueryClient();
    const published: PublishedRuleSetResponse = {
      id: 7,
      version: 3,
      published_at: STORED.published_at,
      source: 'published',
      ruleset: STORED.ruleset,
      summary: STORED.summary,
    };
    qc.setQueryData(PUBLISHED_RULES_QUERY_KEY, published);
    qc.setQueryData(DRAFT_RULES_QUERY_KEY, { draft: null, warnings: [] });
    qc.setQueryData(VERSIONS_RULES_QUERY_KEY, { versions: [STORED] });
    const html = render(createElement(RegrasCargaClient), qc);
    expect(html).toContain('Vigente: v3, publicada em');
    expect(html).toContain('a primeira que casa vence');
    expect(html).toContain('ajuste do pedido');
  });

  it('sem nada publicado: padrão do sistema', () => {
    const qc = new QueryClient();
    qc.setQueryData(PUBLISHED_RULES_QUERY_KEY, {
      id: null,
      version: 0,
      published_at: null,
      source: 'default',
      ruleset: MOTOR_DEFAULT_RULESET,
      summary: '',
    });
    qc.setQueryData(DRAFT_RULES_QUERY_KEY, { draft: null, warnings: [] });
    qc.setQueryData(VERSIONS_RULES_QUERY_KEY, { versions: [] });
    const html = render(createElement(RegrasCargaClient), qc);
    expect(html).toContain('Vigente: padrão do sistema');
    expect(html).toContain('Nenhuma versão publicada');
  });
});

describe('RuleSetTable', () => {
  it('mostra o texto do servidor por regra, ou "gerando" enquanto não chega', () => {
    const rules = draftFromRuleSet(MOTOR_DEFAULT_RULESET).rules;
    const html = render(
      createElement(RuleSetTable, { rules, texts: ['texto A', null, 'texto C'], onChange: () => undefined }),
    );
    expect(html).toContain('texto A');
    expect(html).toContain('texto C');
    expect(html).toContain('Gerando o texto da regra');
  });
});

describe('VersionsList', () => {
  it('vigente com quem publicou e notas', () => {
    const html = render(
      createElement(VersionsList, {
        versions: [STORED, { ...STORED, id: 6, version: 2, current: false, notes: null }],
        isLoading: false,
        error: null,
        userName: (id: number | null) => (id === 1 ? 'Doug' : '—'),
      }),
    );
    expect(html).toContain('v3');
    expect(html).toContain('Vigente');
    expect(html).toContain('Encerrada');
    expect(html).toContain('Doug');
  });
});

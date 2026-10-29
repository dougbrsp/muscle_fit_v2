import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  exerciseCardGenerationQueryKey,
  exerciseCardGenerationsQueryKey,
  exerciseCardQueryKey,
  EXERCISE_CARD_FAILURE_MESSAGES,
} from '@/lib/exercise-cards';
import { EXERCISE_CARD_CONTRACT } from '@/lib/exercise-cards-fixtures';
import type { ExerciseCardGeneration, PublishedExerciseCard } from '@/lib/exercise-cards-types';
import { theme } from '@/theme';
import { ExerciseCardHistoryList } from './ExerciseCardHistoryList';
import { ExerciseCardSection } from './ExerciseCardSection';
import { RegenerateCardForm } from './RegenerateCardModal';

/**
 * Render da seção de card ilustrado (PR4) pelo `react-dom/server`, sem jsdom:
 * o cache do React Query é pré-carregado (card publicado, listagem e o
 * recurso da geração), então o SSR já mostra cada estado. Os dados saem da
 * fixture de contrato (gerada pelo `serialize` real do backend).
 */

const EXERCISE_ID = 1;
const DETAIL = EXERCISE_CARD_CONTRACT.detail_response.generation;
const PUBLISHED_CARD = EXERCISE_CARD_CONTRACT.card_response.exercise_card as PublishedExerciseCard;
const PUBLISHED_GENERATION = EXERCISE_CARD_CONTRACT.approve_response.generation;
const LIST = EXERCISE_CARD_CONTRACT.list_response.items;

const generation = (overrides: Partial<ExerciseCardGeneration>): ExerciseCardGeneration => ({ ...DETAIL, ...overrides });

function render({
  isAdmin = true,
  enabled = true,
  published = null,
  items = [],
}: {
  isAdmin?: boolean;
  enabled?: boolean;
  published?: PublishedExerciseCard | null;
  items?: ExerciseCardGeneration[];
}): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(exerciseCardQueryKey(EXERCISE_ID), { exercise_id: EXERCISE_ID, exercise_card: published });
  client.setQueryData(exerciseCardGenerationsQueryKey(EXERCISE_ID), { items });
  for (const item of items) {
    client.setQueryData(exerciseCardGenerationQueryKey(EXERCISE_ID, item.id), { generation: item });
  }
  return renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client },
      createElement(
        MantineProvider,
        { theme },
        createElement(ExerciseCardSection, { exerciseId: EXERCISE_ID, exerciseName: 'Supino reto na máquina', isAdmin, enabled }),
      ),
    ),
  );
}

/** Tag de abertura do `<button>` que contém o rótulo. */
function buttonTag(html: string, label: string): string {
  const index = html.indexOf(`>${label}<`);
  expect(index, `botão "${label}" não encontrado`).toBeGreaterThan(-1);
  const start = html.lastIndexOf('<button', index);
  return html.slice(start, html.indexOf('>', start) + 1);
}

function isDisabled(html: string, label: string): boolean {
  return /\sdisabled(=|\s|>)/.test(buttonTag(html, label));
}

function state(html: string): string | null {
  return html.match(/data-card-state="([a-z]+)"/)?.[1] ?? null;
}

beforeEach(() => {
  // "Agora" = 15 s depois do início da geração do detalhe (relógio do contador).
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-04T01:04:01Z'));
});

afterEach(() => {
  vi.useRealTimers();
});

describe('ExerciseCardSection (SSR) — estados', () => {
  it('flag desligada: "Recurso desativado neste ambiente" e nenhum botão', () => {
    const html = render({ enabled: false, published: PUBLISHED_CARD, items: [DETAIL] });
    expect(state(html)).toBe('disabled');
    expect(html).toContain('Recurso desativado neste ambiente');
    expect(html).not.toContain('<button');
  });

  it('sem card: botão "Gerar card do exercício" habilitado para admin', () => {
    const html = render({});
    expect(state(html)).toBe('empty');
    expect(html).toContain('sem card publicado');
    expect(html).toContain('ainda não tem card ilustrado');
    expect(isDisabled(html, 'Gerar card do exercício')).toBe(false);
  });

  it('gerando: skeleton 2:3, segundos decorridos, tempo típico e botão travado', () => {
    const html = render({ items: [generation({ status: 'GENERATING', preview_url: null, preview_expires_at: null })] });
    expect(state(html)).toBe('generating');
    expect(html).toContain('mantine-Skeleton-root');
    expect(html).toContain('aspect-ratio:2 / 3');
    expect(html).toContain('Gerando o card…');
    expect(html).toContain('data-elapsed-seconds="15"');
    expect(html).toContain('15 s decorridos');
    expect(html).toContain('Costuma levar ~20 s (até 2 min).');
    expect(isDisabled(html, 'Gerar card do exercício')).toBe(true);
  });

  it('na fila (PENDING da listagem real), com instrução e card publicado em uso: "Gerar nova versão" travado', () => {
    const html = render({ published: PUBLISHED_CARD, items: LIST });
    expect(state(html)).toBe('generating');
    expect(html).toContain('Na fila para gerar…');
    expect(html).toContain('Antebraços nas almofadas e pés nos degraus na posição inicial.');
    expect(html).toContain('Card em uso agora');
    expect(html).toContain('Card publicado · v2');
    expect(isDisabled(html, 'Gerar nova versão')).toBe(true);
  });

  it('GENERATED: preview do card final + aviso de revisão + aprovar / gerar novamente / descartar', () => {
    const html = render({ items: [DETAIL] });
    expect(state(html)).toBe('review');
    expect(html).toContain('Revise a execução ilustrada antes de publicar.');
    expect(html).toContain(`src="${DETAIL.preview_url!.replace(/&/g, '&amp;')}"`);
    expect(html).toContain('aria-label="Ampliar Card ilustrado — Supino reto na máquina"');
    expect(html).toContain('Aguardando revisão');
    expect(html).toContain('gpt-image-1.5 · MUSCLEFIT_EXERCISE_CARD_V2_ILLUSTRATION');
    expect(html).toContain('Custo da geração: US$ 0,05');
    for (const label of ['Aprovar e publicar', 'Gerar novamente', 'Descartar']) {
      expect(isDisabled(html, label)).toBe(false);
    }
    expect(html).not.toContain('Gerar card do exercício');
  });

  it('GENERATED depois de uma aprovação que falhou: avisa o motivo', () => {
    const html = render({ items: [generation({ error_code: 'S3_UPLOAD_FAILED' })] });
    expect(html).toContain('A última tentativa de publicar falhou');
    expect(html).toContain(EXERCISE_CARD_FAILURE_MESSAGES.S3_UPLOAD_FAILED);
  });

  it('GENERATED sem preview assinado: "Recarregar prévia"', () => {
    const html = render({ items: [generation({ preview_url: null, preview_expires_at: null })] });
    expect(html).toContain('Prévia indisponível');
    expect(html).toContain('Recarregar prévia');
  });

  it('GENERATED com rascunho expirado (M72): aviso no lugar da prévia, sem "Aprovar", com gerar de novo / descartar', () => {
    const html = render({
      items: [generation({ draft_expired: true, preview_url: null, preview_expires_at: null })],
    });
    expect(state(html)).toBe('review');
    expect(html).toContain('data-draft-expired');
    expect(html).toContain('Rascunho expirado — gere de novo');
    expect(html).not.toContain('Prévia indisponível');
    expect(html).not.toContain('Recarregar prévia');
    expect(html).not.toContain('Revise a execução ilustrada antes de publicar.');
    expect(html).not.toContain('Aprovar e publicar');
    expect(isDisabled(html, 'Gerar novamente')).toBe(false);
    expect(isDisabled(html, 'Descartar')).toBe(false);
  });

  it('GENERATED que voltou de um 410 (error_code DRAFT_EXPIRED + flag): não repete "a última tentativa falhou"', () => {
    const html = render({
      items: [generation({ draft_expired: true, error_code: 'DRAFT_EXPIRED', preview_url: null, preview_expires_at: null })],
    });
    expect(html).toContain('Rascunho expirado — gere de novo');
    expect(html).not.toContain('A última tentativa de publicar falhou');
  });

  it('APPROVING: publicando, com todos os botões travados', () => {
    const html = render({ items: [generation({ status: 'APPROVING' })] });
    expect(state(html)).toBe('approving');
    expect(html).toContain('Publicando o card…');
    for (const label of ['Aprovar e publicar', 'Gerar novamente', 'Descartar']) {
      expect(isDisabled(html, label)).toBe(true);
    }
  });

  it('PUBLISHED: thumbnail, badge "Card publicado · vN" e "Gerar nova versão"', () => {
    const html = render({ published: PUBLISHED_CARD, items: [PUBLISHED_GENERATION] });
    expect(state(html)).toBe('published');
    expect(html).toContain('Card publicado · v2');
    expect(html).toContain(`src="${PUBLISHED_CARD.thumbnail_url}"`);
    expect(html).toContain(`href="${PUBLISHED_CARD.png_url}"`);
    expect(html).toContain('Versão 2 em uso');
    expect(isDisabled(html, 'Gerar nova versão')).toBe(false);
  });

  it('FAILED: alerta com a mensagem do error_code e "Tentar novamente"', () => {
    const failed = LIST.find((item) => item.status === 'FAILED')!;
    const html = render({ published: PUBLISHED_CARD, items: [failed] });
    expect(state(html)).toBe('failed');
    expect(html).toContain('A geração falhou');
    expect(html).toContain(EXERCISE_CARD_FAILURE_MESSAGES.OPENAI_TIMEOUT);
    expect(html).toContain('Card em uso agora');
    expect(isDisabled(html, 'Tentar novamente')).toBe(false);
    expect(isDisabled(html, 'Gerar com outra instrução')).toBe(false);
  });

  it('descarte com motivo: a próxima geração avisa que leva o motivo junto', () => {
    const html = render({ items: [generation({ status: 'REJECTED', reject_reason: 'Equipamento errado', preview_url: null })] });
    expect(state(html)).toBe('empty');
    expect(html).toContain('leva em conta o motivo do último descarte: “Equipamento errado”');
  });

  it('histórico colapsável aparece para o admin', () => {
    const html = render({ published: PUBLISHED_CARD, items: LIST });
    expect(html).toContain('Histórico de gerações (5)');
    expect(html).toContain('Custo total: US$ 0,15');
  });
});

describe('ExerciseCardSection (SSR) — não-admin', () => {
  it('vê o card publicado, sem histórico, com "Gerar nova versão" desabilitado', () => {
    const html = render({ isAdmin: false, published: PUBLISHED_CARD, items: LIST });
    expect(state(html)).toBe('published');
    expect(html).toContain('Card publicado · v2');
    expect(html).not.toContain('Histórico de gerações');
    expect(html).not.toContain('Na fila');
    expect(isDisabled(html, 'Gerar nova versão')).toBe(true);
  });

  it('sem card: "Gerar card do exercício" desabilitado', () => {
    const html = render({ isAdmin: false });
    expect(state(html)).toBe('empty');
    expect(isDisabled(html, 'Gerar card do exercício')).toBe(true);
  });
});

describe('ExerciseCardHistoryList (SSR)', () => {
  const html = renderToStaticMarkup(
    createElement(MantineProvider, { theme }, createElement(ExerciseCardHistoryList, { items: LIST, defaultOpened: true })),
  );

  it('versão/id, status, quem, quando, instrução, motivo, custo e falha', () => {
    for (const id of [11, 10, 9, 7, 6]) expect(html).toContain(`data-card-generation="${id}"`);
    expect(html).toContain('>v2<');
    expect(html).toContain('>#11<');
    expect(html).toContain('Na fila');
    expect(html).toContain('Publicado');
    expect(html).toContain('Falhou');
    expect(html).toContain('Descartado');
    expect(html).toContain('por usuário #1');
    expect(html).toContain('US$ 0,05');
    expect(html).toContain('a partir de #10');
    expect(html).toContain('Instrução: “Mostrar os pés apoiados nos degraus na posição inicial.”');
    expect(html).toContain('Motivo: Substituída por uma nova geração');
    expect(html).toContain(EXERCISE_CARD_FAILURE_MESSAGES.OPENAI_TIMEOUT);
  });

  it('sem itens não renderiza nada', () => {
    const empty = renderToStaticMarkup(
      createElement(MantineProvider, { theme }, createElement(ExerciseCardHistoryList, { items: [] })),
    );
    expect(empty).not.toContain('data-card-history');
  });
});

describe('RegenerateCardForm (SSR)', () => {
  it('"O que gostaria de alterar?" com contador até 300', () => {
    const html = renderToStaticMarkup(
      createElement(
        MantineProvider,
        { theme },
        createElement(RegenerateCardForm, {
          description: 'Esta prévia vai para o histórico.',
          confirmLabel: 'Gerar novamente',
          onCancel: () => undefined,
          onConfirm: () => undefined,
          loading: false,
          error: null,
        }),
      ),
    );
    expect(html).toContain('O que gostaria de alterar?');
    expect(html).toContain('maxLength="300"');
    expect(html).toContain('0/300');
    expect(isDisabled(html, 'Gerar novamente')).toBe(false);
  });
});

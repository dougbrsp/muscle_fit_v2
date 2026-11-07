import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  EXERCISE_CARD_API_ERROR_MESSAGES,
  exerciseCardReferencesApi,
  exerciseCardReferencesQueryKey,
} from '@/lib/exercise-cards';
import { EXERCISE_CARD_REFERENCES_CONTRACT } from '@/lib/exercise-cards-fixtures';
import type { ExerciseCardReference, ExerciseCardReferenceLimits } from '@/lib/exercise-cards-types';
import { theme } from '@/theme';
import {
  canUploadReference,
  ExerciseCardReferences,
  ExerciseCardReferencesView,
  isReferenceBlobTooLarge,
  type ExerciseCardReferencesViewProps,
} from './ExerciseCardReferences';

/**
 * Render por `react-dom/server` (sem jsdom): o cache do React Query é
 * pré-carregado com a fixture de contrato (saída do `serialize_reference` e do
 * `limits()` reais), e a parte visual é testada direto pela `View`.
 */

const EXERCISE_ID = 3;
const CONTRACT = EXERCISE_CARD_REFERENCES_CONTRACT.list_response;
const LIMITS: ExerciseCardReferenceLimits = CONTRACT.limits;

function wrap(node: ReturnType<typeof createElement>, client = new QueryClient()): string {
  return renderToStaticMarkup(
    createElement(QueryClientProvider, { client }, createElement(MantineProvider, { theme }, node)),
  );
}

function renderContainer(items: ExerciseCardReference[]): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(exerciseCardReferencesQueryKey(EXERCISE_ID), { items, limits: LIMITS });
  return wrap(createElement(ExerciseCardReferences, { exerciseId: EXERCISE_ID }), client);
}

const noop = () => undefined;
function renderView(overrides: Partial<ExerciseCardReferencesViewProps>): string {
  return wrap(
    createElement(ExerciseCardReferencesView, {
      items: [],
      limits: LIMITS,
      loading: false,
      loadError: null,
      note: '',
      uploading: false,
      actionError: null,
      onNoteChange: noop,
      onPick: noop,
      onAskDelete: noop,
      onRetry: noop,
      ...overrides,
    }),
  );
}

const manyItems = (count: number): ExerciseCardReference[] =>
  Array.from({ length: count }, (_, index) => ({ ...CONTRACT.items[0], id: 100 + index }));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ExerciseCardReferences (SSR)', () => {
  it('lista com miniatura (url pré-assinada), placeholder sem url e nota; texto explicativo', () => {
    const html = renderContainer(CONTRACT.items);
    expect(html).toContain('Fotos de referência');
    expect(html).toContain('geometria do aparelho');
    expect(html).toContain('rosto');
    expect(html).toContain('data-reference-id="7"');
    expect(html).toContain('data-reference-id="8"');
    expect(html).toContain(CONTRACT.items[0].url as string);
    expect(html).toContain('Banco a 30 graus');
    expect(html).toContain('miniatura indisponível');
    expect(html).toContain('aria-label="Excluir foto de referência 7"');
  });

  it('vazio: mensagem e botão de enviar; nota limitada por limits.note_max_chars', () => {
    const html = renderContainer([]);
    expect(html).toContain('data-card-references-empty');
    expect(html).toContain('vai para a IA junto com a foto');
    expect(html).toContain('Enviar foto de referência');
    expect(html).toContain(`maxLength="${LIMITS.note_max_chars}"`);
    expect(html).toContain(`accept="${(LIMITS.accepted_types ?? []).join(',')}"`);
  });

  it('carregando: skeleton e sem botão de enviar; erro de carga com "Tentar de novo"', () => {
    const loading = renderView({ loading: true, limits: null });
    expect(loading).toContain('data-card-references-loading');
    expect(loading).not.toContain('Enviar foto de referência');
    const failed = renderView({ limits: null, loadError: new Error('x') });
    expect(failed).toContain('data-card-references-error');
    expect(failed).toContain('Não foi possível carregar as fotos de referência.');
    expect(failed).toContain('Tentar de novo');
  });

  it('limite atingido (limits.max_active) esconde o envio e avisa', () => {
    const html = renderContainer(manyItems(LIMITS.max_active));
    expect(html).toContain('data-card-references-full');
    expect(html).not.toContain('Enviar foto de referência');
    expect(html).not.toContain('data-card-references-upload');
    expect(canUploadReference(LIMITS.max_active - 1, LIMITS)).toBe(true);
    expect(canUploadReference(LIMITS.max_active, LIMITS)).toBe(false);
  });

  it('enviando: botão desabilitado com rótulo "Enviando..."', () => {
    const html = renderView({ uploading: true });
    expect(html).toContain('Enviando...');
  });

  it('erro do envio (409 limite, 400 tipo) aparece no alerta com a mensagem em PT', () => {
    for (const code of ['exercise_card_reference_limit', 'reference_unsupported_type'] as const) {
      const html = renderView({ actionError: EXERCISE_CARD_API_ERROR_MESSAGES[code] });
      expect(html).toContain('data-card-references-action-error');
      expect(html).toContain(EXERCISE_CARD_API_ERROR_MESSAGES[code]);
    }
  });

  it('foto reduzida acima de limits.max_upload_bytes é recusada localmente', () => {
    expect(isReferenceBlobTooLarge(LIMITS.max_upload_bytes, LIMITS)).toBe(false);
    expect(isReferenceBlobTooLarge(LIMITS.max_upload_bytes + 1, LIMITS)).toBe(true);
  });
});

describe('exclusão', () => {
  it('chama DELETE na rota da foto e aceita 204 sem corpo', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    await exerciseCardReferencesApi.remove(EXERCISE_ID, 7);
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/exercises\/3\/card-references\/7$/);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'DELETE' });
  });

  it('foto inexistente (404) vira a mensagem em PT', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: { error: 'exercise_card_reference_not_found', message: 'x' } }), {
          status: 404,
        }),
      ),
    );
    await expect(exerciseCardReferencesApi.remove(EXERCISE_ID, 7)).rejects.toThrow(
      EXERCISE_CARD_API_ERROR_MESSAGES.exercise_card_reference_not_found,
    );
  });
});

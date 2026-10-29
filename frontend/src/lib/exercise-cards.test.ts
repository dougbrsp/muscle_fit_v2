import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import {
  deriveExerciseCardView,
  elapsedSeconds,
  EXERCISE_CARD_API_ERROR_MESSAGES,
  EXERCISE_CARD_FAILURE_MESSAGES,
  EXERCISE_CARD_POLL_INTERVAL_MS,
  EXERCISE_CARD_PREVIEW_REFRESH_MS,
  ExerciseCardApiError,
  exerciseCardsFeatureEnabled,
  formatCostUsd,
  formatElapsed,
  generationFailureMessage,
  generationLabel,
  generationRefetchInterval,
  IdempotencyKeyKeeper,
  isDraftExpired,
  isLiveStatus,
  newIdempotencyKey,
  parentForNewGeneration,
  rejectReasonLabel,
  toExerciseCardError,
  totalCostUsd,
} from './exercise-cards';
import { EXERCISE_CARD_CONTRACT } from './exercise-cards-fixtures';
import type { ExerciseCardGeneration } from './exercise-cards-types';

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const detail = EXERCISE_CARD_CONTRACT.detail_response.generation;
const generation = (overrides: Partial<ExerciseCardGeneration>): ExerciseCardGeneration => ({ ...detail, ...overrides });

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Contrato (fixture gerada pelo `serialize` real do backend)
// ---------------------------------------------------------------------------

/** Campos de `exercise_card_service.serialize` (sem `prompt_text`, que só vem no detalhe). */
const GENERATION_FIELDS = [
  'id',
  'exercise_id',
  'card_type',
  'status',
  'version',
  'parent_generation_id',
  'idempotency_key',
  'job_id',
  'provider',
  'model',
  'quality',
  'size',
  'prompt_version',
  'text_level',
  'request_instruction',
  'reject_reason',
  'preview_url',
  'preview_expires_at',
  'draft_expires_at',
  'draft_expired',
  'card',
  'width',
  'height',
  'bytes',
  'tokens_in',
  'tokens_out',
  'cost_usd',
  'error_code',
  'error_message',
  'created_by',
  'approved_by',
  'created_at',
  'updated_at',
  'started_at',
  'generated_at',
  'approved_at',
  'published_at',
  'rejected_at',
  'failed_at',
].sort();

describe('contrato com o backend (exercise_card_service.serialize)', () => {
  it('detalhe e criação trazem prompt_text; a listagem não', () => {
    const withPrompt = [...GENERATION_FIELDS, 'prompt_text'].sort();
    expect(Object.keys(EXERCISE_CARD_CONTRACT.detail_response.generation).sort()).toEqual(withPrompt);
    expect(Object.keys(EXERCISE_CARD_CONTRACT.create_response.generation).sort()).toEqual(withPrompt);
    for (const item of EXERCISE_CARD_CONTRACT.list_response.items) {
      expect(Object.keys(item).sort()).toEqual(GENERATION_FIELDS);
    }
  });

  it('preview só em GENERATED; card só em PUBLISHED; listagem do mais recente para o mais antigo', () => {
    expect(detail.status).toBe('GENERATED');
    expect(detail.preview_url).toMatch(/^https:\/\/.+exercise-card-drafts\/10\/preview\.png\?/);
    expect(detail.preview_expires_at).toBe('2026-10-04T01:19:10Z');
    const items = EXERCISE_CARD_CONTRACT.list_response.items;
    expect(items.map((item) => item.id)).toEqual([11, 10, 9, 7, 6]);
    for (const item of items) {
      expect(item.preview_url).toBeNull();
      expect(item.card != null).toBe(item.status === 'PUBLISHED');
    }
  });

  it('card publicado e approve trazem as 3 URLs, a versão e a data', () => {
    const card = EXERCISE_CARD_CONTRACT.card_response.exercise_card;
    expect(card).toEqual(EXERCISE_CARD_CONTRACT.approve_response.exercise_card);
    expect(card?.url).toMatch(/\/exercise-cards\/1\/v2\/card\.webp$/);
    expect(card?.thumbnail_url).toMatch(/\/thumb\.webp$/);
    expect(card?.png_url).toMatch(/\/card\.png$/);
    expect(card?.version).toBe(2);
    expect(EXERCISE_CARD_CONTRACT.card_response_empty.exercise_card).toBeNull();
  });

  it('regenerar devolve a filha e o pai (GENERATED → REJECTED "regenerated")', () => {
    const { generation: child, parent } = EXERCISE_CARD_CONTRACT.regenerate_response;
    expect(child.parent_generation_id).toBe(parent?.id);
    expect(parent?.status).toBe('REJECTED');
    expect(rejectReasonLabel(parent?.reject_reason)).toBe('Substituída por uma nova geração');
  });

  it('erro de negócio: o detail do backend vira código + mensagem da tela', () => {
    const raw = new ApiError('Erro ao consultar (HTTP 409).', 409, EXERCISE_CARD_CONTRACT.error_response.detail);
    const error = toExerciseCardError(raw) as ExerciseCardApiError;
    expect(error).toBeInstanceOf(ExerciseCardApiError);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.code).toBe('exercise_card_generation_in_progress');
    expect(error.generationId).toBe(11);
    expect(error.message).toBe(EXERCISE_CARD_API_ERROR_MESSAGES.exercise_card_generation_in_progress);
  });
});

// ---------------------------------------------------------------------------
// Erros
// ---------------------------------------------------------------------------

/** `exercise_card_service.CODE_*` com o status HTTP de cada um. */
const BACKEND_API_CODES: Array<[string, number]> = [
  ['exercise_card_generation_disabled', 503],
  ['exercise_not_found', 404],
  ['exercise_card_generation_not_found', 404],
  ['exercise_card_generation_in_progress', 409],
  ['not_reviewable', 409],
  ['not_regenerable', 409],
  ['idempotency_key_conflict', 409],
  ['exercise_card_daily_limit', 429],
  ['exercise_card_global_in_flight_limit', 429],
  ['invalid_idempotency_key', 422],
  ['invalid_text_level', 422],
  ['instruction_too_long', 422],
  ['reason_too_long', 422],
  ['image_processing_failed', 502],
  ['s3_upload_failed', 502],
  ['s3_read_failed', 502],
  ['exercise_card_dispatch_failed', 502],
  ['DRAFT_EXPIRED', 410],
];

/** `error_code` que o backend grava na geração (worker, storage, processador, sweep). */
const BACKEND_FAILURE_CODES = [
  'OPENAI_TIMEOUT',
  'OPENAI_RATE_LIMIT',
  'OPENAI_INVALID_REQUEST',
  'OPENAI_GENERATION_FAILED',
  'CARD_COMPOSITION_FAILED',
  'WORKER_LOST',
  'PUBLISH_INTERRUPTED',
  'DISPATCH_FAILED',
  'EXERCISE_NOT_FOUND',
  'PROMPT_INVALID',
  'INTERNAL_ERROR',
  'S3_UPLOAD_FAILED',
  'S3_READ_FAILED',
  'IMAGE_PROCESSING_FAILED',
  'DRAFT_EXPIRED',
];

describe('mensagens por código de erro', () => {
  it.each(BACKEND_API_CODES)('%s (%i) tem mensagem própria em PT', (code, status) => {
    const error = toExerciseCardError(
      new ApiError('genérica', status, { error: code, message: 'texto do backend' }),
    ) as ExerciseCardApiError;
    expect(error.code).toBe(code);
    expect(error.status).toBe(status);
    expect(error.message).toBe(EXERCISE_CARD_API_ERROR_MESSAGES[code]);
    expect(error.message).not.toBe('genérica');
  });

  it('não sobra mensagem para código que o backend não tem', () => {
    expect(Object.keys(EXERCISE_CARD_API_ERROR_MESSAGES).sort()).toEqual(BACKEND_API_CODES.map(([code]) => code).sort());
    expect(Object.keys(EXERCISE_CARD_FAILURE_MESSAGES).sort()).toEqual([...BACKEND_FAILURE_CODES].sort());
  });

  it('código desconhecido usa a mensagem do backend; 403 sem detail vira "restrita"; rede mantém a do apiFetch', () => {
    const unknown = toExerciseCardError(new ApiError('genérica', 409, { error: 'novo_codigo', message: 'Mensagem nova.' }));
    expect((unknown as Error).message).toBe('Mensagem nova.');
    expect((toExerciseCardError(new ApiError('Forbidden', 403)) as Error).message).toBe('Ação restrita a administradores.');
    const network = toExerciseCardError(new ApiError('Não foi possível conectar à API.', 0)) as ExerciseCardApiError;
    expect(network.message).toBe('Não foi possível conectar à API.');
    expect(network.code).toBeNull();
    const plain = new Error('x');
    expect(toExerciseCardError(plain)).toBe(plain);
  });

  it.each(BACKEND_FAILURE_CODES)('falha %s tem mensagem própria', (code) => {
    expect(generationFailureMessage(code)).toBe(EXERCISE_CARD_FAILURE_MESSAGES[code]);
  });

  it('falha sem código conhecido tem mensagem genérica', () => {
    expect(generationFailureMessage(null)).toBe('A geração falhou por um motivo desconhecido.');
    expect(generationFailureMessage('XYZ')).toBe('A geração falhou por um motivo desconhecido.');
  });
});

// ---------------------------------------------------------------------------
// Idempotência
// ---------------------------------------------------------------------------

describe('idempotency_key', () => {
  it('newIdempotencyKey gera UUID v4 (randomUUID ou, fora de contexto seguro, getRandomValues)', () => {
    expect(newIdempotencyKey()).toMatch(UUID_V4);
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.fill(0xab);
        return bytes;
      },
    });
    const fallback = newIdempotencyKey();
    expect(fallback).toMatch(UUID_V4);
    expect(fallback).toBe('abababab-abab-4bab-abab-abababababab');
  });

  it('mesma chave enquanto o resultado é incerto (rede, 5xx sem código); nova depois de resposta definitiva', () => {
    let n = 0;
    const keeper = new IdempotencyKeyKeeper(() => `k${(n += 1)}`);
    expect(keeper.keyFor('new|')).toBe('k1');
    keeper.settle(new ApiError('rede', 0));
    expect(keeper.keyFor('new|')).toBe('k1');
    keeper.settle(new ApiError('gateway', 504));
    expect(keeper.keyFor('new|')).toBe('k1');
    keeper.settle(toExerciseCardError(new ApiError('x', 409, { error: 'exercise_card_generation_in_progress', message: '' })));
    expect(keeper.keyFor('new|')).toBe('k2');
    keeper.settle();
    expect(keeper.keyFor('new|')).toBe('k3');
    // Outro pedido (outro pai ou outra instrução) nunca reaproveita a chave.
    expect(keeper.keyFor('10|mais aberto')).toBe('k4');
    keeper.settle(toExerciseCardError(new ApiError('x', 502, { error: 'exercise_card_dispatch_failed', message: '' })));
    expect(keeper.keyFor('10|mais aberto')).toBe('k5');
  });
});

// ---------------------------------------------------------------------------
// Estado da seção
// ---------------------------------------------------------------------------

describe('deriveExerciseCardView', () => {
  const published = EXERCISE_CARD_CONTRACT.card_response.exercise_card;

  it('flag desligada manda em tudo', () => {
    expect(deriveExerciseCardView({ enabled: false, current: detail, published })).toBe('disabled');
  });

  it.each([
    ['PENDING', 'generating'],
    ['GENERATING', 'generating'],
    ['APPROVING', 'approving'],
    ['GENERATED', 'review'],
    ['FAILED', 'failed'],
  ] as const)('última geração %s → %s', (status, view) => {
    expect(deriveExerciseCardView({ enabled: true, current: generation({ status }), published })).toBe(view);
  });

  it.each(['PUBLISHED', 'REJECTED', 'SUPERSEDED'] as const)('última %s: card publicado ou vazio', (status) => {
    expect(deriveExerciseCardView({ enabled: true, current: generation({ status }), published })).toBe('published');
    expect(deriveExerciseCardView({ enabled: true, current: generation({ status }), published: null })).toBe('empty');
  });

  it('sem geração nenhuma', () => {
    expect(deriveExerciseCardView({ enabled: true, current: null, published: null })).toBe('empty');
    expect(deriveExerciseCardView({ enabled: true, current: null, published })).toBe('published');
  });
});

describe('helpers', () => {
  it('polling de 2,5 s só em PENDING/GENERATING/APPROVING; preview renovado antes dos 15 min', () => {
    for (const status of ['PENDING', 'GENERATING', 'APPROVING'] as const) {
      expect(isLiveStatus(status)).toBe(true);
      expect(generationRefetchInterval(generation({ status }))).toBe(EXERCISE_CARD_POLL_INTERVAL_MS);
    }
    expect(EXERCISE_CARD_POLL_INTERVAL_MS).toBe(2500);
    expect(generationRefetchInterval(detail)).toBe(EXERCISE_CARD_PREVIEW_REFRESH_MS);
    expect(EXERCISE_CARD_PREVIEW_REFRESH_MS).toBeLessThan(15 * 60_000);
    expect(generationRefetchInterval(generation({ preview_url: null }))).toBe(false);
    for (const status of ['PUBLISHED', 'SUPERSEDED', 'REJECTED', 'FAILED'] as const) {
      expect(isLiveStatus(status)).toBe(false);
      expect(generationRefetchInterval(generation({ status, preview_url: null }))).toBe(false);
    }
    expect(generationRefetchInterval(null)).toBe(false);
  });

  it('rascunho expirado (M72): flag do backend, ou 410 DRAFT_EXPIRED da aprovação desta geração', () => {
    expect(detail.draft_expired).toBe(false);
    expect(detail.draft_expires_at).toBe('2026-10-11T01:04:06Z');
    expect(isDraftExpired(detail)).toBe(false);
    expect(isDraftExpired(generation({ draft_expired: true, preview_url: null }))).toBe(true);
    expect(isDraftExpired(generation({ status: 'APPROVING', draft_expired: true }))).toBe(true);
    // Fora da revisão a flag não importa (e o backend nem manda true).
    expect(isDraftExpired(generation({ status: 'REJECTED', draft_expired: true }))).toBe(false);
    expect(isDraftExpired(null)).toBe(false);

    const gone = toExerciseCardError(
      new ApiError('x', 410, { error: 'DRAFT_EXPIRED', message: 'm', generation_id: detail.id }),
    );
    expect((gone as Error).message).toBe('O rascunho expirou (mais de 7 dias). Gere o card de novo.');
    expect(isDraftExpired(detail, { error: gone, generationId: detail.id })).toBe(true);
    // 410 de OUTRA geração, ou outro erro, não marca esta.
    expect(isDraftExpired(detail, { error: gone, generationId: detail.id + 1 })).toBe(false);
    const readFailed = toExerciseCardError(new ApiError('x', 502, { error: 's3_read_failed', message: 'm' }));
    expect(isDraftExpired(detail, { error: readFailed, generationId: detail.id })).toBe(false);
  });

  it('descarte com motivo escrito vira pai da próxima geração; "regenerated" e falha não', () => {
    expect(parentForNewGeneration(generation({ id: 6, status: 'REJECTED', reject_reason: 'Equipamento errado' }))).toBe(6);
    expect(parentForNewGeneration(generation({ id: 6, status: 'REJECTED', reject_reason: 'regenerated' }))).toBeNull();
    expect(parentForNewGeneration(generation({ id: 6, status: 'REJECTED', reject_reason: '  ' }))).toBeNull();
    expect(parentForNewGeneration(generation({ id: 6, status: 'FAILED', reject_reason: null }))).toBeNull();
    expect(parentForNewGeneration(null)).toBeNull();
    expect(rejectReasonLabel(' Equipamento errado ')).toBe('Equipamento errado');
    expect(rejectReasonLabel(null)).toBeNull();
  });

  it('segundos decorridos pelo relógio do servidor, nunca negativos', () => {
    const start = Date.parse('2026-10-04T01:03:46Z');
    expect(elapsedSeconds(detail, start + 19_900)).toBe(19);
    expect(elapsedSeconds(detail, start - 5_000)).toBe(0);
    expect(elapsedSeconds(generation({ started_at: null }), Date.parse('2026-10-04T01:03:55Z'))).toBe(10);
    expect(formatElapsed(7)).toBe('7 s');
    expect(formatElapsed(125)).toBe('2 min 05 s');
  });

  it('custo, rótulo e flag', () => {
    expect(formatCostUsd(0.05)).toBe('US$ 0,05');
    expect(formatCostUsd(null)).toBeNull();
    expect(totalCostUsd(EXERCISE_CARD_CONTRACT.list_response.items)).toBeCloseTo(0.15);
    expect(generationLabel({ id: 7, version: 2 })).toBe('v2');
    expect(generationLabel({ id: 11, version: null })).toBe('#11');
    expect(exerciseCardsFeatureEnabled({ features: { exercise_cards: true } })).toBe(true);
    expect(exerciseCardsFeatureEnabled({ features: { exercise_cards: false } })).toBe(false);
    expect(exerciseCardsFeatureEnabled({})).toBe(false);
    expect(exerciseCardsFeatureEnabled(undefined)).toBe(false);
  });
});

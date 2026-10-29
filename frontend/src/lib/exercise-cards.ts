import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { apiFetch, ApiError } from './api';
import type { SessionUser } from './auth';
import type {
  ExerciseCardApproveResponse,
  ExerciseCardCreatePayload,
  ExerciseCardErrorDetail,
  ExerciseCardGeneration,
  ExerciseCardGenerationListResponse,
  ExerciseCardGenerationResponse,
  ExerciseCardGenerationStatus,
  ExerciseCardRegenerateResponse,
  ExerciseCardRejectPayload,
  ExerciseCardResponse,
  PublishedExerciseCard,
} from './exercise-cards-types';

/**
 * Cards ilustrados de exercícios — helpers puros + hooks React Query da seção
 * de admin (`dominio-exercicios/components/ExerciseCardSection.tsx`).
 * Contrato em `exercise-cards-types.ts`.
 *
 * Polling: SEMPRE pelo recurso (`GET /exercises/{id}/card-generations/{gid}`),
 * nunca por `GET /jobs/{id}` (que não valida o dono). O backend fecha sozinho
 * o que morreu no meio (sweep: PENDING/GENERATING > 10 min → FAILED
 * `WORKER_LOST`; APPROVING > 2 min → GENERATED), então o polling sempre termina.
 */

// ---------------------------------------------------------------------------
// Constantes
// ---------------------------------------------------------------------------

export const EXERCISE_CARD_POLL_INTERVAL_MS = 2500;
/** Mesmos tetos do backend (`exercise_card_prompt.MAX_INSTRUCTION_CHARS`/`MAX_REJECT_REASON_CHARS`). */
export const EXERCISE_CARD_INSTRUCTION_MAX_CHARS = 300;
export const EXERCISE_CARD_REASON_MAX_CHARS = 160;
export const EXERCISE_CARD_HISTORY_LIMIT = 20;
/** Medido no checkpoint (rodada 2): p50 ~20 s; a OpenAI documenta até 2 min. */
export const EXERCISE_CARD_TYPICAL_SECONDS = 20;
export const EXERCISE_CARD_SLOW_AFTER_SECONDS = 120;
/**
 * O `preview_url` é pré-assinado por 15 min a cada GET. Refaz o GET um pouco
 * antes disso, contado a partir da última resposta (não do `preview_expires_at`,
 * para não depender do relógio do navegador bater com o do servidor).
 */
export const EXERCISE_CARD_PREVIEW_REFRESH_MS = 14 * 60_000;

/** Reject reason gravado pelo backend no pai quando o admin regenera — código, não texto humano. */
export const REJECT_REASON_REGENERATED = 'regenerated';

const LIVE_STATUSES: ReadonlySet<ExerciseCardGenerationStatus> = new Set(['PENDING', 'GENERATING', 'APPROVING']);

export function isLiveStatus(status: ExerciseCardGenerationStatus | null | undefined): boolean {
  return status != null && LIVE_STATUSES.has(status);
}

export const EXERCISE_CARD_STATUS_BADGES: Record<ExerciseCardGenerationStatus, { label: string; color: string }> = {
  PENDING: { label: 'Na fila', color: 'gray' },
  GENERATING: { label: 'Gerando', color: 'blue' },
  GENERATED: { label: 'Aguardando revisão', color: 'yellow' },
  APPROVING: { label: 'Publicando', color: 'blue' },
  PUBLISHED: { label: 'Publicado', color: 'teal' },
  SUPERSEDED: { label: 'Substituído', color: 'gray' },
  REJECTED: { label: 'Descartado', color: 'gray' },
  FAILED: { label: 'Falhou', color: 'red' },
};

// ---------------------------------------------------------------------------
// Feature flag
// ---------------------------------------------------------------------------

/** `features.exercise_cards` de `/auth/me`; ausente (backend antigo) = desligado. */
export function exerciseCardsFeatureEnabled(user: Pick<SessionUser, 'features'> | null | undefined): boolean {
  return user?.features?.exercise_cards === true;
}

// ---------------------------------------------------------------------------
// Idempotência
// ---------------------------------------------------------------------------

function formatUuid(bytes: Uint8Array): string {
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * UUID v4 para o `idempotency_key`. `crypto.randomUUID` só existe em contexto
 * seguro (https/localhost); o DEV aberto pelo IP da rede local não é — aí cai
 * no `getRandomValues`, que existe em qualquer contexto.
 */
export function newIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID();
  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  return formatUuid(bytes);
}

/**
 * True quando não dá para saber se o POST foi gravado: rede (status 0) ou 5xx
 * sem erro de negócio (ex.: 504 do API Gateway com a escrita já feita).
 */
export function isOutcomeUnknown(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  if (error.status === 0) return true;
  return error.status >= 500 && parseExerciseCardErrorDetail(error.detail) == null;
}

/**
 * Guarda a MESMA chave enquanto o pedido (mesma ação, mesmo alvo, mesma
 * instrução) não teve resposta definitiva: repetir um POST que estourou com a
 * mesma chave faz o backend devolver a geração existente (replay 200) em vez
 * de criar — e cobrar — outra. Resposta definitiva (2xx ou erro de negócio) →
 * a próxima tentativa nasce com chave nova.
 */
export class IdempotencyKeyKeeper {
  private current: { fingerprint: string; key: string } | null = null;

  constructor(private readonly generate: () => string = newIdempotencyKey) {}

  keyFor(fingerprint: string): string {
    if (!this.current || this.current.fingerprint !== fingerprint) {
      this.current = { fingerprint, key: this.generate() };
    }
    return this.current.key;
  }

  /** Chamar depois de cada resposta: sem erro ou com erro definitivo, descarta a chave. */
  settle(error?: unknown): void {
    if (error === undefined || !isOutcomeUnknown(error)) this.current = null;
  }
}

// ---------------------------------------------------------------------------
// Erros
// ---------------------------------------------------------------------------

/** Mensagens por `detail.error` das rotas (`exercise_card_service.CODE_*`). */
export const EXERCISE_CARD_API_ERROR_MESSAGES: Record<string, string> = {
  exercise_card_generation_disabled: 'A geração de cards ilustrados está desativada neste ambiente.',
  exercise_not_found: 'Exercício não encontrado — ele pode ter sido removido. Feche e abra a lista de novo.',
  exercise_card_generation_not_found: 'Esta geração não existe mais para este exercício. A seção foi atualizada.',
  exercise_card_generation_in_progress:
    'Já existe uma geração em andamento para este exercício — acompanhe o andamento abaixo.',
  not_reviewable:
    'Esta geração não está mais aguardando revisão (pode ter sido aprovada ou descartada em outra aba). A seção foi atualizada.',
  not_regenerable: 'Esta geração não pode ser refeita no estado atual. A seção foi atualizada; tente de novo.',
  idempotency_key_conflict: 'Conflito ao registrar o pedido. Tente de novo.',
  exercise_card_daily_limit: 'Você atingiu o limite diário de gerações de card. Tente de novo amanhã.',
  exercise_card_global_in_flight_limit:
    'Há muitas gerações de card em andamento agora. Aguarde alguma terminar e tente de novo.',
  invalid_idempotency_key: 'Pedido inválido (chave de idempotência). Recarregue a página e tente de novo.',
  invalid_text_level: 'Nível de texto do card não aceito pela versão atual.',
  instruction_too_long: `A instrução passou de ${EXERCISE_CARD_INSTRUCTION_MAX_CHARS} caracteres. Encurte e tente de novo.`,
  reason_too_long: `O motivo passou de ${EXERCISE_CARD_REASON_MAX_CHARS} caracteres. Encurte e tente de novo.`,
  image_processing_failed:
    'A imagem foi recusada no processamento e nada foi publicado. A geração continua em revisão: aprove de novo ou gere outra.',
  s3_upload_failed: 'Falha ao gravar o card no armazenamento; nada foi publicado. Tente aprovar de novo.',
  s3_read_failed: 'Não foi possível ler a imagem gerada no armazenamento. Tente aprovar de novo em instantes.',
  exercise_card_dispatch_failed: 'Não foi possível acionar o gerador de imagens. Tente de novo em instantes.',
  DRAFT_EXPIRED: 'O rascunho expirou (mais de 7 dias). Gere o card de novo.',
};

/** Mensagens por `error_code` gravado na geração (worker / publicação). */
export const EXERCISE_CARD_FAILURE_MESSAGES: Record<string, string> = {
  OPENAI_TIMEOUT: 'A OpenAI demorou demais para responder e a geração foi interrompida.',
  OPENAI_RATE_LIMIT: 'A OpenAI recusou por excesso de pedidos (limite de taxa). Aguarde um minuto antes de tentar.',
  OPENAI_INVALID_REQUEST:
    'A OpenAI recusou o pedido (conteúdo ou configuração). Se havia uma instrução, tente reformulá-la.',
  OPENAI_GENERATION_FAILED: 'A OpenAI não conseguiu gerar a ilustração.',
  CARD_COMPOSITION_FAILED: 'A ilustração foi gerada, mas a montagem do card (logo, nome e músculos) falhou.',
  WORKER_LOST: 'A geração ficou mais de 10 minutos sem resposta e foi encerrada.',
  DISPATCH_FAILED: 'Não foi possível acionar o gerador de imagens.',
  EXERCISE_NOT_FOUND: 'O exercício não foi encontrado pelo gerador.',
  PROMPT_INVALID:
    'Não foi possível montar o pedido para a IA com os dados deste exercício. Revise nome, músculos e descrição de execução.',
  INTERNAL_ERROR: 'Erro interno no gerador.',
  S3_UPLOAD_FAILED: 'Falha ao gravar a imagem no armazenamento.',
  S3_READ_FAILED: 'Falha ao ler a imagem do armazenamento.',
  IMAGE_PROCESSING_FAILED: 'A imagem foi recusada no processamento.',
  PUBLISH_INTERRUPTED: 'A publicação foi interrompida no meio.',
  DRAFT_EXPIRED: 'O rascunho expirou (mais de 7 dias) e não pode mais ser publicado.',
};

/** `detail.error` / `error_code` do rascunho expirado (410 na aprovação). */
export const DRAFT_EXPIRED_CODE = 'DRAFT_EXPIRED';

export function generationFailureMessage(errorCode: string | null | undefined): string {
  return (errorCode && EXERCISE_CARD_FAILURE_MESSAGES[errorCode]) || 'A geração falhou por um motivo desconhecido.';
}

export function parseExerciseCardErrorDetail(detail: unknown): ExerciseCardErrorDetail | null {
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null;
  const { error, message, generation_id: generationId } = detail as Record<string, unknown>;
  if (typeof error !== 'string') return null;
  return {
    error,
    message: typeof message === 'string' ? message : '',
    ...(typeof generationId === 'number' ? { generation_id: generationId } : {}),
  };
}

/**
 * `ApiError` com a mensagem já traduzida para a tela + o código estável. Como
 * continua sendo `ApiError`, os helpers genéricos (`error.message`, 403 →
 * "Ação restrita") de `ConfirmDeleteModal` e afins funcionam sem adaptação.
 */
export class ExerciseCardApiError extends ApiError {
  code: string | null;
  generationId: number | null;

  constructor(message: string, status: number, detail: unknown, code: string | null, generationId: number | null) {
    super(message, status, detail);
    this.name = 'ExerciseCardApiError';
    this.code = code;
    this.generationId = generationId;
  }
}

export function exerciseCardErrorMessage(status: number, detail: ExerciseCardErrorDetail | null, fallback: string): string {
  if (detail) {
    const known = EXERCISE_CARD_API_ERROR_MESSAGES[detail.error];
    if (known) return known;
    if (detail.message) return detail.message;
  }
  if (status === 403) return 'Ação restrita a administradores.';
  return fallback;
}

export function toExerciseCardError(error: unknown): unknown {
  if (!(error instanceof ApiError) || error instanceof ExerciseCardApiError) return error;
  const detail = parseExerciseCardErrorDetail(error.detail);
  return new ExerciseCardApiError(
    exerciseCardErrorMessage(error.status, detail, error.message),
    error.status,
    error.detail,
    detail?.error ?? null,
    detail?.generation_id ?? null,
  );
}

export function exerciseCardErrorCode(error: unknown): string | null {
  return error instanceof ExerciseCardApiError ? error.code : null;
}

/** Texto para a tela a partir de qualquer erro das chamadas desta feature. */
export function exerciseCardErrorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

async function cardFetch<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    return await apiFetch<T>(path, init);
  } catch (error) {
    throw toExerciseCardError(error);
  }
}

// ---------------------------------------------------------------------------
// Estado da seção
// ---------------------------------------------------------------------------

export type ExerciseCardViewKind = 'disabled' | 'empty' | 'generating' | 'approving' | 'review' | 'failed' | 'published';

/**
 * O que a seção mostra. `current` = geração mais recente do exercício (só
 * admin enxerga gerações); `published` = card vigente (`GET /card`).
 */
export function deriveExerciseCardView({
  enabled,
  current,
  published,
}: {
  enabled: boolean;
  current: ExerciseCardGeneration | null;
  published: PublishedExerciseCard | null;
}): ExerciseCardViewKind {
  if (!enabled) return 'disabled';
  switch (current?.status) {
    case 'PENDING':
    case 'GENERATING':
      return 'generating';
    case 'APPROVING':
      return 'approving';
    case 'GENERATED':
      return 'review';
    case 'FAILED':
      return 'failed';
    default:
      return published ? 'published' : 'empty';
  }
}

/**
 * Rascunho da geração em revisão que não pode mais ser aprovado (M72): o
 * backend marca `draft_expired` (7 dias, a regra do lifecycle do S3), ou a
 * aprovação desta geração acabou de voltar 410 `DRAFT_EXPIRED` (corrida perto
 * do limite, antes do próximo GET trazer a flag).
 */
export function isDraftExpired(
  generation: Pick<ExerciseCardGeneration, 'id' | 'status' | 'draft_expired'> | null | undefined,
  approveError?: { error: unknown; generationId: number | undefined },
): boolean {
  if (!generation || (generation.status !== 'GENERATED' && generation.status !== 'APPROVING')) return false;
  if (generation.draft_expired) return true;
  return (
    approveError != null &&
    approveError.generationId === generation.id &&
    exerciseCardErrorCode(approveError.error) === DRAFT_EXPIRED_CODE
  );
}

export function isHumanRejectReason(reason: string | null | undefined): reason is string {
  const text = (reason ?? '').trim();
  return text !== '' && text !== REJECT_REASON_REGENERATED;
}

export function rejectReasonLabel(reason: string | null | undefined): string | null {
  if (reason === REJECT_REASON_REGENERATED) return 'Substituída por uma nova geração';
  return isHumanRejectReason(reason) ? reason.trim() : null;
}

/**
 * Pai da PRÓXIMA geração pedida pelo botão de gerar: se a última foi
 * descartada COM motivo escrito, regenerar a partir dela leva o motivo para o
 * prompt (o backend só usa motivo humano, nunca o código `regenerated`). Sem
 * isso, uma geração nova (sem pai).
 */
export function parentForNewGeneration(latest: ExerciseCardGeneration | null): number | null {
  return latest && latest.status === 'REJECTED' && isHumanRejectReason(latest.reject_reason) ? latest.id : null;
}

/** Intervalo de refetch do recurso da geração (React Query `refetchInterval`). */
export function generationRefetchInterval(generation: ExerciseCardGeneration | null | undefined): number | false {
  if (!generation) return false;
  if (isLiveStatus(generation.status)) return EXERCISE_CARD_POLL_INTERVAL_MS;
  if (generation.status === 'GENERATED' && generation.preview_url) return EXERCISE_CARD_PREVIEW_REFRESH_MS;
  return false;
}

/** Segundos desde o início (ou criação) da geração, pelo relógio do servidor; nunca negativo. */
export function elapsedSeconds(generation: ExerciseCardGeneration, nowMs: number): number {
  const start = Date.parse(generation.started_at ?? generation.created_at ?? '');
  if (!Number.isFinite(start)) return 0;
  return Math.max(0, Math.floor((nowMs - start) / 1000));
}

export function formatElapsed(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  if (safe < 60) return `${safe} s`;
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  return `${minutes} min ${String(rest).padStart(2, '0')} s`;
}

const COST_FORMAT = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 4 });

export function formatCostUsd(cost: number | null | undefined): string | null {
  if (cost == null || !Number.isFinite(cost)) return null;
  return `US$ ${COST_FORMAT.format(cost)}`;
}

export function totalCostUsd(items: readonly ExerciseCardGeneration[]): number {
  return items.reduce((sum, item) => sum + (typeof item.cost_usd === 'number' ? item.cost_usd : 0), 0);
}

export function formatCardDateTime(iso: string | null | undefined, timeZone?: string): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    ...(timeZone ? { timeZone } : {}),
  }).format(date);
}

/** Rótulo curto da geração no histórico: versão quando publicada, senão o id. */
export function generationLabel(generation: Pick<ExerciseCardGeneration, 'id' | 'version'>): string {
  return generation.version != null ? `v${generation.version}` : `#${generation.id}`;
}

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export const exerciseCardQueryKey = (exerciseId: number) => ['exercise-card', exerciseId] as const;
export const exerciseCardGenerationsQueryKey = (exerciseId: number) => ['exercise-card-generations', exerciseId] as const;
export const exerciseCardGenerationQueryKey = (exerciseId: number, generationId: number) =>
  ['exercise-card-generation', exerciseId, generationId] as const;

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export type StartGenerationInput = {
  /** Geração de origem → `POST …/{gid}/regenerate`; null → `POST …/card-generations`. */
  parentId: number | null;
  instruction?: string;
};

/**
 * Estado + ações da seção de card de UM exercício. `canManage` (admin com a
 * flag ligada) habilita as rotas de geração; sem ele só o card publicado é lido
 * (e só com a flag ligada — desligada, nenhuma chamada sai).
 */
export function useExerciseCardManager({
  exerciseId,
  enabled,
  isAdmin,
}: {
  exerciseId: number;
  enabled: boolean;
  isAdmin: boolean;
}) {
  const qc = useQueryClient();
  const canManage = enabled && isAdmin;
  const cardKey = exerciseCardQueryKey(exerciseId);
  const listKey = exerciseCardGenerationsQueryKey(exerciseId);

  const cardQuery = useQuery({
    queryKey: cardKey,
    queryFn: () => cardFetch<ExerciseCardResponse>(`/exercises/${exerciseId}/card`),
    enabled,
    staleTime: 5_000,
  });

  const listQuery = useQuery({
    queryKey: listKey,
    queryFn: () =>
      cardFetch<ExerciseCardGenerationListResponse>(
        `/exercises/${exerciseId}/card-generations?limit=${EXERCISE_CARD_HISTORY_LIMIT}`,
      ),
    enabled: canManage,
    staleTime: 5_000,
  });

  const items = listQuery.data?.items ?? [];
  // A geração acompanhada é a mais recente: a da lista (ids crescem) ou a que
  // esta aba acabou de criar/encontrar num 409 (antes de a lista recarregar).
  const [trackedId, setTrackedId] = useState<number | null>(null);
  const latestListedId = items[0]?.id ?? null;
  const currentId =
    trackedId == null ? latestListedId : latestListedId == null ? trackedId : Math.max(trackedId, latestListedId);
  const listedCurrent = currentId == null ? undefined : items.find((item) => item.id === currentId);

  const generationQuery = useQuery({
    queryKey: exerciseCardGenerationQueryKey(exerciseId, currentId ?? 0),
    queryFn: () =>
      cardFetch<ExerciseCardGenerationResponse>(`/exercises/${exerciseId}/card-generations/${currentId}`),
    enabled: canManage && currentId != null,
    placeholderData: listedCurrent ? { generation: listedCurrent } : undefined,
    refetchInterval: (query) => generationRefetchInterval(query.state.data?.generation),
  });

  const current = canManage ? generationQuery.data?.generation ?? listedCurrent ?? null : null;
  const published = cardQuery.data?.exercise_card ?? null;

  // Fim de uma geração acompanhada (viva → terminal): histórico e card publicado mudaram.
  const lastSeen = useRef<{ id: number; status: ExerciseCardGenerationStatus } | null>(null);
  const currentStatus = current?.status ?? null;
  const currentGenerationId = current?.id ?? null;
  useEffect(() => {
    const previous = lastSeen.current;
    lastSeen.current =
      currentGenerationId != null && currentStatus != null ? { id: currentGenerationId, status: currentStatus } : null;
    if (!previous || previous.id !== currentGenerationId || currentStatus == null) return;
    if (isLiveStatus(previous.status) && !isLiveStatus(currentStatus)) {
      void qc.invalidateQueries({ queryKey: listKey });
      if (currentStatus === 'PUBLISHED') void qc.invalidateQueries({ queryKey: cardKey });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentGenerationId, currentStatus]);

  const [keeper] = useState(() => new IdempotencyKeyKeeper());

  function storeGeneration(generation: ExerciseCardGeneration) {
    qc.setQueryData<ExerciseCardGenerationResponse>(exerciseCardGenerationQueryKey(exerciseId, generation.id), {
      generation,
    });
  }

  function refreshAfterConflict(error: unknown) {
    if (!(error instanceof ExerciseCardApiError) || error.status === 0 || error.status === 403) return;
    void qc.invalidateQueries({ queryKey: listKey });
    void qc.invalidateQueries({ queryKey: ['exercise-card-generation', exerciseId] });
  }

  const startMutation = useMutation({
    mutationFn: async ({ parentId, instruction }: StartGenerationInput) => {
      const text = instruction?.trim() || undefined;
      const key = keeper.keyFor(`${parentId ?? 'new'}|${text ?? ''}`);
      const body: ExerciseCardCreatePayload = { idempotency_key: key, ...(text ? { instruction: text } : {}) };
      const path =
        parentId == null
          ? `/exercises/${exerciseId}/card-generations`
          : `/exercises/${exerciseId}/card-generations/${parentId}/regenerate`;
      try {
        const response = await cardFetch<ExerciseCardGenerationResponse | ExerciseCardRegenerateResponse>(path, {
          method: 'POST',
          body: JSON.stringify(body),
        });
        keeper.settle();
        return response;
      } catch (error) {
        keeper.settle(error);
        throw error;
      }
    },
    onSuccess: (response) => {
      storeGeneration(response.generation);
      if ('parent' in response && response.parent) storeGeneration(response.parent);
      setTrackedId(response.generation.id);
      void qc.invalidateQueries({ queryKey: listKey });
    },
    onError: (error) => {
      // 409 "já existe uma em andamento" traz o id: passa a acompanhar essa.
      if (error instanceof ExerciseCardApiError && error.code === 'exercise_card_generation_in_progress' && error.generationId) {
        setTrackedId(error.generationId);
      }
      refreshAfterConflict(error);
    },
  });

  const approveMutation = useMutation({
    mutationFn: (generationId: number) =>
      cardFetch<ExerciseCardApproveResponse>(`/exercises/${exerciseId}/card-generations/${generationId}/approve`, {
        method: 'POST',
      }),
    onSuccess: (response) => {
      storeGeneration(response.generation);
      qc.setQueryData<ExerciseCardResponse>(cardKey, { exercise_id: exerciseId, exercise_card: response.exercise_card });
      void qc.invalidateQueries({ queryKey: listKey });
    },
    // 502 devolve a geração a GENERATED (com `error_code`); 409 = mudou de estado.
    onError: refreshAfterConflict,
  });

  const rejectMutation = useMutation({
    mutationFn: ({ generationId, reason }: { generationId: number; reason?: string }) => {
      const text = reason?.trim() || undefined;
      const body: ExerciseCardRejectPayload = text ? { reason: text } : {};
      return cardFetch<ExerciseCardGenerationResponse>(
        `/exercises/${exerciseId}/card-generations/${generationId}/reject`,
        { method: 'POST', body: JSON.stringify(body) },
      );
    },
    onSuccess: (response) => {
      storeGeneration(response.generation);
      void qc.invalidateQueries({ queryKey: listKey });
    },
    onError: refreshAfterConflict,
  });

  return {
    canManage,
    current,
    published,
    history: items,
    cardQuery,
    listQuery,
    generationQuery,
    startMutation,
    approveMutation,
    rejectMutation,
  };
}

/** `Date.now()` que avança a cada `intervalMs` enquanto `active` (contador de segundos). */
export function useNow(active: boolean, intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [active, intervalMs]);
  return now;
}

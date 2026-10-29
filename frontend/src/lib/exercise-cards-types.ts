/**
 * Contrato dos cards ilustrados de exercícios (PR3, `api/app/exercise_cards.py`
 * + `exercise_card_service.serialize`). Rotas (todas snake_case):
 *
 *   POST /exercises/{id}/card-generations                   admin  202 {generation} (replay: 200)
 *   GET  /exercises/{id}/card-generations?limit=20          admin  {items} (sem prompt_text)
 *   GET  /exercises/{id}/card-generations/{gid}             admin  {generation}
 *   POST /exercises/{id}/card-generations/{gid}/approve     admin  {generation, exercise_card}
 *   POST /exercises/{id}/card-generations/{gid}/reject      admin  {generation}
 *   POST /exercises/{id}/card-generations/{gid}/regenerate  admin  202 {generation, parent} (replay: 200)
 *   GET  /exercises/{id}/card                               autenticado  {exercise_id, exercise_card | null}
 *
 * Toda rota `card-generations` depende da flag `EXERCISE_CARD_GENERATION_ENABLED`
 * (exposta em `/auth/me` como `features.exercise_cards`); desligada → 503
 * `exercise_card_generation_disabled`. `GET /card` não depende da flag.
 *
 * Estratégia B (#349): a IA gera só a ilustração e o worker compõe o card
 * final (logo, título, rótulos, músculos) — o `preview_url` JÁ É o card que
 * será publicado; aprovar só codifica e publica.
 *
 * O teste de contrato (`exercise-cards.test.ts`) prende estes tipos a uma
 * fixture gerada pelo `serialize` real do backend (`exercise-cards-fixtures.ts`).
 */

export const EXERCISE_CARD_GENERATION_STATUSES = [
  'PENDING',
  'GENERATING',
  'GENERATED',
  'APPROVING',
  'PUBLISHED',
  'SUPERSEDED',
  'REJECTED',
  'FAILED',
] as const;

export type ExerciseCardGenerationStatus = (typeof EXERCISE_CARD_GENERATION_STATUSES)[number];

/** Card de uma geração PUBLISHED/SUPERSEDED (URLs públicas pela CDN). */
export interface ExerciseCardGenerationCard {
  url: string;
  png_url: string | null;
  thumbnail_url: string | null;
  version: number | null;
}

/** `generation` da API (`exercise_card_service.serialize`). Nunca traz chaves S3. */
export interface ExerciseCardGeneration {
  id: number;
  exercise_id: number;
  card_type: string | null;
  status: ExerciseCardGenerationStatus;
  /** Só na publicação (v1, v2, ...). */
  version: number | null;
  parent_generation_id: number | null;
  idempotency_key: string | null;
  /** Diagnóstico apenas — o polling é pelo recurso, nunca por `GET /jobs/{id}`. */
  job_id: string | null;
  provider: string | null;
  model: string | null;
  quality: string | null;
  size: string | null;
  prompt_version: string | null;
  /** Só `minimal` existe na versão atual do prompt. */
  text_level: string | null;
  request_instruction: string | null;
  /** Texto humano, ou o código interno `regenerated` (pai substituído por uma regeneração). */
  reject_reason: string | null;
  /** URL pré-assinada (15 min) do rascunho — só em GENERATED/APPROVING. */
  preview_url: string | null;
  preview_expires_at: string | null;
  /**
   * M72 — validade do rascunho (`generated_at` + 7 dias, a mesma regra do
   * lifecycle do S3). Só em GENERATED/APPROVING; senão `null`.
   */
  draft_expires_at: string | null;
  /**
   * Rascunho expirado (calculado no backend, não pelo 404 da imagem): não dá
   * mais para aprovar — só descartar ou gerar de novo. Expirado vem sem
   * `preview_url`.
   */
  draft_expired: boolean;
  card: ExerciseCardGenerationCard | null;
  width: number | null;
  height: number | null;
  bytes: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  error_code: string | null;
  error_message: string | null;
  created_by: number | null;
  approved_by: number | null;
  created_at: string | null;
  updated_at: string | null;
  started_at: string | null;
  generated_at: string | null;
  approved_at: string | null;
  published_at: string | null;
  rejected_at: string | null;
  failed_at: string | null;
  /** Só no detalhe/criação (a listagem não traz). */
  prompt_text?: string | null;
}

/** Card publicado vigente (ponteiro em `exercise_catalog`). */
export interface PublishedExerciseCard {
  url: string;
  thumbnail_url: string;
  png_url: string;
  version: number;
  published_at: string | null;
}

export interface ExerciseCardResponse {
  exercise_id: number;
  exercise_card: PublishedExerciseCard | null;
}

export interface ExerciseCardGenerationResponse {
  generation: ExerciseCardGeneration;
}

export interface ExerciseCardRegenerateResponse {
  generation: ExerciseCardGeneration;
  parent: ExerciseCardGeneration | null;
}

export interface ExerciseCardApproveResponse {
  generation: ExerciseCardGeneration;
  exercise_card: PublishedExerciseCard;
}

export interface ExerciseCardGenerationListResponse {
  items: ExerciseCardGeneration[];
}

export interface ExerciseCardCreatePayload {
  idempotency_key: string;
  instruction?: string;
}

export interface ExerciseCardRejectPayload {
  reason?: string;
}

/** `detail` dos erros de negócio (`ExerciseCardError.detail()`). */
export interface ExerciseCardErrorDetail {
  error: string;
  message: string;
  generation_id?: number;
}

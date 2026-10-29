// Chamadas do plano nutricional (Lote L2b), do catálogo de alimentos (L3a), das fontes externas (L3b:
// código de barras e USDA), do diário (L4) e da leitura da refeição por IA (L5b). A API calcula tudo; aqui só há
// transporte, chaves do React Query e a leitura do `detail` estruturado dos
// erros 400/422 (códigos e textos do motor de nutrição).

import { apiFetch, ApiError } from './api';
import { ASYNC_JOB_POLL_TIMEOUT_MS, dispatchAndPollJob, pollAsyncJob } from './async-job-poll';
import { blobToBase64 } from './base64';
import { resizeImageForUpload } from './image-resize';
import { LABEL_PHOTO } from './nutrition-constants';
import type {
  AiQuota,
  DayLogPayload,
  DayLogResponse,
  ExternalFoodResponse,
  FoodDetail,
  FoodItem,
  FoodItemCreatePayload,
  FoodItemPayload,
  FoodPortion,
  FoodPortionPayload,
  FoodSaveResponse,
  FoodSearchResponse,
  FoodSearchScope,
  LabelPhotoDispatch,
  LabelPhotoReading,
  LabelPhotoUploadPayload,
  Meal,
  MealCreatePayload,
  MealParsePayload,
  MealParseResult,
  MealPayload,
  MealPreview,
  MealPreviewPayload,
  NutritionDay,
  NutritionLimitation,
  NutritionMyPlan,
  NutritionPlan,
  NutritionPlanListResponse,
  NutritionPlanPayload,
  NutritionPlanPreview,
  NutritionPlanPublishPayload,
  NutritionPlanSettingsPayload,
  NutritionPublishResponse,
  NutritionRecomputePayload,
  NutritionRecomputeResponse,
  NutritionWeek,
  UsdaSearchResponse,
} from './nutrition-types';

export const nutritionKeys = {
  plans: (userId: number) => ['nutrition-plans', userId] as const,
  preview: (planId: number) => ['nutrition-plan-preview', planId] as const,
  myPlan: ['nutrition-my-plan'] as const,
};

export function fetchNutritionPlans(userId: number): Promise<NutritionPlanListResponse> {
  return apiFetch<NutritionPlanListResponse>(`/nutrition/plans?user_id=${encodeURIComponent(String(userId))}`);
}

export function createNutritionPlan(payload: NutritionPlanPayload): Promise<NutritionPlan> {
  return apiFetch<NutritionPlan>('/nutrition/plans', { method: 'POST', body: JSON.stringify(payload) });
}

export function updateNutritionPlan(planId: number, payload: NutritionPlanSettingsPayload): Promise<NutritionPlan> {
  return apiFetch<NutritionPlan>(`/nutrition/plans/${planId}`, { method: 'PUT', body: JSON.stringify(payload) });
}

export function deleteNutritionPlan(planId: number): Promise<{ status: string; id: number }> {
  return apiFetch(`/nutrition/plans/${planId}`, { method: 'DELETE' });
}

export function previewNutritionPlan(planId: number): Promise<NutritionPlanPreview> {
  return apiFetch<NutritionPlanPreview>(`/nutrition/plans/${planId}/preview`, { method: 'POST' });
}

/**
 * Publica o rascunho. `expectedCurrentId` é o id da vigente que a tela mostrou
 * (ou `null`): se outra publicação mudou a vigente no meio, a API responde 409
 * `publish_conflict` (concorrência otimista) e a tela pede para recarregar.
 */
export function publishNutritionPlan(planId: number, expectedCurrentId: number | null): Promise<NutritionPublishResponse> {
  const payload: NutritionPlanPublishPayload = { expected_current_id: expectedCurrentId };
  return apiFetch<NutritionPublishResponse>(`/nutrition/plans/${planId}/publish`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function recomputeNutritionTargets(payload: NutritionRecomputePayload): Promise<NutritionRecomputeResponse> {
  return apiFetch<NutritionRecomputeResponse>('/nutrition/targets/recompute', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function fetchMyNutritionPlan(): Promise<NutritionMyPlan> {
  return apiFetch<NutritionMyPlan>('/nutrition/me/plan');
}

// ---------------------------------------------------------------------------
// Catálogo de alimentos (Lote L3a). A busca, a ordem (nota do motor), as
// permissões e as regras de entrada são da API; aqui só transporte.
// ---------------------------------------------------------------------------

export const foodKeys = {
  search: (q: string, scope: FoodSearchScope, source: string | null) =>
    ['nutrition-foods-search', q, scope, source] as const,
  detail: (foodId: number) => ['nutrition-food', foodId] as const,
};

export function searchFoods(
  q: string,
  options: { scope?: FoodSearchScope; source?: string | null; limit?: number } = {},
): Promise<FoodSearchResponse> {
  const params = new URLSearchParams({ q });
  if (options.scope && options.scope !== 'visible') params.set('scope', options.scope);
  if (options.source) params.set('source', options.source);
  if (options.limit) params.set('limit', String(options.limit));
  return apiFetch<FoodSearchResponse>(`/nutrition/foods/search?${params.toString()}`);
}

export function fetchFood(foodId: number): Promise<FoodDetail> {
  return apiFetch<FoodDetail>(`/nutrition/foods/${foodId}`);
}

export function createFood(payload: FoodItemCreatePayload): Promise<FoodSaveResponse> {
  return apiFetch<FoodSaveResponse>('/nutrition/foods', { method: 'POST', body: JSON.stringify(payload) });
}

export function updateFood(foodId: number, payload: FoodItemPayload): Promise<FoodSaveResponse> {
  return apiFetch<FoodSaveResponse>(`/nutrition/foods/${foodId}`, { method: 'PUT', body: JSON.stringify(payload) });
}

export function addFoodPortion(foodId: number, payload: FoodPortionPayload): Promise<FoodPortion> {
  return apiFetch<FoodPortion>(`/nutrition/foods/${foodId}/portions`, { method: 'POST', body: JSON.stringify(payload) });
}

/** Admin: alimento privado de um usuário vira público (com as porções do dono). */
export function promoteFood(foodId: number): Promise<FoodItem> {
  return apiFetch<FoodItem>(`/nutrition/foods/${foodId}/promote`, { method: 'POST' });
}

// ---------------------------------------------------------------------------
// Fontes externas (Lote L3b). O código de barras é validado, mapeado e limitado
// pela API (motor `external_sources`); aqui só transporte.
// ---------------------------------------------------------------------------

export const externalFoodKeys = {
  usdaSearch: (q: string) => ['nutrition-usda-search', q] as const,
};

/** Código de barras: catálogo (cache) ou Open Food Facts. 404 = produto desconhecido; 429 = limite do dia. */
export function lookupBarcode(ean: string): Promise<ExternalFoodResponse> {
  return apiFetch<ExternalFoodResponse>(`/nutrition/foods/barcode/${encodeURIComponent(ean)}`);
}

/** Busca no USDA FoodData Central (em inglês; só Foundation e SR Legacy). 503 sem chave configurada. */
export function searchUsdaFoods(q: string): Promise<UsdaSearchResponse> {
  return apiFetch<UsdaSearchResponse>(`/nutrition/foods/usda/search?${new URLSearchParams({ q }).toString()}`);
}

/** Importa um item do USDA para o catálogo (idempotente: o mesmo FDC ID devolve a mesma linha). */
export function importUsdaFood(usdaFoodId: number): Promise<ExternalFoodResponse> {
  return apiFetch<ExternalFoodResponse>(`/nutrition/foods/usda/${usdaFoodId}/import`, { method: 'POST' });
}

// ---------------------------------------------------------------------------
// Foto do rótulo (Lote L6). A tela reduz a foto (canvas, parâmetros do dono pelo
// JSON gerado), envia, e acompanha o job; leitura, revisão e cota são da API.
// ---------------------------------------------------------------------------

export const labelPhotoKeys = {
  usage: ['nutrition-label-photo-usage'] as const,
};

const MS_PER_SECOND = 1000;

/** Cota de leituras de rótulo do dia (a tela mostra "x de N hoje"; N vem da API). */
export function fetchLabelPhotoUsage(): Promise<AiQuota> {
  return apiFetch<AiQuota>('/nutrition/foods/label-photo/usage');
}

/**
 * Envia a foto JÁ REDUZIDA como base64 sem padding num JSON (`LabelPhotoUploadIn`):
 * o alfabeto do base64 não forma as assinaturas que o WAF bloqueia no corpo, e
 * bytes crus de JPEG poderiam formar. 413/415 = fora do limite/tipo do motor;
 * 429 = cota do dia.
 */
export async function uploadLabelPhoto(photo: Blob): Promise<LabelPhotoDispatch> {
  const payload: LabelPhotoUploadPayload = { image_base64: await blobToBase64(photo, { padding: false }) };
  return apiFetch<LabelPhotoDispatch>('/nutrition/foods/label-photo', { method: 'POST', body: JSON.stringify(payload) });
}

export type LabelPhotoStage = 'resizing' | 'uploading' | 'reading';

/**
 * Foto escolhida -> redução por canvas (sempre: tira EXIF/XMP do corpo; se o
 * navegador não conseguir reduzir, NADA é enviado) -> upload -> polling do job
 * até o prazo que a API devolveu. Resolve com o formulário pré-preenchido.
 */
export async function readLabelPhoto(file: Blob, onStage?: (stage: LabelPhotoStage) => void): Promise<LabelPhotoReading> {
  onStage?.('resizing');
  const reduzida = await resizeImageForUpload(file, {
    maxEdge: LABEL_PHOTO.resize_max_edge_px,
    quality: LABEL_PHOTO.resize_quality,
    contentType: LABEL_PHOTO.content_type,
  });
  onStage?.('uploading');
  const despacho = await uploadLabelPhoto(reduzida);
  onStage?.('reading');
  const prazo = despacho.poll_timeout_seconds;
  return pollAsyncJob<LabelPhotoReading>(despacho.job_id, {
    timeoutMs: prazo ? prazo * MS_PER_SECOND : ASYNC_JOB_POLL_TIMEOUT_MS,
    failedFallbackMessage: 'Não foi possível ler o rótulo. Tente de novo.',
    timeoutMessage: 'A leitura do rótulo demorou demais. Tente de novo em instantes.',
  });
}

// ---------------------------------------------------------------------------
// Diário de refeições (Lote L4). Macros, totais, meta do dia, semana e kcal do
// treino vêm prontos da API (motor); o preview da refeição é uma chamada (E9).
// ---------------------------------------------------------------------------

export const diaryKeys = {
  all: ['nutrition-diary'] as const,
  day: (userId: number | null, date: string) => ['nutrition-diary', 'day', userId ?? 'me', date] as const,
  week: (userId: number | null, start: string | null) => ['nutrition-diary', 'week', userId ?? 'me', start ?? 'today'] as const,
};

function withUser(path: string, userId: number | null | undefined, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams(extra);
  if (userId != null) params.set('user_id', String(userId));
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

export function fetchNutritionDay(date: string, userId: number | null): Promise<NutritionDay> {
  return apiFetch<NutritionDay>(withUser(`/nutrition/days/${encodeURIComponent(date)}`, userId));
}

/** A semana (segunda a domingo) que contém `start`; sem `start`, a de hoje do aluno. */
export function fetchNutritionWeek(start: string | null, userId: number | null): Promise<NutritionWeek> {
  return apiFetch<NutritionWeek>(withUser('/nutrition/weeks', userId, start ? { start } : {}));
}

export function saveNutritionDayLog(date: string, payload: DayLogPayload, userId: number | null): Promise<DayLogResponse> {
  return apiFetch<DayLogResponse>(withUser(`/nutrition/days/${encodeURIComponent(date)}`, userId), {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export function previewMeal(payload: MealPreviewPayload): Promise<MealPreview> {
  return apiFetch<MealPreview>('/nutrition/meals/preview', { method: 'POST', body: JSON.stringify(payload) });
}

export function createMeal(payload: MealCreatePayload): Promise<Meal> {
  return apiFetch<Meal>('/nutrition/meals', { method: 'POST', body: JSON.stringify(payload) });
}

export function updateMeal(mealId: number, payload: MealPayload): Promise<Meal> {
  return apiFetch<Meal>(`/nutrition/meals/${mealId}`, { method: 'PUT', body: JSON.stringify(payload) });
}

export function deleteMeal(mealId: number): Promise<{ status: string; id: number }> {
  return apiFetch(`/nutrition/meals/${mealId}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Leitura da refeição por IA (Lote L5b): 202 + polling (`dispatchAndPollJob`).
// ---------------------------------------------------------------------------

export const mealParseKeys = {
  quota: ['nutrition-meal-reading', 'quota'] as const,
};

/** Cota de hoje de quem pede (a do admin/PT quando lê para um aluno). */
export function fetchMealParseQuota(): Promise<AiQuota> {
  return apiFetch<AiQuota>('/nutrition/meals/parse/quota');
}

/** Dispara a leitura e espera o resultado do job (itens com a decisão do motor). */
export function parseMealText(payload: MealParsePayload): Promise<MealParseResult> {
  return dispatchAndPollJob<MealParseResult>(
    '/nutrition/meals/parse',
    { body: JSON.stringify(payload) },
    {
      failedFallbackMessage: 'Não foi possível ler a refeição. Tente novamente.',
      timeoutMessage: 'A leitura da refeição demorou demais. Tente novamente em instantes.',
    },
  );
}

type StructuredDetail = {
  error?: string;
  message?: string;
  errors?: NutritionLimitation[];
  missing?: NutritionLimitation[];
};

function structuredDetail(error: unknown): StructuredDetail | null {
  if (!(error instanceof ApiError) || typeof error.detail !== 'object' || error.detail === null) return null;
  return error.detail as StructuredDetail;
}

/** Código do erro estruturado da API (`invalid_plan`, `plan_not_draft`...). */
export function nutritionErrorCode(error: unknown): string | null {
  return structuredDetail(error)?.error ?? null;
}

/** Mensagem + itens (códigos do motor já rotulados) de um erro da API de nutrição. */
export function nutritionErrorInfo(
  error: unknown,
  fallback: string,
): { message: string; items: NutritionLimitation[] } {
  const detail = structuredDetail(error);
  if (detail) {
    return { message: detail.message || fallback, items: detail.errors ?? detail.missing ?? [] };
  }
  return { message: error instanceof ApiError ? error.message : fallback, items: [] };
}

/**
 * `true` só quando a API disse "sem plano publicado" (`no_published_plan`). Um
 * 404 de outra origem (rota ausente num backend antigo, caminho errado) NÃO é
 * "sem plano": cai no erro genérico da tela.
 */
export function isNoPublishedPlan(error: unknown): boolean {
  return nutritionErrorCode(error) === 'no_published_plan';
}

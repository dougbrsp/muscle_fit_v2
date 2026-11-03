// Sugestão de metas por IA (N6, decisão D3): pedido, polling, prévia do formulário e o preenchimento do rascunho.
// Só transporte: a IA sugere parâmetros, o motor valida na API e calcula as gramas. O texto de "o que mudou" e os
// motivos já vêm prontos da API; a cota também. Aqui só a conversão de fração para o % que o formulário mostra.

import { apiFetch } from './api';
import { ASYNC_JOB_POLL_TIMEOUT_MS, pollAsyncJob } from './async-job-poll';
import { asField, shareToPercent } from './nutrition-format';
import type {
  AiQuota,
  NutritionLimitation,
  NutritionPlanPreview,
  NutritionPlanSettingsPayload,
} from './nutrition-types';

const MS_PER_SECOND = 1000;

/** Os campos que a IA pode sugerir, no vocabulário do motor (`plan_suggestion.SUGGESTION_FIELDS`). */
export type PlanSuggestionFields = {
  strategy: string;
  advanced: boolean;
  protein_g_per_kg: number;
  fat_kcal_share: number;
  weekly_rate_share: number | null;
  surplus_share: number | null;
  carb_min_g_per_kg: number | null;
  macro_priority: string;
  meals_per_day: number;
};

/** Uma linha de "o que mudou": rótulo e os dois valores, já em texto do motor. */
export type PlanSuggestionChange = { field: string; label: string; before_label: string; after_label: string };

export type PlanSuggestionResult = {
  fields: PlanSuggestionFields;
  reasons: NutritionLimitation[];
  changes: PlanSuggestionChange[];
  user_id: number;
  model: string;
  prompt_version: string;
  engine_version: string;
};

/** Estado do formulário enviado no pedido: só comparação e contexto (as gramas não vão, só o sinal). */
export type PlanSuggestionCurrent = {
  strategy: string | null;
  advanced: boolean;
  protein_g_per_kg: number | null;
  fat_kcal_share: number | null;
  weekly_rate_share: number | null;
  surplus_share: number | null;
  carb_min_g_per_kg: number | null;
  macro_priority: string | null;
  meals_per_day: number | null;
  goal_weight_kg: number | null;
  goal_body_fat_percent: number | null;
  activity_level: string | null;
  tdee_mode: string | null;
  bmr_formula: string | null;
  manual_targets: boolean;
};

export type PlanSuggestionDispatch = { job_id: string; status: string; poll_timeout_seconds: number | null };

/** A prévia do formulário ainda não salvo: a mesma do plano salvo, sem o plano. */
export type NutritionPlanDraftPreview = Omit<NutritionPlanPreview, 'plan'> & { plan: null };

export const planSuggestionKeys = {
  usage: ['nutrition-plan-suggestion-usage'] as const,
  preview: (corpo: string | null) => ['nutrition-plan-draft-preview', corpo] as const,
};

/** Cota de sugestões de hoje, de quem pede (N vem da API). */
export function fetchPlanSuggestionUsage(): Promise<AiQuota> {
  return apiFetch<AiQuota>('/nutrition/plan-suggestion/usage');
}

/** Dispara a sugestão e espera o job até o prazo que a API devolveu. */
export async function requestPlanSuggestion(current: PlanSuggestionCurrent, userId: number | null): Promise<PlanSuggestionResult> {
  const corpo = userId == null ? { current } : { user_id: userId, current };
  const despacho = await apiFetch<PlanSuggestionDispatch>('/nutrition/plan-suggestion', {
    method: 'POST',
    body: JSON.stringify(corpo),
  });
  const prazo = despacho.poll_timeout_seconds;
  return pollAsyncJob<PlanSuggestionResult>(despacho.job_id, {
    timeoutMs: prazo ? prazo * MS_PER_SECOND : ASYNC_JOB_POLL_TIMEOUT_MS,
    failedFallbackMessage: 'Não foi possível gerar a sugestão. Tente de novo.',
    timeoutMessage: 'A sugestão demorou demais. Tente de novo em instantes.',
  });
}

/** O motor calcula o formulário como está, sem gravar nada (`POST /nutrition/plan-preview`). */
export function previewPlanDraft(payload: NutritionPlanSettingsPayload & { user_id?: number }): Promise<NutritionPlanDraftPreview> {
  return apiFetch<NutritionPlanDraftPreview>('/nutrition/plan-preview', { method: 'POST', body: JSON.stringify(payload) });
}

/** Do corpo do formulário, o que o pedido leva: sem gramas, sem ciclo e sem observações. */
export function suggestionCurrentPayload(payload: NutritionPlanSettingsPayload, manualOn: boolean): PlanSuggestionCurrent {
  return {
    strategy: payload.strategy || null,
    advanced: payload.advanced,
    protein_g_per_kg: payload.protein_g_per_kg,
    fat_kcal_share: payload.fat_kcal_share,
    weekly_rate_share: payload.weekly_rate_share,
    surplus_share: payload.surplus_share,
    carb_min_g_per_kg: payload.carb_min_g_per_kg,
    macro_priority: payload.macro_priority ?? null,
    meals_per_day: payload.meals_per_day,
    goal_weight_kg: payload.goal_weight_kg,
    goal_body_fat_percent: payload.goal_body_fat_percent,
    activity_level: payload.activity_level ?? null,
    tdee_mode: payload.tdee_mode ?? null,
    bmr_formula: payload.bmr_formula ?? null,
    manual_targets: manualOn,
  };
}

/** O que a sugestão preenche no formulário. Aplicar desliga as gramas manuais: com elas, os parâmetros não valeriam. */
export function suggestionFormPatch(fields: PlanSuggestionFields) {
  return {
    strategy: fields.strategy as string | null,
    advanced: fields.advanced,
    protein: asField(fields.protein_g_per_kg),
    fatPercent: asField(shareToPercent(fields.fat_kcal_share)),
    ratePercent: asField(shareToPercent(fields.weekly_rate_share)),
    surplusPercent: asField(shareToPercent(fields.surplus_share)),
    carbMin: asField(fields.carb_min_g_per_kg),
    macroPriority: fields.macro_priority,
    meals: asField(fields.meals_per_day),
    manualOn: false,
  };
}

/** Só as chaves que a sugestão preenche: é o que "Desfazer" devolve, sem perder outra edição. */
export type SuggestionFormSlice = ReturnType<typeof suggestionFormPatch>;

/** O formulário e o que desfazer. `undo` = null: nada a desfazer. */
export type SuggestionFormState<F extends SuggestionFormSlice> = { form: F; undo: SuggestionFormSlice | null };

/** Com uma sugestão ainda aberta, "Desfazer" continua voltando ao estado anterior à primeira. */
export function applySuggestion<F extends SuggestionFormSlice>(
  state: SuggestionFormState<F>,
  fields: PlanSuggestionFields,
): SuggestionFormState<F> {
  const patch = suggestionFormPatch(fields);
  const fatia = Object.fromEntries(Object.keys(patch).map((chave) => [chave, state.form[chave as keyof SuggestionFormSlice]]));
  return { form: { ...state.form, ...patch }, undo: state.undo ?? (fatia as SuggestionFormSlice) };
}

/** "Desfazer": volta só as chaves da sugestão; o que a pessoa mudou nos outros campos fica. */
export function undoSuggestion<F extends SuggestionFormSlice>(state: SuggestionFormState<F>): SuggestionFormState<F> {
  return { form: state.undo ? { ...state.form, ...state.undo } : state.form, undo: null };
}

/** "Manter": o formulário fica como está e não há mais o que desfazer. */
export function keepSuggestion<F extends SuggestionFormSlice>(state: SuggestionFormState<F>): SuggestionFormState<F> {
  return { form: state.form, undo: null };
}

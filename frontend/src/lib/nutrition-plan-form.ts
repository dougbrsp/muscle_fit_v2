// Montagem do corpo do plano autogerido (N2). Só transporte: o FE não valida faixas nem calcula
// nada; o motor valida na API (400 com o código) e devolve os avisos. Aqui ficam apenas as regras
// de FORMULÁRIO: gramas manuais mandam sobre a prioridade, e dias desligados viram null.

import { DEFAULT_MACRO_PRIORITY, DEFAULT_MANUAL_TRAINING_MODE, type NutritionCode } from './nutrition-constants';
import { diaryKeys, nutritionKeys } from './nutrition-api';
import { checkinKeys } from './nutrition-checkin';
import { dashboardKeys } from './nutrition-dashboard';
import type { NutritionManualTargetsInput } from './nutrition-types';

/** Valor de campo numérico do formulário: número, texto digitado ou vazio. */
export type FormNumber = number | string;

function numberOrNull(value: FormNumber): number | null {
  if (value === '' || value == null) return null;
  const n = typeof value === 'number' ? value : Number(String(value).replace(',', '.'));
  return Number.isFinite(n) ? n : null;
}

/**
 * Gramas manuais do corpo. Campo vazio vira `null` (a API responde `manual_invalid_value:<campo>`
 * em vez de o FE inventar 0); desligado, `null` (o plano volta ao cálculo automático).
 */
export function manualTargetsPayload(
  on: boolean,
  protein: FormNumber,
  carb: FormNumber,
  fat: FormNumber,
): NutritionManualTargetsInput | null {
  if (!on) return null;
  return {
    protein_g: numberOrNull(protein),
    carb_g: numberOrNull(carb),
    fat_g: numberOrNull(fat),
  };
}

/** Modo das gramas manuais com o treino; sem gramas manuais, `null` (o motor recusa o modo sem gramas). */
export function manualTrainingModePayload(manualOn: boolean, mode: NutritionCode | null | undefined): NutritionCode | null {
  return manualOn ? (mode ?? DEFAULT_MANUAL_TRAINING_MODE) : null;
}

/** Com gramas manuais a prioridade de macros não se aplica (o motor recusa as duas): manda o padrão. */
export function macroPriorityPayload(manualOn: boolean, priority: NutritionCode): NutritionCode {
  return manualOn ? DEFAULT_MACRO_PRIORITY : priority;
}

/** Dias alto/normal/baixo do corpo; desligado, `null` (sem ciclo). */
export function cycleDaysPayload(
  on: boolean,
  days: Readonly<Record<string, NutritionCode>>,
): Record<string, NutritionCode> | null {
  return on ? { ...days } : null;
}

/**
 * Proposta do "puxar do Modelo" aplicada aos dias marcados: sem proposta (`null`: sem Modelo, Modelo parcial ou
 * que treina todo dia) o que o usuário marcou fica como está.
 */
export function applyTemplateProposal(
  current: Readonly<Record<string, NutritionCode>>,
  proposal: Readonly<Record<string, NutritionCode>> | null,
): Record<string, NutritionCode> {
  return proposal ? { ...current, ...proposal } : { ...current };
}

/**
 * Ajuste da manutenção (N7) que o formulário envia: o do rascunho em edição, ou, num rascunho NOVO, o do plano em
 * vigor (senão salvar um rascunho novo zeraria o que o check-in semanal já ajustou, e publicá-lo desfaria o ajuste).
 * Fora da manutenção é `null`. A tela não edita o valor; ele é do check-in.
 */
export function maintenanceAdjustPayload(
  applies: boolean,
  editing: { maintenance_kcal_adjust?: number } | null,
  inherited: number | null | undefined,
): number | null {
  if (!applies) return null;
  return editing ? (editing.maintenance_kcal_adjust ?? 0) : (inherited ?? 0);
}

/**
 * Caches que ficam velhos quando as metas mudam (publicar ou recalcular): o plano em vigor, o diário
 * (que lê a meta de cada dia; o card de hoje, `/nutrition/today`, mora sob `diaryKeys.all`) e o painel
 * `/dashboard/nutricao` (N5) e o check-in semanal (N7: a proposta é do plano que a gerou). Chaves do React Query.
 */
export function targetsChangedKeys(): ReadonlyArray<readonly unknown[]> {
  return [nutritionKeys.myPlan, diaryKeys.all, dashboardKeys.all, checkinKeys.all];
}

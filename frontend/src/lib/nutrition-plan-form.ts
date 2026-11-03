// Montagem do corpo do plano autogerido (N2). Só transporte: o FE não valida faixas nem calcula
// nada; o motor valida na API (400 com o código) e devolve os avisos. Aqui ficam apenas as regras
// de FORMULÁRIO: gramas manuais mandam sobre a prioridade, e dias desligados viram null.

import { DEFAULT_MACRO_PRIORITY, type NutritionCode } from './nutrition-constants';
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

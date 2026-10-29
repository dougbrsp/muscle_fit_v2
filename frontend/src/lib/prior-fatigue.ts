import { forEachMatchedPreviewEcho, itemActivationKey } from '@/lib/item-activation-summary';
import type {
  PreviewActivationResponse,
  PriorFatigue,
  WorkoutPlanTemplateSlotPayload,
} from '@/lib/workout-plan-templates-types';

/**
 * Fadiga prévia por item no editor de Modelo — só APRESENTAÇÃO.
 *
 * O cálculo (músculo principal, séries prévias, nível 0..1, cortes das faixas e
 * os rótulos) é todo do dono `api/app/domain/prior_fatigue.py`, que o preview
 * (`POST /workout-plan-templates/preview-activation` → `item_prior_fatigue[]`)
 * devolve pronto. Aqui não há limiar: a cor é uma função contínua do `level` e
 * o texto usa o `band_label` da API (guarda `fadiga_previa`).
 */

// Matiz do gradiente: verde (nível 0) → amarelo → vermelho (nível 1).
const HUE_RESTED = 120;
const HUE_FATIGUED = 0;
const STRIPE_SATURATION_PCT = 72;
const STRIPE_LIGHTNESS_PCT = 46;

function clampUnit(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/** Cor da faixa lateral do card (legível nos temas claro e escuro). */
export function priorFatigueColor(level: number): string {
  const t = clampUnit(level);
  const hue = HUE_RESTED + (HUE_FATIGUED - HUE_RESTED) * t;
  return `hsl(${Math.round(hue)}, ${STRIPE_SATURATION_PCT}%, ${STRIPE_LIGHTNESS_PCT}%)`;
}

/** "Fadiga prévia no Peitoral: pré-fatigado" (rótulo da faixa vem da API). */
export function priorFatigueLabel(priorFatigue: PriorFatigue): string {
  const group = priorFatigue.muscle_group_name || 'grupo principal';
  return `Fadiga prévia no ${group}: ${priorFatigue.band_label}`;
}

/**
 * `itemActivationKey(slot.sequence, índice no estado do builder)` → fadiga
 * prévia. Mesma correspondência (e a mesma proteção contra resposta de corpo
 * anterior) da linha de ativação (`forEachMatchedPreviewItem`).
 */
export function buildItemPriorFatigueLookup(
  slots: WorkoutPlanTemplateSlotPayload[],
  response: PreviewActivationResponse | null | undefined,
): Map<string, PriorFatigue> {
  const lookup = new Map<string, PriorFatigue>();
  forEachMatchedPreviewEcho(slots, response?.item_prior_fatigue, ({ slot, stateIndex, echoed }) => {
    if (echoed.prior_fatigue) lookup.set(itemActivationKey(slot.sequence, stateIndex), echoed.prior_fatigue);
  });
  return lookup;
}

// Política do ciclo de carboidratos (#242). Só transporte e exibição: a conta é do motor
// (`carb_cycling.py`); rótulos e dicas vêm do espelho (`CARB_CYCLE_POLICY_*`).

import {
  CARB_CYCLE_POLICY_HINTS,
  CARB_CYCLE_POLICY_OPTIONS,
  DAY_KIND_OPTIONS,
  DEFAULT_CARB_CYCLE_POLICY,
  nutritionLabel,
  type NutritionCode,
} from './nutrition-constants';

function isKnown(policy: NutritionCode | null | undefined): boolean {
  return CARB_CYCLE_POLICY_OPTIONS.some((o) => o.value === policy);
}

/** Compensação ativa: opção conhecida que não é o padrão do espelho. */
function isCompensation(policy: NutritionCode | null | undefined): boolean {
  return isKnown(policy) && policy !== DEFAULT_CARB_CYCLE_POLICY;
}

/** Valor do formulário: o do plano, ou o padrão (plano sem o campo ou com valor desconhecido). */
export function initialCyclePolicy(policy: NutritionCode | null | undefined): NutritionCode {
  return isKnown(policy) ? (policy as NutritionCode) : DEFAULT_CARB_CYCLE_POLICY;
}

/** Com ciclo, sempre explícita; sem ciclo, `null` (o motor recusa a compensação sem ciclo). */
export function cyclePolicyPayload(on: boolean, policy: NutritionCode): NutritionCode | null {
  return on ? initialCyclePolicy(policy) : null;
}

/** Dica da opção escolhida (texto do dono). */
export function cyclePolicyHint(policy: NutritionCode): string | null {
  return CARB_CYCLE_POLICY_HINTS[policy] ?? null;
}

/** Política do plano para o resumo: só com ciclo e só se o plano a trouxer. */
export function planPolicyLabel(plan: {
  cycle_days: unknown;
  carb_cycle_policy?: NutritionCode | null;
}): string | null {
  if (!plan.cycle_days || !plan.carb_cycle_policy) return null;
  return nutritionLabel(CARB_CYCLE_POLICY_OPTIONS, plan.carb_cycle_policy) ?? null;
}

export type DiaryCycleInfo = { dayKind: string | null; policy: string | null };

/**
 * O que o diário mostra da meta: tipo do dia (alto/baixo, ou normal quando a compensação está ativa) e, só com
 * compensação, o rótulo da política. Meta sem os campos novos: `null` (nada novo na tela).
 */
export function diaryCycleInfo(
  target: { day_kind?: NutritionCode | null; carb_cycle_policy?: NutritionCode | null } | null | undefined,
): DiaryCycleInfo | null {
  if (!target || !target.carb_cycle_policy) return null;
  const compensa = isCompensation(target.carb_cycle_policy);
  const kind = target.day_kind;
  const dayKind = kind && (kind !== 'normal' || compensa) ? (nutritionLabel(DAY_KIND_OPTIONS, kind) ?? null) : null;
  const policy = compensa ? (nutritionLabel(CARB_CYCLE_POLICY_OPTIONS, target.carb_cycle_policy) ?? null) : null;
  return dayKind || policy ? { dayKind, policy } : null;
}

/** Texto curto do selo do diário. */
export function diaryCycleText(info: DiaryCycleInfo): string {
  return [info.dayKind, info.policy].filter(Boolean).join(' · ');
}

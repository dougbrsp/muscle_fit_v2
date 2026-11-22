import { describe, expect, it } from 'vitest';
import motor from '@/lib/motor-constants.generated.json';
import {
  cyclePolicyHint,
  cyclePolicyPayload,
  diaryCycleInfo,
  diaryCycleText,
  initialCyclePolicy,
  planPolicyLabel,
} from './nutrition-cycle-policy';

const N = motor.nutrition;
const [VARY, FAT] = N.carb_cycle_policies;
const lab = N.carb_cycle_policy_labels as Record<string, string>;
const kind = N.day_kind_labels as Record<string, string>;

describe('política do ciclo (#242)', () => {
  it('payload: sempre a política escolhida, com ou sem ciclo (nunca null)', () => {
    expect(cyclePolicyPayload(FAT)).toBe(FAT);
    expect(cyclePolicyPayload(VARY)).toBe(VARY);
  });
  it('valor inicial: do plano, sem campo ou desconhecido = padrão', () => {
    expect(initialCyclePolicy(FAT)).toBe(FAT);
    expect(initialCyclePolicy(undefined)).toBe(N.default_carb_cycle_policy);
    expect(initialCyclePolicy('xyz')).toBe(N.default_carb_cycle_policy);
  });
  it('dica vem do espelho', () => {
    expect(cyclePolicyHint(FAT)).toBe((N.carb_cycle_policy_hints as Record<string, string>)[FAT]);
    expect(cyclePolicyHint('xyz')).toBeNull();
  });
  it('resumo: só com ciclo e campo', () => {
    expect(planPolicyLabel({ cycle_days: { '0': 'high' }, carb_cycle_policy: FAT })).toBe(lab[FAT]);
    expect(planPolicyLabel({ cycle_days: null, carb_cycle_policy: FAT })).toBeNull();
    expect(planPolicyLabel({ cycle_days: { '0': 'high' } })).toBeNull();
  });
  it('diário', () => {
    expect(diaryCycleInfo(null)).toBeNull();
    expect(diaryCycleInfo({ day_kind: 'high' })).toBeNull();
    expect(diaryCycleInfo({ day_kind: 'normal', carb_cycle_policy: VARY })).toBeNull();
    expect(diaryCycleInfo({ day_kind: 'low', carb_cycle_policy: VARY })).toEqual({ dayKind: kind.low, policy: null });
    expect(diaryCycleInfo({ day_kind: 'low', carb_cycle_policy: FAT })).toEqual({ dayKind: kind.low, policy: lab[FAT] });
    expect(diaryCycleInfo({ day_kind: null, carb_cycle_policy: FAT })).toEqual({ dayKind: null, policy: lab[FAT] });
    expect(diaryCycleText({ dayKind: 'A', policy: 'B' })).toBe('A · B');
  });
});

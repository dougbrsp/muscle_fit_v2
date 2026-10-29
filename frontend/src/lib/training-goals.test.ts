import { describe, expect, it } from 'vitest';
import parity from './training-goal.parity.json';
import {
  INHERIT_GOAL_VALUE,
  applySlotGoal,
  defaultGoal,
  effectiveItemGoal,
  goalAllowsTechnique,
  goalOption,
  goalSelectData,
  itemFieldPlaceholders,
  itemGoalFromSelect,
  itemGoalSelectData,
  itemGoalSelectValue,
  slotCommonGoal,
  slotEffectiveGoals,
  type TrainingGoalsResponse,
} from './training-goals';

// Payload REAL de GET /training-goals, gerado do backend (nenhum número aqui).
const goals = parity.payload as TrainingGoalsResponse;
const def = defaultGoal(goals)!;
const other = goals.items.find((item) => !item.is_default)!;

describe('precedência do objetivo (paridade com training_goal.resolve_goal)', () => {
  it.each(parity.precedence.map((c) => [c.override, c.template_goal, c.resolved] as const))(
    'override=%s modelo=%s -> %s',
    (override, templateGoal, resolved) => {
      expect(effectiveItemGoal(goals, override, templateGoal)?.value).toBe(resolved);
    },
  );
});

describe('leituras tolerantes', () => {
  it('ausente/desconhecido = default', () => {
    expect(goalOption(goals, null)?.value).toBe(goals.default);
    expect(goalOption(goals, 'xyz')?.value).toBe(goals.default);
    expect(goalOption(goals, ` ${other.value.toUpperCase()} `)?.value).toBe(other.value);
  });
  it('sem a lista não inventa objetivo', () => {
    expect(goalOption(null, other.value)).toBeNull();
    expect(itemFieldPlaceholders(null)).toBeNull();
  });
});

describe('select do item', () => {
  it('herdar mostra o objetivo do Modelo e volta null no payload', () => {
    const data = itemGoalSelectData(goals, other.value);
    expect(data[0]).toEqual({ value: INHERIT_GOAL_VALUE, label: `Herdar do Modelo (${other.label})` });
    expect(data.slice(1)).toEqual(goalSelectData(goals));
    expect(itemGoalFromSelect(goals, INHERIT_GOAL_VALUE)).toBeNull();
    expect(itemGoalFromSelect(goals, other.value)).toBe(other.value);
    expect(itemGoalSelectValue(goals, null)).toBe(INHERIT_GOAL_VALUE);
    expect(itemGoalSelectValue(goals, other.value)).toBe(other.value);
  });
});

describe('objetivo do dia', () => {
  const items = [
    { item_kind: 'equipment', training_goal: null as string | null },
    { item_kind: 'cardio', training_goal: null as string | null },
    { item_kind: 'muscle_group', training_goal: def.value as string | null },
  ];
  it('aplica a todos os itens que aceitam objetivo, sem tocar o cardio', () => {
    const out = applySlotGoal(items, other.value);
    expect(out.map((i) => i.training_goal)).toEqual([other.value, null, other.value]);
    expect(slotCommonGoal(goals, out)).toBe(other.value);
    expect(applySlotGoal(out, null).map((i) => i.training_goal)).toEqual([null, null, null]);
  });
  it('misto = undefined; efetivos na ordem do vocabulário', () => {
    expect(slotCommonGoal(goals, items)).toBeUndefined();
    expect(slotCommonGoal(goals, [{ item_kind: 'cardio' }])).toBeUndefined();
    expect(slotEffectiveGoals(goals, items, other.value).map((g) => g.value)).toEqual(
      goals.items.map((g) => g.value),
    );
  });
});

describe('defaults e técnicas vêm do dono', () => {
  it('placeholders interpolam os defaults do objetivo', () => {
    const p = itemFieldPlaceholders(other)!;
    expect(p.reps).toContain(other.defaults.reps_target);
    expect(p.rpe).toContain(other.defaults.rpe_target);
    expect(p.sets).toBe(String(other.defaults.sets_count));
    expect(itemFieldPlaceholders(def)!.rest).toBe('da geração');
    expect(p.rest).toContain(String(other.defaults.rest_seconds));
  });
  it('técnica fora da política do objetivo', () => {
    for (const code of other.disallowed_techniques) expect(goalAllowsTechnique(other, code)).toBe(false);
    for (const code of other.disallowed_techniques) expect(goalAllowsTechnique(def, code)).toBe(true);
    expect(goalAllowsTechnique(other, null)).toBe(true);
  });
});

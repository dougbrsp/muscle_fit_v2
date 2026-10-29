import { describe, expect, it } from 'vitest';
import { buildRoleActivationWeekly } from './role-activation-weekly';
import type { PreviewActivationGroup, PreviewActivationResponse } from './workout-plan-templates-types';

function g(muscle_group: string, primary: number, secondary = 0, stabilizer = 0): PreviewActivationGroup {
  return {
    muscle_group,
    primary_equivalent: primary,
    secondary_equivalent: secondary,
    stabilizer_equivalent: stabilizer,
    total_equivalent: primary + secondary + stabilizer,
  };
}

/** `days` já na janela de 7 dias que o backend devolve (sequence = slot de cada dia). */
function response(cycleLength: number, days: [number, PreviewActivationGroup[]][]): PreviewActivationResponse {
  return {
    cycle_length: cycleLength,
    window_days: days.length,
    days: days.map(([sequence, groups], index) => ({
      day: index + 1,
      slot_sequence: sequence,
      slot_name: `Slot ${sequence}`,
      slot_type: groups.length ? 'normal' : 'descanso',
      groups,
    })),
  };
}

describe('buildRoleActivationWeekly', () => {
  it('soma por grupo e papel, ordenada pelo total', () => {
    const rows = buildRoleActivationWeekly(
      response(7, [
        [1, [g('Peitoral', 6), g('Tríceps', 0, 3)]],
        [2, [g('Tríceps', 4), g('Abdômen', 0, 0, 2)]],
        [3, []],
        [4, []],
        [5, []],
        [6, []],
        [7, []],
      ]),
    );
    expect(rows).toEqual([
      { muscleGroup: 'Tríceps', primary: 4, secondary: 3, stabilizer: 0, total: 7 },
      { muscleGroup: 'Peitoral', primary: 6, secondary: 0, stabilizer: 0, total: 6 },
      { muscleGroup: 'Abdômen', primary: 0, secondary: 0, stabilizer: 2, total: 2 },
    ]);
  });

  it('ciclo de 8 slots: soma os 7 dias da janela sem escala 7/8', () => {
    const days: [number, PreviewActivationGroup[]][] = [1, 2, 3, 4, 5, 6, 7].map((s) => [s, [g('Costas', 2)]]);
    expect(buildRoleActivationWeekly(response(8, days))).toEqual([
      { muscleGroup: 'Costas', primary: 14, secondary: 0, stabilizer: 0, total: 14 },
    ]);
  });

  it('ciclo de 3 slots: o slot que cai 3 vezes na janela conta 3 vezes', () => {
    const a = [g('Peitoral', 1)];
    const b = [g('Costas', 1)];
    const c = [g('Pernas', 1)];
    const rows = buildRoleActivationWeekly(
      response(3, [[1, a], [2, b], [3, c], [1, a], [2, b], [3, c], [1, a]]),
    );
    expect(rows.map((row) => [row.muscleGroup, row.total])).toEqual([
      ['Peitoral', 3],
      ['Costas', 2],
      ['Pernas', 2],
    ]);
  });

  it('omite grupo zerado; resposta vazia ou nula devolve []', () => {
    expect(buildRoleActivationWeekly(response(1, [[1, [g('Zero', 0)]]]))).toEqual([]);
    expect(buildRoleActivationWeekly(null)).toEqual([]);
    expect(buildRoleActivationWeekly({ cycle_length: 1, days: [], window_days: 0 })).toEqual([]);
  });
});

import { describe, expect, it } from 'vitest';
import { overlayQueuedSets } from './overlay';
import type { OfflineOp } from './op-types';
import type { ActivityDetail } from '../workout-log-types';

function activity(sets: Array<{ set_number: number; client_key?: string }>): ActivityDetail {
  return {
    id: 10,
    local_date: '2026-11-01',
    activity_type: 'strength',
    name: 'Treino',
    exercises: [
      { id: 5, activity_id: 10, sequence: 1, original_name: 'Supino', sets },
      { id: 6, activity_id: 10, sequence: 2, original_name: 'Remada', sets: [] },
    ],
  } as unknown as ActivityDetail;
}

function op(overrides: Partial<OfflineOp>): OfflineOp {
  return {
    opId: 'o1',
    userId: 1,
    kind: 'addSet',
    sessionId: 10,
    exerciseId: 5,
    payload: { set_number: 2, reps: 10, weight_kg: 40 },
    clientKey: 'k2',
    createdAt: 1,
    attempts: 0,
    status: 'saved_local',
    ...overrides,
  };
}

describe('overlayQueuedSets', () => {
  it('sem ops aplicáveis devolve o mesmo objeto', () => {
    const base = activity([{ set_number: 1 }]);
    expect(overlayQueuedSets(base, [])).toBe(base);
    expect(overlayQueuedSets(base, [op({ sessionId: 99 })])).toBe(base);
    expect(overlayQueuedSets(undefined, [op({})])).toBeUndefined();
  });

  it('anexa a série local copiando o corpo como está, marcada com o estado de sincronização', () => {
    const result = overlayQueuedSets(activity([{ set_number: 1 }]), [op({})]);
    const sets = result?.exercises[0].sets as Array<Record<string, unknown>>;
    expect(sets.map((s) => s.set_number)).toEqual([1, 2]);
    expect(sets[1]).toMatchObject({ reps: 10, weight_kg: 40, client_key: 'k2', local_sync_status: 'saved_local' });
    expect(result?.exercises[1].sets).toEqual([]);
  });

  it('não duplica quando o servidor já devolveu a série (por client_key ou por set_number)', () => {
    const byKey = overlayQueuedSets(activity([{ set_number: 1 }, { set_number: 2, client_key: 'k2' }]), [op({ status: 'synced' })]);
    expect(byKey?.exercises[0].sets).toHaveLength(2);
    const byNumber = overlayQueuedSets(activity([{ set_number: 1 }, { set_number: 2 }]), [op({})]);
    expect(byNumber?.exercises[0].sets).toHaveLength(2);
  });

  it('ignora addExercise e série de exercício ainda sem id do servidor', () => {
    const base = activity([]);
    expect(overlayQueuedSets(base, [op({ kind: 'addExercise' }), op({ exerciseId: 'ex:A' })])).toBe(base);
  });
});

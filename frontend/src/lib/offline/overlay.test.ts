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


describe('overlayQueuedSets: exercício criado offline', () => {
  const exOp = (overrides: Partial<OfflineOp> = {}): OfflineOp =>
    op({
      kind: 'addExercise',
      exerciseId: null,
      localId: -1001,
      clientKey: 'ex:A',
      payload: { original_name: 'Crucifixo', workout_plan_exercise_id: 7002, mapping_id: 12 },
      ...overrides,
    });

  it('cria a linha sintética (id local) com as séries pendentes dela', () => {
    const base = activity([{ set_number: 1 }]);
    const result = overlayQueuedSets(base, [exOp(), op({ exerciseId: -1001, clientKey: 'k9', payload: { set_number: 1, reps: 8 } })]);
    expect(result?.exercises).toHaveLength(3);
    const created = result?.exercises[2];
    expect(created).toMatchObject({ id: -1001, original_name: 'Crucifixo', workout_plan_exercise_id: 7002 });
    expect(created?.sets).toHaveLength(1);
  });

  it('copia form_practice do payload (ausente = false)', () => {
    const base = activity([]);
    const marked = overlayQueuedSets(base, [exOp({ payload: { original_name: 'X', form_practice: true } })]);
    expect(marked?.exercises[2].form_practice).toBe(true);
    const plain = overlayQueuedSets(base, [exOp()]);
    expect(plain?.exercises[2].form_practice).toBe(false);
  });

  it('quando o servidor já devolveu o exercício, a série pendente vai para a linha real (sem duplicar o exercício)', () => {
    const base = activity([]);
    const synced = exOp({ status: 'synced', serverResponse: { exercise_id: 6 } });
    const result = overlayQueuedSets(base, [synced, op({ exerciseId: -1001, clientKey: 'k9', payload: { set_number: 1, reps: 8 } })]);
    expect(result?.exercises).toHaveLength(2);
    expect(result?.exercises[1].sets).toHaveLength(1);
  });

  it('#225: continuação e original do mesmo item são duas linhas; a série de cada uma aparece na dela', () => {
    const base = activity([]);
    const cont = exOp({ opId: 'o2', localId: -1002, clientKey: 'ex:B' });
    const result = overlayQueuedSets(base, [
      exOp(),
      cont,
      op({ opId: 's1', exerciseId: -1001, clientKey: 'k1', payload: { set_number: 1, reps: 8 } }),
      op({ opId: 's2', exerciseId: -1002, clientKey: 'k2', payload: { set_number: 1, reps: 9 } }),
    ]);
    const orig = result?.exercises.find((e) => e.id === -1001);
    const next = result?.exercises.find((e) => e.id === -1002);
    expect(orig?.sets.map((s) => s.reps)).toEqual([8]);
    expect(next?.sets.map((s) => s.reps)).toEqual([9]);
  });

  it('#225: duas séries offline da continuação ficam com set_number 1 e 2 na linha local dela', () => {
    const result = overlayQueuedSets(activity([{ set_number: 1 }]), [
      exOp({ opId: 'o2', localId: -1002, clientKey: 'ex:B' }),
      op({ opId: 's1', exerciseId: -1002, clientKey: 'k1', payload: { set_number: 1, reps: 8 } }),
      op({ opId: 's2', exerciseId: -1002, clientKey: 'k2', payload: { set_number: 2, reps: 9 } }),
    ]);
    expect(result?.exercises.find((e) => e.id === -1002)?.sets.map((s) => s.set_number)).toEqual([1, 2]);
  });
});

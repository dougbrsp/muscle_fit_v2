import { describe, expect, it } from 'vitest';
import type { DeloadSession, E1rmPoint, StrengthLift, StrengthPanelResponse } from './dashboard-v3-types';
import { buildUnifiedE1rmChart } from './exercise-progress';
import { strengthLiftRow, strengthPanelRows, topSetText } from './strength-panel';

function ponto(overrides: Partial<E1rmPoint>): E1rmPoint {
  return {
    activity_id: 1,
    local_date: '2026-10-01',
    e1rm: 100,
    e1rm_reps: 5,
    e1rm_reliable: true,
    training_goal: 'hypertrophy',
    same_physical_key: true,
    ...overrides,
  };
}

describe('buildUnifiedE1rmChart (linha única de e1RM, M97 F4)', () => {
  it('ordena por data, destaca força e põe o deload como marca fora da linha', () => {
    const deloads: DeloadSession[] = [
      { activity_id: 9, local_date: '2026-10-05', deload_source: 'manual', deload_label: 'Deload (avulso)' },
    ];
    const chart = buildUnifiedE1rmChart(
      [
        ponto({ activity_id: 3, local_date: '2026-10-08', e1rm: 110, training_goal: 'strength' }),
        ponto({ activity_id: 1, local_date: '2026-10-01', e1rm: 100 }),
      ],
      deloads,
      (goal) => goal === 'strength',
    );
    expect(chart.points.map((p) => p.activityId)).toEqual([1, 9, 3]);
    expect(chart.points.map((p) => p.value)).toEqual([100, null, 110]);
    expect(chart.points.map((p) => p.strengthValue)).toEqual([null, null, 110]);
    expect(chart.points[1]).toMatchObject({ isDeload: true, deloadLabel: 'Deload (avulso)' });
    expect(chart.points.map((p) => p.xKey)).toEqual(['0', '1', '2']);
    expect(chart).toMatchObject({ hasSeries: true, strengthPoints: 1, deloadSessions: 1, otherKeyPoints: 0 });
  });

  it('ponto de outra chave física fica fora e é contado', () => {
    const chart = buildUnifiedE1rmChart([ponto({}), ponto({ activity_id: 2, same_physical_key: false })], []);
    expect(chart.points).toHaveLength(1);
    expect(chart.otherKeyPoints).toBe(1);
    expect(chart.hasSeries).toBe(false);
  });

  it('resposta antiga (sem os campos) não quebra', () => {
    expect(buildUnifiedE1rmChart(undefined, undefined)).toMatchObject({ points: [], hasSeries: false });
  });
});

describe('painel de força', () => {
  const lift: StrengthLift = {
    exercise_id: 7,
    name: 'Agachamento livre',
    strength_sessions: 3,
    e1rm_series: [ponto({ e1rm: 120, local_date: '2026-10-01' }), ponto({ e1rm: 125, local_date: '2026-10-08', training_goal: 'strength' })],
    top_sets: [
      { activity_id: 1, local_date: '2026-10-01', load_kg: 100, reps: 5, top_sets_count: 3, avg_rpe: 8, e1rm: 120, e1rm_reliable: true },
      { activity_id: 2, local_date: '2026-10-08', load_kg: 105, reps: 4, top_sets_count: 2, avg_rpe: 8.5, e1rm: 125, e1rm_reliable: true },
    ],
    best_e1rm: { value: 125, activity_id: 2, local_date: '2026-10-08', reliable: true, max_reliable_reps: 12 },
  };

  it('linha do composto: último e1RM, melhor, último top set', () => {
    const row = strengthLiftRow(lift, 'strength');
    expect(row).toMatchObject({
      exerciseId: 7,
      latestE1rm: 125,
      latestE1rmDate: '2026-10-08',
      bestE1rm: 125,
      strengthSessions: 3,
    });
    expect(row.lastTopSet?.load_kg).toBe(105);
    expect(row.chart.strengthPoints).toBe(1);
    expect(strengthLiftRow(lift).chart.strengthPoints).toBe(0);
  });

  it('top set em texto (carga × reps), travessão sem carga', () => {
    expect(topSetText(lift.top_sets[0])).toMatch(/100.*kg × 5/);
    expect(topSetText(null)).toBe('—');
  });

  it('sem sessão de força: nenhuma linha (a tela esconde o painel)', () => {
    expect(strengthPanelRows(undefined)).toEqual([]);
    expect(strengthPanelRows({ lifts: [] } as unknown as StrengthPanelResponse)).toEqual([]);
  });
});

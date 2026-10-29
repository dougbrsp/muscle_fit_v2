import { describe, expect, it } from 'vitest';
import type { ProgressionSessionItem } from './dashboard-v2-types';
import { buildProgressionChart, isSessionE1rmReliable, sessionLoad, sessionRepsText } from './progression-chart';

function sessao(overrides: Partial<ProgressionSessionItem>): ProgressionSessionItem {
  return {
    activity_id: 1,
    local_date: '2026-09-10',
    activity_name: 'Treino',
    sets_count: 3,
    best_load_kg: 55,
    best_reps: 20,
    avg_rpe: 9,
    e1rm: 90,
    intensity_status: null,
    rpe_target_effective: null,
    failure_allowed: null,
    ...overrides,
  };
}

describe('buildProgressionChart', () => {
  it('plota só as sessões da chave do veredito', () => {
    const chart = buildProgressionChart([
      sessao({ activity_id: 1, in_verdict_key: false, top_load_kg: 80, e1rm: 100, e1rm_reliable: true }),
      sessao({ activity_id: 2, in_verdict_key: true, top_load_kg: 60, e1rm: 84, e1rm_reliable: true }),
      sessao({ activity_id: 3, in_verdict_key: true, top_load_kg: 62, e1rm: 86, e1rm_reliable: true }),
    ]);
    expect(chart.points.map((p) => p.carga)).toEqual([60, 62]);
    expect(chart.otherKeySessions).toBe(1);
  });

  it('carga do gráfico é a de topo, não a da série do melhor e1RM', () => {
    const chart = buildProgressionChart([sessao({ in_verdict_key: true, top_load_kg: 60, best_load_kg: 55 })]);
    expect(chart.points[0].carga).toBe(60);
  });

  it('e1RM de série com mais de 12 reps fica fora da linha e é contado', () => {
    const chart = buildProgressionChart([
      sessao({ in_verdict_key: true, e1rm: 91.7, e1rm_reliable: false }),
      sessao({ in_verdict_key: true, e1rm: 84, e1rm_reliable: true }),
    ]);
    expect(chart.points.map((p) => p.e1rm)).toEqual([null, 84]);
    expect(chart.unreliableE1rm).toBe(1);
  });

  it('backend anterior (sem os campos novos) plota tudo como antes', () => {
    const chart = buildProgressionChart([sessao({ activity_id: 1 }), sessao({ activity_id: 2, e1rm: 95 })]);
    expect(chart.points.map((p) => [p.carga, p.e1rm])).toEqual([
      [55, 90],
      [55, 95],
    ]);
    expect(chart.otherKeySessions).toBe(0);
  });
});

describe('buildProgressionChart — objetivo e deload (M97 F4)', () => {
  it('sessão de força destacada na MESMA linha de e1RM', () => {
    const chart = buildProgressionChart([
      sessao({ activity_id: 1, in_verdict_key: true, e1rm: 90, e1rm_reliable: true, training_goal: 'hypertrophy' }),
      sessao({ activity_id: 2, in_verdict_key: true, e1rm: 95, e1rm_reliable: true, training_goal: 'strength' }),
    ], (goal) => goal === 'strength');
    expect(chart.points.map((p) => p.e1rm)).toEqual([90, 95]);
    expect(chart.points.map((p) => p.e1rmForca)).toEqual([null, 95]);
    expect(chart.strengthPoints).toBe(1);
  });

  it('deload vira marca fora da linha (sem e1RM nem carga), mesmo sem chave do veredito', () => {
    const chart = buildProgressionChart([
      sessao({ activity_id: 1, in_verdict_key: true, e1rm: 90, e1rm_reliable: true }),
      sessao({
        activity_id: 2,
        in_verdict_key: null,
        e1rm: null,
        deload_source: 'period',
        deload_label: 'Deload (período)',
      }),
      sessao({ activity_id: 3, in_verdict_key: false, e1rm: 99 }),
    ]);
    expect(chart.points).toHaveLength(2);
    expect(chart.points[1]).toMatchObject({ deload: true, e1rm: null, carga: null, deloadLabel: 'Deload (período)' });
    expect(chart.deloadSessions).toBe(1);
    expect(chart.otherKeySessions).toBe(1);
    expect(chart.points.map((p) => p.xKey)).toEqual(['0', '1']);
  });
});

describe('colunas da tabela', () => {
  it('Carga = topo; Reps = séries de topo; fallback para os campos legados', () => {
    expect(sessionLoad(sessao({ top_load_kg: 60 }))).toBe(60);
    expect(sessionLoad(sessao({}))).toBe(55);
    expect(sessionRepsText(sessao({ top_set_reps: [10, 10, 9] }))).toBe('10+10+9');
    expect(sessionRepsText(sessao({}))).toBe('20');
    expect(isSessionE1rmReliable(sessao({ e1rm_reliable: false }))).toBe(false);
    expect(isSessionE1rmReliable(sessao({}))).toBe(true);
  });
});

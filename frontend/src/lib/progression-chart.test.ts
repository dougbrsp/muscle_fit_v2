import { describe, expect, it } from 'vitest';
import type { ProgressionSessionItem } from './dashboard-v2-types';
import { buildProgressionChart, formPracticeSessionsNote, isSessionE1rmReliable, sessionLoad, sessionRepsText } from './progression-chart';

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

describe('buildProgressionChart: aprimoramento de técnica (#196)', () => {
  it('rotula a sessão de técnica, sem confundir com deload; sem o campo nada muda', () => {
    // Formato real do backend: a sessão de técnica vem com in_context_run nulo.
    const chart = buildProgressionChart([
      sessao({ activity_id: 0, in_context_run: false, top_load_kg: 99, e1rm: 120, e1rm_reliable: true }),
      sessao({ activity_id: 1, in_context_run: true, top_load_kg: 50, e1rm: 70, e1rm_reliable: true }),
      sessao({ activity_id: 2, in_context_run: null, top_load_kg: 40, e1rm: null, e1rm_reps: null, e1rm_reliable: false, form_practice: true, training_goal: 'strength' }),
      sessao({ activity_id: 3, in_context_run: true, top_load_kg: 60, e1rm: 84, e1rm_reliable: true }),
      sessao({ activity_id: 4, in_context_run: null, top_load_kg: 30, e1rm: null, deload_source: 'manual', deload_label: 'Deload manual' }),
    ], (goal) => goal === 'strength');
    expect(chart.points).toHaveLength(4);
    const [antes, tec, normal, deload] = chart.points;
    expect(antes).toMatchObject({ carga: 50, e1rm: 70 });
    expect(tec).toMatchObject({ formPractice: true, formPracticeLabel: 'Aprimoramento de técnica', e1rm: null, carga: null, e1rmForca: null, deload: false });
    expect(normal).toMatchObject({ formPractice: false, formPracticeLabel: null, e1rm: 84, carga: 60 });
    expect(deload).toMatchObject({ formPractice: false, deload: true, deloadLabel: 'Deload manual', carga: null, e1rm: null });
    expect(chart.unreliableE1rm).toBe(0);
    expect(chart.strengthPoints).toBe(0);
    expect(chart.deloadSessions).toBe(1);
    expect(chart.formPracticeSessions).toBe(1);
    expect(chart.otherKeySessions).toBe(1);
  });

  it('lista sem in_context_run: plota tudo como antes, técnica rotulada', () => {
    const chart = buildProgressionChart([
      sessao({ activity_id: 1 }),
      sessao({ activity_id: 2, form_practice: true, e1rm: null, e1rm_reps: null, e1rm_reliable: false }),
    ]);
    expect(chart.points.map((p) => p.formPractice)).toEqual([false, true]);
    expect(chart.formPracticeSessions).toBe(1);
    expect(chart.otherKeySessions).toBe(0);
  });
});

describe('buildProgressionChart', () => {
  it('plota só in_context_run e destaca in_verdict_run (in_verdict_key é ignorado)', () => {
    const chart = buildProgressionChart([
      // in_verdict_key legado true, mas fora do contexto: fora do gráfico.
      sessao({ activity_id: 1, in_context_run: false, in_verdict_key: true, top_load_kg: 80, e1rm: 100, e1rm_reliable: true }),
      sessao({ activity_id: 2, in_context_run: true, in_verdict_run: false, top_load_kg: 60, e1rm: 84, e1rm_reliable: true }),
      sessao({ activity_id: 3, in_context_run: true, in_verdict_run: true, top_load_kg: 62, e1rm: 86, e1rm_reliable: true }),
    ]);
    expect(chart.points.map((p) => p.carga)).toEqual([60, 62]);
    expect(chart.points.map((p) => p.inVerdict)).toEqual([false, true]);
    expect(chart.otherKeySessions).toBe(1);
  });

  it('calibração: pontos do contexto continuam no gráfico com o veredito vazio', () => {
    const chart = buildProgressionChart([
      sessao({ activity_id: 1, in_context_run: true, in_verdict_run: false, top_load_kg: 60 }),
      sessao({ activity_id: 2, in_context_run: true, in_verdict_run: false, top_load_kg: 62 }),
    ]);
    expect(chart.points.map((p) => p.carga)).toEqual([60, 62]);
    expect(chart.points.every((p) => p.inVerdict === false)).toBe(true);
  });

  it('carga do gráfico é a de topo, não a da série do melhor e1RM', () => {
    const chart = buildProgressionChart([sessao({ in_context_run: true, top_load_kg: 60, best_load_kg: 55 })]);
    expect(chart.points[0].carga).toBe(60);
  });

  it('e1RM de série com mais de 12 reps fica fora da linha e é contado', () => {
    const chart = buildProgressionChart([
      sessao({ in_context_run: true, e1rm: 91.7, e1rm_reliable: false }),
      sessao({ in_context_run: true, e1rm: 84, e1rm_reliable: true }),
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
      sessao({ activity_id: 1, in_context_run: true, e1rm: 90, e1rm_reliable: true, training_goal: 'hypertrophy' }),
      sessao({ activity_id: 2, in_context_run: true, e1rm: 95, e1rm_reliable: true, training_goal: 'strength' }),
    ], (goal) => goal === 'strength');
    expect(chart.points.map((p) => p.e1rm)).toEqual([90, 95]);
    expect(chart.points.map((p) => p.e1rmForca)).toEqual([null, 95]);
    expect(chart.strengthPoints).toBe(1);
  });

  it('deload vira marca fora da linha (sem e1RM nem carga), mesmo sem chave do veredito', () => {
    const chart = buildProgressionChart([
      sessao({ activity_id: 1, in_context_run: true, e1rm: 90, e1rm_reliable: true }),
      sessao({
        activity_id: 2,
        in_context_run: null,
        e1rm: null,
        deload_source: 'period',
        deload_label: 'Deload (período)',
      }),
      sessao({ activity_id: 3, in_context_run: false, e1rm: 99 }),
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

describe('formPracticeSessionsNote', () => {
  it('zero ou ausente não mostra; singular e plural concordam', () => {
    expect(formPracticeSessionsNote(0)).toBe('');
    expect(formPracticeSessionsNote(undefined)).toBe('');
    expect(formPracticeSessionsNote(1)).toBe('1 sessão de aprimoramento de técnica marcada, fora da linha do 1RM estimado.');
    expect(formPracticeSessionsNote(3)).toBe('3 sessões de aprimoramento de técnica marcadas, fora da linha do 1RM estimado.');
  });
});

import { describe, expect, it } from 'vitest';

import {
  formatCurrentGroupFatigueLine,
  formatPreviousSessionRecordLines,
  formatPreviousSessionSets,
  formatRpe,
  selectPreviousSessionRecord,
  selectPreviousSessionWorkingSets,
} from './workout-mode-previous-session';
import { MEASURE_VALUES } from './set-measures';
import type { LastSessionSetsResponse, PreviousSessionSet } from './workout-mode-types';

function set(partial: Partial<PreviousSessionSet> & { set_number: number }): PreviousSessionSet {
  return { set_type: 'working', reps: 10, weight_kg: 70, rpe: 9, ...partial };
}

function response(sets: PreviousSessionSet[]): LastSessionSetsResponse {
  return { item: { activity_id: 1, local_date: '2026-09-20', sets } };
}

describe('selectPreviousSessionWorkingSets', () => {
  it('sem sessao anterior devolve lista vazia', () => {
    expect(selectPreviousSessionWorkingSets({ item: null })).toEqual([]);
    expect(selectPreviousSessionWorkingSets(undefined)).toEqual([]);
  });

  it('descarta aquecimento/ajuste e renumera por POSICAO, nao pelo set_number fisico', () => {
    const selected = selectPreviousSessionWorkingSets(
      response([
        set({ set_number: 1, set_type: 'warmup', reps: 15, weight_kg: 14 }),
        set({ set_number: 2, set_type: 'feeler', reps: 8, weight_kg: 35 }),
        set({ set_number: 3, reps: 11 }),
        set({ set_number: 4, reps: 10 }),
      ]),
    );
    expect(selected.map((row) => row.reps)).toEqual([11, 10]);
    expect(formatPreviousSessionSets(selected, 'kg').map((entry) => entry.position)).toEqual([1, 2]);
  });

  it('ordena por set_number mesmo se a resposta vier fora de ordem', () => {
    const selected = selectPreviousSessionWorkingSets(
      response([set({ set_number: 3, reps: 8 }), set({ set_number: 1, reps: 12 }), set({ set_number: 2, reps: 10 })]),
    );
    expect(selected.map((row) => row.reps)).toEqual([12, 10, 8]);
  });

  it('set_type ausente conta como serie de trabalho; legado dropset e aplicacao de tecnica', () => {
    const selected = selectPreviousSessionWorkingSets(
      response([set({ set_number: 1, set_type: null }), set({ set_number: 2, set_type: 'dropset' })]),
    );
    expect(selected.map((row) => row.set_number)).toEqual([1]);
  });

  it('aplicacoes de tecnica (marcadas ou pelo set_type) nao viram "Serie N"', () => {
    const selected = selectPreviousSessionWorkingSets(
      response([
        set({ set_number: 1, reps: 12 }),
        set({ set_number: 2, reps: 10 }),
        set({ set_number: 3, set_type: 'rest_pause', reps: 4, is_technique_application: true, anchor_set_number: 2 }),
        set({ set_number: 4, set_type: 'drop_set', reps: 6 }),
        set({ set_number: 5, set_type: 'forced_rep', reps: 2 }),
      ]),
    );
    expect(selected.map((row) => row.reps)).toEqual([12, 10]);
    expect(formatPreviousSessionSets(selected, 'kg').map((entry) => entry.position)).toEqual([1, 2]);
  });

  it('backoff e apelidos legados seguem o backend (M76 Lote 3b): backoff nao e "Serie N"', () => {
    const selected = selectPreviousSessionWorkingSets(
      response([
        set({ set_number: 1, set_type: 'warm-up', reps: 15 }),
        set({ set_number: 2, set_type: 'WORKING', reps: 10 }),
        set({ set_number: 3, set_type: 'backoff', reps: 12 }),
        set({ set_number: 4, set_type: 'rest-pause', reps: 4 }),
      ]),
    );
    expect(selected.map((row) => row.reps)).toEqual([10]);
  });
});

describe('formatPreviousSessionSets', () => {
  it('formata na unidade preferida do usuario (M3) com RPE em pt-BR', () => {
    const sets = [
      set({ set_number: 1, reps: 11, weight_kg: 70, rpe: 9.5 }),
      set({ set_number: 2, reps: 10, weight_kg: 70, rpe: 10 }),
    ];
    expect(formatPreviousSessionSets(sets, 'kg').map((entry) => entry.text)).toEqual([
      '1ª série - 11 x 70kg@RPE9,5',
      '2ª série - 10 x 70kg@RPE10',
    ]);
    expect(formatPreviousSessionSets(sets, 'lb')[0].text).toBe('1ª série - 11 x 154,3lb@RPE9,5');
  });

  it('carga decimal com virgula e sem zeros a direita', () => {
    const [quebrada, redonda] = formatPreviousSessionSets(
      [set({ set_number: 1, reps: 10, weight_kg: 24.95, rpe: 8.5 }), set({ set_number: 2, reps: 10, weight_kg: 30.0 })],
      'kg',
    );
    expect(quebrada.text).toBe('1ª série - 10 x 24,95kg@RPE8,5');
    expect(redonda.text).toBe('2ª série - 10 x 30kg@RPE9');
  });

  it('omite carga e RPE ausentes em vez de mostrar valor falso', () => {
    const [semCarga, semRpe] = formatPreviousSessionSets(
      [set({ set_number: 1, reps: 12, weight_kg: null, rpe: 8 }), set({ set_number: 2, reps: 12, rpe: null })],
      'kg',
    );
    expect(semCarga.text).toBe('1ª série - 12 reps@RPE8');
    expect(semRpe.text).toBe('2ª série - 12 x 70kg');
  });
});

describe('selectPreviousSessionRecord + formatPreviousSessionRecordLines', () => {
  function withRecord(item: Partial<NonNullable<LastSessionSetsResponse['item']>>): LastSessionSetsResponse {
    return { item: { activity_id: 1, local_date: '2026-09-20', sets: [], ...item } };
  }

  it('sem historico (ou resposta antiga sem os campos) nada aparece', () => {
    expect(selectPreviousSessionRecord({ item: null })).toBeNull();
    expect(selectPreviousSessionRecord(undefined)).toBeNull();
    expect(selectPreviousSessionRecord(withRecord({}))).toBeNull();
    expect(formatPreviousSessionRecordLines(null, 'kg')).toEqual([]);
  });

  it('recorde e e1RM nesse equipamento em pt-BR (e1RM com 1 casa)', () => {
    const summary = selectPreviousSessionRecord(
      withRecord({ record_scope: 'equipment', record: { reps: 13, weight_kg: 30 }, e1rm_kg: 43.33 }),
    );
    expect(formatPreviousSessionRecordLines(summary, 'kg')).toEqual([
      'Recorde nesse equipamento: 13 x 30kg',
      'e1RM estimado nesse equipamento: 43,3kg',
    ]);
  });

  it('e1RM redondo nao ganha zero a direita; libras convertem', () => {
    const summary = selectPreviousSessionRecord(withRecord({ record: { reps: 13, weight_kg: 30 }, e1rm_kg: 43 }));
    expect(formatPreviousSessionRecordLines(summary, 'kg')[1]).toBe('e1RM estimado nesse equipamento: 43kg');
    expect(formatPreviousSessionRecordLines(summary, 'lb')).toEqual([
      'Recorde nesse equipamento: 13 x 66,1lb',
      'e1RM estimado nesse equipamento: 94,8lb',
    ]);
  });

  it('sem carga (peso corporal): recorde so de reps e sem linha de e1RM', () => {
    const summary = selectPreviousSessionRecord(withRecord({ record: { reps: 15, weight_kg: null }, e1rm_kg: null }));
    expect(formatPreviousSessionRecordLines(summary, 'kg')).toEqual(['Recorde nesse equipamento: 15 reps']);
  });

  it('sem equipamento no match o rotulo e do exercicio', () => {
    const summary = selectPreviousSessionRecord(
      withRecord({ record_scope: 'exercise', record: { reps: 8, weight_kg: 24.95 }, e1rm_kg: null }),
    );
    expect(formatPreviousSessionRecordLines(summary, 'kg')).toEqual(['Recorde nesse exercício: 8 x 24,95kg']);
  });

  it('assistido: o recorde e a menor assistencia e diz isso', () => {
    const summary = selectPreviousSessionRecord(
      withRecord({ record: { reps: 8, weight_kg: 20 }, e1rm_kg: null, load_convention: 'assisted' }),
    );
    expect(summary?.assisted).toBe(true);
    expect(formatPreviousSessionRecordLines(summary, 'kg')).toEqual([
      'Recorde nesse equipamento: 8 x 20kg de assistência',
    ]);
    const semAssistencia = selectPreviousSessionRecord(
      withRecord({ record: { reps: 3, weight_kg: 0 }, e1rm_kg: null, load_convention: 'assisted' }),
    );
    expect(formatPreviousSessionRecordLines(semAssistencia, 'kg')).toEqual([
      'Recorde nesse equipamento: 3 reps sem assistência',
    ]);
  });

  it('sem recorde vigente o backend manda a melhor carga e o rotulo muda (M76 Lote 3d)', () => {
    const summary = selectPreviousSessionRecord(
      withRecord({ record: { reps: 10, weight_kg: 30, kind: 'best_load' }, e1rm_kg: 40 }),
    );
    expect(formatPreviousSessionRecordLines(summary, 'kg')).toEqual([
      'Melhor carga nesse equipamento: 10 x 30kg',
      'e1RM estimado nesse equipamento: 40kg',
    ]);
    const comRecorde = selectPreviousSessionRecord(
      withRecord({ record: { reps: 8, weight_kg: 32.5, kind: 'record' }, e1rm_kg: null }),
    );
    expect(formatPreviousSessionRecordLines(comRecorde, 'kg')).toEqual(['Recorde nesse equipamento: 8 x 32,5kg']);
  });

  it('melhor carga assistida diz a menor assistencia', () => {
    const summary = selectPreviousSessionRecord(
      withRecord({ record: { reps: 6, weight_kg: 25, kind: 'best_load' }, e1rm_kg: null, load_convention: 'assisted' }),
    );
    expect(formatPreviousSessionRecordLines(summary, 'kg')).toEqual([
      'Melhor carga nesse equipamento: 6 x 25kg de assistência',
    ]);
  });

  it('resposta antiga sem load_convention continua no formato de carga total', () => {
    const summary = selectPreviousSessionRecord(withRecord({ record: { reps: 8, weight_kg: 20 }, e1rm_kg: null }));
    expect(summary?.assisted).toBe(false);
    expect(formatPreviousSessionRecordLines(summary, 'kg')).toEqual(['Recorde nesse equipamento: 8 x 20kg']);
  });
});

describe('formatRpe', () => {
  it.each([
    [9.5, '9,5'],
    [10, '10'],
    [null, null],
  ])('%s -> %s', (input, expected) => {
    expect(formatRpe(input as number | null)).toBe(expected);
  });
});

describe('fadiga do grupo (M79, rotulos prontos do backend)', () => {
  const reading = (band_label: string) => ({
    muscle_group_id: 1,
    muscle_group_name: 'Peitoral',
    prior_sets: 3,
    level: 0.3333,
    band: 'pre_fatigued' as const,
    band_label,
  });

  it('acrescenta o rotulo da fadiga antes de cada serie do treino anterior', () => {
    const entries = formatPreviousSessionSets(
      [
        set({ set_number: 1, reps: 11, weight_kg: 45.36, rpe: 10, group_fatigue: reading('Pré-fatigado') }),
        set({ set_number: 2, reps: 10, weight_kg: 45.36, rpe: 10, group_fatigue: reading('Fatigado') }),
        set({ set_number: 3, reps: 9, weight_kg: 45.36, rpe: 10, group_fatigue: null }),
      ],
      'kg',
    );
    expect(entries.map((entry) => entry.text)).toEqual([
      '1ª série - 11 x 45,36kg@RPE10 - Pré-fatigado',
      '2ª série - 10 x 45,36kg@RPE10 - Fatigado',
      '3ª série - 9 x 45,36kg@RPE10',
    ]);
  });

  it('linha da fadiga atual do grupo, mesmo sem sessao anterior', () => {
    expect(formatCurrentGroupFatigueLine({ item: null, current_group_fatigue: reading('Pré-fatigado') })).toBe(
      'Fadiga atual do grupo: Pré-fatigado',
    );
    expect(formatCurrentGroupFatigueLine({ item: null })).toBeNull();
    expect(formatCurrentGroupFatigueLine(undefined)).toBeNull();
  });
});

describe('Lote T5: "Treino anterior" formatado pela medida', () => {
  const DURATION = MEASURE_VALUES.duration;
  const MAX_REPS = MEASURE_VALUES.max_reps;

  it('série por tempo usa o measure_text do servidor; com carga, a carga junto', () => {
    const lines = formatPreviousSessionSets(
      [
        set({ set_number: 1, reps: null, weight_kg: null, rpe: 8, measure: DURATION, duration_seconds: 58, measure_text: '58 s' }),
        set({ set_number: 2, reps: null, weight_kg: 10, rpe: null, measure: DURATION, duration_seconds: 45, measure_text: '45 s' }),
      ],
      'kg',
    );
    expect(lines.map((line) => line.text)).toEqual(['1ª série - 58 s@RPE8', '2ª série - 45 s x 10kg']);
  });

  it('máximo de reps sem measure_text (resposta antiga) cai no espelho do dono', () => {
    const [line] = formatPreviousSessionSets(
      [set({ set_number: 1, reps: 15, weight_kg: 0, rpe: 9, measure: MAX_REPS, duration_seconds: 30 })],
      'kg',
    );
    expect(line.text).toBe('1ª série - 15 reps em 30 s@RPE9');
  });

  it('série de repetições continua igual (com ou sem medida explícita)', () => {
    const plain = formatPreviousSessionSets([set({ set_number: 1 })], 'kg');
    const explicit = formatPreviousSessionSets(
      [set({ set_number: 1, measure: MEASURE_VALUES.reps, measure_text: '10 reps' })],
      'kg',
    );
    expect(plain[0].text).toBe('1ª série - 10 x 70kg@RPE9');
    expect(explicit).toEqual(plain);
  });
});

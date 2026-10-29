import { describe, expect, it } from 'vitest';
import {
  describeSets,
  formatLoggedSets,
  newMeasuredSetFromLast,
  setEditFields,
  setFormFromRow,
  setUpdatePayload,
} from './exercise-set-edit';
import { MEASURE_LIMITS, MEASURE_VALUES } from './set-measures';

describe('edição do registro: round-trip da série (Fase 5 · PR 1)', () => {
  it('carrega e reenvia set_type, pausa, notas, FC e dor sem perder nada', () => {
    const row = {
      set_number: 3,
      set_type: 'drop_set',
      reps: 8,
      weight_kg: '40.00',
      rpe: '9.50',
      rest_seconds: 0,
      notes: 'drop 1',
      hr_avg_bpm: '131.00',
      hr_max_bpm: '150.00',
      muscle_pain: 4,
      joint_pain: 1,
      technique_code: 'drop_set',
      anchor_set_number: 2,
    };

    const payload = setUpdatePayload(setFormFromRow(row, 2), 2);

    expect(payload).toEqual({
      set_number: 3,
      reps: 8,
      weight_kg: 40,
      rpe: 9.5,
      set_type: 'drop_set',
      rest_seconds: 0,
      notes: 'drop 1',
      hr_avg_bpm: 131,
      hr_max_bpm: 150,
      muscle_pain: 4,
      joint_pain: true,
      technique_code: 'drop_set',
      anchor_set_number: 2,
      // Lote T5: a medida vai sempre explícita (série sem medida = reps).
      measure: 'reps',
      duration_seconds: null,
      // M102: lados sempre explícitos (série bilateral = null).
      reps_left: null,
      reps_right: null,
      rpe_left: null,
      rpe_right: null,
    });
  });

  it('série nova (sem campos extras) vai com o default explícito, sem depender do servidor', () => {
    const payload = setUpdatePayload({ set_number: 5, reps: 10, weight_kg: 20, rpe: null }, 4);

    expect(payload).toEqual({
      set_number: 5,
      reps: 10,
      weight_kg: 20,
      rpe: null,
      set_type: 'working',
      rest_seconds: null,
      notes: null,
      hr_avg_bpm: null,
      hr_max_bpm: null,
      muscle_pain: null,
      joint_pain: null,
      technique_code: null,
      anchor_set_number: null,
      measure: 'reps',
      duration_seconds: null,
      // M102: lados sempre explícitos (série bilateral = null).
      reps_left: null,
      reps_right: null,
      rpe_left: null,
      rpe_right: null,
    });
  });

  it('renumerar depois de remover uma série mantém o tipo da série certa', () => {
    const rows = [
      { set_number: 1, set_type: 'warmup', reps: 15 },
      { set_number: 2, set_type: 'working', reps: 10 },
      { set_number: 3, set_type: 'rest_pause', reps: 4 },
    ];
    const form = rows.map(setFormFromRow).filter((_, i) => i !== 1);

    expect(form.map(setUpdatePayload).map((s) => [s.set_number, s.set_type])).toEqual([
      [1, 'warmup'],
      [2, 'rest_pause'],
    ]);
  });

  it('renumerar remapeia a âncora da técnica; âncora removida vai null para o servidor derivar', () => {
    const rows = [
      { set_number: 1, set_type: 'warmup', reps: 15 },
      { set_number: 2, set_type: 'working', reps: 10 },
      { set_number: 3, set_type: 'working', reps: 10 },
      { set_number: 4, set_type: 'forced_rep', reps: 3, technique_code: 'negative_reps', anchor_set_number: 3 },
      { set_number: 5, set_type: 'drop_set', reps: 6, technique_code: 'drop_set', anchor_set_number: 2 },
    ];
    const form = rows
      .map(setFormFromRow)
      .filter((_, i) => i !== 1)
      .map((set, i) => ({ ...set, set_number: i + 1 }));

    expect(
      form.map(setUpdatePayload).map((s) => [s.set_number, s.set_type, s.technique_code, s.anchor_set_number]),
    ).toEqual([
      [1, 'warmup', null, null],
      [2, 'working', null, null],
      [3, 'forced_rep', 'negative_reps', 2],
      [4, 'drop_set', 'drop_set', null],
    ]);
  });
});

describe('Fase 5 · PR 3: tipo visível e aplicações sob a âncora', () => {
  const ROWS = [
    { set_number: 1, set_type: 'warmup', reps: 15, weight_kg: 10, rpe: null },
    { set_number: 2, set_type: 'working', reps: 12, weight_kg: '50.00', rpe: '9.0' },
    { set_number: 3, set_type: 'working', reps: 12, weight_kg: '50.00', rpe: '10.0' },
    { set_number: 4, set_type: 'rest_pause', reps: 6, weight_kg: '50.00', rpe: '10.0', technique_code: 'rest_pause', anchor_set_number: 3 },
    // sem vínculo (Zepp/antigo): âncora = última série de trabalho anterior
    { set_number: 5, set_type: 'rest_pause', reps: 6, weight_kg: '50.00', rpe: null },
    { set_number: 6, set_type: 'forced_rep', reps: 2, weight_kg: '50.00', rpe: null, technique_code: 'forced_reps', anchor_set_number: 2 },
  ];

  it('"Série N" conta só trabalho; aplicações numeradas por âncora', () => {
    expect(describeSets(ROWS).map((row) => [row.label, row.isApplication, row.anchorLabel])).toEqual([
      ['Aquecimento', false, null],
      ['Série 1', false, null],
      ['Série 2', false, null],
      ['Mini-bloco 1', true, 'Série 2'],
      ['Mini-bloco 2', true, 'Série 2'],
      ['Reps extras', true, 'Série 1'],
    ]);
  });

  it('no formulário, o vínculo segue o set_number GRAVADO mesmo após remover uma série', () => {
    const form = ROWS.map(setFormFromRow).filter((set) => set.source_set_number !== 1);
    const display = describeSets(form);
    expect(display[2]).toMatchObject({ label: 'Mini-bloco 1', anchorLabel: 'Série 2', anchorIndex: 1 });
    // e o PUT remapeia a âncora para a nova posição (3 -> 2)
    expect(setUpdatePayload(form[2], 2, form).anchor_set_number).toBe(2);
    // série nova (sem source_set_number) nunca vira âncora por coincidência de número
    const withNew = [...form, { set_number: 6, reps: 10, weight_kg: 20, rpe: null }];
    expect(describeSets(withNew)[2].anchorLabel).toBe('Série 2');
  });

  it('resumo da tabela marca as aplicações como subitens', () => {
    expect(formatLoggedSets(ROWS.slice(1, 4))).toBe(
      'Série 1: 12x 50kg @RPE 9 | Série 2: 12x 50kg @RPE 10 | ↳ Mini-bloco 1: 6x 50kg @RPE 10',
    );
  });
});

describe('Lote T5: medida e segundos no round-trip da edição', () => {
  const PRANCHA = {
    set_number: 1,
    set_type: 'working',
    reps: null,
    weight_kg: '0.00',
    rpe: '8.00',
    measure: MEASURE_VALUES.duration,
    duration_seconds: 58,
    measure_text: '58 s',
  };
  const MAXIMO = {
    set_number: 2,
    set_type: 'working',
    reps: 15,
    weight_kg: null,
    rpe: '9.00',
    measure: MEASURE_VALUES.max_reps,
    duration_seconds: 30,
    measure_text: '15 reps em 30 s',
  };

  it('editar uma série por tempo não apaga a medida nem os segundos e mantém reps nulas', () => {
    const payload = setUpdatePayload(setFormFromRow(PRANCHA, 0), 0);
    expect(payload).toMatchObject({ measure: MEASURE_VALUES.duration, duration_seconds: 58, reps: null });
  });

  it('máximo de reps leva reps e a janela', () => {
    const payload = setUpdatePayload(setFormFromRow(MAXIMO, 1), 1);
    expect(payload).toMatchObject({ measure: MEASURE_VALUES.max_reps, duration_seconds: 30, reps: 15 });
  });

  it('série sem medida (resposta antiga) vai como reps, sem duração', () => {
    const payload = setUpdatePayload(setFormFromRow({ set_number: 1, reps: 10, weight_kg: '20' }, 0), 0);
    expect(payload).toMatchObject({ measure: 'reps', duration_seconds: null, reps: 10 });
  });

  it('renumerar depois de remover uma série reenvia a medida certa de cada linha', () => {
    const form = [setFormFromRow({ set_number: 1, reps: 10, weight_kg: 20 }, 0), setFormFromRow(PRANCHA, 1)];
    const afterRemove = [form[1]];
    expect(afterRemove.map((set, index) => setUpdatePayload(set, index, afterRemove))).toEqual([
      expect.objectContaining({ set_number: 1, measure: MEASURE_VALUES.duration, duration_seconds: 58, reps: null }),
    ]);
  });

  it('campos da linha pela medida: "Tempo (s)" fora de reps, com os limites do dono', () => {
    expect(setEditFields(null)).toMatchObject({ showReps: true, showDuration: false });
    expect(setEditFields(MEASURE_VALUES.duration)).toEqual({
      showReps: false,
      showDuration: true,
      durationMin: MEASURE_LIMITS.durationSecondsMin,
      durationMax: MEASURE_LIMITS.durationSecondsMax,
      repsMin: null,
    });
    expect(setEditFields(MEASURE_VALUES.max_reps)).toEqual({
      showReps: true,
      showDuration: true,
      durationMin: MEASURE_LIMITS.timeWindowSecondsMin,
      durationMax: MEASURE_LIMITS.timeWindowSecondsMax,
      repsMin: MEASURE_LIMITS.maxRepsMinReps,
    });
  });

  it('série adicionada depois de uma por tempo herda a medida; depois de reps, a tela usa a de sempre', () => {
    expect(newMeasuredSetFromLast(setFormFromRow(PRANCHA, 0), 2)).toMatchObject({
      set_number: 2,
      reps: null,
      measure: MEASURE_VALUES.duration,
      duration_seconds: 58,
    });
    expect(newMeasuredSetFromLast(setFormFromRow({ set_number: 1, reps: 10, weight_kg: 20 }, 0), 2)).toBeNull();
    expect(newMeasuredSetFromLast(undefined, 1)).toBeNull();
  });

  it('resumo da tabela usa o texto da medida do servidor', () => {
    expect(formatLoggedSets([PRANCHA, { ...MAXIMO, weight_kg: '10' }])).toBe(
      'Série 1: 58 s @RPE 8 | Série 2: 15 reps em 30 s x 10kg @RPE 9',
    );
    // Sem `measure_text` (resposta antiga com medida), o espelho do dono.
    expect(formatLoggedSets([{ ...PRANCHA, measure_text: null }])).toBe('Série 1: 58 s @RPE 8');
  });
});

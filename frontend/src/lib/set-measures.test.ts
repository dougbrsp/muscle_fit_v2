import { describe, expect, it } from 'vitest';

import motorConstants from './motor-constants.generated.json';
import parity from './set-measures.parity.json';
import {
  DEFAULT_MEASURE,
  MEASURE_LIMITS,
  MEASURE_OPTIONS,
  MEASURE_REASON_LABELS,
  MEASURE_VALUES,
  REASON_MEASURE_NOT_REP_BASED,
  formatSetMeasureText,
  isRepBased,
  measureLabel,
  normalizeMeasure,
  parseMeasure,
  plannedSetExecutionSeconds,
} from './set-measures';

const BLOCK = motorConstants.set_measures;

// Paridade com o dono (`api/app/domain/set_measures.py`): os casos foram
// calculados pelo backend e gravados em `set-measures.parity.json` (Lote T1).
describe('set-measures — paridade com api/app/domain/set_measures.py', () => {
  it.each(parity.normalize)('normaliza $input', (caso) => {
    expect(normalizeMeasure(caso.input)).toBe(caso.normalized);
    expect(isRepBased(caso.input)).toBe(caso.rep_based);
    expect(measureLabel(caso.input)).toBe(caso.label);
  });

  it.each(parity.format)('texto de $measure / $reps / $duration_seconds', (caso) => {
    expect(
      formatSetMeasureText({ measure: caso.measure, reps: caso.reps, durationSeconds: caso.duration_seconds }),
    ).toBe(caso.text);
  });

  // Lote T2: tempo de execução planejado da série (estimativa de tempo do Modelo).
  it.each(parity.planned_execution)(
    'execução planejada de $measure / $duration_seconds_target / $time_window_seconds',
    (caso) => {
      expect(plannedSetExecutionSeconds(caso.measure, caso.duration_seconds_target, caso.time_window_seconds)).toBe(
        caso.seconds,
      );
    },
  );

  it('o vocabulário, o default, os limites e os motivos são os do JSON gerado', () => {
    expect(MEASURE_OPTIONS.map((o) => o.value)).toEqual(BLOCK.measures.map((m) => m.value));
    expect(MEASURE_OPTIONS.map((o) => o.label)).toEqual(BLOCK.measures.map((m) => m.label));
    expect(MEASURE_OPTIONS.map((o) => o.shortLabel)).toEqual(BLOCK.measures.map((m) => m.short_label));
    expect(MEASURE_OPTIONS.map((o) => o.repBased)).toEqual(BLOCK.measures.map((m) => m.rep_based));
    expect(DEFAULT_MEASURE).toBe(BLOCK.default_measure);
    expect(MEASURE_VALUES).toEqual(BLOCK.values);
    expect(MEASURE_LIMITS).toEqual({
      durationSecondsMin: BLOCK.limits.duration_seconds_min,
      durationSecondsMax: BLOCK.limits.duration_seconds_max,
      timeWindowSecondsMin: BLOCK.limits.time_window_seconds_min,
      timeWindowSecondsMax: BLOCK.limits.time_window_seconds_max,
      maxRepsMinReps: BLOCK.limits.max_reps_min_reps,
    });
    expect(REASON_MEASURE_NOT_REP_BASED).toBe(BLOCK.reason_not_rep_based);
    expect(MEASURE_REASON_LABELS).toEqual(BLOCK.reason_labels);
    expect(MEASURE_REASON_LABELS[REASON_MEASURE_NOT_REP_BASED]).toBeTruthy();
  });

  it('só a medida default é baseada em reps; o default existe na lista', () => {
    const repBased = MEASURE_OPTIONS.filter((o) => o.repBased).map((o) => o.value);
    expect(repBased).toEqual([DEFAULT_MEASURE]);
    expect(parseMeasure(DEFAULT_MEASURE)?.value).toBe(DEFAULT_MEASURE);
    expect(parseMeasure(undefined)).toBeNull();
    expect(isRepBased(undefined)).toBe(true);
  });

  it('o texto ignora decimais e booleanos como o dono (asInt)', () => {
    expect(formatSetMeasureText({ measure: MEASURE_VALUES.duration, durationSeconds: 60.5 })).toBe(BLOCK.empty_text);
    expect(formatSetMeasureText({ measure: MEASURE_VALUES.reps, reps: '' })).toBe(BLOCK.empty_text);
    expect(formatSetMeasureText({ measure: MEASURE_VALUES.reps, reps: ' 12 ' })).toBe(
      `12 ${BLOCK.measures.find((m) => m.value === MEASURE_VALUES.reps)?.short_label}`,
    );
  });
});

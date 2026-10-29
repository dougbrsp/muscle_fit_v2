import { describe, expect, it } from 'vitest';
import { SET_LOGGER_FALLBACK_REPS } from './prescription-defaults';
import { DEFAULT_MEASURE, MEASURE_LIMITS, MEASURE_VALUES } from './set-measures';
import {
  executionTimerSpec,
  isDurationSecondsValid,
  isMaxRepsValid,
  measureLoggerDefaults,
  measureSetLabel,
  measureTargetText,
  setMeasureOf,
  setMeasurePayload,
} from './workout-mode-measures';

const { duration: DURATION, max_reps: MAX_REPS, reps: REPS } = MEASURE_VALUES;

describe('setMeasureOf: medida do item da ficha', () => {
  it('ausente = reps (toda ficha de hoje)', () => {
    expect(setMeasureOf({})).toEqual({ measure: DEFAULT_MEASURE, durationSecondsTarget: null, timeWindowSeconds: null });
    expect(setMeasureOf(null).measure).toBe(DEFAULT_MEASURE);
  });

  it('lê a linha da ficha e prefere a prescrição congelada quando ela traz a medida', () => {
    expect(setMeasureOf({ measure: DURATION, duration_seconds_target: 60 })).toEqual({
      measure: DURATION,
      durationSecondsTarget: 60,
      timeWindowSeconds: null,
    });
    expect(
      setMeasureOf({ measure: REPS, prescription: { measure: MAX_REPS, time_window_seconds: 30 } }),
    ).toEqual({ measure: MAX_REPS, durationSecondsTarget: null, timeWindowSeconds: 30 });
    // Snapshot antigo sem `measure`: vale a linha.
    expect(setMeasureOf({ measure: DURATION, duration_seconds_target: 45, prescription: {} }).measure).toBe(DURATION);
  });

  it('medida desconhecida cai em reps (leitura tolerante do dono)', () => {
    expect(setMeasureOf({ measure: 'xyz' }).measure).toBe(REPS);
  });
});

describe('measureLoggerDefaults: campos e valores iniciais do card', () => {
  it('reps: IA > última série > alvo > fallback do backend, como antes do T5', () => {
    const base = { measure: REPS, repsTarget: '8-12' };
    expect(measureLoggerDefaults({ ...base, suggestedReps: 9.6, lastSet: { reps: 7 } }).reps).toBe(10);
    expect(measureLoggerDefaults({ ...base, lastSet: { reps: 7 } }).reps).toBe(7);
    expect(measureLoggerDefaults(base).reps).toBe(8);
    expect(measureLoggerDefaults({ measure: null }).reps).toBe(SET_LOGGER_FALLBACK_REPS);
    expect(measureLoggerDefaults(base)).toMatchObject({
      loadOptional: false,
      repsRequired: true,
      durationRequired: false,
      durationSeconds: null,
      windowSeconds: null,
    });
  });

  it('tempo: sem reps, segundos da última série ou do alvo, carga opcional', () => {
    expect(measureLoggerDefaults({ measure: DURATION, durationSecondsTarget: 60, suggestedReps: 10 })).toEqual({
      reps: null,
      durationSeconds: 60,
      windowSeconds: null,
      loadOptional: true,
      repsRequired: false,
      durationRequired: true,
    });
    expect(
      measureLoggerDefaults({ measure: DURATION, durationSecondsTarget: 60, lastSet: { duration_seconds: 52 } })
        .durationSeconds,
    ).toBe(52);
  });

  it('máximo de reps: reps obrigatórias sem alvo, janela do item, carga opcional', () => {
    expect(measureLoggerDefaults({ measure: MAX_REPS, timeWindowSeconds: 30, repsTarget: '8-12' })).toEqual({
      reps: null,
      durationSeconds: null,
      windowSeconds: 30,
      loadOptional: true,
      repsRequired: true,
      durationRequired: false,
    });
    expect(measureLoggerDefaults({ measure: MAX_REPS, lastSet: { reps: 14 } }).reps).toBe(14);
  });
});

describe('executionTimerSpec: cronômetro de execução', () => {
  it('reps não tem cronômetro', () => {
    expect(executionTimerSpec({ measure: REPS, durationSecondsTarget: 60 })).toEqual({ mode: 'none', targetSeconds: null });
  });
  it('tempo: regressivo até o alvo; sem alvo, livre', () => {
    expect(executionTimerSpec({ measure: DURATION, durationSecondsTarget: 60 })).toEqual({ mode: 'countdown', targetSeconds: 60 });
    expect(executionTimerSpec({ measure: DURATION })).toEqual({ mode: 'stopwatch', targetSeconds: null });
  });
  it('máximo: regressivo da janela; sem janela, livre', () => {
    expect(executionTimerSpec({ measure: MAX_REPS, timeWindowSeconds: 30 })).toEqual({ mode: 'countdown', targetSeconds: 30 });
    expect(executionTimerSpec({ measure: MAX_REPS })).toEqual({ mode: 'stopwatch', targetSeconds: null });
  });
});

describe('texto do alvo e rótulo da série', () => {
  it('"Série 1 · 60 s" / "reps máx. em 30 s"; reps fica como veio', () => {
    expect(measureTargetText({ measure: DURATION, durationSecondsTarget: 60 })).toBe('60 s');
    expect(measureTargetText({ measure: MAX_REPS, timeWindowSeconds: 30 })).toBe('reps máx. em 30 s');
    expect(measureTargetText({ measure: MAX_REPS })).toBe('reps máx.');
    expect(measureTargetText({ measure: REPS })).toBeNull();
    expect(measureSetLabel('Série 1 de 3', { measure: DURATION, durationSecondsTarget: 60 })).toBe('Série 1 de 3 · 60 s');
    expect(measureSetLabel('Série 1 de 3', { measure: null })).toBe('Série 1 de 3');
  });
});

describe('setMeasurePayload: chaves de medida do POST da série', () => {
  it('reps (ou ausente) não acrescenta nada: payload idêntico ao de antes do T5', () => {
    expect(setMeasurePayload({})).toEqual({});
    expect(setMeasurePayload({ measure: REPS, duration_seconds: 30 })).toEqual({});
  });

  it('o POST de uma série de reps é o MESMO objeto de antes', () => {
    // Como o card devolve uma série de reps: sem `measure` nem `duration_seconds`.
    const values: { reps: number; weight_kg: number; rpe: number; measure?: string } = { reps: 10, weight_kg: 40, rpe: 8 };
    const before = { set_number: 1, reps: values.reps, weight_kg: values.weight_kg, rpe: values.rpe, rest_seconds: 90 };
    const after = {
      set_number: 1,
      reps: values.reps,
      weight_kg: values.weight_kg,
      rpe: values.rpe,
      ...setMeasurePayload(values),
      rest_seconds: 90,
    };
    expect(JSON.stringify(after)).toBe(JSON.stringify(before));
  });

  it('tempo e máximo mandam a medida explícita e os segundos', () => {
    expect(setMeasurePayload({ measure: DURATION, duration_seconds: 58 })).toEqual({ measure: DURATION, duration_seconds: 58 });
    expect(setMeasurePayload({ measure: MAX_REPS, duration_seconds: null })).toEqual({ measure: MAX_REPS, duration_seconds: null });
  });
});

describe('isDurationSecondsValid: limites do dono', () => {
  it('inteiro dentro de DURATION_SECONDS_MIN/MAX', () => {
    expect(isDurationSecondsValid(MEASURE_LIMITS.durationSecondsMin)).toBe(true);
    expect(isDurationSecondsValid(MEASURE_LIMITS.durationSecondsMax)).toBe(true);
    expect(isDurationSecondsValid(MEASURE_LIMITS.durationSecondsMin - 1)).toBe(false);
    expect(isDurationSecondsValid(MEASURE_LIMITS.durationSecondsMax + 1)).toBe(false);
    expect(isDurationSecondsValid(null)).toBe(false);
  });
});

describe('isMaxRepsValid: piso do dono', () => {
  it('reps >= MAX_REPS_MIN_REPS', () => {
    expect(isMaxRepsValid(MEASURE_LIMITS.maxRepsMinReps)).toBe(true);
    expect(isMaxRepsValid(MEASURE_LIMITS.maxRepsMinReps - 1)).toBe(false);
    expect(isMaxRepsValid(null)).toBe(false);
  });
});

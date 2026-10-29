import { describe, expect, it } from 'vitest';

import motorConstants from './motor-constants.generated.json';
import {
  DEFAULT_MAX_REST_SECONDS,
  DEFAULT_MIN_REST_SECONDS,
  DEFAULT_REPS_TARGET,
  DEFAULT_REST_SECONDS,
  DEFAULT_RPE_TARGET,
  MIN_SETS_COUNT,
  SET_LOGGER_FALLBACK_LOAD_KG,
  SET_LOGGER_FALLBACK_REPS,
} from './prescription-defaults';
import { REST_WHEN_UNDEFINED_SECONDS } from './template-time-estimate';

// M76 Lote 4c (E8): o dono é `api/app/domain/prescription_defaults.py`.
describe('defaults de prescrição', () => {
  it('lê os valores do dono exportados em motor-constants.generated.json', () => {
    const p = motorConstants.prescription;
    expect(DEFAULT_REPS_TARGET).toBe(p.default_reps_target);
    expect(DEFAULT_RPE_TARGET).toBe(p.default_rpe_target);
    expect(DEFAULT_MIN_REST_SECONDS).toBe(p.default_min_rest_seconds);
    expect(DEFAULT_MAX_REST_SECONDS).toBe(p.default_max_rest_seconds);
    expect(DEFAULT_REST_SECONDS).toBe(p.default_rest_seconds);
    expect(MIN_SETS_COUNT).toBe(p.min_sets_count);
    expect(SET_LOGGER_FALLBACK_REPS).toBe(p.set_logger_fallback_reps);
    expect(SET_LOGGER_FALLBACK_LOAD_KG).toBe(p.set_logger_fallback_load_kg);
  });

  it('piso de séries e valores iniciais do registro da série não mudaram (B2 da Evolução ZEPP)', () => {
    // Antes escritos à mão na tela do Modo Treino (`?? 1`/`|| 1`, `?? 10`, `?? 20`).
    expect(MIN_SETS_COUNT).toBe(1);
    expect(SET_LOGGER_FALLBACK_REPS).toBe(10);
    expect(SET_LOGGER_FALLBACK_LOAD_KG).toBe(20);
  });

  it('descanso default do timer não é o "sem descanso" da estimativa de tempo', () => {
    expect(DEFAULT_REST_SECONDS).toBeGreaterThan(0);
    expect(REST_WHEN_UNDEFINED_SECONDS).toBe(0);
  });
});

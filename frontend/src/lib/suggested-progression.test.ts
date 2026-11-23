import { describe, expect, it } from 'vitest';

import {
  calibrationLabel,
  suggestedCalibration,
  suggestedProgressionTargets,
  suggestionCalibrationLabel,
} from './suggested-progression';
import { parseNumericTarget, type SuggestedProgressionResponse } from './workout-mode-types';

// Resposta real do motor v2 com o grupo em NO_DIRECT_WORK (decisão SKIP):
// sem faixa de reps, carga de calibração, teto 8, falha não permitida.
const skip: SuggestedProgressionResponse = {
  reps_target: null,
  load_target: '45,25kg',
  rpe_target: '8',
  progression_note: 'Grupo sem trabalho direto pela recuperação: carga mantida, sem progressão.',
  has_history: true,
  suggested_reps: null,
  suggested_load_kg: 45.25,
  decision: 'SKIP',
  effective_rpe_ceiling: 8,
  failure_allowed: false,
};

describe('suggestedProgressionTargets', () => {
  it('reps_target nulo (SKIP) vira string vazia, sem inventar faixa, e mantém a nota do motor', () => {
    const targets = suggestedProgressionTargets(skip);
    expect(targets).toEqual({
      reps_target: '',
      load_target: '45,25kg',
      rpe_target: '8',
      progression_note: 'Grupo sem trabalho direto pela recuperação: carga mantida, sem progressão.',
    });
    // Os consumidores fazem parse do texto: vazio não quebra e não vira número.
    expect(parseNumericTarget(targets.reps_target)).toBeNull();
  });

  it('string vazia do backend e campos ausentes também viram vazio', () => {
    const empty = { ...skip, reps_target: '', load_target: undefined, rpe_target: null, progression_note: '  ' };
    expect(suggestedProgressionTargets(empty as unknown as SuggestedProgressionResponse)).toEqual({
      reps_target: '',
      load_target: '',
      rpe_target: '',
      progression_note: null,
    });
  });

  it('resposta normal passa como veio', () => {
    expect(suggestedProgressionTargets({ ...skip, reps_target: '10-12', decision: 'MAINTAIN' }).reps_target).toBe('10-12');
  });
});

describe('calibração por academia (#188)', () => {
  it('API antiga sem os campos: sem calibração e sem selo', () => {
    expect(suggestedCalibration(skip)).toEqual({ calibrating: false, calibration_code: null, load_source: null });
    expect(suggestionCalibrationLabel(skip)).toBeNull();
    expect(suggestionCalibrationLabel(null)).toBeNull();
  });

  it('leva adiante os campos e rotula pelo código do motor', () => {
    const outra: SuggestedProgressionResponse = {
      ...skip,
      calibrating: true,
      calibration_code: 'calibration_start_other_gym',
      load_source: 'other_gym_start',
    };
    expect(suggestedCalibration(outra)).toEqual({
      calibrating: true,
      calibration_code: 'calibration_start_other_gym',
      load_source: 'other_gym_start',
    });
    expect(suggestionCalibrationLabel(outra)).toBe('Carga de partida de outra academia');
    expect(suggestionCalibrationLabel({ ...skip, calibrating: true, calibration_code: 'context_calibration' })).toBe(
      'Calibrando',
    );
  });

  it('calibrando sem código vira "Calibrando"; código desconhecido fica sem selo', () => {
    expect(calibrationLabel(null, true)).toBe('Calibrando');
    expect(calibrationLabel('codigo_novo', true)).toBeNull();
  });

  it('não altera os alvos em texto (paridade do Modo Treino)', () => {
    expect(suggestedProgressionTargets({ ...skip, calibrating: true, calibration_code: 'context_calibration' })).toEqual(
      suggestedProgressionTargets(skip),
    );
  });
});

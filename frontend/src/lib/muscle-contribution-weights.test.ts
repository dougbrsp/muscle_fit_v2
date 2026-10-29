import { describe, expect, it } from 'vitest';
import motorConstants from './motor-constants.generated.json';
import { defaultWeightsForRole } from './muscle-contribution-weights';

describe('defaultWeightsForRole (M76 Lote 3c)', () => {
  it('lê os defaults do dono exportados em motor-constants.generated.json', () => {
    const weights = motorConstants.muscle_contributions.default_write_weights;
    for (const role of ['PRIMARY', 'SECONDARY', 'STABILIZER'] as const) {
      expect(defaultWeightsForRole(role)).toEqual({ stimulus: weights[role], fatigue: weights[role] });
    }
  });

  it('mantém a ordem primário > secundário > estabilizador', () => {
    const primary = defaultWeightsForRole('PRIMARY').stimulus;
    const secondary = defaultWeightsForRole('SECONDARY').stimulus;
    const stabilizer = defaultWeightsForRole('STABILIZER').stimulus;
    expect(primary).toBeGreaterThan(secondary);
    expect(secondary).toBeGreaterThan(stabilizer);
  });

  it('papel desconhecido cai no peso de estabilizador', () => {
    expect(defaultWeightsForRole('OUTRO')).toEqual(defaultWeightsForRole('STABILIZER'));
  });
});

import { describe, expect, it } from 'vitest';

import { suggestedProgressionTargets } from './suggested-progression';
import { parseNumericTarget, type SuggestedProgressionResponse } from './workout-mode-types';

// Resposta real do motor v2 com o grupo em NO_DIRECT_WORK (decisão SKIP):
// sem faixa de reps, carga de calibração, teto 8, falha não permitida.
const skip: SuggestedProgressionResponse = {
  reps_target: null,
  load_target: '45,25kg',
  rpe_target: '8',
  progression_note: 'Grupo sem trabalho direto pela recuperação: sem progressão nesta sessão.',
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
      progression_note: 'Grupo sem trabalho direto pela recuperação: sem progressão nesta sessão.',
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

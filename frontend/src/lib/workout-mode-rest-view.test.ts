import { describe, expect, it } from 'vitest';
import type { SavedSetIntensity } from './set-engine-status';
import { restSavedSetView } from './workout-mode-rest-view';

const SAVED: SavedSetIntensity = {
  intensity_status: 'BELOW_TARGET',
  intensity_status_label: 'Esforço abaixo do alvo',
  reps_position: 'AT_TOP',
  reps_position_label: 'Reps no teto da faixa',
  next_set_load: {
    direction: 'INCREASE_LOAD',
    load_kg: 47.5,
    reason: 'NEXT_SET_LOW_RPE_AT_REPS',
    reason_label: 'Esforço abaixo do alvo com reps no teto',
    rule_text: 'Se fizer 12 reps ou mais com RPE abaixo de 9, sobe a carga.',
  },
};

describe('restSavedSetView — série salva + recomendação do motor', () => {
  it('sem confirmação do servidor: nem chip "SÉRIE SALVA", nem selos, nem recomendação', () => {
    const view = restSavedSetView({ setSaved: false, saved: SAVED, isExerciseTransition: false, weightUnit: 'kg' });
    expect(view).toEqual({ showSavedChip: false, badges: [], engine: null });
  });

  it('série salva: chip, selos do motor e a carga da próxima série vinda do dado', () => {
    const view = restSavedSetView({ setSaved: true, saved: SAVED, isExerciseTransition: false, weightUnit: 'kg' });
    expect(view.showSavedChip).toBe(true);
    expect(view.badges.map((badge) => badge.label)).toEqual(['Esforço abaixo do alvo', 'Reps no teto da faixa']);
    expect(view.engine).toMatchObject({
      direction: 'UP',
      loadText: '47.5 kg',
      summary: `Próxima série: ${view.engine?.actionLabel} → 47.5 kg`,
      reason: 'Esforço abaixo do alvo com reps no teto',
      explanation: 'Se fizer 12 reps ou mais com RPE abaixo de 9, sobe a carga.',
    });
  });

  it('a carga acompanha o dado do motor e a unidade do usuário (nada fixo)', () => {
    const other = { ...SAVED, next_set_load: { ...SAVED.next_set_load!, load_kg: 20 } };
    expect(restSavedSetView({ setSaved: true, saved: other, isExerciseTransition: false, weightUnit: 'kg' }).engine?.loadText).toBe(
      '20 kg',
    );
    expect(
      restSavedSetView({ setSaved: true, saved: other, isExerciseTransition: false, weightUnit: 'lb' }).engine?.loadText,
    ).toMatch(/lb$/);
  });

  it('reduzir sem carga: só a ação; sem regra casada: sem "Ver explicação"', () => {
    const down = {
      next_set_load: { direction: 'DECREASE_LOAD', load_kg: null, reason: 'NEXT_SET_PAIN' },
    } satisfies SavedSetIntensity;
    const engine = restSavedSetView({ setSaved: true, saved: down, isExerciseTransition: false, weightUnit: 'kg' }).engine;
    expect(engine?.direction).toBe('DOWN');
    expect(engine?.loadText).toBeNull();
    expect(engine?.summary).not.toContain('→');
    expect(engine?.explanation).toBeNull();
  });

  it('transição de exercício: sem "próxima série" deste exercício', () => {
    const view = restSavedSetView({ setSaved: true, saved: SAVED, isExerciseTransition: true, weightUnit: 'kg' });
    expect(view.showSavedChip).toBe(true);
    expect(view.engine).toBeNull();
  });

  it('etapa gravada sem resposta do motor: só o chip', () => {
    expect(restSavedSetView({ setSaved: true, saved: null, isExerciseTransition: false, weightUnit: 'kg' })).toEqual({
      showSavedChip: true,
      badges: [],
      engine: null,
    });
  });
});

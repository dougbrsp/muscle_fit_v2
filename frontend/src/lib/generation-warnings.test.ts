import { describe, expect, it } from 'vitest';
import { exerciseWithGroupLabel, recoveryExcludedGroups } from './generation-warnings';

describe('recoveryExcludedGroups', () => {
  it('lista o grupo muscular, sem duplicar', () => {
    expect(
      recoveryExcludedGroups([
        { exercise_name: 'Supino', muscle_primary: 'Peitoral' },
        { exercise_name: 'Crucifixo', muscle_primary: 'Peitoral' },
        { muscle_primary: 'Costas' },
      ]),
    ).toEqual(['Peitoral', 'Costas']);
  });
  it('cai para exercise_name se faltar o grupo e ignora vazios', () => {
    expect(
      recoveryExcludedGroups([{ exercise_name: 'Supino' }, { muscle_primary: '  ' }, {}, { muscle_primary: null }]),
    ).toEqual(['Supino']);
  });
  it('tolera null/undefined', () => {
    expect(recoveryExcludedGroups(undefined)).toEqual([]);
    expect(recoveryExcludedGroups(null)).toEqual([]);
  });
});

describe('exerciseWithGroupLabel', () => {
  it('sem espaço solto', () => {
    expect(exerciseWithGroupLabel({ exercise_name: 'Supino', muscle_primary: 'Peitoral' })).toBe('Supino (Peitoral)');
    expect(exerciseWithGroupLabel({ exercise_name: 'Supino' })).toBe('Supino');
    expect(exerciseWithGroupLabel({ muscle_primary: 'Peitoral' })).toBe('Peitoral');
    expect(exerciseWithGroupLabel({})).toBe('');
    expect(exerciseWithGroupLabel(null)).toBe('');
  });
});

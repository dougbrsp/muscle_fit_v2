import { describe, expect, it } from 'vitest';
import vocabulary from './set-types.generated.json';
import {
  isPreparatorySetType,
  isTechniqueApplicationSetType,
  isWorkingSetType,
  normalizeSetType,
} from './set-types';

// Paridade com o dono (`api/app/set_types.py`): os casos foram calculados
// pelo backend e gravados em `set-types.generated.json` (M76, Lote 3b).
describe('set-types — paridade com api/app/set_types.py', () => {
  it.each(vocabulary.cases)('$input', (caso) => {
    expect(normalizeSetType(caso.input)).toBe(caso.normalized);
    expect(isWorkingSetType(caso.input)).toBe(caso.is_working);
    expect(isPreparatorySetType(caso.input)).toBe(caso.is_preparatory);
    expect(isTechniqueApplicationSetType(caso.input)).toBe(caso.is_technique_application);
  });

  it('backoff é trabalho reduzido, não série de trabalho', () => {
    expect(isWorkingSetType('backoff')).toBe(false);
    expect(isPreparatorySetType('backoff')).toBe(false);
    expect(isTechniqueApplicationSetType('backoff')).toBe(false);
  });

  it('undefined vale como legado sem tipo', () => {
    expect(normalizeSetType(undefined)).toBe('working');
  });
});

import { describe, expect, it } from 'vitest';

import type { MappingCanonicalOptionItem } from './mappings-types';
import {
  ASSOCIATION_INVALIDATED_QUERY_KEYS,
  canonicalOptionMatches,
  formatIsoDate,
  normalizeSearch,
  rowsFromUnmappedByName,
} from './unmapped-exercises';

function option(overrides: Partial<MappingCanonicalOptionItem>): MappingCanonicalOptionItem {
  return {
    id: 15,
    exercise_name: 'Extensão de tríceps com apoio de braços na máquina',
    amazfit_name: 'Triceps Extension Machine',
    primary_group: 'Tríceps',
    secondary_group: null,
    primary_muscles: null,
    secondary_muscles: null,
    ...overrides,
  };
}

describe('rowsFromUnmappedByName', () => {
  it('mapeia o formato agrupado por nome', () => {
    expect(
      rowsFromUnmappedByName([
        { original_name: 'Triceps Curl', exercises_count: 4, sets_count: 12, first_seen: '2025-01-02', last_seen: '2026-09-01' },
      ]),
    ).toEqual([{ name: 'Triceps Curl', sets: 12, exercisesCount: 4, firstSeen: '2025-01-02', lastSeen: '2026-09-01' }]);
    expect(rowsFromUnmappedByName(undefined)).toEqual([]);
  });
});

describe('canonicalOptionMatches', () => {
  it('ignora acento, caixa e ordem dos termos', () => {
    expect(canonicalOptionMatches(option({}), 'triceps maquina')).toBe(true);
    expect(canonicalOptionMatches(option({}), 'MÁQUINA extensão')).toBe(true);
  });

  it('busca no nome Amazfit e no grupo primário', () => {
    expect(canonicalOptionMatches(option({ exercise_name: 'X' }), 'extension machine')).toBe(true);
    expect(canonicalOptionMatches(option({ exercise_name: 'X', amazfit_name: null }), 'triceps')).toBe(true);
  });

  it('não casa termo ausente e aceita busca vazia', () => {
    expect(canonicalOptionMatches(option({}), 'supino')).toBe(false);
    expect(canonicalOptionMatches(option({}), '   ')).toBe(true);
  });
});

describe('utilitários', () => {
  it('normalizeSearch segue o normalize do backend', () => {
    expect(normalizeSearch('  Tríceps-Pulley  (corda) ')).toBe('triceps pulley corda');
  });

  it('formatIsoDate não depende de fuso', () => {
    expect(formatIsoDate('2026-01-01')).toBe('01/01/2026');
    expect(formatIsoDate(null)).toBe('—');
  });

  it('invalida lista, tendência e opções', () => {
    expect(ASSOCIATION_INVALIDATED_QUERY_KEYS.map((key) => key[0])).toEqual([
      'mapping-canonical-unmapped',
      'muscle-groups-mapped-trend',
      'mapping-canonical-options',
    ]);
  });
});

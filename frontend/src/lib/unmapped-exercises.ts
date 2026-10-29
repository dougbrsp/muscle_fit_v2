// Regras puras da associação de exercícios sem classificação canônica
// (componente `components/exercise-association`), usadas pelas telas
// Domínio de exercícios (aba "Sem classificação").

import type { MappingCanonicalOptionItem, UnmappedExerciseByNameItem } from './mappings-types';

// Linha exibida na tabela, independente da origem dos dados.
export type UnmappedExerciseRow = {
  name: string;
  sets: number;
  exercisesCount?: number;
  firstSeen?: string | null;
  lastSeen?: string | null;
};

// Domínio de exercícios: GET /mapping-canonical/unmapped?group_by=name (sem janela).
export function rowsFromUnmappedByName(rows: UnmappedExerciseByNameItem[] | undefined): UnmappedExerciseRow[] {
  return (rows || []).map((row) => ({
    name: row.original_name || '',
    sets: row.sets_count,
    exercisesCount: row.exercises_count,
    firstSeen: row.first_seen,
    lastSeen: row.last_seen,
  }));
}

// Mesmo critério do backend (`normalize`): minúsculas, sem acento, só
// letras/dígitos separados por um espaço.
export function normalizeSearch(value: string | null | undefined): string {
  return (value || '')
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

// Busca do exercício canônico no modal: todos os termos precisam aparecer no
// nome, no nome Amazfit ou no grupo primário (em qualquer ordem).
export function canonicalOptionMatches(item: MappingCanonicalOptionItem, search: string): boolean {
  const terms = normalizeSearch(search).split(' ').filter(Boolean);
  if (terms.length === 0) return true;
  const haystack = normalizeSearch([item.exercise_name, item.amazfit_name, item.primary_group].join(' '));
  return terms.every((term) => haystack.includes(term));
}

// 'YYYY-MM-DD' -> 'DD/MM/YYYY' sem passar por Date (evita deslocamento de fuso).
export function formatIsoDate(value: string | null | undefined): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value || '');
  return match ? `${match[3]}/${match[2]}/${match[1]}` : '—';
}

// Queries afetadas por uma associação (prefixos do React Query): a lista sem
// janela, a tendência -mapped-trend (Dashboard > Músculos) e as opções do
// catálogo.
export const ASSOCIATION_INVALIDATED_QUERY_KEYS: readonly (readonly string[])[] = [
  ['mapping-canonical-unmapped'],
  ['muscle-groups-mapped-trend'],
  ['mapping-canonical-options'],
];

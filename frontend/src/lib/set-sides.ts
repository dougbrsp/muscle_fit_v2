// M102 — reps e RPE por lado em exercício unilateral.
//
// Dono da regra: `api/app/domain/set_sides.py` (reps/RPE oficiais = pior lado,
// calculados SEMPRE no backend ao gravar). Aqui não há regra numérica: só o
// transporte dos lados no payload e a leitura do texto pronto da API
// (`sides_text`, "reps E 10 · D 8; RPE E 8 · D 9"). A tela nunca recalcula o
// canônico (guarda `pior_lado`).

import setTypeVocabulary from './set-types.generated.json';
import { isWorkingSetType, normalizeSetType } from './set-types';

export const SIDE_FIELDS = ['reps_left', 'reps_right', 'rpe_left', 'rpe_right'] as const;

export type SideField = (typeof SIDE_FIELDS)[number];

export type SetSides = Partial<Record<SideField, number | null>>;

function toNumberOrNull(value: unknown): number | null {
  if (value == null || value === '') return null;
  const parsed = Number(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/** A série tem algum lado registrado. */
export function hasSides(set: Partial<Record<SideField, unknown>> | null | undefined): boolean {
  if (!set) return false;
  return SIDE_FIELDS.some((field) => toNumberOrNull(set[field]) != null);
}

/** Lados lidos da API (DECIMAL chega como string) em números. */
export function sidesFromRow(set: Partial<Record<SideField, unknown>> | null | undefined): Record<SideField, number | null> {
  return {
    reps_left: toNumberOrNull(set?.reps_left),
    reps_right: toNumberOrNull(set?.reps_right),
    rpe_left: toNumberOrNull(set?.rpe_left),
    rpe_right: toNumberOrNull(set?.rpe_right),
  };
}

/**
 * Chaves dos lados no POST/PATCH/PUT da série. Sem lado = `{}`: o payload da
 * série bilateral continua idêntico ao de antes (relógio e clientes antigos
 * também não mandam lados).
 */
export function setSidesPayload(values: SetSides | null | undefined): SetSides {
  if (!hasSides(values)) return {};
  return sidesFromRow(values);
}

/** Valores oficiais que o servidor gravou (pior lado), se a resposta os trouxer. */
export function withSavedCanonical<T extends { reps: number | null; rpe: number | null }>(
  values: T,
  saved: { reps?: number | null; rpe?: number | null } | null | undefined,
): T {
  if (!saved || !hasSides(values as SetSides)) return values;
  return { ...values, reps: saved.reps ?? values.reps, rpe: saved.rpe ?? values.rpe };
}

/**
 * Tipos de série em que o Modo Treino pede os lados (v1): trabalho e backoff.
 * Aquecimento/ajuste e aplicações de técnica avançada ficam com valor único.
 */
export function setTypeTakesSides(setType?: string | null): boolean {
  const normalized = normalizeSetType(setType);
  return isWorkingSetType(normalized) || normalized === setTypeVocabulary.backoff;
}

/** Sufixo de exibição com o texto pronto da API: " (reps E 10 · D 8)". */
export function sidesSuffix(set: { sides_text?: string | null } | null | undefined): string {
  const text = set?.sides_text?.trim();
  return text ? ` (${text})` : '';
}

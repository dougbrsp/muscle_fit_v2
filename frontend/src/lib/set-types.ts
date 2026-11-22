// Classificação de séries (`exercise_sets.set_type`) no frontend — espelho
// ÚNICO do dono no backend, `api/app/set_types.py` (M76, Lote 3b).
//
// Nenhum valor do vocabulário é escrito à mão aqui: canônicos, apelidos
// legados (`dropset`, `warm-up`, `feeler_set`...) e os conjuntos vêm de
// `set-types.generated.json`, exportado pelo backend
// (`api/tests/unit/test_m76_lote3b_set_types_fe.py --gravar`, que falha se o
// JSON divergir do dono). `set-types.test.ts` roda estas funções contra os
// casos de paridade do mesmo arquivo.
//
// A API já devolve `set_type` canônico; a normalização aqui cobre respostas
// antigas em cache e as linhas criadas localmente no Modo Treino.

import vocabulary from './set-types.generated.json';

const ALIASES: Readonly<Record<string, string>> = vocabulary.aliases;
const PREPARATORY: ReadonlySet<string> = new Set(vocabulary.preparatory);
const TECHNIQUE_APPLICATION: ReadonlySet<string> = new Set(vocabulary.technique_application);

/** `set_type` de série de trabalho (default histórico da coluna). */
export const WORKING_SET_TYPE: string = vocabulary.working;

/**
 * Normaliza como o backend (`normalize_set_type`): ausente/vazio = `working`;
 * caixa e espaços ignorados; apelidos legados viram o canônico; valor fora
 * do vocabulário volta normalizado (não vira `working` em silêncio).
 */
export function normalizeSetType(setType?: string | null): string {
  const value = (setType ?? '').trim().toLowerCase();
  if (!value) return WORKING_SET_TYPE;
  return ALIASES[value] ?? value;
}

/** Série de TRABALHO (`working`, legado sem tipo). `backoff` NÃO é. */
export function isWorkingSetType(setType?: string | null): boolean {
  return normalizeSetType(setType) === WORKING_SET_TYPE;
}

/** Aquecimento / série de ajuste: gravadas, mas fora de agregados e progressão. */
export function isPreparatorySetType(setType?: string | null): boolean {
  return PREPARATORY.has(normalizeSetType(setType));
}

/** `set_type` da série de exercício laboral (M100): preparatória, só conta no tempo de treino. */
export const LABORAL_SET_TYPE: string = vocabulary.laboral;

/** Série de exercício laboral (M100). */
export function isLaboralSetType(setType?: string | null): boolean {
  return normalizeSetType(setType) === LABORAL_SET_TYPE;
}

/** Tipos que a edição do registro pode trocar (`swappable` do dono; técnica e laboral ficam de fora). */
export const SWAPPABLE_SET_TYPES: readonly string[] = vocabulary.swappable;
const SWAPPABLE: ReadonlySet<string> = new Set(SWAPPABLE_SET_TYPES);

/** Rótulos dos tipos trocáveis: única fonte (seletor "Tipo da série" e Badge das telas). */
export const SET_TYPE_LABELS: Readonly<Record<string, string>> = {
  [WORKING_SET_TYPE]: 'Série de trabalho',
  backoff: 'Backoff',
  warmup: 'Aquecimento',
  feeler: 'Série de ajuste',
};

/** A série pode trocar de tipo na edição? (a decisão final é do servidor: 422 `set_type_locked`). */
export function isSwappableSetType(setType?: string | null): boolean {
  return SWAPPABLE.has(normalizeSetType(setType));
}

/** Aplicação de técnica avançada (rest-pause, drop, reps extras). */
export function isTechniqueApplicationSetType(setType?: string | null): boolean {
  return TECHNIQUE_APPLICATION.has(normalizeSetType(setType));
}

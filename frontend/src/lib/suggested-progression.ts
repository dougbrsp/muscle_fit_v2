// Leitura segura de `GET /exercises/suggested-progression` (M76, Lote 1a/1b).
//
// Com o grupo em NO_DIRECT_WORK o motor decide SKIP: a resposta vem sem faixa
// de repetições (`reps_target` vazio, `suggested_reps` null), com a carga de
// calibração, o teto e a nota "Grupo sem trabalho direto…". O backend manda
// string vazia, mas a tela não pode depender disso: `null`/ausente vira `''`.
// Nenhuma faixa é inventada aqui; quem decide o que mostrar é a nota do motor.

import type { SuggestedProgressionResponse } from './workout-plans-types';

export type SuggestedTargets = {
  reps_target: string;
  load_target: string;
  rpe_target: string;
  progression_note: string | null;
};

function text(value: string | null | undefined): string {
  return typeof value === 'string' ? value.trim() : '';
}

/** Alvos em texto prontos para a linha da ficha / Modo Treino (nunca `null`). */
export function suggestedProgressionTargets(suggestion: SuggestedProgressionResponse): SuggestedTargets {
  return {
    reps_target: text(suggestion.reps_target),
    load_target: text(suggestion.load_target),
    rpe_target: text(suggestion.rpe_target),
    progression_note: text(suggestion.progression_note) || null,
  };
}

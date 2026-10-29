// Peso inicial (estímulo e fadiga) de uma contribuição muscular por papel, no
// formulário de admin de `/dominio-exercicios` (`MuscleContributionsSection`).
//
// M76 Lote 3c: o número NÃO é escrito aqui. O dono é
// `api/app/intelligence/muscle_contribution_resolver.py` (`DEFAULT_WRITE_WEIGHTS`),
// exportado para `motor-constants.generated.json` e conferido por
// `api/tests/unit/test_m76_lote1b_espelhos_fe.py`. É só o ponto de partida do
// formulário: o admin ajusta antes de salvar, e quem lê o volume usa o
// `stimulus_weight` gravado, nunca este default.

import motorConstants from './motor-constants.generated.json';

export type ContributionRole = 'PRIMARY' | 'SECONDARY' | 'STABILIZER';

const DEFAULT_WRITE_WEIGHTS: Readonly<Record<ContributionRole, number>> =
  motorConstants.muscle_contributions.default_write_weights;

function isContributionRole(role: string): role is ContributionRole {
  return Object.prototype.hasOwnProperty.call(DEFAULT_WRITE_WEIGHTS, role);
}

/** Peso inicial do papel; papel desconhecido usa o de STABILIZER (o menor), como antes. */
export function defaultWeightsForRole(role: string): { stimulus: number; fatigue: number } {
  const weight = DEFAULT_WRITE_WEIGHTS[isContributionRole(role) ? role : 'STABILIZER'];
  return { stimulus: weight, fatigue: weight };
}

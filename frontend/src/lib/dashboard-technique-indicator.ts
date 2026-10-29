// "Técnica aplicada X/N" de um item da sessão (Fase 5, PR 4) — indicador
// PRÓPRIO do item: a completude conta só séries de trabalho; as aplicações da
// técnica (mini-bloco, drop, reps além da falha) aparecem aqui, com o
// equivalente que somaram ao volume do grupo.

import { getAdvancedTechniqueLabel } from './advanced-techniques';
import { fmtInt, fmtSets } from './dashboard-format';
import type { PlanItemTechnique } from './dashboard-workouts-types';

export function textoTecnicaAplicada(technique: PlanItemTechnique): string {
  const nome = getAdvancedTechniqueLabel(technique.code) ?? 'Técnica';
  const feitas = fmtInt(technique.performed_applications);
  const contagem =
    technique.planned_applications != null
      ? `${feitas}/${fmtInt(technique.planned_applications)} aplicação(ões)`
      : `${feitas} aplicação(ões)`;
  const equivalente =
    technique.equivalent_sets != null && technique.equivalent_sets > 0
      ? ` · +${fmtSets(technique.equivalent_sets)} série(s) equivalente(s)`
      : '';
  return `${nome}: ${contagem}${equivalente}`;
}

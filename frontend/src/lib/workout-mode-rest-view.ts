// Apresentação da tela de descanso do Modo Treino (novo visual, PR3/3).
// Só monta o que a tela mostra a partir de dados PRONTOS: o status e a carga
// da próxima série vêm do motor no salvamento da série (`SavedSetIntensity`,
// dono `api/app/domain/next_set_load.py`), com rótulos do JSON gerado. Nenhum
// cálculo de carga aqui; só a conversão de unidade para exibição (M3).

import {
  nextSetLoadView,
  savedSetStatusViews,
  setEvaluationCategoryDirection,
  type BadgeView,
  type SavedSetIntensity,
} from './set-engine-status';
import { formatWeight, type WeightUnit } from './weight-units';

export type EngineNextSetView = {
  /** Rótulo da ação do motor ("Aumentar carga"). */
  actionLabel: string;
  color: string;
  direction: 'UP' | 'HOLD' | 'DOWN' | null;
  /** Carga da próxima série na unidade do usuário; null sem carga (ex.: reduzir sem passo). */
  loadText: string | null;
  /** "Próxima série: Aumentar carga → 47,5 kg". */
  summary: string;
  /** Motivo curto (rótulo do servidor). */
  reason: string | null;
  /** Texto da regra que casou (R1), atrás de "Ver explicação". */
  explanation: string | null;
};

export type RestSavedSetView = {
  /** Chip "SÉRIE SALVA": só depois da confirmação do servidor. */
  showSavedChip: boolean;
  badges: BadgeView[];
  engine: EngineNextSetView | null;
};

/**
 * Bloco "Série salva + recomendação" do descanso.
 * - `setSaved`: a série que abriu o descanso foi gravada (fixado no
 *   `onSuccess` do salvamento; etapa pulada não grava nada).
 * - Na transição de exercício não há próxima série deste exercício: a
 *   recomendação de carga some (o card do próximo exercício assume).
 */
export function restSavedSetView({
  setSaved,
  saved,
  isExerciseTransition,
  weightUnit,
}: {
  setSaved: boolean;
  saved: SavedSetIntensity | null | undefined;
  isExerciseTransition: boolean;
  weightUnit: WeightUnit;
}): RestSavedSetView {
  const badges = setSaved ? savedSetStatusViews(saved) : [];
  const next = setSaved && !isExerciseTransition ? saved?.next_set_load : null;
  const view = nextSetLoadView(next);
  let engine: EngineNextSetView | null = null;
  if (next && view) {
    const loadText = next.load_kg != null ? formatWeight(next.load_kg, weightUnit) : null;
    engine = {
      actionLabel: view.badge.label,
      color: view.badge.color,
      direction: setEvaluationCategoryDirection(next.direction),
      loadText,
      summary: `Próxima série: ${view.badge.label}${loadText ? ` → ${loadText}` : ''}`,
      reason: view.reason,
      explanation: (next.rule_text || '').trim() || null,
    };
  }
  return { showSavedChip: setSaved, badges, engine };
}

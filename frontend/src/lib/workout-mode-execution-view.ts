// Apresentação da tela de Execução do Modo Treino (novo visual, PR2/3).
// Só formatação e decisões de EXIBIÇÃO sobre dados já prontos — nenhum
// limiar, passo de carga, teto de RPE ou cálculo de treino mora aqui (donos:
// motores do backend; ver `docs/motores/FONTES_DA_VERDADE.md`).

import type { SetEvaluationResult } from './workout-mode-types';

/**
 * Card "Dica da IA" da execução. Só existe com o toggle "IA por série"
 * LIGADO **e** uma avaliação real com texto (`note`, escrito pela IA). A
 * categoria da avaliação é a AÇÃO DO MOTOR (set-eval-v4+), então ela não
 * entra no card como se fosse da IA; quando o servidor realinhou a categoria
 * da IA à direção do motor (`category_from_ai` presente), o card diz que a
 * carga final segue o motor.
 */
export type AiTipView = {
  note: string;
  realignedByEngine: boolean;
};

export function aiTipView(enabled: boolean, result: SetEvaluationResult | null | undefined): AiTipView | null {
  if (!enabled || !result) return null;
  const note = (result.note || '').trim();
  if (!note) return null;
  return { note, realignedByEngine: Boolean((result.category_from_ai || '').trim()) };
}

/** Texto longo da dica/descrição ganha "Ver mais" (corte só visual, por CSS). */
export const LONG_TEXT_TOGGLE_MIN_CHARS = 140;

export function isLongText(text: string | null | undefined): boolean {
  return (text || '').trim().length > LONG_TEXT_TOGGLE_MIN_CHARS;
}

/**
 * "Alvo: 10-12 reps · 15 kg · RPE 9" só com os campos que EXISTEM na
 * prescrição (nada de "-" no lugar de uma carga que a ficha não tem). Em
 * série medida (tempo / máximo de reps), o alvo da medida vem pronto de
 * `measureTargetText`. `null` = sem alvo nenhum: a linha não aparece.
 */
export function setTargetLine({
  measured,
  measureTarget,
  repsTarget,
  loadText,
  rpeTarget,
}: {
  measured: boolean;
  measureTarget?: string | null;
  repsTarget?: string | null;
  loadText?: string | null;
  rpeTarget?: string | null;
}): string | null {
  const rpe = (rpeTarget || '').trim();
  const parts: string[] = [];
  if (measured) {
    parts.push((measureTarget || '').trim() || '-');
  } else {
    const reps = (repsTarget || '').trim();
    const load = (loadText || '').trim();
    if (reps) parts.push(`${reps} reps`);
    if (load) parts.push(load);
  }
  if (rpe) parts.push(`RPE ${rpe}`);
  if (parts.length === 0) return null;
  return `Alvo: ${parts.join(' · ')}`;
}

/**
 * Campos editáveis do card de série. Um campo editado à mão nesta série não
 * é sobrescrito quando o valor sugerido muda (ex.: a avaliação da IA chega
 * depois de o usuário já ter ajustado a carga); trocar de série/exercício
 * reinicia tudo pelos valores sugeridos.
 */
export type LoggerField = 'reps' | 'weight' | 'rpe' | 'duration';

export function fieldsToRefresh(keyChanged: boolean, edited: ReadonlySet<LoggerField>): Set<LoggerField> {
  const all: LoggerField[] = ['reps', 'weight', 'rpe', 'duration'];
  return new Set(keyChanged ? all : all.filter((field) => !edited.has(field)));
}

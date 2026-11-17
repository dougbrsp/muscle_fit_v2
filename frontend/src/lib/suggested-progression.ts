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

// #188: rótulo curto (apresentação) do selo por código do motor. A decisão de
// calibrar e o texto da nota vêm prontos da API (`calibrating`,
// `calibration_code`, `progression_note`); o cliente só traduz o código.
export const CALIBRATION_LABELS: Readonly<Record<string, string>> = {
  context_calibration: 'Calibrando',
  calibration_start_other_gym: 'Carga de partida de outra academia',
};

export type SuggestedCalibration = {
  calibrating: boolean;
  calibration_code: string | null;
  load_source: string | null;
};

/** Campos de calibração da sugestão; API antiga (sem os campos) → `false`/`null`. */
export function suggestedCalibration(suggestion: SuggestedProgressionResponse | null | undefined): SuggestedCalibration {
  return {
    calibrating: suggestion?.calibrating === true,
    calibration_code: suggestion?.calibration_code ?? null,
    load_source: suggestion?.load_source ?? null,
  };
}

/**
 * Rótulo do selo: o do código conhecido; sem código mas com `calibrating`,
 * "Calibrando"; código desconhecido → sem selo.
 */
export function calibrationLabel(code: string | null | undefined, calibrating = false): string | null {
  if (code) return CALIBRATION_LABELS[code] ?? null;
  return calibrating ? CALIBRATION_LABELS.context_calibration : null;
}

/** Selo de calibração da sugestão de carga (null = sem selo). */
export function suggestionCalibrationLabel(suggestion: SuggestedProgressionResponse | null | undefined): string | null {
  const { calibrating, calibration_code } = suggestedCalibration(suggestion);
  return calibrationLabel(calibration_code, calibrating);
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

// Medida da série no Modo Treino (Lote T5 das medidas de série, 2026-10-20).
//
// Funções PURAS (sem React/DOM/`Intl`), exportadas pelo núcleo
// `workout-mode-core` para o celular e, depois, para o relógio. Nenhuma regra
// numérica nova: a semântica das medidas é do dono `api/app/domain/set_measures.py`
// e chega pelo espelho `./set-measures` (vocabulário, `isRepBased`, limites,
// texto). Aqui só se decide, a partir dela, o que a tela de registro mostra:
// qual cronômetro, quais campos são obrigatórios, os valores iniciais e as
// chaves de medida do POST da série.
//
// Garantia de compatibilidade: série de repetições (toda ficha de hoje) não
// ganha chave nenhuma no payload (`setMeasurePayload` devolve `{}`), então o
// POST sai idêntico ao de antes do T5.

import {
  formatSetMeasureText,
  isRepBased,
  MEASURE_LIMITS,
  MEASURE_VALUES,
  normalizeMeasure,
  parseMeasure,
} from './set-measures';
import { SET_LOGGER_FALLBACK_REPS } from './prescription-defaults';
import { parseNumericTarget } from './workout-mode-types';

/** O que o Modo Treino lê do item da ficha para decidir a medida. */
export type MeasurePlanItem = {
  measure?: string | null;
  duration_seconds_target?: number | null;
  time_window_seconds?: number | null;
};

/** A série anterior desta sessão (âncora), só os campos que pesam aqui. */
export type MeasureLastSet = {
  reps?: number | null;
  weight_kg?: number | string | null;
  duration_seconds?: number | null;
  measure?: string | null;
};

export type SetMeasureSpec = {
  /** Medida canônica do item (`reps` quando ausente/desconhecida). */
  measure: string;
  durationSecondsTarget: number | null;
  timeWindowSeconds: number | null;
};

function intOrNull(value: unknown): number | null {
  if (value === null || value === undefined || typeof value === 'boolean') return null;
  const n = Number(value);
  return Number.isFinite(n) && Number.isInteger(n) ? n : null;
}

/**
 * Medida do item da ficha (prescrição congelada > linha da ficha), com os
 * alvos de tempo. Ausente = `reps` (dono: medida ausente é repetições).
 */
export function setMeasureOf(
  item: (MeasurePlanItem & { prescription?: MeasurePlanItem | null }) | null | undefined,
): SetMeasureSpec {
  const frozen = item?.prescription;
  const source = frozen && frozen.measure != null ? frozen : item;
  const measure = normalizeMeasure(source?.measure);
  return {
    measure,
    durationSecondsTarget: intOrNull(source?.duration_seconds_target),
    timeWindowSeconds: intOrNull(source?.time_window_seconds),
  };
}

export type MeasureLoggerDefaultsInput = {
  measure?: string | null;
  durationSecondsTarget?: number | null;
  timeWindowSeconds?: number | null;
  /** Alvo de reps da ficha (texto livre, "8-12"); só pesa em `reps`. */
  repsTarget?: string | null;
  lastSet?: MeasureLastSet | null;
  /** Reps sugeridas pela IA para a próxima série (vale em `reps` e `max_reps`). */
  suggestedReps?: number | null;
};

export type MeasureLoggerDefaults = {
  /** Valor inicial do campo de reps (null = vazio ou campo ausente). */
  reps: number | null;
  /** Valor inicial do campo "Segundos" (só `duration`). */
  durationSeconds: number | null;
  /** Janela do máximo de reps (só `max_reps` com janela). */
  windowSeconds: number | null;
  /** Carga pode ficar vazia (fora de `reps`). */
  loadOptional: boolean;
  /** O campo de reps aparece e é obrigatório. */
  repsRequired: boolean;
  /** O campo de segundos aparece e é obrigatório (`duration`). */
  durationRequired: boolean;
};

/**
 * Campos e valores iniciais do card de registro pela medida:
 *
 * - `reps`: reps = sugestão da IA > última série > alvo da ficha > fallback
 *   do backend (`SET_LOGGER_FALLBACK_REPS`), como sempre; carga obrigatória.
 * - `duration`: sem reps; segundos = última série > alvo do item; carga opcional.
 * - `max_reps`: reps obrigatórias (sugestão da IA > última série; nunca o
 *   alvo, que o máximo não tem); janela do item; carga opcional.
 */
export function measureLoggerDefaults(input: MeasureLoggerDefaultsInput): MeasureLoggerDefaults {
  const measure = normalizeMeasure(input.measure);
  const lastSet = input.lastSet ?? null;
  const suggested = input.suggestedReps != null && Number.isFinite(input.suggestedReps) ? Math.round(input.suggestedReps) : null;
  const lastReps = intOrNull(lastSet?.reps);
  if (isRepBased(measure)) {
    return {
      reps: suggested ?? lastReps ?? parseNumericTarget(input.repsTarget) ?? SET_LOGGER_FALLBACK_REPS,
      durationSeconds: null,
      windowSeconds: null,
      loadOptional: false,
      repsRequired: true,
      durationRequired: false,
    };
  }
  if (measure === MEASURE_VALUES.duration) {
    return {
      reps: null,
      durationSeconds: intOrNull(lastSet?.duration_seconds) ?? intOrNull(input.durationSecondsTarget),
      windowSeconds: null,
      loadOptional: true,
      repsRequired: false,
      durationRequired: true,
    };
  }
  return {
    reps: suggested ?? lastReps,
    durationSeconds: null,
    windowSeconds: intOrNull(input.timeWindowSeconds),
    loadOptional: true,
    repsRequired: true,
    durationRequired: false,
  };
}

export type ExecutionTimerMode = 'none' | 'countdown' | 'stopwatch';

export type ExecutionTimerSpec = {
  mode: ExecutionTimerMode;
  /** Segundos do countdown; null no cronômetro livre e sem cronômetro. */
  targetSeconds: number | null;
};

/**
 * Cronômetro de execução da série:
 * - `duration` com alvo: regressivo até o alvo (sem alvo: cronômetro livre);
 * - `max_reps` com janela: regressivo da janela; sem janela: cronômetro livre;
 * - `reps`: nenhum.
 */
export function executionTimerSpec(input: {
  measure?: string | null;
  durationSecondsTarget?: number | null;
  timeWindowSeconds?: number | null;
}): ExecutionTimerSpec {
  const measure = normalizeMeasure(input.measure);
  if (isRepBased(measure)) return { mode: 'none', targetSeconds: null };
  const target =
    measure === MEASURE_VALUES.duration ? intOrNull(input.durationSecondsTarget) : intOrNull(input.timeWindowSeconds);
  return target != null && target > 0 ? { mode: 'countdown', targetSeconds: target } : { mode: 'stopwatch', targetSeconds: null };
}

/**
 * Texto do ALVO da série pela medida, para o rótulo ("Série 1 de 3 · 60 s") e o
 * card recolhido do editor: `duration` = "60 s"; `max_reps` = "reps máx. em
 * 30 s" (sem janela: "reps máx."); `reps` = null (o rótulo segue como antes).
 * O número com unidade vem de `formatSetMeasureText` (espelho do dono).
 */
export function measureTargetText(input: {
  measure?: string | null;
  durationSecondsTarget?: number | null;
  timeWindowSeconds?: number | null;
}): string | null {
  const measure = normalizeMeasure(input.measure);
  if (isRepBased(measure)) return null;
  if (measure === MEASURE_VALUES.duration) {
    return formatSetMeasureText({ measure, durationSeconds: input.durationSecondsTarget });
  }
  const label = parseMeasure(measure)?.shortLabel ?? measure;
  if (input.timeWindowSeconds == null) return label;
  const windowText = formatSetMeasureText({ measure: MEASURE_VALUES.duration, durationSeconds: input.timeWindowSeconds });
  return `${label} em ${windowText}`;
}

/** Rótulo da série com a medida: "Série 1 de 3 · 60 s"; em `reps`, o rótulo como veio. */
export function measureSetLabel(
  baseLabel: string,
  input: { measure?: string | null; durationSecondsTarget?: number | null; timeWindowSeconds?: number | null },
): string {
  const target = measureTargetText(input);
  return target ? `${baseLabel} · ${target}` : baseLabel;
}

/**
 * Chaves de medida do POST/avaliação da série. `reps` (ou ausente) = `{}`:
 * o payload de uma série de repetições continua idêntico ao de antes. Fora de
 * `reps`, `measure` explícita e `duration_seconds` (segundos executados em
 * `duration`; a janela usada em `max_reps`, ou null).
 */
export function setMeasurePayload(values: {
  measure?: string | null;
  duration_seconds?: number | null;
}): { measure?: string; duration_seconds?: number | null } {
  const measure = normalizeMeasure(values.measure);
  if (isRepBased(measure)) return {};
  return { measure, duration_seconds: intOrNull(values.duration_seconds) };
}

/**
 * Segundos de uma série por tempo dentro dos limites do dono
 * (`DURATION_SECONDS_MIN/MAX`, via `MEASURE_LIMITS`): inteiro, inclusivo. É o
 * que habilita "Salvar série"; a palavra final é o 422 do servidor.
 */
export function isDurationSecondsValid(seconds: number | null | undefined): boolean {
  const value = intOrNull(seconds);
  return value != null && value >= MEASURE_LIMITS.durationSecondsMin && value <= MEASURE_LIMITS.durationSecondsMax;
}

/**
 * Reps de uma série de máximo de reps no piso do dono (`MAX_REPS_MIN_REPS`,
 * via `MEASURE_LIMITS.maxRepsMinReps`): inteiro, inclusivo.
 */
export function isMaxRepsValid(reps: number | null | undefined): boolean {
  const value = intOrNull(reps);
  return value != null && value >= MEASURE_LIMITS.maxRepsMinReps;
}

// O texto da série realizada é o do espelho (mesmo de `format_set_measure`).
export { formatSetMeasureText };

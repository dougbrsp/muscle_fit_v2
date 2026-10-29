// Medidas de série (repetições / tempo / máximo de repetições) no frontend —
// espelho ÚNICO do dono no backend, `api/app/domain/set_measures.py`
// (medidas de série, Lote T1, 2026-10-20).
//
// Nenhum valor do vocabulário, rótulo ou limite é escrito à mão aqui: tudo vem
// do bloco `set_measures` de `motor-constants.generated.json`, exportado pelo
// backend (`api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`, que falha
// se o JSON divergir do dono). `set-measures.test.ts` roda estas funções contra
// `set-measures.parity.json`, calculado pelo mesmo dono.
//
// Semântica (do dono): medida ausente = `reps` (toda série anterior à 089 e o
// relógio antigo); só `reps` é "baseada em reps" para progressão,
// comparabilidade, e1RM e carga da próxima série; nunca deduzir a medida de
// uma série pelo item da ficha.

import motorConstants from './motor-constants.generated.json';

const BLOCK = motorConstants.set_measures;

export interface SetMeasureOption {
  /** Valor gravado em `measure` (`reps`, `duration`, `max_reps`). */
  value: string;
  /** Rótulo pt-BR ("Repetições", "Tempo", "Máximo de repetições"). */
  label: string;
  /** Sufixo curto ao lado do número ("reps", "s", "reps máx."). */
  shortLabel: string;
  /** Alimenta progressão/comparabilidade/e1RM (só `reps`). */
  repBased: boolean;
}

/** As medidas na ordem de exibição do dono. */
export const MEASURE_OPTIONS: readonly SetMeasureOption[] = BLOCK.measures.map((m) => ({
  value: m.value,
  label: m.label,
  shortLabel: m.short_label,
  repBased: m.rep_based,
}));

/** Medida assumida quando a série/item não diz (`reps`). */
export const DEFAULT_MEASURE: string = BLOCK.default_measure;

/** Valor de cada medida pelo nome, para comparar sem literal. */
export const MEASURE_VALUES: Readonly<typeof BLOCK.values> = BLOCK.values;

/** Limites inclusivos dos campos de tempo e do máximo (segundos / reps). */
export const MEASURE_LIMITS = {
  durationSecondsMin: BLOCK.limits.duration_seconds_min,
  durationSecondsMax: BLOCK.limits.duration_seconds_max,
  timeWindowSecondsMin: BLOCK.limits.time_window_seconds_min,
  timeWindowSecondsMax: BLOCK.limits.time_window_seconds_max,
  maxRepsMinReps: BLOCK.limits.max_reps_min_reps,
} as const;

/**
 * Campos do item cuja presença marca o "bloco avançado" (técnica, aquecimento,
 * ajuste, pico) — proibido fora de `reps` (`ADVANCED_BLOCK_PRESENCE_FIELDS`).
 */
export const ADVANCED_BLOCK_PRESENCE_FIELDS: readonly string[] = BLOCK.advanced_block_presence_fields;

/** Motivo (código) de progressão/comparabilidade neutras por causa da medida. */
export const REASON_MEASURE_NOT_REP_BASED: string = BLOCK.reason_not_rep_based;
export const MEASURE_REASON_LABELS: Readonly<Record<string, string>> = BLOCK.reason_labels;

const EMPTY_TEXT: string = BLOCK.empty_text;
const BY_VALUE: ReadonlyMap<string, SetMeasureOption> = new Map(MEASURE_OPTIONS.map((o) => [o.value, o]));
const DEFAULT_OPTION: SetMeasureOption = MEASURE_OPTIONS.find((o) => o.value === DEFAULT_MEASURE) ?? MEASURE_OPTIONS[0];

/** Leitura estrita (`parse_measure`): ausente/vazio/desconhecido = null. */
export function parseMeasure(value?: string | null): SetMeasureOption | null {
  const text = (value ?? '').trim().toLowerCase();
  if (!text) return null;
  return BY_VALUE.get(text) ?? null;
}

/** Leitura tolerante (`normalize_measure`): ausente/vazio/desconhecido = `reps`. */
export function normalizeMeasure(value?: string | null): string {
  return (parseMeasure(value) ?? DEFAULT_OPTION).value;
}

/** Só `reps` (ou ausente) alimenta progressão, comparabilidade, e1RM e carga da próxima série. */
export function isRepBased(value?: string | null): boolean {
  return (parseMeasure(value) ?? DEFAULT_OPTION).repBased;
}

/** Rótulo pt-BR da medida; null quando ausente ou desconhecida. */
export function measureLabel(value?: string | null): string | null {
  return parseMeasure(value)?.label ?? null;
}

function shortLabelOf(measureValue: string): string {
  return (BY_VALUE.get(measureValue) ?? DEFAULT_OPTION).shortLabel;
}

/** Inteiro do valor (número integral ou texto numérico, vírgula aceita); null quando ausente/ilegível. */
function asInt(value: unknown): number | null {
  if (value === null || value === undefined || typeof value === 'boolean') return null;
  if (typeof value === 'number') return Number.isInteger(value) ? value : null;
  const text = String(value).trim().replace(',', '.');
  if (!text) return null;
  const n = Number(text);
  return Number.isFinite(n) && Number.isInteger(n) ? n : null;
}

export interface SetMeasureTextInput {
  measure?: string | null;
  reps?: number | string | null;
  durationSeconds?: number | string | null;
}

/**
 * Texto da série pela medida, igual a `format_set_measure` do dono:
 * "10 reps" / "60 s" / "15 reps em 30 s" (máximo sem janela: "15 reps");
 * sem o dado que a medida exige, "—".
 */
export function formatSetMeasureText({ measure, reps, durationSeconds }: SetMeasureTextInput): string {
  const kind = normalizeMeasure(measure);
  const count = asInt(reps);
  const seconds = asInt(durationSeconds);
  const secondsText = seconds === null ? null : `${seconds} ${shortLabelOf(MEASURE_VALUES.duration)}`;
  if (kind === MEASURE_VALUES.duration) return secondsText ?? EMPTY_TEXT;
  if (count === null) return EMPTY_TEXT;
  const repsText = `${count} ${shortLabelOf(MEASURE_VALUES.reps)}`;
  if (kind === MEASURE_VALUES.max_reps && secondsText !== null) return `${repsText} em ${secondsText}`;
  return repsText;
}

/**
 * Tempo de execução planejado de UMA série quando a medida o define, igual a
 * `planned_set_execution_seconds` do dono (Lote T2): o alvo em `duration`, a
 * janela em `max_reps` (se houver); null = a estimativa usa a conta por
 * repetições de sempre (`template-time-estimate.ts`).
 */
export function plannedSetExecutionSeconds(
  measure: string | null | undefined,
  durationSecondsTarget: number | string | null | undefined,
  timeWindowSeconds: number | string | null | undefined,
): number | null {
  const kind = normalizeMeasure(measure);
  if (kind === MEASURE_VALUES.duration) return asInt(durationSecondsTarget);
  if (kind === MEASURE_VALUES.max_reps) return asInt(timeWindowSeconds);
  return null;
}

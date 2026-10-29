// Referência histórica do modo treino: as séries da ÚLTIMA sessão do mesmo
// exercício canônico (`GET /exercises/last-session-sets`), exibidas inteiras
// no `SetLoggerCard` ("1ª série - 11 x 70kg@RPE9,5 - Pré-fatigado", uma por
// linha), junto com o recorde, o e1RM do histórico nesse equipamento e a
// fadiga atual do grupo (M79: rótulos prontos do backend, sem regra aqui).
//
// Por que um módulo próprio: a seleção (quais séries entram, em que ordem e
// com que numeração) é a parte arriscada — o `set_number` FÍSICO gravado no
// banco inclui aquecimento/ajuste e pode estar deslocado (ex.: aquecimentos
// em 1-2, trabalho em 3-5), enquanto a tela numera "Série 1..N" contando só
// séries de trabalho. Isolar aqui deixa isso testável sem render.
//
// Decisão (consistente com o resto do app): só séries de TRABALHO aparecem —
// nem aquecimento/ajuste, nem aplicação de técnica avançada, nem `backoff`.
// O backend já exclui `warmup`/`feeler` nesse endpoint, e a progressão também
// só considera trabalho — mostrar aquecimento como "Série 1" faria a
// referência do card discordar da numeração da sessão atual. O filtro é
// repetido aqui de propósito (defesa em profundidade para clientes antigos /
// resposta em cache de uma versão anterior do endpoint).

import { formatSetMeasureText, isRepBased } from './set-measures';
import { DISPLAY_DECIMALS, fromKg, normalizeWeightUnit, WEIGHT_UNIT_LABEL, type WeightUnit } from './weight-units';
import {
  type LastSessionSetsResponse,
  type PreviousSessionRecord,
  type PreviousSessionSet,
} from './workout-mode-types';
import { sidesSuffix } from './set-sides';

// O seletor mora em `workout-mode-previous-session-sets.ts` (módulo puro,
// exportado pelo núcleo do Modo Treino, PR B3 da Evolução ZEPP); reexportado
// aqui para os consumidores de sempre.
export { selectPreviousSessionWorkingSets } from './workout-mode-previous-session-sets';

/** Número em pt-BR, vírgula decimal, sem zeros à direita nem separador de milhar. */
function formatDecimalPtBr(value: number, maximumFractionDigits: number): string {
  return value.toLocaleString('pt-BR', { maximumFractionDigits, useGrouping: false });
}

/** RPE em pt-BR ("9,5", "10"), como o usuário lê no app. */
export function formatRpe(rpe: number | null | undefined): string | null {
  if (rpe == null || !Number.isFinite(Number(rpe))) return null;
  return formatDecimalPtBr(Number(rpe), 1);
}

/**
 * Carga (sempre kg no backend) na unidade preferida, em pt-BR e colada à
 * unidade: "30kg", "24,95kg", "154,3lb". `maximumFractionDigits` padrão = a
 * precisão da unidade (kg: 2, lb: 1 — `DISPLAY_DECIMALS`).
 */
export function formatLoadPtBr(weightKg: number, unit: WeightUnit, maximumFractionDigits?: number): string {
  const target = normalizeWeightUnit(unit);
  const digits = maximumFractionDigits ?? DISPLAY_DECIMALS[target];
  return `${formatDecimalPtBr(fromKg(weightKg, target), digits)}${WEIGHT_UNIT_LABEL[target]}`;
}

function hasLoad(weightKg: number | null | undefined): weightKg is number {
  return weightKg != null && Number.isFinite(Number(weightKg)) && Number(weightKg) > 0;
}

/** "10 x 30kg" ou, sem carga (peso corporal), "10 reps". */
function formatRepsAndLoad(reps: number, weightKg: number | null | undefined, unit: WeightUnit): string {
  return hasLoad(weightKg) ? `${reps} x ${formatLoadPtBr(Number(weightKg), unit)}` : `${reps} reps`;
}

export type PreviousSessionSetEntry = {
  /** Posição exibida (1-based) entre as séries de trabalho. */
  position: number;
  /** "1ª série - 10 x 30kg@RPE9" (carga/RPE ausentes são omitidos). */
  text: string;
};

/**
 * Texto de cada série da sessão anterior, já na unidade preferida do usuário
 * (M3 — o armazenamento é sempre kg; só a exibição converte).
 */
export function formatPreviousSessionSets(
  sets: ReadonlyArray<PreviousSessionSet>,
  unit: WeightUnit,
): PreviousSessionSetEntry[] {
  return sets.map((set, index) => {
    const rpe = formatRpe(set.rpe);
    // M79: rótulo da fadiga do grupo antes da série (pronto do backend).
    const fatigue = set.group_fatigue?.band_label;
    const text =
      `${index + 1}ª série - ${formatSetBody(set, unit)}${rpe ? `@RPE${rpe}` : ''}${sidesSuffix(set)}` +
      (fatigue ? ` - ${fatigue}` : '');
    return { position: index + 1, text };
  });
}

/**
 * Corpo da linha pela medida (Lote T5). Repetições: "10 x 30kg" / "10 reps",
 * como sempre. Tempo / máximo de reps: o texto do dono (`measure_text` do
 * servidor; sem ele, o espelho `formatSetMeasureText`) e a carga, se houver:
 * "58 s", "15 reps em 30 s x 10kg".
 */
function formatSetBody(set: PreviousSessionSet, unit: WeightUnit): string {
  if (isRepBased(set.measure)) return formatRepsAndLoad(Number(set.reps ?? 0), set.weight_kg, unit);
  const measureText =
    set.measure_text || formatSetMeasureText({ measure: set.measure, reps: set.reps, durationSeconds: set.duration_seconds });
  return hasLoad(set.weight_kg) ? `${measureText} x ${formatLoadPtBr(Number(set.weight_kg), unit)}` : measureText;
}

/**
 * "Fadiga atual do grupo: Pré-fatigado" (M79) — a leitura da sessão em
 * andamento antes da 1ª série do exercício, pronta do backend (dono
 * `domain/prior_fatigue.py`). `null` sem leitura.
 */
export function formatCurrentGroupFatigueLine(response: LastSessionSetsResponse | undefined | null): string | null {
  const label = response?.current_group_fatigue?.band_label;
  return label ? `Fadiga atual do grupo: ${label}` : null;
}

/** Quantas séries do treino anterior aparecem antes do "ver outras". */
export const PREVIOUS_SESSION_VISIBLE_SETS = 3;

export type PreviousSessionRecordSummary = {
  scope: 'equipment' | 'exercise';
  record: PreviousSessionRecord | null;
  e1rmKg: number | null;
  /** `true` em `assisted`: o recorde é a MENOR assistência (menos kg = melhor). */
  assisted?: boolean;
};

/** Recorde/e1RM do histórico vindos com a sessão anterior (`null` sem histórico). */
export function selectPreviousSessionRecord(
  response: LastSessionSetsResponse | undefined | null,
): PreviousSessionRecordSummary | null {
  const item = response?.item;
  if (!item) return null;
  const record = item.record && Number(item.record.reps) > 0 ? item.record : null;
  const e1rmKg = hasLoad(item.e1rm_kg) ? Number(item.e1rm_kg) : null;
  if (!record && e1rmKg == null) return null;
  return {
    scope: item.record_scope === 'exercise' ? 'exercise' : 'equipment',
    record,
    e1rmKg,
    assisted: item.load_convention === 'assisted',
  };
}

/** Recorde em `assisted`: "8 x 20kg de assistência" / "8 reps sem assistência". */
function formatAssistedRecord(record: PreviousSessionRecord, unit: WeightUnit): string {
  if (record.weight_kg == null) return `${record.reps} reps`;
  return hasLoad(record.weight_kg)
    ? `${record.reps} x ${formatLoadPtBr(Number(record.weight_kg), unit)} de assistência`
    : `${record.reps} reps sem assistência`;
}

/**
 * Linhas exibidas acima das séries do treino anterior:
 *   "Recorde nesse equipamento: 13 x 30kg"
 *   "Melhor carga nesse equipamento: 10 x 30kg" (sem recorde ainda: o backend
 *   manda `kind: 'best_load'`, M76 Lote 3d — a tela só troca o rótulo)
 *   "e1RM estimado nesse equipamento: 43,3kg"
 * Sem carga no histórico: recorde só de reps ("15 reps") e sem e1RM.
 * Assistido: o recorde é a MENOR assistência ("8 x 20kg de assistência").
 * e1RM com no máximo 1 casa decimal (o backend só o calcula de séries com até
 * 12 reps, a mesma regra do dashboard).
 */
export function formatPreviousSessionRecordLines(
  summary: PreviousSessionRecordSummary | null | undefined,
  unit: WeightUnit,
): string[] {
  if (!summary) return [];
  const where = summary.scope === 'exercise' ? 'nesse exercício' : 'nesse equipamento';
  const lines: string[] = [];
  if (summary.record) {
    const value = summary.assisted
      ? formatAssistedRecord(summary.record, unit)
      : formatRepsAndLoad(summary.record.reps, summary.record.weight_kg, unit);
    const label = summary.record.kind === 'best_load' ? 'Melhor carga' : 'Recorde';
    lines.push(`${label} ${where}: ${value}`);
  }
  if (summary.e1rmKg != null) {
    lines.push(`e1RM estimado ${where}: ${formatLoadPtBr(summary.e1rmKg, unit, 1)}`);
  }
  return lines;
}

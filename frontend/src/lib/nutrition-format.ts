// Apresentação dos números de nutrição (Lote L2b). Só formatação e conversão de
// UNIDADE para exibir (fração <-> %): nenhuma regra de nutrição mora aqui — o
// motor (`api/app/domain/nutrition/`) calcula e a API devolve pronto
// (`docs/motores/FONTES_DA_VERDADE.md` §3.7). Ausente vira `-` (família de
// `lib/format.ts`), nunca 0.

import { DASH, formatNumberOrDash } from './format';

const PERCENT = 100;

/** Fração (0,25) em % para o campo do formulário (25). `null` fica `null`. */
export function shareToPercent(share: number | null | undefined): number | null {
  if (share == null || !Number.isFinite(share)) return null;
  return Math.round(share * PERCENT * PERCENT) / PERCENT;
}

/** % digitado no formulário (25) em fração para a API (0,25). Vazio vira `null`. */
export function percentToShare(percent: number | string | null | undefined): number | null {
  if (percent === null || percent === undefined || percent === '') return null;
  const valor = Number(percent);
  return Number.isFinite(valor) ? valor / PERCENT : null;
}

/** Número do campo (`NumberInput` devolve `number | string`); vazio vira `null`. */
export function inputNumberOrNull(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const valor = Number(value);
  return Number.isFinite(valor) ? valor : null;
}

export function formatKcal(value: number | null | undefined): string {
  const texto = formatNumberOrDash(value, 0);
  return texto === DASH ? DASH : `${texto} kcal`;
}

export function formatGrams(value: number | null | undefined): string {
  const texto = formatNumberOrDash(value, 0);
  return texto === DASH ? DASH : `${texto} g`;
}

/** Valor de tabela de alimento com a unidade do campo (`2,52 g`); ausente = `-`. */
export function formatWithUnit(value: number | null | undefined, unit: string, digits = 2): string {
  const texto = formatNumberOrDash(value, digits);
  return texto === DASH ? DASH : `${texto} ${unit}`;
}

export function formatShareAsPercent(share: number | null | undefined, digits = 1): string {
  const texto = formatNumberOrDash(shareToPercent(share), digits);
  return texto === DASH ? DASH : `${texto}%`;
}

/** Dia da semana curto (seg., ter.) de uma data ISO, sem escorregar de fuso. */
export function weekdayShort(isoDate: string): string {
  const [ano, mes, dia] = isoDate.slice(0, 10).split('-').map(Number);
  if (!ano || !mes || !dia) return isoDate;
  return new Date(Date.UTC(ano, mes - 1, dia)).toLocaleDateString('pt-BR', { weekday: 'short', timeZone: 'UTC' });
}

// ---------------------------------------------------------------------------
// Diário (L4): só apresentação. Total incompleto (o motor marca o campo) é um
// mínimo: aparece com `≥`, nunca como um total fechado.
// ---------------------------------------------------------------------------

/** Total de um nutriente com a unidade; incompleto vira `≥ valor` (mínimo). */
export function formatTotal(value: number | null | undefined, unit: string, complete: boolean, digits = 0): string {
  const texto = formatWithUnit(value, unit, digits);
  if (texto === DASH || complete) return texto;
  return `≥ ${texto}`;
}

/** kcal com sinal (`+120 kcal`, `-350 kcal`) para o balanço; ausente vira `-`. */
export function formatSignedKcal(value: number | null | undefined): string {
  const texto = formatKcal(value);
  if (texto === DASH || value == null || value <= 0) return texto;
  return `+${texto}`;
}

const SECONDS_PER_MINUTE = 60;

/** Duração em minutos inteiros (`45 min`); ausente vira `-`. */
export function formatDurationMinutes(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return DASH;
  return `${Math.round(seconds / SECONDS_PER_MINUTE)} min`;
}

function partesDaData(isoDate: string): [number, number, number] | null {
  const [ano, mes, dia] = isoDate.slice(0, 10).split('-').map(Number);
  return ano && mes && dia ? [ano, mes, dia] : null;
}

/** Data ISO deslocada em dias de calendário (navegação da tela), sem escorregar de fuso. */
export function shiftIsoDate(isoDate: string, days: number): string {
  const partes = partesDaData(isoDate);
  if (!partes) return isoDate;
  const [ano, mes, dia] = partes;
  return new Date(Date.UTC(ano, mes - 1, dia + days)).toISOString().slice(0, 10);
}

/** `sábado, 17/10/2026` a partir de uma data ISO. */
export function formatDayLong(isoDate: string): string {
  const partes = partesDaData(isoDate);
  if (!partes) return isoDate;
  const [ano, mes, dia] = partes;
  return new Date(Date.UTC(ano, mes - 1, dia)).toLocaleDateString('pt-BR', {
    weekday: 'long',
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

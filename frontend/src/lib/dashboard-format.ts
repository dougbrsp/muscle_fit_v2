/**
 * Formatadores compartilhados pelas telas do hub `/dashboard/*`.
 *
 * Ponto único de formato de data/número do hub: antes da F0.4 cada tela grande
 * redefinia o seu `formatKg`/`formatNumber`/`formatRpe` localmente e elas
 * divergiam (0 vs 1 casa em kg, `-` vs `—` para ausente). Mesma decisão que
 * motivou `chartTheme.ts` para gráficos.
 *
 * Nenhuma função aqui inventa valor: ausente é ausente (`EM_DASH`), NUNCA `0`
 * (plano §8.4).
 */

/** Traço de "não calculável" (plano §8.4). Nunca substituir por `0`. */
export const EM_DASH = '—';

/** `YYYY-MM-DD` (ou ISO) → `dd/mm/aaaa`, sem deslocar por fuso. */
export function fmtDate(value: string | null | undefined): string {
  if (!value) return EM_DASH;
  const isoDay = String(value).slice(0, 10);
  const parsed = new Date(`${isoDay}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return isoDay;
  return parsed.toLocaleDateString('pt-BR');
}

/**
 * `DATETIME` do backend (sem fuso, já na parede do usuário) → `dd/mm/aaaa hh:mm`.
 *
 * O backend grava e devolve horário LOCAL do usuário (`now_local`), sem sufixo
 * de fuso. Passar isso por `new Date(value)` no navegador reinterpretaria como
 * UTC e deslocaria 3 horas — por isso a data é montada com `T` explícito e sem
 * `Z`, exatamente como `fmtDate` faz para o dia.
 */
export function fmtDateTime(value: string | null | undefined): string {
  if (!value) return EM_DASH;
  const texto = String(value).replace(' ', 'T').replace(/Z$/, '');
  const parsed = new Date(texto);
  if (Number.isNaN(parsed.getTime())) return fmtDate(value);
  return `${parsed.toLocaleDateString('pt-BR')} ${parsed.toLocaleTimeString('pt-BR', {
    hour: '2-digit',
    minute: '2-digit',
  })}`;
}

/**
 * Instante ISO COM fuso (`2026-10-18T22:30:00-03:00`) → `dom 18/10 22:30`, no
 * fuso `timeZone` (o do usuário, `window.timezone` da resposta).
 *
 * Diferente de `fmtDateTime`, que recebe horário de parede SEM fuso: aqui o
 * backend já mandou o instante com o deslocamento, então ele é o mesmo em
 * qualquer navegador e o `timeZone` só escolhe em que relógio mostrar. Nenhuma
 * conta de data acontece aqui. Fuso inválido cai no do navegador.
 */
export function fmtWeekdayDateTime(value: string | null | undefined, timeZone?: string): string {
  if (!value) return EM_DASH;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return EM_DASH;
  const options: Intl.DateTimeFormatOptions = {
    weekday: 'short',
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  };
  let parts: Intl.DateTimeFormatPart[];
  try {
    parts = new Intl.DateTimeFormat('pt-BR', { ...options, timeZone }).formatToParts(parsed);
  } catch {
    parts = new Intl.DateTimeFormat('pt-BR', options).formatToParts(parsed);
  }
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? '';
  const weekday = part('weekday').replace(/\.$/, '');
  return `${weekday} ${part('day')}/${part('month')} ${part('hour')}:${part('minute')}`;
}

/** Número com no máximo `digits` casas, em pt-BR. `null`/`undefined` → `—`. */
export function fmtDecimal(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(Number(value))) return EM_DASH;
  return Number(value).toLocaleString('pt-BR', { maximumFractionDigits: digits });
}

/** Inteiro em pt-BR (contagens: séries, sessões, exercícios). */
export function fmtInt(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return EM_DASH;
  return Math.round(Number(value)).toLocaleString('pt-BR');
}

/** Segundos → `N min`. */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(Number(seconds))) return EM_DASH;
  return `${Math.round(Number(seconds) / 60)} min`;
}

/** Carga de UMA série/exercício: 1 casa (`60,5 kg`). */
export function fmtKg(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return EM_DASH;
  return `${fmtDecimal(value)} kg`;
}

/**
 * Tonelagem/volume AGREGADO: arredondado para o kg (`12.480 kg`). Separado de
 * `fmtKg` de propósito — casa decimal em soma de milhares é ruído, e as telas
 * divergiam justamente nisso.
 */
export function fmtKgTotal(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return EM_DASH;
  return `${fmtInt(value)} kg`;
}

/** RPE: 1 casa, sem unidade (a coluna já diz "RPE"). */
export function fmtRpe(value: number | null | undefined): string {
  return fmtDecimal(value, 1);
}

/** Séries equivalentes (estímulo/fadiga): 2 casas por padrão. */
export function fmtSets(value: number | null | undefined, digits = 2): string {
  return fmtDecimal(value, digits);
}

/** Percentual já em pontos percentuais (`12.5` → `12,5%`). */
export function fmtPercent(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(Number(value))) return EM_DASH;
  return `${fmtDecimal(value, digits)}%`;
}

/** Variação com sinal explícito (`+3,2%` / `-1,4%`). */
export function fmtSignedPercent(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(Number(value))) return EM_DASH;
  const sign = Number(value) > 0 ? '+' : '';
  return `${sign}${fmtDecimal(value, digits)}%`;
}

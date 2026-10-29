/**
 * Formatadores das telas FORA do hub `/dashboard/*` (COD-09).
 *
 * Duas famílias convivem no frontend e NÃO são intercambiáveis:
 *
 * - `lib/dashboard-format.ts` (hub): ausente vira `—` (travessão) e as casas
 *   decimais são "no máximo N".
 * - este arquivo (telas mais antigas: registro de treinos, planos, cardio,
 *   indicadores físicos, volume por grupamento...): ausente vira `-` (hífen) e
 *   cada função preserva exatamente o que a tela mostrava antes.
 *
 * Cada função daqui substitui cópias IDÊNTICAS que estavam espalhadas pelas
 * telas. Cópias que divergiam continuam onde estavam, com o porquê registrado
 * no PR do COD-09; trocar uma tela de família muda o que o usuário vê e é
 * decisão de produto, não refatoração.
 *
 * Só apresentação: nenhuma regra numérica de domínio mora aqui
 * (docs/motores/FONTES_DA_VERDADE.md).
 */

/** Marcador de ausente das telas fora do hub. */
export const DASH = '-';

/**
 * Número em pt-BR com NO MÁXIMO `digits` casas; `null`/`undefined`/não finito
 * vira `-`. Aceita string numérica (a Data API devolve DECIMAL como string).
 *
 * Era `formatNumberOrDash` (indicadores físicos), `formatNumber` de
 * `workout-log-types` e `formatNumber` de `MuscleActivationTable`, idênticas.
 */
export function formatNumberOrDash(value: number | string | null | undefined, digits = 1): string {
  if (value == null) return DASH;
  const num = Number(value);
  if (!Number.isFinite(num)) return DASH;
  return num.toLocaleString('pt-BR', { maximumFractionDigits: digits });
}

/**
 * Número em pt-BR com EXATAMENTE `digits` casas (`12,0`). Não finito vira `-`.
 *
 * Atenção, comportamento preservado de propósito: `null` passa por `Number()`
 * e vira `0` (mostra `0,0`), como as telas originais faziam; `undefined` vira
 * `-`. Era `formatNumber` de /exercicios, `fmt` e `formatRpe` de
 * /volume-grupamentos, idênticas a menos do `digits` padrão, que agora é
 * explícito em cada chamada.
 */
export function formatFixed(value: number | null | undefined, digits: number): string {
  const n = Number(value);
  return Number.isFinite(n)
    ? n.toLocaleString('pt-BR', { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : DASH;
}

/** `formatFixed` + ` kg`. Era `formatKg` de /exercicios e de /volume-grupamentos. */
export function formatKgFixed(value: number | null | undefined, digits: number): string {
  const n = Number(value);
  return Number.isFinite(n) ? `${formatFixed(n, digits)} kg` : DASH;
}

/**
 * Contagem de séries que pode ser EQUIVALENTE ponderada (fracionária, ex.:
 * 7,5): inteiro sem casa, fracionário com 1 casa fixa; não finito vira `-`
 * (`null` vira `0`, como `formatFixed`). Era `formatSets` de
 * /volume-grupamentos; mora aqui porque a tela e o módulo de gráficos dela
 * (carregado sob demanda) usam a mesma função.
 *
 * NÃO é o `formatSets` de `methodology-volume.ts` (até 2 casas, ausente `—`),
 * nem o de `generation-warnings-format.ts` (ponto decimal, ausente `null`),
 * nem o do `WeeklyActivationPreviewTable` (sempre 1 casa, zero vira `-`).
 */
export function formatSetsCount(value: number | null | undefined): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return DASH;
  return n.toLocaleString('pt-BR', {
    minimumFractionDigits: Number.isInteger(n) ? 0 : 1,
    maximumFractionDigits: Number.isInteger(n) ? 0 : 1,
  });
}

/**
 * Dia `YYYY-MM-DD` → `dd/mm/aaaa`; vazio vira `-`; texto que não é data volta
 * como veio (inclusive um DATETIME, que não é recortado: comportamento das
 * telas originais). Era `formatDate` de /planos-treino, /planos-treino/[id] e
 * /agente-cardio, idênticas.
 */
export function formatDateOrDash(value?: string | null): string {
  if (!value) return DASH;
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

/**
 * Data e hora `dd/mm/aaaa hh:mm`; vazio vira `-`. Dia sem hora é lido como
 * meia-noite local. Era `formatDateTime` de /agente-cardio.
 */
export function formatDateTimeOrDash(value?: string | null): string {
  if (!value) return DASH;
  const parsed = new Date(value.includes('T') || value.includes(' ') ? value : `${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Vista PURA do gráfico e da tabela de `/dashboard/progressao` (alinhada à
 * regra única de progressão em 2026-10-06).
 *
 * Nada aqui decide progresso. O backend (`analytics._attach_session_evolution`
 * + `_build_progression_sessions`) já marca, por sessão:
 * - `in_verdict_key`: a sessão é da chave do veredito (mesmo equipamento e
 *   convenção da última exposição — o "Comparado em" da tela);
 * - `top_load_kg` / `top_set_reps`: carga de TOPO da exposição e as reps das
 *   séries feitas nela (a carga que a regra compara);
 * - `e1rm_reliable`: o e1RM veio de série com até 12 reps.
 *
 * Esta camada só escolhe o que plotar com esses campos — por isso mora em
 * `lib/` e é testada sem React.
 */

import { fmtDate } from './dashboard-format';
import type { ProgressionSessionItem } from './dashboard-v2-types';

export type ProgressionChartPoint = {
  label: string;
  /** e1RM só quando confiável (≤ 12 reps); senão `null` (fora da linha). */
  e1rm: number | null;
  /** Carga de topo da exposição (fallback: carga legada da sessão). */
  carga: number | null;
  /** M97 F4: o mesmo e1RM só nos pontos de FORÇA (série de destaque). */
  e1rmForca?: number | null;
  /** M97 F4: chave única do eixo X (marca de deload no mesmo dia não funde). */
  xKey?: string;
  /** M97 F4: sessão de deload (marca no gráfico; fora da linha de e1RM). */
  deload?: boolean;
  deloadLabel?: string | null;
};

export type ProgressionChart = {
  points: ProgressionChartPoint[];
  /** Sessões de outra chave (equipamento/convenção) deixadas fora do gráfico. */
  otherKeySessions: number;
  /** Pontos com e1RM omitido por vir de série com mais de 12 reps. */
  unreliableE1rm: number;
  /** M97 F4: sessões de deload marcadas (fora da linha). */
  deloadSessions?: number;
  /** M97 F4: pontos de força destacados. */
  strengthPoints?: number;
};

/** M97 F4: a sessão é de deload? (marca pronta do backend). */
export function isDeloadSession(item: Pick<ProgressionSessionItem, 'deload_source'>): boolean {
  return item.deload_source != null && item.deload_source !== '';
}

/** Carga mostrada na coluna "Carga": a de topo da exposição. */
export function sessionLoad(item: ProgressionSessionItem): number | null {
  return item.top_load_kg ?? item.best_load_kg ?? null;
}

/** Reps mostradas na coluna "Reps": as das séries de topo (`10+10+9`). */
export function sessionRepsText(item: ProgressionSessionItem): string | null {
  if (item.top_set_reps && item.top_set_reps.length > 0) return item.top_set_reps.join('+');
  return item.best_reps != null ? String(item.best_reps) : null;
}

/** `false` só quando o backend disse que o e1RM não é confiável. */
export function isSessionE1rmReliable(item: ProgressionSessionItem): boolean {
  return item.e1rm_reliable !== false;
}

/**
 * Pontos do gráfico: só as sessões da chave do veredito. Backend anterior
 * (sem `in_verdict_key` em nenhuma sessão) plota tudo, como antes.
 */
export function buildProgressionChart(
  items: readonly ProgressionSessionItem[],
  /** M97 F4: objetivos destacados (decididos pela lista de `/training-goals`); ausente = nenhum. */
  isHighlighted: (goal: string) => boolean = () => false,
): ProgressionChart {
  const keyKnown = items.some((item) => item.in_verdict_key != null);
  // M97 F4: a sessão de deload não entra na linha (o backend já manda o e1RM
  // nulo e sem chave), mas fica no gráfico como MARCA, na ordem da tabela.
  const inKey = keyKnown
    ? items.filter((item) => item.in_verdict_key === true || isDeloadSession(item))
    : [...items];
  let unreliableE1rm = 0;
  let strengthPoints = 0;
  const points = inKey.map((item, index) => {
    const deload = isDeloadSession(item);
    const reliable = isSessionE1rmReliable(item);
    if (!deload && !reliable && item.e1rm != null) unreliableE1rm += 1;
    const e1rm = !deload && reliable ? item.e1rm : null;
    const strength = !deload && item.training_goal != null && isHighlighted(item.training_goal);
    if (strength && e1rm != null) strengthPoints += 1;
    return {
      label: fmtDate(item.local_date),
      e1rm,
      carga: deload ? null : sessionLoad(item),
      e1rmForca: strength ? e1rm : null,
      xKey: `${index}`,
      deload,
      deloadLabel: deload ? (item.deload_label ?? 'Deload') : null,
    };
  });
  const otherKeySessions = items.length - inKey.length;
  const markedDeloads = points.filter((point) => point.deload).length;
  return { points, otherKeySessions, unreliableE1rm, deloadSessions: markedDeloads, strengthPoints };
}

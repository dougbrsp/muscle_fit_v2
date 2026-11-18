/**
 * Vista PURA do gráfico e da tabela de `/dashboard/progressao` (alinhada à
 * regra única de progressão em 2026-10-06).
 *
 * Nada aqui decide progresso. O backend (`analytics._attach_session_evolution`
 * + `_build_progression_sessions`) já marca, por sessão:
 * - `in_context_run`: a sessão é da sequência do contexto atual (o gráfico
 *   plota estas) e `in_verdict_run`: o veredito a usou (destaque; issue #188);
 * - `top_load_kg` / `top_set_reps`: carga de TOPO da exposição e as reps das
 *   séries feitas nela (a carga que a regra compara);
 * - `e1rm_reliable`: o e1RM veio de série com até 12 reps.
 *
 * Esta camada só escolhe o que plotar com esses campos — por isso mora em
 * `lib/` e é testada sem React.
 */

import { fmtDate } from './dashboard-format';
import { FORM_PRACTICE_LABEL, isFormPracticeRow } from './form-practice';
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
  /** Issue #188: o veredito usou esta sessão (`in_verdict_run`); `false` em calibração. */
  inVerdict?: boolean;
  /** #196: sessão de aprimoramento de técnica (não é deload); rótulo pronto. */
  formPractice?: boolean;
  formPracticeLabel?: string | null;
};

export type ProgressionChart = {
  points: ProgressionChartPoint[];
  /** Sessões de outra chave (equipamento/convenção) deixadas fora do gráfico. */
  otherKeySessions: number;
  /** Pontos com e1RM omitido por vir de série com mais de 12 reps. */
  unreliableE1rm: number;
  /** M97 F4: sessões de deload marcadas (fora da linha). */
  deloadSessions?: number;
  /** #196: sessões de aprimoramento de técnica marcadas (fora da linha). */
  formPracticeSessions?: number;
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
 * Pontos do gráfico: as sessões que o motor marcou `in_context_run` (sequência
 * do contexto atual; em calibração continuam plotadas). `inVerdict` destaca as
 * que o veredito usou (`in_verdict_run`). Backend anterior (sem
 * `in_context_run` em nenhuma sessão) plota tudo, sem filtro próprio.
 */
export function buildProgressionChart(
  items: readonly ProgressionSessionItem[],
  /** M97 F4: objetivos destacados (decididos pela lista de `/training-goals`); ausente = nenhum. */
  isHighlighted: (goal: string) => boolean = () => false,
): ProgressionChart {
  const runKnown = items.some((item) => item.in_context_run != null);
  // M97 F4 / #196: a sessão de deload e a de técnica não entram na linha (sem
  // exposição, o backend manda e1RM nulo e `in_context_run` nulo), mas ficam
  // no gráfico como MARCA, na ordem da tabela.
  const inKey = runKnown
    ? items.filter((item) => item.in_context_run === true || isDeloadSession(item) || isFormPracticeRow(item))
    : [...items];
  let unreliableE1rm = 0;
  let strengthPoints = 0;
  const points = inKey.map((item, index) => {
    const deload = isDeloadSession(item);
    const formPractice = !deload && isFormPracticeRow(item);
    const reliable = isSessionE1rmReliable(item);
    if (!deload && !reliable && item.e1rm != null) unreliableE1rm += 1;
    const e1rm = !deload && reliable ? item.e1rm : null;
    const strength = !deload && item.training_goal != null && isHighlighted(item.training_goal);
    if (strength && e1rm != null) strengthPoints += 1;
    return {
      label: fmtDate(item.local_date),
      e1rm,
      carga: deload || formPractice ? null : sessionLoad(item),
      e1rmForca: strength ? e1rm : null,
      xKey: `${index}`,
      deload,
      deloadLabel: deload ? (item.deload_label ?? 'Deload') : null,
      inVerdict: item.in_verdict_run === true,
      formPractice,
      formPracticeLabel: formPractice ? FORM_PRACTICE_LABEL : null,
    };
  });
  const otherKeySessions = items.length - inKey.length;
  const markedDeloads = points.filter((point) => point.deload).length;
  const formPracticeSessions = points.filter((point) => point.formPractice).length;
  return { points, otherKeySessions, unreliableE1rm, deloadSessions: markedDeloads, formPracticeSessions, strengthPoints };
}

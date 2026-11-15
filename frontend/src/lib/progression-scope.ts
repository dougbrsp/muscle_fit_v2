import { fmtDate } from './dashboard-format';
import { EXERCISE_PROGRESSION_LABELS, type ExerciseProgressionCode } from './dashboard-status';
import type { ExerciseProgressionCounts } from './dashboard-v2-types';
import type { DashboardWindow } from './dashboard-v3-types';

/**
 * Escopo de uma leitura de progressão (issue #152): o que cada selo compara.
 * Vem PRONTO do backend (`label` curto para a tela, `description` para o
 * tooltip); aqui só há formatação. Toda chave é opcional nas respostas: sem
 * ela (cache antigo, mock) o escopo simplesmente não aparece.
 */
export type ProgressionScope = {
  code: string;
  entity: string;
  metric: string;
  comparator: string;
  label: string;
  description: string;
};

/** Texto curto e visível do escopo; `null` sem descritor. */
export function scopeLabel(scope: ProgressionScope | null | undefined): string | null {
  return scope?.label || null;
}

/** Frase do tooltip; `null` sem descritor ou sem descrição. */
export function scopeDescription(scope: ProgressionScope | null | undefined): string | null {
  return scope?.description || null;
}

/** "15/10/2026 a 14/11/2026 (30 dias)"; `null` sem janela. */
export function scopeWindowText(window: DashboardWindow | null | undefined): string | null {
  if (!window) return null;
  const dias = `${window.days} ${window.days === 1 ? 'dia' : 'dias'}`;
  return `${fmtDate(window.date_from)} a ${fmtDate(window.date_to)} (${dias})`;
}

/** Janela e versão da regra numa linha só; `null` quando nenhum dos dois veio. */
export function scopeFootnote(
  window: DashboardWindow | null | undefined,
  ruleVersion: string | null | undefined,
): string | null {
  const parts = [
    scopeWindowText(window) ? `Janela ${scopeWindowText(window)}` : null,
    ruleVersion ? `regra ${ruleVersion}` : null,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : null;
}

const COUNT_KEYS: ReadonlyArray<[keyof ExerciseProgressionCounts, ExerciseProgressionCode]> = [
  ['progressing', 'PROGRESSING'],
  ['stable', 'STABLE'],
  ['plateau_suspected', 'PLATEAU_SUSPECTED'],
  ['regressing', 'REGRESSING'],
  ['insufficient_data', 'INSUFFICIENT_DATA'],
  ['not_comparable', 'NOT_COMPARABLE'],
];

/**
 * Composição do grupo por status de exercício ("Progredindo 2 · Estável 1"),
 * a partir de `progression_counts` do backend. Contagem zerada não aparece;
 * `null` sem contagens ou com todas zeradas.
 */
export function groupCompositionText(counts: ExerciseProgressionCounts | null | undefined): string | null {
  if (!counts) return null;
  const parts = COUNT_KEYS.filter(([key]) => (counts[key] ?? 0) > 0).map(
    ([key, code]) => `${EXERCISE_PROGRESSION_LABELS[code]} ${counts[key]}`,
  );
  return parts.length > 0 ? parts.join(' · ') : null;
}

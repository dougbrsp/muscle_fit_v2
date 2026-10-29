import type { DashboardFilters } from './dashboard-filters';
import { dashboardHref } from './dashboard-filters';
import { EM_DASH, fmtDate, fmtDecimal } from './dashboard-format';
import type {
  Insight,
  InsightComparison,
  InsightEvidence,
  ProgressionBlock,
  ProgressionStatusCount,
} from './dashboard-overview-types';
import { statusVisual } from './dashboard-status';

/**
 * Lógica PURA de `/dashboard` (visão geral, F1.6) — extraída da tela para ser
 * testada sem React e sem rede.
 *
 * Nenhuma regra mora aqui. Contagens, frases, severidade, evidência,
 * comparação, confiança e ação chegam prontas de
 * `GET /analytics/dashboard/overview`; este módulo apenas FORMATA para
 * exibição e monta links que preservam o filtro global do hub.
 */

/** Ordem de exibição dos 6 blocos (plano §8.1 do brief). */
export const OVERVIEW_BLOCK_ORDER = [
  'next_actions',
  'progression',
  'consistency',
  'priority_muscles',
  'recovery',
  'body',
] as const;

export type OverviewBlockKey = (typeof OVERVIEW_BLOCK_ORDER)[number];

export const OVERVIEW_BLOCK_TITLES: Record<OverviewBlockKey, string> = {
  next_actions: 'Próxima ação',
  progression: 'Progressão',
  consistency: 'Consistência',
  priority_muscles: 'Músculos prioritários',
  recovery: 'Recuperação',
  body: 'Corpo',
};

/** Rótulos das seções do `InsightCard` (contrato §12), na ordem em que aparecem. */
export const INSIGHT_SECTION_ORDER = [
  'observation',
  'evidence',
  'comparison',
  'confidence',
  'alternatives',
  'action',
  'reassess',
] as const;

export type InsightSectionKey = (typeof INSIGHT_SECTION_ORDER)[number];

export const INSIGHT_SECTION_LABELS: Record<InsightSectionKey, string> = {
  observation: 'Observação',
  evidence: 'Evidência',
  comparison: 'Comparação',
  confidence: 'Confiança',
  alternatives: 'Explicações alternativas',
  action: 'Ação sugerida',
  reassess: 'Quando reavaliar',
};

/** Frase do estado positivo quando nenhuma regra dispara — nunca inventa sugestão. */
export const NO_ACTIONS_MESSAGE = 'Nenhuma ação sugerida agora — continue o plano.';

const METRIC_LABELS: Readonly<Record<string, string>> = {
  weekly_effective_sets: 'Séries efetivas/semana',
  target_min_weekly_sets: 'Mínimo da meta',
  recovery_pct: 'Recuperação estimada',
  best_load_kg: 'Melhor carga',
  total_reps: 'Repetições totais',
  paired_top_reps: 'Repetições por série (séries de topo emparelhadas)',
  e1rm: 'e1RM estimado',
  exercises_regressing: 'Exercícios regredindo',
  skipped_count: 'Vezes pulado',
  sessions_with_plan: 'Sessões com esta ficha',
  direct_sets: 'Séries diretas',
  indirect_sets: 'Séries indiretas',
  body_weight_ma7: 'Peso (média 7d)',
  progress_share: 'Exercícios progredindo/estáveis',
  unmapped_working_sets_ratio: 'Séries sem vínculo',
  working_sets_total: 'Séries de trabalho',
  event_to_exposure_days: 'Dias entre evento e exposição',
  muscle_groups_below_min: 'Grupos abaixo do mínimo',
  muscle_groups_with_target: 'Grupos com meta',
};

const BASIS_LABELS: Readonly<Record<string, string>> = {
  weekly_target_min_two_consecutive_weeks: 'mínimo semanal da meta, em duas semanas consecutivas',
  weekly_target_min_two_consecutive_weeks_majority:
    'mínimo semanal da meta, em duas semanas consecutivas, na maioria dos grupos com meta',
  weekly_target_max_and_readiness_model: 'máximo semanal da meta e o modelo de prontidão',
  trailing_comparable_run: 'últimas exposições na mesma máquina/convenção de carga',
  exercise_progression_status_last_exposure: 'status de progressão na última exposição',
  plan_item_status_by_session: 'status derivado do item, sessão a sessão',
  direct_to_indirect_ratio: 'razão entre séries diretas e indiretas',
  weekly_target_achievement_between_sibling_groups: 'atingimento do alvo semanal entre grupos irmãos',
  weight_ma7_stability_and_progression_share: 'estabilidade da média de peso e parcela de exercícios progredindo',
  working_sets_without_canonical_mapping: 'séries de trabalho sem vínculo canônico',
  context_event_within_72h: 'evento de contexto até 72h antes',
};

/** Nome legível da métrica de uma evidência (o código continua disponível no título). */
export function metricLabel(metric: string): string {
  const base = metric.replace(/\[.*\]$/, '');
  const suffix = metric.slice(base.length);
  return `${METRIC_LABELS[base] ?? base}${suffix}`;
}

function fmtValue(value: number | null, unit: string | null): string {
  if (value == null) return EM_DASH;
  const digits = Number.isInteger(value) ? 0 : 1;
  return `${fmtDecimal(value, digits)}${unit ? ` ${unit}` : ''}`;
}

/**
 * "Séries efetivas/semana: 5 → 4 séries/sem (22/09/2026)". Sem valor
 * anterior mostra só o atual — nunca um zero no lugar do que não existe.
 */
export function formatEvidence(evidence: InsightEvidence): string {
  const to = fmtValue(evidence.to_value, evidence.unit);
  const range = evidence.from_value == null ? to : `${fmtValue(evidence.from_value, null)} → ${to}`;
  const when = evidence.observed_at ? ` (${fmtDate(evidence.observed_at)})` : '';
  return `${metricLabel(evidence.metric)}: ${range}${when}`;
}

/** "Base: …; janela: …; chaves: a=1, b=2". */
export function formatComparison(comparison: InsightComparison | null): string {
  if (!comparison) return 'Sem comparação declarada.';
  const keys = Object.entries(comparison.keys)
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join(',') : String(value ?? EM_DASH)}`)
    .join(', ');
  const basis = BASIS_LABELS[comparison.basis] ?? comparison.basis;
  return `Base: ${basis}; janela: ${comparison.window}${keys ? `; chaves: ${keys}` : ''}`;
}

export type InsightSection = { key: InsightSectionKey; label: string; lines: string[] };

/**
 * As sete seções do contrato §12, todas SEMPRE presentes (uma seção sem
 * conteúdo diz "—" em vez de sumir: o leitor precisa ver que a alternativa
 * ou a ação não existe, não supor que foi esquecida).
 */
export function insightSections(insight: Insight): InsightSection[] {
  const confidence = statusVisual('dataQuality', insight.data_quality).label;
  const confidenceLines = [
    `${confidence}${insight.limitations.length ? ' — limitações:' : ''}`,
    ...insight.limitations,
  ];
  const content: Record<InsightSectionKey, string[]> = {
    observation: [insight.answer],
    evidence: insight.evidence.length ? insight.evidence.map(formatEvidence) : [EM_DASH],
    comparison: [formatComparison(insight.comparison)],
    confidence: confidenceLines,
    alternatives: insight.alternative_explanations.length ? insight.alternative_explanations : [EM_DASH],
    action: [insight.suggested_action ?? 'Nenhuma ação: apenas informação.'],
    reassess: [insight.reassess_after ? fmtDate(insight.reassess_after) : EM_DASH],
  };
  return INSIGHT_SECTION_ORDER.map((key) => ({
    key,
    label: INSIGHT_SECTION_LABELS[key],
    lines: content[key],
  }));
}

/**
 * Link da tela de origem do insight preservando o filtro global quando o
 * destino é dentro do hub; fora dele (ex.: `/dominio-exercicios`) vai limpo.
 */
export function insightHref(insight: Insight, filters: DashboardFilters): string {
  return insight.source_screen.startsWith('/dashboard')
    ? dashboardHref(insight.source_screen, filters)
    : insight.source_screen;
}

export type ProgressionBar = ProgressionStatusCount & {
  /** Parcela (0–1) do total de exercícios; 0 quando não há exercício. */
  share: number;
  color: string;
  hex: string;
};

/**
 * Barras empilhadas do bloco Progressão: uma por status COM contagem, na
 * ordem do backend. Contagens são o resumo; não existe score.
 */
export function progressionBars(block: ProgressionBlock): ProgressionBar[] {
  const total = block.exercises_total;
  return block.counts
    .filter((count) => count.count > 0)
    .map((count) => {
      const visual = statusVisual('exerciseProgression', count.status);
      return {
        ...count,
        share: total > 0 ? count.count / total : 0,
        color: visual.color,
        hex: visual.hex,
      };
    });
}

/** "Progredindo 3 · Estável 2 · Regredindo 1" para a legenda e o `aria-label`. */
export function progressionLegend(bars: readonly ProgressionBar[]): string {
  return bars.map((bar) => `${bar.label} ${bar.count}`).join(' · ');
}

/** "≈ 2 dias" / "≈ 8 h" a partir de horas; `null` quando não há prazo. */
export function reassessHoursLabel(hours: number | null): string | null {
  if (hours == null || !Number.isFinite(hours) || hours < 0) return null;
  if (hours >= 24) return `≈ ${Math.round(hours / 24)} dia${Math.round(hours / 24) === 1 ? '' : 's'}`;
  return `≈ ${Math.round(hours)} h`;
}

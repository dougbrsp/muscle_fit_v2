/**
 * Lógica PURA da tela `/dashboard/exercicios/[exerciseId]` (plano §8.2, F1.3).
 *
 * Tudo aqui é apresentação e seleção do que já veio pronto de
 * `GET /analytics/dashboard/exercises/{id}/progress`. Nada nesta camada
 * classifica progressão, calcula e1RM ou decide comparabilidade DE NOVO: o
 * status, as razões, o e1RM e a `comparability_key` de cada exposição chegam
 * do backend (`exposure_service.py` + `comparability.py`).
 *
 * O que existe aqui e por quê:
 * - `statusReasonsText`: os códigos de `comparability.REASON_*` viram frase em
 *   pt-BR. O backend emite código de propósito ("o frontend traduz; nenhum
 *   texto livre aqui"), então este é o lugar previsto para o texto.
 * - `comparabilityMismatch`: compara a chave JÁ CALCULADA de uma exposição com
 *   a chave atual para dizer, na tabela, por que aquela linha não conta. É
 *   igualdade de campos, não uma segunda implementação da regra §8.1 — a
 *   classificação oficial continua sendo `status`/`status_reasons`.
 * - `buildExposurePair` e `buildTrendSeries`: montagem de vista (par
 *   anterior/atual e série do gráfico sem as exposições não comparáveis).
 *
 * Mora em `lib/` e não na `page.tsx` porque é exatamente o que dá para testar
 * sem React (critério de testes da F1.3).
 */

import { dashboardHref, type DashboardFilters } from './dashboard-filters';
import { fmtDate, fmtDecimal } from './dashboard-format';
import { EXERCISE_PROGRESSION_LABELS } from './dashboard-status';
import motorConstants from './motor-constants.generated.json';
import type {
  ComparabilityKey,
  DataNature,
  DataQuality,
  DeloadSession,
  E1rmPoint,
  ExerciseRecord,
  Exposure,
  LatestComparison,
  Metric,
  ProgressionDecision,
} from './dashboard-v3-types';
import type { LoadConvention } from './equipment-exercises-types';
import { MEASURE_REASON_LABELS, REASON_MEASURE_NOT_REP_BASED } from './set-measures';

// ---------------------------------------------------------------------------
// Navegação
// ---------------------------------------------------------------------------

export const EXERCISE_PROGRESS_BASE_PATH = '/dashboard/exercicios';

/**
 * Link para o drill-down do exercício PRESERVANDO o filtro global do hub — a
 * janela escolhida em `/dashboard/progressao` continua valendo na tela nova.
 *
 * `exerciseId` é o id canônico (`exercise_catalog`): o mesmo `items[].id` de
 * `GET /mapping-canonical/options` e o `top_exercises[].mapping_id` do
 * drill-down de músculos.
 */
export function exerciseProgressHref(
  exerciseId: number,
  filters: DashboardFilters,
): string {
  return dashboardHref(`${EXERCISE_PROGRESS_BASE_PATH}/${exerciseId}`, filters);
}

// ---------------------------------------------------------------------------
// Razões do status (`comparability.REASON_*`)
// ---------------------------------------------------------------------------

/**
 * Espelho dos códigos `REASON_*` de
 * `api/app/intelligence/dashboard/comparability.py`. A frase explica POR QUE a
 * pessoa recebeu aquele status — é o ponto do §7 ("progresso em condições
 * comparáveis?"): sem isso o badge é um veredito sem justificativa.
 */
export const STATUS_REASON_TEXTS: Readonly<Record<string, string>> = {
  same_load_more_reps:
    'Mesma carga (dentro de 2%) com mais repetições por série do que na exposição anterior (séries comparadas uma a uma).',
  load_up_reps_in_prescribed_range:
    'A carga subiu e as repetições ficaram dentro da faixa prescrita na ficha.',
  same_performance_lower_rpe:
    'Mesma carga e mesmas repetições, com esforço percebido (RPE) menor — o trabalho ficou mais fácil.',
  e1rm_up: '1RM estimado subiu pelo menos 3%, com séries de até 12 repetições nas duas exposições.',
  e1rm_down: '1RM estimado caiu 3% ou mais, com séries de até 12 repetições nas duas exposições.',
  same_load_fewer_reps: 'Mesma carga, porém com menos repetições por série do que na exposição anterior.',
  same_reps_higher_rpe:
    'Mesma carga e as mesmas repetições por série, com esforço percebido (RPE) maior: isso não conta como regressão — o desempenho ficou estável.',
  paired_reps_effort_adjusted:
    'As repetições por série foram comparadas somadas às repetições em reserva (reps + RIR, do RPE de cada série): 11 repetições a RPE 9 valem o mesmo que 12 a RPE 10.',
  more_load_fewer_reps_inconclusive:
    'Mais carga com menos repetições: isoladamente isso não é progresso nem regressão — só o 1RM estimado decidiria.',
  no_change: 'Nenhuma diferença relevante entre a última exposição e a anterior.',
  comparability_key_changed:
    'A última exposição mudou de equipamento ou de convenção de carga: comparar com a anterior levaria a conclusão errada.',
  not_enough_comparable_exposures:
    'São necessárias duas exposições comparáveis (mesmo equipamento e mesma convenção de carga) no período; há menos que isso.',
  equipment_not_resolved:
    'A última exposição não tem equipamento identificado, então não dá para garantir que foi o mesmo aparelho.',
  e1rm_unavailable_without_rpe:
    'Sem RPE não há RIR, e sem RIR não há 1RM estimado por exercício — este critério ficou de fora da avaliação. (Só o Desempenho por grupo assume RIR 0 nas séries sem RPE.)',
  bodyweight_reps_only:
    'Convenção "peso corporal": a carga movida não está registrada na série, então a comparação olha apenas as repetições.',
  no_progress_nor_regression_in_run:
    'Nenhuma das últimas exposições comparáveis apresentou progresso nem regressão.',
  fewer_working_sets:
    'Você fez menos séries que na exposição anterior. Isso não conta como regressão: as repetições foram comparadas série a série.',
  other_fatigue_context_exposures_skipped:
    'Exposições feitas em outra condição de fadiga (o exercício em outra posição do treino, depois de outros que cansaram o mesmo músculo) ficaram de fora: a comparação é só entre exposições na mesma condição.',
  first_exposure_in_fatigue_context:
    'É a primeira exposição nesta condição de fadiga (outra posição no treino). Ela ainda não tem com o que ser comparada e não conta como progresso nem regressão.',
  // Medidas de série (Lote T3): o texto é o rótulo do dono
  // (`set_measures.MEASURE_REASON_LABELS`), lido do JSON gerado.
  measure_not_rep_based: MEASURE_REASON_LABELS[REASON_MEASURE_NOT_REP_BASED],
};

export type StatusReasonText = {
  code: string;
  text: string;
  /** `false` quando o código não existe neste frontend (contrato à frente). */
  known: boolean;
};

/**
 * Códigos → frases, preservando a ordem do backend. Código desconhecido NÃO
 * some: aparece com o próprio código, para a divergência de versão ficar
 * visível em vez de virar uma tela silenciosamente incompleta.
 */
export function statusReasonsText(reasons: readonly string[] | null | undefined): StatusReasonText[] {
  if (!reasons) return [];
  return reasons.map((code) => {
    const text = STATUS_REASON_TEXTS[code];
    return { code, text: text ?? code, known: text != null };
  });
}

// ---------------------------------------------------------------------------
// Seta "Evolução" por sessão de `/dashboard/progressao`
// ---------------------------------------------------------------------------

export type SessionEvolutionKind = 'up' | 'down' | 'flat' | 'not_comparable' | 'none';

export type SessionEvolutionView = {
  kind: SessionEvolutionKind;
  /** Texto curto exibido ao lado da seta. */
  label: string;
  /** Por quê (razões do backend em frase), para o tooltip. Vazio = sem tooltip. */
  tooltip: string;
};

/**
 * `items[].evolution` (já classificado no backend por `compare_exposures`) →
 * o que a célula desenha. Só apresentação: nenhuma comparação de números
 * acontece aqui. Status desconhecido cai em `none` em vez de inventar seta.
 */
export function sessionEvolutionView(
  evolution: { status: string; reasons?: readonly string[] | null } | null | undefined,
): SessionEvolutionView {
  if (!evolution) return { kind: 'none', label: '—', tooltip: '' };
  const tooltip = statusReasonsText(evolution.reasons)
    .map((reason) => reason.text)
    .join(' ');
  switch (evolution.status) {
    case 'PROGRESSING':
      return { kind: 'up', label: 'Progrediu', tooltip };
    case 'REGRESSING':
      return { kind: 'down', label: 'Regrediu', tooltip };
    case 'STABLE':
      return { kind: 'flat', label: EXERCISE_PROGRESSION_LABELS.STABLE, tooltip };
    case 'NOT_COMPARABLE':
      return { kind: 'not_comparable', label: EXERCISE_PROGRESSION_LABELS.NOT_COMPARABLE, tooltip };
    default:
      return { kind: 'none', label: '—', tooltip: '' };
  }
}

// ---------------------------------------------------------------------------
// Comparabilidade de uma exposição com a chave ATUAL (§8.1)
// ---------------------------------------------------------------------------

export type ComparabilityMismatch =
  | 'equipment'
  | 'load_convention'
  | 'equipment_unknown'
  | 'fatigue_context_changed'
  | null;

export const COMPARABILITY_MISMATCH_LABELS: Readonly<Record<
  Exclude<ComparabilityMismatch, null>,
  string
>> = {
  equipment: 'Outro equipamento',
  load_convention: 'Outra convenção de carga',
  equipment_unknown: 'Equipamento não identificado',
  fatigue_context_changed: 'Outra condição de fadiga',
};

export const COMPARABILITY_MISMATCH_HINTS: Readonly<Record<
  Exclude<ComparabilityMismatch, null>,
  string
>> = {
  equipment:
    'Esta exposição foi em outro equipamento. Mudar de aparelho muda a carga possível, então ela não entra na comparação nem no gráfico.',
  load_convention:
    'A convenção de carga desta exposição é diferente da atual (ex.: por halter vs. carga total). Os números não significam a mesma coisa.',
  equipment_unknown:
    'Esta exposição não tem equipamento identificado, então não é possível afirmar que foi o mesmo aparelho da exposição atual.',
  fatigue_context_changed:
    'Esta exposição foi feita em outra condição de fadiga (outra posição no treino, com o músculo mais ou menos cansado pelos exercícios anteriores). Ela não entra na comparação nem no gráfico do status atual.',
};

/**
 * Por que esta exposição NÃO é comparável com a chave atual — ou `null` quando
 * é. Vem PRONTO do backend (`mismatch_with_current`, calculado por
 * `comparability.mismatch_with_current` sobre `keys_match`): o frontend não
 * reimplementa a regra §8.1 (M76 Lote 3c, C13).
 */
export function comparabilityMismatch(
  exposure: Pick<Exposure, 'mismatch_with_current'>,
): ComparabilityMismatch {
  return exposure.mismatch_with_current ?? null;
}

/**
 * Rótulo do contexto de fadiga prévia — o texto do dono
 * (`PRIOR_FATIGUE_BAND_LABELS`), que chega pronto em `band_label`.
 */
export function fatigueContextLabel(
  context: { band_label?: string | null } | null | undefined,
): string | null {
  return context?.band_label ?? null;
}

/** "Progresso · pré-fatigado": o status vale para o contexto da última exposição. */
export function statusWithContextLabel(
  statusLabel: string,
  context: { band_label?: string | null } | null | undefined,
): string {
  const label = fatigueContextLabel(context);
  return label ? `${statusLabel} · ${label}` : statusLabel;
}

export function isComparableWithCurrent(exposure: Pick<Exposure, 'mismatch_with_current'>): boolean {
  return comparabilityMismatch(exposure) === null;
}

// ---------------------------------------------------------------------------
// Par "anterior vs. atual"
// ---------------------------------------------------------------------------

export type ExposureMetricCode = 'best_load_kg' | 'paired_reps' | 'total_reps' | 'avg_rpe' | 'e1rm' | 'tonnage';

/** Direção que significa MELHORA — não é veredito, é legenda da variação. */
export type BetterWhen = 'up' | 'down' | 'neutral';

type MetricDescriptor = {
  code: ExposureMetricCode;
  label: string;
  unit: string | null;
  digits: number;
  nature: DataNature;
  /** Chave em `metric_versions` do payload. */
  versionKey: string;
  betterWhen: BetterWhen;
};

const METRIC_DESCRIPTORS: readonly MetricDescriptor[] = [
  {
    code: 'best_load_kg',
    label: 'Melhor carga',
    unit: 'kg',
    digits: 1,
    nature: 'OBSERVED',
    versionKey: 'exposure',
    betterWhen: 'up',
  },
  {
    // O que a regra compara: reps POR SÉRIE emparelhada nas cargas de topo
    // (N = menor número de séries de topo dos dois lados). Vem pronto do
    // backend em `latest_comparison.paired_reps`.
    code: 'paired_reps',
    label: 'Repetições por série (séries de topo)',
    unit: null,
    digits: 0,
    nature: 'DERIVED',
    versionKey: 'exposure',
    betterWhen: 'up',
  },
  {
    // Informativo: fazer menos séries não é perder desempenho, então a soma
    // de todas as séries não é pintada de verde/vermelho.
    code: 'total_reps',
    label: 'Repetições (total, informativo)',
    unit: null,
    digits: 0,
    nature: 'DERIVED',
    versionKey: 'exposure',
    betterWhen: 'neutral',
  },
  {
    code: 'avg_rpe',
    label: 'RPE médio',
    unit: null,
    digits: 1,
    nature: 'DERIVED',
    versionKey: 'exposure',
    betterWhen: 'down',
  },
  {
    code: 'e1rm',
    label: '1RM estimado',
    unit: 'kg',
    digits: 1,
    nature: 'ESTIMATED',
    versionKey: 'e1rm',
    betterWhen: 'up',
  },
  {
    // Volume, não desempenho: menos séries derrubam a tonelagem sem que isso
    // seja regressão — variação neutra, como o total de reps.
    code: 'tonnage',
    label: 'Tonelagem',
    unit: 'kg',
    digits: 0,
    nature: 'DERIVED',
    versionKey: 'tonnage',
    betterWhen: 'neutral',
  },
];

/**
 * `assisted` inverte o sentido da carga (menos quilos de assistência = mais
 * forte) e `bodyweight` não registra a carga movida — mesma regra de
 * `comparability.inverts_load_direction`/`compares_reps_only`, aplicada aqui só
 * para a LEGENDA da variação não mentir sobre o que é melhora.
 */
export function loadBetterWhen(convention: LoadConvention | null | undefined): BetterWhen {
  if (convention === 'assisted') return 'down';
  if (convention === 'bodyweight') return 'neutral';
  return 'up';
}

export type ExposurePairRow = {
  code: ExposureMetricCode;
  label: string;
  betterWhen: BetterWhen;
  previous: Metric;
  current: Metric;
  /** Diferença absoluta (atual − anterior); `null` se faltar um dos lados. */
  deltaAbs: number | null;
  /** Variação percentual em pontos (`+3.2`); `null` quando o anterior é 0. */
  deltaPct: number | null;
  /** Detalhe de cada lado (ex.: reps emparelhadas `11+10`). */
  previousDetail?: string | null;
  currentDetail?: string | null;
  /** Aviso da linha (ex.: e1RM não decisivo acima de 12 reps). */
  note?: string | null;
};

export type ExposurePair = {
  current: Exposure;
  previous: Exposure | null;
  /** A anterior é comparável com a atual? */
  comparable: boolean;
  mismatch: ComparabilityMismatch;
  rows: ExposurePairRow[];
};

function metricEnvelope(
  value: number | null,
  descriptor: MetricDescriptor,
  metricVersions: Readonly<Record<string, string>>,
  dataQuality: DataQuality,
): Metric {
  return {
    value,
    unit: descriptor.unit,
    nature: descriptor.nature,
    metric_version: metricVersions[descriptor.versionKey] ?? 'desconhecida',
    data_quality: dataQuality,
    limitations: [],
  };
}

function pctChange(current: number | null, previous: number | null): number | null {
  if (current == null || previous == null || previous === 0) return null;
  return ((current - previous) / Math.abs(previous)) * 100;
}

/**
 * Última exposição e a imediatamente anterior, com a variação de cada métrica.
 *
 * Usa as DUAS últimas posições do histórico (a mesma dupla que o backend
 * classifica em `classify_exercise_progression`) e marca se elas são
 * comparáveis — em vez de escolher silenciosamente uma exposição antiga só
 * porque ela "casa" com a chave atual, o que esconderia a troca de aparelho.
 */
/**
 * O e1RM decidiu a comparação? Só com séries de até 12 reps nos DOIS lados
 * (teto da regra). Vem pronto em `latest_comparison.e1rm_decisive`; backend
 * antigo (sem o campo) cai no `e1rm_reliable` de cada exposição e, sem ele
 * também, mantém a leitura de antes.
 */
export function isE1rmDecisive(
  previous: Exposure | null,
  current: Exposure,
  latestComparison: LatestComparison | null | undefined,
): boolean {
  if (latestComparison) return latestComparison.e1rm_decisive;
  if (!previous) return false;
  return previous.e1rm_reliable !== false && current.e1rm_reliable !== false;
}

/** Rótulo da linha de reps emparelhadas quando o backend as ajustou pelo esforço (M78). */
export const PAIRED_REPS_EFFORT_LABEL = 'Reps + RIR por série (séries de topo)';

/** `11+10` — só formata os números prontos do backend (meia rep com vírgula). */
function joinReps(values: readonly number[] | undefined): string {
  return (values ?? []).map((value) => fmtDecimal(value, 1)).join('+');
}

export const E1RM_NOT_DECISIVE_NOTE =
  'Não decisivo: alguma das séries do 1RM estimado passou de 12 repetições — a comparação usa as repetições por série.';

export function buildExposurePair(payload: {
  exposures: readonly Exposure[];
  metric_versions: Readonly<Record<string, string>>;
  data_quality: DataQuality;
  latest_comparison?: LatestComparison | null;
}): ExposurePair | null {
  const { exposures, metric_versions: versions, data_quality: quality } = payload;
  if (exposures.length === 0) return null;

  const current = exposures[exposures.length - 1];
  // A anterior COMPARÁVEL (mesma chave, inclusive o contexto de fadiga) — a
  // mesma que o backend compara: exposições de outro contexto são puladas,
  // troca de equipamento/convenção interrompe. Sem nenhuma, a imediatamente
  // anterior, para a tela dizer por que não comparou.
  let previous: Exposure | null = null;
  for (let index = exposures.length - 2; index >= 0; index -= 1) {
    const mismatch = comparabilityMismatch(exposures[index]);
    if (mismatch === null) {
      previous = exposures[index];
      break;
    }
    if (mismatch !== 'fatigue_context_changed') break;
  }
  if (previous == null && exposures.length >= 2) previous = exposures[exposures.length - 2];
  const mismatch = previous ? comparabilityMismatch(previous) : null;
  const comparable = previous != null && mismatch === null;
  const paired = comparable ? payload.latest_comparison?.paired_reps ?? null : null;
  const e1rmDecisive = isE1rmDecisive(previous, current, payload.latest_comparison);

  const valueOf = (exposure: Exposure | null, code: ExposureMetricCode, side: 'previous' | 'current') => {
    if (code === 'paired_reps') {
      if (!paired) return null;
      return side === 'previous' ? paired.previous_sum : paired.current_sum;
    }
    return exposure ? exposure[code] ?? null : null;
  };

  const rows = METRIC_DESCRIPTORS.map((descriptor) => {
    const currentValue = valueOf(current, descriptor.code, 'current');
    const previousValue = valueOf(previous, descriptor.code, 'previous');
    const betterWhen =
      descriptor.code === 'best_load_kg'
        ? loadBetterWhen(current.comparability_key.load_convention)
        : descriptor.code === 'e1rm' && !e1rmDecisive
          ? 'neutral'
          : descriptor.betterWhen;
    // Com `effort_adjusted` os valores já vêm como reps + RIR do backend; o
    // detalhe mostra também as reps registradas das mesmas séries.
    const detail =
      descriptor.code === 'paired_reps' && paired
        ? paired.effort_adjusted
          ? {
              previousDetail: `${joinReps(paired.previous)} (reps ${joinReps(paired.previous_reps)})`,
              currentDetail: `${joinReps(paired.current)} (reps ${joinReps(paired.current_reps)})`,
            }
          : { previousDetail: joinReps(paired.previous), currentDetail: joinReps(paired.current) }
        : {};
    const label =
      descriptor.code === 'paired_reps' && paired?.effort_adjusted ? PAIRED_REPS_EFFORT_LABEL : descriptor.label;
    const note = descriptor.code === 'e1rm' && comparable && !e1rmDecisive ? E1RM_NOT_DECISIVE_NOTE : null;
    return {
      ...detail,
      note,
      code: descriptor.code,
      label,
      betterWhen,
      previous: metricEnvelope(previousValue, descriptor, versions, quality),
      current: metricEnvelope(currentValue, descriptor, versions, quality),
      // Sem par comparável, variação não significa nada: fica `null` em vez de
      // um número que a pessoa leria como progresso.
      deltaAbs:
        comparable && currentValue != null && previousValue != null ? currentValue - previousValue : null,
      deltaPct: comparable ? pctChange(currentValue, previousValue) : null,
    } satisfies ExposurePairRow;
  });

  return { current, previous, comparable, mismatch, rows };
}

// ---------------------------------------------------------------------------
// Série do gráfico de tendência
// ---------------------------------------------------------------------------

export type TrendMetricCode = 'e1rm' | 'best_load_kg' | 'total_reps';

export type TrendPoint = {
  label: string;
  localDate: string | null;
  activityId: number | null;
  value: number | null;
};

export type TrendSeries = {
  /** `null` quando não há duas exposições comparáveis com valor. */
  metric: TrendMetricCode | null;
  label: string;
  /** Sufixo do eixo Y (` kg`), ou `null` para grandeza sem unidade. */
  unit: string | null;
  points: TrendPoint[];
  /** Quantas exposições ficaram de fora por não serem comparáveis. */
  excluded: number;
};

const TREND_LABELS: Readonly<Record<TrendMetricCode, { label: string; unit: string | null }>> = {
  e1rm: { label: '1RM estimado', unit: 'kg' },
  best_load_kg: { label: 'Melhor carga', unit: 'kg' },
  total_reps: { label: 'Repetições (total)', unit: null },
};

function countPresent(exposures: readonly Exposure[], code: TrendMetricCode): number {
  return exposures.reduce((total, item) => (item[code] == null ? total : total + 1), 0);
}

/**
 * Linha de tendência sobre as exposições COMPARÁVEIS com a chave atual.
 *
 * Métrica escolhida pelo dado que existe, nunca por regra nova:
 * 1. e1RM, se ao menos duas exposições comparáveis o têm (é a grandeza que o
 *    §8.2 usa para progresso/regressão);
 * 2. `bodyweight` compara só repetições (a carga movida não está na série);
 * 3. senão a melhor carga; em último caso as repetições.
 *
 * Eixo Y ÚNICO por construção: uma série só. A regra "nunca dual-axis" está
 * documentada em `DashboardProgressaoClient.tsx`.
 */
export function buildTrendSeries(
  exposures: readonly Exposure[],
  current: ComparabilityKey | null | undefined,
): TrendSeries {
  const comparable = exposures.filter((item) => isComparableWithCurrent(item));
  const excluded = exposures.length - comparable.length;

  const repsOnly = current?.load_convention === 'bodyweight';
  let metric: TrendMetricCode | null = null;
  if (!repsOnly && countPresent(comparable, 'e1rm') >= 2) metric = 'e1rm';
  else if (repsOnly && countPresent(comparable, 'total_reps') >= 2) metric = 'total_reps';
  else if (countPresent(comparable, 'best_load_kg') >= 2) metric = 'best_load_kg';
  else if (countPresent(comparable, 'total_reps') >= 2) metric = 'total_reps';

  if (metric == null) {
    return { metric: null, label: '', unit: null, points: [], excluded };
  }

  const { label, unit } = TREND_LABELS[metric];
  return {
    metric,
    label,
    unit,
    points: comparable.map((item) => ({
      label: fmtDate(item.local_date),
      localDate: item.local_date,
      activityId: item.activity_id,
      value: item[metric as TrendMetricCode] ?? null,
    })),
    excluded,
  };
}

// ---------------------------------------------------------------------------
// Linha ÚNICA de e1RM entre objetivos (M97 F4)
// ---------------------------------------------------------------------------

export type UnifiedE1rmPoint = {
  /** Chave única do eixo X (duas exposições no mesmo dia não se fundem). */
  xKey: string;
  label: string;
  localDate: string | null;
  activityId: number | null;
  /** e1RM do ponto (`null` nas marcas de deload: fora da linha). */
  value: number | null;
  /** O mesmo valor só nos pontos de FORÇA (série de destaque). */
  strengthValue: number | null;
  isStrength: boolean;
  isDeload: boolean;
  deloadLabel: string | null;
  reliable: boolean;
};

export type UnifiedE1rmChart = {
  points: UnifiedE1rmPoint[];
  /** Pelo menos dois pontos com e1RM (dá para desenhar uma linha). */
  hasSeries: boolean;
  strengthPoints: number;
  deloadSessions: number;
  /** Pontos de outra chave física (outro equipamento/convenção), fora da linha. */
  otherKeyPoints: number;
};

/**
 * Monta o gráfico de e1RM ÚNICO do drill-down: os pontos de TODOS os objetivos
 * (o e1RM é único — decisão do usuário), na mesma chave física do recorde, com
 * os de força destacados, e as sessões de deload como marcas fora da linha.
 * Tudo chega pronto do backend (`e1rm_series`, `deload_sessions`); aqui só se
 * ordena e se escolhe a série de cada ponto.
 */
export function buildUnifiedE1rmChart(
  series: readonly E1rmPoint[] | null | undefined,
  deloads: readonly DeloadSession[] | null | undefined,
  /** Quais objetivos destacar (o chamador decide pela lista de `/training-goals`); ausente = nenhum. */
  isHighlighted: (goal: string) => boolean = () => false,
): UnifiedE1rmChart {
  const all = series ?? [];
  const sameKey = all.filter((point) => point.same_physical_key !== false);
  type Raw = { localDate: string | null; order: number; point: UnifiedE1rmPoint };
  const raw: Raw[] = [];
  sameKey.forEach((point, index) => {
    const isStrength = isHighlighted(point.training_goal);
    raw.push({
      localDate: point.local_date,
      order: index,
      point: {
        xKey: '',
        label: fmtDate(point.local_date),
        localDate: point.local_date,
        activityId: point.activity_id,
        value: point.e1rm,
        strengthValue: isStrength ? point.e1rm : null,
        isStrength,
        isDeload: false,
        deloadLabel: null,
        reliable: point.e1rm_reliable,
      },
    });
  });
  (deloads ?? []).forEach((session, index) => {
    raw.push({
      localDate: session.local_date,
      order: sameKey.length + index,
      point: {
        xKey: '',
        label: fmtDate(session.local_date),
        localDate: session.local_date,
        activityId: session.activity_id,
        value: null,
        strengthValue: null,
        isStrength: false,
        isDeload: true,
        deloadLabel: session.deload_label ?? 'Deload',
        reliable: false,
      },
    });
  });
  raw.sort((a, b) => {
    const left = a.localDate ?? '';
    const right = b.localDate ?? '';
    if (left !== right) return left < right ? -1 : 1;
    return a.order - b.order;
  });
  const points = raw.map((item, index) => ({ ...item.point, xKey: `${index}` }));
  return {
    points,
    hasSeries: points.filter((point) => point.value != null).length >= 2,
    strengthPoints: points.filter((point) => point.isStrength).length,
    deloadSessions: points.filter((point) => point.isDeload).length,
    otherKeyPoints: all.length - sameKey.length,
  };
}

// ---------------------------------------------------------------------------
// RPE médio por SESSÃO (F2.5 / M35)
// ---------------------------------------------------------------------------

/**
 * A tela legada `/exercicios` mostrava carga, volume e RPE por sessão; o hub
 * tinha os dois primeiros e não o terceiro. Este é o terceiro.
 *
 * **Só séries `working`.** `ExposureOut.avg_rpe` é calculado pelo
 * `exposure_service`, que lê apenas séries de trabalho (`sql_is_working`) —
 * aquecimento e série de ajuste nunca entram. Foi uma decisão, não um
 * acidente: o RPE de um aquecimento mede o esforço de aquecer, e somá-lo à
 * média puxaria o número para baixo, fazendo uma sessão dura parecer leve. A
 * progressão do projeto (status, e1RM, tonelagem) já lê só `working`;
 * incluir preparatórias AQUI criaria dois RPE médios incompatíveis na mesma
 * tela, e o usuário não teria como saber qual dos dois está olhando.
 *
 * **Não é veredito.** RPE subindo com a mesma carga costuma indicar fadiga
 * acumulada, mas também pode ser sono ruim, cafeína, ordem do exercício na
 * sessão. A tela mostra a linha e diz isso; quem classifica progressão é o
 * `status` do backend, que não usa RPE isolado.
 */
export type RpeSeries = {
  /** `false` quando menos de duas exposições comparáveis têm RPE. */
  hasSeries: boolean;
  points: TrendPoint[];
  /** Média simples das exposições com RPE — `null` se nenhuma tem. */
  average: number | null;
  /** Exposições comparáveis SEM RPE registrado. */
  missing: number;
  /** Exposições descartadas por não serem comparáveis com a chave atual. */
  excluded: number;
};

export function buildRpeSeries(exposures: readonly Exposure[]): RpeSeries {
  const comparable = exposures.filter((item) => isComparableWithCurrent(item));
  const excluded = exposures.length - comparable.length;
  const withRpe = comparable.filter((item) => item.avg_rpe != null);

  const points = comparable.map((item) => ({
    label: fmtDate(item.local_date),
    localDate: item.local_date,
    activityId: item.activity_id,
    value: item.avg_rpe ?? null,
  }));

  const average =
    withRpe.length > 0
      ? Math.round((withRpe.reduce((total, item) => total + (item.avg_rpe ?? 0), 0) / withRpe.length) * 10) / 10
      : null;

  return {
    hasSeries: withRpe.length >= 2,
    points,
    average,
    missing: comparable.length - withRpe.length,
    excluded,
  };
}

// ---------------------------------------------------------------------------
// Decisões do motor de progressão (evidência secundária, §8.2)
// ---------------------------------------------------------------------------

/**
 * Marca do "Melhor 1RM estimado" (M76 Lote 3d): o backend só manda um e1RM
 * NÃO confiável (`reliable === false`, séries acima do teto de reps) quando a
 * chave não tem nenhum confiável. A tela só exibe a marca — o teto vem no
 * payload (`max_reliable_reps`, dono `domain/e1rm.py`), não é escrito aqui.
 * `null` = confiável, resposta antiga ou sem e1RM.
 */
export function e1rmReliabilityNote(record: ExerciseRecord | null | undefined): string | null {
  if (!record || record.value == null || record.reliable !== false) return null;
  const teto = record.max_reliable_reps;
  return teto != null ? `não confiável (>${teto} reps)` : 'não confiável';
}

/**
 * Ações de `domain/progression_engine.py` em pt-BR. Dono: `DECISION_LABELS`
 * (`api/app/domain/exercise_briefing.py`), exportado para
 * `motor-constants.generated.json` (RPE 10 Lote 6/P9: era uma cópia manual;
 * paridade em `api/tests/unit/test_m76_lote1b_espelhos_fe.py`).
 */
export const PROGRESSION_DECISION_LABELS: Readonly<Record<string, string>> =
  motorConstants.progression_decision_labels;

export function progressionDecisionLabel(decision: string | null | undefined): string {
  if (!decision) return '—';
  return PROGRESSION_DECISION_LABELS[decision] ?? decision;
}

/**
 * Decisões do motor que valem como evidência PARA ESTA CHAVE: as de outro
 * equipamento falam de outra trilha. `equipment_id` nulo (trilha sem
 * equipamento) é mantido — descartá-lo esconderia o histórico antigo.
 */
export function decisionsForKey(
  decisions: readonly ProgressionDecision[],
  current: ComparabilityKey | null | undefined,
): ProgressionDecision[] {
  if (!current || current.equipment_agnostic || current.equipment_id == null) return [...decisions];
  return decisions.filter(
    (item) => item.equipment_id == null || item.equipment_id === current.equipment_id,
  );
}

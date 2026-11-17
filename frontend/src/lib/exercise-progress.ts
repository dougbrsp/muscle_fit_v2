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
 * - `comparabilityMismatch`: lê o `mismatch_with_current` pronto só para o
 *   RÓTULO/dica de uma linha da tabela; nunca filtra nem decide.
 * - `buildExposurePair`, `buildTrendSeries`, `buildRpeSeries`: montagem de
 *   vista. O par, o desfecho (`metrics[].outcome`), os pontos do gráfico
 *   (`in_context_run`) e as decisões do contexto (`in_current_context`) vêm
 *   marcados pelo motor (issue #188); aqui nada é escolhido.
 *
 * Mora em `lib/` e não na `page.tsx` porque é exatamente o que dá para testar
 * sem React (critério de testes da F1.3).
 */

import { dashboardHref, type DashboardFilters } from "./dashboard-filters";
import { fmtDate, fmtDecimal, fmtSignedPercent } from "./dashboard-format";
import { EXERCISE_PROGRESSION_LABELS } from "./dashboard-status";
import motorConstants from "./motor-constants.generated.json";
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
} from "./dashboard-v3-types";
import type { ExerciseProgressResponse } from "./analytics-types";
import {
  MEASURE_REASON_LABELS,
  REASON_MEASURE_NOT_REP_BASED,
} from "./set-measures";

// ---------------------------------------------------------------------------
// Navegação
// ---------------------------------------------------------------------------

export const EXERCISE_PROGRESS_BASE_PATH = "/dashboard/exercicios";

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
// Limiares do dono (`comparability.THRESHOLDS`, `domain/e1rm.py`), lidos do JSON gerado.
const LIMIARES = motorConstants.progression_thresholds;

export const STATUS_REASON_TEXTS: Readonly<Record<string, string>> = {
  same_load_more_reps: `Mesma carga (dentro de ${LIMIARES.same_load_tolerance_percent}%) com mais repetições por série do que na exposição anterior (séries comparadas uma a uma).`,
  load_up_reps_in_prescribed_range:
    "A carga subiu e as repetições ficaram dentro da faixa prescrita na ficha.",
  same_performance_lower_rpe:
    "Mesma carga e mesmas repetições, com esforço percebido (RPE) menor — o trabalho ficou mais fácil.",
  e1rm_up: `1RM estimado subiu pelo menos ${LIMIARES.e1rm_progress_percent}%, com séries de até ${LIMIARES.e1rm_max_reliable_reps} repetições nas duas exposições.`,
  e1rm_down: `1RM estimado caiu ${LIMIARES.e1rm_progress_percent}% ou mais, com séries de até ${LIMIARES.e1rm_max_reliable_reps} repetições nas duas exposições.`,
  same_load_fewer_reps:
    "Mesma carga, porém com menos repetições por série do que na exposição anterior.",
  same_reps_higher_rpe:
    "Mesma carga e as mesmas repetições por série, com esforço percebido (RPE) maior: isso não conta como regressão — o desempenho ficou estável.",
  paired_reps_effort_adjusted:
    "As repetições por série foram comparadas somadas às repetições em reserva (reps + RIR, do RPE de cada série): 11 repetições a RPE 9 valem o mesmo que 12 a RPE 10.",
  more_load_fewer_reps_inconclusive:
    "Mais carga com menos repetições: isoladamente isso não é progresso nem regressão — só o 1RM estimado decidiria.",
  no_change:
    "Nenhuma diferença relevante entre a última exposição e a anterior.",
  comparability_key_changed:
    "A última exposição mudou de equipamento ou de convenção de carga: comparar com a anterior levaria a conclusão errada.",
  not_enough_comparable_exposures:
    "São necessárias duas exposições comparáveis (mesmo equipamento e mesma convenção de carga) no período; há menos que isso.",
  equipment_not_resolved:
    "A última exposição não tem equipamento identificado, então não dá para garantir que foi o mesmo aparelho.",
  e1rm_unavailable_without_rpe:
    "Sem RPE não há RIR, e sem RIR não há 1RM estimado por exercício — este critério ficou de fora da avaliação. (Só o Desempenho por grupo assume RIR 0 nas séries sem RPE.)",
  bodyweight_reps_only:
    'Convenção "peso corporal": a carga movida não está registrada na série, então a comparação olha apenas as repetições.',
  no_progress_nor_regression_in_run:
    "Nenhuma das últimas exposições comparáveis apresentou progresso nem regressão.",
  fewer_working_sets:
    "Você fez menos séries que na exposição anterior. Isso não conta como regressão: as repetições foram comparadas série a série.",
  other_fatigue_context_exposures_skipped:
    "Exposições feitas em outra condição de fadiga (o exercício em outra posição do treino, depois de outros que cansaram o mesmo músculo) ficaram de fora: a comparação é só entre exposições na mesma condição.",
  first_exposure_in_fatigue_context:
    "É a primeira exposição nesta condição de fadiga (outra posição no treino). Ela ainda não tem com o que ser comparada e não conta como progresso nem regressão.",
  equipment_context_in_calibration:
    "Aparelho em calibração: depois de trocar de aparelho ou de academia, as primeiras sessões servem de referência e ainda não contam como avanço ou regressão.",
  other_gym_exposures_skipped:
    "Sessões feitas em outra academia não entram nesta comparação.",
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
export function statusReasonsText(
  reasons: readonly string[] | null | undefined,
): StatusReasonText[] {
  if (!reasons) return [];
  return reasons.map((code) => {
    const text = STATUS_REASON_TEXTS[code];
    return { code, text: text ?? code, known: text != null };
  });
}

// ---------------------------------------------------------------------------
// Seta "Evolução" por sessão de `/dashboard/progressao`
// ---------------------------------------------------------------------------

export type SessionEvolutionKind =
  "up" | "down" | "flat" | "not_comparable" | "none";

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
  evolution:
    { status: string; reasons?: readonly string[] | null } | null | undefined,
): SessionEvolutionView {
  if (!evolution) return { kind: "none", label: "—", tooltip: "" };
  const tooltip = statusReasonsText(evolution.reasons)
    .map((reason) => reason.text)
    .join(" ");
  switch (evolution.status) {
    case "PROGRESSING":
      return { kind: "up", label: "Progrediu", tooltip };
    case "REGRESSING":
      return { kind: "down", label: "Regrediu", tooltip };
    case "STABLE":
      return {
        kind: "flat",
        label: EXERCISE_PROGRESSION_LABELS.STABLE,
        tooltip,
      };
    case "NOT_COMPARABLE":
      return {
        kind: "not_comparable",
        label: EXERCISE_PROGRESSION_LABELS.NOT_COMPARABLE,
        tooltip,
      };
    default:
      return { kind: "none", label: "—", tooltip: "" };
  }
}

// ---------------------------------------------------------------------------
// Comparabilidade de uma exposição com a chave ATUAL (§8.1)
// ---------------------------------------------------------------------------

export type ComparabilityMismatch =
  | "equipment"
  | "load_convention"
  | "equipment_unknown"
  | "fatigue_context_changed"
  | "gym_changed"
  | null;

export const COMPARABILITY_MISMATCH_LABELS: Readonly<
  Record<Exclude<ComparabilityMismatch, null>, string>
> = {
  equipment: "Outro equipamento",
  load_convention: "Outra convenção de carga",
  equipment_unknown: "Equipamento não identificado",
  fatigue_context_changed: "Outra condição de fadiga",
  gym_changed: "Outra academia",
};

export const COMPARABILITY_MISMATCH_HINTS: Readonly<
  Record<Exclude<ComparabilityMismatch, null>, string>
> = {
  equipment:
    "Esta exposição foi em outro equipamento. Mudar de aparelho muda a carga possível, então ela não entra na comparação nem no gráfico.",
  load_convention:
    "A convenção de carga desta exposição é diferente da atual (ex.: por halter vs. carga total). Os números não significam a mesma coisa.",
  equipment_unknown:
    "Esta exposição não tem equipamento identificado, então não é possível afirmar que foi o mesmo aparelho da exposição atual.",
  fatigue_context_changed:
    "Esta exposição foi feita em outra condição de fadiga (outra posição no treino, com o músculo mais ou menos cansado pelos exercícios anteriores). Ela não entra na comparação nem no gráfico do status atual.",
  gym_changed:
    "Mesmo aparelho em outra academia: máquinas iguais podem exigir esforços diferentes.",
};

/**
 * Por que esta exposição NÃO é comparável com a chave atual — ou `null` quando
 * é. Vem PRONTO do backend (`mismatch_with_current`, calculado por
 * `comparability.mismatch_with_current` sobre `keys_match`): o frontend não
 * reimplementa a regra §8.1 (M76 Lote 3c, C13).
 */
export function comparabilityMismatch(
  exposure: Pick<Exposure, "mismatch_with_current">,
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

// ---------------------------------------------------------------------------
// Par "anterior vs. atual"
// ---------------------------------------------------------------------------

export type ExposureMetricCode =
  | "best_load_kg"
  | "paired_reps"
  | "total_reps"
  | "avg_rpe"
  | "e1rm"
  | "tonnage";

/** Desfecho pronto da métrica (`metrics[].outcome`, dono `comparability.metric_outcome`). */
export type PairOutcome = "improved" | "worsened" | "flat" | "neutral";

type MetricDescriptor = {
  code: ExposureMetricCode;
  label: string;
  unit: string | null;
  digits: number;
  nature: DataNature;
  /** Chave em `metric_versions` do payload. */
  versionKey: string;
};

// Só apresentação (rótulo, unidade, natureza). Valores, variação e direção de
// melhora vêm prontos em `latest_comparison.metrics` (dono
// `exposure_service._pair_metrics` + `comparability.metric_better_when`).
const METRIC_DESCRIPTORS: readonly MetricDescriptor[] = [
  {
    code: "best_load_kg",
    label: "Melhor carga",
    unit: "kg",
    digits: 1,
    nature: "OBSERVED",
    versionKey: "exposure",
  },
  {
    // O que a regra compara: reps POR SÉRIE emparelhada nas cargas de topo
    // (N = menor número de séries de topo dos dois lados). Vem pronto do
    // backend em `latest_comparison.paired_reps`.
    code: "paired_reps",
    label: "Repetições por série (séries de topo)",
    unit: null,
    digits: 0,
    nature: "DERIVED",
    versionKey: "exposure",
  },
  {
    // Informativo: fazer menos séries não é perder desempenho, então a soma
    // de todas as séries não é pintada de verde/vermelho.
    code: "total_reps",
    label: "Repetições (total, informativo)",
    unit: null,
    digits: 0,
    nature: "DERIVED",
    versionKey: "exposure",
  },
  {
    code: "avg_rpe",
    label: "RPE médio",
    unit: null,
    digits: 1,
    nature: "DERIVED",
    versionKey: "exposure",
  },
  {
    code: "e1rm",
    label: "1RM estimado",
    unit: "kg",
    digits: 1,
    nature: "ESTIMATED",
    versionKey: "e1rm",
  },
  {
    // Volume, não desempenho: menos séries derrubam a tonelagem sem que isso
    // seja regressão — variação neutra, como o total de reps.
    code: "tonnage",
    label: "Tonelagem",
    unit: "kg",
    digits: 0,
    nature: "DERIVED",
    versionKey: "tonnage",
  },
];

export type ExposurePairRow = {
  code: ExposureMetricCode;
  label: string;
  /** `metrics[].outcome` do backend; `null` sem par ou API antiga (tela pinta neutro). */
  outcome: PairOutcome | null;
  previous: Metric;
  current: Metric;
  /** `metrics[].delta` do backend; `null` sem par comparável. */
  deltaAbs: number | null;
  /** `metrics[].delta_pct` do backend (pontos percentuais); `null` sem par. */
  deltaPct: number | null;
  /** Detalhe de cada lado (ex.: reps emparelhadas `11+10`). */
  previousDetail?: string | null;
  currentDetail?: string | null;
  /** Aviso da linha (ex.: e1RM não decisivo acima do teto de reps). */
  note?: string | null;
};

export type ExposurePair = {
  current: Exposure;
  /** `exposures[latest_comparison.previous_index]`; `null` sem par. */
  previous: Exposure | null;
  /** `latest_comparison.comparable` (e há anterior). */
  comparable: boolean;
  /** Resposta sem `latest_comparison` (API antiga): par indisponível. */
  unavailable: boolean;
  /** `latest_comparison.reasons` em frase: por que não há par. */
  reasons: StatusReasonText[];
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
    metric_version: metricVersions[descriptor.versionKey] ?? "desconhecida",
    data_quality: dataQuality,
    limitations: [],
  };
}

/** Rótulo da linha de reps emparelhadas quando o backend as ajustou pelo esforço (M78). */
export const PAIRED_REPS_EFFORT_LABEL = "Reps + RIR por série (séries de topo)";

/** `11+10` — só formata os números prontos do backend (meia rep com vírgula). */
function joinReps(values: readonly number[] | undefined): string {
  return (values ?? []).map((value) => fmtDecimal(value, 1)).join("+");
}

export const E1RM_NOT_DECISIVE_NOTE = `Não decisivo: alguma das séries do 1RM estimado passou de ${LIMIARES.e1rm_max_reliable_reps} repetições — a comparação usa as repetições por série.`;

export const PAIR_UNAVAILABLE_TEXT =
  "A comparação com a exposição anterior não veio do servidor: os números da última execução estão abaixo, sem variação.";
export const PAIR_NO_PREVIOUS_TEXT =
  "Não há exposição anterior no período: os números da última execução estão abaixo, sem variação.";

/** Tom da variação pelo desfecho pronto do motor; sem desfecho (API antiga) = neutro. */
export function outcomeTone(
  outcome: PairOutcome | null | undefined,
): "positive" | "negative" | "neutral" {
  if (outcome === "improved") return "positive";
  if (outcome === "worsened") return "negative";
  return "neutral";
}

const SIGNED_NUMBER = new Intl.NumberFormat("pt-BR", {
  signDisplay: "exceptZero",
  maximumFractionDigits: 1,
});

/** Texto da variação (`+3,2%` ou `+2`) — só formata o número pronto; `null` sem variação. */
export function pairDeltaText(
  row: Pick<ExposurePairRow, "deltaAbs" | "deltaPct">,
): string | null {
  if (row.deltaPct != null) return fmtSignedPercent(row.deltaPct);
  if (row.deltaAbs != null) return SIGNED_NUMBER.format(row.deltaAbs);
  return null;
}

/**
 * Por que o par não tem variação — frase pronta para a tela (só quando
 * `!comparable`). Vários motivos saem na ordem do backend, um após o outro
 * (cada frase já termina em ponto).
 */
export function pairNotComparableText(pair: ExposurePair): string {
  if (pair.unavailable) return PAIR_UNAVAILABLE_TEXT;
  if (pair.reasons.length > 0)
    return pair.reasons.map((reason) => reason.text).join(" ");
  return PAIR_NO_PREVIOUS_TEXT;
}

/**
 * Par "anterior vs. atual" exatamente como o motor o escolheu
 * (`comparability.latest_comparable_pair`): a anterior é
 * `exposures[latest_comparison.previous_index]`, a comparabilidade é
 * `latest_comparison.comparable` e variação/direção vêm de
 * `latest_comparison.metrics`. Nada é escolhido nem calculado aqui; sem
 * `latest_comparison` (API antiga) o par fica indisponível.
 */
export function buildExposurePair(payload: {
  exposures: readonly Exposure[];
  metric_versions: Readonly<Record<string, string>>;
  data_quality: DataQuality;
  latest_comparison?: LatestComparison | null;
}): ExposurePair | null {
  const {
    exposures,
    metric_versions: versions,
    data_quality: quality,
    latest_comparison: latest,
  } = payload;
  if (exposures.length === 0) return null;

  // A "atual" é a exposição mais recente (lista cronológica); o backend a aponta em `current_index`.
  const current =
    (latest?.current_index != null
      ? exposures[latest.current_index]
      : undefined) ?? exposures[exposures.length - 1];
  const previous =
    latest?.previous_index != null
      ? (exposures[latest.previous_index] ?? null)
      : null;
  const comparable = previous != null && latest?.comparable === true;
  const paired = comparable ? (latest?.paired_reps ?? null) : null;
  const e1rmDecisive = latest?.e1rm_decisive === true;
  const byCode = new Map(
    (latest?.metrics ?? []).map((metric) => [metric.code, metric]),
  );

  const rows = METRIC_DESCRIPTORS.map((descriptor) => {
    const metric = byCode.get(descriptor.code);
    // Sem `metrics` (API antiga) a coluna "Atual" mostra o campo da exposição, sem variação.
    const currentValue = metric
      ? metric.current
      : descriptor.code === "paired_reps"
        ? null
        : (current[descriptor.code] ?? null);
    const previousValue = comparable ? (metric?.previous ?? null) : null;
    // Com `effort_adjusted` os valores já vêm como reps + RIR do backend; o
    // detalhe mostra também as reps registradas das mesmas séries.
    const detail =
      descriptor.code === "paired_reps" && paired
        ? paired.effort_adjusted
          ? {
              previousDetail: `${joinReps(paired.previous)} (reps ${joinReps(paired.previous_reps)})`,
              currentDetail: `${joinReps(paired.current)} (reps ${joinReps(paired.current_reps)})`,
            }
          : {
              previousDetail: joinReps(paired.previous),
              currentDetail: joinReps(paired.current),
            }
        : {};
    const label =
      descriptor.code === "paired_reps" && paired?.effort_adjusted
        ? PAIRED_REPS_EFFORT_LABEL
        : descriptor.label;
    const note =
      descriptor.code === "e1rm" && comparable && !e1rmDecisive
        ? E1RM_NOT_DECISIVE_NOTE
        : null;
    return {
      ...detail,
      note,
      code: descriptor.code,
      label,
      outcome: comparable ? (metric?.outcome ?? null) : null,
      previous: metricEnvelope(previousValue, descriptor, versions, quality),
      current: metricEnvelope(currentValue, descriptor, versions, quality),
      // Sem par comparável, variação não significa nada: `null`.
      deltaAbs: comparable ? (metric?.delta ?? null) : null,
      deltaPct: comparable ? (metric?.delta_pct ?? null) : null,
    } satisfies ExposurePairRow;
  });

  return {
    current,
    previous,
    comparable,
    // Só a API antiga omite o campo; `null` (sem exposição) ou sem anterior = sem par, com os motivos.
    unavailable: latest === undefined,
    reasons: statusReasonsText(latest?.reasons),
    rows,
  };
}

// ---------------------------------------------------------------------------
// Série do gráfico de tendência
// ---------------------------------------------------------------------------

export type TrendMetricCode = "e1rm" | "best_load_kg" | "total_reps";

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
  /** Quantas exposições ficaram fora da sequência do contexto atual (`in_context_run`). */
  excluded: number;
  /** API antiga (sem `in_context_run`): a série não veio do servidor. */
  unavailable: boolean;
};

/** API antiga (sem `in_context_run`): a tela não escolhe pontos sozinha. */
export const SERIES_UNAVAILABLE_TEXT =
  "Os pontos do gráfico não vieram do servidor (versão anterior da API). Atualize a página em instantes.";

const TREND_LABELS: Readonly<
  Record<TrendMetricCode, { label: string; unit: string | null }>
> = {
  e1rm: { label: "1RM estimado", unit: "kg" },
  best_load_kg: { label: "Melhor carga", unit: "kg" },
  total_reps: { label: "Repetições (total)", unit: null },
};

/**
 * Linha de tendência sobre as exposições da sequência do contexto atual.
 *
 * A métrica vem pronta da API (`trend_metric`, motor de progressão); aqui só
 * se mapeia para o campo da exposição e para rótulo/unidade. `null` = sem
 * pontos suficientes; `undefined` = API antiga (série indisponível).
 *
 * Eixo Y ÚNICO por construção: uma série só. A regra "nunca dual-axis" está
 * documentada em `DashboardProgressaoClient.tsx`.
 */
/**
 * Exposições que o gráfico plota: as que o motor marcou `in_context_run`
 * (sequência do contexto atual; em calibração continuam plotadas). `null`
 * quando a API é antiga (campo ausente em todas): sem critério do motor, a
 * tela não escolhe pontos por conta própria.
 */
function contextRun(exposures: readonly Exposure[]): Exposure[] | null {
  if (!exposures.some((item) => item.in_context_run != null)) return null;
  return exposures.filter((item) => item.in_context_run === true);
}

export function buildTrendSeries(
  exposures: readonly Exposure[],
  trendMetric: TrendMetricCode | null | undefined,
): TrendSeries {
  const run = contextRun(exposures);
  // `trend_metric` ausente = API antiga: a tela não escolhe a métrica sozinha.
  if (run == null || trendMetric === undefined) {
    return {
      metric: null,
      label: "",
      unit: null,
      points: [],
      excluded: 0,
      unavailable: exposures.length > 0,
    };
  }
  const comparable = run;
  const excluded = exposures.length - comparable.length;

  const metric = trendMetric;
  if (metric == null) {
    return {
      metric: null,
      label: "",
      unit: null,
      points: [],
      excluded,
      unavailable: false,
    };
  }

  const { label, unit } = TREND_LABELS[metric];
  return {
    metric,
    label,
    unit,
    unavailable: false,
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
  type Raw = {
    localDate: string | null;
    order: number;
    point: UnifiedE1rmPoint;
  };
  const raw: Raw[] = [];
  sameKey.forEach((point, index) => {
    const isStrength = isHighlighted(point.training_goal);
    raw.push({
      localDate: point.local_date,
      order: index,
      point: {
        xKey: "",
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
        xKey: "",
        label: fmtDate(session.local_date),
        localDate: session.local_date,
        activityId: session.activity_id,
        value: null,
        strengthValue: null,
        isStrength: false,
        isDeload: true,
        deloadLabel: session.deload_label ?? "Deload",
        reliable: false,
      },
    });
  });
  raw.sort((a, b) => {
    const left = a.localDate ?? "";
    const right = b.localDate ?? "";
    if (left !== right) return left < right ? -1 : 1;
    return a.order - b.order;
  });
  const points = raw.map((item, index) => ({
    ...item.point,
    xKey: `${index}`,
  }));
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
  /** Exposições comparáveis SEM RPE registrado. */
  missing: number;
  /** Exposições fora da sequência do contexto atual (`in_context_run`). */
  excluded: number;
  /** API antiga (sem `in_context_run`): a série não veio do servidor. */
  unavailable: boolean;
  /** Repasse de `avg_rpe_period` (valor do backend, sem cálculo aqui); `null` = não exibir. */
  average: number | null;
};

export function buildRpeSeries(
  exposures: readonly Exposure[],
  avgRpePeriod?: number | null,
): RpeSeries {
  const average = avgRpePeriod ?? null;
  const run = contextRun(exposures);
  if (run == null) {
    return {
      hasSeries: false,
      points: [],
      missing: 0,
      excluded: 0,
      unavailable: exposures.length > 0,
      average,
    };
  }
  const comparable = run;
  const excluded = exposures.length - comparable.length;
  const withRpe = comparable.filter((item) => item.avg_rpe != null);

  const points = comparable.map((item) => ({
    label: fmtDate(item.local_date),
    localDate: item.local_date,
    activityId: item.activity_id,
    value: item.avg_rpe ?? null,
  }));

  return {
    // Apresentação: um gráfico de linha precisa de ao menos 2 pontos para desenhar.
    hasSeries: withRpe.length >= 2,
    points,
    missing: comparable.length - withRpe.length,
    excluded,
    unavailable: false,
    average,
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
export function e1rmReliabilityNote(
  record: ExerciseRecord | null | undefined,
): string | null {
  if (!record || record.value == null || record.reliable !== false) return null;
  const teto = record.max_reliable_reps;
  return teto != null ? `não confiável (>${teto} reps)` : "não confiável";
}

/**
 * Ações de `domain/progression_engine.py` em pt-BR. Dono: `DECISION_LABELS`
 * (`api/app/domain/exercise_briefing.py`), exportado para
 * `motor-constants.generated.json` (RPE 10 Lote 6/P9: era uma cópia manual;
 * paridade em `api/tests/unit/test_m76_lote1b_espelhos_fe.py`).
 */
export const PROGRESSION_DECISION_LABELS: Readonly<Record<string, string>> =
  motorConstants.progression_decision_labels;

export function progressionDecisionLabel(
  decision: string | null | undefined,
): string {
  if (!decision) return "—";
  return PROGRESSION_DECISION_LABELS[decision] ?? decision;
}

/**
 * Decisões do motor do contexto atual: o backend marca `in_current_context`
 * (`exposure_service.decision_in_context`). API antiga (campo ausente em
 * todas): mostra todas, sem filtro próprio — filtrar exigiria uma regra aqui.
 */
export function decisionsInCurrentContext(
  decisions: readonly ProgressionDecision[],
): ProgressionDecision[] {
  if (!decisions.some((item) => item.in_current_context != null))
    return [...decisions];
  return decisions.filter((item) => item.in_current_context === true);
}

// ---------------------------------------------------------------------------
// /exercicios: curvas por exercício (issue #188)
// ---------------------------------------------------------------------------

export type ExerciseProgressCurve = {
  key: string;
  name: string;
  weight: { data: string; melhorCarga: number | null; volume: number | null }[];
  rpe: { data: string; rpeMedio: number | null }[];
};

/**
 * Uma curva por item de `series` (montada pelo motor), na ordem recebida: nada
 * é agrupado, filtrado nem comparado aqui. Sem `series` (API antiga) devolve
 * `null` e a tela avisa do formato inesperado em vez de reagrupar `items`.
 */
export function buildExerciseProgressCurves(
  payload: Pick<ExerciseProgressResponse, "series"> | undefined,
): ExerciseProgressCurve[] | null {
  if (!payload) return [];
  if (!Array.isArray(payload.series)) return null;
  return payload.series.map((serie) => ({
    key: String(serie.exercise_id),
    name: serie.mapped_name || serie.exercise_name,
    weight: serie.items.map((item) => ({
      data: item.local_date,
      melhorCarga: item.best_weight_kg ?? null,
      volume: item.volume_kg ?? null,
    })),
    rpe: serie.items.map((item) => ({
      data: item.local_date,
      rpeMedio: item.avg_rpe ?? null,
    })),
  }));
}

/** Aviso de busca cortada pelo backend; os números vêm da resposta. */
export function exerciseSearchTruncationNotice(
  payload: Pick<ExerciseProgressResponse, "series" | "truncated" | "matched_exercises"> | undefined,
): string | null {
  if (!payload?.truncated) return null;
  const shown = payload.series?.length ?? 0;
  const total = payload.matched_exercises;
  return total != null
    ? `Mostrando ${shown} de ${total} exercícios encontrados. Refine a busca para ver os demais.`
    : "Há mais exercícios do que os mostrados. Refine a busca para ver os demais.";
}

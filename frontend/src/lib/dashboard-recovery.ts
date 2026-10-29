import { EM_DASH, fmtDate, fmtDecimal, fmtDateTime, fmtPercent, fmtWeekdayDateTime } from './dashboard-format';
import { READINESS_ORDER, statusHexScale, statusVisual, type ReadinessCode } from './dashboard-status';
import type {
  GlobalReadinessAdjustment,
  RecoveryGroup,
  RecoverySignal,
  RecoverySignalsResponse,
  TrainingVsRestMetric,
} from './dashboard-recovery-types';

/**
 * Lógica PURA de `/dashboard/recovery` (F1.5) — extraída da tela para ser
 * testada sem React e sem rede.
 *
 * Nenhuma regra de recuperação mora aqui. Prontidão, direção, leitura,
 * concordância, nível e incerteza chegam prontos de
 * `GET /analytics/dashboard/recovery-signals`; este módulo apenas SEPARA,
 * ORDENA e formata para exibição.
 *
 * A separação é o coração da tela: o que CONCORDA com a prontidão e o que
 * DIVERGE dela aparecem lado a lado, e o que não tem baseline aparece como
 * "sem baseline" — nunca escondido e nunca convertido em zero.
 */

/** Grupos de exibição do painel de sinais, na ordem em que a tela mostra. */
export type SignalBucketKey = 'diverging' | 'agreeing' | 'neutral' | 'unavailable';

export type SignalBuckets = Record<SignalBucketKey, RecoverySignal[]>;

export const SIGNAL_BUCKET_ORDER: readonly SignalBucketKey[] = [
  'diverging',
  'agreeing',
  'neutral',
  'unavailable',
];

export const SIGNAL_BUCKET_LABELS: Record<SignalBucketKey, string> = {
  diverging: 'Divergem da prontidão',
  agreeing: 'Concordam com a prontidão',
  neutral: 'Dentro do habitual',
  unavailable: 'Sem baseline suficiente',
};

export const SIGNAL_BUCKET_HINTS: Record<SignalBucketKey, string> = {
  diverging:
    'Apontam para o lado oposto da prontidão calculada. Ficam visíveis de propósito: divergência é informação, não ruído.',
  agreeing: 'Apontam para o mesmo lado da prontidão calculada.',
  neutral: 'Dentro da sua faixa habitual — não sustentam nenhuma conclusão.',
  unavailable:
    'Menos pontos do que o mínimo exigido na janela de referência: sem comparação possível (nada é estimado).',
};

/**
 * Separa os sinais pelos quatro baldes de exibição. `DIVERGES` vem primeiro
 * porque é o caso que a tela existe para não esconder.
 */
export function bucketSignals(signals: readonly RecoverySignal[]): SignalBuckets {
  const buckets: SignalBuckets = { diverging: [], agreeing: [], neutral: [], unavailable: [] };
  for (const signal of signals) {
    if (signal.agreement === 'DIVERGES') buckets.diverging.push(signal);
    else if (signal.agreement === 'AGREES') buckets.agreeing.push(signal);
    else if (signal.agreement === 'NO_BASELINE') buckets.unavailable.push(signal);
    else buckets.neutral.push(signal);
  }
  return buckets;
}

/** Quantos sinais apontam recuperação incompleta (com comparação possível). */
export function countUnfavorable(signals: readonly RecoverySignal[]): number {
  return signals.filter(
    (signal) => signal.reading === 'UNFAVORABLE' && signal.agreement !== 'NO_BASELINE',
  ).length;
}

/**
 * Ordena os grupos pelo estado mais restritivo primeiro (`NO_DIRECT_WORK` →
 * `REDUCED_DOSE` → `READY`) e, dentro do mesmo estado, pelo que falta mais
 * tempo para recuperar. Empate resolve por nome, para a ordem não dançar
 * entre dois carregamentos.
 */
export function sortGroupsByUrgency(groups: readonly RecoveryGroup[]): RecoveryGroup[] {
  // `READINESS_ORDER` vai do menos para o mais restritivo; aqui a ordem é a
  // inversa (o que está bloqueado aparece primeiro). Código fora do contrato
  // vai para o fim, em vez de derrubar a ordenação.
  const rank = (status: string) => READINESS_ORDER.indexOf(status as ReadinessCode);
  return [...groups].sort((left, right) => {
    const byStatus = rank(right.readiness_status) - rank(left.readiness_status);
    if (byStatus !== 0) return byStatus;
    const byHours = (right.remaining_hours.value ?? 0) - (left.remaining_hours.value ?? 0);
    if (byHours !== 0) return byHours;
    return left.muscle_group.localeCompare(right.muscle_group, 'pt-BR');
  });
}

/** Quantos grupos estão em cada estado de prontidão. */
export function countByReadiness(
  groups: readonly RecoveryGroup[],
): Record<ReadinessCode, number> {
  const counts = { READY: 0, REDUCED_DOSE: 0, NO_DIRECT_WORK: 0 } as Record<
    ReadinessCode,
    number
  >;
  for (const group of groups) {
    if (group.readiness_status in counts) {
      counts[group.readiness_status as ReadinessCode] += 1;
    }
  }
  return counts;
}

/**
 * Texto de "quando reavaliar". `null` (nada pendente) vira uma frase própria
 * em vez de "0 h", que diria algo diferente.
 */
export function reassessLabel(hours: number | null): string {
  if (hours == null || hours <= 0) return 'Nada pendente para reavaliar agora.';
  if (hours < 1) return 'Reavaliar em menos de 1 h.';
  if (hours < 24) return `Reavaliar em cerca de ${Math.round(hours)} h.`;
  const days = hours / 24;
  return `Reavaliar em cerca de ${days.toFixed(days < 2 ? 1 : 0)} dia(s).`;
}

/**
 * Todas as limitações a exibir de uma vez: as do envelope mais as de cada
 * sinal, sem repetir. É isso que impede a tela de "parecer completa" quando
 * metade dos sinais não pôde ser comparada.
 */
export function collectLimitations(payload: RecoverySignalsResponse): string[] {
  const seen = new Set<string>();
  const all: string[] = [];
  for (const limitation of [
    ...payload.limitations,
    ...payload.signals.flatMap((signal) => signal.limitations),
    ...payload.training_vs_rest.limitations,
    ...payload.training_vs_rest.metrics.flatMap((metric) => metric.limitations),
  ]) {
    if (!seen.has(limitation)) {
      seen.add(limitation);
      all.push(limitation);
    }
  }
  return all;
}

// ---------------------------------------------------------------------------
// F2.5 — M33: o detalhamento que só existia em `/recuperacao-muscular`
//
// Tudo aqui é FORMATAÇÃO do que o backend já decidiu. Nenhuma destas funções
// escolhe número de série, recalcula dose ou reclassifica prontidão: a regra
// é do modelo `fullbody-no-reps-v1`, e a tela só a escreve em português.
// ---------------------------------------------------------------------------

/**
 * Coluna "Bom para": quando o grupo volta a "Pronto". O instante e o rótulo vêm
 * prontos do backend (`recovery_projection.ready_forecast`); aqui só se formata
 * o instante no fuso do usuário. Sem instante e sem rótulo = sem previsão (`—`).
 */
export function readyAtLabel(group: RecoveryGroup, timeZone: string): string {
  if (group.ready_at) return fmtWeekdayDateTime(group.ready_at, timeZone);
  return group.ready_label ?? EM_DASH;
}

/**
 * Texto da recomendação de séries do grupo. `direct_sets_max = 0` NÃO é
 * "zero séries": é o estado em que o modelo bloqueia trabalho direto e ainda
 * permite o indireto — dizer "0 séries" esconderia essa diferença.
 */
export function recommendationLabel(group: RecoveryGroup): string {
  const recommendation = group.recommendation;
  if (!recommendation) return EM_DASH;

  const extras = [`RPE máx ${recommendation.maximum_rpe ?? EM_DASH}`];
  if (recommendation.allow_failure_on_last_set === false) extras.push('sem falha');
  if (recommendation.allow_progression === false) extras.push('sem progressão');

  // M76 B2: a dose reduzida é uma REGRA sobre as séries planejadas
  // (`ceil(S × 0,75)`, mín. 1), não uma faixa fixa — o texto vem pronto do modelo.
  if (recommendation.sets_rule) {
    return `Séries: ${recommendation.sets_rule} · ${extras.join(' · ')}`;
  }
  if (recommendation.direct_sets_max == null) return EM_DASH;

  if (recommendation.direct_sets_max <= 0) {
    const indirect = recommendation.allow_indirect_work === false ? 'nem indireto' : 'indireto permitido';
    return `Sem trabalho direto (${indirect}, RPE máx ${recommendation.maximum_rpe ?? EM_DASH})`;
  }

  const min = recommendation.direct_sets_min ?? recommendation.direct_sets_max;
  const sets = min === recommendation.direct_sets_max ? `${min}` : `${min}-${recommendation.direct_sets_max}`;
  return `${sets} série(s) diretas · ${extras.join(' · ')}`;
}

/**
 * Linhas de "de onde saiu este número": a explicação que o modelo já escreveu
 * mais os fatores do cálculo. Campo ausente NÃO vira uma linha com zero — ele
 * simplesmente não gera linha.
 */
export function factorLines(group: RecoveryGroup): string[] {
  const lines = [...group.explanation];
  const factors = group.factors;
  if (!factors) return lines;

  if (factors.session_dose != null || factors.dose_last_72h != null) {
    lines.push(
      `Dose da última sessão: ${fmtDecimal(factors.session_dose, 2)} · dose nas últimas 72 h: ${fmtDecimal(factors.dose_last_72h, 2)}`,
    );
  }
  if (factors.avg_rpe_used != null) {
    lines.push(`RPE médio usado no cálculo: ${fmtDecimal(factors.avg_rpe_used, 1)}`);
  }
  // F2.4 (2a passada): `avg_rpe_window` e o MESMO numero da coluna "RPE medio"
  // de `/recuperacao-muscular` (`MuscleRecoveryItem.avg_rpe`, repassado por
  // `recovery_signals_service`). Ja vinha no payload desde o M33, mas nenhuma
  // tela o exibia - e era a unica coluna da tela legada sem equivalente aqui.
  // Fica ao lado do `avg_rpe_used` de proposito: sao dois RPE diferentes (o da
  // JANELA e o que entrou no calculo da dose), e mostrar so um deles com o
  // rotulo generico "RPE medio" confundiria os dois.
  if (factors.avg_rpe_window != null) {
    lines.push(`RPE médio da janela: ${fmtDecimal(factors.avg_rpe_window, 1)}`);
  }
  if (factors.density_penalty != null && factors.density_penalty > 0) {
    lines.push(`Penalidade por densidade de 72 h: ${fmtDecimal(factors.density_penalty, 1)} ponto(s).`);
  }
  if (factors.signal_penalty != null && factors.signal_penalty > 0) {
    lines.push(`Penalidade por sinais negativos: ${fmtDecimal(factors.signal_penalty, 1)} ponto(s).`);
  }
  if (factors.performance_ratio != null) {
    lines.push(`Razão de desempenho da última sessão: ${fmtDecimal(factors.performance_ratio, 2)}.`);
  }

  const mostRecent = factors.recent_sets[0];
  if (mostRecent) {
    lines.push(
      `Série mais recente (${fmtDateTime(mostRecent.trained_at)}): reps=${mostRecent.reps ?? EM_DASH} RPE=${fmtDecimal(mostRecent.rpe, 1)} (${mostRecent.set_type}) — esforço ${fmtDecimal(mostRecent.effort_points, 2)} × fator reps ${fmtDecimal(mostRecent.repetition_factor, 2)} × fator tipo ${fmtDecimal(mostRecent.set_type_factor, 2)}`,
    );
  }
  lines.push(...factors.notes);
  return lines;
}

/** Entrada do mapa corporal. Espelha `MuscleMapEntry` sem importar o componente. */
export type RecoveryMapEntry = {
  muscle_group: string;
  /** 1 = pronto, 2 = dose reduzida, 3 = evitar trabalho direto (posição em `READINESS_ORDER` + 1). */
  intensity: number;
  tooltip: string;
};

/**
 * Cores do mapa corporal do recovery, alinhadas a `RecoveryMapEntry.intensity`
 * (`colors[intensity - 1]`): a ordem canônica de prontidão, sem inverter. A
 * legenda (`statusLegend('readiness')`) sai do mesmo registro de cores.
 */
export const RECOVERY_BODY_MAP_COLORS: string[] = statusHexScale('readiness');

/**
 * Grupos no formato do mapa corporal. A intensidade cresce com a gravidade
 * (pronto = 1 ... evitar trabalho direto = 3) porque o mapa pinta cada região
 * com a MAIOR intensidade entre os grupos que caem nela: quando dois grupos da
 * mesma região divergem (ex.: "Peitoral superior" pronto e "Peitoral
 * médio/inferior" bloqueado, ambos em `chest`), o PIOR estado vence — decisão
 * do usuário em 2026-10-03. O toque na região lista os dois grupos.
 * Status desconhecido não some do mapa: entra no degrau mais brando.
 */
export function bodyMapEntries(groups: readonly RecoveryGroup[]): RecoveryMapEntry[] {
  return groups.map((group) => {
    const rank = READINESS_ORDER.indexOf(group.readiness_status as ReadinessCode);
    const intensity = rank < 0 ? 1 : rank + 1;
    const label = statusVisual('readiness', group.readiness_status).label;
    return {
      muscle_group: group.muscle_group,
      intensity,
      tooltip: `${group.muscle_group}: ${label} (${fmtPercent(group.recovery_pct.value, 0)})`,
    };
  });
}

/**
 * M52: o último wellness do Intervals.icu passou da idade máxima e o ajuste
 * global foi ignorado (fator neutro). A tela troca o tom do aviso.
 */
export function isAdjustmentStale(adjustment: GlobalReadinessAdjustment | null): boolean {
  return Boolean(adjustment?.stale);
}

/**
 * Frase do ajuste global de fadiga. `null` tem frase própria: "não há dado do
 * Intervals.icu" é diferente de "o ajuste deu neutro".
 */
export function adjustmentLabel(adjustment: GlobalReadinessAdjustment | null): string {
  if (!adjustment) {
    return 'Sem dado do Intervals.icu: nenhum ajuste global de fadiga foi aplicado ao tempo de recuperação.';
  }
  if (isAdjustmentStale(adjustment)) {
    const limite = adjustment.max_age_days ?? 3;
    return `Dado do Intervals.icu desatualizado (último em ${fmtDate(adjustment.wellness_date)}): com mais de ${limite} dias ele é ignorado e nenhum ajuste global de fadiga foi aplicado ao tempo de recuperação.`;
  }
  const pct = Math.round((adjustment.factor - 1) * 100);
  const tsb = `TSB ${fmtDecimal(adjustment.form_tsb, 1)}`;
  if (pct === 0) return `Ajuste por fadiga geral: sem alteração no tempo de recuperação · ${tsb}`;
  const direction = pct > 0 ? 'a mais' : 'a menos';
  return `Ajuste por fadiga geral: ${Math.abs(pct)}% ${direction} no tempo de recuperação · ${tsb}`;
}

// ---------------------------------------------------------------------------
// F2.5 — M36: dia COM treino × dia SEM treino
// ---------------------------------------------------------------------------

/**
 * Texto da diferença entre os dois lados. Lado ausente vira frase, nunca
 * zero: "não houve noite registrada em dia de treino" é diferente de "dormiu
 * 0 h".
 */
export function trainingVsRestLabel(metric: TrainingVsRestMetric): string {
  const delta = metric.delta.value;
  if (delta == null) return 'Sem comparação possível nesta janela.';
  if (metric.direction === 'IN_LINE') return 'Praticamente igual nos dois casos.';
  const worse = metric.reading === 'UNFAVORABLE';
  const side = delta > 0 ? 'maior' : 'menor';
  return `${fmtDecimal(Math.abs(delta), 2)} ${metric.unit} ${side} em dia de treino — ${
    worse ? 'desfavorável' : 'favorável'
  } para a recuperação.`;
}

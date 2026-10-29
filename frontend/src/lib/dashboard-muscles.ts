import type { MuscleExposure, MuscleItem, MuscleSession } from './dashboard-v3-types';

/**
 * Lógica PURA de `/dashboard/musculos` (F1.2) — extraída da tela para poder
 * ser testada sem React e sem rede.
 *
 * O que NÃO mora aqui, por decisão do plano (§6, §9): nenhuma regra de
 * negócio. Status, séries efetivas, metas, qualidade e limitações chegam
 * prontos do `response_model`; este módulo só posiciona pixels e ordena
 * listas.
 */

// ---------------------------------------------------------------------------
// Barra mínimo → alvo → máximo
// ---------------------------------------------------------------------------

/**
 * Onde cada marca cai numa barra de 0–100%.
 *
 * A escala vai até `max * ESCALA_ALEM_DO_MAXIMO` para que um volume ACIMA do
 * máximo continue visível dentro da barra em vez de encostar na borda e
 * parecer "só um pouco acima" — `ABOVE_MAX` é justamente o estado que precisa
 * ser lido de longe.
 */
export const ESCALA_ALEM_DO_MAXIMO = 1.25;

export type MinTargetMaxScale = {
  /** Valor máximo representado pela barra. */
  scaleMax: number;
  minPct: number;
  targetPct: number;
  maxPct: number;
  /** Posição do realizado; `null` quando não há valor a posicionar. */
  actualPct: number | null;
  /** Posição do projetado somado ao realizado (marca separada, nunca somada
   * no número exibido); `null` quando não há projeção. */
  projectedPct: number | null;
  /** O realizado passou do máximo (a barra precisa dizer isso). */
  overflow: boolean;
};

function pct(value: number, scaleMax: number): number {
  if (!Number.isFinite(value) || scaleMax <= 0) return 0;
  return Math.max(0, Math.min(100, (value / scaleMax) * 100));
}

export function minTargetMaxScale(input: {
  min: number | null;
  target: number | null;
  max: number | null;
  actual: number | null;
  projected?: number | null;
}): MinTargetMaxScale {
  const min = input.min ?? 0;
  const target = input.target ?? 0;
  const max = input.max ?? 0;
  const actual = input.actual;
  const projected = input.projected ?? null;

  const scaleMax = Math.max(
    max * ESCALA_ALEM_DO_MAXIMO,
    actual ?? 0,
    (actual ?? 0) + (projected ?? 0),
    1,
  );

  return {
    scaleMax,
    minPct: pct(min, scaleMax),
    targetPct: pct(target, scaleMax),
    maxPct: pct(max, scaleMax),
    actualPct: actual == null ? null : pct(actual, scaleMax),
    projectedPct:
      projected == null || projected <= 0 ? null : pct((actual ?? 0) + projected, scaleMax),
    overflow: actual != null && max > 0 && actual > max,
  };
}

// ---------------------------------------------------------------------------
// Barra planejado x realizado (meta do próprio Modelo por equipamento)
// ---------------------------------------------------------------------------

export type PlannedVsRealizedScale = {
  /** Valor máximo representado pela barra. */
  scaleMax: number;
  /** Posição da marca do planejado; `null` quando o grupo está fora do Modelo. */
  plannedPct: number | null;
  /** Posição do realizado; `null` quando não há valor a posicionar. */
  actualPct: number | null;
  /** O Modelo não trabalha este grupo (planejado zero ou ausente). */
  outsideModel: boolean;
  /** O realizado passou do planejado. */
  overflow: boolean;
};

/**
 * Escala da barra `planejado x realizado`: só DUAS marcas, porque um plano
 * fixo não tem mínimo nem máximo — `min`/`max` chegam `null` e NUNCA viram
 * zero aqui. A folga de `ESCALA_ALEM_DO_MAXIMO` sobre o planejado é a mesma
 * da barra de três marcas: um realizado acima do plano precisa continuar
 * visível dentro da barra.
 */
export function plannedVsRealizedScale(input: {
  planned: number | null;
  actual: number | null;
}): PlannedVsRealizedScale {
  const planned = input.planned ?? 0;
  const actual = input.actual;
  const outsideModel = !(planned > 0);

  const scaleMax = Math.max(planned * ESCALA_ALEM_DO_MAXIMO, actual ?? 0, 1);

  return {
    scaleMax,
    plannedPct: outsideModel ? null : pct(planned, scaleMax),
    actualPct: actual == null ? null : pct(actual, scaleMax),
    outsideModel,
    overflow: actual != null && actual > planned,
  };
}

/** A meta do item veio do próprio Modelo por equipamento? */
export function isTemplatePlanItem(item: Pick<MuscleItem, 'targets'>): boolean {
  return item.targets.source === 'template_plan';
}

// ---------------------------------------------------------------------------
// Ordenação e recortes da tabela
// ---------------------------------------------------------------------------

export const MUSCLE_SORTS = ['volume', 'status', 'nome', 'ultimo_estimulo'] as const;

export type MuscleSort = (typeof MUSCLE_SORTS)[number];

export const MUSCLE_SORT_LABELS: Readonly<Record<MuscleSort, string>> = {
  volume: 'Mais volume primeiro',
  status: 'Mais atrasados primeiro',
  nome: 'Nome (A–Z)',
  ultimo_estimulo: 'Treinado há mais tempo',
};

/**
 * Prioridade de atenção dos 5 estados. Não é uma reclassificação: é só a
 * ordem em que a pessoa quer olhar. Abaixo do mínimo e acima do máximo são os
 * dois extremos que exigem decisão; o meio da faixa não exige nada.
 */
const STATUS_ATTENTION_ORDER: Readonly<Record<string, number>> = {
  BELOW_MIN: 0,
  BELOW_TARGET: 0,
  ABOVE_MAX: 1,
  ABOVE_TARGET: 1,
  BETWEEN_MIN_TARGET: 2,
  BETWEEN_TARGET_MAX: 3,
  ON_TARGET: 4,
};

function attention(status: string): number {
  return STATUS_ATTENTION_ORDER[status] ?? 99;
}

function stimulus(item: MuscleItem): number {
  return item.realized.effective_sets_stimulus.value ?? 0;
}

export function sortMuscleItems(items: readonly MuscleItem[], sort: MuscleSort): MuscleItem[] {
  const copy = [...items];
  switch (sort) {
    case 'status':
      return copy.sort(
        (a, b) => attention(a.status) - attention(b.status) || stimulus(b) - stimulus(a),
      );
    case 'nome':
      return copy.sort((a, b) => a.muscle_group.localeCompare(b.muscle_group, 'pt-BR'));
    case 'ultimo_estimulo':
      // Nunca treinado vem PRIMEIRO: é a maior lacuna possível, não a menor.
      return copy.sort((a, b) => {
        const da = a.last_stimulus_local_date ?? '';
        const db = b.last_stimulus_local_date ?? '';
        return da.localeCompare(db);
      });
    case 'volume':
    default:
      return copy.sort((a, b) => stimulus(b) - stimulus(a));
  }
}

/** Contagem por estado, para o resumo no topo da tela. */
export function countByStatus(items: readonly MuscleItem[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const item of items) counts[item.status] = (counts[item.status] ?? 0) + 1;
  return counts;
}

/**
 * Grupos com meta genérica. A tela mostra isso como estado PARCIAL: contra o
 * fallback, "abaixo" e "acima" não falam do objetivo de ninguém.
 */
export function groupsWithGenericTarget(items: readonly MuscleItem[]): string[] {
  return items
    .filter((item) => item.targets.source === 'fallback_generic')
    .map((item) => item.muscle_group);
}

/**
 * Grupos que o Modelo por equipamento NÃO trabalha (meta do próprio Modelo,
 * alvo zero). O realizado neles é trabalho extra, não déficit — a tela diz
 * isso em vez de deixar "acima do planejado" parecer excesso.
 */
export function groupsOutsideTemplatePlan(items: readonly MuscleItem[]): string[] {
  return items
    .filter((item) => isTemplatePlanItem(item) && !((item.targets.target.value ?? 0) > 0))
    .map((item) => item.muscle_group);
}

// ---------------------------------------------------------------------------
// Drill-down
// ---------------------------------------------------------------------------

export type ExerciseContribution = {
  exerciseId: number | null;
  name: string;
  sets: number;
  stimulus: number;
  share: number | null;
  roles: string[];
  sessions: number;
};

/**
 * Sessões → contribuição por EXERCÍCIO, o primeiro nível do drill-down
 * ("grupo → exercícios → sessões → séries").
 *
 * Feito aqui, e não no backend, porque o backend já devolve as sessões com as
 * séries: agregar de novo lá custaria uma consulta a mais para uma soma que o
 * payload já permite. `share` usa o total das sessões RETORNADAS — quando o
 * drill-down trunca, ele descreve a amostra exibida, não o período inteiro.
 */
export function contributionsByExercise(
  sessions: readonly MuscleSession[],
): ExerciseContribution[] {
  const buckets = new Map<string, ExerciseContribution & { _sessions: Set<number> }>();
  let total = 0;

  for (const session of sessions) {
    for (const exposure of session.exposures) {
      const key = exposure.exercise_id != null ? `id:${exposure.exercise_id}` : `nome:${exposure.name}`;
      let bucket = buckets.get(key);
      if (!bucket) {
        bucket = {
          exerciseId: exposure.exercise_id,
          name: exposure.name,
          sets: 0,
          stimulus: 0,
          share: null,
          roles: [],
          sessions: 0,
          _sessions: new Set<number>(),
        };
        buckets.set(key, bucket);
      }
      bucket.sets += exposure.sets.length;
      bucket.stimulus += exposure.effective_sets_stimulus;
      if (!bucket.roles.includes(exposure.role)) bucket.roles.push(exposure.role);
      bucket._sessions.add(session.activity_id);
      total += exposure.effective_sets_stimulus;
    }
  }

  return [...buckets.values()]
    .map(({ _sessions, ...bucket }) => ({
      ...bucket,
      sessions: _sessions.size,
      stimulus: Number(bucket.stimulus.toFixed(2)),
      share: total > 0 ? Number((bucket.stimulus / total).toFixed(3)) : null,
      roles: [...bucket.roles].sort(),
    }))
    .sort((a, b) => b.stimulus - a.stimulus);
}

/** Sessões em que um exercício específico apareceu (2º nível do drill-down). */
export function sessionsForExercise(
  sessions: readonly MuscleSession[],
  exerciseId: number | null,
  name: string,
): Array<MuscleSession & { exposures: MuscleExposure[] }> {
  const matches = (exposure: MuscleExposure) =>
    exerciseId != null ? exposure.exercise_id === exerciseId : exposure.name === name;

  return sessions
    .map((session) => ({ ...session, exposures: session.exposures.filter(matches) }))
    .filter((session) => session.exposures.length > 0);
}

/** Texto curto de "há quanto tempo", a partir das horas do backend. */
export function humanizeHours(hours: number | null | undefined): string | null {
  if (hours == null || !Number.isFinite(hours)) return null;
  if (hours < 1) return 'menos de 1 h';
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} dias`;
}


// ---------------------------------------------------------------------------
// Comparativos POR SESSAO e mapa de calor (F2.5 / M34)
// ---------------------------------------------------------------------------

/**
 * **A unidade destes blocos é SESSÃO, não janela.** T-0 é a última sessão em
 * que o grupo apareceu; T-1, a anterior. Não é "esta semana contra a
 * passada": duas sessões seguidas podem cair no mesmo dia, e três semanas
 * parado não criam coluna vazia.
 *
 * A tela legada `/volume-grupamentos` empilhava as duas leituras (uma tabela
 * por sessão e outra por semana) sem dizer qual era qual. Aqui o rótulo vem
 * daqui, uma vez só, e nenhuma tela escreve "T-0" solto.
 */
export const SESSION_UNIT_TITLE = 'Últimas sessões (T-0 a T-3)';

export const SESSION_UNIT_SUBTITLE =
  'Unidade: SESSÃO. T-0 é a última sessão em que o grupo apareceu, T-1 a anterior. ' +
  'Não é semana nem mês — o restante desta tela compara por período.';

/** Rótulo curto da coluna: `T-0`, `T-1`… */
export function sessionOffsetLabel(offset: number): string {
  return `T-${offset}`;
}

export type SessionDelta = {
  current: number | null;
  previous: number | null;
  /** `null` quando falta T-0 ou T-1 — nunca 0 fingindo "sem mudança". */
  delta: number | null;
  direction: 'up' | 'down' | 'stable' | 'none';
};

/**
 * Variação entre T-0 e T-1 de uma série de pontos já ordenada por `offset`.
 *
 * `epsilon` existe porque séries efetivas são fracionárias (pesos de
 * contribuição): 3.0000001 contra 3.0 é a mesma coisa, e uma seta de
 * "aumentou" aí seria ruído.
 */
export function sessionDelta(
  values: ReadonlyArray<number | null>,
  epsilon = 0.01,
): SessionDelta {
  const current = values[0] ?? null;
  const previous = values[1] ?? null;
  if (current == null || previous == null) {
    return { current, previous, delta: null, direction: 'none' };
  }
  const delta = current - previous;
  if (Math.abs(delta) <= epsilon) return { current, previous, delta, direction: 'stable' };
  return { current, previous, delta, direction: delta > 0 ? 'up' : 'down' };
}

/** Nome exibido de um equipamento — `null` é "não registrado", nunca um aparelho. */
export function equipmentLabel(name: string | null | undefined): string {
  return name && name.trim() ? name : 'Equipamento não registrado';
}

/**
 * Pontos de um grupo indexados por `offset`, com buraco explícito.
 *
 * Um equipamento que não apareceu em T-2 fica com `null` nessa coluna em vez
 * de deslizar o T-3 para lá — deslizar sugeriria uma comparação que não
 * existe (foi exatamente o que a tabela por equipamento legada fazia ao
 * reindexar as semanas de cada equipamento separadamente).
 */
export function pointsByOffset<T extends { offset: number }>(
  points: readonly T[],
  columns: number,
): Array<T | null> {
  const slots: Array<T | null> = Array.from({ length: columns }, () => null);
  for (const point of points) {
    if (point.offset >= 0 && point.offset < columns) slots[point.offset] = point;
  }
  return slots;
}

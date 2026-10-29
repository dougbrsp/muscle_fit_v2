import { buildActivationPreviewPayload } from '@/lib/activation-preview-payload';
import { statusVisual, type StatusVisual } from '@/lib/dashboard-status';
import type {
  MethodologyAxisEvaluation,
  MethodologyDataQuality,
  MethodologyGroupEvaluation,
  MethodologyIntentDeclaration,
  MethodologyIntentKind,
  MethodologyNotEvaluableReason,
  MethodologySessionDirectWork,
  MethodologyVolumeEvaluation,
  MethodologyVolumePreviewPayload,
  MethodologyVolumeStatus,
} from '@/lib/methodology-volume-types';
import { countingModeOption, setsUnitOf, UNCLASSIFIED_VOLUME_CLASS_LABEL } from '@/lib/volume-counting-mode';
import type { VolumeMode, WorkoutPlanTemplateSlotPayload } from '@/lib/workout-plan-templates-types';

/**
 * Lógica PURA do painel de metodologia de volume (Fase 4, PR F): corpo do
 * preview, rótulos em pt-BR e o "view-model" de cada linha da tabela. Fica
 * fora do componente para ser testável sem DOM (vitest em `node`) e para o
 * painel do builder e a tela de diagnóstico (PR G) lerem os MESMOS textos.
 *
 * Regra de unidade: todo número sai daqui acompanhado do que ele é
 * (`por sessão`, `/sem`). Cor NUNCA é o único sinal: o status vem com rótulo,
 * ícone e `ariaLabel` prontos de `dashboard-status.ts`.
 */

// ---------------------------------------------------------------------------
// Corpo da requisição
// ---------------------------------------------------------------------------

/**
 * Mesmo corpo mínimo do preview de ativação (M10) + intenção declarada. Até o
 * PR H a intenção viaja só como parâmetro — nada é persistido. Lote 3: o modo
 * de contagem do FORMULÁRIO vai no corpo (e na queryKey) — trocar o seletor
 * reavalia; sem ele, o backend usa o do Modelo / o default.
 */
export function buildMethodologyVolumePayload(
  slots: WorkoutPlanTemplateSlotPayload[],
  volumeMode: VolumeMode | undefined,
  intent: MethodologyIntentKind,
  targetGroupIds: number[],
  countingMode?: string | null,
): MethodologyVolumePreviewPayload {
  return {
    ...buildActivationPreviewPayload(slots, volumeMode),
    intent,
    // Só a especialização tem alvo; nas demais o motor ignora e o corpo fica
    // estável (mesma queryKey) mesmo que a seleção de grupos permaneça.
    target_group_ids: intent === 'specialization' ? [...targetGroupIds].sort((a, b) => a - b) : [],
    ...(countingMode ? { volume_counting_mode: countingMode } : {}),
  };
}

export function serializeMethodologyVolumePayload(
  slots: WorkoutPlanTemplateSlotPayload[],
  volumeMode: VolumeMode | undefined,
  intent: MethodologyIntentKind,
  targetGroupIds: number[],
  countingMode?: string | null,
): string {
  return JSON.stringify(buildMethodologyVolumePayload(slots, volumeMode, intent, targetGroupIds, countingMode));
}

// ---------------------------------------------------------------------------
// Vocabulário
// ---------------------------------------------------------------------------

export const METHODOLOGY_INTENT_OPTIONS: ReadonlyArray<{
  value: MethodologyIntentKind;
  label: string;
  description: string;
}> = [
  { value: 'standard', label: 'Padrão', description: 'Faixas da metodologia sem ajuste.' },
  {
    value: 'specialization',
    label: 'Especialização',
    description: 'Acima da faixa nos grupos-alvo é esperado (vira informativo); abaixo continua aviso.',
  },
  { value: 'maintenance', label: 'Manutenção', description: 'Abaixo da faixa é esperado (vira informativo).' },
  { value: 'cutting', label: 'Cutting', description: 'Abaixo da faixa é esperado (vira informativo).' },
];

/** Onde o admin corrige tudo que é do macro-grupo (Fase 6). */
const VOLUME_CLASSES_TAB = 'Domínio de exercícios › Macro-grupos de volume';

/** Motivo de `NOT_EVALUABLE` traduzido; `fixHint` diz ONDE corrigir. */
export const NOT_EVALUABLE_REASON_LABEL: Record<MethodologyNotEvaluableReason, { label: string; fixHint?: string }> =
  {
    UNCLASSIFIED_GROUP: {
      label: 'Grupo sem macro-grupo',
      fixHint: `Associe o grupo a um macro-grupo em ${VOLUME_CLASSES_TAB}.`,
    },
    MODE_A_NO_EXERCISES: {
      label: 'Modo por grupo não define exercícios',
      fixHint: 'Quem escolhe os exercícios é a geração; só os eixos de séries são avaliados neste modo.',
    },
    MODE_A_PERCENT_ONLY: {
      label: 'Só percentuais, sem séries',
      fixHint: 'Informe as séries dos itens (ou use o modo por equipamento) para a metodologia avaliar.',
    },
    NO_TRAINING_SESSIONS: {
      label: 'Nenhuma sessão de treino no ciclo',
    },
    NO_RANGE_FOR_FREQUENCY: {
      label: 'Macro-grupo sem faixa para esta frequência',
      fixHint: `Preencha a linha desta frequência (1x, 2x ou 3x+) do macro-grupo em ${VOLUME_CLASSES_TAB}.`,
    },
    NO_EXERCISES_RANGE: {
      label: 'Macro-grupo sem faixa de exercícios',
      fixHint: `A faixa de exercícios por treino é opcional; para avaliar este eixo, preencha-a em ${VOLUME_CLASSES_TAB}.`,
    },
    NO_WEEKLY_TARGET: {
      label: 'Macro-grupo sem meta semanal',
      fixHint: `Defina a meta semanal do macro-grupo em ${VOLUME_CLASSES_TAB}.`,
    },
  };

export function notEvaluableReasonLabel(reason: MethodologyNotEvaluableReason | string | null): string {
  if (!reason) return 'Motivo não informado';
  const entry = NOT_EVALUABLE_REASON_LABEL[reason as MethodologyNotEvaluableReason];
  return entry ? entry.label : reason;
}

export function notEvaluableReasonHint(reason: MethodologyNotEvaluableReason | string | null): string | undefined {
  if (!reason) return undefined;
  return NOT_EVALUABLE_REASON_LABEL[reason as MethodologyNotEvaluableReason]?.fixHint;
}

// ---------------------------------------------------------------------------
// Formatação
// ---------------------------------------------------------------------------

export function formatPerSession(value: number | null | undefined): string {
  if (value == null) return '—';
  return value.toLocaleString('pt-BR', { minimumFractionDigits: 0, maximumFractionDigits: 1 });
}

/** Séries com equivalente de técnica (Fase 5, PR 5): até 2 casas ("4,8", "11,65", "12"). */
export function formatSets(value: number | null | undefined): string {
  if (value == null) return '—';
  return (Math.round(value * 100) / 100).toLocaleString('pt-BR', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

/**
 * "9 séries + 0,8 equiv." — séries com a parcela da técnica escrita à parte
 * (o equivalente PLANEJADO é estimativa do catálogo; o realizado conta as reps
 * reais). Sem técnica: só o número.
 */
export function formatSetsWithTechnique(total: number, techniqueEquivalent: number | null | undefined): string {
  const technique = techniqueEquivalent ?? 0;
  if (technique <= 0) return formatSets(total);
  const base = Math.max(0, total - technique);
  return `${formatSets(total)} (${formatSets(base)} ${plural(base, 'série', 'séries')} + ${formatSets(technique)} equiv.)`;
}

export const TECHNIQUE_EQUIVALENT_HINT =
  'Equivalente de técnica avançada: estimativa do catálogo (aplicações × equivalente por aplicação, com teto por série). O realizado conta as repetições reais — a diferença é informativa.';

/** "9–16", "12,5–20" ou "15" (mín = máx): faixas do cadastro podem ser fracionárias. */
export function formatRange(min: number | null | undefined, max: number | null | undefined): string {
  if (min == null || max == null) return '—';
  return min === max ? formatPerSession(min) : `${formatPerSession(min)}–${formatPerSession(max)}`;
}

/** "2x/sem" ou "1,4x/sem" (normalizada). */
export function formatWeeklyFrequency(value: number): string {
  return `${value.toLocaleString('pt-BR', { minimumFractionDigits: 0, maximumFractionDigits: 1 })}x/sem`;
}

// ---------------------------------------------------------------------------
// View-model por eixo e por linha
// ---------------------------------------------------------------------------

export type MethodologyAxisKind = 'exercises' | 'sets' | 'weekly';

export type MethodologyAxisView = {
  axis: MethodologyAxisKind;
  /** Unidade escrita, ex.: "exercícios por sessão". */
  unit: string;
  /** Valor formatado ("3", "2,5" ou "—"). */
  valueText: string;
  /** Faixa formatada ("3–4" ou "—"). */
  rangeText: string;
  /** Como a faixa é chamada na tela: "faixa" (por sessão) ou "meta" (semanal). */
  rangeWord: string;
  /** `true` quando a faixa é número NOSSO, não do autor. */
  isDerived: boolean;
  /** Origem crua da faixa (`methodology`/`derived`/`admin`) ou null. */
  sourceCode: string | null;
  /** Origem da faixa já resolvida em `dashboard-status.ts` (null sem faixa). */
  sourceVisual: StatusVisual | null;
  /** Evidência da faixa (EXG-NNN ou regra de derivação) para tooltip. */
  evidence: string | null;
  status: StatusVisual;
  statusCode: MethodologyAxisEvaluation['status'];
  /** `true` quando o desvio é o esperado pela intenção (severity informational). */
  isInformational: boolean;
  /** Motivo traduzido quando NOT_EVALUABLE; null nos demais. */
  reasonLabel: string | null;
  reasonHint: string | null;
  /** Texto acessível completo: eixo + status + motivo/valor. */
  ariaLabel: string;
};

/** Unidade do eixo no modo de contagem: "séries diretas" ou "séries ponderadas" (vem do motor). */
export function axisUnit(axis: MethodologyAxisKind, countingMode?: string | null): string {
  if (axis === 'exercises') return 'exercícios por sessão';
  const sets = setsUnitOf(countingMode).plural;
  return axis === 'weekly' ? `${sets} por semana` : `${sets} por sessão`;
}

/** Eixo ausente (payload anterior à Fase 6): sem status, sem inventar número. */
const MISSING_AXIS: MethodologyAxisEvaluation = {
  status: 'NOT_EVALUABLE',
  reason: null,
  severity: null,
  value_per_session: null,
  value_weekly: null,
  range_min: null,
  range_max: null,
  range_source: null,
  range_evidence: null,
};

export function buildAxisView(
  axis: MethodologyAxisKind,
  evaluation: MethodologyAxisEvaluation,
  countingMode?: string | null,
): MethodologyAxisView {
  const status = statusVisual('methodologyVolume', evaluation.status);
  const isNotEvaluable = evaluation.status === 'NOT_EVALUABLE';
  const reasonLabel = isNotEvaluable ? notEvaluableReasonLabel(evaluation.reason) : null;
  const reasonHint = isNotEvaluable ? (notEvaluableReasonHint(evaluation.reason) ?? null) : null;
  const sourceVisual = evaluation.range_source ? statusVisual('methodologyRangeSource', evaluation.range_source) : null;
  const unit = axisUnit(axis, countingMode);
  const valueText = formatPerSession(axis === 'weekly' ? evaluation.value_weekly : evaluation.value_per_session);
  const rangeText = formatRange(evaluation.range_min, evaluation.range_max);
  const rangeWord = axis === 'weekly' ? 'meta' : 'faixa';
  const isDerived = evaluation.range_source === 'derived';

  const ariaParts = [`${unit}: ${status.label}`];
  if (reasonLabel) ariaParts.push(reasonLabel);
  else if (valueText !== '—') {
    ariaParts.push(`${valueText} ${unit}`);
    if (rangeText !== '—') ariaParts.push(`${rangeWord} ${rangeText}${isDerived ? ' (derivada)' : ''}`);
  }
  if (evaluation.severity === 'informational') ariaParts.push('desvio esperado pela intenção declarada');

  return {
    axis,
    unit,
    valueText,
    rangeText,
    rangeWord,
    isDerived,
    sourceCode: evaluation.range_source ?? null,
    sourceVisual,
    evidence: evaluation.range_evidence,
    status,
    statusCode: evaluation.status,
    isInformational: evaluation.severity === 'informational',
    reasonLabel,
    reasonHint,
    ariaLabel: ariaParts.join('; '),
  };
}

/** Uma sessão do ciclo com trabalho direto no bloco (linha expandida da tela de diagnóstico). */
export type MethodologySessionRowView = {
  key: number;
  /** "Sessão 3 · Peito A" (ou só "Sessão 3" sem nome de slot). */
  label: string;
  /** Exercícios diretos na sessão, formatado. */
  exercisesText: string;
  /** Séries diretas na sessão, formatado. */
  setsText: string;
};

export function buildSessionRows(sessions: MethodologySessionDirectWork[]): MethodologySessionRowView[] {
  return [...sessions]
    .sort((a, b) => a.slot_sequence - b.slot_sequence)
    .map((session) => {
      const name = (session.slot_name ?? '').trim();
      return {
        key: session.slot_sequence,
        label: name ? `Sessão ${session.slot_sequence} · ${name}` : `Sessão ${session.slot_sequence}`,
        exercisesText: formatPerSession(session.direct_exercises),
        setsText: formatSetsWithTechnique(session.direct_sets, session.technique_equivalent),
      };
    });
}

export type MethodologyRowView = {
  key: number;
  groupName: string;
  /** Quantas fatias foram somadas neste bloco (1 = o próprio grupo). */
  memberCount: number;
  /** Nome do macro-grupo do bloco, ou "Sem macro-grupo". */
  volumeClassName: string;
  isIntentTarget: boolean;
  frequencyText: string;
  /** Nota escrita quando o ciclo não tem 7 dias. */
  frequencyNote: string | null;
  exercises: MethodologyAxisView;
  sets: MethodologyAxisView;
  /** Fase 6: séries diretas por semana contra a meta semanal do macro-grupo. */
  weekly: MethodologyAxisView;
  /** Lote 3: faixa/meta usada é cópia não calibrada (modo ponderado). */
  uncalibrated: boolean;
  /** Fase 6: status COMBINADO dos eixos (o que o ícone do grupo segue). */
  overall: MethodologyOverallView;
  /** Sessões do ciclo com trabalho direto (só a tela de diagnóstico expande). */
  sessions: MethodologySessionRowView[];
  /** Totais do ciclo, escritos com unidade ("6 exercícios diretos · 24 séries diretas no ciclo"). */
  cycleTotalsText: string;
  /** Fase 5 (PR 5): "inclui 1,6 equiv. de técnica no ciclo"; null sem técnica. */
  techniqueNote: string | null;
};

export type MethodologyOverallView = {
  status: StatusVisual;
  statusCode: MethodologyVolumeStatus;
  isInformational: boolean;
  /** Um eixo abaixo e outro acima (metas incoerentes ou volume mal distribuído). */
  conflict: boolean;
  reasonLabel: string | null;
  reasonHint: string | null;
  /** Explicação do combinado (tooltip). */
  description: string;
  ariaLabel: string;
};

export const STATUS_CONFLICT_TEXT =
  'Um eixo está abaixo e outro acima da faixa: as metas do macro-grupo podem estar incoerentes entre si, ou o volume está mal distribuído no ciclo.';

/**
 * Status COMBINADO do grupo (Fase 6). Payload anterior sem `status` cai no
 * eixo de séries por sessão — o mesmo que o ícone seguia antes.
 */
export function buildOverallView(group: MethodologyGroupEvaluation): MethodologyOverallView {
  const hasCombined = group.status != null;
  const statusCode = hasCombined ? group.status : group.direct_sets_axis.status;
  const severity = hasCombined ? group.severity : group.direct_sets_axis.severity;
  const reason = hasCombined ? group.status_reason : group.direct_sets_axis.reason;
  const status = statusVisual('methodologyVolume', statusCode);
  const isNotEvaluable = statusCode === 'NOT_EVALUABLE';
  const reasonLabel = isNotEvaluable ? notEvaluableReasonLabel(reason) : null;
  const reasonHint = isNotEvaluable ? (notEvaluableReasonHint(reason) ?? null) : null;
  const conflict = Boolean(group.status_conflict);
  const isInformational = severity === 'informational';
  const parts = [status.description ?? status.label];
  if (reasonLabel) parts.push(`${reasonLabel}.`);
  if (conflict) parts.push(STATUS_CONFLICT_TEXT);
  if (isInformational) parts.push('Desvio esperado pela intenção declarada: não é aviso.');
  const ariaParts = [`Status do grupo: ${status.label}`];
  if (reasonLabel) ariaParts.push(reasonLabel);
  if (conflict) ariaParts.push('eixos em conflito');
  if (isInformational) ariaParts.push('desvio esperado pela intenção declarada');
  return {
    status,
    statusCode,
    isInformational,
    conflict,
    reasonLabel,
    reasonHint,
    description: parts.join(' '),
    ariaLabel: ariaParts.join('; '),
  };
}

export function buildRowView(
  group: MethodologyGroupEvaluation,
  cycleDays: number,
  countingMode?: string | null,
): MethodologyRowView {
  const setsUnit = setsUnitOf(countingMode);
  return {
    key: group.muscle_group_id,
    groupName: group.muscle_group_name,
    memberCount: group.member_group_ids.length,
    volumeClassName: group.volume_class_name || UNCLASSIFIED_VOLUME_CLASS_LABEL,
    isIntentTarget: group.is_intent_target,
    frequencyText: formatWeeklyFrequency(group.weekly_frequency),
    frequencyNote: group.frequency_is_normalized
      ? `contada em 7 dias a partir do 1º treino (ciclo de ${cycleDays} dias)`
      : null,
    exercises: buildAxisView('exercises', group.direct_exercises_axis, countingMode),
    sets: buildAxisView('sets', group.direct_sets_axis, countingMode),
    weekly: buildAxisView('weekly', group.weekly_sets_axis ?? MISSING_AXIS, countingMode),
    uncalibrated: Boolean(group.uncalibrated_ranges),
    overall: buildOverallView(group),
    sessions: buildSessionRows(group.sessions ?? []),
    cycleTotalsText: `${formatPerSession(group.direct_exercises_cycle_total)} ${plural(
      group.direct_exercises_cycle_total,
      'exercício direto',
      'exercícios diretos',
    )} · ${formatSets(group.direct_sets_cycle_total)} ${plural(
      group.direct_sets_cycle_total,
      setsUnit.singular,
      setsUnit.plural,
    )} no ciclo`,
    techniqueNote:
      (group.technique_equivalent_cycle_total ?? 0) > 0
        ? `inclui ${formatSets(group.technique_equivalent_cycle_total)} equiv. de técnica no ciclo`
        : null,
  };
}

/** Linhas na ordem do payload (o motor já ordena por bloco). */
export function buildMethodologyRows(evaluation: MethodologyVolumeEvaluation): MethodologyRowView[] {
  return evaluation.groups.map((group) => buildRowView(group, evaluation.cycle_days, evaluation.counting_mode));
}

// ---------------------------------------------------------------------------
// Rodapé de qualidade de dado
// ---------------------------------------------------------------------------

export type DataQualityItem = {
  key: keyof MethodologyDataQuality;
  count: number;
  text: string;
  /** Onde corrigir (rota do app) — null quando é só informativo. */
  fixHref: string | null;
  fixLabel: string | null;
};

function plural(n: number, one: string, many: string): string {
  return n === 1 ? one : many;
}

/** Só os contadores > 0, com a frase e o lugar de correção. */
export function buildDataQualityItems(quality: MethodologyDataQuality | null | undefined): DataQualityItem[] {
  if (!quality) return [];
  const candidates: DataQualityItem[] = [
    {
      key: 'items_without_primary_contribution',
      count: quality.items_without_primary_contribution,
      text: `${quality.items_without_primary_contribution} ${plural(
        quality.items_without_primary_contribution,
        'item sem contribuição PRIMARY cadastrada',
        'itens sem contribuição PRIMARY cadastrada',
      )} — invisíveis para a metodologia; o grupo pode parecer subtreinado sem explicação.`,
      fixHref: '/dominio-exercicios',
      fixLabel: 'Corrigir em Domínio de exercícios (contribuições musculares)',
    },
    {
      key: 'items_without_canonical',
      count: quality.items_without_canonical,
      text: `${quality.items_without_canonical} ${plural(
        quality.items_without_canonical,
        'item sem exercício canônico',
        'itens sem exercício canônico',
      )} — contados pelo mapeamento; o mesmo exercício em duas academias pode inflar os exercícios por sessão.`,
      fixHref: '/dominio-exercicios',
      fixLabel: 'Corrigir em Domínio de exercícios (mapeamentos)',
    },
    {
      key: 'items_without_mapping',
      count: quality.items_without_mapping,
      text: `${quality.items_without_mapping} ${plural(
        quality.items_without_mapping,
        'item sem mapeamento de exercício',
        'itens sem mapeamento de exercício',
      )} — ignorados (não há como ligar a um grupo).`,
      fixHref: null,
      fixLabel: null,
    },
    {
      key: 'items_without_sets_count',
      count: quality.items_without_sets_count,
      text: `${quality.items_without_sets_count} ${plural(
        quality.items_without_sets_count,
        'item sem número de séries',
        'itens sem número de séries',
      )} — entram com 0 séries diretas; preencha as séries no próprio slot.`,
      fixHref: null,
      fixLabel: null,
    },
    {
      key: 'groups_with_unknown_parent',
      count: quality.groups_with_unknown_parent,
      text: `${quality.groups_with_unknown_parent} ${plural(
        quality.groups_with_unknown_parent,
        'fatia cujo grupo-pai não foi encontrado',
        'fatias cujo grupo-pai não foi encontrado',
      )} — avaliadas como bloco próprio.`,
      fixHref: '/dominio-exercicios',
      fixLabel: 'Revisar em Domínio de exercícios (grupos musculares)',
    },
  ];
  return candidates.filter((item) => item.count > 0);
}

/** Cabeçalho do painel: modo, ciclo e normalização, escritos. */
export function buildEvaluationSummary(evaluation: MethodologyVolumeEvaluation): string {
  const mode = evaluation.volume_mode === 'equipment_series' ? 'por equipamento (modo B)' : 'por grupo (modo A)';
  const sessions = `${evaluation.training_sessions} ${plural(evaluation.training_sessions, 'sessão', 'sessões')} de treino`;
  const cycle = `ciclo de ${evaluation.cycle_days} ${plural(evaluation.cycle_days, 'dia', 'dias')}`;
  const normalized = evaluation.frequency_is_normalized
    ? ' — frequência e volume contados em 7 dias a partir do primeiro slot de treino'
    : '';
  // Lote 3: o modo de contagem só existe no Modo B (payload v5).
  const counting =
    evaluation.volume_mode === 'equipment_series' && evaluation.counting_mode
      ? ` · contagem: ${countingModeOption(evaluation.counting_mode).label}`
      : '';
  return `Modelo ${mode} · ${cycle} (${sessions})${counting}${normalized}`;
}

// ---------------------------------------------------------------------------
// Intenção declarada: persistida (PR H) × simulada ("e se") na tela de
// diagnóstico (PR G). Tudo puro para o teste de render afirmar os textos.
// ---------------------------------------------------------------------------

export const DEFAULT_INTENT_DECLARATION: MethodologyIntentDeclaration = { intent: 'standard', target_group_ids: [] };

export function intentLabel(kind: MethodologyIntentKind | string): string {
  return METHODOLOGY_INTENT_OPTIONS.find((option) => option.value === kind)?.label ?? String(kind);
}

/** Alvo só com `specialization` (regra do PUT); ids únicos e ordenados para comparação estável. */
export function normalizeIntentDeclaration(
  intent: MethodologyIntentKind,
  targetGroupIds: Iterable<number | string>,
): MethodologyIntentDeclaration {
  if (intent !== 'specialization') return { intent, target_group_ids: [] };
  const ids = new Set<number>();
  for (const raw of targetGroupIds) {
    const id = Number(raw);
    if (Number.isInteger(id) && id > 0) ids.add(id);
  }
  return { intent, target_group_ids: [...ids].sort((a, b) => a - b) };
}

export function sameIntentDeclaration(a: MethodologyIntentDeclaration, b: MethodologyIntentDeclaration): boolean {
  const na = normalizeIntentDeclaration(a.intent, a.target_group_ids);
  const nb = normalizeIntentDeclaration(b.intent, b.target_group_ids);
  return (
    na.intent === nb.intent &&
    na.target_group_ids.length === nb.target_group_ids.length &&
    na.target_group_ids.every((id, index) => id === nb.target_group_ids[index])
  );
}

/** Mensagem de bloqueio do salvar (espelha o 422 do PUT) ou null quando válido. */
export function intentDeclarationValidationMessage(declaration: MethodologyIntentDeclaration): string | null {
  if (declaration.intent === 'specialization' && declaration.target_group_ids.length === 0) {
    return 'Especialização exige ao menos um grupo-alvo.';
  }
  return null;
}

/** "Especialização (alvo: Quadríceps, Peitoral)" / "Padrão". */
export function describeIntentDeclaration(
  declaration: MethodologyIntentDeclaration,
  groupNameById: ReadonlyMap<number, string> | null | undefined,
): string {
  const label = intentLabel(declaration.intent);
  if (declaration.intent !== 'specialization') return label;
  const names = declaration.target_group_ids.map((id) => groupNameById?.get(id) ?? `grupo #${id}`);
  return names.length ? `${label} (alvo: ${names.join(', ')})` : `${label} (sem grupo-alvo)`;
}

/**
 * Query string de `GET /workout-plan-templates/{id}/methodology-volume`.
 * Vazia quando a tela segue a intenção persistida (o servidor aplica a
 * gravada); com `intent=` explícito o servidor IGNORA a persistida por inteiro
 * — é o "e se", nada é salvo. `target_group_ids` repete a chave (lista do
 * FastAPI).
 */
export function buildMethodologyVolumeQueryString(simulated: MethodologyIntentDeclaration | null): string {
  if (!simulated) return '';
  const params = new URLSearchParams();
  params.set('intent', simulated.intent);
  for (const id of simulated.target_group_ids) params.append('target_group_ids', String(id));
  return `?${params.toString()}`;
}

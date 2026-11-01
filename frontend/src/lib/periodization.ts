// Lógica PURA do Motor de Periodização no frontend (PR-17): montagem do
// pedido, validação do formulário do modal, leitura do relatório de validação,
// tradução da falha do job e mapeamento do rascunho para o builder.
//
// Nada aqui faz requisição — assim o modal e o builder ficam com só UI e o
// comportamento fica coberto por vitest (`periodization.test.ts`).
//
// Regra que atravessa o arquivo inteiro: **nenhuma escrita de modelo**. O
// rascunho vive em `sessionStorage` entre a listagem e o builder, e só vira
// modelo no `POST /workout-plan-templates` que o usuário dispara no "Salvar".

import { ASYNC_JOB_POLL_INTERVAL_MS } from '@/lib/async-job-poll';
import { DEFAULT_COUNTING_MODE, normalizeCountingMode } from '@/lib/volume-counting-mode';
import motorConstants from './motor-constants.generated.json';
import type {
  PeriodizationDraftRequest,
  PeriodizationFailure,
  PeriodizationFormState,
  PeriodizationJobResult,
  PeriodizationReportEntry,
  PeriodizationSeed,
  PeriodizationValidationReport,
} from './periodization-types';
import { VOLUME_GAP_REASON_LABEL } from './periodization-types';
import type { VolumeMode, WorkoutPlanTemplateSlot } from './workout-plan-templates-types';

// Teto de instruções livres (§7.1 passo 2), o mesmo `max_length=2000` de
// `PeriodizationDraftIn.instructions`.
export const PERIODIZATION_INSTRUCTIONS_MAX_LENGTH = 2000;

// `days_per_cycle: Field(default=4, ge=1, le=8)` — teto = MAX_TEMPLATE_SLOTS
// (8): o rascunho vira um Modelo, que cabe numa grade de no máximo 2x4.
export const PERIODIZATION_DAYS_MIN = 1;
export const PERIODIZATION_DAYS_MAX = 8;
export const PERIODIZATION_DAYS_DEFAULT = 4;

// Teto de fontes RAG: `Field(max_length=10)` em `rag_sources`.
export const PERIODIZATION_MAX_RAG_SOURCES = 10;

// Espera do polling: o prazo em que o backend fecha o job `pending` como
// `failed`/`worker_lost` (timeout da Lambda + folga; `periodization_job` do
// espelho gerado de `api/app/periodization/core.py`), mais um intervalo de
// polling para ler esse fechamento. Assim a tela nunca desiste de um job que
// ainda vai responder (a chamada à LLM tem `llm_timeout_seconds`).
export const PERIODIZATION_POLL_TIMEOUT_MS =
  motorConstants.periodization_job.job_deadline_seconds * 1000 + ASYNC_JOB_POLL_INTERVAL_MS;

export const PERIODIZATION_DRAFT_STORAGE_KEY = 'musclefit.periodization-draft.v1';

// Marca na URL que o `/modelos-treino/novo` foi aberto pelo fluxo de
// periodização. Sem ela o builder ignora qualquer rascunho guardado — um
// "Novo modelo" comum nunca é contaminado por um rascunho antigo da sessão.
export const PERIODIZATION_ORIGIN_PARAM = 'origem';
export const PERIODIZATION_ORIGIN_VALUE = 'periodizacao';

export function periodizationOriginQuery(): string {
  return `?${PERIODIZATION_ORIGIN_PARAM}=${PERIODIZATION_ORIGIN_VALUE}`;
}

export function hasPeriodizationOriginFlag(search: string): boolean {
  return new URLSearchParams(search || '').get(PERIODIZATION_ORIGIN_PARAM) === PERIODIZATION_ORIGIN_VALUE;
}

// --- Formulário -------------------------------------------------------------

export function newPeriodizationForm(): PeriodizationFormState {
  return {
    ragSources: [],
    trainingTypeId: null,
    volumeMode: 'percent_budget',
    countingMode: DEFAULT_COUNTING_MODE,
    gymId: null,
    daysPerCycle: PERIODIZATION_DAYS_DEFAULT,
    instructions: '',
    trainingGoal: null,
    dayGoals: [],
    cardio: 'auto',
  };
}

/**
 * Objetivo de cada dia do ciclo no formulário (M97 F5), com exatamente
 * `daysPerCycle` posições: `null` = herda o objetivo do Modelo. Mudar o número
 * de dias corta ou completa a lista sem perder as escolhas já feitas.
 */
export function periodizationDayGoals(form: Pick<PeriodizationFormState, 'daysPerCycle' | 'dayGoals'>): (string | null)[] {
  const days = Math.max(0, Math.trunc(form.daysPerCycle) || 0);
  return Array.from({ length: days }, (_, index) => form.dayGoals?.[index] ?? null);
}

/**
 * Espelha `_validate_periodization_request` no que dá para conferir sem banco:
 * evita uma viagem de rede para o 422 óbvio. O backend continua sendo a fonte
 * de verdade (fonte RAG inexistente, por exemplo, só ele sabe).
 */
export function validatePeriodizationForm(form: PeriodizationFormState): string | null {
  if (form.volumeMode === 'equipment_series' && !form.gymId) {
    return 'O modo "Equipamento + séries fixas" exige uma academia para o modelo escolher equipamentos.';
  }
  if (form.ragSources.length > PERIODIZATION_MAX_RAG_SOURCES) {
    return `Selecione no máximo ${PERIODIZATION_MAX_RAG_SOURCES} fontes de RAG.`;
  }
  if (
    !Number.isInteger(form.daysPerCycle) ||
    form.daysPerCycle < PERIODIZATION_DAYS_MIN ||
    form.daysPerCycle > PERIODIZATION_DAYS_MAX
  ) {
    return `O número de dias do ciclo precisa ser um inteiro entre ${PERIODIZATION_DAYS_MIN} e ${PERIODIZATION_DAYS_MAX}.`;
  }
  if (form.instructions.length > PERIODIZATION_INSTRUCTIONS_MAX_LENGTH) {
    return `As instruções livres não podem passar de ${PERIODIZATION_INSTRUCTIONS_MAX_LENGTH} caracteres.`;
  }
  return null;
}

/**
 * Formulário -> corpo do `POST /workout-plan-templates/periodization-drafts`.
 *
 * `gym_id` é descartado no Modo A? Não: a academia é OPCIONAL no
 * `percent_budget` (refina o contexto) e obrigatória no `equipment_series`, e
 * nos dois casos vai como escolhida. Instruções em branco viram `null` (e não
 * `""`) para não ocupar espaço no prompt.
 */
export function buildPeriodizationRequest(
  form: PeriodizationFormState,
  defaultGoalValue: string | null = null,
): PeriodizationDraftRequest {
  const instructions = form.instructions.trim();
  // M97 F5: o objetivo do Modelo e o de cada dia só entram no corpo quando
  // escolhidos — sem escolha, o pedido é o de sempre. Dia que herda vira o
  // objetivo do Modelo (ou o default do dono, `GET /training-goals`); a lista
  // vai inteira, um valor por dia, como `PeriodizationDraftIn.day_goals` exige.
  const fallbackGoal = form.trainingGoal ?? defaultGoalValue;
  const dayGoals = periodizationDayGoals(form);
  const resolvedDayGoals = dayGoals.map((goal) => goal ?? fallbackGoal);
  const sendDayGoals =
    dayGoals.some((goal) => goal != null) && resolvedDayGoals.every((goal): goal is string => goal != null);
  return {
    rag_sources: form.ragSources.slice(),
    training_type_id: form.trainingTypeId ? Number(form.trainingTypeId) : null,
    volume_mode: form.volumeMode,
    // O modo de contagem só existe no Modo B; o Modo A segue com o corpo de antes.
    ...(form.volumeMode === 'equipment_series' ? { volume_counting_mode: normalizeCountingMode(form.countingMode) } : {}),
    gym_id: form.gymId ? Number(form.gymId) : null,
    days_per_cycle: form.daysPerCycle,
    instructions: instructions ? instructions.slice(0, PERIODIZATION_INSTRUCTIONS_MAX_LENGTH) : null,
    ...(form.trainingGoal ? { training_goal: form.trainingGoal } : {}),
    ...(sendDayGoals ? { day_goals: resolvedDayGoals as string[] } : {}),
    // M98: `auto` não envia o campo — sem ele o backend decide pelas preferências.
    ...(form.cardio === 'include' ? { include_cardio: true } : {}),
    ...(form.cardio === 'exclude' ? { include_cardio: false } : {}),
  };
}

// --- Falha do job -----------------------------------------------------------

function emptyReport(): PeriodizationValidationReport {
  return { errors: [], warnings: [], auto_fixes: [] };
}

function asEntries(value: unknown): PeriodizationReportEntry[] {
  if (!Array.isArray(value)) return [];
  return value.filter((entry): entry is PeriodizationReportEntry => !!entry && typeof entry === 'object');
}

function asReport(value: unknown): PeriodizationValidationReport | null {
  if (!value || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  return {
    errors: asEntries(raw.errors),
    warnings: asEntries(raw.warnings),
    auto_fixes: asEntries(raw.auto_fixes),
  };
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asStringOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

export const PERIODIZATION_GENERIC_FAILURE =
  'A geração automática do modelo falhou. Nenhum modelo foi criado — tente novamente.';

/**
 * Lê o campo `error` de um job `failed`.
 *
 * Erro ESTRUTURAL vem como JSON (`PeriodizationError.detail`) com
 * `{message, validation_report, generation_log_id, model, prompt_version}` —
 * é isso que permite dizer ao usuário *o que* o validador recusou em vez de um
 * "falhou" genérico. Falha de configuração (sem `OPENAI_MODEL_PERIODIZATION`)
 * ou da OpenAI vem como texto simples no mesmo campo, e aí o texto É a
 * mensagem.
 */
export function parsePeriodizationJobError(raw: unknown): PeriodizationFailure {
  const fallback: PeriodizationFailure = {
    message: PERIODIZATION_GENERIC_FAILURE,
    validationReport: null,
    generationLogId: null,
    model: null,
    promptVersion: null,
  };
  if (typeof raw !== 'string' || !raw.trim()) return fallback;

  const text = raw.trim();
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ...fallback, message: text };
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { ...fallback, message: text };
  }

  const data = parsed as Record<string, unknown>;
  return {
    message: asStringOrNull(data.message) ?? text,
    validationReport: asReport(data.validation_report),
    generationLogId: asNumberOrNull(data.generation_log_id),
    model: asStringOrNull(data.model),
    promptVersion: asStringOrNull(data.prompt_version),
  };
}

// --- Leitura do relatório ---------------------------------------------------

function numberText(value: unknown): string {
  return typeof value === 'number' ? String(Math.round(value * 100) / 100) : String(value ?? '?');
}

function groupText(entry: PeriodizationReportEntry): string {
  const name = asStringOrNull(entry.muscle_group_name);
  if (name) return name;
  const id = asNumberOrNull(entry.muscle_group_id);
  return id != null ? `grupo #${id}` : 'grupo muscular';
}

// Vocabulário da metodologia de volume por grupo (Fase 4), em português.
const FREQUENCY_BUCKET_TEXT: Record<string, string> = {
  '1x': '1x/semana',
  '2x': '2x/semana',
  '3x_plus': '3x ou mais por semana',
};

// Fase 6: o grupo é avaliado pelo MACRO-GRUPO (cadastro editável), não mais
// pelo porte da 055 — `size_class` ainda vem no payload, mas não é lido.
function volumeClassText(entry: PeriodizationReportEntry): string {
  const name = asStringOrNull(entry.volume_class_name);
  return name ? `macro-grupo ${name}` : 'sem macro-grupo';
}

// Faixas do cadastro podem ser fracionárias (12,5): vírgula decimal pt-BR,
// inteiro quando inteiro.
function decimalText(value: unknown): string {
  return typeof value === 'number'
    ? value.toLocaleString('pt-BR', { minimumFractionDigits: 0, maximumFractionDigits: 2 })
    : String(value ?? '?');
}

function frequencyText(entry: PeriodizationReportEntry): string {
  const bucket = asStringOrNull(entry.frequency_bucket);
  return (bucket && FREQUENCY_BUCKET_TEXT[bucket]) || 'frequência não avaliada';
}

// Código do autor (EXG-NNN) cabe na frase; a faixa derivada (propostas D1/D2)
// vem com um parágrafo de justificativa que não cabe no painel.
function evidenceText(entry: PeriodizationReportEntry): string {
  const evidence = asStringOrNull(entry.evidence);
  if (!evidence) return '';
  return evidence.startsWith('EXG-') ? ` (${evidence})` : ' (faixa derivada da metodologia)';
}

function methodologyRangeText(entry: PeriodizationReportEntry, direction: 'abaixo' | 'acima'): string {
  const limit = direction === 'abaixo' ? entry.min : entry.max;
  const limitLabel = direction === 'abaixo' ? 'mínimo' : 'máximo';
  return `${groupText(entry)} (${volumeClassText(entry)}, ${frequencyText(entry)}): ${decimalText(
    entry.projected_sets,
  )} séries diretas por sessão, ${direction} do ${limitLabel} de ${decimalText(limit)}${evidenceText(entry)}.`;
}

// Fase 6: meta SEMANAL do macro-grupo (independe da frequência).
function methodologyWeeklyText(entry: PeriodizationReportEntry, direction: 'abaixo' | 'acima'): string {
  const limit = direction === 'abaixo' ? entry.min : entry.max;
  const limitLabel = direction === 'abaixo' ? 'mínimo' : 'máximo';
  return `${groupText(entry)} (${volumeClassText(entry)}): ${decimalText(
    entry.weekly_sets,
  )} séries diretas por semana, ${direction} do ${limitLabel} de ${decimalText(limit)} da meta semanal${evidenceText(
    entry,
  )}.`;
}

function positionText(entry: PeriodizationReportEntry): string {
  const slot = asNumberOrNull(entry.slot);
  const item = asNumberOrNull(entry.item);
  if (slot == null && item == null) return '';
  return ` (dia ${slot ?? '?'}, item ${item ?? '?'})`;
}

// M46: complemento de volume. `axis` = 'session' (séries por sessão) ou
// 'weekly' (séries por semana).
function axisText(entry: PeriodizationReportEntry): string {
  return entry.axis === 'weekly' ? 'séries diretas por semana' : 'séries diretas por sessão';
}

const TECHNIQUE_TEXT: Record<string, string> = { drop_set: 'drop set', rest_pause: 'rest-pause' };

function techniqueText(value: unknown): string {
  const code = asStringOrNull(value);
  return code ? (TECHNIQUE_TEXT[code] ?? code) : 'técnica';
}

// Campos de `OPTIONAL_ZERO_NULLED.fields` (validation.OPTIONAL_POSITIVE_ITEM_FIELDS).
const OPTIONAL_FIELD_TEXT: Record<string, string> = {
  advanced_technique_applications: 'aplicações da técnica',
  advanced_technique_duration_seconds: 'duração da técnica',
  warmup_sets_count: 'séries de aquecimento',
  warmup_reps: 'repetições do aquecimento',
  warmup_rest_seconds: 'descanso do aquecimento',
  feeler_set_sets_count: 'séries de ajuste',
  feeler_set_reps: 'repetições da série de ajuste',
  feeler_set_rest_seconds: 'descanso da série de ajuste',
  peak_contraction_seconds: 'pico de contração',
};

function complementPrefix(entry: PeriodizationReportEntry): string {
  const exercise = [asStringOrNull(entry.exercise_name), asStringOrNull(entry.equipment_name)].filter(Boolean).join(' / ');
  return `${groupText(entry)}${positionText(entry)}${exercise ? ` — ${exercise}` : ''}`;
}

function complementTarget(entry: PeriodizationReportEntry): string {
  return `; faltavam ${decimalText(entry.deficit_before)} ${axisText(entry)} e agora faltam ${decimalText(
    entry.deficit_after,
  )}`;
}

/**
 * Uma entrada do relatório em português, para o painel lateral do builder.
 * Código desconhecido devolve o JSON dos campos — visível e depurável, em vez
 * de sumir da tela.
 */
export function describePeriodizationEntry(entry: PeriodizationReportEntry): string {
  switch (entry.code) {
    case 'SCHEMA_INVALID':
      return `A resposta do modelo não seguiu o formato esperado: ${asStringOrNull(entry.message) ?? 'sem detalhe'}.`;
    case 'VOLUME_MODE_MISMATCH':
      return `O modelo respondeu no modo "${entry.received}" quando o pedido era "${entry.expected}".`;
    case 'MUSCLE_GROUP_NOT_FOUND':
      return `O modelo citou um grupo muscular que não existe no catálogo (id ${numberText(entry.muscle_group_id)}).`;
    case 'EQUIPMENT_NOT_IN_GYM':
      return `Exercício ${numberText(entry.mapping_id)} / equipamento ${numberText(
        entry.equipment_id,
      )} não está ativo na academia ${numberText(entry.gym_id)}.`;
    case 'GYM_REQUIRED':
      return 'O rascunho prescreveu equipamentos, mas nenhuma academia foi informada no pedido.';
    case 'TEMPLATE_INVALID':
      return `As regras de modelo recusaram o rascunho: ${asStringOrNull(entry.message) ?? 'sem detalhe'}.`;
    case 'SLOT_COUNT_MISMATCH':
      return `Você pediu ${numberText(entry.expected)} dias no ciclo e o rascunho veio com ${numberText(
        entry.received,
      )}. Ajuste os slots se quiser.`;
    case 'ACTIVATION_BELOW_MIN':
      return `${groupText(entry)}: projeção de ${numberText(entry.projected)} séries na janela de ${numberText(
        entry.window_days,
      )} dias, abaixo do mínimo de ${numberText(entry.min)}.`;
    case 'ACTIVATION_ABOVE_MAX':
      return `${groupText(entry)}: projeção de ${numberText(entry.projected)} séries na janela de ${numberText(
        entry.window_days,
      )} dias, acima do máximo de ${numberText(entry.max)}.`;
    case 'ACTIVATION_COVERAGE':
      if (entry.status === 'UNAVAILABLE') {
        return `Não foi possível projetar a ativação deste rascunho: ${
          asStringOrNull(entry.message) ?? 'sem detalhe'
        }.`;
      }
      return `${groupText(entry)}: ${numberText(entry.sum_pct_min)}%–${numberText(
        entry.sum_pct_max,
      )}% da meta semanal cobertos pelos slots (${entry.status}).`;
    case 'METHODOLOGY_BELOW_RANGE':
      return methodologyRangeText(entry, 'abaixo');
    case 'METHODOLOGY_ABOVE_RANGE':
      return methodologyRangeText(entry, 'acima');
    case 'METHODOLOGY_WEEKLY_BELOW_RANGE':
      return methodologyWeeklyText(entry, 'abaixo');
    case 'METHODOLOGY_WEEKLY_ABOVE_RANGE':
      return methodologyWeeklyText(entry, 'acima');
    case 'SLOT_TOO_FEW_EXERCISES': {
      const name = asStringOrNull(entry.slot_name);
      return `Dia ${numberText(entry.slot)}${name ? ` (${name})` : ''} tem ${numberText(
        entry.items,
      )} exercício(s); uma sessão completa tem pelo menos ${numberText(entry.min)}.`;
    }
    case 'RPE_CLAMPED': {
      // M97 F5: o teto pode ser o do OBJETIVO do item (rótulo vem do backend).
      const goalLabel = asStringOrNull(entry.training_goal_label);
      return `RPE ajustado de ${entry.from ?? '?'} para ${entry.to ?? '?'}${positionText(entry)} ${
        goalLabel ? `pelo teto do objetivo ${goalLabel}` : 'pelo teto seguro do equipamento'
      }.`;
    }
    case 'TECHNIQUE_REMOVED': {
      const goalLabel = asStringOrNull(entry.training_goal_label);
      return `Técnica "${entry.technique ?? '?'}" removida${positionText(entry)}: ${
        goalLabel
          ? `fora da política do objetivo ${goalLabel}`
          : asStringOrNull(entry.reason) ?? 'incompatível com o equipamento'
      }.`;
    }
    case 'ISOLATION_GOAL_OVERRIDE':
      return `Isolador${positionText(entry)} num dia de ${
        asStringOrNull(entry.day_goal_label) ?? '?'
      } ficou no objetivo ${asStringOrNull(entry.training_goal_label) ?? '?'} (faixa moderada, não carga máxima).`;
    case 'GOAL_REPS_FIXED':
      return `Repetições${positionText(entry)} ajustadas de ${entry.from ?? '?'} para ${entry.to ?? '?'}: a faixa do objetivo ${
        asStringOrNull(entry.training_goal_label) ?? '?'
      }.`;
    case 'DAY_HEAVY_COMPOUNDS_OUT_OF_RANGE': {
      const name = asStringOrNull(entry.slot_name);
      return `Dia ${numberText(entry.slot)}${name ? ` (${name})` : ''} de ${
        asStringOrNull(entry.training_goal_label) ?? '?'
      } tem ${numberText(entry.compound_count)} composto(s) pesado(s); o objetivo pede de ${numberText(
        entry.min,
      )} a ${numberText(entry.max)}.`;
    }
    case 'EQUIPMENT_REPLACED': {
      const from = entry.from as { mapping_id?: number; equipment_id?: number } | undefined;
      const to = entry.to as { mapping_id?: number; equipment_id?: number } | undefined;
      const nome = [asStringOrNull(entry.exercise_name), asStringOrNull(entry.equipment_name)]
        .filter(Boolean)
        .join(' / ');
      return `Exercício ${numberText(from?.mapping_id)} / equipamento ${numberText(
        from?.equipment_id,
      )} não existe nesta academia${positionText(entry)}: trocado por ${numberText(
        to?.mapping_id,
      )} / ${numberText(to?.equipment_id)}${nome ? ` (${nome})` : ''}, do mesmo grupo muscular. Confira antes de salvar.`;
    }
    case 'MUSCLE_GROUP_RELABELED': {
      const nome = [asStringOrNull(entry.exercise_name), asStringOrNull(entry.equipment_name)]
        .filter(Boolean)
        .join(' / ');
      const fromName = asStringOrNull(entry.from_name) ?? `grupo #${numberText(entry.from)}`;
      const toName = asStringOrNull(entry.to_name) ?? `grupo #${numberText(entry.to)}`;
      return `${nome || 'Exercício'}${positionText(
        entry,
      )} veio marcado como ${fromName}; na academia ele está em ${toName}, e o rascunho passou a usar esse grupo.`;
    }
    case 'OPTIONAL_ZERO_NULLED': {
      const fields = Array.isArray(entry.fields)
        ? entry.fields.map((f) => OPTIONAL_FIELD_TEXT[String(f)] ?? String(f)).join(', ')
        : '';
      return `Campos com 0${positionText(entry)} ficaram vazios (0 significa "nenhum")${fields ? `: ${fields}` : ''}.`;
    }
    // M98 C5: o backend monta o texto do cardio com os rótulos dos donos.
    case 'CARDIO_ADJUSTED':
    case 'CARDIO_REMOVED':
      return asStringOrNull(entry.message) ?? `Cardio do dia ${numberText(entry.slot)} ajustado pelo sistema.`;
    case 'SLOT_REMOVED':
      return `Dia ${numberText(entry.slot)}${
        asStringOrNull(entry.slot_name) ? ` (${asStringOrNull(entry.slot_name)})` : ''
      } foi removido: o rascunho veio com mais dias do que os ${numberText(entry.expected)} pedidos.`;
    case 'SLOT_ADDED':
      return `Dia ${numberText(
        entry.slot,
      )} foi acrescentado como descanso para fechar os ${numberText(entry.expected)} dias pedidos.`;
    case 'SLOT_SEQUENCE_RENUMBERED':
      return `Ordem dos dias corrigida: o dia ${entry.from ?? '?'} passou a ser o ${entry.to ?? '?'}.`;
    case 'SLOT_TYPE_FIXED':
      return `Dia ${numberText(
        entry.slot,
      )} veio marcado como descanso mas tinha exercícios: virou dia de treino.`;
    case 'PRIORITIZE_UNMARKED':
      return `Prioridade desmarcada${positionText(entry)}: o dia tinha ${numberText(
        entry.received,
      )} itens priorizados e o máximo é ${numberText(entry.max)}.`;
    case 'ITEM_REMOVED':
      return `Item removido${positionText(entry)}: ${
        asStringOrNull(entry.reason) ?? 'não dava para usar como exercício'
      }. O resto do dia foi mantido.`;
    case 'TECHNIQUE_FIELDS_CLEARED':
      return `Técnica "${entry.technique ?? '?'}"${positionText(
        entry,
      )} só define o número de séries: aplicações e pausa foram zeradas.`;
    case 'SETS_COUNT_FIXED':
      return `Séries ajustadas de ${entry.from ?? 'vazio'} para ${entry.to ?? '?'}${positionText(
        entry,
      )}: todo exercício precisa de pelo menos uma série.`;
    case 'VOLUME_SETS_ADDED':
    case 'VOLUME_SETS_ABOVE_IDEAL':
      return `${complementPrefix(entry)}: séries de ${entry.from ?? '?'} para ${entry.to ?? '?'}${complementTarget(entry)}${
        entry.code === 'VOLUME_SETS_ABOVE_IDEAL'
          ? ` (acima do ideal de ${numberText(entry.ideal)} por exercício${
              entry.max_sets != null ? `; teto de ${numberText(entry.max_sets)}` : ''
            })`
          : ''
      }.`;
    case 'VOLUME_EXERCISE_ADDED':
      return `${complementPrefix(entry)}: exercício novo da academia com ${numberText(entry.sets)} série(s)${complementTarget(
        entry,
      )}. Confira antes de salvar.`;
    case 'VOLUME_TECHNIQUE_ADDED':
      return `${complementPrefix(entry)}: ${techniqueText(entry.technique)} ×${numberText(
        entry.applications,
      )} (+${decimalText(entry.equivalent)} série equivalente)${complementTarget(entry)}.`;
    case 'VOLUME_REDUCED':
      return `${complementPrefix(entry)}: séries de ${entry.from ?? '?'} para ${entry.to ?? '?'}; passavam ${decimalText(
        entry.excess_before,
      )} ${axisText(entry)} do máximo de ${decimalText(entry.max)} e agora passam ${decimalText(entry.excess_after)}${
        entry.prioritized ? ' (exercício priorizado: sem outra opção de corte)' : ''
      }.`;
    case 'VOLUME_GAP_UNRESOLVED': {
      const reasons = Array.isArray(entry.reasons)
        ? entry.reasons.map((r) => VOLUME_GAP_REASON_LABEL[String(r)] ?? String(r)).join('; ')
        : '';
      return `${groupText(entry)}: ${decimalText(entry.value_after)} ${axisText(entry)}, ainda abaixo do mínimo de ${decimalText(
        entry.min,
      )}; o complemento automático parou${reasons ? ` (${reasons})` : ''}.`;
    }
    default:
      return JSON.stringify(entry);
  }
}

export function countPeriodizationReport(report: PeriodizationValidationReport | null): number {
  if (!report) return 0;
  return report.errors.length + report.warnings.length + report.auto_fixes.length;
}

// --- Rascunho -> builder ----------------------------------------------------

const KNOWN_ITEM_KINDS = new Set(['muscle_group', 'cardio', 'equipment']);

/**
 * `result` do job -> o que o builder precisa para abrir pré-preenchido.
 *
 * Normaliza o que o builder não deveria ter que reconferir: slots em ordem de
 * `sequence`, itens em ordem de `item_order` e `item_kind` fora do contrato
 * descartado (o builder não sabe renderizar um item desconhecido). O
 * `training_type_id` volta como string porque é o que o `Select` do Mantine
 * usa.
 */
export function periodizationJobResultToSeed(result: PeriodizationJobResult): PeriodizationSeed {
  const draft = result.draft;
  const volumeMode: VolumeMode = draft.volume_mode === 'equipment_series' ? 'equipment_series' : 'percent_budget';
  const slots: WorkoutPlanTemplateSlot[] = (draft.slots || [])
    .slice()
    .sort((a, b) => a.sequence - b.sequence)
    .map((slot) => ({
      ...slot,
      items: (slot.items || [])
        .filter((item) => KNOWN_ITEM_KINDS.has(item.item_kind))
        .slice()
        .sort((a, b) => a.item_order - b.item_order),
    }));

  return {
    header: {
      name: draft.name || '',
      description: draft.description || '',
      trainingTypeId: draft.training_type_id != null ? String(draft.training_type_id) : null,
      volumeMode,
      countingMode: draft.volume_counting_mode ? normalizeCountingMode(draft.volume_counting_mode) : undefined,
      // M97 F5: objetivo do Modelo do rascunho (ausente = o builder fica no default).
      trainingGoal: draft.training_goal ?? undefined,
      isActive: draft.is_active !== false,
    },
    slots,
    grid:
      draft.grid_rows != null && draft.grid_cols != null ? { rows: draft.grid_rows, cols: draft.grid_cols } : null,
    report: asReport(result.validation_report) ?? emptyReport(),
    rationale: result.rationale || '',
    sourceCitations: Array.isArray(result.source_citations)
      ? result.source_citations.filter((c): c is string => typeof c === 'string')
      : [],
    // O `generation_log_id` do resultado é a única coisa que liga o modelo
    // salvo à geração que o produziu. Perdê-lo aqui faz o modelo nascer
    // `origin='manual'` e a auditoria ficar órfã.
    generationLogId: result.generation_log_id ?? null,
    model: result.model || '',
    promptVersion: result.prompt_version || '',
  };
}

// --- Persistência do rascunho na sessão (nunca em banco) --------------------

// `Storage` injetável só para os testes; em runtime é sempre o
// `window.sessionStorage`. `sessionStorage` (e não `localStorage`) de
// propósito: um rascunho não salvo não deve sobreviver ao fechamento da aba.
type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

function defaultStorage(): StorageLike | null {
  try {
    if (typeof window === 'undefined' || !window.sessionStorage) return null;
    return window.sessionStorage;
  } catch {
    // Safari em modo privado pode lançar ao acessar sessionStorage.
    return null;
  }
}

export function storePeriodizationSeed(seed: PeriodizationSeed, storage: StorageLike | null = defaultStorage()): void {
  if (!storage) return;
  try {
    storage.setItem(PERIODIZATION_DRAFT_STORAGE_KEY, JSON.stringify(seed));
  } catch {
    // Cota estourada / storage bloqueado: o rascunho simplesmente não abre
    // pré-preenchido. Nada foi gravado em banco de qualquer forma.
  }
}

export function readPeriodizationSeed(storage: StorageLike | null = defaultStorage()): PeriodizationSeed | null {
  if (!storage) return null;
  let raw: string | null = null;
  try {
    raw = storage.getItem(PERIODIZATION_DRAFT_STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as PeriodizationSeed;
    if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.slots) || !parsed.header) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function clearPeriodizationSeed(storage: StorageLike | null = defaultStorage()): void {
  if (!storage) return;
  try {
    storage.removeItem(PERIODIZATION_DRAFT_STORAGE_KEY);
  } catch {
    // idem
  }
}

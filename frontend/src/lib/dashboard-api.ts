import { apiFetch } from './api';
import { dispatchAndPollJob } from './async-job-poll';
import type {
  RpeDistributionResponse,
  TrainingTrendResponse,
} from './analytics-types';
import type {
  BodyMeasurementInput,
  BodyMeasurementsListResponse,
  BodyMeasurementWriteResponse,
  BodyResponse,
} from './dashboard-body-types';
import type {
  DailyCheckinInput,
  DailyCheckinsListResponse,
  DailyCheckinWriteResponse,
} from './dashboard-checkin-types';
import type { CardioResponse } from './dashboard-cardio-types';
import type {
  ContextEventInput,
  ContextEventWriteResponse,
  ContextResponse,
} from './dashboard-context-types';
import type {
  EnginePlanResponse,
  EngineRunsResponse,
} from './dashboard-engine-types';
import type { DashboardDayRange, DashboardWindowQuery } from './dashboard-filters';
import type { InsightsResponse, OverviewResponse } from './dashboard-overview-types';
import type { RecoverySignalsResponse } from './dashboard-recovery-types';
import type {
  DashboardCycleResponse,
  DashboardProgressionResponse,
  DashboardProgressionSuggestionResponse,
  DashboardProgressionTrendResponse,
  MuscleGroupProgressionResponse,
} from './dashboard-v2-types';
import type {
  DataQualityResponse,
  ExerciseProgressResponse,
  MuscleDrilldownResponse,
  MusclesResponse,
  StrengthPanelResponse,
  TrainingGoalValue,
} from './dashboard-v3-types';
import type {
  AdherenceResponse,
  WorkoutDetailResponse,
  WorkoutsResponse,
} from './dashboard-workouts-types';
import type { MappingCanonicalOptionsResponse } from './mappings-types';
import type {
  TrainingEvolutionResponse,
  WorkoutsAnalysisGenerateRequest,
  WorkoutsAnalysisGenerateResponse,
} from './workouts-analysis-types';

/**
 * Camada fina de acesso aos endpoints do hub `/dashboard/*` (plano §11, F0.4).
 *
 * Antes desta etapa cada tela montava a string do path dentro do próprio
 * `queryFn`, com a `queryKey` escrita à mão ao lado — o mesmo endpoint
 * aparecia com chaves diferentes em telas diferentes e uma mudança de
 * parâmetro exigia caçar literais. Aqui há UMA função por endpoint e UMA
 * chave por consulta.
 *
 * Duas gerações convivem de propósito (a F2.4 aposentou telas, não rotas):
 * - **v3** (`/analytics/dashboard/*`): tem `response_model` no backend, aceita
 *   `window` nomeada e devolve o envelope semântico;
 * - **v2** (`/analytics/dashboard-*`, com hífen): devolve `dict` livre e
 *   recebe `days`, derivado da janela por `dashboardWindowDays()`.
 *
 * Escopo deliberado: só as telas do hub. O resto do app continua chamando
 * `apiFetch` direto — migrar tudo seria uma mudança sem consumidor nesta fase.
 */

// ---------------------------------------------------------------------------
// Chaves de cache
// ---------------------------------------------------------------------------

/**
 * `queryKey` padronizada. Prefixo comum `['dashboard']` para permitir
 * invalidação em bloco; o segundo nível é o endpoint e o terceiro os
 * parâmetros que de fato mudam a resposta.
 */
export const dashboardKeys = {
  all: ['dashboard'] as const,
  summary: (days: number) => ['dashboard', 'analytics-summary', days] as const,
  trainingTrend: (days: number) => ['dashboard', 'analytics-training-trend', days] as const,
  rpeDistribution: (days: number) => ['dashboard', 'analytics-rpe-distribution', days] as const,
  trainingEvolution: (days: number) => ['dashboard', 'analytics-training-evolution', days] as const,
  workoutSessions: (days: number) => ['dashboard', 'workout-sessions', days] as const,
  muscleStatus: (days: number) => ['dashboard', 'muscle-status', days] as const,
  muscleDrilldown: (groupId: number | null, days: number) =>
    ['dashboard', 'muscle-drilldown', groupId, days] as const,
  stimulusFatigue: (groupId: number | null, days: number) =>
    ['dashboard', 'stimulus-fatigue', groupId, days] as const,
  recovery: (days: number) => ['dashboard', 'recovery', days] as const,
  cycle: () => ['dashboard', 'cycle'] as const,
  /** M97 F4: `goal` = filtro de objetivo (`null`/ausente = todas as sessões). */
  progression: (mappingId: number | null, range: DashboardDayRange, goal: TrainingGoalValue | null = null) =>
    [
      'dashboard',
      'progression',
      mappingId,
      range.days,
      range.date_from ?? null,
      range.date_to ?? null,
      goal,
    ] as const,
  /** M97 F4: `goal` = objetivo da progressão (`null` = o default do backend). */
  progressionTrend: (mappingId: number | null, range: DashboardDayRange, goal: TrainingGoalValue | null = null) =>
    [
      'dashboard',
      'progression-trend',
      mappingId,
      range.days,
      range.date_from ?? null,
      range.date_to ?? null,
      goal,
    ] as const,
  /** M97 F4: `goal` = objetivo da sugestão (`null` = o default do backend). */
  progressionSuggestion: (mappingId: number | null, goal: TrainingGoalValue | null = null) =>
    ['dashboard', 'progression-suggestion', mappingId, goal] as const,
  progressionByMuscleGroup: (window: DashboardWindowQuery) =>
    [
      'dashboard',
      'progression-by-muscle-group',
      window.window,
      window.date_from ?? null,
      window.date_to ?? null,
    ] as const,
  exerciseOptions: () => ['dashboard', 'mapping-canonical-options'] as const,
  /**
   * v3 (F3.1). A chave da LISTA leva a janela; a do DETALHE leva só o id, e é
   * de propósito: a ficha é um objeto, não um recorte de período — trocar o
   * filtro global não pode invalidar (nem recarregar) a auditoria dela.
   */
  engineRuns: (window: DashboardWindowQuery) =>
    ['dashboard', 'engine-runs', window.window, window.date_from ?? null, window.date_to ?? null] as const,
  enginePlan: (planId: number | null) => ['dashboard', 'engine-plan', planId] as const,
  dataQuality: (window: DashboardWindowQuery) =>
    ['dashboard', 'data-quality', window.window, window.date_from ?? null, window.date_to ?? null] as const,
  recoverySignals: (window: DashboardWindowQuery) =>
    [
      'dashboard',
      'recovery-signals',
      window.window,
      window.date_from ?? null,
      window.date_to ?? null,
    ] as const,
  muscles: (
    window: DashboardWindowQuery,
    includeReadiness: boolean,
    includeComparatives = false,
  ) =>
    [
      'dashboard',
      'muscles',
      window.window,
      window.date_from ?? null,
      window.date_to ?? null,
      includeReadiness,
      includeComparatives,
    ] as const,
  muscleDetail: (groupId: number | null, window: DashboardWindowQuery) =>
    [
      'dashboard',
      'muscle-detail',
      groupId,
      window.window,
      window.date_from ?? null,
      window.date_to ?? null,
    ] as const,
  workouts: (window: DashboardWindowQuery, limit: number, offset: number) =>
    [
      'dashboard',
      'workouts',
      window.window,
      window.date_from ?? null,
      window.date_to ?? null,
      limit,
      offset,
    ] as const,
  workoutDetail: (activityId: number | null) => ['dashboard', 'workout-detail', activityId] as const,
  adherence: (window: DashboardWindowQuery) =>
    ['dashboard', 'adherence', window.window, window.date_from ?? null, window.date_to ?? null] as const,
  /** M97 F4: `goal` = objetivo da progressão (`null` = o default do backend). */
  exerciseProgress: (
    exerciseId: number | null,
    window: DashboardWindowQuery,
    goal: TrainingGoalValue | null = null,
  ) =>
    [
      'dashboard',
      'exercise-progress',
      exerciseId,
      window.window,
      window.date_from ?? null,
      window.date_to ?? null,
      goal,
    ] as const,
  /** M97 F4: painel de força (compostos principais, top set). */
  strengthPanel: (window: DashboardWindowQuery) =>
    ['dashboard', 'strength-panel', window.window, window.date_from ?? null, window.date_to ?? null] as const,
  overview: (window: DashboardWindowQuery) =>
    ['dashboard', 'overview', window.window, window.date_from ?? null, window.date_to ?? null] as const,
  insights: (window: DashboardWindowQuery, limit: number, offset: number) =>
    [
      'dashboard',
      'insights',
      window.window,
      window.date_from ?? null,
      window.date_to ?? null,
      limit,
      offset,
    ] as const,
  body: (window: DashboardWindowQuery) =>
    ['dashboard', 'body', window.window, window.date_from ?? null, window.date_to ?? null] as const,
  /** Prefixo comum das consultas que uma medida nova invalida (`body` + lista). */
  bodyAll: () => ['dashboard', 'body'] as const,
  bodyMeasurements: (site: string | null, limit: number) =>
    ['dashboard', 'body', 'measurements', site, limit] as const,
  cardio: (window: DashboardWindowQuery) =>
    ['dashboard', 'cardio', window.window, window.date_from ?? null, window.date_to ?? null] as const,
  context: (window: DashboardWindowQuery) =>
    ['dashboard', 'context', window.window, window.date_from ?? null, window.date_to ?? null] as const,
  /** Prefixo comum das consultas que um evento novo invalida (timeline + lista). */
  contextAll: () => ['dashboard', 'context'] as const,
  contextEvents: (eventType: string | null, limit: number) =>
    ['dashboard', 'context', 'events', eventType, limit] as const,
  /** Prefixo comum das consultas que um check-in novo invalida (lista + insights). */
  checkinAll: () => ['dashboard', 'checkin'] as const,
  checkins: (dateFrom: string | null, limit: number) =>
    ['dashboard', 'checkin', 'list', dateFrom, limit] as const,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value != null && value !== '') search.set(key, String(value));
  }
  const serialized = search.toString();
  return serialized ? `?${serialized}` : '';
}

/**
 * Ids opcionais que a tela só consulta quando algo está selecionado. O
 * `enabled` do `useQuery` garante que a função não roda com `null`; este
 * guard transforma um eventual descuido em erro claro em vez de uma URL com
 * `mapping_id=null`.
 */
function required(name: string, value: number | null | undefined): number {
  if (value == null) throw new Error(`${name} é obrigatório para esta consulta`);
  return value;
}

// ---------------------------------------------------------------------------
// v3 — /analytics/dashboard/*
// ---------------------------------------------------------------------------

export function fetchDataQuality(window: DashboardWindowQuery, examplesLimit?: number) {
  return apiFetch<DataQualityResponse>(
    `/analytics/dashboard/data-quality${query({
      window: window.window,
      date_from: window.date_from,
      date_to: window.date_to,
      examples_limit: examplesLimit,
    })}`,
  );
}

/**
 * "Estou evoluindo neste exercício?" (`/dashboard/exercicios/[exerciseId]`).
 *
 * `exerciseId` é o id CANÔNICO (`exercise_catalog`) — o mesmo que
 * `GET /mapping-canonical/options` devolve em `items[].id` e que
 * `dashboard-muscle-drilldown` devolve em `top_exercises[].mapping_id`.
 *
 * `window=cycle` passou a ser aceita por este endpoint na F1.1 (o tamanho do
 * ciclo vem do Modelo atribuído); sem Modelo ele responde 400 com a mensagem
 * do que fazer, exibida pelo `ErrorState` da tela.
 */
export function fetchExerciseProgress(
  exerciseId: number | null,
  window: DashboardWindowQuery,
  limit?: number,
  goal?: TrainingGoalValue | null,
) {
  return apiFetch<ExerciseProgressResponse>(
    `/analytics/dashboard/exercises/${required('exercise_id', exerciseId)}/progress${query({
      window: window.window,
      date_from: window.date_from,
      date_to: window.date_to,
      limit,
      goal,
    })}`,
  );
}

/**
 * Painel de força (M97 F4): e1RM (linha única entre objetivos, sem deload) dos
 * compostos principais e o top set das sessões de força. A seleção dos
 * compostos é do backend (`strength_panel.py`); sem sessão de força, `lifts: []`.
 */
export function fetchStrengthPanel(window: DashboardWindowQuery) {
  return apiFetch<StrengthPanelResponse>(
    `/analytics/dashboard/strength-panel${query({
      window: window.window,
      date_from: window.date_from,
      date_to: window.date_to,
    })}`,
  );
}

/**
 * "Quais grupos estão atrasados ou em excesso, e o volume chegou no músculo
 * certo?" (`/dashboard/musculos`).
 *
 * `includeReadiness` vem LIGADO por padrão desde a F1.5: a prontidão custava
 * ~4s a mais (o modelo lia dois datasets inteiros de séries por chamada) e a
 * M20 derrubou esse custo. A tela ainda a pede numa consulta SEPARADA, para a
 * tabela pintar sem esperar pelo bloco.
 *
 * `includeComparatives` (M34) traz o bloco POR SESSÃO (T-0..T-3, por
 * equipamento e mapa de calor) ao custo de UMA consulta a mais no backend.
 * Vem DESLIGADO: só a primeira chamada da tela o usa — pedi-lo também na
 * chamada da prontidão pagaria a consulta duas vezes pelo mesmo resultado.
 */
export function fetchMuscles(
  window: DashboardWindowQuery,
  includeReadiness = true,
  includeComparatives = false,
) {
  return apiFetch<MusclesResponse>(
    `/analytics/dashboard/muscles${query({
      window: window.window,
      date_from: window.date_from,
      date_to: window.date_to,
      include_readiness: includeReadiness ? 'true' : undefined,
      include_comparatives: includeComparatives ? 'true' : undefined,
    })}`,
  );
}

/** Drill-down de um grupo: exercícios → sessões → séries de origem. */
export function fetchMuscleDetail(muscleGroupId: number | null, window: DashboardWindowQuery) {
  return apiFetch<MuscleDrilldownResponse>(
    `/analytics/dashboard/muscles/${required('muscle_group_id', muscleGroupId)}${query({
      window: window.window,
      date_from: window.date_from,
      date_to: window.date_to,
    })}`,
  );
}

/**
 * "Cumpri o planejado?" — sessões da janela com planejado x executado
 * resumido (`/dashboard/treinos`, F1.1).
 *
 * Único endpoint paginado do hub (§10): o número de sessões cresce sem teto e
 * a resposta da Data API tem limite de 1 MB.
 */
export function fetchWorkoutsV3(window: DashboardWindowQuery, limit = 20, offset = 0) {
  return apiFetch<WorkoutsResponse>(
    `/analytics/dashboard/workouts${query({
      window: window.window,
      date_from: window.date_from,
      date_to: window.date_to,
      limit,
      offset,
    })}`,
  );
}

/** Uma sessão item a item: prescrito x executado e as séries de origem. */
export function fetchWorkoutDetail(activityId: number | null) {
  return apiFetch<WorkoutDetailResponse>(
    `/analytics/dashboard/workouts/${required('activity_id', activityId)}`,
  );
}

/**
 * Bloco "Consistência": ciclo atual, sessões esperadas/realizadas/puladas e a
 * próxima do ciclo. Sem Modelo atribuído responde 200 com `rate: null`.
 */
export function fetchAdherence(window: DashboardWindowQuery) {
  return apiFetch<AdherenceResponse>(
    `/analytics/dashboard/adherence${query({
      window: window.window,
      date_from: window.date_from,
      date_to: window.date_to,
    })}`,
  );
}

/**
 * A visão geral do hub (`/dashboard`, F1.6): 6 blocos + "Próxima ação".
 *
 * Um único endpoint: o backend compõe aderência, progressão em lote,
 * músculos, corpo, recuperação e as regras de insight numa chamada (cada
 * serviço uma vez). Nada é recalculado aqui.
 */
export function overviewPath(window: DashboardWindowQuery): string {
  return `/analytics/dashboard/overview${query({
    window: window.window,
    date_from: window.date_from,
    date_to: window.date_to,
  })}`;
}

export function fetchOverview(window: DashboardWindowQuery) {
  return apiFetch<OverviewResponse>(overviewPath(window));
}

/**
 * Insights disparados na janela (`/dashboard/insights`), paginados.
 *
 * F2.2 — cooldown: por padrão a lista traz só o que o usuário ainda NÃO viu (ou
 * viu e piorou de severidade); `suppressed_total` diz quantos ficaram de fora e
 * `includeSuppressed` acrescenta-os ao fim, cada um com o bloco `cooldown`.
 */
export function fetchInsights(
  window: DashboardWindowQuery,
  limit = 20,
  offset = 0,
  includeSuppressed = false,
) {
  return apiFetch<InsightsResponse>(
    `/analytics/dashboard/insights${query({
      window: window.window,
      date_from: window.date_from,
      date_to: window.date_to,
      limit,
      offset,
      include_suppressed: includeSuppressed ? 'true' : null,
    })}`,
  );
}

/**
 * "Como está o corpo?" (`/dashboard/corpo`, F1.4): peso com média móvel de 7
 * dias, cintura e demais medidas por fita, bioimpedância (ESTIMATED), sinal
 * de recomposição (INFERRED, janela própria de 28 dias) e e1RM médio dos
 * exercícios comparáveis para o gráfico alinhado no tempo.
 *
 * Sucede `/evolucao-corporal`, aposentada na F2.4: aquele caminho agora
 * redireciona para `/dashboard/corpo`. A série histórica bruta da balança
 * (IMC, visceral e as demais colunas) continua em `/indicadores-fisicos`.
 */
export function fetchBody(window: DashboardWindowQuery) {
  return apiFetch<BodyResponse>(
    `/analytics/dashboard/body${query({
      window: window.window,
      date_from: window.date_from,
      date_to: window.date_to,
    })}`,
  );
}

// ---------------------------------------------------------------------------
// api-core — CRUD `/body-measurements` (entrada de dado do /dashboard/corpo)
// ---------------------------------------------------------------------------

export function fetchBodyMeasurements(site: string | null = null, limit = 200) {
  return apiFetch<BodyMeasurementsListResponse>(`/body-measurements${query({ site, limit })}`);
}

/** Upsert por `(local_date, site)`: repetir a cintura do dia corrige, não duplica. */
export function createBodyMeasurement(input: BodyMeasurementInput) {
  return apiFetch<BodyMeasurementWriteResponse>('/body-measurements', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function deleteBodyMeasurement(id: number) {
  return apiFetch<{ status: string; id: number }>(`/body-measurements/${id}`, { method: 'DELETE' });
}

/**
 * "O que mais estava acontecendo?" (`/dashboard/contexto`, F2.1): a timeline
 * de eventos manuais (`context_events`, migração 048) unida aos DERIVADOS do
 * histórico (troca de ficha e de academia), que o backend calcula na leitura.
 *
 * Só leitura: o registro manual é do CRUD `/context-events` (api-core).
 */
export function fetchContext(window: DashboardWindowQuery) {
  return apiFetch<ContextResponse>(
    `/analytics/dashboard/context${query({
      window: window.window,
      date_from: window.date_from,
      date_to: window.date_to,
    })}`,
  );
}

/**
 * "Quanto de cardio eu fiz, e com que FC eu treinei?" (`/dashboard/cardio`,
 * F2.3): as atividades de cardio do CACHE do Intervals.icu
 * (`intervals_activities`, mantido pelo job agendado) mais a FC de cada
 * sessão de treino, lida de `activity_intervals_link` (migração 041, M2).
 *
 * É a única leitura do Intervals para cardio (M98 C4): o endpoint AO VIVO
 * `/integrations/intervals/cardio-dashboard` e a tela `/cardio` saíram. A
 * resposta é do cache, e a data da última sincronização vem declarada no
 * payload.
 */
export function fetchCardio(window: DashboardWindowQuery) {
  return apiFetch<CardioResponse>(
    `/analytics/dashboard/cardio${query({
      window: window.window,
      date_from: window.date_from,
      date_to: window.date_to,
    })}`,
  );
}

// ---------------------------------------------------------------------------
// api-core — CRUD `/context-events` (entrada de dado do /dashboard/contexto)
// ---------------------------------------------------------------------------

/** INSERT, nunca upsert: o mesmo tipo no mesmo dia pode acontecer duas vezes. */
export function createContextEvent(input: ContextEventInput) {
  return apiFetch<ContextEventWriteResponse>('/context-events', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function deleteContextEvent(id: number) {
  return apiFetch<{ status: string; id: number }>(`/context-events/${id}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// api-core — CRUD `/daily-checkins` (entrada de dado do /dashboard/checkin)
// ---------------------------------------------------------------------------

export function fetchDailyCheckins(dateFrom: string | null = null, limit = 90) {
  return apiFetch<DailyCheckinsListResponse>(
    `/daily-checkins${query({ date_from: dateFrom, limit })}`,
  );
}

/**
 * UPSERT por `(user_id, local_date)`: repetir o check-in do dia CORRIGE.
 *
 * Campo omitido não é apagado — o backend só atualiza as colunas enviadas.
 * Para apagar um escore existe o PUT com `null` explícito.
 */
export function saveDailyCheckin(input: DailyCheckinInput) {
  return apiFetch<DailyCheckinWriteResponse>('/daily-checkins', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function deleteDailyCheckin(id: number) {
  return apiFetch<{ status: string; id: number }>(`/daily-checkins/${id}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// v2 — visão geral antiga (/dashboard até a F1.6). `dashboard-alerts` saiu no
// M76 Lote 2c e `dashboard-overview` no COD-05 (2026-10-09), ambas sem
// consumidor.
// ---------------------------------------------------------------------------

export function fetchTrainingTrend(days: number) {
  return apiFetch<TrainingTrendResponse>(`/analytics/training-trend${query({ period: 'week', days })}`);
}

export function fetchRpeDistribution(days: number) {
  return apiFetch<RpeDistributionResponse>(`/analytics/rpe-distribution${query({ days })}`);
}

/**
 * Payload agregado de `/treinos` (resumo da semana, volume por grupo,
 * destaques, recuperação e correlação com o Intervals.icu). Em
 * `/dashboard/treinos` só o `weekly_summary` é lido daqui.
 */
export function fetchTrainingEvolution(days: number) {
  return apiFetch<TrainingEvolutionResponse>(`/analytics/training-evolution${query({ days })}`);
}

/**
 * Análise de treinos por IA: `POST /workouts-analysis/generate` responde 202
 * `{ job_id }` e o resultado (`{ model, insight, training }`) vem do polling
 * em `GET /jobs/{job_id}`. Mesmo contrato consumido por `/treinos`.
 */
export function generateWorkoutsAnalysis(days: number) {
  const payload: WorkoutsAnalysisGenerateRequest = { days };
  return dispatchAndPollJob<WorkoutsAnalysisGenerateResponse>('/workouts-analysis/generate', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// v2 — sub-rotas do hub
// ---------------------------------------------------------------------------

/** v3 (F1.5): prontidao do modelo + sinais objetivos contra a baseline pessoal. */
export function fetchRecoverySignals(window: DashboardWindowQuery) {
  return apiFetch<RecoverySignalsResponse>(
    `/analytics/dashboard/recovery-signals${query({
      window: window.window,
      date_from: window.date_from,
      date_to: window.date_to,
    })}`,
  );
}

/** `dashboard-cycle` não aceita período: a janela vem do próprio modelo. */
export function fetchCycle() {
  return apiFetch<DashboardCycleResponse>('/analytics/dashboard-cycle');
}

/**
 * As três rotas v2 de `/dashboard/progressao` recebem `days` e, na janela
 * `custom` válida, `date_from`/`date_to` (M61) — `query()` omite as datas
 * quando ausentes, então as janelas pré-definidas mandam o mesmo de sempre.
 */
export function fetchProgression(
  mappingId: number | null,
  range: DashboardDayRange,
  goal?: TrainingGoalValue | null,
) {
  return apiFetch<DashboardProgressionResponse>(
    `/analytics/dashboard-progression${query({ mapping_id: required('mapping_id', mappingId), ...range, goal })}`,
  );
}

export function fetchProgressionTrend(
  mappingId: number | null,
  range: DashboardDayRange,
  goal?: TrainingGoalValue | null,
) {
  return apiFetch<DashboardProgressionTrendResponse>(
    `/analytics/dashboard-progression-trend${query({
      mapping_id: required('mapping_id', mappingId),
      ...range,
      goal,
    })}`,
  );
}

/**
 * M97 F4: `goal` opcional — a sugestão na trilha/escada daquele objetivo (motor
 * de prescrição). Sem `goal`, a URL e a resposta de sempre (objetivo default).
 */
export function fetchProgressionSuggestion(mappingId: number | null, goal?: TrainingGoalValue | null) {
  return apiFetch<DashboardProgressionSuggestionResponse>(
    `/analytics/dashboard-progression-suggestion${query({
      mapping_id: required('mapping_id', mappingId),
      goal,
    })}`,
  );
}

/**
 * Mesma janela do overview (`window` + datas no `custom`): a home e
 * `/progressao` precisam resolver o MESMO período, inclusive `cycle`.
 */
export function progressionByMuscleGroupPath(window: DashboardWindowQuery): string {
  return `/analytics/dashboard-progression-by-muscle-group${query({
    window: window.window,
    date_from: window.date_from,
    date_to: window.date_to,
  })}`;
}

export function fetchProgressionByMuscleGroup(window: DashboardWindowQuery) {
  return apiFetch<MuscleGroupProgressionResponse>(progressionByMuscleGroupPath(window));
}

/**
 * v3 (F3.1): as GERAÇÕES de ficha da janela — o seletor de `/dashboard/engine`.
 *
 * Duas fontes com preferência declarada: `generation_runs` (migração 054, que
 * registra toda geração desde 2026-09-26, inclusive a de rascunho descartado)
 * e, para as fichas anteriores, o `generation_audit` ecoado no `raw_payload`.
 * Cada linha diz de qual fonte veio — do `raw_payload` não sai data de geração.
 *
 * Substitui `fetchEngineAudit` (v2), que respondia só o orçamento por grupo. A
 * rota v2 continua existindo com telemetria de legado: medir antes de remover.
 */
export function fetchEngineRuns(window: DashboardWindowQuery) {
  return apiFetch<EngineRunsResponse>(
    `/analytics/dashboard/engine${query({
      window: window.window,
      date_from: window.date_from,
      date_to: window.date_to,
    })}`,
  );
}

/** v3 (F3.1): planejado × gerado × executado de UMA ficha, item a item. */
export function fetchEnginePlan(planId: number | null) {
  return apiFetch<EnginePlanResponse>(
    `/analytics/dashboard/engine/plans/${required('plan_id', planId)}`,
  );
}

// ---------------------------------------------------------------------------
// Apoio (seletores das telas do hub)
// ---------------------------------------------------------------------------

export function fetchExerciseOptions() {
  return apiFetch<MappingCanonicalOptionsResponse>('/mapping-canonical/options');
}

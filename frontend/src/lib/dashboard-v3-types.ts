/**
 * Tipos do Novo Dashboard (`/analytics/dashboard/*`, plano §10) — espelho dos
 * contratos Pydantic de `api/app/intelligence/dashboard/contracts.py`.
 *
 * Separado de `dashboard-v2-types.ts` de propósito: as rotas v2
 * (`/analytics/dashboard-*`, com hífen) devolvem `dict` livre e continuam
 * valendo até o hub migrar (§17, F2.4). Aqui todo payload vem de um
 * `response_model`, então o tipo é o contrato — não um chute sobre o JSON.
 *
 * Nada neste arquivo recalcula regra: severidade, contagem, texto e `overall`
 * chegam prontos do backend (`metrics_registry.DATA_QUALITY_RULES`).
 */

import type { LoadConvention } from './equipment-exercises-types';

export type DataNature = 'OBSERVED' | 'DERIVED' | 'PLANNED' | 'PROJECTED' | 'ESTIMATED' | 'INFERRED';

export type DataQuality = 'HIGH' | 'MEDIUM' | 'LOW' | 'INSUFFICIENT';

export type Severity = 'INFO' | 'ATTENTION' | 'ACTION';

/** `window` = conta dentro do período; `catalog` = cadastro, ignora o período. */
export type DataQualityScope = 'window' | 'catalog';

export type DashboardWindow = {
  kind: string;
  date_from: string;
  date_to: string;
  timezone: string;
  days: number;
};

export type DataQualityExample = {
  id: number | null;
  label: string;
  detail: string | null;
};

export type DataQualityItem = {
  code: string;
  label: string;
  description: string;
  count: number;
  severity: Severity;
  scope: DataQualityScope;
  fix_link: string;
  examples: DataQualityExample[];
  limitations: string[];
};

export type DataQualityResponse = {
  window: DashboardWindow;
  items: DataQualityItem[];
  /** Veredito sobre os DADOS do usuário (derivado das severidades). */
  overall: DataQuality;
  /** Confiança na PRÓPRIA resposta (cai quando uma regra não pôde rodar). */
  data_quality: DataQuality;
  checked_rules: number;
  limitations: string[];
  metric_versions: Record<string, string>;
};

/**
 * Envelope semântico de um valor exibido (plano §8.4, `contracts.Metric`).
 *
 * `value: null` = NÃO calculável — a UI mostra "—", nunca `0` (que significa
 * "observado zero"). `nature` diz de onde o número veio e `limitations` diz o
 * que ele não permite concluir. Renderizado por `components/dashboard/MetricValue`.
 */
export type Metric = {
  value: number | null;
  unit: string | null;
  nature: DataNature;
  metric_version: string;
  data_quality: DataQuality;
  limitations: string[];
};

// ---------------------------------------------------------------------------
// GET /analytics/dashboard/exercises/{exercise_id}/progress (F0.2 → tela F1.3)
// Espelho de `contracts.ExerciseProgressResponse`.
// ---------------------------------------------------------------------------

/** Espelho de `contracts.ComparabilityKeyOut` (plano §8.1). */
export type ComparabilityKey = {
  canonical_exercise_id: number | null;
  equipment_id: number | null;
  /** Vocabulário de `api/app/load_conventions.LoadConvention`. */
  load_convention: LoadConvention;
  /**
   * Única situação em que comparar SEM equipamento é permitido: nenhuma
   * exposição do exercício tem equipamento resolvido (§8.1).
   */
  equipment_agnostic: boolean;
  /**
   * Faixa de fadiga prévia da exposição (dono `domain/prior_fatigue.py`);
   * `null`/ausente = contexto não medido. Opcional: backend anterior não envia.
   */
  fatigue_context?: FatigueBand | null;
};

/** Faixas do dono `domain/prior_fatigue.py` (`PriorFatigueBand`). */
export type FatigueBand = 'rested' | 'pre_fatigued' | 'heavily_fatigued';

/**
 * Contexto de fadiga prévia de uma exposição na sessão REAL — pronto do
 * backend (`exposure_service.fatigue_context_payload`). A tela só mostra
 * `band_label`; nenhum número é comparado aqui.
 */
export type FatigueContext = {
  band: FatigueBand;
  band_label: string | null;
  muscle_group_id: number | null;
  muscle_group_name: string | null;
  prior_sets: number;
};

/** Uma série `working` como foi registrada (OBSERVED). */
export type ExerciseSetOut = {
  set_number: number | null;
  reps: number | null;
  weight_kg: number | null;
  rpe: number | null;
  rest_seconds: number | null;
  notes: string | null;
  /** Medida da série (Lote T3; opcional: backend anterior não envia). */
  measure?: string | null;
  duration_seconds?: number | null;
  /** Texto pronto do dono ("10 reps" / "58 s" / "15 reps em 30 s"). */
  measure_text?: string | null;
};

/**
 * Uma execução do exercício dentro de uma sessão (métrica `exposure`).
 *
 * `e1rm` é `null` — NUNCA `0` — quando a série não tem RPE ou a convenção de
 * carga não permite estimar 1RM (`assisted`/`bodyweight`).
 */
export type Exposure = {
  activity_id: number | null;
  local_date: string | null;
  started_at: string | null;
  equipment_id: number | null;
  equipment_name: string | null;
  load_convention: LoadConvention;
  /** `equipment_exercises` = cadastrada; `default_total` = assumida (D12). */
  load_convention_source: 'equipment_exercises' | 'default_total' | string;
  position_in_session: number | null;
  /** Contexto de fadiga prévia na sessão real (opcional: backend anterior não envia). */
  fatigue_context?: FatigueContext | null;
  sets: ExerciseSetOut[];
  total_reps: number | null;
  best_load_kg: number | null;
  avg_rpe: number | null;
  e1rm: number | null;
  tonnage: number | null;
  comparability_key: ComparabilityKey;
  /** Reps da série que gerou o e1RM (opcional: backend anterior não envia). */
  e1rm_reps?: number | null;
  /** e1RM confiável (série de até 12 reps) — o mesmo teto da regra. */
  e1rm_reliable?: boolean;
  /** Série por tempo/máximo de reps (Lote T3): fora dos agregados; não comparável. */
  has_non_rep_sets?: boolean;
  /**
   * Comparável com a chave ATUAL (a da última exposição) e, se não, por quê —
   * pronto do backend (`comparability.mismatch_with_current`, M76 Lote 3c).
   */
  comparable_with_current?: boolean;
  mismatch_with_current?:
    | 'equipment'
    | 'load_convention'
    | 'equipment_unknown'
    | 'fatigue_context_changed'
    | null;
};

/**
 * Reps POR SÉRIE emparelhada nas cargas de topo — o que
 * `comparability.compare_exposures` compara (N = menor número de séries de
 * topo dos dois lados). Vem pronto do backend; a tela não recalcula.
 *
 * M78: com `effort_adjusted`, `previous`/`current`/somas/`delta` são
 * `reps + RIR` por série (pode ter meia rep); `previous_reps`/`current_reps`
 * são as reps registradas das mesmas séries.
 */
export type PairedReps = {
  pairs: number;
  previous: number[];
  current: number[];
  previous_sum: number;
  current_sum: number;
  delta: number;
  effort_adjusted?: boolean;
  previous_reps?: number[];
  current_reps?: number[];
};

/** As duas últimas exposições com os números que a regra usou. */
export type LatestComparison = {
  comparable: boolean;
  paired_reps: PairedReps | null;
  /** e1RM decidiu sozinho? (séries do e1RM com até 12 reps nos dois lados) */
  e1rm_decisive: boolean;
  e1rm_max_reliable_reps: number;
};

/** Melhor marca DENTRO da mesma `comparability_key`. */
export type ExerciseRecord = {
  value: number | null;
  activity_id: number | null;
  local_date: string | null;
  /**
   * Só em `best_e1rm` (M76 Lote 3d): `true` = e1RM confiável (série com até
   * 12 reps); `false` = a chave não tem nenhum confiável e o valor veio de
   * série com mais de 12 reps (a tela marca "não confiável (>12 reps)").
   * Ausente em resposta antiga.
   */
  reliable?: boolean | null;
  /** Teto de reps do e1RM confiável (dono: `domain/e1rm.py`). Só em `best_e1rm`. */
  max_reliable_reps?: number | null;
};

export type ExerciseRecords = {
  best_e1rm: ExerciseRecord | null;
  best_load_kg: ExerciseRecord | null;
};

/** Decisão do motor de progressão v2, anexada como EVIDÊNCIA (§8.2). */
export type ProgressionDecision = {
  id: number;
  kind: string | null;
  decision: string | null;
  phase_before: string | null;
  phase_after: string | null;
  engine_version: string | null;
  created_at: string | null;
  equipment_id: number | null;
};

export type ExerciseRef = {
  exercise_id: number;
  name: string | null;
  amazfit_name: string | null;
};

export type ExerciseProgressResponse = {
  window: DashboardWindow;
  exercise: ExerciseRef;
  /** Chave da ÚLTIMA exposição; `null` quando não houve exposição na janela. */
  comparability_key: ComparabilityKey | null;
  /** Código de `ExerciseProgressionCode` (`lib/dashboard-status.ts`). */
  status: string;
  /** Códigos `comparability.REASON_*`; texto em `lib/exercise-progress.ts`. */
  status_reasons: string[];
  /** Contexto de fadiga em que o status vale (o da exposição mais recente). */
  fatigue_context?: FatigueContext | null;
  /** Ordem CRONOLÓGICA crescente (mais antiga primeiro). */
  exposures: Exposure[];
  /** Opcional: backend anterior a 2026-10-06 não envia. */
  latest_comparison?: LatestComparison | null;
  records: ExerciseRecords;
  /** M97 F4: objetivo da progressão lida (`?goal=`; ausente = hipertrofia). */
  training_goal?: TrainingGoalValue | null;
  /** M97 F4: linha ÚNICA de e1RM (todos os objetivos, sem deload). */
  e1rm_series?: E1rmPoint[];
  /** M97 F4: sessões de deload do exercício na janela (só a marca). */
  deload_sessions?: DeloadSession[];
  progression_decisions: ProgressionDecision[];
  data_quality: DataQuality;
  limitations: string[];
  metric_versions: Record<string, string>;
};

/** Objetivo de treino: valor do vocabulário vindo de `GET /training-goals` (dono `domain/training_goal.py`). */
export type TrainingGoalValue = string;

/** Ponto da linha única de e1RM (M97 F4): uma exposição de qualquer objetivo. */
export type E1rmPoint = {
  activity_id: number | null;
  local_date: string | null;
  e1rm: number;
  e1rm_reps: number | null;
  e1rm_reliable: boolean;
  training_goal: TrainingGoalValue;
  /** Mesma chave física do recorde (mesmo equipamento/convenção). */
  same_physical_key: boolean;
};

/** Sessão de deload (M97 F4): fora do e1RM/recorde; o gráfico só a marca. */
export type DeloadSession = {
  activity_id: number | null;
  local_date: string | null;
  deload_source: string;
  deload_label?: string | null;
};

/** Top set de uma sessão de força (`GET /analytics/dashboard/strength-panel`). */
export type StrengthTopSet = {
  activity_id: number | null;
  local_date: string | null;
  load_kg: number | null;
  reps: number | null;
  top_sets_count: number;
  avg_rpe: number | null;
  e1rm: number | null;
  e1rm_reliable: boolean;
};

export type StrengthLift = {
  exercise_id: number;
  name: string | null;
  strength_sessions: number;
  e1rm_series: E1rmPoint[];
  top_sets: StrengthTopSet[];
  best_e1rm: ExerciseRecord | null;
};

/** `GET /analytics/dashboard/strength-panel` (M97 F4). */
export type StrengthPanelResponse = {
  window: DashboardWindow;
  training_goal: TrainingGoalValue;
  training_goal_label: string;
  max_exercises: number;
  lifts: StrengthLift[];
  data_quality: DataQuality;
  limitations: string[];
  metric_versions: Record<string, string>;
};

// ---------------------------------------------------------------------------
// F1.2 — `/analytics/dashboard/muscles` e `/muscles/{muscle_group_id}`
//
// Regra estrutural do contrato: `realized`, `planned` e `projected` são três
// campos SEPARADOS e nunca somados na mesma métrica exibida. `status`
// classifica só `realized` (`status_basis: 'realized_only'`).
// ---------------------------------------------------------------------------

/**
 * `template_plan`: o Modelo atribuído é por equipamento (`equipment_series`) e
 * a meta é a ativação planejada dele — `min`/`max` vêm `null` (plano fixo não
 * tem faixa) e o `status` usa os TRÊS estados de `plannedVolume`.
 */
export type TargetSource = 'explicit' | 'assigned_template' | 'template_plan' | 'fallback_generic';

export type MuscleRole = 'PRIMARY' | 'SECONDARY' | 'STABILIZER';

export type MuscleTargets = {
  min: Metric;
  target: Metric;
  max: Metric;
  source: TargetSource;
  training_type_id: number | null;
};

export type MuscleRealized = {
  physical_sets: Metric;
  effective_sets_stimulus: Metric;
  effective_sets_fatigue: Metric;
  direct_sets: Metric;
  indirect_sets: Metric;
  stabilizer_sets: Metric;
  weekly_effective_sets: Metric;
  avg_rpe: Metric;
};

export type MusclePlanned = {
  effective_sets: Metric;
  weekly_effective_sets: Metric;
  plans_count: number;
};

/** Séries que ainda faltam no CICLO — não é taxa semanal e não entra no status. */
export type MuscleProjected = {
  remaining_sets: Metric;
  pending_slots: number;
  cycle_length_days: number | null;
};

export type MuscleFrequency = {
  direct_sessions: Metric;
  participation_sessions: Metric;
  median_interval_hours: Metric;
};

export type MuscleReadiness = {
  status: string | null;
  guidance: string | null;
  estimated_recovery_hours: Metric;
  hours_since_last_stimulus: Metric;
  explanation: string[];
};

export type MuscleTopExercise = {
  /** Id CANÔNICO (`exercise_catalog`) — o mesmo de `/dashboard/exercicios/[id]`. */
  exercise_id: number | null;
  name: string;
  sets_count: number;
  effective_sets_stimulus: number;
  /** Fração do estímulo do grupo que veio deste exercício. */
  contribution_share: number | null;
  roles: MuscleRole[];
  last_local_date: string | null;
};

export type MuscleItem = {
  muscle_group_id: number;
  muscle_group: string;
  targets: MuscleTargets;
  realized: MuscleRealized;
  planned: MusclePlanned;
  projected: MuscleProjected;
  frequency: MuscleFrequency;
  readiness: MuscleReadiness | null;
  last_stimulus_local_date: string | null;
  last_direct_local_date: string | null;
  hours_since_last_stimulus: Metric;
  status: string;
  status_basis: string;
  top_exercises: MuscleTopExercise[];
  data_quality: DataQuality;
  limitations: string[];
};

/**
 * F2.5 / M34 — comparativos POR SESSÃO.
 *
 * **Unidade diferente do resto de `MusclesResponse`.** Todo o resto compara
 * por JANELA (28/90/180 dias, normalizado para taxa semanal); aqui T-0 é a
 * ÚLTIMA SESSÃO em que o grupo apareceu, T-1 a anterior. Duas sessões
 * seguidas podem cair no mesmo dia, e uma pausa de três semanas não cria
 * buraco nenhum. Ler esses números como "semana passada" leva a decisão
 * errada — por isso a tela rotula os dois blocos.
 */
export type MuscleHeatmapBand = 'NONE' | 'LOW' | 'MODERATE' | 'HIGH' | 'VERY_HIGH';

export type MuscleSessionPoint = {
  /** 0 = T-0 (a sessão mais recente do grupo). */
  offset: number;
  activity_id: number;
  activity_name: string | null;
  local_date: string | null;
  physical_sets: number;
  effective_sets_stimulus: number;
  /** `null` quando nenhuma série registrou carga — nunca 0 kg inventado. */
  tonnage_kg: number | null;
  /** Só séries `working`: aquecimento e ajuste ficam fora. */
  avg_rpe: number | null;
  has_direct_work: boolean;
};

export type MuscleSessionComparative = {
  muscle_group_id: number;
  muscle_group: string;
  points: MuscleSessionPoint[];
  /** Sessões do grupo na janela inteira — `points` menor é RECORTE. */
  sessions_in_window: number;
};

export type MuscleEquipmentPoint = {
  /** Indexa as sessões DO GRUPO, para as colunas se alinharem. */
  offset: number;
  activity_id: number;
  local_date: string | null;
  physical_sets: number;
  effective_sets_stimulus: number;
  tonnage_kg: number | null;
  avg_rpe: number | null;
};

export type MuscleEquipmentRow = {
  /** `null` = a sessão não registrou equipamento. */
  equipment_name: string | null;
  points: MuscleEquipmentPoint[];
  effective_sets_stimulus: number;
  share_of_group: number | null;
};

export type MuscleEquipmentComparative = {
  muscle_group_id: number;
  muscle_group: string;
  equipments: MuscleEquipmentRow[];
  equipment_limit: number;
  equipment_truncated: boolean;
};

export type MuscleHeatmapCell = {
  muscle_group_id: number;
  muscle_group: string;
  physical_sets: number;
  effective_sets_stimulus: number;
  tonnage_kg: number | null;
  avg_rpe: number | null;
  /** Razão contra a maior célula da MESMA sessão. */
  share_of_max: number | null;
  /** Já CLASSIFICADA pelo backend — o frontend só pinta. */
  band: MuscleHeatmapBand;
};

export type MuscleHeatmapSession = {
  activity_id: number;
  activity_name: string | null;
  local_date: string | null;
  max_effective_sets_stimulus: number;
  cells: MuscleHeatmapCell[];
};

export type MuscleComparatives = {
  unit: 'session';
  sessions_compared: number;
  by_session: MuscleSessionComparative[];
  by_equipment: MuscleEquipmentComparative[];
  heatmap: MuscleHeatmapSession[];
  data_quality: DataQuality;
  limitations: string[];
};

export type MusclesResponse = {
  window: DashboardWindow;
  items: MuscleItem[];
  /** `null`/ausente quando `include_comparatives=false` — "não foi pedido". */
  comparatives?: MuscleComparatives | null;
  target_source: TargetSource;
  training_type_id: number | null;
  /** Só com `target_source = 'template_plan'`: qual Modelo deu a meta. */
  template_id?: number | null;
  template_name?: string | null;
  unattributed_sets: number;
  data_quality: DataQuality;
  limitations: string[];
  metric_versions: Record<string, string>;
};

export type MuscleSourceSet = {
  set_id: number;
  set_number: number | null;
  set_type: string;
  reps: number | null;
  weight_kg: number | null;
  rpe: number | null;
  effective_sets_stimulus: number;
  effective_sets_fatigue: number;
  role: MuscleRole;
};

export type MuscleExposure = {
  exercise_row_id: number;
  exercise_id: number | null;
  name: string;
  position_in_session: number | null;
  role: MuscleRole;
  effective_sets_stimulus: number;
  sets: MuscleSourceSet[];
};

export type MuscleSession = {
  activity_id: number;
  activity_name: string | null;
  local_date: string | null;
  started_at: string | null;
  physical_sets: number;
  effective_sets_stimulus: number;
  has_direct_work: boolean;
  exposures: MuscleExposure[];
};

export type MuscleDrilldownResponse = {
  window: DashboardWindow;
  item: MuscleItem;
  sessions: MuscleSession[];
  /** Teto de séries desta resposta (limite de 1 MB da Data API). */
  sets_limit: number;
  sets_truncated: boolean;
  target_source: TargetSource;
  training_type_id: number | null;
  template_id?: number | null;
  template_name?: string | null;
  data_quality: DataQuality;
  limitations: string[];
  metric_versions: Record<string, string>;
};

// Tipos para a tela "Volume por grupamento" (Fase 4, Etapa 12 da migração).
// Endpoint confirmado lendo diretamente:
//   - `C:\muscle_fit\api\app\analytics.py` (rota `muscle-groups-recent-exercise-volume`)
//   - `C:\muscle_fit\api\app\intelligence\volume_service.py`
//     (`VolumeService.get_recent_exercise_volume`, fonte de verdade do
//     cálculo/agregação — a mesma usada em produção pela versão legada da
//     tela, reaproveitada aqui sem duplicar a fórmula).
//
import type { VolumeGroup, VolumeGroupingLevel } from './volume-grouping';

// Grupo pai e macro-grupo do grupo da linha, resolvidos pelo backend (dono:
// `methodology_volume.resolve_group_hierarchy`, mapa FONTES_DA_VERDADE §50).
// A tela só escolhe a chave de agrupamento (`lib/volume-grouping.ts`).
export type GroupHierarchyFields = {
  /** Raiz do bloco da metodologia (a fatia soma no pai); `null` para grupo fora do cadastro. */
  parent_group_id: number | null;
  parent_group_name: string;
  /** Classe de volume do bloco; `null` no balde sem macro-grupo. */
  volume_class_id: number | null;
  /** Nome do macro-grupo ou o rótulo do balde, já preenchido pelo backend. */
  volume_class_name: string;
  /** Posição do macro-grupo no cadastro (ordem da tabela); `null` no balde. */
  volume_class_order: number | null;
};

// GET /analytics/muscle-groups-recent-exercise-volume retorna volume BRUTO
// agregado por (activity_id, muscle_group, equipment_name) — 1 linha por
// combinação, já pronta para o comparativo T-0..T-3 ser montado no cliente
// (agrupando pela chave do nível escolhido e pegando as últimas 4 sessões distintas).
export type RecentExerciseVolumeItem = GroupHierarchyFields & {
  activity_id: number;
  local_date: string;
  muscle_group: string;
  equipment_name: string | null;
  sets_count: number;
  volume_kg: number;
  avg_rpe: number | null;
  last_weight_kg: number | null;
};

export type RecentExerciseVolumeResponse = {
  limit: number | null;
  date_from: string | null;
  date_to: string | null;
  items: RecentExerciseVolumeItem[];
} & RecentVolumeBlocks;

// T-0 (mais recente) .. T-3 (mais antigo). No comparativo por sessão: T-0 =
// última sessão da chave; T-1..T-3 = as anteriores no MESMO slot do Modelo
// (escolhidas pelo backend, `slot_session_comparison`). Nas tabelas por semana
// as mesmas chaves representam S-0..S-3.
export type WorkoutPeriodKey = 't3' | 't2' | 't1' | 't0';

/** Direção pronta do backend; `null` = sem vizinho para comparar ou valor ausente. */
export type ApiTrendDirection = 'up' | 'down' | 'stable' | null;

export type WorkoutPeriodMetrics = {
  activity_id: number;
  local_date: string;
  volume_kg: number;
  avg_rpe: number | null;
  /** Contra o período seguinte (t<k> vs t<k+1>), pronto do backend; ausente na API antiga. */
  volume_direction?: ApiTrendDirection;
  rpe_direction?: ApiTrendDirection;
};

export type MuscleGroupComparativeRow = {
  /** Macro-grupo, grupo pai ou grupo, conforme o seletor "Agrupar por". */
  group: VolumeGroup;
  byPeriod: Record<WorkoutPeriodKey, WorkoutPeriodMetrics | null>;
};

// GET /analytics/muscle-groups-slot-comparison — comparativo T-0..T-3 pelo
// MESMO slot do Modelo, pronto (dono: `api/app/intelligence/slot_session_comparison.py`,
// mapa FONTES_DA_VERDADE §3.5). A chave do slot é `template_id` + `slot_sequence`;
// `label` é só exibição. A tela não escolhe sessões.
export type SlotComparisonStatus = 'same_slot' | 'no_slot';

export type SlotComparisonSlot = {
  template_id: number;
  slot_sequence: number;
  slot_id: number | null;
  slot_name: string | null;
  template_name: string | null;
  label: string;
};

export type SlotComparisonInfo = {
  /** Slot do T-0; `null` quando o T-0 não tem slot (treino livre/sem Modelo). */
  slot: SlotComparisonSlot | null;
  comparisonStatus: SlotComparisonStatus;
};

type SlotComparisonApiRow<TPeriod> = {
  group: VolumeGroup;
  comparison_status: SlotComparisonStatus;
  slot: SlotComparisonSlot | null;
  by_period: Record<WorkoutPeriodKey, TPeriod | null>;
};

export type SlotComparisonVolumePeriod = WorkoutPeriodMetrics & {
  sets_count: number;
  volume_delta_kg?: number | null;
};
export type SlotComparisonSetsPeriod = {
  activity_id: number;
  local_date: string;
  equivalent_sets: number;
  equivalent_sets_delta?: number | null;
  sets_direction?: ApiTrendDirection;
};

/** Agregados prontos do backend (cards, volume total por período). */
export type SlotComparisonTotals = {
  by_period: Record<WorkoutPeriodKey, number>;
  groups_with_t0: number;
  groups_tracked: number;
  avg_rpe_t0: number | null;
};

export type SlotComparisonTopVolumeRow = { group: VolumeGroup; t0: number; t1: number | null };

export type SlotComparisonResponse = {
  limit: number;
  sessions_per_slot: number;
  period_keys: WorkoutPeriodKey[];
  levels: Record<
    VolumeGroupingLevel,
    {
      volume: Array<SlotComparisonApiRow<SlotComparisonVolumePeriod>>;
      equivalent_sets: Array<SlotComparisonApiRow<SlotComparisonSetsPeriod>>;
      /** Ausentes na API antiga. */
      totals?: SlotComparisonTotals;
      top_volume_t0?: SlotComparisonTopVolumeRow[];
    }
  >;
};

export type SlotComparativeVolumeRow = MuscleGroupComparativeRow & SlotComparisonInfo;

// ─── Fase 3, blocos adicionais portados de
// fitness-frontend/.../TrainingVolumeClient.tsx (referência on-premise) ────

export type WorkoutSetsMetrics = {
  sets_count: number;
  /** Pronta do backend; ausente na API antiga. */
  sets_direction?: ApiTrendDirection;
};

// Mesmo shape de `MuscleGroupComparativeRow` (T-3..T-0), mas para séries
// equivalentes em vez de volume/RPE.
export type ComparativeSetsRow = {
  group: VolumeGroup;
  byPeriod: Record<WorkoutPeriodKey, WorkoutSetsMetrics | null>;
};

export type SlotComparativeSetsRow = ComparativeSetsRow & SlotComparisonInfo;

// Comparativo semanal e por equipamento (S-3..S-0), prontos do backend em
// GET /analytics/muscle-groups-recent-exercise-volume?include=weekly,equipment_weekly
// (dono: `intelligence/slot_session_comparison.py`). t3..t0 = S-3..S-0.
export type WeeklyPeriodMetrics = {
  week_start: string;
  volume_kg: number;
  sets_count: number;
  avg_rpe: number | null;
  volume_delta_kg: number | null;
  volume_direction: ApiTrendDirection;
  rpe_direction: ApiTrendDirection;
};

export type WeeklyComparisonRow = {
  group: VolumeGroup;
  by_period: Partial<Record<WorkoutPeriodKey, WeeklyPeriodMetrics | null>>;
};

export type EquipmentWeekMetrics = {
  week_start: string;
  sets_count: number;
  volume_kg: number;
  last_weight_kg: number | null;
  sets_delta: number | null;
  sets_direction: ApiTrendDirection;
  last_weight_delta_kg: number | null;
  last_weight_direction: ApiTrendDirection;
};

/** Linha Total primeiro (`is_total`); `equipment_name` nulo = sem equipamento. */
export type EquipmentWeeklyEntry = {
  equipment_name: string | null;
  is_total: boolean;
  by_period: Partial<Record<WorkoutPeriodKey, EquipmentWeekMetrics | null>>;
};

export type EquipmentWeeklyGroupRow = { group: VolumeGroup; equipments: EquipmentWeeklyEntry[] };

/** Célula pronta do mapa de calor; `volume_ratio` (0..1) = posição entre o menor e o maior volume. */
export type HeatmapCell = {
  group: VolumeGroup;
  sets_count: number;
  volume_kg: number;
  avg_weight_kg: number | null;
  volume_ratio: number | null;
};

type ByLevel<T> = Partial<Record<VolumeGroupingLevel, T>>;

export type RecentVolumeBlocks = {
  weekly?: { period_keys: WorkoutPeriodKey[]; levels: ByLevel<WeeklyComparisonRow[]> };
  equipment_weekly?: { period_keys: WorkoutPeriodKey[]; levels: ByLevel<EquipmentWeeklyGroupRow[]> };
  heatmap?: { levels: ByLevel<{ min_volume_kg: number | null; max_volume_kg: number | null; cells: HeatmapCell[] }> };
};

export type TrainingVolumeGranularity = 'd7' | 'week' | 'month';

// GET /analytics/training-volume?days=... — ver `training_volume` em
// `C:\muscle_fit\api\app\dashboard_modules.py`: `overview` via
// `VolumeService.get_volume_breakdown`, `trend` via
// `VolumeService.get_volume_trend` (clampado em [7,180] dias
// internamente). Usada pelo bloco "Gráfico de repetições + Variação por
// grupamento" desta tela: com `granularity`, o backend devolve
// `period_comparison` (janelas atual/anterior, deltas e direção prontos).
export type TrainingVolumeOverviewItem = {
  muscle_group: string;
  sessions: number;
  sets_count: number;
  reps_total: number;
  volume_kg: number;
  avg_weight_kg?: number | null;
  avg_rpe?: number | null;
  last_trained_at?: string | null;
  last_trained_at_ts?: string | null;
  days_since_trained?: number | null;
  /** Séries equivalentes/semana (janela normalizada por 7/days), unidade das Metas de Ativação. */
  weekly_equivalent_sets?: number | null;
  /** `min_weekly_sets` da Meta de Ativação do grupo; `null` sem meta (nunca "subtreino"). */
  min_weekly_sets?: number | null;
  /** `max_weekly_sets` da Meta de Ativação do grupo; `null` sem meta (nunca "excesso"). */
  max_weekly_sets?: number | null;
  volume_target_training_type_id?: number | null;
  /**
   * Pela Meta de Ativação (séries equivalentes/semana): "subtreino" < min; "excesso" > max;
   * "ideal" entre min e max (inclusivos) ou sem meta. Recência (`days_since_trained`) não entra.
   */
  volume_status: string;
};

// Só a série ganha os campos de hierarquia: status e metas do overview são por
// grupo e não se agregam por pai nem por macro-grupo.
export type TrainingVolumeTrendItem = GroupHierarchyFields & {
  period: string;
  muscle_group: string;
  sets_count: number;
  reps_total: number;
  volume_kg: number;
  avg_weight_kg?: number | null;
  avg_rpe?: number | null;
};

export type PeriodComparisonRow = {
  group: VolumeGroup;
  current_reps: number;
  previous_reps: number;
  reps_delta: number;
  current_volume_kg: number;
  previous_volume_kg: number;
  volume_delta_kg: number;
  volume_direction: ApiTrendDirection;
};

export type PeriodComparison = {
  granularity: TrainingVolumeGranularity;
  current: { from: string; to: string };
  previous: { from: string; to: string };
  levels: ByLevel<PeriodComparisonRow[]>;
};

export type TrainingVolumeResponse = {
  overview: {
    days: number;
    items: TrainingVolumeOverviewItem[];
  };
  trend: {
    days: number;
    items: TrainingVolumeTrendItem[];
  };
  /** Só com `granularity`; ausente na API antiga (a tela fica vazia). */
  period_comparison?: PeriodComparison;
};

export type HeatmapViewMode = 'session' | 'week' | 'month';

// Tipos do catálogo canônico de grupos musculares e da engine -mapped-*.
// Nasceram com a antiga tela "Grupos musculares" (Fase 4, Etapa 11 da
// migração), aposentada; hoje servem `/dashboard/musculos` (tendência) e
// `/equipamentos-exercicios` (lista do catálogo por grupo).
// Endpoints confirmados lendo diretamente:
//   - `C:\muscle_fit\api\app\muscle_groups_mapped.py` (muscle-groups-mapped-trend|-table)
//   - `C:\muscle_fit\api\app\exercise_mapping_catalog.py` (exercise-mapping-catalog)
//   - `C:\muscle_fit\api\app\exercise_mapping_canonical.py` (mapping-canonical)
// As rotas de regex no nome do exercício (/analytics/muscle-groups, -trend,
// -table) foram removidas no COD-05 (2026-10-09).

// --- GET /exercise-mapping-catalog -----------------------------------------
// Leitura vem de `exercise_catalog` (canônico). Nenhuma tela usa mais a
// escrita (POST/PUT/DELETE) deste router: a edição do catálogo é feita em
// `/dominio-exercicios`.
export type ExerciseMappingCatalogItem = {
  id: number;
  exercise_name: string;
  amazfit_name: string | null;
  equipment: string | null;
  body_position: string | null;
  movement: string | null;
  trajectory: string | null;
  biomechanical_pattern: string | null;
  primary_joint: string | null;
  resistance_vector: string | null;
  primary_group_id: number | null;
  primary_group: string | null;
  secondary_group_id: number | null;
  secondary_group: string | null;
  keywords: string | null;
  description: string | null;
  mapping_status: string | null;
  source: string | null;
  is_active: boolean | number;
  created_at?: string;
  updated_at?: string;
};

export type ExerciseMappingCatalogListResponse = {
  query: string;
  primary_group: string;
  active_only: boolean;
  limit: number;
  offset: number;
  total: number;
  items: ExerciseMappingCatalogItem[];
};

// --- Engine canônica de grupos musculares (Lambda `musclefit-api-core`) ----
// GET /analytics/muscle-groups-mapped-trend|-mapped-table — ver
// `api/app/muscle_groups_mapped.py::aggregate()`. Usada por
// `/dashboard/musculos` (tendência). Substitui as rotas de regex
// /analytics/muscle-groups|-trend|-table (payload "keyword", sem
// mapped_ratio/classificação canônica), removidas no COD-05.
export type MuscleGroupsMappedPeriod = 'daily' | 'weekly' | 'monthly' | 'quarterly' | 'yearly';
export type MuscleGroupsMappedMetric = 'sets' | 'volume' | 'reps' | 'load';

// Linha unmapped do payload -mapped- (agregada por nome de exercício, com
// contagem de séries, restrita à janela de dias) — diferente dos tipos de
// GET /mapping-canonical/unmapped em `mappings-types.ts` (sem janela).
export type MappedUnmappedRow = {
  exercise: string;
  sets: number;
};

// Campos comuns de uma linha agregada. `value` segue o parâmetro `metric`;
// `volume_kg` é sempre reps x carga (sem o fallback de reps de `metric=volume`
// para série sem carga); `avg_rpe` é a média das séries com RPE (ponderada
// pelo `stimulus_weight` cadastrado do grupo no exercício — M76 Lote 3c),
// `rpe_sets` quantas séries tinham RPE. Exercício sem contribuição cadastrada
// conta no grupo "Outros", como no restante do dashboard.
type MuscleGroupsMappedAggregateFields = {
  group: string;
  muscle_group: string;
  value: number;
  sets: number;
  volume_kg: number;
  avg_rpe: number | null;
  rpe_sets: number;
  exercise_count: number;
  exercises: string[];
  source: string;
};

// Uma linha por (período, grupo) — usada na tendência.
export type MuscleGroupsMappedTrendRow = MuscleGroupsMappedAggregateFields & {
  period: string;
};

// Uma linha por grupo, agregada na janela inteira — usada na tabela.
export type MuscleGroupsMappedTableRow = MuscleGroupsMappedAggregateFields & {
  period_count: number;
};

export type MuscleGroupsMappedPayload = {
  ok: boolean;
  source: string;
  metric: MuscleGroupsMappedMetric;
  period: MuscleGroupsMappedPeriod;
  days: number;
  total_sets: number;
  mapped_sets: number;
  unmapped_sets: number;
  mapped_ratio: number;
  unmapped: MappedUnmappedRow[];
  // trend_rows/table_rows/rows/data/table/groups carregam o mesmo array
  // (por (período,grupo) em -mapped-trend, por grupo em -mapped-table) —
  // o backend duplica o campo sob vários nomes por compatibilidade com
  // consumidores legados; usar sempre trend_rows/table_rows aqui.
  trend_rows: MuscleGroupsMappedTrendRow[];
  table_rows: MuscleGroupsMappedTableRow[];
};

// GET /mapping-canonical/options|unmapped e POST /mapping-canonical/associate:
// tipos em `mappings-types.ts` (fonte única do router mapping-canonical).

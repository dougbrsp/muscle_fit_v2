// Tipos e helpers da tela "Metas de Ativação" (rota /metas-ativacao) (Fase 4, Etapa 14 da
// migração). Cadastro de metas semanais de volume por grupo muscular.
// `rag_folder` continua no contrato da API (e na coluna da migração 015, ainda
// consumida por `workout_plan_templates_engine`), mas saiu da interface no
// PR-15 (D16): o builder só carrega o valor salvo e o devolve intacto no PUT.
// Contratos confirmados lendo
// C:\muscle_fit\api\app\training_types.py e models.py (TrainingTypeIn /
// TrainingTypeMuscleGroupTargetIn) — router inteiro exige
// `require_admin_or_pt` (role 'admin' ou 'personal_trainer').

export type ApiListResponse<T> = { items: T[] };

// Item da listagem — GET /training-types (training_types.py::_list_training_types).
export type TrainingTypeListItem = {
  id: number;
  name: string;
  description: string | null;
  rag_folder: string | null;
  is_active: boolean;
  created_by_user_id: number;
  created_by_username: string | null;
  created_at: string;
  updated_at: string;
  muscle_groups_count: number;
};

// Meta semanal por grupo muscular — parte de GET /training-types/{id}
// (training_types.py::_get_training_type).
export type TrainingTypeTarget = {
  muscle_group_id: number;
  muscle_group_name: string;
  min_weekly_sets: number;
  target_weekly_sets: number;
  max_weekly_sets: number;
};

// Detalhe completo — GET /training-types/{id}.
export type TrainingTypeDetail = {
  id: number;
  name: string;
  description: string | null;
  rag_folder: string | null;
  is_active: boolean;
  created_by_user_id: number;
  created_at: string;
  updated_at: string;
  targets: TrainingTypeTarget[];
};

// Payload de POST/PUT /training-types (TrainingTypeIn/TrainingTypeMuscleGroupTargetIn
// em models.py). Replace-all transacional dos targets a cada save; linhas com
// min=target=max=0 são descartadas pelo backend, não precisam ser filtradas
// no cliente antes de enviar.
export type TrainingTypeTargetPayload = {
  muscle_group_id: number;
  min_weekly_sets: number;
  target_weekly_sets: number;
  max_weekly_sets: number;
};

export type TrainingTypePayload = {
  name: string;
  description: string | null;
  rag_folder: string | null;
  is_active: boolean;
  targets: TrainingTypeTargetPayload[];
};

// GET /muscle-groups (exercise_mapping_catalog.py::list_muscle_groups) — usado
// para popular o seletor de "adicionar grupo muscular" no builder de metas.
export type MuscleGroupOption = {
  id: number;
  name: string;
  primary_muscles: string | null;
  secondary_muscles: string | null;
  // Agregação da metodologia (migração 055): id do grupo PAI; null = o próprio
  // grupo é o principal. Opcional para respostas anteriores ao campo.
  methodology_parent_group_id?: number | null;
};

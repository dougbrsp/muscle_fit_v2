// Tipos dos endpoints CANÔNICOS da relação equipamento <-> exercício (PR-05).
// Contrato confirmado em `api/app/equipment_exercises.py`.
//
// `equipment_exercises` é a fonte canônica (lida por `gym_equipment_sync`,
// `GET /exercises/{id}` -> `equipment_links` e pela view
// `vw_muscle_group_equipment_exercises`). Os ids retornados aqui são de
// `equipment_exercises` — os mesmos de `ExerciseEquipmentLink.id` — então
// não há mais resolução de ids entre tabelas.
//
// As rotas `/equipment-exercise-mappings` (tabela legada) foram removidas no
// COD-05 (2026-10-09).
//
// Leitura (`GET`) exige usuário autenticado; escrita (`POST`/`DELETE`) exige admin.

// --- Convenção de carga do par (migração 045) --------------------------------
// O que `exercise_sets.weight_kg` significa NAQUELE par equipamento/exercício.
// O banco guarda sempre carga total em kg (D12); a convenção diz como LER esse
// número — e é parte da chave de comparabilidade do dashboard
// (`api/app/load_conventions.py`, espelho servidor).
//
// `null` = não cadastrada, e vale como `total` (é o que o sistema já assume em
// 100% do histórico). NÃO é "desconhecida": nada fica bloqueado por isso.
export const LOAD_CONVENTIONS = [
  'total',
  'per_side',
  'per_dumbbell',
  'assisted',
  'bodyweight',
  'bodyweight_plus',
] as const;

export type LoadConvention = (typeof LOAD_CONVENTIONS)[number];

export const LOAD_CONVENTION_LABELS: Record<LoadConvention, string> = {
  total: 'Carga total',
  per_side: 'Por lado',
  per_dumbbell: 'Por halter',
  assisted: 'Assistido (menos kg = progresso)',
  bodyweight: 'Peso corporal',
  bodyweight_plus: 'Peso corporal + carga',
};

// Rótulo da opção vazia do Select: diz o que acontece quando ninguém cadastrou.
export const LOAD_CONVENTION_EMPTY_LABEL = 'Não informada (assume total)';

export const LOAD_CONVENTION_OPTIONS = LOAD_CONVENTIONS.map((value) => ({
  value,
  label: LOAD_CONVENTION_LABELS[value],
}));

/** Rótulo para exibição, incluindo o caso `null` (não cadastrada). */
export function loadConventionLabel(value: LoadConvention | null | undefined): string {
  if (!value) return LOAD_CONVENTION_EMPTY_LABEL;
  return LOAD_CONVENTION_LABELS[value] ?? LOAD_CONVENTION_EMPTY_LABEL;
}

/** Valor do `Select` (string vazia = limpar) -> corpo do PUT. */
export function toLoadConventionPayload(raw: string | null): LoadConvention | null {
  if (!raw) return null;
  return (LOAD_CONVENTIONS as readonly string[]).includes(raw) ? (raw as LoadConvention) : null;
}

// --- GET /equipment-exercises?equipment_id&exercise_id&primary_group&active_only ---
export type EquipmentExerciseLink = {
  id: number;
  equipment_id: number;
  equipment_name: string;
  has_photo: boolean;
  photo_url: string | null;
  exercise_id: number;
  exercise_name: string | null;
  exercise_amazfit_name: string | null;
  // Medida sugerida da série do exercício (Lote T2, `exercise_catalog.default_measure`):
  // dica do editor do Modelo (T4). Opcional até a API nova; null = sem sugestão.
  default_measure?: string | null;
  primary_group: string | null;
  secondary_group: string | null;
  is_preferred: boolean;
  is_active: boolean;
  notes: string | null;
  load_convention: LoadConvention | null;
  created_at: string;
  updated_at: string;
};

// --- POST /equipment-exercises (admin-only) ---------------------------------
// Idempotente: se o par (equipment_id, exercise_id) já existir, reativa.
// `notes` omitido preserva o valor atual; string vazia limpa.
export type EquipmentExerciseCreatePayload = {
  equipment_id: number;
  exercise_id: number;
  notes?: string | null;
  load_convention?: LoadConvention | null;
};

export type EquipmentExerciseLegacyMirror =
  | 'mirrored'
  | 'skipped_no_legacy_table'
  | 'skipped_fk_target_missing'
  | 'skipped_error';

export type EquipmentExerciseCreateResponse = {
  status: 'ok';
  item: {
    id: number;
    equipment_id: number;
    exercise_id: number;
    is_preferred: boolean;
    is_active: boolean;
    notes: string | null;
    load_convention: LoadConvention | null;
    created_at: string;
    updated_at: string;
    legacy_mirror: EquipmentExerciseLegacyMirror;
  };
  gym_sync_warnings: GymSyncWarning[];
};

// --- Avisos do recálculo de `gym_equipment` ---------------------------------
// Enquanto a FK de `gym_equipment.mapping_id` apontar para o catálogo legado,
// exercícios que só existem em `exercise_catalog` não são materializados para
// as academias (a escrita do vínculo em si é feita normalmente).
export type GymSyncWarning = {
  code: 'gym_equipment_sync.fk_legacy_excluded';
  gym_id: number;
  fk_referenced_table: string;
  excluded_count: number;
  excluded_exercise_ids: number[];
  excluded_ids_truncated: boolean;
};

// --- DELETE /equipment-exercises/{id} (admin-only) --------------------------
// Soft delete (`is_active = 0`); recriar via POST reativa a mesma linha.
export type EquipmentExerciseDeleteResponse = {
  status: 'ok';
  id: number;
  is_active: false;
  gym_sync_warnings: GymSyncWarning[];
};

// --- PUT /equipment-exercises/{id} (admin-only) -----------------------------
// Edita os ATRIBUTOS do par já existente. O par em si (equipment_id/exercise_id)
// não muda aqui — trocar o par é outra associação, criada pelo POST. Campo
// ausente preserva o valor gravado; `null` limpa.
export type EquipmentExerciseUpdatePayload = {
  notes?: string | null;
  load_convention?: LoadConvention | null;
};

export type EquipmentExerciseUpdateResponse = {
  status: 'ok';
  item: {
    id: number;
    equipment_id: number;
    exercise_id: number;
    is_preferred: boolean;
    is_active: boolean;
    notes: string | null;
    load_convention: LoadConvention | null;
    created_at: string;
    updated_at: string;
  };
};

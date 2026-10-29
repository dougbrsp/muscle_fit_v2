// Tipos para a tela "Mapeamentos" (Fase 4, Etapa 16 da migração).
// Endpoints confirmados lendo diretamente `C:\muscle_fit\api\app\exercise_mapping_canonical.py`
// (router `/mapping-canonical`, prefixo aplicado no arquivo). Leitura exige
// apenas usuário autenticado (`Depends(get_current_identity)` no router);
// `POST /reclassify` e `POST /associate` exigem `Depends(require_admin)`
// (role === "admin" — personal_trainer NÃO passa, diferente do padrão
// admin_or_pt usado em outras telas).
//
// Não confundir com `GET /exercise-mapping-catalog/health` (router
// `exercise_mapping_catalog.py`, já consumido por
// `GruposMuscularesClient.tsx`): é um endpoint DIFERENTE, com formato de
// resposta diferente ({status, read_table, write_table, purpose}). Este
// arquivo cobre exclusivamente o router `mapping-canonical`.

// --- GET /mapping-canonical/health ------------------------------------------
export type MappingCanonicalHealthCounts = {
  total_exercises: number;
  classified_exercises: number | null;
  unclassified_exercises: number | null;
};

export type MappingCanonicalHealthGroup = {
  group_name: string;
  exercise_rows: number;
};

export type MappingCanonicalHealthResponse = {
  ok: boolean;
  source_of_truth: string;
  canonical_storage: string;
  ensure: {
    added: string[];
    available: Record<string, boolean>;
    index_errors_ignored: string[];
  };
  mapping_count: number;
  counts: MappingCanonicalHealthCounts;
  groups: MappingCanonicalHealthGroup[];
};

// --- GET /mapping-canonical/resolve ------------------------------------------
export type MappingCanonicalMatch = {
  id: number;
  exercise_name: string | null;
  amazfit_name: string | null;
  primary_group: string | null;
  secondary_group: string | null;
  primary_muscles: string | null;
  secondary_muscles: string | null;
  status: string;
  source: string;
  confidence: number;
  match_type: string;
};

export type MappingCanonicalResolveResponse = {
  ok: boolean;
  input: { name: string; mapped_name: string | null };
  match: MappingCanonicalMatch | null;
};

// --- POST /mapping-canonical/reclassify (admin-only) -------------------------
export type MappingCanonicalReclassifyExample = {
  id: number;
  original_name: string | null;
  canonical_exercise_name: string | null;
  canonical_amazfit_name: string | null;
  canonical_primary_group: string | null;
  mapping_id: number;
  match_type: string;
  confidence: number;
};

export type MappingCanonicalReclassifyUnresolvedExample = {
  id: number;
  original_name: string | null;
};

export type MappingCanonicalReclassifyResponse = {
  ok: boolean;
  error?: string;
  processed: number;
  updated: number;
  unresolved: number;
  mapping_count: number;
  examples: MappingCanonicalReclassifyExample[];
  unresolved_examples: MappingCanonicalReclassifyUnresolvedExample[];
};

// --- GET /mapping-canonical/unmapped ------------------------------------------
// Fonte única dos tipos do router `mapping-canonical` (options/unmapped/
// associate); `muscle-groups-types.ts` não os duplica mais.
// Sem `group_by` (formato original): uma linha por exercício.
export type UnmappedExerciseItem = {
  id: number;
  original_name: string | null;
};

export type UnmappedExercisesResponse = {
  ok: boolean;
  rows: UnmappedExerciseItem[];
};

// `?group_by=name&limit=&offset=`: um item por nome sem vínculo válido, sem
// janela de dias. `sets_count` exclui séries preparatórias; datas ISO
// (YYYY-MM-DD) de `activities.local_date`.
export type UnmappedExerciseByNameItem = {
  original_name: string;
  exercises_count: number;
  sets_count: number;
  first_seen: string | null;
  last_seen: string | null;
};

export type UnmappedExercisesByNameResponse = {
  ok: boolean;
  group_by: 'name';
  rows: UnmappedExerciseByNameItem[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

// --- GET /mapping-canonical/options -------------------------------------------
export type MappingCanonicalOptionItem = {
  id: number;
  exercise_name: string | null;
  amazfit_name: string | null;
  primary_group: string | null;
  secondary_group: string | null;
  primary_muscles: string | null;
  secondary_muscles: string | null;
};

export type MappingCanonicalOptionsResponse = {
  ok: boolean;
  source: string;
  items: MappingCanonicalOptionItem[];
};

// --- POST /mapping-canonical/associate (admin-only) ---------------------------
export type MappingCanonicalAssociateRequest = {
  alias_name: string;
  target_mapping_id: number;
};

export type MappingCanonicalAssociateResponse = {
  ok: boolean;
  error?: string;
  operation?: 'created' | 'updated';
  alias_name?: string;
  alias_mapping_id?: number;
  target_mapping_id?: number;
  target_exercise_name?: string | null;
  target_amazfit_name?: string | null;
  target_primary_group?: string | null;
  reclassification?: MappingCanonicalReclassifyResponse;
};

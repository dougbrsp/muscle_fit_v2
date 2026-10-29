// Tipos para a tela "Admin / Domínio de Exercícios" (Fase 4, Etapa 18 da
// migração). Contrato confirmado lendo diretamente
// `C:\muscle_fit\api\app\exercise_catalog_admin.py`.
//
// Três routers no mesmo arquivo:
// - `/exercises` (prefix): CRUD de `exercise_catalog` + aliases +
//   external-mappings aninhados por exercício.
// - `/equipment-types`: CRUD simples (poucas linhas, catálogo fixo de tipos).
// - `/exercise-external-mappings`: listagem FLAT (todas as linhas, não
//   agrupadas por exercício) usada pela aba "Mapeamentos externos" de
//   `/admin/dominio-exercicios` (`ExternalMappingsPanel.tsx`) — não existe mais
//   uma tela `/mapeamentos` separada nesta migração. Complementa o GET
//   por-exercício abaixo, usado pelo drawer de detalhe desta mesma tela
//   (aninhado em `/exercises/{id}/...`).
//
// Leitura (`GET`) exige apenas usuário autenticado (`_require_identity`).
// Escrita (`POST`/`PUT`/`DELETE`) exige role EXATAMENTE "admin"
// (`_require_admin_identity`) — mesmo padrão estrito de `/mapeamentos` e
// `/equipamentos-exercicios`.
//
// Nota: este router é ADITIVO ao `exercise_mapping_catalog.py` que já serve a
// tela legada `/mapeamentos` sobre a mesma `exercise_catalog`/`exercise_aliases`
// (os ids coincidem) — não duplicar funcionalidade daquela tela aqui.
//
// Contribuições musculares e equipamentos vinculados (Fase 4, Etapa 31):
// editáveis diretamente nesta tela, embora o CRUD "primário" continue
// existindo em outras telas/routers:
// - Contribuições musculares: `PUT`/`DELETE /muscle-contributions/{mapping_id}/{muscle_group_id}`,
//   expostos por `muscle_contributions_router` em
//   `C:\muscle_fit\api\app\exercise_mapping_catalog.py` (não em
//   `exercise_catalog_admin.py`, que só devolve leitura agregada via
//   `GET /exercises/{id}`). `GET` desse router exige apenas usuário
//   autenticado; `PUT`/`DELETE` exigem admin.
// - Equipamentos vinculados: endpoints canônicos `POST /equipment-exercises` e
//   `DELETE /equipment-exercises/{id}` (`C:\muscle_fit\api\app\equipment_exercises.py`,
//   admin-only), que escrevem em `equipment_exercises` — a mesma tabela que
//   `GET /exercises/{id}` lê para `equipment_links`. O `id` de cada
//   `ExerciseEquipmentLink` é usado direto no `DELETE`. Ver
//   `EquipmentLinksSection.tsx` e `equipment-exercises-types.ts`.

export type ApiListResponse<T> = { items: T[] };

// --- exercise_catalog --------------------------------------------------------

export type ExerciseCatalogItem = {
  id: number;
  name: string;
  body_position: string | null;
  movement: string | null;
  trajectory: string | null;
  biomechanical_pattern: string | null;
  primary_joint: string | null;
  resistance_vector: string | null;
  keywords: string | null;
  description: string | null;
  is_active: boolean | number;
  is_unilateral: boolean | number;
  is_compound: boolean | number | null;
  // Descricao de EXECUCAO (migracao 039), usada pela tela de briefing do modo
  // treino. `null` = ainda nao gerada — a tela simplesmente omite a secao.
  // `_reviewed_at` preenchido = texto revisado por um admin: a geracao em
  // lote nao o sobrescreve sem `force`, e `_model`/`_prompt_version` voltam a
  // `null` porque o texto deixou de ser o que a IA produziu.
  execution_description?: string | null;
  execution_description_model?: string | null;
  execution_description_prompt_version?: string | null;
  execution_description_generated_at?: string | null;
  execution_description_reviewed_by?: number | null;
  execution_description_reviewed_at?: string | null;
  // Medida sugerida da série (Lote T2, migração 089; `lib/set-measures.ts`):
  // dica do editor do Modelo. `null` = sem sugestão.
  default_measure?: string | null;
  // Exercício laboral (M100, migração 099; dono `api/app/domain/laboral.py`):
  // sem progressão; no Modo Treino só Concluir/Pular com o padrão abaixo.
  is_laboral?: boolean | number | null;
  laboral_default_sets?: number | null;
  laboral_default_reps?: number | null;
  laboral_default_load_kg?: number | string | null;
  created_at?: string;
  updated_at?: string;
};

// --- POST /exercises/{id}/execution-description/generate (admin) -------------
// 409 quando ja existe descricao e `force` nao foi pedido; 502 quando a OpenAI
// falha (o catalogo fica intacto).
export type ExecutionDescriptionGenerateResponse = {
  status: 'ok';
  generation: {
    exercise_id: number;
    status: 'generated' | 'skipped' | 'failed';
    // `failed`: mensagem da OpenAI/validacao. `skipped`: `not_found` ou
    // `already_generated`. Usados pela tela do backfill para listar o que
    // nao deu certo no fim do lote.
    error?: string;
    reason?: string;
    model?: string;
    prompt_version?: string;
    tokens_in?: number | null;
    tokens_out?: number | null;
  };
  item: ExerciseCatalogItem;
};

// --- GET /exercises/execution-descriptions/pending ---------------------------
// Somente leitura: quantos exercicios ATIVOS ainda estao sem descricao. A tela
// mostra este numero ANTES do backfill — cada item do lote e uma chamada de
// LLM paga.
export type ExecutionDescriptionPendingResponse = {
  pending: number;
  total_active: number;
  max_batch: number;
  /** Orcamento de tempo de UM lote no backend (segundos). Informativo. */
  time_budget_seconds?: number;
  prompt_version: string;
};

// --- POST /exercises/execution-descriptions/generate-batch (admin) -----------
// Backfill do catalogo: chamar ate `remaining` chegar a zero. O lote e limitado
// no backend em itens (`MAX_BATCH`) e em TEMPO (`BATCH_TIME_BUDGET_SECONDS`)
// por causa do timeout de 29 s do API Gateway: quando o tempo acaba, a
// resposta volta parcial (`time_budget_exhausted: true`, `requested` menor que
// o pedido) e o encadeamento simplesmente chama de novo. Cada item gerado ja
// esta commitado no banco quando a resposta chega.
export type ExecutionDescriptionBatchResponse = {
  status: 'ok';
  /** Itens efetivamente TENTADOS nesta chamada (gerados + pulados + falhos). */
  requested: number;
  generated: number;
  skipped: number;
  failed: number;
  remaining: number;
  /** `true` quando o lote parou por tempo antes de tentar todos os itens. */
  time_budget_exhausted?: boolean;
  /** Quantos itens escolhidos NAO chegaram a ser tentados (voltam em `remaining`). */
  deferred?: number;
  elapsed_seconds?: number;
  time_budget_seconds?: number;
  prompt_version: string;
  items: ExecutionDescriptionGenerateResponse['generation'][];
};

// --- GET /exercises ----------------------------------------------------------
export type ExerciseListResponse = {
  query: string;
  active_only: boolean;
  limit: number;
  offset: number;
  total: number;
  items: ExerciseCatalogItem[];
};

// --- POST /exercises, PUT /exercises/{id} (admin) ----------------------------
// `name` é o único campo obrigatório na criação; os demais são opcionais e,
// no PUT, campos ausentes no payload preservam o valor atual (update
// parcial) — nunca sobrescrever com null por omissão.
export type ExerciseUpsertPayload = {
  name?: string;
  body_position?: string | null;
  movement?: string | null;
  trajectory?: string | null;
  biomechanical_pattern?: string | null;
  primary_joint?: string | null;
  resistance_vector?: string | null;
  keywords?: string | null;
  description?: string | null;
  is_active?: boolean;
  is_unilateral?: boolean;
  is_compound?: boolean | null;
  // Lote T2: ausente = não muda; null/'' = sem sugestão; fora do vocabulário = 400.
  default_measure?: string | null;
  // M100: ausente = não muda; null = sem padrão; fora dos limites do dono = 400.
  is_laboral?: boolean;
  laboral_default_sets?: number | null;
  laboral_default_reps?: number | null;
  laboral_default_load_kg?: number | null;
};

export type ExerciseUpsertResponse = { status: 'ok'; item: ExerciseCatalogItem };

// --- exercise_aliases ---------------------------------------------------------

export type ExerciseAlias = {
  id: number;
  exercise_id: number;
  alias: string;
  source: string | null;
  created_at: string;
};

export type ExerciseAliasCreatePayload = { alias: string; source?: string | null };
export type ExerciseAliasCreateResponse = { status: 'ok'; item: ExerciseAlias };
export type ExerciseAliasDeleteResponse = { status: 'ok'; id: number };

// --- exercise_external_mappings (por exercício) -------------------------------

export type ExerciseExternalMapping = {
  id: number;
  exercise_id: number;
  provider: string;
  external_id: string | null;
  external_name: string;
  mapping_status: string;
  source: string | null;
  notes: string | null;
  is_active: boolean | number;
  created_at: string;
  updated_at: string;
};

// `provider` observado em uso real: 'AMAZFIT'. O backend não valida um enum
// fixo (campo livre em `exercise_external_mappings.provider`), mas a UI
// oferece 'AMAZFIT' como padrão/sugestão por ser o único provider usado hoje.
export type ExerciseExternalMappingCreatePayload = {
  provider: string;
  external_id?: string | null;
  external_name: string;
  mapping_status?: string; // default no backend: "CONFIRMED"
  source?: string | null;
  notes?: string | null;
  is_active?: boolean;
};

export type ExerciseExternalMappingUpdatePayload = Partial<ExerciseExternalMappingCreatePayload>;

export type ExerciseExternalMappingUpsertResponse = { status: 'ok'; item: ExerciseExternalMapping };
export type ExerciseExternalMappingDeleteResponse = { status: 'ok'; id: number };

// --- GET /exercise-external-mappings (listagem flat, uma linha por mapeamento) ---
// Usada pela aba "Mapeamentos externos" de `/admin/dominio-exercicios`
// (`ExternalMappingsPanel.tsx`). Mesmos campos de `ExerciseExternalMapping`
// mais o nome/status do exercício dono, já resolvidos via JOIN no backend.

export type ExerciseExternalMappingFlatItem = {
  id: number;
  exercise_id: number;
  exercise_name: string;
  exercise_is_active: boolean | number;
  provider: string;
  external_id: string | null;
  external_name: string;
  mapping_status: string | null;
  source: string | null;
  notes: string | null;
  is_active: boolean | number;
  created_at: string;
  updated_at: string;
};

export type ExerciseExternalMappingFlatListResponse = {
  query: string;
  provider: string;
  active_only: boolean;
  limit: number;
  offset: number;
  total: number;
  items: ExerciseExternalMappingFlatItem[];
};

// --- GET /exercise-external-mappings/providers ---

export type ExerciseExternalMappingProviderCount = { provider: string; count: number };
export type ExerciseExternalMappingProvidersResponse = { items: ExerciseExternalMappingProviderCount[] };

// --- GET /exercises/{id} (agregado: detalhe + relacionados) -------------------

export type ExerciseMuscleContribution = {
  id: number;
  exercise_id: number;
  muscle_group_id: number;
  muscle_group_name: string;
  role: 'PRIMARY' | 'SECONDARY' | 'STABILIZER';
  stimulus_weight: number;
  fatigue_weight: number;
  is_active: boolean | number;
};

export type ExerciseEquipmentLink = {
  id: number;
  equipment_id: number;
  equipment_name: string;
  photo_url: string | null;
  is_preferred: boolean | number;
  is_active: boolean | number;
  notes: string | null;
};

export type ExerciseDetailResponse = {
  exercise: ExerciseCatalogItem;
  aliases: ExerciseAlias[];
  external_mappings: ExerciseExternalMapping[];
  // Editável nesta tela via `muscle-contributions/{mapping_id}/{muscle_group_id}`
  // (ver nota de escopo acima) — `MuscleContributionsSection.tsx`.
  muscle_contributions: ExerciseMuscleContribution[];
  // Editável nesta tela via `/equipment-exercises` (ver nota de
  // escopo acima) — `EquipmentLinksSection.tsx`.
  equipment_links: ExerciseEquipmentLink[];
  legacy_ids_merged: number[];
};

// --- PUT/DELETE /muscle-contributions/{mapping_id}/{muscle_group_id} (exercise_mapping_catalog.py) ---
// `role` obrigatório; `stimulus_weight`/`fatigue_weight` obrigatórios e
// numéricos em [0, 2] (teto de segurança do backend, não [0,1]) — sem
// default no backend, a UI decide um ponto de partida por role.
export type MuscleContributionUpsertPayload = {
  role: 'PRIMARY' | 'SECONDARY' | 'STABILIZER';
  stimulus_weight: number;
  fatigue_weight: number;
};

export type MuscleContributionUpsertResponse = { status: 'ok'; item: ExerciseMuscleContribution };

export type MuscleContributionDeactivateResponse = {
  status: 'ok';
  mapping_id: number;
  muscle_group_id: number;
  is_active: false;
};

// --- GET /muscle-groups (catálogo de grupos musculares) ---------------------
// Usado aqui somente para popular o seletor de "adicionar contribuição" —
// não é o CRUD de `exercise_mapping_catalog` (tabela diferente). `GET` exige
// apenas usuário autenticado.
export type MuscleGroupOption = {
  id: number;
  name: string;
};

// --- CRUD /muscle-groups (exercise_mapping_catalog.py, muscle_groups_router) ---
// Usado pela aba "Grupos musculares" de `/admin/dominio-exercicios`
// (`MuscleGroupsPanel.tsx`). Confirmado lendo
// `api/app/exercise_mapping_catalog.py`: `primary_muscles`/`secondary_muscles`
// são colunas de TEXTO livre (não lista/array — passam por `_clean_optional`,
// que só faz `str(...).strip()`), tipicamente nomes de músculos separados por
// vírgula. Sem `DELETE` — o backend só expõe `GET`/`POST`/`PUT`.
// Porte do grupo no vocabulário da metodologia de volume (migração 055).
// Domínio FECHADO: espelha o ENUM da coluna e a tupla
// `MUSCLE_GROUP_SIZE_CLASSES` de `api/app/exercise_mapping_catalog.py`, que
// valida em Python e devolve 422 para valor de fora.
export const MUSCLE_GROUP_SIZE_CLASSES = ['large', 'small', 'abs', 'unclassified'] as const;
export type MuscleGroupSizeClass = (typeof MUSCLE_GROUP_SIZE_CLASSES)[number];

export type MuscleGroupItem = {
  id: number;
  name: string;
  primary_muscles: string | null;
  secondary_muscles: string | null;
  // `size_class` é NOT NULL DEFAULT 'unclassified' no banco; opcional aqui só
  // para o tipo continuar válido contra uma resposta de API anterior à 055.
  size_class?: MuscleGroupSizeClass;
  // NULL = o próprio grupo já é o bloco da metodologia; um id = este grupo é
  // uma FATIA que soma naquele.
  methodology_parent_group_id?: number | null;
  // Macro-grupo de volume (migração 057, Fase 6). NULL = sem macro-grupo (o
  // grupo não é avaliado pela metodologia). Opcional pelo mesmo motivo de
  // `size_class`: resposta de API anterior à 057 não traz o campo.
  volume_class_id?: number | null;
  created_at?: string;
  updated_at?: string;
};

export type MuscleGroupListResponse = { items: MuscleGroupItem[] };

// `POST` faz upsert por `name` (ON DUPLICATE KEY UPDATE); `PUT` faz update
// parcial por id — campos ausentes/vazios no payload viram `NULL` para
// `primary_muscles`/`secondary_muscles` (sem preservar valor anterior, ver
// `update_muscle_group`), diferente do padrão de outros PUTs deste arquivo —
// a UI deve sempre reenviar os dois campos, mesmo sem mudança.
//
// `size_class` e `methodology_parent_group_id` ficam FORA do
// `ON DUPLICATE KEY UPDATE` do `POST`: re-cadastrar um nome que já existe
// preserva a classificação em vez de zerá-la. Para mudar porte/agregação de
// um grupo existente, use o `PUT`. No `PUT`, campo AUSENTE preserva e
// `methodology_parent_group_id: null` DESAGREGA (é a única forma de limpar).
export type MuscleGroupUpsertPayload = {
  name?: string;
  primary_muscles?: string | null;
  secondary_muscles?: string | null;
  size_class?: MuscleGroupSizeClass;
  methodology_parent_group_id?: number | null;
};

export type MuscleGroupUpsertResponse = { status: 'ok'; item: MuscleGroupItem };

// --- equipment_types -----------------------------------------------------------

export type EquipmentType = {
  id: number;
  code: string;
  name: string;
  description: string | null;
};

export type EquipmentTypeUpsertPayload = { code?: string; name?: string; description?: string | null };
export type EquipmentTypeUpsertResponse = { status: 'ok'; item: EquipmentType };
export type EquipmentTypeDeleteResponse = { status: 'ok'; id: number };

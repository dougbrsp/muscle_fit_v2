// Recomendador de equipamento por usuário e preferência em estrelas (PR-08 da
// refatoração dos motores). Contratos de `GET /gym-equipment/recommend(-all)`
// (`api/app/equipment_recommender.py`) e de `/me/equipment-exercise-preferences`
// (`api/app/equipment_exercise_preferences.py`), mais a lógica pura das telas
// (`/preferencias-treino` e `/dominio-exercicios` > vínculos do exercício,
// onde a nota é dada; `EquipmentSubstituteModal` e `FreeformScreen`, onde ela
// só é exibida). O ranking e a segurança moram no backend; aqui só há
// tipos, montagem de URL, atualização otimista, agrupamento para exibição e
// rótulos.
//
// TIPO ÚNICO: `RecommendItem` (`gyms-types.ts`) e `GymEquipmentRecommendItem`
// (`workout-mode-types.ts`) eram duas cópias do mesmo item (G20) — agora são
// aliases de `EquipmentRecommendItem`.

import type { SafetySource, TechniquePermission } from './training-safety';
import { normalizeSearch } from './unmapped-exercises';

export const DEFAULT_PREFERENCE_STARS = 3;
export const MIN_PREFERENCE_STARS = 1;
export const MAX_PREFERENCE_STARS = 5;

export type EquipmentRecommendItem = {
  // id da ASSOCIAÇÃO (`gym_equipment.id`), o mesmo de `exercises.equipment_id`.
  id: number;
  gym_equipment_id?: number;
  mapping_id: number;
  equipment_id: number;
  equipment_name: string;
  photo_url: string | null;
  mapping_exercise_name: string;
  mapping_amazfit_name: string | null;
  muscle_group_id?: number;
  primary_group: string;
  // Desde o PR-08, uso DO USUÁRIO corrente (antes era global).
  last_used_date?: string | null;
  last_used_at?: string | null;
  last_used_by_user_at?: string | null;
  mapping_aliases?: string[];
  // Nota do usuário para ESTE par exercício+equipamento; null/ausente = sem
  // avaliação (vale 3 no ranking).
  user_preference_stars?: number | null;
  // Segurança do par (só informa; ver `required_technique`).
  max_safe_rpe?: number;
  failure_allowed?: boolean;
  allowed_advanced_techniques?: TechniquePermission[];
  safety_source?: SafetySource;
};

export type RecommendExclusionReason = 'TECHNIQUE_INCOMPATIBLE' | 'FAILURE_REQUIRED_NOT_ALLOWED';

export type EquipmentRecommendExcluded = {
  gym_equipment_id: number;
  id: number;
  equipment_id: number;
  equipment_name: string;
  mapping_id: number;
  mapping_exercise_name: string;
  primary_group?: string;
  user_preference_stars?: number | null;
  reason: RecommendExclusionReason | string;
  max_safe_rpe?: number;
  failure_allowed?: boolean;
};

export type EquipmentRecommendGroup = {
  has_equipment_for_group: boolean;
  recommended: EquipmentRecommendItem | null;
  alternatives: EquipmentRecommendItem[];
  excluded?: EquipmentRecommendExcluded[];
};

export type EquipmentRecommendResponse = EquipmentRecommendGroup & {
  ok: boolean;
  gym_id: number;
  primary_group: string;
  required_technique?: string | null;
  items: EquipmentRecommendItem[];
};

export type EquipmentRecommendAllResponse = {
  ok: boolean;
  gym_id: number;
  required_technique?: string | null;
  groups: Record<string, EquipmentRecommendGroup>;
  groups_by_id?: Record<number, EquipmentRecommendGroup>;
};

// Nota do usuário para um PAR exercício+equipamento (PR-08b, decisão do
// usuário em 2026-09-17: a nota é daquele exercício NAQUELE equipamento).
// `equipment_exercise_id` = `equipment_exercises.id` (o `link.id` do Domínio de
// exercícios); `exercise_id` = `exercise_catalog.id` = `mapping_id`.
export type EquipmentExercisePreference = {
  equipment_exercise_id: number;
  equipment_id: number;
  equipment_name: string | null;
  exercise_id: number;
  mapping_id?: number;
  exercise_name: string | null;
  stars: number;
  updated_at: string | null;
};

export type EquipmentExercisePreferencesResponse = {
  ok: boolean;
  default_stars: number;
  items: EquipmentExercisePreference[];
};

export const EQUIPMENT_EXERCISE_PREFERENCES_PATH = '/me/equipment-exercise-preferences';
export const EQUIPMENT_EXERCISE_PREFERENCES_QUERY_KEY = ['me-equipment-exercise-preferences'] as const;

export const PAIR_STARS_HELP =
  'Sua nota pessoal para este exercício neste equipamento. Pares com mais estrelas são sugeridos primeiro nas suas fichas e trocas; sem nota conta como 3.';

// ---------------------------------------------------------------------------
// URL de /gym-equipment/recommend
// ---------------------------------------------------------------------------

export type RecommendPathInput = {
  gymId: number;
  mappingId?: number | null;
  primaryGroup?: string | null;
  excludeEquipmentLinkId?: number | null;
  // Técnica prescrita na ficha: o backend tira os incompatíveis da lista e
  // os devolve em `excluded[]` com o motivo. Vazio = sem filtro.
  requiredTechnique?: string | null;
};

export function buildRecommendPath(input: RecommendPathInput): string {
  const params = new URLSearchParams({ gym_id: String(input.gymId) });
  if (input.mappingId) params.set('mapping_id', String(input.mappingId));
  else if (input.primaryGroup) params.set('primary_group', input.primaryGroup);
  if (input.excludeEquipmentLinkId) params.set('exclude_equipment_id', String(input.excludeEquipmentLinkId));
  const technique = (input.requiredTechnique || '').trim();
  if (technique) params.set('required_technique', technique);
  return `/gym-equipment/recommend?${params.toString()}`;
}

// ---------------------------------------------------------------------------
// Estrelas
// ---------------------------------------------------------------------------

export function isValidStars(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isInteger(value) &&
    value >= MIN_PREFERENCE_STARS &&
    value <= MAX_PREFERENCE_STARS
  );
}

// equipment_exercises.id -> estrelas (Domínio de exercícios, onde a nota é dada).
export function starsByPairId(items: EquipmentExercisePreference[] | undefined | null): Map<number, number> {
  const map = new Map<number, number>();
  for (const item of items || []) {
    if (isValidStars(item.stars)) map.set(item.equipment_exercise_id, item.stars);
  }
  return map;
}

// Chave do par para quem só tem `equipment_id` + `mapping_id` (itens de
// `gym_equipment`, no Modo Treino).
export function pairKey(equipmentId: number, exerciseId: number): string {
  return `${equipmentId}:${exerciseId}`;
}

export function starsByPairKey(items: EquipmentExercisePreference[] | undefined | null): Map<string, number> {
  const map = new Map<string, number>();
  for (const item of items || []) {
    if (isValidStars(item.stars)) map.set(pairKey(item.equipment_id, item.exercise_id), item.stars);
  }
  return map;
}

// Atualização OTIMISTA do cache de `/me/equipment-exercise-preferences`:
// `stars = null` remove a avaliação (volta a "sem avaliação"). Nunca muta a
// entrada — o snapshot anterior é o próprio rollback em caso de erro.
export type PairPreferenceChange = {
  equipmentExerciseId: number;
  equipmentId: number;
  exerciseId: number;
  equipmentName?: string | null;
  exerciseName?: string | null;
  stars: number | null;
};

export function applyPreferenceChange(
  current: EquipmentExercisePreferencesResponse | undefined,
  change: PairPreferenceChange,
): EquipmentExercisePreferencesResponse {
  const base: EquipmentExercisePreferencesResponse = current ?? {
    ok: true,
    default_stars: DEFAULT_PREFERENCE_STARS,
    items: [],
  };
  const others = base.items.filter((item) => item.equipment_exercise_id !== change.equipmentExerciseId);
  if (change.stars === null) return { ...base, items: others };
  if (!isValidStars(change.stars)) return base;
  const previous = base.items.find((item) => item.equipment_exercise_id === change.equipmentExerciseId);
  const next: EquipmentExercisePreference = {
    equipment_exercise_id: change.equipmentExerciseId,
    equipment_id: change.equipmentId,
    equipment_name: change.equipmentName ?? previous?.equipment_name ?? null,
    exercise_id: change.exerciseId,
    mapping_id: change.exerciseId,
    exercise_name: change.exerciseName ?? previous?.exercise_name ?? null,
    stars: change.stars,
    updated_at: previous?.updated_at ?? null,
  };
  return { ...base, items: [...others, next] };
}

// Mesma ordem de faixas do backend para listas montadas no front (passo
// "escolha o exercício" do treino livre, já dentro de UM equipamento):
// estrelas do par DESC (ausente = 3), depois nome. A recência por usuário só
// existe no recomendador.
export function sortExercisesByPairStars<T extends { mapping_id: number; exercise_name: string }>(
  options: T[],
  equipmentId: number,
  stars: Map<string, number>,
): T[] {
  const of = (option: T) => stars.get(pairKey(equipmentId, option.mapping_id)) ?? DEFAULT_PREFERENCE_STARS;
  return [...options].sort(
    (a, b) => of(b) - of(a) || a.exercise_name.localeCompare(b.exercise_name, 'pt-BR') || a.mapping_id - b.mapping_id,
  );
}

export function starsLabel(stars: number | null | undefined): string {
  if (!isValidStars(stars)) return 'Sem avaliação (conta como 3 estrelas)';
  return `${stars} ${stars === 1 ? 'estrela' : 'estrelas'}`;
}

// ---------------------------------------------------------------------------
// Excluídos por `required_technique`
// ---------------------------------------------------------------------------

export function exclusionReasonLabel(reason: string, techniqueName?: string | null): string {
  const technique = techniqueName ? `"${techniqueName}"` : 'a técnica prescrita';
  switch (reason) {
    case 'FAILURE_REQUIRED_NOT_ALLOWED':
      return `${technique} exige chegar à falha, e a falha não é permitida neste equipamento.`;
    case 'TECHNIQUE_INCOMPATIBLE':
      return `Equipamento não marcado como compatível com ${technique}.`;
    default:
      return `Incompatível com ${technique}.`;
  }
}

// gym_equipment.id -> motivo, para sinalizar o item na lista manual.
export function exclusionByLinkId(
  excluded: EquipmentRecommendExcluded[] | undefined | null,
): Map<number, EquipmentRecommendExcluded> {
  const map = new Map<number, EquipmentRecommendExcluded>();
  for (const entry of excluded || []) map.set(entry.gym_equipment_id ?? entry.id, entry);
  return map;
}

// ---------------------------------------------------------------------------
// Preferências de Treino (`GET /me/training-preferences`)
// ---------------------------------------------------------------------------

// Par exercício+equipamento disponível na academia (`gym_equipment` ativo),
// com a nota do usuário. `stars` null = sem avaliação (o neutro do ranking é
// do backend, `domain/equipment_ranking.py`).
export type TrainingPreferenceItem = {
  equipment_exercise_id: number;
  exercise_id: number;
  exercise_name: string | null;
  muscle_group_id: number | null;
  primary_group: string | null;
  equipment_id: number;
  equipment_name: string | null;
  photo_url: string | null;
  stars: number | null;
};

export type TrainingPreferencesResponse = {
  ok: boolean;
  gym_id: number | null;
  // Academia da ficha mais recente do usuário: valor inicial do seletor.
  default_gym_id: number | null;
  default_stars: number;
  items: TrainingPreferenceItem[];
};

export const TRAINING_PREFERENCES_PATH = '/me/training-preferences';
export const TRAINING_PREFERENCES_QUERY_KEY = ['me-training-preferences'] as const;

export function buildTrainingPreferencesPath(gymId: number | null | undefined): string {
  return gymId ? `${TRAINING_PREFERENCES_PATH}?gym_id=${gymId}` : TRAINING_PREFERENCES_PATH;
}

export const UNGROUPED_LABEL = 'Sem grupo muscular';

export type TrainingPreferenceExercise = {
  exerciseId: number;
  exerciseName: string;
  pairs: TrainingPreferenceItem[];
};

export type TrainingPreferenceGroup = {
  group: string;
  exercises: TrainingPreferenceExercise[];
};

// Agrupa para EXIBIÇÃO: grupo muscular -> exercício -> equipamentos, em ordem
// alfabética (sem ranking: a nota não reordena a lista, senão o item "pularia"
// ao ser avaliado). A busca casa todos os termos no nome do exercício, do
// equipamento ou do grupo, sem acento e em qualquer ordem.
export function groupTrainingPreferences(
  items: TrainingPreferenceItem[] | undefined | null,
  search = '',
): TrainingPreferenceGroup[] {
  const terms = normalizeSearch(search).split(' ').filter(Boolean);
  const groups = new Map<string, Map<number, TrainingPreferenceExercise>>();
  for (const item of items || []) {
    const group = item.primary_group || UNGROUPED_LABEL;
    const exerciseName = item.exercise_name || `Exercício #${item.exercise_id}`;
    if (terms.length) {
      const haystack = normalizeSearch(`${exerciseName} ${item.equipment_name || ''} ${group}`);
      if (!terms.every((term) => haystack.includes(term))) continue;
    }
    const exercises = groups.get(group) ?? new Map<number, TrainingPreferenceExercise>();
    groups.set(group, exercises);
    const exercise = exercises.get(item.exercise_id) ?? { exerciseId: item.exercise_id, exerciseName, pairs: [] };
    exercises.set(item.exercise_id, exercise);
    exercise.pairs.push(item);
  }
  const byName = (a: string, b: string) => a.localeCompare(b, 'pt-BR');
  return [...groups.entries()]
    .sort(([a], [b]) => (a === UNGROUPED_LABEL ? 1 : b === UNGROUPED_LABEL ? -1 : byName(a, b)))
    .map(([group, exercises]) => ({
      group,
      exercises: [...exercises.values()]
        .sort((a, b) => byName(a.exerciseName, b.exerciseName) || a.exerciseId - b.exerciseId)
        .map((exercise) => ({
          ...exercise,
          pairs: [...exercise.pairs].sort(
            (a, b) =>
              byName(a.equipment_name || '', b.equipment_name || '') || a.equipment_exercise_id - b.equipment_exercise_id,
          ),
        })),
    }));
}

// Nota exibida: a do cache de `/me/equipment-exercise-preferences` (onde a
// atualização otimista acontece) quando ele já carregou; antes disso, a que
// veio na própria lista.
export function displayedPairStars(
  item: TrainingPreferenceItem,
  preferences: Map<number, number> | null,
): number | null {
  if (preferences) return preferences.get(item.equipment_exercise_id) ?? null;
  return isValidStars(item.stars) ? item.stars : null;
}

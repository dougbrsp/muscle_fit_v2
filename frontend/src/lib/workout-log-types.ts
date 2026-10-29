// Tipos compartilhados entre a tela de Registro de Exercícios
// (`src/app/(app)/registro-exercicios/page.tsx`), a tela de detalhe de um
// treino (`src/app/(app)/registro-exercicios/[activityId]/page.tsx`) e o
// modal de novo treino/edição de cabeçalho (`src/components/NewWorkoutModal.tsx`).
//
// Espelham o payload de GET /activities (lista, enriquecido), GET
// /activities/{id} (detalhe, com exercises[]/sets[] aninhados) e o body de
// POST /workout-sessions/start — contratos confirmados lendo
// C:\muscle_fit\api\app\main.py (rotas /activities e /exercises/logs) e
// C:\muscle_fit\api\app\workout_sessions.py (SessionStartIn/SessionMetaIn/
// SessionExerciseIn/SessionSetIn).

export type ApiList<T> = { items: T[] };

export const conductedByOptions = [
  { value: 'personal', label: 'Personal trainer' },
  { value: 'dougfitness', label: 'DougFitness (ficha da IA)' },
];

// Item da listagem principal (GET /activities) — um treino, com agregados.
export type WorkoutListItem = {
  id: number;
  local_date: string;
  activity_type: string;
  name: string;
  duration_seconds?: number | null;
  calories_kcal?: number | string | null;
  avg_hr_bpm?: number | string | null;
  max_hr_bpm?: number | string | null;
  avg_rpe?: number | string | null;
  notes?: string | null;
  gym_id?: number | null;
  gym_name?: string | null;
  training_type_id?: number | null;
  training_type_name?: string | null;
  conducted_by?: string | null;
  session_status?: string | null;
  workout_plan_id?: number | null;
  workout_plan_name?: string | null;
  total_volume_kg?: number | string | null;
};

export type Gym = { id: number; name: string };
export type GymResponse = { items: Gym[] };

export type TrainingType = { id: number; name: string; is_active?: boolean };
export type TrainingTypeResponse = { items: TrainingType[] };

export type WorkoutPlanListItem = {
  id: number;
  name: string;
  plan_date?: string | null;
};
export type WorkoutPlanListResponse = { items: WorkoutPlanListItem[] };

// Cabeçalho do treino para o form de criação (POST /workout-sessions/start).
export type WorkoutHeaderForm = {
  name: string;
  local_date: string;
  started_at_time: string;
  gym_id: string;
  training_type_id: string;
  conducted_by: string;
  workout_plan_id: string;
};

export const sessionStatusLabels: Record<string, { label: string; color: string }> = {
  in_progress: { label: 'Em andamento', color: 'yellow' },
  done: { label: 'Finalizado', color: 'green' },
};

export function periodToDateFrom(periodDays: string): string | null {
  if (periodDays === 'all') return null;
  const days = Number(periodDays);
  if (!Number.isFinite(days)) return null;
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

export function buildStartedAt(localDate: string, timeValue: string): string | null {
  if (!localDate || !timeValue) return null;
  const normalizedTime = timeValue.length === 5 ? `${timeValue}:00` : timeValue;
  return `${localDate}T${normalizedTime}`;
}

export function extractTime(startedAt?: string | null): string {
  if (!startedAt) return '';
  const match = startedAt.match(/T(\d{2}:\d{2})/);
  return match ? match[1] : '';
}

// ---------------------------------------------------------------------------
// Etapa 4: detalhe de um treino (GET /activities/{id}), edição/adição de
// exercícios e séries, ativação muscular e finalização de sessão.
// ---------------------------------------------------------------------------

export type ExerciseSetRow = {
  id?: number;
  set_number: number;
  set_type?: string;
  reps?: number | null;
  weight_kg?: number | string | null;
  rpe?: number | string | null;
  rest_seconds?: number | null;
  // Frequência cardíaca da série (relógio); a API devolve DECIMAL como string.
  hr_avg_bpm?: number | string | null;
  hr_max_bpm?: number | string | null;
  muscle_pain?: number | null;
  joint_pain?: boolean | number | null;
  notes?: string | null;
  // PR-14 (seção 5.7): alvo de RPE efetivamente cobrado nesta série (depois do
  // overlay de segurança) e evento de segurança registrado nela
  // (`RPE_ABOVE_SAFE_CEILING` = RPE 10 num par onde a falha não é permitida,
  // alerta de segurança; `RPE_ABOVE_RECOVERY_TARGET` = RPE 10 num par que
  // permite a falha, negada só pela recuperação do dia — informativo).
  // Ambos gravados pelo SERVIDOR; `null` em série anterior ao PR-14.
  rpe_target_effective?: number | null;
  safety_event?: string | null;
  // Fase 5: técnica aplicada nesta série e a série de trabalho (set_number)
  // de onde ela saiu. `null` em série que não é aplicação de técnica.
  technique_code?: string | null;
  anchor_set_number?: number | null;
  // Medida da série (Lote T3, colunas da 089): `reps` | `duration` |
  // `max_reps` (lib/set-measures.ts; ausente = reps). `duration_seconds` =
  // segundos da série por tempo / janela do máximo; `measure_text` = texto do
  // dono ("58 s"), devolvido por `GET /activities/{id}`.
  measure?: string | null;
  duration_seconds?: number | null;
  measure_text?: string | null;
  // M102 (colunas da 100): reps/RPE por lado em exercício unilateral (DECIMAL
  // do RPE chega como string). `reps`/`rpe` acima são os OFICIAIS (pior lado,
  // calculados pelo servidor); `sides_text` = texto pronto ("reps E 10 · D 8").
  reps_left?: number | null;
  reps_right?: number | null;
  rpe_left?: number | string | null;
  rpe_right?: number | string | null;
  sides_text?: string | null;
  // Modo Intensivo (colunas da 092): série concluída com reps a confirmar
  // (`reps` null + `reps_pending`), hora real da conclusão, carga planejada e
  // origens. `client_key` = chave de idempotência do POST.
  reps_pending?: boolean | number | null;
  completed_at?: string | null;
  planned_weight_kg?: number | string | null;
  load_adjust_source?: string | null;
  rpe_target_source?: string | null;
  client_key?: string | null;
};

// Um exercício dentro do detalhe do treino (exercises[] de GET /activities/{id}).
export type ExerciseLogRow = {
  id: number;
  activity_id: number;
  sequence: number;
  original_name: string;
  mapping_id?: number | null;
  mapped_name?: string | null;
  muscle_primary?: string | null;
  muscle_secondary?: string | null;
  equipment_id?: number | null;
  equipment_catalog_id?: number | null;
  equipment_name?: string | null;
  equipment_has_photo?: boolean;
  equipment_photo_url?: string | null;
  workout_plan_exercise_id?: number | null;
  planned_mapping_id?: number | null;
  planned_exercise_name?: string | null;
  // Nome/descrição do exercício REALMENTE lançado (`exercise_catalog` do
  // `mapping_id` gravado) — podem diferir do item da ficha após troca.
  exercise_name?: string | null;
  exercise_description?: string | null;
  muscle_pain?: number | null;
  joint_pain?: boolean | null;
  sets_count?: number | null;
  volume_kg?: number | string | null;
  avg_rpe?: number | string | null;
  sets: ExerciseSetRow[];
};

// Detalhe completo de um treino (GET /activities/{id}).
export type ActivityDetail = {
  id: number;
  source_id?: number;
  external_id?: string | null;
  local_date: string;
  started_at?: string | null;
  ended_at?: string | null;
  activity_type: string;
  name: string;
  notes?: string | null;
  gym_id?: number | null;
  gym_name?: string | null;
  training_type_id?: number | null;
  training_type_name?: string | null;
  conducted_by?: string | null;
  duration_seconds?: number | null;
  calories_kcal?: number | string | null;
  /**
   * Origem da kcal, RESOLVIDA pelo backend (`domain/calories_source.py`,
   * nutrição L0): `manual` | `intervals` | null. A tela só exibe o rótulo
   * pronto em `calories_source_label`.
   */
  calories_source?: string | null;
  calories_source_label?: string | null;
  avg_hr_bpm?: number | string | null;
  max_hr_bpm?: number | string | null;
  avg_rpe?: number | string | null;
  session_status?: string | null;
  workout_plan_id?: number | null;
  workout_plan_name?: string | null;
  total_volume_kg?: number | string | null;
  ai_summary?: string | null;
  ai_summary_generated_at?: string | null;
  exercises: ExerciseLogRow[];
};

// Item do catálogo canônico (GET /exercise-mapping-catalog).
export type MappingItem = {
  id?: number;
  exercise_name: string;
  amazfit_name: string;
  primary_group?: string | null;
  secondary_group?: string | null;
};

export type MappingResponse = { items: MappingItem[] };

// Grupo muscular do catálogo canônico (GET /exercise-mapping-catalog/groups).
export type MuscleGroupOption = {
  primary_group: string;
  count: number;
};

export type MuscleGroupsResponse = { items: MuscleGroupOption[] };

// Associação academia + grupo muscular + equipamento (GET /gym-equipment).
export type GymEquipmentLink = {
  id: number;
  gym_id: number;
  gym_name?: string | null;
  mapping_id: number;
  mapping_exercise_name?: string | null;
  mapping_amazfit_name?: string | null;
  // Descrição do exercício da associação (`exercise_catalog.description`).
  mapping_exercise_description?: string | null;
  primary_group?: string | null;
  secondary_group?: string | null;
  equipment_id: number;
  equipment_name: string;
  has_photo?: boolean;
  photo_url?: string | null;
  is_active?: boolean;
};

export type GymEquipmentResponse = { items: GymEquipmentLink[] };

// Exercício dentro de uma ficha de treino (GET /workout-plans/{id}).
export type WorkoutPlanExercise = {
  workout_plan_exercise_id?: number;
  sequence: number;
  exercise_name: string;
  amazfit_name?: string | null;
  mapping_id?: number | null;
  muscle_primary?: string | null;
  muscle_secondary?: string | null;
  equipment_name?: string | null;
  preferred_equipment_id?: number | null;
  sets_count?: number | null;
  reps_target?: string | null;
  load_target?: string | null;
  rpe_target?: string | null;
  rest_seconds?: number | null;
  notes?: string | null;
  exercise_description?: string | null;
  progression?: 'increased' | 'decreased' | 'same' | null;
  progression_note?: string | null;
  // Técnica avançada + aquecimento/ajuste configuráveis + pico de contração
  // (modo "Equipamentos e séries" do builder de modelos) — mesmos campos de
  // `workout_plan_exercises` no backend e de `WorkoutPlanExercise` em
  // `workout-plans-types.ts`. Usados pelo "Fazer treino avançado" do modo
  // treino (`modo-treino/PlanExecutionScreen.tsx`).
  advanced_technique?: string | null;
  advanced_technique_applications?: number | null;
  advanced_technique_duration_seconds?: number | null;
  warmup_sets_count?: number | null;
  warmup_reps?: number | null;
  warmup_rest_seconds?: number | null;
  feeler_set_sets_count?: number | null;
  feeler_set_reps?: number | null;
  feeler_set_rest_seconds?: number | null;
  peak_contraction_seconds?: number | null;
};

export type WorkoutPlanDetail = {
  id: number;
  name: string;
  plan_date?: string | null;
  plan_type?: string | null;
  gym_id?: number | null;
  gym_name?: string | null;
  exercises: WorkoutPlanExercise[];
};

// Alvo por grupo muscular da activity (alvo do slot da ficha ou meta do
// training_type). Só informativo: o status de cada grupo já vem pronto em
// `items[].assessment` (M76 E4).
export type TrainingTypeWeeklyTarget = {
  muscle_group_id: number;
  muscle_group_name: string;
  sets_count: number;
  max_weekly_sets?: number | null;
  min_weekly_sets?: number | null;
  target_source?: 'template_slot' | 'training_type';
};

export type MuscleActivationResponse = {
  activity_id: number;
  items: import('./analytics-types').MuscleActivationRow[];
  training_type_targets?: TrainingTypeWeeklyTarget[] | null;
};

// Formulário de lançar/editar um exercício dentro de um treino já aberto.
export type ExerciseFormState = {
  exercise_name: string;
  equipment_id: string;
  muscle_group: string | null;
  workout_plan_exercise_id: string;
  muscle_pain: number | null;
  joint_pain: boolean;
  sets: Array<{
    set_number: number;
    // `null` só em série por tempo (Lote T5).
    reps: number | null;
    weight_kg: number;
    rpe: number | null;
    // Campos sem controle na tela, carregados só para voltar intactos no PUT
    // (ver lib/exercise-set-edit.ts). Ausentes numa série nova.
    set_type?: string;
    rest_seconds?: number | null;
    notes?: string | null;
    hr_avg_bpm?: number | null;
    hr_max_bpm?: number | null;
    muscle_pain?: number | null;
    joint_pain?: boolean | null;
    technique_code?: string | null;
    // Lote T5: medida e segundos da série (round-trip do PUT; ausente = reps).
    measure?: string | null;
    duration_seconds?: number | null;
    // M102: lados da série unilateral (round-trip do PUT; o servidor recalcula
    // reps/RPE oficiais pelo pior lado).
    reps_left?: number | null;
    reps_right?: number | null;
    rpe_left?: number | null;
    rpe_right?: number | null;
    // `anchor_set_number` aponta para o set_number GRAVADO da âncora;
    // `source_set_number` é o set_number gravado desta série. Os dois
    // sobrevivem à renumeração da tela para a âncora ser remapeada no PUT.
    anchor_set_number?: number | null;
    source_set_number?: number;
  }>;
};


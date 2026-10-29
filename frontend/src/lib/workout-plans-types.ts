// Tipos da tela Planos de treino (Fase 4, etapa 13). Contrato confirmado
// lendo `C:\muscle_fit\api\app\workout_plans.py`, `models.py`
// (WorkoutPlanIn/WorkoutPlanExerciseIn/WorkoutPlanGenerateIn),
// `services.py` (get_workout_plan/list_workout_plans/create_workout_plan),
// `workout_plan_ai.py` (generate_plan_draft, full body) e
// `abc_workout_template.py`/`abc_workout_ai.py` (generate_abc_plan_draft(_ai),
// fluxo ABC) — não copiado do frontend legado sem checar contra o backend
// novo.
//
// Fase 3 (paridade com onpremise): seleção de "Modelo de Ficha de Treino
// Personalizado" (template_id/template_slot_sequence) foi incorporada à
// geração/salvamento desta tela — ver GenerateDraftModal.tsx e
// `GET /workout-plan-templates/assigned-to-me`. CRUD de modelos em si
// ("Metas de Ativação" etc.) continua fora de escopo aqui.

import type { AdvancedTechniqueParameters } from './workout-mode-intensive';
import type { RecoverySignal } from './intensity-status';
import type { SessionLoadPlan } from './workout-mode-load-suggestions';

export type ApiListResponse<T> = { items: T[] };

export const WORKOUT_PLAN_STATUS_OPTIONS = [
  { value: 'planned', label: 'Planejado' },
  { value: 'completed', label: 'Concluído' },
  { value: 'skipped', label: 'Pulado' },
] as const;

// LEITURA de fichas legadas (PR-18, §12 do PLANO_20260912.md). Desde o PR-18
// a geração exige um Modelo de Treino e o usuário não escolhe mais "tipo de
// plano"/"letra do ciclo" — os antigos PLAN_TYPE_OPTIONS/SPLIT_DAY_OPTIONS,
// que eram opções de FORMULÁRIO, saíram. O que fica é só o rótulo: fichas
// geradas antes do PR-18 continuam com `plan_type`/`split_day` gravados e
// precisam aparecer corretamente na listagem e no detalhe.
export const LEGACY_PLAN_TYPE_LABELS: Record<string, string> = {
  full_body: 'Full Body (IA)',
  abc: 'ABC por alças musculares',
};

/** Rótulo de `plan_type` de ficha legada; valor desconhecido aparece cru. */
export function legacyPlanTypeLabel(planType: string | null | undefined): string {
  if (!planType) return '-';
  return LEGACY_PLAN_TYPE_LABELS[planType] || planType;
}

/**
 * Rótulo da coluna "Tipo" (listagem) e da badge do detalhe de uma ficha.
 *
 * Ficha gerada de Modelo (`template_id` preenchido) mostra o NOME do Modelo:
 * desde o M24 o "tipo de modelo" saiu da tela e todo Modelo novo grava o
 * default `plan_type='full_body'`, então o rótulo legado ("Full Body (IA)")
 * seria falso para ela. O rótulo legado fica só para fichas sem Modelo
 * (pré-PR-18). Se o nome não vier (Modelo removido), cai em "Modelo #<id>".
 */
export function planTypeDisplayLabel(plan: {
  plan_type: string | null | undefined;
  template_id?: number | null;
  template_name?: string | null;
}): string {
  if (plan.template_id != null) {
    const name = plan.template_name?.trim();
    return name || `Modelo #${plan.template_id}`;
  }
  return legacyPlanTypeLabel(plan.plan_type);
}

export type ExerciseProgression = 'increased' | 'decreased' | 'same' | null;

// --- Prescrição congelada (PR-11; api/app/domain/prescription.py) ----------
// O backend congela a prescrição quando a ficha é criada e NUNCA recalcula na
// leitura: abrir a mesma ficha duas vezes devolve exatamente isto. É
// SOMENTE LEITURA para o cliente — não vai no payload de save (o servidor
// preserva o snapshot no PUT; ver workout-plan-payload.ts).

export type PrescriptionClamp = {
  field: string;
  from: number | string;
  to: number | string;
  // 'EQUIPMENT_SAFETY' | 'RECOVERY' | 'LEGACY_PLAN' | 'AI_SUGGESTION'
  reason: string;
};

export type PrescriptionWarning =
  | 'MODEL_TECHNIQUE_INVALID'
  | 'EQUIPMENT_SUBSTITUTED'
  | 'EQUIPMENT_UNRESOLVED'
  // Fix M97: a geração ajustou valores do Modelo ao objetivo do item (detalhe em `goal_adjustments`).
  | 'GOAL_NORMALIZED'
  // M101b: exercício livre no Modelo, escolhido pela geração (não é substituição).
  | 'EXERCISE_AUTO_CHOSEN';

// Fix M97 (2026-10-26): faixa de carga de FORÇA por %1RM congelada pelo motor
// (`progression_engine.e1rm_load_range`). Tudo pronto do backend: a tela só
// formata (`exercise-briefing.formatStrengthLoadRange`), sem número próprio.
export type StrengthLoadRange = {
  e1rm_kg: number;
  pct_min: number;
  pct_max: number;
  load_min_kg: number;
  load_max_kg: number;
  suggested_kg: number;
  step_kg: number;
};

// Um ajuste da geração ao objetivo (`prescription_defaults.normalize_item_for_goal`).
export type PrescriptionGoalAdjustment = {
  field: string;
  from: number | string | null;
  to: number | string | null;
  training_goal: string;
  label: string;
};

// Auditoria de uma substituição feita pelo servidor (M6/M8;
// `api/app/generation/exercise_substitution.py`). `kind: 'EXERCISE'` = o
// exercício do modelo foi trocado por outro do mesmo grupo muscular (M6);
// `'EQUIPMENT'` = mesmo exercício, outro equipamento (D13 caminho a, ou troca
// manual na ficha). `reason` diz por quê.
export type PrescriptionSubstitution = {
  kind: 'EXERCISE' | 'EQUIPMENT';
  // 'EXERCISE_UNAVAILABLE_IN_GYM' | 'EQUIPMENT_UNAVAILABLE_IN_GYM' | 'USER_CHOICE'
  reason: string;
  from_exercise_id: number | null;
  from_equipment_id: number | null;
  to_exercise_id: number | null;
  to_equipment_id: number | null;
  to_gym_equipment_id?: number | null;
};

// `SessionLoadPlan` (M76 Lote 1a/1b): a prescrição EXIBIDA traz as cargas de
// aquecimento/ajuste/técnica já calculadas pelo backend
// (`domain/session_loads.py`), arredondadas no passo do equipamento. Ausentes
// em payloads anteriores ao Lote 1a.
export type WorkoutPlanPrescription = SessionLoadPlan & {
  schema_version: number;
  engine_version: string;
  // 'PLAN_CREATED' (congelada ao salvar) | 'REFRESH' (refresh-prescription)
  source: string;
  frozen_at: string;
  decision_id: number | null;
  phase: string | null;
  decision: string | null;
  sets_count: number | null;
  // Medida da série congelada (Lote T2, aditivo): ausente em snapshot antigo = repetições.
  measure?: string;
  duration_seconds_target?: number | null;
  time_window_seconds?: number | null;
  reps_target: string | null;
  load_target: string | null;
  rpe_target: string | null;
  effective_rpe_target: number | null;
  effective_rpe_ceiling: number | null;
  failure_allowed: boolean;
  // RPE 10 Lote 6/P9 (aditivo; ausente em snapshots antigos): sinais de
  // recuperação que cortaram teto/dose, com texto pronto do backend.
  recovery_signals?: RecoverySignal[] | null;
  advanced_technique: string | null;
  // Fase 5 / Modo Intensivo (aditivo; ausente em snapshot antigo): parâmetros da
  // técnica congelados pelo motor, com o RPE-alvo do protocolo (`target_rpe`).
  advanced_technique_parameters?: AdvancedTechniqueParameters | null;
  requires_user_choice: boolean;
  safety_constraints: { max_safe_rpe: number | null; source: string | null };
  clamps: PrescriptionClamp[];
  warnings: PrescriptionWarning[];
  // Ausente em snapshots anteriores ao M6 (campo aditivo, mesmo schema_version).
  substitution?: PrescriptionSubstitution | null;
  progression: ExerciseProgression;
  progression_note: string | null;
  suggested_load_kg: number | null;
  suggested_reps: number | null;
  reason: string;
  // Fix M97 (aditivos; ausentes em hipertrofia e em snapshot antigo).
  strength_load?: StrengthLoadRange | null;
  goal_adjustments?: PrescriptionGoalAdjustment[] | null;
};

// Teto de segurança de HOJE para o par exercício x equipamento. O overlay só
// RESTRINGE a prescrição congelada, nunca amplia (seção 6.3 do plano).
export type WorkoutPlanSafetyOverlay = {
  effective_rpe_ceiling: number;
  failure_allowed: boolean;
  max_safe_rpe: number;
  safety_source: string | null;
  clamps: PrescriptionClamp[];
};

// 'FROZEN' = tem snapshot; 'LEGACY' = ficha anterior ao PR-11, exibida como
// está, sem recálculo (seção 12).
export type PrescriptionStatus = 'FROZEN' | 'LEGACY';

export const PRESCRIPTION_WARNING_LABELS: Record<PrescriptionWarning, string> = {
  MODEL_TECHNIQUE_INVALID: 'Técnica do modelo descartada',
  EQUIPMENT_SUBSTITUTED: 'Equipamento substituído',
  EQUIPMENT_UNRESOLVED: 'Equipamento não identificado',
  GOAL_NORMALIZED: 'Ajustado ao objetivo',
  EXERCISE_AUTO_CHOSEN: 'Exercício escolhido pelo motor',
};

// Tooltip específico por código (PR-12): o genérico "aviso registrado ao
// congelar" não dizia o que aconteceu nem o que fazer. Cada texto explica a
// causa e a ação esperada do usuário.
export const PRESCRIPTION_WARNING_TOOLTIPS: Record<PrescriptionWarning, string> = {
  MODEL_TECHNIQUE_INVALID:
    'A técnica avançada definida no modelo não é aplicável a este exercício/equipamento e foi descartada ao congelar a prescrição. Faça as séries normalmente, ou troque o equipamento no modo treino por um que suporte a técnica.',
  EQUIPMENT_SUBSTITUTED:
    'O equipamento previsto no modelo não existe nesta academia. O sistema escolheu outro equipamento do mesmo exercício, compatível com a técnica e ao menos tão seguro quanto o original. Confira antes de treinar — você pode trocá-lo no modo treino.',
  EQUIPMENT_UNRESOLVED:
    'Não foi possível identificar nenhum equipamento desta academia para este exercício. Você precisa escolher o equipamento no modo treino antes de iniciar a primeira série.',
  EXERCISE_AUTO_CHOSEN:
    'O modelo deixou o exercício livre para este grupo muscular. A geração escolheu um exercício (e o equipamento dele) desta academia pelas suas estrelas e pelo uso mais antigo (treinos e fichas geradas), variando a cada ficha. Você pode trocá-lo no modo treino.',
  GOAL_NORMALIZED:
    'O modelo tinha valores incompatíveis com o objetivo deste exercício (descanso, repetições, RPE, séries ou técnica). A geração ajustou a ficha ao perfil do objetivo; o modelo continua salvo como foi digitado.',
};

// M6: quando nem o equipamento nem outro equipamento do exercício existem na
// academia, o servidor troca o EXERCÍCIO por outro do mesmo grupo muscular
// (ranking determinístico, sem IA). Mesmo código de aviso, rótulo/tooltip
// próprios — a auditoria fica em `prescription.substitution`.
export const EXERCISE_SUBSTITUTED_LABEL = 'Exercício substituído';
export const EXERCISE_SUBSTITUTED_TOOLTIP =
  'Nenhum equipamento desta academia atende ao exercício previsto no modelo. O sistema o substituiu por outro exercício do mesmo grupo muscular, com equipamento desta academia, compatível com a técnica e ao menos tão seguro quanto o original (ranking por suas estrelas e segurança, sem IA). Confira antes de treinar — você pode trocar no modo treino.';

/** Rótulo e tooltip do aviso `EQUIPMENT_SUBSTITUTED`, distinguindo a troca de exercício (M6) da de equipamento. */
export function substitutionWarningPresentation(
  substitution: PrescriptionSubstitution | null | undefined,
): { label: string; tooltip: string } {
  if (substitution?.kind === 'EXERCISE') {
    return { label: EXERCISE_SUBSTITUTED_LABEL, tooltip: EXERCISE_SUBSTITUTED_TOOLTIP };
  }
  return {
    label: PRESCRIPTION_WARNING_LABELS.EQUIPMENT_SUBSTITUTED,
    tooltip: PRESCRIPTION_WARNING_TOOLTIPS.EQUIPMENT_SUBSTITUTED,
  };
}

// Cor do badge por código: substituição é um aviso (laranja), os demais
// pedem atenção/ação (vermelho).
export const PRESCRIPTION_WARNING_COLORS: Record<PrescriptionWarning, string> = {
  MODEL_TECHNIQUE_INVALID: 'orange',
  EQUIPMENT_SUBSTITUTED: 'orange',
  EQUIPMENT_UNRESOLVED: 'red',
  GOAL_NORMALIZED: 'orange',
  EXERCISE_AUTO_CHOSEN: 'blue',
};

export type RefreshPrescriptionResponse = {
  plan: WorkoutPlan;
  refreshed: number;
};

// --- M8: troca de EQUIPAMENTO de um exercício da ficha ----------------------
// `GET /workout-plans/{id}/exercises/{exercise_id}/equipment-options` e
// `POST .../substitute-equipment` (api/app/workout_plan_equipment.py). Só
// equipamento — o exercício e o grupo não mudam (decisão do usuário). O
// servidor grava o vínculo e RE-DERIVA a prescrição da linha pelo mesmo
// caminho do refresh; o cliente nunca escreve `prescription_json`.

export type PlanExerciseEquipmentOption = {
  // `gym_equipment.id` — o mesmo valor de `preferred_equipment_id`.
  gym_equipment_id: number;
  equipment_id: number;
  equipment_name: string | null;
  photo_url: string | null;
  user_preference_stars: number | null;
  max_safe_rpe: number | null;
  failure_allowed: boolean | null;
  is_current: boolean;
  // D13a: `max_safe_rpe` >= o do equipamento atual e técnica prescrita válida.
  eligible: boolean;
  // 'MAX_SAFE_RPE_LOWER' | 'TECHNIQUE_INCOMPATIBLE' | null
  reason: string | null;
};

export type PlanExerciseEquipmentOptionsResponse = {
  plan_id: number;
  workout_plan_exercise_id: number;
  mapping_id: number;
  exercise_name: string | null;
  current_gym_equipment_id: number | null;
  current_max_safe_rpe: number | null;
  required_technique: string | null;
  items: PlanExerciseEquipmentOption[];
};

export type SubstituteEquipmentResponse = {
  plan: WorkoutPlan;
  // A linha já relida do servidor (prescrição recongelada).
  exercise: WorkoutPlanExercise | null;
};

export function planExerciseEquipmentOptionsPath(planId: number, exerciseId: number): string {
  return `/workout-plans/${planId}/exercises/${exerciseId}/equipment-options`;
}

export function planExerciseSubstituteEquipmentPath(planId: number, exerciseId: number): string {
  return `/workout-plans/${planId}/exercises/${exerciseId}/substitute-equipment`;
}

/** Motivo de inelegibilidade (D13a) em texto para o usuário. */
export function equipmentOptionReasonLabel(reason: string | null, currentMaxSafeRpe: number | null): string {
  switch (reason) {
    case 'MAX_SAFE_RPE_LOWER':
      return currentMaxSafeRpe != null
        ? `Teto de RPE abaixo do equipamento atual (${currentMaxSafeRpe}) — a troca nunca reduz a segurança.`
        : 'Sem segurança conhecida para este par.';
    case 'TECHNIQUE_INCOMPATIBLE':
      return 'Incompatível com a técnica prescrita nesta linha.';
    default:
      return reason ? `Indisponível (${reason}).` : '';
  }
}

// Card ilustrado PUBLICADO de um exercício (cards ilustrados, PR5): a
// imagem 2:3 completa (logo, título, cenas "1 Posição inicial"/"2 Movimento",
// músculos) da versão vigente no catálogo canônico. URLs imutáveis por versão
// (CDN em produção, LocalStack em DEV), montadas pelo backend
// (`exercise_card_storage.training_card`). Só existe depois dos 3 uploads —
// a UI confia no objeto não nulo e nunca depende de `onError` (a CDN devolve
// `index.html` 200 em 403/404).
export type ExerciseCardSummary = {
  url: string;
  thumbnail_url: string;
  version: number;
};

// M100: padrão do exercício laboral resolvido pelo backend.
export type LaboralPrescription = {
  sets: number;
  reps: number;
  load_kg: number | null;
};

// Exercício de uma ficha — usado tanto para o rascunho gerado (sem
// workout_plan_exercise_id/exercise_description ainda) quanto para uma
// ficha salva (GET /workout-plans/{id}, com esses campos preenchidos).
export type WorkoutPlanExercise = {
  workout_plan_exercise_id?: number;
  sequence: number;
  exercise_name: string;
  amazfit_name?: string | null;
  mapping_id?: number | null;
  preferred_equipment_id?: number | null;
  muscle_group_id?: number | null;
  muscle_primary?: string | null;
  muscle_secondary?: string | null;
  equipment_name?: string | null;
  sets_count?: number | null;
  reps_target?: string | null;
  load_target?: string | null;
  rpe_target?: string | null;
  rest_seconds?: number | null;
  warmup?: boolean;
  substitution_options?: string[] | null;
  notes?: string | null;
  exercise_description?: string | null;
  // Exercício LABORAL do catálogo (M100; GET /workout-plans/{id} e rascunho
  // do Modo B): sem progressão; no Modo Treino, card só leitura com
  // Concluir/Pular. Read-only: nunca vai no save.
  is_laboral?: boolean;
  // Séries, reps e carga (kg TOTAL) prontas do dono (`domain/laboral.py`):
  // o Modo Treino só exibe e grava. `null` quando o exercício não é laboral.
  laboral?: LaboralPrescription | null;
  // Card ilustrado publicado do exercício CANÔNICO do item (GET
  // /workout-plans/{id}; resolve o id legado). `null` = sem card (nada
  // aparece); ausente = payload que não o traz (rascunho, continuação
  // sintética do modo treino). Read-only: nunca vai no save.
  exercise_card?: ExerciseCardSummary | null;
  // M102: exercício unilateral no catálogo (GET /workout-plans/{id}). O Modo
  // Treino pede reps/RPE por lado (E/D) na série de trabalho/backoff.
  is_unilateral?: boolean;
  // Full Body (via IA): indicador de progressão vs. sessão anterior do
  // mesmo exercício (progression_service, mesmo motor do fluxo ABC).
  progression?: ExerciseProgression;
  // ABC (template fixo/IA): sugestão determinística de carga/reps
  // (progression_service.suggest_load_and_reps).
  suggested_load_kg?: number | null;
  suggested_reps?: number | null;
  progression_note?: string | null;
  // Técnica avançada + aquecimento/ajuste + pico de contração do modo
  // "Equipamentos e séries" do builder de modelos (WorkoutPlanExerciseIn em
  // models.py) — persistidos em workout_plan_exercises e devolvidos por
  // GET /workout-plans/{id}. Populados só quando configurados no modelo de
  // origem. Não editáveis na ficha (só exibidos, ExercisesTable.tsx); usados
  // pelo "Fazer treino avançado" do modo treino (PlanExecutionScreen.tsx).
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
  // Medida da série (Lote T2, migração 089): copiada do Modelo na geração;
  // `reps` | `duration` | `max_reps` (lib/set-measures.ts). Ausente = repetições.
  measure?: string | null;
  duration_seconds_target?: number | null;
  time_window_seconds?: number | null;
  // Objetivo de treino da linha (M97, coluna da 093): o resolvido na geração e
  // carimbado pelo servidor no congelamento. Ecoado no save (ausente = o
  // servidor preserva o gravado).
  training_goal?: string | null;
  // Prescrição CONGELADA (PR-11) — read-only, nunca enviada no save.
  // `null` com prescription_status='LEGACY' = ficha anterior ao PR-11.
  prescription?: WorkoutPlanPrescription | null;
  prescription_status?: PrescriptionStatus;
  safety_overlay?: WorkoutPlanSafetyOverlay | null;
  // Passo de carga do equipamento da linha (`equipment.load_increment_kg`;
  // NULL no cadastro vira o default do backend). Usado pelo stepper de carga
  // do Modo Treino. Ausente em continuações sintéticas antigas.
  load_increment_kg?: number | null;
  // Exercício que exige uma escolha do usuário antes de executar (ex.:
  // técnica prescrita inválida para o equipamento).
  requires_user_choice?: boolean;
};

export type WorkoutPlanExerciseInput = {
  sequence: number;
  // Id da linha já salva, ecoado no PUT: o servidor trava o grupamento
  // muscular dela (`workout_plan_edit_rules`). null = linha nova.
  workout_plan_exercise_id?: number | null;
  exercise_name: string;
  amazfit_name?: string | null;
  mapping_id?: number | null;
  preferred_equipment_id?: number | null;
  muscle_primary?: string | null;
  muscle_secondary?: string | null;
  equipment_name?: string | null;
  sets_count?: number | null;
  reps_target?: string | null;
  load_target?: string | null;
  rpe_target?: string | null;
  rest_seconds?: number | null;
  warmup: boolean;
  substitution_options?: string[] | null;
  notes?: string | null;
  // Mesmos 10 campos avançados de WorkoutPlanExercise acima — precisam ir
  // no save, senão o PUT grava NULL por cima (ver lib/workout-plan-payload.ts).
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
  // Medida da série (Lote T2): também vai no save (o rascunho gerado vira a
  // ficha por este payload; sem ela a medida do Modelo se perderia).
  measure?: string | null;
  duration_seconds_target?: number | null;
  time_window_seconds?: number | null;
  // M97 F3: objetivo da linha, ecoado como veio (`undefined` some do JSON).
  training_goal?: string | null;
};

export type WorkoutPlanListItem = {
  id: number;
  plan_date: string | null;
  plan_time: string | null;
  gym_id: number | null;
  gym_name: string | null;
  name: string;
  plan_type: string;
  split_day: string | null;
  // Modelo de origem (LEFT JOIN em workout_plan_templates); null em ficha
  // legada, sem Modelo.
  template_id: number | null;
  template_name: string | null;
  goal: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  exercises_count: number;
};

export type WorkoutPlan = {
  id: number;
  plan_date: string | null;
  plan_time: string | null;
  gym_id: number | null;
  gym_name: string | null;
  name: string;
  plan_type: string;
  split_day: string | null;
  template_id: number | null;
  template_name: string | null;
  template_slot_sequence: number | null;
  // Versão do Modelo da qual a ficha nasceu (migração 066). null em ficha
  // antiga ou sem Modelo.
  template_version_id?: number | null;
  goal: string | null;
  status: string;
  // Os exercícios podem ser editados? Decidido pelo servidor
  // (`workout_plan_edit_rules.is_plan_editable`): só ficha não finalizada.
  editable?: boolean;
  raw_payload: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  exercises: WorkoutPlanExercise[];
};

export type WorkoutPlanInput = {
  plan_date?: string | null;
  plan_time?: string | null;
  name: string;
  plan_type: string;
  split_day?: string | null;
  goal?: string | null;
  status: string;
  gym_id?: number | null;
  gym_name?: string | null;
  raw_payload?: Record<string, unknown> | null;
  template_id?: number | null;
  template_slot_sequence?: number | null;
  // Versão do Modelo em que o rascunho foi gerado (066). O servidor valida
  // que ela pertence ao `template_id`; se não pertencer, grava null.
  template_version_id?: number | null;
  // Ausente no PUT = não tocar nos exercícios (ficha finalizada só salva o
  // cabeçalho). `[]` explícito = ficha sem exercícios.
  exercises?: WorkoutPlanExerciseInput[];
};

// Entrada de POST /workout-plans/generate (api/app/models.py,
// WorkoutPlanGenerateIn). PR-18: `template_id` é OBRIGATÓRIO — não existe
// mais geração sem Modelo de Treino, e o backend responde 422 sem ele.
// `plan_type`/`split_day` saíram deste payload (eram a porta dos caminhos
// ABC/Full Body).
export type WorkoutPlanGenerateInput = {
  gym_id?: number | null;
  gym_name?: string | null;
  plan_date?: string | null;
  plan_time?: string | null;
  goal?: string | null;
  min_rest_seconds: number;
  max_rest_seconds: number;
  // Modelo de Treino atribuído ao usuário; o `volume_mode` dele decide o
  // motor (Modo A/Modo B). template_slot_sequence null = próximo slot
  // automático do ciclo; um valor força esse slot específico.
  template_id: number;
  template_slot_sequence?: number | null;
};

// Um grupo muscular cujo volume-alvo do slot nao foi alcancado.
//
// ATENCAO: TODOS os campos numericos sao opcionais de proposito. Existem dois
// formatos em circulacao e a tela tem de aguentar os dois:
//  - legado (abc_volume_reinforcement.py, workout_plan_templates_engine.py):
//    `equivalent_sets_achieved` / `equivalent_sets_target` / `exercises_added`;
//  - motor deterministico (generation/mode_a.py, mode_a_legacy.py): passou um
//    periodo emitindo SO `deficit_equivalent_sets`, sem os campos acima — e
//    fichas geradas nesse periodo ja estao gravadas em `raw_payload`.
// O mode_a voltou a emitir o contrato completo, mas o dado antigo nao muda:
// nunca acesse estes campos sem checar (ver `formatInsufficientVolumeGroup`).
export type InsufficientTargetVolumeGroup = {
  muscle_primary?: string | null;
  equivalent_sets_achieved?: number | null;
  equivalent_sets_target?: number | null;
  exercises_added?: number | null;
  deficit_equivalent_sets?: number | null;
  reason?: string | null;
};

// Prescrição de cardio pronta (`cardio_prescription.summarize` no backend):
// campos normalizados, tempo total em segundos e o texto canônico. Só exibição.
export type CardioPrescriptionSummary = {
  cardio_mode: string | null;
  cardio_duration_minutes: number | null;
  cardio_warmup_minutes: number | null;
  cardio_cooldown_minutes: number | null;
  cardio_target_zone: number | null;
  hiit_low_seconds: number | null;
  hiit_high_seconds: number | null;
  hiit_low_zone: number | null;
  hiit_high_zone: number | null;
  hiit_rounds: number | null;
  total_seconds: number;
  text: string;
};

// Rascunho gerado por POST /workout-plans/generate (via GET /jobs/{job_id}),
// NÃO persistido — só vira ficha real após POST /workout-plans. Formato
// unificado (mesmas chaves) entre o fluxo Full Body (workout_plan_ai.py) e o
// fluxo ABC (abc_workout_template.py/abc_workout_ai.py).
export type WorkoutPlanDraft = {
  name: string;
  plan_type: string;
  split_day?: string | null;
  goal?: string | null;
  generation_summary?: string | null;
  // Presentes quando o rascunho veio de um Modelo de Ficha de Treino
  // Personalizado (payload.template_id na geração) — ver
  // workout_plan_templates_engine(_v2).py. null/undefined = fluxo estático
  // de sempre (ABC/Full Body), sem modelo.
  template_id?: number | null;
  template_slot_sequence?: number | null;
  // Versão do Modelo lida na geração (066): repassada no save para a ficha
  // congelar a estrutura que o usuário viu, mesmo se o Modelo for editado.
  template_version_id?: number | null;
  template_slot_targets?: unknown[] | null;
  // Cardio do slot do Modelo (M98 C0; dono `domain/cardio_prescription.py`):
  // minutos totais (aquecimento + parte principal + recuperação) e a
  // prescrição de cada item de cardio, com o texto canônico pronto. Vão para
  // o `raw_payload` no save (`buildDraftRawPayload`): a ficha não tem coluna
  // de cardio. Opcionais: rascunho de API anterior não os tem.
  cardio_minutes?: number | null;
  cardio_prescriptions?: CardioPrescriptionSummary[];
  exercises: WorkoutPlanExercise[];
  excluded_due_to_recovery?: Array<{ exercise_name?: string; muscle_primary?: string | null }>;
  excluded_due_to_duplicate?: Array<Record<string, unknown>>;
  adjusted_due_to_catalog_mismatch?: Array<{ exercise_name?: string; original_exercise_name?: string }>;
  adjusted_due_to_repeat_equipment?: Array<Record<string, unknown>>;
  excluded_due_to_no_equipment?: Array<{ exercise_name?: string; muscle_primary?: string | null }>;
  reduced_dose_groups?: string[];
  insufficient_target_volume_groups?: InsufficientTargetVolumeGroup[];
  plan_date?: string | null;
  gym_id?: number | null;
  gym_name?: string | null;
  raw_payload?: Record<string, unknown> | null;
};

// Contrato de POST /analytics/muscle-activation-preview — ver
// `VolumeService.get_muscle_activation_for_exercises`. Mesma linha de
// GET /analytics/muscle-activation-by-activity (`MuscleActivationRow`, já em
// `analytics-types.ts`).
// M76 E3: payload único de ativação (o mesmo shape do item do Modelo); o
// backend soma o equivalente da técnica avançada.
export type MuscleActivationPreviewItem = {
  mapping_id: number | null;
  sets_count: number | null;
  advanced_technique: string | null;
  advanced_technique_applications: number | null;
  // M100: linha de exercício laboral (a ficha expõe `is_laboral`): o backend
  // planeja 0 séries para ela. Ausente = não laboral.
  is_laboral?: boolean;
};

export type MuscleActivationPreviewInput = {
  exercises: MuscleActivationPreviewItem[];
};

// Contrato de GET /exercises/suggested-progression?mapping_id=&equipment_id=
// — `services.suggested_progression_for_mapping`, que desde o M76 (Lote 1a)
// usa o motor v2 por par (`domain/prescription_service.suggest_for_pair`), já
// composto com a segurança do par e a recuperação projetada. Usada ao trocar
// o exercício de uma linha na ficha (ExercisesTable.tsx), ao trocar de
// equipamento e no treino livre do Modo Treino. Os campos aditivos do Lote 1a
// são opcionais aqui só para tolerar uma API anterior durante o deploy.
// Com o grupo em NO_DIRECT_WORK (decisão SKIP) não há faixa: `reps_target`
// vem vazio e `suggested_reps` null. O backend manda '' nos três alvos de
// texto, mas o tipo admite null — leia via `suggestedProgressionTargets`.
export type SuggestedProgressionResponse = {
  reps_target: string | null;
  load_target: string | null;
  rpe_target: string | null;
  progression_note: string | null;
  has_history: boolean;
  progression?: ExerciseProgression;
  // Topo da faixa do motor; null quando não há faixa (SKIP).
  suggested_reps?: number | null;
  last_load_kg?: number | null;
  last_reps?: number | null;
  trend?: string;
  last_date?: string | null;
  // Carga sugerida pelo motor (ou a da calibração pela última execução).
  suggested_load_kg?: number | null;
  load_source?: 'engine' | 'last_execution' | null;
  // Passo do equipamento da trilha usada (stepper e arredondamento).
  load_increment_kg?: number | null;
  equipment_id?: number | null;
  decision?: string | null;
  // Teto e falha FINAIS do par (segurança + recuperação projetada).
  effective_rpe_ceiling?: number | null;
  failure_allowed?: boolean;
  clamps?: PrescriptionClamp[];
  engine_version?: string;
};

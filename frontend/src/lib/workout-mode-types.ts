// Tipos e helpers do domínio "Modo Treino" (`/modo-treino`, Fase 4, etapa
// 25). Reaproveita ao máximo os tipos já existentes de outras telas —
// `workout-log-types.ts` (mesmo domínio de sessão ao vivo/atividade usado por
// `WorkoutDetailClient.tsx`) e `workout-plans-types.ts` (fichas/exercícios de
// plano) — em vez de duplicar.
//
// Contratos confirmados lendo diretamente `C:\muscle_fit\api\app\main.py`
// (`/exercises/*`, `/activities/*`) e `C:\muscle_fit\api\app\workout_sessions.py`
// (`SessionStartIn`/`SessionMetaIn`/`SessionExerciseIn`/`SessionSetIn`), não
// copiados sem checar do projeto de referência
// `C:\openclaw-dev-agent\fitness-frontend\src\features\workout-mode\types.ts`
// (aquele usava um proxy server-side que não existe mais nesta arquitetura
// estática — aqui toda chamada é via `apiFetch` direto na API).
//
// "Fazer treino avançado" (rest-pause/drop-set/FST-7/aquecimento/série de
// ajuste/pico de contração configurados em modelos de ficha, modo
// `equipment_series` do builder) usa os campos
// `advanced_technique`/`warmup_*`/`feeler_set_*`/`peak_contraction_seconds`
// já presentes em `WorkoutPlanExercise` (`workout-plans-types.ts`) — ver
// `AdvancedLocalStageProgress` abaixo e a orquestração em
// `modo-treino/PlanExecutionScreen.tsx`. Portado de
// `C:\openclaw-dev-agent\fitness-frontend\src\features\workout-mode\types.ts`.

import type { IntensiveBlockState } from './workout-mode-intensive';
import type { LoadAdjustment, NextSetLoad } from './set-engine-status';
import type { StoredRestTimer } from './workout-mode-rest-timer';
import {
  INTENSITY_STATUS_COLORS,
  INTENSITY_STATUS_LABELS,
  SAFETY_EVENT_LABELS,
  SAFETY_EVENT_RPE_ABOVE_CEILING,
  SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET,
} from './intensity-status';
import { isPreparatorySetType, normalizeSetType } from './set-types';
import type { EquipmentRecommendItem, EquipmentRecommendResponse } from './equipment-recommend';
import type {
  ActivityDetail,
  ApiList,
  ExerciseLogRow,
  Gym,
  GymEquipmentLink,
  GymEquipmentResponse,
  MuscleGroupsResponse,
  WorkoutPlanDetail,
  WorkoutPlanExercise as ExerciseLogWorkoutPlanExercise,
} from './workout-log-types';
import type { PrescriptionClamp, WorkoutPlanExercise, WorkoutPlanListItem } from './workout-plans-types';

// Reexport de conveniencia: as telas de execucao ja importam tudo deste
// modulo. A fonte dos rotulos e `intensity-status.ts`, compartilhada com os
// dashboards.
export {
  INTENSITY_STATUS_COLORS,
  INTENSITY_STATUS_LABELS,
  SAFETY_EVENT_LABELS,
  SAFETY_EVENT_RPE_ABOVE_CEILING,
  SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET,
};

export type {
  ActivityDetail,
  ApiList,
  ExerciseLogRow,
  Gym,
  GymEquipmentLink,
  GymEquipmentResponse,
  MuscleGroupsResponse,
  WorkoutPlanDetail,
};

// ---------------------------------------------------------------------------
// Sessão ativa persistida em localStorage — protege contra o navegador
// descartar a aba/PWA em background no meio do treino. Deliberadamente NÃO
// guarda índice de exercício/série atuais: o progresso real é sempre
// recalculado a partir de `GET /activities/{session_id}` (o que já foi
// lançado) + `GET /workout-plans/{id}` (o que está previsto) — mesma lógica
// de "já lançado" de `WorkoutDetailClient.tsx`. Mais robusto que manter um
// índice manual em sincronia: mesmo fechando a aba no meio de uma série, o
// estado real do banco é a única fonte de verdade ao reabrir.
// ---------------------------------------------------------------------------

// Substituição de equipamento escolhida em `PlanExecutionScreen` (Parte B,
// via `EquipmentSubstituteModal`), chaveada por `workout_plan_exercise_id`.
export type WorkoutModeEquipmentOverride = {
  mapping_id: number;
  equipment_link_id: number;
  exercise_name: string;
  equipment_name: string;
};

// Estado das séries de aquecimento (20%/40% da carga planejada) sugeridas
// antes das séries de trabalho de um exercício, quando o grupo muscular
// primário ainda não foi ativado nesta sessão de execução real. Chaveado por
// `workout_plan_exercise_id`. Aquecimentos CONCLUÍDOS são gravados no backend
// como `set_type: 'warmup'` (fora dos agregados/progressão); os pulados ficam
// só neste estado local. Reconciliado com o backend por
// `reconcileWarmupState`.
export type WarmupState = { first: 'pending' | 'done' | 'skipped'; second: 'pending' | 'done' | 'skipped' };

// Progresso LOCAL das sub-etapas de aquecimento/ajuste/técnica do modo
// "Fazer treino avançado", chaveado por `workout_plan_exercise_id` — mesmo
// espírito de `WarmupState` acima (client-side, nunca gravado no backend,
// não entra em histórico/analytics/recuperação muscular), mas um conceito
// PARALELO/DIFERENTE: aqui as sub-etapas são REAIS (configuradas na ficha
// via `warmup_reps`/`feeler_set_reps`), com contagem simples de "quantas já
// foram marcadas como concluídas/puladas" em vez do first/second fixo de
// `WarmupState`. Aquecimento e série de ajuste CONCLUÍDOS são gravados no
// backend com `set_type` `warmup`/`feeler` (o backend os exclui de
// agregados, progressão de carga e e1RM; o frontend os exclui da contagem
// "Série N de S" e da carga de referência). Os contadores daqui incluem
// também as etapas PULADAS (nunca gravadas); o progresso efetivo é o maior
// entre este contador e o que já está gravado, então perder o localStorage
// não duplica séries já enviadas. `techniqueSkipped` registra quantas
// aplicações da técnica foram puladas (para não travar a contagem de
// "próxima aplicação" quando o usuário pula uma).
export type AdvancedLocalStageProgress = { warmupDone: number; feelerDone: number; techniqueSkipped: number };

// Continuação sintética de um exercício encerrado no meio (algumas séries já
// logadas) trocando de equipamento, mantendo o mesmo grupo muscular. Vira um
// `WorkoutPlanExercise` "virtual" (sem `workout_plan_exercise_id` real, ver
// `isSyntheticPlanExerciseId` abaixo) injetado na fila de pendentes com
// `sets_count: remainingSets` como alvo local. O exercício original é
// marcado em `cancelledIds` (removido da fila pendente, mas as séries já
// logadas continuam no histórico da atividade). Escopo de implementação:
// Parte B (`PlanExecutionScreen`/`EquipmentSubstituteModal`) — tipo definido
// aqui para o contrato ficar estável desde já.
export type MidExerciseExtra = {
  localId: number; // negativo, único, só para chave local (skip/cancel/priority)
  exercise_name: string;
  mapping_id: number;
  equipment_link_id: number;
  equipment_name: string;
  muscle_primary?: string | null;
  muscle_secondary?: string | null;
  sets_count: number;
  reps_target?: string | null;
  load_target?: string | null;
  rpe_target?: string | null;
  rest_seconds?: number | null;
  // `workout_plan_exercise_id` do item ORIGINAL da ficha que esta
  // continuação sintética substitui. Usado ao vincular o exercício real
  // criado no backend (`exercises.workout_plan_exercise_id`) ao item do
  // plano, em vez de enviar null — sem isso o item fica órfão e nunca
  // aparece como "Já lançado"/"Trocado" em `WorkoutDetailClient`. Null
  // quando o exercício original já não tinha vínculo com o plano.
  originalWorkoutPlanExerciseId: number | null;
  // Configuração avançada herdada do item original (aquecimento/ajuste/
  // técnica/pico de contração) — sem ela a continuação ia direto às séries
  // de trabalho e pulava a série de ajuste com o equipamento novo. Opcionais:
  // snapshots antigos do localStorage não têm estes campos.
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
  // Lote T5: medida do item original (ausente = repetições).
  measure?: string | null;
  duration_seconds_target?: number | null;
  time_window_seconds?: number | null;
  // M76 (Lote 1b): passo e teto do PAR NOVO, lidos de
  // `GET /exercises/suggested-progression` na hora da troca (o backend já
  // compõe segurança + recuperação). Opcionais: snapshots antigos do
  // localStorage não têm estes campos (sem eles, stepper no default e sem teto).
  load_increment_kg?: number | null;
  effective_rpe_ceiling?: number | null;
  failure_allowed?: boolean | null;
};

export type StoredWorkoutModeSession = {
  session_id: number;
  source: 'plan' | 'freeform';
  workout_plan_id?: number | null;
  gym_id?: number | null;
  conducted_by?: string | null;
  // Itens da ficha pulados (reinseridos no fim da fila desta sessão) ou
  // cancelados (removidos de vez), chaveados por `workout_plan_exercise_id`.
  skipped_exercise_ids?: number[];
  cancelled_exercise_ids?: number[];
  // Exercício escolhido em `SkipExerciseModal` para ser o PRÓXIMO a aparecer,
  // com precedência sobre a ordem/sequence original e sobre
  // `skipped_exercise_ids`. Null quando nenhuma escolha manual está ativa.
  jump_target_exercise_id?: number | null;
  // Substituições de equipamento feitas via `EquipmentSubstituteModal`
  // (Parte B) nesta sessão, chaveadas por `workout_plan_exercise_id`. Chaves
  // de objeto viram string em JSON — rehidratar ao ler do localStorage.
  equipment_overrides?: Record<number, WorkoutModeEquipmentOverride>;
  // Estado das séries de aquecimento resolvidas nesta sessão, chaveado por
  // `workout_plan_exercise_id` — mesmo motivo/padrão de persistência de
  // `equipment_overrides` acima.
  warmup_state?: Record<number, WarmupState>;
  // Continuações sintéticas de exercícios encerrados no meio com troca de
  // equipamento — persistidas pelo mesmo motivo dos campos acima.
  mid_exercise_extras?: MidExerciseExtra[];
  // Progresso local das sub-etapas de aquecimento/ajuste/técnica do modo
  // "Fazer treino avançado" (ver `AdvancedLocalStageProgress` acima) —
  // persistido pelo mesmo motivo/padrão de `warmup_state` acima (sobreviver
  // a reload/aba suspensa durante o treino).
  advanced_stage_progress?: Record<number, AdvancedLocalStageProgress>;
  // Modo avançado da sessão: ativa as sub-etapas reais (aquecimento/ajuste/
  // trabalho/aplicações da técnica) em `PlanExecutionScreen`. Desde o PR B4
  // da Evolução ZEPP decidido pela FICHA ao iniciar a sessão
  // (`resolveWorkoutModeFromPlan`, sem escolha do usuário) e gravado aqui uma
  // vez: o modo não muda no meio do treino. Sessão salva antes do B4 mantém o
  // valor escolhido à mão; ausente/false = comportamento normal.
  advanced_mode?: boolean;
  // Chaves (`wpe-<id>`/`mapping-<id>`) dos exercícios cujo briefing já foi
  // lido nesta sessão (novo visual, PR1/3): um reload entre "Iniciar
  // exercício" e a 1ª série não reabre o briefing. Ausente em sessão salva
  // antes do campo = nenhum briefing lido (comportamento anterior).
  briefed_keys?: string[];
  // Descanso em andamento (novo visual, PR3/3): fim ABSOLUTO do relógio
  // (`rest_ends_at`, epoch ms) + a chave da série/exercício que o abriu, para
  // um reload reconstruir a tela de descanso sem reiniciar a contagem. Pular
  // ou terminar o descanso grava `null`. Ausente em sessão salva antes do
  // campo = sem descanso (comportamento anterior). Ver `restoreRestTimer`.
  rest_timer?: StoredRestTimer | null;
  // Modo Intensivo (FST-7): estado do bloco por `workout_plan_exercise_id`
  // (fase, série atual, carga planejada, pilha de desfazer, séries ainda não
  // confirmadas e o descanso do bloco). Validado na leitura por
  // `reconcileIntensiveBlock`, que também cruza com o servidor.
  intensive_blocks?: Record<number, IntensiveBlockState>;
};

// Persistência em localStorage (load/save/park/clear e a preferência da IA por
// série): `workout-mode-storage.ts`.

// ---------------------------------------------------------------------------
// Respostas de IA (não persistidas exceto o resumo de treino).
//
// Assíncronas desde a extração da Lambda `musclefit-workout-ai` (mesmo
// padrão 202 + job_id + polling via `GET /jobs/{job_id}` já usado por
// `/workout-plans/generate`/`/cardio-recommendations/generate` — ver
// `AsyncJob<T>` em `analytics-types.ts`): os 3 `POST` abaixo
// (`/exercises/evaluate-set`, `/exercises/{id}/summarize`,
// `/activities/{id}/ai-summary/generate`) agora respondem só
// `{job_id, status}` (HTTP 202); `fetchWorkoutAiResult` (`workout-mode-ai.ts`) encapsula o
// polling e devolve uma Promise com o MESMO shape de resultado de antes
// (`SetEvaluationResult`/`ExerciseSummaryResult`/`WorkoutAiSummaryResult`),
// para os 3 call-sites em `PlanExecutionScreen.tsx`/`FreeformScreen.tsx`/
// `WorkoutModeClient.tsx` continuarem com `.then(...).catch(...)` igual a
// antes, só trocando `apiFetch` por `fetchWorkoutAiResult`.
// ---------------------------------------------------------------------------

// Resposta de `POST /exercises/evaluate-set` — ver `SetEvaluationIn` em
// `models.py` e `exercise_set_evaluation_ai.evaluate_set`.
export type SetEvaluationResult = {
  model: string;
  // set-eval-v4 (RPE 10 Lote 6/P9): código de AÇÃO do motor (HOLD /
  // INCREASE_LOAD / DECREASE_LOAD), rótulo em `category_label`. Resposta
  // antiga em cache pode trazer o vocabulário anterior: a tela mostra neutro.
  category: string;
  category_label?: string | null;
  // A IA respondeu outra categoria e o servidor a alinhou à direção da carga final.
  category_from_ai?: string | null;
  note: string;
  raw_text: string;
  suggested_next_reps?: number | null;
  suggested_next_load_kg?: number | null;
  // Já CLAMPADO no servidor (PR-14, seção 5.7): nunca vem acima do teto seguro
  // do par exercício × equipamento.
  suggested_next_rpe?: number | null;
  // Cortes aplicados à sugestão da IA (hoje só `rpe`), para a UI explicar por
  // que o número mudou. Lista vazia = a IA já sugeriu dentro do teto.
  clamps?: PrescriptionClamp[];
  effective_rpe_ceiling?: number | null;
  failure_allowed?: boolean;
  // M76 Lote 1c: carga sugerida no passo do equipamento e na direção do motor.
  // Ajustes de CARGA (`EQUIPMENT_STEP`/`ENGINE_TARGET_LOAD`), separados do corte de RPE.
  load_adjustments?: LoadAdjustment[];
  load_step_kg?: number | null;
  engine_decision?: string | null;
  // Lote 6/P9: o status do motor que a IA recebeu para esta série.
  set_intensity_status?: string | null;
  set_intensity_status_label?: string | null;
  // set-eval-v6: carga da próxima série pelo motor (a carga sugerida acima já é
  // a dela quando a regra se aplica); null fora da regra.
  next_set_load?: NextSetLoad | null;
};

export type PreviousSetInput = {
  set_number?: number | null;
  set_type?: string | null;
  reps?: number | null;
  weight_kg?: number | null;
  rpe?: number | null;
};

// Body de `POST /exercises/evaluate-set`.
export type SetEvaluationInput = {
  exercise_name: string;
  reps_target?: string | null;
  load_target?: string | null;
  rpe_target?: string | null;
  reps?: number | null;
  weight_kg?: number | null;
  rpe?: number | null;
  set_number?: number | null;
  sets_count?: number | null;
  previous_sets_this_session?: PreviousSetInput[];
  // Par exercício × equipamento (PR-14): `mapping_id` = exercício do catálogo,
  // `equipment_id` = `gym_equipment.id`. O servidor resolve o teto seguro a
  // partir deles; `effective_rpe_ceiling` (da prescrição congelada) só pode
  // restringir mais.
  mapping_id?: number | null;
  equipment_id?: number | null;
  effective_rpe_ceiling?: number | null;
  // Lote 6/P9: tipo da série recém-salva (só `working` tem status do motor).
  set_type?: string | null;
  muscle_pain?: number | null;
  joint_pain?: boolean | null;
  notes?: string | null;
  // R1 (regras de carga): sessão em andamento e `exercises.id` da série, para o
  // SERVIDOR ler a faixa de fadiga prévia do exercício (só regras "qualquer
  // fadiga" casam sem eles).
  session_id?: number | null;
  exercise_id?: number | null;
};

// Resposta de `POST /exercises/{id}/summarize` — resumo de fim de EXERCÍCIO,
// gerado incondicionalmente e descartado ao fim da sessão (não persiste).
export type ExerciseSummaryResult = {
  model: string;
  summary: string;
};

// Resposta de `POST /activities/{id}/ai-summary/generate` — resumo de fim de
// TREINO, gerado incondicionalmente ao finalizar a sessão e PERSISTIDO no
// banco (consultável depois em `GET /activities/{id}`).
export type WorkoutAiSummaryResult = {
  summary: string;
  generated_at: string;
};


// Resposta de `GET /exercises/suggested-progression` — tipo único em
// `workout-plans-types.ts` (M76 Lote 1b).
export type { SuggestedProgressionResponse } from './workout-plans-types';

// Resposta de `GET /exercises/suggested-set-count-for-substitution` — usada
// pelo `EquipmentSubstituteModal` (Parte B) ao trocar de equipamento em
// pleno treino.
export type SuggestedSetCountForSubstitutionResponse = {
  suggested_sets_count: number | null;
  reason: 'no_contribution_to_original_primary_group' | null;
  original_primary_muscle_group: string | null;
  original_stimulus_weight: number;
  new_stimulus_weight: number | null;
  original_sets_count: number;
};

// M79: indicador de fadiga do grupo muscular do exercício, calculado no
// backend pelo dono (`domain/prior_fatigue.group_fatigue_for_sequence`:
// `fatigue_weight` acumulado das ativações anteriores do músculo principal).
// A tela só mostra `band_label` — nunca compara `prior_sets`/`level`.
export type GroupFatigueBand = 'rested' | 'pre_fatigued' | 'fatigued' | 'heavily_fatigued';

export type GroupFatigueReading = {
  muscle_group_id: number | null;
  muscle_group_name: string | null;
  prior_sets: number;
  level: number;
  band: GroupFatigueBand;
  band_label: string;
};

// Uma série da sessão anterior mais recente do mesmo exercício canônico
// (`mapping_id`), de `GET /exercises/last-session-sets` — referência
// histórica exibida no `SetLoggerCard`, nunca como default do input.
export type PreviousSessionSet = {
  set_number: number;
  set_type?: string | null;
  // `null` em série por tempo (Lote T3: reps NULL quando `measure=duration`).
  reps: number | null;
  weight_kg: number | null;
  rpe: number | null;
  // Medida da série (Lote T3, `services._attach_set_measure`): ausente em
  // resposta antiga = repetições. `measure_text` é o texto do dono
  // (`format_set_measure`: "58 s", "15 reps em 30 s").
  measure?: string | null;
  duration_seconds?: number | null;
  measure_text?: string | null;
  // M102: lados da série unilateral, texto pronto do dono
  // (`set_sides.sides_text`: "reps E 10 · D 8"); `null` sem lados.
  sides_text?: string | null;
  // Fase 5 (PR 4): aplicação de técnica avançada vem marcada, com a técnica
  // e a série âncora — nunca é exibida como "Série N".
  technique_code?: string | null;
  anchor_set_number?: number | null;
  is_technique_application?: boolean;
  // M79: fadiga do grupo ANTES desta série, na sessão anterior (opcional:
  // resposta antiga não traz).
  group_fatigue?: GroupFatigueReading | null;
};

// Recorde VIGENTE na chave exercício + equipamento + convenção (regra única do
// dashboard, `session_records`, M76 Lote 3b): carga estritamente maior que
// todas as exposições anteriores; 1ª exposição não é recorde; sem equipamento
// resolvido ou peso corporal → sem recorde (`record: null`). `reps` = as da
// série recorde, só informação. `weight_kg: null` só em resposta antiga.
// M76 Lote 3d: sem recorde vigente (a 1ª exposição ainda detém a marca), o
// backend manda a MELHOR CARGA da mesma chave com `kind: 'best_load'`; com
// recorde, `kind: 'record'`. A tela só escolhe o rótulo ("Melhor carga" ×
// "Recorde"); ausente (resposta antiga) = recorde.
export type PreviousSessionRecordKind = 'record' | 'best_load';

export type PreviousSessionRecord = {
  reps: number;
  weight_kg: number | null;
  kind?: PreviousSessionRecordKind;
};

export type LastSessionSetsResponse = {
  item: {
    activity_id: number;
    local_date: string;
    sets: PreviousSessionSet[];
    // Opcionais: respostas de uma versão anterior do endpoint não os trazem.
    // `equipment` = match estrito pelo equipamento; `exercise` = sem equipamento.
    record_scope?: 'equipment' | 'exercise';
    record?: PreviousSessionRecord | null;
    // e1RM (Epley + RIR do RPE, mesma fórmula do dashboard de progressão),
    // só de séries com até 12 reps (teto de confiabilidade do dashboard).
    e1rm_kg?: number | null;
    // Convenção de carga do recorde (mesma chave do dashboard). `assisted`:
    // o recorde é a MENOR assistência.
    load_convention?: string | null;
  } | null;
  // M79: "Fadiga atual do grupo" na sessão em andamento, antes da 1ª série
  // do exercício. Vem mesmo sem sessão anterior (`item: null`).
  current_group_fatigue?: GroupFatigueReading | null;
};

// ---------------------------------------------------------------------------
// Classificação de séries (`exercise_sets.set_type`): o espelho ÚNICO do dono
// `api/app/set_types.py` é `./set-types` (M76 Lote 3b, vocabulário exportado
// pelo backend). Reexportado aqui para os consumidores antigos.
// ---------------------------------------------------------------------------

export { isPreparatorySetType, normalizeSetType };

export type PreparatorySetType = 'warmup' | 'feeler';

export function countNonPreparatorySets(sets: ReadonlyArray<{ set_type?: string | null }>): number {
  return sets.filter((set) => !isPreparatorySetType(set.set_type)).length;
}

export function countSetsOfType(
  sets: ReadonlyArray<{ set_type?: string | null }>,
  setType: PreparatorySetType,
): number {
  return sets.filter((set) => normalizeSetType(set.set_type) === setType).length;
}

// Próximo `set_number` FÍSICO do exercício (unique `(exercise_id,
// set_number)` no banco) — conta todas as séries gravadas, inclusive
// aquecimento/ajuste. Diferente do número exibido "Série N de S", que conta
// só séries de trabalho/aplicações de técnica.
export function nextPhysicalSetNumber(sets?: ReadonlyArray<{ set_number?: number | null }> | null): number {
  let max = 0;
  for (const set of sets || []) {
    if (typeof set.set_number === 'number' && set.set_number > max) max = set.set_number;
  }
  return max + 1;
}

// Reconcilia o `WarmupState` local com quantos aquecimentos genéricos já
// estão gravados no backend: preenche como `done`, em ordem, as posições
// ainda `pending` até igualar a contagem gravada. Evita oferecer de novo (e
// duplicar) um aquecimento já enviado quando o localStorage foi perdido ou o
// app caiu entre o POST e o save local.
export function reconcileWarmupState(local: WarmupState | undefined, persistedCount: number): WarmupState {
  const result: WarmupState = { ...(local ?? { first: 'pending', second: 'pending' }) };
  let doneCount = (result.first === 'done' ? 1 : 0) + (result.second === 'done' ? 1 : 0);
  for (const key of ['first', 'second'] as const) {
    if (doneCount >= persistedCount) break;
    if (result[key] === 'pending') {
      result[key] = 'done';
      doneCount += 1;
    }
  }
  return result;
}

// Item/resposta de `GET /gym-equipment/recommend(-all)`: tipo ÚNICO em
// `equipment-recommend.ts` (PR-08, G20) — aliases mantidos para os
// consumidores existentes.
export type GymEquipmentRecommendItem = EquipmentRecommendItem;
export type GymEquipmentRecommendResponse = EquipmentRecommendResponse;

// ---------------------------------------------------------------------------
// Helpers puros compartilhados pelas telas de execução (Parte B) e pelos
// componentes de apoio desta etapa (SetLoggerCard/MuscleMapDisclosure).
// ---------------------------------------------------------------------------

// Extrai o primeiro número de uma string alvo tipo "10-12", "40kg", "8-9-10"
// ou "42,5kg" — usada para popular o valor inicial editável dos steppers a
// partir de um alvo em texto livre vindo da ficha/progressão sugerida.
export function parseNumericTarget(target: string | null | undefined): number | null {
  if (!target) return null;
  const normalized = target.replace(',', '.');
  const match = normalized.match(/\d+(\.\d+)?/);
  if (!match) return null;
  const value = Number(match[0]);
  return Number.isFinite(value) ? value : null;
}

// Limita um RPE SUGERIDO (default do stepper, sugestão da IA, RPE repetido da
// última série) ao teto seguro do par (PR-14, §5.7). Só restringe: teto
// ausente ou valor já dentro dele devolvem o valor como veio. O que o usuário
// REGISTROU nunca é reescrito — isto vale para o que a tela OFERECE.
export function clampRpeSuggestion(value: number | null, ceiling: number | null | undefined): number | null {
  if (value == null || ceiling == null) return value;
  return Math.min(value, ceiling);
}

// Um `workout_plan_exercise_id` negativo identifica um item SINTÉTICO,
// criado localmente (sem contrapartida em `workout_plan_exercises`) para
// representar a continuação de um exercício encerrado no meio com troca de
// equipamento (ver `MidExerciseExtra` acima) — serve só para dar uma chave
// estável a skip/cancel/priority, nunca é enviado ao backend nem usado para
// casar com um `exercises.workout_plan_exercise_id` real (sempre >= 1).
export function isSyntheticPlanExerciseId(id: number | null | undefined): boolean {
  return id != null && id < 0;
}

// Casa um item da ficha (`GET /workout-plans/{id}`) com o exercício que já
// foi efetivamente lançado nesta sessão (`GET /activities/{session_id}`) —
// mesma heurística de `WorkoutDetailClient.tsx`: o vínculo forte é
// `exercises.workout_plan_exercise_id`; itens SINTÉTICOS (id negativo, ver
// `isSyntheticPlanExerciseId`) não têm contrapartida em
// `workout_plan_exercises`, então caem no match por `mapping_id`.
export function findLoggedExerciseForPlanItem(
  activity: ActivityDetail | undefined,
  planExercise: WorkoutPlanExercise,
): ExerciseLogRow | undefined {
  if (!activity) return undefined;
  const planItemId = planExercise.workout_plan_exercise_id;
  if (planItemId != null && !isSyntheticPlanExerciseId(planItemId)) {
    return activity.exercises.find((ex) => ex.workout_plan_exercise_id === planItemId);
  }
  return planExercise.mapping_id != null
    ? activity.exercises.find((ex) => ex.mapping_id === planExercise.mapping_id)
    : undefined;
}

// Um exercício de ficha, no formato usado tanto por `GET /workout-plans/{id}`
// (`workout-plans-types.ts`) quanto pelo aninhado em `GET /activities/{id}`
// (`workout-log-types.ts`) — os dois tipos têm o mesmo formato de chaves
// relevantes para o modo treino (sequence/exercise_name/mapping_id/etc.).
export type WorkoutModePlanExercise = WorkoutPlanExercise;
export type { ExerciseLogWorkoutPlanExercise };

// Monta os `items` de `MuscleBodyMap`/`MuscleMapDisclosure` a partir de
// `muscle_primary`/`muscle_secondary` de um exercício da ficha — primary com
// intensidade 2, secondary (se existir) com intensidade 1. Cores/legenda
// combinadas ficam a cargo de quem chama (ver `MUSCLE_MAP_COLORS`/
// `MUSCLE_MAP_LEGEND` abaixo), sempre as mesmas nos lugares onde o diagrama
// aparece.
export function buildMuscleMapItems(
  musclePrimary: string | null | undefined,
  muscleSecondary: string | null | undefined,
): Array<{ muscle_group: string; intensity: number; tooltip: string }> {
  const items: Array<{ muscle_group: string; intensity: number; tooltip: string }> = [];
  if (musclePrimary) {
    items.push({ muscle_group: musclePrimary, intensity: 2, tooltip: `${musclePrimary} (principal)` });
  }
  if (muscleSecondary && muscleSecondary !== musclePrimary) {
    items.push({ muscle_group: muscleSecondary, intensity: 1, tooltip: `${muscleSecondary} (secundário)` });
  }
  return items;
}

export const MUSCLE_MAP_COLORS = ['#facc15', '#ef4444'];
export const MUSCLE_MAP_LEGEND = [
  { color: '#ef4444', label: 'Principal' },
  { color: '#facc15', label: 'Secundário' },
];

// Reexport de conveniência — telas de execução (Parte B) consultam fichas
// planejadas com os mesmos tipos já usados em `/planos-treino`.
export type { WorkoutPlanExercise, WorkoutPlanListItem };

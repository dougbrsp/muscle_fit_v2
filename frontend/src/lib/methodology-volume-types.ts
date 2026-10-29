import type { PreviewActivationPayload, WorkoutPlanTemplateItemKind } from '@/lib/workout-plan-templates-types';

/**
 * Contrato de `POST /workout-plan-templates/methodology-volume-preview` e de
 * `GET /workout-plan-templates/{id}/methodology-volume` (Fase 4, PR E).
 *
 * Espelha as dataclasses de `api/app/intelligence/methodology_volume.py`
 * serializadas por `evaluation_to_payload` (enums pelo valor). A UNIDADE é o
 * contrato: `direct_sets` (séries diretas, sem ponderação, sem aquecimento) e
 * `direct_exercises` (exercícios canônicos distintos) POR SESSÃO com trabalho
 * direto. Nunca chamar de "volume" nem de "séries" sozinho — a ativação
 * ponderada do outro painel é outra unidade.
 */

export type MethodologyIntentKind = 'standard' | 'specialization' | 'maintenance' | 'cutting';

export type MethodologyVolumeStatus =
  | 'WITHIN_RANGE'
  | 'BELOW_RANGE'
  | 'ABOVE_RANGE'
  | 'NOT_DIRECTLY_TRAINED'
  | 'NOT_EVALUABLE';

export type MethodologyNotEvaluableReason =
  | 'UNCLASSIFIED_GROUP'
  | 'MODE_A_NO_EXERCISES'
  | 'MODE_A_PERCENT_ONLY'
  | 'NO_TRAINING_SESSIONS'
  // Fase 6: o macro-grupo não tem linha para a frequência do grupo.
  | 'NO_RANGE_FOR_FREQUENCY'
  // Fase 6: a linha da frequência não tem faixa de exercícios (eixo opcional).
  | 'NO_EXERCISES_RANGE'
  // Fase 6: o macro-grupo não tem meta semanal.
  | 'NO_WEEKLY_TARGET';

export type MethodologySeverity = 'warning' | 'informational';

export type MethodologyRangeSource = 'methodology' | 'derived' | 'admin';

export type MethodologyFrequencyBucket = '1x' | '2x' | '3x_plus';

/** Corpo do preview: o MESMO corpo mínimo do preview de ativação + intenção. */
export type MethodologyVolumePreviewPayload = PreviewActivationPayload & {
  intent: MethodologyIntentKind;
  target_group_ids: number[];
  /** Lote 3: modo de contagem do formulário (valor de `VolumeCountingMode`). */
  volume_counting_mode?: string;
};

/**
 * Um eixo do bloco. Nos eixos POR SESSÃO o valor comparado é
 * `value_per_session`; no eixo SEMANAL (Fase 6) é `value_weekly`. Faixas
 * podem ser fracionárias (DECIMAL(5,1) no cadastro de macro-grupos).
 */
export type MethodologyAxisEvaluation = {
  status: MethodologyVolumeStatus;
  reason: MethodologyNotEvaluableReason | null;
  severity: MethodologySeverity | null;
  value_per_session: number | null;
  /** Só no eixo semanal (`weekly_sets_axis`); ausente em payloads antigos. */
  value_weekly?: number | null;
  range_min: number | null;
  range_max: number | null;
  range_source: MethodologyRangeSource | null;
  range_evidence: string | null;
};

export type MethodologySessionDirectWork = {
  slot_sequence: number;
  slot_name: string;
  /** v4 (Fase 5, PR 5): inclui o equivalente planejado da técnica (pode ser fracionário). */
  direct_sets: number;
  direct_exercises: number;
  /** v4: parcela de `direct_sets` que vem de técnica avançada; ausente em payload anterior. */
  technique_equivalent?: number;
};

export type MethodologyGroupEvaluation = {
  muscle_group_id: number;
  muscle_group_name: string;
  /** Macro-grupo do bloco (o do grupo pai); null = sem macro-grupo. */
  volume_class_id: number | null;
  volume_class_name: string | null;
  member_group_ids: number[];
  /** v3: sessões com trabalho direto na janela de 7 dias a partir do primeiro slot de treino. */
  frequency_raw: number;
  /** v3: o mesmo número de `frequency_raw` (sem escala `7 / dias do ciclo`). */
  weekly_frequency: number;
  /** O ciclo não tem 7 slots: a janela de 7 dias dá a volta ou corta slots. */
  frequency_is_normalized: boolean;
  frequency_bucket: MethodologyFrequencyBucket | null;
  direct_sets_cycle_total: number;
  /** v4 (Fase 5, PR 5): parcela de `direct_sets_cycle_total` que vem de técnica; ausente em payload anterior. */
  technique_equivalent_cycle_total?: number;
  direct_exercises_cycle_total: number;
  direct_sets_per_session: number | null;
  direct_exercises_per_session: number | null;
  direct_exercises_axis: MethodologyAxisEvaluation;
  direct_sets_axis: MethodologyAxisEvaluation;
  /** Fase 6: séries diretas do ciclo × 7/dias contra a meta semanal do macro-grupo. */
  weekly_sets_axis: MethodologyAxisEvaluation;
  /** Fase 6: status COMBINADO dos eixos avaliados — é o que o ícone do grupo segue. */
  status: MethodologyVolumeStatus;
  severity: MethodologySeverity | null;
  /** Um eixo abaixo e outro acima: o combinado é BELOW_RANGE e este sinal fica `true`. */
  status_conflict: boolean;
  /** Motivo quando o combinado é NOT_EVALUABLE. */
  status_reason: MethodologyNotEvaluableReason | null;
  is_intent_target: boolean;
  sessions: MethodologySessionDirectWork[];
  /** v5 (Lote 3): faixa/meta usada ainda é a cópia não calibrada de "Só primários". */
  uncalibrated_ranges?: boolean;
};

/** v5 (Lote 3): quanto UM item soma num bloco, no modo do Modelo (o card só exibe). */
export type MethodologyItemGroupShare = {
  muscle_group_id: number;
  muscle_group_name: string;
  /** (séries + equivalente da técnica) × peso do modo. */
  direct_sets: number;
  technique_equivalent: number;
  /** Peso do modo: 1 em "Só primários"; a participação cadastrada no ponderado. */
  weight: number;
  is_primary: boolean;
  slice_group_id: number | null;
  slice_group_name: string | null;
};

/** v5 (Lote 3): fatia de um item; o eco é o das listas por item do preview de ativação. */
export type MethodologyItemDirectShare = {
  slot_sequence: number;
  /** Posição do item em `slots[].items` do CORPO enviado. */
  item_index: number;
  item_kind: WorkoutPlanTemplateItemKind;
  mapping_id: number | null;
  muscle_group_id: number | null;
  groups: MethodologyItemGroupShare[];
};

export type MethodologyDataQuality = {
  items_without_primary_contribution: number;
  items_without_canonical: number;
  items_without_mapping: number;
  items_without_sets_count: number;
  groups_with_unknown_parent: number;
};

/**
 * Intenção declarada PERSISTIDA do Modelo (Fase 4, PR H):
 * `GET/PUT /workout-plan-templates/{id}/methodology-intent`. Sem linha no
 * banco a API devolve `standard` sem alvo. `target_group_ids` só existe com
 * `specialization` (o PUT recusa alvo nas demais com 422).
 */
export type MethodologyIntentDeclaration = {
  intent: MethodologyIntentKind;
  target_group_ids: number[];
};

export type MethodologyIntentDeclarationResponse = MethodologyIntentDeclaration & { template_id: number };

export type MethodologyVolumeEvaluation = {
  metric: string;
  metric_version: string;
  volume_mode: string;
  intent: { kind: MethodologyIntentKind | string; target_group_ids: number[] };
  cycle_days: number;
  training_sessions: number;
  /** v3: sempre 1 com slots (a janela já é a semana); mantido pelo contrato. */
  weekly_factor: number;
  /** O ciclo não tem 7 slots: a janela de 7 dias dá a volta ou corta slots. */
  frequency_is_normalized: boolean;
  groups: MethodologyGroupEvaluation[];
  data_quality: MethodologyDataQuality;
  /** v3: dias da janela de avaliação (7). Ausente em payload anterior. */
  window_days?: number;
  /** v3: `sequence` do primeiro slot de treino, onde a janela começa. */
  window_start_sequence?: number | null;
  /** v5: modo de contagem em que o Modelo foi avaliado (migração 073). */
  counting_mode?: string;
  /** v5: unidade das séries no modo ("séries diretas" / "séries ponderadas"). */
  sets_unit_label?: string;
  /** v5 (Lote 3): fatia de cada item por bloco (Modo B). */
  item_shares?: MethodologyItemDirectShare[];
  /** Só no GET do Modelo salvo. */
  template_id?: number;
  template_name?: string;
};

/**
 * `GET /workout-plan-templates/{id}/methodology-volume/counting-modes` (Lote
 * 3): o Modelo SALVO avaliado em cada modo de contagem, lado a lado. Tudo
 * vem pronto do motor (`methodology_volume.compare_counting_modes`),
 * inclusive as transições (`status_changed`/`bucket_changed`).
 */
export type CountingModeGroupSnapshot = {
  counting_mode: string;
  sets_unit_label: string;
  weekly_frequency: number;
  frequency_bucket: MethodologyFrequencyBucket | null;
  direct_sets_weekly: number | null;
  direct_sets_per_session: number | null;
  status: MethodologyVolumeStatus;
  severity: MethodologySeverity | null;
  status_reason: MethodologyNotEvaluableReason | null;
  session_range_min: number | null;
  session_range_max: number | null;
  weekly_range_min: number | null;
  weekly_range_max: number | null;
  uncalibrated_ranges: boolean;
};

export type CountingModeGroupComparison = {
  muscle_group_id: number;
  muscle_group_name: string;
  volume_class_name: string | null;
  /** Um por modo, na ordem de `counting_modes`. */
  modes: CountingModeGroupSnapshot[];
  status_changed: boolean;
  bucket_changed: boolean;
};

export type CountingModeComparison = {
  metric: string;
  metric_version: string;
  /** Modo gravado no Modelo. */
  current_counting_mode: string;
  counting_modes: string[];
  groups: CountingModeGroupComparison[];
  /** Modos em que algum grupo foi avaliado contra faixa/meta ainda copiada (não calibrada). */
  uncalibrated_modes: string[];
  template_id: number;
  template_name: string;
};

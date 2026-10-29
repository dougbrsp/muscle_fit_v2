import type {
  MethodologyAxisEvaluation,
  MethodologyGroupEvaluation,
  MethodologyItemDirectShare,
  MethodologyVolumeEvaluation,
} from '@/lib/methodology-volume-types';

/**
 * Fixture de resposta de `POST /workout-plan-templates/methodology-volume-preview`
 * usada SÓ pelos testes (view-model e render do painel). Cobre os 5 estados
 * do domínio e as três origens de faixa (metodologia, derivada, admin). Não é
 * importada por código de tela.
 *
 * Fase 6: cada grupo traz o macro-grupo (`volume_class_*`), o eixo semanal
 * (`weekly_sets_axis`) e o status COMBINADO. Sem override, o combinado repete
 * o eixo de séries por sessão (o que o ícone seguia antes).
 */

const axis = (overrides: Partial<MethodologyAxisEvaluation>): MethodologyAxisEvaluation => ({
  status: 'WITHIN_RANGE',
  reason: null,
  severity: null,
  value_per_session: null,
  value_weekly: null,
  range_min: null,
  range_max: null,
  range_source: null,
  range_evidence: null,
  ...overrides,
});

const group = (overrides: Partial<MethodologyGroupEvaluation>): MethodologyGroupEvaluation => ({
  muscle_group_id: 0,
  muscle_group_name: '',
  volume_class_id: 1,
  volume_class_name: 'Grandes',
  member_group_ids: [0],
  frequency_raw: 2,
  weekly_frequency: 2,
  frequency_is_normalized: false,
  frequency_bucket: '2x',
  direct_sets_cycle_total: 0,
  direct_exercises_cycle_total: 0,
  direct_sets_per_session: null,
  direct_exercises_per_session: null,
  direct_exercises_axis: axis({}),
  direct_sets_axis: axis({}),
  weekly_sets_axis: axis({ status: 'NOT_EVALUABLE', reason: 'NO_WEEKLY_TARGET' }),
  status: overrides.direct_sets_axis?.status ?? 'WITHIN_RANGE',
  severity: overrides.direct_sets_axis?.severity ?? null,
  status_conflict: false,
  status_reason: overrides.direct_sets_axis?.reason ?? null,
  is_intent_target: false,
  sessions: [],
  ...overrides,
});

/** Peitoral: dentro da faixa, faixa do autor (EXG-016). */
export const FIXTURE_GROUP_WITHIN: MethodologyGroupEvaluation = group({
  muscle_group_id: 52,
  muscle_group_name: 'Peitoral',
  member_group_ids: [12, 52],
  direct_sets_cycle_total: 24,
  direct_exercises_cycle_total: 6,
  direct_sets_per_session: 12,
  direct_exercises_per_session: 3,
  // Fora de ordem de propósito: a linha expandida ordena por `slot_sequence`.
  sessions: [
    { slot_sequence: 4, slot_name: 'Peito B', direct_sets: 12, direct_exercises: 3 },
    { slot_sequence: 1, slot_name: 'Peito A', direct_sets: 12, direct_exercises: 3 },
  ],
  direct_exercises_axis: axis({
    status: 'WITHIN_RANGE',
    value_per_session: 3,
    range_min: 3,
    range_max: 4,
    range_source: 'methodology',
    range_evidence: 'EXG-016',
  }),
  direct_sets_axis: axis({
    status: 'WITHIN_RANGE',
    value_per_session: 12,
    range_min: 9,
    range_max: 16,
    range_source: 'methodology',
    range_evidence: 'EXG-031',
  }),
  // Meta semanal fracionária editada pelo admin (vírgula decimal na tela).
  weekly_sets_axis: axis({
    status: 'WITHIN_RANGE',
    value_weekly: 16.8,
    range_min: 12.5,
    range_max: 20,
    range_source: 'admin',
  }),
});

/** Bíceps: abaixo da faixa, com faixa DERIVADA (3x+) e frequência normalizada de ciclo de 10 dias. */
export const FIXTURE_GROUP_BELOW_DERIVED: MethodologyGroupEvaluation = group({
  muscle_group_id: 5,
  muscle_group_name: 'Bíceps',
  volume_class_id: 2,
  volume_class_name: 'Pequenos',
  member_group_ids: [5],
  frequency_raw: 5,
  weekly_frequency: 3.5,
  frequency_is_normalized: true,
  frequency_bucket: '3x_plus',
  direct_sets_per_session: 3,
  direct_exercises_per_session: 1,
  direct_exercises_axis: axis({
    status: 'BELOW_RANGE',
    severity: 'warning',
    value_per_session: 1,
    range_min: 1,
    range_max: 2,
    range_source: 'derived',
    range_evidence: 'derived (D2): extrapolacao de EXG-018 para 3x+',
  }),
  direct_sets_axis: axis({
    status: 'BELOW_RANGE',
    severity: 'warning',
    value_per_session: 3,
    range_min: 4,
    range_max: 6,
    range_source: 'derived',
    range_evidence: 'derived (D2): extrapolacao de EXG-031 para 3x+',
  }),
});

/** Panturrilha: sem trabalho direto no ciclo — terminal e neutro. */
export const FIXTURE_GROUP_NOT_DIRECTLY_TRAINED: MethodologyGroupEvaluation = group({
  muscle_group_id: 51,
  muscle_group_name: 'Panturrilha posterior',
  volume_class_id: 2,
  volume_class_name: 'Pequenos',
  member_group_ids: [51, 53],
  frequency_raw: 0,
  weekly_frequency: 0,
  frequency_bucket: null,
  direct_exercises_axis: axis({ status: 'NOT_DIRECTLY_TRAINED', value_per_session: 0 }),
  direct_sets_axis: axis({ status: 'NOT_DIRECTLY_TRAINED', value_per_session: 0 }),
  weekly_sets_axis: axis({ status: 'NOT_DIRECTLY_TRAINED' }),
});

/** Quadríceps em Modo A: séries avaliáveis (acima, esperado por especialização), exercícios não avaliáveis. */
export const FIXTURE_GROUP_MODE_A: MethodologyGroupEvaluation = group({
  muscle_group_id: 4,
  muscle_group_name: 'Quadríceps',
  member_group_ids: [4],
  is_intent_target: true,
  direct_sets_per_session: 20,
  direct_sets_cycle_total: 40,
  sessions: [
    { slot_sequence: 2, slot_name: '', direct_sets: 20, direct_exercises: 0 },
    { slot_sequence: 5, slot_name: 'Pernas', direct_sets: 20, direct_exercises: 0 },
  ],
  direct_exercises_axis: axis({ status: 'NOT_EVALUABLE', reason: 'MODE_A_NO_EXERCISES' }),
  direct_sets_axis: axis({
    status: 'ABOVE_RANGE',
    severity: 'informational',
    value_per_session: 20,
    range_min: 9,
    range_max: 16,
    range_source: 'methodology',
    range_evidence: 'EXG-031',
  }),
  weekly_sets_axis: axis({
    status: 'ABOVE_RANGE',
    severity: 'informational',
    value_weekly: 28,
    range_min: 10,
    range_max: 20,
    range_source: 'methodology',
    range_evidence: 'EXG-019',
  }),
});

export const FIXTURE_EVALUATION: MethodologyVolumeEvaluation = {
  metric: 'methodology_volume',
  metric_version: 'methodology-volume-v2',
  volume_mode: 'equipment_series',
  intent: { kind: 'specialization', target_group_ids: [4] },
  cycle_days: 10,
  training_sessions: 7,
  weekly_factor: 0.7,
  frequency_is_normalized: true,
  groups: [FIXTURE_GROUP_WITHIN, FIXTURE_GROUP_BELOW_DERIVED, FIXTURE_GROUP_NOT_DIRECTLY_TRAINED, FIXTURE_GROUP_MODE_A],
  data_quality: {
    items_without_primary_contribution: 2,
    items_without_canonical: 1,
    items_without_mapping: 0,
    items_without_sets_count: 0,
    groups_with_unknown_parent: 0,
  },
};

/**
 * M43 — fixture da linha de participação por séries diretas dos cards
 * (Modo B). Um Modelo com 3 sessões: Quadríceps na faixa (o exemplo literal
 * do pedido, `Quadríceps: 3/9/12`), Peitoral como bloco com fatia (id 12 =
 * Peitoral superior), Bíceps abaixo (cinza) com eixos em conflito (séries por
 * sessão abaixo, semana acima), Ombros acima (laranja) e Trapézio sem
 * macro-grupo (NOT_EVALUABLE, sem ícone).
 */
export const FIXTURE_CARD_SHARE_EVALUATION: MethodologyVolumeEvaluation = {
  metric: 'methodology_volume',
  metric_version: 'methodology-volume-v2',
  volume_mode: 'equipment_series',
  intent: { kind: 'standard', target_group_ids: [] },
  cycle_days: 7,
  training_sessions: 3,
  weekly_factor: 1,
  frequency_is_normalized: false,
  groups: [
    group({
      muscle_group_id: 4,
      muscle_group_name: 'Quadríceps',
      member_group_ids: [4],
      direct_sets_cycle_total: 12,
      sessions: [
        { slot_sequence: 1, slot_name: 'Pernas A', direct_sets: 9, direct_exercises: 3 },
        { slot_sequence: 3, slot_name: 'Pernas B', direct_sets: 3, direct_exercises: 1 },
      ],
      direct_sets_axis: axis({
        status: 'WITHIN_RANGE',
        value_per_session: 6,
        range_min: 4,
        range_max: 8,
        range_source: 'methodology',
        range_evidence: 'EXG-031',
      }),
    }),
    group({
      muscle_group_id: 52,
      muscle_group_name: 'Peitoral',
      member_group_ids: [12, 52],
      direct_sets_cycle_total: 16,
      sessions: [{ slot_sequence: 2, slot_name: 'Peito', direct_sets: 16, direct_exercises: 4 }],
      direct_sets_axis: axis({
        status: 'WITHIN_RANGE',
        value_per_session: 16,
        range_min: 9,
        range_max: 16,
        range_source: 'methodology',
        range_evidence: 'EXG-031',
      }),
    }),
    group({
      muscle_group_id: 5,
      muscle_group_name: 'Bíceps',
      volume_class_id: 2,
      volume_class_name: 'Pequenos',
      member_group_ids: [5],
      direct_sets_cycle_total: 3,
      sessions: [{ slot_sequence: 2, slot_name: 'Peito', direct_sets: 3, direct_exercises: 1 }],
      direct_sets_axis: axis({
        status: 'BELOW_RANGE',
        severity: 'warning',
        value_per_session: 3,
        range_min: 4,
        range_max: 6,
        range_source: 'methodology',
        range_evidence: 'EXG-031',
      }),
      weekly_sets_axis: axis({
        status: 'ABOVE_RANGE',
        severity: 'warning',
        value_weekly: 13.5,
        range_min: 6,
        range_max: 12,
        range_source: 'admin',
      }),
      status_conflict: true,
    }),
    group({
      muscle_group_id: 7,
      muscle_group_name: 'Ombros',
      volume_class_id: 2,
      volume_class_name: 'Pequenos',
      member_group_ids: [7],
      direct_sets_cycle_total: 10,
      sessions: [{ slot_sequence: 2, slot_name: 'Peito', direct_sets: 10, direct_exercises: 3 }],
      direct_sets_axis: axis({
        status: 'ABOVE_RANGE',
        severity: 'informational',
        value_per_session: 10,
        range_min: 4,
        range_max: 6,
        range_source: 'methodology',
        range_evidence: 'EXG-031',
      }),
    }),
    group({
      muscle_group_id: 9,
      muscle_group_name: 'Trapézio',
      volume_class_id: null,
      volume_class_name: null,
      member_group_ids: [9],
      direct_sets_cycle_total: 4,
      sessions: [{ slot_sequence: 3, slot_name: 'Pernas B', direct_sets: 4, direct_exercises: 1 }],
      direct_sets_axis: axis({ status: 'NOT_EVALUABLE', reason: 'UNCLASSIFIED_GROUP', value_per_session: 4 }),
    }),
  ],
  data_quality: {
    items_without_primary_contribution: 0,
    items_without_canonical: 0,
    items_without_mapping: 0,
    items_without_sets_count: 0,
    groups_with_unknown_parent: 0,
  },
};

/**
 * Lote 3: fatia de UM item por bloco (`item_shares[]` do payload), para os
 * testes do card. O número do card é o que o backend calcula; aqui só a forma.
 */
export function cardShare(
  slotSequence: number,
  groups: Array<{
    id: number;
    name: string;
    sets: number;
    technique?: number;
    weight?: number;
    primary?: boolean;
    slice?: { id: number; name: string };
  }>,
  itemIndex = 0,
): MethodologyItemDirectShare {
  return {
    slot_sequence: slotSequence,
    item_index: itemIndex,
    item_kind: 'equipment',
    mapping_id: 100 + itemIndex,
    muscle_group_id: null,
    groups: groups.map((group) => ({
      muscle_group_id: group.id,
      muscle_group_name: group.name,
      direct_sets: group.sets,
      technique_equivalent: group.technique ?? 0,
      weight: group.weight ?? 1,
      is_primary: group.primary ?? true,
      slice_group_id: group.slice?.id ?? null,
      slice_group_name: group.slice?.name ?? null,
    })),
  };
}

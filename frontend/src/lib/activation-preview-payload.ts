import { isSetsOnlyTechnique } from '@/lib/advanced-techniques';
import { isFreeExerciseItem } from '@/lib/workout-plan-template-form';
import type {
  PreviewActivationPayload,
  VolumeMode,
  WorkoutPlanTemplateSlotItemPayload,
  WorkoutPlanTemplateSlotPayload,
} from '@/lib/workout-plan-templates-types';

/**
 * Monta o corpo de `POST /workout-plan-templates/preview-activation` só com os
 * campos que ENTRAM no cálculo de ativação (M10). O motor do preview
 * (`_compute_slot_activation` / `_compute_slot_activation_equipment_series` em
 * `workout_plan_templates.py`) lê apenas:
 *
 * - slot: `sequence` (ordem do ciclo) e `slot_type` (`descanso` zera o dia);
 * - item `muscle_group` (modo `percent_budget`): `muscle_group_id`, `sets_count`
 *   e, com tipo de treino, `priority_min_pct`/`priority_max_pct` (+ o
 *   `training_type_id` no corpo) — o volume do item vira o orçamento projetado;
 * - item `equipment` (modo `equipment_series`): `mapping_id`, `sets_count` e,
 *   desde a Fase 5 (PR 5), `advanced_technique` + `advanced_technique_applications`
 *   (o equivalente PLANEJADO da técnica soma às séries — preview e metodologia).
 *   FST-7/GVT não somam nada: as aplicações vão `null` (só o código segue);
 * - item `equipment` de exercício livre (M101b, só o grupo, sem par): vai com
 *   `muscle_group_id` e o backend o conta como séries do grupo primário.
 *
 * Tudo o mais (nome do slot, dia da semana, faixa % do slot, reps/RPE alvo,
 * priorizado, pausa da técnica, aquecimento, ajuste, pico, cardio) NÃO altera o
 * resultado e fica FORA do corpo — assim editar esses campos gera o mesmo JSON,
 * a mesma queryKey e nenhuma requisição. A ORDEM dos itens dentro do slot fica
 * no corpo (posição no array): a fadiga prévia por item (`item_prior_fatigue[]`)
 * depende dela, então reordenar gera uma requisição nova.
 *
 * Itens que não contribuem (cardio, grupo sem `muscle_group_id`, equipamento
 * sem `mapping_id` que não seja exercício livre) e os itens de slot `descanso` também saem. `name` é
 * obrigatório no contrato do backend, então vai um valor estável por posição;
 * o nome real é exibido localmente pela tabela.
 */
/** Item que entra no corpo do preview (e, portanto, no cálculo). Fonte única
 * do filtro: usada no corpo e para casar a resposta por item (M11) com a
 * linha do builder. */
export function isActivationContributingItem(
  slot: Pick<WorkoutPlanTemplateSlotPayload, 'slot_type'>,
  item: Pick<WorkoutPlanTemplateSlotItemPayload, 'item_kind' | 'muscle_group_id' | 'mapping_id' | 'equipment_id'>,
): boolean {
  if (slot.slot_type === 'descanso') return false;
  return (
    (item.item_kind === 'muscle_group' && item.muscle_group_id != null) ||
    (item.item_kind === 'equipment' && item.mapping_id != null) ||
    isFreeExerciseItem(item)
  );
}

/**
 * Modo `percent_budget` com tipo de treino: o volume do item de grupo é o
 * orçamento (meta semanal × faixa %), então `training_type_id` e a faixa % dos
 * itens de grupo passam a entrar no cálculo (e no corpo). Sem tipo de treino,
 * ou no modo equipamento, o corpo fica idêntico ao de antes.
 */
function usesBudgetProjection(volumeMode: VolumeMode | undefined, trainingTypeId: number | null | undefined) {
  return volumeMode !== 'equipment_series' && trainingTypeId != null;
}

export function buildActivationPreviewPayload(
  slots: WorkoutPlanTemplateSlotPayload[],
  volumeMode?: VolumeMode,
  trainingTypeId?: number | null,
): PreviewActivationPayload {
  const budget = usesBudgetProjection(volumeMode, trainingTypeId);
  return {
    volume_mode: volumeMode,
    ...(budget ? { training_type_id: trainingTypeId as number } : {}),
    slots: slots.map((slot) => {
      const contributing = slot.items.filter((item) => isActivationContributingItem(slot, item));
      return {
        sequence: slot.sequence,
        name: `Dia ${slot.sequence}`,
        weekday: null,
        slot_type: slot.slot_type,
        priority_min_pct: null,
        priority_max_pct: null,
        items: contributing.map((item, index): WorkoutPlanTemplateSlotItemPayload => {
          const isGroup = item.item_kind === 'muscle_group';
          const freeExercise = isFreeExerciseItem(item);
          const technique = isGroup ? null : (item.advanced_technique ?? null);
          const applications =
            technique && !isSetsOnlyTechnique(technique) ? (item.advanced_technique_applications ?? null) : null;
          return {
            item_order: index + 1,
            item_kind: item.item_kind,
            muscle_group_id: isGroup || freeExercise ? item.muscle_group_id : null,
            mapping_id: isGroup ? null : item.mapping_id,
            equipment_id: null,
            sets_count: item.sets_count,
            reps_target: null,
            rpe_target: null,
            prioritize: false,
            cardio_duration_minutes: null,
            priority_min_pct: budget && isGroup ? item.priority_min_pct : null,
            priority_max_pct: budget && isGroup ? item.priority_max_pct : null,
            advanced_technique: technique,
            advanced_technique_applications: applications,
            advanced_technique_duration_seconds: null,
            warmup_sets_count: null,
            warmup_reps: null,
            warmup_rest_seconds: null,
            feeler_set_sets_count: null,
            feeler_set_reps: null,
            feeler_set_rest_seconds: null,
            peak_contraction_seconds: null,
            rest_seconds: null,
          };
        }),
      };
    }),
  };
}

/** Corpo serializado: igual para edições que não mudam o cálculo. */
export function serializeActivationPreviewPayload(
  slots: WorkoutPlanTemplateSlotPayload[],
  volumeMode?: VolumeMode,
  trainingTypeId?: number | null,
): string {
  return JSON.stringify(buildActivationPreviewPayload(slots, volumeMode, trainingTypeId));
}

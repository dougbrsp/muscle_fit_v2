// Estado local e funções puras do builder de "Modelo de treino"
// (`/modelos-treino/[id]`). Extraído de `WorkoutPlanTemplateBuilderClient.tsx`
// sem mudança de comportamento: tipos do formulário, hidratação a partir do
// backend, conversão para o payload de save, validação client-side e o
// casamento dos avisos de segurança (PR-12) com os itens do builder.
import { isSetsOnlyTechnique } from '@/lib/advanced-techniques';
import {
  CARDIO_LIMITS,
  cardioMode,
  newCardioPrescription,
  pickCardioExtras,
  type CardioPrescriptionExtras,
} from '@/lib/cardio-prescription';
import motorConstants from '@/lib/motor-constants.generated.json';
import {
  DEFAULT_MEASURE,
  MEASURE_LIMITS,
  MEASURE_OPTIONS,
  MEASURE_VALUES,
  isRepBased,
  normalizeMeasure,
  parseMeasure,
} from '@/lib/set-measures';
import { MAX_TEMPLATE_SLOTS, gridCapacity, type SlotGrid } from '@/lib/slot-grid';
import { measureTargetText } from '@/lib/workout-mode-measures';
import type {
  AdvancedTechnique,
  TemplateSafetyWarning,
  VolumeMode,
  WorkoutPlanTemplateItemKind,
  WorkoutPlanTemplateSlot,
  WorkoutPlanTemplateSlotItemPayload,
  WorkoutPlanTemplateSlotPayload,
  WorkoutPlanTemplateSlotType,
} from '@/lib/workout-plan-templates-types';

// Teto de itens priorizados por slot: o mesmo do save do Modelo e do validador
// da Periodização (dono `api/app/periodization/limits.py`, M76 Lote 4c).
const MAX_PRIORITIZED_PER_SLOT: number = motorConstants.periodization_limits.max_prioritized_per_slot;

export function localKey(): string {
  return typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : String(Math.random());
}

// Estado local por item de slot — espelha WorkoutPlanTemplateSlotItemPayload
// mas com uma chave estável de React (`localId`), já que `id`/`item_order`
// só existem depois de salvo.
export type ItemState = {
  localId: string;
  item_kind: WorkoutPlanTemplateItemKind;
  muscle_group_id: number | null;
  // FK exercise_catalog(id) — só usado por item_kind='equipment'.
  mapping_id: number | null;
  // FK equipment(id) — só usado por item_kind='equipment'.
  equipment_id: number | null;
  sets_count: number | null;
  reps_target: string | null;
  rpe_target: string | null;
  prioritize: boolean;
  cardio_duration_minutes: number | null;
  priority_min_pct: number | null;
  priority_max_pct: number | null;
  // Card item_kind='equipment' (modo 'equipment_series') — técnica avançada,
  // aquecimento, série de ajuste e pico de contração.
  advanced_technique: AdvancedTechnique | null;
  advanced_technique_applications: number | null;
  advanced_technique_duration_seconds: number | null;
  warmup_sets_count: number | null;
  warmup_reps: number | null;
  warmup_rest_seconds: number | null;
  feeler_set_sets_count: number | null;
  feeler_set_reps: number | null;
  feeler_set_rest_seconds: number | null;
  peak_contraction_seconds: number | null;
  // Descanso por exercício (Fase 7); null = "da geração".
  rest_seconds: number | null;
  // Medida da série (Lote T2; espelho `lib/set-measures.ts`): null = repetições.
  // Editada no card de equipamento (Lote T4): `measureChangePatch` /
  // `pairSelectionPatch`.
  measure: string | null;
  duration_seconds_target: number | null;
  time_window_seconds: number | null;
  // M97 F3: override do objetivo do Modelo (valor de `GET /training-goals`);
  // null = herda. Só em item de grupo muscular/equipamento (cardio vai null).
  training_goal: string | null;
  // Só do navegador (nunca vai no payload): o usuário já escolheu a medida
  // deste item. Enquanto false, escolher o exercício aplica o
  // `default_measure` do par. Item hidratado (salvo ou rascunho da
  // Periodização) nasce true: a medida gravada é uma decisão, não um palpite.
  measure_touched?: boolean;
} & CardioPrescriptionExtras;

export type SlotState = {
  localId: string;
  name: string;
  weekday: number | null;
  slot_type: WorkoutPlanTemplateSlotType;
  priority_min_pct: number | null;
  priority_max_pct: number | null;
  items: ItemState[];
};

export function newItem(kind: WorkoutPlanTemplateItemKind): ItemState {
  return {
    localId: localKey(),
    item_kind: kind,
    muscle_group_id: null,
    mapping_id: null,
    equipment_id: null,
    sets_count: kind === 'muscle_group' || kind === 'equipment' ? 3 : null,
    reps_target: null,
    rpe_target: null,
    prioritize: false,
    cardio_duration_minutes: null,
    priority_min_pct: null,
    priority_max_pct: null,
    advanced_technique: null,
    advanced_technique_applications: null,
    advanced_technique_duration_seconds: null,
    warmup_sets_count: null,
    warmup_reps: null,
    warmup_rest_seconds: null,
    feeler_set_sets_count: null,
    feeler_set_reps: null,
    feeler_set_rest_seconds: null,
    peak_contraction_seconds: null,
    rest_seconds: null,
    measure: null,
    duration_seconds_target: null,
    time_window_seconds: null,
    training_goal: null,
    measure_touched: false,
    // M98 C1: cardio novo nasce no modo padrão do dono com os seus padrões
    // (`cardio_prescription.DEFAULTS`, via JSON gerado).
    ...(kind === 'cardio' ? newCardioPrescription() : {}),
  };
}

export function newSlot(sequence: number): SlotState {
  return {
    localId: localKey(),
    name: `Dia ${sequence}`,
    weekday: null,
    slot_type: 'normal',
    priority_min_pct: null,
    priority_max_pct: null,
    items: [],
  };
}

/**
 * Slots vindos do backend -> estado local do builder.
 *
 * Extraído da hidratação do detalhe salvo porque o PR-17 passou a ter uma
 * SEGUNDA origem com exatamente o mesmo formato: o rascunho da periodização
 * (`WorkoutPlanTemplateIn` serializado, ver `lib/periodization.ts`). Duas
 * cópias desta conversão divergiriam no primeiro campo novo.
 */
export function hydrateSlots(slots: WorkoutPlanTemplateSlot[]): SlotState[] {
  return hydrateSlotsWithItemIds(slots).slots;
}

/**
 * `hydrateSlots` + o `id` do item no servidor por `localId` (só os itens que
 * vieram com `id`; o rascunho da periodização não traz). O `localId` é
 * regerado a cada carregamento, então é este `id` que identifica o item entre
 * recargas — usado para lembrar quais cards o usuário recolheu (só navegador).
 * Mesmo filtro/ordem de `hydrateSlots`, por construção.
 */
export function hydrateSlotsWithItemIds(slots: WorkoutPlanTemplateSlot[]): {
  slots: SlotState[];
  itemServerIds: Map<string, number>;
} {
  const itemServerIds = new Map<string, number>();
  const hydrated: SlotState[] = slots
    .slice()
    .sort((a, b) => a.sequence - b.sequence)
    .map((slot) => ({
      localId: localKey(),
      name: slot.name,
      weekday: slot.weekday,
      slot_type: slot.slot_type,
      priority_min_pct: slot.priority_min_pct,
      priority_max_pct: slot.priority_max_pct,
      items: slot.items
        .filter(
          (item) =>
            item.item_kind === 'muscle_group' || item.item_kind === 'cardio' || item.item_kind === 'equipment',
        )
        .map((item) => {
          const localId = localKey();
          if (typeof item.id === 'number') itemServerIds.set(localId, item.id);
          return {
            localId,
            item_kind: item.item_kind,
            muscle_group_id: item.muscle_group_id,
            mapping_id: item.mapping_id,
            equipment_id: item.equipment_id,
            sets_count: item.sets_count,
            reps_target: item.reps_target,
            rpe_target: item.rpe_target,
            prioritize: item.prioritize,
            cardio_duration_minutes: item.cardio_duration_minutes,
            priority_min_pct: item.priority_min_pct,
            priority_max_pct: item.priority_max_pct,
            advanced_technique: item.advanced_technique,
            advanced_technique_applications: item.advanced_technique_applications,
            advanced_technique_duration_seconds: item.advanced_technique_duration_seconds,
            warmup_sets_count: item.warmup_sets_count,
            warmup_reps: item.warmup_reps,
            warmup_rest_seconds: item.warmup_rest_seconds,
            feeler_set_sets_count: item.feeler_set_sets_count,
            feeler_set_reps: item.feeler_set_reps,
            feeler_set_rest_seconds: item.feeler_set_rest_seconds,
            peak_contraction_seconds: item.peak_contraction_seconds,
            rest_seconds: item.rest_seconds ?? null,
            measure: item.measure ?? null,
            duration_seconds_target: item.duration_seconds_target ?? null,
            time_window_seconds: item.time_window_seconds ?? null,
            training_goal: item.training_goal ?? null,
            // M98 C0/C1: prescrição de cardio gravada, como veio. Modo ausente
            // (item antigo) fica ausente: a tela o lê como Normal
            // (`cardioMode`) e o save sem mudança não abre versão.
            ...(item.item_kind === 'cardio' ? pickCardioExtras(item) : {}),
            measure_touched: true,
          };
        }),
    }));
  return { slots: hydrated, itemServerIds };
}

/**
 * Depois de um save: `localId` dos itens ENVIADOS -> `id` do item na resposta.
 * A API não ecoa o `localId`; o par é o mesmo `sequence`/`item_order` que
 * `slotsToPayload` gerou para o `sent` (a foto dos slots no momento do envio),
 * então não depende da ordem atual da tela. Item sem par fica de fora.
 */
export function mapSavedItemServerIds(
  sent: SlotState[],
  saved: ReadonlyArray<Pick<WorkoutPlanTemplateSlot, 'sequence' | 'items'>>,
): Map<string, number> {
  const idByPosition = new Map<string, number>();
  for (const slot of saved) {
    for (const item of slot.items) {
      if (typeof item.id === 'number') idByPosition.set(`${slot.sequence}:${item.item_order}`, item.id);
    }
  }
  const result = new Map<string, number>();
  sent.forEach((slot, slotIndex) => {
    slot.items.forEach((item, itemIndex) => {
      const id = idByPosition.get(`${slotIndex + 1}:${itemIndex + 1}`);
      if (id != null) result.set(item.localId, id);
    });
  });
  return result;
}

export function slotsToPayload(slots: SlotState[]): WorkoutPlanTemplateSlotPayload[] {
  return slots.map((slot, slotIndex) => ({
    sequence: slotIndex + 1,
    name: slot.name,
    weekday: slot.weekday,
    slot_type: slot.slot_type,
    priority_min_pct: slot.priority_min_pct,
    priority_max_pct: slot.priority_max_pct,
    items: slot.items.map((item, itemIndex): WorkoutPlanTemplateSlotItemPayload => {
      const isEquipment = item.item_kind === 'equipment';
      const isMuscleGroup = item.item_kind === 'muscle_group';
      return {
        item_order: itemIndex + 1,
        item_kind: item.item_kind,
        muscle_group_id: isMuscleGroup || isEquipment ? item.muscle_group_id : null,
        mapping_id: isEquipment ? item.mapping_id : null,
        equipment_id: isEquipment ? item.equipment_id : null,
        sets_count: isMuscleGroup || isEquipment ? item.sets_count : null,
        reps_target: isMuscleGroup || isEquipment ? item.reps_target : null,
        rpe_target: isMuscleGroup || isEquipment ? item.rpe_target : null,
        prioritize: item.prioritize,
        cardio_duration_minutes: item.item_kind === 'cardio' ? item.cardio_duration_minutes : null,
        priority_min_pct: isMuscleGroup ? item.priority_min_pct : null,
        priority_max_pct: isMuscleGroup ? item.priority_max_pct : null,
        advanced_technique: isEquipment ? item.advanced_technique : null,
        // Fase 5 (PR 5): FST-7/GVT só preenchem o nº de séries — aplicações e
        // pausa vão zeradas (a API zera também: Modelo antigo reaberto e salvo).
        advanced_technique_applications:
          isEquipment && !isSetsOnlyTechnique(item.advanced_technique) ? item.advanced_technique_applications : null,
        advanced_technique_duration_seconds:
          isEquipment && !isSetsOnlyTechnique(item.advanced_technique) ? item.advanced_technique_duration_seconds : null,
        warmup_sets_count: isEquipment ? item.warmup_sets_count : null,
        warmup_reps: isEquipment ? item.warmup_reps : null,
        warmup_rest_seconds: isEquipment ? item.warmup_rest_seconds : null,
        feeler_set_sets_count: isEquipment ? item.feeler_set_sets_count : null,
        feeler_set_reps: isEquipment ? item.feeler_set_reps : null,
        feeler_set_rest_seconds: isEquipment ? item.feeler_set_rest_seconds : null,
        peak_contraction_seconds: isEquipment ? item.peak_contraction_seconds : null,
        rest_seconds: isEquipment ? item.rest_seconds : null,
        // Medida da série (Lote T2): só no item de equipamento (a API recusa
        // medida fora de reps nos demais); null = repetições.
        measure: isEquipment ? item.measure : null,
        duration_seconds_target: isEquipment ? item.duration_seconds_target : null,
        time_window_seconds: isEquipment ? item.time_window_seconds : null,
        // M97 F3: o PUT é replace-all dos itens; sem o campo o override se
        // perderia a cada save. Cardio não tem objetivo.
        training_goal: isMuscleGroup || isEquipment ? item.training_goal : null,
        // M98 C0: o PUT é replace-all; sem os campos novos a prescrição de
        // cardio (aquecimento, zona, HIIT) se perderia a cada save.
        ...(item.item_kind === 'cardio' ? pickCardioExtras(item) : {}),
      };
    }),
  }));
}

// --- Medida da série no card de equipamento (Lote T4) ---------------------
// Vocabulário, rótulos e limites vêm do espelho `lib/set-measures.ts` (dono
// `api/app/domain/set_measures.py`); aqui só a FORMA do estado do editor. A
// regra definitiva é a do servidor (`validate_item_measure` -> 422).

/** Opções do Select "Medida" (ordem e rótulos do dono). */
export const MEASURE_SELECT_DATA: { value: string; label: string }[] = MEASURE_OPTIONS.map((o) => ({
  value: o.value,
  label: o.label,
}));

/**
 * Patch da troca de medida: grava a medida e limpa o que ela não admite (o
 * servidor recusaria) — o campo de tempo da outra medida e, fora de reps, o
 * alvo de reps e todo o bloco avançado (técnica, aquecimento, ajuste, pico).
 */
function measurePatch(measure: string | null | undefined): Partial<ItemState> {
  const kind = normalizeMeasure(measure);
  const patch: Partial<ItemState> = { measure: kind };
  if (kind !== MEASURE_VALUES.duration) patch.duration_seconds_target = null;
  if (kind !== MEASURE_VALUES.max_reps) patch.time_window_seconds = null;
  if (!isRepBased(kind)) {
    Object.assign(patch, {
      reps_target: null,
      advanced_technique: null,
      advanced_technique_applications: null,
      advanced_technique_duration_seconds: null,
      warmup_sets_count: null,
      warmup_reps: null,
      warmup_rest_seconds: null,
      feeler_set_sets_count: null,
      feeler_set_reps: null,
      feeler_set_rest_seconds: null,
      peak_contraction_seconds: null,
    } satisfies Partial<ItemState>);
  }
  return patch;
}

/** O usuário escolheu a medida no Select: aplica e marca como decidida. */
export function measureChangePatch(measure: string | null | undefined): Partial<ItemState> {
  return { ...measurePatch(measure), measure_touched: true };
}

/**
 * M101b: item do Modo B com o grupo e SEM par = "O motor escolhe o exercício".
 * Só o grupo é gravado (`mapping_id` e `equipment_id` NULL); a geração escolhe
 * o exercício e o equipamento vem junto (`generation/mode_b._resolve_free_exercise`).
 */
export function isFreeExerciseItem(
  item: Pick<ItemState, 'item_kind' | 'muscle_group_id' | 'mapping_id' | 'equipment_id'>,
): boolean {
  return (
    item.item_kind === 'equipment' && item.muscle_group_id != null && item.mapping_id == null && item.equipment_id == null
  );
}

/** Patch da opção "O motor escolhe o exercício": o par sai, o grupo fica. */
export const FREE_EXERCISE_PATCH: Readonly<Pick<ItemState, 'equipment_id' | 'mapping_id'>> = {
  equipment_id: null,
  mapping_id: null,
};

function measureSuggestionPatch(
  item: Pick<ItemState, 'measure' | 'measure_touched'>,
  defaultMeasure: string | null | undefined,
): Partial<ItemState> {
  if (item.measure_touched) return {};
  const suggested = parseMeasure(defaultMeasure)?.value ?? DEFAULT_MEASURE;
  if (suggested === normalizeMeasure(item.measure)) return {};
  return measurePatch(suggested);
}

/**
 * Escolha do par equipamento+exercício. Se o usuário ainda não mexeu na
 * medida do item, aplica o `default_measure` do par (sem sugestão = medida
 * padrão do dono); depois de mexer, a escolha dele vale.
 */
export function pairSelectionPatch(
  item: Pick<ItemState, 'measure' | 'measure_touched'>,
  equipmentId: number,
  mappingId: number,
  defaultMeasure: string | null | undefined,
): Partial<ItemState> {
  return { equipment_id: equipmentId, mapping_id: mappingId, ...measureSuggestionPatch(item, defaultMeasure) };
}

/**
 * Texto curto da medida do item para o card recolhido ("3 × 60 s",
 * "3 × reps máx. em 30 s"); null em reps (o card segue como antes).
 */
export function itemMeasureSummary(
  item: Pick<ItemState, 'measure' | 'sets_count' | 'duration_seconds_target' | 'time_window_seconds'>,
): string | null {
  // Mesmo texto do alvo no Modo Treino (`measureTargetText`, Lote T5).
  const text = measureTargetText({
    measure: item.measure,
    durationSecondsTarget: item.duration_seconds_target,
    timeWindowSeconds: item.time_window_seconds,
  });
  if (text == null) return null;
  return item.sets_count != null ? `${item.sets_count} × ${text}` : text;
}

function measureShapeError(slotName: string, item: ItemState): string | null {
  const kind = normalizeMeasure(item.measure);
  const within = (v: number, min: number, max: number) => Number.isInteger(v) && v >= min && v <= max;
  if (kind === MEASURE_VALUES.duration) {
    const target = item.duration_seconds_target;
    if (
      target == null ||
      !within(target, MEASURE_LIMITS.durationSecondsMin, MEASURE_LIMITS.durationSecondsMax)
    ) {
      return `Slot "${slotName}": item por tempo precisa do tempo por série entre ${MEASURE_LIMITS.durationSecondsMin} e ${MEASURE_LIMITS.durationSecondsMax} s.`;
    }
  }
  if (kind === MEASURE_VALUES.max_reps && item.time_window_seconds != null) {
    if (!within(item.time_window_seconds, MEASURE_LIMITS.timeWindowSecondsMin, MEASURE_LIMITS.timeWindowSecondsMax)) {
      return `Slot "${slotName}": a janela do máximo de repetições deve ficar entre ${MEASURE_LIMITS.timeWindowSecondsMin} e ${MEASURE_LIMITS.timeWindowSecondsMax} s (ou vazia).`;
    }
  }
  return null;
}

// Validação client-side espelhando `_validate_template_payload`
// (workout_plan_templates.py) — o backend é a fonte de verdade final (um
// 422 dele sempre pode acontecer mesmo passando aqui), isto só evita uma
// viagem de rede óbvia para erros comuns.
//
// Grade (migração 062): `grid` é a grade que vai no payload (`null` = sem
// grade, só no legado com mais de 8 slots); `maxSlots` é o teto aceito — 8,
// ou a quantidade já gravada num legado acima disso (o PUT do backend aceita
// até ela).
export type ValidateSlotsOptions = {
  grid?: SlotGrid | null;
  maxSlots?: number;
};

export function validateSlots(
  slots: SlotState[],
  volumeMode: VolumeMode,
  trainingTypeId: string | null,
  options: ValidateSlotsOptions = {},
): string | null {
  if (slots.length < 1) {
    return 'Um modelo precisa de pelo menos 1 slot.';
  }
  const maxSlots = options.maxSlots ?? MAX_TEMPLATE_SLOTS;
  if (slots.length > maxSlots) {
    return `Um modelo pode ter no máximo ${maxSlots} slots.`;
  }
  if (options.grid) {
    const capacity = gridCapacity(options.grid);
    if (slots.length > capacity) {
      return `A grade ${options.grid.rows}x${options.grid.cols} comporta ${capacity} slots, mas o modelo tem ${slots.length}.`;
    }
  }
  for (const slot of slots) {
    if (slot.slot_type === 'descanso') {
      const hasMuscleGroup = slot.items.some((item) => item.item_kind === 'muscle_group' || item.item_kind === 'equipment');
      if (hasMuscleGroup) {
        return `Slot "${slot.name}" (descanso) não pode ter itens de grupo muscular/equipamento — apenas cardio é permitido.`;
      }
    }
    const prioritizedCount = slot.items.filter((item) => item.prioritize).length;
    if (prioritizedCount > MAX_PRIORITIZED_PER_SLOT) {
      return `Slot "${slot.name}": no máximo ${MAX_PRIORITIZED_PER_SLOT} itens podem ser marcados como priorizados.`;
    }
    for (const item of slot.items) {
      if (item.item_kind === 'muscle_group') {
        if (volumeMode === 'equipment_series') {
          return `Slot "${slot.name}": modo "Equipamento + séries fixas" não permite itens de grupo muscular — use itens de equipamento.`;
        }
        if (!item.muscle_group_id) {
          return `Slot "${slot.name}": todo item de grupo muscular precisa de um grupo selecionado.`;
        }
      }
      if (item.item_kind === 'equipment') {
        if (volumeMode !== 'equipment_series') {
          return `Slot "${slot.name}": itens de equipamento só são permitidos no modo "Equipamento + séries fixas".`;
        }
        // M101b: exige só o grupo; vale o par completo ou o exercício livre
        // (os dois vazios: "O motor escolhe o exercício").
        if (!item.muscle_group_id) {
          return `Slot "${slot.name}": todo item de equipamento precisa de grupo muscular selecionado.`;
        }
        if (!item.mapping_id !== !item.equipment_id) {
          return `Slot "${slot.name}": todo item de equipamento precisa do par equipamento — exercício, ou de "O motor escolhe o exercício".`;
        }
        if (!item.sets_count || item.sets_count < 1) {
          return `Slot "${slot.name}": todo item de equipamento precisa de ao menos 1 série de trabalho.`;
        }
        const measureError = measureShapeError(slot.name, item);
        if (measureError) return measureError;
      }
      // Duração mínima do Normal: a do dono (`cardio_prescription.FIELD_LIMITS`);
      // o HIIT não usa a duração (a validação completa é do backend).
      if (
        item.item_kind === 'cardio' &&
        cardioMode(item) === 'continuous' &&
        (item.cardio_duration_minutes == null ||
          item.cardio_duration_minutes < CARDIO_LIMITS.cardio_duration_minutes.min)
      ) {
        return `Slot "${slot.name}": todo item de cardio precisa de uma duração de pelo menos ${CARDIO_LIMITS.cardio_duration_minutes.min} min.`;
      }
      const hasMin = item.priority_min_pct !== null;
      const hasMax = item.priority_max_pct !== null;
      if (hasMin !== hasMax) {
        return `Slot "${slot.name}": faixa de prioridade do motor v2 exige mínimo e máximo preenchidos juntos (ou nenhum dos dois).`;
      }
      if (hasMin && hasMax && item.priority_min_pct! > item.priority_max_pct!) {
        return `Slot "${slot.name}": prioridade mínima não pode ser maior que a máxima.`;
      }
      // Motor v2 (training_type_id preenchido): Min%/Max% deixam de ser
      // opcionais para itens de grupo muscular. Sem eles o backend
      // (build_template_volume_plan) hoje distribui um fallback igualitário
      // automático — correto como rede de segurança, mas não substitui uma
      // decisão explícita do usuário sobre a participação de cada grupo no
      // slot. Só se aplica a item_kind === 'muscle_group': itens de
      // equipamento/cardio não usam priority_min_pct/priority_max_pct.
      if (
        item.item_kind === 'muscle_group' &&
        trainingTypeId != null &&
        (item.priority_min_pct === null || item.priority_max_pct === null)
      ) {
        return `Slot "${slot.name}": preencha o Min%/Max% de participação de cada grupo muscular — obrigatório quando o modelo tem Meta de Ativação vinculada.`;
      }
    }
  }
  return null;
}

// --- Avisos de segurança por item (PR-12, decisão D14) ---------------------
// O backend devolve `safety_warnings` em GET/POST/PUT do modelo, endereçados
// por `slot_sequence` + `item_order` (ambos 1-based, iguais ao payload de
// save). Como o builder é totalmente local até o próximo save, casar só pela
// posição apontaria para o item errado depois de o usuário adicionar/remover
// itens — por isso a posição é confirmada pela identidade do item
// (grupo/exercício/equipamento) quando o backend a informa.
export function safetyWarningsForItem(
  all: TemplateSafetyWarning[],
  slotIndex: number,
  itemIndex: number,
  item: ItemState,
): TemplateSafetyWarning[] {
  if (all.length === 0) return [];
  const slotSequence = slotIndex + 1;
  const itemOrder = itemIndex + 1;
  return all.filter((w) => {
    if (w.slot_sequence !== slotSequence || w.item_order !== itemOrder) return false;
    if (w.mapping_id != null && w.mapping_id !== item.mapping_id) return false;
    if (w.equipment_id != null && w.equipment_id !== item.equipment_id) return false;
    if (w.muscle_group_id != null && w.muscle_group_id !== item.muscle_group_id) return false;
    return true;
  });
}

export function countSlotSafetyWarnings(all: TemplateSafetyWarning[], slotIndex: number, slot: SlotState): number {
  if (all.length === 0) return 0;
  return slot.items.reduce(
    (count, item, itemIndex) => count + (safetyWarningsForItem(all, slotIndex, itemIndex, item).length > 0 ? 1 : 0),
    0,
  );
}

// Slot `normal` sem nenhum item: provavelmente um dia de descanso cadastrado
// com o tipo errado (ex.: slot chamado "Descanso" que ficou `normal`). Ele
// conta como treino esperado na aderência do dashboard — que NÃO adivinha
// descanso pelo conteúdo (decisão de produto de 2026-10-02). O editor só
// avisa e oferece trocar o Tipo; quem decide é o usuário, ao salvar.
export function isNormalSlotWithoutItems(slot: Pick<SlotState, 'slot_type' | 'items'>): boolean {
  return slot.slot_type === 'normal' && slot.items.length === 0;
}

export function safetyWarningColor(code: TemplateSafetyWarning['code']): string {
  return code === 'RPE_ABOVE_SAFE_CEILING' ? 'orange' : 'red';
}

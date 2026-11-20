// Registro do treino: mudar o equipamento de um exercício que JÁ TEM séries
// move TODAS as séries (e o histórico, o recorde e a progressão que contam
// nelas) para o equipamento novo. Aqui só se decide SE a tela pede
// confirmação e qual o texto; nenhuma regra de carga/histórico (dono: backend).
import { loadConventionLabel, type LoadConvention } from './equipment-exercises-types';
import { needsLoadConventionWarning } from './equipment-picker';

export type EquipmentChangeInput = {
  /** `exercises.equipment_id` gravado (id da associação); `null` = sem equipamento. */
  previousLinkId: number | null | undefined;
  /** Equipamento escolhido no formulário; `null` = nenhum. */
  nextLinkId: number | null | undefined;
  /** Séries já gravadas no exercício (0 = exercício novo ou sem séries). */
  loggedSetsCount: number;
  previousConvention?: LoadConvention | null;
  nextConvention?: LoadConvention | null;
};

export type EquipmentChangeConfirmation = {
  message: string;
  /** Só com as duas convenções conhecidas e diferentes. */
  conventionWarning: string | null;
};

/** `null` = salvar direto (equipamento inalterado ou exercício sem séries). */
export function equipmentChangeConfirmation(input: EquipmentChangeInput): EquipmentChangeConfirmation | null {
  const previous = input.previousLinkId ?? null;
  const next = input.nextLinkId ?? null;
  if (previous === next || input.loggedSetsCount <= 0) return null;
  const single = input.loggedSetsCount === 1;
  const subject = single ? 'A série deste exercício' : `Todas as ${input.loggedSetsCount} séries deste exercício`;
  const message =
    next == null
      ? `${subject} ${single ? 'fica' : 'ficam'} sem equipamento, e o histórico, o recorde e a progressão do exercício deixam de contar no equipamento atual.`
      : `${subject} ${single ? 'passa' : 'passam'} para o novo equipamento, e o histórico, o recorde e a progressão do exercício passam a contar nele.`;
  const conventionWarning = needsLoadConventionWarning(input.nextConvention, input.previousConvention)
    ? `Neste equipamento a carga é registrada: ${loadConventionLabel(input.nextConvention)}.`
    : null;
  return { message, conventionWarning };
}

/** Confirmação para o "Salvar" do formulário de edição; `row` = exercício gravado (null = lançamento novo). */
export function equipmentChangeForSave(params: {
  row: { equipment_id?: number | null; sets: ReadonlyArray<unknown> } | null | undefined;
  formEquipmentId: string;
  links: ReadonlyArray<{ id: number; load_convention?: LoadConvention | null }>;
}): EquipmentChangeConfirmation | null {
  const { row, formEquipmentId, links } = params;
  if (!row) return null;
  const nextLinkId = formEquipmentId ? Number(formEquipmentId) : null;
  const convention = (id: number | null | undefined) =>
    id == null ? null : (links.find((link) => link.id === id)?.load_convention ?? null);
  return equipmentChangeConfirmation({
    previousLinkId: row.equipment_id,
    nextLinkId,
    loggedSetsCount: row.sets.length,
    previousConvention: convention(row.equipment_id),
    nextConvention: convention(nextLinkId),
  });
}

/** Modo Treino, troca com séries já lançadas: não há troca retroativa. */
export const SWAP_REMAINING_NOTE =
  'As séries já lançadas continuam no equipamento atual e a troca vale só para as séries restantes. Para corrigir as já lançadas, use o Registro do treino depois.';

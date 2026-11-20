import type { EquipmentExerciseLink } from './equipment-exercises-types';
import { normalizeSearchText, type EquipmentPickerItem } from './equipment-picker';

// Terceira seção do seletor de equipamento (#163, parte 2): equipamentos do catálogo
// global que a academia do treino ainda NÃO tem. Só cruza listas da API por ids; a
// autorização real (`treino.academias`/`editar`) é do servidor.

export const CATALOG_NOT_IN_GYM_NOTICE = 'Não cadastrado nesta academia';
export const CATALOG_NEEDS_ONLINE_NOTICE = 'Acrescentar à academia exige conexão';
export const CATALOG_ADDED_WITHOUT_LINK_MESSAGE =
  'O equipamento foi acrescentado à academia, mas não está ligado a este exercício no catálogo.';

/**
 * O item da academia é o mesmo par equipamento×exercício do catálogo?
 * `/gym-equipment` devolve `mapping_id` (pode ser id legado) e o catálogo o
 * `exercise_id` canônico, sem o id canônico na rota da academia: casa por id e,
 * como reserva, pelo nome normalizado do exercício (mesmo `equipment_id` sempre).
 * shortcut: dois exercícios com o mesmo nome no mesmo equipamento contam como um só.
 */
export function isSamePair(pair: EquipmentExerciseLink, item: EquipmentPickerItem): boolean {
  if (pair.equipment_id !== item.equipment_id) return false;
  if (pair.exercise_id === item.mapping_id) return true;
  const name = normalizeSearchText(pair.exercise_name);
  return name !== '' && name === normalizeSearchText(item.mapping_exercise_name);
}

/**
 * Pares do catálogo que a academia não tem, filtrados pela busca; primeiro os do
 * exercício atual, depois os demais, na ordem da API.
 */
export function buildCatalogSection(
  catalog: ReadonlyArray<EquipmentExerciseLink> | null | undefined,
  gymItems: ReadonlyArray<EquipmentPickerItem>,
  currentMappingId: number | null | undefined,
  searchText: string,
): EquipmentExerciseLink[] {
  if (!catalog || catalog.length === 0) return [];
  const query = normalizeSearchText(searchText);
  const missing = catalog.filter(
    (pair) =>
      pair.is_active !== false &&
      !gymItems.some((item) => isSamePair(pair, item)) &&
      (!query ||
        [pair.equipment_name, pair.exercise_name].some((field) => normalizeSearchText(field).includes(query))),
  );
  const isCurrent = (pair: EquipmentExerciseLink) => currentMappingId != null && pair.exercise_id === currentMappingId;
  return [...missing.filter(isCurrent), ...missing.filter((pair) => !isCurrent(pair))];
}

/** Pergunta de confirmação; sem nome da academia, cai em "a esta academia". */
export function formatCatalogAddQuestion(equipmentName: string, gymName: string | null | undefined): string {
  const where = gymName?.trim() ? `à academia ${gymName.trim()}` : 'a esta academia';
  return `Acrescentar ${equipmentName} ${where}? Ele ficará disponível para todos que treinam nela.`;
}

export type CatalogItemState =
  | { kind: 'add'; notice: null }
  | { kind: 'add_offline'; notice: string }
  | { kind: 'disabled'; notice: string };

/** O que cada item do catálogo mostra: ação, ação bloqueada offline ou item desabilitado. */
export function catalogItemState(canAdd: boolean, online: boolean): CatalogItemState {
  if (!canAdd) return { kind: 'disabled', notice: CATALOG_NOT_IN_GYM_NOTICE };
  if (!online) return { kind: 'add_offline', notice: CATALOG_NEEDS_ONLINE_NOTICE };
  return { kind: 'add', notice: null };
}

export type GymEquipmentCatalogAddResponse = {
  status: string;
  links?: Array<{ id: number; mapping_id: number; equipment_id: number }> | null;
};

/**
 * Associação recém-criada do par: o `id` de `links` com o `mapping_id` do par
 * (e que exista na lista recarregada); na falta, o item da lista recarregada.
 */
export function findAddedItem(
  links: GymEquipmentCatalogAddResponse['links'],
  freshItems: ReadonlyArray<EquipmentPickerItem>,
  pair: EquipmentExerciseLink,
): EquipmentPickerItem | null {
  const linkId = (links ?? []).find(
    (link) => link.mapping_id === pair.exercise_id && link.equipment_id === pair.equipment_id,
  )?.id;
  const byId = linkId != null ? freshItems.find((item) => item.id === linkId) : undefined;
  return byId ?? freshItems.find((item) => isSamePair(pair, item)) ?? null;
}

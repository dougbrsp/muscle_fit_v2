import type { LoadConvention } from './equipment-exercises-types';
import type { GymEquipmentLink } from './workout-mode-types';

/** Item de `GET /gym-equipment`; `load_convention` ausente = servidor antigo. */
export type EquipmentPickerItem = GymEquipmentLink & { load_convention?: LoadConvention | null };

export type EquipmentPickerSectionKey = 'same_exercise' | 'same_group';

export type EquipmentPickerSection = {
  key: EquipmentPickerSectionKey;
  title: string;
  items: EquipmentPickerItem[];
};

// Para uma 3a seção (catálogo global) basta acrescentar uma chave e um título aqui.
const SECTION_TITLES: Record<EquipmentPickerSectionKey, string> = {
  same_exercise: 'Mesmo exercício',
  same_group: 'Mesmo grupo muscular',
};

/** Minúsculas e sem acento, para busca. */
export function normalizeSearchText(value: string | null | undefined): string {
  return (value ?? '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .trim();
}

function matches(item: EquipmentPickerItem, query: string): boolean {
  if (!query) return true;
  return [item.equipment_name, item.mapping_exercise_name, item.manufacturer].some((field) =>
    normalizeSearchText(field).includes(query),
  );
}

/**
 * Seções do seletor de equipamento: só agrupa, filtra e mantém a ordem da API.
 * A seção vem da comparação de ids (`mapping_id` do item x do exercício atual).
 */
export function buildEquipmentPickerSections(
  items: ReadonlyArray<EquipmentPickerItem>,
  currentMappingId: number | null | undefined,
  currentLinkId: number | null | undefined,
  searchText: string,
): EquipmentPickerSection[] {
  const query = normalizeSearchText(searchText);
  const visible = items.filter((item) => item.id !== currentLinkId && matches(item, query));
  const sections: EquipmentPickerSection[] = [
    {
      key: 'same_exercise',
      title: SECTION_TITLES.same_exercise,
      items: visible.filter((item) => currentMappingId != null && item.mapping_id === currentMappingId),
    },
    {
      key: 'same_group',
      title: SECTION_TITLES.same_group,
      items: visible.filter((item) => currentMappingId == null || item.mapping_id !== currentMappingId),
    },
  ];
  return sections.filter((section) => section.items.length > 0);
}

/** Convenção do equipamento atual, lida da própria lista da API (o atual vem na resposta). */
export function currentLoadConvention(
  items: ReadonlyArray<EquipmentPickerItem>,
  currentLinkId: number | null | undefined,
): LoadConvention | null {
  if (currentLinkId == null) return null;
  return items.find((item) => item.id === currentLinkId)?.load_convention ?? null;
}

/** Avisar só quando as duas convenções são conhecidas e diferentes. */
export function needsLoadConventionWarning(
  chosen: LoadConvention | null | undefined,
  current: LoadConvention | null | undefined,
): boolean {
  return Boolean(chosen && current && chosen !== current);
}

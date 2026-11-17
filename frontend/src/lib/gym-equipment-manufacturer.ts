// #188: fabricante do equipamento POR ACADEMIA. Só apresentação e o PATCH:
// quem pode editar e o fabricante efetivo vêm prontos do servidor
// (`can_edit_manufacturer`, `manufacturer_source`). O limite de tamanho e a
// validação do texto também são do servidor (422 → mensagem na tela); não há
// espelho do limite em `motor-constants.generated.json`, então o input não
// bloqueia por tamanho.
import { apiFetch } from './api';
import type { GymEquipmentCatalogLink, ManufacturerSource } from './gyms-types';

export const CATALOG_DEFAULT_HINT = 'padrão do catálogo';

export function gymEquipmentManufacturerPath(linkId: number): string {
  return `/gym-equipment-catalog/${linkId}`;
}

/** Corpo do PATCH: vazio vira null (= volta ao padrão do catálogo). */
export function gymEquipmentManufacturerBody(input: string): { manufacturer: string | null } {
  return { manufacturer: input.trim() || null };
}

export function saveGymEquipmentManufacturer(
  linkId: number,
  input: string,
): Promise<{ status: string; item: GymEquipmentCatalogLink }> {
  return apiFetch(gymEquipmentManufacturerPath(linkId), {
    method: 'PATCH',
    body: JSON.stringify(gymEquipmentManufacturerBody(input)),
  });
}

/** Texto exibido: o fabricante efetivo, com a origem quando é o padrão do catálogo. */
export function manufacturerLabel(manufacturer: string | null | undefined, source: ManufacturerSource | undefined): string | null {
  if (!manufacturer) return null;
  return source === 'catalog' ? `${manufacturer} (${CATALOG_DEFAULT_HINT})` : manufacturer;
}

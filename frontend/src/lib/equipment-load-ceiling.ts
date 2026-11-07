// Teto de carga do equipamento (migração 110, `api/app/equipment_load_ceiling.py`).
//
// Só transporte e apresentação. A REGRA (nenhuma carga sugerida passa do teto;
// no teto o motor não arma aumento e a progressão segue por reps/volume) é dos
// motores (`domain/next_set_load.py`, `domain/progression_engine.py`); aqui a
// tela só lê o `max_load_kg` pronto da API, mostra o estado e deixa marcar ou
// desfazer. Sem número de regra neste arquivo.

export const EQUIPMENT_LOAD_CEILING_PATH = '/me/equipment-load-ceiling';
export const EQUIPMENT_LOAD_CEILING_QUERY_ROOT = 'equipment-load-ceiling';

export type EquipmentLoadCeilingResponse = {
  ok: boolean;
  /** Teto do par em kg TOTAIS; `null` = sem teto. */
  max_load_kg: number | null;
};

export type EquipmentLoadCeilingPair = { mappingId: number; gymEquipmentId: number };

export function equipmentLoadCeilingQueryKey(pair: EquipmentLoadCeilingPair | null) {
  return [EQUIPMENT_LOAD_CEILING_QUERY_ROOT, pair?.mappingId ?? null, pair?.gymEquipmentId ?? null] as const;
}

/** `?mapping_id=&gym_equipment_id=` do par (o servidor resolve o vínculo e o usuário). */
export function equipmentLoadCeilingPath(pair: EquipmentLoadCeilingPair): string {
  return `${EQUIPMENT_LOAD_CEILING_PATH}?mapping_id=${pair.mappingId}&gym_equipment_id=${pair.gymEquipmentId}`;
}

export const LOAD_CEILING_ACTION_LABEL = 'Carga máxima do equipamento';
export const LOAD_CEILING_UNDO_LABEL = 'Desfazer';
export const LOAD_CEILING_OFFLINE_MESSAGE =
  'Sem conexão: não consegui marcar a carga máxima. Tente de novo quando voltar a ficar online.';
export const LOAD_CEILING_ERROR_MESSAGE = 'Não foi possível salvar a carga máxima do equipamento. Tente de novo.';

/**
 * Carga mostrada no campo, limitada ao teto que o servidor devolveu (ambos em
 * kg). Só apresentação: o valor sugerido já sai limitado dos motores; isto cobre
 * uma sugestão congelada ANTES de o teto ser marcado. Sem teto, devolve a carga.
 */
export function displayLoadWithinCeiling(loadKg: number | null, maxLoadKg: number | null | undefined): number | null {
  if (loadKg == null || maxLoadKg == null) return loadKg;
  return loadKg > maxLoadKg ? maxLoadKg : loadKg;
}

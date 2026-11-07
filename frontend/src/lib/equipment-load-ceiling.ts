// Teto de carga do equipamento (migração 110, `api/app/equipment_load_ceiling.py`).
//
// Só transporte e apresentação. A REGRA (nenhuma carga sugerida passa do teto;
// no teto o motor não arma aumento e a progressão segue por reps/volume) e todo
// cálculo com o teto são do dono (`progression_engine.cap_load_to_ceiling`).
// A tela lê valores PRONTOS da API: `max_load_kg` (o teto, para mostrar) e
// `load_within_ceiling` (a carga da ficha já limitada pelo dono) e deixa marcar
// ou desfazer. Nenhuma carga é calculada, limitada ou arredondada aqui.

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
 * O botão "+" do stepper da carga pode dar mais um passo? `serverLimit` é o
 * limite PRONTO do servidor, já na unidade do campo (só conversão de unidade,
 * feita por quem chama). Só HABILITA/DESABILITA o botão: nunca recorta o valor
 * digitado nem a carga sugerida. Respeita o passo da unidade (kg ou lb): o
 * próximo passo inteiro não pode ultrapassar o limite. Sem limite, sempre pode.
 */
export function stepUpAllowed(valueDisplay: number | null, stepDisplay: number, serverLimit: number | null | undefined): boolean {
  if (serverLimit == null) return true;
  const next = Number(((valueDisplay ?? 0) + stepDisplay).toFixed(2));
  return next <= serverLimit;
}

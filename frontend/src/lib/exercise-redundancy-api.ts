// Cliente da prévia de redundância do editor de Modelo (issue #288, fatia 2). Nível, rótulos, motivos e
// "o que muda" vêm da API (dono: api/app/domain/exercise_redundancy.py); aqui não há regra de redundância.
import { apiFetch } from './api';

export type RedundancyPreviewItem = { mapping_id: number; equipment_id?: number | null };

export type RedundancyPair = {
  a_index: number;
  b_index: number;
  a_mapping_id: number;
  b_mapping_id: number;
  version: string;
  level: string;
  level_label: string;
  confidence: string;
  reasons: { code: string; label: string }[];
  matching: string[];
  diverging: string[];
  limitations: string[];
};

export function previewRedundancy(
  items: RedundancyPreviewItem[],
  targetIndex: number,
  signal?: AbortSignal,
): Promise<{ items: RedundancyPair[] }> {
  return apiFetch('/workout-plan-templates/redundancy-preview', {
    method: 'POST',
    body: JSON.stringify({
      items: items.map((i) => (i.equipment_id ? { mapping_id: i.mapping_id, equipment_id: i.equipment_id } : { mapping_id: i.mapping_id })),
      target_index: targetIndex,
    }),
    signal,
  });
}

'use client';

import { VolumeGrupamentosClient } from '@/app/(app)/volume-grupamentos/VolumeGrupamentosClient';
import { MobileRecordsPresentation } from './MobileRecordsPresentation';

/**
 * Volume por grupamento (celular): mesmas queries, filtros (objetivo, agrupar por,
 * granularidade, mapa de calor) e leitura de tendência do desktop; os comparativos
 * viram lista (período mais recente em destaque, todos os períodos por toque).
 */
export function VolumeScreen() {
  return (
    <MobileRecordsPresentation>
      <VolumeGrupamentosClient />
    </MobileRecordsPresentation>
  );
}

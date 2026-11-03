'use client';

import { PreferenciasTreinoClient } from '@/app/(app)/preferencias-treino/PreferenciasTreinoClient';
import { MobileRecordsPresentation } from './MobileRecordsPresentation';

/** Preferências de treino (celular): mesma lista de notas por exercício e equipamento do desktop, com o cabeçalho do celular. */
export function PreferenciasScreen() {
  return (
    <MobileRecordsPresentation>
      <PreferenciasTreinoClient />
    </MobileRecordsPresentation>
  );
}

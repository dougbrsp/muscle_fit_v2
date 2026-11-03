'use client';

import { Suspense } from 'react';
import { PerfilClient } from '@/app/(app)/perfil/PerfilClient';
import { LoadingState } from '../../ui';
import { MobileRecordsPresentation } from './MobileRecordsPresentation';

/**
 * Perfil (celular): o mesmo formulário e as mesmas mutações do desktop
 * (`PerfilClient`: dados, foto, Intervals.icu, dispositivos e troca de senha,
 * inclusive `?troque-senha=1`), com o cabeçalho do celular.
 */
export function PerfilScreen() {
  return (
    <MobileRecordsPresentation>
      <Suspense fallback={<LoadingState label="Carregando perfil..." />}>
        <PerfilClient />
      </Suspense>
    </MobileRecordsPresentation>
  );
}

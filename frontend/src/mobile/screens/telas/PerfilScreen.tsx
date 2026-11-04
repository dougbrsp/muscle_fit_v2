'use client';

import { Suspense } from 'react';
import { PerfilClient } from '@/app/(app)/perfil/PerfilClient';
import { LoadingState } from '../../ui';
import { MobileRecordsPresentation } from './MobileRecordsPresentation';

// "Salvar perfil" sempre à vista, acima da tab bar.
const STICKY = `[data-mf-sticky] { position: sticky; bottom: calc(var(--m-nav-offset, 0px) + 8px); z-index: 5; min-height: 48px; }
[data-mf-sticky] ~ *, .mantine-Avatar-root + div button { margin-left: 0; }`;

/**
 * Perfil (celular): o mesmo formulário e as mesmas mutações do desktop
 * (`PerfilClient`: dados, foto, Intervals.icu, dispositivos e troca de senha,
 * inclusive `?troque-senha=1`), com o cabeçalho do celular.
 */
export function PerfilScreen() {
  return (
    <MobileRecordsPresentation>
      <style>{STICKY}</style>
      <Suspense fallback={<LoadingState label="Carregando perfil..." />}>
        <PerfilClient />
      </Suspense>
    </MobileRecordsPresentation>
  );
}

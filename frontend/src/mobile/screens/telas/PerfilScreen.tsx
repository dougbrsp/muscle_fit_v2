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
// Um só botão primário (laranja) por tela: o "Salvar perfil" fixo. Os botões dos cartões de
// Dispositivos e Troca de senha (componentes do desktop) ficam em estilo secundário aqui.
const SECONDARY_BUTTONS = `[data-mobile-shell] main .mantine-Card-root .mantine-Button-root:not([data-variant]) {
  --button-bg: var(--mantine-color-brand-light) !important;
  --button-hover: var(--mantine-color-brand-light-hover) !important;
  --button-color: var(--mantine-color-brand-light-color) !important;
}`;

export function PerfilScreen() {
  return (
    <MobileRecordsPresentation>
      <style>{SECONDARY_BUTTONS}</style>
      <Suspense fallback={<LoadingState label="Carregando perfil..." />}>
        <PerfilClient />
      </Suspense>
    </MobileRecordsPresentation>
  );
}

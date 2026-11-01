'use client';

import { Center, Loader } from '@mantine/core';
import dynamic from 'next/dynamic';
import type { ReactNode } from 'react';
import { useLayoutMode } from '@/lib/mobile/layout-mode';
import { MOBILE_TOKENS } from '@/theme';

// O shell mobile só é baixado quando a flag está ligada E a largura é de
// celular: o bundle desktop não paga por ele.
const MobileShell = dynamic(() => import('@/mobile/layout/MobileShell'), {
  ssr: false,
  loading: () => <NeutralScreen />,
});

function NeutralScreen() {
  return (
    <Center style={{ minHeight: '100dvh', background: MOBILE_TOKENS.bg }}>
      <Loader color="brand" aria-label="Carregando" />
    </Center>
  );
}

/**
 * Escolhe a apresentação pela largura (`useLayoutMode`, sem User-Agent).
 * Montado só com a flag mobile ligada. Estado de domínio NÃO vive aqui nem no
 * shell: cache de dados é do TanStack Query (acima) e a sessão de treino fica
 * em storage; trocar de viewport desmonta apenas a apresentação.
 * Tablet (>= 768 px) cai no layout desktop atual.
 */
export function MobileLayoutSelector({
  children,
  renderDesktop,
}: {
  children: ReactNode;
  renderDesktop: () => ReactNode;
}) {
  const mode = useLayoutMode();
  if (mode === null) return <NeutralScreen />;
  if (mode === 'desktop') return <>{renderDesktop()}</>;
  return <MobileShell>{children}</MobileShell>;
}

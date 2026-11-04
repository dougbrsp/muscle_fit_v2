'use client';

import { Center, Loader } from '@mantine/core';
import dynamic from 'next/dynamic';
import type { ReactNode } from 'react';
import { useLayoutMode } from '@/lib/mobile/layout-mode';
import { MOBILE_TOKENS } from '@/mobile/tokens';

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
 * Escolhe a apresentação pela largura da janela ou pelo aparelho (`useLayoutMode`, sem User-Agent).
 * Montado só com a flag mobile ligada. Estado de domínio NÃO vive aqui nem no
 * shell: cache de dados é do TanStack Query (acima) e a sessão de treino fica
 * em storage; trocar de viewport desmonta apenas a apresentação.
 * Tablet (>= 768 px) cai no layout desktop atual; celular em paisagem (toque e
 * menor lado da tela física < 500 px, ver `layout-mode.ts`) segue no mobile.
 *
 * Cruzar 768 px REMONTA a página (children): as duas árvores (AppShell do
 * Mantine x shell mobile) têm tipos diferentes na mesma posição, e manter
 * `children` estável exigiria mexer na estrutura do desktop. Sobrevivem: a
 * URL, o cache do TanStack Query e a sessão de treino (storage). Perde-se o
 * estado local da página (campo digitado, aba aberta). Ver
 * docs/mobile/BACKLOG_MOBILE.md (MB-10). O Modo Treino (fullscreen) não passa
 * por aqui.
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

'use client';

import { InstallBanner } from './InstallBanner';

/** Ponto único de montagem do PWA (convite de instalação; registro do SW na etapa C2). */
export function PwaProvider({ children }: { children: React.ReactNode }) {
  return (
    <>
      {children}
      <InstallBanner />
    </>
  );
}

'use client';

import { useServiceWorker } from '@/lib/pwa/use-service-worker';

import { InstallBanner } from './InstallBanner';
import { UpdateNotice } from './UpdateNotice';

/** Ponto único de montagem do PWA: convite de instalação, registro do SW e aviso de atualização. */
export function PwaProvider({ children }: { children: React.ReactNode }) {
  const sw = useServiceWorker();
  return (
    <>
      {children}
      <InstallBanner />
      <UpdateNotice {...sw} />
    </>
  );
}

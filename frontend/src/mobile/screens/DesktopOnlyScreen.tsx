'use client';

import { Button } from '@mantine/core';
import { IconDeviceDesktop } from '@tabler/icons-react';
import Link from 'next/link';
import { desktopOnlySectionLabel } from '@/lib/nav-items';
import { usePathname } from 'next/navigation';
import styles from '../layout/mobile-shell.module.css';

/**
 * Tela das seções só do computador (mobile v2, D1). A URL é mantida; a página
 * da rota NÃO é montada, então nenhuma chamada de API dela acontece. Restrição
 * de interface, não de segurança: backend e permissões não mudam.
 */
export function DesktopOnlyScreen() {
  const pathname = usePathname();
  const section = desktopOnlySectionLabel(pathname);
  return (
    <section className={styles.desktopOnly} aria-labelledby="desktop-only-title" data-desktop-only="true">
      <span className={styles.desktopOnlyIcon} aria-hidden>
        <IconDeviceDesktop size={34} />
      </span>
      <h1 id="desktop-only-title" className={styles.desktopOnlyTitle}>
        Disponível no computador
      </h1>
      <p className={styles.desktopOnlyText}>
        {section ? (
          <>
            <strong>{section}</strong> tem telas grandes demais para o celular.{' '}
          </>
        ) : null}
        Abra o MuscleFit no computador para usar esta seção. O resto do app continua aqui.
      </p>
      <Button component={Link} href="/dashboard/" color="brand" size="md" fullWidth maw={320} mih={44}>
        Ir para o Início
      </Button>
    </section>
  );
}

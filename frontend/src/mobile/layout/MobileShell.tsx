'use client';

import { Avatar, Image } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconDots, IconUser } from '@tabler/icons-react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useMemo, type CSSProperties, type ReactNode } from 'react';
import { logoutAndRedirect } from '@/lib/app-logout';
import { navVisibilityFromUser, visibleMainNavItems } from '@/lib/nav-items';
import { MOBILE_TOKENS } from '@/theme';
import { OfflineBanner } from '../ui';
import { useCurrentUser } from '../use-current-user';
import { resolveMobileScreen } from '../screens/registry';
import { MoreSheet } from './MoreSheet';
import { PRIMARY_TABS, buildMoreSections, isMoreActive, isPrimaryTabActive } from './primary-nav';
import styles from './mobile-shell.module.css';

const CSS_VARS = {
  '--m-bg': MOBILE_TOKENS.bg,
  '--m-surface': MOBILE_TOKENS.surface,
  '--m-border': MOBILE_TOKENS.border,
  '--m-accent': MOBILE_TOKENS.accent,
  '--m-text': MOBILE_TOKENS.text,
  '--m-text-muted': MOBILE_TOKENS.textMuted,
  '--m-touch': `${MOBILE_TOKENS.touchTarget}px`,
  '--m-header': `${MOBILE_TOKENS.headerHeight}px`,
  '--m-bottom-nav': `${MOBILE_TOKENS.bottomNavHeight}px`,
  '--m-pad': `${MOBILE_TOKENS.contentPadding}px`,
} as CSSProperties;

/**
 * Shell mobile: header compacto, conteúdo (as MESMAS páginas, mesma URL e
 * mesmas queries) e bottom nav com "Mais". Carregado por `next/dynamic`
 * (`ssr: false`) só com a flag ligada e largura de celular. Sem estado de
 * domínio: nada aqui sobrevive à troca de viewport além do cache do Query.
 */
export default function MobileShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [moreOpened, { open: openMore, close: closeMore }] = useDisclosure(false);
  const userQuery = useCurrentUser();

  const sections = useMemo(
    () =>
      buildMoreSections(
        visibleMainNavItems(navVisibilityFromUser(userQuery.data, userQuery.isSuccess)),
      ),
    [userQuery.data, userQuery.isSuccess],
  );

  // Navegou (inclusive voltar/avançar do navegador): a folha fecha.
  useEffect(() => {
    closeMore();
  }, [pathname, closeMore]);

  function handleLogout() {
    void logoutAndRedirect(router.replace);
  }

  // Tela mobile propria da rota (A2b), se existir; senao a pagina de sempre.
  const Screen = resolveMobileScreen(pathname);
  const displayName = userQuery.data?.full_name || userQuery.data?.username || '';

  return (
    <div className={styles.root} style={CSS_VARS} data-mobile-shell="true">
      <a href="#mobile-main" className={styles.skip}>
        Pular para o conteúdo
      </a>

      <header className={styles.header}>
        <div className={styles.headerInner}>
          <Link href="/dashboard" className={styles.brand} aria-label="MuscleFit - ir para o Início">
            <Image src="/logo.svg" alt="" w={40} h={33} fit="contain" />
            <span className="brand-gradient-text" style={{ fontWeight: 700 }}>
              MuscleFit
            </span>
          </Link>
          <span style={{ flex: 1 }} />
          <Link href="/perfil" style={{ textDecoration: "none" }} aria-label={displayName ? `Perfil de ${displayName}` : 'Perfil'}>
            <Avatar radius="xl" size={32} color="brand">
              {displayName ? displayName.charAt(0).toUpperCase() : <IconUser size={16} />}
            </Avatar>
          </Link>
        </div>
      </header>

      <OfflineBanner />

      <main id="mobile-main" className={styles.main}>
        {Screen ? <Screen /> : children}
      </main>

      <nav className={styles.nav} aria-label="Navegação principal">
        {PRIMARY_TABS.map((tab) => {
          const active = isPrimaryTabActive(tab, pathname);
          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={styles.tab}
              aria-current={active ? 'page' : undefined}
            >
              <tab.icon size={22} aria-hidden />
              <span>{tab.label}</span>
            </Link>
          );
        })}
        <button
          type="button"
          className={styles.tab}
          aria-haspopup="dialog"
          aria-expanded={moreOpened}
          aria-current={isMoreActive(pathname) ? 'page' : undefined}
          onClick={openMore}
        >
          <IconDots size={22} aria-hidden />
          <span>Mais</span>
        </button>
      </nav>

      <MoreSheet
        opened={moreOpened}
        onClose={closeMore}
        sections={sections}
        pathname={pathname}
        onLogout={handleLogout}
      />
    </div>
  );
}

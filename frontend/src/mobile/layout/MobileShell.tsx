'use client';

import { Avatar, Image } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconChevronLeft, IconUser } from '@tabler/icons-react';
import { useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  Suspense,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from 'react';
import { logoutAndRedirect } from '@/lib/app-logout';
import { isDesktopOnlyPath, mobileMainNavItems, navVisibilityFromPermissions } from '@/lib/nav-items';
import { MOBILE_CSS_VARS } from '../css-vars';
import { OfflineBanner } from '../ui';
import { useCurrentUser } from '../use-current-user';
import { DesktopOnlyScreen } from '../screens/DesktopOnlyScreen';
import { resolveMobileScreen } from '../screens/registry';
import { hasOpenView } from '../screens/telas/view-stack';
import { useKeyboardOpen } from './keyboard';
import { scrollMemoryKey } from './nav-active';
import { MoreSheet } from './MoreSheet';
import {
  MORE_TAB,
  backTargetFor,
  buildMoreGroups,
  evolucaoHubHref,
  isMoreActive,
  isPrimaryTabActive,
  primaryTabsFor,
} from './primary-nav';
import styles from './mobile-shell.module.css';

/**
 * Shell mobile (iPhone): header (marca nas abas, voltar nas telas de 2º nível),
 * conteúdo (tela mobile própria do registry, tela "Disponível no computador"
 * ou a MESMA página do desktop, mesma URL e mesmas queries) e tab bar de 5
 * abas. Carregado por `next/dynamic` (`ssr: false`) só com a flag ligada e
 * layout de celular. Sem estado de domínio: nada aqui sobrevive à troca de
 * viewport além do cache do Query.
 */
export default function MobileShell({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={null}>
      <Shell>{children}</Shell>
    </Suspense>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const search = searchParams.toString();
  const [moreOpened, { open: openMore, close: closeMore }] = useDisclosure(false);
  const userQuery = useCurrentUser();
  const keyboardOpen = useKeyboardOpen();
  const scrollPositions = useRef(new Map<string, number>());

  const groups = useMemo(
    () =>
      buildMoreGroups(
        mobileMainNavItems(navVisibilityFromPermissions(userQuery.data)),
      ),
    [userQuery.data],
  );

  // Navegou (inclusive voltar/avançar do navegador): a folha fecha.
  useEffect(() => {
    closeMore();
  }, [pathname, search, closeMore]);

  // Voltar/gesto do navegador: o navegador restaura a rolagem sozinho; a posicao
  // guardada por toque em aba nao pode sobreviver a essa saida.
  useEffect(() => {
    const positions = scrollPositions.current;
    const clear = () => positions.clear();
    window.addEventListener('popstate', clear);
    return () => window.removeEventListener('popstate', clear);
  }, []);

  // Abas preservam a rolagem: guarda a posicao ao sair (toque) e restaura ao
  // voltar. Gesto do usuario (toque, roda, teclado) cancela a restauracao.
  const memoryKey = scrollMemoryKey(pathname, search);
  useEffect(() => {
    if (!memoryKey) return undefined;
    const saved = scrollPositions.current.get(memoryKey);
    if (!saved) return undefined;
    let cancelled = false;
    const cancel = () => {
      cancelled = true;
    };
    const events = ['touchstart', 'wheel', 'keydown', 'pointerdown'] as const;
    for (const name of events) window.addEventListener(name, cancel, { once: true, passive: true });
    const restore = () => {
      if (!cancelled) window.scrollTo(0, saved);
    };
    const raf = requestAnimationFrame(restore);
    const late = setTimeout(restore, 250);
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      clearTimeout(late);
      for (const name of events) window.removeEventListener(name, cancel);
    };
  }, [memoryKey]);

  function rememberScroll() {
    if (memoryKey) scrollPositions.current.set(memoryKey, window.scrollY);
  }

  function handleLogout() {
    void logoutAndRedirect(router.replace, queryClient);
  }

  const desktopOnly = isDesktopOnlyPath(pathname);
  const Screen = desktopOnly ? null : resolveMobileScreen(pathname, search);
  const back = backTargetFor(pathname, search);
  const displayName = userQuery.data?.full_name || userQuery.data?.username || '';

  return (
    <div
      className={styles.root}
      style={MOBILE_CSS_VARS}
      data-mobile-shell="true"
      data-keyboard={keyboardOpen ? 'open' : 'closed'}
      onClickCapture={rememberScroll}
    >
      <a href="#mobile-main" className={styles.skip}>
        Pular para o conteúdo
      </a>

      <header className={styles.header}>
        <div className={styles.headerInner}>
          {back ? (
            back.href ? (
              <Link
                href={back.href}
                className={styles.back}
                aria-label={`Voltar para ${back.label}`}
                onClick={(event) => {
                  // Visão de página inteira aberta (formulário, editor): o voltar só a fecha, na mesma rota.
                  if (hasOpenView()) {
                    event.preventDefault();
                    router.back();
                  }
                }}
              >
                <IconChevronLeft size={26} aria-hidden />
                <span>{back.label}</span>
              </Link>
            ) : (
              <button
                type="button"
                className={styles.back}
                aria-label={`Voltar para ${back.label}`}
                onClick={() => {
                  if (hasOpenView()) router.back();
                  else openMore();
                }}
              >
                <IconChevronLeft size={26} aria-hidden />
                <span>{back.label}</span>
              </button>
            )
          ) : (
            <Link href="/dashboard/" className={styles.brand} aria-label="MuscleFit - ir para o Início">
              <Image src="/logo.svg" alt="" w={36} h={30} fit="contain" />
              <span className="brand-gradient-text" style={{ fontWeight: 700 }}>
                MuscleFit
              </span>
            </Link>
          )}
          <span style={{ flex: 1 }} />
          {back ? null : (
            <Link
              href="/perfil"
              className={styles.avatarLink}
              aria-label={displayName ? `Perfil de ${displayName}` : 'Perfil'}
            >
              <Avatar radius="xl" size={32} color="brand">
                {displayName ? displayName.charAt(0).toUpperCase() : <IconUser size={16} />}
              </Avatar>
            </Link>
          )}
        </div>
      </header>

      <OfflineBanner />

      <main id="mobile-main" tabIndex={-1} className={styles.main}>
        {desktopOnly ? <DesktopOnlyScreen /> : Screen ? <Screen /> : children}
      </main>

      <nav
        className={styles.nav}
        aria-label="Navegação principal"
        data-hidden={keyboardOpen ? 'true' : 'false'}
      >
        {primaryTabsFor(userQuery.data).map((tab) => {
          const active = isPrimaryTabActive(tab, pathname, search);
          return (
            <Link
              key={tab.id}
              href={tab.id === 'evolucao' ? evolucaoHubHref(search) : tab.href}
              className={styles.tab}
              aria-current={active ? 'page' : undefined}
            >
              <tab.icon size={24} stroke={active ? 2.1 : 1.6} aria-hidden />
              <span>{tab.label}</span>
            </Link>
          );
        })}
        <button
          type="button"
          className={styles.tab}
          aria-haspopup="dialog"
          aria-expanded={moreOpened}
          aria-current={isMoreActive(pathname, search) ? 'page' : undefined}
          onClick={openMore}
        >
          <MORE_TAB.icon size={24} stroke={moreOpened || isMoreActive(pathname, search) ? 2.1 : 1.6} aria-hidden />
          <span>{MORE_TAB.label}</span>
        </button>
      </nav>

      <MoreSheet
        opened={moreOpened}
        onClose={closeMore}
        groups={groups}
        pathname={pathname}
        displayName={displayName}
        onLogout={handleLogout}
      />
    </div>
  );
}

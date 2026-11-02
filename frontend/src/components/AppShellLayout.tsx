'use client';

import {
  ActionIcon,
  AppShell,
  Avatar,
  Burger,
  Center,
  Group,
  Image,
  Loader,
  rem,
  Menu,
  NavLink,
  ScrollArea,
  Stack,
  Text,
  Tooltip,
  UnstyledButton,
  useMantineTheme,
} from '@mantine/core';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
import {
  IconChevronDown,
  IconLayoutDashboard,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand,
  IconLogout,
  IconUser,
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Suspense, lazy, useCallback, useEffect, useState, type ReactNode } from 'react';
import {
  DashboardNavLinks,
  DashboardNavLinksFallback,
  DashboardNavMenuItems,
  DashboardNavMenuItemsFallback,
} from '@/components/dashboard/DashboardNavLinks';
import { apiFetch } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import {
  browserStorage,
  isNavbarCollapsed,
  navbarToggleLabel,
  navbarWidth,
  readNavbarCollapsed,
  writeNavbarCollapsed,
} from '@/lib/navbar-collapse';
import {
  DASHBOARD_NAV_ITEMS,
  isNavItemActive,
  navVisibilityFromUser,
  visibleMainNavItems,
  type MainNavItem,
} from '@/lib/nav-items';
import { logoutAndRedirect } from '@/lib/app-logout';
import { applyQueryOverride, useMobileUi } from '@/lib/mobile/flags';
import { BRAND } from '@/theme';

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

/**
 * Item do menu RECOLHIDO: só o ícone, centralizado, com o rótulo no tooltip
 * à direita e no `aria-label`. O ativo mantém o destaque "light" da marca.
 */
function CollapsedNavItem({ item, active }: { item: MainNavItem; active: boolean }) {
  return (
    <Tooltip label={item.label} position="right" withArrow openDelay={300}>
      <ActionIcon
        component={Link}
        href={item.href}
        size={44}
        radius={8}
        variant={active ? 'light' : 'subtle'}
        color={active ? 'brand' : 'gray'}
        aria-label={item.label}
        aria-current={active ? 'page' : undefined}
      >
        <item.icon size={20} />
      </ActionIcon>
    </Tooltip>
  );
}

/**
 * Preferência "menu recolhido", persistida em localStorage. Começa SEMPRE
 * expandido (igual ao HTML do export estático) e só lê o storage depois da
 * montagem — sem hydration mismatch. Abaixo do breakpoint o estado efetivo é
 * sempre expandido: o drawer mobile não muda.
 */
function useNavbarCollapsed() {
  const theme = useMantineTheme();
  const isDesktop = useMediaQuery(`(min-width: ${theme.breakpoints.sm})`) ?? false;
  const [preference, setPreference] = useState(false);

  useEffect(() => {
    setPreference(readNavbarCollapsed(browserStorage()));
  }, []);

  const toggle = useCallback(() => {
    setPreference((current) => {
      const next = !current;
      writeNavbarCollapsed(browserStorage(), next);
      return next;
    });
  }, []);

  return { collapsed: isNavbarCollapsed(preference, isDesktop), toggle };
}

function NeutralScreen() {
  return (
    <Center style={{ minHeight: '100dvh', background: BRAND.bg }}>
      <Loader color="brand" aria-label="Carregando" />
    </Center>
  );
}

// Chunk separado: com a flag desligada o seletor nem é baixado.
const MobileLayoutSelector = lazy(() =>
  import('@/mobile/MobileLayoutSelector').then((m) => ({ default: m.MobileLayoutSelector })),
);

/**
 * Seletor de layout (plano mobile, 3.1/3.2). Flag desligada = a árvore
 * desktop de sempre, sem montar o seletor nem baixar o shell mobile. Ligada,
 * o seletor escolhe por largura (`useLayoutMode`): desktop continua sendo
 * este mesmo `DesktopAppShell`. A flag é lida uma vez na montagem; este
 * componente só renderiza depois do guard de sessão, ou seja, já no cliente.
 */
export function AppShellLayout({ children }: { children: ReactNode }) {
  // `?mobile=0|1` (QA) persiste o override uma vez; o efeito vem ANTES do de
  // `useMobileUi`, que le o storage ja atualizado.
  useEffect(() => {
    applyQueryOverride();
  }, []);
  const mobileUi = useMobileUi();
  if (mobileUi === null) return <NeutralScreen />;
  if (!mobileUi) return <DesktopAppShell>{children}</DesktopAppShell>;
  return (
    <Suspense fallback={<NeutralScreen />}>
      <MobileLayoutSelector renderDesktop={() => <DesktopAppShell>{children}</DesktopAppShell>}>
        {children}
      </MobileLayoutSelector>
    </Suspense>
  );
}

function DesktopAppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [navbarOpened, { toggle: toggleNavbar, close: closeNavbar }] = useDisclosure();

  const currentUserQuery = useQuery({
    queryKey: ['current-user'],
    queryFn: fetchCurrentUser,
    staleTime: 60_000,
    retry: false,
  });

  const user = currentUserQuery.data;
  const displayName = user?.full_name || user?.username || '';
  // Permissões de visibilidade e lista de rotas: `lib/nav-items.ts` (fonte
  // única, compartilhada com o shell mobile).
  const visibleNavItems = visibleMainNavItems(navVisibilityFromUser(user, currentUserQuery.isSuccess));

  const { collapsed, toggle: toggleCollapsed } = useNavbarCollapsed();
  const toggleLabel = navbarToggleLabel(collapsed);

  const dashboardActive = Boolean(pathname?.startsWith('/dashboard'));

  function handleLogout() {
    void logoutAndRedirect(router.replace);
  }

  return (
    <AppShell
      header={{ height: `calc(${rem(64)} + env(safe-area-inset-top, 0px))` }}
      navbar={{
        width: navbarWidth(collapsed),
        breakpoint: 'sm',
        collapsed: { mobile: !navbarOpened, desktop: false },
      }}
      padding="lg"
      styles={{
        header: {
          background: BRAND.bgElevated,
          borderBottom: `1px solid ${BRAND.border}`,
          // Safe areas (celular com flag OFF, PWA, paisagem com notch): env() = 0 no desktop.
          paddingTop: 'env(safe-area-inset-top, 0px)',
          paddingLeft: 'env(safe-area-inset-left, 0px)',
          paddingRight: 'env(safe-area-inset-right, 0px)',
        },
        navbar: {
          background: BRAND.bgElevated,
          borderRight: `1px solid ${BRAND.border}`,
          paddingLeft: 'env(safe-area-inset-left, 0px)',
          paddingBottom: 'env(safe-area-inset-bottom, 0px)',
          // O Mantine já anima transform/top/height da navbar (e o padding do
          // `main`); a largura acompanha a alternância recolhido/expandido.
          transitionProperty: 'transform, top, height, width',
          overflow: 'hidden',
        },
        main: {
          background: BRAND.bg,
          // Safe areas (viewport-fit=cover): env() = 0 no desktop.
          paddingRight: 'max(var(--app-shell-padding), env(safe-area-inset-right, 0px))',
          paddingBottom: 'max(var(--app-shell-padding), env(safe-area-inset-bottom, 0px))',
        },
      }}
    >
      <AppShell.Header>
        <Group h="100%" px="lg" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={navbarOpened} onClick={toggleNavbar} hiddenFrom="sm" size="sm" />
            <Link
              href="/dashboard"
              aria-label="MuscleFit - ir para o Dashboard"
              style={{ display: 'inline-flex' }}
            >
              <Image src="/logo.svg" alt="MuscleFit" w={52} h={43} fit="contain" />
            </Link>
            <Text fw={700} className="brand-gradient-text">
              MuscleFit
            </Text>
          </Group>

          <Menu shadow="md" width={200} position="bottom-end">
            <Menu.Target>
              <UnstyledButton style={{ minHeight: 40, display: 'flex', alignItems: 'center' }}>
                <Group gap={8} wrap="nowrap">
                  <Avatar radius="xl" size="sm" color="brand">
                    {displayName ? displayName.charAt(0).toUpperCase() : <IconUser size={16} />}
                  </Avatar>
                  <Text size="sm" fw={600} c={BRAND.textPrimary} visibleFrom="sm">
                    {displayName || 'Carregando...'}
                  </Text>
                  <IconChevronDown size={14} color={BRAND.textMuted} />
                </Group>
              </UnstyledButton>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item component={Link} href="/perfil" leftSection={<IconUser size={16} />}>
                Meu perfil
              </Menu.Item>
              <Menu.Item color="red" leftSection={<IconLogout size={16} />} onClick={handleLogout}>
                Sair
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p={0} aria-label="Menu principal">
        <AppShell.Section
          grow
          component={ScrollArea}
          scrollbars="y"
          p={collapsed ? 'sm' : 'md'}
        >
          {collapsed ? (
            <Stack gap={6} align="center">
              {/* Recolhido: o Dashboard (único item com submenu) vira um ícone
                  que abre as sub-rotas num menu flutuante à direita. */}
              <Menu position="right-start" offset={12} shadow="md" width={240} withinPortal>
                <Tooltip label="Dashboard" position="right" withArrow openDelay={300}>
                  <Menu.Target>
                    <ActionIcon
                      size={44}
                      radius={8}
                      variant={dashboardActive ? 'light' : 'subtle'}
                      color={dashboardActive ? 'brand' : 'gray'}
                      aria-label="Dashboard"
                    >
                      <IconLayoutDashboard size={20} />
                    </ActionIcon>
                  </Menu.Target>
                </Tooltip>
                <Menu.Dropdown>
                  <Menu.Label>Dashboard</Menu.Label>
                  <Suspense fallback={<DashboardNavMenuItemsFallback items={DASHBOARD_NAV_ITEMS} />}>
                    <DashboardNavMenuItems items={DASHBOARD_NAV_ITEMS} />
                  </Suspense>
                </Menu.Dropdown>
              </Menu>
              {visibleNavItems.map((item) => (
                <CollapsedNavItem
                  key={item.href}
                  item={item}
                  active={isNavItemActive(item, pathname)}
                />
              ))}
            </Stack>
          ) : (
            <Stack gap="xs" onClick={closeNavbar}>
              {/*
                "Dashboard" é o único item com submenu: além da visão geral
                (rota `/dashboard`, inalterada), o Dashboard v2 acrescentou
                sub-rotas analíticas. Ficam aninhadas para não empurrar as 20+
                telas do menu principal para fora da primeira dobra.
                `onClick` com stopPropagation impede que abrir/fechar o submenu
                dispare o `closeNavbar` do Stack no mobile.
              */}
              <NavLink
                label="Dashboard"
                leftSection={<IconLayoutDashboard size={18} />}
                defaultOpened={dashboardActive}
                childrenOffset={18}
                variant="light"
                color="brand"
                styles={{ root: { borderRadius: 8, fontWeight: 600 } }}
                onClick={(event) => event.stopPropagation()}
              >
                {/* Os links do hub carregam o filtro global (período, exercício,
                    grupo) na query string — ver DashboardNavLinks. O Suspense é
                    obrigatório porque esse componente lê `useSearchParams` e o
                    app é export estático. */}
                <Suspense fallback={<DashboardNavLinksFallback items={DASHBOARD_NAV_ITEMS} />}>
                  <DashboardNavLinks items={DASHBOARD_NAV_ITEMS} />
                </Suspense>
              </NavLink>
              {visibleNavItems.map((item) => (
                <NavLink
                  key={item.href}
                  component={Link}
                  href={item.href}
                  label={item.label}
                  leftSection={<item.icon size={18} />}
                  active={isNavItemActive(item, pathname)}
                  variant="light"
                  color="brand"
                  styles={{ root: { borderRadius: 8, fontWeight: 600 } }}
                />
              ))}
            </Stack>
          )}
        </AppShell.Section>

        {/* Alternância expandido/recolhido: só desktop (no mobile o menu é o
            drawer do burger e sempre mostra os rótulos). */}
        <AppShell.Section
          visibleFrom="sm"
          p={collapsed ? 'sm' : 'md'}
          style={{ borderTop: `1px solid ${BRAND.border}` }}
        >
          {collapsed ? (
            <Stack align="center">
              <Tooltip label={toggleLabel} position="right" withArrow>
                <ActionIcon
                  size={44}
                  radius={8}
                  variant="subtle"
                  color="gray"
                  onClick={toggleCollapsed}
                  aria-label={toggleLabel}
                  aria-expanded={false}
                >
                  <IconLayoutSidebarLeftExpand size={20} />
                </ActionIcon>
              </Tooltip>
            </Stack>
          ) : (
            <NavLink
              component="button"
              type="button"
              label={toggleLabel}
              leftSection={<IconLayoutSidebarLeftCollapse size={18} />}
              onClick={toggleCollapsed}
              aria-label={toggleLabel}
              aria-expanded
              c={BRAND.textMuted}
              styles={{ root: { borderRadius: 8, fontWeight: 600 } }}
            />
          )}
        </AppShell.Section>
      </AppShell.Navbar>

      <AppShell.Main>{children}</AppShell.Main>
    </AppShell>
  );
}

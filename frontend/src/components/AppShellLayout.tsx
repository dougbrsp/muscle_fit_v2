'use client';

import {
  ActionIcon,
  AppShell,
  Avatar,
  Burger,
  Group,
  Image,
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
  IconActivity,
  IconApple,
  IconBarbell,
  IconBodyScan,
  IconCalendarEvent,
  IconMoodSmile,
  IconBuildingWarehouse,
  IconChartBar,
  IconChartHistogram,
  IconEngine,
  IconGauge,
  IconHistory,
  IconRepeat,
  IconChevronDown,
  IconClipboardList,
  IconDatabase,
  IconHeartRateMonitor,
  IconLayoutDashboard,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand,
  IconFileAnalytics,
  IconLogout,
  IconMessageChatbot,
  IconNotes,
  IconPlayerPlay,
  IconPlugConnected,
  IconRun,
  IconReportMedical,
  IconScale,
  IconShieldCheck,
  IconStars,
  IconTarget,
  IconTemplate,
  IconUser,
  IconUsers,
  IconWeight,
  type Icon as TablerIcon,
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Suspense, useCallback, useEffect, useState, type ReactNode } from 'react';
import {
  DashboardNavLinks,
  DashboardNavLinksFallback,
  DashboardNavMenuItems,
  DashboardNavMenuItemsFallback,
  type DashboardNavItem,
} from '@/components/dashboard/DashboardNavLinks';
import { apiFetch } from '@/lib/api';
import { clearSession, type SessionUser } from '@/lib/auth';
import {
  browserStorage,
  isNavbarCollapsed,
  navbarToggleLabel,
  navbarWidth,
  readNavbarCollapsed,
  writeNavbarCollapsed,
} from '@/lib/navbar-collapse';
import { BRAND } from '@/theme';

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

/**
 * Sub-itens do menu "Dashboard": a visão geral (rota `/dashboard`, existente
 * desde a Etapa 2) mais as 6 sub-rotas analíticas do Dashboard v2.
 */
const DASHBOARD_NAV_ITEMS: readonly DashboardNavItem[] = [
  { href: '/dashboard', label: 'Visão geral', icon: IconGauge },
  { href: '/dashboard/treinos', label: 'Treinos', icon: IconHistory },
  { href: '/dashboard/progressao', label: 'Progressão', icon: IconChartHistogram },
  { href: '/dashboard/recovery', label: 'Recuperação', icon: IconHeartRateMonitor },
  { href: '/dashboard/musculos', label: 'Grupos musculares', icon: IconBodyScan },
  { href: '/dashboard/corpo', label: 'Corpo', icon: IconScale },
  { href: '/dashboard/cardio', label: 'Cardio', icon: IconRun },
  { href: '/dashboard/ciclo', label: 'Ciclo de treino', icon: IconRepeat },
  { href: '/dashboard/contexto', label: 'Contexto', icon: IconCalendarEvent },
  { href: '/dashboard/checkin', label: 'Check-in diário', icon: IconMoodSmile },
  { href: '/dashboard/engine', label: 'Motor de geração', icon: IconEngine },
  { href: '/dashboard/data-quality', label: 'Qualidade dos dados', icon: IconShieldCheck },
  { href: '/dashboard/relatorios', label: 'Relatórios', icon: IconFileAnalytics },
] as const;

/** Item simples (sem submenu) do menu principal. */
type MainNavItem = {
  href: string;
  label: string;
  icon: TablerIcon;
  /** Prefixos de rota que marcam o item como ativo (default: o próprio `href`). */
  prefixes?: readonly string[];
  /** Regra de permissão/configuração; ausente = sempre visível. */
  visible?: boolean;
};

function isNavItemActive(item: MainNavItem, pathname: string | null): boolean {
  return (item.prefixes ?? [item.href]).some((prefix) => Boolean(pathname?.startsWith(prefix)));
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

export function AppShellLayout({ children }: { children: ReactNode }) {
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
  // Router /training-types inteiro é admin/PT-only no backend
  // (require_admin_or_pt). Enquanto /auth/me ainda não resolveu, mantém o
  // item visível por padrão (evita "sumir e voltar"); a própria tela trata
  // 403 com mensagem clara para quem não tem permissão.
  const canManageTrainingTypes =
    !currentUserQuery.isSuccess || user?.role === 'admin' || user?.role === 'personal_trainer';
  // Router /workout-plan-templates inteiro (exceto assigned-to-me) também é
  // admin/PT-only no backend (require_admin_or_pt) — mesma regra de nav de
  // "Metas de Ativação".
  const canManageWorkoutPlanTemplates = canManageTrainingTypes;
  // GET /users inteiro é admin-only no backend (require_admin, role
  // EXATAMENTE "admin" — personal_trainer não passa aqui, diferente da regra
  // acima). Igual às demais, mantém visível até /auth/me resolver; a própria
  // tela trata 403 com mensagem clara para quem não é admin.
  const isAdmin = !currentUserQuery.isSuccess || user?.role === 'admin';
  // Itens "Intervals.icu" e "Cardio" dependem de credenciais do Intervals.icu
  // configuradas (`GET /auth/me`.`intervals_configured`). Mesma convenção das
  // demais flags de nav: mantém visível até /auth/me resolver (evita
  // "sumir e voltar"); acesso direto às rotas continua mostrando a mensagem
  // de "configure suas credenciais" já existente nessas páginas.
  const hasIntervalsConfigured = !currentUserQuery.isSuccess || user?.intervals_configured === true;

  const { collapsed, toggle: toggleCollapsed } = useNavbarCollapsed();
  const toggleLabel = navbarToggleLabel(collapsed);

  // Itens simples do menu, na ordem de exibição (o "Dashboard", com submenu,
  // vem antes e é renderizado à parte). Uma lista só alimenta os dois modos
  // (expandido e recolhido) para não duplicar rota/rótulo/ícone/permissão.
  //
  // Rotas que saíram do menu mas continuam respondendo (redirect), só para
  // não quebrar favoritos:
  // - "Recuperação muscular" (/recuperacao-muscular) -> /dashboard/recovery
  //   (2ª passada da F2.4: mapa corporal, recomendação de séries e fatores do
  //   cálculo, M33, foram para "Dashboard > Recuperação").
  // - "Evolução corporal" (/evolucao-corporal) -> /dashboard/corpo (F2.4: a
  //   tabela/os gráficos brutos já estavam em "Indicadores Físicos").
  // - "Grupos musculares" (/grupos-musculares) -> /dashboard/musculos (#292;
  //   catálogo canônico e exercícios sem classificação ficaram em "Domínio
  //   de exercícios").
  // - "Treinos" (/treinos) -> /dashboard/treinos (#294; "Ativação muscular
  //   (últimos 7 dias)" foi para "Dashboard > Músculos", #295).
  // - "Cardio" (/cardio, ao vivo) -> /dashboard/cardio (M98 C4: uma leitura
  //   só do Intervals, pelo cache; o item segue em "Dashboard > Cardio").
  const navItems: readonly MainNavItem[] = [
    { href: '/registro-exercicios', label: 'Registro de exercícios', icon: IconClipboardList },
    { href: '/modo-treino', label: 'Modo Treino', icon: IconPlayerPlay },
    { href: '/agente-cardio', label: 'Agente de Cardio', icon: IconActivity },
    { href: '/insights', label: 'Insights', icon: IconMessageChatbot },
    { href: '/indicadores-fisicos', label: 'Indicadores Físicos', icon: IconReportMedical },
    // Nutrição: a entrada abre o diário (`/nutricao`, L4). Todos veem: admin/PT
    // acompanham o diário e o plano dos alunos, os demais o próprio (o backend
    // decide o acesso). Plano (`/nutricao/plano`, L2b) e catálogo de alimentos
    // (`/nutricao/alimentos`, L3a) são seções da mesma entrada: o prefixo
    // `/nutricao` mantém o item ativo e a troca de seção fica no
    // `NutritionSubnav` das páginas, sem item novo aqui.
    { href: '/nutricao', label: 'Nutrição', icon: IconApple, prefixes: ['/nutricao'] },
    { href: '/intervals-icu', label: 'Intervals.icu', icon: IconRun, visible: hasIntervalsConfigured },
    { href: '/exercicios', label: 'Exercícios', icon: IconBarbell },
    { href: '/volume-grupamentos', label: 'Volume por grupamento', icon: IconChartBar },
    { href: '/planos-treino', label: 'Planos de treino', icon: IconNotes },
    { href: '/academias', label: 'Academias', icon: IconBuildingWarehouse },
    { href: '/preferencias-treino', label: 'Preferências de Treino', icon: IconStars },
    // Catálogo de exercícios/equipamentos: só admin (decisão do usuário em
    // 2026-10-13; personal trainer também não vê). As páginas têm guarda de
    // rota (`AdminOnlyRoute`) e o backend continua barrando a escrita.
    {
      href: '/equipamentos-exercicios',
      label: 'Equipamentos x Exercícios',
      icon: IconPlugConnected,
      visible: isAdmin,
    },
    { href: '/dominio-exercicios', label: 'Domínio de Exercícios', icon: IconDatabase, visible: isAdmin },
    // Regras de carga da próxima série (R4): só admin; publicar vale para todos
    // os usuários. Guarda de rota `AdminOnlyRoute` + `require_admin` no backend.
    { href: '/regras-carga', label: 'Regras de carga', icon: IconWeight, visible: isAdmin },
    {
      href: '/metas-ativacao',
      label: 'Metas de Ativação',
      icon: IconTarget,
      visible: canManageTrainingTypes,
    },
    {
      href: '/modelos-treino',
      label: 'Modelos de treino',
      icon: IconTemplate,
      prefixes: ['/modelos-treino', '/diagnostico-volume'],
      visible: canManageWorkoutPlanTemplates,
    },
    { href: '/usuarios', label: 'Usuários', icon: IconUsers, visible: isAdmin },
  ];
  const visibleNavItems = navItems.filter((item) => item.visible ?? true);
  const dashboardActive = Boolean(pathname?.startsWith('/dashboard'));

  async function handleLogout() {
    try {
      await apiFetch('/auth/logout', { method: 'POST' });
    } catch {
      // mesmo se a chamada falhar (token já expirado, rede etc.), a sessão
      // local é limpa e o usuário é redirecionado de qualquer forma.
    } finally {
      clearSession();
      router.replace('/');
    }
  }

  return (
    <AppShell
      header={{ height: 64 }}
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
        },
        navbar: {
          background: BRAND.bgElevated,
          borderRight: `1px solid ${BRAND.border}`,
          // O Mantine já anima transform/top/height da navbar (e o padding do
          // `main`); a largura acompanha a alternância recolhido/expandido.
          transitionProperty: 'transform, top, height, width',
          overflow: 'hidden',
        },
        main: {
          background: BRAND.bg,
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

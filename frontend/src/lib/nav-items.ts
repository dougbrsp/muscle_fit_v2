import {
  IconActivity,
  IconApple,
  IconBarbell,
  IconBodyScan,
  IconBuildingWarehouse,
  IconCalendarEvent,
  IconChartBar,
  IconChartHistogram,
  IconClipboardList,
  IconDatabase,
  IconEngine,
  IconFileAnalytics,
  IconGauge,
  IconHeartRateMonitor,
  IconHistory,
  IconLockAccess,
  IconMessageChatbot,
  IconMoodSmile,
  IconNotes,
  IconPlayerPlay,
  IconPlugConnected,
  IconReportMedical,
  IconRepeat,
  IconRun,
  IconScale,
  IconShieldCheck,
  IconStars,
  IconTarget,
  IconTemplate,
  IconUsers,
  IconWeight,
  type Icon as TablerIcon,
} from '@tabler/icons-react';
import type { SessionUser } from '@/lib/auth';
import { can, type Scope } from '@/lib/permissions';

/**
 * Lista única de rotas do menu autenticado. Alimenta o `AppShellLayout`
 * (desktop) e o shell mobile (`src/mobile`), para as duas navegações nunca
 * divergirem em rota, rótulo, ícone ou permissão. Só apresentação: nenhuma
 * regra de treino mora aqui.
 */

export type NavItem = { href: string; label: string; icon: TablerIcon };

/** Sub-itens do menu "Dashboard": visão geral e as sub-rotas analíticas do Dashboard v2. */
export const DASHBOARD_NAV_ITEMS: readonly NavItem[] = [
  { href: '/dashboard', label: 'Visão geral', icon: IconGauge },
  { href: '/dashboard/treinos', label: 'Treinos', icon: IconHistory },
  { href: '/dashboard/progressao', label: 'Progressão', icon: IconChartHistogram },
  { href: '/dashboard/recovery', label: 'Recuperação', icon: IconHeartRateMonitor },
  { href: '/dashboard/musculos', label: 'Grupos musculares', icon: IconBodyScan },
  { href: '/dashboard/corpo', label: 'Corpo', icon: IconScale },
  { href: '/dashboard/cardio', label: 'Cardio', icon: IconRun },
  { href: '/dashboard/nutricao', label: 'Nutrição', icon: IconApple },
  { href: '/dashboard/ciclo', label: 'Ciclo de treino', icon: IconRepeat },
  { href: '/dashboard/contexto', label: 'Contexto', icon: IconCalendarEvent },
  { href: '/dashboard/checkin', label: 'Check-in diário', icon: IconMoodSmile },
  { href: '/dashboard/engine', label: 'Motor de geração', icon: IconEngine },
  { href: '/dashboard/data-quality', label: 'Qualidade dos dados', icon: IconShieldCheck },
  { href: '/dashboard/relatorios', label: 'Relatórios', icon: IconFileAnalytics },
] as const;

/** Item simples (sem submenu) do menu principal. */
export type MainNavItem = NavItem & {
  /** Prefixos de rota que marcam o item como ativo (default: o próprio `href`). */
  prefixes?: readonly string[];
  /** Permissão/configuração do item; ausente = sempre visível. */
  visible?: boolean;
  /**
   * Seção só do computador (mobile v2, D1): o shell do iPhone não lista o item
   * e mostra a tela "Disponível no computador" na rota. Sem efeito no desktop.
   */
  desktopOnly?: boolean;
};

export function isNavItemActive(item: MainNavItem, pathname: string | null): boolean {
  return (item.prefixes ?? [item.href]).some((prefix) => Boolean(pathname?.startsWith(prefix)));
}

/**
 * Visibilidade do menu, derivada das permissões de `GET /auth/me`
 * (`navVisibilityFromPermissions`). `can` é a função de `lib/permissions`
 * já presa às permissões do usuário; `hasIntervalsConfigured` vem de
 * `intervals_configured` (configuração, não permissão).
 */
export type NavVisibility = {
  can: (resource: string, action?: string, minScope?: Scope) => boolean;
  hasIntervalsConfigured: boolean;
};

/**
 * Decide pela PRESENÇA de dados (`user`), não pelo status da query: um refetch
 * que falha mantém `data` em cache e o menu não pode sumir por isso. Sem dados
 * (carregando ou erro sem cache), ou com `permissions` nulo, tudo fica
 * ESCONDIDO (ADR 0011, fail-closed). O backend é a barreira; aqui só se esconde.
 */
export function navVisibilityFromPermissions(
  user: Pick<SessionUser, 'permissions' | 'intervals_configured'> | undefined,
): NavVisibility {
  const perms = user?.permissions ?? null;
  return {
    can: (resource, action = 'ver', minScope) => can(perms, resource, action, minScope),
    hasIntervalsConfigured: user?.intervals_configured === true,
  };
}

/** Visibilidade total (só para derivar metadados estáticos, como `desktopOnly`). */
const SEE_ALL: NavVisibility = { can: () => true, hasIntervalsConfigured: true };

/** O "Dashboard" (com submenu) é renderizado à parte pelos shells. */
export function canSeeDashboard(visibility: NavVisibility): boolean {
  return visibility.can('dashboards', 'ver');
}

/**
 * Itens simples do menu, na ordem de exibição (o "Dashboard", com submenu,
 * vem antes e é renderizado à parte).
 *
 * Rotas que saíram do menu mas continuam respondendo (redirect), só para não
 * quebrar favoritos: /recuperacao-muscular -> /dashboard/recovery;
 * /evolucao-corporal -> /dashboard/corpo; /grupos-musculares ->
 * /dashboard/musculos; /treinos -> /dashboard/treinos; /cardio ->
 * /dashboard/cardio.
 */
export function buildMainNavItems(visibility: NavVisibility): readonly MainNavItem[] {
  const { can: allowed, hasIntervalsConfigured } = visibility;
  return [
    { href: '/registro-exercicios', label: 'Registro de exercícios', icon: IconClipboardList, visible: allowed('treino.registro') },
    { href: '/modo-treino', label: 'Modo Treino', icon: IconPlayerPlay, visible: allowed('treino.modo_treino') },
    { href: '/agente-cardio', label: 'Agente de Cardio', icon: IconActivity, visible: allowed('ia.cardio') },
    { href: '/insights', label: 'Insights', icon: IconMessageChatbot, visible: allowed('ia.insights') },
    { href: '/indicadores-fisicos', label: 'Indicadores Físicos', icon: IconReportMedical, visible: allowed('corpo.indicadores') },
    // Nutrição: a entrada abre o diário (`/nutricao`); plano e alimentos são
    // seções da mesma entrada (prefixo `/nutricao` mantém o item ativo).
    {
      href: '/nutricao',
      label: 'Nutrição',
      icon: IconApple,
      prefixes: ['/nutricao'],
      visible: allowed('nutricao.diario'),
    },
    { href: '/intervals-icu', label: 'Intervals.icu', icon: IconRun, visible: allowed('integracoes.intervals') && hasIntervalsConfigured },
    { href: '/exercicios', label: 'Exercícios', icon: IconBarbell, visible: allowed('exercicios.catalogo') },
    { href: '/volume-grupamentos', label: 'Volume por grupamento', icon: IconChartBar, visible: allowed('volume.grupamentos') },
    { href: '/planos-treino', label: 'Planos de treino', icon: IconNotes, visible: allowed('treino.planos') },
    { href: '/academias', label: 'Academias', icon: IconBuildingWarehouse, visible: allowed('treino.academias'), desktopOnly: true },
    { href: '/preferencias-treino', label: 'Preferências de Treino', icon: IconStars, visible: allowed('treino.preferencias') },
    // Catálogo de exercícios/equipamentos: só admin (decisão de 2026-10-13).
    {
      href: '/equipamentos-exercicios',
      label: 'Equipamentos x Exercícios',
      icon: IconPlugConnected,
      visible: allowed('admin.catalogos'),
      desktopOnly: true,
    },
    {
      href: '/dominio-exercicios',
      label: 'Domínio de Exercícios',
      icon: IconDatabase,
      visible: allowed('admin.catalogos'),
      desktopOnly: true,
    },
    // Regras de carga da próxima série (R4): só admin.
    { href: '/regras-carga', label: 'Regras de carga', icon: IconWeight, visible: allowed('admin.regras_carga'), desktopOnly: true },
    {
      href: '/metas-ativacao',
      label: 'Metas de Ativação',
      icon: IconTarget,
      visible: allowed('modelos.metas_ativacao'),
      desktopOnly: true,
    },
    {
      href: '/modelos-treino',
      label: 'Modelos de treino',
      icon: IconTemplate,
      prefixes: ['/modelos-treino', '/diagnostico-volume'],
      // Gerir modelos = editar com escopo todos (o usuário comum só vê os próprios).
      visible: allowed('modelos.treino', 'editar', 'todos'),
      desktopOnly: true,
    },
    { href: '/usuarios', label: 'Usuários', icon: IconUsers, visible: allowed('admin.usuarios') },
    // Matriz de concessões (RBAC por grupos): só admin e só no computador.
    {
      href: '/grupos-acessos',
      label: 'Grupos e acessos',
      icon: IconLockAccess,
      visible: allowed('admin.grupos'),
      desktopOnly: true,
    },
  ];
}

export function visibleMainNavItems(visibility: NavVisibility): readonly MainNavItem[] {
  return buildMainNavItems(visibility).filter((item) => item.visible ?? true);
}

/** Itens do menu que o iPhone mostra (sem as seções só do computador). */
export function mobileMainNavItems(visibility: NavVisibility): readonly MainNavItem[] {
  return visibleMainNavItems(visibility).filter((item) => !item.desktopOnly);
}

/**
 * Rotas que existem só como redirecionamento legado de uma seção só-desktop
 * (`/tipos-treinamento` -> `/metas-ativacao`): não são item de menu, mas no
 * iPhone caem na mesma tela "Disponível no computador".
 */
const DESKTOP_ONLY_LEGACY: readonly { prefix: string; label: string }[] = [
  { prefix: '/tipos-treinamento', label: 'Metas de Ativação' },
];

/**
 * Prefixos (de segmento) das rotas só-desktop com o nome da seção, derivados de
 * `desktopOnly` (inclui `[id]` e `/diagnostico-volume/*`, que pertencem a
 * "Modelos de treino"). Independe de papel: o papel só decide o que o menu
 * desktop lista.
 */
const DESKTOP_ONLY_ENTRIES: readonly { prefix: string; label: string }[] = [
  ...buildMainNavItems(SEE_ALL)
    .filter((item) => item.desktopOnly)
    .flatMap((item) => (item.prefixes ?? [item.href]).map((prefix) => ({ prefix, label: item.label }))),
  ...DESKTOP_ONLY_LEGACY,
];

/** Nome da seção só-desktop a que a rota pertence, ou `null`. Casa por segmento. */
export function desktopOnlySectionLabel(pathname: string | null | undefined): string | null {
  if (!pathname) return null;
  const path = pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
  const hit = DESKTOP_ONLY_ENTRIES.find(
    ({ prefix }) => path === prefix || path.startsWith(`${prefix}/`),
  );
  return hit?.label ?? null;
}

/** Pertence a uma seção só-desktop? (`/academias2` não casa com `/academias`.) */
export function isDesktopOnlyPath(pathname: string | null | undefined): boolean {
  return desktopOnlySectionLabel(pathname) !== null;
}

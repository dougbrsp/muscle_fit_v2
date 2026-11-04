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
  /** Regra de permissão/configuração; ausente = sempre visível. */
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

/** Permissões de visibilidade, derivadas de `GET /auth/me` por `navVisibilityFromUser`. */
export type NavVisibility = {
  canManageTrainingTypes: boolean;
  canManageWorkoutPlanTemplates: boolean;
  isAdmin: boolean;
  hasIntervalsConfigured: boolean;
};

/**
 * Enquanto `/auth/me` não resolveu, os itens ficam visíveis (evita "sumir e
 * voltar"); a própria tela trata 403/"configure suas credenciais".
 * - `/training-types` e `/workout-plan-templates`: admin ou personal_trainer.
 * - `/users` e catálogos: role EXATAMENTE "admin".
 * - Intervals.icu/Cardio: `intervals_configured`.
 */
export function navVisibilityFromUser(
  user: { role?: string; intervals_configured?: boolean } | undefined,
  isSuccess: boolean,
): NavVisibility {
  const canManageTrainingTypes = !isSuccess || user?.role === 'admin' || user?.role === 'personal_trainer';
  return {
    canManageTrainingTypes,
    canManageWorkoutPlanTemplates: canManageTrainingTypes,
    isAdmin: !isSuccess || user?.role === 'admin',
    hasIntervalsConfigured: !isSuccess || user?.intervals_configured === true,
  };
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
  const { canManageTrainingTypes, canManageWorkoutPlanTemplates, isAdmin, hasIntervalsConfigured } =
    visibility;
  return [
    { href: '/registro-exercicios', label: 'Registro de exercícios', icon: IconClipboardList },
    { href: '/modo-treino', label: 'Modo Treino', icon: IconPlayerPlay },
    { href: '/agente-cardio', label: 'Agente de Cardio', icon: IconActivity },
    { href: '/insights', label: 'Insights', icon: IconMessageChatbot },
    { href: '/indicadores-fisicos', label: 'Indicadores Físicos', icon: IconReportMedical },
    // Nutrição: a entrada abre o diário (`/nutricao`); plano e alimentos são
    // seções da mesma entrada (prefixo `/nutricao` mantém o item ativo).
    { href: '/nutricao', label: 'Nutrição', icon: IconApple, prefixes: ['/nutricao'] },
    { href: '/intervals-icu', label: 'Intervals.icu', icon: IconRun, visible: hasIntervalsConfigured },
    { href: '/exercicios', label: 'Exercícios', icon: IconBarbell },
    { href: '/volume-grupamentos', label: 'Volume por grupamento', icon: IconChartBar },
    { href: '/planos-treino', label: 'Planos de treino', icon: IconNotes },
    { href: '/academias', label: 'Academias', icon: IconBuildingWarehouse, desktopOnly: true },
    { href: '/preferencias-treino', label: 'Preferências de Treino', icon: IconStars },
    // Catálogo de exercícios/equipamentos: só admin (decisão de 2026-10-13).
    {
      href: '/equipamentos-exercicios',
      label: 'Equipamentos x Exercícios',
      icon: IconPlugConnected,
      visible: isAdmin,
      desktopOnly: true,
    },
    {
      href: '/dominio-exercicios',
      label: 'Domínio de Exercícios',
      icon: IconDatabase,
      visible: isAdmin,
      desktopOnly: true,
    },
    // Regras de carga da próxima série (R4): só admin.
    { href: '/regras-carga', label: 'Regras de carga', icon: IconWeight, visible: isAdmin, desktopOnly: true },
    {
      href: '/metas-ativacao',
      label: 'Metas de Ativação',
      icon: IconTarget,
      visible: canManageTrainingTypes,
      desktopOnly: true,
    },
    {
      href: '/modelos-treino',
      label: 'Modelos de treino',
      icon: IconTemplate,
      prefixes: ['/modelos-treino', '/diagnostico-volume'],
      visible: canManageWorkoutPlanTemplates,
      desktopOnly: true,
    },
    { href: '/usuarios', label: 'Usuários', icon: IconUsers, visible: isAdmin },
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
  ...buildMainNavItems({
    canManageTrainingTypes: true,
    canManageWorkoutPlanTemplates: true,
    isAdmin: true,
    hasIntervalsConfigured: true,
  })
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

import {
  IconBarbell,
  IconClipboardList,
  IconLayoutDashboard,
  IconPlayerPlay,
  IconUser,
  type Icon as TablerIcon,
} from '@tabler/icons-react';
import { DASHBOARD_NAV_ITEMS, type MainNavItem, type NavItem } from '@/lib/nav-items';

/**
 * Abas fixas do bottom nav (plano mobile, 3.3). A sexta aba, "Mais", abre a
 * lista com TODAS as demais rotas do menu desktop: nenhuma rota fica
 * inacessível no celular. Funções puras: testáveis sem DOM.
 */
export type PrimaryTab = {
  href: string;
  label: string;
  icon: TablerIcon;
  /** `exact`: ativa só na própria rota (o Início não pode acender em /dashboard/treinos). */
  exact?: boolean;
};

export const PRIMARY_TABS: readonly PrimaryTab[] = [
  { href: '/dashboard', label: 'Início', icon: IconLayoutDashboard, exact: true },
  { href: '/modo-treino', label: 'Treino', icon: IconPlayerPlay },
  { href: '/registro-exercicios', label: 'Histórico', icon: IconClipboardList },
  { href: '/exercicios', label: 'Biblioteca', icon: IconBarbell },
  { href: '/perfil', label: 'Perfil', icon: IconUser },
];

/** Normaliza `/rota/` (trailingSlash do export) para `/rota`. */
function normalize(pathname: string | null): string {
  if (!pathname) return '';
  return pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
}

export function isPrimaryTabActive(tab: PrimaryTab, pathname: string | null): boolean {
  const path = normalize(pathname);
  if (tab.exact) return path === tab.href;
  return path === tab.href || path.startsWith(`${tab.href}/`);
}

export function isMoreActive(pathname: string | null): boolean {
  return !PRIMARY_TABS.some((tab) => isPrimaryTabActive(tab, pathname));
}

export type MoreSections = {
  /** Sub-rotas do Dashboard (sem a visão geral, que é a aba Início). */
  dashboard: readonly NavItem[];
  /** Demais itens do menu principal ainda visíveis para o usuário. */
  main: readonly MainNavItem[];
};

/** Tudo que não é aba fixa vai para "Mais". `/perfil` não está no menu desktop (fica no menu do usuário). */
export function buildMoreSections(visibleMain: readonly MainNavItem[]): MoreSections {
  const primary = new Set(PRIMARY_TABS.map((tab) => tab.href));
  return {
    dashboard: DASHBOARD_NAV_ITEMS.filter((item) => item.href !== '/dashboard'),
    main: visibleMain.filter((item) => !primary.has(item.href)),
  };
}

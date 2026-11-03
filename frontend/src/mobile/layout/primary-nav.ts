import {
  IconChartLine,
  IconClipboardList,
  IconDots,
  IconLayoutDashboard,
  IconPlayerPlay,
  IconUser,
  type Icon as TablerIcon,
} from '@tabler/icons-react';
import {
  DASHBOARD_NAV_ITEMS,
  isDesktopOnlyPath,
  type MainNavItem,
  type NavItem,
} from '@/lib/nav-items';

/**
 * Navegação do iPhone (mobile v2, ADR 0004). Funções puras, testáveis sem DOM.
 *
 * - Tab bar de 5 abas (HIG): Início, Treino, Histórico, Evolução, Mais.
 * - Evolução é um hub (lista por objetivo) na URL `/dashboard/?hub=evolucao`:
 *   reaproveita a rota `/dashboard` (nenhuma rota nova aparece no desktop,
 *   onde o parâmetro é ignorado).
 * - Mais é uma folha com lista agrupada; as seções só-desktop não entram.
 */

export const EVOLUCAO_HUB_HREF = '/dashboard/?hub=evolucao';

export type PrimaryTab = {
  id: 'inicio' | 'treino' | 'historico' | 'evolucao';
  href: string;
  label: string;
  icon: TablerIcon;
};

/** As 4 abas que são links; a 5ª ("Mais") abre a folha e vem de `MORE_TAB`. */
export const PRIMARY_TABS: readonly PrimaryTab[] = [
  { id: 'inicio', href: '/dashboard/', label: 'Início', icon: IconLayoutDashboard },
  { id: 'treino', href: '/modo-treino', label: 'Treino', icon: IconPlayerPlay },
  { id: 'historico', href: '/registro-exercicios', label: 'Histórico', icon: IconClipboardList },
  { id: 'evolucao', href: EVOLUCAO_HUB_HREF, label: 'Evolução', icon: IconChartLine },
];

export const MORE_TAB = { id: 'mais', label: 'Mais', icon: IconDots } as const;

/** Rotas fora de `/dashboard/*` que pertencem ao hub Evolução. */
const EVOLUCAO_EXTRA_PATHS: readonly string[] = ['/insights', '/agente-cardio', '/intervals-icu'];

/** Normaliza `/rota/` (trailingSlash do export) para `/rota`. */
export function normalizePath(pathname: string | null | undefined): string {
  if (!pathname) return '';
  return pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
}

function underPrefix(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(`${prefix}/`);
}

/** `search` aceita com ou sem `?`. */
export function isEvolucaoHub(
  pathname: string | null | undefined,
  search: string | null | undefined,
): boolean {
  if (normalizePath(pathname) !== '/dashboard') return false;
  return new URLSearchParams(search ?? '').get('hub') === 'evolucao';
}

export function isPrimaryTabActive(
  tab: PrimaryTab,
  pathname: string | null | undefined,
  search: string | null | undefined = '',
): boolean {
  const path = normalizePath(pathname);
  const hub = isEvolucaoHub(path, search);
  switch (tab.id) {
    case 'inicio':
      return path === '/dashboard' && !hub;
    case 'evolucao':
      return (
        hub ||
        path.startsWith('/dashboard/') ||
        EVOLUCAO_EXTRA_PATHS.some((prefix) => underPrefix(path, prefix))
      );
    case 'treino':
      return underPrefix(path, '/modo-treino');
    case 'historico':
      return underPrefix(path, '/registro-exercicios');
    default:
      return false;
  }
}

export function isMoreActive(
  pathname: string | null | undefined,
  search: string | null | undefined = '',
): boolean {
  return !PRIMARY_TABS.some((tab) => isPrimaryTabActive(tab, pathname, search));
}

// ---------------------------------------------------------------------------
// Evolução (hub) e Mais (folha): grupos de apresentação
// ---------------------------------------------------------------------------

export type NavGroup = { title: string; items: readonly NavItem[] };

type GroupLayout = readonly { title: string; hrefs: readonly string[] }[];

const EVOLUCAO_LAYOUT: GroupLayout = [
  {
    title: 'Treino',
    hrefs: ['/dashboard/treinos', '/dashboard/progressao', '/dashboard/ciclo', '/dashboard/musculos'],
  },
  {
    title: 'Corpo e recuperação',
    hrefs: ['/dashboard/recovery', '/dashboard/corpo', '/dashboard/checkin', '/dashboard/contexto'],
  },
  {
    title: 'Cardio e nutrição',
    hrefs: ['/dashboard/cardio', '/agente-cardio', '/intervals-icu', '/dashboard/nutricao'],
  },
  {
    title: 'Análises',
    hrefs: ['/insights', '/dashboard/relatorios', '/dashboard/data-quality', '/dashboard/engine'],
  },
];

const PERFIL_ITEM: NavItem = { href: '/perfil', label: 'Perfil', icon: IconUser };

const MORE_LAYOUT: GroupLayout = [
  {
    title: 'Treino',
    hrefs: ['/planos-treino', '/exercicios', '/volume-grupamentos', '/preferencias-treino'],
  },
  { title: 'Corpo e nutrição', hrefs: ['/indicadores-fisicos', '/nutricao'] },
  { title: 'Conta', hrefs: ['/perfil', '/usuarios'] },
];

/** Itens do menu principal que o iPhone mostra: sem as seções só-desktop. */
function mobileCandidates(visibleMain: readonly MainNavItem[]): readonly MainNavItem[] {
  return visibleMain.filter((item) => !item.desktopOnly && !isDesktopOnlyPath(item.href));
}

function groupBy(layout: GroupLayout, pool: ReadonlyMap<string, NavItem>) {
  const used = new Set<string>();
  const groups: NavGroup[] = [];
  for (const entry of layout) {
    const items = entry.hrefs.flatMap((href) => {
      const item = pool.get(href);
      if (!item) return [];
      used.add(href);
      return [item];
    });
    if (items.length > 0) groups.push({ title: entry.title, items });
  }
  return { groups, used };
}

/**
 * Grupos do hub Evolução. `visibleMain` são os itens do menu já filtrados por
 * papel/configuração (ex.: Intervals.icu só se configurado).
 */
export function buildEvolucaoGroups(visibleMain: readonly MainNavItem[]): readonly NavGroup[] {
  const pool = new Map<string, NavItem>();
  for (const item of DASHBOARD_NAV_ITEMS) if (item.href !== '/dashboard') pool.set(item.href, item);
  for (const item of mobileCandidates(visibleMain)) {
    if (EVOLUCAO_EXTRA_PATHS.includes(item.href)) pool.set(item.href, item);
  }
  return groupBy(EVOLUCAO_LAYOUT, pool).groups;
}

/**
 * Grupos da folha "Mais": tudo que o iPhone alcança e não é aba nem hub.
 * Itens sem grupo definido caem em "Outros" (nenhuma rota fica inacessível).
 */
export function buildMoreGroups(visibleMain: readonly MainNavItem[]): readonly NavGroup[] {
  const tabHrefs = new Set(PRIMARY_TABS.map((tab) => normalizePath(tab.href)));
  const pool = new Map<string, NavItem>([[PERFIL_ITEM.href, PERFIL_ITEM]]);
  for (const item of mobileCandidates(visibleMain)) {
    if (tabHrefs.has(normalizePath(item.href)) || EVOLUCAO_EXTRA_PATHS.includes(item.href)) continue;
    pool.set(item.href, item);
  }
  const { groups, used } = groupBy(MORE_LAYOUT, pool);
  const rest = [...pool.values()].filter((item) => !used.has(item.href));
  return rest.length > 0 ? [...groups, { title: 'Outros', items: rest }] : groups;
}

// ---------------------------------------------------------------------------
// Header de 2º nível
// ---------------------------------------------------------------------------

/** Destino do botão voltar; `href: null` = reabrir a folha "Mais". */
export type BackTarget = { label: string; href: string | null };

const BACK_LABELS: ReadonlyMap<string, string> = new Map([
  ['/registro-exercicios', 'Histórico'],
  ['/planos-treino', 'Planos de treino'],
  ['/nutricao', 'Nutrição'],
  ['/exercicios', 'Exercícios'],
  ['/indicadores-fisicos', 'Indicadores Físicos'],
]);

/**
 * `null` nas raízes das abas (Início, Treino, Histórico, Evolução). Nas demais
 * telas, o destino do voltar: hub Evolução para o que é de Evolução, a raiz da
 * própria seção para detalhes, e "Mais" para o resto.
 */
export function backTargetFor(pathname: string | null | undefined): BackTarget | null {
  const path = normalizePath(pathname);
  if (!path || path === '/dashboard') return null; // Início e hub
  if (path === '/modo-treino' || path === '/registro-exercicios') return null;
  if (
    path.startsWith('/dashboard/') ||
    EVOLUCAO_EXTRA_PATHS.some((prefix) => underPrefix(path, prefix))
  ) {
    return { label: 'Evolução', href: EVOLUCAO_HUB_HREF };
  }
  const segments = path.split('/').filter(Boolean);
  const root = `/${segments[0]}`;
  if (segments.length > 1) {
    const label = BACK_LABELS.get(root);
    if (label) return { label, href: `${root}/` };
  }
  return { label: 'Mais', href: null };
}

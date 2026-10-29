'use client';

import { Menu, NavLink } from '@mantine/core';
import type { Icon as TablerIcon } from '@tabler/icons-react';
import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { dashboardHref, parseDashboardFilters } from '@/lib/dashboard-filters';

export type DashboardNavItem = { href: string; label: string; icon: TablerIcon };

/**
 * Links do menu "Dashboard" que PROPAGAM o filtro global do hub.
 *
 * O contexto (período, exercício, grupo, ficha) já vive na query string, mas
 * o `<NavLink href="/dashboard/musculos">` fixo a descartava: trocar de aba
 * zerava o período escolhido. Aqui o `href` é reconstruído a partir dos
 * filtros atuais quando a navegação é DENTRO do hub.
 *
 * Fora do hub (`/treinos`, `/planos-treino`...) os links vão limpos: herdar
 * uma janela de 180 dias ao entrar no dashboard vindo de outra tela seria
 * surpreendente.
 *
 * Usa `useSearchParams`, então precisa de um `<Suspense>` ao redor no layout —
 * o app é export estático e o `next build` falha sem ele.
 */
function isDashboardItemActive(pathname: string | null, href: string): boolean {
  return pathname === href || pathname === `${href}/`;
}

/** `href` de um item do hub, carregando o filtro global quando já estamos nele. */
function useDashboardItemHref(): (href: string) => string {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const insideHub = Boolean(pathname?.startsWith('/dashboard'));
  const filters = parseDashboardFilters(new URLSearchParams(searchParams.toString()));
  return (href) => (insideHub ? dashboardHref(href, filters) : href);
}

export function DashboardNavLinks({ items }: { items: readonly DashboardNavItem[] }) {
  const pathname = usePathname();
  const itemHref = useDashboardItemHref();

  return (
    <>
      {items.map((item) => (
        <NavLink
          key={item.href}
          component={Link}
          href={itemHref(item.href)}
          label={item.label}
          leftSection={<item.icon size={16} />}
          active={isDashboardItemActive(pathname, item.href)}
          variant="light"
          color="brand"
          styles={{ root: { borderRadius: 8 } }}
        />
      ))}
    </>
  );
}

/** Versão estática, usada como `fallback` do Suspense (sem query string). */
export function DashboardNavLinksFallback({ items }: { items: readonly DashboardNavItem[] }) {
  return (
    <>
      {items.map((item) => (
        <NavLink
          key={item.href}
          component={Link}
          href={item.href}
          label={item.label}
          leftSection={<item.icon size={16} />}
          variant="light"
          color="brand"
          styles={{ root: { borderRadius: 8 } }}
        />
      ))}
    </>
  );
}

/**
 * Mesmos links como `Menu.Item` — usados pelo menu lateral RECOLHIDO, em que
 * o ícone do Dashboard abre um menu flutuante à direita com as sub-rotas.
 * Também lê `useSearchParams`: precisa do mesmo `<Suspense>` ao redor.
 */
export function DashboardNavMenuItems({ items }: { items: readonly DashboardNavItem[] }) {
  const pathname = usePathname();
  const itemHref = useDashboardItemHref();
  return (
    <>
      {items.map((item) => (
        <DashboardMenuItem
          key={item.href}
          item={item}
          href={itemHref(item.href)}
          active={isDashboardItemActive(pathname, item.href)}
        />
      ))}
    </>
  );
}

/** Fallback estático de `DashboardNavMenuItems` (sem query string). */
export function DashboardNavMenuItemsFallback({ items }: { items: readonly DashboardNavItem[] }) {
  return (
    <>
      {items.map((item) => (
        <DashboardMenuItem key={item.href} item={item} href={item.href} active={false} />
      ))}
    </>
  );
}

function DashboardMenuItem({
  item,
  href,
  active,
}: {
  item: DashboardNavItem;
  href: string;
  active: boolean;
}) {
  return (
    <Menu.Item
      component={Link}
      href={href}
      leftSection={<item.icon size={16} />}
      color={active ? 'brand' : undefined}
      fw={active ? 700 : undefined}
      aria-current={active ? 'page' : undefined}
    >
      {item.label}
    </Menu.Item>
  );
}

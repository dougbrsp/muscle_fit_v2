import { describe, expect, it } from 'vitest';
import { DASHBOARD_NAV_ITEMS, visibleMainNavItems, navVisibilityFromUser } from '@/lib/nav-items';
import { PRIMARY_TABS, buildMoreSections, isMoreActive, isPrimaryTabActive } from './primary-nav';

const admin = navVisibilityFromUser({ role: 'admin', intervals_configured: true }, true);
const plain = navVisibilityFromUser({ role: 'user', intervals_configured: false }, true);

describe('bottom nav mobile', () => {
  it('Início só acende na própria rota (com ou sem barra final)', () => {
    const inicio = PRIMARY_TABS[0];
    expect(isPrimaryTabActive(inicio, '/dashboard')).toBe(true);
    expect(isPrimaryTabActive(inicio, '/dashboard/')).toBe(true);
    expect(isPrimaryTabActive(inicio, '/dashboard/treinos/')).toBe(false);
  });

  it('abas por prefixo acendem em subrotas; "Mais" acende no resto', () => {
    const biblioteca = PRIMARY_TABS.find((t) => t.label === 'Biblioteca')!;
    expect(isPrimaryTabActive(biblioteca, '/exercicios/12/')).toBe(true);
    expect(isPrimaryTabActive(biblioteca, '/exercicios-extra')).toBe(false);
    expect(isMoreActive('/nutricao/')).toBe(true);
    expect(isMoreActive('/dashboard/progressao/')).toBe(true);
    expect(isMoreActive('/perfil/')).toBe(false);
  });
});

describe('"Mais" cobre todas as rotas do menu desktop', () => {
  for (const [nome, vis] of [
    ['admin', admin],
    ['usuário comum', plain],
  ] as const) {
    it(`nenhuma rota fica inacessível (${nome})`, () => {
      const main = visibleMainNavItems(vis);
      const sections = buildMoreSections(main);
      const reachable = new Set<string>([
        ...PRIMARY_TABS.map((t) => t.href),
        ...sections.dashboard.map((i) => i.href),
        ...sections.main.map((i) => i.href),
      ]);
      for (const item of main) expect(reachable.has(item.href), item.href).toBe(true);
      for (const item of DASHBOARD_NAV_ITEMS) expect(reachable.has(item.href), item.href).toBe(true);
    });
  }

  it('itens administrativos respeitam a permissão', () => {
    const hrefs = (v: typeof admin) => visibleMainNavItems(v).map((i) => i.href);
    expect(hrefs(admin)).toContain('/usuarios');
    expect(hrefs(plain)).not.toContain('/usuarios');
    expect(hrefs(plain)).not.toContain('/intervals-icu');
  });

  it('enquanto /auth/me não resolve, itens condicionais ficam visíveis', () => {
    const pending = navVisibilityFromUser(undefined, false);
    expect(visibleMainNavItems(pending).map((i) => i.href)).toContain('/usuarios');
  });
});

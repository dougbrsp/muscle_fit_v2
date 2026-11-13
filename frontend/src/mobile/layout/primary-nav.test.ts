import { describe, expect, it } from 'vitest';
import {
  DASHBOARD_NAV_ITEMS,
  isDesktopOnlyPath,
  mobileMainNavItems,
  navVisibilityFromPermissions,
  visibleMainNavItems,
} from '@/lib/nav-items';
import {
  EVOLUCAO_HUB_HREF,
  PRIMARY_TABS,
  backTargetFor,
  buildEvolucaoGroups,
  buildMoreGroups,
  evolucaoHubHref,
  visiblePrimaryTabs,
  isEvolucaoHub,
  isMoreActive,
  isPrimaryTabActive,
} from './primary-nav';
import { isNavGroupItemActive, scrollMemoryKey } from './nav-active';

// Permissões mínimas por papel (espelho reduzido das concessões padrão do catálogo);
// a paridade completa com o menu por role está em lib/nav-items.test.ts.
type Perms = Record<string, Record<string, string>>;
const give = (p: Perms, keys: string[], scope: string) => {
  for (const k of keys) p[k] = { ver: scope, editar: scope, excluir: scope };
};
const COMUM_KEYS = [
  'dashboards', 'treino.registro', 'treino.modo_treino', 'treino.planos', 'treino.preferencias',
  'treino.academias', 'volume.grupamentos', 'corpo.indicadores', 'integracoes.intervals',
  'nutricao.diario', 'ia.cardio', 'ia.insights', 'exercicios.catalogo',
];
const plainPerms: Perms = {};
give(plainPerms, COMUM_KEYS, 'proprios');
const ptPerms: Perms = JSON.parse(JSON.stringify(plainPerms));
give(ptPerms, ['modelos.treino', 'modelos.metas_ativacao'], 'todos');
const adminPerms: Perms = JSON.parse(JSON.stringify(ptPerms));
give(adminPerms, ['admin.usuarios', 'admin.grupos', 'admin.regras_carga', 'admin.catalogos'], 'todos');

const admin = navVisibilityFromPermissions({ permissions: adminPerms, intervals_configured: true });
const pt = navVisibilityFromPermissions({ permissions: ptPerms, intervals_configured: false });
const plain = navVisibilityFromPermissions({ permissions: plainPerms, intervals_configured: false });

const tab = (id: string) => PRIMARY_TABS.find((t) => t.id === id)!;

describe('tab bar mobile (5 abas)', () => {
  it('são 4 links + Mais = 5, na ordem do HIG', () => {
    expect(PRIMARY_TABS.map((t) => t.label)).toEqual(['Início', 'Treino', 'Histórico', 'Evolução']);
  });

  it('Início só acende em /dashboard sem o hub (com ou sem barra final)', () => {
    expect(isPrimaryTabActive(tab('inicio'), '/dashboard')).toBe(true);
    expect(isPrimaryTabActive(tab('inicio'), '/dashboard/')).toBe(true);
    expect(isPrimaryTabActive(tab('inicio'), '/dashboard/', 'hub=evolucao')).toBe(false);
    expect(isPrimaryTabActive(tab('inicio'), '/dashboard/treinos/')).toBe(false);
  });

  it('Evolução acende no hub e em tudo que é análise; Mais acende no resto', () => {
    const evo = tab('evolucao');
    expect(isEvolucaoHub('/dashboard/', '?hub=evolucao')).toBe(true);
    expect(isEvolucaoHub('/dashboard/', '?hub=outro')).toBe(false);
    expect(isPrimaryTabActive(evo, '/dashboard/', '?hub=evolucao')).toBe(true);
    expect(isPrimaryTabActive(evo, '/dashboard/progressao/')).toBe(true);
    expect(isPrimaryTabActive(evo, '/insights/')).toBe(true);
    expect(isPrimaryTabActive(evo, '/agente-cardio')).toBe(true);
    expect(isPrimaryTabActive(evo, '/dashboard/')).toBe(false);
    expect(isMoreActive('/nutricao/')).toBe(true);
    expect(isMoreActive('/perfil/')).toBe(true);
    expect(isMoreActive('/dashboard/progressao/')).toBe(false);
    expect(isMoreActive('/registro-exercicios/9/')).toBe(false);
  });

  it('Treino e Histórico casam por segmento', () => {
    expect(isPrimaryTabActive(tab('historico'), '/registro-exercicios/12/')).toBe(true);
    expect(isPrimaryTabActive(tab('historico'), '/registro-exercicios-extra')).toBe(false);
    expect(isPrimaryTabActive(tab('treino'), '/modo-treino')).toBe(true);
  });
});

describe('D1: seções só do computador', () => {
  const SEIS = [
    '/academias',
    '/equipamentos-exercicios',
    '/dominio-exercicios',
    '/regras-carga',
    '/metas-ativacao',
    '/modelos-treino',
  ];

  for (const [nome, vis] of [
    ['admin', admin],
    ['personal trainer', pt],
    ['usuário comum', plain],
  ] as const) {
    it(`nenhuma das 6 aparece na navegação mobile (${nome})`, () => {
      const main = mobileMainNavItems(vis);
      const groups = buildMoreGroups(visibleMainNavItems(vis));
      const evolucao = buildEvolucaoGroups(visibleMainNavItems(vis), vis);
      const shown = new Set<string>([
        ...main.map((i) => i.href),
        ...groups.flatMap((g) => g.items.map((i) => i.href)),
        ...evolucao.flatMap((g) => g.items.map((i) => i.href)),
        ...PRIMARY_TABS.map((t) => t.href),
      ]);
      for (const href of SEIS) expect(shown.has(href), href).toBe(false);
    });
  }

  it('admin/PT ainda veem as seis no desktop (visibleMainNavItems não muda)', () => {
    const hrefs = visibleMainNavItems(admin).map((i) => i.href);
    for (const href of SEIS) expect(hrefs, href).toContain(href);
    const ptHrefs = visibleMainNavItems(pt).map((i) => i.href);
    expect(ptHrefs).toContain('/modelos-treino');
    expect(ptHrefs).toContain('/metas-ativacao');
    expect(ptHrefs).toContain('/academias');
  });

  it('isDesktopOnlyPath cobre subrotas, diagnóstico de volume e o redirect legado', () => {
    for (const p of [
      '/academias',
      '/academias/',
      '/equipamentos-exercicios',
      '/dominio-exercicios/',
      '/regras-carga',
      '/metas-ativacao',
      '/metas-ativacao/7',
      '/modelos-treino',
      '/modelos-treino/12/',
      '/diagnostico-volume/3',
      '/tipos-treinamento',
      '/tipos-treinamento/4',
    ]) {
      expect(isDesktopOnlyPath(p), p).toBe(true);
    }
    for (const p of ['/dashboard', '/academias-x', '/exercicios', '/planos-treino/1', '/usuarios', '/', '', null, undefined]) {
      expect(isDesktopOnlyPath(p), String(p)).toBe(false);
    }
  });
});

describe('toda rota mobile continua alcançável', () => {
  for (const [nome, vis] of [
    ['admin', admin],
    ['personal trainer', pt],
    ['usuário comum', plain],
  ] as const) {
    it(`menu principal e dashboard (${nome})`, () => {
      const visible = visibleMainNavItems(vis);
      const reachable = new Set<string>([
        ...PRIMARY_TABS.map((t) => t.href.split('?')[0].replace(/\/$/, '')),
        ...buildMoreGroups(visible).flatMap((g) => g.items.map((i) => i.href)),
        ...buildEvolucaoGroups(visible, vis).flatMap((g) => g.items.map((i) => i.href)),
      ]);
      for (const item of visible) {
        if (item.desktopOnly) continue;
        expect(reachable.has(item.href), item.href).toBe(true);
      }
      for (const item of DASHBOARD_NAV_ITEMS) {
        if (item.href === '/dashboard') continue;
        expect(reachable.has(item.href), item.href).toBe(true);
      }
      expect(reachable.has('/perfil')).toBe(true);
    });
  }

  it('itens administrativos e condicionais respeitam o papel', () => {
    const more = (v: typeof admin) => buildMoreGroups(visibleMainNavItems(v)).flatMap((g) => g.items.map((i) => i.href));
    const evo = (v: typeof admin) => buildEvolucaoGroups(visibleMainNavItems(v), v).flatMap((g) => g.items.map((i) => i.href));
    expect(more(admin)).toContain('/usuarios');
    expect(more(plain)).not.toContain('/usuarios');
    expect(evo(admin)).toContain('/intervals-icu');
    expect(evo(plain)).not.toContain('/intervals-icu');
  });

  it('enquanto /auth/me não resolve, nenhum item do menu aparece (esconde por padrão)', () => {
    const pending = navVisibilityFromPermissions(undefined);
    expect(visibleMainNavItems(pending)).toEqual([]);
    expect(mobileMainNavItems(pending)).toEqual([]);
  });

  it('nenhum item aparece em dois lugares da navegação', () => {
    const visible = visibleMainNavItems(admin);
    const all = [
      ...buildMoreGroups(visible).flatMap((g) => g.items.map((i) => i.href)),
      ...buildEvolucaoGroups(visible, admin).flatMap((g) => g.items.map((i) => i.href)),
    ];
    expect(new Set(all).size).toBe(all.length);
  });
});

describe('abas e pool do hub por permissões', () => {
  const ALL = ['inicio', 'treino', 'historico', 'evolucao'];
  const poolOf = (v: typeof admin) =>
    buildEvolucaoGroups(mobileMainNavItems(v), v).flatMap((g) => g.items.map((i) => i.href));

  for (const [nome, vis] of [
    ['admin', admin],
    ['personal trainer', pt],
    ['usuário comum', plain],
  ] as const) {
    it(`concessões padrão mantêm as 4 abas e o pool completo (${nome})`, () => {
      expect(visiblePrimaryTabs(vis).map((t) => t.id)).toEqual(ALL);
      const pool = poolOf(vis);
      for (const item of DASHBOARD_NAV_ITEMS) {
        if (item.href !== '/dashboard') expect(pool, item.href).toContain(item.href);
      }
      expect(pool).toContain('/insights');
      expect(pool).toContain('/agente-cardio');
    });
  }

  for (const [nome, user] of [
    ['sem /auth/me', undefined],
    ['permissions nulo', { permissions: null, intervals_configured: true }],
    ['permissions vazio', { permissions: {}, intervals_configured: true }],
  ] as const) {
    it(`fail-closed: nenhuma aba de link nem pool (${nome}); Mais segue disponível`, () => {
      const v = navVisibilityFromPermissions(user);
      expect(visiblePrimaryTabs(v)).toEqual([]);
      expect(poolOf(v)).toEqual([]);
      expect(buildMoreGroups(visibleMainNavItems(v)).flatMap((g) => g.items.map((i) => i.href))).toEqual(['/perfil']);
    });
  }

  it('sem o parâmetro de visibilidade o pool do Dashboard fica escondido', () => {
    const hrefs = buildEvolucaoGroups(mobileMainNavItems(admin)).flatMap((g) => g.items.map((i) => i.href));
    expect(hrefs.some((h) => h.startsWith('/dashboard'))).toBe(false);
  });

  it('cada aba segue o recurso do item correspondente', () => {
    const only = (key: string) =>
      visiblePrimaryTabs(navVisibilityFromPermissions({ permissions: { [key]: { ver: 'proprios' } }, intervals_configured: false })).map((t) => t.id);
    expect(only('dashboards')).toEqual(['inicio', 'evolucao']);
    expect(only('treino.modo_treino')).toEqual(['treino']);
    expect(only('treino.registro')).toEqual(['historico']);
    expect(only('ia.insights')).toEqual(['evolucao']);
  });
});

describe('header de 2º nível', () => {
  it('raízes das abas não têm voltar', () => {
    for (const p of ['/dashboard', '/dashboard/', '/modo-treino', '/registro-exercicios/']) {
      expect(backTargetFor(p), p).toBeNull();
    }
  });
  it('análises voltam para o hub Evolução', () => {
    for (const p of ['/dashboard/progressao/', '/dashboard/exercicios/5', '/insights', '/agente-cardio', '/intervals-icu']) {
      expect(backTargetFor(p), p).toEqual({ label: 'Evolução', href: EVOLUCAO_HUB_HREF });
    }
  });
  it('o voltar propaga o filtro global e preserva hub=evolucao', () => {
    expect(backTargetFor('/dashboard/progressao/', '?window=90d')).toEqual({
      label: 'Evolução',
      href: '/dashboard/?hub=evolucao&window=90d',
    });
    expect(evolucaoHubHref('')).toBe(EVOLUCAO_HUB_HREF);
  });
  it('detalhes voltam para a raiz da seção; o resto volta para Mais', () => {
    expect(backTargetFor('/registro-exercicios/9')).toEqual({ label: 'Histórico', href: '/registro-exercicios/' });
    expect(backTargetFor('/planos-treino/3/')).toEqual({ label: 'Planos de treino', href: '/planos-treino/' });
    expect(backTargetFor('/nutricao/plano')).toEqual({ label: 'Nutrição', href: '/nutricao/' });
    expect(backTargetFor('/perfil')).toEqual({ label: 'Mais', href: null });
    expect(backTargetFor('/exercicios')).toEqual({ label: 'Mais', href: null });
  });
});

describe('auxiliares', () => {
  it('isNavGroupItemActive casa por segmento', () => {
    expect(isNavGroupItemActive('/nutricao', '/nutricao/plano/')).toBe(true);
    expect(isNavGroupItemActive('/nutricao', '/nutricao-x')).toBe(false);
  });
  it('scrollMemoryKey só existe nas raízes das abas', () => {
    expect(scrollMemoryKey('/dashboard/', '')).toBe('inicio');
    expect(scrollMemoryKey('/dashboard/', 'hub=evolucao')).toBe('evolucao');
    expect(scrollMemoryKey('/registro-exercicios', '')).toBe('historico');
    expect(scrollMemoryKey('/perfil', '')).toBeNull();
  });
});

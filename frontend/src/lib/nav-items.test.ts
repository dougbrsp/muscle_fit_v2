import { describe, expect, it } from 'vitest';
import {
  buildMainNavItems,
  canSeeDashboard,
  desktopOnlySectionLabel,
  isDesktopOnlyPath,
  mobileMainNavItems,
  navVisibilityFromPermissions,
  visibleMainNavItems,
} from './nav-items';

// Concessões padrão copiadas de api/app/access/catalog.py (matriz dos grupos de sistema).
type Perms = Record<string, Record<string, string>>;
const ACTIONS = ['ver', 'editar', 'excluir'];
const ALL_RESOURCES = [
  'dashboards', 'treino.registro', 'treino.modo_treino', 'treino.planos', 'treino.preferencias',
  'treino.academias', 'progressao', 'volume.grupamentos', 'cardio', 'integracoes.intervals',
  'relatorios', 'exercicios.catalogo', 'corpo.indicadores', 'corpo.checkin', 'nutricao.diario',
  'nutricao.plano', 'nutricao.alimentos', 'nutricao.checkin', 'modelos.treino',
  'modelos.metas_ativacao', 'ia.modo_treino', 'ia.insights', 'ia.analise_treinos',
  'ia.geracao_plano', 'ia.cardio', 'ia.nutricao', 'ia.modelo_periodizacao', 'modelos.tipos_treino',
  'ia.cards_exercicio', 'conta.dispositivos', 'admin.usuarios', 'admin.grupos',
  'admin.regras_carga', 'admin.catalogos', 'admin.seguranca_treino',
];
const DADOS_PROPRIOS = [
  'dashboards', 'treino.registro', 'treino.modo_treino', 'treino.planos', 'treino.preferencias',
  'treino.academias', 'progressao', 'volume.grupamentos', 'corpo.indicadores', 'corpo.checkin',
  'cardio', 'integracoes.intervals', 'nutricao.diario', 'nutricao.plano', 'nutricao.alimentos',
  'nutricao.checkin', 'relatorios',
];
const IA_USUARIO = [
  'ia.modo_treino', 'ia.insights', 'ia.analise_treinos', 'ia.geracao_plano', 'ia.cardio', 'ia.nutricao',
];

function grant(perms: Perms, resources: string[], actions: string[], scope: string) {
  for (const r of resources) for (const a of actions) (perms[r] ??= {})[a] = scope;
}
function usuario(): Perms {
  const p: Perms = {};
  grant(p, [...DADOS_PROPRIOS, ...IA_USUARIO, 'conta.dispositivos'], ACTIONS, 'proprios');
  grant(p, ['exercicios.catalogo'], ['ver'], 'todos');
  grant(p, ['modelos.treino'], ['ver'], 'proprios');
  return p;
}
function personalTrainer(): Perms {
  const p = usuario();
  grant(
    p,
    [
      'modelos.treino', 'modelos.metas_ativacao', 'modelos.tipos_treino', 'ia.modelo_periodizacao',
      'nutricao.diario', 'nutricao.plano', 'nutricao.checkin', 'ia.nutricao',
    ],
    ACTIONS,
    'todos',
  );
  return p;
}
function admin(): Perms {
  const p: Perms = {};
  grant(p, ALL_RESOURCES, ACTIONS, 'todos');
  return p;
}
const vis = (perms: Perms | null | undefined, intervals = true, ok = true) =>
  navVisibilityFromPermissions(ok ? { permissions: perms, intervals_configured: intervals } : undefined);
const hrefs = (v: ReturnType<typeof vis>) => visibleMainNavItems(v).map((i) => i.href);

const ALL = vis(admin());

// Itens que cada papel via antes do RBAC (por role), na ordem do menu.
const COMUM = [
  '/registro-exercicios', '/modo-treino', '/agente-cardio', '/insights', '/indicadores-fisicos',
  '/nutricao', '/intervals-icu', '/exercicios', '/volume-grupamentos', '/planos-treino',
  '/academias', '/preferencias-treino',
];
const PT = [...COMUM, '/metas-ativacao', '/modelos-treino'];
const ADMIN = [
  ...COMUM, '/equipamentos-exercicios', '/dominio-exercicios', '/regras-carga',
  '/metas-ativacao', '/modelos-treino', '/usuarios', '/grupos-acessos',
];

describe('menu derivado das permissões (paridade com o menu por role)', () => {
  it('usuário comum: sem admin, sem metas e sem modelos', () => {
    expect(hrefs(vis(usuario()))).toEqual(COMUM);
    expect(canSeeDashboard(vis(usuario()))).toBe(true);
  });
  it('personal trainer: comum + metas e modelos', () => {
    expect(hrefs(vis(personalTrainer()))).toEqual(PT);
  });
  it('admin: tudo', () => {
    expect(hrefs(ALL)).toEqual(ADMIN);
    expect(canSeeDashboard(ALL)).toBe(true);
  });
  it('Intervals.icu exige também intervals_configured', () => {
    expect(hrefs(vis(usuario(), false))).not.toContain('/intervals-icu');
    expect(hrefs(vis(admin(), false))).not.toContain('/intervals-icu');
  });
  it('carregando, erro ou permissions null/{}: nenhum item e sem Dashboard', () => {
    for (const v of [vis(undefined, true, false), vis(null), vis(undefined), vis({}), vis(admin(), true, false)]) {
      expect(hrefs(v)).toEqual([]);
      expect(canSeeDashboard(v)).toBe(false);
    }
    expect(navVisibilityFromPermissions(undefined).hasIntervalsConfigured).toBe(false);
  });
  it('Modelos de treino exige editar com escopo todos (ver próprios não basta)', () => {
    const p = usuario();
    expect(hrefs(vis(p))).not.toContain('/modelos-treino');
    p['modelos.treino'].editar = 'alunos';
    expect(hrefs(vis(p))).not.toContain('/modelos-treino');
    p['modelos.treino'].editar = 'todos';
    expect(hrefs(vis(p))).toContain('/modelos-treino');
  });
});

describe('link admin Grupos e acessos', () => {
  it('aparece só para admin, ao lado de Usuários, e é só do computador', () => {
    const adminHrefs = visibleMainNavItems(ALL).map((i) => i.href);
    expect(adminHrefs).toContain('/grupos-acessos');
    expect(adminHrefs.indexOf('/grupos-acessos')).toBe(adminHrefs.indexOf('/usuarios') + 1);
    expect(hrefs(vis(usuario()))).not.toContain('/grupos-acessos');
    expect(isDesktopOnlyPath('/grupos-acessos')).toBe(true);
    expect(desktopOnlySectionLabel('/grupos-acessos')).toBe('Grupos e acessos');
    expect(mobileMainNavItems(ALL).map((i) => i.href)).not.toContain('/grupos-acessos');
  });
});

describe('desktopOnly (mobile v2, D1)', () => {
  it('marca exatamente as seis seções pedidas (mais Grupos e acessos)', () => {
    const marked = buildMainNavItems(ALL)
      .filter((i) => i.desktopOnly)
      .map((i) => i.href)
      .sort();
    expect(marked).toEqual(
      [
        '/academias',
        '/dominio-exercicios',
        '/equipamentos-exercicios',
        '/grupos-acessos',
        '/metas-ativacao',
        '/modelos-treino',
        '/regras-carga',
      ].sort(),
    );
  });

  it('não altera o menu desktop: visibleMainNavItems continua listando-as', () => {
    expect(visibleMainNavItems(ALL).filter((i) => i.desktopOnly)).toHaveLength(7);
    expect(mobileMainNavItems(ALL).filter((i) => i.desktopOnly)).toHaveLength(0);
  });

  it('nomeia a seção da rota (inclui [id], diagnóstico de volume e legado)', () => {
    expect(desktopOnlySectionLabel('/metas-ativacao/3/')).toBe('Metas de Ativação');
    expect(desktopOnlySectionLabel('/diagnostico-volume/8')).toBe('Modelos de treino');
    expect(desktopOnlySectionLabel('/tipos-treinamento')).toBe('Metas de Ativação');
    expect(desktopOnlySectionLabel('/academias-extra')).toBeNull();
    expect(isDesktopOnlyPath('/perfil')).toBe(false);
  });
});

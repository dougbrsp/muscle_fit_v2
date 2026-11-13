import { describe, expect, it } from 'vitest';
import {
  academiasControls,
  alimentosControls,
  catalogosControls,
  exerciseCardsControls,
  gruposControls,
  modelosTreinoControls,
  planoNutricionalControls,
  segurancaTreinoControls,
  tiposTreinoControls,
  usuariosControls,
} from './screen-permissions';

// Concessões padrão copiadas de api/app/access/catalog.py (iguais às de nav-items.test.ts).
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

const ALL_FNS = [
  academiasControls, catalogosControls, segurancaTreinoControls, exerciseCardsControls,
  tiposTreinoControls, modelosTreinoControls, usuariosControls, gruposControls,
  alimentosControls, planoNutricionalControls,
] as const;
const allTrue = (o: Record<string, boolean>) => Object.values(o).every((v) => v === true);
const allFalse = (o: Record<string, boolean>) => Object.values(o).every((v) => v === false);

describe('controles das telas de gestão (paridade com o que cada papel via por role)', () => {
  it('usuário comum: só academias próprias; nada de catálogo, modelos, usuários, grupos', () => {
    const p = usuario();
    expect(academiasControls(p)).toEqual({
      canEdit: true, canDelete: true, canEditAdminFields: false, canRemoveLogo: false,
    });
    expect(allFalse(catalogosControls(p))).toBe(true);
    expect(allFalse(segurancaTreinoControls(p))).toBe(true);
    expect(allFalse(exerciseCardsControls(p))).toBe(true);
    expect(allFalse(tiposTreinoControls(p))).toBe(true);
    expect(allFalse(modelosTreinoControls(p))).toBe(true);
    expect(allFalse(usuariosControls(p))).toBe(true);
    expect(allFalse(gruposControls(p))).toBe(true);
    expect(allFalse(alimentosControls(p))).toBe(true);
    expect(allFalse(planoNutricionalControls(p))).toBe(true);
  });

  it('personal trainer: metas, modelos e plano de outros; sem catálogo, usuários e grupos', () => {
    const p = personalTrainer();
    expect(academiasControls(p).canEditAdminFields).toBe(false);
    expect(allFalse(catalogosControls(p))).toBe(true);
    expect(allFalse(segurancaTreinoControls(p))).toBe(true);
    expect(allFalse(exerciseCardsControls(p))).toBe(true);
    expect(allTrue(tiposTreinoControls(p))).toBe(true);
    expect(allTrue(modelosTreinoControls(p))).toBe(true);
    expect(allFalse(usuariosControls(p))).toBe(true);
    expect(allFalse(gruposControls(p))).toBe(true);
    expect(allFalse(alimentosControls(p))).toBe(true);
    expect(planoNutricionalControls(p).canManageOthers).toBe(true);
  });

  it('admin: todos os controles', () => {
    const p = admin();
    for (const fn of ALL_FNS) expect(allTrue(fn(p))).toBe(true);
  });

  it('null, undefined e {}: nenhum controle (fail-closed)', () => {
    for (const p of [null, undefined, {}]) {
      for (const fn of ALL_FNS) expect(allFalse(fn(p))).toBe(true);
    }
  });
});

describe('escopo e ação exatos', () => {
  it('modelos de treino exige escopo todos: próprios ou alunos não bastam', () => {
    for (const scope of ['proprios', 'alunos']) {
      const p: Perms = { 'modelos.treino': { ver: scope, editar: scope, excluir: scope } };
      expect(allFalse(modelosTreinoControls(p))).toBe(true);
    }
  });

  it('plano de outros exige editar com escopo todos', () => {
    expect(planoNutricionalControls({ 'nutricao.plano': { editar: 'alunos' } }).canManageOthers).toBe(false);
    expect(planoNutricionalControls({ 'nutricao.plano': { ver: 'todos' } }).canManageOthers).toBe(false);
    expect(planoNutricionalControls({ 'nutricao.plano': { editar: 'todos' } }).canManageOthers).toBe(true);
  });

  it('ver sem editar/excluir não libera escrita (catálogo, usuários, grupos, tipos)', () => {
    const ver = (r: string): Perms => ({ [r]: { ver: 'todos' } });
    expect(catalogosControls(ver('admin.catalogos'))).toEqual({ canEdit: false, canDelete: false });
    expect(usuariosControls(ver('admin.usuarios'))).toEqual({ canAccess: true, canEdit: false, canDelete: false });
    expect(gruposControls(ver('admin.grupos'))).toMatchObject({ canAccess: true, canEdit: false, canDelete: false });
    expect(tiposTreinoControls(ver('modelos.tipos_treino'))).toEqual({ canAccess: true, canEdit: false, canDelete: false });
    expect(segurancaTreinoControls(ver('admin.seguranca_treino'))).toEqual({ canView: true, canEdit: false });
    expect(alimentosControls(ver('admin.catalogos')).canListPrivate).toBe(true);
  });

  it('grupos sem admin.usuarios: lista de usuários indisponível, edição de grupo intacta', () => {
    const p: Perms = { 'admin.grupos': { ver: 'todos', editar: 'todos', excluir: 'todos' } };
    expect(gruposControls(p)).toEqual({ canAccess: true, canEdit: true, canDelete: true, canListUsers: false });
  });
});

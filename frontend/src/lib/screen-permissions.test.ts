import { describe, expect, it } from 'vitest';
import {
  academiasControls,
  alimentosControls,
  catalogosControls,
  exerciseCardsControls,
  gruposControls,
  iaControls,
  modeloTreinoDefaultVisibility,
  modeloTreinoItemControls,
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
  'treino.academias', 'treino.academias.equipamentos', 'progressao', 'volume.grupamentos', 'cardio', 'integracoes.intervals',
  'relatorios', 'exercicios.catalogo', 'corpo.indicadores', 'corpo.checkin', 'nutricao.diario',
  'nutricao.plano', 'nutricao.alimentos', 'nutricao.checkin', 'modelos.treino',
  'modelos.metas_ativacao', 'ia.modo_treino', 'ia.insights', 'ia.analise_treinos',
  'ia.geracao_plano', 'ia.cardio', 'ia.nutricao', 'ia.modelo_periodizacao', 'modelos.tipos_treino',
  'ia.cards_exercicio', 'conta.dispositivos', 'admin.usuarios', 'admin.grupos',
  'admin.regras_carga', 'admin.catalogos', 'admin.seguranca_treino',
];
const DADOS_PROPRIOS = [
  'dashboards', 'treino.registro', 'treino.modo_treino', 'treino.planos', 'treino.preferencias',
  'treino.academias', 'treino.academias.equipamentos', 'progressao', 'volume.grupamentos', 'corpo.indicadores', 'corpo.checkin',
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
      'nutricao.diario', 'nutricao.checkin', 'ia.nutricao',
    ],
    ACTIONS,
    'todos',
  );
  grant(p, ['nutricao.plano'], ACTIONS, 'alunos');
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

describe('gatilhos de IA (lote 4B-2)', () => {
  it('usuario, personal e admin têm todos os gatilhos (igual a hoje)', () => {
    for (const p of [usuario(), personalTrainer(), admin()]) {
      expect(iaControls(p)).toEqual({
        canInsights: true,
        canAnaliseTreinos: true,
        canGerarPlano: true,
        canCardio: true,
        canNutricao: true,
        canModoTreino: true,
      });
    }
  });

  it('sem permissões (null/undefined/{}) nenhum gatilho aparece', () => {
    for (const p of [null, undefined, {}] as const) expect(allFalse(iaControls(p))).toBe(true);
  });

  it('cada gatilho depende só do seu recurso e exige editar', () => {
    const so = (resource: string, action = 'editar'): Perms => ({ [resource]: { [action]: 'proprios' } });
    expect(iaControls(so('ia.insights'))).toMatchObject({ canInsights: true, canCardio: false, canNutricao: false });
    expect(iaControls(so('ia.cardio')).canCardio).toBe(true);
    expect(iaControls(so('ia.nutricao')).canNutricao).toBe(true);
    expect(iaControls(so('ia.modo_treino')).canModoTreino).toBe(true);
    expect(iaControls(so('ia.geracao_plano')).canGerarPlano).toBe(true);
    expect(iaControls(so('ia.analise_treinos')).canAnaliseTreinos).toBe(true);
    expect(allFalse(iaControls(so('ia.insights', 'ver')))).toBe(true);
  });
});

describe('controles das telas de gestão (paridade com o que cada papel via por role)', () => {
  it('usuário comum: só academias próprias; nada de catálogo, modelos, usuários, grupos', () => {
    const p = usuario();
    expect(academiasControls(p)).toEqual({
      canEdit: true, canAddEquipment: true, canDelete: true, canEditAdminFields: false, canRemoveLogo: false,
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

  it('acrescentar equipamento e editar academia são permissões independentes', () => {
    const so = (resource: string, action: string): Perms => ({ [resource]: { [action]: 'proprios' } });
    expect(academiasControls(so('treino.academias.equipamentos', 'editar'))).toMatchObject({ canAddEquipment: true, canEdit: false });
    expect(academiasControls(so('treino.academias', 'editar'))).toMatchObject({ canAddEquipment: false, canEdit: true });
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
    expect(planoNutricionalControls(p)).toEqual({ canManageOthers: true, studentsOnly: true });
  });

  it('admin: todos os controles (plano: gere todos, sem restrição a alunos)', () => {
    const p = admin();
    for (const fn of ALL_FNS) {
      if (fn === planoNutricionalControls) continue;
      expect(allTrue(fn(p))).toBe(true);
    }
    expect(planoNutricionalControls(p)).toEqual({ canManageOthers: true, studentsOnly: false });
  });

  it('null, undefined e {}: nenhum controle (fail-closed)', () => {
    for (const p of [null, undefined, {}]) {
      for (const fn of ALL_FNS) expect(allFalse(fn(p))).toBe(true);
    }
  });
});

describe('escopo e ação exatos', () => {
  it('modelos de treino exige escopo alunos: próprios não basta; gerar rascunho segue exigindo todos', () => {
    const grantAll = (scope: string): Perms => ({ 'modelos.treino': { ver: scope, editar: scope, excluir: scope } });
    expect(allFalse(modelosTreinoControls(grantAll('proprios')))).toBe(true);
    expect(modelosTreinoControls(grantAll('alunos'))).toEqual({
      canAccess: true, canView: true, canEdit: true, canDelete: true, canGenerateDraft: false,
    });
  });

  it('por Modelo: admin escreve em qualquer um; PT (alunos) só nos próprios; sem permissão/id, nada', () => {
    const pt: Perms = { 'modelos.treino': { ver: 'alunos', editar: 'alunos', excluir: 'alunos' } };
    const own = { created_by_user_id: 7, visibility: 'privado' };
    const other = { created_by_user_id: 9, visibility: 'publico' };
    const allow = { isOwner: true, canWrite: true, canDelete: true, canDuplicate: true, canAssign: true, canEndAssignment: true };
    expect(modeloTreinoItemControls(admin(), 1, other)).toEqual({ ...allow, isOwner: false });
    expect(modeloTreinoItemControls(pt, 7, own)).toEqual(allow);
    expect(modeloTreinoItemControls(pt, 7, other)).toEqual({
      ...allow, isOwner: false, canWrite: false, canDelete: false,
    });
    expect(modeloTreinoItemControls(usuario(), 7, own)).toMatchObject({
      canWrite: false, canDelete: false, canDuplicate: false, canAssign: false, canEndAssignment: false,
    });
    for (const perms of [null, undefined, {}]) {
      expect(Object.values(modeloTreinoItemControls(perms, 7, own)).every((v) => v === false)).toBe(true);
    }
    expect(Object.values(modeloTreinoItemControls(admin(), undefined, own)).every((v) => v === false)).toBe(true);
  });

  it('visibilidade padrão do Modelo novo: todos = publico; alunos e sem permissão = privado', () => {
    const pt: Perms = { 'modelos.treino': { ver: 'alunos', editar: 'alunos', excluir: 'alunos' } };
    expect(modeloTreinoDefaultVisibility(admin())).toBe('publico');
    expect(modeloTreinoDefaultVisibility(pt)).toBe('privado');
    for (const perms of [null, undefined, {}]) expect(modeloTreinoDefaultVisibility(perms)).toBe('privado');
  });

  it('plano de outros exige editar com escopo todos', () => {
    expect(planoNutricionalControls({ 'nutricao.plano': { editar: 'alunos' } })).toEqual({ canManageOthers: true, studentsOnly: true });
    expect(planoNutricionalControls({ 'nutricao.plano': { editar: 'proprios' } })).toEqual({ canManageOthers: false, studentsOnly: false });
    expect(planoNutricionalControls({ 'nutricao.plano': { ver: 'todos' } })).toEqual({ canManageOthers: false, studentsOnly: false });
    expect(planoNutricionalControls({ 'nutricao.plano': { editar: 'todos' } })).toEqual({ canManageOthers: true, studentsOnly: false });
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

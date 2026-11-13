// Controles das telas de gestão decididos por permissões efetivas (RBAC, ADR 0011,
// lote 4B-1). Cada função é PURA: recebe `permissions` de `GET /auth/me` e devolve
// quais controles a tela mostra. A condição espelha o `require_permission(recurso,
// ação, min_scope)` da rota que o controle chama; o backend segue sendo a barreira
// real (403). Nenhuma regra de acesso nasce aqui: sem permissões (null/undefined/{}),
// nenhum controle aparece (fail-closed).

import { useQuery } from '@tanstack/react-query';
import { can, type Permissions } from './permissions';
import { fetchCurrentUser } from './use-is-admin';

type Perms = Permissions | null | undefined;

/** Academias (`/gyms*`, `/gym-equipment-catalog`). */
export function academiasControls(perms: Perms) {
  return {
    /** `POST /gyms` e `PUT /gyms/{id}` (dias, avulsa) e vínculo de equipamento: treino.academias/editar. */
    canEdit: can(perms, 'treino.academias', 'editar'),
    /** `DELETE /gyms/{id}` e `DELETE /gym-equipment-catalog/{id}`: treino.academias/excluir. */
    canDelete: can(perms, 'treino.academias', 'excluir'),
    /** Nome/horário (`PUT /gyms/{id}` com campos admin) e envio de logo: admin.catalogos/editar. */
    canEditAdminFields: can(perms, 'admin.catalogos', 'editar'),
    /** `DELETE /gyms/{id}/logo`: admin.catalogos/excluir. */
    canRemoveLogo: can(perms, 'admin.catalogos', 'excluir'),
  };
}

/** Catálogo de exercícios/equipamentos/grupos musculares (Domínio de Exercícios, Equipamentos x Exercícios). */
export function catalogosControls(perms: Perms) {
  return {
    /** POST/PUT das rotas de catálogo: admin.catalogos/editar. */
    canEdit: can(perms, 'admin.catalogos', 'editar'),
    /** DELETE das rotas de catálogo: admin.catalogos/excluir. */
    canDelete: can(perms, 'admin.catalogos', 'excluir'),
  };
}

/** Aba Segurança de Equipamentos x Exercícios (`training_safety_admin.py`). */
export function segurancaTreinoControls(perms: Perms) {
  return {
    canView: can(perms, 'admin.seguranca_treino', 'ver'),
    canEdit: can(perms, 'admin.seguranca_treino', 'editar'),
  };
}

/** Card ilustrado (`/exercises/{id}/card-generations*`, `card-references*`). */
export function exerciseCardsControls(perms: Perms) {
  return {
    /** Gerar, aprovar, rejeitar, regenerar, referências: ia.cards_exercicio/editar. */
    canGenerate: can(perms, 'ia.cards_exercicio', 'editar'),
  };
}

/**
 * Gatilhos de IA (lote 4B-2). Cada flag espelha o `require_permission` do POST que
 * dispara a geração (todos `ia.*`/editar, sem min_scope). Leituras de conteúdo já
 * gerado não exigem `ia.*` (ex.: `GET /cardio-recommendations` é `cardio/ver`),
 * então só o gatilho some, não o histórico.
 */
export function iaControls(perms: Perms) {
  return {
    /** `POST /insights/generate`: ia.insights/editar. */
    canInsights: can(perms, 'ia.insights', 'editar'),
    /** `POST /workouts-analysis/generate`: ia.analise_treinos/editar. */
    canAnaliseTreinos: can(perms, 'ia.analise_treinos', 'editar'),
    /** `POST /workout-plans/generate`: ia.geracao_plano/editar. */
    canGerarPlano: can(perms, 'ia.geracao_plano', 'editar'),
    /** `POST /cardio-recommendations/generate`: ia.cardio/editar. */
    canCardio: can(perms, 'ia.cardio', 'editar'),
    /** `POST /nutrition/meals/parse` e `/photo`: ia.nutricao/editar. */
    canNutricao: can(perms, 'ia.nutricao', 'editar'),
    /** `POST /activities/{id}/ai-summary/generate`: ia.modo_treino/editar. */
    canModoTreino: can(perms, 'ia.modo_treino', 'editar'),
  };
}

/** Hook dos gatilhos de IA: reaproveita a query `['current-user']` (sem requisição extra); sem dados, tudo `false`. */
export function useIaControls() {
  const query = useQuery({ queryKey: ['current-user'], queryFn: fetchCurrentUser, staleTime: 60_000, retry: false });
  return iaControls(query.data?.permissions);
}

/** Tipos de treino / Metas de Ativação (`/training-types*`). */
export function tiposTreinoControls(perms: Perms) {
  return {
    /** `GET /training-types`: modelos.tipos_treino/ver. */
    canAccess: can(perms, 'modelos.tipos_treino', 'ver'),
    /** POST, PUT, PATCH status, duplicar: modelos.tipos_treino/editar. */
    canEdit: can(perms, 'modelos.tipos_treino', 'editar'),
    /** DELETE: modelos.tipos_treino/excluir. */
    canDelete: can(perms, 'modelos.tipos_treino', 'excluir'),
  };
}

/** Lista de Modelos de treino (`/workout-plan-templates*`, gestão = escopo todos). */
export function modelosTreinoControls(perms: Perms) {
  return {
    /** Gerir modelos (mesmo critério do menu): modelos.treino/editar com escopo todos. */
    canAccess: can(perms, 'modelos.treino', 'editar', 'todos'),
    canView: can(perms, 'modelos.treino', 'ver', 'todos'),
    canEdit: can(perms, 'modelos.treino', 'editar', 'todos'),
    canDelete: can(perms, 'modelos.treino', 'excluir', 'todos'),
    /** `POST /workout-plan-templates/periodization-drafts`: ia.modelo_periodizacao/editar com escopo todos. */
    canGenerateDraft: can(perms, 'ia.modelo_periodizacao', 'editar', 'todos'),
  };
}

/** Usuários (`/users*`). */
export function usuariosControls(perms: Perms) {
  return {
    /** `GET /users`: admin.usuarios/ver. */
    canAccess: can(perms, 'admin.usuarios', 'ver'),
    /** POST, PUT, reset de senha: admin.usuarios/editar. */
    canEdit: can(perms, 'admin.usuarios', 'editar'),
    /** DELETE (desativar): admin.usuarios/excluir. */
    canDelete: can(perms, 'admin.usuarios', 'excluir'),
  };
}

/** Grupos e acessos (`/access/*`). */
export function gruposControls(perms: Perms) {
  return {
    /** Catálogo, grupos, membros, auditoria (GET): admin.grupos/ver. */
    canAccess: can(perms, 'admin.grupos', 'ver'),
    /** Criar/editar grupo, salvar matriz, adicionar membro: admin.grupos/editar. */
    canEdit: can(perms, 'admin.grupos', 'editar'),
    /** Excluir grupo, remover membro: admin.grupos/excluir. */
    canDelete: can(perms, 'admin.grupos', 'excluir'),
    /** A lista de usuários (opções de membro, nomes na auditoria) vem de `GET /users`: admin.usuarios/ver. */
    canListUsers: can(perms, 'admin.usuarios', 'ver'),
  };
}

/** Alimentos (`/nutrition/foods*`): "administra o catálogo público" = admin.catalogos. */
export function alimentosControls(perms: Perms) {
  return {
    /** `scope=private_all` (privados de todos, promover): admin.catalogos/ver (`can_list_private_foods`). */
    canListPrivate: can(perms, 'admin.catalogos', 'ver'),
  };
}

/**
 * Plano nutricional (`/nutrition/plans*`): gerir o plano de outros = nutricao.plano/editar
 * com escopo alunos (que cobre todos). `studentsOnly`: o escopo não chega a todos, então o
 * seletor lista só os alunos vinculados (o backend decide o acesso de verdade).
 */
export function planoNutricionalControls(perms: Perms) {
  const canManageOthers = can(perms, 'nutricao.plano', 'editar', 'alunos');
  return {
    canManageOthers,
    studentsOnly: canManageOthers && !can(perms, 'nutricao.plano', 'editar', 'todos'),
  };
}

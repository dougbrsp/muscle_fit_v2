// Guarda de rota por permissão das telas administrativas ("Regras de carga",
// "Equipamentos x Exercícios", "Domínio de Exercícios"). O backend continua
// sendo a garantia real; a guarda só tira a tela de quem não tem a permissão e
// manda para "Preferências de Treino", onde a nota é dada.

import { can, type Permissions, type Scope } from './permissions';

export const ADMIN_ROUTE_FALLBACK = '/preferencias-treino';

export type AdminRouteState = 'loading' | 'allow' | 'redirect';

export type RoutePermission = { resource: string; action?: string; minScope?: Scope };

// Decide pela PRESENÇA de dados (`hasData`), não por isSuccess: um refetch que
// falha mantém `data` em cache e a guarda segue decidindo por `can()`. Sem dados:
// erro redireciona (sem loader eterno); senão loader (nada vaza).
// `permissions` nulo/ausente (backend de acesso indisponível) = sem acesso.
export function permissionRouteState(input: {
  hasData: boolean;
  isError?: boolean;
  permissions: Permissions | null | undefined;
  required: RoutePermission;
}): AdminRouteState {
  if (!input.hasData) return input.isError ? 'redirect' : 'loading';
  const { resource, action = 'ver', minScope } = input.required;
  return can(input.permissions, resource, action, minScope) ? 'allow' : 'redirect';
}

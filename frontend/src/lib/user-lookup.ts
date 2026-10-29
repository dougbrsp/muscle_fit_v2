// Busca de usuários para quem administra alunos (admin/PT): `GET
// /workout-plan-templates/users-lookup` (lookup mínimo; evita expor `GET /users`,
// que é admin-only, a personal trainers). Nasceu no modal de atribuição de
// Modelos e é reaproveitada pelo seletor de aluno do plano nutricional (L2b):
// mesma rota, mesma chave do React Query, mesmo rótulo.

import { apiFetch } from './api';
import type { ApiListResponse, UserLookupItem } from './workout-plan-templates-types';

export function userLookupKey(search: string) {
  return ['workout-plan-template-users-lookup', search] as const;
}

export function fetchUserLookup(search: string): Promise<ApiListResponse<UserLookupItem>> {
  return apiFetch<ApiListResponse<UserLookupItem>>(
    `/workout-plan-templates/users-lookup?search=${encodeURIComponent(search)}`,
  );
}

/** "Nome (usuário)" ou só o usuário. */
export function userLookupLabel(user: Pick<UserLookupItem, 'username' | 'full_name'>): string {
  return user.full_name ? `${user.full_name} (${user.username})` : user.username;
}

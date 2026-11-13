import { apiFetch, ApiError } from './api';
import type {
  AccessAuditEntry,
  AccessCatalog,
  AccessGrant,
  AccessGroup,
  AccessMember,
  AddMemberResponse,
  CreateGroupPayload,
  CreateTrainerLinkPayload,
  TrainerLink,
  TrainerLinkFilters,
  UpdateGroupPayload,
} from './access-admin-types';

export const accessKeys = {
  all: ['access-admin'] as const,
  catalog: ['access-admin', 'catalog'] as const,
  groups: ['access-admin', 'groups'] as const,
  members: (groupId: number) => ['access-admin', 'members', groupId] as const,
  audit: (groupId: number) => ['access-admin', 'audit', groupId] as const,
  links: (status: string | null = null) => ['access-admin', 'links', status] as const,
  linksAll: ['access-admin', 'links'] as const,
};

const json = (method: string, body?: unknown): RequestInit =>
  body === undefined ? { method } : { method, body: JSON.stringify(body) };

export const fetchAccessCatalog = () => apiFetch<AccessCatalog>('/access/catalog');
export const fetchAccessGroups = () => apiFetch<AccessGroup[]>('/access/groups');
export const createAccessGroup = (payload: CreateGroupPayload) =>
  apiFetch<AccessGroup>('/access/groups', json('POST', payload));
export const updateAccessGroup = (id: number, payload: UpdateGroupPayload) =>
  apiFetch<AccessGroup>(`/access/groups/${id}`, json('PUT', payload));
export const replaceAccessGrants = (id: number, grants: AccessGrant[]) =>
  apiFetch<AccessGroup>(`/access/groups/${id}/grants`, json('PUT', { grants }));
export const deleteAccessGroup = (id: number) => apiFetch<void>(`/access/groups/${id}`, json('DELETE'));
export const fetchGroupMembers = (id: number) => apiFetch<AccessMember[]>(`/access/groups/${id}/members`);
export const addGroupMember = (id: number, userId: number) =>
  apiFetch<AddMemberResponse>(`/access/groups/${id}/members`, json('POST', { user_id: userId }));
export const removeGroupMember = (id: number, userId: number) =>
  apiFetch<unknown>(`/access/groups/${id}/members/${userId}`, json('DELETE'));
export const fetchGroupAudit = (groupId: number, limit = 50, offset = 0) =>
  apiFetch<AccessAuditEntry[]>(`/access/audit?group_id=${groupId}&limit=${limit}&offset=${offset}`);

export function trainerLinksPath(filters: TrainerLinkFilters = {}): string {
  return filters.status ? `/access/links?status=${encodeURIComponent(filters.status)}` : '/access/links';
}
export const fetchTrainerLinks = (filters: TrainerLinkFilters = {}) =>
  apiFetch<TrainerLink[]>(trainerLinksPath(filters));
export const createTrainerLink = (payload: CreateTrainerLinkPayload) =>
  apiFetch<TrainerLink>('/access/links', json('POST', payload));
export const endTrainerLink = (id: number) => apiFetch<TrainerLink>(`/access/links/${id}`, json('DELETE'));

/**
 * Mensagem de erro para a tela: o backend manda `{error, message}` (em PT-BR)
 * no corpo de topo; o `apiFetch` o guarda em `ApiError.detail`.
 */
export function accessErrorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  const detail = error.detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === 'string' && message) return message;
  }
  // Detalhe estruturado (ex.: 422 do FastAPI com lista) não traz texto do backend: usa o fallback em PT-BR.
  if (detail && typeof detail === 'object') return fallback;
  return error.message || fallback;
}

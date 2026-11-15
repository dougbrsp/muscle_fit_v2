// Funções puras da aba "Vínculos" (treinador–aluno, RBAC lote 5B, ADR 0011). Só
// apresentação e filtro de candidatos: quem pode criar/encerrar e todas as regras do
// vínculo (treinador precisa ser personal_trainer, ator não é parte, par já ativo) são
// do backend (`api/app/access/admin.py`), que devolve a mensagem exibida na tela.

import type { AccessMember, TrainerLink, TrainerLinkStatus } from './access-admin-types';

/** Chave do grupo de sistema cujos membros podem ser treinadores (`catalog.GROUP_PERSONAL_TRAINER`). */
export const PERSONAL_TRAINER_GROUP_KEY = 'personal_trainer';

export type LinkStatusFilter = TrainerLinkStatus | 'todos';

export const LINK_STATUS_FILTER_OPTIONS: { value: LinkStatusFilter; label: string }[] = [
  { value: 'ativo', label: 'Ativos' },
  { value: 'encerrado', label: 'Encerrados' },
  { value: 'todos', label: 'Todos' },
];

export function isLinkStatusFilter(value: string | null | undefined): value is LinkStatusFilter {
  return LINK_STATUS_FILTER_OPTIONS.some((option) => option.value === value);
}

/** Filtro da tela -> parâmetro `status` da API (`todos` = sem filtro). */
export function linkStatusParam(filter: LinkStatusFilter): TrainerLinkStatus | null {
  return filter === 'todos' ? null : filter;
}

export function linkStatusLabel(status: string): string {
  if (status === 'ativo') return 'Ativo';
  if (status === 'encerrado') return 'Encerrado';
  return status;
}

export function linkStatusColor(status: string): string {
  return status === 'ativo' ? 'green' : 'gray';
}

/** "Nome (usuário)" ou só o usuário. */
export function personLabel(username: string, fullName: string | null | undefined): string {
  return fullName ? `${fullName} (${username})` : username;
}

type Candidate = { id: number; username: string; full_name: string | null; is_active: boolean };
type Option = { value: string; label: string };

/** Treinadores elegíveis: membros ativos do grupo personal_trainer, exceto o próprio admin. */
export function trainerOptions(members: ReadonlyArray<AccessMember>, currentUserId: number | null): Option[] {
  return members
    .filter((m) => m.is_active && m.user_id !== currentUserId)
    .map((m) => ({ value: String(m.user_id), label: personLabel(m.username, m.full_name) }));
}

/**
 * Alunos elegíveis: usuários ativos, exceto o próprio admin, o treinador escolhido e quem já
 * tem vínculo ativo com esse treinador. O backend repete as checagens (409/422).
 */
export function studentOptions(
  users: ReadonlyArray<Candidate>,
  links: ReadonlyArray<Pick<TrainerLink, 'trainer_user_id' | 'student_user_id' | 'status'>>,
  currentUserId: number | null,
  trainerUserId: number | null,
): Option[] {
  const already = new Set(
    links.filter((l) => l.status === 'ativo' && l.trainer_user_id === trainerUserId).map((l) => l.student_user_id),
  );
  return users
    .filter((u) => u.is_active && u.id !== currentUserId && u.id !== trainerUserId && !already.has(u.id))
    .map((u) => ({ value: String(u.id), label: personLabel(u.username, u.full_name) }));
}

/** Admin não pode encerrar vínculo em que é parte (o backend recusa com 409); some o botão. */
export function canEndLink(link: Pick<TrainerLink, 'status' | 'trainer_user_id' | 'student_user_id'>, currentUserId: number | null): boolean {
  return link.status === 'ativo' && link.trainer_user_id !== currentUserId && link.student_user_id !== currentUserId;
}

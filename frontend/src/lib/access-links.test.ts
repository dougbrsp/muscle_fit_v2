import { describe, expect, it } from 'vitest';
import type { AccessMember } from './access-admin-types';
import {
  canEndLink,
  isLinkStatusFilter,
  linkStatusColor,
  linkStatusLabel,
  linkStatusParam,
  personLabel,
  studentOptions,
  trainerOptions,
} from './access-links';

const member = (user_id: number, is_active = true, full_name: string | null = null): AccessMember => ({
  user_id, username: `u${user_id}`, full_name, role: 'regular', is_active, created_at: null, created_by_user_id: null,
});
const user = (id: number, is_active = true, full_name: string | null = null) => ({
  id, username: `u${id}`, full_name, is_active,
});

describe('access-links', () => {
  it('filtro de status vira parâmetro da API (todos = sem filtro)', () => {
    expect(linkStatusParam('todos')).toBeNull();
    expect(linkStatusParam('ativo')).toBe('ativo');
    expect(linkStatusParam('encerrado')).toBe('encerrado');
    expect(isLinkStatusFilter('todos')).toBe(true);
    expect(isLinkStatusFilter('x')).toBe(false);
    expect(isLinkStatusFilter(null)).toBe(false);
  });

  it('rótulos e cores de status', () => {
    expect(linkStatusLabel('ativo')).toBe('Ativo');
    expect(linkStatusLabel('encerrado')).toBe('Encerrado');
    expect(linkStatusLabel('outro')).toBe('outro');
    expect(linkStatusColor('ativo')).toBe('green');
    expect(linkStatusColor('encerrado')).toBe('gray');
  });

  it('personLabel com e sem nome completo', () => {
    expect(personLabel('ana', 'Ana Souza')).toBe('Ana Souza (ana)');
    expect(personLabel('ana', null)).toBe('ana');
  });

  it('treinadores: só membros ativos e nunca o próprio admin', () => {
    const opts = trainerOptions([member(1), member(2, false), member(3, true, 'Caio')], 1);
    expect(opts).toEqual([{ value: '3', label: 'Caio (u3)' }]);
  });

  it('alunos: exclui inativos, o admin, o treinador e quem já tem vínculo ativo com ele', () => {
    const users = [user(1), user(2), user(3, false), user(4), user(5), user(6)];
    const links = [
      { trainer_user_id: 2, student_user_id: 4, status: 'ativo' as const },
      { trainer_user_id: 2, student_user_id: 5, status: 'encerrado' as const },
      { trainer_user_id: 9, student_user_id: 6, status: 'ativo' as const },
    ];
    const opts = studentOptions(users, links, 1, 2).map((o) => o.value);
    expect(opts).toEqual(['5', '6']);
    // sem treinador escolhido: nada é excluído por vínculo
    expect(studentOptions(users, links, 1, null).map((o) => o.value)).toEqual(['2', '4', '5', '6']);
  });

  it('encerrar: só vínculo ativo em que o admin não é parte', () => {
    expect(canEndLink({ status: 'ativo', trainer_user_id: 2, student_user_id: 3 }, 1)).toBe(true);
    expect(canEndLink({ status: 'encerrado', trainer_user_id: 2, student_user_id: 3 }, 1)).toBe(false);
    expect(canEndLink({ status: 'ativo', trainer_user_id: 1, student_user_id: 3 }, 1)).toBe(false);
    expect(canEndLink({ status: 'ativo', trainer_user_id: 2, student_user_id: 1 }, 1)).toBe(false);
  });
});

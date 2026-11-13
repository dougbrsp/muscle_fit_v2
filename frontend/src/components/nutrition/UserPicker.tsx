'use client';

import { Select, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { fetchMyStudents, fetchUserLookup, MY_STUDENTS_KEY, userLookupKey, userLookupLabel } from '@/lib/user-lookup';
import type { UserLookupItem } from '@/lib/workout-plan-templates-types';

type Option = { value: string; label: string };

type Props = {
  value: number | null;
  onChange: (userId: number | null) => void;
  /** Usuário já escolhido na abertura (o rótulo aparece sem esperar a busca). */
  initial?: { id: number; label: string };
  /** `all` (padrão): busca em todos os usuários; `students`: só os alunos vinculados ao usuário logado. */
  source?: 'all' | 'students';
};

/** Busca por nome/usuário, sem acento e sem caixa, sobre a lista dos vinculados. */
export function filterStudents(students: ReadonlyArray<UserLookupItem>, term: string): UserLookupItem[] {
  const norm = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  const needle = norm(term.trim());
  if (!needle) return [...students];
  return students.filter((s) => norm(`${s.full_name ?? ''} ${s.username}`).includes(needle));
}

/**
 * Seletor de aluno do plano nutricional (admin/PT). Mesma busca e mesmo rótulo
 * da atribuição de Modelos (`lib/user-lookup.ts`): o PT segue sem vínculo
 * formal e enxerga qualquer aluno ativo (decisão D-L; a regra de acesso de
 * verdade é do backend, `nutrition_access.py`).
 *
 * Depois de escolher, o texto da caixa vira o rótulo do aluno; esse texto não
 * vai para a busca (senão a lista se esvaziaria) e a opção escolhida fica
 * guardada para continuar visível.
 */
export function UserPicker({ value, onChange, initial, source = 'all' }: Props) {
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Option | null>(initial ? { value: String(initial.id), label: initial.label } : null);
  const termo = selected && search === selected.label ? '' : search;

  const studentsMode = source === 'students';

  const usersQuery = useQuery({
    queryKey: userLookupKey(termo),
    queryFn: () => fetchUserLookup(termo),
    enabled: !studentsMode,
  });
  // Modo alunos: a API devolve a lista inteira dos vinculados; a busca filtra no cliente.
  const studentsQuery = useQuery({
    queryKey: MY_STUDENTS_KEY,
    queryFn: fetchMyStudents,
    enabled: studentsMode,
    retry: false,
  });
  const activeQuery = studentsMode ? studentsQuery : usersQuery;
  const items = studentsMode ? filterStudents(studentsQuery.data ?? [], termo) : usersQuery.data?.items || [];
  const options: Option[] = items.map((user) => ({
    value: String(user.id),
    label: userLookupLabel(user),
  }));
  const noStudents = studentsMode && studentsQuery.isSuccess && (studentsQuery.data ?? []).length === 0;
  // A opção escolhida é SEMPRE a guardada (rótulo congelado no momento da escolha), nunca a da
  // busca: o Select copia o rótulo da opção selecionada para a caixa de texto, e se esse rótulo
  // mudasse com o resultado da busca (ex.: "Doug" inicial x "Doug (doug)" do lookup) a caixa e a
  // busca se realimentariam sem fim (React #185).
  // Mantém a ordem da API: o escolhido substitui a opção no mesmo lugar; só vai ao início se a busca não o trouxe.
  if (selected) {
    const posicao = options.findIndex((option) => option.value === selected.value);
    if (posicao >= 0) options[posicao] = selected;
    else options.unshift(selected);
  }

  return (
    <Stack gap={6}>
    <Select
      label="Aluno"
      placeholder="Buscar por nome ou usuário..."
      data={options}
      value={value === null ? null : String(value)}
      onChange={(escolhido, option) => {
        setSelected(escolhido ? { value: escolhido, label: option?.label ?? escolhido } : null);
        onChange(escolhido ? Number(escolhido) : null);
      }}
      searchable
      searchValue={search}
      onSearchChange={setSearch}
      clearable
      nothingFoundMessage={activeQuery.isLoading ? 'Carregando...' : 'Nenhum usuário encontrado.'}
      maw={420}
    />
    {noStudents && (
      <Text size="sm" c="dimmed" role="status">
        Você não tem alunos vinculados. Peça a um administrador para criar o vínculo em Grupos e acessos.
      </Text>
    )}
    {studentsMode && studentsQuery.isError && (
      <Text size="sm" c="red" role="alert">
        Não foi possível carregar seus alunos.
      </Text>
    )}
    </Stack>
  );
}

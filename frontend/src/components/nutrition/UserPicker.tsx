'use client';

import { Select } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { fetchUserLookup, userLookupKey, userLookupLabel } from '@/lib/user-lookup';

type Option = { value: string; label: string };

type Props = {
  value: number | null;
  onChange: (userId: number | null) => void;
};

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
export function UserPicker({ value, onChange }: Props) {
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Option | null>(null);
  const termo = selected && search === selected.label ? '' : search;

  const usersQuery = useQuery({
    queryKey: userLookupKey(termo),
    queryFn: () => fetchUserLookup(termo),
  });
  const options: Option[] = (usersQuery.data?.items || []).map((user) => ({
    value: String(user.id),
    label: userLookupLabel(user),
  }));
  if (selected && !options.some((option) => option.value === selected.value)) {
    options.unshift(selected);
  }

  return (
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
      nothingFoundMessage={usersQuery.isLoading ? 'Carregando...' : 'Nenhum usuário encontrado.'}
      maw={420}
    />
  );
}

'use client';

import { Alert, Button, Group, Modal, Select, Stack, Text } from '@mantine/core';
import type { ComboboxItem, OptionsFilter } from '@mantine/core';
import { IconAlertTriangle, IconLink } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import type {
  MappingCanonicalAssociateRequest,
  MappingCanonicalAssociateResponse,
  MappingCanonicalOptionItem,
  MappingCanonicalOptionsResponse,
} from '@/lib/mappings-types';
import { ASSOCIATION_INVALIDATED_QUERY_KEYS, canonicalOptionMatches } from '@/lib/unmapped-exercises';
import { BRAND } from '@/theme';

const MAX_VISIBLE_OPTIONS = 50;

type Props = {
  aliasName: string | null;
  onClose: () => void;
  onAssociated: (response: MappingCanonicalAssociateResponse) => void;
};

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError || error instanceof Error ? error.message : fallback;
}

/**
 * Modal "Associar exercício sem correspondência": grava o nome como alias de
 * um exercício canônico (`POST /mapping-canonical/associate`, admin-only). O
 * catálogo inteiro vem uma vez de `GET /mapping-canonical/options` e é
 * filtrado no cliente (nome, nome Amazfit, grupo primário).
 */
export function AssociateCanonicalExerciseModal({ aliasName, onClose, onAssociated }: Props) {
  const qc = useQueryClient();
  const opened = aliasName != null;
  const [targetId, setTargetId] = useState<string | null>(null);

  const optionsQuery = useQuery({
    queryKey: ['mapping-canonical-options', 'all'],
    queryFn: () => apiFetch<MappingCanonicalOptionsResponse>('/mapping-canonical/options'),
    enabled: opened,
    staleTime: 60_000,
  });

  const byId = useMemo(() => {
    const map = new Map<string, MappingCanonicalOptionItem>();
    for (const item of optionsQuery.data?.items || []) map.set(String(item.id), item);
    return map;
  }, [optionsQuery.data]);

  const data = useMemo<ComboboxItem[]>(
    () => Array.from(byId.entries()).map(([value, item]) => ({ value, label: item.exercise_name || 'Sem nome' })),
    [byId],
  );

  const filter: OptionsFilter = ({ options, search, limit }) => {
    const matched = options.filter((entry) => {
      if (!('value' in entry)) return false;
      const item = byId.get(entry.value);
      return item ? canonicalOptionMatches(item, search) : false;
    });
    return limit ? matched.slice(0, limit) : matched;
  };

  const mutation = useMutation({
    mutationFn: async () => {
      if (!aliasName || !targetId) throw new Error('Selecione o exercício canônico de referência.');
      const payload: MappingCanonicalAssociateRequest = { alias_name: aliasName, target_mapping_id: Number(targetId) };
      const response = await apiFetch<MappingCanonicalAssociateResponse>('/mapping-canonical/associate', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(response.error || 'Não foi possível salvar a associação.');
      return response;
    },
    onSuccess: async (response) => {
      handleClose();
      onAssociated(response);
      await Promise.all(ASSOCIATION_INVALIDATED_QUERY_KEYS.map((queryKey) => qc.invalidateQueries({ queryKey: [...queryKey] })));
    },
  });

  function handleClose() {
    setTargetId(null);
    mutation.reset();
    onClose();
  }

  return (
    <Modal opened={opened} onClose={handleClose} title="Associar exercício sem correspondência" size="lg" centered>
      <Stack gap="md">
        <Alert color="blue" variant="light">
          <Text size="sm">Nome sem correspondência:</Text>
          <Text fw={700}>{aliasName || '—'}</Text>
        </Alert>

        <Select
          label="Exercício canônico"
          description="Digite parte do nome, do nome Amazfit ou do grupo muscular primário."
          placeholder={optionsQuery.isLoading ? 'Carregando opções...' : 'Pesquisar exercício'}
          searchable
          clearable
          data={data}
          filter={filter}
          limit={MAX_VISIBLE_OPTIONS}
          maxDropdownHeight={320}
          value={targetId}
          onChange={setTargetId}
          disabled={optionsQuery.isLoading}
          nothingFoundMessage="Nenhum exercício encontrado"
          renderOption={({ option }) => {
            const item = byId.get(option.value);
            return (
              <Stack gap={0}>
                <Text size="sm" fw={600}>
                  {item?.exercise_name || 'Sem nome'}
                </Text>
                <Text size="xs" c={BRAND.textMuted}>
                  {item?.primary_group || 'Sem grupo'} · Amazfit: {item?.amazfit_name || '—'}
                </Text>
              </Stack>
            );
          }}
        />

        {optionsQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle />}>
            {errorMessage(optionsQuery.error, 'Não foi possível carregar os exercícios canônicos.')}
          </Alert>
        )}

        {mutation.isError && (
          <Alert color="red" icon={<IconAlertTriangle />}>
            {errorMessage(mutation.error, 'Não foi possível salvar a associação.')}
          </Alert>
        )}

        <Group justify="flex-end">
          <Button variant="default" onClick={handleClose}>
            Cancelar
          </Button>
          <Button
            leftSection={<IconLink size={16} />}
            loading={mutation.isPending}
            disabled={!targetId}
            onClick={() => mutation.mutate()}
          >
            Salvar associação
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

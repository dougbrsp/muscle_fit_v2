'use client';

import { ActionIcon, Alert, Group, Loader, Paper, Stack, Text } from '@mantine/core';
import { IconAlertTriangle, IconChevronLeft, IconChevronRight } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { UnmappedExercisesAssociation } from '@/components/exercise-association/UnmappedExercisesAssociation';
import { apiFetch, ApiError } from '@/lib/api';
import type { UnmappedExercisesByNameResponse } from '@/lib/mappings-types';
import { rowsFromUnmappedByName } from '@/lib/unmapped-exercises';
import { BRAND } from '@/theme';

const PAGE_SIZE = 50;

/**
 * Aba "Sem classificação" de `/dominio-exercicios`: nomes de exercício
 * executados cujo vínculo não resolve para `exercise_catalog` ativo, SEM
 * janela de dias (`GET /mapping-canonical/unmapped?group_by=name`), com
 * associação direta a um exercício canônico. Leitura aberta; associar é
 * admin-only (mesmo `isAdmin` das outras abas).
 */
export function UnmappedExercisesPanel({ isAdmin }: { isAdmin: boolean }) {
  const [offset, setOffset] = useState(0);

  const unmappedQuery = useQuery({
    queryKey: ['mapping-canonical-unmapped', 'by-name', offset],
    queryFn: () =>
      apiFetch<UnmappedExercisesByNameResponse>(
        `/mapping-canonical/unmapped?group_by=name&limit=${PAGE_SIZE}&offset=${offset}`,
      ),
  });

  const data = unmappedQuery.data;
  const total = data?.total ?? 0;
  const rows = rowsFromUnmappedByName(data?.rows);

  // Última página esvaziou (ex.: associou o último nome dela): volta uma.
  const pageEmptied = Boolean(data) && rows.length === 0 && offset > 0 && offset >= total;
  useEffect(() => {
    if (pageEmptied) setOffset((current) => Math.max(0, current - PAGE_SIZE));
  }, [pageEmptied]);

  return (
    <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Stack gap="sm">
        <Text c={BRAND.textMuted} size="sm">
          Nomes de exercício já executados que não resolvem para um exercício ativo do catálogo canônico, em todo o
          histórico. Enquanto não forem associados, as séries ficam fora dos grupos musculares (Dashboard, Insights e
          Grupos musculares). &quot;Associar&quot; grava o nome como apelido do exercício escolhido e reclassifica só
          os exercícios desse nome e os que estão sem vínculo.
        </Text>

        {unmappedQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle />}>
            {unmappedQuery.error instanceof ApiError
              ? unmappedQuery.error.message
              : 'Não foi possível carregar os exercícios sem classificação.'}
          </Alert>
        )}

        {unmappedQuery.isLoading ? (
          <Group justify="center" py="md">
            <Loader size="sm" />
          </Group>
        ) : (
          data && <UnmappedExercisesAssociation rows={rows} canAssociate={isAdmin} showHistory />
        )}

        {data && total > 0 && (
          <Group justify="space-between">
            <Text c={BRAND.textMuted} size="xs">
              {offset + 1}–{offset + rows.length} de {total} nome(s).
            </Text>
            <Group gap="xs">
              <ActionIcon
                variant="light"
                color="brand"
                aria-label="Página anterior"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                <IconChevronLeft size={16} />
              </ActionIcon>
              <ActionIcon
                variant="light"
                color="brand"
                aria-label="Próxima página"
                disabled={!data.has_more}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                <IconChevronRight size={16} />
              </ActionIcon>
            </Group>
          </Group>
        )}
      </Stack>
    </Paper>
  );
}
